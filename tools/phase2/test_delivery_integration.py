"""Core + real POSIX store + TLS sender code, using ONLY synthetic sockets.

The validator, authorization and API/TLS endpoint are test doubles. This is not
an operating integration/CLI or a real producer/cluster/credential acceptance.
"""
import json
import os
import ssl
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

import delivery_adapter as core
from delivery_authorization import BoundAuthorizer, VerifiedInputs
import delivery_store as store
import delivery_transport as transport
from test_delivery_adapter import Harness
from test_delivery_transport import CA, TOKEN, FakeSocket, response


class InjectedCrash(BaseException):
    """Escape ordinary exception handlers to model missing post-send cleanup."""


@unittest.skipUnless(os.name == 'posix', 'Integrated durability requires POSIX')
class DeliveryIntegrationTests(unittest.TestCase):
    def setUp(self):
        # Private workspace subtree: do not relax the store's parent checks.
        self.tmp = tempfile.TemporaryDirectory(prefix='p2-integration-test-', dir=Path.cwd())
        self.root = Path(self.tmp.name)
        self.root.chmod(0o700)
        self.handles = []
        self.addCleanup(self.cleanup)
        self.context = Mock()
        self.context.verify_mode = ssl.CERT_REQUIRED
        self.context.check_hostname = True
        self.context.keylog_filename = None
        self.context.wrap_socket.side_effect = lambda sock, **kwargs: sock
        self.sockets = []
        # Any accidental direct socket/DNS path fails this offline test.
        for target in (
                patch.object(transport.ssl, 'SSLContext', return_value=self.context),
                patch.object(transport.socket, 'socket', side_effect=self.socket_for_request),
                patch.object(transport.socket, 'getaddrinfo', side_effect=AssertionError('NO_DNS'))):
            target.start()
            self.addCleanup(target.stop)
        self.current_request = None
        self.http_failure = self.read_failure = None
        self.deny_after = None

    def cleanup(self):
        for handle in reversed(self.handles):
            handle.close()
        self.tmp.cleanup()

    def acquire(self, binding):
        claim = store.acquire(self.root, binding)
        self.handles.append(claim)
        return claim

    def connect(self, harness=None):
        h = harness or Harness()
        h.rebind(ca_sha256=core.digest(CA))
        h.lease = replace(h.lease, credential_fingerprint=core.digest(TOKEN.encode()))
        claim = self.acquire(h.binding)
        journal = store.Journal(claim)
        self.handles.append(journal)
        sender = transport.Sender(h.binding, ca_pem=CA, token=TOKEN, endpoint_ip='192.0.2.10')
        self.h, self.claim, self.journal = h, claim, journal

        def verify(binding):
            if self.deny_after is not None and len(h.requests) >= self.deny_after:
                raise RuntimeError('SYNTHETIC_PRIVATE_AUTH_DETAIL')
            # Synthetic verified input only; no live identity/approval claim.
            return VerifiedInputs(binding, binding, h.lease)

        def send(request):
            if request.method == 'PATCH':
                # Prove that the actual sender is reached only after the exact
                # write intent is on disk, not just in an in-memory receipt.
                latest = json.loads((self.root / journal.dirname / journal.records[-1][0]).read_bytes())
                self.assertEqual(latest['receipt']['status'], 'WRITE_PENDING')
                self.assertEqual(latest['receipt']['pending'], '/'.join(core.identity(json.loads(request.body))))
            self.current_request = request
            return sender(request)

        h.authorizer = BoundAuthorizer(verify=verify, claim=claim, journal=journal, wall=lambda: h.now)
        h.adapter.send = send
        h.adapter.checkpoint = journal
        return h, claim, journal

    def socket_for_request(self, *args):
        request = self.current_request
        self.assertIsNotNone(request)
        # The response model is synthetic; the real sender still serializes
        # HTTP, partially writes/reads, parses and validates the response.
        obj = self.h.send(request)
        number = len(self.h.requests)
        status = 409 if number == self.http_failure else 200
        raw = core.encoded({'message': 'SYNTHETIC_PRIVATE_SERVER_DETAIL'} if status != 200 else obj)
        sock = FakeSocket(response(raw, status=status))
        if number == self.read_failure:
            sock.recv_error = TimeoutError('SYNTHETIC_PRIVATE_IO_DETAIL')
        self.sockets.append(sock)
        return sock

    def assert_no_secret_receipts(self):
        for path in self.root.rglob('*.json'):
            raw = path.read_bytes()
            for secret in (TOKEN.encode(), b'SYNTHETIC_PRIVATE_'):
                self.assertNotIn(secret, raw)

    def assert_stopped_fence(self, h, claim, journal):
        self.assertTrue((self.root / claim.active_name).exists())
        with self.assertRaises(store.StoreError):
            claim.complete(journal)
        journal.close()
        claim.close()
        for binding in (h.binding, replace(h.binding, run_id='synthetic-new-run', action='restore')):
            with self.assertRaisesRegex(store.StoreError, 'TARGET_RECONCILIATION_REQUIRED'):
                self.acquire(binding)
        self.assert_no_secret_receipts()

    def test_normal_completion_then_separately_bound_restore_preserves_both_journals(self):
        h, claim, journal = self.connect()
        result = h.run()
        self.assertEqual(result['status'], 'ROLLOUT_OBSERVED', result)
        self.assertEqual(len(self.sockets), 8)
        for sock, request in zip(self.sockets, h.requests):
            self.assertTrue(sock.closed)
            self.assertTrue(bytes(sock.sent).startswith((request.method + ' ' + request.path + ' HTTP/1.1\r\n').encode()))
            self.assertTrue(bytes(sock.sent).endswith(request.body))
        old_records = {p.name: p.read_bytes() for p in (self.root / journal.dirname).iterdir()}
        claim.complete(journal)
        journal.close()
        claim.close()

        restored = Harness()
        docs = json.loads(restored.render)
        images = []
        for obj in docs:
            if obj['kind'] == 'Deployment':
                container = obj['spec']['template']['spec']['containers'][0]
                container['image'] = container['image'].split('@')[0] + '@sha256:' + 'b' * 64
                images.append((container['name'], container['image']))
        restored.render = core.encoded(docs)
        restored.rebind(run_id='synthetic-approved-restore', action='restore',
                        images=tuple(images), rendered_sha256=core.digest(restored.render))
        r, second, second_journal = self.connect(restored)
        outcome = r.run()
        self.assertEqual(outcome['status'], 'ROLLOUT_OBSERVED', outcome)
        self.assertEqual(len(self.sockets), 16)
        self.assertNotEqual(h.binding.key, r.binding.key)
        for request in r.requests[:6]:
            if json.loads(request.body)['kind'] == 'Deployment':
                image = json.loads(request.body)['spec']['template']['spec']['containers'][0]['image']
                self.assertTrue(image.endswith('b' * 64))
        second.complete(second_journal)
        self.assertEqual(old_records, {p.name: p.read_bytes() for p in (self.root / journal.dirname).iterdir()})
        for receipt in (result, outcome):
            self.assertEqual([receipt[k] for k in ('delivery_gate', 'health_e2e', 'pod_image_id')],
                             ['NOT_VERIFIED'] * 3)
        self.assert_no_secret_receipts()

    def test_http_conflict_remains_unknown_without_force_retry_or_fence_release(self):
        self.http_failure = 3
        h, claim, journal = self.connect()
        result = h.run()
        self.assertEqual(result['error_code'], 'WRITE_OUTCOME_UNKNOWN', result)
        self.assertEqual(journal.latest['acknowledged'], list(store.IDENTITIES[:2]))
        self.assertEqual(journal.latest['pending'], store.IDENTITIES[2])
        self.assertEqual(len(self.sockets), 3)
        self.assertTrue(all(s.closed and b'force=' not in s.sent for s in self.sockets))
        self.assert_stopped_fence(h, claim, journal)

    def test_response_timeout_after_request_preserves_exact_pending_and_prior_acks(self):
        self.read_failure = 2
        h, claim, journal = self.connect()
        result = h.run()
        self.assertEqual(result['error_code'], 'WRITE_OUTCOME_UNKNOWN', result)
        self.assertEqual(journal.latest['acknowledged'], list(store.IDENTITIES[:1]))
        self.assertEqual(journal.latest['pending'], store.IDENTITIES[1])
        self.assertEqual(len(self.sockets), 2)
        self.assertTrue(all(s.closed and s.sent for s in self.sockets))
        self.assert_stopped_fence(h, claim, journal)

    def test_authority_loss_after_response_keeps_ack_but_blocks_next_socket(self):
        self.deny_after = 1
        h, claim, journal = self.connect()
        result = h.run()
        self.assertEqual(result['error_code'], 'AUTHORIZATION_DENIED', result)
        self.assertEqual(journal.latest['acknowledged'], list(store.IDENTITIES[:1]))
        self.assertIsNone(journal.latest['pending'])
        self.assertEqual(len(self.sockets), 1)
        self.assert_stopped_fence(h, claim, journal)

    def test_failed_ack_record_keeps_durable_intent_even_when_memory_observed_response(self):
        h, claim, journal = self.connect()
        original = store.create_file
        def fail_ack(parent, name, raw):
            if name == '0003.json':
                original(parent, name, b'{"incomplete":')
                raise OSError('SYNTHETIC_PRIVATE_STORAGE_DETAIL')
            return original(parent, name, raw)
        with patch.object(store, 'create_file', side_effect=fail_ack):
            result = h.run()
        self.assertEqual(result['error_code'], 'CHECKPOINT_FAILED', result)
        self.assertEqual(result['checkpoint_error'], 'CHECKPOINT_FAILED')
        self.assertEqual(result['acknowledged'], list(store.IDENTITIES[:1]))
        self.assertEqual(journal.latest['status'], 'WRITE_PENDING')
        self.assertEqual(journal.latest['pending'], store.IDENTITIES[0])
        self.assertEqual(len(self.sockets), 1)
        self.assertEqual((self.root / journal.dirname / '0003.json').read_bytes(), b'{"incomplete":')
        self.assert_stopped_fence(h, claim, journal)

    def test_injected_exit_after_response_before_ack_leaves_only_durable_pending(self):
        h, claim, journal = self.connect()
        original = h.adapter.save
        def crash_before_ack():
            if h.adapter.receipt['status'] == 'WRITE_ACKNOWLEDGED':
                raise InjectedCrash()
            original()
        h.adapter.save = crash_before_ack
        with self.assertRaises(InjectedCrash):
            h.run()
        self.assertEqual(len(self.sockets), 1)
        self.assertTrue(self.sockets[0].closed)
        self.assertEqual(journal.latest['status'], 'WRITE_PENDING')
        self.assertEqual(journal.latest['pending'], store.IDENTITIES[0])
        self.assertEqual(len(journal.records), 2)
        self.assert_stopped_fence(h, claim, journal)


if __name__ == '__main__':
    unittest.main()
