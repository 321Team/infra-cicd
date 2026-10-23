"""Offline configuration selection. Per-run plan/credentials are SYNTHETIC.

No real issuance, SSH, original readback, Runtime or operating baseline capture.
"""
import contextlib
from dataclasses import replace
import io
import json
from pathlib import Path
import socket
import unittest
from unittest.mock import Mock, patch

import delivery_fds_operating as operating
import delivery_fds_site_inputs as profile
import delivery_job as job
import delivery_job_site as declaration
import delivery_runtime_site as runtime
import test_delivery_fds_job_site as fixtures
from test_delivery_job import CONTEXT


class OperatingSelectionTests(unittest.TestCase):
    def setUp(self):
        for name in ('socket', 'create_connection', 'getaddrinfo'):
            p = patch.object(socket, name, side_effect=AssertionError('NETWORK_FORBIDDEN'))
            p.start(); self.addCleanup(p.stop)
        self.f = fixtures.AssemblyTests('runTest'); self.f.setUp(); self.addCleanup(self.f.doCleanups)
        self.args = {k: v for k, v in self.f.args.items() if k != 'runner'}

    def test_selected_runner_matches_existing_workflow_and_real_assembly_checks(self):
        workflow = (Path(__file__).resolve().parents[2] /
                    '.github/workflows/app-test-build-scan.yml').read_text(encoding='utf-8')
        runner = operating.EXPECTED_RUNNER
        for expected in (f'test "$(hostname -s)" = {runner.hostname}',
                         f'test "$(id -un)" = {runner.account}',
                         f'test "$(id -u)" = {runner.uid}'):
            self.assertIn(expected, workflow)
        value = operating.assemble_job(**self.args)
        self.assertIs(type(value), job.JobSite)
        self.assertEqual(runner, value.runner)
        # Helper UID comes from explicit input, never the Runner selection.
        self.assertEqual(1001, value.build._owner._uid)
        self.assertIs(self.f.f.plan, value.build._parent._plan)
        self.assertIsNone(value.build._owner._listener)
        self.f.clock.assert_not_called(); self.f.publish.assert_not_called()

    def test_selected_runner_cannot_be_overridden_and_missing_helper_does_not_fallback(self):
        with self.assertRaises(TypeError):
            operating.assemble_job(**self.args, runner=job.RunnerIdentity('other', 'other', 2000))
        missing = self.args.copy(); del missing['expected_helper_uid']
        with self.assertRaises(TypeError): operating.assemble_job(**missing)
        self.f.publish.assert_not_called()

    def test_prepared_plan_remains_deferred_and_fixed_profile_rejects_foreign_target(self):
        materializer = Mock(side_effect=AssertionError('NO_ORIGINAL_OR_CREDENTIAL_ACCESS'))
        value = operating.assemble_job(**(self.args | {'plan': None, 'materialize_plan': materializer}))
        self.addCleanup(value.build._parent._plan.close)
        self.assertIs(type(value.build._parent._plan), runtime.PreparedAuthorityPlan)
        materializer.assert_not_called()
        foreign = replace(self.args['config'], binding=replace(
            self.args['config'].binding, namespace_uid='SYNTHETIC_FOREIGN_NAMESPACE'))
        with self.assertRaises(operating.assembly.SiteAssemblyError):
            operating.assemble_job(**(self.args | {'config': foreign}))
        self.f.clock.assert_not_called(); self.f.publish.assert_not_called()

    def test_native_driver_rejects_actual_identity_mismatch_before_selected_builder(self):
        selected = operating.assemble_job(**self.args)
        builder = Mock(side_effect=AssertionError('NO_JOB_BUILD'))
        selected = replace(selected, build=builder)
        for different in (replace(selected.runner, uid=2000),
                          replace(selected.runner, account='different'),
                          replace(selected.runner, hostname='different')):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = job.main(['deploy', '--execute'], declaration=selected, context=CONTEXT,
                    revision=lambda: CONTEXT.workflow_sha, identity=lambda: different)
            self.assertEqual(2, rc)
            self.assertEqual('P2_JOB_RUNNER_REJECTED', json.loads(out.getvalue())['error_code'])
        builder.assert_not_called()

    def test_default_status_reports_partial_selection_without_file_clock_identity_or_runtime(self):
        out = io.StringIO()
        with patch.object(Path, 'open', side_effect=AssertionError('NO_FILE')), \
                patch.object(job, 'native_context', side_effect=AssertionError('NO_NATIVE')), \
                contextlib.redirect_stdout(out):
            rc = job.main(['status'], revision=Mock(side_effect=AssertionError('NO_GIT')),
                          identity=Mock(side_effect=AssertionError('NO_IDENTITY')))
        result = json.loads(out.getvalue())
        self.assertEqual((2, 'P2_JOB_UNCONFIGURED', False, 'NOT_RUN'),
            (rc, result['error_code'], result['dispatch_configured'], result['runtime']))
        state = result['operating_configuration']
        self.assertEqual('PARTIAL', state['selection'])
        self.assertFalse(state['selected']['issuance_assignment']['credential_authenticated'])
        self.assertFalse(state['selected']['runner_expectation']['current_host_observed'])
        self.assertEqual(3, len(state['pending_before_run']))
        self.assertTrue(state['produced_during_run'])

    def test_partial_status_mutation_or_execute_flag_never_opens_default_gate(self):
        status = operating.configuration_status()
        status['pending_before_run'].clear(); status['execution_authorized'] = True
        fresh = operating.configuration_status()
        self.assertEqual(3, len(fresh['pending_before_run']))
        self.assertFalse(fresh['execution_authorized'])
        gate = job.workflow_gate(context=CONTEXT, revision=lambda: CONTEXT.workflow_sha)
        self.assertFalse(gate['dispatch_configured']); self.assertIsNone(declaration.SITE)
        for command in ('preflight', 'deploy', 'restore'):
            with contextlib.redirect_stdout(io.StringIO()):
                rc = job.main([command, '--execute'], identity=Mock(side_effect=AssertionError('NO_HOST')))
            self.assertEqual(2, rc)

    def test_injected_site_status_does_not_claim_default_operating_selection(self):
        selected = operating.assemble_job(**self.args)
        out = io.StringIO()
        with contextlib.redirect_stdout(out): rc = job.main(['status'], declaration=selected)
        self.assertEqual(0, rc)
        self.assertNotIn('operating_configuration', json.loads(out.getvalue()))
        self.assertIs(operating.build_configuration, profile.build_configuration)
        self.assertIs(operating.build_token_issuer, profile.build_token_issuer)


if __name__ == '__main__': unittest.main()
