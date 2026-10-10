"""Real local socket + mocked SSH/CP1 tests. No live tokens or cluster calls."""
import copy
import datetime as dt
import os
from pathlib import Path
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

import deploy_binding as b
import deploy_handoff as h
import deploy_native_probe as n
import transfer_deploy_credential as transfer
from test_deploy_binding import NOW, encode
from test_deploy_credential import fixture, Reviewer, SA_UID
from test_deploy_plan import plan_inputs


def scope():
    return {'execution': plan_inputs()[3], 'record_sha256': 'a' * 64,
            'plan_sha256': 'b' * 64, 'sa_uid': SA_UID}


class Endpoint:
    def __init__(self, messages):
        self.messages, self.sent = messages, []
        self.ready = {'sa_uid': SA_UID}
    def send(self, obj, timeout=10):
        self.sent.append(copy.deepcopy(obj))
    def recv(self, timeout=10):
        b.need(timeout > 0, 'EXPIRED')
        return self.messages.pop(0)


class RelayTests(unittest.TestCase):
    def setUp(self):
        claims, token, response = fixture()
        self.token = token
        self.expiry = dt.datetime.fromtimestamp(claims['exp'] - 1, dt.timezone.utc).isoformat()
        self.binding = scope()
        credential = n.c.Credential(token, SA_UID, Reviewer(response), now=lambda: NOW)
        credential.close()
        metadata = credential.observation()
        metadata.update(token_request_expires_at=self.expiry,
                        input_reference_cleanup='LOCAL_REFERENCE_DROPPED')
        self.cp1 = Endpoint([
            dict(stage='candidate', token=token, sa_uid=SA_UID, status_expires_at=self.expiry),
            dict(stage='review-result', response=response),
            dict(stage='done', token_issuance='ISSUED_AND_CP1_REVIEWED', credential=metadata)])
        self.runner = Endpoint([
            dict(stage='deploy-ready', binding=self.binding, expires_at=self.expiry),
            dict(stage='review', token=token, audience=b.AUDIENCE, timeout=10),
            dict(stage='review-received')])
        self.window = n.Window(self.expiry, now=lambda: NOW)
        real_window = n.Window
        mocked = patch.object(h.native, 'Window', side_effect=lambda value: real_window(value, now=lambda: NOW))
        mocked.start()
        self.addCleanup(mocked.stop)

    def test_relay_finishes_cp1_without_waiting_for_rollout(self):
        result = h.relay(self.cp1, self.runner, self.binding, self.window)
        self.assertEqual(result['HANDOFF'], 'REVIEW_RESPONSE_RECEIVED')
        self.assertEqual(result['DEPLOY'], 'CHECK_WORKFLOW_RESULT')
        self.assertEqual(self.cp1.sent[-1], {'action': 'finish'})
        self.assertNotIn(self.token, str(result))

    def test_wrong_binding_prevents_issuance(self):
        expected = copy.deepcopy(self.binding)
        expected['record_sha256'] = 'c' * 64
        with self.assertRaises(b.Invalid):
            h.relay(self.cp1, self.runner, expected, self.window)
        self.assertEqual(self.cp1.sent, [])

    def test_sa_drift_prevents_issuance(self):
        self.cp1.ready['sa_uid'] = 'other'
        with self.assertRaises(b.Invalid):
            h.relay(self.cp1, self.runner, self.binding, self.window)
        self.assertEqual(self.cp1.sent, [])

    def test_short_window_prevents_issuance(self):
        self.runner.messages[0]['expires_at'] = (NOW + dt.timedelta(seconds=600)).isoformat()
        with self.assertRaises(b.Invalid):
            h.relay(self.cp1, self.runner, self.binding, self.window)
        self.assertEqual(self.cp1.sent, [])

    def test_review_of_different_token_is_never_forwarded(self):
        self.runner.messages[1]['token'] = 'other'
        with self.assertRaises(b.Invalid):
            h.relay(self.cp1, self.runner, self.binding, self.window)
        self.assertEqual(self.cp1.sent, [{'action': 'issue-once'}])

    def test_native_status_cannot_extend_the_actual_900_second_token(self):
        self.cp1.messages[0]['status_expires_at'] = (NOW + dt.timedelta(seconds=900)).isoformat()
        with self.assertRaisesRegex(b.Invalid, '^HANDOFF_EXPIRY_INVALID$'):
            h.relay(self.cp1, self.runner, self.binding, self.window)
        self.assertEqual(self.runner.sent, [])

    def test_denied_cp1_response_is_not_forwarded(self):
        self.cp1.messages[1]['response']['status']['authenticated'] = False
        with self.assertRaises(n.c.CredentialError):
            h.relay(self.cp1, self.runner, self.binding, self.window)
        self.assertEqual(len(self.runner.sent), 1)

    def test_public_scope_hash_and_source_must_match(self):
        raw = encode(self.binding)
        source = self.binding['execution']['workflow_sha']
        self.assertEqual(transfer.scope_checked(raw, b.sha(raw), source), self.binding)
        for digest, sha in [('0' * 64, source), (b.sha(raw), 'f' * 40)]:
            with self.assertRaises(b.Invalid):
                transfer.scope_checked(raw, digest, sha)


