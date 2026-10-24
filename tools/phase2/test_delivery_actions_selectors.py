"""SYNTHETIC native responses only; no real run/credential/lineage/Runtime GO.

Selectors are reviewed code criteria. Actual job IDs are read from the fixture
native response, never populated as operational inputs or inferred authority.
"""
import copy
from dataclasses import replace
import unittest

import delivery_actions_source as actions
from delivery_actions_jobs import resolve_named_jobs
from delivery_object_reader import BearerCredential
from test_delivery_actions_source import NativeApiFixture
from test_delivery_adapter import Harness, SENSITIVE
import test_delivery_runtime_site as runtime_fixture


def selectors(pins):
    return tuple(actions.StageSelector(p.stage, p.producer_reference, p.run,
                                       p.job_name, p.steps) for p in pins)


class SelectorSourceTests(NativeApiFixture, unittest.TestCase):
    def jobs_object(self, pin):
        body = super().jobs_object(pin)
        for job in body['jobs']: job['run_attempt'] = pin.attempt
        return body

    def setUp(self):
        self.api_setup(Harness(), BearerCredential(SENSITIVE))
        self.selection = selectors(self.pins)

    def selected_gate(self, values=None):
        return actions.ActionsRunSource(reader=self.api_reader,
            pins=self.selection if values is None else values,
            binding=self.h.binding, credential=self.credential,
            progress=lambda: self.progress, monotonic=lambda: self.h.mono)

    def test_opt_in_selection_reads_exact_job_ids_without_extra_requests_or_app_sha_inference(self):
        self.assertTrue(all(not hasattr(x, 'job_id') for x in self.selection))
        result = self.selected_gate().inspect(self.request())
        self.assertEqual(self.pins, result.resolved_pins)
        self.assertTrue(all(type(x) is actions.StagePin for x in result.resolved_pins))
        self.assertEqual(10, len(self.calls))
        self.assertEqual(1, sum('/runs/101/attempts/1/jobs?' in p for p, _ in self.calls))
        self.assertNotEqual(self.h.binding.source_commit, self.workflow_sha)
        self.assertFalse(result.execution_authorized); self.assertFalse(result.app_lineage_authenticated)
        self.assertNotIn(SENSITIVE, repr(result))
        self.assertTrue(all(end == self.request().deadline for _, end in self.calls))

    def test_changed_native_ids_are_observed_only_in_explicit_selector_mode(self):
        def changed(path, obj):
            if '/jobs?' in path: obj['jobs'][0]['id'] += 600
            return obj
        self.hook = changed
        observed = self.selected_gate().inspect(self.request())
        self.assertEqual((901, 901, 901, 902), tuple(p.job_id for p in observed.resolved_pins))
        with self.assertRaises(actions.ActionsSourceError): self.gate().inspect(self.request())

    def test_duplicate_exact_names_never_pick_first_or_choose_the_successful_one(self):
        def changed(path, obj):
            if '/jobs?' in path:
                extra = copy.deepcopy(obj['jobs'][0]); extra.update(id=999, conclusion='failure')
                obj['jobs'].append(extra); obj['total_count'] = 2
            return obj
        self.hook = changed
        with self.assertRaises(actions.ActionsSourceError): self.selected_gate().inspect(self.request())

    def test_wrong_case_prefix_or_missing_name_does_not_fallback(self):
        for name in ('SYNTHETIC-BUILD', 'synthetic-build-extra', None, ''):
            self.hook = lambda path, obj: (obj | {'jobs': [obj['jobs'][0] | {'name': name}]}
                if '/jobs?' in path else obj)
            with self.subTest(name=name), self.assertRaises(actions.ActionsSourceError):
                self.selected_gate().inspect(self.request())

    def test_required_native_job_and_step_guards_are_still_enforced(self):
        mutations = (lambda j: j.update(id=True), lambda j: j.update(id='301'),
            lambda j: j.pop('run_attempt'), lambda j: j.update(run_attempt=True),
            lambda j: j.update(run_attempt=2),
            lambda j: j.update(run_id=True), lambda j: j.update(head_sha='a'*40),
            lambda j: j.update(conclusion='failure'),
            lambda j: j['steps'][0].update(conclusion='skipped'),
            lambda j: j['steps'][0].update(number=True),
            lambda j: j['steps'][0].update(name='other'))
        for mutate in mutations:
            def changed(path, obj):
                if '/jobs?' in path: mutate(obj['jobs'][0])
                return obj
            self.hook = changed
            with self.assertRaises(actions.ActionsSourceError): self.selected_gate().inspect(self.request())

    def test_incomplete_pages_and_invalid_counts_are_rejected_even_if_required_name_is_present(self):
        for count in (True, 0, 2, 101):
            self.hook = lambda path, obj: obj | {'total_count': count} if '/jobs?' in path else obj
            with self.subTest(total=count), self.assertRaises(actions.ActionsSourceError):
                self.selected_gate().inspect(self.request())

    def test_mixed_policy_or_conflicting_attempt_and_steps_fail_before_io(self):
        mixed = (self.selection[0],) + self.pins[1:]
        conflict = (self.selection[0], replace(self.selection[1], steps=((3, 'wrong'),))) + self.selection[2:]
        rerun = (self.selection[0], replace(self.selection[1],
                                          run=replace(self.selection[1].run, attempt=2))) + self.selection[2:]
        for values in (mixed, conflict, rerun, self.selection[:-1]):
            with self.assertRaises(actions.ActionsSourceError): self.selected_gate(values)
        self.assertEqual([], self.calls)

    def test_latest_attempt_change_or_shared_deadline_expiry_does_not_admit_old_jobs(self):
        for mode in ('attempt', 'deadline'):
            self.h.mono = 1000; count = 0
            def changed(path, obj):
                nonlocal count
                if mode == 'attempt' and path.endswith('/runs/101'):
                    count += 1
                    if count == 2: obj['run_attempt'] = 2
                if mode == 'deadline' and '/jobs?' in path: self.h.mono = 1100
                return obj
            self.hook = changed
            with self.assertRaises(actions.ActionsSourceError): self.selected_gate().inspect(self.request())

    def test_pure_resolver_uses_the_existing_native_validator_and_accepts_only_unique_code_gates(self):
        p = self.pins[0]; body = self.jobs_object(p.run)
        self.assertEqual(((p.job_id, p.job_name, p.steps),), resolve_named_jobs(
            body, p.run.run_id, p.run.workflow_commit, ((p.job_name, p.steps),), attempt=p.run.attempt))
        for gates in ((), ((p.job_name, p.steps),)*2, [('name', p.steps)], (('name',),)):
            with self.assertRaises(ValueError): resolve_named_jobs(
                body, p.run.run_id, p.run.workflow_commit, gates, attempt=p.run.attempt)
        for attempt in (None, True, 0, '1', 2):
            with self.assertRaises(ValueError): resolve_named_jobs(body, p.run.run_id,
                p.run.workflow_commit, ((p.job_name, p.steps),), attempt=attempt)


class SelectorRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.f = runtime_fixture.RuntimeSiteTests('runTest'); self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.native = SelectorSourceTests('runTest')
        # Same actual fixture Binding/credential used by provider and finish.
        self.native.api_setup(self.f.h, self.f.credential)
        self.addCleanup(self.native.doCleanups)
        self.selection = selectors(self.native.pins)

    def app(self, *, finish=True):
        factory = self.f.factory(self.f.slot, producer_pins=self.selection,
                                  producer_run_reader=self.native.api_reader)
        return self.f.app(factory=factory, install=finish)

    def test_real_factory_accepts_selectors_but_finishes_only_after_same_run_sender_close(self):
        out = self.app().run('deploy')
        self.assertEqual('SESSION_COMPLETED', out['status'], out)
        self.assertEqual('RELEASED', out['fence']); self.assertEqual(1, self.f.p.c.storage.complete_calls)
        self.assertTrue(self.native.calls)
        self.assertEqual(['PATCH']*6+['GET']*2, [r.method for r in self.f.h.requests])

    def test_native_failure_blocks_writes_and_does_not_create_finish(self):
        self.native.hook = lambda path, obj: obj | {'conclusion': 'failure'} if '/runs/' in path else obj
        out = self.app().run('deploy')
        self.assertNotEqual('SESSION_COMPLETED', out['status'])
        self.assertEqual(0, self.f.p.c.storage.complete_calls)
        self.assertFalse(any(r.method == 'PATCH' for r in self.f.h.requests))
        self.assertFalse(any(pin.path == 'finish' for _, pin in self.f.read_calls))

    def test_native_producer_success_is_not_a_substitute_for_missing_finish_authority(self):
        out = self.app(finish=False).run('deploy')
        self.assertEqual('COMPLETION_DENIED', out['error_code'], out)
        self.assertEqual('RETAINED', out['fence']); self.assertEqual(0, self.f.p.c.storage.complete_calls)
        self.assertTrue(self.native.calls)

    def test_control_withdrawal_after_sender_close_keeps_fence_despite_native_success(self):
        self.f.read_hook = lambda request, pin, original: (
            replace(original, body=b'SYNTHETIC_WITHDRAWN')
            if pin.path == 'control' and self.f.p.c.sender_closes else original)
        out = self.app().run('deploy')
        self.assertEqual('RETAINED', out['fence'], out)
        self.assertEqual(0, self.f.p.c.storage.complete_calls)


if __name__ == '__main__': unittest.main()
