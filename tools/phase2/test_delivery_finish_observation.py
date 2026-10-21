"""SYNTHETIC event/API/scope originals. Network/DNS forbidden by shared fixture.

Real session/provider/reconciler/decoder/authority run. POSIX cases additionally
exercise actual private local files, never a server, token issuer or deployment.
"""
import base64
import copy
from dataclasses import replace
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import delivery_adapter as core
import delivery_finish_observation as obs
import delivery_provider as provider
import delivery_runtime_site as runtime
from delivery_object_reader import BearerCredential, ObjectReader, Observation
from delivery_session import SenderClosed
from delivery_verifier import Progress, VerificationError
import test_delivery_runtime_site as prior
from test_delivery_object_reader import absent
from test_delivery_adapter import Harness
from test_delivery_objects import owned
from test_delivery_nine_objects import nine


class ObservationTests(unittest.TestCase):
    def setUp(self):
        self.base = prior.RuntimeSiteTests('runTest'); self.base.setUp()
        self.addCleanup(self.base.doCleanups)
        self.h, self.credential = self.base.h, self.base.credential
        self.saved, self.reads, self.close_events = [], [], []
        self.base.raws['completion-scope'] = b'SYNTHETIC_EXISTING_READBACK_COMPLETION_SCOPE'
        self.scope_grant = replace(self.base.grants['approval'], original_pin=replace(
            self.base.pins['approval'], path='completion-scope',
            sha256=core.digest(self.base.raws['completion-scope'])),
            adoption_reference='synthetic:reviewed-readback-completion-meaning')
        self.read_hook = lambda r, o: o
        def read(reader, request, key):
            reader.check_request(request)
            self.reads.append((request, key, self.base.p.c.sender_closes))
            obj = copy.deepcopy(self.h.objects.get(core.path_for(key, self.h.binding)))
            if obj is not None and obj['kind'] == 'Deployment':
                obj['status'] = {'observedGeneration': 1, 'replicas': 3, 'updatedReplicas': 3,
                    'readyReplicas': 3 if self.h.ready else 0, 'availableReplicas': 3}
            return self.read_hook(request, Observation(key, 404 if obj is None else 200,
                core.encoded(absent(key) if obj is None else obj)))
        p = patch.object(ObjectReader, 'read', new=read); p.start(); self.addCleanup(p.stop)
        def save(root, binding, raw):
            self.saved.append((binding, raw))
        p = patch.object(obs, 'persist', side_effect=save); self.persist_mock = p.start(); self.addCleanup(p.stop)
        original = provider.OperationalProviders.sender_closed
        def closed(p, event):
            self.close_events.append(event); return original(p, event)
        p = patch.object(provider.OperationalProviders, 'sender_closed', new=closed)
        p.start(); self.addCleanup(p.stop)

    def config(self, **changes):
        # A reviewed existing completion scope is explicit, not an automatic
        # copy from deployment approval. Actual adoption is site-reviewed code.
        cfg = obs.FinishObservationConfig(self.scope_grant,
            runtime.SourceReaders(self.base.reader, self.base.reader), obs.FOLLOWUPS)
        return replace(cfg, **changes)

    def app(self, *, config=None, finalize=True):
        factory = self.base.factory(finish_observation=config or self.config())
        return self.base.app(install=False, factory=factory, finalize=finalize)

    def completed(self):
        out = self.app().run('deploy')
        self.assertEqual('SESSION_COMPLETED', out['status'], out)
        self.assertEqual('RELEASED', out['fence'])
        self.assertEqual(1, len(self.saved)); self.assertEqual(1, len(self.close_events))
        return out

    def decode(self, raw):
        request = self.reads[-1][0]
        # Actual provider/reconciler used by the completed application is closed;
        # construct the same admitted empty baseline for isolated negative parsing.
        from delivery_objects import ObjectReconciler
        from test_delivery_adapter import validator
        reconciler = ObjectReconciler(self.h.binding, self.h.render, self.h.contract,
            validator, (None,) * len(core.objects_for(self.h.binding)))
        return obs.decode_original(raw, request, self.close_events[0], progress=lambda:request.progress,
            reconciler=reconciler, now=self.h.now, mono=self.h.mono)

    def stopped(self, out):
        self.assertEqual('STOPPED', out['status'], out)
        self.assertEqual('RETAINED', out['fence'], out)
        self.assertEqual(0, self.base.p.c.storage.complete_calls)
        self.assertNotIn(self.credential.token, json.dumps(out))
        self.assertNotIn('SYNTHETIC_SECRET', json.dumps(out))

    def test_actual_session_generates_native_bytes_after_close_and_before_claim_complete(self):
        out = self.completed(); b, raw = self.saved[0]; doc = json.loads(raw)
        self.assertEqual(obs.SCHEMA, doc['schema']); self.assertIs(False, doc['execution_authorized'])
        self.assertEqual(self.h.binding.run_id, doc['run_id'])
        self.assertIs(self.close_events[0].credential, self.credential)
        self.assertEqual(6, len(doc['observations']))
        for row, key in zip(doc['observations'], core.objects_for(b)):
            body = base64.b64decode(row['body_base64'])
            self.assertEqual(key, core.identity(json.loads(body)))
        after = [(r, k) for r, k, closes in self.reads if closes == 1]
        # Verifier pre/post checks also read, but captured rows come from the
        # explicit six-object producer after its separate scope authentication.
        self.assertGreaterEqual(len(after), 18)
        decoded = self.decode(raw); self.assertEqual(core.digest(raw), decoded.original_sha256)
        self.assertEqual('NOT_VERIFIED', out['delivery_gate'])
        self.assertEqual('NOT_VERIFIED', out['runtime']); self.assertFalse(self.base.slot.installed)
        self.assertNotIn(self.credential.token.encode(), raw)
        self.assertFalse(any(pin.path == 'finish' for _, pin in self.base.read_calls))
        self.assertEqual(4, sum(pin.path == 'completion-scope' for _, pin in self.base.read_calls))

    def test_original_must_be_persisted_before_release_and_persist_failure_retains_fence(self):
        def fail(root, binding, raw):
            self.assertEqual(0, self.base.p.c.storage.complete_calls)
            raise OSError('SYNTHETIC_SECRET')
        self.persist_mock.side_effect = fail
        self.stopped(self.app().run('deploy'))
        self.assertEqual(1, self.persist_mock.call_count)

    def test_sender_close_failure_never_produces_event_or_original(self):
        self.base.p.c.sender_change = lambda sender: replace(sender, close=Mock(side_effect=ValueError('SYNTHETIC_SECRET')))
        self.stopped(self.app().run('deploy'))
        self.assertEqual([], self.close_events); self.assertEqual([], self.saved)

    def test_preflight_no_finalize_and_status_do_not_observe_or_persist(self):
        app = self.app(); self.assertEqual('NOT_RUN', app.status()['runtime'])
        self.assertEqual('PREFLIGHT_OBSERVED', app.run('preflight')['status'])
        self.assertEqual([], self.close_events); self.assertEqual([], self.saved)
        self.assertEqual(0, self.base.p.c.sender_calls)

    def test_no_finalize_retains_claim_without_creating_original(self):
        out = self.app(finalize=False).run('deploy')
        self.assertEqual('RETAINED', out['fence']); self.assertEqual([], self.close_events)
        self.assertEqual([], self.saved)

    def test_fresh_post_close_uid_drift_or_denied_get_cannot_release(self):
        def change(r, o):
            if self.base.p.c.sender_closes:
                d = json.loads(o.raw); d['metadata']['uid'] = 'SYNTHETIC_CHANGED'
                return replace(o, raw=core.encoded(d))
            return o
        self.read_hook = change
        self.stopped(self.app().run('deploy')); self.assertEqual([], self.saved)

    def test_rollout_regression_after_close_is_not_hidden_by_status_normalization(self):
        def change(r, o):
            if self.base.p.c.sender_closes and o.key[1] == 'Deployment':
                d = json.loads(o.raw); d['status']['readyReplicas'] = 0
                return replace(o, raw=core.encoded(d))
            return o
        self.read_hook = change
        self.stopped(self.app().run('deploy')); self.assertEqual([], self.saved)

    def test_scope_withdrawal_after_persistence_keeps_original_and_fence(self):
        def save(root, binding, raw):
            self.saved.append((binding, raw)); self.base.raws['control'] = b'SYNTHETIC_WITHDRAWN'
        self.persist_mock.side_effect = save
        self.stopped(self.app().run('deploy')); self.assertEqual(1, len(self.saved))

    def test_expired_deadline_during_persistence_cannot_release(self):
        def save(root, binding, raw):
            self.saved.append((binding, raw)); self.h.mono += 10
        self.persist_mock.side_effect = save
        self.stopped(self.app().run('deploy')); self.assertEqual(1, len(self.saved))

    def test_raw_duplicate_nonfinite_unknown_and_bool_numeric_are_rejected(self):
        self.completed(); raw = self.saved[0][1]; d = json.loads(raw)
        invalid_bool = dict(d, observed_at=True)
        variants = [b'{"schema":1,"schema":2}', b'{"observed_at":NaN}', b'[]',
                    raw[:-1] + b',"extra":true}', core.encoded(invalid_bool)]
        for value in variants:
            with self.subTest(value=value[:45]), self.assertRaises(obs.FinishObservationError): self.decode(value)
        self.assertEqual(obs.SCHEMA, d['schema'])

    def test_duplicate_keys_and_size_fail_at_parser_before_deeper_reconciliation(self):
        self.completed(); raw = self.saved[0][1]; d = json.loads(raw)
        duplicate = raw[:-1] + b',"run_id":' + json.dumps(d['run_id']).encode() + b'}'
        with self.assertRaisesRegex(obs.FinishObservationError, '^FINISH_OBSERVATION_JSON_INVALID$'):
            self.decode(duplicate)
        with self.assertRaisesRegex(obs.FinishObservationError, '^FINISH_OBSERVATION_SIZE_INVALID$'):
            self.decode(b'x' * (obs.MAX_ORIGINAL + 1))

    def test_raw_context_scope_and_native_body_tamper_all_deny(self):
        self.completed(); raw = self.saved[0][1]
        for field, value in [('run_id', 'SYNTHETIC_OTHER_RUN'), ('action', 'restore'),
                             ('progress_key', 'f'*64), ('credential_fingerprint', 'f'*64),
                             ('observed_at', self.h.now+1), ('sender_closed_at', self.h.now-1),
                             ('execution_authorized', True)]:
            d = json.loads(raw); d[field] = value
            with self.subTest(field=field), self.assertRaises(obs.FinishObservationError): self.decode(core.encoded(d))
        for mode in ('uid', 'spec', 'scope', 'missing', '404', 'base64'):
            d = json.loads(raw); row = d['observations'][0]
            if mode == 'missing': d['observations'].pop()
            elif mode == '404': row['status'] = 404
            elif mode == 'base64': row['body_base64'] = '***'
            else:
                body = json.loads(base64.b64decode(row['body_base64']))
                if mode == 'uid': body['metadata']['uid'] = 'SYNTHETIC_REPLACED'
                elif mode == 'spec': body['spec']['ports'][0]['port'] += 1
                else: body['metadata']['namespace'] = 'SYNTHETIC_OTHER_NS'
                row['body_base64'] = base64.b64encode(core.encoded(body)).decode()
            with self.subTest(mode=mode), self.assertRaises(obs.FinishObservationError): self.decode(core.encoded(d))

    def test_context_rejects_missing_changed_or_foreign_close_event(self):
        self.completed(); request = self.reads[-1][0]; closed = self.close_events[0]
        for event in (None, replace(closed, credential=BearerCredential(self.credential.token)),
                      replace(closed, progress_key='f'*64), replace(closed, monotonic=self.h.mono+1)):
            with self.subTest(event=event), self.assertRaises(obs.FinishObservationError):
                obs.context_checked(request, event, lambda:request.progress, self.h.now, self.h.mono)

    def test_previous_readback_cache_cannot_become_a_new_observation(self):
        from delivery_object_reader import ObjectProvider
        from delivery_verifier import ObjectEvidence
        active = [False]
        real_finish = obs.ReadbackFinishAuthority.finish_observed
        real_objects = ObjectProvider.objects
        def finish(authority, *args):
            active[0] = True
            try: return real_finish(authority, *args)
            finally: active[0] = False
        def objects(p, r):
            if active[0]: return ObjectEvidence(r, r.progress.key)
            return real_objects(p, r)
        with patch.object(obs.ReadbackFinishAuthority, 'finish_observed', new=finish), patch.object(
                ObjectProvider, 'objects', new=objects):
            self.stopped(self.app().run('deploy'))
        self.assertEqual([], self.saved)

    def test_close_effective_clock_anchor_handles_stalled_wall_without_extending_deadline(self):
        self.base.terminal(); request = self.base.request()
        closed = SenderClosed(self.credential, self.h.binding.key, request.progress.key,
                              self.h.now+2, self.h.mono)
        authority = self.config().bind(self.h.binding, self.credential, lambda:request.progress,
            wall=lambda:self.h.now, monotonic=lambda:self.h.mono)
        self.addCleanup(authority.close)
        self.assertEqual(self.h.now+2, authority.clock(request, closed)[0])
        self.h.mono = request.deadline
        with self.assertRaises(obs.FinishObservationError): authority.clock(request, closed)

    def test_configuration_cannot_mix_old_finish_or_adopt_unsupported_followup_scope(self):
        for changes in ({'finish_capture': Mock()}, {'finish_collection': Mock()}):
            with self.assertRaises(runtime.RuntimeSiteError):
                self.base.factory(finish_observation=self.config(), **changes)
        for follows in ((), ('HEALTH_RULE',), list(obs.FOLLOWUPS)):
            with self.assertRaises(runtime.RuntimeSiteError): self.base.factory(
                finish_observation=self.config(required_followups=follows))
        self.assertEqual([], self.saved); self.assertEqual([], self.base.read_calls)

    def test_nine_object_profile_is_explicit_not_inferred_from_raw(self):
        self.base.context(nine(self.h), self.credential)
        self.base.slot = runtime.FinishAdoptionSlot(self.h.binding)
        self.base.raws['completion-scope'] = b'SYNTHETIC_EXISTING_NINE_OBJECT_COMPLETION_SCOPE'
        self.scope_grant = replace(self.base.grants['approval'], original_pin=replace(
            self.base.pins['approval'], path='completion-scope',
            sha256=core.digest(self.base.raws['completion-scope'])))
        def server(request):
            out = Harness.send(self.h, request)
            if request.method == 'PATCH':
                out = owned(out, core.objects_for(self.h.binding).index(core.identity(out)))
                self.h.objects[request.path.split('?')[0]] = out
            return out
        self.h.send = server
        self.completed(); self.assertEqual(9, len(json.loads(self.saved[0][1])['observations']))

    def test_restore_uses_its_separate_adopted_existing_baseline(self):
        _, factory = self.base.prepare_restore()
        # prepare_restore creates its own restore Binding/source/baseline policy.
        self.base.raws['completion-scope'] = b'SYNTHETIC_EXISTING_RESTORE_COMPLETION_SCOPE'
        self.scope_grant = replace(self.base.grants['approval'], original_pin=replace(
            self.base.pins['approval'], path='completion-scope',
            sha256=core.digest(self.base.raws['completion-scope'])))
        factory = runtime.RuntimeAuthorityFactory(factory.plan, factory.readers, self.base.slot,
            wall=lambda:self.h.now, monotonic=lambda:self.h.mono, finish_observation=self.config())
        out = self.base.app(install=False, factory=factory).run('restore')
        self.assertEqual('SESSION_COMPLETED', out['status'], out)
        self.assertEqual('restore', json.loads(self.saved[0][1])['action'])

    def test_runtime_main_status_passes_policy_without_receiving_or_producing(self):
        rc, out = self.base.call_cli(['status'], finish_observation=self.config())
        self.assertEqual(0, rc); self.assertEqual('NOT_RUN', out['runtime'])
        self.assertEqual([], self.saved); self.assertEqual([], self.base.read_calls)


class PersistenceTests(unittest.TestCase):
    @unittest.skipUnless(os.name == 'posix', 'Actual secure custody requires Linux')
    def test_actual_private_original_create_once_no_overwrite_or_symlink(self):
        binding = Harness().binding; raw = b'SYNTHETIC_PRIVATE_NATIVE_BYTES'
        with tempfile.TemporaryDirectory(prefix='p2-finish-', dir=Path.cwd()) as name:
            root = Path(name); root.chmod(0o700)
            obs.persist(root, binding, raw)
            target = root / (binding.key + '.finish-observation.json')
            self.assertEqual(raw, target.read_bytes()); self.assertEqual(0o600, target.stat().st_mode & 0o777)
            with self.assertRaises(obs.FinishObservationError): obs.persist(root, binding, b'OTHER')
            self.assertEqual(raw, target.read_bytes())
            other = replace(binding, run_id='SYNTHETIC_OTHER_RUN')
            link = root / (other.key + '.finish-observation.json'); link.symlink_to(target)
            with self.assertRaises(obs.FinishObservationError): obs.persist(root, other, b'OTHER')
            self.assertEqual(raw, target.read_bytes())


if __name__ == '__main__': unittest.main()
