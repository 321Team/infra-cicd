#!/usr/bin/env python3
"""Produce fixed OCI base and four native-target wheel/lock profiles on Runner .20."""
import argparse
import fcntl
import hashlib
import json
import math
import io
import os
from pathlib import Path
import secrets
import signal
import stat
import subprocess
import sys
import tarfile
import time

import supply_pip_bridge
from supply_inputs import CONFIG_DIGEST, MANIFEST_DIGEST, REQUIREMENTS, SOURCE_SHA
from supply_transport import (Client, HOME, LEASE, SCOPE, Stop, base_archive, document, encode,
                              idle, preflight, require, sha, window, write_new)
from supply_wheels import WheelIndex, make_lock

PROBE = """import json, platform, sys
from pip._vendor.packaging.tags import sys_tags
import pip
print(json.dumps({'python':sys.version,'platform':platform.platform(),
 'pip':pip.__version__,'tags':[str(t) for t in sys_tags()]}))
"""


def clean_env():
    return {'HOME': str(HOME), 'PATH': '/usr/local/bin:/usr/bin:/bin', 'LANG': 'C.UTF-8',
            'XDG_RUNTIME_DIR': '/run/user/1000', 'PIP_CONFIG_FILE': os.devnull}


class Commands:
    def __init__(self, folder):
        self.folder, self.client, self.records = folder, None, []
        self.podman = ['/usr/bin/podman', '--root', str(folder / 'storage'),
                       '--runroot', str(folder / 'runroot'), '--storage-driver', 'vfs',
                       '--tmpdir', str(folder / 'podman-tmp'), '--events-backend', 'none']

    def run(self, args, timeout=300):
        idle()
        if self.client:
            self.client.check()
            timeout = min(timeout, max(1, self.client.deadline - time.monotonic()))
        number = len(self.records)
        logfile = self.folder / ('command-%02d.stdout.log' % number)
        errfile = self.folder / ('command-%02d.stderr.log' % number)
        record = {'argv': [str(x) for x in args], 'stdout': logfile.name,
                  'stderr': errfile.name, 'started_epoch': time.time()}
        self.records.append(record)
        with logfile.open('xb') as out, errfile.open('xb') as err:
            proc = subprocess.Popen([str(x) for x in args], stdin=subprocess.DEVNULL,
                stdout=out, stderr=err, env=clean_env(), start_new_session=True)
            try:
                code = proc.wait(timeout=timeout)
            except BaseException:
                os.killpg(proc.pid, signal.SIGTERM)
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid, signal.SIGKILL)
                    proc.wait()
                raise
        record.update(returncode=code, finished_epoch=time.time())
        require(logfile.stat().st_size + errfile.stat().st_size <= 8 * 1024**2, 'COMMAND_LOG_TOO_LARGE')
        require(code == 0, 'COMMAND_FAILED_' + str(number))
        if self.client:
            self.client.check()
        return logfile.read_bytes()

    def container(self, args, timeout=300):
        # All containers have no external network, including dependency resolution.
        seconds = min(timeout, max(1, math.floor(self.client.deadline - time.monotonic())))
        return self.podman + ['run', '--rm', '--pull=never', '--network=none', '--read-only',
            '--cap-drop=ALL', '--security-opt=no-new-privileges', '--security-opt=label=disable',
            '--pids-limit=128', '--memory=1g', '--cpus=1', '--timeout=' + str(seconds),
            '--no-healthcheck', '--entrypoint=/usr/local/bin/python',
            '--env=PIP_CONFIG_FILE=/dev/null', '--env=HOME=/tmp',
            '--mount=type=bind,src=' + str(self.folder) + ',dst=/supply',
            'sha256:' + CONFIG_DIGEST[7:], '-I', '-B'] + args


def roots_for(app, mode):
    roots = REQUIREMENTS[app + '/requirements.txt'].splitlines()
    if mode == 'test':
        roots += [x for x in REQUIREMENTS[app + '/requirements-test.txt'].splitlines()
                  if not x.startswith('-r ')]
    return roots


