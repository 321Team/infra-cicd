"""SYNTHETIC framed bytes and fake clocks; no host/issuer/token or socket use."""
import contextlib
import copy
from dataclasses import replace
import io
import json
import os
import pickle
import socket
import struct
import sys
import threading
import unittest
from unittest.mock import Mock, patch

import delivery_crosshost_ingress as ingress
import delivery_job as job
import delivery_job_handoff as handoff
import delivery_job_site as declaration
import delivery_runtime_site as runtime
import delivery_site as site
import test_delivery_source_session as fixture
from test_delivery_job import CONTEXT, RUNNER


class MemoryChannel:
    """Own local bytes only. peer() is a fake transport, not an SSH observation."""
    def __init__(self, raw, peer, *, chunk=65536):
        self.raw, self.peer, self.chunk = raw, peer, chunk
        self.auth_calls = self.read_calls = self.close_calls = 0
        self.auth_request = None
        self.on_auth = self.on_read = self.on_close = None
        self.channel = ingress.ReviewedIngressChannel(authenticate=self.auth, read=self.read, close=self.close)

    def auth(self, request, deadline):
        self.auth_calls += 1; self.auth_request = request
        if self.on_auth: return self.on_auth(request, deadline)
        return self.peer

    def read(self, size, deadline):
        self.read_calls += 1
        if self.on_read: return self.on_read(size, deadline)
        n = min(size, self.chunk)
        result, self.raw = self.raw[:n], self.raw[n:]
        return result

    def close(self):
        self.close_calls += 1
        return self.on_close() if self.on_close else True


