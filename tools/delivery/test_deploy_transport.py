"""Mock socket/HTTP/deadline tests only; no real TLS handshake or cluster."""
import copy
import hashlib
import json
import os
import ssl
import unittest
from unittest.mock import Mock, patch

import deploy_adapter as core
import deploy_transport as t
from test_deploy_adapter import Fake, fixtures

CA = b'-----BEGIN CERTIFICATE-----\nYWJj\n-----END CERTIFICATE-----\n'


def wire(obj, code=200, extra=b''):
    raw = obj if type(obj) is bytes else json.dumps(obj).encode()
    return (f'HTTP/1.1 {code} TEST\r\nContent-Type: application/json\r\n'
            f'Content-Length: {len(raw)}\r\n'.encode() + extra + b'\r\n' + raw)


class Socket:
    def __init__(self, response=b''):
        self.response, self.sent, self.timeouts = response, [], []
        self.read_hook = self.send_hook = self.handshake_hook = None
        self.closed = False
        self.chunk = 8192
        self.connected = None

    def settimeout(self, value):
        self.timeouts.append(value)

    def connect(self, address):
        self.connected = address

    def do_handshake(self):
        if self.handshake_hook:
            self.handshake_hook()

    def sendall(self, data):
        self.sent.append(data)
        if self.send_hook:
            self.send_hook(data)

    def recv_into(self, buffer):
        if self.read_hook:
            self.read_hook()
        size = min(len(buffer), len(self.response), self.chunk)
        buffer[:size] = self.response[:size]
        self.response = self.response[size:]
        return size

    def close(self):
        self.closed = True


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.targets, self.objects = fixtures()
        self.mono, self.approval, self.credential = 0, 3600, 900
        self.sock = Socket(wire(self.objects['transaction-api']))
        self.context = Mock()
        self.context.wrap_socket.return_value = self.sock
        self.socket_patch = patch.object(t.socket, 'socket', return_value=self.sock)
        self.socket_factory = self.socket_patch.start()
        self.ca_patch = patch.object(t, 'context_checked', return_value=self.context)
        self.ca_patch.start()
        self.addCleanup(self.socket_patch.stop)
        self.addCleanup(self.ca_patch.stop)

    def client(self, **kwargs):
        return t.Client(
            self.targets, CA, hashlib.sha256(CA).hexdigest(),
            kwargs.pop('token_provider', lambda: 'MOCK_PRIVATE_TOKEN'),
            remaining_seconds=lambda: self.approval,
            credential_remaining_seconds=lambda: self.credential,
            clock=lambda: self.mono, **kwargs)

    def image_patch(self):
        return core.image_patch(self.objects['transaction-api'], self.targets[0],
                                self.targets[0].previous_image, self.targets[0].image)

    def test_fixed_ip_exact_path_and_hostname_verified_context(self):
        result = self.client().get('transaction-api')
        self.assertEqual(result, self.objects['transaction-api'])
        self.assertEqual(self.sock.connected, ('10.1.93.109', 6443))
        self.context.wrap_socket.assert_called_once_with(
            self.sock, server_hostname='10.1.93.109', do_handshake_on_connect=False)
        request = self.sock.sent[0]
        self.assertTrue(request.startswith(
            b'GET /apis/apps/v1/namespaces/fds-app/deployments/transaction-api HTTP/1.1\r\n'))
        self.assertIn(b'Connection: close\r\n', request)
        self.assertTrue(self.sock.closed)

    def test_patch_contains_only_five_exact_image_operations(self):
        self.client().patch('transaction-api', self.image_patch())
        head, body = self.sock.sent[0].split(b'\r\n\r\n')
        self.assertIn(b'Content-Type: application/json-patch+json', head)
        self.assertEqual(json.loads(body), self.image_patch())
        self.assertEqual(len(self.sock.sent), 1)

    def test_reverse_image_patch_is_supported_for_independently_authorized_rollback(self):
        obj = copy.deepcopy(self.objects['transaction-api'])
        obj['spec']['template']['spec']['containers'][1]['image'] = self.targets[0].image
        backward = core.image_patch(obj, self.targets[0], self.targets[0].image,
                                    self.targets[0].previous_image)
        self.client().patch('transaction-api', backward)
        self.assertEqual(json.loads(self.sock.sent[0].split(b'\r\n\r\n')[1]), backward)

    def test_extra_path_uid_sidecar_and_unapproved_digest_block_before_network(self):
        for index, field, value in (
                (0, 'value', 'other-uid'), (2, 'value', 'sidecar'),
                (4, 'path', '/spec/replicas'), (4, 'value', 'unapproved:latest'),
                (1, 'value', 'rv\r\nHeader: unsafe')):
            bad = self.image_patch()
            bad[index][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(t.TransportError):
                self.client().patch('transaction-api', bad)
        bad = self.image_patch() + [{'op': 'add', 'path': '/x', 'value': 1}]
        with self.assertRaises(t.TransportError):
            self.client().patch('transaction-api', bad)
        self.assertEqual(self.sock.sent, [])
        self.socket_factory.assert_not_called()

    def test_unknown_target_and_header_injection_do_not_connect(self):
        for name in ('other', '../secrets', 'transaction-api?x=1', 'transaction-api\r\nX: y'):
            with self.subTest(name=name), self.assertRaises(t.TransportError):
                self.client().get(name)
        for token in ('', 'x\ny', 'x y', None):
            with self.subTest(token=token), self.assertRaises(t.TransportError):
                self.client(token_provider=lambda: token).get('transaction-api')
        self.socket_factory.assert_not_called()

    def test_invalid_expired_or_unavailable_windows_do_not_connect(self):
        for value in (0, -1, True, None, float('nan'), float('inf')):
            self.approval = value
            with self.subTest(value=value), self.assertRaises(t.TransportError):
                self.client().get('transaction-api')
        self.approval = 3600
        self.credential = 0
        with self.assertRaises(t.TransportError):
            self.client().get('transaction-api')
        self.socket_factory.assert_not_called()

    def test_slow_drip_headers_cannot_extend_absolute_deadline(self):
        self.sock.chunk = 1
        self.sock.read_hook = lambda: setattr(self, 'mono', self.mono + 1)
        client = self.client(request_timeout=3)
        with self.assertRaisesRegex(t.TransportError, 'REQUEST_DEADLINE_EXCEEDED'):
            client.get('transaction-api')
        self.assertEqual(self.mono, 3)
        self.assertTrue(client.blocked)
        self.assertTrue(all(0 < x <= 3 for x in self.sock.timeouts))

    def test_socket_budget_shrinks_between_reads(self):
        self.sock.chunk = 20
        self.sock.read_hook = lambda: setattr(self, 'mono', self.mono + 0.01)
        self.client(request_timeout=10).get('transaction-api')
        self.assertLess(self.sock.timeouts[-1], self.sock.timeouts[0])

    def test_token_expiry_during_response_blocks_further_requests(self):
        self.sock.read_hook = lambda: setattr(self, 'credential', 0)
        client = self.client()
        with self.assertRaisesRegex(t.TransportError, 'REQUEST_AUTH_EXPIRED'):
            client.get('transaction-api')
        with self.assertRaisesRegex(t.TransportError, 'TRANSPORT_BLOCKED'):
            client.patch('transaction-api', self.image_patch())
        self.assertEqual(len(self.sock.sent), 1)

    def test_timeout_after_patch_send_is_unknown_and_is_never_retried(self):
        self.sock.send_hook = lambda _: (_ for _ in ()).throw(TimeoutError('MOCK_PRIVATE_TOKEN'))
        client = self.client()
        with self.assertRaisesRegex(t.TransportError, '^TRANSPORT_FAILED$'):
            client.patch('transaction-api', self.image_patch())
        with self.assertRaisesRegex(t.TransportError, '^TRANSPORT_BLOCKED$'):
            client.patch('transaction-api', self.image_patch())
        self.assertEqual(len(self.sock.sent), 1)

    def test_certificate_rejection_sends_no_bearer_or_patch(self):
        self.sock.handshake_hook = lambda: (_ for _ in ()).throw(
            ssl.SSLCertVerificationError('MOCK_PRIVATE_TOKEN'))
        with self.assertRaisesRegex(t.TransportError, '^TRANSPORT_FAILED$'):
            self.client().patch('transaction-api', self.image_patch())
        self.assertEqual(self.sock.sent, [])
        self.assertTrue(self.sock.closed)

    def test_only_well_formed_409_and_422_are_definitive_rejections(self):
        for code, reason in ((409, 'Conflict'), (422, 'Invalid')):
            obj = {'apiVersion': 'v1', 'kind': 'Status', 'status': 'Failure',
                   'code': code, 'reason': reason, 'message': 'MOCK_PRIVATE_TOKEN',
                   'details': {'name': 'transaction-api', 'group': 'apps', 'kind': 'deployments'}}
            self.sock.response = wire(obj, code)
            client = self.client()
            with self.subTest(code=code), self.assertRaisesRegex(core.Rejected, '^PATCH_REJECTED$'):
                client.patch('transaction-api', self.image_patch())
            self.assertFalse(client.blocked)

    def test_redirect_auth_loss_and_5xx_permanently_block_even_if_body_claims_rejection(self):
        for code in (301, 307, 400, 401, 403, 404, 429, 500, 503):
            obj = {'apiVersion': 'v1', 'kind': 'Status', 'status': 'Failure', 'code': code}
            self.sock.response = wire(obj, code, b'Location: https://other.example\r\n')
            client = self.client()
            with self.subTest(code=code), self.assertRaisesRegex(
                    t.TransportError, '^HTTP_RESPONSE_UNTRUSTED$'):
                client.patch('transaction-api', self.image_patch())
            self.assertTrue(client.blocked)
        self.assertEqual(len(self.sock.sent), 9)  # No redirect follow or retry.

    def test_malformed_duplicate_truncated_and_oversized_responses_are_unknown(self):
        for raw in (b'{', b'{"kind":"Deployment","kind":"Status"}', b'[]', b'x' * (t.MAX_RESPONSE + 1)):
            self.sock.response = wire(raw)
            with self.subTest(length=len(raw)), self.assertRaises(t.TransportError):
                self.client().patch('transaction-api', self.image_patch())
        self.sock.response = b'HTTP/1.1 200 OK\r\nContent-Length: 999\r\n\r\n{}'
        with self.assertRaises(t.TransportError):
            self.client().patch('transaction-api', self.image_patch())

    def test_wrong_kind_compression_and_malformed_status_are_unknown(self):
        for response in (
                wire({'apiVersion': 'v1', 'kind': 'Status'}),
                wire(self.objects['transaction-api'], extra=b'Content-Encoding: gzip\r\n'),
                wire({'apiVersion': 'v1', 'kind': 'Status', 'status': 'Failure',
                      'code': 409, 'reason': 'Conflict', 'details': {'name': 'other'}}, 409)):
            self.sock.response = response
            with self.assertRaises(t.TransportError):
                self.client().patch('transaction-api', self.image_patch())

    def test_adapter_does_not_auto_rollback_or_retry_an_ambiguous_patch(self):
        fake = Fake(self.objects)
        def respond(data):
            head, _ = data.split(b'\r\n\r\n')
            method, path, _ = head.split(b'\r\n', 1)[0].decode().split(' ')
            if method == 'GET':
                self.sock.response = wire(fake.get(path.rsplit('/', 1)[1]))
            else:
                self.sock.response = wire({'apiVersion': 'v1', 'kind': 'Status'}, 500)
        self.sock.send_hook = respond
        receipts = []
        adapter = core.Adapter(self.client(), lambda _: True, receipts.append,
                               remaining_seconds=lambda: 3600,
                               credential_remaining_seconds=lambda: 900,
                               timeout=3, interval=1, clock=lambda: self.mono)
        result = adapter.run(self.targets, rollback_on_failure=True)
        self.assertEqual(result['error_code'], 'PATCH_OUTCOME_UNKNOWN')
        self.assertEqual(result['ROLLBACK'], 'MANUAL_RECONCILIATION_REQUIRED')
        self.assertEqual(sum(x.startswith(b'PATCH ') for x in self.sock.sent), 1)


class CaTests(unittest.TestCase):
    def test_hash_and_pem_are_required_before_loading(self):
        for raw, digest in ((CA, '0' * 64), (b'PRIVATE KEY', hashlib.sha256(b'PRIVATE KEY').hexdigest())):
            with self.subTest(raw=raw), self.assertRaises(t.TransportError):
                t.context_checked(raw, digest)

    def test_client_context_verifies_chain_hostname_and_does_not_use_env_keylog(self):
        with patch.object(t.ssl, 'SSLContext') as factory, patch.dict(
                os.environ, {'SSLKEYLOGFILE': 'must-not-open'}):
            ctx = t.context_checked(CA, hashlib.sha256(CA).hexdigest())
        factory.assert_called_once_with(ssl.PROTOCOL_TLS_CLIENT)
        self.assertEqual(ctx.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(ctx.check_hostname)
        self.assertEqual(ctx.minimum_version, ssl.TLSVersion.TLSv1_2)
        ctx.load_verify_locations.assert_called_once_with(cadata=CA.decode())

    def test_bad_certificate_loader_errors_are_sanitized(self):
        with patch.object(t.ssl, 'SSLContext') as factory:
            factory.return_value.load_verify_locations.side_effect = ValueError('PRIVATE_TOKEN')
            with self.assertRaisesRegex(t.TransportError, '^CA_LOAD_FAILED$'):
                t.context_checked(CA, hashlib.sha256(CA).hexdigest())
