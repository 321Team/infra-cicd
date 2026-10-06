"""One native SA candidate, fresh CP1 reviews, Runner GET/SSAR; no rollout.

Endpoint stdout is a PRIVATE SSH protocol, never terminal output or evidence.
Tokens and raw TokenReview responses remain in bounded memory/pipe frames.
"""
import datetime as dt
import http.client
import json
import math
import os
import re
import select
import socket
import stat
import time

import deploy_binding as b
import deploy_cp1_access as a
import deploy_credential as c
import deploy_transport as t

TOKEN_PATH = '/api/v1/namespaces/ci-cd/serviceaccounts/cicd-runner-sa/token'
TOKEN_ARGS = ['create', '--raw=' + TOKEN_PATH, '-f', '-']
SA_ARGS = ['get', 'serviceaccount', 'cicd-runner-sa', '-n', 'ci-cd', '-o', 'json']
SSAR_PATH = '/apis/authorization.k8s.io/v1/selfsubjectaccessreviews'
SSAR_ARGS = ['create', '--raw=' + SSAR_PATH, '-f', '-']
CP1_PERMISSIONS = (
    {'verb': 'create', 'group': '', 'version': 'v1', 'resource': 'serviceaccounts',
     'subresource': 'token', 'name': 'cicd-runner-sa', 'namespace': 'ci-cd'},
    {'verb': 'create', 'group': 'authentication.k8s.io', 'version': 'v1', 'resource': 'tokenreviews'},
)
TTL = 900
MAX_FRAME = 1024 * 1024


class ProbeError(RuntimeError):
    """Static codes only."""


def need(ok, code):
    if not ok:
        raise ProbeError(code)


def encode(obj):
    return json.dumps(obj, ensure_ascii=True, sort_keys=True,
                      separators=(',', ':'), allow_nan=False).encode('ascii')


def review_attributes_match(obj, attrs):
    spec = obj.get('spec')
    if type(spec) is not dict or set(spec) != {'resourceAttributes'}:
        return False
    actual = spec['resourceAttributes']
    if type(actual) is not dict:
        return False
    # ResourceAttributes.group has omitempty; core API group "" is omitted.
    normalized = {'group': '', **actual}
    return normalized == attrs


