"""Synthetic recovery contracts; never approve or execute a real recovery."""
import copy
import datetime as dt
import unittest

import deploy_binding as b
import deploy_plan as p
from test_deploy_binding import NOW, encode
from test_deploy_plan import plan_inputs


def recovery_inputs():
    prior, old_approval, push, old_execution = plan_inputs()
    old_approval.update(allow_rollback=True,
                        not_before=(NOW - dt.timedelta(hours=2)).isoformat(),
                        expires_at=(NOW - dt.timedelta(hours=1)).isoformat())
    plan, approval, _, execution = plan_inputs()
    plan.update(schema=p.RECOVERY_SCHEMA,
                request_id='22222222-2222-3333-4444-555555555555',
                recovery={
                    'origin_plan': encode(prior).decode(),
                    'origin_approval': encode(old_approval).decode(),
                    'origin_plan_sha256': b.sha(encode(prior)),
                    'origin_approval_sha256': b.sha(encode(old_approval)),
                    'origin_execution': old_execution,
                    'evidence_reference': 'https://github.com/321Team/fds-project-evidence/pull/1'})
    approval.update(plan_sha256=b.sha(encode(plan)), allow_rollback=True)
    execution['run_id'] = 1000
    return plan, approval, push, execution


class RecoveryPlanTests(unittest.TestCase):
    def setUp(self):
        self.plan, self.approval, self.push, self.execution = recovery_inputs()

    def check(self, reapprove=False):
        if reapprove:
            self.approval['plan_sha256'] = b.sha(encode(self.plan))
        return p.validate(encode(self.plan), encode(self.approval), self.push,
                          b.sha(encode(self.plan)), b.sha(encode(self.approval)),
                          self.execution, NOW)

    def origin_change(self, field, change):
        obj = b.read(self.plan['recovery'][field].encode())
        change(obj)
        raw = encode(obj)
        self.plan['recovery'][field] = raw.decode()
        self.plan['recovery'][field + '_sha256'] = b.sha(raw)

    def test_inverse_pair_keeps_original_build_push_and_does_not_assert_health(self):
        result = self.check()
        forward, approve, push, execution = plan_inputs()
        old = p.validate(encode(forward), encode(approve), push, b.sha(encode(forward)),
                         b.sha(encode(approve)), execution, NOW)
        self.assertEqual(result['operation'], 'RESTORE_PREVIOUS')
        self.assertEqual(result['build_workflow'], old['build_workflow'])
        self.assertEqual(result['push_execution'], old['push_execution'])
        for restored, original in zip(result['targets'], old['targets']):
            self.assertEqual(restored.uid, original.uid)
            self.assertEqual(restored.previous_image, original.image)
            self.assertEqual(restored.image, original.previous_image)
        self.assertEqual(result['recovery_basis']['historical_runtime'], 'NOT_VERIFIED')
        self.assertEqual(result['recovery_basis']['historical_approval_verification'], 'SUPPLIED_BYTES_ONLY')
        self.assertEqual(result['claim'], 'NOT_ACQUIRED')
        self.assertEqual(result['delivery_gate'], 'NOT_VERIFIED')

    def test_normal_schema_still_means_forward_only(self):
        self.plan['schema'] = 'P1-CICD-deploy-plan-2'
        with self.assertRaisesRegex(b.Invalid, 'PLAN_FIELDS_INVALID'):
            self.check(True)

    def test_switching_intent_without_new_approval_rejected(self):
        del self.plan['recovery']
        self.plan['schema'] = 'P1-CICD-deploy-plan-2'
        with self.assertRaisesRegex(b.Invalid, 'APPROVAL_PLAN_MISMATCH'):
            self.check()

    def test_old_expired_approval_is_basis_only_and_cannot_authorize_now(self):
        self.approval = b.read(self.plan['recovery']['origin_approval'].encode())
        self.approval['plan_sha256'] = b.sha(encode(self.plan))
        with self.assertRaisesRegex(b.Invalid, 'APPROVAL_WINDOW_INVALID'):
            self.check()

    def test_hash_mismatch_even_with_new_outer_approval(self):
        for field in ('origin_plan', 'origin_approval'):
            original = self.plan['recovery'][field]
            self.plan['recovery'][field] += ' '
            with self.subTest(field=field), self.assertRaisesRegex(b.Invalid, 'HASH_MISMATCH'):
                self.check(True)
            self.plan['recovery'][field] = original

    def test_source_rollback_must_have_been_in_scope(self):
        self.origin_change('origin_approval', lambda o: o.update(allow_rollback=False))
        with self.assertRaisesRegex(b.Invalid, 'ORIGIN_ROLLBACK_NOT_APPROVED'):
            self.check(True)

    def test_arbitrary_prior_image_uid_or_build_metadata_cannot_replace_source(self):
        for field in ('previous_image', 'uid'):
            original = copy.deepcopy(self.plan)
            self.plan['targets']['transaction-api'][field] = (
                '10.1.93.54/fds/transaction-api@sha256:' + '9' * 64
                if field == 'previous_image' else 'aaaaaaaa-1111-2222-3333-444444444444')
            with self.subTest(field=field), self.assertRaisesRegex(b.Invalid, 'ORIGIN_SCOPE_MISMATCH'):
                self.check(True)
            self.plan = original

    def test_origin_must_be_forward_plan_not_recursive(self):
        self.origin_change('origin_plan', lambda o: o.update(schema=p.RECOVERY_SCHEMA, recovery={}))
        with self.assertRaisesRegex(b.Invalid, 'ORIGIN_FORWARD_PLAN_REQUIRED'):
            self.check(True)

    def test_old_approval_still_binds_original_plan(self):
        self.origin_change('origin_plan', lambda o: o.update(request_id='aaaaaaaa-2222-3333-4444-555555555555'))
        with self.assertRaisesRegex(b.Invalid, 'APPROVAL_PLAN_MISMATCH'):
            self.check(True)

    def test_origin_request_run_and_retry_not_reusable(self):
        for slot in ('request', 'run', 'attempt'):
            plan, execution = copy.deepcopy(self.plan), copy.deepcopy(self.execution)
            if slot == 'request':
                self.plan['request_id'] = '11111111-2222-3333-4444-555555555555'
            elif slot == 'run':
                self.execution['run_id'] = self.plan['recovery']['origin_execution']['run_id']
            else:
                self.plan['recovery']['origin_execution']['attempt'] = 2
            with self.subTest(slot=slot), self.assertRaises(b.Invalid):
                self.check(True)
            self.plan, self.execution = plan, execution

    def test_origin_types_fields_and_references_fail_closed(self):
        for field, value in [('origin_plan', {}), ('origin_approval', '\ud800'),
                              ('origin_execution', {}), ('evidence_reference', 'https://example.org'),
                              ('extra', 'ignored')]:
            original = copy.deepcopy(self.plan)
            self.plan['recovery'][field] = value
            with self.subTest(field=field), self.assertRaises(b.Invalid):
                self.check(True)
            self.plan = original

    def test_origin_future_window_rejected(self):
        self.origin_change('origin_approval', lambda o: o.update(not_before=NOW.isoformat()))
        with self.assertRaisesRegex(b.Invalid, 'ORIGIN_TIME_INVALID'):
            self.check(True)

    def test_new_workflow_is_allowed_only_with_matching_fresh_approval(self):
        self.plan['workflow']['workflow_sha'] = 'c' * 40
        self.execution['workflow_sha'] = 'c' * 40
        with self.assertRaisesRegex(b.Invalid, 'APPROVAL_WORKFLOW_MISMATCH'):
            self.check(True)
        self.approval['workflow'] = copy.deepcopy(self.plan['workflow'])
        self.assertEqual(self.check()['workflow']['workflow_sha'], 'c' * 40)

    def test_push_tampering_and_claim_attempt_still_rejected(self):
        self.push += b' '
        with self.assertRaisesRegex(b.Invalid, 'PUSH_HASH_MISMATCH'):
            self.check()
        self.push = self.push[:-1]
        self.execution['attempt'] = 2
        with self.assertRaisesRegex(b.Invalid, 'RETRY_ATTEMPT_NOT_APPROVED'):
            self.check()


if __name__ == '__main__':
    unittest.main()
