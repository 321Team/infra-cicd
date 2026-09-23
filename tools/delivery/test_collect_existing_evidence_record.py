import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / 'precheck/collect_existing_evidence_record.py'
SPEC = importlib.util.spec_from_file_location('existing_record', SCRIPT)
collector = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(collector)


class ExistingRecordTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.original = b'fixture archive bytes'
        self.sha_patch = patch.object(collector, 'ARCHIVE_SHA', collector.sha(self.original))
        self.sha_patch.start()
        self.addCleanup(self.sha_patch.stop)
        self.case = self.make_case('ABC123')

    def make_case(self, suffix):
        case = self.home / ('CICD_Evidence35683010080_1.' + suffix)
        (case / 'export').mkdir(parents=True)
        (case / 'export/app-evidence.tar.gz').write_bytes(self.original)
        log = ('ARCHIVE=' + str(case / 'export/app-evidence.tar.gz') + '\nSHA256=' +
               collector.ARCHIVE_SHA + '\n' + json.dumps(collector.LOG_RESULT, sort_keys=True) + '\n')
        (case / 'collect.log').write_text(log)
        (case / 'started-at.txt').write_text('2026-09-23T23:10:00+09:00\n')
        (case / 'finished-at.txt').write_text('2026-09-23T23:10:05+09:00\n')
        return case

    def read_bundle(self, path):
        with tarfile.open(path) as archive:
            return {m.name: archive.extractfile(m).read() for m in archive}

    def assert_no_output(self):
        self.assertFalse(list(self.home.glob('CICD_CollectRecord*')))

    def test_complete_copies_original_bytes_and_never_claims_old_exit_code(self):
        before = {p: p.read_bytes() for p in self.case.rglob('*') if p.is_file()}
        archive, sidecar, state = collector.collect(self.home)
        self.assertEqual(state, 'COLLECTED')
        self.assertEqual(sidecar.read_text().split()[0], collector.sha(archive.read_bytes()))
        files = self.read_bundle(archive)
        for name in ('collect.log', 'started-at.txt', 'finished-at.txt'):
            self.assertEqual(files['original/' + name], before[self.case / name])
        record = json.loads(files['collection-record.json'])
        self.assertEqual(record['historical_collect_exit_code'], 'NOT_RECORDED')
        self.assertEqual(record['raw_app_payloads'], 'NOT_READ')
        self.assertEqual(record['tool_sha256'], collector.sha(SCRIPT.read_bytes()))
        self.assertEqual(before, {p: p.read_bytes() for p in before})

    def test_missing_records_remain_missing_not_synthesized(self):
        (self.case / 'finished-at.txt').unlink()
        archive, _, state = collector.collect(self.home)
        self.assertEqual(state, 'COLLECTED_WITH_MISSING')
        files = self.read_bundle(archive)
        self.assertNotIn('original/finished-at.txt', files)
        self.assertEqual(json.loads(files['collection-record.json'])['missing_original_records'], ['finished-at.txt'])
        self.assertFalse((self.case / 'finished-at.txt').exists())

    def test_mismatching_archive_stops_without_output(self):
        (self.case / 'export/app-evidence.tar.gz').write_bytes(b'other run')
        with self.assertRaises(collector.Stop):
            collector.collect(self.home)
        self.assert_no_output()

    def test_ambiguous_cases_require_explicit_selection(self):
        self.make_case('DEF456')
        with self.assertRaises(collector.Stop):
            collector.collect(self.home)
        self.assert_no_output()
        self.assertEqual(collector.collect(self.home, self.case)[2], 'COLLECTED')

    def test_symlink_record_is_rejected(self):
        (self.case / 'collect.log').rename(self.home / 'external')
        (self.case / 'collect.log').symlink_to(self.home / 'external')
        with self.assertRaises(OSError):
            collector.collect(self.home)
        self.assert_no_output()

    def test_symlink_directory_is_rejected(self):
        (self.case / 'export').rename(self.case / 'old-export')
        (self.case / 'export').symlink_to(self.case / 'old-export', target_is_directory=True)
        with self.assertRaises(OSError):
            collector.collect(self.home)
        self.assert_no_output()

    def test_fifo_is_rejected_without_blocking(self):
        (self.case / 'collect.log').unlink()
        os.mkfifo(self.case / 'collect.log')
        with self.assertRaises(collector.Stop):
            collector.collect(self.home)
        self.assert_no_output()

    def test_oversized_record_is_rejected(self):
        (self.case / 'collect.log').write_bytes(b'x' * 8193)
        with self.assertRaises(collector.Stop):
            collector.collect(self.home)
        self.assert_no_output()

    def test_reversed_timestamp_is_rejected(self):
        (self.case / 'finished-at.txt').write_text('2026-09-23T23:00:00+09:00\n')
        with self.assertRaises(collector.Stop):
            collector.collect(self.home)
        self.assert_no_output()

    def test_duplicate_key_is_rejected(self):
        log = (self.case / 'collect.log').read_text().replace('{', '{"EVIDENCE_CHECK":"PASS",', 1)
        (self.case / 'collect.log').write_text(log)
        with self.assertRaises(collector.Stop):
            collector.collect(self.home)
        self.assert_no_output()

    def test_unexpected_log_is_not_printed_or_exported(self):
        marker = 'PRIVATE_TEST_SENTINEL_123'
        (self.case / 'collect.log').write_text(marker)
        out = io.StringIO()
        with patch.object(collector.os, 'getuid', return_value=1000), \
                patch.object(collector.pwd, 'getpwuid', return_value=SimpleNamespace(pw_name='server01_cicd-runner')), \
                patch.object(collector.socket, 'gethostname', return_value='cicd-runner01'), \
                patch.object(collector, 'HOME_DIR', self.home), contextlib.redirect_stdout(out):
            self.assertEqual(collector.main([]), 1)
        self.assertNotIn(marker, out.getvalue())
        self.assert_no_output()

    def test_wrong_account_is_rejected_before_reads(self):
        with patch.object(collector.os, 'getuid', return_value=0), \
                patch.object(collector, 'collect') as operation, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(collector.main([]), 1)
        operation.assert_not_called()

    def test_new_output_never_overwrites_previous_output(self):
        first, _, _ = collector.collect(self.home)
        original = first.read_bytes()
        second, _, _ = collector.collect(self.home)
        self.assertNotEqual(first, second)
        self.assertEqual(first.read_bytes(), original)


if __name__ == '__main__':
    unittest.main()
