"""Composition and CLI tests. Authorities, API, wire and store are SYNTHETIC.

Real Verifier/ObjectProvider/Reconciler/Session run; no socket/DNS/token issuer.
Separate POSIX cases below exercise real private diagnostic file persistence.
"""
import contextlib
import copy
from dataclasses import replace
import io
import json
import os
from pathlib import Path
import socket
import ssl
import tempfile
import unittest
from unittest.mock import Mock, patch

import delivery_adapter as core
import delivery_cli as cli
import delivery_diagnostics as diagnostics
import delivery_provider as p
import delivery_session as session
import delivery_store as store
import delivery_transport as transport
from delivery_object_reader import (BearerCredential, FailureDiagnostics,
                                    ObjectReader, Observation)
from delivery_verifier import Progress, ReadRequest, VerificationError
from test_delivery_adapter import SENSITIVE, validator
from test_delivery_operational import ContractHarness, ORIGIN
from test_delivery_objects import owned
from test_delivery_transport import CA, TOKEN
from test_delivery_object_reader import absent


class ProviderTests(unittest.TestCase):
    def setUp(self):
        for name in ('socket', 'getaddrinfo', 'create_connection'):
            guard = patch.object(socket, name, side_effect=AssertionError('NETWORK_FORBIDDEN'))
            guard.start(); self.addCleanup(guard.stop)
        self.c = ContractHarness()
        # Synthetic CA is not PEM. Keep that explicit; mock TLS construction,
        # never weaken product certificate validation to accommodate fixtures.
        context = Mock()
        context.verify_mode = ssl.CERT_REQUIRED
        context.check_hostname = True
        context.keylog_filename = None
        guard = patch.object(transport.ssl, 'SSLContext', return_value=context)
        guard.start(); self.addCleanup(guard.stop)
        self.credential = BearerCredential(TOKEN)
        self.c.credential = self.credential
        self.c.providers.credential = self.credential
        self.c.h.rebind(ca_sha256=core.digest(CA))
        self.c.providers.hooks['platform'] = lambda r, v: replace(
            v, credential_fingerprint=self.credential.fingerprint, ca_pem=CA)
        self.baselines = 0
        self.reads = 0
        self.fail_read = None
        self.saved = []
        self.fail_diagnostic = False
        self.after_write = lambda: None
        self.adapters = p.AuthorityAdapters(self.c.providers.approval,
            self.c.providers.platform, self.c.providers.producer,
            self.baseline, self.c.providers.finish)
        original = self.c.h.send
        def server(request):
            out = original(request)
            if request.method == 'PATCH':
                out = owned(out, core.OBJECTS.index(core.identity(out)))
                self.c.h.objects[request.path.split('?')[0]] = copy.deepcopy(out)
                self.after_write()
            return out
        self.c.h.send = server
        def read(reader, r, key):
            reader.check_request(r)
            self.reads += 1
            if self.fail_read == self.reads:
                raise VerificationError('HTTP_FORBIDDEN')
            obj = self.c.h.objects.get(core.path_for(key))
            return Observation(key, 404 if obj is None else 200,
                core.encoded(absent(key) if obj is None else obj))
        def run(*args, **kwargs):
            kwargs.update(acquire=self.c.storage.acquire,
                          journal_factory=self.c.storage.journal)
            return session.run_session(*args, **kwargs)
        def save(root, binding, progress, now, diagnostic, *, phase='session'):
            if self.fail_diagnostic:
                raise OSError(SENSITIVE)
            self.saved.append(json.loads(diagnostics.diagnostic_bytes(
                binding, progress, now, diagnostic, phase=phase)))
        for target, name, kw in (
            (p, 'CommittedProducerBridge', {'return_value': validator}),
            (p, 'run_session', {'side_effect': run}),
            (p, 'bind_tls_sender', {'side_effect': self.c.sender}),
            (ObjectReader, 'read', {'new': read}),
            (diagnostics, 'persist', {'side_effect': save})):
            guard = patch.object(target, name, **kw)
            guard.start(); self.addCleanup(guard.stop)

    def baseline(self, r):
        self.baselines += 1
        values = tuple(None if core.path_for(k) not in self.c.h.objects else
                       core.encoded(self.c.h.objects[core.path_for(k)]) for k in core.OBJECTS)
        return p.AdmittedBaseline(r, r.binding, ORIGIN, values)

    def app(self, *, adapters=None, finalize=True):
        h = self.c.h
        inputs = p.RunInputs(h.binding, h.render, h.contract, 'UNUSED_SYNTHETIC_SOURCE',
                             'UNUSED_SYNTHETIC_STORE', 5, finalize)
        return p.OperationalApplication(inputs, self.credential, adapters or self.adapters,
            wall=lambda: h.now, monotonic=lambda: h.mono, sleep=h.sleep)

    def safe(self, result):
        text = json.dumps(result)
        self.assertNotIn(SENSITIVE, text)
        self.assertNotIn(TOKEN, text)
        self.assertFalse(result['execution_authorized'])
        self.assertEqual(result['delivery_gate'], 'NOT_VERIFIED')

    def test_status_does_not_read_authorities_or_claim(self):
        app = self.app(); out = app.status()
        self.assertEqual(out['status'], 'CONFIGURED_NOT_VERIFIED')
        self.assertFalse(app.used); self.assertEqual(self.c.providers.calls, [])
        self.assertEqual(self.c.storage.claims, [])

    def test_deploy_connects_real_progress_reconciler_and_session(self):
        app = self.app(); out = app.run('deploy')
        self.assertEqual(out['status'], 'SESSION_COMPLETED', out)
        self.assertEqual([r.method for r in self.c.h.requests], ['PATCH'] * 6 + ['GET'] * 2)
        self.assertEqual(self.baselines, 1)
        self.assertGreater(self.reads, 6)
        self.assertEqual(self.c.storage.complete_calls, 1)
        self.assertIsNone(self.c.storage.active)
        self.assertIsNone(app._credential)
        self.safe(out)

    def test_preflight_never_acquires_or_constructs_write_sender(self):
        out = self.app().run('preflight')
        self.assertEqual(out['status'], 'PREFLIGHT_OBSERVED', out)
        self.assertEqual(self.c.storage.claims, [])
        self.assertEqual(self.c.sender_calls, 0)
        self.assertEqual(self.c.h.requests, [])
        self.assertEqual(self.reads, 6)
        self.safe(out)

    def test_preflight_and_session_failure_keep_separate_immutable_diagnostics(self):
        files = {}
        def create_once(root, binding, progress, now, diagnostic, *, phase='session'):
            name = diagnostics.diagnostic_name(binding, phase)
            if name in files:
                raise FileExistsError('SYNTHETIC_ALREADY_EXISTS')
            raw = diagnostics.diagnostic_bytes(binding, progress, now, diagnostic, phase=phase)
            files[name] = raw
        with patch.object(diagnostics, 'persist', side_effect=create_once):
            self.fail_read = 3
            first = self.app().run('preflight')
            self.assertEqual(first['diagnostics'], 'RECORDED', first)
            self.assertEqual(first['error_code'], 'HTTP_FORBIDDEN')
            before = dict(files)
            self.fail_read = None
            self.after_write = lambda: self.c.h.objects[core.path_for(core.OBJECTS[0])]['spec'].update(foreign=True)
            second = self.app().run('deploy')
        self.assertEqual(second['status'], 'STOPPED', second)
        self.assertEqual(second['diagnostics'], 'RECORDED', second)
        self.assertEqual(len(self.c.h.requests), 1)
        self.assertIsNotNone(self.c.storage.active)
        self.assertEqual(self.c.storage.complete_calls, 0)
        self.assertEqual(len(files), 2)
        self.assertTrue(all(files[k] == raw for k, raw in before.items()))
        self.assertEqual({json.loads(raw)['phase'] for raw in files.values()}, {'preflight', 'session'})
        self.assertEqual({json.loads(raw)['code'] for raw in files.values()}, {'HTTP_FORBIDDEN', 'OBJECT_DRIFT'})
        self.safe(first); self.safe(second)

    def test_unrecognized_preflight_exception_cannot_be_public_code(self):
        with patch.object(p, 'Verifier', side_effect=VerificationError(SENSITIVE)):
            out = self.app().run('preflight')
        self.assertEqual(out['error_code'], 'OPERATIONAL_PREPARATION_FAILED')
        self.safe(out)

    def provider(self, progress):
        return p.OperationalProviders(self.c.h.binding, self.credential, lambda: progress,
            rendered=self.c.h.render, contract=self.c.h.contract, validator=validator,
            adapters=self.adapters, diagnostic_sink=lambda *args: None,
            wall=lambda: self.c.h.now, monotonic=lambda: self.c.h.mono)

    def test_first_object_read_after_pending_or_reply_cannot_adopt_baseline(self):
        for progress in (Progress(journal=b'{"status":"WRITE_PENDING","acknowledged":[]}'),
                         Progress(responses=(b'{"synthetic":"observed_reply"}',))):
            with self.subTest(progress_key=progress.key):
                provider = self.provider(progress)
                request = ReadRequest(self.c.h.binding, self.credential, progress, self.c.h.mono + 5)
                provider.platform(request)
                with self.assertRaisesRegex(VerificationError, '^BASELINE_ADMISSION_REQUIRED$'):
                    provider.objects(request)
                self.assertEqual(self.baselines, 0)
                self.assertEqual(self.reads, 0)
                self.assertIsNone(provider.object_provider)
                provider.close()

    def test_non_ip_platform_pin_change_is_rejected_by_provider_itself(self):
        for change in ({'ca_pem': CA + b'\n'}, {'expires_at': 1901}):
            with self.subTest(field=next(iter(change))):
                self.c.providers.hooks['platform'] = lambda r, v: replace(
                    v, credential_fingerprint=self.credential.fingerprint, ca_pem=CA)
                provider = self.provider(Progress())
                request = ReadRequest(self.c.h.binding, self.credential, Progress(), self.c.h.mono + 5)
                provider.platform(request)
                original_reader, original_pins = provider.reader, provider.pins
                self.c.providers.hooks['platform'] = lambda r, v: replace(
                    replace(v, credential_fingerprint=self.credential.fingerprint, ca_pem=CA), **change)
                with self.assertRaisesRegex(VerificationError, '^PLATFORM_CONTEXT_CHANGED$'):
                    provider.platform(request)
                self.assertIs(provider.reader, original_reader)
                self.assertEqual(provider.pins, original_pins)
                self.assertEqual(self.c.h.requests, [])
                provider.close()

    def test_action_mismatch_denies_before_authority_io(self):
        app = self.app(); out = app.run('restore')
        self.assertEqual(out['status'], 'STOPPED')
        self.assertEqual(self.c.providers.calls, [])
        self.assertEqual(self.c.storage.claims, [])
        self.assertTrue(app.used)

    def test_separate_restore_binding_uses_same_guards(self):
        self.c.h.rebind(action='restore', run_id='synthetic-restore')
        out = self.app().run('restore')
        self.assertEqual(out['status'], 'SESSION_COMPLETED', out)
        self.assertEqual(len(self.c.h.requests), 8)

    def test_same_application_cannot_run_twice(self):
        app = self.app(); self.assertEqual(app.run('deploy')['status'], 'SESSION_COMPLETED')
        count = len(self.c.h.requests)
        self.assertEqual(app.run('deploy')['status'], 'STOPPED')
        self.assertEqual(len(self.c.h.requests), count)

    def test_plain_json_approval_cannot_authorize(self):
        adapters = replace(self.adapters, approval=lambda r: {'approved': True})
        out = self.app(adapters=adapters).run('deploy')
        self.assertEqual(out['error_code'], 'PREFLIGHT_DENIED')
        self.assertEqual(self.c.storage.claims, [])
        self.assertEqual(self.c.h.requests, [])

    def test_stale_request_evidence_denied(self):
        self.c.providers.hooks['approval'] = lambda r, v: replace(v, request=replace(r))
        out = self.app().run('deploy')
        self.assertEqual(out['error_code'], 'PREFLIGHT_DENIED')
        self.assertEqual(self.c.storage.claims, [])

    def test_expired_adapter_response_denied_before_claim(self):
        def late(r, v):
            self.c.h.mono += 6
            return v
        self.c.providers.hooks['approval'] = late
        out = self.app().run('deploy')
        self.assertEqual(out['error_code'], 'PREFLIGHT_DENIED')
        self.assertEqual(self.c.storage.claims, [])

    def test_platform_ip_change_stops_before_second_patch(self):
        def platform(r, v):
            return replace(v, ca_pem=CA, credential_fingerprint=self.credential.fingerprint,
                           endpoint_ip='192.0.2.11' if self.c.h.requests else '192.0.2.10')
        self.c.providers.hooks['platform'] = platform
        out = self.app().run('deploy')
        self.assertEqual(out['status'], 'STOPPED')
        self.assertEqual(len(self.c.h.requests), 1)
        self.assertIsNotNone(self.c.storage.active)
        self.assertEqual(self.c.storage.complete_calls, 0)

    def test_authority_exception_secret_is_not_in_result(self):
        def fail(r): raise RuntimeError(SENSITIVE)
        out = self.app(adapters=replace(self.adapters, approval=fail)).run('deploy')
        self.assertEqual(out['error_code'], 'PREFLIGHT_DENIED')
        self.safe(out)

    def test_baseline_requires_admission_adapter(self):
        out = self.app(adapters=replace(self.adapters, baseline=lambda r: (None,) * 6)).run('deploy')
        self.assertEqual(out['error_code'], 'PREFLIGHT_DENIED')
        self.assertEqual(self.c.storage.claims, [])

    def test_partial_get_failure_persists_only_classified_replies(self):
        self.fail_read = 3
        out = self.app().run('deploy')
        self.assertEqual(out['status'], 'STOPPED')
        self.assertEqual(out['diagnostics'], 'RECORDED')
        self.assertEqual(self.saved[0]['code'], 'HTTP_FORBIDDEN')
        self.assertEqual(len(self.saved[0]['observations']), 2)
        self.assertFalse(self.saved[0]['execution_authorized'])
        self.assertEqual(self.saved[0]['failed_key'], list(core.OBJECTS[2]))
        self.assertEqual(self.c.storage.claims, [])
        self.safe(out)

    def test_drift_after_write_keeps_original_baseline_and_fence(self):
        def drift():
            obj = self.c.h.objects[core.path_for(core.OBJECTS[0])]
            obj['spec']['selector']['foreign'] = 'writer'
        self.after_write = drift
        out = self.app().run('deploy')
        self.assertEqual(out['status'], 'STOPPED')
        self.assertEqual(out['diagnostics'], 'RECORDED')
        self.assertEqual(self.saved[0]['code'], 'OBJECT_DRIFT')
        self.assertEqual(len(self.c.h.requests), 1)
        self.assertEqual(self.baselines, 1)
        self.assertIsNotNone(self.c.storage.active)
        self.assertEqual(self.c.storage.complete_calls, 0)

    def test_diagnostic_failure_is_visible_and_does_not_release_fence(self):
        self.fail_diagnostic = True
        self.after_write = lambda: self.c.h.objects[core.path_for(core.OBJECTS[0])]['spec'].update(foreign=True)
        out = self.app().run('deploy')
        self.assertEqual(out['diagnostics'], 'FAILED')
        self.assertEqual(out['status'], 'STOPPED')
        self.assertEqual(len(self.c.h.requests), 1)
        self.assertIsNotNone(self.c.storage.active)
        self.safe(out)

    def test_finish_denial_does_not_complete_even_after_rollout(self):
        self.c.providers.hooks['finish'] = lambda r, v: replace(v, disposition='UNKNOWN')
        out = self.app().run('deploy')
        self.assertEqual(out['error_code'], 'COMPLETION_DENIED')
        self.assertIsNotNone(self.c.storage.active)
        self.assertEqual(self.c.sender_closes, 1)

    def test_new_restore_run_cannot_bypass_interrupted_fence(self):
        self.c.h.fail_at = 1
        self.assertEqual(self.app().run('deploy')['status'], 'STOPPED')
        count = len(self.c.h.requests)
        self.c.h.rebind(action='restore', run_id='synthetic-new-restore')
        out = self.app().run('restore')
        self.assertEqual(out['error_code'], 'CLAIM_ACQUIRE_FAILED', out)
        self.assertEqual(len(self.c.h.requests), count)

    def test_cli_dispatches_typed_application_only(self):
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream): rc = cli.main(['deploy'], application=self.app())
        self.assertEqual(rc, 0)
        self.safe(json.loads(stream.getvalue()))
        self.assertNotIn(TOKEN, stream.getvalue())

    def test_cli_json_configuration_and_secret_flags_rejected(self):
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            self.assertEqual(cli.main(['deploy'], application={'approved': True, 'token': TOKEN}), 2)
        self.assertNotIn(TOKEN, stream.getvalue())
        err = io.StringIO()
        with contextlib.redirect_stderr(err), self.assertRaises(SystemExit):
            cli.main(['deploy', '--token', SENSITIVE], application=self.app())
        self.assertEqual(err.getvalue(), 'CLI_ARGUMENTS_REJECTED\n')

    def test_source_validation_failure_does_not_reach_authority(self):
        def reject(render, contract): raise RuntimeError(SENSITIVE)
        with patch.object(p, 'CommittedProducerBridge', return_value=reject):
            out = self.app().run('deploy')
        self.assertEqual(out['error_code'], 'INPUT_PREPARATION_FAILED')
        self.assertEqual(self.c.providers.calls, [])
        self.assertEqual(self.c.storage.claims, [])
        self.safe(out)

    def test_unconfigured_preflight_cannot_load_ambient_inputs(self):
        stream = io.StringIO()
        with patch.dict(os.environ, {'KUBECONFIG': 'NEVER_READ', 'P2_TOKEN': TOKEN}), contextlib.redirect_stdout(stream):
            self.assertEqual(cli.main(['preflight']), 2)
        report = json.loads(stream.getvalue())
        self.assertEqual(report['sender_calls'], 0)
        self.assertFalse(report['claim_acquired'])
        self.assertEqual(self.c.providers.calls, [])
        self.assertNotIn(TOKEN, stream.getvalue())

    def test_diagnostic_rejects_unclassified_reply_and_uncontrolled_code(self):
        b = self.c.h.binding
        for d in (FailureDiagnostics(SENSITIVE, None, ()),
                  FailureDiagnostics('OBJECT_DRIFT', None,
                      (Observation(core.OBJECTS[0], 403, b'{"synthetic":"invalid"}'),)),
                  FailureDiagnostics('OBJECT_DRIFT', ('v1', 'Secret', 'forbidden'), ())):
            with self.assertRaises(VerificationError):
                diagnostics.diagnostic_bytes(b, Progress(), 1000, d)

    def test_factory_failure_is_preclaim_and_never_constructs_sender(self):
        def fail(*args): raise RuntimeError(SENSITIVE)
        h = self.c.h
        out = session.run_session(h.binding, h.render, h.contract, validator,
            root='UNUSED', providers=None, credential=self.credential,
            sender_factory=self.c.sender, provider_factory=fail, budget_seconds=5,
            acquire=self.c.storage.acquire, journal_factory=self.c.storage.journal,
            wall=lambda: h.now, monotonic=lambda: h.mono, sleep=h.sleep)
        self.assertEqual(out['error_code'], 'PROVIDER_INITIALIZATION_FAILED')
        self.assertEqual(self.c.storage.claims, [])
        self.assertEqual(self.c.sender_calls, 0)


