"""Offline recovery diagnostics; no cloud, credentials or live network."""
import copy
from dataclasses import asdict, replace
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import delivery_adapter as core
import delivery_recovery as recovery
import delivery_store as store
from test_delivery_adapter import Harness, fixture
from test_delivery_store import prepared


def record(receipt, sequence=1, previous=None):
    return core.encoded({'schema': 'P2-CICD-journal-1', 'sequence': sequence,
                         'previous_sha256': previous, 'binding_key': receipt['binding_key'],
                         'receipt': receipt})


def successful_records():
    h = Harness()
    records = []
    h.adapter.checkpoint = lambda r: records.append(record(
        r, len(records) + 1, core.digest(records[-1]) if records else None))
    assert h.run()['status'] == 'ROLLOUT_OBSERVED'
    return h.binding, records


class RecoveryContentTests(unittest.TestCase):
    def test_binding_roundtrip_and_unknown_fields_rejected(self):
        b = fixture()[0]
        self.assertEqual(recovery.binding_from_bytes(core.encoded(asdict(b))), b)
        for data in ({**asdict(b), 'token': 'PRIVATE'}, {**asdict(b), 'api_origin': 'http://p1.example.com'}):
            with self.assertRaises(recovery.RecoveryError):
                recovery.binding_from_bytes(core.encoded(data))

    def test_duplicate_json_nan_and_size_rejected(self):
        for raw in (b'{"a":1,"a":2}', b'{"a":NaN}', b' ' * 16385, b'{'):
            with self.assertRaises(recovery.RecoveryError):
                recovery.decode(raw)

    def test_replay_rejects_altered_chain_sequence_or_receipt(self):
        b, originals = successful_records()
        for change in ('previous_sha256', 'sequence', 'binding_key', 'receipt', 'extra'):
            values = list(originals)
            item = json.loads(values[1])
            if change == 'receipt':
                item['receipt']['delivery_gate'] = 'PASS'
            else:
                item[change] = True if change == 'sequence' else 'tampered'
            values[1] = core.encoded(item)
            with self.assertRaises(recovery.RecoveryError):
                recovery.replay(values, b.key)

    def test_replay_rejects_terminal_restart_and_noncanonical_bytes(self):
        b, records = successful_records()
        with self.assertRaises(recovery.RecoveryError):
            recovery.replay(records + [record(prepared(b), 17, core.digest(records[-1]))], b.key)
        with self.assertRaises(recovery.RecoveryError):
            recovery.replay([records[0] + b'\n'], b.key)

    def test_pending_is_unknown_even_when_not_acknowledged(self):
        b = fixture()[0]
        first = record(prepared(b))
        receipt = prepared(b)
        receipt.update(status='WRITE_PENDING', pending=store.IDENTITIES[0])
        pending = record(receipt, 2, core.digest(first))
        result = recovery.decision(b, [first, pending], active=True, complete=None)
        self.assertEqual(result['state'], 'WRITE_OUTCOME_UNCONFIRMED')
        self.assertFalse(result['automatic_resume_allowed'])
        self.assertEqual(result['latest_receipt']['acknowledged'], [])

    def test_no_intent_and_missing_fence_do_not_authorize_retry(self):
        b = fixture()[0]
        self.assertEqual(recovery.decision(b, [], active=True, complete=None)['state'], 'NO_DURABLE_INTENT')
        self.assertEqual(recovery.decision(b, [], active=False, complete=None)['state'], 'INCOMPLETE_FENCE')

    def test_complete_is_diagnostic_and_binds_last_checkpoint(self):
        b, records = successful_records()
        marker = core.encoded({'schema': 'P2-CICD-complete-1', 'binding_key': b.key,
                               'last_checkpoint_sha256': core.digest(records[-1])})
        r = recovery.decision(b, records, active=False, complete=marker)
        self.assertEqual(r['state'], 'COMPLETED_RECORD')
        self.assertEqual(r['delivery_gate'], 'NOT_VERIFIED')
        self.assertFalse(r['automatic_resume_allowed'])
        self.assertEqual(recovery.decision(b, records, active=True, complete=marker)['state'],
                         'TERMINAL_FENCE_RETAINED')
        with self.assertRaisesRegex(recovery.RecoveryError, 'COMPLETE_INVALID'):
            recovery.decision(b, records[:-1], active=False, complete=marker)

    def test_cli_sanitizes_filesystem_errors(self):
        output = io.StringIO()
        with patch.object(os, 'open', side_effect=OSError('SECRET_DETAIL')), patch('sys.stdout', output):
            self.assertEqual(recovery.main(['--root', 'unused', '--binding', 'unused']), 2)
        self.assertNotIn('SECRET_DETAIL', output.getvalue())

    @unittest.skipIf(os.name == 'posix', 'Windows boundary only')
    def test_windows_refuses_store_before_access(self):
        with patch.object(store, 'root_open') as opened:
            with self.assertRaisesRegex(recovery.RecoveryError, 'PLATFORM_UNSUPPORTED'):
                recovery.inspect('unused', fixture()[0])
            opened.assert_not_called()


