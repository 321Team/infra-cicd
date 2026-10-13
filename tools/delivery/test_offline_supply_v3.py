"""Schema v3 regression tests for approved Wolfi/apko two-stage offline delivery."""
import copy
import gzip
import io
import hashlib
import json
from pathlib import Path
import shutil
import tarfile
import tempfile
import unittest
from unittest.mock import patch

import app_workflow as app
import build_scan as flow
import offline_supply_v3 as supply
import probe_offline_supply as probe
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


def write_apko_docker_archive(path, role):
    layer_plain = ('fixture-layer:' + role + '\n').encode()
    layer_raw = gzip.compress(layer_plain, mtime=0)
    diff_id = 'sha256:' + hashlib.sha256(layer_plain).hexdigest()
    config = {
        'architecture': 'amd64',
        'os': 'linux',
        'rootfs': {'type': 'layers', 'diff_ids': [diff_id]},
        'config': {'User': '10001' if role == 'runtime' else ''},
    }
    config_raw = json.dumps(config, sort_keys=True, separators=(',', ':')).encode()
    config_digest = 'sha256:' + hashlib.sha256(config_raw).hexdigest()
    layer_name = hashlib.sha256(layer_raw).hexdigest() + '.tar.gz'
    manifest_raw = json.dumps([{
        'Config': config_digest,
        'RepoTags': ['fds-apko-wolfi-' + role + ':fixture'],
        'Layers': [layer_name],
    }], sort_keys=True, separators=(',', ':')).encode()
    index_raw = json.dumps({'manifests': []}, sort_keys=True, separators=(',', ':')).encode()
    extra_raw = b'{}'
    extra_name = 'sha256:' + hashlib.sha256(extra_raw).hexdigest()
    with tarfile.open(path, 'w') as archive:
        for name, raw in (
            (config_digest, config_raw),
            (layer_name, layer_raw),
            ('manifest.json', manifest_raw),
            (extra_name, extra_raw),
            ('index.json', index_raw),
        ):
            item = tarfile.TarInfo(name)
            item.size = len(raw)
            archive.addfile(item, io.BytesIO(raw))
    return supply.inspect_supply_archive(path)


def fixture(root):
    source, old, _ = legacy_fixture(root)
    (source / 'base.oci.tar').unlink()
    profiles = source / 'profiles'
    profiles.mkdir()
    for name in supply.PROFILES:
        (source / name).rename(profiles / name)
    identities = {
        role: write_apko_docker_archive(source / (role + '.oci.tar'), role)
        for role in supply.ROLES
    }
    record = {
        'directory': str(source),
        'images': copy.deepcopy(identities),
        'profiles': copy.deepcopy(old['profiles']),
        'requirements': copy.deepcopy(old['requirements']),
        'source_inputs': {
            path: hashlib.sha256(raw).hexdigest()
            for path, raw in SOURCE_INPUT_BYTES.items()
        },
    }
    return source, record, identities


