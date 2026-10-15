"""Real POSIX file fault tests plus mock approval/Kube connection, no server."""
import copy
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import deploy_adapter as core
import deploy_approval as approval
import deploy_binding as b
import deploy_checkpoint as j
import deploy_claim
import deploy_execution as e
from test_deploy_adapter import Fake, fixtures
from test_deploy_approval import Source, approval_fixture
from test_deploy_binding import NOW, encode
from test_deploy_plan import plan_inputs
from test_recovery_plan import recovery_inputs
import deploy_plan


def receipt_for(claim):
    result = copy.deepcopy(core.Adapter(
        None, lambda _: True, lambda _: None, remaining_seconds=lambda: 3600,
        credential_remaining_seconds=lambda: 900).receipt)
    result.update({
        'schema': 'P1-CICD-deploy-execution-2', 'execution': claim.record['execution'],
        'plan_sha256': claim.record['plan_sha256'],
        'approval_sha256': claim.record['approval_sha256'],
        'push_receipt_sha256': 'c' * 64, 'authority_error_code': 'NONE',
        'DEPLOY_APPROVAL_REFERENCE': 'MATCHED_AT_LAST_SUCCESSFUL_LOOKUP',
        'claim': {'state': 'CLAIMED', 'sha256': claim.sha256,
                  'execution': claim.record['execution']},
        'approval_reference': 'https://github.com/321Team/infra-cicd/issues/11#issuecomment-123',
        'CREDENTIAL_METADATA': 'CALLER_SUPPLIED_NOT_VERIFIED_HERE',
        'SOURCE_APPROVAL': 'NOT_INDEPENDENTLY_VERIFIED',
    })
    return result


class ReceiptMetadataTests(unittest.TestCase):
    def setUp(self):
        plan, approved, push, execution = recovery_inputs()
        binding = deploy_plan.validate(encode(plan), encode(approved), push,
                                        b.sha(encode(plan)), b.sha(encode(approved)), execution, NOW)
        self.claim = SimpleNamespace(record={
            'execution': execution, 'plan_sha256': binding['plan_sha256'],
            'approval_sha256': binding['approval_sha256']}, sha256='a' * 64)
        self.receipt = receipt_for(self.claim)
        self.receipt.update(operation=binding['operation'], recovery_basis=binding['recovery_basis'])

    def test_recovery_and_forward_metadata_and_legacy_readback(self):
        self.assertEqual(j.receipt_checked(self.receipt, self.claim)['operation'], 'RESTORE_PREVIOUS')
        self.receipt.update(operation='DEPLOY_PUSHED', recovery_basis=None)
        self.assertEqual(j.receipt_checked(self.receipt, self.claim)['operation'], 'DEPLOY_PUSHED')
        del self.receipt['operation'], self.receipt['recovery_basis']
        self.assertNotIn('operation', j.receipt_checked(self.receipt, self.claim))

    def test_metadata_is_not_arbitrary_raw_data_or_runtime_success(self):
        for field, value in [('token', 'PRIVATE_TOKEN'), ('origin_plan', '{}'),
                              ('origin_plan_sha256', 'PRIVATE_TOKEN'),
                              ('historical_runtime', 'PASS'),
                              ('historical_approval_verification', 'VERIFIED'),
                              ('origin_execution', {}), ('evidence_reference', 'PRIVATE_TOKEN')]:
            bad = copy.deepcopy(self.receipt)
            bad['recovery_basis'][field] = value
            with self.subTest(field=field), self.assertRaises(j.CheckpointError):
                j.receipt_checked(bad, self.claim)

    def test_partial_or_contradictory_metadata_rejected(self):
        for change in (lambda r: r.pop('operation'), lambda r: r.pop('recovery_basis'),
                       lambda r: r.update(operation='UNKNOWN'),
                       lambda r: r.update(operation='DEPLOY_PUSHED'),
                       lambda r: r.update(recovery_basis=None)):
            bad = copy.deepcopy(self.receipt)
            change(bad)
            with self.assertRaises(j.CheckpointError):
                j.receipt_checked(bad, self.claim)

    def test_origin_retry_and_same_run_rejected(self):
        for key, value in [('attempt', 2), ('run_id', self.receipt['execution']['run_id'])]:
            bad = copy.deepcopy(self.receipt)
            bad['recovery_basis']['origin_execution'][key] = value
            with self.subTest(key=key), self.assertRaises(j.CheckpointError):
                j.receipt_checked(bad, self.claim)


