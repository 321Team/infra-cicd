"""Synthetic TLS sockets only; no token issuance, DNS or live API access."""
import copy
from dataclasses import replace
import json
import socket
import ssl
import unittest
from unittest.mock import Mock, patch

import delivery_adapter as core
import delivery_transport as transport
from delivery_object_reader import BearerCredential, ObjectProvider, ObjectReader
from delivery_objects import ObjectReconciler
from delivery_verifier import Progress, ReadRequest, VerificationError
from test_delivery_adapter import fixture, validator
from test_delivery_objects import owned
from test_delivery_operational import ContractHarness, CA_RAW
from test_delivery_transport import CA, TOKEN, FakeSocket, response


def absent(key):
    group = key[0].split('/')[0] if '/' in key[0] else ''
    return {'apiVersion': 'v1', 'kind': 'Status', 'status': 'Failure', 'reason': 'NotFound',
            'code': 404, 'details': {'name': key[2], 'group': group,
            'kind': {'Service': 'services', 'Deployment': 'deployments', 'Route': 'routes'}[key[1]]}}


class ReaderTests(unittest.TestCase):
    def setUp(self):
        b, self.render, self.contract = fixture()
        self.b = replace(b, ca_sha256=core.digest(CA))
        self.docs = tuple(owned(o, i) for i, o in enumerate(json.loads(self.render)))
        self.raws = tuple(core.encoded(o) for o in self.docs)
        self.clock = 100.0
        self.credential = BearerCredential(TOKEN)
        self.progress = Progress()
        self.request = ReadRequest(self.b, self.credential, self.progress, 110.0)
        self.sockets = []
        self.queue = []
        self.context = Mock()
        self.context.verify_mode = ssl.CERT_REQUIRED
        self.context.check_hostname = True
        self.context.keylog_filename = None
        self.context.wrap_socket.side_effect = lambda sock, **kw: sock
        for name in ('getaddrinfo', 'create_connection'):
            guard = patch.object(socket, name, side_effect=AssertionError('NETWORK_FORBIDDEN'))
            guard.start()
            self.addCleanup(guard.stop)
        factory = patch.object(socket, 'socket', side_effect=self.make_socket)
        self.socket_factory = factory.start()
        self.addCleanup(factory.stop)
        with patch.object(transport.ssl, 'SSLContext', return_value=self.context):
            self.reader = ObjectReader(self.b, credential=self.credential, ca_pem=CA,
                                       endpoint_ip='192.0.2.10', monotonic=lambda: self.clock)

    def make_socket(self, *args):
        if not self.queue:
            raise AssertionError('UNEXPECTED_SOCKET')
        item = self.queue.pop(0)
        sock = item if isinstance(item, FakeSocket) else FakeSocket(item)
        self.sockets.append(sock)
        return sock

    def provider(self, baseline=None):
        baseline = (None,) * 6 if baseline is None else baseline
        return ObjectProvider(self.reader, ObjectReconciler(
            self.b, self.render, self.contract, validator, baseline), lambda: self.progress)

    def queue_absence(self):
        self.queue = [response(core.encoded(absent(k)), 404) for k in core.OBJECTS]

    def queue_objects(self):
        self.queue = [response(raw) for raw in self.raws]

    def test_all_six_exact_gets_same_ca_sni_token_and_raw_absence(self):
        self.queue_absence()
        provider = self.provider()
        result = provider.objects(self.request)
        self.assertIs(result.request, self.request)
        self.assertEqual(result.progress_key, self.progress.key)
        self.assertEqual(len(provider.last_observations), 6)
        for key, observation, sock in zip(core.OBJECTS, provider.last_observations, self.sockets):
            self.assertEqual(observation.raw, core.encoded(absent(key)))
            self.assertIsNone(observation.object_raw)
            self.assertEqual(sock.connected, ('192.0.2.10', 6443))
            self.assertTrue(sock.closed)
            self.assertTrue(bytes(sock.sent).startswith(('GET ' + core.path_for(key) + ' HTTP/1.1\r\n').encode()))
            self.assertIn(('Authorization: Bearer ' + TOKEN).encode(), sock.sent)
            self.assertNotIn(b'PATCH', sock.sent)
        self.assertTrue(all(c.kwargs['server_hostname'] == 'api.rosa.example.com'
                            for c in self.context.wrap_socket.call_args_list))
        self.assertNotIn(TOKEN, repr(self.credential) + repr(self.reader) + repr(provider.last_observations))

    def test_existing_admitted_baseline_is_compared_with_raw_get_results(self):
        self.queue_objects()
        provider = self.provider(self.raws)
        self.assertEqual(provider.objects(self.request).progress_key, self.progress.key)
        self.assertEqual(tuple(o.object_raw for o in provider.last_observations), self.raws)

    def test_baseline_is_not_automatically_captured_from_fresh_objects(self):
        self.queue_objects()
        provider = self.provider()
        with self.assertRaisesRegex(VerificationError, 'OBJECT_DRIFT'):
            provider.objects(self.request)
        self.assertEqual(provider.last_observations, ())

    def test_acknowledged_objects_use_existing_durable_ack_rules(self):
        self.queue_objects()
        journal = {'schema': 'P2-CICD-apply-core-1', 'status': 'WRITE_ACKNOWLEDGED',
                   'binding_key': self.b.key, 'acknowledged': ['/'.join(k) for k in core.OBJECTS],
                   'pending': None, 'rollout': [], 'error_code': None, 'delivery_gate': 'NOT_VERIFIED',
                   'health_e2e': 'NOT_VERIFIED', 'pod_image_id': 'NOT_VERIFIED'}
        self.progress = Progress(core.encoded(journal), self.raws)
        req = replace(self.request, progress=self.progress)
        self.assertEqual(self.provider().objects(req).progress_key, self.progress.key)

    def test_missing_admitted_baseline_object_is_drift_not_success(self):
        self.queue_absence()
        with self.assertRaisesRegex(VerificationError, 'OBJECT_DRIFT'):
            self.provider(self.raws).objects(self.request)

    def test_tls_failure_returns_no_absence_or_credential_and_closes(self):
        sock = FakeSocket()
        sock.handshake_error = ssl.SSLCertVerificationError('SECRET')
        self.queue = [sock]
        with self.assertRaisesRegex(VerificationError, '^TLS_CERTIFICATE_FAILED$'):
            self.provider().objects(self.request)
        self.assertEqual(sock.sent, b'')
        self.assertTrue(sock.closed)

    def test_403_401_redirect_429_5xx_and_201_are_not_absence_and_do_not_retry(self):
        for status in (201, 301, 307, 401, 403, 429, 500, 503):
            with self.subTest(status=status):
                self.queue = [response(core.encoded(absent(core.OBJECTS[0])), status)]
                before = len(self.sockets)
                code = {401: 'HTTP_UNAUTHORIZED', 403: 'HTTP_FORBIDDEN',
                        429: 'HTTP_RATE_LIMITED'}.get(status, 'HTTP_NOT_ACCEPTED')
                with self.assertRaisesRegex(VerificationError, '^' + code + '$'):
                    self.provider().objects(self.request)
                self.assertEqual(len(self.sockets), before + 1)
                self.assertTrue(self.sockets[-1].closed)

    def test_404_requires_exact_status_resource_group_name_and_numeric_code(self):
        for change in (lambda o: o.update(kind='Service'), lambda o: o.update(apiVersion='v2'),
                       lambda o: o.update(code='404'), lambda o: o.update(status='Success'),
                       lambda o: o.update(reason='Forbidden'), lambda o: o.pop('details'),
                       lambda o: o['details'].pop('name'),
                       lambda o: o['details'].update(name='other'),
                       lambda o: o['details'].update(kind='Service'),
                       lambda o: o['details'].update(group='apps')):
            obj = absent(core.OBJECTS[0])
            change(obj)
            self.queue = [response(core.encoded(obj), 404)]
            with self.assertRaisesRegex(VerificationError, '^OBJECT_ABSENCE_UNCONFIRMED$'):
                self.reader.read(self.request, core.OBJECTS[0])

    def test_core_resource_empty_group_may_be_omitted_by_api(self):
        obj = absent(core.OBJECTS[0])
        del obj['details']['group']
        self.queue = [response(core.encoded(obj), 404)]
        self.assertIsNone(self.reader.read(self.request, core.OBJECTS[0]).object_raw)

    def test_named_group_404_cannot_omit_group(self):
        obj = absent(core.OBJECTS[2])
        del obj['details']['group']
        self.queue = [response(core.encoded(obj), 404)]
        with self.assertRaises(VerificationError):
            self.reader.read(self.request, core.OBJECTS[2])

    def test_200_status_wrong_identity_namespace_and_duplicate_json_stop(self):
        wrong = copy.deepcopy(self.docs[0])
        wrong['metadata']['namespace'] = 'rosa-app'
        for raw in (core.encoded(absent(core.OBJECTS[0])), core.encoded(self.docs[1]),
                    core.encoded(wrong), b'{"kind":"Service","kind":"Status"}',
                    b'{"value":1e400}', b'[]'):
            self.queue = [response(raw)]
            with self.assertRaises(VerificationError):
                self.reader.read(self.request, core.OBJECTS[0])

    def test_partial_body_and_non_json_404_stop_without_false_absence(self):
        for raw in (response(b'{}', 404, [('Content-Type','text/html')]),
                    response(b'{}', 404, [('Content-Type','application/json'), ('Content-Length','30')]),
                    response(b'{"reason":"NotFound"}', 404)):
            self.queue = [raw]
            with self.assertRaises(VerificationError):
                self.reader.read(self.request, core.OBJECTS[0])

    def test_different_credential_same_token_still_fails_before_socket(self):
        self.queue_absence()  # A missing fake response must not cause a false PASS.
        with self.assertRaisesRegex(VerificationError, '^OBJECT_READ_BINDING_INVALID$'):
            self.provider().objects(replace(self.request, credential=BearerCredential(TOKEN)))
        self.socket_factory.assert_not_called()

    def test_other_binding_or_scope_fails_before_socket(self):
        with self.assertRaises(VerificationError):
            self.reader.read(replace(self.request, binding=replace(self.b, run_id='other')), core.OBJECTS[0])
        with self.assertRaises(VerificationError):
            self.reader.read(self.request, ('v1', 'Secret', 'x'))
        self.assertFalse(self.sockets)

    def test_patch_list_query_subresource_cannot_use_reader_exchange(self):
        for method, path in [('PATCH', core.path_for(core.OBJECTS[0])),
                             ('GET', '/api/v1/namespaces/fds-app/services'),
                             ('GET', core.path_for(core.OBJECTS[0]) + '?watch=true'),
                             ('GET', core.path_for(core.OBJECTS[2]) + '/status')]:
            req = core.Request(self.b.key, self.credential.fingerprint, self.b.api_origin,
                               self.b.ca_sha256, method, path, '', b'', 10)
            with self.assertRaises(VerificationError):
                self.reader(req)
            with self.assertRaises(transport.TransportError):
                self.reader._exchange(req, transport.Deadline(10, lambda: self.clock))
        self.assertFalse(self.sockets)

    def test_expired_deadline_does_not_connect(self):
        for deadline in (100, 99, float('nan'), float('inf')):
            with self.assertRaises(VerificationError):
                self.provider().objects(replace(self.request, deadline=deadline))
        self.assertFalse(self.sockets)

    def test_shared_deadline_cannot_be_reset_for_each_object(self):
        self.queue_absence()
        original = self.context.wrap_socket.side_effect
        def wrap(sock, **kw):
            self.clock += 4
            return original(sock, **kw)
        self.context.wrap_socket.side_effect = wrap
        provider = self.provider()
        with self.assertRaisesRegex(VerificationError, '^DEADLINE_EXCEEDED$'):
            provider.objects(self.request)
        self.assertEqual(len(self.sockets), 3)
        self.assertEqual(provider.last_observations, ())
        self.assertTrue(all(s.closed for s in self.sockets))

    def test_clock_reversal_stops_and_does_not_connect_again(self):
        self.queue_absence()
        self.reader.read(self.request, core.OBJECTS[0])
        self.clock = 99
        with self.assertRaises(VerificationError):
            self.reader.read(self.request, core.OBJECTS[1])
        self.assertEqual(len(self.sockets), 1)

    def test_progress_change_during_get_stops_before_next_get(self):
        self.queue_absence()
        def wrap(sock, **kw):
            self.progress = Progress(b'changed')
            return sock
        self.context.wrap_socket.side_effect = wrap
        with self.assertRaisesRegex(VerificationError, 'OBJECT_PROGRESS_CHANGED'):
            self.provider().objects(self.request)
        self.assertEqual(len(self.sockets), 1)

    def test_last_get_failure_cannot_return_partial_evidence(self):
        self.queue_absence()
        self.queue[-1] = response(b'{}', 503)
        provider = self.provider()
        with self.assertRaises(VerificationError):
            provider.objects(self.request)
        self.assertEqual(len(self.sockets), 6)
        self.assertEqual(provider.last_observations, ())

    def test_provider_callback_exception_is_sanitized(self):
        provider = self.provider()
        provider.progress = Mock(side_effect=RuntimeError('SECRET'))
        with self.assertRaisesRegex(VerificationError, '^OBJECT_PROVIDER_FAILED$'):
            provider.objects(self.request)
        self.assertFalse(self.sockets)

    def test_socket_wait_uses_absolute_deadline_after_setup_time(self):
        # Two seconds elapse between the relative budget calculation and
        # Deadline construction. Removing the absolute clamp would wait 5s,
        # not the remaining 3s, despite rejecting the late reply afterwards.
        ticks = iter((100.0, 100.0))
        def clock():
            value = next(ticks, None)
            return self.clock if value is None else value
        self.clock = 102.0
        self.reader.monotonic = clock
        sock = FakeSocket()
        sock.handshake_error = ssl.SSLWantReadError()
        self.queue = [sock]
        selector = Mock()
        selector.__enter__ = Mock(return_value=selector)
        selector.__exit__ = Mock(return_value=False)
        def select(timeout):
            self.assertEqual(timeout, 3.0)
            self.clock = 105.0
            return []
        selector.select.side_effect = select
        with patch.object(transport.selectors, 'DefaultSelector', return_value=selector):
            with self.assertRaisesRegex(VerificationError, '^DEADLINE_EXCEEDED$'):
                self.reader.read(replace(self.request, deadline=105.0), core.OBJECTS[0])
        selector.select.assert_called_once_with(3.0)
        self.assertEqual(sock.sent, b'')
        self.assertTrue(sock.closed)

    def test_last_read_deadline_retains_partial_diagnostics_not_evidence(self):
        self.queue_absence()
        def wrap(sock, **kw):
            if len(self.sockets) == 6:
                self.clock = self.request.deadline
            return sock
        self.context.wrap_socket.side_effect = wrap
        provider = self.provider()
        with self.assertRaisesRegex(VerificationError, '^DEADLINE_EXCEEDED$'):
            provider.objects(self.request)
        self.assertEqual(len(self.sockets), 6)
        self.assertEqual(provider.last_observations, ())
        diagnostic = provider.failure_diagnostics
        self.assertEqual(diagnostic.code, 'DEADLINE_EXCEEDED')
        self.assertEqual(diagnostic.failed_key, core.OBJECTS[-1])
        self.assertEqual(len(diagnostic.observations), 5)
        self.assertFalse(diagnostic.execution_authorized)
        self.assertEqual(diagnostic.purpose, 'DIAGNOSTIC_ONLY')

    def test_named_group_confusion_is_not_absence(self):
        obj = absent(core.OBJECTS[2])
        obj['details']['group'] = 'route.openshift.io'
        self.queue = [response(core.encoded(obj), 404)]
        with self.assertRaisesRegex(VerificationError, '^OBJECT_ABSENCE_UNCONFIRMED$'):
            self.reader.read(self.request, core.OBJECTS[2])

    def test_drift_retains_six_private_raws_without_promoting_evidence(self):
        self.queue_objects()
        provider = self.provider()
        with self.assertRaisesRegex(VerificationError, '^OBJECT_DRIFT$'):
            provider.objects(self.request)
        diagnostic = provider.failure_diagnostics
        self.assertEqual(tuple(o.raw for o in diagnostic.observations), self.raws)
        self.assertIsNone(diagnostic.failed_key)
        self.assertEqual(provider.last_observations, ())
        self.assertNotIn('synthetic-uid', repr(diagnostic))
        self.assertFalse(diagnostic.execution_authorized)

    def test_diagnostics_reset_per_attempt_and_invalid_body_is_not_retained(self):
        provider = self.provider()
        self.queue_objects()
        with self.assertRaises(VerificationError):
            provider.objects(self.request)
        self.queue = [response(b'SECRET_INVALID_BODY', 403)]
        with self.assertRaisesRegex(VerificationError, '^HTTP_FORBIDDEN$'):
            provider.objects(self.request)
        self.assertEqual(provider.failure_diagnostics.observations, ())
        self.assertNotIn('SECRET', repr(provider.failure_diagnostics))
        self.queue_absence()
        provider.objects(self.request)
        self.assertIsNone(provider.failure_diagnostics)
        self.assertEqual(len(provider.last_observations), 6)

    def test_arbitrary_verification_error_text_is_not_a_safe_code(self):
        provider = self.provider()
        provider.progress = Mock(side_effect=VerificationError('SECRET_TOKEN_UPPERCASE'))
        with self.assertRaisesRegex(VerificationError, '^OBJECT_PROVIDER_FAILED$'):
            provider.objects(self.request)
        self.assertNotIn('SECRET', repr(provider.failure_diagnostics))
        self.assertEqual(provider.failure_diagnostics.observations, ())

    def test_read_transport_preserves_known_codes_but_not_arbitrary_text(self):
        for error, expected in ((TimeoutError(TOKEN), 'IO_TIMEOUT'),
                                (OSError(TOKEN), 'OBJECT_READ_FAILED'),
                                (transport.TransportError(TOKEN), 'OBJECT_READ_FAILED')):
            sock = FakeSocket()
            sock.recv_error = error
            self.queue = [sock]
            with self.assertRaisesRegex(VerificationError, '^' + expected + '$'):
                self.reader.read(self.request, core.OBJECTS[0])
            self.assertTrue(sock.closed)

    def connected_session(self, fail_after_first=False):
        c = ContractHarness()
        c.credential = self.credential
        c.providers.credential = self.credential
        c.providers.hooks['platform'] = lambda req, p: replace(p,
            credential_fingerprint=self.credential.fingerprint)
        with patch.object(transport.ssl, 'SSLContext', return_value=self.context):
            reader = ObjectReader(c.h.binding, credential=self.credential, ca_pem=CA_RAW,
                endpoint_ip='192.0.2.10', monotonic=lambda: c.h.mono)
        replies = []
        def progress():
            latest = c.storage.journals[-1].latest if c.storage.journals else None
            return Progress(None if latest is None else core.encoded(latest), tuple(replies))
        reconciler = ObjectReconciler(c.h.binding, c.h.render, c.h.contract, validator, (None,) * 6)
        provider = ObjectProvider(reader, reconciler, progress)
        original = c.h.send
        def send(req):
            obj = original(req)
            if req.method == 'PATCH':
                key = core.identity(obj)
                obj = owned(obj, core.OBJECTS.index(key))
                c.h.objects[core.path_for(key)] = copy.deepcopy(obj)
                replies.append(core.encoded(obj))
            return obj
        c.h.send = send
        def objects(req):
            self.assertFalse(self.queue)
            for key in core.OBJECTS:
                obj = c.h.objects.get(core.path_for(key))
                self.queue.append(response(core.encoded(obj if obj is not None else absent(key)),
                                           200 if obj is not None else 404))
            if fail_after_first and replies:
                self.queue[0] = response(b'{}', 403)
            return provider.objects(req)
        c.providers.objects = objects
        return c, provider

    def test_reader_integrates_session_six_acknowledgements_and_completion(self):
        c, provider = self.connected_session()
        result = c.run()
        self.assertEqual(result['status'], 'SESSION_COMPLETED', result)
        self.assertEqual([r.method for r in c.h.requests], ['PATCH'] * 6 + ['GET'] * 2)
        self.assertEqual(c.storage.complete_calls, 1)
        self.assertEqual(len(provider.last_observations), 6)
        self.assertTrue(all(b'GET ' == bytes(s.sent[:4]) and s.closed for s in self.sockets))
        self.assertEqual(result['runtime'], 'NOT_VERIFIED')

    def test_reader_failure_stops_next_write_preserves_ack_and_fence(self):
        c, provider = self.connected_session(fail_after_first=True)
        result = c.run()
        self.assertEqual(result['status'], 'STOPPED', result)
        self.assertEqual(len(c.h.requests), 1)
        self.assertIsNotNone(c.storage.active)
        self.assertEqual(c.storage.complete_calls, 0)
        self.assertEqual(len(c.storage.journals[0].latest['acknowledged']), 1)
        self.assertEqual(provider.last_observations, ())
        self.assertTrue(all(s.closed for s in self.sockets))


if __name__ == '__main__':
    unittest.main()
