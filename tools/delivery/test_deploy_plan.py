"""Synthetic v2 contract tests; no independent approval or server access."""
import copy
import datetime as dt
import unittest

import deploy_binding as b
import deploy_plan as p
from test_deploy_binding import NOW, encode, inputs


def plan_inputs():
    legacy, push, execution = inputs()
    plan = {key: copy.deepcopy(legacy[key]) for key in (
        'delivery', 'push_execution', 'push_receipt_sha256', 'targets')}
    plan.update(schema='P1-CICD-deploy-plan-2', template_only=False,
                request_id='11111111-2222-3333-4444-555555555555',
                workflow={key: execution[key] for key in p.WORKFLOW_FIELDS},
                kubernetes={key: legacy['kubernetes'][key]
                            for key in p.KUBERNETES_FIELDS})
    old = legacy['approval']
    approval = {key: old[key] for key in (
        'reference', 'approved_by', 'not_before', 'expires_at', 'allow_rollback')}
    approval.update(schema='P1-CICD-deploy-approval-2', template_only=False,
                    plan_sha256=b.sha(encode(plan)), workflow=copy.deepcopy(plan['workflow']))
    return plan, approval, push, execution


class PlanTests(unittest.TestCase):
    def setUp(self):
        self.plan, self.approval, self.push, self.execution = plan_inputs()

    def check(self, *, plan_hash=None, approval_hash=None, now=NOW):
        plan, approval = encode(self.plan), encode(self.approval)
        return p.validate(plan, approval, self.push,
                          plan_hash or b.sha(plan), approval_hash or b.sha(approval),
                          self.execution, now)

    def reapprove_fixture(self):
        # Only test fixture construction: never a production approval creator.
        self.approval['plan_sha256'] = b.sha(encode(self.plan))

    def test_pre_dispatch_approval_without_run_id_records_actual_execution(self):
        self.assertNotIn('execution', self.plan)
        self.assertNotIn('execution', self.approval)
        result = self.check()
        self.assertEqual(result['execution'], self.execution)
        self.assertEqual(result['build_workflow']['run_id'], 123)
        self.assertEqual(result['push_execution']['run_id'], '456')
        self.assertEqual(result['plan_sha256'], b.sha(encode(self.plan)))
        self.assertEqual(result['approval_sha256'], b.sha(encode(self.approval)))
        self.assertEqual(result['remaining_seconds'], 3600)
        self.assertEqual(result['claim'], 'NOT_ACQUIRED')
        self.assertEqual(result['approval_verification'], 'NOT_INDEPENDENTLY_VERIFIED')
        self.assertEqual(result['runtime_enforcement'], 'NOT_INTEGRATED')
        self.assertEqual(result['delivery_gate'], 'NOT_VERIFIED')

    def test_first_run_can_be_known_after_approval_but_requires_claim(self):
        self.execution['run_id'] = 1000
        self.assertEqual(self.check()['execution']['run_id'], 1000)
        self.assertEqual(self.check()['claim'], 'NOT_ACQUIRED')

    def test_old_token_policy_plan_cannot_be_reused_or_reapproved(self):
        for ttl in (901, 3600):
            self.plan['kubernetes']['token_ttl_seconds'] = ttl
            with self.subTest(ttl=ttl), self.assertRaisesRegex(
                    b.Invalid, 'KUBERNETES_SCOPE_INVALID'):
                self.check()
            self.reapprove_fixture()
            with self.subTest(ttl=ttl), self.assertRaisesRegex(
                    b.Invalid, 'KUBERNETES_SCOPE_INVALID'):
                self.check()

    def test_token_policy_correction_does_not_rebind_old_approval_bytes(self):
        self.plan['kubernetes']['token_ttl_seconds'] = 3600
        self.reapprove_fixture()
        self.plan['kubernetes']['token_ttl_seconds'] = 900
        with self.assertRaisesRegex(b.Invalid, 'APPROVAL_PLAN_MISMATCH'):
            self.check()

    def test_retry_attempt_and_bool_attempt_rejected(self):
        for attempt in (2, True, 0, '1'):
            with self.subTest(attempt=attempt), self.assertRaises(b.Invalid):
                self.execution['attempt'] = attempt
                self.check()

    def test_actual_workflow_sha_ref_and_repository_must_match(self):
        for key, value in (('workflow_sha', 'c' * 40), ('ref', 'refs/heads/other'),
                           ('repository', 'other/repo')):
            with self.subTest(key=key), self.assertRaises(b.Invalid):
                old = copy.deepcopy(self.execution)
                self.execution[key] = value
                try:
                    self.check()
                finally:
                    self.execution = old

    def test_detached_approval_cannot_rebind_workflow(self):
        self.approval['workflow']['workflow_sha'] = 'c' * 40
        with self.assertRaisesRegex(b.Invalid, 'APPROVAL_WORKFLOW_MISMATCH'):
            self.check()

    def test_external_plan_and_approval_hashes_are_both_required(self):
        for options, code in (({'plan_hash': '0' * 64}, 'PLAN_HASH_MISMATCH'),
                              ({'approval_hash': '0' * 64}, 'APPROVAL_HASH_MISMATCH')):
            with self.subTest(options=options), self.assertRaisesRegex(b.Invalid, code):
                self.check(**options)

    def test_changed_previous_uid_or_request_needs_a_new_approval(self):
        for field in ('previous_image', 'uid', 'request_id'):
            old = copy.deepcopy(self.plan)
            if field == 'request_id':
                self.plan[field] = '22222222-2222-3333-4444-555555555555'
            elif field == 'uid':
                self.plan['targets']['transaction-api'][field] = 'aaaaaaaa-2222-3333-4444-555555555555'
            else:
                self.plan['targets']['transaction-api'][field] = (
                    '10.1.93.54/fds/transaction-api@sha256:' + '9' * 64)
            with self.subTest(field=field), self.assertRaisesRegex(b.Invalid, 'APPROVAL_PLAN_MISMATCH'):
                self.check()
            self.plan = old

    def test_no_embedded_approval_execution_or_circular_hash(self):
        for key, value in (('approval', self.approval), ('execution', self.execution),
                           ('plan_sha256', '0' * 64)):
            old = copy.deepcopy(self.plan)
            self.plan[key] = value
            with self.subTest(key=key), self.assertRaisesRegex(b.Invalid, 'PLAN_FIELDS_INVALID'):
                self.check()
            self.plan = old

    def test_approval_run_id_and_unknown_field_are_not_silently_accepted(self):
        for key in ('run_id', 'execution', 'extra'):
            self.approval[key] = 999
            with self.subTest(key=key), self.assertRaisesRegex(b.Invalid, 'APPROVAL_FIELDS_INVALID'):
                self.check()
            del self.approval[key]

    def test_expiry_future_naive_and_now_boundary(self):
        for field, value in (('expires_at', NOW.isoformat()),
                             ('not_before', '2026-10-06T02:00:00+00:00'),
                             ('expires_at', '2026-10-06T02:00:00')):
            old = copy.deepcopy(self.approval)
            self.approval[field] = value
            with self.subTest(field=field), self.assertRaises(b.Invalid):
                self.check()
            self.approval = old
        with self.assertRaises(b.Invalid):
            self.check(now=dt.datetime(2026, 10, 6, 1))

    def test_build_and_push_ids_are_preserved_and_not_reused(self):
        for run_id in (123, 456):
            self.execution['run_id'] = run_id
            with self.subTest(run_id=run_id), self.assertRaises(b.Invalid):
                self.check()

    def test_push_bytes_hash_and_build_binding_still_checked(self):
        self.push += b' '
        with self.assertRaisesRegex(b.Invalid, 'PUSH_HASH_MISMATCH'):
            self.check()

    def test_wrong_scope_target_and_audience_fail_even_if_fixture_reapproved(self):
        for field, value in (('namespace', 'default'), ('previous_image', 'repo:latest')):
            old = copy.deepcopy(self.plan)
            self.plan['targets']['transaction-api'][field] = value
            self.reapprove_fixture()
            with self.subTest(field=field), self.assertRaises(b.Invalid):
                self.check()
            self.plan = old
        self.plan['kubernetes']['token_audience'] = 'other'
        self.reapprove_fixture()
        with self.assertRaisesRegex(b.Invalid, 'KUBERNETES_SCOPE_INVALID'):
            self.check()

    def test_unknown_target_or_kubernetes_field_cannot_declare_runtime_pass(self):
        self.plan['kubernetes']['worker_pull'] = 'PASS'
        self.reapprove_fixture()
        with self.assertRaisesRegex(b.Invalid, 'KUBERNETES_FIELDS_INVALID'):
            self.check()
        del self.plan['kubernetes']['worker_pull']
        self.plan['targets']['transaction-api']['replicas'] = 99
        self.reapprove_fixture()
        with self.assertRaisesRegex(b.Invalid, 'TARGET_FIELDS_INVALID'):
            self.check()

    def test_schema_template_approver_reference_and_rollback_are_strict(self):
        for field, value in (('schema', 'P1-CICD-deploy-binding-1'),
                             ('template_only', True), ('approved_by', 'x\nTOKEN'),
                             ('reference', 'https://github.com/other/repo/issues/1'),
                             ('allow_rollback', 'true')):
            old = copy.deepcopy(self.approval)
            self.approval[field] = value
            with self.subTest(field=field), self.assertRaises(b.Invalid):
                self.check()
            self.approval = old

    def test_duplicate_keys_nonfinite_malformed_and_oversized_bytes(self):
        for raw in (b'{"x":1,"x":2}', b'{"x":NaN}', b'[]', b'bad', b' ' * (1024 * 1024 + 1)):
            for slot in ('plan', 'approval'):
                plan_raw, approval_raw = encode(self.plan), encode(self.approval)
                if slot == 'plan':
                    plan_raw = raw
                else:
                    approval_raw = raw
                with self.subTest(slot=slot, raw_length=len(raw)), self.assertRaises(b.Invalid):
                    p.validate(plan_raw, approval_raw, self.push, b.sha(plan_raw),
                               b.sha(approval_raw), self.execution, NOW)

    def test_v1_still_requires_exact_known_run_id(self):
        legacy, push, execution = inputs()
        execution['run_id'] = 1000
        raw = encode(legacy)
        with self.assertRaisesRegex(b.Invalid, 'DEPLOY_EXECUTION_MISMATCH'):
            b.validate(raw, push, b.sha(raw), execution, NOW)

    def test_missing_fields_are_sanitized(self):
        del self.approval['expires_at']
        with self.assertRaises(b.Invalid) as caught:
            self.check()
        self.assertNotIsInstance(caught.exception, KeyError)


if __name__ == '__main__':
    unittest.main()
