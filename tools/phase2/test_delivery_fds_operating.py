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
import delivery_fds_existing_inputs as existing
import delivery_fds_management_route as management
import delivery_job_sources as sources
import delivery_finish_observation as observation
import delivery_job as job
import delivery_job_site as declaration
import delivery_runtime_site as runtime
import test_delivery_fds_job_site as fixtures
import test_delivery_job_sources as source_fixtures
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

    def test_actor_channel_uses_fixed_profile_and_requires_all_current_inputs_without_io(self):
        from delivery_object_reader import BearerCredential
        import delivery_actor_channel as actor_channel
        clock = Mock(side_effect=AssertionError('NO_CLOCK'))
        args = dict(actor=BearerCredential('sha256~SYNTHETIC_ACTOR_TOKEN_ONLY_1234567890'),
            endpoint_ip='192.0.2.10', operation_deadline=20, cleanup_deadline=30,
            monotonic=clock, wall=clock)
        # This fixture already patches fixed profile values to SYNTHETIC ones.
        with patch.object(actor_channel, 'ActorCredentialChannel') as create:
            operating.build_actor_channel(self.args['config'], **args)
        passed = create.call_args.kwargs
        self.assertEqual(operating.ISSUANCE_OPERATOR, passed['expected_username'])
        self.assertEqual(profile.SUBJECT_UID, passed['target'].uid)
        self.assertEqual(profile.CA_PEM, passed['ca_pem'])
        self.assertEqual(args['endpoint_ip'], passed['endpoint_ip'])
        for field in args:
            missing = args.copy(); del missing[field]
            with self.subTest(field=field), self.assertRaises(TypeError):
                operating.build_actor_channel(self.args['config'], **missing)
        with self.assertRaises(TypeError):
            operating.build_actor_channel(self.args['config'], **args, expected_username='other')
        foreign = replace(self.args['config'], binding=replace(
            self.args['config'].binding, namespace_uid='SYNTHETIC_OTHER'))
        with patch.object(actor_channel, 'ActorCredentialChannel') as create, \
             self.assertRaises(profile.FdsSiteInputError):
            operating.build_actor_channel(foreign, **args)
        create.assert_not_called(); clock.assert_not_called(); self.assertIsNone(declaration.SITE)

    def test_deferred_login_builder_uses_adopted_actor_and_sa_without_oauth_defaults(self):
        import delivery_login_channel as deferred
        clock=Mock(side_effect=AssertionError('NO_CLOCK'))
        args=dict(login_provider=object(), endpoint_ip='192.0.2.10',
                  operation_deadline=20, cleanup_deadline=30, monotonic=clock, wall=clock)
        with patch.object(deferred,'LoginActorChannel') as create:
            operating.build_login_actor_channel(self.args['config'],**args)
        passed=create.call_args.kwargs
        self.assertIs(args['login_provider'],passed['login_provider'])
        self.assertEqual(operating.ISSUANCE_OPERATOR,passed['expected_username'])
        self.assertEqual(profile.SUBJECT_UID,passed['target'].uid)
        self.assertEqual(profile.CA_PEM,passed['ca_pem'])
        for key in args:
            with self.subTest(key=key), self.assertRaises(TypeError):
                operating.build_login_actor_channel(self.args['config'],**{k:v for k,v in args.items() if k!=key})
        clock.assert_not_called(); self.assertIsNone(declaration.SITE)

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
        self.assertEqual(2, len(state['pending_before_run']))
        self.assertFalse(state['selected']['baseline_expectation']['current_live_checked'])
        self.assertFalse(state['selected']['baseline_expectation']['force_authorized'])
        self.assertTrue(state['produced_during_run'])

    def test_partial_status_mutation_or_execute_flag_never_opens_default_gate(self):
        status = operating.configuration_status()
        status['pending_before_run'].clear(); status['execution_authorized'] = True
        fresh = operating.configuration_status()
        self.assertEqual(2, len(fresh['pending_before_run']))
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


class AdoptedOperatingTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.AssemblyTests('runTest'); self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.scope = replace(self.f.f.plan.approval,
            original_pin=replace(self.f.f.plan.approval.original_pin,
                path='SYNTHETIC-existing-completion-scope'),
            adoption_reference='synthetic:explicit-existing-completion-scope')
        self.args = {k: v for k, v in self.f.args.items()
                     if k not in ('runner', 'wire', 'expected_helper_uid')}
        self.args.update(role_routes=self.f.wire._routes, finish_scope=self.scope,
                         wall=self.f.wall)
        self.bridge_args = dict(workspace='/SYNTHETIC/workspace', context=CONTEXT,
            port=2201, host_key_alias='synthetic-reviewed-host',
            remote_python='/synthetic/python3', remote_timeout_seconds=30)

    def test_helper_path_uses_exact_existing_native_checkout_without_io_or_ambient_env(self):
        workflow = (Path(__file__).resolve().parents[2] /
                    '.github/workflows/p2-rosa-delivery.yml').read_text(encoding='utf-8')
        self.assertIn('P2_CODE_CHECKOUT: p2-job-${{ github.run_id }}-${{ github.run_attempt }}', workflow)
        self.assertIn('ref: ${{ github.sha }}', workflow)
        self.assertTrue((Path(__file__).resolve().parents[2] / operating.HELPER_ENTRYPOINT).is_file())
        with patch.object(Path, 'open', side_effect=AssertionError('NO_FILE')), \
             patch.object(job, 'native_context', side_effect=AssertionError('NO_ENV_CONTEXT')), \
             patch.object(job.subprocess, 'Popen', side_effect=AssertionError('NO_SSH')):
            config = operating.build_job_bridge_configuration(**self.bridge_args)
        self.assertEqual('/SYNTHETIC/workspace/p2-job-123-1/' + operating.HELPER_ENTRYPOINT,
                         config.remote_helper)
        self.assertEqual((management.TARGET_IP, management.EXPECTED_RUNNER.account,
                          management.IDENTITY_FILE, management.KNOWN_HOSTS),
                         (config.host_ip, config.user, config.identity_file, config.known_hosts))
        # A new native run selects its OWN lexical checkout, not a cached helper.
        next_config = operating.build_job_bridge_configuration(**(self.bridge_args |
                      {'context': replace(CONTEXT, run_id='124')}))
        self.assertIn('/p2-job-124-1/', next_config.remote_helper)

    def test_helper_rejects_ambiguous_workspace_without_normalization_or_discovery(self):
        for workspace in (None, Path('/synthetic'), '', '/', 'relative', '/a/', '/a//b',
                          '/a/../b', '/a/./b', '//host/a', 'C:/a', '/a b', '/a/%h',
                          '/a/~user', '/a;command', '/a\\b', '/' + 'a'*1000):
            with self.subTest(workspace=workspace), self.assertRaisesRegex(
                    operating.assembly.SiteAssemblyError, 'HELPER_CONFIGURATION_INVALID'):
                operating.build_job_bridge_configuration(**(self.bridge_args | {'workspace': workspace}))

    def test_helper_rejects_invalid_native_context_and_no_helper_override(self):
        for context in (None, CONTEXT.__dict__, replace(CONTEXT, run_id='../1'),
                        replace(CONTEXT, run_id='01'), replace(CONTEXT, run_id=True),
                        replace(CONTEXT, attempt=True), replace(CONTEXT, attempt=2),
                        replace(CONTEXT, workflow_sha='F'*40), replace(CONTEXT, workflow_sha='f'*39),
                        replace(CONTEXT, ref='refs/heads/other'), replace(CONTEXT, event='push')):
            with self.subTest(context=context), self.assertRaises(operating.assembly.SiteAssemblyError):
                operating.build_job_bridge_configuration(**(self.bridge_args | {'context': context}))
        with self.assertRaises(TypeError):
            operating.build_job_bridge_configuration(**self.bridge_args, remote_helper='/other/helper.py')

    def test_transport_unknowns_remain_required_and_existing_strict_validation_runs(self):
        for field in self.bridge_args:
            args = self.bridge_args.copy(); del args[field]
            with self.subTest(missing=field), self.assertRaises(TypeError):
                operating.build_job_bridge_configuration(**args)
        for change in ({'port': 0}, {'port': True}, {'host_key_alias': '-unsafe'},
                       {'remote_python': 'python3'}, {'remote_timeout_seconds': 0}):
            with self.subTest(change=change), self.assertRaises(operating.assembly.SiteAssemblyError):
                operating.build_job_bridge_configuration(**(self.bridge_args | change))

    def test_observed_assembly_fixes_five_roles_same_account_policy_and_borrowed_plan_without_io(self):
        with patch.object(Path, 'open', side_effect=AssertionError('NO_FILE')), \
             patch.object(socket, 'socket', side_effect=AssertionError('NO_SOCKET')):
            selected = operating.assemble_observed_job(**self.args)
        parent, owner = selected.build._parent, selected.build._owner
        self.assertEqual(operating.EXPECTED_RUNNER, selected.runner)
        self.assertEqual(management.EXPECTED_HELPER_UID, owner._uid)
        self.assertIs(parent._plan, self.f.f.plan)
        self.assertIs(parent._wire._finish.scope_grant, self.scope)
        self.assertEqual(observation.FOLLOWUPS, parent._wire._finish.required_followups)
        self.assertEqual(sources.ROLES + ('baseline',), parent._wire.roles)
        self.assertIs(self.f.clock, parent._wire._clock)
        self.assertIs(self.f.wall, parent._wire._wall)
        self.assertEqual(self.args['deadline'], owner._deadline)
        self.assertIsNone(parent._capture); self.assertIsNone(owner._listener)
        self.f.wall.assert_not_called(); self.f.clock.assert_not_called(); self.f.publish.assert_not_called()
        self.assertIsNone(declaration.SITE)

    def test_observation_policy_does_not_accept_capture_wire_uid_or_runner_override(self):
        for change in ({'finish_capture': Mock()}, {'wire': self.f.wire},
                       {'expected_helper_uid': 1001}, {'runner': operating.EXPECTED_RUNNER}):
            with self.subTest(change=tuple(change)), self.assertRaises(TypeError):
                operating.assemble_observed_job(**(self.args | change))

    def test_missing_or_foreign_scope_and_invalid_role_routes_stop_before_rendezvous(self):
        other_repo = replace(self.scope.original_pin.repository, name='SYNTHETIC-other')
        wrong_binding = replace(self.scope, binding=replace(self.scope.binding, run_id='SYNTHETIC_OTHER'))
        for scope in (None, {}, wrong_binding,
                      replace(self.scope, original_pin=replace(self.scope.original_pin, repository=other_repo)),
                      replace(self.scope, control_pin=replace(self.scope.control_pin, repository=other_repo))):
            with self.subTest(scope_type=type(scope)), \
                 patch.object(operating.assembly.rendezvous, 'UnixJobRendezvous') as create:
                with self.assertRaises(operating.assembly.SiteAssemblyError):
                    operating.assemble_observed_job(**(self.args | {'finish_scope': scope}))
                create.assert_not_called()
        roles = self.args['role_routes']
        for bad in (roles[:-1], roles[1:]+roles[:1], roles+(roles[-1],), list(roles)):
            with self.subTest(roles_type=type(bad)), self.assertRaises(operating.assembly.SiteAssemblyError):
                operating.assemble_observed_job(**(self.args | {'role_routes': bad}))
        self.f.publish.assert_not_called(); self.f.clock.assert_not_called()

    def test_deferred_plan_remains_unconsumed_and_existing_validation_is_not_bypassed(self):
        materialize = Mock(return_value=self.f.f.plan)
        selected = operating.assemble_observed_job(**(self.args |
            {'plan': None, 'materialize_plan': materialize}))
        plan = selected.build._parent._plan; self.addCleanup(plan.close)
        self.assertIs(type(plan), runtime.PreparedAuthorityPlan)
        materialize.assert_not_called()
        self.assertIs(self.f.f.plan, plan._materialize(None, None, None, None, None))
        # Existing profile rejects an empty-target plan, even through materialization.
        materialize.return_value = self.f.f.f.plan
        with self.assertRaises(existing.FdsExistingInputError):
            plan._materialize(None, None, None, None, None)
        self.f.clock.assert_not_called(); self.f.publish.assert_not_called()

    def test_failed_observed_assembly_closes_only_its_internal_prepared_plan(self):
        made = []; original = runtime.PreparedAuthorityPlan.__init__
        def observe(value, *args, **kwargs):
            original(value, *args, **kwargs); made.append(value)
        with patch.object(runtime.PreparedAuthorityPlan, '__init__', new=observe), \
             patch.object(operating.assembly.rendezvous, 'OwnedRendezvousBuilder',
                          side_effect=ValueError('SYNTHETIC_SECRET')), \
             patch.object(self.args['finish_slot'], 'close', create=True) as close_slot:
            with self.assertRaises(operating.assembly.SiteAssemblyError) as caught:
                operating.assemble_observed_job(**(self.args | {'plan': None, 'materialize_plan': Mock()}))
        self.assertEqual(1, len(made)); self.assertTrue(made[0]._closed)
        close_slot.assert_not_called(); self.assertNotIn('SYNTHETIC_SECRET', str(caught.exception))

    def test_adopted_design_status_is_fresh_and_never_claims_live_readiness_or_grant(self):
        status = operating.configuration_status(); picked = status['selected']
        self.assertEqual('gwonuk', picked['issuance_assignment']['operator'])
        self.assertEqual((management.SOURCE_HOST, management.SOURCE_ACCOUNT),
            (picked['issuer_location_expectation']['hostname'], picked['issuer_location_expectation']['account']))
        self.assertFalse(picked['issuer_location_expectation']['current_location_verified'])
        self.assertFalse(picked['helper_layout']['workspace_selected'])
        self.assertFalse(picked['finish_policy']['scope_control_connected'])
        self.assertIs(picked['finish_policy']['includes_resource_cleanup'], False)
        picked['finish_policy']['followups'].clear()
        self.assertEqual(list(observation.FOLLOWUPS), operating.configuration_status()['selected']['finish_policy']['followups'])
        self.assertFalse(job.workflow_gate(context=CONTEXT, revision=lambda: CONTEXT.workflow_sha)['dispatch_configured'])
        self.assertFalse(status['execution_authorized']); self.assertIsNone(declaration.SITE)


