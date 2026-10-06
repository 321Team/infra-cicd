"""POSIX filesystem and competing-process tests in isolated temporary directories."""
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import deploy_claim as c
from test_deploy_binding import inputs


@unittest.skipUnless(os.name == 'posix', 'Runner claim requires POSIX flock and dir_fd')
class ClaimTests(unittest.TestCase):
    def setUp(self):
        # Avoid /tmp: the production helper rejects writable shared ancestors.
        self.tmp = tempfile.TemporaryDirectory(prefix='claim-test-', dir=Path.cwd())
        self.root = Path(self.tmp.name)
        self.plan_hash = 'a' * 64
        self.approval_hash = 'b' * 64
        self.execution = inputs()[2]
        self.open_claims = []

    def tearDown(self):
        for claim in self.open_claims:
            claim.close()
        self.tmp.cleanup()

    def acquire(self, **kwargs):
        claim = c.acquire(self.root, kwargs.get('plan_hash', self.plan_hash),
                          kwargs.get('approval_hash', self.approval_hash),
                          kwargs.get('execution', self.execution))
        self.open_claims.append(claim)
        return claim

    def filename(self):
        return self.root / (self.plan_hash + '.claim.json')

    def test_claim_records_actual_identity_and_exact_hash_without_credentials(self):
        claim = self.acquire()
        claim.check()
        raw = self.filename().read_bytes()
        record = json.loads(raw)
        self.assertEqual(record['execution'], self.execution)
        self.assertEqual(record['plan_sha256'], self.plan_hash)
        self.assertEqual(record['approval_sha256'], self.approval_hash)
        self.assertEqual(record['DELIVERY_GATE'], 'NOT_VERIFIED')
        self.assertEqual(claim.sha256, hashlib.sha256(raw).hexdigest())
        self.assertEqual(self.filename().stat().st_mode & 0o777, 0o600)
        self.assertNotIn('token', record)

    def test_global_lock_blocks_a_different_plan_without_consuming_it(self):
        self.acquire()
        with self.assertRaisesRegex(c.ClaimError, 'DEPLOYMENT_LOCK_BUSY'):
            self.acquire(plan_hash='c' * 64)
        self.assertFalse((self.root / ('c' * 64 + '.claim.json')).exists())

    def test_different_process_cannot_bypass_global_lock(self):
        self.acquire()
        script = (
            "import deploy_claim as c,json,sys\n"
            "try: c.acquire(sys.argv[1], 'c'*64, 'b'*64, json.loads(sys.argv[2]))\n"
            "except c.ClaimError as e: print(str(e)); sys.exit(7)\n"
        )
        result = subprocess.run(
            [sys.executable, '-c', script, str(self.root), json.dumps(self.execution)],
            cwd=Path(__file__).parent, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 7, result.stderr)
        self.assertEqual(result.stdout.strip(), 'DEPLOYMENT_LOCK_BUSY')

    def test_closed_claim_still_blocks_a_new_run_of_same_plan(self):
        first = self.acquire()
        first.close()
        new_execution = dict(self.execution, run_id=1000)
        with self.assertRaisesRegex(c.ClaimError, 'PLAN_ALREADY_CLAIMED'):
            self.acquire(execution=new_execution)
        self.assertEqual(json.loads(self.filename().read_bytes())['execution'], self.execution)

    def test_changed_approval_does_not_reset_a_consumed_plan(self):
        self.acquire().close()
        with self.assertRaisesRegex(c.ClaimError, 'PLAN_ALREADY_CLAIMED'):
            self.acquire(approval_hash='d' * 64)

    def test_release_allows_a_different_independently_approved_plan(self):
        self.acquire().close()
        other = self.acquire(plan_hash='c' * 64)
        other.check()
        self.assertEqual(len(list(self.root.glob('*.claim.json'))), 2)

    def test_retry_and_bool_attempt_rejected_before_any_claim(self):
        for attempt in (2, True):
            with self.subTest(attempt=attempt), self.assertRaises(c.ClaimError):
                self.acquire(execution=dict(self.execution, attempt=attempt))
        self.assertEqual(list(self.root.iterdir()), [])

    def test_bad_hash_and_context_rejected_before_io(self):
        for options in ({'plan_hash': '../../escape'},
                        {'approval_hash': 'bad'},
                        {'execution': dict(self.execution, repository='other/repo')}):
            with self.subTest(options=options), self.assertRaises(c.ClaimError):
                self.acquire(**options)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_partial_write_is_consumed_and_error_text_is_sanitized(self):
        with patch.object(c, 'write_all', side_effect=OSError('PRIVATE_TOKEN')):
            with self.assertRaisesRegex(c.ClaimError, '^CLAIM_IO_FAILED$'):
                self.acquire()
        self.assertTrue(self.filename().exists())
        self.assertEqual(self.filename().read_bytes(), b'')
        with self.assertRaisesRegex(c.ClaimError, 'PLAN_ALREADY_CLAIMED'):
            self.acquire()

    def test_fsync_failure_never_returns_a_claim_or_deletes_it(self):
        with patch.object(c.os, 'fsync', side_effect=OSError('PRIVATE_TOKEN')):
            with self.assertRaisesRegex(c.ClaimError, '^CLAIM_IO_FAILED$'):
                self.acquire()
        self.assertTrue(self.filename().exists())
        with self.assertRaisesRegex(c.ClaimError, 'PLAN_ALREADY_CLAIMED'):
            self.acquire()

    def test_process_exit_releases_mutex_but_not_consumed_claim(self):
        script = (
            "import deploy_claim as c,json,os,sys\n"
            "claim=c.acquire(sys.argv[1], 'a'*64, 'b'*64, json.loads(sys.argv[2]))\n"
            "os._exit(0)\n"
        )
        result = subprocess.run(
            [sys.executable, '-c', script, str(self.root), json.dumps(self.execution)],
            cwd=Path(__file__).parent, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        with self.assertRaisesRegex(c.ClaimError, 'PLAN_ALREADY_CLAIMED'):
            self.acquire()

    def test_claim_contents_or_permission_drift_stops_use(self):
        claim = self.acquire()
        self.filename().write_bytes(b'changed')
        with self.assertRaisesRegex(c.ClaimError, 'CLAIM_BYTES_CHANGED'):
            claim.check()
        self.filename().chmod(0o644)
        with self.assertRaisesRegex(c.ClaimError, 'CLAIM_FILE_UNSAFE'):
            claim.check()

    def test_lock_replacement_and_claim_unlink_are_detected(self):
        claim = self.acquire()
        lock = self.root / 'deploy.lock'
        lock.unlink()
        lock.write_bytes(b'')
        lock.chmod(0o600)
        with self.assertRaises(c.ClaimError):
            claim.check()
        self.filename().unlink()
        with self.assertRaises(c.ClaimError):
            claim.check()

    def test_unsafe_directory_and_symlink_root_rejected(self):
        self.root.chmod(0o755)
        with self.assertRaisesRegex(c.ClaimError, 'CLAIM_DIRECTORY_UNSAFE'):
            self.acquire()
        self.root.chmod(0o700)
        alias = self.root / 'alias'
        alias.symlink_to(self.root, target_is_directory=True)
        with self.assertRaisesRegex(c.ClaimError, 'CLAIM_PARENT_UNSAFE'):
            c.acquire(alias, self.plan_hash, self.approval_hash, self.execution)

    def test_symlink_lock_or_preexisting_claim_not_followed(self):
        target = self.root / 'untouched'
        target.write_bytes(b'keep')
        target.chmod(0o600)
        lock = self.root / 'deploy.lock'
        lock.symlink_to(target)
        with self.assertRaises(c.ClaimError):
            self.acquire()
        self.assertEqual(target.read_bytes(), b'keep')
        lock.unlink()
        self.filename().symlink_to(target)
        with self.assertRaisesRegex(c.ClaimError, 'PLAN_ALREADY_CLAIMED'):
            self.acquire()
        self.assertEqual(target.read_bytes(), b'keep')

    def test_hardlinked_lock_rejected_without_modifying_target(self):
        original = self.root / 'original'
        original.write_bytes(b'keep')
        original.chmod(0o600)
        os.link(original, self.root / 'deploy.lock')
        with self.assertRaisesRegex(c.ClaimError, 'CLAIM_FILE_UNSAFE'):
            self.acquire()
        self.assertEqual(original.read_bytes(), b'keep')

    def test_close_releases_mutex_on_exception_and_cannot_reenter(self):
        first = self.acquire()
        with self.assertRaisesRegex(RuntimeError, 'fixture'):
            with first:
                raise RuntimeError('fixture')
        with self.assertRaisesRegex(c.ClaimError, 'CLAIM_CLOSED'):
            first.check()
        with self.acquire(plan_hash='c' * 64) as second:
            second.check()


if __name__ == '__main__':
    unittest.main()
