"""SYNTHETIC code adoption + actual composition/session; all network forbidden.

MemoryStore is not POSIX durability evidence. Existing Linux store tests cover
fsync/claims. No actual credentials, host values, issuers or GO are created here.
"""
import contextlib
from dataclasses import replace
import io
import json
import os
import socket
import unittest
from unittest.mock import Mock, patch

import delivery_adapter as core
import delivery_provider as provider
import delivery_runtime_site as runtime
import delivery_semantic_authority as semantic
import delivery_site as site
from delivery_credential_channel import PipeCredentialReceiver
from delivery_platform_readback import PlatformReadback
from delivery_verifier import Progress, VerificationError
import test_delivery_provider as fixture
import test_delivery_nine_objects as nine_fixture
from delivery_object_reader import ObjectReader, Observation
from test_delivery_adapter import Harness
from test_delivery_objects import owned
from test_delivery_object_reader import absent
from test_delivery_semantic_authority import SemanticFixture


class RuntimeSiteTests(SemanticFixture, unittest.TestCase):
    def setUp(self):
        # Reuse existing offline server, real reconciler/provider/session and
        # synthetic memory store without rerunning its already-covered tests.
        self.p=fixture.ProviderTests('runTest');self.p.setUp();self.addCleanup(self.p.doCleanups)
        self.context(self.p.c.h,self.p.credential)
        self.captured=[]
        self.slot=runtime.FinishAdoptionSlot(self.h.binding)
        def inspect(reader,request,**unused):
            reader.check_request(request)
            return PlatformReadback(request,reader.subject,reader.credential,
                reader.credential.fingerprint,reader._ip,reader.capabilities,())
        p=patch.object(runtime.PlatformObservationReader,'inspect',new=inspect)
        p.start();self.addCleanup(p.stop)

    def app(self, *, install=True, factory=None, finalize=True):
        actual_factory=factory or self.factory(self.slot)
        def build(binding,credential,progress):
            bundle=actual_factory(binding,credential,progress)
            self.captured.append((bundle,progress))
            return bundle
        if install:
            def sender_change(sender):
                old=sender.close
                def close():
                    old()
                    p=self.captured[0][1]()
                    if json.loads(p.journal)['status']=='ROLLOUT_OBSERVED':
                        facts=semantic.FinishFacts(self.h.binding.run_id,self.h.binding.action,
                            p.key,self.h.now,'synthetic:actual-followup','synthetic:actual-exclusive-end')
                        self.slot.install(runtime.EvidenceAdoption(self.grants['finish'],facts),p)
                return replace(sender,close=close)
            self.p.c.sender_change=sender_change
        inputs=provider.RunInputs(self.h.binding,self.h.render,self.h.contract,
            'UNUSED_SYNTHETIC_SOURCE','UNUSED_SYNTHETIC_STORE',5,finalize)
        return provider.OperationalApplication(inputs,self.credential,authority_factory=build,
            wall=lambda:self.h.now,monotonic=lambda:self.h.mono,sleep=self.h.sleep)

    def test_real_factory_deploy_progress_and_post_run_finish_install_complete(self):
        out=self.app().run('deploy')
        self.assertEqual('SESSION_COMPLETED',out['status'],out)
        self.assertEqual('RELEASED',out['fence']);self.assertEqual(1,self.p.c.storage.complete_calls)
        self.assertEqual(['PATCH']*6+['GET']*2,[r.method for r in self.h.requests])
        self.assertEqual('NOT_VERIFIED',out['runtime'])
        self.assertEqual('NOT_VERIFIED',out['delivery_gate'])
        bundle,progress=self.captured[0]
        self.assertTrue(bundle.closed);self.assertIsNone(bundle.adapters)
        self.assertEqual(6,len(progress().responses))

    def test_missing_actual_finish_is_not_fabricated_and_fence_retained(self):
        out=self.app(install=False).run('deploy')
        self.assertEqual('STOPPED',out['status'],out)
        self.assertEqual('COMPLETION_DENIED',out['error_code'])
        self.assertEqual('RETAINED',out['fence']);self.assertEqual(0,self.p.c.storage.complete_calls)
        self.assertIsNotNone(self.p.c.storage.active)
        self.assertFalse(any(pin.path=='finish' for _,pin in self.read_calls))
        self.assertTrue(self.captured[0][0].closed)

    def test_nine_object_site_composition_keeps_exact_profile_through_finish(self):
        self.context(nine_fixture.nine(self.h),self.credential)
        self.slot=runtime.FinishAdoptionSlot(self.h.binding)
        def server(request):
            out=Harness.send(self.h,request)
            if request.method=='PATCH':
                out=owned(out,core.objects_for(self.h.binding).index(core.identity(out)))
                self.h.objects[request.path.split('?')[0]]=out
            return out
        self.h.send=server
        def read(reader,request,key):
            reader.check_request(request)
            obj=self.h.objects.get(core.path_for(key,self.h.binding))
            return Observation(key,404 if obj is None else 200,core.encoded(absent(key) if obj is None else obj))
        with patch.object(ObjectReader,'read',new=read):out=self.app().run('deploy')
        self.assertEqual('SESSION_COMPLETED',out['status'],out)
        self.assertEqual(['PATCH']*9+['GET']*3,[r.method for r in self.h.requests])
        self.assertEqual(9,len(self.captured[0][1]().responses))
        self.assertEqual('RELEASED',out['fence'])

    def test_no_finalize_retains_fence_even_after_rollout_and_does_not_require_finish(self):
        out=self.app(install=False,finalize=False).run('deploy')
        self.assertEqual('REVIEW_REQUIRED',out['status'],out)
        self.assertEqual('RETAINED',out['fence']);self.assertEqual(0,self.p.c.storage.complete_calls)
        self.assertFalse(any(pin.path=='finish' for _,pin in self.read_calls))

    def test_preflight_is_read_only_no_write_sender_claim_or_finish(self):
        out=self.app(install=False).run('preflight')
        self.assertEqual('PREFLIGHT_OBSERVED',out['status'],out)
        self.assertEqual([],self.h.requests);self.assertEqual([],self.p.c.storage.claims)
        self.assertEqual(0,self.p.c.sender_calls);self.assertTrue(self.captured[0][0].closed)

    def test_readback_identity_or_capability_denial_prevents_claim_and_sender(self):
        with patch.object(runtime.PlatformObservationReader,'inspect',side_effect=ValueError('SECRET')):
            out=self.app().run('deploy')
        self.assertEqual('PREFLIGHT_DENIED',out['error_code'],out)
        self.assertEqual(0,self.p.c.sender_calls);self.assertEqual([],self.p.c.storage.claims)
        self.assertNotIn('SECRET',json.dumps(out))
        self.assertTrue(self.captured[0][0].closed)

    def test_existing_object_is_not_adopted_as_first_deployment(self):
        key=core.objects_for(self.h.binding)[0]
        self.h.objects[core.path_for(key,self.h.binding)]=json.loads(self.h.render)[0]
        out=self.app().run('deploy')
        self.assertEqual('PREFLIGHT_DENIED',out['error_code'],out)
        self.assertEqual([],self.p.c.storage.claims);self.assertEqual(0,self.p.c.sender_calls)

    def test_post_write_source_withdrawal_stops_without_release(self):
        def hook(r,p,o):
            if p.path=='control' and self.h.requests:return replace(o,body=b'SYNTHETIC_WITHDRAWN')
            return o
        self.read_hook=hook
        out=self.app().run('deploy')
        self.assertEqual('STOPPED',out['status'],out)
        self.assertEqual('RETAINED',out['fence']);self.assertEqual(0,self.p.c.storage.complete_calls)
        self.assertEqual(1,len(self.h.requests))

    def test_bundle_uses_exact_same_credential_and_actual_progress_all_roles(self):
        progress=lambda:self.progress
        factory=self.factory(self.slot);bundle=factory(self.h.binding,self.credential,progress)
        a=bundle.adapters
        self.assertIs(a.approval.credential,self.credential)
        self.assertIs(a.platform.reader.credential,self.credential)
        self.assertIs(a.producer.credential,self.credential)
        self.assertIs(a.baseline.reader.credential,self.credential)
        self.assertIs(a.finish.credential,self.credential)
        self.assertTrue(all(p is progress for p in (a.approval.progress,a.platform.reader.progress,
            a.producer.progress,a.baseline.progress,a.finish.progress)))
        observer=a.platform.reader;issuer=a.platform.resolver
        with patch.object(observer,'close',wraps=observer.close) as closed:
            bundle.close();bundle.close()
            self.assertEqual(1,closed.call_count)
        self.assertIsNone(issuer.credential)
        with self.assertRaises(runtime.RuntimeSiteError):factory(self.h.binding,self.credential,progress)

    def test_constructor_failure_and_keyboard_interrupt_close_all_partial_handles(self):
        for error in (ValueError('SECRET'),KeyboardInterrupt()):
            captured=[]
            original=runtime.ReviewedEvidenceSource
            def port(**kwargs):
                value=original(**kwargs);captured.append(value);return value
            with patch.object(runtime,'ReviewedEvidenceSource',side_effect=port),patch.object(
                runtime,'ObjectReader',side_effect=error):
                with self.subTest(error=type(error)),self.assertRaises((runtime.RuntimeSiteError,KeyboardInterrupt)):
                    self.factory()(self.h.binding,self.credential,lambda:self.progress)
            self.assertEqual(2,len(captured))
            self.assertTrue(all(p.credential is None and p.resolver is None for p in captured))
        # Shared readers remain caller-owned and can still read their originals.
        self.assertIsNotNone(self.reader._transport)

    def test_approval_constructor_failure_closes_inline_resolver(self):
        made=[];original=runtime.CodeAdoptedGrantResolver
        def resolver(**kwargs):
            value=original(**kwargs);made.append(value);return value
        with patch.object(runtime,'CodeAdoptedGrantResolver',side_effect=resolver),patch.object(
            runtime,'ReviewedApprovalAdapter',side_effect=ValueError('SECRET')):
            with self.assertRaises(runtime.RuntimeSiteError):
                self.factory()(self.h.binding,self.credential,lambda:self.progress)
        self.assertIsNone(made[0].credential);self.assertIsNone(made[0].reader)

    def test_mutually_exclusive_factory_and_legacy_paths(self):
        inputs=self.p.app().inputs
        for kwargs in ({},{'adapters':self.p.adapters,'authority_factory':lambda *a:None}):
            with self.assertRaises(VerificationError):
                provider.OperationalApplication(inputs,self.credential,**kwargs)
        self.assertIsInstance(provider.OperationalApplication(inputs,self.credential,self.p.adapters),
                              provider.OperationalApplication)

    def test_bundle_cleanup_attempts_all_handles_and_preserves_actual_fence_result(self):
        a,b=Mock(),Mock();b.close.side_effect=ValueError('SECRET')
        bundle=provider.AuthorityBundle(self.p.adapters,(a,b))
        with self.assertRaisesRegex(VerificationError,'AUTHORITY_CLEANUP_FAILED'):bundle.close()
        a.close.assert_called_once();b.close.assert_called_once();self.assertTrue(bundle.closed)
        def factory(binding,credential,progress):
            bundle=self.factory(self.slot)(binding,credential,progress)
            bad=Mock();bad.close.side_effect=ValueError('SECRET')
            bundle.owned+=(bad,)
            return bundle
        out=self.app(factory=factory).run('deploy')
        self.assertEqual('STOPPED',out['status']);self.assertEqual('AUTHORITY_CLEANUP_FAILED',out['error_code'])
        self.assertEqual('RELEASED',out['fence'])  # Never lie about an already recorded release.
        self.assertNotIn('SECRET',json.dumps(out))

    def test_first_deployment_refuses_restore_and_cross_binding_before_source_io(self):
        plan=self.plan()
        bad=replace(plan,approval=replace(plan.approval,binding=replace(self.h.binding,action='restore')))
        with self.assertRaisesRegex(runtime.RuntimeSiteError,'FIRST_DEPLOY_ONLY'):
            runtime.RuntimeAuthorityFactory(bad,tuple(runtime.SourceReaders(self.reader,self.reader)
                for _ in range(4)),self.slot)
        with self.assertRaisesRegex(runtime.RuntimeSiteError,'BINDING_MISMATCH'):
            runtime.RuntimeAuthorityFactory(replace(plan,producer=replace(plan.producer,
                grant=replace(plan.producer.grant,binding=replace(self.h.binding,run_id='other')))),
                tuple(runtime.SourceReaders(self.reader,self.reader) for _ in range(4)),self.slot)
        self.assertEqual([],self.read_calls)

    def prepare_restore(self):
        # Prior synthetic deployment produces existing objects. The restore has
        # its OWN explicit binding/run/adoption; no stale run is resumed.
        self.assertEqual('ROLLOUT_OBSERVED',self.h.run()['status'])
        for index,key in enumerate(core.objects_for(self.h.binding)):
            path=core.path_for(key,self.h.binding)
            self.h.objects[path]=owned(self.h.objects[path],index)
        previous=tuple(core.encoded(self.h.objects[core.path_for(k,self.h.binding)])
                       for k in core.objects_for(self.h.binding))
        self.h.requests.clear();self.h.rebind(action='restore',run_id='synthetic-approved-restore')
        self.context(self.h,self.credential);self.slot=runtime.FinishAdoptionSlot(self.h.binding)
        self.raws['baseline']=b'SYNTHETIC_REVIEWED_PREVIOUS_OBJECTS_AND_OWNERS'
        pin=replace(self.pins['producer'],path='baseline',sha256=core.digest(self.raws['baseline']))
        grant=replace(self.grants['approval'],original_pin=pin,adoption_reference='synthetic:baseline-adoption')
        record=runtime.BaselineAdoption(grant,previous)
        plan=replace(self.plan(),baseline=record)
        factory=runtime.RuntimeAuthorityFactory(plan,tuple(runtime.SourceReaders(self.reader,self.reader)
            for _ in range(5)),self.slot,wall=lambda:self.h.now,monotonic=lambda:self.h.mono)
        return record,factory

    def test_restore_requires_its_own_source_adoption_and_existing_baseline(self):
        record,factory=self.prepare_restore()
        out=self.app(factory=factory).run('restore')
        self.assertEqual('SESSION_COMPLETED',out['status'],out)
        self.assertEqual('RELEASED',out['fence']);self.assertEqual(6,len(out['receipt']['acknowledged']))
        self.assertTrue(any(pin.path=='baseline' for _,pin in self.read_calls))

    def test_restore_refuses_live_uid_or_spec_drift_without_adopting_get(self):
        record,factory=self.prepare_restore()
        self.h.objects[core.path_for(core.OBJECTS[0])]['metadata']['uid']='synthetic-replacement'
        out=self.app(factory=factory).run('restore')
        self.assertEqual('PREFLIGHT_DENIED',out['error_code'],out)
        self.assertEqual(0,self.p.c.sender_calls);self.assertEqual([],self.p.c.storage.claims)

    def test_restore_policy_cannot_admit_absence(self):
        record,factory=self.prepare_restore()
        plan=replace(factory.plan,baseline=replace(record,objects=(None,)+record.objects[1:]))
        rejected=runtime.RuntimeAuthorityFactory(plan,factory.readers,self.slot,
            wall=lambda:self.h.now,monotonic=lambda:self.h.mono)
        out=self.app(factory=rejected).run('restore')
        self.assertEqual('PROVIDER_INITIALIZATION_FAILED',out['error_code'],out)
        self.assertEqual(0,self.p.c.sender_calls);self.assertEqual([],self.p.c.storage.claims)

    def config(self):
        from pathlib import Path
        return site.SiteConfiguration(self.h.binding,Path.cwd()/'SYNTHETIC_INPUT','render.yaml','contract.yaml',
            Path.cwd()/'SYNTHETIC_SOURCE',Path.cwd()/'SYNTHETIC_STORE',5)

    def call_cli(self,args,**changes):
        kwargs=dict(config=self.config(),plan=self.plan(),readers=tuple(
            runtime.SourceReaders(self.reader,self.reader) for _ in range(4)),finish_slot=self.slot)
        kwargs.update(changes);out=io.StringIO()
        with contextlib.redirect_stdout(out):rc=runtime.main(args,**kwargs)
        return rc,json.loads(out.getvalue()) if out.getvalue() else None

    def test_status_performs_no_receive_build_source_or_claim(self):
        read,write=os.pipe();os.close(write);receiver=PipeCredentialReceiver(read)
        with patch.object(receiver,'receive',side_effect=AssertionError('RECEIVE_FORBIDDEN')),patch.object(
            site,'build_application',side_effect=AssertionError('BUILD_FORBIDDEN')):
            rc,result=self.call_cli(['status'],receiver=receiver)
        self.assertEqual(0,rc);self.assertEqual('NOT_RUN',result['runtime'])
        self.assertIsNone(receiver._fd);self.assertEqual([],self.read_calls)

    def test_main_default_action_mismatch_and_missing_channel_block(self):
        out=io.StringIO()
        with contextlib.redirect_stdout(out):self.assertEqual(2,runtime.main(['deploy']))
        self.assertEqual('RUNTIME_SITE_UNCONFIGURED',json.loads(out.getvalue())['error_code'])
        for args in (['restore'],['deploy']):
            rc,result=self.call_cli(args)
            self.assertEqual(2,rc);self.assertEqual('NOT_RUN',result['runtime'])
        self.assertEqual([],self.read_calls)

    def test_command_receives_once_then_builds_with_concrete_factory_and_closes(self):
        read,write=os.pipe();os.close(write);receiver=PipeCredentialReceiver(read)
        app=self.p.app(finalize=False)
        with patch.object(receiver,'receive',return_value=self.credential) as receive,patch.object(
            site,'build_application',return_value=app) as build,patch.object(runtime.cli,'main',return_value=0):
            self.assertEqual(0,self.call_cli(['deploy'],receiver=receiver,handoff_deadline=1001)[0])
        receive.assert_called_once_with(1001)
        self.assertIs(build.call_args.kwargs['credential'],self.credential)
        self.assertIsInstance(build.call_args.kwargs['authority_factory'],runtime.RuntimeAuthorityFactory)
        self.assertIsNone(receiver._fd)

    def test_bad_cli_arguments_are_redacted_and_owned_channel_closed(self):
        read,write=os.pipe();os.close(write);receiver=PipeCredentialReceiver(read);out=io.StringIO()
        with contextlib.redirect_stderr(out),self.assertRaises(SystemExit):
            runtime.main(['deploy','--token','SYNTHETIC_SECRET'],receiver=receiver)
        self.assertIsNone(receiver._fd);self.assertNotIn('SYNTHETIC_SECRET',out.getvalue())

    def test_build_failure_closes_channel_no_traceback_or_secret(self):
        read,write=os.pipe();os.close(write);receiver=PipeCredentialReceiver(read)
        with patch.object(receiver,'receive',return_value=self.credential),patch.object(
            site,'build_application',side_effect=ValueError('SYNTHETIC_SECRET')):
            rc,out=self.call_cli(['deploy'],receiver=receiver,handoff_deadline=1001)
        self.assertEqual(2,rc);self.assertEqual('RUNTIME_SITE_PREPARATION_FAILED',out['error_code'])
        self.assertIsNone(receiver._fd);self.assertNotIn('SYNTHETIC_SECRET',json.dumps(out))


if __name__=='__main__':unittest.main()
