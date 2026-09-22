#!/usr/bin/env python3
"""Scan both built OCI archives. Exit 0 is scan success, not Push/Deploy approval."""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile

from oci_archive import inspect_archive, json_object
from prepare_db import (PreflightError, prepare, regular, require, runtime_context,
                        scanner_environment, utcnow, validate_metadata, verify_binary)
from verify_runner import BINARY_SHA256, TRIVY, sha256

SERVICES = ('transaction-api', 'fds-engine')


def check_report(report, rc, identity):
    """Require real OS and Python inventory, not just an empty findings list."""
    require(rc in (0, 10), 'SCANNER_EXECUTION_ERROR')
    require(type(report.get('SchemaVersion')) is int and report['SchemaVersion'] == 2,
            'REPORT_SCHEMA_INVALID')
    require(report.get('Trivy', {}).get('Version') == '0.74.0', 'REPORT_VERSION_MISMATCH')
    require(report.get('ArtifactType') == 'container_image', 'REPORT_ARTIFACT_INVALID')
    metadata = report.get('Metadata', {})
    require(metadata.get('ImageID') == identity['config_digest'] and
            metadata.get('DiffIDs') == identity['diff_ids'], 'REPORT_IMAGE_MISMATCH')
    results = report.get('Results')
    require(isinstance(results, list) and results, 'REPORT_RESULTS_MISSING')
    counts, coverage = {'HIGH': 0, 'CRITICAL': 0}, set()
    for item in results:
        require(isinstance(item, dict), 'REPORT_RESULT_INVALID')
        require(not item.get('ExperimentalModifiedFindings'), 'SUPPRESSED_FINDINGS_UNSUPPORTED')
        packages = item.get('Packages', [])
        require(isinstance(packages, list), 'REPORT_PACKAGES_INVALID')
        if packages:
            require(all(isinstance(p, dict) and p.get('Name') and p.get('Version') for p in packages),
                    'REPORT_PACKAGES_INVALID')
            if item.get('Class') == 'os-pkgs':
                coverage.add('os')
            if item.get('Class') == 'lang-pkgs' and item.get('Type') == 'python-pkg':
                coverage.add('python')
        findings = item.get('Vulnerabilities', [])
        require(isinstance(findings, list), 'REPORT_FINDINGS_INVALID')
        for finding in findings:
            require(isinstance(finding, dict) and finding.get('Severity') in counts and
                    finding.get('VulnerabilityID') and finding.get('PkgName') and
                    finding.get('InstalledVersion'), 'REPORT_FINDING_INVALID')
            counts[finding['Severity']] += 1
    require(coverage == {'os', 'python'}, 'REPORT_COVERAGE_INCOMPLETE')
    require((rc == 10) == (sum(counts.values()) > 0), 'REPORT_EXIT_CODE_MISMATCH')
    return counts


def check_db(evidence, db_result):
    require(db_result.get('DB_PREFLIGHT') == 'PASS', 'DB_PREFLIGHT_FAILED')
    cache = evidence / 'db' / 'cache'
    for name, key in (('trivy.db', 'db_sha256'), ('metadata.json', 'metadata_sha256')):
        path = cache / 'db' / name
        regular(path)
        require(sha256(path) == db_result.get(key), 'DB_CHANGED')
    metadata = json_object((cache / 'db' / 'metadata.json').read_bytes())
    validate_metadata(metadata, utcnow())  # No old-cache/expiry grace period.
    return cache


def scan_one(directory, archive, identity, cache, env):
    directory.mkdir(mode=0o700)
    # Trivy's OCI-image opener stats <input>/index.json, so --input must be an
    # extracted layout directory; the packed oci-archive tar itself is rejected
    # ("not a directory") even though it is a well-formed OCI archive.
    layout = directory / 'layout'
    layout.mkdir(mode=0o700)
    with tarfile.open(archive, 'r:') as tar:
        tar.extractall(layout, filter='data')
    config, ignore = directory / 'empty.yaml', directory / 'empty.ignore'
    config.write_text('{}\n')
    ignore.write_text('')
    report_path = directory / 'report.json'
    command = [str(TRIVY), 'image', '--input', str(layout), '--cache-dir', str(cache),
               '--config', str(config), '--ignorefile', str(ignore), '--ignore-unfixed=false',
               '--scanners', 'vuln', '--pkg-types', 'os,library', '--list-all-pkgs',
               '--skip-db-update', '--skip-java-db-update', '--offline-scan',
               '--skip-version-check', '--severity', 'HIGH,CRITICAL', '--exit-code', '10',
               '--format', 'json', '--output', str(report_path), '--no-progress', '--timeout', '10m']
    # Reports can contain image config/secrets. Keep them private; never echo them.
    completed = subprocess.run(command, cwd=directory, env=scanner_environment(env),
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               timeout=630, check=False)
    rc = completed.returncode
    (directory / 'exit-code.txt').write_text(str(rc) + '\n')
    require(rc in (0, 10), 'SCANNER_EXECUTION_ERROR')
    regular(report_path)
    require(report_path.stat().st_size <= 64 * 1024 * 1024, 'REPORT_TOO_LARGE')
    report = json_object(report_path.read_bytes())
    counts = check_report(report, rc, identity)
    return {'scan': 'PASS' if rc == 0 else 'BLOCK', 'exit_code': rc, 'counts': counts,
            'report_sha256': sha256(report_path), **identity}