class OfflineSupplyV3Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source, self.record, self.identities = fixture(self.root)
        self.target = self.root / 'staged'

    def test_two_images_profiles_and_wheels_stage_fail_closed(self):
        identities = supply.stage(self.record, self.target)
        self.assertEqual(set(identities), set(supply.ROLES))
        self.assertTrue(all(item['format'] == 'docker-archive' for item in identities.values()))
        supply.verify_staged(self.target, self.record)
        (self.target / 'builder.oci.tar').write_bytes(b'changed')
        with self.assertRaises(flow.PreflightError):
            supply.verify_staged(self.target, self.record)

    def test_record_shape_requires_exact_builder_and_runtime(self):
        for mutate in (
            lambda r: r['images'].pop('builder'),
            lambda r: r['images'].update(extra=copy.deepcopy(r['images']['runtime'])),
            lambda r: r['images']['runtime'].pop('config_digest'),
            lambda r: r['images']['runtime'].__setitem__('manifest_json_sha256', 'not-a-hash'),
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
                    (self.source / 'profiles' / (name + '-' + mode) / 'requirements.lock').read_bytes())
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
        self.source, self.record, self.identities = fixture(h.root)
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
                    (self.source / 'profiles' / (name + '-' + mode) / 'requirements.lock').read_bytes())
        h.commit()
        self.argv = {}
        h.commands.side_effect = self.command
        self.smoke = patch.object(flow.image_smoke, 'check_image', side_effect=self.smoke_pass).start()

    def smoke_pass(self, command, podman, name, identity, evidence, receipt):
        receipt.update(status='PASS', identity=identity, scope='MOCK_ONLY')

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
                'RootFS': {'Layers': self.identities[role]['diff_ids']},
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
            self.assertIn('--pull=never', argv)
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
        self.assertEqual(result['schema'], 'P1-CICD-app-build-scan-3')
        self.assertTrue(set(supply.SOURCE_INPUTS) <= set(result['files']))

        apko_path = self.h.repo / supply.SOURCE_INPUTS[0]
        original_apko = apko_path.read_bytes()
        apko_path.write_bytes(original_apko + b'# changed\n')
        self.h.commit()
        changed = self.h.root / 'handoff-v3-apko-changed'
        changed.mkdir()
        with self.assertRaisesRegex(flow.PreflightError, 'SOURCE_APKO_INPUT_CHANGED'):
            app.validate_handoff(
                self.h.config, self.h.repo, app.command_runner(changed, {}, []))
        apko_path.write_bytes(original_apko)
        self.h.commit()

        docker = self.h.repo / 'transaction-api/Dockerfile'
        docker.write_text(docker.read_text().replace('USER 10001:999', 'USER 65532:65532'))
        self.h.commit()
        bad = self.h.root / 'handoff-v3-bad'
        bad.mkdir()
        with self.assertRaisesRegex(flow.PreflightError, 'HANDOFF_DOCKERFILE_MISMATCH'):
            app.validate_handoff(
                self.h.config, self.h.repo, app.command_runner(bad, {}, []))

    def test_final_image_failure_blocks_scan_for_either_service(self):
        for failed in flow.SERVICES:
            with self.subTest(service=failed):
                self.h.evidence = self.h.root / ('failed-smoke-' + failed)
                self.h.evidence.mkdir()
                self.h.scanner.reset_mock()

                def fail(command, podman, name, identity, evidence, receipt):
                    receipt['status'] = 'FAIL' if name == failed else 'PASS'
                    if name == failed:
                        raise flow.image_smoke.SmokeError('SMOKE_PROBE_NOT_PASS')

                self.smoke.side_effect = fail
                rc, data = self.h.execute()
                self.assertEqual((rc, data['BUILD_SCAN']), (1, 'FAIL'))
                self.assertEqual(data['error_code'], 'SMOKE_PROBE_NOT_PASS')
                self.h.scanner.assert_not_called()

    def test_smoke_must_explicitly_pass_and_archive_must_stay_bound(self):
        for failure in ('missing-pass', 'changed-archive'):
            with self.subTest(failure=failure):
                self.h.evidence = self.h.root / failure
                self.h.evidence.mkdir()
                self.h.scanner.reset_mock()

                def fail(command, podman, name, identity, evidence, receipt):
                    if failure == 'changed-archive':
                        receipt['status'] = 'PASS'
                        (evidence / (name + '.oci.tar')).write_bytes(b'changed fixture')

                self.smoke.side_effect = fail
                rc, data = self.h.execute()
                self.assertEqual((rc, data['BUILD_SCAN']), (1, 'FAIL'))
                self.assertIn(data['error_code'], ('FINAL_IMAGE_STARTUP_NOT_PASS', 'BUILT_ARCHIVE_CHANGED'))
                self.h.scanner.assert_not_called()

    def test_v3_rejects_mutable_dockerfile_before_podman(self):
        docker = self.h.repo / 'transaction-api/Dockerfile'
        docker.write_text('FROM python:3.13\n')
        self.h.commit()
        rc, data = self.h.execute()
        self.assertEqual((rc, data['error_code']), (1, 'DOCKERFILE_TWO_STAGE_PROFILE_REQUIRED'))
        self.assertNotIn('podman-version', self.argv)


class ProbeV3Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source, self.record, self.identities = fixture(self.root)
        self.folder = self.root / 'probe'
        self.folder.mkdir()
        self.argv = {}

    def command(self, argv, cwd, env, output, timeout):
        label = output.stem
        self.argv[label] = list(argv)
        if label == 'podman-version':
            output.write_text('podman version 6.1.0\n')
        elif label == 'podman-rootless':
            output.write_text('true\n')
        elif label in ('offline-builder-resolve', 'offline-runtime-resolve'):
            role = label.split('-')[1]
            image = self.record['images'][role]
            output.write_text(json.dumps([{
                'Id': image['config_digest'],
                'Os': 'linux',
                'Architecture': 'amd64',
                'RootFS': {'Layers': self.identities[role]['diff_ids']},
            }]))
        elif label == 'offline-base-build':
            Path(argv[argv.index('--iidfile') + 1]).write_text('sha256:' + '1' * 64 + '\n')
            output.write_text('probe build\n')
        else:
            output.write_text('fixture\n')
        return 0

    def test_v3_probe_imports_both_images_and_builds_without_network_pull(self):
        with patch.object(probe, 'run_command', side_effect=self.command), \
                patch.object(probe, 'idle'):
            rc, result = probe.execute(self.record, None, self.folder)
        self.assertEqual((rc, result['OFFLINE_PROBE'], result['IMAGE_IMPORT']),
                         (0, 'PASS', 'PASS'), result)
        self.assertEqual(result['REPOSITORY_DIGEST_RESOLUTION'], 'NOT_APPLICABLE')
        self.assertEqual(set(result['imports']), set(supply.ROLES))
        for role in supply.ROLES:
            self.assertTrue(result['imports'][role]['reference'].startswith(
                'localhost/fds-apko-' + role + ':cicd-offline-'))
        self.assertFalse(any('pull' in argv for argv in self.argv.values()))
        build = self.argv['offline-base-build']
        self.assertIn('--pull=never', build)
        self.assertIn('--network=none', build)
        self.assertTrue(any(
            a.startswith('BUILDER_IMAGE=localhost/fds-apko-builder:') for a in build))
        self.assertTrue(any(
            a.startswith('RUNTIME_IMAGE=localhost/fds-apko-runtime:') for a in build))


if __name__ == '__main__':
    unittest.main()
