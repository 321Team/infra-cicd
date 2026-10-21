"""SYNTHETIC job/source/Actions data only; no real authority or outbound network."""
import contextlib
from dataclasses import replace
import io
import json
import os
import socket
import subprocess
import sys
import unittest
from unittest.mock import Mock, patch

import delivery_job as job
import delivery_job_site as declaration
import delivery_runtime_site as runtime
import delivery_site as site
import test_delivery_source_session as source_fixture
import test_delivery_actions_source as actions_fixture


CONTEXT = job.JobContext(job.REPOSITORY, 'workflow_dispatch', 'refs/heads/main', 'f'*40, '123', 1)
RUNNER = job.RunnerIdentity('synthetic-runner', 'synthetic_user', 1000)


class JobGuardTests(unittest.TestCase):
    def setUp(self):
        self.builder = Mock(side_effect=AssertionError('BUILDER_FORBIDDEN'))
        self.site = job.JobSite(RUNNER, self.builder)
        for name in ('socket', 'getaddrinfo', 'create_connection'):
            p = patch.object(socket, name, side_effect=AssertionError('NETWORK_FORBIDDEN'))
            p.start(); self.addCleanup(p.stop)

    def call(self, args=('deploy', '--execute'), **kwargs):
        options = dict(declaration=self.site, context=CONTEXT,
            revision=lambda: CONTEXT.workflow_sha, identity=lambda: RUNNER)
        options.update(kwargs)
        out = io.StringIO()
        with contextlib.redirect_stdout(out): rc = job.main(list(args), **options)
        self.assertEqual(1, len(out.getvalue().strip().splitlines()))
        self.assertNotIn('SYNTHETIC_SECRET', out.getvalue())
        return rc, json.loads(out.getvalue())

    def test_default_site_stays_unconfigured_no_identity_or_builder(self):
        self.assertIsNone(declaration.SITE)
        for command in ('status', 'deploy'):
            rc, result = self.call((command, '--execute'), declaration=job._DEFAULT,
                identity=Mock(side_effect=AssertionError('IDENTITY_FORBIDDEN')))
            self.assertEqual((2, 'P2_JOB_UNCONFIGURED', 'NOT_RUN'),
                (rc, result['error_code'], result['runtime']))
        self.builder.assert_not_called()

    def test_status_does_not_access_native_metadata_git_runner_or_factory(self):
        rc, result = self.call(('status',), context=None,
            revision=Mock(side_effect=AssertionError('GIT_FORBIDDEN')),
            identity=Mock(side_effect=AssertionError('IDENTITY_FORBIDDEN')))
        self.assertEqual(0, rc); self.assertFalse(result['execution_authorized'])
        self.assertEqual('NOT_RUN', result['runtime']); self.builder.assert_not_called()

    def test_hosted_gate_checks_code_but_never_runner_or_builder(self):
        result = job.workflow_gate(declaration=self.site, context=CONTEXT, revision=lambda: CONTEXT.workflow_sha)
        self.assertTrue(result['dispatch_configured']); self.assertFalse(result['execution_authorized'])
        self.assertEqual('NOT_RUN', result['runtime']); self.builder.assert_not_called()
        rc, result = self.call(('gate',), declaration=job._DEFAULT)
        self.assertEqual(2, rc); self.assertFalse(result['dispatch_configured'])

    def test_execute_request_required_before_native_code_or_builder(self):
        rc, result = self.call(('deploy',), revision=Mock(side_effect=AssertionError('GIT_FORBIDDEN')))
        self.assertEqual('P2_JOB_EXECUTE_REQUIRED', result['error_code']); self.builder.assert_not_called()

    def test_native_context_rejects_each_wrong_field_before_builder(self):
        changes = (dict(repository='other/repo'), dict(event='push'), dict(ref='refs/heads/feature'),
            dict(workflow_sha='f'*39), dict(run_id='0'), dict(run_id=123), dict(attempt=2), dict(attempt=True))
        for change in changes:
            with self.subTest(change=change):
                rc, result = self.call(context=replace(CONTEXT, **change))
                self.assertEqual((2, 'P2_JOB_CONTEXT_REJECTED'), (rc, result['error_code']))
        self.builder.assert_not_called()

    def test_code_sha_and_every_runner_identity_field_reject_before_builder(self):
        rc, result = self.call(revision=lambda: 'a'*40)
        self.assertEqual('P2_JOB_CODE_REVISION_REJECTED', result['error_code'])
        for actual in (replace(RUNNER, hostname='other'), replace(RUNNER, account='other'),
                       replace(RUNNER, uid=1001), Mock()):
            rc, result = self.call(identity=lambda: actual)
            self.assertEqual('P2_JOB_RUNNER_REJECTED', result['error_code'])
        self.builder.assert_not_called()

    def test_native_environment_only_metadata_attempt_exact(self):
        env = dict(GITHUB_REPOSITORY=CONTEXT.repository, GITHUB_EVENT_NAME=CONTEXT.event,
            GITHUB_REF=CONTEXT.ref, GITHUB_SHA=CONTEXT.workflow_sha, GITHUB_RUN_ID=CONTEXT.run_id,
            GITHUB_RUN_ATTEMPT='1', GITHUB_TOKEN='SYNTHETIC_SECRET', AWS_SECRET_ACCESS_KEY='SYNTHETIC_SECRET')
        with patch.dict(os.environ, env, clear=True):
            self.assertEqual(CONTEXT, job.native_context())
            for attempt in ('2', '01', '', 'true'):
                os.environ['GITHUB_RUN_ATTEMPT'] = attempt
                with self.assertRaises(job.JobError): job.native_context()

    def test_git_revision_clears_ambient_git_options_and_never_echoes_stderr(self):
        completed = subprocess.CompletedProcess([], 0, b'f'*40+b'\n')
        with patch.dict(os.environ, {'GIT_DIR': 'SYNTHETIC_SECRET', 'GIT_WORK_TREE': 'other'}), \
                patch.object(subprocess, 'run', return_value=completed) as call:
            self.assertEqual(CONTEXT.workflow_sha, job.code_revision())
        env = call.call_args.kwargs['env']
        self.assertNotIn('GIT_DIR', env); self.assertNotIn('GIT_WORK_TREE', env)
        self.assertEqual('1', env['GIT_NO_LAZY_FETCH'])
        self.assertEqual(subprocess.DEVNULL, call.call_args.kwargs['stderr'])

    def test_untyped_return_never_invokes_arbitrary_close(self):
        fake = Mock(); self.builder.side_effect = None; self.builder.return_value = fake
        rc, result = self.call()
        self.assertEqual('P2_JOB_INVOCATION_REJECTED', result['error_code']); fake.close.assert_not_called()

    def test_builder_error_and_prints_do_not_leak_or_claim_started_runtime(self):
        def bad(*unused):
            print('SYNTHETIC_SECRET')
            raise ValueError('SYNTHETIC_SECRET')
        self.builder.side_effect = bad
        rc, result = self.call()
        self.assertEqual('P2_JOB_PREPARATION_FAILED', result['error_code'])
        self.assertEqual('NOT_RUN', result['runtime'])

    def test_safe_error_uses_exact_type_and_literal_allowlist(self):
        for error in (ValueError('SYNTHETIC_SECRET'), job.JobError('P2_JOB_SECRET_VALUE')):
            self.assertEqual('P2_JOB_PREPARATION_FAILED', job.safe_error(error))
        self.assertEqual('P2_JOB_UNCONFIGURED', job.safe_error(job.JobError('P2_JOB_UNCONFIGURED')))

    def test_output_decoder_rejects_duplicate_nonfinite_or_authorizing_output(self):
        for text in ('{}', '[]', '{"status":"x","status":"y"}', '{"status":"x","n":NaN}',
                     '{"status":"x","execution_authorized":true}', 'SYNTHETIC_SECRET'):
            with self.subTest(text=text), self.assertRaises(job.JobError): job.decode_result(text)
        out = job.BoundedOutput(); out.write('가'*(job.MAX_OUTPUT//3))
        out.write('가')
        self.assertTrue(out.overflow)
        self.assertLessEqual(len(out.getvalue().encode('utf-8')), job.MAX_OUTPUT)

    def test_arguments_do_not_echo_unrecognized_secret(self):
        out = io.StringIO()
        with contextlib.redirect_stderr(out), self.assertRaises(SystemExit):
            self.call(('deploy', '--token', 'SYNTHETIC_SECRET'))
        self.assertNotIn('SYNTHETIC_SECRET', out.getvalue()); self.builder.assert_not_called()


class JobIntegrationTests(actions_fixture.NativeApiFixture, unittest.TestCase):
    def setUp(self):
        self.s = source_fixture.SourceSessionTests('runTest'); self.s.setUp()
        self.addCleanup(self.s.doCleanups)
        self.f = self.s.f
        self.invocation = job.RuntimeInvocation(CONTEXT, self.f.config(), self.f.plan(),
            self.f.slot, self.s.rosa, self.f.h.mono+5, source_session=self.s.session)
        self.builder = Mock(return_value=self.invocation)
        self.site = job.JobSite(RUNNER, self.builder)
        for name in ('socket', 'getaddrinfo', 'create_connection'):
            p = patch.object(socket, name, side_effect=AssertionError('NETWORK_FORBIDDEN'))
            p.start(); self.addCleanup(p.stop)

    def call(self, command='deploy', **kwargs):
        options = dict(declaration=self.site, context=CONTEXT,
            revision=lambda: CONTEXT.workflow_sha, identity=lambda: RUNNER)
        options.update(kwargs)
        original = runtime.RuntimeAuthorityFactory
        def factory(*a, **kw):
            return original(*a, **kw, wall=lambda: self.f.h.now, monotonic=lambda: self.f.h.mono)
        def build(config, *, credential, authority_factory):
            self.assertIs(credential, self.f.credential)
            return self.f.app(factory=authority_factory)
        out = io.StringIO()
        with patch.object(runtime, 'RuntimeAuthorityFactory', side_effect=factory), \
                patch.object(site, 'build_application', side_effect=build), contextlib.redirect_stdout(out):
            rc = job.main([command, '--execute'], **options)
        self.assertEqual(1, len(out.getvalue().strip().splitlines()))
        self.assertNotIn(source_fixture.TOKEN, out.getvalue())
        return rc, json.loads(out.getvalue())

    def test_actual_runtime_source_factory_finish_connected_distinct_workflow_and_app_sha(self):
        self.assertNotEqual(CONTEXT.workflow_sha, self.f.config().binding.source_commit)
        rc, result = self.call()
        self.assertEqual(0, rc); self.assertEqual('SESSION_COMPLETED', result['status'], result)
        self.assertEqual('RELEASED', result['result']['fence'])
        self.assertEqual(CONTEXT.workflow_sha, result['job']['workflow_sha'])
        self.assertEqual(CONTEXT.run_id, result['job']['run_id'])
        self.builder.assert_called_once_with(CONTEXT, 'deploy')
        self.assertEqual(1, self.f.p.c.storage.complete_calls)
        self.assertIn('finish', self.s.blobs); self.s.closed()

    def test_preflight_observes_without_claim_or_write(self):
        rc, result = self.call('preflight')
        self.assertEqual((0, 'PREFLIGHT_OBSERVED'), (rc, result['status']), result)
        self.assertEqual([], self.f.p.c.storage.claims); self.assertEqual([], self.f.h.requests)
        self.s.closed()

    def test_action_mismatch_closes_live_owned_channels_before_any_receipt(self):
        rc, result = self.call('restore')
        self.assertEqual('P2_JOB_ACTION_REJECTED', result.get('error_code'))
        for receiver in self.s.receivers: receiver.receive.assert_not_called()
        self.assertEqual([], self.f.p.c.storage.claims); self.s.closed_receivers()

    def test_invocation_context_mismatch_also_closes_before_receipt(self):
        self.builder.return_value = replace(self.invocation, context=replace(CONTEXT, run_id='456'))
        rc, result = self.call()
        self.assertEqual('P2_JOB_INVOCATION_REJECTED', result.get('error_code'))
        for receiver in self.s.receivers: receiver.receive.assert_not_called()
        self.s.closed_receivers()

    def test_second_use_rejected_without_replaying_runtime(self):
        rc, first = self.call(); self.assertEqual(0, rc)
        rc, second = self.call()
        self.assertEqual('P2_JOB_INVOCATION_REJECTED', second['error_code'])
        self.assertEqual(1, self.f.p.c.storage.complete_calls)

    def test_runtime_exception_preserves_uncertain_state_and_closes_channels(self):
        with patch.object(runtime, 'main', side_effect=ValueError(source_fixture.TOKEN)):
            rc, result = self.call()
        self.assertEqual((2, 'NOT_VERIFIED', 'INSPECT_REQUIRED'), (rc, result['runtime'], result['fence']))
        self.s.closed_receivers()

    def test_output_limit_does_not_interrupt_runtime_and_return_code_is_not_a_receipt(self):
        reached = []
        def output(*args, **kwargs):
            print('SYNTHETIC_SECRET' * 20000, file=sys.stderr)
            reached.append('AFTER_OUTPUT')
            print('{"status":"SESSION_COMPLETED","execution_authorized":false}')
            return 0
        with patch.object(runtime, 'main', side_effect=output):
            rc, result = self.call()
        self.assertEqual(['AFTER_OUTPUT'], reached)
        self.assertEqual((2, 'P2_JOB_OUTPUT_REJECTED', 'INSPECT_REQUIRED', 0),
            (rc, result.get('error_code'), result.get('fence'), result.get('runtime_returncode')))
        self.assertNotIn('result', result); self.s.closed_receivers()

    def test_builder_overflow_after_typed_transfer_still_closes_handles(self):
        def output(*unused):
            print('SYNTHETIC_SECRET' * 20000)
            return self.invocation
        self.builder.side_effect = output
        rc, result = self.call()
        self.assertEqual((2, 'P2_JOB_OUTPUT_REJECTED', 'NOT_RUN'),
            (rc, result['error_code'], result['runtime']))
        for receiver in self.s.receivers: receiver.receive.assert_not_called()
        self.s.closed_receivers()

    def test_runtime_bad_json_is_redacted_without_asserting_no_fence(self):
        with patch.object(runtime, 'main', side_effect=lambda *a, **k: print('SYNTHETIC_SECRET') or 2):
            rc, result = self.call()
        self.assertEqual((2, 'P2_JOB_OUTPUT_REJECTED', 'NOT_VERIFIED', 2),
            (rc, result['error_code'], result['runtime'], result['runtime_returncode']))
        self.s.closed_receivers()

    def test_keyboard_interrupt_still_closes_transferred_handles(self):
        with patch.object(runtime, 'main', side_effect=KeyboardInterrupt()), self.assertRaises(KeyboardInterrupt):
            self.call()
        self.s.closed_receivers()

    def test_cleanup_error_preserves_completed_receipt_and_released_fence(self):
        with patch.object(job.RuntimeInvocation, 'close', return_value=False):
            rc, result = self.call()
        self.assertEqual((2, 'STOPPED', 'P2_JOB_CHANNEL_CLEANUP_FAILED'),
            (rc, result['status'], result.get('error_code')))
        self.assertEqual('SESSION_COMPLETED', result['result']['status'])
        self.assertEqual('RELEASED', result['result']['fence'])
        self.assertEqual(1, self.f.p.c.storage.complete_calls)

    def test_source_failure_after_write_keeps_fence_and_closes_owned_handles(self):
        def changed(request, pin, original):
            return replace(original, body=b'SYNTHETIC_WITHDRAWN') if self.f.h.requests else original
        self.f.read_hook = changed
        rc, result = self.call()
        self.assertEqual(2, rc); self.assertEqual('RETAINED', result['result']['fence'])
        self.assertEqual(1, len(self.f.h.requests)); self.s.closed()

    def actions(self):
        self.api_setup(self.f.h, self.f.credential)
        def wire(pool):
            roles = self.s.wire(pool)
            roles['producer_run_reader'] = self.api_reader
            return roles
        self.s.session.wire = wire
        self.invocation = replace(self.invocation, producer_pins=self.pins)
        self.builder.return_value = self.invocation

    def test_native_actions_source_role_forwarded_through_real_job_source_runtime(self):
        self.actions()
        rc, result = self.call()
        self.assertEqual((0, 'SESSION_COMPLETED'), (rc, result['status']), result)
        self.assertTrue(any('/jobs?' in path for path, _ in self.calls))
        self.assertNotEqual(self.workflow_sha, self.f.h.binding.source_commit)
        self.assertEqual('RELEASED', result['result']['fence']); self.s.closed()

    def test_failed_native_actions_run_blocks_before_claim_and_write(self):
        self.actions()
        self.hook = lambda path, obj: obj | {'conclusion': 'failure'} if '/runs/' in path else obj
        rc, result = self.call()
        self.assertEqual(2, rc); self.assertNotEqual('SESSION_COMPLETED', result['status'])
        self.assertEqual([], self.f.p.c.storage.claims); self.assertEqual([], self.f.h.requests)
        self.assertTrue(self.calls); self.s.closed()


if __name__ == '__main__': unittest.main()
