import copy
import unittest
from unittest.mock import Mock

import delivery_gate as gate


def complete_record():
    # Synthetic metadata; never evidence of a real scan, approval or deployment.
    workflow_sha = 'a' * 40
    record = {'schema': 'P1-CICD-01-gate-input-1.0', 'template_only': False,
              'workflow': {'repository': gate.REPO, 'ref': 'refs/heads/main',
                           'commit': workflow_sha, 'approved_commit': workflow_sha,
                           'approval_reference': 'fixture:workflow', 'run_id': 10, 'run_attempt': 2},
              'runtime': {k: 'PASS' for k in ('runner_job', 'builder_rootless', 'harbor_tls',
                                             'kubernetes_access', 'token_valid', 'rbac_scope',
                                             'worker_trust', 'deployment_lock')},
              'deployment_approval': {'approved_by': 'fixture-reviewer', 'reference': 'fixture:approval',
                                      'run_id': 10, 'run_attempt': 2, 'workflow_commit': workflow_sha},
              'images': []}
    for name, letter in zip(gate.DEPLOYMENTS, ('b', 'c')):
        digest = 'sha256:' + letter * 64
        record['images'].append({'deployment': name,
            'source': {'repository': 'fixture/' + name, 'approved_repository': 'fixture/' + name,
                       'commit': 'd' * 40, 'approved_commit': 'd' * 40, 'approval_reference': 'fixture:source'},
            'tests': {'unit': 'PASS', 'contract': 'PASS'},
            'build': {'result': 'PASS', 'manifest_digest': digest},
            'scan': {'result': 'PASS', 'exit_code': 0, 'critical_count': 0, 'high_count': 7,
                     'manifest_digest': digest, 'report_sha256': 'e' * 64,
                     'tool_version': 'fixture-version', 'db_reference': 'fixture:db'},
            'registry_repository': '10.1.93.54/fds/fixture-' + name,
            'approved_registry_repository': '10.1.93.54/fds/fixture-' + name,
            'push': {'result': 'PASS', 'manifest_digest': digest},
            'approved_manifest_digest': digest,
            'target': {'namespace': 'fds-app', 'service_account': 'ci-cd/cicd-runner-sa',
                       'container': 'fixture-app', 'previous_manifest_digest': 'sha256:' + 'f' * 64}})
    return record


