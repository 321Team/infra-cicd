"""Real OCI/wheel byte faults and Git snapshots; Podman/Trivy command doubles."""
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
import zipfile

import offline_supply as supply
import offline_supply_v3 as supply_v3
import build_scan as flow
import test_build_scan as legacy
import probe_offline_supply as probe
import build_offline_probe_bundle as bundle
from oci_test_fixtures import image_members, write_archive


def fixture(root):
    source = root / 'supply-source'
    source.mkdir()
    archive = source / 'base.oci.tar'
    write_archive(archive, image_members())
    identity = supply.inspect_archive(archive)
    record = {'directory': str(source), 'archive_sha256': identity['archive_sha256'],
              'manifest_digest': identity['manifest_digest'], 'config_digest': identity['config_digest'],
              'profiles': {}, 'requirements': {}}
    for name in supply.PROFILES:
        folder = source / name
        (folder / 'wheels').mkdir(parents=True)
        wheel = folder / 'wheels' / 'fixture_pkg-1.0-py3-none-any.whl'
        with zipfile.ZipFile(wheel, 'w') as z:
            z.writestr('fixture_pkg/__init__.py', 'VALUE=42\n')
            z.writestr('fixture_pkg-1.0.dist-info/METADATA', 'Metadata-Version: 2.1\nName: fixture-pkg\nVersion: 1.0\n')
            z.writestr('fixture_pkg-1.0.dist-info/WHEEL', 'Wheel-Version: 1.0\nGenerator: fixture\nRoot-Is-Purelib: true\nTag: py3-none-any\n')
            z.writestr('fixture_pkg-1.0.dist-info/RECORD', '')
        row = {'name': 'fixture-pkg', 'version': '1.0', 'file': wheel.name, 'sha256': supply.sha256(wheel)}
        (folder / 'inventory.json').write_text(json.dumps([row]))
        (folder / 'requirements.lock').write_text('fixture-pkg==1.0 --hash=sha256:' + row['sha256'] + '\n')
        (folder / 'roots.txt').write_text('fixture-pkg==1.0\n')
        record['profiles'][name] = {key: supply.sha256(folder / filename) for filename, key in
                [('roots.txt', 'roots_sha256'), ('requirements.lock', 'lock_sha256'), ('inventory.json', 'inventory_sha256')]}
    for name in supply.SERVICES:
        for path in ('requirements.txt', 'requirements-test.txt'):
            raw = b'fixture-pkg==1.0\n' if path == 'requirements.txt' else b'-r requirements.txt\n'
            record['requirements'][name + '/' + path] = hashlib.sha256(raw).hexdigest()
    return source, record, identity


class SupplyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source, self.record, self.identity = fixture(self.root)
        self.target = self.root / 'staged'

    def test_copy_all_profiles_preserves_source(self):
        before = {p.relative_to(self.source): supply.sha256(p) for p in self.source.rglob('*') if p.is_file()}
        self.assertEqual(supply.stage(self.record, self.target), self.identity)
        supply.verify_staged(self.target, self.record)
        after = {p.relative_to(self.source): supply.sha256(p) for p in self.source.rglob('*') if p.is_file()}
        self.assertEqual(before, after)

    def test_existing_target_never_reused(self):
        self.target.mkdir()
        sentinel = self.target / 'keep'
        sentinel.write_text('keep')
        with self.assertRaises(FileExistsError):
            supply.stage(self.record, self.target)
        self.assertEqual(sentinel.read_text(), 'keep')

    def test_base_or_profile_hash_fault_blocks(self):
        for field in ('archive_sha256', 'manifest_digest', 'config_digest'):
            record = copy.deepcopy(self.record)
            record[field] = ('sha256:' if field != 'archive_sha256' else '') + '0' * 64
            with self.subTest(field=field), self.assertRaises(flow.PreflightError):
                supply.stage(record, self.root / field)
        self.record['profiles']['fds-engine-test']['inventory_sha256'] = '0' * 64
        with self.assertRaisesRegex(flow.PreflightError, 'HASH_MISMATCH'):
            supply.stage(self.record, self.target)

    def test_missing_extra_and_corrupt_wheels_block(self):
        wheel = next((self.source / 'transaction-api-runtime/wheels').iterdir())
        original = wheel.read_bytes()
        for fault in ('missing', 'extra', 'corrupt'):
            wheel.write_bytes(original)
            extra = wheel.parent / 'extra.whl'
            extra.unlink(missing_ok=True)
            if fault == 'missing':
                wheel.unlink()
            elif fault == 'extra':
                extra.write_bytes(original)
            else:
                wheel.write_bytes(b'corrupt')
            with self.subTest(fault=fault), self.assertRaises(flow.PreflightError):
                supply.stage(self.record, self.root / fault)

    def test_wheel_directory_and_file_symlinks_rejected(self):
        wheels = self.source / 'transaction-api-runtime/wheels'
        original = wheels.with_name('original')
        wheels.rename(original)
        wheels.symlink_to(original, target_is_directory=True)
        with self.assertRaisesRegex(flow.PreflightError, 'PATH_LINK'):
            supply.stage(self.record, self.target)
        wheels.unlink()
        original.rename(wheels)
        wheel = next(wheels.iterdir())
        external = self.root / 'outside.whl'
        wheel.rename(external)
        wheel.symlink_to(external)
        with self.assertRaisesRegex(flow.PreflightError, 'PATH_LINK'):
            supply.stage(self.record, self.root / 'other-target')

    def test_lock_cannot_add_options_or_url_even_with_updated_hash(self):
        folder = self.source / 'transaction-api-runtime'
        (folder / 'requirements.lock').write_text('--extra-index-url https://example.invalid\n')
        self.record['profiles']['transaction-api-runtime']['lock_sha256'] = supply.sha256(folder / 'requirements.lock')
        with self.assertRaisesRegex(flow.PreflightError, 'LOCK_INVENTORY_MISMATCH'):
            supply.stage(self.record, self.target)

    def test_wheel_direct_dependency_rejected(self):
        path = self.root / 'direct.whl'
        with zipfile.ZipFile(path, 'w') as z:
            z.writestr('fixture_pkg-1.0.dist-info/METADATA',
                'Name: fixture-pkg\nVersion: 1.0\nRequires-Dist: remote @ https://example.invalid/remote.whl\n')
        with self.assertRaisesRegex(flow.PreflightError, 'DIRECT_DEPENDENCY'):
            supply.wheel_identity(path, {'name': 'fixture-pkg', 'version': '1.0'})

    def test_staged_wheel_tampering_is_detected(self):
        supply.stage(self.record, self.target)
        next((self.target / 'fds-engine-runtime/wheels').iterdir()).write_bytes(b'changed')
        with self.assertRaisesRegex(flow.PreflightError, 'STAGED_WHEEL_CHANGED'):
            supply.verify_staged(self.target, self.record)

    def test_real_pip_offline_hash_install_and_check(self):
        supply.stage(self.record, self.target)
        venv = self.root / 'venv'
        env = {'PATH': os.environ['PATH'], 'HOME': str(self.root), 'PIP_CONFIG_FILE': os.devnull}
        subprocess.run([sys.executable, '-I', '-m', 'venv', str(venv)], env=env, check=True, capture_output=True, timeout=60)
        python = str(venv / 'bin/python')
        profile = self.target / 'fds-engine-test'
        subprocess.run([python, '-I', '-m', 'pip', '--isolated', '--disable-pip-version-check',
            'install', '--no-index', '--no-cache-dir', '--find-links', str(profile / 'wheels'),
            '--only-binary=:all:', '--require-hashes', '-r', str(profile / 'requirements.lock')],
            env=env, check=True, capture_output=True, timeout=60)
        result = subprocess.run([python, '-I', '-m', 'pip', '--disable-pip-version-check', 'check'],
            env=env, check=True, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.stdout, 'No broken requirements found.\n')


