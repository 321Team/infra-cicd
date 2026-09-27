"""Schema v3 regression tests for approved Wolfi/apko two-stage offline delivery."""
import copy
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest

import app_workflow as app
import build_scan as flow
import offline_supply_v3 as supply
import test_build_scan as legacy
from test_offline_supply import fixture as legacy_fixture


SOURCE_INPUT_BYTES = {
    path: ('fixture:' + path + '\n').encode()
    for path in supply.SOURCE_INPUTS
}


def write_source_inputs(root):
    for path, raw in SOURCE_INPUT_BYTES.items():
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)


def fixture(root):
    source, old, identity = legacy_fixture(root)
    base = source / 'base.oci.tar'
    for role in supply.ROLES:
        shutil.copy2(base, source / (role + '.oci.tar'))
    base.unlink()
    record = {
        'directory': str(source),
        'images': {
            role: {key: identity[key] for key in
                   ('archive_sha256', 'manifest_digest', 'config_digest')}
            for role in supply.ROLES
        },
        'profiles': copy.deepcopy(old['profiles']),
        'requirements': copy.deepcopy(old['requirements']),
        'source_inputs': {
            path: hashlib.sha256(raw).hexdigest()
            for path, raw in SOURCE_INPUT_BYTES.items()
        },
    }
    return source, record, identity


class OfflineSupplyV3Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source, self.record, self.identity = fixture(self.root)
        self.target = self.root / 'staged'

    def test_two_images_profiles_and_wheels_stage_fail_closed(self):
        identities = supply.stage(self.record, self.target)
        self.assertEqual(set(identities), set(supply.ROLES))
        supply.verify_staged(self.target, self.record)
        (self.target / 'builder.oci.tar').write_bytes(b'changed')
        with self.assertRaises(flow.PreflightError):
            supply.verify_staged(self.target, self.record)

    def test_record_shape_requires_exact_builder_and_runtime(self):
        for mutate in (
            lambda r: r['images'].pop('builder'),
            lambda r: r['images'].update(extra=copy.deepcopy(r['images']['runtime'])),
            lambda r: r['images']['runtime'].pop('config_digest'),
        ):
            record = copy.deepcopy(self.record)
            mutate(record)
            with self.subTest(mutate=mutate), self.assertRaises(flow.PreflightError):
                supply.validate_record(record)

    def test_exact_operating_dockerfiles_and_source_locks(self):
        app_root = self.root / 'app-source'
        app_root.mkdir()
        write_source_inputs(app_root)
        for name in supply.SERVICES:
            folder = app_root / name
            folder.mkdir()
            (folder / 'Dockerfile').write_text(supply.dockerfile(name))
            (folder / 'requirements.txt').write_text('fixture-pkg==1.0\n')
            (folder / 'requirements-test.txt').write_text('-r requirements.txt\n')
            for mode in ('runtime', 'test'):
                (folder / ('requirements-' + mode + '.lock')).write_bytes(
                    (self.source / (name + '-' + mode) / 'requirements.lock').read_bytes())
        supply.stage(self.record, self.target)
        config = {'offline_supply': self.record, 'services': {
            name: {'context': name, 'dockerfile': name + '/Dockerfile'}
            for name in supply.SERVICES}}
        supply.validate_source(app_root, config, self.target)
        docker = app_root / 'transaction-api/Dockerfile'
        docker.write_text(docker.read_text().replace('USER 10001:999', 'USER 65532:65532'))
        with self.assertRaisesRegex(flow.PreflightError, 'OFFLINE_DOCKERFILE_PROFILE_REQUIRED'):
            supply.validate_source(app_root, config, self.target)


class FlowV3Tests(unittest.TestCase):
    def setUp(self):
        self.h = legacy.FlowTests()
        self.h.setUp()
        self.addCleanup(self.h.doCleanups)
        h = self.h
        self.source, self.record, self.identity = fixture(h.root)
        write_source_inputs(h.repo)
        h.config['schema'] = 'P1-CICD-app-build-scan-3'
        h.config['offline_supply'] = self.record
        for name, service in h.config['services'].items():
            service.pop('base_image')
            (h.repo / name / 'Dockerfile').write_text(supply.dockerfile(name))
            (h.repo / name / 'requirements.txt').write_text('fixture-pkg==1.0\n')
            (h.repo / name / 'requirements-test.txt').write_text('-r requirements.txt\n')
            for mode in ('runtime', 'test'):
                (h.repo / name / ('requirements-' + mode + '.lock')).write_bytes(
                    (self.source / (name + '-' + mode) / 'requirements.lock').read_bytes())
        h.commit()
        self.argv = {}
        h.commands.side_effect = self.command

    def command(self, argv, cwd, env, output, timeout):
        label = output.stem
        self.argv[label] = list(argv)
        rc = self.h.fake_command(argv, cwd, env, output, timeout)
        if label in ('offline-builder-resolve', 'offline-runtime-resolve') and rc == 0:
            role = label.split('-')[1]
            image = self.record['images'][role]
            output.write_text(json.dumps([{
                'Id': image['config_digest'],
                'Os': 'linux',
                'Architecture': 'amd64',
                'RootFS': {'Layers': self.identity['diff_ids']},
            }]))
        return rc

    def test_v3_runs_tests_in_builder_and_builds_with_two_local_images(self):
        rc, data = self.h.execute()
        self.assertEqual((rc, data['BUILD_SCAN']), (0, 'PASS'), data)
        self.assertEqual(data['offline_supply']['IMAGE_IMPORT'], 'PASS')
        self.assertFalse(any('pull' in argv for argv in self.argv.values()))
        for label in ('transaction-api-unit-0', 'fds-engine-contract-0'):
            argv = self.argv[label]
            self.assertIn('run', argv)
            self.assertIn('--network=none', argv)
            self.assertTrue(any('fds-apko-builder:cicd-offline-' in a for a in argv))
        for name in flow.SERVICES:
            argv = self.argv[name + '-build']
            self.assertIn('--pull=never', argv)
            self.assertIn('--network=none', argv)
            self.assertIn('--build-context', argv)
            self.assertTrue(any(
                a.startswith('BUILDER_IMAGE=localhost/fds-apko-builder:') for a in argv))
            self.assertTrue(any(
                a.startswith('RUNTIME_IMAGE=localhost/fds-apko-runtime:') for a in argv))

    def test_v3_handoff_binds_exact_two_stage_dockerfiles(self):
        folder = self.h.root / 'handoff-v3'
        folder.mkdir()
        result = app.validate_handoff(
            self.h.config, self.h.repo, app.command_runner(folder, {}, []))
        self.assertEqual(result['HANDOFF'], 'PASS')
        docker = self.h.repo / 'transaction-api/Dockerfile'
        docker.write_text(docker.read_text().replace('USER 10001:999', 'USER 65532:65532'))
        self.h.commit()
        bad = self.h.root / 'handoff-v3-bad'
        bad.mkdir()
        with self.assertRaisesRegex(flow.PreflightError, 'HANDOFF_DOCKERFILE_MISMATCH'):
            app.validate_handoff(
                self.h.config, self.h.repo, app.command_runner(bad, {}, []))

    def test_v3_rejects_mutable_dockerfile_before_podman(self):
        docker = self.h.repo / 'transaction-api/Dockerfile'
        docker.write_text('FROM python:3.13\n')
        self.h.commit()
        rc, data = self.h.execute()
        self.assertEqual((rc, data['error_code']), (1, 'DOCKERFILE_TWO_STAGE_PROFILE_REQUIRED'))
        self.assertNotIn('podman-version', self.argv)


if __name__ == '__main__':
    unittest.main()
