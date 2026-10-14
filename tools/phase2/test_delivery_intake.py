"""Synthetic, offline contract tests. No ROSA/Registry/P1 runtime evidence."""
import copy
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import delivery_intake as intake


def fixture():
    ref = 'https://github.com/321Team/fds-project-baseline/issues/46'
    record = {
        'schema': 'P2-CICD-intake-1', 'template_only': False, 'environment': 'ROSA',
        'target': {'cluster_reference': ref, 'api_url': 'https://api.rosa.example.invalid:6443',
                   'ca_reference': ref, 'context': 'synthetic-rosa', 'namespace': 'fds-app',
                   'health_url': 'https://app.example.invalid/readyz'},
        'source': {'repository': '321Team/fds-msa', 'commit': 'a' * 40,
                   'manifest_repository': '321Team/fds-msa', 'manifest_commit': 'b' * 40,
                   'base_path': 'deploy/base', 'overlay_path': 'deploy/overlays/rosa',
                   'rendered_sha256': 'c' * 64},
        'registry': {'endpoint': 'https://registry.example.invalid', 'decision_reference': ref},
        'credential': {'reference': 'handoff-reference:SYNTHETIC_ONLY', 'policy_reference': ref},
        'entry': dict.fromkeys(('platform_reference', 'app_reference', 'runtime_scope_reference'), ref),
        'workloads': {},
    }
    for stage, run_id in (('build', 101), ('push', 202)):
        record[stage] = {'source_commit': 'a' * 40, 'workflow_commit': 'd' * 40,
                         'run_id': run_id, 'attempt': 1, 'evidence_reference': ref, 'result': 'PASS'}
    for service in intake.SERVICES:
        image = 'registry.example.invalid/fds/' + service + '@sha256:' + 'e' * 64
        record['workloads'][service] = {
            'deployment': service, 'container': service, 'approved_image': image, 'pushed_image': image,
            'previous_approved_image': image[:-64] + 'f' * 64, 'health_contract_reference': ref,
            'test_result': 'PASS', 'scan_result': 'PASS', 'scan_evidence_reference': ref,
            'rollback_reference': ref}
    return record


