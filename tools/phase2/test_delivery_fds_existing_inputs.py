"""SYNTHETIC baseline/adoption only; no live capture, token, API or Runtime."""
import copy
from dataclasses import replace
import json
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

import delivery_adapter as core
import delivery_fds_site_inputs as fds
import delivery_fds_existing_inputs as existing
import delivery_job_sources as wiring
import delivery_runtime_site as runtime
from test_delivery_adapter import fixture
from test_delivery_objects import owned
import test_delivery_fds_site_inputs as observed
import test_delivery_runtime_site as actual_factory


class ExistingProfileTests(unittest.TestCase):
    def setUp(self):
        self.f = observed.ObservedSiteTests('runTest'); self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.config = self.f.config
        self.previous = tuple((name, 'registry.example.com/fds/'+name+'@sha256:'+'9'*64)
                              for name in core.SERVICES)
        _, rendered, _ = fixture(); objects = []
        for i, obj in enumerate(json.loads(rendered)):
            obj = owned(obj, i)
            if obj['kind'] == 'Deployment':
                obj['spec']['template']['spec']['containers'][0]['image'] = dict(self.previous)[obj['metadata']['name']]
                obj['spec']['replicas'] = 0
                obj['status'] = {'observedGeneration': 0, 'readyReplicas': 0}
            objects.append(core.encoded(obj))
        grant = replace(self.f.plan.approval, original_pin=replace(
            self.f.plan.approval.original_pin, path='baseline', sha256=core.digest(b'SYNTHETIC_OWNER_ADOPTION')),
            adoption_reference='synthetic:explicit-uid-spec-owner-adoption')
        self.baseline = runtime.BaselineAdoption(grant, tuple(objects))
        self.plan = replace(self.f.plan, baseline=self.baseline, producer=replace(
            self.f.plan.producer, facts=replace(self.f.producer, previous_images=self.previous)))

    def args(self, **changes):
        return self.f.parent_args | {'plan': self.plan} | changes

    def changed_object(self, index, change):
        objects = list(self.baseline.objects); obj = json.loads(objects[index]); change(obj)
        objects[index] = core.encoded(obj)
        return replace(self.plan, baseline=replace(self.baseline, objects=tuple(objects)))

    def test_explicit_plan_and_exact_supplied_raw_previous_are_preserved_without_io(self):
        before = self.baseline.objects
        with patch.object(Path, 'open', side_effect=AssertionError('FILE_IO_FORBIDDEN')):
            self.assertIs(self.plan, existing.validate_existing_plan(self.config, self.plan))
            parent = existing.connect_existing_parent(**self.args())
        self.assertIs(parent._plan, self.plan)
        self.assertIs(self.plan.baseline.objects, before)
        self.assertIs(self.plan.producer.facts.previous_images, self.previous)
        for callback in self.f.callbacks: callback.assert_not_called()

    def test_first_empty_and_existing_target_paths_never_auto_switch(self):
        with self.assertRaises(fds.FdsSiteInputError): fds.connect_parent(**self.args())
        with self.assertRaises(existing.FdsExistingInputError):
            existing.connect_existing_parent(**self.args(plan=self.f.plan))
        with self.assertRaises(existing.FdsExistingInputError):
            existing.validate_existing_plan(self.config, replace(self.plan, baseline=None))

    def test_no_previous_partial_roles_tags_or_wrong_images_are_inferred(self):
        for value in (None, (), self.previous[:1], tuple(reversed(self.previous)),
                      self.previous + (('end-page', 'registry.example.com/fds/end-page@sha256:'+'9'*64),),
                      ((self.previous[0][0], 'registry.example.com/fds/app:latest'), self.previous[1]),
                      fds.IMAGES):
            plan = replace(self.plan, producer=replace(self.plan.producer,
                           facts=replace(self.plan.producer.facts, previous_images=value)))
            with self.subTest(previous=value), self.assertRaises(existing.FdsExistingInputError):
                existing.validate_existing_plan(self.config, plan)
        self.assertEqual(self.previous, self.plan.producer.facts.previous_images)

    def test_same_digest_is_allowed_only_when_explicit_previous_and_raw_both_match(self):
        objects = []
        for raw in self.baseline.objects:
            obj = json.loads(raw)
            if obj['kind'] == 'Deployment':
                obj['spec']['template']['spec']['containers'][0]['image'] = dict(fds.IMAGES)[obj['metadata']['name']]
            objects.append(core.encoded(obj))
        plan = replace(self.plan, baseline=replace(self.baseline, objects=tuple(objects)),
            producer=replace(self.plan.producer, facts=replace(self.plan.producer.facts, previous_images=fds.IMAGES)))
        self.assertIs(plan, existing.validate_existing_plan(self.config, plan))

    def test_six_present_ordered_bytes_and_unique_uids_are_required(self):
        for objects in (self.baseline.objects[:-1], (None,)+self.baseline.objects[1:],
                        (bytearray(self.baseline.objects[0]),)+self.baseline.objects[1:],
                        (memoryview(self.baseline.objects[0]),)+self.baseline.objects[1:],
                        tuple(reversed(self.baseline.objects)),
                        (self.baseline.objects[0],)*6, list(self.baseline.objects)):
            with self.subTest(objects_type=type(objects)), self.assertRaises(existing.FdsExistingInputError):
                existing.validate_existing_plan(self.config, replace(self.plan,
                    baseline=replace(self.baseline, objects=objects)))

    def test_other_namespace_missing_uid_or_unknown_owner_shape_is_not_accepted(self):
        changes = (lambda o:o['metadata'].update(namespace='another-namespace'),
                   lambda o:o['metadata'].pop('uid'),
                   lambda o:o['metadata']['managedFields'][0].update(operation='Unknown'),
                   lambda o:o['metadata'].update(deletionTimestamp='2026-10-23T00:00:00Z'),
                   lambda o:o['metadata'].update(uid='synthetic-uid-1'))
        for change in changes:
            with self.assertRaises(existing.FdsExistingInputError):
                existing.validate_existing_plan(self.config, self.changed_object(0, change))

    def test_undeclared_sidecar_init_ephemeral_or_container_name_is_rejected(self):
        changes = (lambda p:p['containers'].append(copy.deepcopy(p['containers'][0])),
                   lambda p:p.update(initContainers=[{'name':'synthetic-init','image':'other:tag'}]),
                   lambda p:p.update(ephemeralContainers=[{'name':'synthetic-debug'}]),
                   lambda p:p['containers'][0].update(name='other'))
        index = next(i for i, key in enumerate(core.OBJECTS) if key[1] == 'Deployment')
        for change in changes:
            with self.assertRaises(existing.FdsExistingInputError):
                existing.validate_existing_plan(self.config, self.changed_object(index,
                    lambda obj:change(obj['spec']['template']['spec'])))

    def test_baseline_other_binding_and_fixed_fds_source_drift_are_rejected(self):
        bad = replace(self.plan, baseline=replace(self.baseline, grant=replace(
            self.baseline.grant, binding=replace(self.config.binding, run_id='SYNTHETIC_OTHER_RUN'))))
        with self.assertRaises(existing.FdsExistingInputError): existing.validate_existing_plan(self.config, bad)
        for field, value in (('cluster_id','SYNTHETIC_OTHER_CLUSTER'), ('source_commit','0'*40),
                             ('action','restore'), ('namespace_uid','SYNTHETIC_OTHER_NAMESPACE')):
            with self.assertRaises(existing.FdsExistingInputError):
                existing.validate_existing_plan(replace(self.config,
                    binding=replace(self.config.binding, **{field:value})), self.plan)

    def test_prepared_path_checks_actual_return_with_existing_validator_only(self):
        materialize = Mock(return_value=self.plan)
        parent = existing.connect_existing_parent(**self.args(plan=None, materialize_plan=materialize))
        self.addCleanup(parent._plan.close)
        materialize.assert_not_called()
        self.assertIs(parent._plan._materialize(None, None, None, None, None), self.plan)
        materialize.return_value = self.f.plan
        with self.assertRaises(existing.FdsExistingInputError):
            parent._plan._materialize(None, None, None, None, None)
        with self.assertRaises(fds.FdsSiteInputError):
            existing.connect_existing_parent(**self.args(materialize_plan=materialize))

    def test_source_wiring_four_five_layout_mismatch_is_rejected_before_ready(self):
        repo = self.plan.approval.original_pin.repository
        def router(roles):
            return wiring.SourceRoleWiring(binding=self.config.binding,
                routes=tuple(wiring.RoleRoute(role, repo, repo) for role in roles),
                wall=lambda:1000, monotonic=lambda:1000)
        with self.assertRaises(existing.FdsExistingInputError):
            existing.connect_existing_parent(**self.args(wire=router(wiring.ROLES)))
        with self.assertRaises(fds.FdsSiteInputError):
            fds.connect_parent(**self.args(plan=self.f.plan, wire=router(wiring.ROLES+('baseline',))))
        parent = existing.connect_existing_parent(**self.args(wire=router(wiring.ROLES+('baseline',))))
        self.assertIs(parent._plan, self.plan)
        for callback in self.f.callbacks: callback.assert_not_called()


