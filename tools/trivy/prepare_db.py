#!/usr/bin/env python3
"""Prepare a fresh, traceable vulnerability DB; this does not authorize delivery."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import socket
import stat
import subprocess
import sys
import tempfile

from verify_runner import BINARY_SHA256, TRIVY, sha256

DB_REPOSITORY = 'ghcr.io/aquasecurity/trivy-db:2'
SYSTEM_PATH = '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin'


class PreflightError(Exception):
    """Public error codes only; do not export subprocess output or credentials."""


def require(condition, code):
    if not condition:
        raise PreflightError(code)


def utcnow():
    return datetime.now(timezone.utc)


def regular(path):
    require(stat.S_ISREG(path.lstat().st_mode), 'EXPECTED_REGULAR_FILE')


def verify_binary():
    info = TRIVY.lstat()
    require(stat.S_ISREG(info.st_mode), 'BINARY_NOT_REGULAR')
    require(info.st_uid == 0 and info.st_gid == 0 and
            stat.S_IMODE(info.st_mode) == 0o755, 'BINARY_OWNERSHIP_OR_MODE')
    require(sha256(TRIVY) == BINARY_SHA256, 'BINARY_HASH_MISMATCH')


def timestamp(value):
    require(isinstance(value, str), 'METADATA_TIME_INVALID')
    try:
        # Python >= 3.11 accepts RFC3339Nano; datetime keeps microseconds.
        # Preserve the original strings in result.json and metadata.json.
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        raise PreflightError('METADATA_TIME_INVALID') from None
    require(parsed.tzinfo is not None and parsed.utcoffset() is not None,
            'METADATA_TIMEZONE_REQUIRED')
    return parsed.astimezone(timezone.utc)


def validate_metadata(data, now):
    require(isinstance(data, dict), 'METADATA_OBJECT_REQUIRED')
    require(type(data.get('Version')) is int and data['Version'] == 2,
            'DB_SCHEMA_UNSUPPORTED')
    updated = timestamp(data.get('UpdatedAt'))
    downloaded = timestamp(data.get('DownloadedAt'))
    next_update = timestamp(data.get('NextUpdate'))
    require(updated <= downloaded <= now, 'METADATA_TIME_ORDER_OR_FUTURE')
    require(updated < next_update, 'METADATA_UPDATE_INTERVAL_INVALID')
    require(now < next_update, 'DB_REFRESH_REQUIRED')
    return {key: data[key] for key in ('Version', 'UpdatedAt', 'DownloadedAt', 'NextUpdate')}


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, 'METADATA_DUPLICATE_KEY')
        result[key] = value
    return result


def runtime_context(env):
    require(os.getuid() == 1000 and os.geteuid() == 1000, 'RUNNER_UID_REQUIRED')
    require(pwd.getpwuid(os.getuid()).pw_name == 'server01_cicd-runner', 'RUNNER_USER_REQUIRED')
    require(socket.gethostname().split('.')[0] == 'cicd-runner01', 'RUNNER_HOST_REQUIRED')
    require(env.get('GITHUB_REPOSITORY') == '321Team/infra-cicd', 'REPOSITORY_REQUIRED')
    require(env.get('GITHUB_REF') == 'refs/heads/main', 'MAIN_REF_REQUIRED')
    require(env.get('GITHUB_EVENT_NAME') == 'workflow_dispatch', 'MANUAL_EVENT_REQUIRED')
    sha = env.get('GITHUB_SHA', '')
    run_id, attempt = env.get('GITHUB_RUN_ID', ''), env.get('GITHUB_RUN_ATTEMPT', '')
    require(bool(re.fullmatch('[0-9a-f]{40}', sha)), 'WORKFLOW_SHA_INVALID')
    require(bool(re.fullmatch('[1-9][0-9]*', run_id)) and
            bool(re.fullmatch('[1-9][0-9]*', attempt)), 'RUN_IDENTITY_INVALID')
    head = subprocess.check_output(['/usr/bin/git', 'rev-parse', 'HEAD'],
                                   text=True, timeout=15, stderr=subprocess.DEVNULL).strip()
    require(head == sha, 'CHECKOUT_SHA_MISMATCH')
    return {'workflow_sha': sha, 'run_id': run_id, 'attempt': int(attempt),
            'run_url': f'https://github.com/321Team/infra-cicd/actions/runs/{run_id}',
            'host': 'cicd-runner01', 'user': 'server01_cicd-runner', 'uid': 1000}


def scanner_environment(env):
    # Preserve configured proxy routing, not arbitrary Trivy options or Actions secrets.
    allowed = ('HTTP_PROXY', 'HTTPS_PROXY', 'NO_PROXY', 'http_proxy', 'https_proxy', 'no_proxy')
    result = {key: env[key] for key in allowed if key in env}
    result.update(PATH=SYSTEM_PATH, HOME=str(Path.home()), LC_ALL='C')
    return result


def prepare(evidence, context, env):
    """Caller supplies a newly created private directory. No old-cache fallback."""
    result = {'DB_PREFLIGHT': 'FAIL', 'DELIVERY_GATE': 'NOT_VERIFIED',
              'started_at': utcnow().isoformat(), 'context': context,
              'binary_sha256': BINARY_SHA256, 'db_repository': DB_REPOSITORY}
    phase = 'binary'
    rc = 1
    try:
        verify_binary()
        phase = 'download'
        cache = evidence / 'cache'
        cache.mkdir(mode=0o700)  # Refuse an existing cache, including a symlink.
        command = [str(TRIVY), 'image', '--cache-dir', str(cache),
                   '--db-repository', DB_REPOSITORY, '--download-db-only',
                   '--no-progress', '--timeout', '5m', '--skip-version-check']
        download = subprocess.run(command, cwd=evidence, env=scanner_environment(env),
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                  timeout=330, check=False)
        result['download_exit_code'] = download.returncode
        require(download.returncode == 0, 'DB_DOWNLOAD_FAILED')
        phase = 'metadata'
        db_dir = cache / 'db'
        require(stat.S_ISDIR(db_dir.lstat().st_mode), 'DB_DIRECTORY_NOT_REGULAR')
        db, metadata = db_dir / 'trivy.db', db_dir / 'metadata.json'
        regular(db)
        regular(metadata)
        require(db.stat().st_size > 0, 'DB_EMPTY')
        require(metadata.stat().st_size <= 1024 * 1024, 'METADATA_TOO_LARGE')
        raw = metadata.read_bytes()
        data = json.loads(raw, object_pairs_hook=unique_object)
        checked = utcnow()
        result['metadata'] = validate_metadata(data, checked)
        phase = 'hash'
        result['db_sha256'] = sha256(db)
        result['metadata_sha256'] = hashlib.sha256(raw).hexdigest()
        require(sha256(metadata) == result['metadata_sha256'], 'METADATA_CHANGED')
        verify_binary()
        checked = utcnow()  # Hashing may cross NextUpdate.
        validate_metadata(data, checked)
        result['validated_at'] = checked.isoformat()
        result['cache_dir'] = str(cache)
        result['DB_PREFLIGHT'] = 'PASS'
        rc = 0
    except PreflightError as exc:
        result['error_code'] = str(exc)
    except subprocess.TimeoutExpired:
        result['error_code'] = 'DB_DOWNLOAD_TIMEOUT'
    except (OSError, ValueError, subprocess.SubprocessError):
        result['error_code'] = 'IO_OR_METADATA_ERROR'
    finally:
        result['phase'] = phase
        result['finished_at'] = utcnow().isoformat()
        (evidence / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
        hashes = [(sha256(evidence / 'result.json'), 'result.json')]
        if result['DB_PREFLIGHT'] == 'PASS':
            hashes += [(result['db_sha256'], 'cache/db/trivy.db'),
                       (result['metadata_sha256'], 'cache/db/metadata.json')]
        (evidence / 'SHA256SUMS').write_text(''.join(f'{value}  {name}\n' for value, name in hashes))
    return rc, result


def main():
    python_version = '.'.join(str(part) for part in sys.version_info[:3])
    print(f'PYTHON_VERSION={python_version}', flush=True)
    print(f'PYTHON_EXECUTABLE={sys.executable}', flush=True)
    if sys.version_info < (3, 11):
        print('DB_PREFLIGHT=FAIL\nERROR_CODE=PYTHON_3_11_REQUIRED\nDELIVERY_GATE=NOT_VERIFIED')
        return 1
    os.umask(0o077)
    try:
        context = runtime_context(os.environ)
    except (PreflightError, OSError, ValueError, KeyError, subprocess.SubprocessError):
        print('DB_PREFLIGHT=FAIL\nERROR_CODE=RUNNER_CONTEXT_INVALID\nDELIVERY_GATE=NOT_VERIFIED')
        return 1
    context.update(python_version=python_version, python_executable=sys.executable)
    evidence = Path(tempfile.mkdtemp(prefix=f'trivy-db-{context["run_id"]}-{context["attempt"]}-',
                                   dir=Path.home()))
    print(f'EVIDENCE_DIR={evidence}', flush=True)
    rc, result = prepare(evidence, context, os.environ)
    print(f'DB_PREFLIGHT={result["DB_PREFLIGHT"]}')
    print(f'ERROR_CODE={result.get("error_code", "NONE")}')
    print('DELIVERY_GATE=NOT_VERIFIED')
    if rc == 0:
        print(f'DB_CACHE_DIR={result["cache_dir"]}')
    summary = os.environ.get('GITHUB_STEP_SUMMARY')
    if summary:
        with open(summary, 'a') as stream:
            stream.write(f'DB preflight: **{result["DB_PREFLIGHT"]}**\n\n'
                         f'Run: {context["run_url"]} / attempt {context["attempt"]}\n\n'
                         f'Workflow SHA: `{context["workflow_sha"]}`\n\n'
                         f'Evidence on runner: `{evidence}`\n\nDELIVERY_GATE=NOT_VERIFIED\n')
    return rc


if __name__ == '__main__':
    raise SystemExit(main())
