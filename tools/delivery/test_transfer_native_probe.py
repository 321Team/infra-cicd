"""Source integrity and synthetic broker routing; no remote hosts or native token."""
import copy
import datetime as dt
import hashlib
import json
import os
import subprocess
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import deploy_native_probe as n
import transfer_native_probe as bridge
from test_deploy_binding import NOW
from test_deploy_credential import SA_UID
from test_deploy_native_probe import native_fixture


def bundle():
    files = {}
    for name in bridge.FILES:
        data = b'SYNTHETIC_SOURCE = True\n'
        files[name] = {
            'content': data.decode(),
            'git_blob': hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest(),
        }
    return n.encode({'schema': 'P1-CICD-native-probe-source-1',
                     'source_sha': 'a' * 40, 'files': files}) + b'\n'


@unittest.skipUnless(os.name == 'posix', 'POSIX protected paths')
class BundleTests(unittest.TestCase):
    def setUp(self):
        self.checks = bridge.loader()
        self.checks['identity'] = lambda role: None  # Synthetic identity only.
        self.raw, self.sha = bundle(), hashlib.sha256(bundle()).hexdigest()

    def test_exact_bundle_stages_only_seven_verified_source_files(self):
        root = self.checks['stage'](self.raw, self.sha, 'a' * 40, 'ansible')
        try:
            self.assertEqual({p.name for p in root.iterdir()}, bridge.FILES)
            self.assertEqual(root.stat().st_mode & 0o777, 0o700)
            for path in root.iterdir():
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                self.assertEqual(path.read_bytes(), b'SYNTHETIC_SOURCE = True\n')
        finally:
            for path in root.iterdir():
                path.unlink()
            root.rmdir()

    def test_source_sha_hash_file_set_blob_and_syntax_mismatch_rejected(self):
        original = n.b.read(self.raw)
        changes = [
            lambda x: x.update(source_sha='b' * 40),
            lambda x: x['files'].pop('deploy_native_probe.py'),
            lambda x: x['files'].update(extra={'content': 'x', 'git_blob': 'a' * 40}),
            lambda x: x['files']['deploy_native_probe.py'].update(content='MODIFIED'),
            lambda x: x['files']['deploy_native_probe.py'].update(git_blob='0' * 40),
        ]
        with self.assertRaises(ValueError):
            self.checks['stage'](self.raw, '0' * 64, 'a' * 40, 'ansible')
        for change in changes:
            value = copy.deepcopy(original)
            change(value)
            raw = n.encode(value) + b'\n'
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.checks['stage'](raw, hashlib.sha256(raw).hexdigest(), 'a' * 40, 'ansible')

    def test_pretty_or_duplicate_key_json_and_missing_final_lf_rejected(self):
        for raw in (self.raw[:-1], json.dumps(n.b.read(self.raw), indent=2).encode() + b'\n',
                    self.raw.replace(b'{"files":', b'{"schema":"duplicate","files":', 1)):
            with self.subTest(raw=raw[:15]), self.assertRaises(ValueError):
                self.checks['stage'](raw, hashlib.sha256(raw).hexdigest(), 'a' * 40, 'ansible')

    def test_source_file_symlink_hardlink_and_writable_parent_rejected(self):
        with tempfile.TemporaryDirectory(dir=Path.home()) as folder:
            root = Path(folder)
            original = root / 'source.json'
            original.write_bytes(self.raw)
            original.chmod(0o600)
            symlink = root / 'link'
            symlink.symlink_to(original)
            with self.assertRaises(ValueError):
                self.checks['read'](symlink)
            link = root / 'hardlink'
            os.link(original, link)
            with self.assertRaises(ValueError):
                self.checks['read'](original)
            link.unlink()
            root.chmod(0o777)
            with self.assertRaises(ValueError):
                self.checks['read'](original)
            root.chmod(0o700)


def metadata():
    _, token, review, response = native_fixture()
    reviewer = Mock()
    reviewer.review.return_value = review
    credential = n.c.Credential(token, SA_UID, reviewer, now=lambda: NOW, clock=lambda: 100)
    credential.close()
    value = credential.observation()
    value.update({'token_request_expires_at': response['status']['expirationTimestamp'],
                  'input_reference_cleanup': 'LOCAL_REFERENCE_DROPPED'})
    return value


