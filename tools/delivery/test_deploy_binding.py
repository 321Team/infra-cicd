"""Synthetic input tests; no runtime or independently verified approval."""
import copy
import datetime as dt
import hashlib
import json
import unittest

import deploy_binding as b
import delivery_gate as gate
from test_delivery_gate import fixture

NOW = dt.datetime(2026, 10, 6, 1, tzinfo=dt.timezone.utc)


def encode(obj):
    return json.dumps(obj, sort_keys=True).encode()


def inputs():
    delivery = fixture()
    execution = {'repository': gate.WORKFLOW_REPOSITORY, 'ref': 'refs/heads/main',
                 'workflow_sha': 'a' * 40, 'run_id': 999, 'attempt': 1}
    push_execution = {'workflow_sha': 'b' * 40, 'run_id': '456', 'attempt': 1}
    push = {'PUSH': 'PASS', 'error_code': 'NONE', 'build_workflow': delivery['workflow'],
            'execution_context': push_execution, 'images': {}}
    targets, images = {}, {}
    for i, name in enumerate(gate.SERVICES, 1):
        digest = delivery['build_scan']['images'][name]['manifest_digest']
        push['images'][name] = {'result': 'PASS', 'registry_repository': gate.REGISTRY_PREFIX + name,
                                'manifest_digest': digest}
        targets[name] = {'namespace': gate.NAMESPACE, 'deployment': name, 'container': name,
                         'uid': f'{i:08x}-1111-2222-3333-444444444444',
                         'previous_image': gate.REGISTRY_PREFIX + name + '@sha256:' + str(i) * 64}
        images[name] = gate.REGISTRY_PREFIX + name + '@' + digest
    push_raw = encode(push)
    record = {'schema': 'P1-CICD-deploy-binding-1', 'template_only': False,
              'delivery': delivery, 'execution': execution, 'push_execution': push_execution,
              'push_receipt_sha256': b.sha(push_raw), 'targets': targets,
              'kubernetes': copy.deepcopy(delivery['kubernetes'])}
    record['kubernetes']['token_audience'] = b.AUDIENCE
    record['approval'] = {'execution': copy.deepcopy(execution),
                          'reference': 'https://github.com/321Team/infra-cicd/issues/11#fixture',
                          'approved_by': 'synthetic-reviewer',
                          'push_receipt_sha256': b.sha(push_raw),
                          'targets': copy.deepcopy(targets), 'images': images,
                          'allow_rollback': False,
                          'not_before': '2026-10-06T09:00:00+09:00',
                          'expires_at': '2026-10-06T11:00:00+09:00'}
    return record, push_raw, copy.deepcopy(execution)


