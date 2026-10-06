"""Synthetic JWT, API, protocol and POSIX pipe tests; no token/cluster/SSH."""
import copy
import datetime as dt
import json
import os
import subprocess
import threading
import unittest
from unittest.mock import Mock, patch

import deploy_native_probe as n
from test_deploy_binding import NOW
from test_deploy_credential import SA_UID, fixture, jwt


def native_fixture():
    claims, _, review = fixture()
    claims['exp'] = claims['iat'] + n.TTL
    token = jwt(claims)
    review['spec']['token'] = token
    status_expiry = dt.datetime.fromtimestamp(
        claims['exp'] - 1, dt.timezone.utc).isoformat()
    response = {
        'apiVersion': 'authentication.k8s.io/v1', 'kind': 'TokenRequest',
        'metadata': {'name': 'cicd-runner-sa', 'namespace': 'ci-cd', 'uid': SA_UID},
        'spec': {'audiences': [n.b.AUDIENCE], 'expirationSeconds': n.TTL},
        'status': {'token': token, 'expirationTimestamp': status_expiry},
    }
    return claims, token, review, response


def access(response=None):
    obj = object.__new__(n.NativeAccess)
    obj.attempted, obj.issue_uid, obj.token_command_seen = False, None, False
    obj.clock, obj.blocked = lambda: 100.0, False
    obj.sa_uid = Mock(return_value=SA_UID)
    obj.request = Mock(return_value=subprocess.CompletedProcess(
        [], 0, n.encode(response or native_fixture()[3]), b''))
    return obj


