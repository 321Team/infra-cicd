"""SYNTHETIC native context and existing prepared assembly; no live dispatch.

Tests only the final code declaration/build boundary. Existing source, policy,
channel, finish and provider tests remain their own contracts. No issuance.
"""
import contextlib
from dataclasses import replace
import io
import json
import unittest
from unittest.mock import Mock, patch

import delivery_fds_operating as operating
import delivery_fds_operating_policy as policy
import delivery_job as job
import delivery_job_site as declaration
import delivery_job_rendezvous as rendezvous
import delivery_runtime_site as runtime
import test_delivery_fds_operating_policy as fixtures
from test_delivery_job import CONTEXT


class DeferredSiteTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.OperatingPolicyTests('runTest'); self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.inputs = dict(self.f.args); self.inputs.pop('context')
        self.select = Mock(return_value=self.inputs)
        self.inner = []

    def declared(self):
        return operating.declare_observed_native_job(select_inputs=self.select)

    def capture_inner(self, inner, context, command):
        self.inner.append((inner, context, command))
        self.addCleanup(inner._parent._plan.close)
        return 'SYNTHETIC_DELEGATE_RESULT'

    def test_inert_declaration_and_hosted_gate_status_never_need_a_future_run_or_selector(self):
        value = self.declared()
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(0, job.main(['status'], declaration=value))
        self.assertEqual('NOT_RUN', json.loads(output.getvalue())['runtime'])
        gate = job.workflow_gate(declaration=value, context=CONTEXT,
                                  revision=lambda: CONTEXT.workflow_sha)
        self.assertTrue(gate['dispatch_configured']); self.assertFalse(gate['execution_authorized'])
        self.select.assert_not_called(); self.f.mono.assert_not_called()
        self.f.wall.assert_not_called(); self.f.issue.assert_not_called()
        self.assertIsNone(declaration.SITE)

    def test_actual_context_and_command_reach_existing_native_assembly_only_at_build(self):
        context = replace(CONTEXT, run_id='456')
        value = self.declared()
        with patch.object(rendezvous.OwnedRendezvousBuilder, '__call__',
                          lambda inner, ctx, cmd: self.capture_inner(inner, ctx, cmd)):
            self.assertEqual('SYNTHETIC_DELEGATE_RESULT', value.build(context, 'deploy'))
        self.select.assert_called_once_with(context, 'deploy')
        inner, received, command = self.inner[0]
        self.assertIs(received, context); self.assertEqual('deploy', command)
        self.assertIs(type(inner._parent._plan), runtime.PreparedAuthorityPlan)
        self.assertIsNone(inner._owner._listener)
        self.f.issue.assert_not_called(); self.f.mono.assert_not_called()
        with self.assertRaises(policy.OperatingPolicyError): value.build(context, 'deploy')
        self.assertEqual(1, self.select.call_count)

    def test_preflight_uses_same_existing_policy_and_does_not_change_action_or_create_approval(self):
        with patch.object(rendezvous.OwnedRendezvousBuilder, '__call__',
                          lambda inner, ctx, cmd: self.capture_inner(inner, ctx, cmd)):
            self.assertEqual('SYNTHETIC_DELEGATE_RESULT', self.declared().build(CONTEXT, 'preflight'))
        self.select.assert_called_once_with(CONTEXT, 'preflight')
        self.assertIs(self.f.selection, self.inputs['selection'])
        self.assertEqual('deploy', self.inputs['config'].binding.action)
        self.f.issue.assert_not_called()

    def test_bad_context_or_non_deploy_command_consumes_without_calling_selector(self):
        for context, command in ((None, 'deploy'), (replace(CONTEXT, attempt=2), 'deploy'),
                                 (CONTEXT, 'restore'), (CONTEXT, 'status'), (CONTEXT, None)):
            value = self.declared()
            with self.assertRaises(policy.OperatingPolicyError): value.build(context, command)
            with self.assertRaises(policy.OperatingPolicyError): value.build(CONTEXT, 'deploy')
        self.select.assert_not_called()

    def test_missing_actual_inputs_foreign_dict_and_native_context_override_never_open_a_channel(self):
        borrowed = Mock()
        for selected in ({}, None, [], self.inputs | {'context': replace(CONTEXT, run_id='999')},
                         self.inputs | {'unrecognized_handle': borrowed}):
            self.select.return_value = selected
            with patch.object(rendezvous.OwnedRendezvousBuilder, '__call__') as invoke:
                with self.assertRaises(policy.OperatingPolicyError): self.declared().build(CONTEXT, 'deploy')
            invoke.assert_not_called()
        self.f.issue.assert_not_called()
        borrowed.close.assert_not_called()

    def test_original_control_failure_still_rejects_before_the_owned_native_builder(self):
        selection = self.f.selection
        self.select.return_value = self.inputs | {'selection': replace(selection,
            approval=replace(selection.approval, control_pin=replace(policy.CONTROL_PIN, anchor_commit='e'*40)))}
        with patch.object(rendezvous.OwnedRendezvousBuilder, '__call__') as invoke:
            with self.assertRaises(policy.OperatingPolicyError): self.declared().build(CONTEXT, 'deploy')
        invoke.assert_not_called(); self.f.issue.assert_not_called()

    def test_selector_exception_does_not_leak_or_survive_in_exception_context(self):
        self.select.side_effect = RuntimeError('SYNTHETIC_PRIVATE_SECRET')
        value = self.declared()
        with self.assertRaises(policy.OperatingPolicyError) as caught: value.build(CONTEXT, 'deploy')
        self.assertIsNone(caught.exception.__context__)
        self.assertNotIn('SYNTHETIC_PRIVATE_SECRET', repr(caught.exception))
        with self.assertRaises(policy.OperatingPolicyError): value.build(CONTEXT, 'deploy')
        self.assertEqual(1, self.select.call_count)

    def test_actual_parent_failure_retains_existing_prepared_and_listener_cleanup(self):
        original = operating.assemble_observed_native_job
        captured = []
        def assemble(**kwargs):
            value = original(**kwargs); captured.append(value)
            return value
        with patch.object(operating, 'assemble_observed_native_job', side_effect=assemble):
            with self.assertRaises(policy.OperatingPolicyError): self.declared().build(CONTEXT, 'deploy')
        self.assertEqual(1, len(captured))
        inner = captured[0].build
        self.assertTrue(inner._owner._closed)
        self.assertTrue(inner._parent._plan._closed)
        self.assertIsNone(inner._parent._plan._materialize)
        self.assertTrue(inner._parent._plan.close())
        self.f.issue.assert_not_called()

    def test_interrupts_are_sanitized_and_consumed_without_runtime_retry(self):
        for error, kind in ((KeyboardInterrupt('SYNTHETIC_PRIVATE'), KeyboardInterrupt),
                            (SystemExit('SYNTHETIC_PRIVATE'), SystemExit)):
            self.select.side_effect = error; value = self.declared()
            with self.assertRaises(kind) as caught: value.build(CONTEXT, 'deploy')
            self.assertIsNone(caught.exception.__context__)
            self.assertNotIn('SYNTHETIC_PRIVATE', repr(caught.exception))
            if kind is SystemExit: self.assertEqual(130, caught.exception.code)
            with self.assertRaises(policy.OperatingPolicyError): value.build(CONTEXT, 'deploy')

    def test_assembled_plan_is_closed_when_delegate_interrupts_before_parent_entry(self):
        original = operating.assemble_observed_native_job
        captured = []
        def assemble(**kwargs):
            value = original(**kwargs); captured.append(value)
            return value
        with patch.object(operating, 'assemble_observed_native_job', side_effect=assemble), \
             patch.object(rendezvous.OwnedRendezvousBuilder, '__call__', side_effect=KeyboardInterrupt()):
            with self.assertRaises(KeyboardInterrupt): self.declared().build(CONTEXT, 'deploy')
        inner = captured[0].build
        self.assertTrue(inner._parent._plan._closed)
        self.assertIsNone(inner._parent._plan._materialize)
        self.assertTrue(inner._owner._closed)
        self.f.issue.assert_not_called()

    def test_internal_cleanup_failure_is_reported_without_closing_borrowed_selector_values(self):
        original = operating.assemble_observed_native_job
        captured = []
        def assemble(**kwargs):
            value = original(**kwargs); captured.append(value)
            return value
        original_close = runtime.PreparedAuthorityPlan.close
        def incomplete(plan): original_close(plan); return False
        with patch.object(runtime.PreparedAuthorityPlan, 'close', incomplete), \
             patch.object(operating, 'assemble_observed_native_job', side_effect=assemble), \
             patch.object(rendezvous.OwnedRendezvousBuilder, '__call__', side_effect=ValueError('PRIVATE')):
            with self.assertRaises(policy.OperatingPolicyError) as caught:
                self.declared().build(CONTEXT, 'deploy')
        self.assertEqual('FDS_OPERATING_SELECTION_CLEANUP_FAILED', caught.exception.args[0])
        self.assertTrue(captured[0].build._owner._closed)
        self.assertIs(self.inputs['selection'], self.f.selection)
        self.f.issue.assert_not_called()


if __name__ == '__main__': unittest.main()
