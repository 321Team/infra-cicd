import copy
import unittest

from verify_runner import check_findings, check_missing_db, MISSING_DB_ERROR


def report():
    return {'Trivy': {'Version': '0.74.0'}, 'ArtifactType': 'filesystem', 'Results': [
        {'Target': 'requirements.txt', 'Class': 'lang-pkgs', 'Type': 'pip', 'Vulnerabilities': [
            {'PkgName': 'Django', 'InstalledVersion': '2.2.0', 'Severity': 'HIGH',
             'VulnerabilityID': 'CVE-TEST-HIGH'},
            {'PkgName': 'Django', 'InstalledVersion': '2.2.0', 'Severity': 'CRITICAL',
             'VulnerabilityID': 'CVE-TEST-CRITICAL'}]}]}


class ScannerResultTests(unittest.TestCase):
    def test_expected_findings_and_exit(self):
        self.assertEqual(check_findings(10, report()), {'HIGH': 1, 'CRITICAL': 1})

    def test_different_db_finding_count_is_allowed(self):
        sample = report()
        sample['Results'][0]['Vulnerabilities'].pop()
        self.assertEqual(check_findings(10, sample), {'HIGH': 1, 'CRITICAL': 0})

    def test_clean_exit_or_scanner_error_cannot_pass_finding_test(self):
        for rc in (0, 1, 124, -9):
            with self.subTest(rc=rc), self.assertRaises(ValueError):
                check_findings(rc, report())

    def test_empty_report_cannot_pass(self):
        for empty in (None, []):
            sample = report()
            sample['Results'][0]['Vulnerabilities'] = empty
            with self.subTest(empty=empty), self.assertRaises(ValueError):
                check_findings(10, sample)

    def test_wrong_artifact_version_or_analyzer_cannot_pass(self):
        samples = []
        sample = report()
        sample['ArtifactType'] = 'container_image'
        samples.append(sample)
        sample = report()
        sample['Trivy']['Version'] = '0.0.0'
        samples.append(sample)
        for key, value in [('Target', 'other.txt'), ('Class', 'os-pkgs'), ('Type', 'npm')]:
            sample = report()
            sample['Results'][0][key] = value
            samples.append(sample)
        for sample in samples:
            with self.subTest(sample=sample), self.assertRaises(ValueError):
                check_findings(10, sample)

    def test_wrong_package_version_severity_or_missing_id_cannot_pass(self):
        for key, value in [('PkgName', 'other'), ('InstalledVersion', '5.0.0'),
                           ('Severity', 'LOW'), ('VulnerabilityID', '')]:
            sample = report()
            sample['Results'][0]['Vulnerabilities'][0][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                check_findings(10, sample)

    def test_missing_or_multiple_targets_cannot_pass(self):
        for results in ([], None, report()['Results'] * 2):
            sample = report()
            sample['Results'] = copy.deepcopy(results)
            with self.subTest(results=results), self.assertRaises(ValueError):
                check_findings(10, sample)

    def test_expected_missing_db_error(self):
        check_missing_db(1, 'FATAL DB error: ' + MISSING_DB_ERROR)

    def test_unrelated_errors_cannot_pass_missing_db_test(self):
        for rc, log in [(0, MISSING_DB_ERROR), (10, MISSING_DB_ERROR),
                        (124, MISSING_DB_ERROR), (1, 'permission denied'), (1, '')]:
            with self.subTest(rc=rc, log=log), self.assertRaises(ValueError):
                check_missing_db(rc, log)


if __name__ == '__main__':
    unittest.main()
