import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

import runner_git_preflight as probe


class GitPreflightTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.folder = self.root / 'probe'
        self.folder.mkdir()
        supply = self.root / 'supply'
        supply.mkdir()
        (supply / 'base.oci.tar').write_bytes(b'fixture OCI placeholder, access check only')
        self.supply = {'directory': str(supply), 'profiles': {}}
        for name in probe.SUPPLY['profiles']:
            folder = supply / name
            folder.mkdir()
            (folder / 'wheels').mkdir()
            (folder / 'wheels/example-1-py3-none-any.whl').write_bytes(b'fixture wheel access only')
            values = {'roots.txt': b'example==1\n', 'requirements.lock': b'fixture lock\n',
                      'inventory.json': json.dumps([{'file': 'example-1-py3-none-any.whl'}]).encode()}
            hashes = {}
            for n, key in [('roots.txt', 'roots_sha256'), ('requirements.lock', 'lock_sha256'),
                           ('inventory.json', 'inventory_sha256')]:
                (folder / n).write_bytes(values[n])
                hashes[key] = hashlib.sha256(values[n]).hexdigest()
            self.supply['profiles'][name] = hashes

    def test_environment_drops_auth_config_and_keeps_proxy_without_reporting_value(self):
        incoming = {'GITHUB_TOKEN': 'secret', 'GIT_ASKPASS': '/bad', 'GIT_CONFIG_COUNT': '1',
                    'GIT_SSL_NO_VERIFY': 'true', 'SSH_AUTH_SOCK': '/bad', 'HOME': '/old',
                    'HTTPS_PROXY': 'http://fixture-proxy:3128', 'GIT_TRACE_CURL': '1'}
        env = probe.git_environment(self.root / 'isolated', incoming)
        self.assertEqual(env['HTTPS_PROXY'], incoming['HTTPS_PROXY'])
        self.assertEqual(env['GIT_CONFIG_GLOBAL'], '/dev/null')
        for key in incoming.keys() - {'HTTPS_PROXY', 'HOME'}:
            self.assertNotIn(key, env)
        self.assertNotEqual(env['HOME'], incoming['HOME'])

    def test_supply_access_preserves_files_and_does_not_claim_raw_integrity(self):
        base = Path(self.supply['directory'])
        original = {str(p): p.read_bytes() for p in base.rglob('*') if p.is_file()}
        result = probe.supply_access(self.supply)
        self.assertEqual(result['SUPPLY_ACCESS'], 'PASS')
        self.assertEqual(result['RAW_PAYLOAD_HASHES'], 'NOT_RECHECKED')
        self.assertEqual({str(p): p.read_bytes() for p in base.rglob('*') if p.is_file()}, original)

    def test_metadata_change_rejected(self):
        (Path(self.supply['directory']) / 'transaction-api-runtime/roots.txt').write_text('changed')
        with self.assertRaisesRegex(probe.ProbeError, 'METADATA_HASH'):
            probe.supply_access(self.supply)

    def test_missing_extra_and_symlink_wheels_rejected(self):
        wheel = Path(self.supply['directory']) / 'transaction-api-runtime/wheels/example-1-py3-none-any.whl'
        content = wheel.read_bytes()
        wheel.unlink()
        with self.assertRaisesRegex(probe.ProbeError, 'WHEEL_SET'):
            probe.supply_access(self.supply)
        outside = self.root / 'outside'
        outside.write_bytes(content)
        wheel.symlink_to(outside)
        with self.assertRaisesRegex(probe.ProbeError, 'PATH_LINK'):
            probe.supply_access(self.supply)
        wheel.unlink()
        wheel.write_bytes(content)
        (wheel.parent / 'extra.whl').write_text('extra')
        with self.assertRaisesRegex(probe.ProbeError, 'WHEEL_SET'):
            probe.supply_access(self.supply)

    def fixture_git(self):
        source = self.root / 'upstream'
        source.mkdir()
        env = probe.git_environment(self.root, {})
        env.update(GIT_AUTHOR_NAME='fixture', GIT_AUTHOR_EMAIL='fixture@example.invalid',
                   GIT_COMMITTER_NAME='fixture', GIT_COMMITTER_EMAIL='fixture@example.invalid')
        def git(*args):
            return subprocess.check_output(['/usr/bin/git', '-C', str(source), *args], env=env,
                                           stderr=subprocess.DEVNULL).decode().strip()
        git('init', '--template=')
        (source / 'run.py').write_text('raise RuntimeError("must never execute")\n')
        git('add', 'run.py')
        git('commit', '-m', 'fixture')
        return source, git('rev-parse', 'HEAD'), git('rev-parse', 'HEAD^{tree}')

    def local_transport(self, source):
        actual = probe.command
        def mapped(folder, steps, env, label, argv, timeout=60):
            self.assertIn('protocol.https.allow=always', argv)
            self.assertIn('http.sslVerify=true', argv)
            self.assertIn('http.followRedirects=false', argv)
            if label == 'git-origin':
                argv = [str(source) if v == probe.ORIGIN else v for v in argv]
            # Only the test transport allows a local repository; production remains HTTPS-only.
            argv = [argv[0], '-c', 'protocol.file.allow=always', *argv[1:]]
            return actual(folder, steps, env, label, argv, timeout)
        return mapped

    def test_real_bare_fetch_matches_commit_tree_and_never_executes_app(self):
        source, sha, tree = self.fixture_git()
        record = {'steps': []}
        with patch.object(probe, 'command', side_effect=self.local_transport(source)):
            result = probe.probe_git(self.folder, record, {}, sha, tree)
        self.assertEqual(result['GIT_INTAKE'], 'PASS')
        self.assertEqual(result['SOURCE_APPROVAL'], 'NOT_GRANTED_HERE')
        self.assertEqual(len(record['steps']), 7)
        self.assertFalse((self.folder / 'source.git/run.py').exists())
        self.assertTrue((self.folder / 'source.git/objects').is_dir())

    def test_wrong_tree_cannot_claim_success(self):
        source, sha, _ = self.fixture_git()
        with patch.object(probe, 'command', side_effect=self.local_transport(source)):
            with self.assertRaisesRegex(probe.ProbeError, 'OBSERVED_TREE_MISMATCH'):
                probe.probe_git(self.folder, {'steps': []}, {}, sha, '0' * 40)

    def test_failed_git_still_exports_result_without_raw_logs_or_proxy_secret(self):
        raw = self.folder / 'git-fetch.log'
        raw.write_text('fixture secret that must stay on server')
        with patch.object(probe, 'probe_git', side_effect=probe.ProbeError('GIT_COMMAND_FAILED:git-fetch')):
            result, archive = probe.run(self.root, self.folder, {}, self.supply)
        self.assertEqual(result['OVERALL'], 'FAIL')
        self.assertEqual(result['supply']['SUPPLY_ACCESS'], 'PASS')
        self.assertEqual(result['errors']['git'], 'GIT_COMMAND_FAILED:git-fetch')
        with tarfile.open(archive) as tar:
            self.assertEqual(set(tar.getnames()), {'result.json', 'SHA256SUMS'})
            self.assertNotIn(b'fixture secret', tar.extractfile('result.json').read())

    def test_low_capacity_is_not_overall_pass_even_with_git_and_supply_success(self):
        with patch.object(probe, 'capacity', return_value={'APP_START_MINIMUM': 'BELOW_MINIMUM'}), \
             patch.object(probe, 'probe_git', return_value={'GIT_INTAKE': 'PASS'}):
            result, _ = probe.run(self.root, self.folder, {}, self.supply)
        self.assertEqual(result['OVERALL'], 'FAIL')
        self.assertEqual(result['git']['GIT_INTAKE'], 'PASS')

    def test_timeout_records_nonzero_result(self):
        steps = []
        with self.assertRaisesRegex(probe.ProbeError, 'GIT_COMMAND_TIMEOUT:timeout'):
            probe.command(self.folder, steps, probe.git_environment(self.root, {}), 'timeout',
                          [sys.executable, '-c', 'import time; time.sleep(60)'], timeout=0.05)
        self.assertEqual(steps[0]['exit_code'], 124)
        self.assertTrue(steps[0]['timeout'])

    def test_existing_git_path_preserved(self):
        old = self.folder / 'source.git'
        old.mkdir()
        (old / 'keep').write_text('keep')
        with self.assertRaisesRegex(probe.ProbeError, 'NEW_GIT_PATH_REQUIRED'):
            probe.probe_git(self.folder, {'steps': []}, {})
        self.assertEqual((old / 'keep').read_text(), 'keep')

    def test_single_file_isolated_relative_and_absolute_invocation(self):
        target = self.root / 'runner_git_preflight.py'
        target.write_bytes(Path(probe.__file__).read_bytes())
        for name in ('./runner_git_preflight.py', str(target)):
            r = subprocess.run([sys.executable, '-I', '-B', name, '--help'], cwd=self.root,
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 0)
            self.assertIn('--prepare', r.stdout)


if __name__ == '__main__':
    unittest.main()