@unittest.skipUnless(os.name == 'posix', 'Actual secure POSIX persistence requires Linux')
class DiagnosticPosixTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='p2-diagnostic-', dir=Path.cwd())
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name); self.root.chmod(0o700)
        self.c = ContractHarness()
        self.diagnostic = FailureDiagnostics('OBJECT_DRIFT', None,
            (Observation(core.OBJECTS[0], 200, core.encoded(
                owned(json.loads(self.c.h.render)[0], 0))),))

    def save(self, phase='session'):
        diagnostics.persist(self.root, self.c.h.binding, Progress(), 1000, self.diagnostic, phase=phase)

    def test_private_create_once_and_original_bytes(self):
        self.save()
        target = self.root / diagnostics.diagnostic_name(self.c.h.binding, 'session')
        self.assertEqual(target.stat().st_mode & 0o777, 0o600)
        before = target.read_bytes()
        self.assertEqual(json.loads(before)['purpose'], 'DIAGNOSTIC_ONLY')
        with self.assertRaises(store.StoreError): self.save()
        self.assertEqual(target.read_bytes(), before)
        self.assertFalse(any('active' in p.name or 'journal' in p.name for p in self.root.iterdir()))

    def test_symlink_target_cannot_be_overwritten(self):
        outside = self.root / 'keep'; outside.write_bytes(b'original')
        link = self.root / diagnostics.diagnostic_name(self.c.h.binding, 'session')
        link.symlink_to(outside)
        with self.assertRaises(store.StoreError): self.save()
        self.assertEqual(outside.read_bytes(), b'original')

    def test_unsafe_root_fails_without_writing(self):
        self.root.chmod(0o777)
        with self.assertRaises(store.StoreError): self.save()
        self.assertEqual(list(self.root.iterdir()), [])

    def test_preflight_and_session_files_coexist_without_overwrite(self):
        self.save('preflight')
        target = self.root / diagnostics.diagnostic_name(self.c.h.binding, 'preflight')
        before = target.read_bytes()
        self.save('session')
        self.assertEqual(target.read_bytes(), before)
        self.assertEqual(len(list(self.root.iterdir())), 2)
        for phase in ('preflight', 'session'):
            with self.assertRaises(store.StoreError): self.save(phase)
            saved = self.root / diagnostics.diagnostic_name(self.c.h.binding, phase)
            self.assertEqual(saved.stat().st_mode & 0o777, 0o600)
            self.assertEqual(json.loads(saved.read_bytes())['phase'], phase)

    def test_untrusted_phase_cannot_select_a_path(self):
        for phase in ('../outside', '', 'deploy', None):
            with self.assertRaises(store.StoreError): self.save(phase)
        self.assertEqual(list(self.root.iterdir()), [])


if __name__ == '__main__': unittest.main()
