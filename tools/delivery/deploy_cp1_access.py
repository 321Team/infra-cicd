"""CP1-only existing-auth access: TokenReview, named reads, failing dry-run test.

No issuance, kubeconfig export, RBAC changes, image patch or token logging.
Subprocess output is held in memory; only allowlisted observations may be emitted.
"""
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import socket
import stat
import subprocess
import time

import deploy_binding as b
import deploy_credential as c
import deploy_transport as t

MAX_BYTES = 1024 * 1024
NAMES = ('transaction-api', 'fds-engine')
ZERO_UID = '00000000-0000-0000-0000-000000000000'


class AccessError(RuntimeError):
    """Static codes; do not forward command output or exception messages."""


def need(ok, code):
    if not ok:
        raise AccessError(code)


def host_checked(mode):
    need(os.name == 'posix', 'POSIX_HOST_REQUIRED')
    import pwd
    user = pwd.getpwuid(os.getuid()).pw_name
    host = socket.gethostname().split('.')[0]
    if mode == 'cp1':
        need(host == 'k8s-control01' and user == 'server02', 'CP1_IDENTITY_MISMATCH')
    elif mode == 'runner':
        need(host == 'cicd-runner01' and user == 'server01_cicd-runner'
             and os.getuid() == 1000, 'RUNNER_IDENTITY_MISMATCH')
    else:
        raise AccessError('HOST_MODE_INVALID')


def path_checked(path, *, regular=True):
    path = Path(path)
    need(path.is_absolute(), 'ABSOLUTE_PATH_REQUIRED')
    for node in path.parents:
        info = node.lstat()
        need(stat.S_ISDIR(info.st_mode) and info.st_uid in (0, os.getuid())
             and not info.st_mode & 0o022, 'UNSAFE_PARENT')
    info = path.lstat()
    need((stat.S_ISREG(info.st_mode) if regular else stat.S_ISDIR(info.st_mode))
         and info.st_uid in (0, os.getuid()) and not info.st_mode & 0o022
         and (not regular or info.st_nlink == 1), 'UNSAFE_FILE')
    return info


def read_checked(path):
    try:
        before = path_checked(path)
        need(0 < before.st_size <= MAX_BYTES, 'FILE_SIZE_INVALID')
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, 'rb') as stream:
            opened = os.fstat(stream.fileno())
            need((before.st_dev, before.st_ino) == (opened.st_dev, opened.st_ino),
                 'FILE_CHANGED')
            raw = stream.read(MAX_BYTES + 1)
            final = os.fstat(stream.fileno())
        after = path_checked(path)
        snapshot = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
        need(snapshot(before) == snapshot(final) == snapshot(after), 'FILE_CHANGED')
        need(0 < len(raw) <= MAX_BYTES, 'FILE_SIZE_INVALID')
        return raw
    except AccessError:
        raise
    except Exception:
        raise AccessError('FILE_READ_FAILED') from None


def ca_checked(path, expected_hash):
    raw = read_checked(path)
    try:
        context = t.context_checked(raw, expected_hash)
    except Exception:
        raise AccessError('PINNED_CA_INVALID') from None
    return raw, context


def timeout_checked(value):
    need(type(value) in (int, float) and math.isfinite(value) and 0 < value <= 10,
         'TIMEOUT_INVALID')
    return float(value)


def status_projection(stderr):
    """Read client-go's body log only; never persist verbose stderr or headers."""
    need(type(stderr) is bytes and len(stderr) <= MAX_BYTES, 'PROBE_LOG_INVALID')
    bodies = []
    try:
        lines = stderr.decode('utf-8').splitlines()
        for line in lines:
            if '"Response Body"' in line and ' body=' in line:
                fragment = line.split(' body=', 1)[1]
                body, end = json.JSONDecoder().raw_decode(fragment)
                need(type(body) is str and not fragment[end:].strip(), 'PROBE_BODY_INVALID')
                bodies.append(body.encode('utf-8'))
            elif 'Response Body: ' in line:
                bodies.append(line.split('Response Body: ', 1)[1].encode('utf-8'))
        candidates = []
        for raw in bodies:
            need(b'[truncated ' not in raw, 'PROBE_BODY_TRUNCATED')
            obj = b.read(raw)
            if obj.get('kind') == 'Status' and obj.get('status') == 'Failure':
                need(obj.get('apiVersion') == 'v1' and type(obj.get('code')) is int
                     and obj['code'] in (409, 422)
                     and obj.get('reason') in ('Conflict', 'Invalid'), 'PROBE_STATUS_INVALID')
                candidates.append((raw, obj))
        need(len(candidates) == 1, 'PROBE_STATUS_UNCONFIRMED')
        raw, obj = candidates[0]
        result = {key: obj[key] for key in ('apiVersion', 'kind', 'status', 'code', 'reason')}
        if 'details' in obj:
            details = obj['details']
            need(type(details) is dict, 'PROBE_DETAILS_INVALID')
            allowed = {'name': NAMES, 'namespace': ('fds-app',),
                       'group': ('apps',), 'kind': ('deployments',)}
            result['details'] = {
                key: details[key] if details[key] in choices else 'MISMATCH'
                for key, choices in allowed.items() if key in details
            }
        return {'status_projection': result, 'status_body_sha256': b.sha(raw),
                'payload': 'ALLOWLISTED_PROJECTION_ONLY', 'transport_policy': 'UNCHANGED'}
    except AccessError:
        raise
    except Exception:
        raise AccessError('PROBE_BODY_INVALID') from None


