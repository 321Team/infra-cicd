"""Offline privacy/traceability tests; all credentials and objects are synthetic."""
import base64
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest

import delivery_adapter as core
import delivery_diagnostics as d
import delivery_store as store
from delivery_object_reader import FailureDiagnostics, Observation
from delivery_verifier import Progress, VerificationError
from test_delivery_adapter import fixture
from test_delivery_object_reader import absent

SECRET = 'synthetic-diagnostic-secret-marker'


class PrivacyTests(unittest.TestCase):
    def setUp(self):
        self.binding, raw, _ = fixture()
        self.docs = json.loads(raw)

    def object(self, kind):
        obj = copy.deepcopy(next(v for v in self.docs if v['kind'] == kind))
        obj['metadata'].update(uid='synthetic-uid', resourceVersion='7')
        return obj

    def record(self, obj, *, status=200, key=None):
        key = key or core.identity(obj)
        original = core.encoded(obj)
        observation = Observation(key, status, original)
        diagnostic = FailureDiagnostics('OBJECT_DRIFT', key, (observation,))
        raw = d.diagnostic_bytes(self.binding, Progress(), 1000, diagnostic)
        report = json.loads(raw)
        row = report['observations'][0]
        snapshot = base64.b64decode(row['snapshot_base64'], validate=True)
        self.assertEqual(row['source_sha256'], core.digest(original))
        self.assertEqual(row['snapshot_sha256'], core.digest(snapshot))
        self.assertEqual(observation.raw, original)
        self.assertFalse(report['execution_authorized'])
        self.assertFalse(report['snapshot_is_original'])
        self.assertEqual(report['schema'], 'P2-CICD-private-diagnostic-3')
        self.assertNotIn('body_base64', row)
        self.assertNotIn(SECRET.encode(), raw)
        self.assertNotIn(SECRET.encode(), snapshot)
        return report, json.loads(snapshot)

    def test_route_tls_material_removed_but_identity_and_original_hash_retained(self):
        obj = self.object('Route')
        for name in ('key', 'certificate', 'caCertificate', 'destinationCACertificate'):
            obj['spec']['tls'][name] = SECRET
        original = copy.deepcopy(obj)
        report, saved = self.record(obj)
        self.assertEqual(obj, original)
        self.assertEqual(saved['metadata']['uid'], 'synthetic-uid')
        self.assertEqual(saved['spec']['host'], obj['spec']['host'])
        self.assertEqual(saved['spec']['tls']['termination'], 'edge')
        self.assertEqual(saved['spec']['tls']['key'], d.REDACTED)
        self.assertEqual(report['observations'][0]['redaction_counts']['route_tls'], 4)

    def test_container_env_commands_headers_and_annotation_shadow_copies_removed(self):
        obj = self.object('Deployment')
        obj['metadata']['annotations'] = {'kubectl.kubernetes.io/last-applied-configuration': SECRET}
        template = obj['spec']['template']
        template['metadata']['annotations'] = {'opaque': SECRET}
        for group in ('containers', 'initContainers', 'ephemeralContainers'):
            template['spec'][group] = [{'name': 'synthetic', 'image': 'synthetic',
                'env': [{'name': 'PASSWORD', 'value': SECRET},
                        {'name': 'DATABASE_URL', 'valueFrom': {'secretKeyRef': {'name': 'db', 'key': 'url'}}}],
                'command': [SECRET], 'args': [SECRET],
                'readinessProbe': {'httpGet': {'httpHeaders': [{'name': 'Authorization', 'value': SECRET}]}}}]
        obj['status'] = {'conditions': [{'message': SECRET, 'type': 'Available', 'status': 'False'}]}
        _, saved = self.record(obj)
        for group in ('containers', 'initContainers', 'ephemeralContainers'):
            c = saved['spec']['template']['spec'][group][0]
            self.assertEqual(c['env'][0]['value'], d.REDACTED)
            self.assertEqual(c['env'][1]['valueFrom']['secretKeyRef'], {'name': 'db', 'key': 'url'})
            self.assertEqual(c['command'], [d.REDACTED])
        self.assertEqual(saved['status']['conditions'][0]['status'], 'False')

    def test_valid_404_keeps_only_classified_absence_fields(self):
        key = core.OBJECTS[0]
        obj = absent(key); obj.update(message=SECRET, extra=SECRET)
        _, saved = self.record(obj, status=404, key=key)
        self.assertEqual(saved['code'], 404)
        self.assertEqual(saved['details']['name'], key[2])
        self.assertNotIn('message', saved)
        self.assertNotIn('extra', saved)

    def test_unmodified_nonsecret_spec_remains_available_for_diagnosis(self):
        obj = self.object('Service')
        obj['metadata']['labels'] = {'app': 'synthetic-service'}
        _, saved = self.record(obj)
        self.assertEqual(saved['spec'], obj['spec'])
        self.assertEqual(saved['metadata']['resourceVersion'], '7')
        self.assertEqual(saved['metadata']['labels'], obj['metadata']['labels'])

    def test_invalid_identity_namespace_status_and_opaque_root_are_rejected(self):
        obj = self.object('Service')
        cases = []
        wrong = copy.deepcopy(obj); wrong['metadata']['namespace'] = 'p1'; cases.append((wrong, 200))
        wrong = copy.deepcopy(obj); wrong['kind'] = 'Secret'; cases.append((wrong, 200))
        wrong = copy.deepcopy(obj); wrong['data'] = {'sensitive': SECRET}; cases.append((wrong, 200))
        wrong = absent(core.OBJECTS[0]); wrong['details']['name'] = 'another'; cases.append((wrong, 404))
        for value, status in cases:
            with self.subTest(status=status, kind=value['kind']):
                diag = FailureDiagnostics('OBJECT_DRIFT', None,
                    (Observation(core.OBJECTS[0], status, core.encoded(value)),))
                with self.assertRaises(VerificationError):
                    d.diagnostic_bytes(self.binding, Progress(), 1000, diag)

    def test_malformed_duplicate_and_nonfinite_json_fail_before_serialization(self):
        for raw in (b'broken', b'{"kind":"Service","kind":"Secret"}', b'{"x":NaN}', b'{"x":1e999}'):
            diag = FailureDiagnostics('OBJECT_DRIFT', None, (Observation(core.OBJECTS[0], 200, raw),))
            with self.assertRaises(VerificationError):
                d.diagnostic_bytes(self.binding, Progress(), 1000, diag)

    def test_malformed_redaction_slots_and_excess_depth_are_rejected(self):
        for mutate in (lambda v: v['metadata'].update(annotations=SECRET),
                       lambda v: v['spec'].update(env={'value': SECRET}),
                       lambda v: v['spec'].update(args=SECRET)):
            obj = self.object('Service'); mutate(obj)
            diag = FailureDiagnostics('OBJECT_DRIFT', None,
                (Observation(core.identity(obj), 200, core.encoded(obj)),))
            with self.assertRaises(VerificationError):
                d.diagnostic_bytes(self.binding, Progress(), 1000, diag)
        obj = self.object('Service'); nested = obj['spec']
        for _ in range(70): nested['nested'] = {}; nested = nested['nested']
        diag = FailureDiagnostics('OBJECT_DRIFT', None,
            (Observation(core.identity(obj), 200, core.encoded(obj)),))
        with self.assertRaises(VerificationError):
            d.diagnostic_bytes(self.binding, Progress(), 1000, diag)

    def test_redacted_bytes_are_deterministic_and_phase_still_bound(self):
        obj = self.object('Route'); obj['spec']['tls']['key'] = SECRET
        diag = FailureDiagnostics('OBJECT_DRIFT', None,
            (Observation(core.identity(obj), 200, core.encoded(obj)),))
        a = d.diagnostic_bytes(self.binding, Progress(), 1000, diag)
        self.assertEqual(a, d.diagnostic_bytes(self.binding, Progress(), 1000, diag))
        b = d.diagnostic_bytes(self.binding, Progress(), 1000, diag, phase='preflight')
        self.assertNotEqual(a, b)
        self.assertEqual(json.loads(a)['observations'], json.loads(b)['observations'])

    @unittest.skipUnless(os.name == 'posix', 'Actual secure persistence requires Linux')
    def test_actual_file_contains_no_raw_secret_and_no_overwrite(self):
        obj = self.object('Route'); obj['spec']['tls']['key'] = SECRET
        diag = FailureDiagnostics('OBJECT_DRIFT', None,
            (Observation(core.identity(obj), 200, core.encoded(obj)),))
        with tempfile.TemporaryDirectory(prefix='p2-private-', dir=Path.cwd()) as name:
            root = Path(name); root.chmod(0o700)
            d.persist(root, self.binding, Progress(), 1000, diag)
            target = root / d.diagnostic_name(self.binding, 'session')
            raw = target.read_bytes()
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)
            self.assertNotIn(SECRET.encode(), raw)
            self.assertNotIn(SECRET.encode(), base64.b64decode(json.loads(raw)['observations'][0]['snapshot_base64']))
            with self.assertRaises(store.StoreError): d.persist(root, self.binding, Progress(), 1000, diag)
            self.assertEqual(target.read_bytes(), raw)


if __name__ == '__main__': unittest.main()
