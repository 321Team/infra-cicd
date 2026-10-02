"""Unit tests for the fail-closed delivery gate contract."""
import copy
import unittest

import delivery_gate as gate


def fixture():
    digest_a = 'sha256:' + 'a' * 64
    digest_b = 'sha256:' + 'b' * 64
    previous_a = 'sha256:' + 'c' * 64
    previous_b = 'sha256:' + 'd' * 64
    return {
        'schema': 'P1-CICD-delivery-gate-1',
        'template_only': False,
        'status': 'APPROVED_FOR_DELIVERY',
        'source': {
            'repository': '321Team/fds-msa',
            'commit': '1' * 40,
            'approval_reference': 'https://github.com/321Team/infra-cicd/issues/11#fixture',
        },
        'workflow': {
            'repository': '321Team/infra-cicd',
            'ref': 'refs/heads/main',
            'commit': '2' * 40,
            'run_id': 123,
            'run_attempt': 1,
        },
        'build_scan': {
            'result': 'PASS',
            'source_commit': '1' * 40,
            'workflow_commit': '2' * 40,
            'run_id': 123,
            'run_attempt': 1,
            'input_sha256': '3' * 64,
            'scan_result_sha256': '4' * 64,
            'images': {
                'transaction-api': {
                    'archive_sha256': '5' * 64,
                    'manifest_digest': digest_a,
                    'config_digest': 'sha256:' + '6' * 64,
                },
                'fds-engine': {
                    'archive_sha256': '7' * 64,
                    'manifest_digest': digest_b,
                    'config_digest': 'sha256:' + '8' * 64,
                },
            },
            'scan': {
                'transaction-api': {
                    'result': 'PASS', 'exit_code': 0, 'high_count': 0, 'critical_count': 0,
                    'manifest_digest': digest_a, 'report_sha256': '9' * 64,
                },
                'fds-engine': {
                    'result': 'PASS', 'exit_code': 0, 'high_count': 0, 'critical_count': 0,
                    'manifest_digest': digest_b, 'report_sha256': '0' * 64,
                },
            },
        },
        'harbor': {
            'endpoint': 'https://10.1.93.54',
            'runner_tls_trust': 'PASS',
            'if09_ac02': 'PASS',
            'handoff_reference': 'https://github.com/321Team/fds-project-baseline/issues/13#fixture',
            'credential_reference': 'actions-secret:HARBOR_CI_ROBOT',
            'repositories': {
                'transaction-api': '10.1.93.54/fds/transaction-api',
                'fds-engine': '10.1.93.54/fds/fds-engine',
            },
        },
        'push': {
            'transaction-api': {
                'result': 'PASS', 'registry_repository': '10.1.93.54/fds/transaction-api',
                'manifest_digest': digest_a, 'receipt_sha256': 'e' * 64,
            },
            'fds-engine': {
                'result': 'PASS', 'registry_repository': '10.1.93.54/fds/fds-engine',
                'manifest_digest': digest_b, 'receipt_sha256': 'f' * 64,
            },
        },
        'targets': {
            'transaction-api': {
                'namespace': 'fds-app', 'deployment': 'transaction-api', 'container': 'transaction-api',
                'previous_manifest_digest': previous_a,
            },
            'fds-engine': {
                'namespace': 'fds-app', 'deployment': 'fds-engine', 'container': 'fds-engine',
                'previous_manifest_digest': previous_b,
            },
        },
        'kubernetes': {
            'api_server': 'https://10.1.93.109:6443',
            'service_account': 'ci-cd/cicd-runner-sa',
            'rbac_scope': 'PASS',
            'worker_pull': 'PASS',
            'deployment_lock': 'PASS',
            'token_ttl_seconds': 3600,
        },
        'deployment_approval': {
            'approved_by': 'fixture reviewer',
            'reference': 'https://github.com/321Team/infra-cicd/issues/11#fixture-approval',
            'workflow_commit': '2' * 40,
            'run_id': 123,
            'run_attempt': 1,
        },
    }