class CP1Access:
    def __init__(self, ca_path, expected_ca_sha256, *, clock=time.monotonic):
        host_checked('cp1')
        self.clock, self.blocked = clock, False
        self.ca_path, self.ca_hash = str(Path(ca_path)), expected_ca_sha256
        self.ca_raw, _ = ca_checked(self.ca_path, self.ca_hash)
        self.binary = shutil.which('kubectl')
        need(self.binary is not None and Path(self.binary).is_absolute(), 'KUBECTL_MISSING')
        self.binary_info = path_checked(self.binary)
        need(os.access(self.binary, os.X_OK), 'KUBECTL_NOT_EXECUTABLE')
        configured = os.environ.get('KUBECONFIG')
        need(configured is None or (configured and ':' not in configured),
             'SINGLE_KUBECONFIG_REQUIRED')
        self.config_path = str(Path(configured) if configured else Path.home() / '.kube/config')
        self.config_hash = b.sha(read_checked(self.config_path))
        self.env = {key: value for key, value in os.environ.items()
                    if key.lower() not in ('http_proxy', 'https_proxy', 'all_proxy')}
        self.env['KUBECTL_KUBERC'] = 'false'
        self.context, self.auth_files = None, []
        deadline = self.clock() + 10
        self.inspect_config(deadline)

    def budget(self, deadline):
        value = deadline - self.clock()
        need(math.isfinite(value) and value > 0, 'ACCESS_DEADLINE_EXCEEDED')
        return min(value, 10)

    def files_checked(self):
        current = path_checked(self.binary)
        need((current.st_dev, current.st_ino, current.st_mtime_ns, current.st_ctime_ns) ==
             (self.binary_info.st_dev, self.binary_info.st_ino,
              self.binary_info.st_mtime_ns, self.binary_info.st_ctime_ns),
             'KUBECTL_CHANGED')
        need(b.sha(read_checked(self.config_path)) == self.config_hash, 'KUBECONFIG_CHANGED')
        need(read_checked(self.ca_path) == self.ca_raw, 'CA_CHANGED')
        for path, digest in self.auth_files:
            need(b.sha(read_checked(path)) == digest, 'CLIENT_AUTH_FILE_CHANGED')

    def arguments_checked(self, args, payload, verbosity, allow_failure):
        config = ['config', 'view', '--minify', '-o', 'json']
        sa = ['get', 'serviceaccount', 'cicd-runner-sa', '-n', 'ci-cd', '-o', 'json']
        review = ['create', '--raw=/apis/authentication.k8s.io/v1/tokenreviews', '-f', '-']
        reads = [['get', 'deployment', name, '-n', 'fds-app', '-o', 'json'] for name in NAMES]
        patch = json.dumps([{'op': 'test', 'path': '/metadata/uid', 'value': ZERO_UID}],
                           separators=(',', ':'))
        probes = [['patch', 'deployment', name, '-n', 'fds-app', '--type=json',
                   '--dry-run=server', '-p', patch, '-o', 'json'] for name in NAMES]
        if args == config or args == sa or args in reads:
            need(payload is None and verbosity == 0 and not allow_failure,
                 'COMMAND_SCOPE_INVALID')
        elif args == review:
            obj = b.read(payload)
            need(set(obj) == {'apiVersion', 'kind', 'spec'}
                 and obj['apiVersion'] == 'authentication.k8s.io/v1'
                 and obj['kind'] == 'TokenReview' and type(obj['spec']) is dict
                 and set(obj['spec']) == {'token', 'audiences'}
                 and obj['spec']['audiences'] == [b.AUDIENCE]
                 and type(obj['spec']['token']) is str
                 and 0 < len(obj['spec']['token']) <= 16384
                 and re.fullmatch(r'[A-Za-z0-9_.-]+', obj['spec']['token'])
                 and verbosity == 0 and not allow_failure, 'COMMAND_SCOPE_INVALID')
        else:
            need(args in probes and payload is None and verbosity == 8 and allow_failure,
                 'COMMAND_SCOPE_INVALID')

    def command(self, args, deadline, *, payload=None, verbosity=0, allow_failure=False):
        need(not self.blocked, 'ACCESS_BLOCKED')
        try:
            self.arguments_checked(args, payload, verbosity, allow_failure)
            self.files_checked()
            command = [self.binary, '--kubeconfig=' + self.config_path, '--v=' + str(verbosity)]
            if self.context is not None:
                command += ['--context=' + self.context, '--server=' + t.API_SERVER,
                            '--certificate-authority=' + self.ca_path,
                            '--tls-server-name=' + t.HOST,
                            '--request-timeout=' + str(self.budget(deadline)) + 's']
            result = subprocess.run(command + args, input=payload, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, timeout=self.budget(deadline),
                                    env=self.env, check=False)
            self.budget(deadline)
            self.files_checked()
            need(type(result.stdout) is bytes and type(result.stderr) is bytes and
                 len(result.stdout) <= MAX_BYTES and len(result.stderr) <= MAX_BYTES,
                 'COMMAND_OUTPUT_INVALID')
            need(allow_failure or result.returncode == 0, 'KUBECTL_FAILED')
            return result
        except Exception as exc:
            self.blocked = True
            if isinstance(exc, AccessError):
                raise
            raise AccessError('KUBECTL_FAILED') from None

    def inspect_config(self, deadline):
        result = self.command(['config', 'view', '--minify', '-o', 'json'], deadline)
        try:
            cfg = b.read(result.stdout)
            need(len(cfg.get('clusters', [])) == len(cfg.get('users', [])) ==
                 len(cfg.get('contexts', [])) == 1, 'CONFIG_SELECTION_INVALID')
            cluster_item, user_item, context_item = (
                cfg['clusters'][0], cfg['users'][0], cfg['contexts'][0])
            cluster, user, context = (
                cluster_item['cluster'], user_item['user'], context_item['context'])
            name = context_item['name']
            need(type(name) is str and 0 < len(name) <= 200 and
                 all(ord(x) >= 32 for x in name) and cfg.get('current-context') == name
                 and context.get('cluster') == cluster_item['name']
                 and context.get('user') == user_item['name'], 'CONFIG_SELECTION_INVALID')
            need(cluster.get('server') == t.API_SERVER
                 and not cluster.get('insecure-skip-tls-verify', False)
                 and cluster.get('tls-server-name', t.HOST) in ('', t.HOST)
                 and not cluster.get('proxy-url'), 'CONFIG_TLS_INVALID')
            need(not any(key in user for key in (
                'exec', 'auth-provider', 'token', 'tokenFile', 'username', 'password',
                'as', 'as-uid', 'as-groups', 'as-user-extra')), 'CLIENT_AUTH_UNSUPPORTED')
            files = []
            for prefix in ('client-certificate', 'client-key'):
                embedded, filename = user.get(prefix + '-data'), user.get(prefix)
                need(bool(embedded) != bool(filename), 'CLIENT_CERT_AUTH_REQUIRED')
                if embedded:
                    need(type(embedded) is str, 'CLIENT_CERT_AUTH_REQUIRED')
                if filename:
                    need(type(filename) is str, 'CLIENT_AUTH_PATH_INVALID')
                    files.append((filename, b.sha(read_checked(filename))))
            if self.context is not None:
                need(name == self.context and files == self.auth_files, 'CONFIG_CHANGED')
            self.context, self.auth_files = name, files
        except Exception as exc:
            self.blocked = True
            if isinstance(exc, AccessError):
                raise
            raise AccessError('CONFIG_INVALID') from None

    def request(self, args, *, payload=None, timeout=10, verbosity=0, allow_failure=False):
        try:
            deadline = self.clock() + timeout_checked(timeout)
            need(not self.blocked, 'ACCESS_BLOCKED')
            self.inspect_config(deadline)
            return self.command(args, deadline, payload=payload, verbosity=verbosity,
                                allow_failure=allow_failure)
        except Exception:
            self.blocked = True
            raise

    def service_account_uid(self):
        try:
            result = self.request(['get', 'serviceaccount', 'cicd-runner-sa', '-n', 'ci-cd',
                                   '-o', 'json'])
            obj = b.read(result.stdout)
            meta = obj.get('metadata', {})
            need(obj.get('apiVersion') == 'v1' and obj.get('kind') == 'ServiceAccount'
                 and meta.get('name') == 'cicd-runner-sa' and meta.get('namespace') == 'ci-cd'
                 and type(meta.get('uid')) is str and c.UID.fullmatch(meta['uid'])
                 and not meta.get('deletionTimestamp')
                 and obj.get('automountServiceAccountToken') is False, 'SA_IDENTITY_INVALID')
            return meta['uid']
        except Exception:
            self.blocked = True
            raise AccessError('SA_READ_FAILED') from None

    def review(self, token, audience, timeout):
        try:
            need(type(token) is str and 0 < len(token) <= 16384 and
                 re.fullmatch(r'[A-Za-z0-9_.-]+', token) and audience == b.AUDIENCE,
                 'REVIEW_INPUT_INVALID')
            body = json.dumps({'apiVersion': 'authentication.k8s.io/v1', 'kind': 'TokenReview',
                               'spec': {'token': token, 'audiences': [audience]}},
                              separators=(',', ':')).encode()
            result = self.request(['create', '--raw=/apis/authentication.k8s.io/v1/tokenreviews',
                                   '-f', '-'], payload=body, timeout=timeout)
            return b.read(result.stdout)
        except Exception:
            self.blocked = True
            raise AccessError('TOKEN_REVIEW_FAILED') from None

    def deployment(self, name):
        need(name in NAMES, 'DEPLOYMENT_SCOPE_INVALID')
        result = self.request(['get', 'deployment', name, '-n', 'fds-app', '-o', 'json'])
        try:
            obj = b.read(result.stdout)
            meta = obj['metadata']
            need(obj.get('apiVersion') == 'apps/v1' and obj.get('kind') == 'Deployment'
                 and meta.get('name') == name and meta.get('namespace') == 'fds-app'
                 and type(meta.get('uid')) is str and c.UID.fullmatch(meta['uid'])
                 and meta['uid'] != ZERO_UID and not meta.get('deletionTimestamp')
                 and type(meta.get('resourceVersion')) is str
                 and type(obj.get('spec')) is dict, 'DEPLOYMENT_IDENTITY_INVALID')
            return obj
        except Exception:
            self.blocked = True
            raise AccessError('DEPLOYMENT_READ_FAILED') from None

    def negative_test(self, name):
        """Fixed failed UID test only; never add/replace/remove any field."""
        try:
            before = self.deployment(name)
            patch = json.dumps([{'op': 'test', 'path': '/metadata/uid', 'value': ZERO_UID}],
                               separators=(',', ':'))
            result = self.request(['patch', 'deployment', name, '-n', 'fds-app', '--type=json',
                                   '--dry-run=server', '-p', patch, '-o', 'json'],
                                  verbosity=8, allow_failure=True)
            need(result.returncode != 0, 'NEGATIVE_TEST_UNEXPECTED_SUCCESS')
            projection = status_projection(result.stderr)
            after = self.deployment(name)
            spec_hash = lambda obj: b.sha(json.dumps(
                obj['spec'], sort_keys=True, separators=(',', ':'), allow_nan=False).encode())
            need(before['metadata']['uid'] == after['metadata']['uid']
                 and spec_hash(before) == spec_hash(after), 'DEPLOYMENT_CHANGED_DURING_PROBE')
            projection.update({
                'name': name, 'namespace': 'fds-app', 'uid': before['metadata']['uid'],
                'spec_sha256': spec_hash(before),
                'resource_version_before': before['metadata']['resourceVersion'],
                'resource_version_after': after['metadata']['resourceVersion'],
                'dry_run': 'server', 'patch_operation': 'FAILED_UID_TEST_ONLY',
                'spec_observation': 'UNCHANGED_AT_TWO_READS',
                'DEPLOY': 'NOT_EXECUTED', 'DELIVERY_GATE': 'NOT_VERIFIED'})
            return projection
        except Exception:
            self.blocked = True
            raise AccessError('NEGATIVE_TEST_UNCONFIRMED') from None
