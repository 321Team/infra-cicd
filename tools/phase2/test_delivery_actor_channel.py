"""SYNTHETIC OAuth/TokenRequest HTTP fixtures; no real network, SSH or issuance."""
import base64
import copy
import hashlib
import json
import pickle
import threading
import unittest
from unittest.mock import Mock, patch

import delivery_actor_channel as channel
import delivery_transport as wire
import delivery_token_request as issuance
import delivery_ssh_bridge as ssh
import delivery_adapter as core
from delivery_object_reader import BearerCredential
import test_delivery_token_request as token_fixtures
import test_delivery_transport as transport_fixtures
from test_delivery_ssh_bridge import config

TOKEN = 'sha256~SYNTHETIC_ACTOR_TOKEN_NOT_A_REAL_SECRET_12345'


class ActorChannelTests(unittest.TestCase):
    def setUp(self):
        self.f = token_fixtures.TokenRequestTests('runTest'); self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.clock = lambda: self.f.mono
        self.args = dict(actor=BearerCredential(TOKEN), expected_username='synthetic-user',
            target=self.f.target, ca_pem=transport_fixtures.CA, endpoint_ip='192.0.2.10',
            operation_deadline=20, cleanup_deadline=30, monotonic=self.clock,
            wall=lambda:self.f.wall)

    def client(self, **changes):
        with patch.object(wire.ssl, 'SSLContext', return_value=self.f.f.context):
            c = channel.ActorCredentialChannel(self.f.f.binding, **(self.args | changes))
        self.addCleanup(c.close)
        return c

    def bridge(self, **changes):
        return ssh.SshCredentialBridge(config(), **(dict(deadline=20, monotonic=self.clock) | changes))

    def user(self):
        return {'apiVersion':'user.openshift.io/v1','kind':'User',
                'metadata':{'name':'synthetic-user','uid':'synthetic-user-uid'}}

    def revoked(self):
        name = 'sha256~' + base64.urlsafe_b64encode(hashlib.sha256(
            TOKEN.removeprefix('sha256~').encode()).digest()).decode().rstrip('=')
        return {'apiVersion':'oauth.openshift.io/v1','kind':'OAuthAccessToken',
                'metadata':{'name':name}, 'clientName':'synthetic-cli', 'userName':'synthetic-user'}

    def execute(self, c=None, *, user=None, revoke=None, auth_status=200, revoke_status=200,
                post_status=201, forward_error=None, issue=True, after_forward=None,
                raw_revoke=None, request=None, source_tokens=None, bridge=None):
        c = c or self.client()
        responses = [(self.user() if user is None else user, auth_status)]
        if issue and auth_status == 200 and (user is None or user == self.user()):
            responses.append((self.f.response(), post_status))
        responses.append((self.revoked() if revoke is None else revoke, revoke_status))
        self.sockets = [transport_fixtures.FakeSocket(transport_fixtures.response(
            raw_revoke if raw_revoke is not None and i == len(responses)-1 else core.encoded(obj), status))
            for i,(obj,status) in enumerate(responses)]
        self.f.f.context.wrap_socket.side_effect = lambda raw, **kwargs:raw
        b = bridge or self.bridge()
        def forward(req, *, provide_credentials):
            if issue:
                token, sources = provide_credentials(req, b._budget.limit)
                self.assertEqual(self.f.token(), token)
                self.assertEqual(1, len(sources))
            if after_forward:
                after_forward()
            if forward_error:
                raise forward_error
            return True
        with patch.object(wire.ssl, 'SSLContext', return_value=self.f.f.context), \
             patch.object(wire.socket, 'socket', side_effect=self.sockets) as opened, \
             patch.object(b, 'send_from_supplier', side_effect=forward), \
             patch.object(ssh.subprocess, 'Popen', side_effect=AssertionError('NO_SSH')):
            try:
                self.result = c.send_over_bridge(bridge=b,
                    request=self.f.request() if request is None else request,
                    source_tokens=[{'repository':'synthetic/evidence',
                                    'token':'SYNTHETIC_SOURCE_TOKEN_12345'}]
                                    if source_tokens is None else source_tokens)
            finally:
                self.socket_count = opened.call_count
        return c

    def test_success_real_http_auth_post_delete_and_private_receipt(self):
        c = self.execute()
        self.assertEqual(3, self.socket_count)
        requests = [bytes(s.sent) for s in self.sockets]
        self.assertIn(b'GET /apis/user.openshift.io/v1/users/~ HTTP/1.1', requests[0])
        self.assertIn(b'POST /api/v1/namespaces/synthetic-ci/serviceaccounts/deployer/token ', requests[1])
        self.assertIn(('DELETE /apis/oauth.openshift.io/v1/oauthaccesstokens/' +
                       self.revoked()['metadata']['name'] + ' HTTP/1.1').encode(), requests[2])
        for request in requests:
            self.assertIn(('Authorization: Bearer '+TOKEN).encode(), request)
            self.assertNotIn(TOKEN.encode(), request.split(b'\r\n')[0])
        self.assertTrue(all(s.closed for s in self.sockets))
        self.assertEqual('CONFIRMED', self.result['actor_identity'])
        self.assertEqual('ACKNOWLEDGED', self.result['actor_server_revocation'])
        self.assertTrue(self.result['credential_forwarded'])
        self.assertFalse(self.result['sa_token_revoked'])
        self.assertFalse(self.result['job_accepted'])
        self.assertIsNone(c._token)
        receipt = c.take_receipt()
        self.assertEqual(self.f.token(), receipt.credential.token)
        self.assertIsNone(c.take_receipt())
        self.assertNotIn(TOKEN, repr(c)+json.dumps(c.status()))
        with self.assertRaisesRegex(channel.ActorChannelError,'CONSUMED'):
            c.send_over_bridge(bridge=self.bridge(), request=self.f.request(), source_tokens=[])

    def test_constructor_and_close_are_inert_and_no_copy(self):
        clock = Mock(side_effect=AssertionError('NO_CLOCK'))
        c = self.client(monotonic=clock, wall=clock)
        clock.assert_not_called()
        for duplicate in (copy.copy, copy.deepcopy, pickle.dumps):
            with self.assertRaises(Exception): duplicate(c)
        c.close(); c.close()
        self.assertEqual('NOT_ATTEMPTED', c.status()['actor_server_revocation'])
        self.assertTrue(c.status()['local_references_released'])
        self.assertIsNone(c._name); self.assertIsNone(c._delete_path)

    def test_only_modern_dedicated_oauth_and_valid_deadlines(self):
        # A realistic-length legacy base64url value must not enter a URL-path
        # fallback, even though its length alone looks like a modern token.
        for token in ('legacy-token', 'a'*43, self.f.token(), 'sha256~short',
                      'sha256~../'+'a'*45, 'sha256~'+'a'*257):
            with self.subTest(token_type='synthetic'), self.assertRaises(channel.ActorChannelError):
                self.client(actor=BearerCredential(token))
        for change in ({'operation_deadline':True}, {'cleanup_deadline':20},
                       {'cleanup_deadline':float('nan')}, {'expected_username':'system:admin'},
                       {'expected_username':''}, {'endpoint_ip':'127.0.0.1'}):
            with self.subTest(change=change), self.assertRaises(channel.ActorChannelError):
                self.client(**change)

    def test_identity_mismatch_or_malformed_success_never_issues_or_revokes_other_actor(self):
        for change in ({'kind':'Status'}, {'apiVersion':'v1'},
                       {'metadata':{'name':'other','uid':'uid'}},
                       {'metadata':{'name':'synthetic-user'}},
                       {'metadata':{'name':'synthetic-user','uid':'uid','deletionTimestamp':'now'}}):
            c = self.client()
            with self.assertRaises(channel.ActorChannelError):
                self.execute(c,user=self.user()|change)
            self.assertEqual(1,self.socket_count)
            self.assertEqual('UNCONFIRMED',c.status()['actor_identity'])
            self.assertFalse(c.status()['issuance_attempted'])
            self.assertEqual('NOT_ATTEMPTED',c.status()['actor_server_revocation'])
            self.assertTrue(c.status()['local_references_released'])

    def test_auth_http_errors_do_not_retry_or_infer_revocation(self):
        for status in (302,401,403,404,429,500):
            c = self.client()
            with self.subTest(status=status),self.assertRaises(channel.ActorChannelError):
                self.execute(c,auth_status=status)
            self.assertEqual(1,self.socket_count)
            self.assertEqual('NOT_ATTEMPTED',c.status()['actor_server_revocation'])

    def test_revoke_errors_keep_issuance_receipt_without_false_success(self):
        for status in (202,204,302,401,403,404,429,500):
            c = self.client()
            with self.subTest(status=status),self.assertRaises(channel.ActorChannelError) as caught:
                self.execute(c,revoke_status=status)
            self.assertIsNone(caught.exception.__context__)
            self.assertIsNone(caught.exception.__cause__)
            self.assertEqual('UNCONFIRMED',c.status()['actor_server_revocation'])
            self.assertEqual(3,self.socket_count)
            self.assertIsNotNone(c.take_receipt())
            self.assertTrue(c.status()['local_references_released'])

    def test_revoke_requires_exact_deleted_object_or_success_status(self):
        success = {'apiVersion':'v1','kind':'Status','status':'Success','code':200}
        self.execute(revoke=success)
        bad = [self.revoked()|{'metadata':{'name':'other'}}, {}, success|{'status':'Failure'},
               success|{'code':True}, success|{'code':202}, success|{'details':[]},
               success|{'details':{'name':'other'}}]
        for obj in bad:
            c = self.client()
            with self.subTest(kind=obj.get('kind')),self.assertRaises(channel.ActorChannelError):
                self.execute(c,revoke=obj)
            self.assertEqual('UNCONFIRMED',c.status()['actor_server_revocation'])
        c = self.client()
        with self.assertRaises(channel.ActorChannelError):
            self.execute(c,raw_revoke=b'{"kind":"Status","kind":"OAuthAccessToken"}')

    def test_ready_failure_still_revokes_authenticated_actor_without_issuance(self):
        c = self.client()
        with self.assertRaises(channel.ActorChannelError):
            self.execute(c,issue=False,forward_error=TimeoutError('SYNTHETIC_PRIVATE'))
        self.assertEqual(2,self.socket_count)
        self.assertEqual('ACKNOWLEDGED',c.status()['actor_server_revocation'])
        self.assertFalse(c.status()['issuance_attempted'])
        self.assertIsNone(c.take_receipt())

    def test_post_failure_is_uncertain_and_cleanup_still_runs(self):
        c = self.client()
        with self.assertRaises(channel.ActorChannelError):
            self.execute(c,post_status=500)
        self.assertEqual(3,self.socket_count)
        self.assertTrue(c.status()['issuance_attempted'])
        self.assertIsNone(c.take_receipt())
        self.assertEqual('ACKNOWLEDGED',c.status()['actor_server_revocation'])

    def test_handoff_failure_after_post_preserves_receipt_and_cancellation(self):
        for exc in (ValueError('SYNTHETIC_PRIVATE'),KeyboardInterrupt('SYNTHETIC_PRIVATE'),
                    SystemExit('SYNTHETIC_PRIVATE')):
            c = self.client()
            kind = channel.ActorChannelError if isinstance(exc,Exception) else type(exc)
            with self.assertRaises(kind) as caught:
                self.execute(c,forward_error=exc)
            self.assertNotIn('SYNTHETIC_PRIVATE',str(caught.exception))
            self.assertIsNone(caught.exception.__context__)
            self.assertEqual('ACKNOWLEDGED',c.status()['actor_server_revocation'])
            self.assertIsNotNone(c.take_receipt())
            self.assertFalse(c.status()['credential_forwarded'])

    def test_cleanup_uses_separate_bounded_budget_without_extending_issuance(self):
        c = self.client()
        self.execute(c,after_forward=lambda:setattr(self.f,'mono',25))
        self.assertEqual('ACKNOWLEDGED',c.status()['actor_server_revocation'])
        self.f.mono=10;c=self.client()
        with self.assertRaises(channel.ActorChannelError):
            self.execute(c,after_forward=lambda:setattr(self.f,'mono',31))
        self.assertEqual(2,self.socket_count)
        self.assertEqual('UNCONFIRMED',c.status()['actor_server_revocation'])

    def test_cancellation_survives_simultaneous_delete_failure_and_preserves_receipt(self):
        for cancel in (KeyboardInterrupt, SystemExit):
            for failure in ('http500', 'timeout'):
                with self.subTest(cancel=cancel.__name__, failure=failure):
                    c = self.client(); original = c._exchange
                    deletes = []
                    def exchange(request, deadline):
                        if request.method == 'DELETE':
                            deletes.append(request.method)
                            if failure == 'timeout':
                                raise TimeoutError('SYNTHETIC_PRIVATE_CLEANUP')
                        return original(request, deadline)
                    with patch.object(c, '_exchange', side_effect=exchange), \
                         self.assertRaises(cancel) as caught:
                        self.execute(c, forward_error=cancel('SYNTHETIC_PRIVATE_HANDOFF'),
                                     revoke_status=500)
                    self.assertEqual(['DELETE'], deletes)  # no automatic retry
                    self.assertEqual(3 if failure == 'http500' else 2, self.socket_count)
                    self.assertIsNone(caught.exception.__cause__)
                    self.assertIsNone(caught.exception.__context__)
                    self.assertNotIn('SYNTHETIC_PRIVATE', str(caught.exception))
                    self.assertEqual('UNCONFIRMED', c.status()['actor_server_revocation'])
                    self.assertFalse(c.status()['credential_forwarded'])
                    self.assertTrue(c.status()['local_references_released'])
                    self.assertIsNone(c._token)
                    receipt = c.take_receipt()
                    self.assertEqual(self.f.token(), receipt.credential.token)
                    self.assertIsNone(c.take_receipt())

    def test_invalid_bridge_request_sources_stop_before_network(self):
        cases = [
            {'bridge':self.bridge(monotonic=lambda:10)},
            {'bridge':self.bridge(deadline=21)},
            {'request':self.f.request()|{'nonce':'bad'}},
            {'source_tokens':[]}]
        for change in cases:
            c = self.client()
            with self.subTest(fields=list(change)),self.assertRaises(channel.ActorChannelError):
                self.execute(c,**change)
            self.assertEqual(0,self.socket_count)
            self.assertTrue(c.status()['local_references_released'])

    def test_local_cleanup_error_still_attempts_server_revoke(self):
        original = issuance.RequestBoundSupplier.close
        def fail(value):
            original(value)
            raise ValueError('SYNTHETIC_PRIVATE')
        c = self.client()
        with patch.object(issuance.RequestBoundSupplier,'close',new=fail), \
             self.assertRaisesRegex(channel.ActorChannelError,'LOCAL_CLEANUP_FAILED'):
            self.execute(c)
        self.assertEqual('ACKNOWLEDGED',c.status()['actor_server_revocation'])
        self.assertIsNotNone(c.take_receipt());self.assertIsNone(c._token)

    def test_clock_regression_and_expired_operation_do_not_touch_network(self):
        self.f.mono=21;c=self.client()
        with self.assertRaises(channel.ActorChannelError):self.execute(c)
        self.assertEqual(0,self.socket_count)
        self.f.mono=10;c=self.client()
        with self.assertRaises(channel.ActorChannelError):
            self.execute(c,after_forward=lambda:setattr(self.f,'mono',9))
        self.assertEqual(2,self.socket_count)
        self.assertEqual('UNCONFIRMED',c.status()['actor_server_revocation'])

    def test_overlapping_second_send_cannot_repeat_issue_or_cleanup(self):
        c = self.client(); first_entered = threading.Event(); second_started = threading.Event()
        outcome = []
        def second():
            first_entered.wait(2); second_started.set()
            try:
                c.send_over_bridge(bridge=self.bridge(), request=self.f.request(), source_tokens=[])
            except channel.ActorChannelError as exc:
                outcome.append(str(exc))
        worker = threading.Thread(target=second)
        worker.start()
        def pause():
            first_entered.set()
            self.assertTrue(second_started.wait(2))
        self.execute(c, after_forward=pause)
        worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(['ACTOR_CHANNEL_CONSUMED'],outcome)
        self.assertEqual(3,self.socket_count)

    def test_unknown_network_failure_during_delete_never_retries_or_leaks(self):
        c = self.client(); original = c._exchange
        def exchange(request, deadline):
            if request.method == 'DELETE':
                raise TimeoutError('SYNTHETIC_PRIVATE_TOKEN_DETAIL')
            return original(request,deadline)
        with patch.object(c,'_exchange',side_effect=exchange), self.assertRaises(
                channel.ActorChannelError) as caught:
            self.execute(c)
        self.assertEqual(2,self.socket_count)
        self.assertEqual('UNCONFIRMED',c.status()['actor_server_revocation'])
        self.assertIsNone(caught.exception.__context__)
        self.assertNotIn('SYNTHETIC_PRIVATE_TOKEN_DETAIL',str(caught.exception))
        self.assertIsNotNone(c.take_receipt());self.assertIsNone(c._token)

    def test_close_during_auth_cannot_continue_to_issue(self):
        c = self.client(); original = c._exchange
        def exchange(request, deadline):
            obj = original(request,deadline);c.close();return obj
        with patch.object(c,'_exchange',side_effect=exchange), self.assertRaises(channel.ActorChannelError):
            self.execute(c)
        self.assertEqual(1,self.socket_count)
        self.assertFalse(c.status()['issuance_attempted'])
        self.assertEqual('NOT_ATTEMPTED',c.status()['actor_server_revocation'])

if __name__ == '__main__': unittest.main()