class NativeAccessTests(unittest.TestCase):
    def test_one_fixed_native_command_uid_audience_and_earlier_status_expiry(self):
        obj = access()
        value = obj.issue(SA_UID, lambda: 600)
        self.assertEqual(value['token'], native_fixture()[1])
        self.assertEqual(value['status_expires_at'], native_fixture()[3]['status']['expirationTimestamp'])
        obj.request.assert_called_once()
        args, kwargs = obj.request.call_args
        self.assertEqual(args, (n.TOKEN_ARGS,))
        self.assertEqual(n.b.read(kwargs['payload'])['metadata']['uid'], SA_UID)
        self.assertEqual(n.b.read(kwargs['payload'])['spec'],
                         {'audiences': [n.b.AUDIENCE], 'expirationSeconds': 900})
        self.assertEqual(kwargs['timeout'], 10)
        with self.assertRaises(n.ProbeError):
            obj.issue(SA_UID, lambda: 600)
        self.assertEqual(obj.request.call_count, 1)

    def test_uid_change_and_expired_window_stop_before_issuance(self):
        obj = access()
        obj.sa_uid.return_value = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'
        with self.assertRaises(n.ProbeError):
            obj.issue(SA_UID, lambda: 600)
        obj.request.assert_not_called()
        obj = access()
        points = iter((1, 1, -1))
        with self.assertRaises((n.ProbeError, n.a.AccessError)):
            obj.issue(SA_UID, lambda: next(points))
        obj.request.assert_not_called()

    def test_failed_command_is_not_reissued(self):
        obj = access()
        obj.request.side_effect = RuntimeError('PRIVATE_API_ERROR')
        with self.assertRaises(RuntimeError):
            obj.issue(SA_UID, lambda: 600)
        with self.assertRaises(n.ProbeError):
            obj.issue(SA_UID, lambda: 600)
        self.assertEqual(obj.request.call_count, 1)

    def test_bad_native_response_binding_or_lifetime_is_rejected(self):
        original = native_fixture()[3]
        cases = [
            lambda x: x['metadata'].update(uid='aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'),
            lambda x: x['spec'].update(audiences=['other']),
            lambda x: x['spec'].update(expirationSeconds=901),
            lambda x: x['spec'].update(expirationSeconds=True),
            lambda x: x['spec'].update(boundObjectRef={'kind': 'Secret', 'name': 'other'}),
            lambda x: x['status'].update(expirationTimestamp='2099-01-01T00:00:00Z'),
            lambda x: x['status'].update(expirationTimestamp='2026-01-01T00:00:00Z'),
            lambda x: x.update(kind='Secret'),
        ]
        for mutate in cases:
            value = copy.deepcopy(original)
            mutate(value)
            with self.subTest(mutate=mutate):
                with self.assertRaises((n.ProbeError, n.c.CredentialError)):
                    access(value).issue(SA_UID, lambda: 600)

    def test_allowlist_requires_issue_state_and_one_actual_command(self):
        obj = access()
        body = n.encode({'apiVersion': 'authentication.k8s.io/v1', 'kind': 'TokenRequest',
                         'metadata': {'name': 'cicd-runner-sa', 'namespace': 'ci-cd', 'uid': SA_UID},
                         'spec': {'audiences': [n.b.AUDIENCE], 'expirationSeconds': 900}})
        with self.assertRaises(n.ProbeError):
            obj.arguments_checked(n.TOKEN_ARGS, body, 0, False)
        obj.attempted, obj.issue_uid = True, SA_UID
        for verbosity, failure in ((8, False), (0, True)):
            with self.assertRaises(n.ProbeError):
                obj.arguments_checked(n.TOKEN_ARGS, body, verbosity, failure)
        changed = n.b.read(body)
        changed['spec']['expirationSeconds'] = 3600
        with self.assertRaises(n.ProbeError):
            obj.arguments_checked(n.TOKEN_ARGS, n.encode(changed), 0, False)
        obj.arguments_checked(n.TOKEN_ARGS, body, 0, False)
        with self.assertRaises(n.ProbeError):
            obj.arguments_checked(n.TOKEN_ARGS, body, 0, False)

    def test_old_backend_does_not_gain_token_request_permission(self):
        obj = object.__new__(n.a.CP1Access)
        with self.assertRaises(n.a.AccessError):
            obj.arguments_checked(n.TOKEN_ARGS, b'{}', 0, False)

    def test_preflight_authorization_reviews_are_fixed_and_never_issue(self):
        obj = access()
        obj.request.side_effect = lambda args, **kw: subprocess.CompletedProcess(
            [], 0, n.encode({**n.b.read(kw['payload']), 'status': {'allowed': True}}), b'')
        self.assertEqual(obj.preflight(), SA_UID)
        self.assertEqual(obj.request.call_count, 2)
        self.assertTrue(all(call.args == (n.SSAR_ARGS,) for call in obj.request.call_args_list))
        self.assertFalse(obj.attempted)
        bad = {'apiVersion': 'authorization.k8s.io/v1', 'kind': 'SelfSubjectAccessReview',
               'spec': {'resourceAttributes': {'verb': '*', 'resource': '*'}}}
        with self.assertRaises(n.ProbeError):
            obj.arguments_checked(n.SSAR_ARGS, n.encode(bad), 0, False)

    def test_denied_or_incomplete_cp1_privilege_blocks_preflight(self):
        for status in ({'allowed': False}, {'allowed': True, 'denied': True},
                       {'allowed': True, 'evaluationError': 'PRIVATE_REASON'}):
            obj = access()
            obj.request.side_effect = lambda args, **kw: subprocess.CompletedProcess(
                [], 0, n.encode({**n.b.read(kw['payload']), 'status': status}), b'')
            with self.subTest(status=status):
                with self.assertRaises(n.ProbeError):
                    obj.preflight()
                self.assertFalse(obj.attempted)

    def test_fresh_uid_is_checked_inside_shared_review_budget(self):
        obj = access()
        obj.attempted, obj.issue_uid = True, SA_UID
        with patch.object(n.a.CP1Access, 'review', return_value={'synthetic': True}) as review:
            self.assertEqual(obj.review('synthetic', n.b.AUDIENCE, 3), {'synthetic': True})
            self.assertEqual(obj.sa_uid.call_args.kwargs['timeout'], 3)
            self.assertEqual(review.call_args.args[-1], 3)
        obj.sa_uid.return_value = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'
        with patch.object(n.a.CP1Access, 'review') as review:
            with self.assertRaises(n.ProbeError):
                obj.review('synthetic', n.b.AUDIENCE, 3)
            review.assert_not_called()


class FakeChannel:
    def __init__(self, messages):
        self.messages, self.sent, self.cleared = list(messages), [], False

    def send(self, obj, timeout=10):
        self.sent.append(copy.deepcopy(obj))

    def recv(self, timeout=10):
        if not self.messages:
            raise RuntimeError('PRIVATE_PIPE_ERROR')
        return self.messages.pop(0)

    def clear(self):
        self.cleared = True