class IngressTests(unittest.TestCase):
    def setUp(self):
        self.s = fixture.SourceSessionTests('runTest'); self.s.setUp(); self.addCleanup(self.s.doCleanups)
        self.f = self.s.f
        self.peer = ingress.PeerIdentity('synthetic-issuer', 'synthetic-reviewed-transport')
        self.route = ingress.SourceRoute(self.s.repo, self.f.h.mono + 100, fixture.CA, '192.0.2.10')
        self.routes = (self.route,)
        self.deadline = self.f.h.mono + 50
        self.nonces = iter(('%064x' % x for x in range(1, 200)))
        p = patch.object(ingress.secrets, 'token_hex', side_effect=lambda n: next(self.nonces))
        p.start(); self.addCleanup(p.stop)
        for name in ('socket', 'getaddrinfo', 'create_connection'):
            p = patch.object(socket, name, side_effect=AssertionError('NETWORK_FORBIDDEN'))
            p.start(); self.addCleanup(p.stop)

    def receiver(self, **changes):
        return ingress.CrossHostIngress(**(dict(context=CONTEXT, binding=self.f.h.binding,
            expected_peer=self.peer, routes=self.routes, deadline=self.deadline,
            monotonic=lambda: self.f.h.mono) | changes))

    def frame(self, receiver, **changes):
        tokens = [dict(repository=x.repository.owner+'/'+x.repository.name, token=fixture.TOKEN)
                  for x in receiver._routes]
        return ingress.encode_frame(receiver.request(), **(dict(rosa_token=self.f.credential.token,
                                     source_tokens=tokens) | changes))

    def payload(self, receiver):
        return json.loads(self.frame(receiver)[4:])

    def pack(self, value):
        raw = json.dumps(value, separators=(',', ':')).encode()
        return struct.pack('!I', len(raw)) + raw

    def error(self, receiver, memory, pattern='INGRESS_', exc=ingress.IngressError):
        with self.assertRaisesRegex(exc, pattern) as e:
            receiver.receive(memory.channel)
        if exc is ingress.IngressError:
            self.assertIn(str(e.exception), ingress.SAFE_CODES)
            self.assertNotIn(fixture.TOKEN, str(e.exception))
            self.assertNotIn(self.f.credential.token, str(e.exception))
        self.assertEqual(1, memory.close_calls)
        with self.assertRaisesRegex(ingress.IngressError, 'CONSUMED'): receiver.receive(memory.channel)

    def test_constructor_and_import_do_not_configure_site_or_use_transport(self):
        r = self.receiver()
        self.assertIsNone(declaration.SITE)
        request = r.request()
        self.assertNotIn(fixture.TOKEN, json.dumps(request))
        self.assertNotIn(self.f.credential.token, json.dumps(request))
        self.assertEqual({'schema', 'target', 'nonce', 'repositories'}, set(request))
        self.assertEqual(CONTEXT.run_id, request['target']['run_id'])

    def test_invalid_configuration_rejected_without_nonce_or_allocation(self):
        cases = [dict(context=replace(CONTEXT, attempt=True)), dict(binding=None), dict(expected_peer=True),
                 dict(routes=()), dict(routes=[self.route]), dict(routes=(self.route, self.route)),
                 dict(routes=(replace(self.route, local_use_until=self.f.h.mono),)),
                 dict(routes=(replace(self.route, ca_pem=b''),)), dict(deadline=self.f.h.mono),
                 dict(deadline=float('nan')), dict(monotonic=None),
                 dict(expected_peer=ingress.PeerIdentity('bad\nidentity', 'synthetic'))]
        with patch.object(ingress.secrets, 'token_hex') as nonce:
            for case in cases:
                with self.subTest(case=list(case)), self.assertRaises(ingress.IngressError): self.receiver(**case)
            nonce.assert_not_called()

    def test_same_repo_name_with_different_numeric_pin_is_not_a_second_scope(self):
        other = replace(self.route, repository=replace(self.s.repo, repository_id=999))
        with self.assertRaises(ingress.IngressError): self.receiver(routes=(self.route, other))

    def test_request_is_a_copy_and_all_handles_redact_and_deny_serialization_copy(self):
        r = self.receiver(); request = r.request(); request['target']['run_id'] = '456'
        request['repositories'].append('synthetic/other')
        self.assertEqual(CONTEXT.run_id, r.request()['target']['run_id'])
        m = MemoryChannel(self.frame(r), self.peer); bundle = r.receive(m.channel)
        self.addCleanup(bundle.close)
        for value in (r, m.channel, bundle):
            text = str(value)
            for secret in (fixture.TOKEN, self.f.credential.token, '192.0.2.10', 'BEGIN CERTIFICATE'):
                self.assertNotIn(secret, text)
            for operation in (copy.copy, copy.deepcopy, pickle.dumps):
                with self.assertRaises(ingress.IngressError): operation(value)

    def test_success_exact_bytes_fragmentation_once_and_local_channel_cleanup(self):
        r = self.receiver(); m = MemoryChannel(self.frame(r), self.peer, chunk=1)
        bundle = r.receive(m.channel); self.addCleanup(bundle.close)
        self.assertEqual(self.f.credential.token, bundle._rosa)
        self.assertEqual(fixture.TOKEN, bundle._sources[0].token)
        self.assertEqual(self.route.repository, bundle._sources[0].repository)
        self.assertFalse(bundle.execution_authorized)
        self.assertEqual((1, 1), (m.auth_calls, m.close_calls))
        self.assertGreater(m.read_calls, 4)
        self.assertTrue(m.channel.close()); self.assertEqual(1, m.close_calls)
        with self.assertRaisesRegex(ingress.IngressError, 'CONSUMED'): r.request()

    def test_peer_type_or_principal_transport_mismatch_precedes_secret_read(self):
        for peer in (None, True, 'synthetic-issuer', replace(self.peer, principal='other'),
                     replace(self.peer, transport_reference='other')):
            r = self.receiver(); m = MemoryChannel(self.frame(r), peer)
            self.error(r, m, 'PEER_REJECTED'); self.assertEqual(0, m.read_calls)

    def test_auth_failure_is_fixed_redacted_and_owned_channel_closed(self):
        r = self.receiver(); m = MemoryChannel(self.frame(r), self.peer)
        def bad(*unused): raise ValueError(fixture.TOKEN)
        m.on_auth = bad; self.error(r, m, '^INGRESS_RECEIVE_FAILED$'); self.assertEqual(0, m.read_calls)

    def test_auth_callback_cannot_rewrite_stored_target(self):
        r = self.receiver(); request = r.request(); request['target']['run_id'] = '456'
        raw = ingress.encode_frame(request, rosa_token=self.f.credential.token,
            source_tokens=[{'repository':self.s.repo.owner+'/'+self.s.repo.name,'token':fixture.TOKEN}])
        m = MemoryChannel(raw, self.peer)
        def alter(request, deadline): request['target']['run_id'] = '456'; return self.peer
        m.on_auth = alter; self.error(r, m, 'TARGET_REJECTED')

    def test_replay_to_another_receiver_and_changed_job_binding_nonce_rejected(self):
        r1 = self.receiver(); old = self.frame(r1); r2 = self.receiver()
        self.error(r2, MemoryChannel(old, self.peer), 'TARGET_REJECTED')
        for change in ('run_id','attempt','workflow_sha','binding_key','nonce'):
            r = self.receiver(); value = self.payload(r)
            if change=='nonce': value['nonce']='0'*64
            else:value['target'][change]={'run_id':'456','attempt':True,'workflow_sha':'e'*40,'binding_key':'0'*64}[change]
            self.error(r, MemoryChannel(self.pack(value),self.peer), 'TARGET_REJECTED')

    def test_duplicate_unknown_keys_nested_duplicates_nan_and_invalid_utf8_rejected(self):
        changes = [lambda b:b[:-1]+b',"peer":"forged"}',
                   lambda b:b[:-1]+b',"schema":"duplicate"}',
                   lambda b:b.replace(b'"attempt":1',b'"attempt":1,"attempt":1'),
                   lambda b:b.replace(b'"attempt":1',b'"attempt":NaN'),
                   lambda b:b'\xff',lambda b:b'[]',lambda b:b'null',
                   lambda b:b.decode().encode('utf-16'),lambda b:b.decode().encode('utf-32')]
        for change in changes:
            receiver=self.receiver();raw=change(self.frame(receiver)[4:])
            self.error(receiver, MemoryChannel(struct.pack('!I',len(raw))+raw,self.peer),'MESSAGE_INVALID')

    def test_header_zero_overflow_truncation_extra_frame_and_no_eof(self):
        for kind in ('zero','large','header_short','body_short','trailing','second'):
            r=self.receiver(); frame=self.frame(r)
            raw={'zero':b'\0'*4,'large':struct.pack('!I',ingress.MAX_MESSAGE_BYTES+1),
                 'header_short':frame[:3],'body_short':frame[:-1],
                 'trailing':frame+b'x','second':frame+frame}[kind]
            self.error(r,MemoryChannel(raw,self.peer),'FRAME_INVALID')

    def test_transport_read_contract_type_oversize_and_exception_are_not_payloads(self):
        for bad in ('text',bytearray(b'bytes'),b'x'*100):
            r=self.receiver();m=MemoryChannel(self.frame(r),self.peer);m.on_read=lambda *a,v=bad:v
            self.error(r,m,'FRAME_INVALID')
        r=self.receiver();m=MemoryChannel(self.frame(r),self.peer)
        def bad(*args):raise OSError(fixture.TOKEN)
        m.on_read=bad;self.error(r,m,'^INGRESS_RECEIVE_FAILED$')

    def test_source_order_missing_extra_and_rosa_source_alias_rejected(self):
        for mode in ('missing','extra','repo','alias'):
            r=self.receiver();v=self.payload(r)
            if mode=='missing':v['source_tokens']=[]
            if mode=='extra':v['source_tokens']*=2
            if mode=='repo':v['source_tokens'][0]['repository']='synthetic/unknown'
            if mode=='alias':v['source_tokens'][0]['token']=v['rosa_token']
            self.error(r,MemoryChannel(self.pack(v),self.peer))

    def test_token_boundaries_whitespace_nonascii_bool_size_and_encoder_rejection(self):
        bads=[None,True,123,'','short','x'*4097,'SYNTHETIC TOKEN HAS SPACES','비밀'*20,fixture.TOKEN+'\n']
        for token in bads:
            r=self.receiver();v=self.payload(r);v['source_tokens'][0]['token']=token
            self.error(r,MemoryChannel(self.pack(v),self.peer),'CREDENTIAL_REJECTED')
            with self.assertRaises(ingress.IngressError):self.frame(self.receiver(),source_tokens=[{'repository':self.s.repo.owner+'/'+self.s.repo.name,'token':token}])
        for token in ('', True, 'x'*16385, self.f.credential.token+'\n'):
            r=self.receiver();v=self.payload(r);v['rosa_token']=token
            self.error(r,MemoryChannel(self.pack(v),self.peer),'CREDENTIAL_REJECTED')

    def test_source_equal_token_across_repositories_allowed_but_wrong_order_not(self):
        other=replace(self.route,repository=replace(self.s.repo,name='synthetic-other',repository_id=999))
        r=self.receiver(routes=(self.route,other));m=MemoryChannel(self.frame(r),self.peer)
        b=r.receive(m.channel);self.addCleanup(b.close)
        self.assertEqual([fixture.TOKEN,fixture.TOKEN],[x.token for x in b._sources])
        r=self.receiver(routes=(self.route,other));v=self.payload(r);v['source_tokens'].reverse()
        self.error(r,MemoryChannel(self.pack(v),self.peer),'SOURCE_REJECTED')

    def test_auth_read_or_parse_deadline_crossing_and_clock_regression(self):
        r=self.receiver();m=MemoryChannel(self.frame(r),self.peer)
        def late(*unused):self.f.h.mono=self.deadline;return self.peer
        m.on_auth=late;self.error(r,m,'DEADLINE_EXCEEDED');self.assertEqual(0,m.read_calls)
        self.f.h.mono=100;r=self.receiver(deadline=105);m=MemoryChannel(self.frame(r),self.peer)
        def late_read(size,deadline):self.f.h.mono=deadline;return b'\0'*size
        m.on_read=late_read;self.error(r,m,'DEADLINE_EXCEEDED')
        self.f.h.mono=100;r=self.receiver(deadline=105);m=MemoryChannel(self.frame(r),self.peer)
        def rewind(*unused):self.f.h.mono-=1;return self.peer
        m.on_auth=rewind;self.error(r,m,'CLOCK_INVALID')

    def test_cleanup_false_exception_and_reentrant_close_never_return_bundle(self):
        for close in (lambda:False,lambda:None,Mock(side_effect=OSError(fixture.TOKEN))):
            r=self.receiver();m=MemoryChannel(self.frame(r),self.peer);m.on_close=close
            self.error(r,m,'CLEANUP_FAILED');self.assertFalse(m.channel.close());self.assertEqual(1,m.close_calls)
        r=self.receiver();m=MemoryChannel(self.frame(r),self.peer)
        m.on_close=lambda:m.channel.close()
        self.error(r,m,'CLEANUP_FAILED');self.assertEqual(1,m.close_calls)

    def test_parse_or_successful_cleanup_cannot_extend_original_deadline(self):
        for stage in ('parse','close'):
            self.f.h.mono=100;r=self.receiver(deadline=105);m=MemoryChannel(self.frame(r),self.peer)
            made=[];decode=r._decode
            def capture(raw):
                result=decode(raw);made.append(result)
                if stage=='parse':self.f.h.mono=105
                return result
            r._decode=capture
            if stage=='close':
                def late_close():self.f.h.mono=105;return True
                m.on_close=late_close
            self.error(r,m,'DEADLINE_EXCEEDED')
            self.assertEqual(1,len(made));self.assertTrue(made[0]._used)
            self.assertIsNone(made[0]._rosa);self.assertIsNone(made[0]._sources)

    def test_interrupt_closes_owned_channel_and_redacts_interrupt_arguments(self):
        r=self.receiver();m=MemoryChannel(self.frame(r),self.peer)
        def interrupt(*args):raise KeyboardInterrupt(fixture.TOKEN)
        m.on_read=interrupt
        with self.assertRaises(KeyboardInterrupt) as e:r.receive(m.channel)
        self.assertEqual((),e.exception.args);self.assertEqual(1,m.close_calls)

    def test_invalid_borrowed_or_preclosed_channel_never_arbitrary_close(self):
        r=self.receiver();borrowed=Mock()
        with self.assertRaisesRegex(ingress.IngressError,'CHANNEL_INVALID'):r.receive(borrowed)
        borrowed.close.assert_not_called()
        r=self.receiver();m=MemoryChannel(self.frame(r),self.peer);self.assertTrue(m.channel.close())
        self.error(r,m,'CHANNEL_INVALID');self.assertEqual(1,m.close_calls)

    def test_two_threads_cannot_receive_same_nonce_twice(self):
        r=self.receiver();m=MemoryChannel(self.frame(r),self.peer)
        entered=threading.Event();release=threading.Event();results=[]
        def auth(*unused):entered.set();self.assertTrue(release.wait(3));return self.peer
        m.on_auth=auth
        def receive():
            try:results.append(r.receive(m.channel))
            except BaseException as e:results.append(e)
        t=threading.Thread(target=receive);t.start();self.assertTrue(entered.wait(3))
        other=MemoryChannel(b'',self.peer)
        try:
            with self.assertRaisesRegex(ingress.IngressError,'CONSUMED'):r.receive(other.channel)
            self.assertEqual(0,other.close_calls)  # Rejected before ownership transfer.
        finally:release.set();t.join(3);other.channel.close()
        self.assertFalse(t.is_alive());self.assertEqual(1,len(results));self.assertIsInstance(results[0],ingress.ReceivedCredentials)
        results[0].close();self.assertEqual(1,m.close_calls)

    def test_successful_bundle_build_preserves_outer_deadline_and_detaches_secrets(self):
        r=self.receiver();b=r.receive(MemoryChannel(self.frame(r),self.peer).channel)
        with patch.object(handoff,'build_owned_invocation',return_value='SYNTHETIC_INVOCATION') as build:
            value=b.build_invocation(replace(CONTEXT),'deploy',config=self.f.config(),plan=self.f.plan(),
                finish_slot=self.f.slot,wire=self.s.wire,handoff_deadline=self.deadline+100)
        self.assertEqual('SYNTHETIC_INVOCATION',value)
        self.assertEqual(self.deadline,build.call_args.kwargs['handoff_deadline'])
        self.assertEqual(self.f.credential.token,build.call_args.kwargs['rosa_token'])
        self.assertIsNone(b._rosa);self.assertIsNone(b._sources)
        with self.assertRaisesRegex(ingress.IngressError,'CONSUMED'):b.build_invocation(CONTEXT,'deploy',config=self.f.config(),plan=self.f.plan(),finish_slot=self.f.slot,wire=self.s.wire,handoff_deadline=self.deadline)

    def test_bundle_invalid_context_binding_or_expiry_never_calls_handoff_and_consumes(self):
        for kind in ('context','binding','expiry','bool_attempt'):
            self.f.h.mono=100;r=self.receiver(deadline=105);b=r.receive(MemoryChannel(self.frame(r),self.peer).channel)
            ctx=replace(CONTEXT,run_id='456') if kind=='context' else replace(CONTEXT,attempt=True) if kind=='bool_attempt' else CONTEXT
            config=replace(self.f.config(),binding=replace(self.f.h.binding,route_host='other.invalid')) if kind=='binding' else self.f.config()
            if kind=='expiry':self.f.h.mono=105
            with patch.object(handoff,'build_owned_invocation') as build,self.assertRaises(ingress.IngressError):
                b.build_invocation(ctx,'deploy',config=config,plan=self.f.plan(),finish_slot=self.f.slot,wire=self.s.wire,handoff_deadline=200)
            build.assert_not_called();self.assertTrue(b._used);self.assertIsNone(b._rosa)

    def test_failed_handoff_or_explicit_bundle_close_never_preserves_reusable_tokens(self):
        r=self.receiver();b=r.receive(MemoryChannel(self.frame(r),self.peer).channel)
        with patch.object(handoff,'build_owned_invocation',side_effect=ValueError(fixture.TOKEN)),self.assertRaisesRegex(ingress.IngressError,'^INGRESS_INVOCATION_REJECTED$'):
            b.build_invocation(CONTEXT,'deploy',config=self.f.config(),plan=self.f.plan(),finish_slot=self.f.slot,wire=self.s.wire,handoff_deadline=self.deadline)
        self.assertIsNone(b._rosa);self.assertTrue(b._used)
        r=self.receiver();b=r.receive(MemoryChannel(self.frame(r),self.peer).channel);b.close();b.close()
        with patch.object(handoff,'build_owned_invocation') as build,self.assertRaisesRegex(ingress.IngressError,'CONSUMED'):
            b.build_invocation(CONTEXT,'deploy',config=self.f.config(),plan=self.f.plan(),finish_slot=self.f.slot,wire=self.s.wire,handoff_deadline=self.deadline)
        build.assert_not_called()

    def test_bundle_handoff_interrupt_also_detaches_and_redacts_arguments(self):
        for error,expected in ((KeyboardInterrupt(fixture.TOKEN),()),(SystemExit(fixture.TOKEN),(130,))):
            r=self.receiver();b=r.receive(MemoryChannel(self.frame(r),self.peer).channel)
            with patch.object(handoff,'build_owned_invocation',side_effect=error),self.assertRaises(type(error)) as e:
                b.build_invocation(CONTEXT,'deploy',config=self.f.config(),plan=self.f.plan(),finish_slot=self.f.slot,wire=self.s.wire,handoff_deadline=self.deadline)
            self.assertEqual(expected,e.exception.args);self.assertIsNone(b._rosa);self.assertIsNone(b._sources)

    @unittest.skipUnless(sys.platform=='linux','actual anonymous-pipe/driver integration is Linux only')
    def test_real_pipe_driver_source_factory_finish_with_memory_ingress(self):
        from delivery_source_roles import plan_source_roles
        import delivery_source_session
        p=patch.object(delivery_source_session,'pipe_identity',new=handoff._identity)
        p.start();self.addCleanup(p.stop)  # Real Linux fstat, replacing only the Windows fixture shim.
        r=self.receiver();b=r.receive(MemoryChannel(self.frame(r),self.peer,chunk=7).channel)
        def wire(pool):
            self.s.wire(pool)
            return plan_source_roles(pool,plan=self.f.plan(),finish_repositories=(self.s.repo,self.s.repo))
        invocation=b.build_invocation(CONTEXT,'deploy',config=self.f.config(),plan=self.f.plan(),
            finish_slot=self.f.slot,wire=wire,handoff_deadline=self.deadline)
        owned=[invocation.receiver]+[c.receiver for c in invocation.source_session.channels]
        original=runtime.RuntimeAuthorityFactory
        def factory(*a,**kw):return original(*a,**kw,wall=lambda:self.f.h.now,monotonic=lambda:self.f.h.mono)
        def build(config,*,credential,authority_factory):return self.f.app(factory=authority_factory)
        out=io.StringIO()
        with patch.object(runtime,'RuntimeAuthorityFactory',side_effect=factory),patch.object(site,'build_application',side_effect=build),contextlib.redirect_stdout(out):
            rc=job.main(['deploy','--execute'],declaration=job.JobSite(RUNNER,lambda *a:invocation),
                context=CONTEXT,revision=lambda:CONTEXT.workflow_sha,identity=lambda:RUNNER)
        value=json.loads(out.getvalue());self.assertEqual((0,'SESSION_COMPLETED','RELEASED'),(rc,value['status'],value['result']['fence']),value)
        self.assertNotIn(fixture.TOKEN,out.getvalue());self.assertTrue(invocation._closed)
        self.assertTrue(all(r._fd is None for r in owned))
        self.assertTrue(all(t._closed for t in self.s.transports))


if __name__=='__main__':unittest.main()