class AdoptedSourceConnectionTests(unittest.TestCase):
    """New operating composition -> real private-source/factory/finish code.

    All credentials, origins, objects, grants and observations are SYNTHETIC;
    no bridge process, real host, token issuance or operating SITE is used.
    """
    def setUp(self):
        import delivery_adapter as core
        from test_delivery_objects import owned
        self.core = core
        self.f = source_fixtures.JobSourceWiringTests('runTest'); self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.saved = self.f.observation_setup()
        base = self.f.base
        self.assertEqual('ROLLOUT_OBSERVED', base.h.run()['status'])
        for i, key in enumerate(core.OBJECTS):
            path = core.path_for(key, base.h.binding)
            base.h.objects[path] = owned(base.h.objects[path], i)
        base.h.requests.clear()
        raw = b'SYNTHETIC_EXISTING_BASELINE_ADOPTION'
        base.raws['baseline'] = raw
        grant = replace(base.grants['approval'], original_pin=replace(
            base.pins['approval'], path='baseline', sha256=core.digest(raw)),
            adoption_reference='synthetic:reviewed-existing-baseline')
        plan = base.plan()
        plan = replace(plan, baseline=runtime.BaselineAdoption(grant, tuple(
            core.encoded(base.h.objects[core.path_for(key, base.h.binding)]) for key in core.OBJECTS)))
        b, facts, subject = base.h.binding, plan.issuer.facts, plan.subject
        values = dict(API_ORIGIN=b.api_origin, CLUSTER_UUID=b.cluster_id, NAMESPACE_UID=b.namespace_uid,
            SUBJECT=subject.username, SUBJECT_UID=subject.uid, ISSUER=facts.issuer, AUDIENCE=facts.audience,
            CA_PEM=facts.ca_pem, CA_PEM_SHA256=b.ca_sha256, SOURCE_COMMIT=b.source_commit,
            CONTRACT_SHA256=b.contract_sha256, RENDERED_SHA256=b.rendered_sha256,
            ROUTE_HOST=b.route_host, IMAGES=b.images)
        from delivery_crosshost_ingress import PeerIdentity, SourceRoute
        clock = self.f.session.now
        with patch.multiple(profile, **values):
            self.selected = operating.assemble_observed_job(config=base.config(), plan=plan,
                finish_slot=base.slot, expected_peer=PeerIdentity('synthetic:peer', 'synthetic:transport'),
                routes=(SourceRoute(self.f.repo, base.h.mono+20, facts.ca_pem, '192.0.2.10'),),
                role_routes=self.f.routes() + (sources.RoleRoute('baseline', self.f.repo, self.f.repo),),
                finish_scope=self.f.session.wire._finish.scope_grant,
                wall=lambda: base.h.now, deadline=base.h.mono+5, monotonic=clock,
                publish_ready=Mock(side_effect=AssertionError('NO_READY_OR_SSH')))
        self.addCleanup(self.selected.build._owner.close)
        self.f.session.wire = self.selected.build._parent._wire
        self.plan = plan

    def test_adopted_wiring_authenticates_five_roles_and_produces_post_close_observation(self):
        rc, result = self.f.f.call(plan=self.f.f.prepared(lambda *args: self.plan))
        self.assertEqual((0, 'SESSION_COMPLETED', 'RELEASED'),
                         (rc, result['status'], result['fence']), result)
        self.assertIn('baseline', self.f.f.blobs); self.assertIn('completion-scope', self.f.f.blobs)
        self.assertEqual(1, len(self.saved)); self.assertEqual(6, len(json.loads(self.saved[0])['observations']))
        self.assertFalse(self.f.base.slot.installed); self.f.f.closed()
        self.assertFalse(self.selected.build._owner._published)
        self.assertIsNone(declaration.SITE)

    def test_control_withdrawal_after_sender_close_keeps_fence_without_false_completion(self):
        self.f.base.read_hook = lambda request, pin, original: (
            replace(original, body=b'SYNTHETIC_WITHDRAWN')
            if pin.path == 'control' and self.f.base.p.c.sender_closes else original)
        rc, result = self.f.f.call(plan=self.f.f.prepared(lambda *args: self.plan))
        self.assertEqual((2, 'RETAINED'), (rc, result['fence']), result)
        self.assertEqual(0, self.f.base.p.c.storage.complete_calls)
        self.assertEqual([], self.saved); self.f.f.closed()
        self.assertFalse(self.selected.build._owner._published)


