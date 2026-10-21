"""SYNTHETIC existing #43 no-previous contract -> actual runtime composition.

No actual deployment inputs, network, credentials or Runtime are produced.
"""
from dataclasses import replace
import json
import unittest
from unittest.mock import patch

import delivery_adapter as core
import delivery_runtime_site as runtime
import delivery_semantic_authority as semantic
from delivery_object_reader import ObjectReader, Observation
from test_delivery_adapter import Harness, SENSITIVE
from test_delivery_nine_objects import nine
from test_delivery_object_reader import absent
from test_delivery_objects import owned
import test_delivery_runtime_site as prior


class FirstDeployInputTests(unittest.TestCase):
    def setUp(self):
        self.base = prior.RuntimeSiteTests('runTest'); self.base.setUp()
        self.addCleanup(self.base.doCleanups)
        self.h, self.credential = self.base.h, self.base.credential

    def none(self):
        self.base.producer = replace(self.base.producer, previous_images=None)

    def resolver(self, facts, *, first=False, grant=None):
        return semantic.CodeAdoptedEvidenceResolver(role='producer',
            grant=grant or self.base.grants['producer'], facts=facts,
            control_reader=self.base.reader, credential=self.credential,
            progress=lambda:self.base.progress, first_deployment=first,
            wall=lambda:self.h.now, monotonic=lambda:self.h.mono)

    def test_no_previous_is_explicit_and_has_no_constructor_default(self):
        self.none()
        with self.assertRaisesRegex(semantic.SemanticAuthorityError, '^SEMANTIC_PRODUCER_FIRST_DEPLOY_REQUIRED$'):
            self.resolver(self.base.producer)
        values=dict(self.base.producer.__dict__);values.pop('previous_images')
        with self.assertRaises(TypeError): semantic.ProducerFacts(**values)
        self.assertEqual([], self.base.read_calls)

    def test_explicit_first_deploy_none_keeps_current_lineage_and_original_authentication(self):
        self.none(); r=self.base.request(); resolver=self.resolver(self.base.producer, first=True)
        self.addCleanup(resolver.close)
        from delivery_evidence_source import ReviewedEvidenceSource
        port=ReviewedEvidenceSource(role='producer', reader=self.base.reader,
            pin=self.base.pins['producer'], resolver=resolver, binding=self.h.binding,
            credential=self.credential, progress=lambda:self.base.progress,
            wall=lambda:self.h.now, monotonic=lambda:self.h.mono)
        self.addCleanup(port.close)
        result=port(r)
        self.assertEqual(self.h.binding.images, self.base.producer.images)
        self.assertIsNone(self.base.producer.previous_images)
        self.assertEqual(self.base.raws['producer'], result.raw_receipt)
        self.assertEqual(['producer','control','producer','control'],
            [p.path for _,p in self.base.read_calls])

    def test_empty_partial_mutable_and_untyped_previous_are_not_none(self):
        for value in ((), [], False, '', 'NONE', self.base.producer.images[:-1],
                      tuple(reversed(self.base.producer.images))):
            with self.subTest(value=type(value)), self.assertRaises(Exception):
                self.resolver(replace(self.base.producer, previous_images=value), first=True)
        self.assertEqual([], self.base.read_calls)

    def test_none_does_not_bypass_current_digest_or_clean_lineage(self):
        self.none()
        for changes in ({'images':self.base.producer.images[::-1]},
                        {'source_commit':'f'*40}, {'clean_render_reference':'PENDING'},
                        {'scan_reference':'UNKNOWN'}):
            with self.subTest(changes=list(changes)), self.assertRaises(Exception):
                self.resolver(replace(self.base.producer, **changes), first=True)
        self.assertEqual([], self.base.read_calls)

    def test_standalone_restore_and_untyped_first_policy_reject_none(self):
        self.none()
        grant=replace(self.base.grants['producer'], binding=replace(self.h.binding, action='restore'))
        with self.assertRaisesRegex(semantic.SemanticAuthorityError, '^SEMANTIC_PRODUCER_FIRST_DEPLOY_REQUIRED$'):
            self.resolver(self.base.producer, first=True, grant=grant)
        for value in (1, 'yes', None):
            with self.subTest(value=value), self.assertRaisesRegex(
                    semantic.SemanticAuthorityError, '^SEMANTIC_FIRST_DEPLOY_POLICY_INVALID$'):
                self.resolver(self.base.producer, first=value)
        self.assertEqual([], self.base.read_calls)

    def test_initial_empty_target_none_connects_factory_and_session_without_fake_previous(self):
        self.none()
        out=self.base.app(install=False, finalize=False).run('deploy')
        self.assertEqual('REVIEW_REQUIRED', out['status'], out)
        self.assertEqual('ROLLOUT_OBSERVED', out['receipt']['status'])
        self.assertEqual(['PATCH']*6+['GET']*2, [r.method for r in self.h.requests])
        self.assertEqual('RETAINED', out['fence'])
        self.assertEqual(0, self.base.p.c.storage.complete_calls)
        self.assertEqual('NOT_VERIFIED', out['delivery_gate'])
        self.assertIsNone(self.base.producer.previous_images)

    def test_existing_object_none_stops_before_claim_and_sender(self):
        self.none()
        self.h.objects[core.path_for(core.OBJECTS[0])] = json.loads(self.h.render)[0]
        out=self.base.app(install=False, finalize=False).run('deploy')
        self.assertEqual('PREFLIGHT_DENIED', out['error_code'], out)
        self.assertEqual([], self.base.p.c.storage.claims)
        self.assertEqual(0, self.base.p.c.sender_calls)

    def test_existing_adopted_baseline_cannot_use_none_even_if_its_entries_are_empty(self):
        self.none();plan=self.base.plan()
        baseline=runtime.BaselineAdoption(self.base.grants['approval'], (None,)*6)
        plan=replace(plan, baseline=baseline)
        with self.assertRaisesRegex(runtime.RuntimeSiteError, '^RUNTIME_SITE_PREVIOUS_REQUIRED$'):
            runtime.RuntimeAuthorityFactory(plan,tuple(runtime.SourceReaders(self.base.reader,self.base.reader)
                for _ in range(5)),self.base.slot)
        self.assertEqual([], self.base.read_calls)

    def test_restore_factory_none_rejected_before_source_or_live_reads(self):
        _,factory=self.base.prepare_restore();self.none()
        plan=replace(factory.plan, producer=replace(factory.plan.producer, facts=self.base.producer))
        with self.assertRaisesRegex(runtime.RuntimeSiteError, '^RUNTIME_SITE_PREVIOUS_REQUIRED$'):
            runtime.RuntimeAuthorityFactory(plan, factory.readers, self.base.slot)
        self.assertEqual([], self.base.read_calls)

    def test_partial_first_write_failure_keeps_pending_and_does_not_invent_rollback(self):
        self.none()
        def change(sender):
            real=sender.send; count=[0]
            def send(request):
                result=real(request)
                if request.method=='PATCH':
                    count[0]+=1
                    if count[0]==2:raise RuntimeError(SENSITIVE)
                return result
            return replace(sender, send=send)
        self.base.p.c.sender_change=change
        out=self.base.app(install=False, finalize=False).run('deploy')
        self.assertEqual('STOPPED', out['status'], out)
        self.assertEqual('RETAINED', out['fence'])
        self.assertEqual(1, len(out['receipt']['acknowledged']))
        self.assertIsNotNone(out['receipt']['pending'])
        self.assertEqual(0, self.base.p.c.storage.complete_calls)
        self.assertIsNone(self.base.producer.previous_images)
        self.assertNotIn(SENSITIVE, json.dumps(out))

    def test_nine_object_first_deploy_none_uses_nine_real_absence_checks(self):
        self.base.context(nine(self.h), self.credential); self.none()
        self.base.slot=runtime.FinishAdoptionSlot(self.h.binding)
        def server(request):
            result=Harness.send(self.h, request)
            if request.method=='PATCH':
                result=owned(result, core.objects_for(self.h.binding).index(core.identity(result)))
                self.h.objects[request.path.split('?')[0]]=result
            return result
        self.h.send=server;reads=[]
        def read(reader,request,key):
            reader.check_request(request);reads.append((key, bool(self.h.requests)))
            obj=self.h.objects.get(core.path_for(key,self.h.binding))
            return Observation(key,404 if obj is None else 200,
                core.encoded(absent(key) if obj is None else obj))
        with patch.object(ObjectReader,'read',new=read):
            out=self.base.app(install=False, finalize=False).run('deploy')
        self.assertEqual('REVIEW_REQUIRED',out['status'],out)
        self.assertEqual(9,len(out['receipt']['acknowledged']))
        self.assertEqual(set(core.objects_for(self.h.binding)),{k for k,written in reads if not written})
        self.assertIsNone(self.base.producer.previous_images)


if __name__=='__main__':unittest.main()
