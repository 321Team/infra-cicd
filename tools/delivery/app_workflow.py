#!/usr/bin/env python3
"""Main/manual orchestration; pinned Git intake, then Test/Build/Scan."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import pwd
import shutil
import signal
import stat
import subprocess
import tempfile

import build_scan as flow
import source_auth as auth

WORKFLOW = '.github/workflows/app-test-build-scan.yml'
ORIGIN = 'https://github.com/' + flow.SOURCE_REPOSITORY + '.git'
GIT = auth.GIT


def read_config(path):
    flow.offline.plain_path(path)
    flow.require(path.stat().st_size <= 64 * 1024, 'INPUT_TOO_LARGE')
    raw = path.read_bytes()
    config = flow.validate_config(flow.json_object(raw))
    flow.require(config['schema'] in ('P1-CICD-app-build-scan-2', 'P1-CICD-app-build-scan-3'),
                 'OFFLINE_SCHEMA_REQUIRED')
    return raw, config


def git_environment(home, incoming):
    env = flow.scanner_environment(incoming)
    env.update(HOME=str(home), XDG_CONFIG_HOME=str(home / 'config'),
               GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL='/dev/null',
               GIT_TERMINAL_PROMPT='0', GIT_NO_REPLACE_OBJECTS='1',
               GIT_LFS_SKIP_SMUDGE='1')
    return env


def command_runner(folder, incoming, steps):
    home = folder / 'git-home'
    home.mkdir(mode=0o700)
    env = git_environment(home, incoming)

    def command(label, argv, timeout=60):
        output = folder / (label + '.log')
        rc = flow.run_command(argv, folder, env, output, timeout)
        steps.append({'name': label, 'exit_code': rc, 'log_sha256': flow.sha256(output)})
        flow.require(rc == 0, 'SOURCE_COMMAND_FAILED:' + label)
        return output
    return command


def fetch_source(config, target, command, private_fetch=None):
    # A new bare object repository avoids worktree filters, hooks and app execution.
    repository = config['source']['repository']
    origin = auth.origin(repository)
    private = repository == auth.PRIVATE_REPOSITORY
    flow.require(not private or private_fetch is not None, 'SOURCE_READ_TOKEN_REQUIRED')
    flow.require(not os.path.lexists(target), 'SOURCE_TARGET_ALREADY_EXISTS')
    target.mkdir(mode=0o700)
    command('source-init', GIT + ['init', '--bare', '--template=', str(target)])
    command('source-origin-set', GIT + ['-C', str(target), 'remote', 'add', 'origin', origin])
    if private:
        private_fetch(target, config['source']['commit'])
    else:
        command('source-fetch', GIT + ['-C', str(target), 'fetch', '--no-tags', '--depth=1',
                '--no-auto-maintenance', '--no-recurse-submodules', 'origin',
                config['source']['commit']], timeout=300)
    fetched = command('source-fetched', GIT + ['-C', str(target), 'rev-parse', 'FETCH_HEAD^{commit}'])
    flow.require(flow.read_output(fetched) == config['source']['commit'], 'FETCHED_SOURCE_SHA_MISMATCH')
    command('source-head-set', GIT + ['-C', str(target), 'update-ref', '--no-deref',
                                    'HEAD', config['source']['commit']])


def validate_handoff(config, source, command):
    """Read committed objects only; no import, checkout, pip, tests or containers."""
    flow.validate_config(config)
    flow.require(config['schema'] in ('P1-CICD-app-build-scan-2', 'P1-CICD-app-build-scan-3'),
                 'OFFLINE_SCHEMA_REQUIRED')
    flow.offline.plain_path(source)
    sha = config['source']['commit']
    head = command('handoff-head', GIT + ['-C', str(source), 'rev-parse', 'HEAD'])
    flow.require(flow.read_output(head) == sha, 'SOURCE_HEAD_MISMATCH')
    origin = command('handoff-origin', GIT + ['-C', str(source), 'remote', 'get-url', 'origin'])
    expected_origin = auth.origin(config['source']['repository'])
    flow.require(flow.read_output(origin) in (expected_origin, expected_origin.removesuffix('.git')),
                 'SOURCE_ORIGIN_MISMATCH')
    tree = command('handoff-tree', GIT + ['-C', str(source), 'ls-tree', '-r', '-z', sha])
    flow.require(tree.stat().st_size <= 1024**2, 'SOURCE_TREE_TOO_LARGE')
    entries = {}
    for row in tree.read_bytes().split(b'\0'):
        if not row:
            continue
        meta, raw_path = row.split(b'\t', 1)
        mode, kind, _oid = meta.split(b' ')
        path = flow.relative(raw_path.decode('utf-8'))
        flow.require(mode in (b'100644', b'100755') and kind == b'blob', 'SOURCE_LINK_OR_SUBMODULE')
        flow.require(path not in entries and len(entries) < 10000, 'SOURCE_DUPLICATE_OR_TOO_MANY_FILES')
        flow.require('.venv' not in path.split('/'), 'SOURCE_VENV_NOT_ALLOWED')
        entries[path] = mode.decode()
    checks = {}

    def blob(path):
        flow.require(path in entries, 'HANDOFF_FILE_MISSING:' + path)
        label = 'handoff-' + path.replace('/', '-')
        size = command(label + '-size', GIT + ['-C', str(source), 'cat-file', '-s', sha + ':' + path])
        flow.require(0 <= int(flow.read_output(size)) <= 1024**2, 'HANDOFF_FILE_TOO_LARGE')
        content = command(label, GIT + ['-C', str(source), 'cat-file', 'blob', sha + ':' + path])
        raw = content.read_bytes()
        flow.require(len(raw) <= 1024**2, 'HANDOFF_FILE_TOO_LARGE')
        checks[path] = {'sha256': hashlib.sha256(raw).hexdigest(), 'mode': entries[path]}
        return raw

    for path, digest in config['offline_supply']['requirements'].items():
        flow.require(hashlib.sha256(blob(path)).hexdigest() == digest, 'SOURCE_REQUIREMENTS_CHANGED')
    for name, service in config['services'].items():
        flow.require(service['context'] == name and service['dockerfile'] == name + '/Dockerfile',
                     'OFFLINE_SOURCE_LAYOUT_UNSUPPORTED')
        actual = blob(service['dockerfile'])
        # Exact candidate bytes make the six-file handoff auditable before any app execution.
        flow.require(actual == flow.offline_dockerfile(config, name).encode(),
                     'HANDOFF_DOCKERFILE_MISMATCH')
        for profile in ('runtime', 'test'):
            path = name + '/requirements-' + profile + '.lock'
            digest = config['offline_supply']['profiles'][name + '-' + profile]['lock_sha256']
            flow.require(hashlib.sha256(blob(path)).hexdigest() == digest, 'SOURCE_LOCK_MISMATCH')
        tests = [service['contract']] + ([] if flow.unit_not_applicable(service['unit']) else [service['unit']])
        for step in tests:
            flow.require(any(p.startswith(step['cwd'] + '/') for p in entries), 'TEST_CWD_MISSING')
    return {'HANDOFF': 'PASS', 'source': config['source'], 'files': checks,
            'test_scope': {name: {'unit': item['unit'], 'contract': item['contract']}
                           for name, item in config['services'].items()},
            'SOURCE_APPROVAL': 'DECLARED_NOT_INDEPENDENTLY_VERIFIED',
            'APP_TEST_BUILD_SCAN': 'NOT_EXECUTED'}


def save_receipt(folder, result):
    path = folder / 'result.json'
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    (folder / 'SHA256SUMS').write_text(flow.sha256(path) + '  result.json\n')


def run(config, folder, context, incoming, source=None, execute=False):
    # Consume the dedicated credential. No raw inherited mapping reaches app execution.
    token = incoming.pop(auth.TOKEN_ENV, '')
    result = {'APP_WORKFLOW': 'FAIL', 'started_at': flow.utcnow().isoformat(),
              'context': context, 'steps': [], 'APP_TEST_BUILD_SCAN': 'NOT_EXECUTED',
              'PUSH': 'NOT_EXECUTED', 'DEPLOY': 'NOT_EXECUTED', 'DELIVERY_GATE': 'NOT_VERIFIED'}
    rc = 1
    try:
        flow.validate_config(config)
        flow.require(config['schema'] in ('P1-CICD-app-build-scan-2', 'P1-CICD-app-build-scan-3'),
                 'OFFLINE_SCHEMA_REQUIRED')
        if execute and config['source']['repository'] == auth.PRIVATE_REPOSITORY:
            auth.validate_token(token)
        command = command_runner(folder, incoming, result['steps'])
        if execute:
            source = folder / 'app.git'
            fetch_source(config, source, command, private_fetch=lambda target, sha: auth.fetch(
                target, sha, token, git_environment(folder / 'git-home', incoming),
                folder, result['steps']))
        token = None
        flow.require(source is not None, 'SOURCE_CHECKOUT_REQUIRED')
        handoff = validate_handoff(config, source, command)
        handoff_path = folder / 'handoff.json'
        handoff_path.write_text(json.dumps(handoff, ensure_ascii=False, indent=2) + '\n')
        result['handoff_sha256'] = flow.sha256(handoff_path)
        result['HANDOFF'] = 'PASS'
        if execute:
            build_dir = folder / 'build-scan'
            build_dir.mkdir(mode=0o700)
            build_context = {**context, 'handoff_sha256': result['handoff_sha256']}
            rc, built = flow.execute(config, source, build_dir, build_context,
                                     flow.scanner_environment(incoming))
            result['APP_TEST_BUILD_SCAN'] = built['BUILD_SCAN']
            result['build_result_sha256'] = flow.sha256(build_dir / 'result.json')
            result['APP_WORKFLOW'] = built['BUILD_SCAN']
            result['error_code'] = built.get('error_code', 'NONE')
        else:
            result['APP_WORKFLOW'], rc = 'HANDOFF_CHECK_PASS', 0
    except flow.PreflightError as exc:
        result['error_code'] = str(exc)
    except subprocess.TimeoutExpired:
        result['error_code'] = 'SOURCE_COMMAND_TIMEOUT'
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        result['error_code'] = 'WORKFLOW_OR_HANDOFF_ERROR'
    finally:
        token = None
        result['finished_at'] = flow.utcnow().isoformat()
        save_receipt(folder, result)
    return rc, result


def bind_context(raw, incoming):
    os.chdir(flow.ROOT)
    context = flow.runtime_context(incoming)
    expected = '321Team/infra-cicd/' + WORKFLOW + '@refs/heads/main'
    flow.require(incoming.get('GITHUB_WORKFLOW_REF') == expected, 'CENTRAL_WORKFLOW_REQUIRED')
    flow.require(incoming.get('GITHUB_WORKFLOW_SHA') == context['workflow_sha'], 'WORKFLOW_SHA_MISMATCH')
    committed = subprocess.check_output(GIT + ['show', context['workflow_sha'] + ':' + flow.CONFIG],
                                        timeout=15, stderr=subprocess.DEVNULL)
    flow.require(raw == committed, 'INPUT_NOT_FROM_WORKFLOW_COMMIT')
    context['input_sha256'] = hashlib.sha256(raw).hexdigest()
    return context


def runner_home():
    home = Path(pwd.getpwuid(os.getuid()).pw_dir)
    flow.offline.plain_path(home)
    info = home.stat()
    flow.require(info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o700,
                 'RUNNER_HOME_OWNERSHIP_OR_MODE')
    flow.require(shutil.disk_usage(home).free >= 4 * 1024**3, 'FOUR_GIB_FREE_REQUIRED')
    return home


def main(argv=None):
    incoming = dict(os.environ)
    os.environ.pop(auth.TOKEN_ENV, None)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--source-dir', type=Path, help='Local committed source for a read-only handoff check')
    parser.add_argument('--config', type=Path, help='Local check only; execution always uses the committed config')
    args = parser.parse_args(argv)
    os.umask(0o077)
    try:
        flow.require(not args.execute or (args.config is None and args.source_dir is None),
                     'EXECUTION_INPUT_OVERRIDE_FORBIDDEN')
        raw, config = read_config(args.config or (flow.ROOT / flow.CONFIG))
        signal.signal(signal.SIGTERM, flow.interrupted)
        signal.signal(signal.SIGINT, flow.interrupted)
        if args.execute:
            context = bind_context(raw, incoming)
            home = runner_home()
            # No truncate/chmod/chown of an existing control file or old run directory.
            fd = os.open(home / 'FDS_CICD_AppWorkflow.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, 'r+') as lock:
                info = os.fstat(lock.fileno())
                flow.require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and
                             stat.S_IMODE(info.st_mode) == 0o600 and info.st_nlink == 1, 'LOCK_FILE_INVALID')
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                folder = Path(tempfile.mkdtemp(prefix='FDS_CICD_AppWorkflow.', dir=home))
                print(f'EVIDENCE_DIR={folder}', flush=True)
                rc, result = run(config, folder, context, incoming, execute=True)
        else:
            flow.require(args.source_dir is not None, 'SOURCE_CHECKOUT_REQUIRED')
            folder = Path(tempfile.mkdtemp(prefix='FDS_CICD_HandoffCheck.'))
            print(f'EVIDENCE_DIR={folder}', flush=True)
            rc, result = run(config, folder, {'input_sha256': hashlib.sha256(raw).hexdigest()},
                             incoming, source=args.source_dir.absolute())
        print(f'APP_WORKFLOW={result["APP_WORKFLOW"]}\nERROR_CODE={result.get("error_code", "NONE")}')
        print('PUSH=NOT_EXECUTED\nDEPLOY=NOT_EXECUTED\nDELIVERY_GATE=NOT_VERIFIED')
        return rc
    except flow.PreflightError as exc:
        print(f'APP_WORKFLOW=FAIL\nERROR_CODE={exc}\nDELIVERY_GATE=NOT_VERIFIED')
        return 1
    except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError):
        print('APP_WORKFLOW=FAIL\nERROR_CODE=INPUT_OR_CONTEXT_ERROR\nDELIVERY_GATE=NOT_VERIFIED')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
