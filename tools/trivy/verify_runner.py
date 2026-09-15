#!/usr/bin/env python3
"""Exercise the real installed scanner; this is not an image delivery gate."""

import getpass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import tempfile
from datetime import datetime, timezone

TRIVY = Path('/usr/local/bin/trivy')
BINARY_SHA256 = 'd89bcc6510a267f11b773398cbf1be5520ce39f9e8b6633178c4487f05b7d791'
MISSING_DB_ERROR = '--skip-db-update cannot be specified on the first run'


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha256(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def check_findings(rc, report):
    require(rc == 10, f'finding scan exit code must be 10, got {rc}')
    require(report.get('Trivy', {}).get('Version') == '0.74.0', 'unexpected Trivy version')
    require(report.get('ArtifactType') == 'filesystem', 'unexpected scan artifact type')
    results = report.get('Results')
    require(isinstance(results, list) and len(results) == 1, 'expected one pip result')
    result = results[0]
    require(result.get('Target') == 'requirements.txt', 'unexpected scan target')
    require(result.get('Class') == 'lang-pkgs' and result.get('Type') == 'pip', 'unexpected analyzer')
    findings = result.get('Vulnerabilities')
    require(isinstance(findings, list) and findings, 'expected vulnerability findings')
    counts = {'HIGH': 0, 'CRITICAL': 0}
    for finding in findings:
        require(finding.get('PkgName', '').lower() == 'django', 'unexpected package')
        require(finding.get('InstalledVersion') == '2.2.0', 'unexpected package version')
        severity = finding.get('Severity')
        require(severity in counts, 'unexpected severity')
        require(bool(finding.get('VulnerabilityID')), 'missing vulnerability identifier')
        counts[severity] += 1
    return counts


def check_missing_db(rc, log):
    require(rc == 1, f'missing DB exit code must be 1, got {rc}')
    require(MISSING_DB_ERROR in log, 'expected missing-DB error was not returned')


def run_scan(evidence, cache, label):
    command = [str(TRIVY), 'fs', '--cache-dir', str(cache),
               '--scanners', 'vuln', '--pkg-types', 'library',
               '--skip-db-update', '--skip-java-db-update', '--offline-scan',
               '--severity', 'HIGH,CRITICAL', '--exit-code', '10',
               '--no-progress', '--timeout', '2m', '--format', 'json',
               '--output', f'{label}.json', 'fixture']
    # Explicit CLI options and a new working directory avoid project configuration.
    env = {key: value for key, value in os.environ.items() if not key.startswith('TRIVY_')}
    with (evidence / f'{label}.log').open('w') as log:
        try:
            rc = subprocess.run(command, cwd=evidence, env=env, stdout=log,
                                stderr=subprocess.STDOUT, timeout=150, check=False).returncode
        except subprocess.TimeoutExpired:
            log.write('\nPROCESS_TIMEOUT\n')
            rc = 124
    (evidence / f'{label}.exit-code.txt').write_text(f'{rc}\n')
    print(f'{label.upper()}_EXIT_CODE={rc}', flush=True)
    print((evidence / f'{label}.log').read_text(), end='', flush=True)
    return rc


def execute(evidence, context):
    require(TRIVY.is_file() and not TRIVY.is_symlink(), 'Trivy binary missing or symlinked')
    actual_binary_hash = sha256(TRIVY)
    (evidence / 'installed-binary.sha256').write_text(f'{actual_binary_hash}  {TRIVY}\n')
    require(actual_binary_hash == BINARY_SHA256, 'installed Trivy binary hash mismatch')
    require(subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()
            == context['workflow_sha'], 'checkout differs from workflow commit')
    require(subprocess.check_output(['getenforce'], text=True).strip() == 'Enforcing',
            'SELinux must be Enforcing')
    require(subprocess.check_output(['podman', 'info', '--format', '{{.Host.Security.Rootless}}'],
                                    text=True, timeout=60).strip() == 'true', 'builder is not rootless')
    (evidence / 'fixture').mkdir(mode=0o700)
    (evidence / 'fixture/requirements.txt').write_text('Django==2.2.0\n')
    source = Path.home() / '.cache/trivy/db'
    require((source / 'trivy.db').is_file(), 'prepared vulnerability DB missing')
    require((source / 'metadata.json').is_file(), 'prepared DB metadata missing')
    with tempfile.TemporaryDirectory(prefix='trivy-toolcheck-', dir=os.environ['RUNNER_TEMP']) as tmp:
        cache = Path(tmp) / 'cache'
        snapshot = cache / 'db'
        snapshot.mkdir(parents=True, mode=0o700)
        for name in ('trivy.db', 'metadata.json'):
            shutil.copyfile(source / name, snapshot / name)
            require(sha256(source / name) == sha256(snapshot / name), f'DB copy changed: {name}')
        metadata = json.loads((snapshot / 'metadata.json').read_text())
        require(metadata.get('Version') == 2, 'expected DB schema 2')
        shutil.copyfile(snapshot / 'metadata.json', evidence / 'metadata.json')
        (evidence / 'DB_SHA256SUMS').write_text(''.join(
            f'{sha256(snapshot / name)}  {name}\n' for name in ('trivy.db', 'metadata.json')))
        rc = run_scan(evidence, cache, 'findings')
        counts = check_findings(rc, json.loads((evidence / 'findings.json').read_text()))
        missing_cache = Path(tmp) / 'missing-db'
        missing_cache.mkdir(mode=0o700)
        rc = run_scan(evidence, missing_cache, 'missing-db')
        check_missing_db(rc, (evidence / 'missing-db.log').read_text())
        require(not (missing_cache / 'db/trivy.db').exists(), 'missing-DB test unexpectedly created a DB')
    return counts


def main():
    os.umask(0o077)
    require(os.environ.get('GITHUB_REPOSITORY') == '321Team/infra-cicd', 'unexpected repository')
    require(os.environ.get('GITHUB_REF') == 'refs/heads/main', 'main branch required')
    require(os.environ.get('GITHUB_EVENT_NAME') == 'workflow_dispatch', 'manual dispatch required')
    require(socket.gethostname().split('.')[0] == 'cicd-runner01', 'unexpected runner host')
    require(getpass.getuser() == 'server01_cicd-runner' and os.getuid() == 1000, 'unexpected runner account')
    run_id = os.environ.get('GITHUB_RUN_ID', '')
    attempt = os.environ.get('GITHUB_RUN_ATTEMPT', '')
    commit = os.environ.get('GITHUB_SHA', '')
    require(bool(re.fullmatch(r'[0-9]+', run_id)) and bool(re.fullmatch(r'[0-9]+', attempt)), 'invalid run identity')
    require(bool(re.fullmatch(r'[0-9a-f]{40}', commit)), 'invalid workflow SHA')
    evidence = Path(tempfile.mkdtemp(prefix=f'trivy-ci-{run_id}-{attempt}-', dir=Path.home()))
    print(f'EVIDENCE_DIR={evidence}', flush=True)
    context = {'run_url': f'https://github.com/321Team/infra-cicd/actions/runs/{run_id}',
               'attempt': int(attempt), 'workflow_sha': commit, 'host': socket.gethostname(),
               'user': getpass.getuser(), 'uid': os.getuid(), 'expected_binary_sha256': BINARY_SHA256,
               'started_at': datetime.now(timezone.utc).isoformat()}
    (evidence / 'context.json').write_text(json.dumps(context, indent=2) + '\n')
    result = {'TRIVY_CI_TOOLCHECK': 'FAIL', 'DELIVERY_GATE': 'NOT_VERIFIED'}
    try:
        result['finding_counts'] = execute(evidence, context)
        result['TRIVY_CI_TOOLCHECK'] = 'PASS'
        result['DB_MISSING_ERROR_TEST'] = 'PASS'
        return 0
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        result['error'] = str(exc)
        print(f'TRIVY_CI_TOOLCHECK_ERROR={exc}', flush=True)
        return 1
    finally:
        result['finished_at'] = datetime.now(timezone.utc).isoformat()
        (evidence / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
        manifest = ''.join(f'{sha256(path)}  {path.relative_to(evidence).as_posix()}\n'
                           for path in sorted(evidence.rglob('*')) if path.is_file())
        (evidence / 'SHA256SUMS').write_text(manifest)
        for key in ('TRIVY_CI_TOOLCHECK', 'DELIVERY_GATE'):
            print(f'{key}={result[key]}', flush=True)
        print(f'EVIDENCE_DIR={evidence}', flush=True)
        summary = os.environ.get('GITHUB_STEP_SUMMARY')
        if summary:
            with open(summary, 'a') as stream:
                stream.write(f"Trivy tool check: **{result['TRIVY_CI_TOOLCHECK']}**\n\n"
                             f"Run: {context['run_url']} / attempt {attempt}\n\n"
                             f"Workflow SHA: `{commit}`\n\n"
                             f"Evidence on runner: `{evidence}`\n\n"
                             'DELIVERY_GATE=NOT_VERIFIED\n')


if __name__ == '__main__':
    raise SystemExit(main())
