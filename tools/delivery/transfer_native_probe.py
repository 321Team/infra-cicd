"""Ansible coordinator for the native probe; stdout is sanitized receipt only.

Default preflight issues no token. --execute requires a bounded explicit window.
SSH uses existing host trust/accounts; no trust, role, Secret or key changes.
"""
import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import time

MAX = 1024 * 1024
FILES = {
    'deploy_native_probe.py', 'deploy_cp1_access.py', 'deploy_binding.py',
    'deploy_credential.py', 'deploy_transport.py', 'deploy_adapter.py', 'delivery_gate.py',
}
CA_SHA = '5e4462b48b67aa3c452119f02578feb5b2ace358782c9b050ba0ad2ea951a49c'
CP1_CA = '/home/server02/FDS_CICD_PublicCA.uua3ecs6/kubernetes-ca.pem'
RUNNER_CA = '/home/server01_cicd-runner/runner-tls-a9c89a77-20261006/kubernetes-ca.pem'

# The SAME loader is used locally and on both SSH endpoints.
BOOTSTRAP_LIBRARY = r'''
import hashlib, json, os, pwd, re, socket, stat, sys, tempfile
from pathlib import Path
FILES = {
    'deploy_native_probe.py', 'deploy_cp1_access.py', 'deploy_binding.py',
    'deploy_credential.py', 'deploy_transport.py', 'deploy_adapter.py', 'delivery_gate.py',
}
def require(ok):
    if not ok:
        raise ValueError('SOURCE_BOOTSTRAP_FAILED')
def pairs(items):
    value = {}
    for key, item in items:
        require(key not in value)
        value[key] = item
    return value
def constant(_):
    raise ValueError('SOURCE_BOOTSTRAP_FAILED')
def identity(role):
    expected = {
        'ansible': ('ansible01', 'server01_ansible'),
        'cp1': ('k8s-control01', 'server02'),
        'runner': ('cicd-runner01', 'server01_cicd-runner'),
    }
    require(role in expected and os.name == 'posix')
    require((socket.gethostname().split('.')[0], pwd.getpwuid(os.getuid()).pw_name)
            == expected[role])
    require(role != 'runner' or os.getuid() == 1000)
def safe(path, regular=True):
    path = Path(path)
    require(path.is_absolute())
    for node in (path, *path.parents):
        info = node.lstat()
        require((stat.S_ISREG(info.st_mode) if node == path and regular else
                 stat.S_ISDIR(info.st_mode))
                and info.st_uid in (0, os.getuid()) and not info.st_mode & 0o022
                and (node != path or not regular or info.st_nlink == 1))
    return path.lstat()
def read(path):
    before = safe(path)
    require(0 < before.st_size <= 1024 * 1024)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as stream:
        require((before.st_dev, before.st_ino) ==
                (os.fstat(stream.fileno()).st_dev, os.fstat(stream.fileno()).st_ino))
        raw = stream.read(1024 * 1024 + 1)
        final = os.fstat(stream.fileno())
    after = safe(path)
    signature = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
    require(signature(before) == signature(final) == signature(after))
    require(0 < len(raw) <= 1024 * 1024)
    return raw
def stage(raw, expected_hash, source_sha, role):
    identity(role)
    require(type(raw) is bytes and 0 < len(raw) <= 1024 * 1024
            and raw.endswith(b'\n') and raw.count(b'\n') == 1
            and re.fullmatch('[0-9a-f]{64}', expected_hash)
            and hashlib.sha256(raw).hexdigest() == expected_hash
            and re.fullmatch('[0-9a-f]{40}', source_sha))
    bundle = json.loads(raw.decode('utf-8'), object_pairs_hook=pairs, parse_constant=constant)
    require(type(bundle) is dict and set(bundle) == {'schema', 'source_sha', 'files'}
            and bundle['schema'] == 'P1-CICD-native-probe-source-1'
            and bundle['source_sha'] == source_sha and type(bundle['files']) is dict
            and set(bundle['files']) == FILES)
    prepared = {}
    for name, item in bundle['files'].items():
        require(type(item) is dict and set(item) == {'git_blob', 'content'}
                and type(item['content']) is str and type(item['git_blob']) is str
                and re.fullmatch('[0-9a-f]{40}', item['git_blob']))
        data = item['content'].encode('utf-8')
        require(hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()
                == item['git_blob'])
        compile(data, name, 'exec')
        prepared[name] = data
    home = Path.home()
    safe(home, regular=False)
    os.umask(0o077)
    root = Path(tempfile.mkdtemp(prefix='FDS_NativeProbeSource.', dir=home))
    for name, data in prepared.items():
        path = root / name
        with path.open('xb') as stream:
            stream.write(data)
        require(read(path) == data)
    return root
'''
REMOTE_MAIN = r'''
try:
    role, digest, source, ca, ca_hash, authorization = sys.argv[1:]
    identity(role)
    first = sys.stdin.buffer.readline(1024 * 1024 + 2)
    require(first.endswith(b'\n') and len(first) <= 1024 * 1024 + 1)
    root = stage(first, digest, source, role)
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(root))
    import deploy_native_probe
    deploy_native_probe.endpoint(role, source, str(root), ca, ca_hash, authorization or None)
except Exception:
    print('{"stage":"error","code":"SOURCE_BOOTSTRAP_FAILED"}', flush=True)
'''


