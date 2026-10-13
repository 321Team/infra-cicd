"""Real local filesystem tests on POSIX; MOCK/API calls only, no cloud runtime."""
import copy
import json
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import delivery_adapter as core
import delivery_store as store
from test_delivery_adapter import Harness, fixture


def prepared(binding):
    h = Harness()
    receipt = copy.deepcopy(h.adapter.receipt)
    receipt.update(status='PREPARED', binding_key=binding.key)
    return receipt


class ReceiptTests(unittest.TestCase):
    def test_extra_secret_or_arbitrary_error_is_rejected(self):
        b = fixture()[0]
        for key, value in [('token', 'sensitive'), ('error_code', 'ARBITRARY_SECRET'),
                           ('delivery_gate', 'PASS'), ('binding_key', '0' * 64)]:
            r = prepared(b)
            r[key] = value
            with self.assertRaises(store.StoreError):
                store.receipt_checked(r, b.key)

    def test_ack_order_duplicates_and_premature_rollout_rejected(self):
        b = fixture()[0]
        for change in ({'acknowledged': [store.IDENTITIES[1]]},
                       {'acknowledged': [store.IDENTITIES[0]] * 2},
                       {'rollout': ['transaction-api']}, {'status': 'ROLLOUT_OBSERVED'}):
            r = prepared(b)
            r.update(change)
            with self.assertRaises(store.StoreError):
                store.receipt_checked(r, b.key)

    def test_terminal_without_full_sequence_cannot_start_journal(self):
        r = prepared(fixture()[0])
        r.update(status='ROLLOUT_OBSERVED', acknowledged=list(store.IDENTITIES),
                 rollout=list(core.SERVICES))
        with self.assertRaisesRegex(store.StoreError, 'JOURNAL_INITIAL_INVALID'):
            store.transition_checked(None, r)

    def test_ack_without_pending_or_receipt_regression_is_rejected(self):
        b = fixture()[0]
        before, after = prepared(b), prepared(b)
        after.update(status='WRITE_ACKNOWLEDGED', acknowledged=[store.IDENTITIES[0]])
        with self.assertRaises(store.StoreError):
            store.transition_checked(before, after)
        with self.assertRaisesRegex(store.StoreError, 'RECEIPT_REGRESSION'):
            store.transition_checked(after, before)

    @unittest.skipIf(os.name == 'posix', 'Windows unsupported-platform boundary only')
    def test_non_posix_refuses_before_touching_path(self):
        with patch.object(store, 'root_open') as opened:
            with self.assertRaisesRegex(store.StoreError, 'PLATFORM_UNSUPPORTED'):
                store.acquire('must-not-be-created', fixture()[0])
            opened.assert_not_called()


