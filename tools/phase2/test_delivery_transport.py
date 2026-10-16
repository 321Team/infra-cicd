"""Offline injected-socket tests. No network, actual credential, or ROSA proof."""
import errno
import json
import os
import ssl
import unittest
from dataclasses import replace
from unittest.mock import Mock, patch

import delivery_adapter as core
import delivery_transport as transport
from test_delivery_adapter import fixture

TOKEN = 'synthetic.token.for.offline.test'
CA = b'SYNTHETIC_CA_NOT_VALID_PEM'


def response(body=b'{"ok":true}', status=200, headers=None):
    fields = headers if headers is not None else [
        ('Content-Type', 'application/json'), ('Content-Length', str(len(body)))]
    return (f'HTTP/1.1 {status} Synthetic\r\n' + ''.join(
        f'{k}: {v}\r\n' for k, v in fields) + '\r\n').encode() + body


class FakeSocket:
    def __init__(self, incoming=None):
        self.incoming = response() if incoming is None else incoming
        self.sent, self.closed, self.connected = bytearray(), False, None
        self.connect_code = 0
        self.send_error = self.handshake_error = self.recv_error = None

    def setblocking(self, value):
        assert value is False

    def connect_ex(self, address):
        self.connected = address
        return self.connect_code

    def getsockopt(self, *args):
        return 0

    def do_handshake(self):
        if self.handshake_error:
            raise self.handshake_error

    def selected_alpn_protocol(self):
        return 'http/1.1'

    def send(self, raw):
        if self.send_error:
            raise self.send_error
        # Partial socket writes must be completed without a second HTTP request.
        amount = min(len(raw), 17)
        self.sent.extend(raw[:amount])
        return amount

    def recv(self, length):
        if self.recv_error:
            raise self.recv_error
        raw, self.incoming = self.incoming[:min(length, 13)], self.incoming[min(length, 13):]
        return raw

    def close(self):
        self.closed = True