def produce(folder, commands, result):
    # No network or lease before host venv + local tools have passed.
    tool_venv = folder / 'resolver'
    commands.run(['/usr/bin/python3', '-I', '-B', '-m', 'venv', tool_venv])
    host_python = tool_venv / 'bin/python'
    host_probe = commands.run([host_python, '-I', '-B', '-c', PROBE])
    write_new(folder / 'host-target.json', encode(document(host_probe)))
    commands.run(['/usr/bin/podman', '--version'])
    lease = window(int(time.time()))
    result['window'] = lease
    client = commands.client = Client(lease)
    result['requests'] = client.receipts
    signal.alarm(max(1, math.ceil(lease['expires_epoch'] - time.time())))
    archive = folder / 'base.oci.tar'
    result['base_archive_sha256'] = base_archive(client, archive)
    result['BASE_BLOBS_AND_DIFFIDS'] = 'PASS'
    commands.run(commands.podman + ['load', '--input', archive])
    identities = json.loads(commands.run(commands.podman + ['image', 'inspect', '--format=json', CONFIG_DIGEST]))
    require(isinstance(identities, list) and len(identities) == 1, 'IMPORTED_IMAGE_COUNT')
    identity = identities[0]
    require(identity.get('Id', '').removeprefix('sha256:') == CONFIG_DIGEST[7:] and
            identity.get('Architecture') == 'amd64' and identity.get('Os') == 'linux',
            'IMPORTED_IMAGE_IDENTITY_MISMATCH')
    result['IMAGE_IMPORT_CONFIG_ID'] = 'PASS'
    result['REPOSITORY_DIGEST_RESOLUTION'] = 'NOT_VERIFIED'
    image_probe = document(commands.run(commands.container(['-c', PROBE])))
    write_new(folder / 'image-target.json', encode(image_probe))
    bridge = supply_pip_bridge.__loader__.get_data(supply_pip_bridge.__file__)
    write_new(folder / 'supply_pip_bridge.py', bridge)
    with WheelIndex(client, folder / 'index.sock') as index:
        for app in ('transaction-api', 'fds-engine'):
            for mode in ('runtime', 'test'):
                profile = app + '-' + mode
                print('Preparing ' + profile, flush=True)
                target = folder / profile
                target.mkdir(mode=0o700)
                wheels = target / 'wheels'
                wheels.mkdir(mode=0o700)
                roots = roots_for(app, mode)
                write_new(target / 'roots.txt', ('\n'.join(roots) + '\n').encode())
                prefix = '/supply/' + profile if mode == 'runtime' else str(target)
                download = ['download', '--index-url', '@INDEX@', '--only-binary=:all:',
                            '--retries=0', '--timeout=25', '--progress-bar=off',
                            '--dest', prefix + '/wheels', '-r', prefix + '/roots.txt']
                if mode == 'runtime':
                    args = commands.container(['/supply/supply_pip_bridge.py', '/supply/index.sock'] + download)
                else:
                    args = [host_python, '-I', '-B', folder / 'supply_pip_bridge.py', folder / 'index.sock'] + download
                commands.run(args)
                require(index.error is None, index.error or 'INDEX_FAILED')
                lock, inventory = make_lock(wheels, roots)
                write_new(target / 'requirements.lock', lock)
                write_new(target / 'inventory.json', encode(inventory))
                # Fresh venv per target; full hash lock retains requested extras.
                venv_path = prefix + '/check-venv'
                if mode == 'runtime':
                    commands.run(commands.container(['-m', 'venv', venv_path]))
                    def native(args):
                        command = commands.container(args)
                        i = command.index('--entrypoint=/usr/local/bin/python')
                        command[i] = '--entrypoint=' + venv_path + '/bin/python'
                        return command
                else:
                    commands.run(['/usr/bin/python3', '-I', '-B', '-m', 'venv', venv_path])
                    def native(args):
                        return [venv_path + '/bin/python', '-I', '-B'] + args
                commands.run(native(['-m', 'pip', '--isolated', '--disable-pip-version-check',
                    '--no-cache-dir', 'install', '--no-index', '--find-links', prefix + '/wheels',
                    '--only-binary=:all:', '--require-hashes', '-r', prefix + '/requirements.lock']))
                commands.run(native(['-m', 'pip', '--isolated', '--disable-pip-version-check', 'check']))
                result['profiles'][profile] = {'lock_sha256': sha(lock), 'wheel_count': len(inventory),
                    'inventory_sha256': sha(encode(inventory)), 'OFFLINE_INSTALL_AND_PIP_CHECK': 'PASS',
                    'target': 'pinned-base-image' if mode == 'runtime' else 'runner-host-venv'}
    write_new(HOME / (LEASE + '.completed'), encode({'scope': SCOPE,
              'completed_epoch': time.time(), 'evidence_directory': str(folder)}))
    result['SUPPLY_COLLECTION'] = 'PASS'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--collect', action='store_true', required=True)
    parser.parse_args(argv)
    os.umask(0o077)
    result = {'schema': 'P1-CICD-runner-supply-1', 'scope': SCOPE, 'owner': '이권욱',
              'source_sha_for_requirements': SOURCE_SHA, 'approved_app_source_sha': None,
              'manifest_digest': MANIFEST_DIGEST, 'config_digest': CONFIG_DIGEST,
              'SUPPLY_COLLECTION': 'FAIL', 'BASE_BLOBS_AND_DIFFIDS': 'NOT_EXECUTED',
              'IMAGE_IMPORT_CONFIG_ID': 'NOT_EXECUTED', 'profiles': {},
              'APP_TEST_BUILD_SCAN': 'NOT_EXECUTED', 'DELIVERY_GATE': 'NOT_VERIFIED',
              'NETWORK_ENFORCEMENT': 'CLIENT_ONLY_NOT_FIREWALL_ATTESTED',
              'NETWORK_POLICY': 'NOT_MODIFIED', 'started_epoch': time.time()}
    folder = commands = lock = None
    previous = signal.getsignal(signal.SIGALRM)
    def expired(*_):
        raise Stop('WINDOW_EXPIRED')
    try:
        preflight()
        result['executable_sha256'] = sha(Path(sys.argv[0]).read_bytes())
        fd = os.open(HOME / 'FDS_CICD_Supply.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
        lock = os.fdopen(fd, 'r+b')
        info = os.fstat(fd)
        require(stat.S_ISREG(info.st_mode) and info.st_uid == 1000 and
                stat.S_IMODE(info.st_mode) == 0o600, 'SUPPLY_LOCK_INVALID')
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Stop('SUPPLY_ALREADY_RUNNING') from None
        folder = HOME / ('FDS_CICD_Supply.' + time.strftime('%Y%m%dT%H%M%SZ', time.gmtime()) + '.' + secrets.token_hex(4))
        folder.mkdir(mode=0o700)
        commands = Commands(folder)
        signal.signal(signal.SIGALRM, expired)
        produce(folder, commands, result)
    except Stop as exc:
        result['error_code'] = str(exc)
    except KeyboardInterrupt:
        result['error_code'] = 'INTERRUPTED'
    except Exception:
        result['error_code'] = 'SUPPLY_LOCAL_OR_NETWORK_ERROR'
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)
    result['finished_epoch'] = time.time()
    result['commands'] = commands.records if commands else []
    if folder:
        result['evidence_directory'] = str(folder)
        write_new(folder / 'result.json', encode(result))
        write_new(folder / 'result.json.sha256', (sha(encode(result)) + '  result.json\n').encode())
        # Small receipt bundle only; OCI/wheels/venvs/storage remain on the Runner.
        files = {p.name: p.read_bytes() for p in folder.iterdir()
                 if p.is_file() and p.suffix in ('.json', '.log')}
        for profile in result['profiles']:
            for name in ('roots.txt', 'requirements.lock', 'inventory.json'):
                files[profile + '/' + name] = (folder / profile / name).read_bytes()
        files['SHA256SUMS'] = ''.join(sha(raw) + '  ' + name + '\n'
                                    for name, raw in sorted(files.items())).encode()
        archive_path = folder / 'supply-evidence.tar.gz'
        with archive_path.open('xb') as out, tarfile.open(fileobj=out, mode='w:gz') as archive:
            for name, raw in sorted(files.items()):
                item = tarfile.TarInfo(name)
                item.size, item.mode = len(raw), 0o600
                archive.addfile(item, io.BytesIO(raw))
        write_new(folder / 'supply-evidence.tar.gz.sha256',
                  (sha(archive_path.read_bytes()) + '  supply-evidence.tar.gz\n').encode())
    if lock:
        lock.close()
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result['SUPPLY_COLLECTION'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