def runner_result():
    return {
        'stage': 'runner-result',
        'result': {
            'RUNNER_TOKEN_AUTH': 'PASS', 'EFFECTIVE_RBAC_CHECK': 'PASS',
            'basis': 'LIVE_PINNED_TLS_SA_GET_AND_SELF_SUBJECT_ACCESS_REVIEW',
            'permissions': [{'attributes': attrs, 'allowed': allowed, 'expected': allowed}
                            for attrs, allowed in n.RBAC_TESTS],
            'deployments': [
                {'name': name, 'namespace': 'fds-app',
                 'uid': SA_UID if index == 0 else 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee',
                 'resource_version': 'opaque:7'}
                for index, name in enumerate(n.a.NAMES)
            ],
            'credential': metadata(),
            'input_reference_cleanup': 'LOCAL_REFERENCE_DROPPED',
            'checked_at': NOW.isoformat(), 'DEPLOY': 'NOT_EXECUTED',
            'DELIVERY_GATE': 'NOT_VERIFIED',
        },
    }


class FakeSession:
    def __init__(self, role, messages):
        self.ready = {'stage': 'ready', 'role': role, 'source_sha': 'a' * 40,
                      'workdir': '/synthetic', 'ca_sha256': bridge.CA_SHA,
                      'sa_uid': SA_UID if role == 'cp1' else None}
        self.messages = copy.deepcopy(messages)
        self.sent, self.closed, self.finished = [], False, False

    def send(self, obj, timeout=10):
        self.sent.append(copy.deepcopy(obj))

    def recv(self, timeout=10):
        if not self.messages:
            raise RuntimeError('PRIVATE_PIPE_PAYLOAD')
        return self.messages.pop(0)

    def finish(self):
        self.finished = True

    def close(self):
        self.closed = True


