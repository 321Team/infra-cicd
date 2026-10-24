"""SYNTHETIC native bytes; real network/issuance forbidden. No Runtime proof."""
import base64
import copy
from dataclasses import replace
import json
import os
from pathlib import Path
import pickle
import socket
import struct
import sys
import threading
import time
from types import MappingProxyType
import unittest
from unittest.mock import Mock, patch

import delivery_adapter as core
import delivery_crosshost_ingress as ingress
import delivery_runtime_site as runtime
import delivery_site as site
import delivery_ssh_bridge as ssh
import delivery_source_session as source
import delivery_token_materializer as materializer
import test_delivery_token_materializer as fixture
import test_delivery_crosshost_ingress as frames
import test_delivery_token_request as tokens
import test_delivery_ssh_bridge as local
import test_delivery_unix_ingress as unix_fixture
from test_delivery_semantic_authority import SemanticFixture
from test_delivery_adapter import Harness
from delivery_platform_readback import ExpectedSubject
from test_delivery_job import CONTEXT
from delivery_object_reader import BearerCredential


class NativeOriginalTests(unittest.TestCase):
    def setUp(self):
        self.f=fixture.NativeMaterializerTests('runTest');self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.raw=core.encoded(self.f.tokens.response())
        self.source=self.f.source
        self.source._native_lock=threading.RLock()
        self.source._native=source.NativeIssuanceOriginal(CONTEXT,self.f.h.binding,
            self.f.credential.fingerprint,self.raw)
        self.source._native.check()
        self.source._handles=[];self.source.channels=();self.source._readers={}
        self.source.wire=None;self.source.cleanup_ok=True
        self.addCleanup(self.source.close)

    def take(self, **changes):
        args=dict(context=CONTEXT,binding=self.f.h.binding,credential=self.f.credential,
                  deadline=self.source.deadline)
        args.update(changes)
        return self.source.take_native_original(**args)

    def test_actual_reader_materializes_original_once_through_existing_prepared_plan(self):
        value=self.f.build(read_original=source.native_original_reader(self.f.h.binding))
        plan=self.f.call(value)
        self.assertEqual(self.f.credential.fingerprint,plan.issuer.facts.credential_fingerprint)
        self.assertIsNone(self.source._native)
        self.assertEqual((1000,1900),(plan.issuer.facts.issued_at,plan.issuer.facts.expires_at))
        with self.assertRaises(source.SourceSessionError):self.take()
        self.f.read.assert_not_called()

    def test_foreign_context_binding_credential_or_deadline_consumes_without_fallback(self):
        for changes in (dict(context=replace(CONTEXT,run_id='123456')),
            dict(binding=replace(self.f.h.binding,run_id='other')),
            dict(credential=BearerCredential('SYNTHETIC_OTHER_TOKEN')),
            dict(deadline=self.source.deadline+1)):
            self.source._native=source.NativeIssuanceOriginal(CONTEXT,self.f.h.binding,
                self.f.credential.fingerprint,self.raw)
            with self.subTest(changes=list(changes)),self.assertRaises(source.SourceSessionError) as error:
                self.take(**changes)
            self.assertIsNone(error.exception.__context__)
            self.assertNotIn(self.f.credential.token,str(error.exception))
            self.assertIsNone(self.source._native)
            with self.assertRaises(source.SourceSessionError):self.take()

    def test_expired_or_closed_or_missing_source_never_returns_raw(self):
        value=self.source._native
        self.f.h.mono=self.source.deadline
        with self.assertRaises(source.SourceSessionError):self.take()
        self.source._native=value
        self.source.close()
        with self.assertRaises(source.SourceSessionError):self.take()
        self.assertIsNone(self.source._native)

    def test_two_threads_only_one_take_succeeds(self):
        outcomes=[];barrier=threading.Barrier(3)
        def run():
            barrier.wait()
            try:outcomes.append(self.take())
            except source.SourceSessionError:outcomes.append(None)
        threads=[threading.Thread(target=run) for _ in range(2)]
        for thread in threads:thread.start()
        barrier.wait()
        for thread in threads:thread.join(2);self.assertFalse(thread.is_alive())
        self.assertEqual(1,outcomes.count(self.raw));self.assertEqual(1,outcomes.count(None))

    def test_reentrant_close_during_clock_denies_return_and_releases_original(self):
        def now():self.source.close();return self.f.h.mono
        self.source.clock=now
        with self.assertRaises(source.SourceSessionError):self.take()
        self.assertIsNone(self.source._native)

    def test_reader_close_after_take_does_not_return_materialized_plan(self):
        read=source.native_original_reader(self.f.h.binding)
        def close(*args):
            raw=read(*args);self.source.close();return raw
        with self.assertRaises(materializer.MaterializationError):self.f.call(self.f.build(read_original=close))

    def test_legacy_missing_original_stops_materializer_instead_of_fabricating(self):
        self.source._native=None
        value=self.f.build(read_original=source.native_original_reader(self.f.h.binding))
        with self.assertRaises(materializer.MaterializationError):self.f.call(value)
        self.f.read.assert_not_called()

    def test_native_record_size_repr_copy_pickle_and_no_constructor_io(self):
        value=self.source._native
        self.assertNotIn(self.f.credential.token,repr(value))
        for operation in (copy.copy,copy.deepcopy,pickle.dumps):
            with self.assertRaises(source.SourceSessionError):operation(value)
        for raw in (b'',b'x'*(source.MAX_NATIVE_ORIGINAL_BYTES+1),{},'secret'):
            with self.subTest(kind=type(raw)),self.assertRaises(source.SourceSessionError):
                replace(value,raw=raw).check()