class BindingTests(unittest.TestCase):
    def setUp(self):
        self.record, self.push, self.execution = inputs()

    def check(self):
        raw = encode(self.record)
        return b.validate(raw, self.push, b.sha(raw), self.execution, NOW)

    def test_distinct_build_push_deploy_identity_and_exact_images(self):
        result = self.check()
        self.assertEqual(result['binding'], 'MATCH')
        self.assertEqual(result['build_workflow']['run_id'], 123)
        self.assertEqual(result['execution']['run_id'], 999)
        self.assertEqual(result['remaining_seconds'], 3600)
        self.assertFalse(result['allow_rollback'])
        self.assertEqual(result['approval_verification'], 'NOT_INDEPENDENTLY_VERIFIED')
        self.assertEqual(result['runtime_enforcement'], 'NOT_INTEGRATED')
        self.assertEqual(result['delivery_gate'], 'NOT_VERIFIED')
        self.assertEqual(len(result['targets']), 2)

    def test_input_hash_mismatch(self):
        with self.assertRaisesRegex(b.Invalid, 'INPUT_HASH_MISMATCH'):
            b.validate(encode(self.record), self.push, '0' * 64, self.execution, NOW)

    def test_push_bytes_cannot_be_reformatted_without_new_hash(self):
        self.push += b' '
        with self.assertRaisesRegex(b.Invalid, 'PUSH_HASH_MISMATCH'):
            self.check()

    def test_build_identity_not_replaced_by_deploy_identity(self):
        self.record['delivery']['workflow']['commit'] = self.execution['workflow_sha']
        with self.assertRaisesRegex(b.Invalid, 'BUILD_INPUT_BLOCKED'):
            self.check()

    def test_push_build_mismatch_even_if_hash_updated(self):
        push = json.loads(self.push)
        push['build_workflow']['run_id'] = 777
        self.push = encode(push)
        self.record['push_receipt_sha256'] = b.sha(self.push)
        with self.assertRaisesRegex(b.Invalid, 'PUSH_BUILD_MISMATCH'):
            self.check()

    def test_wrong_actual_run_and_retry_attempt_rejected(self):
        for key, value in [('run_id', 1000), ('attempt', 2), ('workflow_sha', 'c' * 40)]:
            with self.subTest(key=key):
                old = self.execution[key]
                self.execution[key] = value
                with self.assertRaisesRegex(b.Invalid, 'DEPLOY_EXECUTION_MISMATCH'):
                    self.check()
                self.execution[key] = old

    def test_boolean_run_and_extra_context_rejected(self):
        for change in ({'attempt': True}, {'unexpected': 1}):
            original = copy.deepcopy(self.execution)
            self.execution.update(change)
            with self.assertRaises(b.Invalid):
                self.check()
            self.execution = original

    def test_expired_future_and_timezone_less_approval(self):
        for change in ({'expires_at': NOW.isoformat()},
                       {'not_before': '2026-10-06T02:00:00+00:00'},
                       {'expires_at': '2026-10-06T02:00:00'}):
            old = copy.deepcopy(self.record['approval'])
            self.record['approval'].update(change)
            with self.assertRaises(b.Invalid):
                self.check()
            self.record['approval'] = old

    def test_approval_rebinds_targets_images_and_push_hash(self):
        for key, value in [('targets', {}), ('images', {}),
                           ('push_receipt_sha256', '0' * 64)]:
            old = self.record['approval'][key]
            self.record['approval'][key] = value
            with self.assertRaises(b.Invalid):
                self.check()
            self.record['approval'][key] = old

    def test_audience_endpoint_and_ttl_must_match(self):
        for key, value in [('token_audience', 'other'), ('api_server', 'https://other'),
                           ('token_ttl_seconds', 901), ('token_ttl_seconds', 3600),
                           ('token_ttl_seconds', 7200), ('token_ttl_seconds', True),
                           ('service_account', 'default')]:
            old = self.record['kubernetes'][key]
            self.record['kubernetes'][key] = value
            with self.assertRaisesRegex(b.Invalid, 'KUBERNETES_SCOPE_INVALID'):
                self.check()
            self.record['kubernetes'][key] = old

    def test_unapproved_registry_digest_and_failed_push(self):
        for key, value in [('PUSH', 'FAIL'), ('error_code', 'BAD')]:
            push = json.loads(self.push)
            push[key] = value
            raw = encode(push)
            original = self.push
            self.push = raw
            self.record['push_receipt_sha256'] = b.sha(raw)
            with self.assertRaisesRegex(b.Invalid, 'PUSH_NOT_PASS'):
                self.check()
            self.push = original

    def test_image_change_in_push_receipt_blocked(self):
        push = json.loads(self.push)
        push['images']['transaction-api']['manifest_digest'] = 'sha256:' + 'f' * 64
        self.push = encode(push)
        self.record['push_receipt_sha256'] = b.sha(self.push)
        with self.assertRaisesRegex(b.Invalid, 'PUSH_IMAGE_MISMATCH'):
            self.check()

    def test_previous_image_requires_full_approved_registry_digest(self):
        self.record['targets']['transaction-api']['previous_image'] = 'repo:latest'
        with self.assertRaisesRegex(b.Invalid, 'TARGET_IMAGE_OR_UID_INVALID'):
            self.check()

    def test_duplicate_json_keys_and_nonfinite_json_rejected(self):
        for raw in (b'{"x":1,"x":2}', b'{"x": NaN}', b'[]', b'bad'):
            with self.subTest(raw=raw), self.assertRaises(b.Invalid):
                b.validate(raw, self.push, b.sha(raw), self.execution, NOW)

    def test_wrong_org_or_lookalike_approval_reference_rejected(self):
        for url in ('https://githubXcom/321Team/infra-cicd/issues/1',
                    'https://github.com/other/infra-cicd/issues/1',
                    'https://github.com/321Team/infra-cicd/issues/1?token=secret'):
            self.record['approval']['reference'] = url
            with self.assertRaisesRegex(b.Invalid, 'APPROVAL_REFERENCE_INVALID'):
                self.check()

    def test_rollback_boolean_is_not_coerced(self):
        self.record['approval']['allow_rollback'] = 'true'
        with self.assertRaisesRegex(b.Invalid, 'ROLLBACK_SCOPE_INVALID'):
            self.check()

    def test_missing_fields_fail_closed_without_key_error(self):
        del self.record['approval']
        with self.assertRaisesRegex(b.Invalid, 'RECORD_INVALID'):
            self.check()


if __name__ == '__main__':
    unittest.main()