class TransportTests(unittest.TestCase):
    def setUp(self):
        binding, render, contract = fixture()
        self.binding = replace(binding, ca_sha256=core.digest(CA))
        self.request = core.Request(self.binding.key, core.digest(TOKEN.encode()),
                                    self.binding.api_origin, self.binding.ca_sha256, 'PATCH',
                                    core.path_for(core.OBJECTS[0]) + '?fieldManager=fds-deployer',
                                    'application/apply-patch+yaml', core.encoded(json.loads(render)[0]), 10)
        self.sock = FakeSocket()
        self.context = Mock()
        self.context.verify_mode = ssl.CERT_REQUIRED
        self.context.check_hostname = True
        self.context.keylog_filename = None
        self.context.wrap_socket.return_value = self.sock

    def sender(self, **changes):
        args = dict(ca_pem=CA, token=TOKEN, endpoint_ip='192.0.2.10')
        args.update(changes)
        with patch.object(transport.ssl, 'SSLContext', return_value=self.context):
            return transport.Sender(self.binding, **args)

    def send(self, sender=None, request=None):
        sender = sender or self.sender()
        with patch.object(transport.socket, 'socket', return_value=self.sock) as factory:
            result = sender(self.request if request is None else request)
        self.assertEqual(factory.call_count, 1)
        return result

    def test_exact_origin_sni_ca_credential_and_partial_writes(self):
        self.assertEqual(self.send(), {'ok': True})
        self.assertEqual(self.sock.connected, ('192.0.2.10', 6443))
        self.context.load_verify_locations.assert_called_once_with(cadata=CA.decode())
        self.context.wrap_socket.assert_called_once_with(
            self.sock, server_hostname='api.rosa.example.com', do_handshake_on_connect=False)
        wire = bytes(self.sock.sent)
        self.assertEqual(wire.count(b'PATCH '), 1)
        self.assertIn(b'Host: api.rosa.example.com:6443\r\n', wire)
        self.assertIn(('Authorization: Bearer ' + TOKEN + '\r\n').encode(), wire)
        self.assertTrue(wire.endswith(self.request.body))
        self.assertTrue(self.sock.closed)

    def test_get_only_scoped_deployment_without_request_body(self):
        request = replace(self.request, method='GET', path=core.path_for(core.OBJECTS[2]),
                          body=b'', content_type='')
        self.assertEqual(self.send(request=request), {'ok': True})
        self.assertNotIn(b'Content-Type:', self.sock.sent)

    def test_native_context_does_not_load_system_trust_or_enable_key_logging(self):
        with patch.dict(os.environ, {'SSLKEYLOGFILE': 'must-not-be-created',
                                     'HTTPS_PROXY': 'https://proxy.invalid'}), \
                patch.object(ssl.SSLContext, 'load_verify_locations') as load:
            sender = transport.Sender(self.binding, ca_pem=CA, token=TOKEN, endpoint_ip='192.0.2.10')
        self.assertEqual(sender._context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(sender._context.check_hostname)
        self.assertIsNone(sender._context.keylog_filename)
        self.assertEqual(sender._context.cert_store_stats()['x509_ca'], 0)
        load.assert_called_once_with(cadata=CA.decode())
        self.assertNotIn(TOKEN, repr(sender))

    def test_configuration_rejects_bad_ca_token_and_non_numeric_or_unsafe_endpoint(self):
        for changes in ({'ca_pem': CA + b'!'}, {'token': 'bad\r\nHeader: x'},
                        {'token': ''}, {'endpoint_ip': 'api.rosa.example.com'},
                        {'endpoint_ip': '127.0.0.1'}, {'endpoint_ip': '::1'},
                        {'endpoint_ip': '::ffff:127.0.0.1'},
                        {'endpoint_ip': '169.254.169.254'}, {'endpoint_ip': 'fe80::1%eth0'},
                        {'endpoint_ip': '0.0.0.0'}, {'endpoint_ip': '224.0.0.1'}):
            with self.subTest(changes=list(changes)):
                with self.assertRaisesRegex(transport.TransportError, 'TRANSPORT_CONFIGURATION_INVALID'):
                    self.sender(**changes)

    def test_ipv6_endpoint_has_no_dns_lookup(self):
        with patch.object(transport.socket, 'getaddrinfo', side_effect=AssertionError('DNS forbidden')):
            self.send(self.sender(endpoint_ip='2001:db8::10'))
        self.assertEqual(self.sock.connected, ('2001:db8::10', 6443, 0, 0))

    def test_changed_request_identity_is_rejected_before_opening_socket(self):
        sender = self.sender()
        for changes in ({'binding_key': '0' * 64}, {'credential_fingerprint': '0' * 64},
                        {'api_origin': 'https://other.example.com'}, {'ca_sha256': '0' * 64}):
            with self.subTest(field=list(changes)), patch.object(transport.socket, 'socket') as factory:
                with self.assertRaises(transport.TransportError):
                    sender(replace(self.request, **changes))
                factory.assert_not_called()

    def test_methods_path_query_payload_and_timeout_are_enforced_before_socket(self):
        sender = self.sender()
        for changes in ({'method': 'DELETE'}, {'path': self.request.path + '&force=true'},
                        {'path': '/api/v1/namespaces/kube-system/secrets/x'},
                        {'method': 'GET', 'body': b'', 'content_type': ''},
                        {'body': self.request.body + b' '}, {'body': b'{"kind":"Secret"}'},
                        {'content_type': 'application/json\r\nX: x'},
                        {'timeout_seconds': 0}, {'timeout_seconds': 11},
                        {'timeout_seconds': float('nan')}):
            with self.subTest(field=list(changes)), patch.object(transport.socket, 'socket') as factory:
                with self.assertRaises(transport.TransportError):
                    sender(replace(self.request, **changes))
                factory.assert_not_called()

    def test_modified_tls_policy_is_rejected_before_connect(self):
        for field, value in (('check_hostname', False), ('verify_mode', ssl.CERT_NONE),
                             ('keylog_filename', 'forbidden')):
            sender = self.sender()
            old = getattr(self.context, field)
            setattr(self.context, field, value)
            with patch.object(transport.socket, 'socket') as factory:
                with self.assertRaises(transport.TransportError):
                    sender(self.request)
                factory.assert_not_called()
            setattr(self.context, field, old)

    def test_certificate_failure_sends_no_credential_and_closes_connection(self):
        self.sock.handshake_error = ssl.SSLCertVerificationError('sensitive details')
        with self.assertRaisesRegex(transport.TransportError, '^TRANSPORT_FAILED$'):
            self.send()
        self.assertEqual(self.sock.sent, b'')
        self.assertTrue(self.sock.closed)

    def test_no_redirect_retry_or_definite_rejection_for_http_errors(self):
        for status in (301, 307, 401, 403, 409, 422, 429, 500, 503):
            self.sock = FakeSocket(response(status=status, headers=[('Location', 'https://other.example.com')]))
            self.context.wrap_socket.return_value = self.sock
            with self.subTest(status=status), self.assertRaises(transport.TransportError):
                self.send()
            self.assertEqual(self.sock.sent.count(b'PATCH '), 1)
            self.assertTrue(self.sock.closed)

    def test_chunked_response_is_decoded(self):
        raw = b'{"ok":true}'
        self.sock.incoming = response(body=b'b\r\n' + raw + b'\r\n0\r\n\r\n', headers=[
            ('Content-Type', 'application/json'), ('Transfer-Encoding', 'chunked')])
        self.assertEqual(self.send(), {'ok': True})

    def test_ambiguous_compressed_or_wrong_type_headers_stop(self):
        for extra in ([('Content-Length', '11'), ('Content-Length', '11')],
                      [('Content-Length', '11'), ('Transfer-Encoding', 'chunked')],
                      [('Content-Encoding', 'gzip')], [('Transfer-Encoding', 'gzip')],
                      [('Content-Type', 'text/html')], [('Content-Length', '-1')],
                      [('Content-Length', str(transport.MAX_BODY + 1))]):
            with self.subTest(headers=extra):
                self.sock = FakeSocket(response(headers=[('Content-Type', 'application/json')] + extra))
                self.context.wrap_socket.return_value = self.sock
                with self.assertRaises(transport.TransportError):
                    self.send()

    def test_truncated_invalid_duplicate_or_nonobject_json_stops(self):
        for raw in (b'{"ok":true', b'{"ok":true,"ok":false}', b'{"ok":NaN}', b'{"ok":1e400}', b'[]', b'\xff'):
            self.sock = FakeSocket(response(raw))
            self.context.wrap_socket.return_value = self.sock
            with self.subTest(raw=raw), self.assertRaises(transport.TransportError):
                self.send()
        self.sock = FakeSocket(response(b'{}', headers=[('Content-Type', 'application/json'), ('Content-Length', '100')]))
        self.context.wrap_socket.return_value = self.sock
        with self.assertRaises(transport.TransportError):
            self.send()

    def test_socket_failures_close_and_do_not_leak_exception_body(self):
        for field in ('send_error', 'recv_error'):
            self.sock = FakeSocket()
            self.context.wrap_socket.return_value = self.sock
            setattr(self.sock, field, OSError(TOKEN))
            with self.assertRaisesRegex(transport.TransportError, '^TRANSPORT_FAILED$') as error:
                self.send()
            self.assertNotIn(TOKEN, str(error.exception))
            self.assertTrue(self.sock.closed)

    def test_connect_in_progress_waits_with_same_deadline_and_never_retries_address(self):
        self.sock.connect_code = errno.EINPROGRESS
        with patch.object(transport, 'wait_ready') as wait:
            self.send()
        self.assertEqual(wait.call_count, 1)
        self.assertEqual(wait.call_args.args[1], transport.selectors.EVENT_WRITE)

    def test_absolute_deadline_not_reset_by_successful_incremental_progress(self):
        now = [0.0]
        deadline = transport.Deadline(10, clock=lambda: now[0])
        for time_value in (1, 3, 9.9):
            now[0] = time_value
            self.assertEqual(transport.tls_call(self.sock, lambda: 'ok', deadline), 'ok')
        now[0] = 10
        with self.assertRaisesRegex(transport.TransportError, 'DEADLINE_EXCEEDED'):
            transport.tls_call(self.sock, lambda: 'too late', deadline)

    def test_ssl_want_read_write_use_one_deadline(self):
        deadline = transport.Deadline(10)
        operation = Mock(side_effect=[ssl.SSLWantReadError(), ssl.SSLWantWriteError(), 'ok'])
        with patch.object(transport, 'wait_ready') as wait:
            self.assertEqual(transport.tls_call(self.sock, operation, deadline), 'ok')
        self.assertEqual([c.args[1] for c in wait.call_args_list],
                         [transport.selectors.EVENT_READ, transport.selectors.EVENT_WRITE])
        self.assertTrue(all(c.args[2] is deadline for c in wait.call_args_list))

    def test_deadline_also_checked_after_operation_and_clock_regression(self):
        for final, expected in ((10, 'DEADLINE_EXCEEDED'), (-1, 'CLOCK_INVALID')):
            now = [0]
            deadline = transport.Deadline(10, clock=lambda: now[0])
            def operation():
                now[0] = final
            with self.assertRaisesRegex(transport.TransportError, expected):
                transport.tls_call(self.sock, operation, deadline)

    def test_wire_size_cap_includes_header_and_chunk_overhead(self):
        stream = transport.ResponseStream(self.sock, transport.Deadline(10))
        stream.total = transport.MAX_WIRE
        with self.assertRaisesRegex(transport.TransportError, 'RESPONSE_TOO_LARGE'):
            stream.readinto(bytearray(10))


if __name__ == '__main__':
    unittest.main()