class BrokerTests(unittest.TestCase):
    def setUp(self):
        _, self.token, self.review, native = native_fixture()
        self.candidate = {'stage': 'candidate', 'token': self.token, 'sa_uid': SA_UID,
                          'status_expires_at': native['status']['expirationTimestamp']}
        self.request = {'stage': 'review', 'token': self.token,
                        'audience': n.b.AUDIENCE, 'timeout': 10}
        self.done = {'stage': 'done', 'token_issuance': 'ISSUED_AND_CP1_REVIEWED',
                     'credential': metadata()}
        self.cp1 = FakeSession('cp1', [self.candidate,
                                      {'stage': 'review-result', 'response': self.review},
                                      self.done])
        self.runner = FakeSession('runner', [self.request, runner_result()])

    def run_probe(self, execute=True, factory=None):
        factory = factory or (lambda role, *args, **kw: self.cp1 if role == 'cp1' else self.runner)
        with patch.object(n, 'Window', return_value=Mock(remaining=lambda: 600)):
            return bridge.coordinate(n, bundle(), 'b' * 64, 'a' * 40,
                                     execute=execute, authorization='synthetic' if execute else None,
                                     session_factory=factory)

    def test_complete_private_relay_fresh_review_and_public_cleanup_receipt(self):
        result = self.run_probe()
        self.assertEqual(result['NATIVE_AUTH_PROBE'], 'PASS')
        self.assertTrue(result['TOKEN_ISSUED'])
        self.assertEqual(self.cp1.sent[0], {'action': 'issue-once'})
        self.assertEqual(self.cp1.sent[1], self.request)
        self.assertEqual(self.cp1.sent[2], {'action': 'finish'})
        self.assertEqual(self.runner.sent[0], self.candidate)
        self.assertNotIn(self.token, json.dumps(result))
        self.assertNotIn('response', result)
        self.assertTrue(self.cp1.closed and self.runner.closed)
        self.assertTrue(self.cp1.finished and self.runner.finished)

    def test_prepare_sends_no_candidate_or_issue_action(self):
        done = {'stage': 'done', 'token_issuance': 'NOT_ATTEMPTED',
                'local_credential_cleanup': 'NO_CREDENTIAL'}
        self.cp1.messages = [done]
        self.runner.messages = [done]
        result = self.run_probe(execute=False)
        self.assertEqual(result['PREFLIGHT'], 'PASS')
        self.assertFalse(result['TOKEN_ISSUED'])
        self.assertEqual(self.cp1.sent, [{'action': 'stop'}])
        self.assertEqual(self.runner.sent, [{'action': 'stop'}])

    def test_failed_runner_preparation_occurs_before_issue(self):
        def factory(role, *args, **kw):
            if role == 'runner':
                raise RuntimeError('PRIVATE_SSH_ERROR')
            return self.cp1
        result = self.run_probe(factory=factory)
        self.assertEqual(result['TOKEN_ISSUANCE'], 'NOT_ATTEMPTED')
        self.assertEqual(self.cp1.sent, [])
        self.assertTrue(self.cp1.closed)
        self.assertNotIn('PRIVATE_', json.dumps(result))

    def test_different_relay_token_is_never_reviewed_or_returned(self):
        self.runner.messages[0]['token'] = self.token + 'OTHER_PRIVATE_VALUE'
        result = self.run_probe()
        self.assertEqual(result['NATIVE_AUTH_PROBE'], 'NOT_CONFIRMED')
        self.assertEqual(len(self.cp1.sent), 1)
        self.assertNotIn(self.token, json.dumps(result))

    def test_wrong_review_identity_or_audience_is_not_forwarded_as_authentication(self):
        for mutate in (lambda x: x['status']['user'].update(uid='aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'),
                       lambda x: x['status'].update(audiences=['other']),
                       lambda x: x['status'].update(authenticated=False)):
            self.setUp()
            mutate(self.cp1.messages[1]['response'])
            with self.subTest(mutate=mutate):
                result = self.run_probe()
                self.assertEqual(result['NATIVE_AUTH_PROBE'], 'NOT_CONFIRMED')
                self.assertEqual(len(self.runner.sent), 1)

    def test_missing_cleanup_ack_or_nonzero_exit_does_not_report_pass(self):
        for kind in ('ack', 'exit'):
            self.setUp()
            if kind == 'ack':
                self.cp1.messages.pop()
            else:
                self.runner.finish = Mock(side_effect=RuntimeError('PRIVATE_EXIT'))
            with self.subTest(kind=kind):
                result = self.run_probe()
                self.assertEqual(result['NATIVE_AUTH_PROBE'], 'NOT_CONFIRMED')
                self.assertNotIn(self.token, json.dumps(result))
                self.assertTrue(self.cp1.closed and self.runner.closed)

    def test_extra_secret_field_in_receipt_is_not_returned(self):
        self.runner.messages[1]['result']['credential']['token'] = self.token
        result = self.run_probe()
        self.assertEqual(result['NATIVE_AUTH_PROBE'], 'NOT_CONFIRMED')
        self.assertNotIn(self.token, json.dumps(result))

    def test_earliest_expiry_must_match_between_both_receipts(self):
        self.done['credential']['token_request_expires_at'] = (
            NOW + dt.timedelta(seconds=800)).isoformat()
        self.cp1.messages[-1] = self.done
        result = self.run_probe()
        self.assertEqual(result['NATIVE_AUTH_PROBE'], 'NOT_CONFIRMED')

    def test_endpoint_stderr_is_discarded_and_argv_contains_no_candidate(self):
        proc = Mock()
        proc.stdin.fileno.return_value = 10
        proc.stdout.fileno.return_value = 11
        proc.poll.return_value = 0
        checks = {'safe': Mock(return_value=Mock(st_mode=0o600)), 'read': Mock(return_value=b'known')}
        channel = Mock()
        channel.recv.return_value = {
            'stage': 'ready', 'role': 'runner', 'source_sha': 'a' * 40,
            'workdir': '/home/server01_cicd-runner/FDS_NativeProbeSource.synthetic',
            'ca_sha256': bridge.CA_SHA, 'sa_uid': None,
        }
        with patch.object(bridge.shutil, 'which', return_value='/synthetic/ssh'), \
             patch.object(bridge.os, 'access', return_value=True), \
             patch.object(bridge.subprocess, 'Popen', return_value=proc) as popen:
            session = bridge.Session('runner', bundle(), 'b' * 64, 'a' * 40,
                                     bridge.RUNNER_CA, None, lambda *args: channel, checks=checks)
            argv = popen.call_args.args[0]
            self.assertNotIn(self.token, json.dumps(argv))
            self.assertIn('StrictHostKeyChecking=yes', argv)
            self.assertIn('ForwardAgent=no', argv)
            self.assertEqual(popen.call_args.kwargs['stderr'], subprocess.DEVNULL)
            channel.send_raw.assert_called_once_with(bundle()[:-1], 30)
            session.close()

    def test_source_or_ssh_trust_drift_does_not_silently_continue(self):
        session = object.__new__(bridge.Session)
        session.known, session.known_bytes = Path('/synthetic-known'), b'original'
        session.checks = {'read': Mock(return_value=b'changed')}
        session.key, session.key_info = None, None
        with self.assertRaises(bridge.TransferError):
            session.drift_checked()
