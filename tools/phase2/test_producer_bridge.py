"""Offline source-object tests and optional PyYAML bridge tests, all synthetic."""
import json
import os
import subprocess
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import delivery_adapter as core
import producer_bridge as bridge
from test_delivery_adapter import fixture

try:
    import yaml
    YAML_READY = yaml.__version__ == bridge.PYYAML_VERSION
except ImportError:
    YAML_READY = False

FAKE_CHECKER = b'''def check(text, mode, contract, allow_placeholders=False):
    host = contract["host"]
    return {"result": "FAIL" if host == "PENDING" else "PASS", "mode": mode,
            "namespace": "fds-app", "placeholders_allowed": allow_placeholders,
            "placeholders": [], "approved_route_host": host,
            "errors": ["SYNTHETIC_SECRET_NEVER_REPORT"] if host == "PENDING" else []}
'''


class RepositoryFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='p2-source-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.env = {k: v for k, v in os.environ.items() if not k.upper().startswith('GIT_')}
        self.env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull,
                        GIT_TERMINAL_PROMPT='0')
        self.git('init', '-q')
        self.commit = self.publish(FAKE_CHECKER, b'host: app.rosa.example.com\n')

    def git(self, *args):
        result = subprocess.run(['git', '-C', str(self.root), '-c', 'core.autocrlf=false',
                                 '-c', 'commit.gpgsign=false', '-c', 'user.name=P2 Synthetic',
                                 '-c', 'user.email=p2-synthetic@example.invalid', *args],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                timeout=15, env=self.env, check=True)
        return result.stdout

    def publish(self, checker, contract):
        for name, raw in ((bridge.CHECKER_PATH, checker), (bridge.CONTRACT_PATH, contract)):
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
        self.git('add', '--', bridge.CHECKER_PATH, bridge.CONTRACT_PATH)
        self.git('commit', '-qm', 'Synthetic test input only')
        return self.git('rev-parse', 'HEAD').decode().strip()


class SnapshotTests(RepositoryFixture):
    def test_reads_requested_commit_not_worktree_or_new_head(self):
        self.publish(b'raise RuntimeError("do not execute")\n', b'host: other.example.com\n')
        snapshot = bridge.GitSnapshot(self.root, self.commit)
        self.assertEqual(snapshot.blob(bridge.CHECKER_PATH), FAKE_CHECKER)
        self.assertEqual(snapshot.blob(bridge.CONTRACT_PATH), b'host: app.rosa.example.com\n')

    def test_replace_refs_do_not_substitute_the_reviewed_source(self):
        other = self.publish(b'not the reviewed checker', b'host: other.example.com\n')
        self.git('replace', self.commit, other)
        snapshot = bridge.GitSnapshot(self.root, self.commit)
        self.assertEqual(snapshot.blob(bridge.CHECKER_PATH), FAKE_CHECKER)

    def test_ambient_git_dir_cannot_redirect_the_read(self):
        with patch.dict(os.environ, {'GIT_DIR': str(self.root / 'missing'),
                                     'GIT_WORK_TREE': str(self.root / 'missing')}):
            snapshot = bridge.GitSnapshot(self.root, self.commit)
        self.assertEqual(snapshot.blob(bridge.CHECKER_PATH), FAKE_CHECKER)

    def test_full_commit_only_and_two_allowlisted_paths(self):
        for value in ('HEAD', self.commit[:7], '--help', 'x' * 40):
            with self.subTest(value=value), self.assertRaises(bridge.BridgeError):
                bridge.GitSnapshot(self.root, value)
        snapshot = bridge.GitSnapshot(self.root, self.commit)
        for path in ('../../secret', 'tools/other.py', bridge.CHECKER_PATH + ':x'):
            with self.subTest(path=path), self.assertRaises(bridge.BridgeError):
                snapshot.blob(path)

    def test_missing_objects_and_blob_instead_of_commit_fail_closed(self):
        blob = self.git('rev-parse', self.commit + ':' + bridge.CHECKER_PATH).decode().strip()
        for value in ('0' * 40, blob):
            with self.subTest(value=value), self.assertRaises(bridge.BridgeError):
                bridge.GitSnapshot(self.root, value)

    def test_oversized_source_is_rejected_before_content_is_loaded(self):
        commit = self.publish(b'x' * (bridge.MAX_BYTES + 1), b'host: app.rosa.example.com\n')
        with self.assertRaisesRegex(bridge.BridgeError, 'SOURCE_SIZE_INVALID'):
            bridge.GitSnapshot(self.root, commit).blob(bridge.CHECKER_PATH)