class TransferError(RuntimeError):
    """No forwarding raw SSH/API errors."""


def need(ok, code):
    if not ok:
        raise TransferError(code)


def loader():
    namespace = {}
    exec(BOOTSTRAP_LIBRARY, namespace)
    return namespace


def snapshot(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


class Session:
    def __init__(self, role, bundle, digest, source, ca, authorization, channel_class,
                 *, key=None, checks=None):
        self.checks = checks or loader()
        ssh = shutil.which('ssh')
        need(ssh is not None and Path(ssh).is_absolute(), 'SSH_MISSING')
        self.checks['safe'](ssh)
        need(os.access(ssh, os.X_OK), 'SSH_NOT_EXECUTABLE')
        self.known = Path.home() / '.ssh/known_hosts'
        self.known_bytes = self.checks['read'](self.known)
        target = {'cp1': 'server02@10.1.93.102',
                  'runner': 'server01_cicd-runner@10.250.10.20'}[role]
        options = ['-F', '/dev/null', '-T',
                   '-o', 'StrictHostKeyChecking=yes',
                   '-o', 'UserKnownHostsFile=' + str(self.known),
                   '-o', 'UpdateHostKeys=no', '-o', 'VerifyHostKeyDNS=no',
                   '-o', 'ForwardAgent=no', '-o', 'ClearAllForwardings=yes',
                   '-o', 'ControlMaster=no', '-o', 'ControlPath=none',
                   '-o', 'ConnectionAttempts=1', '-o', 'ConnectTimeout=10',
                   '-o', 'LogLevel=ERROR']
        self.key, self.key_info = None, None
        if key:
            self.key = Path(key)
            info = self.checks['safe'](self.key)
            need(not info.st_mode & 0o077, 'PRIVATE_KEY_PERMISSIONS_INVALID')
            self.key_info = snapshot(info)
            options += ['-o', 'IdentitiesOnly=yes', '-i', str(self.key)]
        command = ' '.join(shlex.quote(value) for value in [
            '/usr/bin/python3', '-I', '-B', '-c', BOOTSTRAP_LIBRARY + REMOTE_MAIN,
            role, digest, source, ca, CA_SHA, authorization or '',
        ])
        env = {k: v for k, v in os.environ.items()
               if k not in ('SSH_ASKPASS', 'SSH_ASKPASS_REQUIRE', 'DISPLAY')}
        self.process = subprocess.Popen([ssh] + options + [target, command],
                                        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.DEVNULL, bufsize=0, env=env)
        try:
            self.channel = channel_class(self.process.stdout.fileno(), self.process.stdin.fileno())
            self.channel.send_raw(bundle[:-1], 30)
            self.ready = self.recv(60)
            expected_home = {'cp1': '/home/server02',
                             'runner': '/home/server01_cicd-runner'}[role]
            need(set(self.ready) == {'stage', 'role', 'source_sha', 'workdir', 'ca_sha256', 'sa_uid'}
                 and self.ready['stage'] == 'ready' and self.ready['role'] == role
                 and self.ready['source_sha'] == source and self.ready['ca_sha256'] == CA_SHA
                 and type(self.ready['workdir']) is str
                 and re.fullmatch(re.escape(expected_home) + r'/FDS_NativeProbeSource\.[A-Za-z0-9_]+',
                                  self.ready['workdir']), 'ENDPOINT_SOURCE_UNCONFIRMED')
            need((role == 'runner' and self.ready['sa_uid'] is None)
                 or (role == 'cp1' and type(self.ready['sa_uid']) is str
                     and re.fullmatch(r'[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}',
                                      self.ready['sa_uid'])), 'ENDPOINT_SA_UNCONFIRMED')
        except Exception:
            self.close()
            raise

    def drift_checked(self):
        need(self.checks['read'](self.known) == self.known_bytes, 'SSH_TRUST_CHANGED')
        if self.key:
            need(snapshot(self.checks['safe'](self.key)) == self.key_info, 'SSH_KEY_CHANGED')

    def recv(self, timeout=10):
        self.drift_checked()
        result = self.channel.recv(timeout)
        self.drift_checked()
        return result

    def send(self, obj, timeout=10):
        self.drift_checked()
        self.channel.send(obj, timeout)
        self.drift_checked()

    def finish(self):
        need(self.process.wait(timeout=3) == 0, 'SSH_ENDPOINT_EXIT_UNCONFIRMED')
        need(not self.channel.buffer and os.read(self.process.stdout.fileno(), 1) == b'',
             'SSH_ENDPOINT_EXTRA_OUTPUT')

    def close(self):
        if getattr(self, 'channel', None) is not None:
            self.channel.clear()
        for stream in (self.process.stdin, self.process.stdout):
            if stream:
                try:
                    stream.close()
                except Exception:
                    pass
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=3)