class OriginalFrameTests(unittest.TestCase):
    def setUp(self):
        self.f=frames.IngressTests('runTest');self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.token=self.f.f.credential.token
        self.tokens=[{'repository':self.f.s.repo.owner+'/'+self.f.s.repo.name,'token':'SYNTHETIC_SOURCE_TOKEN_12345'}]

    def frame(self, r, raw):
        return ingress.encode_frame(r.request(),rosa_token=self.token,source_tokens=self.tokens,native_original=raw)

    def receive(self, r, frame, **kwargs):
        channel=frames.MemoryChannel(frame,self.f.peer,**kwargs)
        value=r.receive(channel.channel)
        self.assertEqual(1,channel.close_calls)
        self.addCleanup(value.close)
        return value

    def test_v1_stays_legacy_v2_preserves_exact_bytes_over_single_byte_reads(self):
        r=self.f.receiver();value=self.receive(r,self.frame(r,b'\x00SYNTHETIC\xff'),chunk=1)
        self.assertEqual(b'\x00SYNTHETIC\xff',value._native)
        value.close();self.assertIsNone(value._native)
        r=self.f.receiver();value=self.receive(r,self.frame(r,None))
        self.assertIsNone(value._native)

    def test_strict_base64_required_schema_duplicate_and_no_downgrade(self):
        for mutation in ('missing','space','padding','oversize','empty','v1extra','duplicate'):
            r=self.f.receiver();frame=self.frame(r,b'SYNTHETIC')
            obj=json.loads(frame[4:])
            if mutation=='missing':del obj['native_original']
            elif mutation=='space':obj['native_original']+='\n'
            elif mutation=='padding':obj['native_original']='eB=='
            elif mutation=='oversize':obj['native_original']='e'*65540
            elif mutation=='empty':obj['native_original']=''
            elif mutation=='v1extra':obj['schema']=ingress.MESSAGE_SCHEMA
            raw=core.encoded(obj)
            if mutation=='duplicate':raw=raw[:-1]+b',"native_original":"eA=="}'
            with self.subTest(mutation=mutation),self.assertRaises(ingress.IngressError):
                self.receive(r,struct.pack('!I',len(raw))+raw)

    def test_raw_and_frame_bounds_trailing_bytes_nonce_and_peer_remain_enforced(self):
        r=self.f.receiver();self.receive(r,self.frame(r,b'x'*source.MAX_NATIVE_ORIGINAL_BYTES))
        with self.assertRaises(ingress.IngressError):self.frame(self.f.receiver(),b'x'*(source.MAX_NATIVE_ORIGINAL_BYTES+1))
        for mode in ('trailing','nonce','peer'):
            r=self.f.receiver();frame=self.frame(r,b'SYNTHETIC')
            if mode=='trailing':frame+=b'x'
            if mode=='nonce':
                obj=json.loads(frame[4:]);obj['nonce']='f'*64;raw=core.encoded(obj);frame=struct.pack('!I',len(raw))+raw
            channel=frames.MemoryChannel(frame,ingress.PeerIdentity('foreign','synthetic') if mode=='peer' else self.f.peer)
            with self.subTest(mode=mode),self.assertRaises(ingress.IngressError):r.receive(channel.channel)
            self.assertEqual(1,channel.close_calls)


@unittest.skipUnless(sys.platform=='linux' and hasattr(os,'geteuid') and os.geteuid()>0,
                     'nonroot Linux local helper/pipes only; no real SSH or Runtime')
