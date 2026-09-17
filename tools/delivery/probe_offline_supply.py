#!/usr/bin/env python3
"""Standalone Runner intake/digest-build probe; never runs app tests or Scan."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import signal
import socket
import stat
import subprocess
import sys
import tarfile
import tempfile

import offline_supply as supply
from build_scan import read_output, run_command
from prepare_db import PreflightError, require, utcnow


def idle():
    for path in Path('/proc').glob('[0-9]*/comm'):
        try:
            require(path.read_text().strip() != 'Runner.Worker', 'RUNNER_JOB_ACTIVE')
        except FileNotFoundError:
            continue


def preflight():
    require(os.getuid() == os.geteuid() == 1000, 'RUNNER_UID_REQUIRED')
    user = pwd.getpwuid(1000)
    require(user.pw_name == 'server01_cicd-runner' and
            socket.gethostname().split('.')[0] == 'cicd-runner01', 'RUNNER_HOST_USER_REQUIRED')
    home = Path(user.pw_dir)
    supply.plain_path(home)
    info = home.lstat()
    require(home == Path('/home/server01_cicd-runner') and stat.S_ISDIR(info.st_mode)
            and info.st_uid == 1000 and stat.S_IMODE(info.st_mode) == 0o700, 'HOME_IDENTITY_OR_MODE')
    require(shutil.disk_usage(home).free >= 4 * 1024**3, 'FOUR_GIB_FREE_REQUIRED')
    idle()
    return home


def execute(record, base, folder):
    result = {'schema': 'P1-CICD-offline-probe-1', 'OFFLINE_PROBE': 'FAIL',
              'SUPPLY_COPY': 'NOT_EXECUTED', 'REPOSITORY_DIGEST_RESOLUTION': 'NOT_EXECUTED',
              'BASE_DIGEST_BUILD': 'NOT_EXECUTED', 'APP_TEST_BUILD_SCAN': 'NOT_EXECUTED',
              'DELIVERY_GATE': 'NOT_VERIFIED', 'NETWORK_REQUESTS': 'NONE_BY_THIS_TOOL',
              'started_at': utcnow().isoformat(), 'steps': []}
    rc = 1
    try:
        supply.validate_record(record)
        require(base == 'docker.io/library/python@' + record['manifest_digest'], 'PROBE_BASE_BINDING')
        (folder / 'home').mkdir(mode=0o700)
        env = {'HOME': str(folder / 'home'), 'PATH': '/usr/local/bin:/usr/bin:/bin',
               'LANG': 'C.UTF-8', 'XDG_RUNTIME_DIR': '/run/user/1000', 'PIP_CONFIG_FILE': os.devnull}

        def command(label, argv, cwd=folder, timeout=300):
            idle()  # Observation only; not a GitHub scheduler reservation.
            output = folder / (label + '.log')
            step = {'name': label, 'argv': argv, 'status': 'STARTED', 'started_at': utcnow().isoformat()}
            result['steps'].append(step)
            code = run_command(argv, cwd, env, output, timeout)
            step.update(exit_code=code, status='PASS' if code == 0 else 'FAIL',
                        log_sha256=supply.sha256(output), finished_at=utcnow().isoformat())
            require(code == 0, 'COMMAND_FAILED:' + label)
            require(output.stat().st_size <= 8 * 1024**2, 'PROBE_LOG_TOO_LARGE')
            return output

        staged = folder / 'offline-supply'
        result['SUPPLY_COPY'] = 'FAIL'
        result.update(supply.stage(record, staged), SUPPLY_COPY='PASS')
        podman = ['/usr/bin/podman', '--remote=false', '--root', str(folder / 'storage'),
                  '--runroot', str(folder / 'runroot'), '--storage-driver', 'vfs',
                  '--tmpdir', str(folder / 'podman-tmp'), '--events-backend', 'none']
        require(read_output(command('podman-version', podman + ['--version'])) == 'podman version 6.1.0',
                'PODMAN_VERSION_MISMATCH')
        require(read_output(command('podman-rootless', podman + ['info', '--format', '{{.Host.Security.Rootless}}'])) == 'true',
                'ROOTLESS_REQUIRED')
        result['REPOSITORY_DIGEST_RESOLUTION'] = 'FAIL'
        result.update(supply.import_base(command, podman, record, staged, base))
        context = folder / 'probe-context'
        context.mkdir(mode=0o700)
        (context / 'Dockerfile').write_text('FROM ' + base + '\nRUN ["/usr/local/bin/python", "-I", "-B", "-c", "print(123)"]\n')
        auth = folder / 'empty-auth.json'
        auth.write_text('{"auths":{}}\n')
        iid = folder / 'probe.iid'
        result['BASE_DIGEST_BUILD'] = 'FAIL'
        command('offline-base-build', podman + ['build', '--format', 'oci', '--platform', 'linux/amd64',
                '--pull=never', '--network=none', '--http-proxy=false', '--no-cache',
                '--security-opt=label=disable', '--tls-verify=true', '--authfile', str(auth),
                '--iidfile', str(iid), '--file', str(context / 'Dockerfile'), str(context)], timeout=600)
        image_id = read_output(iid)
        require(re.fullmatch(r'sha256:[0-9a-f]{64}', image_id), 'PROBE_IMAGE_ID_INVALID')
        supply.verify_staged(staged, record)
        result.update(BASE_DIGEST_BUILD='PASS', probe_image_id=image_id, OFFLINE_PROBE='PASS')
        rc = 0
    except PreflightError as exc:
        result['error_code'] = str(exc)
    except (OSError, ValueError, TypeError, KeyError, AttributeError, EOFError,
            supply.zipfile.BadZipFile, tarfile.TarError, subprocess.SubprocessError):
        result['error_code'] = 'PROBE_INPUT_COMMAND_OR_EVIDENCE_ERROR'
    result['finished_at'] = utcnow().isoformat()
    return rc, result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepare', required=True, action='store_true')
    parser.parse_args(argv)
    os.umask(0o077)
    folder = None
    try:
        home = preflight()
        raw = __loader__.get_data(str(Path(__file__).with_name('offline-probe-input.json')))
        record = supply.json_object(raw)
        lock = home / 'FDS_CICD_OfflineProbe.lock'
        descriptor = os.open(lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
        with os.fdopen(descriptor, 'r+') as stream:
            info = os.fstat(stream.fileno())
            require(stat.S_ISREG(info.st_mode) and info.st_uid == 1000 and
                    stat.S_IMODE(info.st_mode) == 0o600, 'PROBE_LOCK_INVALID')
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            folder = Path(tempfile.mkdtemp(prefix='FDS_CICD_OfflineProbe.', dir=home))
            print('EVIDENCE_DIR=' + str(folder), flush=True)
            def interrupted(_signum, _frame):
                raise PreflightError('PROBE_INTERRUPTED_OR_900S_LIMIT')
            for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGALRM):
                signal.signal(sig, interrupted)
            signal.alarm(900)
            rc, result = execute(record, 'docker.io/library/python@' + record['manifest_digest'], folder)
            signal.alarm(0)
            result['input_sha256'] = hashlib.sha256(raw).hexdigest()
            result['tool_sha256'] = supply.sha256(Path(sys.argv[0]).resolve())
            (folder / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
            files = sorted([folder / 'result.json', *folder.glob('*.log')])
            (folder / 'SHA256SUMS').write_text(''.join(supply.sha256(p) + '  ' + p.name + '\n' for p in files))
            archive = folder / 'offline-probe-evidence.tar.gz'
            with tarfile.open(archive, 'x:gz') as tar:
                for p in [*files, folder / 'SHA256SUMS']:
                    tar.add(p, arcname=p.name, recursive=False)
            sidecar = archive.with_name(archive.name + '.sha256')
            sidecar.write_text(supply.sha256(archive) + '  ' + archive.name + '\n')
            print('OFFLINE_PROBE=' + result['OFFLINE_PROBE'])
            print('ERROR_CODE=' + result.get('error_code', 'NONE'))
            print('EVIDENCE_ARCHIVE=' + str(archive))
            return rc
    except (PreflightError, OSError, ValueError, KeyError) as exc:
        code = str(exc) if isinstance(exc, PreflightError) else 'PROBE_PREFLIGHT_OR_EXPORT_ERROR'
        print('OFFLINE_PROBE=FAIL\nERROR_CODE=' + code)
        if folder:
            print('PARTIAL_EVIDENCE_DIR=' + str(folder))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