def safe_credential(obj):
    """Exact public metadata; never return arbitrary endpoint dictionaries."""
    keys = {'schema', 'subject', 'service_account_uid', 'issuer', 'audience',
            'issued_at', 'expires_at', 'checked_at', 'authentication_basis',
            'jwt_signature_check', 'runner_token_authentication', 'server_revocation',
            'DELIVERY_GATE', 'local_credential_cleanup', 'input_reference_cleanup',
            'token_request_expires_at'}
    need(type(obj) is dict and not set(obj) - keys, 'CREDENTIAL_RECEIPT_INVALID')
    fixed = {
        'schema': 'P1-CICD-credential-observation-1',
        'subject': 'system:serviceaccount:ci-cd:cicd-runner-sa',
        'issuer': 'https://kubernetes.default.svc.cluster.local',
        'audience': 'https://kubernetes.default.svc.cluster.local',
        'authentication_basis': 'CALLER_AUTHENTICATED_FRESH_TOKEN_REVIEW',
        'jwt_signature_check': 'NOT_PERFORMED_LOCALLY',
        'runner_token_authentication': 'NOT_VERIFIED',
        'server_revocation': 'NOT_PERFORMED', 'DELIVERY_GATE': 'NOT_VERIFIED',
        'local_credential_cleanup': 'LOCAL_REFERENCE_DROPPED',
    }
    need(all(obj.get(k) == value for k, value in fixed.items()), 'CREDENTIAL_RECEIPT_INVALID')
    need(re.fullmatch(r'[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}',
                      obj.get('service_account_uid', '')), 'CREDENTIAL_RECEIPT_INVALID')
    for key in ('issued_at', 'expires_at', 'checked_at', 'token_request_expires_at'):
        need(type(obj.get(key)) is str and re.fullmatch(
            r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?\+00:00', obj[key]),
            'CREDENTIAL_RECEIPT_INVALID')
    if 'input_reference_cleanup' in obj:
        need(obj['input_reference_cleanup'] == 'LOCAL_REFERENCE_DROPPED',
             'CREDENTIAL_RECEIPT_INVALID')
    issued = dt.datetime.fromisoformat(obj['issued_at'])
    native = dt.datetime.fromisoformat(obj['token_request_expires_at'])
    jwt = dt.datetime.fromisoformat(obj['expires_at'])
    need(issued < native <= jwt, 'CREDENTIAL_RECEIPT_EXPIRY_INVALID')
    return {key: obj[key] for key in keys if key in obj}


def public_result(message, module, expected_uid):
    need(type(message) is dict and set(message) == {'stage', 'result'}
         and message['stage'] == 'runner-result', 'RUNNER_RESULT_INVALID')
    obj = message['result']
    keys = {'RUNNER_TOKEN_AUTH', 'EFFECTIVE_RBAC_CHECK', 'basis', 'permissions', 'deployments',
            'DEPLOY', 'DELIVERY_GATE', 'credential', 'input_reference_cleanup', 'checked_at'}
    need(type(obj) is dict and set(obj) == keys
         and obj['RUNNER_TOKEN_AUTH'] == obj['EFFECTIVE_RBAC_CHECK'] == 'PASS'
         and obj['DEPLOY'] == 'NOT_EXECUTED' and obj['DELIVERY_GATE'] == 'NOT_VERIFIED'
         and obj['basis'] == 'LIVE_PINNED_TLS_SA_GET_AND_SELF_SUBJECT_ACCESS_REVIEW'
         and obj['input_reference_cleanup'] == 'LOCAL_REFERENCE_DROPPED',
         'RUNNER_RESULT_INVALID')
    expected_permissions = [
        {'attributes': attrs, 'allowed': allow, 'expected': allow}
        for attrs, allow in module.RBAC_TESTS
    ]
    need(obj['permissions'] == expected_permissions, 'RUNNER_PERMISSION_RECEIPT_INVALID')
    need(type(obj['deployments']) is list and len(obj['deployments']) == 2,
         'RUNNER_DEPLOYMENT_RECEIPT_INVALID')
    for row, name in zip(obj['deployments'], ('transaction-api', 'fds-engine')):
        need(type(row) is dict and set(row) == {'name', 'namespace', 'uid', 'resource_version'}
             and row['name'] == name and row['namespace'] == 'fds-app'
             and type(row['uid']) is str and module.c.UID.fullmatch(row['uid'])
             and type(row['resource_version']) is str
             and re.fullmatch(r'[A-Za-z0-9_.:-]{1,200}', row['resource_version']),
             'RUNNER_DEPLOYMENT_RECEIPT_INVALID')
    credential = safe_credential(obj['credential'])
    need(credential['service_account_uid'] == expected_uid, 'RUNNER_SA_RECEIPT_INVALID')
    module.expiry(obj['checked_at'])
    return {**obj, 'credential': credential}


def coordinate(module, bundle, digest, source, *, execute=False, authorization=None,
               cp1_ca=CP1_CA, runner_ca=RUNNER_CA, cp1_key=None, runner_key=None,
               session_factory=Session):
    cp1 = runner = candidate = request = response = None
    public_candidate = None
    attempted = False
    try:
        window = module.Window(authorization) if execute else None
        need(execute or authorization is None, 'PREFLIGHT_AUTHORIZATION_UNEXPECTED')
        cp1 = session_factory('cp1', bundle, digest, source, cp1_ca, authorization,
                              module.Channel, key=cp1_key)
        runner = session_factory('runner', bundle, digest, source, runner_ca, authorization,
                                 module.Channel, key=runner_key)
        if not execute:
            for session in (cp1, runner):
                session.send({'action': 'stop'})
                need(session.recv() == {'stage': 'done', 'token_issuance': 'NOT_ATTEMPTED',
                                        'local_credential_cleanup': 'NO_CREDENTIAL'},
                     'PREFLIGHT_DONE_UNCONFIRMED')
                session.finish()
            return {'PREFLIGHT': 'PASS', 'TOKEN_ISSUED': False,
                    'cp1': cp1.ready, 'runner': runner.ready,
                    'RUNNER_TOKEN_AUTH': 'NOT_VERIFIED', 'DEPLOY': 'NOT_EXECUTED',
                    'DELIVERY_GATE': 'NOT_VERIFIED'}
        window.remaining()
        attempted = True
        cp1.send({'action': 'issue-once'})
        candidate = cp1.recv(min(60, window.remaining()))
        need(set(candidate) == {'stage', 'token', 'sa_uid', 'status_expires_at'}
             and candidate['stage'] == 'candidate'
             and candidate['sa_uid'] == cp1.ready['sa_uid'], 'CP1_CANDIDATE_INVALID')
        claims = module.c.claims_checked(candidate['token'], candidate['sa_uid'])
        need(claims['iat'] < module.expiry(candidate['status_expires_at']).timestamp()
             <= claims['exp'], 'CP1_CANDIDATE_EXPIRY_INVALID')
        public_candidate = {'sa_uid': candidate['sa_uid'],
                            'status_expires_at': candidate['status_expires_at']}
        runner.send(candidate, min(10, window.remaining()))
        request = runner.recv(min(10, window.remaining()))
        need(set(request) == {'stage', 'token', 'audience', 'timeout'}
             and request['stage'] == 'review' and request['token'] == candidate['token']
             and request['audience'] == module.b.AUDIENCE, 'REVIEW_RELAY_INVALID')
        timeout = module.a.timeout_checked(request['timeout'])
        started = time.monotonic()
        cp1.send(request, min(timeout, window.remaining()))
        response = cp1.recv(min(timeout, window.remaining()))
        need(set(response) == {'stage', 'response'} and response['stage'] == 'review-result',
             'CP1_REVIEW_RESPONSE_INVALID')
        module.c.review_checked(response['response'], candidate['token'], candidate['sa_uid'])
        left = timeout - (time.monotonic() - started)
        need(left > 0, 'REVIEW_RELAY_TIMEOUT')
        runner.send(response, min(left, window.remaining()))
        candidate = request = response = None
        result = public_result(runner.recv(min(180, window.remaining())), module,
                               cp1.ready['sa_uid'])
        cp1.send({'action': 'finish'}, min(10, window.remaining()))
        done = cp1.recv(min(10, window.remaining()))
        need(set(done) == {'stage', 'token_issuance', 'credential'}
             and done['stage'] == 'done'
             and done['token_issuance'] == 'ISSUED_AND_CP1_REVIEWED', 'CP1_CLEANUP_UNCONFIRMED')
        cp1_credential = safe_credential(done['credential'])
        need(cp1_credential['service_account_uid'] == cp1.ready['sa_uid'],
             'CP1_CLEANUP_UID_INVALID')
        need(result['credential']['token_request_expires_at'] ==
             cp1_credential['token_request_expires_at'] == public_candidate['status_expires_at']
             and result['credential']['issued_at'] == cp1_credential['issued_at']
             and result['credential']['expires_at'] == cp1_credential['expires_at'],
             'CREDENTIAL_RECEIPTS_MISMATCH')
        runner.finish()
        cp1.finish()
        return {'PREFLIGHT': 'PASS', 'TOKEN_ISSUED': True, 'NATIVE_AUTH_PROBE': 'PASS',
                'cp1': cp1.ready, 'runner': runner.ready,
                'cp1_credential': cp1_credential, 'runner_observation': result,
                'ANSIBLE_INPUT_REFERENCE_CLEANUP': 'LOCAL_REFERENCE_DROPPED',
                'server_revocation': 'NOT_PERFORMED', 'DEPLOY': 'NOT_EXECUTED',
                'DELIVERY_GATE': 'NOT_VERIFIED'}
    except Exception:
        return {'NATIVE_AUTH_PROBE': 'NOT_CONFIRMED',
                'error_code': 'NATIVE_AUTH_PROBE_UNCONFIRMED',
                'TOKEN_ISSUANCE': 'ISSUE_COMMAND_SENT_UNCONFIRMED' if attempted else 'NOT_ATTEMPTED',
                'ENDPOINT_CLEANUP': 'NOT_CONFIRMED',
                'ANSIBLE_INPUT_REFERENCE_CLEANUP': 'LOCAL_REFERENCE_DROPPED',
                'server_revocation': 'NOT_PERFORMED', 'DEPLOY': 'NOT_EXECUTED',
                'DELIVERY_GATE': 'NOT_VERIFIED'}
    finally:
        candidate = request = response = None
        for session in (runner, cp1):
            if session is not None:
                try:
                    session.close()
                except Exception:
                    pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', required=True)
    parser.add_argument('--bundle-sha256', required=True)
    parser.add_argument('--source-sha', required=True)
    parser.add_argument('--cp1-key')
    parser.add_argument('--runner-key')
    parser.add_argument('--cp1-ca', default=CP1_CA)
    parser.add_argument('--runner-ca', default=RUNNER_CA)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--authorization-expires')
    args = parser.parse_args()
    try:
        need(not args.execute or args.authorization_expires is not None,
             'EXPLICIT_ISSUANCE_WINDOW_REQUIRED')
        checks = loader()
        checks['identity']('ansible')
        raw = checks['read'](Path(args.bundle))
        need(raw.endswith(b'\n') and raw.count(b'\n') == 1,
             'CANONICAL_SINGLE_LINE_BUNDLE_REQUIRED')
        root = checks['stage'](raw, args.bundle_sha256, args.source_sha, 'ansible')
        sys.dont_write_bytecode = True
        sys.path.insert(0, str(root))
        import deploy_native_probe as module
        result = coordinate(module, raw, args.bundle_sha256, args.source_sha,
                            execute=args.execute, authorization=args.authorization_expires,
                            cp1_ca=args.cp1_ca, runner_ca=args.runner_ca,
                            cp1_key=args.cp1_key, runner_key=args.runner_key)
        result.update({'source_sha': args.source_sha, 'bundle_sha256': args.bundle_sha256,
                       'local_source_workdir': str(root),
                       'SOURCE_APPROVAL': 'OPERATOR_PINNED_NOT_VERIFIED_IN_TOOL',
                       'checked_at': dt.datetime.now(dt.timezone.utc).isoformat()})
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
        return 0 if result.get('PREFLIGHT') == 'PASS' else 64
    except Exception:
        print('STOP: NATIVE_PROBE_PREPARATION_FAILED', file=sys.stderr)
        return 64


if __name__ == '__main__':
    raise SystemExit(main())