def scan_pair(evidence, context, source_sha, archives, env):
    """Internal adapter: same-process fresh DB, exact two images, no callbacks.

    Source approval/Test/Build provenance are the future caller's obligations.
    A stored PASS JSON is diagnostic evidence, never an authorization token.
    """
    result = {'IMAGE_SCAN': 'FAIL', 'DELIVERY_GATE': 'NOT_VERIFIED',
              'push': 'NOT_EXECUTED', 'deploy': 'NOT_EXECUTED',
              'context': context, 'source_sha': source_sha,
              'source_approval': 'NOT_VERIFIED', 'images': {},
              'binary_sha256': BINARY_SHA256, 'started_at': utcnow().isoformat()}
    rc = 1
    try:
        require(isinstance(source_sha, str) and re.fullmatch('[0-9a-f]{40}', source_sha),
                'SOURCE_SHA_INVALID')
        require(set(archives) == set(SERVICES), 'EXACT_TWO_IMAGES_REQUIRED')
        identities, copies = {}, {}
        for name in SERVICES:
            path = Path(archives[name])
            regular(path)
            copies[name] = evidence / (name + '.oci.tar')
            with path.open('rb') as src, copies[name].open('xb') as dst:
                shutil.copyfileobj(src, dst)
            identities[name] = inspect_archive(copies[name])
        db_evidence = evidence / 'db'
        db_evidence.mkdir(mode=0o700)
        db_rc, db_result = prepare(db_evidence, context, env)
        require(db_rc == 0 and db_result.get('context') == context, 'DB_PREFLIGHT_FAILED')
        result['db_result_sha256'] = sha256(db_evidence / 'result.json')
        for name in SERVICES:
            cache = check_db(evidence, db_result)
            verify_binary()
            scanned = scan_one(evidence / name, copies[name], identities[name], cache, env)
            result['images'][name] = scanned
            require(sha256(copies[name]) == identities[name]['archive_sha256'], 'OCI_ARCHIVE_CHANGED')
            check_db(evidence, db_result)
            verify_binary()
            require(scanned['scan'] == 'PASS', 'HIGH_CRITICAL_FINDINGS')
        # Recheck the first archive/report as well after the second scan.
        for name in SERVICES:
            require(sha256(copies[name]) == identities[name]['archive_sha256'], 'OCI_ARCHIVE_CHANGED')
            require(sha256(evidence / name / 'report.json') == result['images'][name]['report_sha256'],
                    'REPORT_CHANGED')
        check_db(evidence, db_result)
        result['IMAGE_SCAN'], rc = 'PASS', 0
    except PreflightError as exc:
        result['error_code'] = str(exc)
        if str(exc) == 'HIGH_CRITICAL_FINDINGS':
            result['IMAGE_SCAN'], rc = 'BLOCK', 10
    except subprocess.TimeoutExpired:
        result['error_code'] = 'SCANNER_TIMEOUT'
    except (OSError, ValueError, TypeError, AttributeError, KeyError, RecursionError,
            tarfile.TarError, subprocess.SubprocessError):
        result['error_code'] = 'IMAGE_OR_REPORT_IO_ERROR'
    result['finished_at'] = utcnow().isoformat()
    (evidence / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
    # Hash sensitive reports locally without publishing report contents.
    names = ['result.json']
    names += [str(p.relative_to(evidence)) for p in sorted(evidence.glob('*/report.json'))]
    (evidence / 'SHA256SUMS').write_text(''.join(f'{sha256(evidence / n)}  {n}\n' for n in names))
    return rc, result


def interrupted(_signum, _frame):
    raise PreflightError('SCAN_INTERRUPTED')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-sha', required=True)
    for service in SERVICES:
        parser.add_argument('--' + service, required=True, type=Path)
    args = parser.parse_args(argv)
    if sys.version_info < (3, 11):
        print('IMAGE_SCAN=FAIL\nERROR_CODE=PYTHON_3_11_REQUIRED\nDELIVERY_GATE=NOT_VERIFIED')
        return 1
    os.umask(0o077)
    signal.signal(signal.SIGINT, interrupted)
    signal.signal(signal.SIGTERM, interrupted)
    try:
        context = runtime_context(os.environ)
        context.update(python_version='.'.join(map(str, sys.version_info[:3])),
                       python_executable=sys.executable)
        evidence = Path(tempfile.mkdtemp(prefix=f'app-scan-{context["run_id"]}-{context["attempt"]}-',
                                        dir=Path.home()))
        print(f'EVIDENCE_DIR={evidence}', flush=True)
        archives = {name: getattr(args, name.replace('-', '_')) for name in SERVICES}
        rc, result = scan_pair(evidence, context, args.source_sha, archives, os.environ)
        print(f'IMAGE_SCAN={result["IMAGE_SCAN"]}\nERROR_CODE={result.get("error_code", "NONE")}')
        print('PUSH=NOT_EXECUTED\nDEPLOY=NOT_EXECUTED\nDELIVERY_GATE=NOT_VERIFIED')
        return rc
    except (PreflightError, OSError, ValueError, KeyError, subprocess.SubprocessError):
        print('IMAGE_SCAN=FAIL\nERROR_CODE=CONTEXT_OR_EVIDENCE_ERROR\nDELIVERY_GATE=NOT_VERIFIED')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
