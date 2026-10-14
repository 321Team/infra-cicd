#!/usr/bin/env python3
"""Fixed AC05 negative scan cases. Always fail the Run; never authorize delivery."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import pwd
import shutil
import signal
import stat
import subprocess
import sys
import tempfile

import scan_images as scan
from prepare_db import PreflightError, regular, require, runtime_context, utcnow, verify_binary
from verify_runner import BINARY_SHA256, MISSING_DB_ERROR, sha256

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = '321Team/infra-cicd/.github/workflows/app-scan-negative.yml@refs/heads/main'
CASES = ('vulnerability', 'scanner-error')
SOURCE_SHA = 'ec69b67067decf35abbfed0a6a99f3073132787b'
ORIGINAL = 'FDS_CICD_AppWorkflow.log3xwta/build-scan'
FIXTURES = {
    'transaction-api': {'bytes': 93404160, 'sha256':
                        '218c57b0613dc37e9628745d3ae580886543902714bbb9ab6249b5ac90d1266f'},
    'fds-engine': {'bytes': 74496512, 'sha256':
                   'a8895e245ea2a2f30aa106524bf858020fccc65a12fc543d26f3c09a87f04d1c'},
}


def bind_context(env, case):
    require(case in CASES, 'NEGATIVE_CASE_INVALID')
    context = runtime_context(env)
    require(context['attempt'] == 1, 'NEGATIVE_RERUN_FORBIDDEN')
    require(env.get('GITHUB_WORKFLOW_REF') == WORKFLOW, 'CENTRAL_WORKFLOW_REQUIRED')
    require(env.get('GITHUB_WORKFLOW_SHA') == context['workflow_sha'], 'WORKFLOW_SHA_MISMATCH')
    context.update(purpose='SCAN_NEGATIVE_NOT_DELIVERABLE', negative_case=case,
                   workflow_ref=WORKFLOW)
    return context


def plain_owned(path, *, directory=False, private=False):
    """No symlinks, shared-write paths, special files or multiply linked payloads."""
    for parent in reversed((path, *path.parents)):
        info = parent.lstat()
        require(not stat.S_ISLNK(info.st_mode), 'SYMLINK_PATH_FORBIDDEN')
        require(info.st_uid in (0, os.getuid()) and not info.st_mode & 0o022,
                'PATH_OWNER_OR_WRITE_MODE')
    info = path.lstat()
    require(info.st_uid == os.getuid(), 'FIXTURE_OWNER_INVALID')
    require(stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode),
            'FIXTURE_TYPE_INVALID')
    if not directory:
        require(info.st_nlink == 1, 'FIXTURE_HARDLINK_FORBIDDEN')
    if private:
        require(stat.S_IMODE(info.st_mode) == 0o700, 'PRIVATE_DIRECTORY_REQUIRED')
    return info


def copy_fixtures(home, output):
    folder = output / 'fixture-inputs'
    folder.mkdir(mode=0o700)
    archives = {}
    for name, expected in FIXTURES.items():
        source = home / ORIGINAL / (name + '.oci.tar')
        before = plain_owned(source)
        require(before.st_size == expected['bytes'], 'FIXTURE_SIZE_MISMATCH')
        target = folder / source.name
        fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as src, target.open('xb') as dst:
            opened = os.fstat(src.fileno())
            require((opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns) ==
                    (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns),
                    'FIXTURE_CHANGED_WHILE_OPENING')
            remaining = expected['bytes']
            while remaining:
                chunk = src.read(min(1024 * 1024, remaining))
                require(bool(chunk), 'FIXTURE_TRUNCATED')
                dst.write(chunk)
                remaining -= len(chunk)
            require(not src.read(1), 'FIXTURE_GREW')
            after = os.fstat(src.fileno())
            require((after.st_size, after.st_mtime_ns, after.st_nlink) ==
                    (before.st_size, before.st_mtime_ns, 1), 'FIXTURE_CHANGED_DURING_COPY')
        require(sha256(target) == expected['sha256'], 'FIXTURE_HASH_MISMATCH')
        archives[name] = target
    return archives


def observed(case, code, result, evidence):
    """Classify only verified scanner outcomes, never a generic failure or timeout."""
    try:
        require(result['push'] == result['deploy'] == 'NOT_EXECUTED', 'DELIVERY_OBSERVED')
        require(result['source_sha'] == SOURCE_SHA and result['binary_sha256'] == BINARY_SHA256,
                'SCAN_IDENTITY_MISMATCH')
        require(result == json.loads((evidence / 'result.json').read_bytes()), 'SCAN_RESULT_CHANGED')
        for name, expected in FIXTURES.items():
            require(sha256(evidence / (name + '.oci.tar')) == expected['sha256'], 'SCAN_INPUT_CHANGED')
        first = evidence / 'transaction-api'
        rc = int((first / 'exit-code.txt').read_text())
        require(not (evidence / 'fds-engine').exists(), 'UNEXPECTED_SECOND_SCAN')
        if case == 'vulnerability':
            require(code == 10 and result['IMAGE_SCAN'] == 'BLOCK' and rc == 10 and
                    result['error_code'] == 'HIGH_CRITICAL_FINDINGS' and
                    set(result['images']) == {'transaction-api'}, 'EXPECTED_BLOCK_NOT_OBSERVED')
            image = result['images']['transaction-api']
            require(image['scan'] == 'BLOCK' and image['exit_code'] == 10 and
                    sum(image['counts'].values()) > 0, 'NO_BLOCK_FINDINGS')
            require(sha256(first / 'report.json') == image['report_sha256'], 'REPORT_CHANGED')
            db = evidence / 'db/result.json'
            require(sha256(db) == result['db_result_sha256'], 'DB_RESULT_CHANGED')
            scan.check_db(evidence, json.loads(db.read_bytes()))
        elif case == 'scanner-error':
            require(code == 1 and result['IMAGE_SCAN'] == 'FAIL' and rc == 1 and
                    result['error_code'] == 'SCANNER_EXECUTION_ERROR' and result['images'] == {} and
                    result['db_mode'] == 'INTENTIONAL_EMPTY_CACHE_NO_DOWNLOAD', 'EXPECTED_ERROR_NOT_OBSERVED')
            log = first / 'scanner-stderr.log'
            require(log.stat().st_size < 1024 * 1024, 'PROBE_LOG_TOO_LARGE')
            invocation = json.loads((first / 'scanner-invocation.json').read_bytes())
            require(invocation['outcome'] == 'COMPLETED' and invocation['exit_code'] == 1 and
                    sha256(log) == invocation['stderr_sha256'], 'PROBE_INVOCATION_NOT_CONFIRMED')
            require(MISSING_DB_ERROR in log.read_text(errors='replace'), 'MISSING_DB_CAUSE_NOT_OBSERVED')
            require(not (first / 'report.json').exists(), 'UNEXPECTED_PROBE_REPORT')
            require(not (evidence / 'db').exists() and
                    not (evidence / 'empty-db-cache/db/trivy.db').exists(), 'PROBE_DB_NOT_EMPTY')
        else:
            return 'NOT_CONFIRMED'
        verify_binary()  # Recheck even the scanner-error path, which exits scan_pair early.
    except (PreflightError, OSError, ValueError, TypeError, KeyError):
        return 'NOT_CONFIRMED'
    return 'EXPECTED_FAILURE_OBSERVED'


def run(folder, home, context, env, case):
    result = {'SCAN_NEGATIVE_WORKFLOW': 'FAIL', 'NEGATIVE_CASE': 'NOT_CONFIRMED',
              'case': case, 'context': context, 'started_at': utcnow().isoformat(),
              'PUSH': 'NOT_EXECUTED', 'DEPLOY': 'NOT_EXECUTED',
              'TEST_BUILD': 'NOT_EXECUTED_FIXED_HISTORICAL_IMAGES',
              'DELIVERY_GATE': 'NOT_VERIFIED',
              'DEPLOYMENT_IMAGES': 'REQUIRES_EXTERNAL_BEFORE_AFTER_OBSERVATIONS',
              'fixture_source_sha': SOURCE_SHA, 'fixture_hashes': FIXTURES,
              'scanner_binary_sha256': BINARY_SHA256}
    try:
        require(case in CASES and context['negative_case'] == case, 'NEGATIVE_CASE_INVALID')
        verify_binary()
        archives = copy_fixtures(home, folder)
        evidence = folder / 'scan'
        evidence.mkdir(mode=0o700)
        options = {'empty_db_probe': True} if case == 'scanner-error' else {}
        rc, scanned = scan.scan_pair(evidence, context, SOURCE_SHA, archives, env, **options)
        result['scanner_adapter_rc'] = rc
        result['scan_result_sha256'] = sha256(evidence / 'result.json')
        result['NEGATIVE_CASE'] = observed(case, rc, scanned, evidence)
        result['error_code'] = scanned.get('error_code', 'EXPECTED_FAILURE_NOT_OBSERVED')
    except PreflightError as exc:
        result['error_code'] = str(exc)
    except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError):
        result['error_code'] = 'NEGATIVE_PREPARATION_OR_IO_ERROR'
    finally:
        result['finished_at'] = utcnow().isoformat()
        (folder / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
        manifest = ''.join(f'{sha256(p)}  {p.relative_to(folder).as_posix()}\n'
                           for p in sorted(folder.rglob('*')) if p.is_file())
        (folder / 'SHA256SUMS').write_text(manifest)
    return 1, result  # Includes a valid expected failure; never turn this Run green.


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case', required=True, choices=CASES)
    args = parser.parse_args(argv)
    os.umask(0o077)
    try:
        require(sys.version_info >= (3, 11), 'PYTHON_3_11_REQUIRED')
        os.chdir(ROOT)
        context = bind_context(os.environ, args.case)
        home = Path(pwd.getpwuid(os.getuid()).pw_dir)
        plain_owned(home, directory=True, private=True)
        require(shutil.disk_usage(home).free >= 4 * 1024**3, 'FOUR_GIB_FREE_REQUIRED')
        signal.signal(signal.SIGINT, scan.interrupted)
        signal.signal(signal.SIGTERM, scan.interrupted)
        fd = os.open(home / 'FDS_CICD_AppWorkflow.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'r+') as lock:
            info = os.fstat(lock.fileno())
            require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and
                    stat.S_IMODE(info.st_mode) == 0o600 and info.st_nlink == 1, 'LOCK_FILE_INVALID')
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            # Run ID alone is the key: changing a case or local repeat cannot reclaim it.
            with (home / ('FDS_CICD_ScanNegative-' + context['run_id'] + '.claim')).open('x') as claim:
                claim.write(json.dumps(context) + '\n')
            folder = Path(tempfile.mkdtemp(prefix='FDS_CICD_ScanNegative.', dir=home))
            print(f'EVIDENCE_DIR={folder}', flush=True)
            rc, result = run(folder, home, context, os.environ, args.case)
        print(f'SCAN_NEGATIVE_WORKFLOW=FAIL\nNEGATIVE_CASE={result["NEGATIVE_CASE"]}')
        print(f'ERROR_CODE={result.get("error_code", "NONE")}')
        print('PUSH=NOT_EXECUTED\nDEPLOY=NOT_EXECUTED\nDELIVERY_GATE=NOT_VERIFIED')
        return rc
    except (PreflightError, OSError, ValueError, KeyError, subprocess.SubprocessError):
        print('SCAN_NEGATIVE_WORKFLOW=FAIL\nNEGATIVE_CASE=NOT_CONFIRMED\nERROR_CODE=CONTEXT_OR_SETUP_ERROR')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