class EndpointTests(unittest.TestCase):
    def setUp(self):
        guard = patch.object(n, 'private_channel_checked')
        guard.start()
        self.addCleanup(guard.stop)

    def test_prepare_never_issues_and_releases_channel_references(self):
        for role in ('cp1', 'runner'):
            channel = FakeChannel([{'action': 'stop'}])
            backend = Mock(attempted=False)
            backend.preflight.return_value = SA_UID
            with patch.object(n, 'Channel', return_value=channel), \
                 patch.object(n.a, 'host_checked'), patch.object(n.a, 'ca_checked', return_value=(b'CA', Mock())), \
                 patch.object(n, 'NativeAccess', return_value=backend):
                n.endpoint(role, 'a' * 40, '/synthetic', '/synthetic-ca', 'b' * 64, None)
            backend.issue.assert_not_called()
            self.assertEqual(channel.sent[0]['stage'], 'ready')
            self.assertEqual(channel.sent[-1]['stage'], 'done')
            self.assertEqual(channel.sent[-1]['token_issuance'], 'NOT_ATTEMPTED')
            self.assertTrue(channel.cleared)

    def test_execute_frame_without_explicit_window_does_not_issue(self):
        channel = FakeChannel([{'action': 'issue-once'}])
        backend = Mock(attempted=False)
        backend.preflight.return_value = SA_UID
        with patch.object(n, 'Channel', return_value=channel), \
             patch.object(n.a, 'host_checked'), patch.object(n.a, 'ca_checked', return_value=(b'CA', Mock())), \
             patch.object(n, 'NativeAccess', return_value=backend):
            n.endpoint('cp1', 'a' * 40, '/synthetic', '/synthetic-ca', 'b' * 64, None)
        backend.issue.assert_not_called()
        self.assertEqual(channel.sent[-1]['stage'], 'error')
        self.assertEqual(channel.sent[-1]['token_issuance'], 'NOT_ATTEMPTED')

    def test_cp1_private_protocol_fresh_review_and_public_cleanup(self):
        _, token, review, response = native_fixture()
        channel = FakeChannel([{'action': 'issue-once'},
                               {'stage': 'review', 'token': token,
                                'audience': n.b.AUDIENCE, 'timeout': 10},
                               {'action': 'finish'}])
        backend = Mock(attempted=True)
        backend.preflight.return_value = SA_UID
        backend.issue.return_value = {'token': token, 'sa_uid': SA_UID,
                                      'status_expires_at': response['status']['expirationTimestamp']}
        backend.review.return_value = review
        actual = n.c.Credential
        factory = lambda token, uid, reviewer, **kw: actual(
            token, uid, reviewer, now=lambda: NOW, clock=lambda: 100.0, **kw)
        with patch.object(n, 'Channel', return_value=channel), \
             patch.object(n.a, 'host_checked'), patch.object(n.a, 'ca_checked', return_value=(b'CA', Mock())), \
             patch.object(n, 'Window', return_value=Mock(remaining=lambda: 600)), \
             patch.object(n, 'NativeAccess', return_value=backend), \
             patch.object(n.c, 'Credential', side_effect=factory), \
             patch.object(n, 'utcnow', return_value=NOW):
            n.endpoint('cp1', 'a' * 40, '/synthetic', '/synthetic-ca', 'b' * 64,
                       'synthetic-window')
        self.assertEqual(backend.issue.call_count, 1)
        self.assertEqual(backend.review.call_count, 2)
        self.assertEqual(channel.sent[1]['stage'], 'candidate')
        self.assertEqual(channel.sent[2]['stage'], 'review-result')
        done = channel.sent[-1]
        self.assertEqual(done['stage'], 'done')
        self.assertEqual(done['credential']['local_credential_cleanup'], 'LOCAL_REFERENCE_DROPPED')
        self.assertNotIn(token, json.dumps(done))
        self.assertTrue(channel.cleared)

    def test_runner_private_protocol_uses_fresh_pipe_review_then_closes_credential(self):
        _, token, review, response = native_fixture()
        candidate = {'stage': 'candidate', 'token': token, 'sa_uid': SA_UID,
                     'status_expires_at': response['status']['expirationTimestamp']}
        channel = FakeChannel([candidate, {'stage': 'review-result', 'response': review}])
        actual, credentials = n.c.Credential, []
        def factory(token, uid, reviewer, **kw):
            self.assertEqual(kw['review_timeout'], 2)
            value = actual(token, uid, reviewer, now=lambda: NOW, clock=lambda: 100.0, **kw)
            credentials.append(value)
            return value
        probe = Mock()
        probe.run.return_value = {'RUNNER_TOKEN_AUTH': 'PASS', 'DEPLOY': 'NOT_EXECUTED',
                                  'DELIVERY_GATE': 'NOT_VERIFIED'}
        with patch.object(n, 'Channel', return_value=channel), \
             patch.object(n.a, 'host_checked'), \
             patch.object(n.a, 'ca_checked', return_value=(b'CA', Mock())), \
             patch.object(n, 'Window', return_value=Mock(remaining=lambda: 2)), \
             patch.object(n.c, 'Credential', side_effect=factory), \
             patch.object(n, 'utcnow', return_value=NOW), \
             patch.object(n, 'RunnerProbe', return_value=probe):
            n.endpoint('runner', 'a' * 40, '/synthetic', '/synthetic-ca', 'b' * 64,
                       'synthetic-window')
        self.assertEqual([obj['stage'] for obj in channel.sent],
                         ['ready', 'review', 'runner-result'])
        self.assertEqual(channel.sent[1]['token'], token)
        self.assertEqual(channel.sent[1]['audience'], n.b.AUDIENCE)
        self.assertTrue(credentials[0].closed and channel.cleared)
        self.assertEqual(channel.sent[-1]['result']['credential']['token_request_expires_at'],
                         candidate['status_expires_at'])
        self.assertNotIn(token, json.dumps(channel.sent[-1]))

    def test_native_status_expiry_stops_before_first_token_review(self):
        _, token, _, response = native_fixture()
        for role in ('cp1', 'runner'):
            candidate = {'token': token, 'sa_uid': SA_UID,
                         'status_expires_at': response['status']['expirationTimestamp']}
            messages = [{'action': 'issue-once'}] if role == 'cp1' else [
                {'stage': 'candidate', **candidate}]
            channel, backend = FakeChannel(messages), Mock(attempted=True)
            backend.preflight.return_value = SA_UID
            backend.issue.return_value = candidate
            with self.subTest(role=role), \
                 patch.object(n, 'Channel', return_value=channel), \
                 patch.object(n.a, 'host_checked'), \
                 patch.object(n.a, 'ca_checked', return_value=(b'CA', Mock())), \
                 patch.object(n, 'Window', return_value=Mock(remaining=lambda: 600)), \
                 patch.object(n, 'NativeAccess', return_value=backend), \
                 patch.object(n.c, 'Credential') as credential, \
                 patch.object(n, 'utcnow', return_value=NOW + dt.timedelta(seconds=900)):
                n.endpoint(role, 'a' * 40, '/synthetic', '/synthetic-ca', 'b' * 64,
                           'synthetic-window')
                credential.assert_not_called()
                backend.review.assert_not_called()
                self.assertEqual(channel.sent[-1]['stage'], 'error')
                self.assertNotIn(token, json.dumps(channel.sent))

    def test_unexpected_errors_do_not_echo_private_values(self):
        channel = FakeChannel([])
        with patch.object(n, 'Channel', return_value=channel), \
             patch.object(n.a, 'host_checked', side_effect=[None, RuntimeError('PRIVATE_TOKEN_ERROR')]):
            n.endpoint('cp1', 'a' * 40, '/synthetic', '/synthetic-ca', 'b' * 64, None)
        self.assertNotIn('PRIVATE_TOKEN_ERROR', json.dumps(channel.sent))
        self.assertEqual(channel.sent[-1]['code'], 'NATIVE_PROBE_UNCONFIRMED')

    def test_pipe_review_has_no_cache_and_binds_one_private_response(self):
        _, token, review, _ = native_fixture()
        channel = FakeChannel([{'stage': 'review-result', 'response': review}])
        reviewer = n.PipeReviewer(channel)
        self.assertEqual(reviewer.review(token, n.b.AUDIENCE, 10), review)
        with self.assertRaises(n.ProbeError):
            reviewer.review(token, n.b.AUDIENCE, 10)
        self.assertEqual(len(channel.sent), 1)