class ReadyOperatingSelectionTests(unittest.TestCase):
    def test_public_helper_uses_same_explicit_trust_and_native_checkout(self):
        args=dict(workspace='/synthetic/work',context=CONTEXT,port=2222,
            host_key_alias='SYNTHETIC-runner',remote_python='/usr/bin/python3',remote_timeout_seconds=5)
        ready=operating.build_ready_pull_configuration(**args)
        bridge=operating.build_job_bridge_configuration(**args)
        self.assertEqual(bridge,replace(ready,remote_helper=bridge.remote_helper))
        self.assertTrue(ready.remote_helper.endswith('/'+operating.READY_ENTRYPOINT))
        self.assertTrue((Path(__file__).resolve().parents[2]/operating.READY_ENTRYPOINT).is_file())
        for required in ('port','host_key_alias','remote_python','workspace'):
            with self.assertRaises(TypeError):operating.build_ready_pull_configuration(**{k:v for k,v in args.items() if k!=required})

    def test_public_mailbox_connected_to_five_roles_without_io_or_site_assignment(self):
        from delivery_ready_pull import ReadyMailbox
        f=AdoptedOperatingTests('runTest')
        f.setUp();self.addCleanup(f.doCleanups)
        args=f.args | {'publish_ready':None,'ready_context':CONTEXT}
        selected=operating.assemble_observed_job(**args)
        self.addCleanup(selected.build._owner.close)
        mailbox=selected.build._owner._publish
        self.assertIs(type(mailbox),ReadyMailbox)
        self.assertEqual(management.EXPECTED_HELPER_UID, mailbox._uid)
        self.assertEqual(args['config'].binding.key,mailbox._selected['target']['binding_key'])
        self.assertEqual(5,len(selected.build._parent._wire._routes))
        self.assertFalse(mailbox._used);f.f.clock.assert_not_called()
        with self.assertRaises(operating.assembly.SiteAssemblyError):
            operating.assemble_observed_job(**(args | {'publish_ready':Mock()}))
        self.assertIsNone(declaration.SITE)


