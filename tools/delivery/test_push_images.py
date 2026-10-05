"""Synthetic archives and fake registry/scanner; no network, Push or credentials."""
import copy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import push_images as push
from test_delivery_gate import fixture
from oci_test_fixtures import image_members, write_archive
from test_scan_images import report


class PushTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.evidence = self.root / 'old'
        self.output = self.root / 'output'
        self.output.mkdir()
        self.record = fixture()
        self.context = {'workflow_sha': 'c' * 40, 'run_id': '456', 'attempt': 1}
        self.record['push_authorization'] = {'workflow_sha': self.context['workflow_sha'],
            'expires_at': (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
            'reference': 'https://github.com/321Team/fds-project-baseline/issues/16#fixture'}
        oldctx = {'workflow_sha': '2' * 40, 'run_id': '123', 'attempt': 1,
                  'input_sha256': '3' * 64}
        self.scan = {'IMAGE_SCAN': 'PASS', 'source_sha': '1' * 40,
                     'context': oldctx, 'images': {}}
        self.build = {'BUILD_SCAN': 'PASS', 'context': oldctx,
                      'source': self.record['source'], 'images': {}}
        self.manifests = {}
        for index, name in enumerate(push.SERVICES):
            archive = self.evidence / 'build-scan' / (name + '.oci.tar')
            archive.parent.mkdir(parents=True, exist_ok=True)
            members = image_members()
            # Distinct, valid manifests, both backed by fully hashed fixture blobs.
            idx = json.loads(members['index.json'])
            descriptor = idx['manifests'][0]
            manifest = json.loads(members['blobs/sha256/' + descriptor['digest'][7:]])
            manifest['annotations'] = {'fixture': str(index)}
            raw = json.dumps(manifest).encode()
            digest = hashlib.sha256(raw).hexdigest()
            members['blobs/sha256/' + digest] = raw
            descriptor.update(digest='sha256:' + digest, size=len(raw))
            members['index.json'] = json.dumps(idx).encode()
            write_archive(archive, members)
            identity = push.inspect_archive(archive)
            self.manifests[name] = raw
            self.build['images'][name] = identity
            report_hash = self.write(self.evidence / 'build-scan/scan' / name / 'report.json', report(identity))
            self.scan['images'][name] = {**identity, 'scan': 'PASS', 'exit_code': 0,
                'counts': {'HIGH': 0, 'CRITICAL': 0}, 'report_sha256': report_hash}
            self.record['build_scan']['images'][name] = copy.deepcopy(identity)
            self.record['build_scan']['scan'][name] = copy.deepcopy(self.scan['images'][name])
        scan_hash = self.write(self.evidence / 'build-scan/scan/result.json', self.scan)
        self.build['scan_result_sha256'] = scan_hash
        self.record['build_scan']['scan_result_sha256'] = scan_hash
        build_hash = self.write(self.evidence / 'build-scan/result.json', self.build)
        self.app = {'APP_WORKFLOW': 'PASS', 'APP_TEST_BUILD_SCAN': 'PASS',
                    'context': oldctx, 'build_result_sha256': build_hash}
        self.record['app_result_sha256'] = self.write(self.evidence / 'result.json', self.app)
        self.auth = self.root / 'credentials.json'
        self.write(self.auth, {'auths': {'10.1.93.54': {'auth': 'fixture-secret-canary'}}})
        self.auth.chmod(0o600)
        self.calls = []

    def write(self, path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data))
        return push.sha256(path)

    def scanner(self, folder, context, source, archives, env):
        result = copy.deepcopy(self.scan)
        result['context'] = context
        for name in push.SERVICES:
            digest = self.write(folder / name / 'report.json', report(push.inspect_archive(archives[name])))
            result['images'][name]['report_sha256'] = digest
        self.write(folder / 'db/result.json', {'DB_PREFLIGHT': 'PASS'})
        self.write(folder / 'result.json', result)
        return 0, result

    def registry(self, argv, home, capture=False):
        self.calls.append(argv)
        self.assertNotIn('fixture-secret-canary', ' '.join(argv))
        self.assertTrue((home / 'auth.json').is_file())
        name = next(n for n in push.SERVICES if '/' + n in argv[-1])
        return self.manifests[name] if capture else b''

    def run_push(self, registry=None, scanner=None, context=True):
        with patch.object(push, 'command', side_effect=registry or self.registry), \
             patch.object(push, 'scan_pair', side_effect=scanner or self.scanner) as scan, \
             patch.object(push, 'check_db'), patch.object(push, 'SKOPEO', '/usr/bin/true'):
            rc, result = push.execute(self.record, self.evidence, self.output,
                                     self.auth, self.context if context else None)
        self.assertFalse(list(self.root.glob('push-private-*')))
        for p in self.output.rglob('*'):
            if p.is_file():
                self.assertNotIn(b'fixture-secret-canary', p.read_bytes())
        return rc, result, scan

    def test_pair_success_verifies_tag_and_digest_tls_and_preservation(self):
        rc, result, scan = self.run_push()
        self.assertEqual((rc, result['PUSH']), (0, 'PASS'))
        self.assertEqual(result['DEPLOY'], 'NOT_EXECUTED')
        self.assertEqual(len(self.calls), 6)
        self.assertEqual(scan.call_count, 1)
        for call in self.calls:
            self.assertIn('--dest-tls-verify=true' if call[0] == 'copy' else '--tls-verify=true', call)
            if call[0] == 'copy':
                self.assertIn('--preserve-digests', call)
        self.assertEqual(set(result['images']), set(push.SERVICES))

    def test_local_validation_has_no_scan_or_registry_calls(self):
        rc, result, scan = self.run_push(context=False)
        self.assertEqual((rc, result['PUSH'], result['VALIDATION']), (0, 'NOT_EXECUTED', 'PASS'))
        self.assertEqual(self.calls, [])
        scan.assert_not_called()

    def test_missing_or_tampered_archive_blocks_all_network(self):
        (self.evidence / 'build-scan/fds-engine.oci.tar').write_bytes(b'bad')
        rc, result, scan = self.run_push()
        self.assertEqual(rc, 1)
        self.assertEqual(self.calls, [])
        scan.assert_not_called()

    def test_receipt_chain_and_context_tampering_block(self):
        self.app['context']['run_id'] = '999'
        self.record['app_result_sha256'] = self.write(self.evidence / 'result.json', self.app)
        rc, _, scan = self.run_push()
        self.assertEqual(rc, 1)
        scan.assert_not_called()

    def test_report_tampering_blocks_before_fresh_scan(self):
        self.write(self.evidence / 'build-scan/scan/transaction-api/report.json', {})
        rc, _, scan = self.run_push()
        self.assertEqual(rc, 1)
        scan.assert_not_called()

    def test_symlink_archive_blocks(self):
        path = self.evidence / 'build-scan/fds-engine.oci.tar'
        moved = self.root / 'moved.tar'
        path.rename(moved)
        path.symlink_to(moved)
        rc, _, scan = self.run_push()
        self.assertEqual(rc, 1)
        scan.assert_not_called()

    def test_malformed_digest_is_block_not_unhandled_typeerror(self):
        self.record['build_scan']['images']['transaction-api']['manifest_digest'] = []
        rc, result, scan = self.run_push()
        self.assertEqual((rc, result['error_code']), (1, 'DELIVERY_INPUT_MALFORMED'))
        scan.assert_not_called()

    def test_wrong_execution_approval_blocks_fresh_scan(self):
        self.record['push_authorization']['workflow_sha'] = 'd' * 40
        rc, _, scan = self.run_push()
        self.assertEqual(rc, 1)
        scan.assert_not_called()

    def test_fresh_scan_failure_blocks_copy(self):
        rc, result, _ = self.run_push(scanner=lambda *args: (10, {'IMAGE_SCAN': 'BLOCK'}))
        self.assertEqual((rc, result['PUSH']), (1, 'NOT_EXECUTED'))
        self.assertEqual(self.calls, [])

    def test_fresh_report_tamper_stops_all_copies(self):
        def tampered(folder, *args):
            rc, result = self.scanner(folder, *args)
            self.write(folder / 'fds-engine/report.json', {})
            return rc, result
        rc, result, _ = self.run_push(scanner=tampered)
        self.assertEqual((rc, result['PUSH']), (1, 'NOT_EXECUTED'))
        self.assertEqual(self.calls, [])

    def test_receipt_hash_mismatch_stops_network(self):
        self.record['app_result_sha256'] = '0' * 64
        rc, _, scan = self.run_push()
        self.assertEqual(rc, 1)
        scan.assert_not_called()

    def test_auth_permissions_block_before_scan(self):
        self.auth.chmod(0o644)
        rc, _, scan = self.run_push()
        self.assertEqual(rc, 1)
        scan.assert_not_called()

    def test_auth_helpers_not_executed(self):
        self.write(self.auth, {'credHelpers': {'10.1.93.54': 'evil'}})
        rc, _, scan = self.run_push()
        self.assertEqual(rc, 1)
        scan.assert_not_called()

    def test_remote_manifest_mismatch_keeps_unknown_and_stops_pair(self):
        def wrong(argv, home, capture=False):
            self.registry(argv, home, capture)
            return b'wrong manifest' if capture else b''
        rc, result, _ = self.run_push(registry=wrong)
        self.assertEqual((rc, result['PUSH']), (1, 'INCOMPLETE'))
        self.assertEqual(set(result['images']), {'transaction-api'})
        self.assertEqual(result['images']['transaction-api']['result'], 'UNKNOWN')

    def test_second_copy_failure_preserves_first_success_no_delete(self):
        def fail(argv, home, capture=False):
            if argv[0] == 'copy' and '/fds-engine:' in argv[-1]:
                raise push.PreflightError('REGISTRY_COMMAND_FAILED')
            return self.registry(argv, home, capture)
        rc, result, _ = self.run_push(registry=fail)
        self.assertEqual((rc, result['PUSH']), (1, 'INCOMPLETE'))
        self.assertEqual(result['images']['transaction-api']['result'], 'PASS')
        self.assertEqual(result['images']['fds-engine']['result'], 'UNKNOWN')
        self.assertFalse(any('delete' in argv for argv in self.calls))

    def test_interruption_records_uncertain_remote_write(self):
        rc, result, _ = self.run_push(registry=lambda *a, **k: (_ for _ in ()).throw(KeyboardInterrupt()))
        self.assertEqual((rc, result['PUSH']), (1, 'INCOMPLETE'))
        self.assertEqual(result['images']['transaction-api']['result'], 'UNKNOWN')


if __name__ == '__main__':
    unittest.main()
