"""SYNTHETIC grants/baseline/native context only; no network or Runtime.

The Windows path shim tests inert composition, not real POSIX placement.
Existing private-baseline/native-handoff/finish tests retain their own scope.
"""
from dataclasses import replace
import contextlib
import io
import os
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

import delivery_crosshost_ingress as ingress
import delivery_fds_operating as operating
import delivery_fds_operating_policy as policy
import delivery_fds_prepared_environment as prepared
import delivery_fds_producer_inputs as producer
import delivery_fds_run_inputs as placement
import delivery_job as job
import delivery_job_rendezvous as rendezvous
import delivery_job_site as declaration
import delivery_job_sources as sources
import delivery_runtime_site as runtime
from delivery_finish_observation import FOLLOWUPS
from test_delivery_job import CONTEXT
import test_delivery_fds_private_baseline as fixture
import test_delivery_fds_baseline_archive as archives


class RunSelectionTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture.PrivateBaselineTests('runTest'); self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.selected = self.f.d.selected
        b = replace(self.selected.approval.binding, supply_receipt_sha256=producer.SUPPLY_SHA256)
        def grant(g): return replace(g, binding=b)
        self.selected = replace(self.selected, approval=grant(self.selected.approval),
            issuer_grant=grant(self.selected.issuer_grant), producer=replace(
                self.selected.producer, grant=replace(grant(self.selected.producer.grant),
                    original_pin=producer.SUPPLY_PIN)))
        config = replace(self.f.d.f.config, binding=b)
        adoption, _ = producer.connect_reviewed_producer(config,
            grant=self.selected.producer.grant,
            previous_images=self.selected.producer.facts.previous_images)
        self.selected = replace(self.selected, producer=adoption)
        self.baseline_grant = grant(self.f.grant)
        self.finish = grant(self.f.d.finish)
        self.files = placement.RunInputFiles('/SYNTHETIC_STAGING', self.f.file)
        a = self.f.d.f.args
        repositories = (a['routes'][0].repository, producer.SUPPLY_PIN.repository,
                        producer.PRODUCER_REPOSITORY)
        self.routes = tuple(replace(a['routes'][0], repository=r) for r in repositories)
        self.options = dict(selection=self.selected, finish_scope=self.finish,
            baseline_grant=self.baseline_grant, baseline_root=archives.ROOT,
            input_files=self.files, expected_peer=a['expected_peer'], routes=self.routes,
            budget_seconds=30, finalize=False, deadline=a['deadline'],
            monotonic=self.f.d.f.mono, wall=self.f.d.f.wall)
        self.expected_input = placement.run_input_root(CONTEXT)
        if os.name != 'posix':
            root=Path.cwd().anchor
            self.expected_input=str(Path(root)/'SYNTHETIC_NATIVE_INPUT')
            p=patch.object(placement,'run_input_root',return_value=self.expected_input)
            p.start(); self.addCleanup(p.stop)
            p=patch.object(prepared,'configuration_paths',return_value=dict(
                source_root=Path(root)/'SYNTHETIC_SOURCE', store_root=Path(root)/'SYNTHETIC_STATE',
                rendered_name='rendered.yaml', contract_name='contract.yaml'))
            p.start(); self.addCleanup(p.stop)

    def select(self, context=CONTEXT, command='deploy', **changes):
        return operating.build_observed_run_inputs(context,command,**(self.options|changes))

    def test_inert_join_preserves_logical_run_grants_paths_and_uninstalled_finish(self):
        with patch.object(Path,'open',side_effect=AssertionError('NO_FILE')), \
             patch.object(placement,'prepare_run_inputs') as copy:
            value=self.select()
        self.assertEqual(self.selected.approval.binding,value['config'].binding)
        self.assertNotEqual(CONTEXT.run_id,value['config'].binding.run_id)
        self.assertEqual(Path(self.expected_input),Path(value['config'].input_root))
        self.assertIs(self.selected,value['selection']);self.assertIs(self.finish,value['finish_scope'])
        self.assertIs(self.baseline_grant,value['baseline_grant'])
        self.assertIs(self.files.baseline_file,value['baseline_file'])
        self.assertFalse(value['finish_slot'].installed)
        self.assertEqual(value['config'].binding,value['finish_slot'].binding)
        self.assertEqual((38395350748,)*3+(38404449622,),
                         tuple(p.run.run_id for p in value['producer_pins']))
        copy.assert_not_called();self.f.d.f.mono.assert_not_called();self.f.d.f.wall.assert_not_called()
        self.f.d.f.issue.assert_not_called();self.assertIsNone(declaration.SITE)

    def test_explicit_five_grants_route_existing_parent_finish_and_private_materializer(self):
        value=self.select()
        inner=operating.assemble_observed_native_job(context=CONTEXT,**value)
        self.addCleanup(inner.build._parent._plan.close);self.addCleanup(inner.build._owner.close)
        wire=inner.build._parent._wire
        self.assertEqual(sources.ROLES+('baseline',),wire.roles)
        wire.check_channels(value['config'].binding,self.routes)
        grants=(self.selected.approval,self.selected.issuer_grant,self.selected.producer.grant,
                self.finish,self.baseline_grant)
        self.assertEqual(tuple((g.original_pin.repository,g.control_pin.repository) for g in grants),
            tuple((r.original,r.control) for r in value['role_routes']))
        self.assertIs(self.finish,wire._finish.scope_grant)
        self.assertEqual(FOLLOWUPS,wire._finish.required_followups)
        self.assertIs(type(inner.build._parent._plan),runtime.PreparedAuthorityPlan)
        with self.assertRaises(sources.JobSourcesError):
            wire.check_channels(value['config'].binding,self.routes[:-1])

    def test_bad_or_missing_actual_roles_and_no_silent_producer_fact_repair(self):
        bad=(dict(selection=None),dict(baseline_grant=None),dict(baseline_root='../invalid'),
             dict(input_files=None),dict(finish_scope=None),
             dict(selection=replace(self.selected,baseline=self.f.d.f.selection.baseline)),
             dict(selection=replace(self.selected,producer=replace(self.selected.producer,
                  facts=replace(self.selected.producer.facts,test_reference='synthetic:changed')))),
             dict(finish_scope=replace(self.finish,binding=replace(self.finish.binding,run_id='OTHER'))),
             dict(baseline_grant=replace(self.baseline_grant,control_pin=replace(
                  self.baseline_grant.control_pin,sha256='9'*64))))
        for change in bad:
            with self.subTest(change=tuple(change)),self.assertRaises(policy.OperatingPolicyError) as caught:
                self.select(**change)
            self.assertIsNone(caught.exception.__context__)
        self.f.d.f.mono.assert_not_called();self.f.d.f.wall.assert_not_called()

    def test_non_reference_producer_fact_changes_cannot_be_silently_reconnected(self):
        facts = self.selected.producer.facts
        for field in ('source_commit', 'rendered_sha256', 'checker_sha256',
                      'clean_render_reference', 'adoption_reference'):
            with self.subTest(field=field), self.assertRaises(policy.OperatingPolicyError):
                self.select(selection=replace(self.selected, producer=replace(
                    self.selected.producer, facts=replace(facts, **{field: 'SYNTHETIC_CHANGED'}))))
        self.f.d.f.mono.assert_not_called(); self.f.d.f.wall.assert_not_called()

    def test_actual_selected_private_association_rejects_foreign_pin_root_file_or_binding(self):
        from delivery_fds_private_baseline import PrivateBaselineFile
        changes = [dict(baseline_root='SYNTHETIC_OTHER_ROOT'),
            dict(baseline_grant=replace(self.baseline_grant, original_pin=replace(
                self.baseline_grant.original_pin, body_sha256='9'*64))),
            dict(baseline_grant=replace(self.baseline_grant, original_pin=replace(
                self.baseline_grant.original_pin, comment_id=999))),
            dict(baseline_grant=replace(self.baseline_grant, original_pin=replace(
                self.baseline_grant.original_pin, updated_at='2026-01-02T00:00:00Z'))),
            dict(baseline_grant=replace(self.baseline_grant,
                binding=replace(self.baseline_grant.binding, run_id='OTHER')))]
        for file in (PrivateBaselineFile('other.tgz', self.f.file.sha256),
                     replace(self.f.file, sha256='9'*64)):
            changes.append(dict(input_files=replace(self.files, baseline_file=file)))
        for change in changes:
            with self.subTest(change=tuple(change)), self.assertRaises(policy.OperatingPolicyError):
                self.select(**change)
        self.f.d.f.mono.assert_not_called(); self.f.d.f.wall.assert_not_called()

    def test_direct_native_private_assembly_cannot_bypass_selected_association(self):
        from delivery_fds_baseline_archive import BaselineArchiveError
        value = self.select()
        for change in (dict(baseline_root='SYNTHETIC_FOREIGN'),
                       dict(baseline_file=replace(self.files.baseline_file, sha256='9'*64)),
                       dict(baseline_grant=replace(self.baseline_grant, original_pin=replace(
                           self.baseline_grant.original_pin, comment_id=999)))):
            with self.subTest(change=tuple(change)), self.assertRaises(BaselineArchiveError) as caught:
                operating.assemble_observed_native_job(context=CONTEXT, **(value | change))
            self.assertIsNone(caught.exception.__context__)
        self.f.d.f.mono.assert_not_called(); self.f.d.f.wall.assert_not_called()

    def test_native_context_and_command_are_not_inferred_from_grants_or_environment(self):
        for ctx,cmd in ((replace(CONTEXT,attempt=2),'deploy'),(CONTEXT,'restore'),
                        (None,'deploy'),(CONTEXT,'DEPLOY')):
            with self.subTest(cmd=cmd),self.assertRaises(policy.OperatingPolicyError):self.select(ctx,cmd)

    def test_changed_role_count_cannot_silently_drop_or_shift_a_grant(self):
        for roles in (sources.ROLES[:-1], sources.ROLES+('other',)):
            with patch.object(sources,'ROLES',roles), self.assertRaises(policy.OperatingPolicyError):
                self.select()
        value=self.select(command='preflight')
        self.assertEqual(30,value['config'].budget_seconds)
        self.assertFalse(value['config'].finalize)
        self.f.d.f.mono.assert_not_called();self.f.d.f.wall.assert_not_called()

    def test_selector_is_deferred_and_existing_declaration_prepares_before_ready_once(self):
        select=Mock(side_effect=lambda context,command:self.select(context,command))
        events=[]
        def invoke(inner,context,command):
            events.append('READY_BOUNDARY');self.addCleanup(inner._parent._plan.close)
            self.addCleanup(inner._owner.close);return 'SYNTHETIC_RESULT'
        with patch.object(placement,'prepare_run_inputs',side_effect=lambda *a,**k:events.append('COPY')), \
             patch.object(rendezvous.OwnedRendezvousBuilder,'__call__',invoke):
            value=operating.declare_observed_native_job(select_inputs=select,input_files=self.files)
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(0,job.main(['status'],declaration=value))
            self.assertFalse(job.workflow_gate(declaration=value,context=CONTEXT,
                revision=lambda:CONTEXT.workflow_sha)['execution_authorized'])
            select.assert_not_called()
            self.assertEqual('SYNTHETIC_RESULT',value.build(CONTEXT,'deploy'))
            with self.assertRaises(policy.OperatingPolicyError):value.build(CONTEXT,'deploy')
        self.assertEqual(['COPY','READY_BOUNDARY'],events);select.assert_called_once()
        self.f.d.f.issue.assert_not_called()

    def test_foreign_failures_are_redacted_and_interrupts_do_not_become_defaults(self):
        for failure,kind in ((RuntimeError('PRIVATE_GRANT'),policy.OperatingPolicyError),
                             (KeyboardInterrupt('PRIVATE'),KeyboardInterrupt),(SystemExit(7),SystemExit)):
            with patch.object(operating,'connect_reviewed_producer',side_effect=failure), \
                 self.assertRaises(kind) as caught:self.select()
            self.assertIsNone(caught.exception.__context__)
            self.assertNotIn('PRIVATE',str(caught.exception))
            if kind is SystemExit:self.assertEqual(130,caught.exception.code)


if __name__=='__main__':unittest.main()