@unittest.skipUnless(YAML_READY, 'Optional bridge requires consumer PyYAML 6.0.3; run separately')
class BridgeTests(RepositoryFixture):
    def setUp(self):
        super().setUp()
        binding, raw, _ = fixture()
        self.render = yaml.safe_dump_all(json.loads(raw), sort_keys=False).encode()
        self.contract = b'host: app.rosa.example.com\n'
        self.binding = replace(binding, source_commit=self.commit,
                               rendered_sha256=core.digest(self.render),
                               contract_sha256=core.digest(self.contract))
        self.pin = patch.object(bridge, 'CHECKER_SHA256', core.digest(FAKE_CHECKER))
        self.pin.start()
        self.addCleanup(self.pin.stop)

    def call(self, binding=None, rendered=None, contract=None):
        return bridge.CommittedProducerBridge(self.root, binding or self.binding)(
            self.render if rendered is None else rendered,
            self.contract if contract is None else contract)

    def rebind(self, *, render=None, contract=None, commit=None):
        if render is not None:
            self.render = render
        if contract is not None:
            self.contract = contract
        self.binding = replace(self.binding, source_commit=commit or self.binding.source_commit,
                               rendered_sha256=core.digest(self.render),
                               contract_sha256=core.digest(self.contract))

    def test_success_binds_raw_bytes_without_claiming_build_or_approval(self):
        report, docs = self.call()
        self.assertEqual(report['result'], 'PASS')
        self.assertFalse(report['placeholders_allowed'])
        self.assertEqual(report['rendered_sha256'], self.binding.rendered_sha256)
        self.assertEqual(report['producer_commit'], self.commit)
        self.assertEqual(report['render_generation'], 'NOT_VERIFIED')
        self.assertEqual(report['source_approval'], 'NOT_VERIFIED')
        self.assertEqual(len(docs), 6)
        self.assertEqual(len(core.prepare(self.binding, self.render, self.contract,
                                        bridge.CommittedProducerBridge(self.root, self.binding))), 6)

    def test_different_unreviewed_checker_fails_before_execution(self):
        commit = self.publish(b'raise AssertionError("unreviewed")\n', self.contract)
        self.rebind(commit=commit)
        with self.assertRaisesRegex(bridge.BridgeError, '^PRODUCER_BRIDGE_FAILED$'):
            self.call()

    def test_input_hashes_and_contract_source_content_must_match(self):
        for kwargs in ({'rendered': self.render + b' '}, {'contract': self.contract + b' '}):
            with self.subTest(change=list(kwargs)), self.assertRaises(bridge.BridgeError):
                self.call(**kwargs)
        # Updating the caller hash cannot make another contract part of this commit.
        self.rebind(contract=b'host: modified.example.com\n')
        with self.assertRaises(bridge.BridgeError):
            self.call()

    def test_crlf_checkout_contract_keeps_original_transfer_hash(self):
        self.rebind(contract=self.contract.replace(b'\n', b'\r\n'))
        report, _ = self.call()
        self.assertEqual(report['contract_sha256'], core.digest(self.contract))
        self.assertNotEqual(report['contract_sha256'], core.digest(self.contract.replace(b'\r\n', b'\n')))

    def test_pending_contract_stays_rejected_and_diagnostics_are_sanitized(self):
        raw = b'host: PENDING\n'
        commit = self.publish(FAKE_CHECKER, raw)
        self.rebind(contract=raw, commit=commit)
        report, _ = self.call()
        self.assertEqual(report['result'], 'FAIL')
        self.assertEqual(report['errors'], ['PRODUCER_REJECTED'])
        self.assertNotIn('SYNTHETIC_SECRET_NEVER_REPORT', json.dumps(report))
        with self.assertRaisesRegex(core.Stop, 'RENDER_CHECK_FAILED'):
            core.prepare(self.binding, self.render, self.contract,
                         bridge.CommittedProducerBridge(self.root, self.binding))

    def test_duplicate_keys_aliases_tags_and_nonstring_keys_are_rejected(self):
        for raw in (b'host: first\nhost: second\n', b'a: &a [*a]\n',
                    b'a: !!str hello\n', b'1: wrong\n', b'"<<": {}\n'):
            commit = self.publish(FAKE_CHECKER, raw)
            self.rebind(contract=raw, commit=commit)
            with self.subTest(raw=raw), self.assertRaises(bridge.BridgeError):
                self.call()

    def test_documents_nulls_nonjson_types_and_excessive_depth_are_rejected(self):
        for raw in (b'null\n', b'- list\n', b'when: 2026-10-13\n', b'x: .nan\n',
                    b'x: ' + b'[' * 50 + b'0' + b']' * 50 + b'\n'):
            with self.subTest(raw=raw), self.assertRaises(Exception):
                bridge.strict_documents(raw, yaml)

    def test_extra_or_missing_render_documents_are_rejected(self):
        for raw in (self.render + b'---\na: 1\n', b'kind: Service\n'):
            self.rebind(render=raw)
            with self.assertRaises(bridge.BridgeError):
                self.call()

    def test_parser_version_drift_is_rejected(self):
        with patch.object(yaml, '__version__', 'unreviewed-version'):
            with self.assertRaises(bridge.BridgeError):
                self.call()


if __name__ == '__main__':
    unittest.main()
