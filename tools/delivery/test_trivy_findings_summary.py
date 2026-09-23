"""Regression checks for the informational pre-check collector (not a gate)."""
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

PATH = Path(__file__).resolve().parents[1] / 'precheck' / 'summarize_trivy_findings.py'
SPEC = importlib.util.spec_from_file_location('findings_precheck', PATH)
collector = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(collector)


def report():
    vuln = {'VulnerabilityID': 'CVE-2025-12345', 'PkgName': 'example',
            'InstalledVersion': '1.0', 'FixedVersion': '2.0', 'Severity': 'HIGH'}
    return {'SchemaVersion': 2, 'ArtifactType': 'container_image',
            'Metadata': {'ImageConfig': {'config': {'Env': ['TOKEN=do-not-export']}}},
            'Results': [{'Class': 'lang-pkgs', 'Type': 'python-pkg',
                         'Target': '/private/do-not-export', 'Vulnerabilities': [vuln]}]}


class FindingsSummaryTests(unittest.TestCase):
    def test_allowlist_excludes_image_config_descriptions_and_paths(self):
        data = report()
        data['Results'][0]['Vulnerabilities'][0]['Description'] = 'secret-do-not-export'
        raw = json.dumps(data).encode()
        result = collector.summarize(raw)
        self.assertNotIn('do-not-export', json.dumps(result))
        self.assertEqual(result['source_report_sha256'], hashlib.sha256(raw).hexdigest())
        self.assertEqual(result['finding_records'], {'CRITICAL': 0, 'HIGH': 1})
        self.assertEqual(result['findings'][0]['fixed_version_reported'], '2.0')

    def test_duplicate_cve_records_and_unique_ids_are_distinct(self):
        data = report()
        row = dict(data['Results'][0]['Vulnerabilities'][0], PkgName='second', Severity='CRITICAL')
        data['Results'][0]['Vulnerabilities'].append(row)
        result = collector.summarize(json.dumps(data).encode())
        self.assertEqual(result['unique_vulnerability_ids'], 1)
        self.assertEqual(result['finding_records'], {'CRITICAL': 1, 'HIGH': 1})

    def test_no_findings_is_information_only(self):
        data = report()
        data['Results'][0]['Vulnerabilities'] = None
        result = collector.summarize(json.dumps(data).encode())
        self.assertEqual(result['findings'], [])
        self.assertEqual(result['scope'], 'INFORMATION_ONLY_NOT_A_GATE_OR_APPROVAL')
        self.assertNotIn('gate', result)

    def test_low_severity_is_not_reported_as_high(self):
        data = report()
        data['Results'][0]['Vulnerabilities'][0]['Severity'] = 'LOW'
        self.assertEqual(collector.summarize(json.dumps(data).encode())['findings'], [])

    def test_malformed_and_duplicate_fields_rejected(self):
        for raw in (b'[]', b'{}', b'{"SchemaVersion":2,"SchemaVersion":2}', b'no-json'):
            with self.subTest(raw=raw), self.assertRaises((ValueError, collector.InputError)):
                collector.summarize(raw)

    def test_control_characters_and_oversized_values_rejected(self):
        for value in ('x\nTOKEN=secret', 'x' * 257):
            data = report()
            data['Results'][0]['Vulnerabilities'][0]['PkgName'] = value
            with self.assertRaises(collector.InputError):
                collector.summarize(json.dumps(data).encode())

    def test_size_limit(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'report.json'
            path.write_bytes(b'12345')
            with patch.object(collector, 'LIMIT', 4), self.assertRaises(collector.InputError):
                collector.read_report(path)

    def test_symlinks_and_fifo_rejected_without_wait(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / 'source.json'
            source.write_text('{}')
            link = root / 'link.json'
            link.symlink_to(source)
            fifo = root / 'fifo'
            os.mkfifo(fifo)
            for path in (link, fifo):
                with self.subTest(path=path), self.assertRaises((OSError, collector.InputError)):
                    collector.read_report(path)
            parent_link = root / 'linked'
            parent_link.symlink_to(root, target_is_directory=True)
            with self.assertRaises(collector.InputError):
                collector.read_report(parent_link / 'source.json')

    def test_cli_new_output_private_and_existing_output_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source, output = root / 'source.json', root / 'summary.json'
            source.write_text(json.dumps(report()))
            stdout, stderr = io.StringIO(), io.StringIO()
            args = ['--report', str(source), '--output', str(output)]
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                self.assertEqual(collector.main(args), 0)
                saved = output.read_bytes()
                self.assertEqual(collector.main(args), 1)
            self.assertEqual(output.read_bytes(), saved)
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
            self.assertNotIn('do-not-export', stdout.getvalue() + stderr.getvalue())

    def test_failure_has_no_raw_report_and_does_not_create_output(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source, output = root / 'source.json', root / 'summary.json'
            source.write_text('a-private-token-do-not-export')
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                self.assertEqual(collector.main(['--report', str(source), '--output', str(output)]), 1)
            self.assertFalse(output.exists())
            self.assertNotIn('do-not-export', stderr.getvalue())


if __name__ == '__main__':
    unittest.main()
