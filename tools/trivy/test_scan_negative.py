"""MOCK image/DB/scanner cases and real local file/process guards; not Runtime."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import scan_negative as negative
import scan_images as scan
import test_scan_images as fixtures
from prepare_db import PreflightError
from verify_runner import sha256


class NegativeCases(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ScanTests('runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = self.fixture.root
        self.context = {**self.fixture.context, 'purpose': 'SCAN_NEGATIVE_NOT_DELIVERABLE',
                        'negative_case': 'scanner-error', 'workflow_ref': negative.WORKFLOW}
        hashes = {name: {'sha256': sha256(self.fixture.archive),
                         'bytes': self.fixture.archive.stat().st_size} for name in scan.SERVICES}
        patch.object(negative, 'FIXTURES', hashes).start()
        patch.object(negative, 'verify_binary').start()
        self.addCleanup(patch.stopall)

    def missing_db(self, command, **kwargs):
        self.fixture.calls.append((command, kwargs))
        self.assertEqual(kwargs['timeout'], 150)
        self.assertEqual(kwargs['preexec_fn'], scan._probe_file_limit)
        self.assertEqual(kwargs['env']['HOME'], str(kwargs['cwd']))
        self.assertNotIn('HTTPS_PROXY', kwargs['env'])
        self.assertNotIn('GITHUB_TOKEN', kwargs['env'])
        self.assertIn('--skip-db-update', command)
        self.assertIn('--offline-scan', command)
        kwargs['stderr'].write(negative.MISSING_DB_ERROR.encode())
        return subprocess.CompletedProcess(command, 1)

    def execute(self, case):
        self.context['negative_case'] = case
        options = {'empty_db_probe': True} if case == 'scanner-error' else {}
        return scan.scan_pair(self.fixture.evidence, self.context, negative.SOURCE_SHA,
                              self.fixture.archives, self.fixture.env, **options)

    def classified(self, case, pair):
        return negative.observed(case, *pair, self.fixture.evidence)

    def test_vulnerability_uses_normal_fresh_db_scan_and_stops_second_image(self):
        self.fixture.severity = 'HIGH'
        pair = self.execute('vulnerability')
        self.assertEqual(pair[0], 10)
        self.assertEqual(self.classified('vulnerability', pair), 'EXPECTED_FAILURE_OBSERVED')
        self.assertEqual(self.fixture.prepare.call_count, 1)
        self.assertEqual(len(self.fixture.calls), 1)
        self.assertFalse((self.fixture.evidence / 'empty-db-cache').exists())

    def test_error_probe_calls_real_entry_with_new_empty_cache_and_never_downloads(self):
        self.fixture.run.side_effect = self.missing_db
        pair = self.execute('scanner-error')
        self.assertEqual(pair[0], 1)
        self.assertEqual(pair[1]['error_code'], 'SCANNER_EXECUTION_ERROR')
        self.assertEqual(self.classified('scanner-error', pair), 'EXPECTED_FAILURE_OBSERVED')
        self.fixture.prepare.assert_not_called()
        self.assertEqual(len(self.fixture.calls), 1)
        command, _ = self.fixture.calls[0]
        self.assertEqual(Path(command[command.index('--cache-dir') + 1]), self.fixture.evidence / 'empty-db-cache')
        self.assertFalse((self.fixture.evidence / 'db').exists())

    def test_error_probe_rejects_context_before_copy_download_or_scan(self):
        for key, value in [('purpose', 'normal'), ('negative_case', 'vulnerability'),
                           ('workflow_ref', 'other'), ('attempt', 2)]:
            with self.subTest(key=key):
                context = {**self.context, key: value}
                out = self.root / key
                out.mkdir()
                rc, result = scan.scan_pair(out, context, negative.SOURCE_SHA,
                                            self.fixture.archives, {}, empty_db_probe=True)
                self.assertEqual((rc, result['error_code']), (1, 'EMPTY_DB_PROBE_CONTEXT_REQUIRED'))
                self.assertFalse((out / 'transaction-api.oci.tar').exists())
        self.fixture.prepare.assert_not_called()
        self.fixture.run.assert_not_called()

    def test_wrong_cause_log_tamper_db_creation_or_report_cannot_be_expected(self):
        self.fixture.run.side_effect = self.missing_db
        pair = self.execute('scanner-error')
        first = self.fixture.evidence / 'transaction-api'
        log = first / 'scanner-stderr.log'
        original = log.read_bytes()
        log.write_bytes(b'an unrelated error')
        self.assertEqual(self.classified('scanner-error', pair), 'NOT_CONFIRMED')
        invocation_path = first / 'scanner-invocation.json'
        invocation = json.loads(invocation_path.read_bytes())
        invocation['stderr_sha256'] = sha256(log)
        invocation_path.write_text(json.dumps(invocation))
        self.assertEqual(self.classified('scanner-error', pair), 'NOT_CONFIRMED')
        log.write_bytes(original)
        invocation['stderr_sha256'] = sha256(log)
        invocation_path.write_text(json.dumps(invocation))
        report = first / 'report.json'
        report.write_text('{}')
        self.assertEqual(self.classified('scanner-error', pair), 'NOT_CONFIRMED')
        report.unlink()
        cache = self.fixture.evidence / 'empty-db-cache/db'
        cache.mkdir()
        (cache / 'trivy.db').write_text('unexpected')
        self.assertEqual(self.classified('scanner-error', pair), 'NOT_CONFIRMED')

    def test_timeout_signal_or_unexpected_valid_report_is_not_expected(self):
        for fault in ('timeout', 'signal', None):
            with self.subTest(fault=fault):
                self.fixture.evidence = self.root / str(fault)
                self.fixture.evidence.mkdir()
                self.fixture.fault = fault
                pair = self.execute('scanner-error')
                self.assertEqual(pair[0], 1)
                self.assertEqual(self.classified('scanner-error', pair), 'NOT_CONFIRMED')
                self.assertNotEqual(pair[1]['IMAGE_SCAN'], 'PASS')
        self.fixture.prepare.assert_not_called()

    def test_clean_scan_db_preflight_or_scanner_error_is_not_vulnerability_case(self):
        for fault in (None, 'rc', 'coverage', 'db_changed'):
            with self.subTest(fault=fault):
                self.fixture.evidence = self.root / str(fault)
                self.fixture.evidence.mkdir()
                self.fixture.fault = fault
                pair = self.execute('vulnerability')
                self.assertEqual(self.classified('vulnerability', pair), 'NOT_CONFIRMED')

    def test_result_or_input_mutation_cannot_be_expected(self):
        self.fixture.severity = 'CRITICAL'
        pair = self.execute('vulnerability')
        changed = copy.deepcopy(pair[1])
        changed['deploy'] = 'EXECUTED'
        self.assertEqual(self.classified('vulnerability', (10, changed)), 'NOT_CONFIRMED')
        (self.fixture.evidence / 'transaction-api.oci.tar').write_bytes(b'changed')
        self.assertEqual(self.classified('vulnerability', pair), 'NOT_CONFIRMED')

    def test_workflow_wrapper_always_fails_including_expected_case(self):
        for outcome in ('EXPECTED_FAILURE_OBSERVED', 'NOT_CONFIRMED'):
            out = self.root / outcome
            out.mkdir()
            def scanner(evidence, *args, **kwargs):
                (evidence / 'result.json').write_text('{}')
                return 10, {'error_code': 'HIGH_CRITICAL_FINDINGS'}
            with patch.object(negative, 'copy_fixtures', return_value={}), \
                    patch.object(scan, 'scan_pair', side_effect=scanner), \
                    patch.object(negative, 'observed', return_value=outcome):
                rc, result = negative.run(out, self.root, {**self.context, 'negative_case':'vulnerability'}, {}, 'vulnerability')
            self.assertEqual(rc, 1)
            self.assertEqual(result['SCAN_NEGATIVE_WORKFLOW'], 'FAIL')
            self.assertEqual(result['NEGATIVE_CASE'], outcome)
            self.assertEqual(result['PUSH'], result['DEPLOY'])
            self.assertEqual(result['DEPLOY'], 'NOT_EXECUTED')


class BoundaryTests(unittest.TestCase):
    def test_context_rejects_rerun_wrong_workflow_sha_and_unknown_case(self):
        context = {'workflow_sha': 'b'*40, 'attempt': 1}
        env = {'GITHUB_WORKFLOW_REF': negative.WORKFLOW, 'GITHUB_WORKFLOW_SHA': 'b'*40}
        with patch.object(negative, 'runtime_context', return_value=context):
            self.assertEqual(negative.bind_context(env, 'vulnerability')['negative_case'], 'vulnerability')
            for bad in ({}, {**env, 'GITHUB_WORKFLOW_SHA': 'c'*40}):
                with self.assertRaises(PreflightError):
                    negative.bind_context(bad, 'scanner-error')
            with self.assertRaises(PreflightError):
                negative.bind_context(env, 'anything')
            context['attempt'] = 2
            with self.assertRaises(PreflightError):
                negative.bind_context(env, 'scanner-error')

    def test_private_diagnostic_is_bounded_by_real_child_file_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            command = [sys.executable, '-c', 'import sys; sys.stderr.write("x" * (2 * 1024**2))']
            result = scan._run_empty_db_probe(command, root)
            self.assertNotEqual(result.returncode, 0)
            self.assertLessEqual((root / 'scanner-stderr.log').stat().st_size, 1024**2)
            receipt = json.loads((root / 'scanner-invocation.json').read_bytes())
            self.assertEqual(receipt['stderr_sha256'], sha256(root / 'scanner-stderr.log'))

    def test_fixed_fixture_copy_rejects_tamper_without_changing_original(self):
        with tempfile.TemporaryDirectory(dir=Path.home()) as tmp:
            home = Path(tmp)
            source = home / negative.ORIGINAL
            source.mkdir(parents=True)
            originals = {}
            hashes = {}
            for name in scan.SERVICES:
                path = source / (name + '.oci.tar')
                path.write_bytes(b'fixture bytes')
                originals[name] = path
                hashes[name] = {'bytes': path.stat().st_size, 'sha256': sha256(path)}
            good = home / 'good'
            good.mkdir()
            with patch.object(negative, 'FIXTURES', hashes):
                copies = negative.copy_fixtures(home, good)
                self.assertEqual(set(copies), set(scan.SERVICES))
                for name in copies:
                    self.assertEqual(copies[name].read_bytes(), originals[name].read_bytes())
                originals['transaction-api'].write_bytes(b'changed bytes')
                bad = home / 'bad'
                bad.mkdir()
                with self.assertRaisesRegex(PreflightError, 'FIXTURE_HASH_MISMATCH'):
                    negative.copy_fixtures(home, bad)
                self.assertEqual(originals['transaction-api'].read_bytes(), b'changed bytes')

    def test_claim_prevents_repeat_and_existing_host_lock_blocks_new_run(self):
        with tempfile.TemporaryDirectory(dir=Path.home()) as tmp:
            home = Path(tmp)
            context = {'run_id':'123', 'attempt':1, 'workflow_sha':'b'*40, 'negative_case':'vulnerability'}
            output = {'NEGATIVE_CASE':'NOT_CONFIRMED'}
            with patch.object(negative, 'bind_context', return_value=context), \
                    patch.object(negative.pwd, 'getpwuid', return_value=SimpleNamespace(pw_dir=str(home))), \
                    patch.object(negative.signal, 'signal'), \
                    patch.object(negative.os, 'chdir'), \
                    patch.object(negative.os, 'umask'), \
                    patch.object(negative.shutil, 'disk_usage', return_value=SimpleNamespace(free=8*1024**3)), \
                    patch.object(negative, 'run', return_value=(1,output)) as run:
                self.assertEqual(negative.main(['--case','vulnerability']), 1)
                self.assertEqual(negative.main(['--case','vulnerability']), 1)
                self.assertEqual(run.call_count, 1)
                context['run_id'] = '124'
                with (home/'FDS_CICD_AppWorkflow.lock').open('r+') as lock:
                    negative.fcntl.flock(lock, negative.fcntl.LOCK_EX | negative.fcntl.LOCK_NB)
                    self.assertEqual(negative.main(['--case','vulnerability']), 1)
                self.assertFalse((home/'FDS_CICD_ScanNegative-124.claim').exists())
                self.assertEqual(run.call_count, 1)


if __name__ == '__main__':
    unittest.main()