@unittest.skipUnless(os.name == 'posix', 'POSIX filesystem semantics run in Linux CI')
class StoreTests(unittest.TestCase):
    def setUp(self):
        # /tmp is deliberately not supported: no writable parents in a trusted root.
        self.tmp = tempfile.TemporaryDirectory(prefix='p2-store-test-', dir=Path.cwd())
        self.root = Path(self.tmp.name)
        os.chmod(self.root, 0o700)
        self.binding = fixture()[0]
        self.handles = []

    def tearDown(self):
        for obj in reversed(self.handles):
            obj.close()
        self.tmp.cleanup()

    def claim(self, binding=None):
        claim = store.acquire(self.root, binding or self.binding)
        self.handles.append(claim)
        return claim

    def journal(self, claim):
        journal = store.Journal(claim)
        self.handles.append(journal)
        return journal

    def test_live_mutex_and_interrupted_fence_block_other_runs(self):
        claim = self.claim()
        another = replace(self.binding, run_id='another-run')
        with self.assertRaisesRegex(store.StoreError, 'TARGET_BUSY'):
            self.claim(another)
        claim.close()
        with self.assertRaisesRegex(store.StoreError, 'TARGET_RECONCILIATION_REQUIRED'):
            self.claim(another)

    def test_api_alias_does_not_bypass_same_target_fence(self):
        claim = self.claim()
        claim.close()
        another = replace(self.binding, run_id='alias-run', api_origin='https://other.rosa.example.com:6443')
        with self.assertRaisesRegex(store.StoreError, 'TARGET_RECONCILIATION_REQUIRED'):
            self.claim(another)

    def test_full_core_journal_normal_completion_and_consumed_run(self):
        h = Harness()
        claim = self.claim(h.binding)
        journal = self.journal(claim)
        h.adapter.checkpoint = journal
        def authorize(b):
            claim.check()
            journal.check()
            return h.lease
        h.authorizer = authorize
        result = h.run()
        self.assertEqual(result['status'], 'ROLLOUT_OBSERVED', result)
        self.assertEqual(len(journal.records), 16)
        previous = None
        for index, (name, raw, _) in enumerate(journal.records, 1):
            obj = json.loads(raw)
            self.assertEqual(obj['sequence'], index)
            self.assertEqual(obj['previous_sha256'], previous)
            self.assertEqual((self.root / journal.dirname / name).read_bytes(), raw)
            previous = core.digest(raw)
        claim.complete(journal)
        self.assertFalse((self.root / claim.active_name).exists())
        self.assertTrue((self.root / (h.binding.key + '.complete.json')).exists())
        claim.close()
        with self.assertRaisesRegex(store.StoreError, 'RUN_ALREADY_CLAIMED'):
            self.claim(replace(h.binding, rendered_sha256='0' * 64))
        self.claim(replace(h.binding, run_id='approved-next-run'))

    def test_unknown_write_preserves_fence_and_disallows_complete(self):
        h = Harness()
        claim = self.claim(h.binding)
        journal = self.journal(claim)
        h.adapter.checkpoint = journal
        h.fail_at = 1
        result = h.run()
        self.assertEqual(result['error_code'], 'WRITE_OUTCOME_UNKNOWN', result)
        self.assertEqual(journal.latest['status'], 'STOPPED')
        self.assertIsNotNone(journal.latest['pending'])
        with self.assertRaisesRegex(store.StoreError, 'RECONCILIATION_REQUIRED'):
            claim.complete(journal)
        claim.close()
        with self.assertRaisesRegex(store.StoreError, 'TARGET_RECONCILIATION_REQUIRED'):
            self.claim(replace(h.binding, run_id='retry'))

    def test_partial_checkpoint_is_retained_and_writer_never_retries(self):
        claim = self.claim()
        journal = self.journal(claim)
        original = store.os.write
        calls = 0
        def partial(fd, raw):
            nonlocal calls
            calls += 1
            if calls == 1:
                return original(fd, raw[:10])
            raise OSError('SENSITIVE_FILESYSTEM_DETAIL')
        with patch.object(store.os, 'write', partial):
            with self.assertRaisesRegex(store.StoreError, '^JOURNAL_SAVE_FAILED$'):
                journal(prepared(self.binding))
        record = self.root / journal.dirname / '0001.json'
        self.assertEqual(record.stat().st_size, 10)
        before = record.read_bytes()
        with self.assertRaises(store.StoreError):
            journal(prepared(self.binding))
        self.assertEqual(record.read_bytes(), before)

    def test_fsync_failure_prevents_first_apply(self):
        h = Harness()
        claim = self.claim(h.binding)
        journal = self.journal(claim)
        h.adapter.checkpoint = journal
        with patch.object(store.os, 'fsync', side_effect=OSError('secret')):
            result = h.run()
        self.assertEqual(result['error_code'], 'CHECKPOINT_FAILED')
        self.assertEqual(h.requests, [])
        self.assertTrue((self.root / claim.active_name).exists())

    def test_record_modification_or_unexpected_file_blocks_future_records(self):
        for change in ('modify', 'extra'):
            with self.subTest(change=change):
                binding = replace(self.binding, cluster_id='test-' + change)
                claim = self.claim(binding)
                journal = self.journal(claim)
                journal(prepared(binding))
                folder = self.root / journal.dirname
                if change == 'modify':
                    (folder / '0001.json').write_bytes(b'changed')
                else:
                    (folder / 'foreign.json').write_bytes(b'{}')
                with self.assertRaises(store.StoreError):
                    journal.check()
                with self.assertRaises(store.StoreError):
                    journal(prepared(binding))

    def test_claim_or_lock_inode_replacement_is_detected(self):
        claim = self.claim()
        target = self.root / claim.lock_name
        target.unlink()
        target.write_bytes(b'')
        target.chmod(0o600)
        with self.assertRaisesRegex(store.StoreError, 'LOCK_CHANGED'):
            claim.check()

    def test_claim_bytes_change_is_detected(self):
        claim = self.claim()
        path = self.root / claim.files[0][0]
        path.write_bytes(b'{}')
        with self.assertRaisesRegex(store.StoreError, 'FILE_BYTES_CHANGED'):
            claim.check()

    def test_root_replacement_is_detected(self):
        claim = self.claim()
        backup = self.root.with_name(self.root.name + '-saved')
        self.root.rename(backup)
        self.root.mkdir(mode=0o700)
        try:
            with self.assertRaisesRegex(store.StoreError, 'ROOT_CHANGED'):
                claim.check()
        finally:
            self.root.rmdir()
            backup.rename(self.root)

    def test_symlink_and_writable_root_are_rejected(self):
        link = self.root / 'link'
        link.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(store.StoreError):
            store.acquire(link, self.binding)
        self.root.chmod(0o777)
        with self.assertRaisesRegex(store.StoreError, 'ROOT_PARENT_UNSAFE'):
            self.claim()
        self.root.chmod(0o700)

    def test_hardlinked_claim_is_rejected(self):
        claim = self.claim()
        os.link(self.root / claim.files[0][0], self.root / 'alias')
        with self.assertRaisesRegex(store.StoreError, 'FILE_UNSAFE'):
            claim.check()

    def test_journal_cannot_open_again_or_overwrite_consumed_records(self):
        claim = self.claim()
        journal = self.journal(claim)
        journal(prepared(self.binding))
        with self.assertRaisesRegex(store.StoreError, 'JOURNAL_OPEN_FAILED'):
            self.journal(claim)
        self.assertEqual(len(list((self.root / journal.dirname).glob('*.json'))), 1)

    def test_malformed_receipt_never_gets_written(self):
        claim = self.claim()
        journal = self.journal(claim)
        bad = prepared(self.binding)
        bad['token'] = 'SENSITIVE'
        with self.assertRaisesRegex(store.StoreError, 'JOURNAL_SAVE_FAILED'):
            journal(bad)
        self.assertEqual(list((self.root / journal.dirname).iterdir()), [])


if __name__ == '__main__':
    unittest.main()