def expiry(raw):
    need(type(raw) is str and re.fullmatch(
        r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?(?:Z|\+00:00)', raw),
        'UTC_EXPIRY_REQUIRED')
    value = dt.datetime.fromisoformat(raw.replace('Z', '+00:00'))
    need(value.utcoffset() == dt.timedelta(0), 'UTC_EXPIRY_REQUIRED')
    return value


class Window:
    def __init__(self, raw, *, now=lambda: dt.datetime.now(dt.timezone.utc),
                 clock=time.monotonic):
        self.now, self.clock, self.expires = now, clock, expiry(raw)
        left = (self.expires - now()).total_seconds()
        need(0 < left <= 3600, 'PROBE_AUTHORIZATION_WINDOW_INVALID')
        self.deadline, self.last = clock() + left, clock()

    def remaining(self):
        point = self.clock()
        need(point >= self.last, 'PROBE_CLOCK_REVERSED')
        self.last = point
        value = min(self.deadline - point, (self.expires - self.now()).total_seconds())
        need(value > 0, 'PROBE_AUTHORIZATION_EXPIRED')
        return value


class Channel:
    """Bounded LF-framed JSON over POSIX pipes. No secret-bearing repr/logging."""
    def __init__(self, reader, writer, *, clock=time.monotonic):
        self.reader, self.writer, self.clock = reader, writer, clock
        self.buffer = bytearray()
        os.set_blocking(reader, False)
        os.set_blocking(writer, False)

    def wait(self, write, deadline):
        remaining = deadline - self.clock()
        need(remaining > 0, 'CHANNEL_DEADLINE_EXCEEDED')
        ready = select.select([] if write else [self.reader],
                              [self.writer] if write else [], [], remaining)
        need(bool(ready[1] if write else ready[0]), 'CHANNEL_DEADLINE_EXCEEDED')

    def send_raw(self, raw, timeout=10):
        need(type(raw) is bytes and 0 < len(raw) <= MAX_FRAME and b'\n' not in raw,
             'CHANNEL_FRAME_INVALID')
        need(type(timeout) in (int, float) and math.isfinite(timeout) and 0 < timeout <= 180,
             'CHANNEL_TIMEOUT_INVALID')
        frame, offset, deadline = raw + b'\n', 0, self.clock() + timeout
        try:
            while offset < len(frame):
                self.wait(True, deadline)
                try:
                    count = os.write(self.writer, frame[offset:offset + 4096])
                except BlockingIOError:
                    continue
                need(count > 0, 'CHANNEL_CLOSED')
                offset += count
            need(self.clock() <= deadline, 'CHANNEL_DEADLINE_EXCEEDED')
        finally:
            frame = None

    def send(self, obj, timeout=10):
        self.send_raw(encode(obj), timeout)

    def recv(self, timeout=10):
        need(type(timeout) in (int, float) and math.isfinite(timeout) and 0 < timeout <= 180,
             'CHANNEL_TIMEOUT_INVALID')
        deadline = self.clock() + timeout
        try:
            while b'\n' not in self.buffer:
                need(len(self.buffer) <= MAX_FRAME, 'CHANNEL_FRAME_TOO_LARGE')
                self.wait(False, deadline)
                try:
                    part = os.read(self.reader, min(65536, MAX_FRAME + 1 - len(self.buffer)))
                except BlockingIOError:
                    continue
                need(bool(part), 'CHANNEL_CLOSED')
                self.buffer.extend(part)
            line, rest = self.buffer.split(b'\n', 1)
            self.buffer = bytearray(rest)
            need(0 < len(line) <= MAX_FRAME and self.clock() <= deadline,
                 'CHANNEL_FRAME_INVALID')
            return b.read(bytes(line))
        except Exception:
            self.buffer.clear()
            raise

    def clear(self):
        self.buffer.clear()  # Logical release, not physical zeroization.


class NativeAccess(a.CP1Access):
    """Isolated extension; the existing CP1Access allowlist is unchanged."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.attempted, self.issue_uid, self.token_command_seen = False, None, False

    def sa_uid(self, timeout=10):
        obj = b.read(self.request(SA_ARGS, timeout=timeout).stdout)
        meta = obj.get('metadata', {})
        need(obj.get('apiVersion') == 'v1' and obj.get('kind') == 'ServiceAccount'
             and meta.get('name') == 'cicd-runner-sa' and meta.get('namespace') == 'ci-cd'
             and type(meta.get('uid')) is str and c.UID.fullmatch(meta['uid'])
             and not meta.get('deletionTimestamp')
             and obj.get('automountServiceAccountToken') is False, 'SA_IDENTITY_INVALID')
        return meta['uid']

    def preflight(self):
        uid = self.sa_uid()
        for attrs in CP1_PERMISSIONS:
            body = {'apiVersion': 'authorization.k8s.io/v1', 'kind': 'SelfSubjectAccessReview',
                    'spec': {'resourceAttributes': attrs}}
            obj = b.read(self.request(SSAR_ARGS, payload=encode(body)).stdout)
            status = obj.get('status', {})
            need(obj.get('apiVersion') == body['apiVersion'] and obj.get('kind') == body['kind']
                 and review_attributes_match(obj, attrs) and status.get('allowed') is True
                 and status.get('denied', False) is False and not status.get('evaluationError'),
                 'CP1_ISSUANCE_PERMISSION_UNCONFIRMED')
        return uid

    def arguments_checked(self, args, payload, verbosity, allow_failure):
        if args == SSAR_ARGS:
            obj = b.read(payload)
            need(verbosity == 0 and allow_failure is False
                 and set(obj) == {'apiVersion', 'kind', 'spec'}
                 and obj['apiVersion'] == 'authorization.k8s.io/v1'
                 and obj['kind'] == 'SelfSubjectAccessReview'
                 and set(obj['spec']) == {'resourceAttributes'}
                 and obj['spec']['resourceAttributes'] in CP1_PERMISSIONS,
                 'CP1_SSAR_SCOPE_INVALID')
            return
        if args != TOKEN_ARGS:
            return super().arguments_checked(args, payload, verbosity, allow_failure)
        need(self.attempted and not self.token_command_seen
             and type(self.issue_uid) is str and c.UID.fullmatch(self.issue_uid)
             and verbosity == 0 and allow_failure is False, 'ISSUANCE_SCOPE_INVALID')
        need(b.read(payload) == {
            'apiVersion': 'authentication.k8s.io/v1', 'kind': 'TokenRequest',
            'metadata': {'name': 'cicd-runner-sa', 'namespace': 'ci-cd', 'uid': self.issue_uid},
            'spec': {'audiences': [b.AUDIENCE], 'expirationSeconds': TTL},
        }, 'ISSUANCE_BODY_INVALID')
        self.token_command_seen = True

    def issue(self, expected_uid, remaining_seconds):
        need(not self.attempted, 'ISSUANCE_ALREADY_ATTEMPTED')

        def remaining():
            value = remaining_seconds()
            need(type(value) in (int, float) and math.isfinite(value) and value > 0,
                 'PROBE_AUTHORIZATION_EXPIRED')
            return value

        deadline = self.clock() + min(10, remaining())
        need(self.sa_uid(timeout=min(self.budget(deadline), remaining())) == expected_uid,
             'SA_CHANGED_BEFORE_ISSUE')
        timeout = min(self.budget(deadline), remaining())
        self.issue_uid, self.attempted = expected_uid, True
        body = encode({
            'apiVersion': 'authentication.k8s.io/v1', 'kind': 'TokenRequest',
            'metadata': {'name': 'cicd-runner-sa', 'namespace': 'ci-cd', 'uid': expected_uid},
            'spec': {'audiences': [b.AUDIENCE], 'expirationSeconds': TTL},
        })
        obj = token = None
        try:
            obj = b.read(self.request(TOKEN_ARGS, payload=body, timeout=timeout).stdout)
            need(obj.get('apiVersion') == 'authentication.k8s.io/v1'
                 and obj.get('kind') == 'TokenRequest', 'TOKEN_REQUEST_OBJECT_INVALID')
            meta, spec, status = obj['metadata'], obj['spec'], obj['status']
            need(meta.get('name') == 'cicd-runner-sa' and meta.get('namespace') == 'ci-cd'
                 and meta.get('uid') == expected_uid and spec.get('audiences') == [b.AUDIENCE]
                 and not spec.get('boundObjectRef')
                 and type(spec.get('expirationSeconds')) is int
                 and 0 < spec['expirationSeconds'] <= TTL, 'TOKEN_REQUEST_BINDING_INVALID')
            token, status_time = status['token'], status['expirationTimestamp']
            claims = c.claims_checked(token, expected_uid)
            stamp = expiry(status_time)
            # Kubernetes records status time before constructing JWT NumericDates.
            # Their expiries need not equal; enforce the earlier authenticated bound.
            need(claims['exp'] - claims['iat'] <= spec['expirationSeconds']
                 and claims['iat'] < stamp.timestamp() <= claims['exp'],
                 'TOKEN_REQUEST_EXPIRY_INVALID')
            return {'token': token, 'sa_uid': expected_uid,
                    'status_expires_at': stamp.isoformat()}
        finally:
            obj = token = None

    def review(self, token, audience, timeout):
        need(self.attempted and audience == b.AUDIENCE, 'NATIVE_REVIEW_SCOPE_INVALID')
        deadline = self.clock() + a.timeout_checked(timeout)
        need(self.sa_uid(timeout=self.budget(deadline)) == self.issue_uid,
             'SA_CHANGED_BEFORE_REVIEW')
        return super().review(token, audience, self.budget(deadline))


def attributes(verb, resource, namespace='', name='', group='apps'):
    obj = {'verb': verb, 'resource': resource, 'group': group, 'version': 'v1'}
    if namespace:
        obj['namespace'] = namespace
    if name:
        obj['name'] = name
    return obj


RBAC_TESTS = tuple(
    (attributes(verb, 'deployments', 'fds-app', name), True)
    for name in a.NAMES for verb in ('get', 'update', 'patch')
) + (
    (attributes('create', 'deployments', 'fds-app'), False),
    (attributes('delete', 'deployments', 'fds-app', 'transaction-api'), False),
    (attributes('patch', 'deployments', 'fds-app', 'unapproved-probe'), False),
    (attributes('patch', 'deployments', 'default', 'transaction-api'), False),
    (attributes('get', 'secrets', 'fds-app', group=''), False),
    (attributes('list', 'pods', 'fds-app', group=''), False),
    (attributes('create', 'tokenreviews', group='authentication.k8s.io'), False),
)


class RunnerProbe:
    """No PATCH client: two named GETs and the fixed SSAR matrix only."""
    def __init__(self, ca_path, ca_hash, credential, status_expiry, window, *,
                 clock=time.monotonic, now=lambda: dt.datetime.now(dt.timezone.utc)):
        self.ca_path, self.ca_hash = ca_path, ca_hash
        self.ca_raw, self.context = a.ca_checked(ca_path, ca_hash)
        self.credential, self.window, self.clock, self.now = credential, window, clock, now
        self.status_expiry = expiry(status_expiry)
        self.status_deadline = clock() + (self.status_expiry - now()).total_seconds()
        self.blocked, self.started = False, False

    def remaining(self):
        value = min(self.credential.remaining(), self.window.remaining(),
                    self.status_deadline - self.clock(),
                    (self.status_expiry - self.now()).total_seconds())
        need(value > 0, 'RUNNER_CREDENTIAL_EXPIRED')
        return value

    def scope(self, method, path, body):
        names = ['/apis/apps/v1/namespaces/fds-app/deployments/' + n for n in a.NAMES]
        if method == 'GET':
            need(path in names and body is None, 'RUNNER_GET_SCOPE_INVALID')
        else:
            need(method == 'POST' and path == SSAR_PATH and type(body) is dict
                 and set(body) == {'apiVersion', 'kind', 'spec'}
                 and body['apiVersion'] == 'authorization.k8s.io/v1'
                 and body['kind'] == 'SelfSubjectAccessReview'
                 and set(body['spec']) == {'resourceAttributes'}
                 and body['spec']['resourceAttributes'] in [r for r, _ in RBAC_TESTS],
                 'RUNNER_POST_SCOPE_INVALID')

    def request(self, method, path, body=None):
        raw_sock = secured = response = None
        token = header = data = None
        try:
            need(not self.blocked, 'RUNNER_PROBE_BLOCKED')
            self.scope(method, path, body)
            need(a.read_checked(self.ca_path) == self.ca_raw, 'RUNNER_CA_CHANGED')
            deadline = self.clock() + min(10, self.remaining())

            def budget():
                value = min(deadline - self.clock(), self.remaining())
                need(value > 0, 'RUNNER_REQUEST_DEADLINE_EXCEEDED')
                return value

            token = self.credential.token()
            payload = b'' if body is None else encode(body)
            header = (method + ' ' + path + ' HTTP/1.1\r\nHost: ' + t.HOST + ':' + str(t.PORT)
                      + '\r\nAuthorization: Bearer ' + token
                      + '\r\nAccept: application/json\r\nAccept-Encoding: identity'
                      + '\r\nConnection: close\r\nContent-Type: application/json'
                      + '\r\nContent-Length: ' + str(len(payload)) + '\r\n\r\n').encode('ascii')
            raw_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            raw_sock.settimeout(budget())
            raw_sock.connect((t.HOST, t.PORT))
            raw_sock.settimeout(budget())
            secured = self.context.wrap_socket(
                raw_sock, server_hostname=t.HOST, do_handshake_on_connect=False)
            secured.settimeout(budget())
            secured.do_handshake()
            secured.settimeout(budget())
            secured.sendall(header + payload)
            budget()
            response = http.client.HTTPResponse(t.ResponseSocket(secured, budget), method=method)
            response.begin()
            budget()
            lengths = response.headers.get_all('Content-Length', [])
            transfers = response.headers.get_all('Transfer-Encoding', [])
            need(len(lengths) <= 1 and len(transfers) <= 1 and not (lengths and transfers)
                 and (not transfers or transfers[0].lower() == 'chunked')
                 and (not lengths or re.fullmatch(r'[0-9]+', lengths[0].strip()))
                 and response.getheader('Content-Encoding', 'identity').lower() == 'identity',
                 'RUNNER_HTTP_FRAMING_INVALID')
            data = response.read(t.MAX_RESPONSE + 1)
            budget()
            need(len(data) <= t.MAX_RESPONSE and
                 (response.length is None or response.length == 0), 'RUNNER_RESPONSE_INVALID')
            need(response.status in ((200,) if method == 'GET' else (200, 201)),
                 'RUNNER_HTTP_REJECTED')
            obj = b.read(data)
            need(a.read_checked(self.ca_path) == self.ca_raw, 'RUNNER_CA_CHANGED')
            budget()
            return obj
        except ProbeError:
            self.blocked = True
            raise
        except Exception:
            self.blocked = True
            raise ProbeError('RUNNER_REQUEST_UNCONFIRMED') from None
        finally:
            token = header = data = None
            for item in (response, secured, raw_sock):
                if item is not None:
                    try:
                        item.close()
                    except Exception:
                        self.blocked = True

    def run(self):
        need(not self.started, 'RUNNER_PROBE_ALREADY_STARTED')
        self.started = True
        try:
            return self.run_checked()
        except Exception:
            self.blocked = True
            raise

    def run_checked(self):
        permissions, deployments = [], []
        for attrs, expected in RBAC_TESTS:
            body = {'apiVersion': 'authorization.k8s.io/v1', 'kind': 'SelfSubjectAccessReview',
                    'spec': {'resourceAttributes': attrs}}
            obj = self.request('POST', SSAR_PATH, body)
            status = obj.get('status', {})
            need(obj.get('apiVersion') == body['apiVersion'] and obj.get('kind') == body['kind']
                 and review_attributes_match(obj, attrs) and not status.get('evaluationError')
                 and type(status.get('allowed')) is bool
                 and type(status.get('denied', False)) is bool
                 and not (status['allowed'] and status.get('denied', False))
                 and status['allowed'] is expected, 'RUNNER_RBAC_MISMATCH')
            permissions.append({'attributes': attrs, 'allowed': status['allowed'],
                                'expected': expected})
        for name in a.NAMES:
            obj = self.request('GET', '/apis/apps/v1/namespaces/fds-app/deployments/' + name)
            meta = obj.get('metadata', {})
            need(obj.get('apiVersion') == 'apps/v1' and obj.get('kind') == 'Deployment'
                 and meta.get('name') == name and meta.get('namespace') == 'fds-app'
                 and type(meta.get('uid')) is str and c.UID.fullmatch(meta['uid'])
                 and not meta.get('deletionTimestamp')
                 and type(meta.get('resourceVersion')) is str
                 and 0 < len(meta['resourceVersion']) <= 200
                 and all(ord(x) >= 32 for x in meta['resourceVersion']),
                 'RUNNER_DEPLOYMENT_IDENTITY_INVALID')
            deployments.append({'name': name, 'namespace': 'fds-app', 'uid': meta['uid'],
                                'resource_version': meta['resourceVersion']})
        need(not self.blocked and len({row['uid'] for row in deployments}) == 2,
             'RUNNER_PROBE_BLOCKED')
        return {'RUNNER_TOKEN_AUTH': 'PASS', 'EFFECTIVE_RBAC_CHECK': 'PASS',
                'basis': 'LIVE_PINNED_TLS_SA_GET_AND_SELF_SUBJECT_ACCESS_REVIEW',
                'permissions': permissions, 'deployments': deployments,
                'DEPLOY': 'NOT_EXECUTED', 'DELIVERY_GATE': 'NOT_VERIFIED'}


class PipeReviewer:
    def __init__(self, channel):
        self.channel, self.used = channel, False

    def review(self, token, audience, timeout):
        need(not self.used, 'PIPE_REVIEW_ALREADY_USED')
        self.used = True
        deadline = time.monotonic() + timeout
        self.channel.send({'stage': 'review', 'token': token,
                           'audience': audience, 'timeout': timeout}, timeout)
        remaining = deadline - time.monotonic()
        need(remaining > 0, 'PIPE_REVIEW_DEADLINE_EXCEEDED')
        result = self.channel.recv(remaining)
        need(set(result) == {'stage', 'response'} and result['stage'] == 'review-result',
             'PIPE_REVIEW_RESPONSE_INVALID')
        return result['response']


def private_channel_checked():
    need(not os.isatty(0) and not os.isatty(1), 'PRIVATE_PIPE_REQUIRED')
    for fd in (0, 1):
        mode = os.fstat(fd).st_mode
        need(stat.S_ISFIFO(mode) or stat.S_ISSOCK(mode), 'PRIVATE_PIPE_REQUIRED')
    need(bool(os.environ.get('SSH_CONNECTION')), 'SSH_SESSION_REQUIRED')


def endpoint(role, source_sha, workdir, ca_path, ca_hash, authorization_expires):
    """Called only after the externally pinned source bundle bootstrap."""
    a.host_checked(role)
    private_channel_checked()
    channel = Channel(0, 1)
    credential = candidate = message = review = token = backend = None
    issued = 'NOT_ATTEMPTED'
    try:
        a.host_checked(role)
        window = Window(authorization_expires) if authorization_expires else None
        _, _ = a.ca_checked(ca_path, ca_hash)
        backend = NativeAccess(ca_path, ca_hash) if role == 'cp1' else None
        uid = backend.preflight() if backend else None
        channel.send({'stage': 'ready', 'role': role, 'source_sha': source_sha,
                      'workdir': workdir, 'ca_sha256': ca_hash, 'sa_uid': uid})
        message = channel.recv(60)
        if message == {'action': 'stop'}:
            channel.send({'stage': 'done', 'token_issuance': 'NOT_ATTEMPTED',
                          'local_credential_cleanup': 'NO_CREDENTIAL'})
            return
        need(window is not None, 'EXPLICIT_ISSUANCE_WINDOW_REQUIRED')
        window.remaining()
        if role == 'cp1':
            need(message == {'action': 'issue-once'}, 'CP1_ACTION_INVALID')
            issued = 'ATTEMPTED_UNCONFIRMED'
            candidate = backend.issue(uid, window.remaining)
            credential = c.Credential(candidate['token'], uid, backend)
            issued = 'ISSUED_AND_CP1_REVIEWED'
            need(credential.remaining() > 30, 'CANDIDATE_WINDOW_TOO_SHORT')
            need(expiry(candidate['status_expires_at']) > dt.datetime.now(dt.timezone.utc),
                 'CANDIDATE_STATUS_EXPIRED')
            window.remaining()
            channel.send({'stage': 'candidate', **candidate})
            message = channel.recv(30)
            need(set(message) == {'stage', 'token', 'audience', 'timeout'}
                 and message['stage'] == 'review' and message['token'] == credential.token()
                 and message['audience'] == b.AUDIENCE, 'RELAY_REVIEW_BINDING_INVALID')
            remaining = min(credential.remaining(), window.remaining(),
                            (expiry(candidate['status_expires_at']) -
                             dt.datetime.now(dt.timezone.utc)).total_seconds())
            timeout = min(a.timeout_checked(message['timeout']), remaining)
            need(timeout > 0, 'RELAY_REVIEW_EXPIRED')
            review = backend.review(message['token'], b.AUDIENCE, timeout)
            c.review_checked(review, credential.token(), uid)
            channel.send({'stage': 'review-result', 'response': review},
                         min(10, window.remaining(), credential.remaining()))
            review = message = None
            need(channel.recv(min(180, window.remaining(), credential.remaining())) ==
                 {'action': 'finish'}, 'CP1_FINISH_INVALID')
            metadata = credential.observation()
            credential.close()
            metadata.update({'token_request_expires_at': candidate['status_expires_at'],
                             'local_credential_cleanup': 'LOCAL_REFERENCE_DROPPED',
                             'input_reference_cleanup': 'LOCAL_REFERENCE_DROPPED',
                             'server_revocation': 'NOT_PERFORMED'})
            candidate = token = None
            channel.send({'stage': 'done', 'token_issuance': issued, 'credential': metadata})
        else:
            need(set(message) == {'stage', 'token', 'sa_uid', 'status_expires_at'}
                 and message['stage'] == 'candidate', 'RUNNER_CANDIDATE_INVALID')
            token = message['token']
            claims = c.claims_checked(token, message['sa_uid'])
            stamp = expiry(message['status_expires_at'])
            need(claims['iat'] < stamp.timestamp() <= claims['exp'],
                 'RUNNER_STATUS_EXPIRY_INVALID')
            credential = c.Credential(token, message['sa_uid'], PipeReviewer(channel))
            probe = RunnerProbe(ca_path, ca_hash, credential, message['status_expires_at'], window)
            native_expiry = message['status_expires_at']
            message = token = None
            result = probe.run()
            metadata = credential.observation()
            credential.close()
            metadata['token_request_expires_at'] = native_expiry
            result['credential'] = metadata
            result['credential']['local_credential_cleanup'] = 'LOCAL_REFERENCE_DROPPED'
            result['input_reference_cleanup'] = 'LOCAL_REFERENCE_DROPPED'
            result['checked_at'] = dt.datetime.now(dt.timezone.utc).isoformat()
            channel.send({'stage': 'runner-result', 'result': result})
    except Exception as exc:
        safe_codes = {
            'CP1_ISSUANCE_PERMISSION_UNCONFIRMED', 'SA_IDENTITY_INVALID',
            'SA_CHANGED_BEFORE_ISSUE', 'SA_CHANGED_BEFORE_REVIEW', 'KUBECTL_FAILED',
            'TOKEN_REQUEST_EXPIRY_INVALID', 'CANDIDATE_WINDOW_TOO_SHORT',
            'CREDENTIAL_NOT_YET_VALID', 'CREDENTIAL_EXPIRED', 'JWT_LIFETIME_INVALID',
            'PROBE_AUTHORIZATION_EXPIRED', 'RUNNER_RBAC_MISMATCH',
            'RUNNER_HTTP_REJECTED', 'RUNNER_CA_CHANGED', 'RUNNER_CREDENTIAL_EXPIRED',
            'TOKEN_REVIEW_DENIED', 'TOKEN_REVIEW_FAILED',
        }
        code = str(exc) if type(exc) in (ProbeError, a.AccessError, c.CredentialError) else ''
        code = code if code in safe_codes else 'NATIVE_PROBE_UNCONFIRMED'
        if role == 'cp1' and backend is not None and not backend.attempted:
            issued = 'NOT_ATTEMPTED'
        try:
            channel.send({'stage': 'error', 'code': code, 'token_issuance': issued})
        except Exception:
            pass
    finally:
        if credential is not None:
            credential.close()
        candidate = message = review = token = None
        channel.clear()