class ExistingRuntimeConnectionTests(unittest.TestCase):
    def setUp(self):
        self.f = actual_factory.RuntimeSiteTests('runTest'); self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        # All values in this integration fixture remain SYNTHETIC.
        self.assertEqual('ROLLOUT_OBSERVED', self.f.h.run()['status'])
        for i, key in enumerate(core.OBJECTS):
            path = core.path_for(key, self.f.h.binding)
            self.f.h.objects[path] = owned(self.f.h.objects[path], i)
        self.f.h.requests.clear()
        objects = tuple(core.encoded(self.f.h.objects[core.path_for(key, self.f.h.binding)]) for key in core.OBJECTS)
        self.f.raws['baseline'] = b'SYNTHETIC_REVIEWED_EXISTING_OBJECTS_OWNERS'
        self.f.raws['baseline-control'] = b'SYNTHETIC_BASELINE_CONTROL'
        pin = replace(self.f.pins['producer'], path='baseline', sha256=core.digest(self.f.raws['baseline']))
        control = replace(self.f.pins['control'], path='baseline-control', sha256=core.digest(self.f.raws['baseline-control']))
        grant = replace(self.f.grants['approval'], original_pin=pin, control_pin=control,
                        adoption_reference='synthetic:baseline-owners-adopted')
        self.plan = replace(self.f.plan(), baseline=runtime.BaselineAdoption(grant, objects))
        b, issuer, subject = self.f.h.binding, self.plan.issuer.facts, self.plan.subject
        values = dict(API_ORIGIN=b.api_origin, CLUSTER_UUID=b.cluster_id, NAMESPACE_UID=b.namespace_uid,
            SUBJECT=subject.username, SUBJECT_UID=subject.uid, ISSUER=issuer.issuer, AUDIENCE=issuer.audience,
            CA_PEM=issuer.ca_pem, CA_PEM_SHA256=b.ca_sha256, SOURCE_COMMIT=b.source_commit,
            CONTRACT_SHA256=b.contract_sha256, RENDERED_SHA256=b.rendered_sha256, ROUTE_HOST=b.route_host, IMAGES=b.images)
        p = patch.multiple(fds, **values); p.start(); self.addCleanup(p.stop)

    def run_app(self):
        existing.validate_existing_plan(self.f.config(), self.plan)
        factory = runtime.RuntimeAuthorityFactory(self.plan,
            tuple(runtime.SourceReaders(self.f.reader, self.f.reader) for _ in range(5)), self.f.slot,
            wall=lambda:self.f.h.now, monotonic=lambda:self.f.h.mono)
        return self.f.app(factory=factory).run('deploy')

    def test_existing_six_objects_are_admitted_by_original_control_and_fresh_reads(self):
        out = self.run_app()
        self.assertEqual(('SESSION_COMPLETED','RELEASED'), (out['status'],out['fence']),out)
        self.assertEqual(6, len(out['receipt']['acknowledged']))
        self.assertTrue(any(pin.path == 'baseline-control' for _, pin in self.f.read_calls))

    def test_fresh_uid_spec_or_owner_drift_stops_before_claim_and_write(self):
        path = core.path_for(core.OBJECTS[0], self.f.h.binding)
        original = json.loads(self.plan.baseline.objects[0])
        changes = (lambda o:o['metadata'].update(uid='synthetic-replaced'),
                   lambda o:o['spec']['ports'][0].update(port=8888),
                   lambda o:o['metadata']['managedFields'][0].update(manager='other-writer'))
        for change in changes:
            self.f.h.objects[path] = copy.deepcopy(original); change(self.f.h.objects[path])
            out = self.run_app()
            self.assertEqual('PREFLIGHT_DENIED', out['error_code'],out)
            self.assertEqual([], self.f.p.c.storage.claims); self.assertEqual([], self.f.h.requests)

    def test_known_status_resource_version_volatility_does_not_redefine_baseline(self):
        path = core.path_for(core.OBJECTS[0], self.f.h.binding)
        self.f.h.objects[path]['metadata']['resourceVersion'] = '99999'
        self.f.h.objects[path]['status'] = {'SYNTHETIC_VOLATILE_STATUS': True}
        out = self.run_app()
        self.assertEqual(('SESSION_COMPLETED','RELEASED'), (out['status'],out['fence']),out)

    def test_baseline_control_withdrawal_during_read_stops_before_claim(self):
        self.f.read_hook = lambda request, pin, original:(
            replace(original, body=b'SYNTHETIC_WITHDRAWN')
            if pin.path == 'baseline-control' and self.f.p.reads else original)
        out = self.run_app()
        self.assertEqual('PREFLIGHT_DENIED', out['error_code'],out)
        self.assertEqual([], self.f.p.c.storage.claims); self.assertEqual([], self.f.h.requests)

    def test_rejection_after_first_write_preserves_existing_receipt_and_fence(self):
        self.f.h.reject_at = 2
        out = self.run_app()
        self.assertEqual(('STOPPED','RETAINED'), (out['status'],out['fence']),out)
        self.assertEqual(1, len(out['receipt']['acknowledged']))
        self.assertEqual(0, self.f.p.c.storage.complete_calls)


if __name__ == '__main__': unittest.main()
