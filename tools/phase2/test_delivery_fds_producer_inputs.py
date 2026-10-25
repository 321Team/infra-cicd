"""Observed non-secret source pins + SYNTHETIC RUN/grant/previous, offline only.

Never treats historical receipt/CI success as current grant or current scan.
Real original bodies are not embedded; existing native source tests exercise
authenticated before/after reads, failed stages, reruns and changed attempts.
"""
from dataclasses import replace
import unittest
from unittest.mock import Mock, patch

import delivery_adapter as core
import delivery_actions_source as actions
import delivery_fds_operating as operating
import delivery_fds_producer_inputs as selected
import delivery_fds_operating_policy as policy
import delivery_fds_site_inputs as profile
import delivery_job_site as declaration
from delivery_adopted_authority import AdoptedGrant
from delivery_github_source import GitHubOriginalAdapter
from delivery_object_reader import BearerCredential
from delivery_semantic_authority import CodeAdoptedEvidenceResolver
from delivery_verifier import Progress
from delivery_verifier import ReadRequest
import test_delivery_fds_site_inputs as fixture


class ReviewedProducerInputsTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture.ObservedSiteTests('runTest'); self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.config = replace(self.f.config, binding=replace(self.f.config.binding,
                              supply_receipt_sha256=selected.SUPPLY_SHA256))
        self.grant = AdoptedGrant(self.config.binding, selected.SUPPLY_PIN,
            policy.CONTROL_PIN, 'SYNTHETIC_PRODUCER', 'synthetic:meaning', 1000, 1900)
        # Explicit synthetic prior profile; never extracted from current live
        # objects, the historical handoff or desired current image defaults.
        self.previous = tuple((n, uri.split('@')[0]+'@sha256:'+'e'*64)
                              for n, uri in profile.IMAGES)

    def build(self, **changes):
        return operating.connect_reviewed_producer(**({'config': self.config,
            'grant': self.grant, 'previous_images': self.previous} | changes))

    def test_inert_real_selections_compose_existing_semantic_and_native_contracts(self):
        adoption, pins = self.build()
        self.assertIs(adoption.grant, self.grant)
        self.assertIs(adoption.facts.previous_images, self.previous)
        self.assertEqual(profile.IMAGES, adoption.facts.images)
        self.assertEqual(profile.SOURCE_COMMIT, adoption.facts.source_commit)
        self.assertNotEqual(selected.IMAGE_SOURCE_COMMIT, adoption.facts.source_commit)
        self.assertNotEqual(selected.TBS.workflow_commit, adoption.facts.source_commit)
        self.assertEqual((selected.TBS_REFERENCE,)*3+(selected.PUSH_REFERENCE,),
                         tuple(p.producer_reference for p in pins))
        actions.policy_checked(pins)
        reader = object.__new__(GitHubOriginalAdapter)
        reader.blob = Mock(side_effect=AssertionError('NO_SOURCE_READ'))
        clock = Mock(side_effect=AssertionError('NO_CLOCK'))
        resolver = CodeAdoptedEvidenceResolver(role='producer', grant=self.grant,
            facts=adoption.facts, control_reader=reader,
            credential=BearerCredential('SYNTHETIC_NON_CREDENTIAL'),
            progress=lambda: Progress(), wall=clock, monotonic=clock)
        self.addCleanup(resolver.close)
        reader.blob.assert_not_called(); clock.assert_not_called()
        self.assertIsNone(declaration.SITE)

    def test_actual_stage_pin_ids_and_successful_step_are_not_discovered_or_defaulted(self):
        _, pins = self.build()
        self.assertEqual((38395350748,)*3+(38404449622,), tuple(p.run.run_id for p in pins))
        self.assertEqual((1,)*4, tuple(p.run.attempt for p in pins))
        self.assertEqual((115289040431,)*3+(115320270242,), tuple(p.job_id for p in pins))
        self.assertEqual(((4, 'Validate handoff and run the approved application'),), pins[0].steps)
        self.assertEqual(((5, 'Execute approved Push'),), pins[3].steps)
        self.assertNotIn('Validate archived evidence without registry credentials', repr(pins))

    def test_missing_or_first_empty_or_wrong_service_previous_is_rejected(self):
        for previous in (None, (), list(self.previous), self.previous[:1],
                         (self.previous[1], self.previous[0])):
            with self.subTest(previous=previous), self.assertRaises(selected.ProducerInputError):
                self.build(previous_images=previous)
        with self.assertRaises(TypeError):
            selected.connect_reviewed_producer(self.config, grant=self.grant)

    def test_no_grant_original_control_or_binding_substitution(self):
        bad = (None, {}, replace(self.grant, original_pin=replace(selected.SUPPLY_PIN, sha256='f'*64)),
            replace(self.grant, original_pin=replace(selected.SUPPLY_PIN, path='synthetic-other')),
            replace(self.grant, original_pin=replace(selected.SUPPLY_PIN, anchor_commit='f'*40)),
            replace(self.grant, control_pin=replace(policy.CONTROL_PIN, sha256='f'*64)),
            replace(self.grant, binding=replace(self.grant.binding, run_id='SYNTHETIC_OTHER')),
            replace(self.grant, starts_at=1900), replace(self.grant, expires_at=float('inf')))
        for grant in bad:
            with self.subTest(grant=type(grant)), self.assertRaises(selected.ProducerInputError) as caught:
                self.build(grant=grant)
            self.assertIsNone(caught.exception.__context__)

    def test_no_silent_receipt_source_render_or_digest_repair(self):
        for change in ({'supply_receipt_sha256':'f'*64}, {'source_commit':'f'*40},
                       {'contract_sha256':'f'*64}, {'rendered_sha256':'f'*64},
                       {'images':self.previous}):
            b = replace(self.config.binding, **change)
            with self.subTest(change=change), self.assertRaises(selected.ProducerInputError):
                self.build(config=replace(self.config, binding=b), grant=replace(self.grant, binding=b))

    def test_fresh_values_do_not_mutate_another_selection_or_infer_current_as_previous(self):
        first, pins1 = self.build(); second, pins2 = self.build()
        self.assertIsNot(first, second); self.assertIsNot(pins1, pins2)
        self.assertNotEqual(first.facts.images, first.facts.previous_images)
        self.assertIs(first.grant, second.grant)

    def test_future_profile_change_cannot_relabel_old_receipt_as_new_source_or_images(self):
        for attr, value in (('SOURCE_COMMIT', 'f'*40), ('IMAGES', self.previous)):
            field = {'SOURCE_COMMIT':'source_commit', 'IMAGES':'images'}[attr]
            b = replace(self.config.binding, **{field:value})
            with patch.object(profile, attr, value), self.assertRaises(selected.ProducerInputError):
                self.build(config=replace(self.config, binding=b), grant=replace(self.grant, binding=b))

    def test_selected_native_pins_use_existing_gate_and_reject_a_failed_push_step(self):
        _, pins = self.build()
        reader = object.__new__(GitHubOriginalAdapter)
        prefix = '/repos/321Team/infra-cicd'
        reader.repository = Mock(return_value=prefix)
        failed = False
        def read(request, path, receipts):
            run = next(p.run for p in pins if '/runs/'+str(p.run.run_id) in path)
            if '/jobs?' in path:
                pin = next(p for p in pins if p.run == run)
                return {'total_count':1, 'jobs':[{'id':pin.job_id, 'run_id':run.run_id,
                    'run_attempt':1, 'head_sha':run.workflow_commit, 'name':pin.job_name,
                    'status':'completed', 'conclusion':'success', 'steps':[
                        {'number':number, 'name':name, 'status':'completed',
                         'conclusion':'failure' if failed and pin.stage=='push' else 'success'}
                        for number,name in pin.steps]}]}
            return {'id':run.run_id, 'run_attempt':run.attempt, 'workflow_id':run.workflow_id,
                'head_sha':run.workflow_commit, 'path':run.workflow_path,
                'event':run.event, 'head_branch':run.branch, 'status':'completed',
                'conclusion':'success', 'repository':{'id':run.repository.repository_id},
                'head_repository':{'id':run.repository.repository_id}}
        reader.read = Mock(side_effect=read)
        credential = BearerCredential('SYNTHETIC_NON_CREDENTIAL'); progress = Progress()
        request = ReadRequest(self.config.binding, credential, progress, 1005)
        gate = actions.ActionsRunSource(reader=reader, pins=pins, binding=self.config.binding,
            credential=credential, progress=lambda:progress, monotonic=lambda:1000)
        self.addCleanup(gate.close)
        result = gate.inspect(request)
        self.assertEqual(pins, result.resolved_pins)
        self.assertEqual(2, len(result.stages))
        self.assertFalse(result.execution_authorized)
        failed = True
        with self.assertRaises(actions.ActionsSourceError): gate.inspect(request)


if __name__ == '__main__': unittest.main()