class RunnerTests(unittest.TestCase):
    def probe(self):
        with patch.object(n.a, 'ca_checked', return_value=(b'CA', Mock())):
            credential = Mock(remaining=lambda: 600, token=lambda: native_fixture()[1])
            return n.RunnerProbe('/synthetic-ca', 'a' * 64, credential,
                                 (NOW + dt.timedelta(seconds=599)).isoformat(),
                                 Mock(remaining=lambda: 600), now=lambda: NOW,
                                 clock=lambda: 100.0)

    def test_fixed_rbac_matrix_and_two_authenticated_gets_without_env_output(self):
        probe = self.probe()
        seen = []

        def reply(method, path, body=None):
            seen.append((method, path))
            if method == 'POST':
                allowed = next(allow for attrs, allow in n.RBAC_TESTS
                               if attrs == body['spec']['resourceAttributes'])
                echoed = copy.deepcopy(body)
                if echoed['spec']['resourceAttributes']['group'] == '':
                    del echoed['spec']['resourceAttributes']['group']
                return {**echoed, 'status': {'allowed': allowed, 'reason': 'PRIVATE_REASON'}}
            name = path.rsplit('/', 1)[1]
            return {'apiVersion': 'apps/v1', 'kind': 'Deployment',
                    'metadata': {'name': name, 'namespace': 'fds-app', 'uid': SA_UID if name == 'transaction-api' else
                                 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee',
                                 'resourceVersion': 'opaque:7'},
                    'spec': {'env': 'PRIVATE_ENV'}}

        probe.request = Mock(side_effect=reply)
        result = probe.run()
        self.assertEqual(result['RUNNER_TOKEN_AUTH'], 'PASS')
        self.assertEqual(len(result['permissions']), 13)
        self.assertEqual(len(result['deployments']), 2)
        self.assertEqual(len(seen), 15)
        self.assertNotIn('PRIVATE_', json.dumps(result))
        self.assertTrue(all(method in ('GET', 'POST') for method, _ in seen))
        with self.assertRaises(n.ProbeError):
            probe.run()

    def test_rbac_mismatch_or_evaluation_error_is_not_pass(self):
        for status in ({'allowed': False}, {'allowed': True, 'evaluationError': 'PRIVATE_REASON'},
                       {'allowed': True, 'denied': True}, {'allowed': 1}):
            probe = self.probe()
            probe.request = Mock(side_effect=lambda method, path, body:
                                 {**body, 'status': status})
            with self.subTest(status=status):
                with self.assertRaises(n.ProbeError):
                    probe.run()
                self.assertTrue(probe.blocked)

    def test_arbitrary_write_or_secret_get_and_wildcard_ssar_are_rejected(self):
        probe = self.probe()
        for method, path, body in (
            ('PATCH', '/apis/apps/v1/namespaces/fds-app/deployments/transaction-api', {}),
            ('GET', '/api/v1/namespaces/fds-app/secrets', None),
            ('POST', n.SSAR_PATH, {'apiVersion': 'authorization.k8s.io/v1',
                                  'kind': 'SelfSubjectAccessReview',
                                  'spec': {'resourceAttributes': {'verb': '*', 'resource': '*'}}}),
        ):
            with self.subTest(method=method):
                with self.assertRaises(n.ProbeError):
                    probe.scope(method, path, body)

    def test_earlier_native_expiry_limits_window_and_does_not_extend_on_wall_rewind(self):
        probe = self.probe()
        self.assertEqual(probe.remaining(), 599)
        probe.now = lambda: NOW - dt.timedelta(days=1)
        self.assertEqual(probe.remaining(), 599)
        probe.clock = lambda: 700
        with self.assertRaises(n.ProbeError):
            probe.remaining()

    def test_live_socket_request_is_pinned_and_does_not_retry(self):
        probe = self.probe()
        raw, secured, response = Mock(), Mock(), Mock()
        probe.context.wrap_socket.return_value = secured
        response.headers.get_all.return_value = []
        response.getheader.side_effect = lambda name, default=None: default
        response.length, response.status = 0, 200
        response.read.return_value = n.encode({'apiVersion': 'apps/v1', 'kind': 'Deployment'})
        with patch.object(n.a, 'read_checked', return_value=b'CA'), \
             patch.object(n.socket, 'socket', return_value=raw) as socket_call, \
             patch.object(n.http.client, 'HTTPResponse', return_value=response):
            probe.request('GET', '/apis/apps/v1/namespaces/fds-app/deployments/transaction-api')
        raw.connect.assert_called_once_with(('10.1.93.109', 6443))
        self.assertEqual(probe.context.wrap_socket.call_args.kwargs['server_hostname'], '10.1.93.109')
        self.assertIn(b'Authorization: Bearer ', secured.sendall.call_args.args[0])
        self.assertEqual(socket_call.call_count, 1)
        for item in (raw, secured, response):
            item.close.assert_called_once()

    def test_http_redirect_denial_oversize_or_disconnect_permanently_block(self):
        for mode in ('redirect', 'deny', 'oversize', 'disconnect', 'duplicate-length'):
            probe = self.probe()
            raw, secured, response = Mock(), Mock(), Mock()
            probe.context.wrap_socket.return_value = secured
            response.headers.get_all.return_value = []
            response.getheader.side_effect = lambda name, default=None: default
            response.length = 0
            response.status = {'redirect': 302, 'deny': 401}.get(mode, 200)
            response.read.return_value = b'x' * (n.t.MAX_RESPONSE + 1) if mode == 'oversize' else b'{}'
            if mode == 'disconnect':
                secured.sendall.side_effect = RuntimeError('PRIVATE_HEADER')
            if mode == 'duplicate-length':
                response.headers.get_all.side_effect = lambda name: ['2', '2'] if name == 'Content-Length' else []
            with self.subTest(mode=mode), \
                 patch.object(n.a, 'read_checked', return_value=b'CA'), \
                 patch.object(n.socket, 'socket', return_value=raw) as socket_call, \
                 patch.object(n.http.client, 'HTTPResponse', return_value=response):
                with self.assertRaises(n.ProbeError):
                    probe.request('GET', '/apis/apps/v1/namespaces/fds-app/deployments/transaction-api')
                with self.assertRaises(n.ProbeError):
                    probe.request('GET', '/apis/apps/v1/namespaces/fds-app/deployments/transaction-api')
                self.assertEqual(socket_call.call_count, 1)
                self.assertTrue(probe.blocked)

    
