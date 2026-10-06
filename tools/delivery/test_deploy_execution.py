"""MOCK Kubernetes/approval and POSIX claim integration, never server execution."""
import copy
import datetime as dt
import json
import os
from pathlib import Path
import tempfile
import unittest

import deploy_adapter as core
import deploy_approval as approval
import deploy_binding as b
import deploy_execution as e
from test_deploy_adapter import Fake, fixtures
from test_deploy_approval import Source, approval_fixture
from test_deploy_binding import NOW, encode
from test_deploy_plan import plan_inputs


@unittest.skipUnless(os.name == 'posix', 'Single Runner claim is POSIX only')
class ExecutionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='execution-test-', dir=Path.cwd())
        self.root = Path(self.tmp.name)
        self.plan, _, self.push, self.execution = plan_inputs()
        self.approval_raw, remote = approval_fixture()
        self.source = Source(remote)
        self.verifier = approval.Verifier(self.source)
        _, objects = fixtures()
        self.client = Fake(objects)
        self.wall, self.mono, self.credential = NOW, 0, 3600
        self.receipts = []

    def tearDown(self):
        self.tmp.cleanup()

    def sleep(self, amount):
        self.mono += amount
        self.wall += dt.timedelta(seconds=amount)

    def helper(self, **kwargs):
        return e.Execution(
            self.client, kwargs.pop('verifier', self.verifier),
            kwargs.pop('checkpoint', self.receipts.append), self.root,
            credential_remaining_seconds=kwargs.pop('credential_provider', lambda: self.credential),
            now=lambda: self.wall, clock=lambda: self.mono, sleep=self.sleep,
            timeout=3, interval=1, **kwargs)

    def run_execution(self, **kwargs):
        helper = kwargs.pop('helper', None) or self.helper(**kwargs)
        raw = encode(self.plan)
        return helper.run(raw, self.approval_raw, self.push, b.sha(raw),
                          b.sha(self.approval_raw), self.execution)

    def claim_path(self):
        return self.root / (b.sha(encode(self.plan)) + '.claim.json')

    def change_approval(self, **fields):
        obj = json.loads(self.approval_raw)
        obj.update(fields)
        self.approval_raw = encode(obj)
        self.source.obj['body'] = self.approval_raw.decode()

    def test_two_images_use_claim_and_fresh_approval_for_every_operation(self):
        result = self.run_execution()
        self.assertEqual(result['ROLLOUT'], 'READY')
        self.assertEqual(len(self.client.patches), 2)
        self.assertEqual(len(self.source.calls), 1 + self.client.gets + len(self.client.patches))
        self.assertEqual(result['execution'], self.execution)
        self.assertEqual(result['claim']['execution'], self.execution)
        self.assertEqual(result['claim']['state'], 'CLAIMED')
        self.assertEqual(result['SOURCE_APPROVAL'], 'NOT_INDEPENDENTLY_VERIFIED')
        self.assertEqual(result['DELIVERY_GATE'], 'NOT_VERIFIED')
        self.assertEqual(result['CREDENTIAL_METADATA'], 'CALLER_SUPPLIED_NOT_VERIFIED_HERE')
        self.assertTrue(self.claim_path().exists())
        self.assertNotIn('never-in-receipt', json.dumps(self.receipts))

    def test_short_token_or_approval_window_does_not_consume_claim(self):
        self.credential = 35
        result = self.run_execution()
        self.assertEqual(result['error_code'], 'CREDENTIAL_WINDOW_TOO_SHORT')
        self.assertEqual(list(self.root.iterdir()), [])
        self.credential = 3600
        self.change_approval(expires_at=(NOW + dt.timedelta(seconds=35)).isoformat())
        result = self.run_execution()
        self.assertEqual(result['error_code'], 'AUTH_WINDOW_TOO_SHORT')
        self.assertEqual(list(self.root.iterdir()), [])
        self.assertEqual(self.client.patches, [])

    def test_invalid_actual_token_metadata_blocks_without_claim(self):
        for value in (None, True, '3600', float('nan'), float('inf')):
            self.credential = value
            with self.subTest(value=value):
                result = self.run_execution()
                self.assertEqual(result['error_code'], 'CREDENTIAL_WINDOW_INVALID')
                self.assertEqual(list(self.root.iterdir()), [])

    def test_reference_not_verified_blocks_before_any_claim_or_kubernetes_read(self):
        self.source.obj['user']['login'] = 'other'
        result = self.run_execution()
        self.assertEqual(result['error_code'], 'APPROVAL_AUTHOR_MISMATCH')
        self.assertEqual(list(self.root.iterdir()), [])
        self.assertEqual(self.client.gets, 0)

    def test_time_spent_looking_up_approval_is_rechecked_before_claim(self):
        def delay():
            self.mono += 3570
            self.wall += dt.timedelta(seconds=3570)
        self.source.hook = delay
        result = self.run_execution()
        self.assertEqual(result['error_code'], 'AUTH_WINDOW_TOO_SHORT')
        self.assertEqual(list(self.root.iterdir()), [])

    def test_unready_preflight_keeps_claim_consumed(self):
        self.client.objects['fds-engine']['status']['readyReplicas'] = 2
        result = self.run_execution()
        self.assertEqual(result['error_code'], 'BASELINE_NOT_READY')
        self.assertTrue(self.claim_path().exists())
        self.assertEqual(self.client.patches, [])

    def test_new_run_cannot_reuse_consumed_plan(self):
        self.run_execution()
        self.execution['run_id'] = 1000
        result = self.run_execution()
        self.assertEqual(result['error_code'], 'PLAN_ALREADY_CLAIMED')
        self.assertEqual(len(self.client.patches), 2)

    def test_claim_drift_during_preflight_blocks_next_get_and_all_patches(self):
        def mutate_claim(client, name):
            self.claim_path().write_bytes(b'tampered')
        self.client.get_hook = mutate_claim
        result = self.run_execution()
        self.assertEqual(result['error_code'], 'AUTHORIZATION_DENIED')
        self.assertEqual(result['authority_error_code'], 'CLAIM_BYTES_CHANGED')
        self.assertEqual(self.client.gets, 1)
        self.assertEqual(self.client.patches, [])

    def test_claim_removed_after_first_patch_never_triggers_rollback(self):
        self.change_approval(allow_rollback=True)
        self.client.patch_hook = lambda client, name: self.claim_path().unlink()
        result = self.run_execution()
        self.assertEqual(result['error_code'], 'AUTHORIZATION_DENIED')
        self.assertEqual(result['ROLLBACK'], 'MANUAL_RECONCILIATION_REQUIRED')
        self.assertEqual(len(self.client.patches), 1)

    def test_revoked_comment_after_first_patch_stops_all_further_writes(self):
        self.change_approval(allow_rollback=True)
        self.client.patch_hook = lambda client, name: self.source.obj.update(body='REVOKED')
        result = self.run_execution()
        self.assertEqual(result['error_code'], 'AUTHORIZATION_DENIED')
        self.assertEqual(result['authority_error_code'], 'APPROVAL_REFERENCE_CHANGED')
        self.assertEqual(result['ROLLBACK'], 'MANUAL_RECONCILIATION_REQUIRED')
        self.assertEqual(len(self.client.patches), 1)

    def test_pending_checkpoint_delay_rechecks_claim_before_patch(self):
        def checkpoint(receipt):
            self.receipts.append(receipt)
            if any(item['deploy'] == 'PATCH_PENDING' for item in receipt['images'].values()):
                self.claim_path().write_bytes(b'tampered')
        result = self.run_execution(checkpoint=checkpoint)
        self.assertEqual(result['images']['transaction-api']['deploy'], 'NOT_SENT_AUTH_DENIED')
        self.assertEqual(result['authority_error_code'], 'CLAIM_BYTES_CHANGED')
        self.assertEqual(self.client.patches, [])

    def test_token_expiry_while_pending_does_not_send_patch(self):
        def checkpoint(receipt):
            self.receipts.append(receipt)
            if any(item['deploy'] == 'PATCH_PENDING' for item in receipt['images'].values()):
                self.credential = 0
        result = self.run_execution(checkpoint=checkpoint)
        self.assertEqual(result['error_code'], 'AUTHORIZATION_DENIED')
        self.assertEqual(result['authority_error_code'], 'CREDENTIAL_EXPIRED')
        self.assertEqual(self.client.patches, [])
        self.assertTrue(self.claim_path().exists())

    def test_lookup_failure_after_patch_is_sanitized_and_stops_without_rollback(self):
        self.change_approval(allow_rollback=True)
        def fail_after_patch(client, name):
            self.source.hook = lambda: (_ for _ in ()).throw(PermissionError('PRIVATE_TOKEN'))
        self.client.patch_hook = fail_after_patch
        result = self.run_execution()
        self.assertEqual(result['authority_error_code'], 'APPROVAL_LOOKUP_FAILED')
        self.assertEqual(len(self.client.patches), 1)
        self.assertNotIn('PRIVATE_TOKEN', json.dumps(result))

    def test_separately_approved_rollback_still_checks_current_comment_and_claim(self):
        self.change_approval(allow_rollback=True)
        def reject_second(client, name):
            if len(client.patches) == 2:
                raise core.Rejected()
        self.client.patch_hook = reject_second
        result = self.run_execution()
        self.assertEqual(result['ROLLBACK'], 'READY_HEALTH_NOT_VERIFIED')
        self.assertEqual(len(self.client.patches), 3)
        self.assertGreater(len(self.source.calls), 5)

    def test_revocation_before_rollback_blocks_the_recovery_patch(self):
        self.change_approval(allow_rollback=True)
        def reject_second(client, name):
            if len(client.patches) == 2:
                self.source.obj['body'] = 'REVOKED'
                raise core.Rejected()
        self.client.patch_hook = reject_second
        result = self.run_execution()
        self.assertEqual(result['ROLLBACK'], 'MANUAL_RECONCILIATION_REQUIRED')
        self.assertEqual(result['authority_error_code'], 'APPROVAL_REFERENCE_CHANGED')
        self.assertEqual(len(self.client.patches), 2)

    def test_checkpoint_failure_before_write_does_not_send_any_patch(self):
        def fail(_):
            raise OSError('PRIVATE_TOKEN')
        result = self.run_execution(checkpoint=fail)
        self.assertEqual(result['error_code'], 'CHECKPOINT_FAILED')
        self.assertEqual(result['ROLLBACK'], 'MANUAL_RECONCILIATION_REQUIRED')
        self.assertEqual(self.client.patches, [])
        self.assertTrue(self.claim_path().exists())
        self.assertNotIn('PRIVATE_TOKEN', json.dumps(result))

    def test_checkpoint_failure_after_patch_does_not_allow_second_patch(self):
        def fail(receipt):
            if self.client.patches:
                raise OSError('PRIVATE_TOKEN')
            self.receipts.append(receipt)
        result = self.run_execution(checkpoint=fail)
        self.assertEqual(result['error_code'], 'CHECKPOINT_FAILED')
        self.assertEqual(result['ROLLBACK'], 'MANUAL_RECONCILIATION_REQUIRED')
        self.assertEqual(len(self.client.patches), 1)

    def test_wall_clock_rollback_cannot_extend_approval_window(self):
        def adjust_clock(client, name):
            self.wall -= dt.timedelta(hours=1)
            self.mono = 3601
        self.client.get_hook = adjust_clock
        result = self.run_execution()
        self.assertEqual(result['authority_error_code'], 'APPROVAL_EXPIRED')
        self.assertEqual(self.client.patches, [])

    def test_untrusted_exception_code_or_invalid_hash_is_never_serialized(self):
        class InvalidVerifier:
            def verify(self, *_):
                raise approval.ApprovalError('PRIVATE_TOKEN')
        result = self.run_execution(verifier=InvalidVerifier())
        self.assertEqual(result['error_code'], 'EXECUTION_HELPER_FAILED')
        self.assertNotIn('PRIVATE_TOKEN', json.dumps(result))
        raw = encode(self.plan)
        result = self.helper().run(raw, self.approval_raw, self.push, 'PRIVATE_TOKEN',
                                   b.sha(self.approval_raw), self.execution)
        self.assertEqual(result['error_code'], 'INPUT_BINDING_REJECTED')
        self.assertIsNone(result['plan_sha256'])
        self.assertNotIn('PRIVATE_TOKEN', json.dumps(result))

    def test_execution_instance_is_single_use_even_if_stopped_before_claim(self):
        self.credential = 0
        helper = self.helper()
        self.run_execution(helper=helper)
        with self.assertRaisesRegex(core.Stop, 'EXECUTION_ALREADY_USED'):
            self.run_execution(helper=helper)


if __name__ == '__main__':
    unittest.main()