class NativeHelperReaderTests(SemanticFixture, unittest.TestCase):
    def test_real_local_helper_frame_owned_job_pipes_and_native_reader_materializer(self):
        t=tokens.TokenRequestTests('runTest');t.setUp();self.addCleanup(t.doCleanups)
        self.context(Harness(),BearerCredential(t.token()))
        t.f.binding=self.h.binding
        repo=self.pins['platform'].repository
        peer=ingress.PeerIdentity('synthetic-issuer','synthetic-private-ssh')
        end=time.monotonic()+5
        route=ingress.SourceRoute(repo,end,self.issuer.ca_pem,'192.0.2.10')
        receiver=ingress.CrossHostIngress(context=CONTEXT,binding=self.h.binding,
            expected_peer=peer,routes=(route,),deadline=end,monotonic=time.monotonic)
        self.addCleanup(receiver.close)
        f=local.BridgeLinuxTests('runTest')
        with patch.object(unix_fixture,'request',side_effect=receiver.request):f.setUp()
        self.addCleanup(f.doCleanups);self.addCleanup(f.tearDown)
        t.request=receiver.request
        supplied=[{'repository':repo.owner+'/'+repo.name,'token':'SYNTHETIC_SOURCE_TOKEN_12345'}]
        supplier=t.supplier(issuer=t.client(deadline=f.deadline,monotonic=time.monotonic),
                            request=f.request,source_tokens=supplied)
        with patch.object(ssh.subprocess,'Popen',side_effect=f.start), \
             patch.object(supplier._issuer,'_exchange',return_value=core.encoded(t.response())) as exchange:
            self.assertTrue(supplier.send_over_bridge(f.client(),include_original=True))
        exchange.assert_called_once();self.assertEqual(0,f.procs[0].returncode)
        with patch.object(socket,'socket',new=tokens.REAL_SOCKET):sock,_=f.listener.accept()
        def authenticate(request,deadline):
            import delivery_unix_ingress as unix
            pid,uid,_=unix._peer_credentials(sock)
            self.assertGreater(pid,0);self.assertEqual(os.geteuid(),uid)
            return peer  # Same local helper UID test, not remote SSH issuer proof.
        def read(n,deadline):sock.settimeout(deadline-time.monotonic());return sock.recv(n)
        def close():sock.close();return True
        bundle=receiver.receive(ingress.ReviewedIngressChannel(authenticate=authenticate,read=read,close=close))
        self.addCleanup(bundle.close)
        self.assertEqual(core.encoded(t.response()),bundle._native)
        selection=materializer.NativePlanSelection(self.grants['approval'],
            replace(self.grants['platform'],principal=t.claims['sub']),
            runtime.EvidenceAdoption(self.grants['producer'],self.producer),None,
            ExpectedSubject(t.claims['sub'],t.target.uid,('system:authenticated',)),
            t.target,self.issuer.ca_pem,self.issuer.endpoint_ip,
            self.issuer.rbac_reference,self.issuer.route_reference)
        native=materializer.NativeTokenMaterializer(binding=self.h.binding,context=CONTEXT,
            selection=selection,read_original=source.native_original_reader(self.h.binding),wall=lambda:t.wall)
        self.addCleanup(native.close)
        plan=runtime.PreparedAuthorityPlan(binding=self.h.binding,materialize=native)
        config=site.SiteConfiguration(self.h.binding,Path.cwd()/'SYNTHETIC_INPUT','render.yaml','contract.yaml',
            Path.cwd()/'SYNTHETIC_SOURCE',Path.cwd()/'SYNTHETIC_STORE',5)
        def wire(session):
            reader=session.reader(repo)
            return {'readers':tuple(runtime.SourceReaders(reader,reader) for _ in range(4))}
        inv=bundle.build_invocation(CONTEXT,'deploy',config=config,plan=plan,
            finish_slot=runtime.FinishAdoptionSlot(self.h.binding),wire=wire,handoff_deadline=end)
        self.addCleanup(inv.close)
        cap=inv.source_session.prepare(inv.receiver,inv.handoff_deadline)
        credential=inv.receiver.receive(cap)
        roles=inv.source_session.open(credential)
        result=plan.resolve(credential=credential,source=inv.source_session,roles=roles,deadline=cap)
        self.assertEqual(t.token(),credential.token)
        self.assertEqual((1000,1900),(result.issuer.facts.issued_at,result.issuer.facts.expires_at))
        self.assertIsNone(inv.source_session._native)
        self.assertEqual([],self.read_calls)  # No application/GitHub authority/claim/write executed.
        for args,kwargs in f.seen:
            self.assertNotIn(t.token(),str(args)+str(kwargs['env']))
        self.assertEqual(core.encoded(t.response()),supplier.take_receipt().raw)


if __name__=='__main__':unittest.main()