class PrivateChannelTests(unittest.TestCase):
    def test_endpoint_requires_two_private_pipe_descriptors_and_ssh_session(self):
        with patch.object(n.os, 'isatty', return_value=False), \
             patch.object(n.os, 'fstat', return_value=Mock(st_mode=n.stat.S_IFIFO)), \
             patch.dict(n.os.environ, {'SSH_CONNECTION': 'synthetic'}):
            n.private_channel_checked()
            with patch.object(n.os, 'isatty', return_value=True):
                with self.assertRaises(n.ProbeError):
                    n.private_channel_checked()
            with patch.object(n.os, 'fstat', return_value=Mock(st_mode=n.stat.S_IFREG)):
                with self.assertRaises(n.ProbeError):
                    n.private_channel_checked()
            with patch.dict(n.os.environ, {}, clear=True):
                with self.assertRaises(n.ProbeError):
                    n.private_channel_checked()


@unittest.skipUnless(os.name == 'posix', 'POSIX pipes')
class ChannelTests(unittest.TestCase):
    def setUp(self):
        self.read_fd, self.write_fd = os.pipe()
        self.addCleanup(os.close, self.read_fd)
        self.addCleanup(os.close, self.write_fd)
        self.channel = n.Channel(self.read_fd, self.write_fd)

    def test_multiple_frames_and_duplicate_json_rejection(self):
        os.write(self.write_fd, b'{"stage":"one"}\n{"stage":"two"}\n')
        self.assertEqual(self.channel.recv()['stage'], 'one')
        self.assertEqual(self.channel.recv()['stage'], 'two')
        os.write(self.write_fd, b'{"stage":"one","stage":"two"}\n')
        with self.assertRaises(n.b.Invalid):
            self.channel.recv()
        self.assertEqual(self.channel.buffer, b'')

    def test_frame_deadline_and_large_send_with_backpressure(self):
        with self.assertRaises(n.ProbeError):
            self.channel.recv(0.01)
        data, received = {'synthetic': 'x' * 90000}, []
        thread = threading.Thread(target=lambda: received.append(self.channel.recv(3)))
        thread.start()
        self.channel.send(data, 3)
        thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(received, [data])

    def test_oversize_or_newline_raw_payload_is_rejected(self):
        for raw in (b'x' * (n.MAX_FRAME + 1), b'{}\n{}'):
            with self.assertRaises(n.ProbeError):
                self.channel.send_raw(raw)
