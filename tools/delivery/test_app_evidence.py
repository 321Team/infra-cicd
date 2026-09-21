"""Synthetic receipts and real tar/OCI bytes; no network, app, Podman or Trivy execution."""
import copy
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest

import app_evidence as evidence
from oci_test_fixtures import image_members, write_archive
from test_scan_images import report


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.run = self.root / 'run'
        self.run.mkdir()
        self.expected = {'workflow_sha': 'a' * 40, 'source_sha': 'b' * 40, 'run_id': '123',
                         'attempt': 1, 'input_sha256': 'c' * 64}
        self.context = {k: v for k, v in self.expected.items() if k != 'source_sha'}
        self.context.update(host='cicd-runner01', user='server01_cicd-runner', uid=1000,
                            run_url='https://github.com/321Team/infra-cicd/actions/runs/123')
        self.top = {'APP_WORKFLOW': 'PASS', 'context': self.context, 'steps': [], 'HANDOFF': 'PASS',
                    'APP_TEST_BUILD_SCAN': 'PASS', 'PUSH': 'NOT_EXECUTED', 'DEPLOY': 'NOT_EXECUTED',
                    'DELIVERY_GATE': 'NOT_VERIFIED', 'started_at': self.time(0), 'finished_at': self.time(50)}
        for name in ('source-init', 'source-origin-set', 'source-fetch', 'source-fetched', 'source-head-set',
                     'handoff-head', 'handoff-origin', 'handoff-tree'):
            self.step(self.top, '', name)
        source = {'repository': evidence.flow.SOURCE_REPOSITORY, 'commit': self.expected['source_sha'],
                  'approved_by': 'fixture only', 'approval_reference': 'https://github.com/fixture/fixture/issues/1'}
        self.handoff = {'HANDOFF': 'PASS', 'source': source, 'files': {}, 'test_scope': {}}
        for service in evidence.flow.SERVICES:
            for name in ('Dockerfile', 'requirements.txt', 'requirements-test.txt', 'requirements-runtime.lock',
                         'requirements-test.lock'):
                label = 'handoff-' + service + '-' + name
                self.step(self.top, '', label)
                self.handoff['files'][service + '/' + name] = {'sha256': self.hash(label + '.log'), 'mode': '100644'}
            cmd = {'cwd': service, 'argv': ['python', '-m', 'pytest'], 'timeout_seconds': 300}
            self.handoff['test_scope'][service] = {'unit': copy.deepcopy(cmd), 'contract': copy.deepcopy(cmd)}
        self.handoff['test_scope']['transaction-api']['unit'] = {
            'status': 'NOT_APPLICABLE', 'reason': 'fixture scope', 'approved_by': 'fixture only',
            'approval_reference': 'https://github.com/fixture/fixture/issues/1'}
        handoff_sha = self.write_json('handoff.json', self.handoff)
        self.top['handoff_sha256'] = handoff_sha
        child_context = {**self.context, 'handoff_sha256': handoff_sha}
        self.built = {'BUILD_SCAN': 'PASS', 'context': child_context, 'source': source,
                      'started_at': self.time(5), 'finished_at': self.time(45), 'steps': [], 'images': {},
                      'PUSH': 'NOT_EXECUTED', 'DEPLOY': 'NOT_EXECUTED', 'DELIVERY_GATE': 'NOT_VERIFIED',
                      'not_applicable_tests': [{'service': 'transaction-api', 'phase': 'unit',
                          **self.handoff['test_scope']['transaction-api']['unit']}],
                      'offline_supply': {'SUPPLY_COPY': 'PASS', 'REPOSITORY_DIGEST_RESOLUTION': 'PASS'}}
        self.write('build-scan/source.tar', b'fixture source snapshot')
        self.built['source_archive_sha256'] = self.hash('build-scan/source.tar')
        for name in ('source-head', 'source-origin', 'source-tree', 'source-archive', 'podman-version',
                     'podman-rootless', 'offline-base-load', 'offline-base-tag', 'offline-base-resolve'):
            self.step(self.built, 'build-scan/', name)
        for service in evidence.flow.SERVICES:
            for name in ('offline-venv', 'offline-install', 'offline-check', 'contract-0', 'build', 'save'):
                self.step(self.built, 'build-scan/', service + '-' + name)
        self.step(self.built, 'build-scan/', 'fds-engine-unit-0')
        self.scan = {'IMAGE_SCAN': 'PASS', 'context': child_context, 'source_sha': source['commit'],
                     'started_at': self.time(20), 'finished_at': self.time(40), 'images': {},
                     'binary_sha256': evidence.BINARY_SHA256, 'push': 'NOT_EXECUTED', 'deploy': 'NOT_EXECUTED',
                     'DELIVERY_GATE': 'NOT_VERIFIED'}
        self.db = {'DB_PREFLIGHT': 'PASS', 'context': child_context, 'binary_sha256': evidence.BINARY_SHA256,
                   'db_repository': evidence.DB_REPOSITORY, 'started_at': self.time(21), 'finished_at': self.time(24),
                   'validated_at': self.time(23), 'metadata': {'Version': 2, 'UpdatedAt': self.time(0),
                       'DownloadedAt': self.time(22), 'NextUpdate': '2026-09-18T00:00:00Z'}}
        self.db['metadata_sha256'] = self.write_json('build-scan/scan/db/cache/db/metadata.json', self.db['metadata'])
        self.write('build-scan/scan/db/cache/db/trivy.db', b'fixture db')
        self.db['db_sha256'] = self.hash('build-scan/scan/db/cache/db/trivy.db')
        self.scan['db_result_sha256'] = self.write_json('build-scan/scan/db/result.json', self.db)
        for service in evidence.flow.SERVICES:
            image_path = self.run / ('build-scan/' + service + '.oci.tar')
            write_archive(image_path, image_members())
            identity = evidence.flow.inspect_archive(image_path)
            self.built['images'][service] = {'image_id': identity['config_digest'], **identity}
            self.write('build-scan/scan/' + service + '.oci.tar', image_path.read_bytes())
            report_sha = self.write_json('build-scan/scan/' + service + '/report.json', report(identity))
            self.scan['images'][service] = {'scan': 'PASS', 'exit_code': 0, 'counts': {'HIGH': 0, 'CRITICAL': 0},
                                            'report_sha256': report_sha, **identity}
        self.flush()

    @staticmethod
    def time(second):
        return '2026-09-17T03:00:' + str(second).zfill(2) + 'Z'

    def write(self, name, raw):
        path = self.run / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)

    def write_json(self, name, value):
        self.write(name, (json.dumps(value) + '\n').encode())
        return self.hash(name)

    def hash(self, name):
        return evidence.flow.sha256(self.run / name)

    def step(self, record, prefix, name):
        self.write(prefix + name + '.log', ('fixture ' + name + '\n').encode())
        record['steps'].append({'name': name, 'exit_code': 0, 'log_sha256': self.hash(prefix + name + '.log')})

    def flush(self):
        self.built['scan_status'] = {n: self.scan['images'].get(n, {}).get('scan', 'NOT_SCANNED') for n in evidence.flow.SERVICES}
        self.built['scan_result_sha256'] = self.write_json('build-scan/scan/result.json', self.scan)
        self.top['build_result_sha256'] = self.write_json('build-scan/result.json', self.built)
        self.write_json('result.json', self.top)

    def check(self):
        return evidence.check(evidence.Local(self.run), self.expected)

    def test_collect_verify_roundtrip_omits_raw_payloads(self):
        before = {str(p.relative_to(self.run)): p.read_bytes() for p in self.run.rglob('*') if p.is_file()}
        archive, checksum, local = evidence.collect(self.run, self.root / 'export', self.expected)
        self.assertEqual(local['RAW_PAYLOAD_CHECK'], 'LOCAL_BYTES')
        with tarfile.open(archive, 'r:gz') as tar:
            self.assertEqual(set(tar.getnames()), evidence.RECEIPTS | {'manifest.json'})
        checked = evidence.verify(archive, checksum, self.expected)
        self.assertEqual(checked['RECORDED_APP_WORKFLOW'], 'PASS')
        self.assertEqual(checked['RAW_PAYLOAD_CHECK'], 'NOT_INCLUDED_SERVER_RECORD_ONLY')
        self.assertEqual(checked['DELIVERY_GATE'], 'NOT_VERIFIED')
        self.assertEqual(before, {str(p.relative_to(self.run)): p.read_bytes() for p in self.run.rglob('*') if p.is_file()})

    def test_first_and_second_image_block_preserve_not_scanned(self):
        original = copy.deepcopy(self.scan)
        for index, service in enumerate(evidence.flow.SERVICES):
            self.scan = copy.deepcopy(original)
            self.scan['images'] = {n: self.scan['images'][n] for n in evidence.flow.SERVICES[:index+1]}
            image = self.scan['images'][service]
            image.update(scan='BLOCK', exit_code=10, counts={'HIGH': 1, 'CRITICAL': 0})
            image['report_sha256'] = self.write_json('build-scan/scan/' + service + '/report.json', report(image, 'HIGH'))
            self.top['APP_WORKFLOW'] = self.top['APP_TEST_BUILD_SCAN'] = self.built['BUILD_SCAN'] = 'BLOCK'
            self.scan.update(IMAGE_SCAN='BLOCK', error_code='HIGH_CRITICAL_FINDINGS')
            self.flush()
            with self.subTest(service=service):
                archive, checksum, result = evidence.collect(self.run, self.root / ('export-' + service), self.expected)
                self.assertEqual(result['RECORDED_APP_WORKFLOW'], 'BLOCK')
                self.assertEqual(evidence.verify(archive, checksum, self.expected)['RECORDED_APP_WORKFLOW'], 'BLOCK')
            # Reset raw reports as well as metadata for the second subcase.
            for name, item in original['images'].items():
                self.write_json('build-scan/scan/' + name + '/report.json', report(item))

    def test_valid_early_failure_is_diagnostic_not_app_pass(self):
        self.top.update(APP_WORKFLOW='FAIL', APP_TEST_BUILD_SCAN='NOT_EXECUTED')
        for key in ('handoff_sha256', 'build_result_sha256', 'HANDOFF'):
            self.top.pop(key)
        self.top['steps'] = self.top['steps'][:3]
        self.top['steps'][-1]['exit_code'] = 128
        self.write_json('result.json', self.top)
        archive, checksum, result = evidence.collect(self.run, self.root / 'failed', self.expected)
        self.assertEqual(result['EVIDENCE_CHECK'], 'PASS')
        self.assertEqual(result['RECORDED_APP_WORKFLOW'], 'FAIL')
        self.assertEqual(result['SOURCE_BINDING'], 'UNAVAILABLE_BEFORE_HANDOFF')
        self.assertEqual(evidence.verify(archive, checksum, self.expected)['BINDING'], 'PARTIAL_FAILURE_RECORD')

    def test_expected_identity_mismatches_rejected(self):
        for key in self.expected:
            expected = {**self.expected, key: 2 if key == 'attempt' else '1' * (40 if key.endswith('_sha') else 64 if key == 'input_sha256' else 4)}
            with self.subTest(key=key), self.assertRaises(evidence.flow.PreflightError):
                evidence.check(evidence.Local(self.run), expected)

    def test_tampered_nested_receipt_without_rehash_is_rejected(self):
        self.write('build-scan/scan/result.json', b'{}')
        with self.assertRaisesRegex(evidence.flow.PreflightError, 'RECEIPT_HASH_MISMATCH'):
            self.check()

    def test_rehashed_wrong_scan_source_or_run_is_rejected(self):
        self.scan['source_sha'] = 'f' * 40
        self.flush()
        with self.assertRaisesRegex(evidence.flow.PreflightError, 'SCAN_BINDING_MISMATCH'):
            self.check()

    def test_changed_raw_files_rejected(self):
        paths = ['source-fetch.log', 'build-scan/source.tar', 'build-scan/transaction-api.oci.tar',
                 'build-scan/scan/transaction-api/report.json', 'build-scan/scan/db/cache/db/trivy.db']
        for name in paths:
            path = self.run / name
            old = path.read_bytes()
            path.write_bytes(old + b'changed')
            with self.subTest(name=name), self.assertRaises(evidence.flow.PreflightError):
                self.check()
            path.write_bytes(old)

    def test_missing_or_linked_payload_rejected(self):
        path = self.run / 'source-fetch.log'
        old = path.read_bytes()
        path.unlink()
        with self.assertRaises(OSError):
            self.check()
        other = self.root / 'external'
        other.write_bytes(old)
        path.symlink_to(other)
        with self.assertRaises(evidence.flow.PreflightError):
            self.check()

    def test_failed_step_or_missing_unit_cannot_claim_pass(self):
        self.built['steps'][-1]['exit_code'] = 1
        self.flush()
        with self.assertRaisesRegex(evidence.flow.PreflightError, 'SUCCESS_WITH_FAILED_STEP'):
            self.check()
        self.built['steps'].pop()
        self.flush()
        with self.assertRaisesRegex(evidence.flow.PreflightError, 'TEST_BUILD_SCOPE_MISMATCH'):
            self.check()

    def test_fifo_inputs_fail_without_waiting_for_a_writer(self):
        expected = self.root / 'expected.json'
        expected.write_text(json.dumps(self.expected))
        script = Path(evidence.__file__)
        for kind in ('expected', 'archive', 'receipt', 'payload'):
            path = {'expected': expected, 'archive': self.root / 'input.tar.gz',
                    'receipt': self.run / 'result.json',
                    'payload': self.run / 'source-fetch.log'}[kind]
            old = path.read_bytes() if path.exists() else None
            if path.exists():
                path.unlink()
            os.mkfifo(path, 0o600)
            args = (['verify', '--archive', str(self.root / 'input.tar.gz'),
                     '--sha256', '0' * 64] if kind in ('expected', 'archive') else
                    ['collect', '--run-dir', str(self.run),
                     '--output-dir', str(self.root / ('export-' + kind))])
            try:
                with self.subTest(kind=kind):
                    result = subprocess.run([sys.executable, '-B', str(script), *args,
                        '--expected', str(expected)], capture_output=True, timeout=3)
                    self.assertEqual(result.returncode, 1, result.stderr)
                    self.assertEqual(result.stdout,
                        b'EVIDENCE_CHECK=FAIL\nDELIVERY_GATE=NOT_VERIFIED\n')
                    self.assertEqual(result.stderr, b'')
                    self.assertFalse((self.root / ('export-' + kind)).exists())
            finally:
                path.unlink()
                if old is not None:
                    path.write_bytes(old)

    def test_scan_partial_or_wrong_identity_rejected(self):
        image = self.scan['images'].pop('fds-engine')
        self.flush()
        with self.assertRaisesRegex(evidence.flow.PreflightError, 'SCAN_PASS_INCOMPLETE'):
            self.check()
        self.scan['images']['fds-engine'] = image
        image['manifest_digest'] = 'sha256:' + 'f' * 64
        self.flush()
        with self.assertRaisesRegex(evidence.flow.PreflightError, 'SCAN_BUILD_IDENTITY_MISMATCH'):
            self.check()

    def test_db_expiry_checked_at_scan_time_not_collection_time(self):
        self.assertEqual(self.check()['EVIDENCE_CHECK'], 'PASS')
        self.db['metadata']['NextUpdate'] = self.time(30)
        self.db['metadata_sha256'] = self.write_json('build-scan/scan/db/cache/db/metadata.json', self.db['metadata'])
        self.scan['db_result_sha256'] = self.write_json('build-scan/scan/db/result.json', self.db)
        self.flush()
        with self.assertRaisesRegex(evidence.flow.PreflightError, 'DB_REFRESH_REQUIRED'):
            self.check()

    def test_existing_output_preserved(self):
        output = self.root / 'existing'
        output.mkdir()
        (output / 'keep').write_text('keep')
        with self.assertRaisesRegex(evidence.flow.PreflightError, 'OUTPUT_ALREADY_EXISTS'):
            evidence.collect(self.run, output, self.expected)
        self.assertEqual((output / 'keep').read_text(), 'keep')

    def test_bad_archive_hash_and_members_rejected(self):
        archive, checksum, _ = evidence.collect(self.run, self.root / 'export', self.expected)
        with self.assertRaisesRegex(evidence.flow.PreflightError, 'ARCHIVE_HASH_MISMATCH'):
            evidence.verify(archive, '0' * 64, self.expected)
        for index, (name, kind) in enumerate([('../outside', tarfile.REGTYPE), ('result.json', tarfile.SYMTYPE),
                                             ('result.json', tarfile.LNKTYPE), ('extra.json', tarfile.REGTYPE)]):
            target = self.root / ('bad-' + str(index) + '.tar.gz')
            with tarfile.open(target, 'w:gz') as tar:
                member = tarfile.TarInfo(name)
                member.type, member.size, member.linkname = kind, 2, '/etc/passwd'
                tar.addfile(member, io.BytesIO(b'{}'))
            with self.subTest(name=name, kind=kind), self.assertRaises(evidence.flow.PreflightError):
                evidence.verify(target, evidence.flow.sha256(target), self.expected)

    def test_compressed_padding_cannot_bypass_package_limit(self):
        archive, _, _ = evidence.collect(self.run, self.root / 'export', self.expected)
        # A valid tar followed by compressed padding used to stop at the first tar EOF.
        decoded = evidence.gzip.decompress(archive.read_bytes())
        archive.write_bytes(evidence.gzip.compress(decoded + b'\0' * evidence.TAR_LIMIT))
        self.assertLess(archive.stat().st_size, evidence.PACKAGE_LIMIT)
        with self.assertRaisesRegex(evidence.flow.PreflightError, 'DECOMPRESSED_PACKAGE_TOO_LARGE'):
            evidence.verify(archive, evidence.flow.sha256(archive), self.expected)

    def test_rehashed_package_with_unbound_payload_rejected(self):
        archive, _, _ = evidence.collect(self.run, self.root / 'export', self.expected)
        with tarfile.open(archive, 'r:gz') as tar:
            files = {m.name: tar.extractfile(m).read() for m in tar}
        manifest = json.loads(files['manifest.json'])
        manifest['payloads']['unrelated'] = {'kind': 'command-log', 'sha256': '0' * 64, 'size': 0}
        files['manifest.json'] = json.dumps(manifest).encode()
        target = self.root / 'extra.tar.gz'
        with tarfile.open(target, 'w:gz') as tar:
            for name, raw in files.items():
                member = tarfile.TarInfo(name)
                member.size = len(raw)
                tar.addfile(member, io.BytesIO(raw))
        with self.assertRaisesRegex(evidence.flow.PreflightError, 'UNBOUND_MANIFEST_CONTENT'):
            evidence.verify(target, evidence.flow.sha256(target), self.expected)


if __name__ == '__main__':
    unittest.main()