@unittest.skipUnless(os.name == 'posix' and hasattr(socket, 'SO_PEERCRED'), 'Linux sockets required')
class SocketTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='hs-', dir=Path.cwd())
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        (self.home / 'FDS_DeploySockets').mkdir(mode=0o700)
        self.receiver = h.Receiver(self.home, scope(), (NOW + dt.timedelta(seconds=900)).isoformat(), lambda: 900)

    def test_socket_credentials_reject_different_uid(self):
        class Other:
            def getsockopt(self, *args):
                return struct.pack('3i', 1, os.geteuid() + 1, 1)
        with self.assertRaises(b.Invalid):
            h.peer_checked(Other())

    def test_existing_path_is_never_deleted(self):
        self.receiver.path.write_text('retain')
        with self.assertRaises(b.Invalid):
            self.receiver.open()
        self.receiver.close()
        self.assertEqual(self.receiver.path.read_text(), 'retain')

    def test_replacement_path_is_never_deleted_on_close(self):
        self.receiver.path.write_text('replacement')
        self.receiver.inode = (0, 0)
        with self.assertRaises(b.Invalid):
            self.receiver.close()
        self.assertEqual(self.receiver.path.read_text(), 'replacement')

    def test_real_socket_roundtrip_exact_candidate_and_review(self):
        _, token, response = fixture()
        errors = []
        original_listen = socket.socket.listen
        listening = threading.Event()
        def listen(sock, value):
            original_listen(sock, value)
            listening.set()
        def server():
            try:
                self.receiver.open()
                candidate = self.receiver.candidate(SA_UID)
                result = self.receiver.review(candidate['token'], b.AUDIENCE, 10)
                self.assertEqual(result, response)
            except BaseException as exc:
                errors.append(exc)
            finally:
                self.receiver.close()
        with patch.object(socket.socket, 'listen', listen):
            thread = threading.Thread(target=server, daemon=True)
            thread.start()
            self.assertTrue(listening.wait(5))
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
                sock.connect(str(self.receiver.path))
                channel = n.Channel(sock.fileno(), sock.fileno())
                self.assertEqual(channel.recv()['binding'], scope())
                channel.send(dict(stage='candidate', token=token, sa_uid=SA_UID,
                                  status_expires_at=(NOW + dt.timedelta(seconds=900)).isoformat()))
                self.assertEqual(channel.recv()['token'], token)
                channel.send(dict(stage='review-result', response=response))
                self.assertEqual(channel.recv(), {'stage': 'review-received'})
            thread.join(5)
            self.assertFalse(thread.is_alive())
        if errors:
            raise errors[0]
        self.assertFalse(self.receiver.path.exists())

    def test_embedded_ssh_bridge_protocol_over_real_process_pipes_and_socket(self):
        _, token, response = fixture()
        errors, listening = [], threading.Event()
        original_listen = socket.socket.listen
        def listen(sock, value):
            original_listen(sock, value)
            listening.set()
        def server():
            try:
                self.receiver.open()
                candidate = self.receiver.candidate(SA_UID)
                self.receiver.review(candidate['token'], b.AUDIENCE, 10)
            except BaseException as exc:
                errors.append(exc)
            finally:
                self.receiver.close()
        # Bootstrap identity/source checks have their own existing tests. Here
        # substitute only bootstrap functions, execute the actual bridge body.
        prelude = ('import sys,os,json,stat\nfrom pathlib import Path\n'
                   'def require(ok):\n if not ok: raise ValueError("TEST_REJECTED")\n'
                   'def identity(role): pass\n'
                   'def safe(path,regular=True): return path.lstat()\n'
                   'def stage(*args): return Path(' + repr(str(Path(__file__).parent)) + ')\n'
                   'Path.home=classmethod(lambda cls: Path(' + repr(str(self.home)) + '))\n')
        process = None
        with patch.object(socket.socket, 'listen', listen):
            thread = threading.Thread(target=server, daemon=True)
            thread.start()
            self.assertTrue(listening.wait(5))
            try:
                process = subprocess.Popen(
                    [sys.executable, '-I', '-B', '-c', prelude + transfer.BRIDGE_MAIN,
                     'a' * 64, scope()['execution']['workflow_sha'], encode(scope()).decode()],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    env={**os.environ, 'SSH_CONNECTION': 'SYNTHETIC_TEST'}, bufsize=0)
                channel = n.Channel(process.stdout.fileno(), process.stdin.fileno())
                channel.send({'test': 'source-placeholder'})
                self.assertEqual(channel.recv()['stage'], 'bridge-ready')
                self.assertEqual(channel.recv()['binding'], scope())
                channel.send(dict(stage='candidate', token=token, sa_uid=SA_UID,
                                  status_expires_at=(NOW + dt.timedelta(seconds=900)).isoformat()))
                self.assertEqual(channel.recv()['token'], token)
                channel.send(dict(stage='review-result', response=response))
                self.assertEqual(channel.recv(), {'stage': 'review-received'})
                self.assertEqual(process.wait(timeout=5), 0)
                self.assertEqual(process.stderr.read(), b'')
            finally:
                if process is not None:
                    if process.poll() is None:
                        process.kill()
                    process.wait(timeout=5)
                    for stream in (process.stdin, process.stdout, process.stderr):
                        stream.close()
                thread.join(5)
        self.assertFalse(thread.is_alive())
        if errors:
            raise errors[0]


class CleanupTests(unittest.TestCase):
    def test_broker_closes_both_endpoints_even_if_runner_close_fails(self):
        from unittest.mock import Mock
        cp1, runner = Mock(), Mock()
        runner.close.side_effect = OSError('PRIVATE_CANARY')
        with patch.object(transfer.native, 'Window'), \
             patch.object(transfer.probe, 'Session', return_value=cp1), \
             patch.object(transfer, 'Bridge', return_value=runner), \
             patch.object(transfer.handoff, 'relay', side_effect=ValueError('PRIVATE_CANARY')):
            result = transfer.execute(b'placeholder', 'a' * 64, 'b' * 40, scope(),
                                      'placeholder', 'cp1-key', 'runner-key')
        cp1.close.assert_called_once()
        runner.close.assert_called_once()
        self.assertEqual(result['HANDOFF'], 'NOT_CONFIRMED')
        self.assertNotIn('PRIVATE_CANARY', str(result))


if __name__ == '__main__':
    unittest.main()