class DeliveryGateTests(unittest.TestCase):
    def blocked(self, record, phase='pre_deploy'):
        action = Mock()
        with self.assertRaises(gate.GateBlocked):
            gate.guarded_call(record, phase, action)
        action.assert_not_called()

    def test_valid_fixture_reaches_callback_with_digest_pins_only(self):
        record = complete_record(); action = Mock(return_value='fixture-done')
        self.assertEqual(gate.guarded_call(record, 'pre_deploy', action), 'fixture-done')
        plan = action.call_args.args[0]
        self.assertEqual({p['deployment'] for p in plan['images']}, set(gate.DEPLOYMENTS))
        self.assertTrue(all('@sha256:' in p['image'] for p in plan['images']))
        self.assertEqual(gate.evaluate(record, 'pre_deploy')['delivery_gate'], 'NOT_VERIFIED')
        self.assertEqual(gate.evaluate(record, 'pre_deploy')['runtime_enforcement'], 'NOT_INTEGRATED')

    def test_test_failure_in_either_app_prevents_push_and_deploy(self):
        for index in range(2):
            for kind in ('unit', 'contract'):
                for phase in ('pre_push', 'pre_deploy'):
                    with self.subTest(index=index, kind=kind, phase=phase):
                        record = complete_record(); record['images'][index]['tests'][kind] = 'FAIL'
                        self.blocked(record, phase)

    def test_critical_findings_block_even_with_exit_zero(self):
        record = complete_record(); record['images'][1]['scan']['critical_count'] = 1
        self.blocked(record, 'pre_push'); self.blocked(record)

    def test_high_findings_do_not_expand_critical_only_policy(self):
        record = complete_record()
        self.assertEqual(gate.evaluate(record, 'pre_push')['decision'], 'CONDITIONS_MATCH')

    def test_scanner_error_blocks_even_with_zero_findings(self):
        for value in (1, 2, None, False, '0'):
            with self.subTest(value=value):
                record = complete_record(); record['images'][0]['scan']['exit_code'] = value
                self.blocked(record)

    def test_count_unknown_negative_bool_or_string_blocks(self):
        for value in (None, -1, False, '0'):
            with self.subTest(value=value):
                record = complete_record(); record['images'][0]['scan']['critical_count'] = value
                self.blocked(record)

    def test_report_or_db_absence_blocks(self):
        for key in ('report_sha256', 'tool_version', 'db_reference', 'result'):
            with self.subTest(key=key):
                record = complete_record(); record['images'][0]['scan'].pop(key)
                self.blocked(record)

    def test_build_scan_push_approval_digest_mismatch_each_blocks(self):
        for stage in ('scan', 'push', 'approval'):
            with self.subTest(stage=stage):
                record = complete_record(); image = record['images'][0]
                if stage == 'approval': image['approved_manifest_digest'] = 'sha256:' + '9' * 64
                else: image[stage]['manifest_digest'] = 'sha256:' + '9' * 64
                self.blocked(record)

    def test_tag_or_short_digest_does_not_qualify(self):
        for value in ('latest', 'sha256:abcd', 'a' * 64, None):
            record = complete_record(); record['images'][0]['build']['manifest_digest'] = value
            with self.subTest(value=value): self.blocked(record)

    def test_stale_approval_attempt_or_workflow_blocks(self):
        for key, value in (('run_id', 11), ('run_attempt', 1), ('workflow_commit', 'b' * 40), ('run_id', True)):
            with self.subTest(key=key):
                record = complete_record(); record['deployment_approval'][key] = value
                self.blocked(record)

    def test_unapproved_source_or_workflow_blocks(self):
        for field in ('source', 'workflow'):
            record = complete_record()
            (record['images'][0]['source'] if field == 'source' else record['workflow'])['commit'] = 'e' * 40
            with self.subTest(field=field): self.blocked(record)

    def test_branch_or_repository_scope_expansion_blocks(self):
        for key, value in (('ref', 'refs/pull/3/merge'), ('repository', '321Team/infra-ansible')):
            record = complete_record(); record['workflow'][key] = value
            with self.subTest(key=key): self.blocked(record)

    def test_duplicate_missing_or_extra_deployment_blocks(self):
        for mode in ('duplicate', 'missing', 'extra'):
            record = complete_record()
            if mode == 'duplicate': record['images'][1] = copy.deepcopy(record['images'][0])
            elif mode == 'missing': record['images'].pop()
            else: record['images'].append({'deployment': 'unapproved'})
            with self.subTest(mode=mode): self.blocked(record)

    def test_wrong_namespace_sa_or_unknown_container_blocks(self):
        for key, value in (('namespace', 'kube-system'), ('service_account', 'admin'), ('container', None)):
            record = complete_record(); record['images'][0]['target'][key] = value
            with self.subTest(key=key): self.blocked(record)

    def test_registry_scope_or_approval_mismatch_blocks(self):
        record = complete_record(); record['images'][0]['registry_repository'] = 'example.org/other/app'
        self.blocked(record, 'pre_push')

    def test_missing_runtime_token_tls_lock_or_rbac_blocks(self):
        for key in complete_record()['runtime']:
            record = complete_record(); record['runtime'][key] = 'NOT_VERIFIED'
            with self.subTest(key=key): self.blocked(record)

    def test_pre_push_does_not_require_future_push_or_deployment_receipts(self):
        record = complete_record(); record['deployment_approval'] = None
        for image in record['images']:
            image['push'] = None; image['approved_manifest_digest'] = None; image['target'] = None
        self.assertEqual(gate.evaluate(record, 'pre_push')['decision'], 'CONDITIONS_MATCH')
        self.blocked(record, 'pre_deploy')

    def test_callback_failure_is_not_retried_or_converted_to_success(self):
        action = Mock(side_effect=RuntimeError('fixture failure'))
        with self.assertRaises(RuntimeError): gate.guarded_call(complete_record(), 'pre_deploy', action)
        self.assertEqual(action.call_count, 1)

    def test_unfilled_or_malformed_inputs_fail_closed_without_echo(self):
        for record in ({}, None, {'images': 'SENTINEL_SECRET'}, {'workflow': []}):
            result = gate.evaluate(record, 'pre_deploy')
            self.assertEqual(result['decision'], 'BLOCK')
            self.assertIsNone(result['plan']); self.assertNotIn('SENTINEL', str(result))


if __name__ == '__main__':
    unittest.main()
