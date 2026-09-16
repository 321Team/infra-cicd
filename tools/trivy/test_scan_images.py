"""Local fixtures and mocked Trivy/DB calls; never run Podman, Push or Deploy."""
import copy
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch

import oci_archive as oci
import scan_images as scan
from prepare_db import PreflightError
from verify_runner import sha256


def encoded(value):
    return json.dumps(value).encode()


def image_members():
    members = {'oci-layout': encoded({'imageLayoutVersion': '1.0.0'})}

    def add(raw, media_type):
        digest = hashlib.sha256(raw).hexdigest()
        members['blobs/sha256/' + digest] = raw
        return {'mediaType': media_type, 'digest': 'sha256:' + digest, 'size': len(raw)}

    layer = add(b'fixture layer bytes; not a runnable application', next(iter(oci.LAYERS)))
    config = add(encoded({'os': 'linux', 'architecture': 'amd64',
                          'rootfs': {'type': 'layers', 'diff_ids': [layer['digest']]}}), oci.CONFIG)
    manifest = add(encoded({'schemaVersion': 2, 'mediaType': oci.MANIFEST,
                            'config': config, 'layers': [layer]}), oci.MANIFEST)
    members['index.json'] = encoded({'schemaVersion': 2, 'manifests': [manifest]})
    return members


def write_archive(path, members, extra=None):
    with tarfile.open(path, 'w') as tar:
        for name, raw in members.items():
            item = tarfile.TarInfo(name)
            item.size = len(raw)
            tar.addfile(item, io.BytesIO(raw))
        if extra:
            tar.addfile(extra)


def report(identity, severity=None):
    data = {'SchemaVersion': 2, 'Trivy': {'Version': '0.74.0'},
            'ArtifactType': 'container_image',
            'Metadata': {'ImageID': identity['config_digest'], 'DiffIDs': identity['diff_ids']},
            'Results': [{'Class': 'os-pkgs', 'Type': 'debian',
                         'Packages': [{'Name': 'libc6', 'Version': '2.36'}]},
                        {'Class': 'lang-pkgs', 'Type': 'python-pkg',
                         'Packages': [{'Name': 'fastapi', 'Version': '0.115.5'}]}]}
    if severity:
        data['Results'][1]['Vulnerabilities'] = [
            {'Severity': severity, 'VulnerabilityID': 'CVE-FIXTURE',
             'PkgName': 'fastapi', 'InstalledVersion': '0.115.5'}]
    return data


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'image.tar'
        self.members = image_members()

    def test_manifest_is_not_archive_hash_or_config_id(self):
        write_archive(self.path, self.members)
        result = oci.inspect_archive(self.path)
        self.assertEqual(result['archive_sha256'], sha256(self.path))
        self.assertEqual(len({result['manifest_digest'], result['config_digest'],
                              'sha256:' + result['archive_sha256']}), 3)

    def test_tampered_blob_blocked(self):
        name = next(name for name in self.members if name.startswith('blobs/'))
        self.members[name] = b'x' * len(self.members[name])
        write_archive(self.path, self.members)
        with self.assertRaisesRegex(PreflightError, 'OCI_BLOB_HASH_MISMATCH'):
            oci.inspect_archive(self.path)

    def test_ambiguous_multiple_images_blocked(self):
        index = json.loads(self.members['index.json'])
        index['manifests'] *= 2
        self.members['index.json'] = encoded(index)
        write_archive(self.path, self.members)
        with self.assertRaisesRegex(PreflightError, 'OCI_SINGLE_IMAGE_REQUIRED'):
            oci.inspect_archive(self.path)

    def test_mixed_docker_format_path_traversal_and_duplicate_blocked(self):
        for name in ('manifest.json', '../escape', '/absolute', 'index.json'):
            with self.subTest(name=name):
                write_archive(self.path, self.members, tarfile.TarInfo(name))
                with self.assertRaises(PreflightError):
                    oci.inspect_archive(self.path)

    def test_links_and_special_files_blocked(self):
        for kind in (tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.FIFOTYPE):
            item = tarfile.TarInfo('blobs/sha256/' + 'a' * 64)
            item.type, item.linkname = kind, '/etc/passwd'
            write_archive(self.path, self.members, item)
            with self.subTest(kind=kind), self.assertRaises(PreflightError):
                oci.inspect_archive(self.path)

    def test_missing_or_external_descriptor_blocked(self):
        for mutation in ('missing', 'external', 'size', 'index'):
            members = copy.deepcopy(self.members)
            index = json.loads(members['index.json'])
            descriptor = index['manifests'][0]
            if mutation == 'missing':
                del members['blobs/sha256/' + descriptor['digest'][7:]]
            elif mutation == 'external':
                descriptor['urls'] = ['https://example.invalid/blob']
            elif mutation == 'index':
                descriptor['mediaType'] = 'application/vnd.oci.image.index.v1+json'
            else:
                descriptor['size'] += 1
            members['index.json'] = encoded(index)
            write_archive(self.path, members)
            with self.subTest(mutation=mutation), self.assertRaises(PreflightError):
                oci.inspect_archive(self.path)

    def test_duplicate_json_keys_blocked(self):
        self.members['oci-layout'] = b'{"imageLayoutVersion":"bad","imageLayoutVersion":"1.0.0"}'
        write_archive(self.path, self.members)
        with self.assertRaisesRegex(PreflightError, 'DUPLICATE_KEY'):
            oci.inspect_archive(self.path)


class ScanTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.archive = self.root / 'source.tar'
        write_archive(self.archive, image_members())
        self.identity = oci.inspect_archive(self.archive)
        self.evidence = self.root / 'evidence'
        self.evidence.mkdir()
        self.context = {'workflow_sha': 'b' * 40, 'run_id': '123', 'attempt': 1}
        self.archives = {name: self.archive for name in scan.SERVICES}
        self.calls = []
        self.severity = None
        self.fault = None
        self.env = {'TRIVY_IGNORE_UNFIXED': 'true', 'TRIVY_SEVERITY': 'LOW',
                    'GITHUB_TOKEN': 'fixture-token', 'HTTPS_PROXY': 'http://proxy.invalid'}
        self.addCleanup(patch.stopall)
        self.prepare = patch.object(scan, 'prepare', side_effect=self.fake_prepare).start()
        patch.object(scan, 'verify_binary').start()
        self.run = patch.object(scan.subprocess, 'run', side_effect=self.fake_scan).start()

    def fake_prepare(self, evidence, context, env):
        db = evidence / 'cache' / 'db'
        db.mkdir(parents=True)
        (db / 'trivy.db').write_bytes(b'fixture DB; scanner calls are mocked')
        now = datetime.now(timezone.utc)
        data = {'Version': 2, 'UpdatedAt': (now - timedelta(hours=1)).isoformat(),
                'DownloadedAt': now.isoformat(), 'NextUpdate': (now + timedelta(hours=12)).isoformat()}
        (db / 'metadata.json').write_bytes(encoded(data))
        result = {'DB_PREFLIGHT': 'PASS', 'context': context,
                  'db_sha256': sha256(db / 'trivy.db'), 'metadata_sha256': sha256(db / 'metadata.json')}
        (evidence / 'result.json').write_bytes(encoded(result))
        return 0, result

    def fake_scan(self, command, **kwargs):
        self.calls.append((command, kwargs))
        if self.fault == 'timeout':
            raise subprocess.TimeoutExpired(command, 630)
        if self.fault == 'signal':
            scan.interrupted(15, None)
        severity = self.severity
        if self.fault == 'second_high':
            severity = 'HIGH' if len(self.calls) == 2 else None
        data = report(self.identity, severity)
        output = Path(command[command.index('--output') + 1])
        if self.fault == 'coverage':
            data['Results'] = []
        if self.fault == 'identity':
            data['Metadata']['ImageID'] = 'sha256:' + '0' * 64
        output.write_bytes(b'{broken' if self.fault == 'json' else encoded(data))
        if self.fault == 'db_changed':
            (self.evidence / 'db/cache/db/trivy.db').write_bytes(b'changed')
        if self.fault == 'archive_changed':
            (self.evidence / 'transaction-api.oci.tar').write_bytes(b'changed')
        if self.fault == 'earlier_report_changed' and len(self.calls) == 2:
            (self.evidence / 'transaction-api/report.json').write_bytes(b'changed')
        rc = 10 if severity else 0
        if self.fault == 'rc':
            rc = 1
        if self.fault == 'mismatch':
            rc = 10
        return subprocess.CompletedProcess(command, rc)

    def execute(self):
        return scan.scan_pair(self.evidence, self.context, 'c' * 40, self.archives, self.env)

    def test_clean_pair_succeeds_without_authorizing_delivery(self):
        rc, result = self.execute()
        self.assertEqual((rc, result['IMAGE_SCAN']), (0, 'PASS'))
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(self.prepare.call_count, 1)
        self.assertEqual(result['DELIVERY_GATE'], 'NOT_VERIFIED')
        self.assertEqual(result['source_approval'], 'NOT_VERIFIED')
        self.assertEqual(result['push'], 'NOT_EXECUTED')
        self.assertEqual(result['deploy'], 'NOT_EXECUTED')
        for line in (self.evidence / 'SHA256SUMS').read_text().splitlines():
            digest, name = line.split('  ')
            self.assertEqual(sha256(self.evidence / name), digest)

    def test_flags_environment_and_empty_config_prevent_ignore_inheritance(self):
        self.execute()
        for command, kwargs in self.calls:
            self.assertEqual(command[command.index('--severity') + 1], 'HIGH,CRITICAL')
            self.assertEqual(command[command.index('--pkg-types') + 1], 'os,library')
            self.assertIn('--skip-db-update', command)
            self.assertIn('--ignore-unfixed=false', command)
            self.assertNotIn('TRIVY_IGNORE_UNFIXED', kwargs['env'])
            self.assertNotIn('GITHUB_TOKEN', kwargs['env'])
            self.assertEqual(kwargs['env']['HTTPS_PROXY'], self.env['HTTPS_PROXY'])
            self.assertEqual(Path(command[command.index('--config') + 1]).read_text(), '{}\n')
            self.assertEqual(Path(command[command.index('--ignorefile') + 1]).read_text(), '')

    def test_high_without_fix_blocks_pair_and_stops_next_scan(self):
        self.severity = 'HIGH'
        rc, result = self.execute()
        self.assertEqual((rc, result['IMAGE_SCAN']), (10, 'BLOCK'))
        self.assertEqual(len(self.calls), 1)

    def test_critical_blocks_pair(self):
        self.severity = 'CRITICAL'
        self.assertEqual(self.execute()[0], 10)

    def test_second_image_failure_blocks_the_entire_pair(self):
        self.fault = 'second_high'
        rc, result = self.execute()
        self.assertEqual((rc, result['IMAGE_SCAN']), (10, 'BLOCK'))
        self.assertEqual(result['images']['transaction-api']['scan'], 'PASS')
        self.assertEqual(result['images']['fds-engine']['scan'], 'BLOCK')
        self.assertEqual(result['push'], 'NOT_EXECUTED')

    def test_db_preflight_error_stops_scanner(self):
        self.prepare.side_effect = None
        self.prepare.return_value = (1, {'DB_PREFLIGHT': 'FAIL'})
        self.assertEqual(self.execute()[0], 1)
        self.run.assert_not_called()

    def test_missing_service_stops_network_and_scanner(self):
        del self.archives['fds-engine']
        self.assertEqual(self.execute()[0], 1)
        self.prepare.assert_not_called()
        self.run.assert_not_called()

    def test_unpinned_source_stops_network_and_scanner(self):
        rc, _ = scan.scan_pair(self.evidence, self.context, 'main', self.archives, self.env)
        self.assertEqual(rc, 1)
        self.prepare.assert_not_called()

    def test_expiry_during_scan_blocks(self):
        future = datetime.now(timezone.utc) + timedelta(days=1)
        with patch.object(scan, 'utcnow', side_effect=[datetime.now(timezone.utc),
                                                     datetime.now(timezone.utc) + timedelta(minutes=1),
                                                     future, future]):
            rc, result = self.execute()
        self.assertEqual(rc, 1)
        self.assertEqual(result['error_code'], 'DB_REFRESH_REQUIRED')

    def test_binary_failure_stops_scanner(self):
        with patch.object(scan, 'verify_binary', side_effect=PreflightError('BINARY_HASH_MISMATCH')):
            self.assertEqual(self.execute()[0], 1)
        self.run.assert_not_called()

    def test_failures_never_produce_scan_pass(self):
        for fault in ('timeout', 'signal', 'coverage', 'identity', 'json', 'db_changed',
                      'archive_changed', 'rc', 'mismatch', 'earlier_report_changed'):
            with self.subTest(fault=fault):
                self.evidence = self.root / fault
                self.evidence.mkdir()
                self.calls = []
                self.fault = fault
                rc, result = self.execute()
                self.assertEqual(rc, 1)
                self.assertEqual(result['IMAGE_SCAN'], 'FAIL')
                self.assertEqual(result['push'], 'NOT_EXECUTED')
                self.assertEqual(result['deploy'], 'NOT_EXECUTED')

    def test_bad_report_and_exit_combinations_block(self):
        good = report(self.identity)
        samples = []
        for field, value in [('SchemaVersion', True), ('ArtifactType', 'filesystem'),
                             ('Results', None), ('Results', [])]:
            data = copy.deepcopy(good)
            data[field] = value
            samples.append((data, 0))
        data = copy.deepcopy(good)
        data['Results'][0]['ExperimentalModifiedFindings'] = [{}]
        samples.append((data, 0))
        data = copy.deepcopy(good)
        del data['Results'][1]['Packages']
        samples.append((data, 0))
        samples += [(report(self.identity, 'HIGH'), 0), (good, 10), (good, 1),
                    (report(self.identity, 'LOW'), 10)]
        for data, rc in samples:
            with self.subTest(data=data, rc=rc), self.assertRaises(PreflightError):
                scan.check_report(data, rc, self.identity)


if __name__ == '__main__':
    unittest.main()
