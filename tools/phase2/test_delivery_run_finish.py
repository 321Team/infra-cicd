"""Native source + finish hook integration, entirely synthetic and offline.

Actual session/provider/adapter code runs with fake HTTP, memory store and clock.
No monkey-patched sender.close is used to install a completion record.
"""
import contextlib
from dataclasses import replace
import io
import json
import os
import unittest
from unittest.mock import Mock,patch

import delivery_runtime_site as runtime
import delivery_semantic_authority as semantic
from delivery_credential_channel import PipeCredentialReceiver
import test_delivery_runtime_site as prior
from test_delivery_actions_source import NativeApiFixture


class RunFinishTests(NativeApiFixture,unittest.TestCase):
    def setUp(self):
        self.base=prior.RuntimeSiteTests('runTest');self.base.setUp();self.addCleanup(self.base.doCleanups)
        self.api_setup(self.base.h,self.base.credential)
        self.captures=[]

    def capture(self,request):
        self.captures.append(request)
        self.assertEqual(1,self.base.p.c.sender_closes)
        journal=json.loads(request.progress.journal)
        self.assertEqual('ROLLOUT_OBSERVED',journal['status'])
        facts=semantic.FinishFacts(request.binding.run_id,request.binding.action,request.progress.key,
            self.h.now,'synthetic:followup-after-close','synthetic:exclusive-after-close')
        return runtime.EvidenceAdoption(self.base.grants['finish'],facts)

    def factory(self,capture=None):
        return self.base.factory(self.base.slot,producer_pins=self.pins,producer_run_reader=self.api_reader,
                                 finish_capture=self.capture if capture is None else capture)

    def app(self,capture=None):return self.base.app(install=False,factory=self.factory(capture))

    def test_native_gate_and_real_post_close_capture_complete_one_session(self):
        app=self.app();out=app.run('deploy')
        self.assertEqual('SESSION_COMPLETED',out['status'],out)
        self.assertEqual('RELEASED',out['fence']);self.assertEqual(1,len(self.captures))
        self.assertEqual(['PATCH']*6+['GET']*2,[r.method for r in self.h.requests])
        self.assertTrue(self.base.slot.installed)
        self.assertIs(self.captures[0].credential,self.credential)
        self.assertEqual(self.base.grants['producer'].binding.source_commit,self.h.binding.source_commit)
        self.assertNotEqual(self.workflow_sha,self.h.binding.source_commit)
        self.assertTrue(any('/jobs?' in path for path,_ in self.calls))
        self.assertIsNone(app._credential);self.assertEqual('NOT_VERIFIED',out['delivery_gate'])

    def test_failed_native_test_blocks_before_claim_sender_and_capture(self):
        self.hook=lambda path,obj:obj|{'conclusion':'failure'} if '/runs/101' in path else obj
        out=self.app().run('deploy')
        self.assertEqual('PREFLIGHT_DENIED',out['error_code'],out)
        self.assertEqual([],self.base.p.c.storage.claims);self.assertEqual(0,self.base.p.c.sender_calls)
        self.assertEqual([],self.captures);self.assertEqual([],self.h.requests)

    def test_successful_job_with_skipped_required_step_cannot_deploy(self):
        def skip(path,obj):
            if '/jobs?' in path:obj['jobs'][0]['steps'][0]['conclusion']='skipped'
            return obj
        self.hook=skip;out=self.app().run('deploy')
        self.assertEqual('PREFLIGHT_DENIED',out['error_code'],out)
        self.assertEqual(0,self.base.p.c.sender_calls);self.assertEqual([],self.base.p.c.storage.claims)

    def test_native_attempt_change_after_write_keeps_fence_without_retry(self):
        def rerun(path,obj):
            if self.h.requests and '/actions/runs/' in path:obj['run_attempt']=2
            return obj
        self.hook=rerun;out=self.app().run('deploy')
        self.assertEqual('STOPPED',out['status'],out)
        self.assertEqual('RETAINED',out['fence']);self.assertEqual(1,len(self.h.requests))
        self.assertEqual(0,self.base.p.c.storage.complete_calls);self.assertEqual([],self.captures)

    def test_late_future_changed_progress_or_untyped_capture_cannot_release(self):
        # Each subcase needs a new RUN/store; isolate the fixture like an actual
        # single invocation instead of retrying or resetting an acquired fence.
        for mode in ('late','future','progress','untyped','exception'):
            case=RunFinishTests('runTest');case.setUp()
            try:
                def capture(r):
                    record=case.capture(r)
                    if mode=='late':case.h.mono=r.deadline
                    elif mode=='future':record=replace(record,facts=replace(record.facts,observed_at=case.h.now+1))
                    elif mode=='progress':case.base.p.c.storage.journals[0].latest['status']='STOPPED'
                    elif mode=='untyped':return {'approved':True}
                    else:raise RuntimeError('SYNTHETIC_SECRET')
                    return record
                out=case.app(capture).run('deploy')
                with self.subTest(mode=mode):
                    self.assertEqual('COMPLETION_DENIED',out['error_code'],out)
                    self.assertEqual('RETAINED',out['fence']);self.assertEqual(0,case.base.p.c.storage.complete_calls)
                    self.assertFalse(case.base.slot.installed);self.assertEqual(1,len(case.captures))
                    self.assertNotIn('SYNTHETIC_SECRET',json.dumps(out))
                    self.assertFalse(any(pin.path=='finish' for _,pin in case.base.read_calls))
            finally:case.doCleanups()

    def test_capture_record_still_requires_actual_pinned_source_control_authentication(self):
        def forged(r):
            record=self.capture(r)
            return replace(record,grant=replace(record.grant,original_pin=replace(
                record.grant.original_pin,sha256='f'*64)))
        out=self.app(forged).run('deploy')
        self.assertEqual('COMPLETION_DENIED',out['error_code'],out)
        self.assertEqual('RETAINED',out['fence']);self.assertEqual(0,self.base.p.c.storage.complete_calls)

    def test_existing_valid_slot_skips_new_capture_but_reauthenticates_original(self):
        factory=self.factory(Mock(side_effect=AssertionError('MUST_NOT_CAPTURE')))
        # This case explicitly exercises compatibility with an already supplied
        # record; the normal case above uses only the new post-close hook.
        out=self.base.app(install=True,factory=factory).run('deploy')
        self.assertEqual('SESSION_COMPLETED',out['status'],out)
        self.assertTrue(any(pin.path=='finish' for _,pin in self.base.read_calls))

    def test_preflight_does_not_capture_finish_or_construct_write_sender(self):
        out=self.app().run('preflight')
        self.assertEqual('PREFLIGHT_OBSERVED',out['status'],out)
        self.assertEqual([],self.captures);self.assertEqual(0,self.base.p.c.sender_calls)
        self.assertEqual([],self.base.p.c.storage.claims)

    def test_bad_capture_or_incomplete_run_configuration_denied_before_io(self):
        for capture in (True,{},'approved'):
            with self.subTest(capture=type(capture)),self.assertRaises(runtime.RuntimeSiteError):
                self.base.factory(self.base.slot,finish_capture=capture)
        for values in ({'producer_pins':self.pins}, {'producer_run_reader':self.api_reader},
                       {'producer_pins':self.pins,'producer_run_reader':object()}):
            with self.subTest(values=list(values)),self.assertRaises(runtime.RuntimeSiteError):
                self.base.factory(self.base.slot,**values)
        self.assertEqual([],self.calls);self.assertEqual([],self.captures)

    def test_wrong_stage_reference_does_not_relabel_native_run_as_app_lineage(self):
        wrong=(replace(self.pins[0],producer_reference='synthetic:other-source'),)+self.pins[1:]
        with self.assertRaisesRegex(runtime.RuntimeSiteError,'REFERENCE_MISMATCH'):
            self.base.factory(self.base.slot,producer_pins=wrong,producer_run_reader=self.api_reader)
        self.assertEqual([],self.calls)

    def test_failed_capture_is_consumed_even_if_a_caller_attempts_again(self):
        self.base.terminal()
        callback=Mock(side_effect=RuntimeError('SYNTHETIC_SECRET'))
        adapter=runtime.DeferredFinish(self.h.binding,self.credential,lambda:self.base.progress,self.base.slot,
            runtime.SourceReaders(self.base.reader,self.base.reader),lambda:self.h.now,lambda:self.h.mono,callback)
        r=self.base.request()
        with self.assertRaisesRegex(runtime.RuntimeSiteError,'FINISH_CAPTURE_FAILED') as error:adapter(r)
        self.assertNotIn('SYNTHETIC_SECRET',str(error.exception))
        with self.assertRaisesRegex(runtime.RuntimeSiteError,'FINISH_CAPTURE_CONSUMED'):adapter(r)
        callback.assert_called_once();adapter.close();self.assertIsNone(adapter.capture)

    def test_deadline_spent_validating_terminal_progress_prevents_capture(self):
        self.base.terminal();callback=Mock(side_effect=AssertionError('MUST_NOT_CAPTURE'))
        adapter=runtime.DeferredFinish(self.h.binding,self.credential,lambda:self.base.progress,self.base.slot,
            runtime.SourceReaders(self.base.reader,self.base.reader),lambda:self.h.now,lambda:self.h.mono,callback)
        request=self.base.request();original=runtime.terminal_checked
        def slow(binding,progress):
            original(binding,progress);self.h.mono=request.deadline
        with patch.object(runtime,'terminal_checked',side_effect=slow):
            with self.assertRaisesRegex(runtime.RuntimeSiteError,'DEADLINE_EXCEEDED'):adapter(request)
        callback.assert_not_called();self.assertFalse(self.base.slot.installed);adapter.close()

    def main(self,command,receiver=None):
        reads=tuple(runtime.SourceReaders(self.base.reader,self.base.reader) for _ in range(4))
        original=runtime.RuntimeAuthorityFactory
        def factory(*args,**kwargs):
            return original(*args,**kwargs,wall=lambda:self.h.now,monotonic=lambda:self.h.mono)
        def build(config,*,credential,authority_factory):
            self.assertIs(credential,self.credential)
            return self.base.app(install=False,factory=authority_factory)
        out=io.StringIO()
        with patch.object(runtime,'RuntimeAuthorityFactory',side_effect=factory),patch.object(
            runtime.site,'build_application',side_effect=build),contextlib.redirect_stdout(out):
            rc=runtime.main([command],config=self.base.config(),plan=self.base.plan(),readers=reads,
                finish_slot=self.base.slot,receiver=receiver,handoff_deadline=self.h.mono+10,
                finish_capture=self.capture,producer_pins=self.pins,producer_run_reader=self.api_reader)
        return rc,json.loads(out.getvalue())

    def test_real_main_forwards_native_gate_and_completion_hook(self):
        read,write=os.pipe();os.close(write);receiver=PipeCredentialReceiver(read)
        with patch.object(receiver,'receive',return_value=self.credential) as receive:
            rc,result=self.main('deploy',receiver)
        self.assertEqual(0,rc,result);self.assertEqual('SESSION_COMPLETED',result['status'])
        self.assertEqual(1,len(self.captures));receive.assert_called_once()
        self.assertIsNone(receiver._fd)

    def test_main_status_has_no_capture_native_read_or_pipe_receive(self):
        read,write=os.pipe();os.close(write);receiver=PipeCredentialReceiver(read)
        with patch.object(receiver,'receive',side_effect=AssertionError('NO_RECEIVE')):
            rc,result=self.main('status',receiver)
        self.assertEqual(0,rc);self.assertEqual([],self.calls);self.assertEqual([],self.captures)
        self.assertIsNone(receiver._fd)


if __name__=='__main__':unittest.main()
