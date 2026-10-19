"""Real factory/session/transport + synthetic authority/store/TLS sockets only."""
import copy
from dataclasses import replace
import json
import socket
import ssl
import unittest
from unittest.mock import Mock, patch

import delivery_adapter as core
import delivery_transport as transport
from delivery_object_reader import BearerCredential
from delivery_sender_binding import bind_tls_sender
from delivery_verifier import Progress, ReadRequest
from test_delivery_operational import ContractHarness
from test_delivery_transport import TOKEN, FakeSocket, response


class SenderBindingTests(unittest.TestCase):
    def setUp(self):
        self.c = ContractHarness()
        self.credential = BearerCredential(TOKEN)
        self.c.credential = self.c.providers.credential = self.credential
        self.c.providers.hooks['platform'] = lambda r, value: replace(
            value, credential_fingerprint=self.credential.fingerprint)
        self.c.sender = bind_tls_sender
        self.b = self.c.h.binding
        self.request = ReadRequest(self.b, self.credential, Progress(), 1005)
        self.platform = self.c.providers.platform(self.request)
        self.context = Mock()
        self.context.verify_mode = ssl.CERT_REQUIRED
        self.context.check_hostname = True
        self.context.keylog_filename = None
        self.context.wrap_socket.side_effect = lambda sock, **kw: sock
        self.queue, self.sockets = [], []
        for name in ('getaddrinfo', 'create_connection'):
            guard = patch.object(socket, name, side_effect=AssertionError('NETWORK_FORBIDDEN'))
            guard.start()
            self.addCleanup(guard.stop)
        for target, name, kwargs in (
                (socket, 'socket', {'side_effect': self.make_socket}),
                (transport.ssl, 'SSLContext', {'return_value': self.context})):
            guard = patch.object(target, name, **kwargs)
            value = guard.start()
            if name == 'socket':
                self.socket_factory = value
            self.addCleanup(guard.stop)

    def make_socket(self, *args):
        if not self.queue:
            raise AssertionError('UNEXPECTED_SOCKET')
        sock = FakeSocket(self.queue.pop(0))
        self.sockets.append(sock)
        return sock

    def queue_success(self):
        docs = json.loads(self.c.h.render)
        for i, obj in enumerate(docs):
            obj['metadata'].update(uid='synthetic-uid-' + str(i), resourceVersion=str(i + 1))
            if obj['kind'] == 'Deployment':
                obj['metadata']['generation'] = 1
            self.queue.append(response(core.encoded(obj)))
        for obj in docs:
            if obj['kind'] == 'Deployment':
                obj = copy.deepcopy(obj)
                obj['status'] = {'observedGeneration': 1, 'replicas': 3, 'updatedReplicas': 3,
                                 'readyReplicas': 3, 'availableReplicas': 3}
                self.queue.append(response(core.encoded(obj)))

    def assert_safe(self, result):
        self.assertNotIn(TOKEN, json.dumps(result))
        self.assertEqual(result['runtime'], 'NOT_VERIFIED')
        self.assertEqual(result['delivery_gate'], 'NOT_VERIFIED')
        self.assertTrue(all(s.closed for s in self.sockets))

    def test_construction_no_socket_and_close_is_idempotent_and_disables_send(self):
        bound = bind_tls_sender(self.b, self.credential, self.platform)
        self.assertIs(bound.credential, self.credential)
        self.assertEqual(bound.credential_fingerprint, self.credential.fingerprint)
        self.assertEqual(bound.ca_sha256, self.b.ca_sha256)
        self.assertEqual(bound.endpoint_ip, self.platform.endpoint_ip)
        self.assertNotIn(TOKEN, repr(bound))
        bound.close()
        bound.close()
        with self.assertRaisesRegex(transport.TransportError, '^SENDER_CLOSED$'):
            bound.send(None)
        self.socket_factory.assert_not_called()

    def test_mismatched_handle_binding_fingerprint_ca_and_endpoint_stop_before_socket(self):
        other = BearerCredential(TOKEN)  # equal value is NOT the same handle
        for p in (None, {'approved': True},
                  replace(self.platform, binding=replace(self.b, cluster_id='other')),
                  replace(self.platform, request=replace(self.request,
                      binding=replace(self.b, cluster_id='other'))),
                  replace(self.platform, credential=other),
                  replace(self.platform, request=replace(self.request, credential=other)),
                  replace(self.platform, credential_fingerprint='a' * 64),
                  replace(self.platform, ca_pem=b'OTHER_CA'),
                  replace(self.platform, endpoint_ip='127.0.0.1')):
            with self.subTest(platform=type(p).__name__):
                with self.assertRaisesRegex(transport.TransportError, '^SENDER_BINDING_INVALID$'):
                    bind_tls_sender(self.b, self.credential, p)
        self.socket_factory.assert_not_called()

    def test_session_six_tls_patches_two_gets_and_explicit_completion(self):
        self.queue_success()
        result = self.c.run()
        self.assertEqual(result['status'], 'SESSION_COMPLETED', result)
        self.assertEqual(len(self.sockets), 8)
        methods = [bytes(s.sent).split(b' ', 1)[0] for s in self.sockets]
        self.assertEqual(methods, [b'PATCH'] * 6 + [b'GET'] * 2)
        for i, sock in enumerate(self.sockets):
            self.assertEqual(sock.connected, ('192.0.2.10', 6443))
            self.assertIn(('Authorization: Bearer ' + TOKEN + '\r\n').encode(), sock.sent)
            if i < 6:
                path = core.path_for(core.OBJECTS[i]) + '?fieldManager=fds-deployer'
                self.assertTrue(bytes(sock.sent).startswith(('PATCH ' + path + ' HTTP/1.1').encode()))
        self.assertEqual(self.c.storage.complete_calls, 1)
        self.assertIsNone(self.c.storage.active)
        self.assert_safe(result)

    def test_http_denial_on_patch_preserves_unknown_pending_and_no_retry(self):
        self.queue = [response(b'{"message":"SYNTHETIC_PRIVATE_BODY"}', 403)]
        result = self.c.run()
        self.assertEqual(result['receipt']['error_code'], 'WRITE_OUTCOME_UNKNOWN', result)
        self.assertIsNotNone(result['receipt']['pending'])
        self.assertEqual(len(self.sockets), 1)
        self.assertEqual(self.c.storage.complete_calls, 0)
        self.assertIsNotNone(self.c.storage.active)
        self.assertNotIn('SYNTHETIC_PRIVATE_BODY', json.dumps(result))
        self.assert_safe(result)

    def test_ack_failure_after_tls_success_stops_next_patch_and_keeps_fence(self):
        self.queue_success()
        self.c.storage.fail.add('ack')
        result = self.c.run()
        self.assertEqual(result['status'], 'STOPPED', result)
        self.assertEqual(len(self.sockets), 1)
        self.assertEqual(self.c.storage.complete_calls, 0)
        self.assertIsNotNone(self.c.storage.active)
        self.assertEqual(self.c.storage.journals[0].latest['status'], 'WRITE_PENDING')
        self.assert_safe(result)

    def test_withdrawal_after_first_patch_blocks_next_network_request(self):
        self.queue_success()
        self.c.providers.hooks['approval'] = lambda r, value: replace(
            value, state='WITHDRAWN') if self.sockets else value
        result = self.c.run()
        self.assertEqual(result['status'], 'STOPPED', result)
        self.assertEqual(len(self.sockets), 1)
        self.assertEqual(self.c.storage.complete_calls, 0)
        self.assertIsNotNone(self.c.storage.active)
        self.assert_safe(result)

    def test_factory_failure_after_claim_keeps_records_and_closes_handles(self):
        self.context.load_verify_locations.side_effect = ValueError(TOKEN)
        result = self.c.run()
        self.assertEqual(result['error_code'], 'SENDER_INITIALIZATION_FAILED', result)
        self.socket_factory.assert_not_called()
        self.assertIsNotNone(self.c.storage.active)
        self.assertTrue(self.c.storage.claims[0].closed)
        self.assertTrue(self.c.storage.journals[0].closed)
        self.assertEqual(self.c.storage.complete_calls, 0)
        self.assert_safe(result)


if __name__ == '__main__':
    unittest.main()
