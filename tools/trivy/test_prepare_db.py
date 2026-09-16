from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import prepare_db as db


NOW = datetime(2026, 9, 16, 4, tzinfo=timezone.utc)
METADATA = {'Version': 2, 'UpdatedAt': '2026-09-16T00:00:00Z',
            'DownloadedAt': '2026-09-16T03:59:00Z', 'NextUpdate': '2026-09-17T00:00:00Z'}


class MetadataTests(unittest.TestCase):
    def test_valid_timezone_conversion(self):
        value = {**METADATA, 'DownloadedAt': '2026-09-16T12:59:00+09:00'}
        self.assertEqual(db.validate_metadata(value, NOW), value)

    def test_expired_at_boundary(self):
        with self.assertRaisesRegex(db.PreflightError, 'DB_REFRESH_REQUIRED'):
            db.validate_metadata({**METADATA, 'NextUpdate': NOW.isoformat()}, NOW)

    def test_missing_or_naive_times(self):
        for key in ('UpdatedAt', 'DownloadedAt', 'NextUpdate'):
            for value in (None, '', 'not-a-date', '2026-09-16T03:59:00'):
                with self.subTest(key=key, value=value), self.assertRaises(db.PreflightError):
                    db.validate_metadata({**METADATA, key: value}, NOW)

    def test_future_and_impossible_order(self):
        values = [{'DownloadedAt': '2027-01-01T00:00:00Z'},
                  {'UpdatedAt': '2026-09-16T03:59:30Z'},
                  {'NextUpdate': METADATA['UpdatedAt']}]
        for changed in values:
            with self.subTest(changed=changed), self.assertRaises(db.PreflightError):
                db.validate_metadata({**METADATA, **changed}, NOW)

    def test_schema_and_object_type(self):
        for value in (None, [], {**METADATA, 'Version': 3}, {**METADATA, 'Version': '2'},
                      {**METADATA, 'Version': True}):
            with self.subTest(value=value), self.assertRaises(db.PreflightError):
                db.validate_metadata(value, NOW)

    def test_duplicate_key(self):
        with self.assertRaisesRegex(db.PreflightError, 'METADATA_DUPLICATE_KEY'):
            json.loads('{"Version":2,"Version":3}', object_pairs_hook=db.unique_object)


class PrepareTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.out = Path(self.tmp.name)
        self.binary = patch.object(db, 'verify_binary')
        self.binary_mock = self.binary.start()
        self.addCleanup(self.binary.stop)
        clock = patch.object(db, 'utcnow', return_value=NOW)
        clock.start()
        self.addCleanup(clock.stop)

    def downloader(self, command, **kwargs):
        self.assertEqual(command[command.index('--db-repository') + 1], db.DB_REPOSITORY)
        self.assertIn('--download-db-only', command)
        self.assertNotIn('--skip-db-update', command)
        self.assertEqual(kwargs['stdout'], subprocess.DEVNULL)
        self.assertEqual(kwargs['stderr'], subprocess.DEVNULL)
        cache = Path(command[command.index('--cache-dir') + 1])
        folder = cache / 'db'
        folder.mkdir()
        (folder / 'trivy.db').write_bytes(b'mock db bytes; not a real scanner database')
        (folder / 'metadata.json').write_text(json.dumps(METADATA))
        return subprocess.CompletedProcess(command, 0)

    def test_success_has_traceable_snapshot_and_no_delivery_pass(self):
        with patch.object(db.subprocess, 'run', side_effect=self.downloader):
            rc, result = db.prepare(self.out, {'run_id': '123'}, {})
        self.assertEqual(rc, 0)
        self.assertEqual(result['DB_PREFLIGHT'], 'PASS')
        self.assertEqual(result['DELIVERY_GATE'], 'NOT_VERIFIED')
        for line in (self.out / 'SHA256SUMS').read_text().splitlines():
            expected, name = line.split('  ', 1)
            self.assertEqual(hashlib.sha256((self.out / name).read_bytes()).hexdigest(), expected)

    def test_download_failure_even_with_complete_files(self):
        def failed(command, **kwargs):
            self.downloader(command, **kwargs)
            return subprocess.CompletedProcess(command, 1)
        with patch.object(db.subprocess, 'run', side_effect=failed):
            rc, result = db.prepare(self.out, {}, {})
        self.assertNotEqual(rc, 0)
        self.assertEqual(result['error_code'], 'DB_DOWNLOAD_FAILED')
        self.assertNotIn('cache_dir', result)
        self.assertEqual(len((self.out / 'SHA256SUMS').read_text().splitlines()), 1)

    def test_timeout_fails_without_error_output(self):
        exc = subprocess.TimeoutExpired('secret-command', 330, output='SECRET_TOKEN')
        with patch.object(db.subprocess, 'run', side_effect=exc):
            rc, result = db.prepare(self.out, {}, {})
        self.assertEqual(result['error_code'], 'DB_DOWNLOAD_TIMEOUT')
        self.assertEqual(rc, 1)
        self.assertNotIn('SECRET_TOKEN', (self.out / 'result.json').read_text())
        self.assertNotIn('secret-command', (self.out / 'result.json').read_text())

    def test_binary_mismatch_stops_before_network(self):
        self.binary_mock.side_effect = db.PreflightError('BINARY_HASH_MISMATCH')
        with patch.object(db.subprocess, 'run') as download:
            rc, result = db.prepare(self.out, {}, {})
        download.assert_not_called()
        self.assertEqual((rc, result['error_code']), (1, 'BINARY_HASH_MISMATCH'))

    def test_existing_cache_is_not_reused(self):
        (self.out / 'cache').mkdir()
        with patch.object(db.subprocess, 'run') as download:
            rc, result = db.prepare(self.out, {}, {})
        download.assert_not_called()
        self.assertEqual(rc, 1)
        self.assertNotEqual(result['DB_PREFLIGHT'], 'PASS')

    def test_missing_or_empty_db(self):
        for mode in ('missing', 'empty'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as tmp:
                out = Path(tmp)
                def bad(command, **kwargs):
                    self.downloader(command, **kwargs)
                    file = out / 'cache/db/trivy.db'
                    file.unlink() if mode == 'missing' else file.write_bytes(b'')
                    return subprocess.CompletedProcess(command, 0)
                with patch.object(db.subprocess, 'run', side_effect=bad):
                    rc, result = db.prepare(out, {}, {})
                self.assertEqual(rc, 1)
                self.assertEqual(result['DB_PREFLIGHT'], 'FAIL')

    def test_db_symlink_rejected(self):
        def linked(command, **kwargs):
            self.downloader(command, **kwargs)
            file = self.out / 'cache/db/trivy.db'
            file.unlink()
            file.symlink_to(self.out / 'cache/db/metadata.json')
            return subprocess.CompletedProcess(command, 0)
        with patch.object(db.subprocess, 'run', side_effect=linked):
            rc, result = db.prepare(self.out, {}, {})
        self.assertEqual((rc, result['error_code']), (1, 'EXPECTED_REGULAR_FILE'))

    def test_metadata_expiry_blocks_success(self):
        def stale(command, **kwargs):
            self.downloader(command, **kwargs)
            (self.out / 'cache/db/metadata.json').write_text(json.dumps({**METADATA, 'NextUpdate': NOW.isoformat()}))
            return subprocess.CompletedProcess(command, 0)
        with patch.object(db.subprocess, 'run', side_effect=stale):
            rc, result = db.prepare(self.out, {}, {})
        self.assertEqual((rc, result['error_code']), (1, 'DB_REFRESH_REQUIRED'))

    def test_environment_does_not_forward_tokens_or_trivy_overrides(self):
        source = {'GITHUB_TOKEN': 'SECRET_TOKEN', 'TRIVY_INSECURE': 'true',
                  'TRIVY_SKIP_DB_UPDATE': 'true', 'PATH': '/bad',
                  'HTTPS_PROXY': 'http://proxy.example:3128', 'NO_PROXY': 'localhost'}
        actual = db.scanner_environment(source)
        self.assertEqual(actual['PATH'], db.SYSTEM_PATH)
        self.assertEqual(actual['HTTPS_PROXY'], source['HTTPS_PROXY'])
        self.assertEqual(actual['NO_PROXY'], 'localhost')
        self.assertNotIn('SECRET_TOKEN', json.dumps(actual))
        self.assertFalse(any(key.startswith('TRIVY_') for key in actual))


if __name__ == '__main__':
    unittest.main()
