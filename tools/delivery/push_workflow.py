#!/usr/bin/env python3
"""Manual main-only dispatch entrypoint; one use per approved input hash."""
import base64
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import tempfile

import push_images as push

WORKFLOW = '321Team/infra-cicd/.github/workflows/app-push.yml@refs/heads/main'
SECRET_KEYS = ('HARBOR_PUSH_USERNAME', 'HARBOR_PUSH_TOKEN')


def private_directory(path):
    for node in (path, *path.parents):
        push.require(not node.is_symlink(), 'DIRECTORY_SYMLINK')
    info = path.stat()
    push.require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid() and
                 stat.S_IMODE(info.st_mode) == 0o700, 'PRIVATE_DIRECTORY_REQUIRED')


def load_inputs(home, incoming):
    name = incoming.get('PUSH_RECORD_NAME', '')
    digest = incoming.get('PUSH_RECORD_SHA256', '')
    push.require(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}\.json', name), 'RECORD_NAME')
    push.require(re.fullmatch(r'[0-9a-f]{64}', digest), 'RECORD_HASH')
    directory = home / 'FDS_CICD_PushInputs'
    private_directory(directory)
    record, actual = push.read_json(directory / name)
    push.require(actual == digest, 'REVIEWED_RECORD_HASH')
    root_name = record.get('source_evidence_directory', '')
    push.require(isinstance(root_name, str) and
                 re.fullmatch(r'FDS_CICD_AppWorkflow\.[A-Za-z0-9_-]+', root_name), 'EVIDENCE_DIRECTORY')
    root = home / root_name
    private_directory(root)
    return record, digest, root


def context(incoming):
    push.require(incoming.get('GITHUB_WORKFLOW_REF') == WORKFLOW, 'PUSH_WORKFLOW_REQUIRED')
    result = push.runtime_context(incoming)
    push.require(incoming.get('GITHUB_WORKFLOW_SHA') == result['workflow_sha'], 'WORKFLOW_SHA_MISMATCH')
    dirty = subprocess.check_output(['/usr/bin/git', 'status', '--porcelain', '--untracked-files=all'],
                                    timeout=15, stderr=subprocess.DEVNULL)
    push.require(not dirty.strip(), 'CLEAN_CHECKOUT_REQUIRED')
    return result


def write_auth(directory, username, token):
    push.require(isinstance(username, str) and username.startswith('robot$') and
                 6 < len(username) <= 200 and ':' not in username and
                 not any(ord(c) < 32 for c in username), 'ROBOT_USERNAME')
    push.require(isinstance(token, str) and 1 <= len(token) <= 4096 and
                 not any(ord(c) < 32 for c in token), 'ROBOT_TOKEN')
    path = directory / 'auth.json'
    with path.open('x') as stream:
        json.dump({'auths': {'10.1.93.54': {'auth': base64.b64encode(
            (username + ':' + token).encode()).decode()}}}, stream)
    path.chmod(0o600)
    return path


def claim(home, digest, execution):
    directory = home / 'FDS_CICD_PushClaims'
    directory.mkdir(mode=0o700, exist_ok=True)
    private_directory(directory)
    # Persist even after failure: a remote write might already have happened.
    path = directory / (digest + '.json')
    with path.open('x') as stream:
        json.dump({'record_sha256': digest, 'execution_context': execution,
                   'state': 'CONSUMED_RECONCILE_BEFORE_RETRY'}, stream)
        stream.flush()
        os.fsync(stream.fileno())
    return path


def run(incoming, home):
    username = incoming.pop(SECRET_KEYS[0], '')
    token = incoming.pop(SECRET_KEYS[1], '')
    mode = incoming.get('PUSH_EXECUTE', 'false')
    push.require(mode in ('true', 'false'), 'EXECUTE_INPUT')
    private_directory(home)
    execution = context(incoming)
    record, digest, root = load_inputs(home, incoming)
    if mode == 'true':
        push.authorize(record, execution)
    fd = os.open(home / 'FDS_CICD_Push.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'r+') as lock:
        info = os.fstat(lock.fileno())
        push.require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and
                     info.st_nlink == 1 and stat.S_IMODE(info.st_mode) == 0o600, 'LOCK_INVALID')
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        folder = Path(tempfile.mkdtemp(prefix='FDS_CICD_Push.', dir=home))
        print('EVIDENCE_DIR=' + str(folder), flush=True)
        metadata = {'record_sha256': digest, 'execution_context': execution,
                    'mode': mode, 'authorization_scope': 'EXACT_RECORD_AND_WORKFLOW_DISPATCH',
                    'approval_verification': 'DECLARED_INPUT_NOT_GITHUB_API_VERIFIED'}
        (folder / 'dispatch.json').write_text(json.dumps(metadata, indent=2) + '\n')
        # Validate all historical bytes without credentials/network before claiming.
        rc, result = push.execute(record, root, folder)
        if rc or mode == 'false':
            return rc
        with tempfile.TemporaryDirectory(prefix='FDS_CICD_PushAuth.', dir=home) as temp:
            authfile = write_auth(Path(temp), username, token)
            username = token = None
            push.require(Path(push.SKOPEO).is_file(), 'SKOPEO_REQUIRED')
            claim(home, digest, execution)
            # No ENV credentials are passed to scanner/Skopeo.
            rc, result = push.execute(record, root, folder, authfile, execution)
        # This line is reached only after credential cleanup.
        (folder / 'credential-cleanup.json').write_text('{"temporary_auth_cleanup":"PASS"}\n')
        (folder / 'dispatch-result.json').write_text(json.dumps({
            'DISPATCH': 'PASS' if rc == 0 else 'FAIL', 'PUSH': result['PUSH'],
            'record_sha256': digest, 'execution_context': execution,
            'credential_cleanup': 'PASS'}) + '\n')
        (folder / 'dispatch-SHA256SUMS').write_text(''.join(
            push.sha256(folder / name) + '  ' + name + '\n'
            for name in ('dispatch.json', 'result.json', 'credential-cleanup.json', 'dispatch-result.json')))
        print('PUSH=' + result['PUSH'])
        return rc


def main():
    incoming = dict(os.environ)
    for key in SECRET_KEYS:
        os.environ.pop(key, None)
    os.umask(0o077)
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    try:
        return run(incoming, Path.home())
    except (Exception, KeyboardInterrupt):
        print('PUSH=NOT_CONFIRMED\nERROR_CODE=DISPATCH_INPUT_CREDENTIAL_OR_SEAL_FAILURE')
        return 1
    finally:
        for key in SECRET_KEYS:
            incoming.pop(key, None)


if __name__ == '__main__':
    raise SystemExit(main())