class IntakeTests(unittest.TestCase):
    def setUp(self):
        self.record = fixture()

    def assertBlocked(self, record):
        result = intake.evaluate(record)
        self.assertEqual(result['status'], 'INCOMPLETE_OR_INCONSISTENT')
        self.assertTrue(result['errors'])
        self.assertIs(result['execution_authorized'], False)
        return result

    def test_consistent_is_never_authorization_or_verified_evidence(self):
        before = copy.deepcopy(self.record)
        report = intake.evaluate(self.record)
        self.assertEqual(report['status'], 'DECLARED_INPUTS_CONSISTENT')
        self.assertFalse(report['execution_authorized'])
        self.assertFalse(report['external_references_verified'])
        self.assertEqual(report['runtime'], 'NOT_RUN')
        self.assertEqual(before, self.record)

    def test_every_required_field_omission_is_reported(self):
        paths = [(key,) for key in self.record]
        for key, value in self.record.items():
            if isinstance(value, dict):
                paths.extend((key, child) for child in value)
        for service, value in self.record['workloads'].items():
            paths.extend(('workloads', service, key) for key in value)
        for path in paths:
            with self.subTest(path=path):
                record = copy.deepcopy(self.record)
                node = record
                for key in path[:-1]:
                    node = node[key]
                del node[path[-1]]
                self.assertBlocked(record)

    def test_wrong_types_fail_without_traceback(self):
        for value in (None, [], 'not-object', 4, True):
            self.assertBlocked(value)
            for path in ('target', 'source', 'credential', 'build', 'push', 'workloads'):
                with self.subTest(value=value, path=path):
                    record = fixture()
                    record[path] = value
                    self.assertBlocked(record)

    def test_lab_or_p1_environment_and_template_block(self):
        for value in ('SHARED_OCP_LAB', 'P1', None):
            self.record['environment'] = value
            self.assertBlocked(self.record)
        self.record = fixture()
        self.record['template_only'] = True
        self.assertBlocked(self.record)
        self.record = fixture()
        self.record['target']['api_url'] = 'https://10.1.93.109:6443'
        self.assertIn('P1_API_NOT_ROSA', self.assertBlocked(self.record)['errors'])

    def test_credentials_and_unknown_fields_never_echo(self):
        sentinel = 'SYNTHETIC_SENSITIVE_SENTINEL'
        for field in ('token', 'kubeconfig', sentinel):
            self.record = fixture()
            self.record['credential'][field] = sentinel
            report = self.assertBlocked(self.record)
            self.assertNotIn(sentinel, json.dumps(report))
        self.record = fixture()
        self.record['credential']['reference'] = sentinel
        self.assertNotIn(sentinel, json.dumps(self.assertBlocked(self.record)))

    def test_invalid_or_secret_bearing_urls(self):
        for url in ('http://api.example.invalid', 'https://u:p@api.example.invalid',
                    'https://api.example.invalid?token=secret', 'https://api.example.invalid#secret',
                    'https://api.example.invalid:bad', 'https://api.example.invalid:0',
                    'https://api.example.invalid:65536', 'https://api.example.invalid\\bad',
                    'https://api.example.invalid/\n', 'https://api.example.invalid/path'):
            with self.subTest(url=url):
                self.record['target']['api_url'] = url
                self.assertBlocked(self.record)

    def test_source_run_attempt_and_gate_failures(self):
        for stage in ('build', 'push'):
            for field, value in (('source_commit', 'b' * 40), ('workflow_commit', 'short'),
                                 ('run_id', True), ('attempt', 0), ('attempt', '1'),
                                 ('result', 'FAIL'), ('result', 'NOT_RUN')):
                with self.subTest(stage=stage, field=field, value=value):
                    record = fixture()
                    record[stage][field] = value
                    self.assertBlocked(record)

    def test_overlay_or_other_namespace_cannot_pass_intake(self):
        for namespace in ('rosa-app', 'default', 'synthetic-app', 'fds-app ', None, [], {}):
            with self.subTest(namespace=namespace):
                record = fixture()
                record['target']['namespace'] = namespace
                with patch('socket.socket', side_effect=AssertionError('network forbidden')), \
                     patch('subprocess.run', side_effect=AssertionError('subprocess forbidden')):
                    rc, report = self.run_cli(json.dumps(record).encode())
                self.assertEqual(rc, 2)
                self.assertIn('P2_NAMESPACE_REQUIRED', report['errors'])
                self.assertFalse(report['execution_authorized'])
                self.assertFalse(report['external_references_verified'])
                self.assertEqual(report['runtime'], 'NOT_RUN')

    def test_test_scan_or_push_failure_blocks_both_workloads(self):
        for service in intake.SERVICES:
            for field, value in (('test_result', 'FAIL'), ('scan_result', 'ERROR'),
                                 ('pushed_image', 'registry.example.invalid/fds/api@sha256:' + '1' * 64)):
                record = fixture()
                record['workloads'][service][field] = value
                self.assertBlocked(record)

    def test_mutable_foreign_and_missing_rollback_images(self):
        for value in ('registry.example.invalid/fds/api:latest',
                      'foreign.example.invalid/fds/api@sha256:' + 'e' * 64, None):
            for field in ('approved_image', 'pushed_image', 'previous_approved_image'):
                record = fixture()
                record['workloads']['transaction-api'][field] = value
                self.assertBlocked(record)

    def test_registry_port_works_without_forcing_harbor_choice(self):
        self.record['registry']['endpoint'] += ':8443'
        for workload in self.record['workloads'].values():
            for field in ('approved_image', 'pushed_image', 'previous_approved_image'):
                workload[field] = workload[field].replace('invalid/', 'invalid:8443/')
        self.assertEqual(intake.evaluate(self.record)['errors'], [])

    def test_duplicate_deployment_and_path_traversal(self):
        self.record['workloads']['fds-engine']['deployment'] = 'transaction-api'
        self.assertBlocked(self.record)
        for value in ('../deploy', '/deploy', 'deploy/../base', 'C:\\deploy', 'deploy/./base'):
            record = fixture()
            record['source']['base_path'] = value
            self.assertBlocked(record)

    def run_cli(self, raw):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'intake.json'
            path.write_bytes(raw)
            before = path.read_bytes()
            output = io.StringIO()
            with redirect_stdout(output):
                rc = intake.main([str(path)])
            self.assertEqual(path.read_bytes(), before)
            return rc, json.loads(output.getvalue())

    def test_cli_reads_only_input_and_returns_no_executable_plan(self):
        with patch('subprocess.run', side_effect=AssertionError('subprocess forbidden')), \
             patch('socket.socket', side_effect=AssertionError('network forbidden')):
            rc, report = self.run_cli(json.dumps(self.record).encode())
        self.assertEqual(rc, 0)
        self.assertFalse(report['execution_authorized'])
        self.assertNotIn('plan', report)

    def test_invalid_duplicate_nonfinite_or_oversized_json(self):
        for raw in (b'{"token":"NEVER_ECHO",', b'{"schema":1,"schema":2}',
                    b'{"extra":NaN}', b'\xff', b' ' * (intake.MAX_BYTES + 1)):
            rc, report = self.run_cli(raw)
            self.assertEqual(rc, 2)
            self.assertEqual(report['errors'], ['INPUT_UNREADABLE_OR_INVALID_JSON'])
            self.assertNotIn('NEVER_ECHO', json.dumps(report))

    def test_deep_nonobject_input_is_blocked_with_any_python_recursion_limit(self):
        rc, report = self.run_cli(b'[' * 2000 + b']' * 2000)
        self.assertEqual(rc, 2)
        self.assertFalse(report['execution_authorized'])

    def test_missing_file_does_not_echo_path(self):
        output = io.StringIO()
        with tempfile.TemporaryDirectory() as directory, redirect_stdout(output):
            rc = intake.main([str(Path(directory) / 'SECRET_MARKER.json')])
        self.assertEqual(rc, 2)
        self.assertNotIn('SECRET_MARKER', output.getvalue())

    def test_checked_in_pending_template_is_incomplete(self):
        path = Path(__file__).resolve().parents[2] / 'templates/phase2/delivery-intake.pending.json'
        self.assertBlocked(json.loads(path.read_text(encoding='utf-8')))


if __name__ == '__main__':
    unittest.main()