class DeliveryGateTests(unittest.TestCase):
    def test_pre_push_matches_only_after_build_scan_and_harbor_handoff(self):
        result = gate.evaluate(fixture(), 'pre_push')
        self.assertEqual(result['decision'], 'CONDITIONS_MATCH')
        self.assertEqual([item['service'] for item in result['plan']['items']],
                         ['transaction-api', 'fds-engine'])
        self.assertEqual(result['runtime_enforcement'], 'NOT_INTEGRATED')
        self.assertEqual(result['delivery_gate'], 'NOT_VERIFIED')

    def test_high_or_critical_finding_blocks_push(self):
        for field in ('high_count', 'critical_count'):
            record = fixture()
            record['build_scan']['scan']['transaction-api'][field] = 1
            with self.subTest(field=field):
                result = gate.evaluate(record, 'pre_push')
                self.assertEqual(result['decision'], 'BLOCK')
                self.assertIn('transaction-api:' + field.replace('_count', '').upper() +
                              '_FINDINGS_OR_INVALID_COUNT', result['reasons'])
                self.assertIsNone(result['plan'])

    def test_scanner_or_build_failure_blocks_before_push(self):
        for mutate, reason in (
            (lambda r: r['build_scan'].update(result='FAIL'), 'BUILD_SCAN_NOT_PASS'),
            (lambda r: r['build_scan']['scan']['fds-engine'].update(result='FAIL', exit_code=1),
             'fds-engine:SCAN_NOT_PASS'),
            (lambda r: r['harbor'].update(runner_tls_trust='FAIL'), 'RUNNER_TLS_TRUST_NOT_PASS'),
            (lambda r: r['harbor'].update(if09_ac02='PENDING'), 'HARBOR_IF09_AC02_NOT_PASS'),
        ):
            record = fixture()
            mutate(record)
            with self.subTest(reason=reason):
                result = gate.evaluate(record, 'pre_push')
                self.assertEqual(result['decision'], 'BLOCK')
                self.assertIn(reason, result['reasons'])

    def test_pre_deploy_requires_same_digest_push_and_minimum_rbac(self):
        result = gate.evaluate(fixture(), 'pre_deploy')
        self.assertEqual(result['decision'], 'CONDITIONS_MATCH')
        self.assertEqual(result['plan']['items'][0]['deployment'], 'transaction-api')

        faults = (
            (lambda r: r['push']['transaction-api'].update(manifest_digest='sha256:' + 'f' * 64),
             'transaction-api:PUSH_MANIFEST_MISMATCH'),
            (lambda r: r['kubernetes'].update(rbac_scope='FAIL'), 'KUBERNETES_RBAC_SCOPE_NOT_PASS'),
            (lambda r: r['kubernetes'].update(token_ttl_seconds=7200), 'TOKEN_TTL_INVALID'),
            (lambda r: r['targets']['fds-engine'].update(container='other'),
             'fds-engine:TARGET_CONTAINER_INVALID'),
        )
        for mutate, reason in faults:
            record = fixture()
            mutate(record)
            with self.subTest(reason=reason):
                blocked = gate.evaluate(record, 'pre_deploy')
                self.assertEqual(blocked['decision'], 'BLOCK')
                self.assertIn(reason, blocked['reasons'])

    def test_approval_and_run_binding_cannot_drift(self):
        for mutate, reason in (
            (lambda r: r['build_scan'].update(run_id=124), 'BUILD_RUN_BINDING_MISMATCH'),
            (lambda r: r['deployment_approval'].update(run_attempt=2),
             'DEPLOY_APPROVAL_ATTEMPT_MISMATCH'),
            (lambda r: r['source'].update(commit='f' * 40), 'BUILD_SOURCE_BINDING_MISMATCH'),
        ):
            record = fixture()
            mutate(record)
            with self.subTest(reason=reason):
                result = gate.evaluate(record, 'pre_deploy')
                self.assertEqual(result['decision'], 'BLOCK')
                self.assertIn(reason, result['reasons'])

    def test_template_unapproved_or_secret_like_credential_value_blocks(self):
        for mutate, reason in (
            (lambda r: r.update(template_only=True), 'TEMPLATE_OR_UNCONFIRMED_INPUT'),
            (lambda r: r.update(status='PENDING_RUNTIME_INPUT'), 'DELIVERY_INPUT_NOT_APPROVED'),
            (lambda r: r['harbor'].update(credential_reference='password=plaintext'),
             'HARBOR_CREDENTIAL_REFERENCE_INVALID'),
        ):
            record = fixture()
            mutate(record)
            with self.subTest(reason=reason):
                result = gate.evaluate(record, 'pre_push')
                self.assertEqual(result['decision'], 'BLOCK')
                self.assertIn(reason, result['reasons'])

    def test_exact_two_services_and_fixed_targets_are_required(self):
        record = fixture()
        record['build_scan']['images']['other'] = copy.deepcopy(
            record['build_scan']['images']['fds-engine'])
        result = gate.evaluate(record, 'pre_push')
        self.assertEqual(result['decision'], 'BLOCK')
        self.assertIn('EXACT_TWO_BUILD_IMAGES_REQUIRED', result['reasons'])


if __name__ == '__main__':
    unittest.main()
