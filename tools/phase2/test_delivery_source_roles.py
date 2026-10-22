"""SYNTHETIC source/plan wiring only; no actual adoption or outbound network."""
import contextlib
from dataclasses import replace
import io
import json
import unittest
from unittest.mock import Mock, patch

import delivery_job as job
import delivery_job_site as declaration
import delivery_runtime_site as runtime
import delivery_site as site
import delivery_source_roles as wiring
from delivery_actions_source import STAGES, RunPin, StagePin
import test_delivery_source_session as fixture
from test_delivery_job import CONTEXT, RUNNER


class SourceRoleTests(unittest.TestCase):
    def setUp(self):
        self.s = fixture.SourceSessionTests('runTest'); self.s.setUp()
        self.addCleanup(self.s.doCleanups)
        self.f, self.session, self.repo = self.s.f, self.s.session, self.s.repo
        self.plan = self.f.plan()
        self.finish = self.repo, self.repo

    def call(self, **changes):
        args = dict(plan=self.plan, finish_repositories=self.finish)
        return wiring.plan_source_roles(self.session, **(args | changes))

    def open(self):
        # The wire is tested directly after the same state/credentials/TLS setup.
        # Existing open requires readers; use the real fixture wire for setup.
        self.session.wire = self.s.wire
        self.session.prepare(self.s.rosa, self.f.h.mono+5)
        self.session.open(self.s.rosa.receive(self.f.h.mono+5))

    def stages(self, repo=None):
        repo = repo or self.repo
        run = RunPin(repo, 123, 1, 321, 'b'*40, '.github/workflows/synthetic.yml', 'push', 'main')
        f = self.plan.producer.facts
        refs = f.test_reference, f.build_reference, f.scan_reference, f.push_reference
        return tuple(StagePin(stage, ref, run, 456, 'SYNTHETIC_JOB', ((i+1, stage),))
                     for i, (stage, ref) in enumerate(zip(STAGES, refs)))

    def scoped_plan(self):
        """Eight distinct explicit scopes make role/order errors observable."""
        repos = [replace(self.repo, name='synthetic-repo-'+str(i), repository_id=200+i)
                 for i in range(8)]
        def grant(g, i):
            return replace(g, original_pin=replace(g.original_pin, repository=repos[i]),
                control_pin=replace(g.control_pin, repository=repos[i+1]))
        plan = replace(self.plan, approval=grant(self.plan.approval, 0),
            issuer=replace(self.plan.issuer, grant=grant(self.plan.issuer.grant, 2)),
            producer=replace(self.plan.producer, grant=grant(self.plan.producer.grant, 4)))
        self.session.close()
        self.session = fixture.source.PrivateSourceSession(
            tuple(self.s.channel(repo) for repo in repos), wire=lambda _: {},
            monotonic=lambda: self.f.h.mono)
        self.addCleanup(self.session.close)
        # No source GET and no factory here; construct reader objects only.
        self.session.wire = lambda pool: {'readers': (pool.reader(repos[0]),)}
        self.session.prepare(self.s.rosa, self.f.h.mono+5)
        self.session.open(self.s.rosa.receive(self.f.h.mono+5))
        return plan, repos

    def test_explicit_original_control_scopes_and_exact_runtime_order(self):
        plan, repos = self.scoped_plan()
        with patch.object(self.session, 'reader', wraps=self.session.reader) as lookup:
            roles = self.call(plan=plan, finish_repositories=tuple(repos[6:]))
        self.assertEqual(repos, [c.args[0] for c in lookup.call_args_list])
        self.assertEqual({'readers'}, set(roles))
        self.assertEqual(4, len(roles['readers']))
        for i, pair in enumerate(roles['readers']):
            self.assertIs(self.session.reader(repos[2*i]), pair.original)
            self.assertIs(self.session.reader(repos[2*i+1]), pair.control)
        self.assertEqual([], self.f.read_calls)
        self.assertFalse(self.f.slot.installed)  # A finish repository is not an original.

    def test_shared_repo_reuses_reader_without_new_token_or_ownership_transfer(self):
        self.open(); reader = self.session.reader(self.repo)
        roles = self.call()
        self.assertTrue(all(p.original is reader and p.control is reader for p in roles['readers']))
        self.assertFalse(reader._transport._closed)
        self.assertFalse(self.session._handles[0]._closed)

    def test_baseline_reader_is_fifth_and_no_original_is_invented(self):
        self.open()
        record = runtime.BaselineAdoption(self.plan.approval, ())
        roles = self.call(plan=replace(self.plan, baseline=record))
        self.assertEqual(5, len(roles['readers']))
        self.assertIs(self.session.reader(self.repo), roles['readers'][4].original)
        self.assertEqual([], self.f.read_calls)

    def test_unopened_or_closed_session_has_no_fallback(self):
        for close in (False, True):
            if close: self.session.close()
            with self.assertRaisesRegex(wiring.SourceRolesError, 'SOURCE_ROLES_UNAVAILABLE'):
                self.call()

    def test_missing_selected_repo_never_substitutes_an_available_reader(self):
        self.open()
        other = replace(self.repo, name='SYNTHETIC_MISSING', repository_id=999)
        with self.assertRaisesRegex(wiring.SourceRolesError, 'SOURCE_ROLES_UNAVAILABLE'):
            self.call(finish_repositories=(other, self.repo))
        self.assertEqual([], self.f.read_calls)

    def test_invalid_plan_scopes_or_binding_denied_before_reader_lookup(self):
        bad_grant = replace(self.plan.producer.grant,
            binding=replace(self.plan.approval.binding, run_id='SYNTHETIC_OTHER_RUN'))
        cases = [dict(plan=None), dict(plan=replace(self.plan, approval=Mock())),
            dict(plan=replace(self.plan, issuer=None)), dict(plan=replace(self.plan, baseline=Mock())),
            dict(plan=replace(self.plan, producer=replace(self.plan.producer, grant=bad_grant))),
            dict(plan=replace(self.plan, approval=replace(self.plan.approval,
                control_pin=None))),
            dict(finish_repositories=[]), dict(finish_repositories=(self.repo,)),
            dict(finish_repositories=(self.repo, Mock()))]
        with patch.object(self.session, 'reader') as lookup:
            for changes in cases:
                with self.subTest(keys=list(changes)), self.assertRaises(wiring.SourceRolesError):
                    self.call(**changes)
            lookup.assert_not_called()

    def test_restore_without_adopted_baseline_is_not_first_deploy(self):
        restore = replace(self.plan.approval.binding, action='restore')
        plan = replace(self.plan, approval=replace(self.plan.approval, binding=restore))
        with patch.object(self.session, 'reader') as lookup, self.assertRaises(wiring.SourceRolesError):
            self.call(plan=plan)
        lookup.assert_not_called()

    def test_invalid_session_never_calls_arbitrary_reader_or_close(self):
        fake = Mock()
        with self.assertRaises(wiring.SourceRolesError):
            wiring.plan_source_roles(fake, plan=self.plan, finish_repositories=self.finish)
        fake.reader.assert_not_called(); fake.close.assert_not_called()

    def test_optional_actions_reader_uses_existing_exact_pins_not_workflow_app_sha_guess(self):
        self.open()
        roles = self.call(producer_pins=self.stages())
        self.assertEqual({'readers', 'producer_run_reader'}, set(roles))
        self.assertIs(self.session.reader(self.repo), roles['producer_run_reader'])
        self.assertNotEqual(self.plan.approval.binding.source_commit,
                            self.stages()[0].run.workflow_commit)

    def test_actions_policy_or_reference_mismatch_precedes_lookup(self):
        stages = self.stages()
        for pins in (stages[:3], (replace(stages[0], producer_reference='SYNTHETIC_OTHER'),)+stages[1:]):
            with patch.object(self.session, 'reader') as lookup, self.assertRaises(wiring.SourceRolesError):
                self.call(producer_pins=pins)
            lookup.assert_not_called()

    def test_wrong_reader_exact_type_is_rejected_but_borrowed_handle_not_closed(self):
        self.open(); fake = Mock()
        with patch.object(self.session, 'reader', return_value=fake), \
                self.assertRaisesRegex(wiring.SourceRolesError, 'SOURCE_ROLES_READER_INVALID'):
            self.call()
        fake.close.assert_not_called()

    def test_callback_secret_error_is_literal_redacted_and_interrupt_is_preserved(self):
        for error in (ValueError(fixture.TOKEN), wiring.SourceRolesError(fixture.TOKEN),
                      wiring.SourceRolesError([])):
            with patch.object(self.session, 'reader', side_effect=error), \
                    self.assertRaisesRegex(wiring.SourceRolesError, '^SOURCE_ROLES_UNAVAILABLE$'):
                self.call()
        with patch.object(self.session, 'reader', side_effect=KeyboardInterrupt()), \
                self.assertRaises(KeyboardInterrupt):
            self.call()

    def test_real_source_driver_factory_finish_connection_uses_plan_wiring(self):
        def wire(pool):
            self.s.wire(pool)  # SYNTHETIC native API boundary, not role selection.
            return wiring.plan_source_roles(pool, plan=self.plan, finish_repositories=self.finish)
        self.session.wire = wire
        invocation = job.RuntimeInvocation(CONTEXT, self.f.config(), self.plan,
            self.f.slot, self.s.rosa, self.f.h.mono+5, source_session=self.session)
        original = runtime.RuntimeAuthorityFactory
        def factory(*a, **kw):
            return original(*a, **kw, wall=lambda: self.f.h.now, monotonic=lambda: self.f.h.mono)
        def build(config, *, credential, authority_factory):
            self.assertIs(self.f.credential, credential)
            return self.f.app(factory=authority_factory)
        out = io.StringIO()
        with patch.object(runtime, 'RuntimeAuthorityFactory', side_effect=factory), \
                patch.object(site, 'build_application', side_effect=build), contextlib.redirect_stdout(out):
            rc = job.main(['deploy', '--execute'], declaration=job.JobSite(RUNNER, lambda *a: invocation),
                context=CONTEXT, revision=lambda: CONTEXT.workflow_sha, identity=lambda: RUNNER)
        result = json.loads(out.getvalue())
        self.assertEqual((0, 'SESSION_COMPLETED', 'RELEASED'),
            (rc, result['status'], result['result']['fence']), result)
        self.assertEqual(1, self.f.p.c.storage.complete_calls)
        self.assertFalse(any('SOURCE_ROLES' in str(x) for x in self.f.read_calls))
        self.s.closed(); self.assertNotIn(fixture.TOKEN, out.getvalue())

    def test_wrong_scope_in_real_source_run_closes_all_before_claim(self):
        other = replace(self.repo, name='SYNTHETIC_MISSING', repository_id=999)
        self.session.wire = lambda pool: wiring.plan_source_roles(pool, plan=self.plan,
            finish_repositories=(other, self.repo))
        rc, result = self.s.call()
        self.assertEqual((2, 'SOURCE_SESSION_PREPARATION_FAILED'), (rc, result['error_code']))
        self.assertEqual([], self.f.p.c.storage.claims)
        self.assertEqual([], self.f.h.requests); self.s.closed()

    def test_default_site_stays_unconfigured_and_status_never_calls_wiring(self):
        self.assertIsNone(declaration.SITE)
        builder = Mock(side_effect=AssertionError('BUILDER_FORBIDDEN'))
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(0, job.main(['status'], declaration=job.JobSite(RUNNER, builder)))
        builder.assert_not_called()


if __name__ == '__main__': unittest.main()