class OfflineFlowTests(unittest.TestCase):
    def setUp(self):
        self.h = legacy.FlowTests()
        self.h.setUp()
        self.addCleanup(self.h.doCleanups)
        h = self.h
        self.source, self.record, self.identity = fixture(h.root)
        h.config['schema'] = 'P1-CICD-app-build-scan-2'
        h.config['offline_supply'] = self.record
        self.base = 'docker.io/library/python@' + self.identity['manifest_digest']
        for name, service in h.config['services'].items():
            service['base_image'] = self.base
            (h.repo / name / 'Dockerfile').write_text(supply.dockerfile(name, self.base))
            for mode in ('runtime', 'test'):
                (h.repo / name / ('requirements-' + mode + '.lock')).write_bytes(
                    (self.source / (name + '-' + mode) / 'requirements.lock').read_bytes())
            for filename in ('requirements.txt', 'requirements-test.txt'):
                (h.repo / name / filename).write_text('fixture-pkg==1.0\n' if filename == 'requirements.txt'
                                                     else '-r requirements.txt\n')
        h.commit()
        self.argv = []
        self.bad_digest, self.mutate_stage = False, False
        h.commands.side_effect = self.command

    def command(self, argv, cwd, env, output, timeout):
        self.argv.append(argv)
        rc = self.h.fake_command(argv, cwd, env, output, timeout)
        if output.stem == 'offline-base-resolve' and rc == 0:
            image = {'Id': self.record['config_digest'][7:], 'Digest': self.record['manifest_digest'],
                     'RepoDigests': [] if self.bad_digest else [self.base], 'Os': 'linux', 'Architecture': 'amd64',
                     'RootFS': {'Layers': self.identity['diff_ids']}}
            output.write_text(json.dumps([image]))
        if self.mutate_stage and output.stem == 'fds-engine-contract-0':
            wheel = next((self.h.evidence / 'offline-supply/fds-engine-runtime/wheels').iterdir())
            wheel.write_bytes(b'test changed supply')
        return rc

    def test_complete_order_and_commands_have_no_supply_network(self):
        rc, data = self.h.execute()
        self.assertEqual((rc, data['BUILD_SCAN']), (0, 'PASS'), data)
        self.assertEqual(data['offline_supply']['REPOSITORY_DIGEST_RESOLUTION'], 'PASS')
        self.assertFalse(any('pull' in argv for argv in self.argv))
        for argv in self.argv:
            if 'build' in argv:
                self.assertIn('--pull=never', argv)
                self.assertIn('--network=none', argv)
                self.assertIn('--http-proxy=false', argv)
                self.assertIn('--build-context', argv)
                self.assertTrue(argv[argv.index('--build-context') + 1].endswith('-runtime/wheels'))
            if 'install' in argv:
                for flag in ('--no-index', '--require-hashes', '--only-binary=:all:'):
                    self.assertIn(flag, argv)
        for env in self.h.envs:
            self.assertNotIn('HTTP_PROXY', env)
            self.assertEqual(env['PIP_CONFIG_FILE'], os.devnull)
        events = self.h.events
        self.assertLess(events.index('offline-base-resolve'), events.index('transaction-api-offline-install'))
        self.assertLess(events.index('fds-engine-contract-0'), events.index('transaction-api-build'))

    def test_config_id_alone_does_not_authorize_digest_build(self):
        self.bad_digest = True
        rc, data = self.h.execute()
        self.assertEqual(rc, 1)
        self.assertEqual(data['error_code'], 'OFFLINE_DIGEST_RESOLUTION_MISMATCH')
        self.assertFalse(any('install' in a or 'build' in a or 'pull' in a for a in self.argv))
        self.h.scanner.assert_not_called()

    def test_ordinary_setup_rejected_before_commands(self):
        self.h.config['services']['transaction-api']['setup'] = [copy.deepcopy(self.h.config['services']['transaction-api']['unit'])]
        rc, data = self.h.execute()
        self.assertEqual((rc, data['error_code']), (1, 'OFFLINE_SETUP_MUST_BE_GENERATED'))
        self.h.commands.assert_not_called()

    def test_changed_app_lock_stops_before_podman(self):
        (self.h.repo / 'transaction-api/requirements-runtime.lock').write_text('unreviewed\n')
        self.h.commit()
        rc, data = self.h.execute()
        self.assertEqual((rc, data['error_code']), (1, 'SOURCE_LOCK_MISMATCH'))
        self.assertFalse(any(a[0] == flow.PODMAN for a in self.argv))

    def test_remote_add_dockerfile_stops_before_podman(self):
        p = self.h.repo / 'transaction-api/Dockerfile'
        p.write_text(p.read_text() + 'ADD https://example.invalid/package /app/\n')
        self.h.commit()
        rc, data = self.h.execute()
        self.assertEqual((rc, data['error_code']), (1, 'OFFLINE_DOCKERFILE_PROFILE_REQUIRED'))
        self.assertFalse(any(a[0] == flow.PODMAN for a in self.argv))

    def test_test_mutation_cannot_enter_build_supply(self):
        self.mutate_stage = True
        rc, data = self.h.execute()
        self.assertEqual((rc, data['error_code']), (1, 'STAGED_WHEEL_CHANGED'))
        self.assertFalse(any('build' in a for a in self.argv))

    def test_offline_install_failure_blocks_tests_build_scan(self):
        self.h.fault = 'transaction-api-offline-install'
        rc, data = self.h.execute()
        self.assertEqual(rc, 1)
        self.assertEqual(data['error_code'], 'COMMAND_FAILED:transaction-api-offline-install')
        self.assertFalse(any('build' in a or '/fixture-test' in a for a in self.argv))
        self.h.scanner.assert_not_called()

    def test_source_requirement_change_requires_new_supply(self):
        (self.h.repo / 'fds-engine/requirements.txt').write_text('fixture-pkg==2.0\n')
        self.h.commit()
        rc, data = self.h.execute()
        self.assertEqual((rc, data['error_code']), (1, 'SOURCE_REQUIREMENTS_CHANGED'))


class ProbeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source, self.record, self.identity = fixture(self.root)
        self.base = 'docker.io/library/python@' + self.identity['manifest_digest']
        self.folder = self.root / 'probe'
        self.folder.mkdir()
        self.bad_digest = False
        self.argv = []

    def command(self, argv, cwd, env, output, timeout):
        self.argv.append(argv)
        if output.stem == 'podman-version':
            output.write_text('podman version 6.1.0\n')
        elif output.stem == 'podman-rootless':
            output.write_text('true\n')
        elif output.stem == 'offline-base-resolve':
            output.write_text(json.dumps([{'Id': self.identity['config_digest'][7:],
                'Digest': self.identity['manifest_digest'], 'RepoDigests': [] if self.bad_digest else [self.base],
                'Os': 'linux', 'Architecture': 'amd64', 'RootFS': {'Layers': self.identity['diff_ids']}}]))
        elif output.stem == 'offline-base-build':
            output.write_text('123\n')
            Path(argv[argv.index('--iidfile') + 1]).write_text('sha256:' + '1' * 64 + '\n')
            self.assertIn('--network=none', argv)
            self.assertIn('--pull=never', argv)
            self.assertEqual((Path(argv[-1]) / 'Dockerfile').read_text().splitlines()[0], 'FROM ' + self.base)
        else:
            output.write_text('fixture\n')
        self.assertFalse(any('PROXY' in key or 'TOKEN' in key for key in env))
        return 0

    def test_probe_builds_only_after_repository_digest_resolution(self):
        with patch.object(probe, 'run_command', side_effect=self.command), patch.object(probe, 'idle'):
            rc, result = probe.execute(self.record, self.base, self.folder)
        self.assertEqual((rc, result['OFFLINE_PROBE'], result['BASE_DIGEST_BUILD']), (0, 'PASS', 'PASS'))
        self.assertEqual(result['APP_TEST_BUILD_SCAN'], 'NOT_EXECUTED')
        self.assertFalse(any('pull' in argv for argv in self.argv))
        self.assertEqual(len(result['steps']), 6)

    def test_probe_keeps_failed_resolution_and_unexecuted_build_distinct(self):
        self.bad_digest = True
        with patch.object(probe, 'run_command', side_effect=self.command), patch.object(probe, 'idle'):
            rc, result = probe.execute(self.record, self.base, self.folder)
        self.assertEqual((rc, result['REPOSITORY_DIGEST_RESOLUTION'], result['BASE_DIGEST_BUILD']),
                         (1, 'FAIL', 'NOT_EXECUTED'))
        self.assertFalse(any('build' in argv for argv in self.argv))

    def test_bundle_deterministic_and_importable_with_embedded_record(self):
        a, b = self.root / 'a.pyz', self.root / 'b.pyz'
        self.assertEqual(bundle.build(a), bundle.build(b))
        result = subprocess.run([sys.executable, '-I', '-B', str(a), '--help'],
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('--prepare', result.stdout)
        with zipfile.ZipFile(a) as z:
            record = json.loads(z.read('offline-probe-input.json'))
        (supply_v3 if 'images' in record else supply).validate_record(record)

    def test_bundle_reads_actual_input_via_dot_relative_and_absolute_paths(self):
        archive = self.root / 'probe.pyz'
        bundle.build(archive)
        with zipfile.ZipFile(archive) as z:
            expected = hashlib.sha256(z.read('offline-probe-input.json')).hexdigest()
        code = ('import hashlib,sys; sys.path.insert(0,sys.argv[1]); '
                'import probe_offline_supply as p; '
                'print(hashlib.sha256(p.read_bundle_input()).hexdigest())')
        for path in ('./probe.pyz', 'probe.pyz', str(archive)):
            with self.subTest(path=path):
                result = subprocess.run([sys.executable, '-I', '-B', '-c', code, path],
                    cwd=self.root, capture_output=True, text=True, timeout=15)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), expected)


if __name__ == '__main__':
    unittest.main()
