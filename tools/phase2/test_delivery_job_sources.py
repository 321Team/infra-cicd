"""SYNTHETIC actual-session/source/finish wiring; no live values or network."""
import copy
from dataclasses import replace
import json
import pickle
import unittest
from unittest.mock import Mock, patch

import delivery_adapter as core
import delivery_job_sources as wiring
import delivery_finish_collector as collector
import delivery_finish_observation as observation
import delivery_runtime_site as runtime
from delivery_github_source import RepositoryPin
from delivery_private_source import PrivateGitHubOriginalAdapter
from delivery_object_reader import ObjectReader, Observation
from test_delivery_object_reader import absent
import test_delivery_source_session as fixture


class JobSourceWiringTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture.SourceSessionTests('runTest'); self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.addCleanup(lambda: [c.close() for c in self.f.made])
        self.base, self.session, self.repo = self.f.f, self.f.session, self.f.repo
        self.binding = self.base.h.binding
        self.other = RepositoryPin('SYNTHETIC', 'other-source', 4567, 9876)
        self.decode = Mock(side_effect=AssertionError('NO_NATIVE_DECODE_BEFORE_TERMINAL'))
        def blob(reader, request, pin):
            self.assertFalse(reader._transport._closed)
            self.f.transports.append(reader._transport)
            self.f.blobs.append(pin.path)
            return self.base.reader.blob(request, pin)
        p = patch.object(PrivateGitHubOriginalAdapter, 'blob', new=blob)
        p.start(); self.addCleanup(p.stop)

    def routes(self, **changes):
        return tuple(wiring.RoleRoute(role, *changes.get(role, (self.repo, self.repo)))
                     for role in wiring.ROLES)

    def router(self, **changes):
        args = dict(binding=self.binding, routes=self.routes(),
                    wall=lambda: self.base.h.now, monotonic=lambda: self.base.h.mono)
        args.update(changes)
        router = wiring.SourceRoleWiring(**args)
        self.session.wire = router
        return router

    def receive(self):
        deadline = self.session.prepare(self.f.rosa, self.base.h.mono + 5)
        return self.session.open(self.f.rosa.receive(deadline))

    def collection(self, **changes):
        grant = self.base.grants['finish']
        policy = collector.FinishCollectionPolicy(self.binding,
            collector.BlobLocation(self.repo, 'actual-finish', 'heads/main'),
            grant.control_pin, grant.principal, grant.adoption_reference,
            grant.starts_at, grant.expires_at)
        spec = wiring.CollectionRoute(policy, self.decode, None, 1.0)
        return replace(spec, **changes)

    def test_constructor_is_inert_private_and_noncopyable(self):
        with patch.object(self.session, 'reader', side_effect=AssertionError('READ_FORBIDDEN')):
            router = self.router(finish=self.collection())
        self.decode.assert_not_called()
        self.assertFalse(router._used)
        self.assertNotIn(self.repo.name, repr(router))
        for copy_op in (copy.copy, copy.deepcopy, pickle.dumps):
            with self.assertRaises(Exception): copy_op(router)

    def test_role_order_missing_duplicate_and_unknown_mode_are_rejected_without_receive(self):
        routes = self.routes()
        for bad in (routes[:-1], routes[1:] + routes[:1], routes + (routes[-1],)):
            with self.assertRaises(wiring.JobSourcesError): self.router(routes=bad)
        with self.assertRaises(wiring.JobSourcesError): self.router(finish={})
        for receiver in self.f.receivers: receiver.receive.assert_not_called()

    def test_one_consumed_session_routes_same_scoped_reader_without_source_io(self):
        router = self.router(producer_repository=self.repo)
        roles = self.receive()
        self.assertEqual({'readers', 'producer_run_reader'}, set(roles))
        self.assertEqual(4, len(roles['readers']))
        for pair in roles['readers']:
            self.assertIs(pair.original, self.session.reader(self.repo))
            self.assertIs(pair.control, self.session.reader(self.repo))
        self.assertIs(roles['producer_run_reader'], self.session.reader(self.repo))
        self.assertEqual([], self.f.blobs)
        with self.assertRaisesRegex(wiring.JobSourcesError, 'CONSUMED'): router(self.session)
        self.session.close(); self.f.rosa.close(); self.f.closed()

    def test_routes_cannot_be_used_before_credentials_and_failure_consumes_wiring(self):
        router = self.router()
        with self.assertRaises(wiring.JobSourcesError): router(self.session)
        self.assertFalse(any(r.receive.called for r in self.f.receivers))
        with self.assertRaisesRegex(wiring.JobSourcesError, 'CONSUMED'): router(self.session)

    def test_unreceived_repository_denies_before_claim_and_closes_actual_channels(self):
        self.router(routes=self.routes(platform=(self.other, self.repo)))
        rc, result = self.f.call()
        self.assertEqual((2, 'NOT_RUN'), (rc, result['runtime']))
        self.assertEqual([], self.base.p.c.storage.claims)
        self.assertEqual([], self.base.h.requests)
        self.f.closed()

    def test_same_token_on_distinct_explicit_repositories_remains_valid(self):
        # One read credential may already cover both repos; no new token policy.
        self.session.channels += (self.f.channel(self.other),)
        self.router(routes=self.routes(platform=(self.other, self.repo)))
        roles = self.receive()
        self.assertIs(roles['readers'][1].original, self.session.reader(self.other))
        self.assertIs(roles['readers'][1].control, self.session.reader(self.repo))
        self.session.close(); self.f.rosa.close(); self.f.closed()

    def test_materialized_cross_repository_plan_is_rejected_before_factory_or_claim(self):
        self.router()
        plan = self.base.plan()
        changed = replace(plan.issuer.grant, original_pin=replace(
            plan.issuer.grant.original_pin, repository=self.other))
        bad = replace(plan, issuer=replace(plan.issuer, grant=changed))
        rc, result = self.f.call(plan=self.f.prepared(lambda *unused: bad))
        self.assertEqual(2, rc, result)
        self.assertEqual([], self.base.h.requests)
        self.assertEqual([], self.base.p.c.storage.claims)
        self.assertEqual([], self.f.blobs)
        self.f.closed()

    def test_plan_control_repository_and_baseline_shape_are_matched(self):
        router = self.router()
        plan = self.base.plan(); router.check_plan(plan)
        changed = replace(plan.approval, control_pin=replace(plan.approval.control_pin,
                                                           repository=self.other))
        with self.assertRaises(wiring.JobSourcesError):
            router.check_plan(replace(plan, approval=changed))
        extra = wiring.RoleRoute('baseline', self.repo, self.repo)
        existing = self.router(routes=self.routes() + (extra,))
        with self.assertRaises(wiring.JobSourcesError): existing.check_plan(plan)
        baseline = runtime.BaselineAdoption(plan.approval, (None,) * 6)
        existing.check_plan(replace(plan, baseline=baseline))
        with self.assertRaises(wiring.JobSourcesError):
            router.check_plan(replace(plan, baseline=baseline))

    def test_collection_builds_existing_native_locator_but_never_reads_or_decodes(self):
        router = self.router(finish=self.collection())
        roles = self.receive(); cfg = roles['finish_collection']
        self.assertEqual({'readers', 'finish_collection'}, set(roles))
        self.assertIs(type(cfg), collector.FinishCollectorConfig)
        self.assertIs(cfg.readers, roles['readers'][3])
        self.assertIs(cfg.locate.reader, self.session.reader(self.repo))
        self.assertFalse(cfg.locate.used); self.decode.assert_not_called()
        self.assertEqual([], self.f.blobs)
        self.assertIs(cfg.locate.wall, router._wall)
        self.assertIs(cfg.locate.monotonic, router._clock)
        reader = cfg.locate.reader
        cfg.locate.close()  # Locator detaches borrowed refs; session still owns TLS.
        self.assertFalse(reader._transport._closed)
        self.session.close(); self.f.rosa.close(); self.f.closed()
        self.assertTrue(reader._transport._closed)

    def test_finish_wrong_binding_repository_mode_and_decoder_are_rejected(self):
        spec = self.collection()
        bad_policy = replace(spec.policy, binding=replace(self.binding, run_id='SYNTHETIC_OTHER_RUN'))
        bad_repo = replace(spec.policy, location=replace(spec.policy.location, repository=self.other))
        for bad in (replace(spec, policy=bad_policy), replace(spec, policy=bad_repo),
                    replace(spec, decode=None), replace(spec, comment_id=1),
                    replace(spec, max_seconds=121)):
            with self.assertRaises(wiring.JobSourcesError): self.router(finish=bad)
        scope = self.base.grants['approval']
        with self.assertRaises(wiring.JobSourcesError):
            self.router(finish=wiring.ObservationRoute(scope, ('OBJECT_READBACK',)))

    def test_status_does_not_receive_invoke_wiring_or_invent_finish_authority(self):
        router = self.router(finish=self.collection())
        rc, result = self.f.call(('status',))
        self.assertEqual((0, 'NOT_RUN'), (rc, result['runtime']))
        self.assertFalse(router._used); self.decode.assert_not_called()
        self.assertFalse(self.base.slot.installed)
        for receiver in self.f.receivers:
            receiver.receive.assert_not_called()
            self.assertIsNone(receiver._fd)
        # Mock-return credentials were never transferred; the test owns them.
        self.assertTrue(all(not c._closed for c in self.f.made))

    def test_actual_factory_uses_wiring_and_original_control_authentication(self):
        self.router()
        rc, result = self.f.call(plan=self.f.prepared())
        self.assertEqual((0, 'SESSION_COMPLETED'), (rc, result['status']), result)
        self.assertIn('control', self.f.blobs); self.assertIn('platform', self.f.blobs)
        self.assertEqual('RELEASED', result['fence']); self.f.closed()

    def test_wiring_time_consumes_same_handoff_deadline_without_claim_or_retry(self):
        router = self.router()
        reader = self.session.reader
        def elapsed(repository):
            self.base.h.mono += 1
            return reader(repository)
        with patch.object(self.session, 'reader', side_effect=elapsed):
            rc, result = self.f.call()
        self.assertEqual((2, 'NOT_RUN'), (rc, result['runtime']), result)
        self.assertTrue(router._used); self.assertEqual([], self.base.p.c.storage.claims)
        self.assertEqual([], self.base.h.requests); self.f.closed()

    def observation_setup(self):
        raw = b'SYNTHETIC_EXPLICIT_EXISTING_COMPLETION_SCOPE'
        self.base.raws['completion-scope'] = raw
        grant = replace(self.base.grants['approval'], original_pin=replace(
            self.base.pins['approval'], path='completion-scope', sha256=core.digest(raw)),
            adoption_reference='synthetic:reviewed-existing-completion-scope')
        self.router(finish=wiring.ObservationRoute(grant, observation.FOLLOWUPS))
        original_app = self.base.app
        p = patch.object(self.base, 'app', side_effect=lambda **kw: original_app(install=False, **kw))
        p.start(); self.addCleanup(p.stop)
        def read(reader, request, key):
            reader.check_request(request)
            obj = copy.deepcopy(self.base.h.objects.get(core.path_for(key, self.binding)))
            if obj is not None and obj['kind'] == 'Deployment':
                obj['status'] = {'observedGeneration': 1, 'replicas': 3, 'updatedReplicas': 3,
                                 'readyReplicas': 3, 'availableReplicas': 3}
            return Observation(key, 404 if obj is None else 200,
                core.encoded(absent(key) if obj is None else obj))
        p = patch.object(ObjectReader, 'read', new=read); p.start(); self.addCleanup(p.stop)
        saved = []
        p = patch.object(observation, 'persist', side_effect=lambda root, binding, raw: saved.append(raw))
        p.start(); self.addCleanup(p.stop)
        return saved

    def test_received_source_wiring_drives_actual_post_close_observation_before_release(self):
        saved = self.observation_setup()
        rc, result = self.f.call(plan=self.f.prepared())
        self.assertEqual((0, 'SESSION_COMPLETED'), (rc, result['status']), result)
        self.assertEqual('RELEASED', result['fence']); self.assertFalse(self.base.slot.installed)
        self.assertEqual(1, len(saved))
        doc = json.loads(saved[0]); self.assertEqual(observation.SCHEMA, doc['schema'])
        self.assertEqual(6, len(doc['observations'])); self.assertFalse(doc['execution_authorized'])
        self.assertIn('completion-scope', self.f.blobs); self.f.closed()

    def test_post_close_control_withdrawal_retains_claim_and_never_fabricates_completion(self):
        saved = self.observation_setup()
        self.base.read_hook = lambda request, pin, original: (
            replace(original, body=b'SYNTHETIC_WITHDRAWN')
            if pin.path == 'control' and self.base.p.c.sender_closes else original)
        rc, result = self.f.call(plan=self.f.prepared())
        self.assertEqual(2, rc, result); self.assertEqual('RETAINED', result['fence'])
        self.assertEqual(0, self.base.p.c.storage.complete_calls)
        self.assertEqual([], saved); self.assertFalse(self.base.slot.installed)
        self.f.closed()

    def test_observation_cannot_override_another_finish_capture_mode(self):
        saved = self.observation_setup()
        forbidden = Mock(side_effect=AssertionError('SYNTHETIC_SECRET_DO_NOT_ECHO'))
        rc, result = self.f.call(plan=self.f.prepared(), finish_capture=forbidden)
        self.assertEqual((2, 'NOT_RUN'), (rc, result['runtime']), result)
        self.assertEqual([], saved); forbidden.assert_not_called()
        self.assertEqual([], self.base.p.c.storage.claims); self.f.closed()


if __name__ == '__main__':
    unittest.main()
