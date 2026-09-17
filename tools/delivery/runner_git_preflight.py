#!/usr/bin/env python3
"""Login-context Git intake and read-only supply/capacity observation; no app run."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import resource
import signal
import socket
import stat
import subprocess
import tarfile
import tempfile

ORIGIN = 'https://github.com/supreme33-kr/fds-msa.git'
OBSERVED_SHA = '254b82231f8af79a06419b581174647bbccbe2bc'
OBSERVED_TREE = '6ee93b5a5788288aa9be71c3c62589abc27dde0c'
REFERENCE_CICD_SHA = 'dcf9fa232f0f4ec5f347dc5dc67a02668664d20b'
SUPPLY = {
    "directory": "/home/server01_cicd-runner/FDS_CICD_Supply.20260917T010757Z.974fb972",
    "profiles": {
        "transaction-api-runtime": {
            "roots_sha256": "0b8f3430a64f4b81429277bf651b2e1b62a68084f8d79b36a63d09d065b76947",
            "lock_sha256": "3a71d0d227415f3f818642b5370920f02a23a9bcb28f4dd31aac1d59d96c756e",
            "inventory_sha256": "b5ddd2595efad3a48dc6d09bb963515ab5cb8bfcd38604116ea2329d819640a4"
        },
        "transaction-api-test": {
            "roots_sha256": "dc9cf9fa2bfb10708504fc283835f8f7e138d115f83de8b94eededa9ef6eceb0",
            "lock_sha256": "72137816616e16e2b88f86630c507800414c3b37c14c7f624b9042fd66a8ef85",
            "inventory_sha256": "acf41ad32fc8aed812f545b5b3e60d74250d5c37b88c30530c95bb0d410d26e9"
        },
        "fds-engine-runtime": {
            "roots_sha256": "11b1f940e83bded8fa8e901005f0467c4652bfac332657b11c118ee8f9b80ca3",
            "lock_sha256": "c9d6138da341b431f0b7fdaf4aee78d59f9207e9d3f2bf4636b0505681c1f80b",
            "inventory_sha256": "6bf41b600e46f61d8c7e1a7886c411938da44fdd9dc65ea5d25127e7df2b80a5"
        },
        "fds-engine-test": {
            "roots_sha256": "3f6aee59d8afee889d56f97bfef08830ce94d630d05097fb2dfa3e8b9bf2d16c",
            "lock_sha256": "3a51dbbb6dd56d09658f0d2343e4b4847394f006f1f2aa082886bc468e797b65",
            "inventory_sha256": "56ec48ee8d9827d90b9e2281ddae125be2f3b8760656404e5f9daa5490f60acb"
        }
    }
}
GIT = ['/usr/bin/git', '--no-replace-objects',
       '-c', 'core.hooksPath=/dev/null', '-c', 'credential.helper=',
       '-c', 'http.sslVerify=true', '-c', 'http.followRedirects=false',
       '-c', 'protocol.allow=never', '-c', 'protocol.https.allow=always',
       '-c', 'fetch.recurseSubmodules=false', '-c', 'fetch.fsckObjects=true',
       '-c', 'maintenance.auto=false', '-c', 'gc.auto=0']


class ProbeError(Exception):
    pass


def require(ok, code):
    if not ok:
        raise ProbeError(code)


def now():
    return datetime.now(timezone.utc).isoformat()


def sha256(path):
    value = hashlib.sha256()
    with path.open('rb') as stream:
        while chunk := stream.read(1024**2):
            value.update(chunk)
    return value.hexdigest()


def plain(path):
    require(path.is_absolute() and path.resolve(strict=True) == path, 'PATH_LINK_OR_NONCANONICAL')


def checked_file(path, maximum, expected=None):
    plain(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        before = os.fstat(stream.fileno())
        require(stat.S_ISREG(before.st_mode) and 0 < before.st_size <= maximum, 'FILE_TYPE_OR_SIZE')
        # Large OCI/wheel contents are intentionally not rehashed by this access check.
        raw = stream.read(maximum + 1) if expected else b''
        after = os.fstat(stream.fileno())
        require((before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) ==
                (after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns), 'FILE_CHANGED')
    if expected:
        require(len(raw) == before.st_size and hashlib.sha256(raw).hexdigest() == expected,
                'SUPPLY_METADATA_HASH_MISMATCH')
    return before.st_size, raw


def capacity(home):
    v = os.statvfs(home)
    free = v.f_bavail * v.f_frsize
    return {'available_bytes': free, 'available_inodes': v.f_favail,
            'app_existing_start_minimum_bytes': 4 * 1024**3,
            'APP_START_MINIMUM': 'MEETS_MINIMUM' if free >= 4 * 1024**3 and v.f_favail > 0
                                 else 'BELOW_MINIMUM',
            'APP_COMPLETION_CAPACITY': 'NOT_PROVEN'}


def supply_access(record):
    base = Path(record['directory'])
    plain(base)
    total, _ = checked_file(base / 'base.oci.tar', 1024**3)
    profiles = {}
    for name, hashes in record['profiles'].items():
        folder = base / name
        for filename, key in (('roots.txt', 'roots_sha256'), ('requirements.lock', 'lock_sha256'),
                              ('inventory.json', 'inventory_sha256')):
            _, raw = checked_file(folder / filename, 1024**2, hashes[key])
            if filename == 'inventory.json':
                rows = json.loads(raw)
        require(isinstance(rows, list) and 0 < len(rows) <= 256, 'SUPPLY_INVENTORY_INVALID')
        names = [row['file'] for row in rows]
        require(all(isinstance(n, str) and re.fullmatch(r'[A-Za-z0-9_.+!-]+\.whl', n) for n in names)
                and len(set(names)) == len(names), 'SUPPLY_WHEEL_NAMES_INVALID')
        wheels = folder / 'wheels'
        plain(wheels)
        require({p.name for p in wheels.iterdir()} == set(names), 'SUPPLY_WHEEL_SET_MISMATCH')
        for filename in names:
            size, _ = checked_file(wheels / filename, 64 * 1024**2)
            total += size
        profiles[name] = {'metadata_hashes': hashes, 'readable_wheels': len(names)}
    return {'SUPPLY_ACCESS': 'PASS', 'directory': str(base), 'profiles': profiles,
            'observed_oci_and_wheel_bytes': total, 'RAW_PAYLOAD_HASHES': 'NOT_RECHECKED',
            'BASE_LOAD_BUILD': 'NOT_EXECUTED'}


def git_environment(home, incoming):
    env = {'PATH': '/usr/bin:/bin', 'LANG': 'C', 'LC_ALL': 'C',
           'HOME': str(home), 'XDG_CONFIG_HOME': str(home / 'config'),
           'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': '/dev/null',
           'GIT_TERMINAL_PROMPT': '0', 'GIT_NO_REPLACE_OBJECTS': '1', 'GIT_LFS_SKIP_SMUDGE': '1'}
    for key in ('HTTP_PROXY', 'HTTPS_PROXY', 'NO_PROXY', 'http_proxy', 'https_proxy', 'no_proxy'):
        if key in incoming:
            env[key] = incoming[key]
    return env


def child_limits():
    resource.setrlimit(resource.RLIMIT_FSIZE, (64 * 1024**2, 64 * 1024**2))


def command(folder, steps, env, label, argv, timeout=60):
    path = folder / (label + '.log')
    timed_out = False
    with path.open('xb') as stream:
        proc = subprocess.Popen(argv, cwd=folder, env=env, stdin=subprocess.DEVNULL,
                                stdout=stream, stderr=subprocess.STDOUT, start_new_session=True,
                                preexec_fn=child_limits)
        try:
            rc = proc.wait(timeout=timeout)
        except BaseException as exc:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait()
            if not isinstance(exc, subprocess.TimeoutExpired):
                raise
            timed_out, rc = True, 124
    steps.append({'name': label, 'exit_code': rc, 'timeout': timed_out,
                  'log_sha256': sha256(path), 'raw_log_exported': False})
    require(rc == 0, ('GIT_COMMAND_TIMEOUT:' if timed_out else 'GIT_COMMAND_FAILED:') + label)
    require(path.stat().st_size <= 64 * 1024**2, 'COMMAND_LOG_SIZE')
    return path


def small_text(path):
    require(path.stat().st_size <= 4096, 'GIT_IDENTITY_OUTPUT_TOO_LARGE')
    return path.read_text().strip()


def probe_git(folder, record, incoming, expected_sha=OBSERVED_SHA, expected_tree=OBSERVED_TREE):
    home, target = folder / 'git-home', folder / 'source.git'
    require(not os.path.lexists(home) and not os.path.lexists(target), 'NEW_GIT_PATH_REQUIRED')
    home.mkdir(mode=0o700)
    target.mkdir(mode=0o700)
    env = git_environment(home, incoming)
    def run(label, args, timeout=60):
        return command(folder, record['steps'], env, label, GIT + args, timeout)
    record['git_version'] = small_text(run('git-version', ['--version']))
    run('git-init', ['init', '--bare', '--template=', str(target)])
    run('git-origin', ['-C', str(target), 'remote', 'add', 'origin', ORIGIN])
    run('git-fetch', ['-C', str(target), 'fetch', '--no-tags', '--depth=1',
                     '--no-recurse-submodules', 'origin', expected_sha], 300)
    actual = small_text(run('git-commit', ['-C', str(target), 'rev-parse', 'FETCH_HEAD^{commit}']))
    require(actual == expected_sha, 'OBSERVED_COMMIT_MISMATCH')
    tree = small_text(run('git-tree', ['-C', str(target), 'rev-parse', 'FETCH_HEAD^{tree}']))
    require(tree == expected_tree, 'OBSERVED_TREE_MISMATCH')
    bare = small_text(run('git-bare', ['-C', str(target), 'rev-parse', '--is-bare-repository']))
    require(bare == 'true', 'BARE_REPOSITORY_REQUIRED')
    return {'GIT_INTAKE': 'PASS', 'commit': actual, 'tree': tree, 'bare': True,
            'SOURCE_APPROVAL': 'NOT_GRANTED_HERE', 'WORKTREE_APP_EXECUTION': 'NOT_EXECUTED'}


def error_code(exc):
    if isinstance(exc, ProbeError):
        return str(exc)
    if isinstance(exc, FileNotFoundError):
        return 'REQUIRED_FILE_OR_PROGRAM_MISSING'
    if isinstance(exc, PermissionError):
        return 'READ_OR_EXECUTE_PERMISSION_DENIED'
    return 'PREFLIGHT_IO_OR_DATA_ERROR'


def export(folder, result):
    file = folder / 'result.json'
    file.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    sums = folder / 'SHA256SUMS'
    sums.write_text(sha256(file) + '  result.json\n')
    archive = folder / 'git-preflight-evidence.tar.gz'
    with tarfile.open(archive, 'x:gz') as tar:
        for item in (file, sums):
            tar.add(item, arcname=item.name, recursive=False)
    checksum = sha256(archive)
    (folder / 'git-preflight-evidence.tar.gz.sha256').write_text(checksum + '  ' + archive.name + '\n')
    return archive


def interrupt(_signum, _frame):
    raise ProbeError('INTERRUPTED')


def run(home, folder, incoming, supply=SUPPLY):
    result = {'schema': 'P1-CICD-runner-git-preflight-1', 'started_at': now(),
              'OVERALL': 'FAIL', 'execution_context': 'RUNNER_ACCOUNT_LOGIN_NOT_ACTIONS_JOB',
              'host': socket.gethostname().split('.')[0], 'uid': os.getuid(),
              'user': pwd.getpwuid(os.getuid()).pw_name, 'tool_sha256': sha256(Path(__file__).absolute()),
              'reference_cicd_sha': REFERENCE_CICD_SHA, 'origin': ORIGIN,
              'observed_sha': OBSERVED_SHA, 'observed_tree': OBSERVED_TREE,
              'source_basis': 'OBSERVED_TRANSPORT_TARGET_NOT_APPROVED_APP_SOURCE',
              'steps': [], 'errors': {}, 'APP_TEST_BUILD_SCAN': 'NOT_EXECUTED',
              'PUSH': 'NOT_EXECUTED', 'DEPLOY': 'NOT_EXECUTED', 'DELIVERY_GATE': 'NOT_VERIFIED'}
    checks = {'capacity': lambda: capacity(home), 'supply': lambda: supply_access(supply),
              'git': lambda: probe_git(folder, result, incoming)}
    for name, check in checks.items():
        result['phase'] = name
        try:
            result[name] = check()
        except (ProbeError, OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError) as exc:
            result['errors'][name] = error_code(exc)
            if str(exc) == 'INTERRUPTED':
                break
    if not result['errors'] and result['capacity']['APP_START_MINIMUM'] == 'MEETS_MINIMUM':
        result['OVERALL'] = 'PASS'
    result['phase'], result['finished_at'] = 'finished', now()
    archive = export(folder, result)
    return result, archive


def runner_home(incoming):
    require(socket.gethostname().split('.')[0] == 'cicd-runner01', 'WRONG_HOST')
    require(os.getuid() == 1000, 'WRONG_UID')
    account = pwd.getpwuid(os.getuid())
    require(account.pw_name == 'server01_cicd-runner', 'WRONG_ACCOUNT')
    require(incoming.get('GITHUB_ACTIONS') != 'true', 'LOGIN_CONTEXT_ONLY')
    home = Path(account.pw_dir)
    plain(home)
    info = home.stat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == 1000 and
            stat.S_IMODE(info.st_mode) == 0o700, 'HOME_OWNER_OR_MODE_MISMATCH')
    return home


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepare', required=True, action='store_true')
    parser.parse_args(argv)
    os.umask(0o077)
    try:
        home = runner_home(os.environ)
        folder = Path(tempfile.mkdtemp(prefix='FDS_CICD_GitPreflight.', dir=home))
        print('EVIDENCE_DIR=' + str(folder), flush=True)
        signal.signal(signal.SIGINT, interrupt)
        signal.signal(signal.SIGTERM, interrupt)
        result, archive = run(home, folder, os.environ)
        print('GIT_PREFLIGHT=' + result['OVERALL'])
        for phase, code in result['errors'].items():
            print('FAILED_PHASE=' + phase + '\nERROR_CODE=' + code)
        print('ARCHIVE=' + str(archive))
        print('CAPACITY_CHECK=' + result.get('capacity', {}).get('APP_START_MINIMUM', 'UNAVAILABLE'))
        print('APP_TEST_BUILD_SCAN=NOT_EXECUTED\nDELIVERY_GATE=NOT_VERIFIED')
        return 0 if result['OVERALL'] == 'PASS' else 1
    except (ProbeError, OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError) as exc:
        print('GIT_PREFLIGHT=FAIL\nERROR_CODE=' + error_code(exc) + '\nDELIVERY_GATE=NOT_VERIFIED')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