class ReportedPermissionTests(unittest.TestCase):
    def test_assignment_change_does_not_reassign_a_historical_role_report(self):
        with patch.object(operating,'ISSUANCE_OPERATOR','synthetic-other'):
            report=operating.reported_actor_grant_status()
        self.assertEqual('gwonuk',report['subject'])
        self.assertFalse(report['assignment_matches_report']);self.assertFalse(report['runtime_go'])

    def test_reported_role_expiry_is_not_a_runtime_window_or_live_authority(self):
        state=operating.configuration_status()
        reported=state['selected']['reported_actor_permission']
        self.assertEqual('OWNER_REPORTED',reported['status'])
        self.assertEqual('2026-10-26T12:00:00+09:00',reported['declared_expires_at'])
        self.assertIsNone(reported['grant_starts_at'])
        self.assertFalse(reported['runtime_go']);self.assertFalse(reported['live_rbac_checked'])
        self.assertFalse(reported['automatic_expiry_verified'])
        self.assertFalse(state['execution_authorized']);self.assertIsNone(declaration.SITE)
        # The report only cites existing metadata; it does not make a grant.
        self.assertIn('issuecomment-6379184539',reported['source'])

    def test_mutated_permission_report_cannot_change_fresh_status_or_default_gate(self):
        state=operating.configuration_status()
        reported=state['selected']['reported_actor_permission']
        reported['reported_bindings'].clear();reported['runtime_go']=True
        state['execution_authorized']=True
        fresh=operating.configuration_status()
        self.assertEqual(2,len(fresh['selected']['reported_actor_permission']['reported_bindings']))
        self.assertFalse(fresh['selected']['reported_actor_permission']['runtime_go'])
        output=io.StringIO()
        with patch.object(job,'native_context',side_effect=AssertionError('NO_NATIVE_JOB')) as native, \
             contextlib.redirect_stdout(output):rc=job.main(['deploy','--execute'])
        native.assert_not_called()
        self.assertEqual(2,rc);self.assertEqual('P2_JOB_UNCONFIGURED',json.loads(output.getvalue())['error_code'])
        self.assertIsNone(declaration.SITE)


if __name__ == '__main__': unittest.main()