@unittest.skipUnless(os.name == 'posix', 'Checkpoint needs the POSIX claim')
class JournalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='checkpoint-test-', dir=Path.cwd())
        self.root = Path(self.tmp.name)
        _, _, _, execution = plan_inputs()
        self.claim = deploy_claim.acquire(self.root, 'a' * 64, 'b' * 64, execution)
        self.writer = j.Journal(self.claim)
        self.receipt = receipt_for(self.claim)
        self.folder = self.root / self.writer.dirname

    def tearDown(self):
        self.writer.close()
        self.claim.close()
        self.tmp.cleanup()

    def test_file_and_directory_are_private_and_checkpoints_form_a_hash_chain(self):
        self.writer(self.receipt)
        self.receipt['ROLLOUT'] = 'IN_PROGRESS'
        self.writer(self.receipt)
        first, second = (self.folder / '0001.json').read_bytes(), (self.folder / '0002.json').read_bytes()
        obj = json.loads(second)
        self.assertEqual(obj['previous_sha256'], hashlib.sha256(first).hexdigest())
        self.assertEqual(obj['sequence'], 2)
        self.assertEqual(obj['claim_sha256'], self.claim.sha256)
        self.assertEqual(obj['receipt']['execution'], self.claim.record['execution'])
        self.assertEqual(obj['receipt']['DELIVERY_GATE'], 'NOT_VERIFIED')
        self.assertEqual(stat.S_IMODE(self.folder.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE((self.folder / '0001.json').stat().st_mode), 0o600)
        self.writer.check()

    def test_file_and_directory_fsync_complete_before_save_returns(self):
        seen, real = [], os.fsync
        def sync(fd):
            seen.append('directory' if stat.S_ISDIR(os.fstat(fd).st_mode) else 'file')
            real(fd)
        with patch.object(j.os, 'fsync', side_effect=sync):
            self.writer(self.receipt)
        self.assertEqual(seen, ['file', 'directory'])

    def test_invalid_binding_is_rejected_before_any_receipt_is_written(self):
        for field, value in (('execution', {}), ('plan_sha256', 'd' * 64),
                             ('approval_sha256', 'd' * 64), ('claim', {})):
            bad = copy.deepcopy(self.receipt)
            bad[field] = value
            with self.subTest(field=field), self.assertRaises(j.CheckpointError):
                j.receipt_checked(bad, self.claim)
        self.assertEqual(list(self.folder.iterdir()), [])

    def test_raw_secret_unknown_field_and_gate_upgrade_are_rejected(self):
        for field, value in (('token', 'PRIVATE_TOKEN'), ('DELIVERY_GATE', 'PASS'),
                             ('HEALTH_E2E', 'PASS'), ('error_code', 'PRIVATE_TOKEN'),
                             ('CREDENTIAL_METADATA', 'VERIFIED')):
            bad = copy.deepcopy(self.receipt)
            bad[field] = value
            with self.subTest(field=field), self.assertRaises(j.CheckpointError):
                j.receipt_checked(bad, self.claim)
        self.assertEqual(list(self.folder.iterdir()), [])

    def test_raw_deployment_fields_cannot_enter_image_summary(self):
        targets, objects = fixtures()
        bad = copy.deepcopy(self.receipt)
        summary = core.summary(objects['transaction-api'], targets[0])
        summary['spec'] = objects['transaction-api']['spec']
        bad['images'] = {'transaction-api': {
            'before': summary, 'deploy': 'PATCH_PENDING', 'rollback': 'NOT_EXECUTED'}}
        with self.assertRaises(j.CheckpointError):
            self.writer(bad)
        self.assertEqual(list(self.folder.iterdir()), [])

    def test_preexisting_sequence_is_preserved_and_writer_cannot_retry(self):
        existing = self.folder / '0001.json'
        existing.write_text('preserve')
        os.chmod(existing, 0o600)
        with self.assertRaises(j.CheckpointError):
            self.writer(self.receipt)
        self.assertEqual(existing.read_text(), 'preserve')
        existing.unlink()  # Test-only drift: a failed writer still cannot retry.
        with self.assertRaises(j.CheckpointError):
            self.writer(self.receipt)
        self.assertEqual(list(self.folder.iterdir()), [])

    def test_partial_write_is_preserved_and_no_second_sequence_is_created(self):
        real = os.write
        calls = []
        def partial(fd, raw):
            calls.append(True)
            if len(calls) == 1:
                return real(fd, raw[:20])
            raise OSError('PRIVATE_TOKEN')
        with patch.object(deploy_claim.os, 'write', side_effect=partial):
            with self.assertRaisesRegex(j.CheckpointError, '^CHECKPOINT_SAVE_FAILED$'):
                self.writer(self.receipt)
        self.assertEqual((self.folder / '0001.json').stat().st_size, 20)
        with self.assertRaises(j.CheckpointError):
            self.writer(self.receipt)
        self.assertFalse((self.folder / '0002.json').exists())

    def test_file_fsync_failure_preserves_new_bytes_but_never_reports_durable(self):
        with patch.object(j.os, 'fsync', side_effect=OSError('PRIVATE_TOKEN')):
            with self.assertRaisesRegex(j.CheckpointError, '^CHECKPOINT_SAVE_FAILED$'):
                self.writer(self.receipt)
        self.assertTrue((self.folder / '0001.json').exists())
        self.assertTrue(self.writer.failed)
        self.assertEqual(self.writer.records, [])

    def test_directory_fsync_failure_is_ambiguous_and_cannot_be_retried(self):
        real = os.fsync
        def fail_directory(fd):
            if stat.S_ISDIR(os.fstat(fd).st_mode):
                raise OSError('PRIVATE_TOKEN')
            return real(fd)
        with patch.object(j.os, 'fsync', side_effect=fail_directory):
            with self.assertRaises(j.CheckpointError):
                self.writer(self.receipt)
        self.assertTrue((self.folder / '0001.json').exists())
        self.assertEqual(self.writer.records, [])
        with self.assertRaises(j.CheckpointError):
            self.writer(self.receipt)

    def test_changed_deleted_or_replaced_previous_record_blocks_new_writes(self):
        self.writer(self.receipt)
        path = self.folder / '0001.json'
        raw = path.read_bytes()
        path.write_bytes(raw + b' ')
        with self.assertRaises(j.CheckpointError):
            self.writer(self.receipt)
        self.assertFalse((self.folder / '0002.json').exists())

    def test_equal_bytes_in_replaced_inode_are_rejected(self):
        self.writer(self.receipt)
        path = self.folder / '0001.json'
        raw = path.read_bytes()
        path.rename(self.folder / 'old.json')
        path.write_bytes(raw)
        os.chmod(path, 0o600)
        with self.assertRaises(j.CheckpointError):
            self.writer(self.receipt)
        self.assertFalse((self.folder / '0002.json').exists())

    def test_symlink_and_hardlink_sequence_do_not_follow_or_modify_external_file(self):
        external = self.root / 'untouched'
        external.write_text('preserve')
        os.chmod(external, 0o600)
        path = self.folder / '0001.json'
        path.symlink_to(external)
        with self.assertRaises(j.CheckpointError):
            self.writer(self.receipt)
        self.assertEqual(external.read_text(), 'preserve')

    def test_prior_record_hardlink_or_permissions_drift_blocks_further_writes(self):
        self.writer(self.receipt)
        path = self.folder / '0001.json'
        os.link(path, self.root / 'alias')
        with self.assertRaises(j.CheckpointError):
            self.writer(self.receipt)
        self.assertFalse((self.folder / '0002.json').exists())

    def test_checkpoint_directory_replacement_is_detected(self):
        self.folder.rename(self.root / 'old')
        self.folder.mkdir(mode=0o700)
        with self.assertRaises(j.CheckpointError):
            self.writer(self.receipt)
        self.assertEqual(list(self.folder.iterdir()), [])

    def test_claim_mutation_blocks_checkpoint_and_closed_claim_cannot_resume(self):
        claim_path = self.root / self.claim.filename
        claim_path.write_bytes(self.claim.raw + b' ')
        with self.assertRaises(j.CheckpointError):
            self.writer(self.receipt)
        self.claim.close()
        with self.assertRaises(j.CheckpointError):
            j.Journal(self.claim)

    def test_new_writer_cannot_reopen_existing_journal_even_with_same_claim(self):
        with self.assertRaisesRegex(j.CheckpointError, '^CHECKPOINT_OPEN_FAILED$'):
            j.Journal(self.claim)
        self.assertEqual(list(self.folder.iterdir()), [])
        self.assertTrue((self.root / self.claim.filename).exists())

    def test_closed_writer_cannot_write_and_does_not_delete_evidence(self):
        self.writer(self.receipt)
        self.writer.close()
        with self.assertRaises(j.CheckpointError):
            self.writer(self.receipt)
        self.assertTrue((self.folder / '0001.json').exists())


@unittest.skipUnless(os.name == 'posix', 'Connection uses the POSIX claim')
class ConnectionCheckpointTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='checkpoint-connection-', dir=Path.cwd())
        self.root = Path(self.tmp.name)
        self.plan, _, self.push, self.context = plan_inputs()
        self.raw, remote = approval_fixture()
        self.verifier = approval.Verifier(Source(remote))
        _, objects = fixtures()
        self.client = Fake(objects)
        self.execution = None
        self.checkpoint = j.BoundCheckpoint(lambda: self.execution.claim)
        self.execution = e.Execution(
            self.client, self.verifier, self.checkpoint, self.root,
            credential_remaining_seconds=lambda: 900, now=lambda: NOW,
            clock=lambda: 0, timeout=3, interval=1)

    def tearDown(self):
        self.checkpoint.close()
        self.tmp.cleanup()

    def run_execution(self):
        plan = encode(self.plan)
        return self.execution.run(plan, self.raw, self.push, b.sha(plan),
                                  b.sha(self.raw), self.context)

    def test_pending_checkpoint_is_durable_before_each_patch_and_all_states_are_saved(self):
        observations = []
        def inspect_checkpoint(_, name):
            journal = self.checkpoint.journal
            journal.check()
            last = json.loads(journal.records[-1][1])
            observations.append(last['receipt']['images'][name]['deploy'])
        self.client.patch_hook = inspect_checkpoint
        result = self.run_execution()
        self.assertEqual(result['ROLLOUT'], 'READY')
        self.assertEqual(observations, ['PATCH_PENDING', 'PATCH_PENDING'])
        folder = self.root / self.checkpoint.journal.dirname
        records = [json.loads(p.read_bytes()) for p in sorted(folder.glob('*.json'))]
        self.assertEqual(records[-1]['receipt'], result)
        self.assertNotIn('never-in-receipt', json.dumps(records))
        self.assertNotIn('PRIVATE_TOKEN', json.dumps(records))
        self.assertEqual(result['DELIVERY_GATE'], 'NOT_VERIFIED')

    def test_checkpoint_failure_before_first_patch_consumes_claim_but_sends_no_patch(self):
        with patch.object(j.Journal, '__call__', side_effect=j.CheckpointError('FAILURE')):
            result = self.run_execution()
        self.assertEqual(self.client.patches, [])
        self.assertEqual(result['ROLLBACK'], 'MANUAL_RECONCILIATION_REQUIRED')
        self.assertTrue((self.root / (b.sha(encode(self.plan)) + '.claim.json')).exists())
        self.assertTrue(self.checkpoint.failed)

    def test_applied_checkpoint_failure_stops_second_patch_and_automatic_rollback(self):
        original = j.Journal.__call__
        def fail_applied(journal, receipt):
            if any(item['deploy'] == 'APPLIED' for item in receipt['images'].values()):
                raise j.CheckpointError('PRIVATE_TOKEN')
            return original(journal, receipt)
        with patch.object(j.Journal, '__call__', fail_applied):
            result = self.run_execution()
        self.assertEqual(len(self.client.patches), 1)
        self.assertEqual(result['ROLLBACK'], 'MANUAL_RECONCILIATION_REQUIRED')
        self.assertEqual(result['error_code'], 'CHECKPOINT_FAILED')
        self.assertNotIn('PRIVATE_TOKEN', json.dumps(result))
        last = json.loads(self.checkpoint.journal.records[-1][1])
        self.assertEqual(last['receipt']['images']['transaction-api']['deploy'], 'PATCH_PENDING')

    def test_authority_failure_before_claim_does_not_create_checkpoint_directory(self):
        self.verifier.source.obj['user']['login'] = 'other'
        result = self.run_execution()
        self.assertEqual(result['ROLLOUT'], 'NOT_EXECUTED')
        self.assertEqual(list(self.root.iterdir()), [])
        self.assertIsNone(self.checkpoint.journal)