@unittest.skipUnless(os.name == 'posix', 'POSIX store readback runs in Linux CI')
class RecoveryStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='p2-recovery-test-', dir=Path.cwd())
        self.root = Path(self.tmp.name)
        self.root.chmod(0o700)
        self.h = Harness()
        self.claim = store.acquire(self.root, self.h.binding)
        self.journal = store.Journal(self.claim)
        self.h.adapter.checkpoint = self.journal
        self.addCleanup(self.cleanup)

    def cleanup(self):
        self.journal.close()
        self.claim.close()
        self.tmp.cleanup()

    def snapshot(self):
        return {str(p.relative_to(self.root)): (p.read_bytes(), p.stat().st_mode)
                for p in self.root.rglob('*') if p.is_file()}

    def stop_writer(self):
        self.journal.close()
        self.claim.close()

    def test_live_writer_refused_without_mutation(self):
        before = self.snapshot()
        with self.assertRaisesRegex(recovery.RecoveryError, 'TARGET_BUSY'):
            recovery.inspect(self.root, self.h.binding)
        self.assertEqual(self.snapshot(), before)

    def test_crash_pending_reconstructed_and_original_fence_preserved(self):
        first = prepared(self.h.binding)
        self.journal(first)
        pending = copy.deepcopy(first)
        pending.update(status='WRITE_PENDING', pending=store.IDENTITIES[0])
        self.journal(pending)
        self.stop_writer()
        before = self.snapshot()
        with patch('socket.socket', side_effect=AssertionError('NO_NETWORK')):
            result = recovery.inspect(self.root, self.h.binding)
        self.assertEqual(result['state'], 'WRITE_OUTCOME_UNCONFIRMED')
        self.assertEqual(result['record_count'], 2)
        self.assertEqual(self.snapshot(), before)
        with self.assertRaisesRegex(store.StoreError, 'TARGET_RECONCILIATION_REQUIRED'):
            store.acquire(self.root, replace(self.h.binding, run_id='never-retry'))

    def test_cli_in_new_process_reads_complete_without_authorizing(self):
        self.h.run()
        self.claim.complete(self.journal)
        self.stop_writer()
        binding_file = self.root / 'input-binding.json'
        binding_file.write_bytes(core.encoded(asdict(self.h.binding)))
        before = self.snapshot()
        result = subprocess.run([sys.executable, str(Path(recovery.__file__).resolve()),
                                 '--root', str(self.root), '--binding', str(binding_file)],
                                capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout)
        report = json.loads(result.stdout)
        self.assertEqual(report['state'], 'COMPLETED_RECORD')
        self.assertFalse(report['automatic_resume_allowed'])
        self.assertEqual(self.snapshot(), before)

    def test_truncated_record_is_preserved_and_blocked(self):
        self.journal(prepared(self.h.binding))
        self.stop_writer()
        path = self.root / self.journal.dirname / '0001.json'
        path.write_bytes(path.read_bytes()[:20])
        before = self.snapshot()
        with self.assertRaises(recovery.RecoveryError):
            recovery.inspect(self.root, self.h.binding)
        self.assertEqual(self.snapshot(), before)

    def test_missing_or_extra_checkpoint_blocks(self):
        self.journal(prepared(self.h.binding))
        self.stop_writer()
        path = self.root / self.journal.dirname
        (path / '0001.json').rename(path / '0002.json')
        with self.assertRaisesRegex(recovery.RecoveryError, 'JOURNAL_INVENTORY_INVALID'):
            recovery.inspect(self.root, self.h.binding)

    def test_symlink_and_hardlink_record_refused(self):
        self.journal(prepared(self.h.binding))
        self.stop_writer()
        path = self.root / self.journal.dirname / '0001.json'
        saved = self.root / 'saved.json'
        path.rename(saved)
        path.symlink_to(saved)
        with self.assertRaises(recovery.RecoveryError):
            recovery.inspect(self.root, self.h.binding)
        path.unlink()
        os.link(saved, path)
        with self.assertRaises(recovery.RecoveryError):
            recovery.inspect(self.root, self.h.binding)

    def test_wrong_binding_and_missing_lock_do_not_create_files(self):
        self.stop_writer()
        before = self.snapshot()
        with self.assertRaises(recovery.RecoveryError):
            recovery.inspect(self.root, replace(self.h.binding, rendered_sha256='0' * 64))
        self.assertEqual(self.snapshot(), before)
        (self.root / self.claim.lock_name).unlink()
        before = self.snapshot()
        with self.assertRaises(recovery.RecoveryError):
            recovery.inspect(self.root, self.h.binding)
        self.assertEqual(self.snapshot(), before)

    def test_partial_complete_keeps_fence_for_manual_review(self):
        self.h.run()
        marker = self.root / (self.h.binding.key + '.complete.json')
        marker.write_bytes(b'{')
        marker.chmod(0o600)
        self.stop_writer()
        before = self.snapshot()
        with self.assertRaisesRegex(recovery.RecoveryError, 'COMPLETE_INVALID'):
            recovery.inspect(self.root, self.h.binding)
        self.assertEqual(self.snapshot(), before)

    def test_fifo_record_refused_without_waiting_for_a_writer(self):
        self.journal(prepared(self.h.binding))
        self.stop_writer()
        path = self.root / self.journal.dirname / '0001.json'
        path.unlink()
        os.mkfifo(path, 0o600)
        with self.assertRaises(recovery.RecoveryError):
            recovery.inspect(self.root, self.h.binding)


if __name__ == '__main__':
    unittest.main()
