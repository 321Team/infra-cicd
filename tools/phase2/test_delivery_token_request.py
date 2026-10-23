"""SYNTHETIC API/JWT fixtures and injected sockets; no network/token issuance."""
import base64
import copy
from dataclasses import replace
from datetime import datetime, timezone
import json
import pickle
import unittest
from unittest.mock import Mock, patch

import delivery_adapter as core
import delivery_crosshost_ingress as ingress
import delivery_token_request as issuer
import delivery_transport as wire
from delivery_object_reader import BearerCredential
from test_delivery_job import CONTEXT
import test_delivery_transport as fixture


def stamp(value): return datetime.fromtimestamp(value, timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def part(obj): return base64.urlsafe_b64encode(core.encoded(obj)).decode().rstrip('=')


class TokenRequestTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture.TransportTests('runTest'); self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.target = issuer.TokenTarget('synthetic-ci', 'deployer', 'synthetic-uid',
                                        'https://issuer.invalid', 'https://audience.invalid')
        self.mono, self.wall = 10, 1001
        self.claims = {'iss':self.target.issuer,'aud':[self.target.audience],
            'sub':'system:serviceaccount:synthetic-ci:deployer','iat':1000,'nbf':1000,'exp':1900,
            'kubernetes.io':{'namespace':self.target.namespace,
                'serviceaccount':{'name':self.target.name,'uid':self.target.uid}}}
        self.header = {'alg':'RS256','kid':'SYNTHETIC_NOT_A_SIGNING_KEY'}
        for name in ('socket','getaddrinfo','create_connection'):
            p = patch.object(wire.socket, name, side_effect=AssertionError('NETWORK_FORBIDDEN'))
            p.start(); self.addCleanup(p.stop)

    def token(self): return part(self.header)+'.'+part(self.claims)+'.'+part('SYNTHETIC_SIGNATURE')

    def response(self):
        return {'apiVersion':'authentication.k8s.io/v1','kind':'TokenRequest',
            'metadata':{'name':self.target.name,'namespace':self.target.namespace,'creationTimestamp':stamp(1000)},
            'spec':{'audiences':[self.target.audience],'expirationSeconds':900},
            'status':{'token':self.token(),'expirationTimestamp':stamp(1900)}}

    def client(self, **changes):
        args = dict(target=self.target, actor=BearerCredential(fixture.TOKEN), ca_pem=fixture.CA,
                    endpoint_ip='192.0.2.10', deadline=20, monotonic=lambda:self.mono, wall=lambda:self.wall)
        args.update(changes)
        with patch.object(wire.ssl, 'SSLContext', return_value=self.f.context):
            c = issuer.TokenRequestIssuer(self.f.binding, **args)
        self.addCleanup(c.close)
        return c

    def issue(self, obj=None, *, client=None, status=201, raw=None):
        c = client or self.client()
        raw = raw if raw is not None else core.encoded(self.response() if obj is None else obj)
        self.sock = fixture.FakeSocket(fixture.response(raw, status))
        self.f.context.wrap_socket.return_value = self.sock
        with patch.object(wire.socket, 'socket', return_value=self.sock) as opened:
            result = c.issue(20)
        self.assertEqual(1, opened.call_count)
        return result

    def request(self):
        return {'schema':ingress.REQUEST_SCHEMA,'target':{
            **{k:getattr(CONTEXT,k) for k in ('repository','event','ref','workflow_sha','run_id','attempt')},
            'binding_key':self.f.binding.key},'nonce':'a'*64,'repositories':['synthetic/evidence']}

    def supplier(self, **changes):
        args = dict(issuer=self.client(),request=self.request(),
                    source_tokens=[{'repository':'synthetic/evidence','token':'SYNTHETIC_SOURCE_TOKEN_12345'}])
        args.update(changes)
        s = issuer.RequestBoundSupplier(**args); self.addCleanup(s.close)
        return s

    def test_constructor_inert_and_explicit_single_post_retains_private_original(self):
        clock = Mock(side_effect=AssertionError('CONSTRUCTOR_CLOCK'))
        c = self.client(monotonic=clock, wall=clock); clock.assert_not_called()
        c = self.client(); result = self.issue(client=c)
        self.assertEqual(core.encoded(self.response()), result.raw)
        self.assertEqual((1000,1900,900),(result.issued_at,result.expires_at,result.requested_seconds))
        self.assertTrue(c.attempted); self.assertIsNone(c._token)
        request = bytes(self.sock.sent)
        self.assertEqual(1, request.count(b'POST '))
        self.assertIn(b'POST /api/v1/namespaces/synthetic-ci/serviceaccounts/deployer/token HTTP/1.1',request)
        self.assertNotIn(b'boundObjectRef',request); self.assertNotIn(b'"uid"',request)
        self.assertIn(('Authorization: Bearer '+fixture.TOKEN).encode(),request)
        self.f.context.wrap_socket.assert_called_once()
        self.assertTrue(self.sock.closed)
        self.assertNotIn(result.credential.token,repr(result)+repr(c))
        with self.assertRaisesRegex(issuer.IssuanceError,'CONSUMED'): c.issue(20)

    def test_shorter_server_lifetime_uses_returned_expiration(self):
        self.claims['exp']=1600; obj=self.response()
        obj['spec']['expirationSeconds']=600; obj['status']['expirationTimestamp']=stamp(1600)
        result=self.issue(obj)
        self.assertEqual(1600,result.expires_at); self.assertEqual(900,result.requested_seconds)

    def test_wrong_subject_uid_audience_issuer_and_bound_tokens_rejected(self):
        original=copy.deepcopy(self.claims)
        for mutate in (lambda c:c.update(iss='https://other.invalid'), lambda c:c.update(aud=['other']),
                       lambda c:c.update(sub='system:serviceaccount:other:deployer'),
                       lambda c:c['kubernetes.io']['serviceaccount'].update(uid='recreated'),
                       lambda c:c['kubernetes.io'].update(pod={'name':'unexpected','uid':'extra'})):
            self.claims=copy.deepcopy(original); mutate(self.claims)
            with self.assertRaisesRegex(issuer.IssuanceError,'SUBJECT_MISMATCH'): self.issue()

    def test_lifetime_never_extended_by_request_or_receipt_time(self):
        original=copy.deepcopy(self.claims)
        for changes in ({'exp':1901},{'iat':999},{'iat':1100,'nbf':1100},{'nbf':999},{'exp':True}):
            self.claims=original|changes
            with self.assertRaisesRegex(issuer.IssuanceError,'LIFETIME_INVALID'): self.issue()
        self.claims=original; self.wall=1900
        with self.assertRaisesRegex(issuer.IssuanceError,'LIFETIME_INVALID'): self.issue()

    def test_response_scope_format_duplicates_and_unsigned_token_rejected(self):
        for key, value in (('kind','Secret'),('apiVersion','v1')):
            obj=self.response(); obj[key]=value
            with self.assertRaises(issuer.IssuanceError):self.issue(obj)
        obj=self.response(); obj['metadata']['name']='other'
        with self.assertRaises(issuer.IssuanceError):self.issue(obj)
        for value in (901,True,0):
            obj=self.response();obj['spec']['expirationSeconds']=value
            with self.assertRaises(issuer.IssuanceError):self.issue(obj)
        self.header['alg']='none'
        with self.assertRaises(issuer.IssuanceError):self.issue()
        with self.assertRaises(issuer.IssuanceError):self.issue(raw=b'{"kind":"a","kind":"b"}')

    def test_non201_and_unknown_transport_failure_not_retried_or_printed(self):
        for status in (200,302,400,401,403,429,500):
            c=self.client()
            with self.assertRaisesRegex(issuer.IssuanceError,'^TOKEN_REQUEST_FAILED$'):
                self.issue(client=c,status=status)
            self.assertTrue(c.attempted); self.assertIsNone(c._token)
            self.assertEqual(1,bytes(self.sock.sent).count(b'POST '))
        c=self.client()
        with patch.object(c,'_exchange',side_effect=ValueError('SYNTHETIC_PRIVATE_FAILURE')):
            with self.assertRaisesRegex(issuer.IssuanceError,'^TOKEN_REQUEST_FAILED$'):c.issue(20)
        self.assertTrue(c.attempted)

    def test_deadline_mismatch_or_expiry_never_starts_post(self):
        for deadline in (21,10,True,float('inf')):
            c=self.client()
            with patch.object(c,'_exchange') as exchange:
                with self.assertRaises(issuer.IssuanceError):c.issue(deadline)
            exchange.assert_not_called(); self.assertFalse(c.attempted)

    def test_late_response_and_wall_rollback_fail_closed(self):
        c=self.client()
        def late(*a):self.mono=21;return core.encoded(self.response())
        with patch.object(c,'_exchange',side_effect=late),self.assertRaises(issuer.IssuanceError):c.issue(20)
        self.mono=10;c=self.client()
        def rollback(*a):self.wall=999;return core.encoded(self.response())
        with patch.object(c,'_exchange',side_effect=rollback),self.assertRaisesRegex(issuer.IssuanceError,'CLOCK_INVALID'):c.issue(20)

    def test_interrupt_and_close_failure_are_sanitized(self):
        for exc,args in ((KeyboardInterrupt('SYNTHETIC_PRIVATE'),()),(SystemExit('SYNTHETIC_PRIVATE'),(130,))):
            c=self.client()
            with patch.object(c,'_exchange',side_effect=exc),self.assertRaises(type(exc)) as got:c.issue(20)
            self.assertEqual(args,got.exception.args);self.assertIsNone(c._token)
        c=self.client()
        with patch.object(c,'_exchange',return_value=core.encoded(self.response())), \
             patch.object(c,'close',side_effect=ValueError('SYNTHETIC_PRIVATE')):
            with self.assertRaisesRegex(issuer.IssuanceError,'^TOKEN_CLEANUP_FAILED$'):c.issue(20)

    def test_supplier_request_match_then_private_receipt_and_existing_frame(self):
        s=self.supplier(); request=self.request()
        with patch.object(s._issuer,'_exchange',return_value=core.encoded(self.response())) as exchange:
            token,sources=s(request,20)
        self.assertEqual(1,exchange.call_count)
        frame=ingress.encode_frame(request,rosa_token=token,source_tokens=sources)
        self.assertIn(token.encode(),frame);self.assertIsNone(s._sources)
        receipt=s.take_receipt(); self.assertEqual(token,receipt.credential.token)
        self.assertIsNone(s.take_receipt())
        with self.assertRaisesRegex(issuer.IssuanceError,'CONSUMED'):s(request,20)

    def test_supplier_wrong_nonce_job_or_repositories_has_no_issue(self):
        for mutate in (lambda r:r.update(nonce='b'*64),lambda r:r['target'].update(run_id='999'),
                       lambda r:r.update(repositories=['synthetic/other'])):
            s=self.supplier();request=self.request();mutate(request)
            with patch.object(s._issuer,'issue') as issue,self.assertRaises(issuer.IssuanceError):s(request,20)
            issue.assert_not_called();self.assertFalse(s._issuer.attempted)

    def test_source_validation_failure_keeps_actual_issuance_for_private_recovery(self):
        s=self.supplier(source_tokens=[{'repository':'synthetic/evidence','token':self.token()}])
        with patch.object(s._issuer,'_exchange',return_value=core.encoded(self.response())):
            with self.assertRaisesRegex(issuer.IssuanceError,'TOKEN_SUPPLY_FAILED'):s(self.request(),20)
        self.assertTrue(s._issuer.attempted);self.assertIsNotNone(s.take_receipt())

    def test_no_copy_pickle_or_default_site(self):
        import delivery_job_site
        c=self.client();s=self.supplier();self.assertIsNone(delivery_job_site.SITE)
        for value in (c,s,self.target):
            for operation in (copy.copy,copy.deepcopy,pickle.dumps):
                with self.assertRaises(ingress.IngressError):operation(value)
        with self.assertRaisesRegex(issuer.IssuanceError,'EXPLICIT_ISSUE_REQUIRED'):c(None)

    def test_observed_profile_pins_target_without_io_or_endpoint_default(self):
        import delivery_fds_site_inputs as fds
        from test_delivery_fds_site_inputs import ObservedSiteTests
        f=ObservedSiteTests('runTest'); f.setUp(); self.addCleanup(f.doCleanups)
        clock=Mock(side_effect=AssertionError('NO_CLOCK_OR_RUNTIME'))
        c=fds.build_token_issuer(f.config,actor=BearerCredential(fixture.TOKEN),
            endpoint_ip='192.0.2.10',deadline=20,monotonic=clock,wall=clock)
        self.addCleanup(c.close);clock.assert_not_called()
        self.assertEqual(('ci-cd','fds-deployer',fds.SUBJECT_UID,fds.ISSUER,fds.AUDIENCE),
            (c.target.namespace,c.target.name,c.target.uid,c.target.issuer,c.target.audience))
        self.assertNotIn('fds-app/serviceaccounts',c._path)
        with self.assertRaises(fds.FdsSiteInputError):
            fds.build_token_issuer(replace(f.config,binding=replace(f.config.binding,namespace_uid='changed')),
                actor=BearerCredential(fixture.TOKEN),endpoint_ip='192.0.2.10',deadline=20,monotonic=clock,wall=clock)

    def test_failure_discards_original_exception_chain_and_raw_frame_locals(self):
        self.claims['sub']='wrong-subject'
        c=self.client()
        try:
            self.issue(client=c)
        except issuer.IssuanceError as e:
            self.assertIsNone(e.__context__); self.assertIsNone(e.__cause__)
            tb=e.__traceback__; names=[]
            while tb:
                frame=tb.tb_frame; names.append(frame.f_code.co_name)
                if frame.f_code is issuer.TokenRequestIssuer.issue.__code__:
                    self.assertIsNone(frame.f_locals['raw']); self.assertIsNone(frame.f_locals['receipt'])
                tb=tb.tb_next
            self.assertNotIn('_receipt', names)
        else: self.fail('wrong subject must fail')
        self.assertIsNone(c._token)
        try: self.client(actor=BearerCredential('INVALID WITH SPACE'))
        except issuer.IssuanceError as e:
            self.assertIsNone(e.__context__); self.assertIsNone(e.__cause__)
        else: self.fail('invalid actor must fail')
        try: self.supplier(source_tokens=[{'repository':'wrong/repo','token':'PRIVATE_TEST_TOKEN'}])
        except issuer.IssuanceError as e:
            self.assertIsNone(e.__context__); self.assertIsNone(e.__cause__)
        else: self.fail('wrong source must fail')

    def test_close_during_exchange_never_returns_token_and_close_is_idempotent(self):
        c=self.client()
        def stop(*args):
            c.close()
            return core.encoded(self.response())
        with patch.object(c,'_exchange',side_effect=stop), self.assertRaisesRegex(issuer.IssuanceError,'CONSUMED'):
            c.issue(20)
        self.assertTrue(c.attempted); self.assertTrue(c.close()); self.assertTrue(c.close())
        self.assertIsNone(c._token)


if __name__=='__main__':unittest.main()
