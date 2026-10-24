"""Offline SYNTHETIC grants/native responses only; no real credentials/Runtime.

Public FDS profile constants are reused as expected targets, never presented as
live receipts. Sockets, DNS and credential issuance are forbidden throughout.
"""
import contextlib
from dataclasses import replace
import io
import json
from pathlib import Path
import socket
import threading
import unittest
from unittest.mock import Mock, patch

import delivery_actions_source as actions
import delivery_adapter as core
import delivery_fds_existing_inputs as existing
import delivery_fds_operating as operating
import delivery_fds_operating_policy as policy
import delivery_fds_observed_connection as connection
import delivery_fds_site_inputs as profile
import delivery_job as job
import delivery_job_site as declaration
import delivery_job_sources as sources
import delivery_runtime_site as runtime
import delivery_source_session as native_source
import delivery_token_request as issuance
from delivery_github_source import BlobPin, CommentPin, RepositoryPin
from delivery_object_reader import BearerCredential
from delivery_token_materializer import NativePlanSelection
from delivery_token_materializer import NativeTokenMaterializer
from test_delivery_job import CONTEXT
import test_delivery_fds_existing_inputs as fixtures
import test_delivery_token_request as tokens


class OperatingPolicyTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.ExistingProfileTests('runTest'); self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        for name in ('socket', 'create_connection', 'getaddrinfo'):
            p = patch.object(socket, name, side_effect=AssertionError('NO_NETWORK'))
            p.start(); self.addCleanup(p.stop)
        p = patch.object(issuance.TokenRequestIssuer, 'issue',
                         side_effect=AssertionError('NO_ISSUANCE'))
        self.issue = p.start(); self.addCleanup(p.stop)
        self.config, self.plan = self.f.config, self.f.plan
        self.selection = NativePlanSelection(self.plan.approval, self.plan.issuer.grant,
            self.plan.producer, self.plan.baseline, self.plan.subject,
            issuance.TokenTarget(*profile.SUBJECT.split(':')[2:], profile.SUBJECT_UID,
                                 profile.ISSUER, profile.AUDIENCE),
            profile.CA_PEM, '192.0.2.10', 'synthetic:rbac', 'synthetic:route')
        self.finish = replace(self.plan.approval,
            original_pin=replace(self.plan.approval.original_pin, path='finish'),
            adoption_reference='synthetic:post-close-exclusive-readback')
        self.repo = self.plan.approval.original_pin.repository
        for name, value in (('CONTROL_PIN', self.selection.approval.control_pin),
                            ('ACTOR', self.selection.approval.principal)):
            p = patch.object(policy, name, value); p.start(); self.addCleanup(p.stop)
        p = patch.object(connection, 'API_ENDPOINT_IP', '192.0.2.10')
        p.start(); self.addCleanup(p.stop)
        run = actions.RunPin(self.repo, 101, 1, 201, 'f'*40,
                            '.github/workflows/synthetic.yml', 'workflow_dispatch', 'synthetic')
        refs = tuple(getattr(self.plan.producer.facts, x + '_reference')
                     for x in ('test', 'build', 'scan', 'push'))
        self.pins = tuple(actions.StagePin(x, ref, run, 301, 'synthetic-job',
                          ((i+1, 'synthetic-'+x),)) for i, (x, ref)
                          in enumerate(zip(actions.STAGES, refs)))
        self.mono = Mock(return_value=1000); self.wall = Mock(return_value=1001)
        a = self.f.args()
        self.args = {k: a[k] for k in ('config', 'finish_slot', 'expected_peer', 'routes', 'deadline')}
        self.args.update(context=CONTEXT, selection=self.selection,
            role_routes=tuple(sources.RoleRoute(x, self.repo, self.repo)
                              for x in sources.ROLES + ('baseline',)),
            finish_scope=self.finish, wall=self.wall, monotonic=self.mono,
            producer_repository=self.repo, producer_pins=self.pins)

    def checked(self, **changes):
        a = dict(config=self.config, context=CONTEXT, selection=self.selection,
                 finish_scope=self.finish, producer_repository=self.repo,
                 producer_pins=self.pins)
        return policy.selection_checked(**(a | changes))

    def build(self, **changes):
        value = operating.assemble_observed_native_job(**(self.args | changes))
        self.addCleanup(value.build._parent._plan.close)
        return value

    def test_inert_concrete_job_selects_existing_uid_ready_and_deferred_native_path(self):
        with patch.object(Path, 'open', side_effect=AssertionError('NO_FILE')), \
             patch.object(native_source, 'native_original_reader') as read:
            value = self.build()
        read.assert_not_called(); self.issue.assert_not_called()
        self.mono.assert_not_called(); self.wall.assert_not_called()
        self.assertIs(type(value), job.JobSite)
        self.assertIs(type(value.build._parent._plan), runtime.PreparedAuthorityPlan)
        self.assertEqual(1000, value.build._owner._uid)
        self.assertIsNone(value.build._owner._listener)
        self.assertIsNone(declaration.SITE)

    def test_policy_report_is_fresh_and_does_not_make_default_job_executable(self):
        first = policy.selection_status(); first['control'].clear(); first['scope']['normal_runs'] = 99
        again = policy.selection_status()
        self.assertEqual('ALWAYS_OPEN', again['scheduling'])
        self.assertEqual(240, again['budget_minutes_reference_only'])
        self.assertEqual(1, again['scope']['normal_runs'])
        self.assertTrue(again['control']); self.assertFalse(again['execution_authorized'])
        with patch.object(job, 'native_context', side_effect=AssertionError('NO_JOB')) as native, \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(2, job.main(['deploy', '--execute']))
        native.assert_not_called(); self.assertIsNone(declaration.SITE)

    def test_always_open_is_not_a_fixed_clock_or_a_240_minute_cap(self):
        selected = replace(self.selection, approval=replace(self.selection.approval,
                            starts_at=1000, expires_at=20000))
        self.assertIs(selected, self.checked(selection=selected))
        for invalid in (None, float('inf'), policy.CLEANUP_END + 1, True):
            with self.subTest(expiry=invalid), self.assertRaises(policy.OperatingPolicyError):
                self.checked(selection=replace(self.selection, approval=replace(
                    self.selection.approval, expires_at=invalid)))
        with self.assertRaises(policy.OperatingPolicyError):
            self.checked(finish_scope=replace(self.finish, expires_at=policy.CLEANUP_END + 1))

    def test_each_role_control_and_exact_finish_binding_are_required_before_any_io(self):
        wrong = replace(policy.CONTROL_PIN, anchor_commit='e'*40)
        changes = [replace(self.selection, approval=replace(self.selection.approval, control_pin=wrong)),
            replace(self.selection, issuer_grant=replace(self.selection.issuer_grant, control_pin=wrong)),
            replace(self.selection, producer=replace(self.selection.producer,
                    grant=replace(self.selection.producer.grant, control_pin=wrong))),
            replace(self.selection, baseline=replace(self.selection.baseline,
                    grant=replace(self.selection.baseline.grant, control_pin=wrong)))]
        for selected in changes:
            with self.subTest(role=changes.index(selected)), self.assertRaises(policy.OperatingPolicyError):
                self.checked(selection=selected)
        for finish in (None, replace(self.finish, control_pin=wrong),
                       replace(self.finish, binding=replace(self.config.binding, run_id='OTHER'))):
            with self.assertRaises(policy.OperatingPolicyError): self.checked(finish_scope=finish)
        self.mono.assert_not_called(); self.wall.assert_not_called(); self.issue.assert_not_called()

    def test_preparation_only_comment_never_becomes_execution_approval_even_if_edited(self):
        for owner, name in (('321Team', 'fds-project-baseline'), ('321team', 'FDS-PROJECT-BASELINE')):
            repo = RepositoryPin(owner, name, 1, 2)
            for updated, sha in (('2026-10-25T00:00:00Z', 'a'*64), ('2026-10-25T01:00:00Z', 'b'*64)):
                pin = CommentPin(repo, 46, 6385867126, 277643063, updated, sha)
                for role in ('approval', 'baseline', 'finish'):
                    kw = {}
                    if role == 'approval':
                        kw['selection'] = replace(self.selection,
                            approval=replace(self.selection.approval, original_pin=pin))
                    elif role == 'baseline':
                        kw['selection'] = replace(self.selection, baseline=replace(
                            self.selection.baseline, grant=replace(self.selection.baseline.grant,
                                                                  original_pin=pin)))
                    else:
                        kw['finish_scope'] = replace(self.finish, original_pin=pin)
                    with self.subTest(role=role, owner=owner), self.assertRaises(policy.OperatingPolicyError):
                        self.checked(**kw)

    def test_missing_baseline_wrong_profile_actor_target_and_producer_do_not_fallback(self):
        changes = [replace(self.selection, baseline=None),
            replace(self.selection, baseline=replace(self.selection.baseline, objects=(None,)*6)),
            replace(self.selection, approval=replace(self.selection.approval, principal='other')),
            replace(self.selection, endpoint_ip='192.0.2.12'),
            replace(self.selection, target=replace(self.selection.target, uid='other')),
            replace(self.selection, producer=replace(self.selection.producer,
                facts=replace(self.selection.producer.facts, previous_images=None)))]
        for selected in changes:
            with self.subTest(selected=changes.index(selected)), self.assertRaises(policy.OperatingPolicyError):
                self.checked(selection=selected)
        for pins in (None, (), self.pins[:-1], (replace(self.pins[0], producer_reference='other'),)+self.pins[1:]):
            with self.assertRaises(policy.OperatingPolicyError): self.checked(producer_pins=pins)
        with self.assertRaises(policy.OperatingPolicyError):
            self.checked(context=replace(CONTEXT, attempt=2))
        # Existing restore guard remains: no implicit restoration render/adoption.
        with self.assertRaises(policy.OperatingPolicyError):
            self.checked(config=replace(self.config, binding=replace(self.config.binding, action='restore')))

    def native_input(self):
        t = tokens.TokenRequestTests('runTest'); t.setUp(); self.addCleanup(t.doCleanups)
        t.target = self.selection.target
        t.claims.update(iss=t.target.issuer, aud=[t.target.audience], sub=profile.SUBJECT)
        t.claims['kubernetes.io'] = {'namespace': t.target.namespace,
            'serviceaccount': {'name': t.target.name, 'uid': t.target.uid}}
        credential = BearerCredential(t.token())
        s = object.__new__(native_source.PrivateSourceSession)
        s._state, s._last = 'CONSUMED', None
        s.clock, s.deadline = self.mono, self.args['deadline']
        s._native_lock = threading.RLock()
        s._native = native_source.NativeIssuanceOriginal(CONTEXT, self.config.binding,
            credential.fingerprint, core.encoded(t.response()))
        s._handles, s.channels, s._readers, s.wire, s.cleanup_ok = [], (), {}, None, True
        self.addCleanup(s.close)
        return credential, s

    def test_real_prepared_connector_reads_native_once_and_preserves_all_grants_and_baseline(self):
        value = self.build(); prepared = value.build._parent._plan
        prepared.bind_context(CONTEXT); credential, source = self.native_input()
        with patch.object(source, 'take_native_original', wraps=source.take_native_original) as read:
            plan = prepared.resolve(credential=credential, source=source, roles={}, deadline=source.deadline)
        read.assert_called_once()
        self.assertIs(plan.approval, self.selection.approval)
        self.assertIs(plan.producer, self.selection.producer)
        self.assertIs(plan.baseline, self.selection.baseline)
        self.assertEqual((1000, 1900, 900), (plan.issuer.facts.issued_at,
                         plan.issuer.facts.expires_at, plan.issuer.facts.requested_seconds))
        self.assertEqual(credential.fingerprint, plan.issuer.facts.credential_fingerprint)
        self.assertIsNone(source._native); self.assertEqual('CONSUMED', source._state)
        self.assertIs(plan, existing.validate_existing_plan(self.config, plan))
        with self.assertRaises(runtime.RuntimeSiteError):
            prepared.resolve(credential=credential, source=source, roles={}, deadline=source.deadline)
        self.issue.assert_not_called()

    def test_wrong_native_context_or_expired_source_consumes_preparation_before_raw_read(self):
        for context, expire in ((replace(CONTEXT, run_id='999'), False), (CONTEXT, True)):
            value = self.build(); prepared = value.build._parent._plan
            prepared.bind_context(context); credential, source = self.native_input()
            if expire: source.deadline = 999
            with patch.object(source, 'take_native_original', wraps=source.take_native_original) as read:
                with self.assertRaises(runtime.RuntimeSiteError):
                    prepared.resolve(credential=credential, source=source, roles={}, deadline=source.deadline)
            read.assert_not_called(); self.assertIsNotNone(source._native)
            with self.assertRaises(runtime.RuntimeSiteError):
                prepared.resolve(credential=credential, source=source, roles={}, deadline=source.deadline)

    def test_failed_native_parse_closes_owned_materializer_but_not_borrowed_source(self):
        value = self.build(); prepared = value.build._parent._plan
        prepared.bind_context(CONTEXT); credential, source = self.native_input()
        source._native = replace(source._native, raw=b'SYNTHETIC_PRIVATE_NOT_A_TOKEN_RESPONSE')
        close = NativeTokenMaterializer.close
        with patch.object(NativeTokenMaterializer, 'close', autospec=True,
                          side_effect=close) as closed:
            with self.assertRaises(runtime.RuntimeSiteError) as caught:
                prepared.resolve(credential=credential, source=source, roles={}, deadline=source.deadline)
        closed.assert_called_once()
        self.assertEqual('CONSUMED', source._state)
        self.assertIsNone(source._native)
        self.assertIsNone(caught.exception.__context__)
        self.assertNotIn('SYNTHETIC_PRIVATE', repr(caught.exception))
        self.issue.assert_not_called()


if __name__ == '__main__': unittest.main()
