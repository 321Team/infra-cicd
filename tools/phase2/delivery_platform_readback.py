"""Opt-in bounded API identity/target/required-capability readback.

No credential issuance, grant, workload write or platform authority defaults.
SelfSubjectReview/AccessReview POSTs are queries, not permission changes.
Returned observations DO NOT prove issuer/audience/expiry or least privilege
and cannot be supplied as PlatformEvidence to the operational verifier.
"""
from dataclasses import dataclass, field
import time

import delivery_adapter as core
import delivery_transport as wire
from delivery_object_reader import BearerCredential, safe_code
from delivery_verifier import (Progress, ReadRequest, PlatformEvidence,
                               provenance_checked)

NAMESPACE_PATH = '/api/v1/namespaces/fds-app'
VERSION_PATH = '/apis/config.openshift.io/v1/clusterversions/version'
SUBJECT_PATH = '/apis/authentication.k8s.io/v1/selfsubjectreviews'
ACCESS_PATH = '/apis/authorization.k8s.io/v1/selfsubjectaccessreviews'
SUBJECT_BODY = core.encoded({'apiVersion': 'authentication.k8s.io/v1', 'kind': 'SelfSubjectReview'})


class PlatformReadbackError(RuntimeError):
    """Fixed, token/server-body/OS-error-free public codes."""


def need(ok, code):
    if not ok:
        raise PlatformReadbackError(code)


def plain(value):
    return (type(value) is str and 0 < len(value) <= 2048 and value.strip() == value
            and not any(ord(c) < 32 for c in value)
            and value.upper() not in ('PENDING', 'UNKNOWN', 'NOT_VERIFIED'))


@dataclass(frozen=True)
class ExpectedSubject:
    """Code-configured expectation; no embedded/derived production identity."""
    username: str
    uid: str
    groups: tuple


@dataclass(frozen=True)
class PlatformReadback:
    request: ReadRequest = field(repr=False)
    subject: ExpectedSubject = field(repr=False)
    credential: object = field(repr=False)
    credential_fingerprint: str
    endpoint_ip: str
    required_capabilities: tuple = field(repr=False)
    raw_responses: tuple = field(repr=False)
    execution_authorized: bool = field(default=False, init=False)
    full_platform_authenticated: bool = field(default=False, init=False)
    least_privilege_verified: bool = field(default=False, init=False)


def capabilities(binding=None):
    result = []
    for version, kind, name in (core.OBJECTS if binding is None else core.objects_for(binding)):
        group, api_version = version.split('/') if '/' in version else ('', version)
        resource = {'Service': 'services', 'Deployment': 'deployments', 'Route': 'routes'}[kind]
        for verb in ('get', 'patch'):
            result.append((group, api_version, resource, name, core.NAMESPACE, verb))
    return tuple(result)


def access_body(capability):
    attrs = dict(zip(('group', 'version', 'resource', 'name', 'namespace', 'verb'), capability))
    return core.encoded({'apiVersion': 'authorization.k8s.io/v1', 'kind': 'SelfSubjectAccessReview',
                         'spec': {'resourceAttributes': attrs}})


CAPABILITIES = capabilities()
ACCESS_BODIES = frozenset(access_body(c) for c in CAPABILITIES)


class PlatformObservationReader(wire.Sender):
    def __init__(self, binding, *, credential, expected_subject, progress,
                 ca_pem, endpoint_ip, monotonic=time.monotonic):
        try:
            need(type(credential) is BearerCredential and callable(progress) and callable(monotonic)
                 and type(expected_subject) is ExpectedSubject, 'PLATFORM_READBACK_CONFIGURATION_INVALID')
            need(plain(expected_subject.username) and plain(expected_subject.uid)
                 and expected_subject.username != 'system:anonymous'
                 and type(expected_subject.groups) is tuple and len(expected_subject.groups) <= 128
                 and all(plain(x) for x in expected_subject.groups)
                 and 'system:unauthenticated' not in expected_subject.groups
                 and tuple(sorted(set(expected_subject.groups))) == expected_subject.groups,
                 'PLATFORM_READBACK_SUBJECT_REQUIRED')
            super().__init__(binding, ca_pem=ca_pem, token=credential.token, endpoint_ip=endpoint_ip)
            self.credential, self.subject = credential, expected_subject
            self.ca_pem = ca_pem
            self.progress, self.monotonic, self._last = progress, monotonic, None
            self.closed = False
            self.capabilities = capabilities(binding)
            self.access_bodies = frozenset(access_body(c) for c in self.capabilities)
        except PlatformReadbackError:
            raise
        except Exception:
            raise PlatformReadbackError('PLATFORM_READBACK_CONFIGURATION_INVALID') from None

    def __repr__(self):
        return '<P2 PlatformObservationReader: credential and subject redacted>'

    def now(self):
        now = self.monotonic()
        need(core.finite(now) and (self._last is None or now >= self._last), 'PLATFORM_READBACK_CLOCK_INVALID')
        self._last = now
        return now

    def check_request(self, request):
        need(not self.closed, 'PLATFORM_READBACK_CLOSED')
        need(type(request) is ReadRequest and request.binding == self._binding
             and request.credential is self.credential and type(request.progress) is Progress
             and self.progress() == request.progress, 'PLATFORM_READBACK_REQUEST_INVALID')
        need(core.finite(request.deadline) and self.now() < request.deadline,
             'PLATFORM_READBACK_DEADLINE_EXCEEDED')

    def __call__(self, request):
        raise PlatformReadbackError('PLATFORM_READ_REQUEST_REQUIRED')

    def close(self):
        # Local references only; never token revocation or guaranteed erasure.
        self.closed = True
        self._token = self.credential = self.progress = None

    def _check_scope(self, request):
        need(not self.closed, 'PLATFORM_READBACK_CLOSED')
        if request.method == 'GET':
            need(request.path in (NAMESPACE_PATH, VERSION_PATH) and request.body == b''
                 and request.content_type == '', 'PLATFORM_READBACK_SCOPE_INVALID')
        else:
            need(request.method == 'POST' and request.content_type == 'application/json'
                 and ((request.path == SUBJECT_PATH and request.body == SUBJECT_BODY)
                      or (request.path == ACCESS_PATH and request.body in self.access_bodies)),
                 'PLATFORM_READBACK_SCOPE_INVALID')

    def _read_response(self, sock, request, deadline):
        # Exact create response for review APIs; a 200/proxy/404 is not evidence.
        return wire.read_json_response(sock, request, deadline, (200,) if request.method == 'GET' else (201,))[1:]

    def _transport_error(self, error):
        return wire.TransportError(safe_code(error, 'PLATFORM_READBACK_TRANSPORT_FAILED'))

    def exchange(self, request, method, path, body=b'', *, deadline_limit=None):
        self.check_request(request)
        end = request.deadline if deadline_limit is None else deadline_limit
        need(core.finite(end) and end <= request.deadline and self.now() < end,
             'PLATFORM_READBACK_DEADLINE_EXCEEDED')
        seconds = min(10, end - self.now())
        deadline = wire.Deadline(seconds, self.now)
        deadline.end = min(deadline.end, end)
        message = core.Request(self._binding.key, self._fingerprint, self._binding.api_origin,
            self._binding.ca_sha256, method, path, 'application/json' if method == 'POST' else '', body, seconds)
        raw, value = self._exchange(message, deadline)
        self.check_request(request)
        need(self.now() < end, 'PLATFORM_READBACK_DEADLINE_EXCEEDED')
        return raw, value

    def inspect(self, request, *, read_deadline=None):
        raws = []
        try:
            self.check_request(request)
            end = request.deadline if read_deadline is None else read_deadline
            need(core.finite(end) and end <= request.deadline and self.now() < end,
                 'PLATFORM_READBACK_DEADLINE_EXCEEDED')
            def read(method, path, body=b''):
                return self.exchange(request, method, path, body, deadline_limit=end)
            def subject():
                raw, value = read('POST', SUBJECT_PATH, SUBJECT_BODY); raws.append(raw)
                info = value.get('status', {}).get('userInfo')
                need(value.get('apiVersion') == 'authentication.k8s.io/v1'
                     and value.get('kind') == 'SelfSubjectReview' and type(info) is dict,
                     'PLATFORM_READBACK_SUBJECT_INVALID')
                groups = info.get('groups')
                need(type(groups) is list and len(groups) <= 128 and all(plain(x) for x in groups)
                     and len(groups) == len(set(groups)) and plain(info.get('username'))
                     and plain(info.get('uid')), 'PLATFORM_READBACK_SUBJECT_INVALID')
                need(ExpectedSubject(info['username'], info['uid'], tuple(sorted(groups))) == self.subject,
                     'PLATFORM_READBACK_SUBJECT_MISMATCH')

            def target():
                raw, value = read('GET', VERSION_PATH); raws.append(raw)
                need(value.get('apiVersion') == 'config.openshift.io/v1'
                     and value.get('kind') == 'ClusterVersion' and value.get('metadata', {}).get('name') == 'version'
                     and value['metadata'].get('deletionTimestamp') is None
                     and value.get('spec', {}).get('clusterID') == self._binding.cluster_id,
                     'PLATFORM_READBACK_CLUSTER_MISMATCH')
                raw, value = read('GET', NAMESPACE_PATH); raws.append(raw)
                need(value.get('apiVersion') == 'v1' and value.get('kind') == 'Namespace'
                     and value.get('metadata', {}).get('name') == core.NAMESPACE
                     and value['metadata'].get('uid') == self._binding.namespace_uid
                     and value['metadata'].get('deletionTimestamp') is None
                     and value.get('status', {}).get('phase') == 'Active', 'PLATFORM_READBACK_NAMESPACE_MISMATCH')

            subject(); target()
            for capability in self.capabilities:
                body = access_body(capability)
                raw, value = read('POST', ACCESS_PATH, body); raws.append(raw)
                attrs = dict(zip(('group', 'version', 'resource', 'name', 'namespace', 'verb'), capability))
                status = value.get('status')
                spec = value.get('spec')
                returned = spec.get('resourceAttributes') if type(spec) is dict else None
                # ResourceAttributes.group is json:",omitempty" in Kubernetes.
                # Missing group means core API group, NOT a wildcard/default
                # namespace. Normalize only this documented empty default.
                if type(returned) is dict:
                    returned = dict(returned)
                    returned.setdefault('group', '')
                need(value.get('apiVersion') == 'authorization.k8s.io/v1'
                     and value.get('kind') == 'SelfSubjectAccessReview'
                     and type(spec) is dict and set(spec) == {'resourceAttributes'}
                     and returned == attrs and type(status) is dict,
                     'PLATFORM_READBACK_ACCESS_INVALID')
                need(status.get('allowed') is True and status.get('denied', False) is False
                     and status.get('evaluationError', '') == '', 'PLATFORM_READBACK_ACCESS_DENIED')
            # Separate sequential reads, never an atomic snapshot or a proof of
            # no extra privilege. No token/authority caches are promoted.
            target(); subject(); self.check_request(request)
            need(self.now() < end, 'PLATFORM_READBACK_DEADLINE_EXCEEDED')
            return PlatformReadback(request, self.subject, self.credential, self._fingerprint,
                                    self._ip, self.capabilities, tuple(raws))
        except PlatformReadbackError:
            raise
        except Exception:
            raise PlatformReadbackError('PLATFORM_READBACK_UNAVAILABLE') from None


class ReadbackCheckedPlatformAdapter:
    """Connect fresh API observations to a SEPARATELY authenticating issuer.

    issuer_resolver is trusted site code, not JSON/claims/hash verification.
    It must authenticate issuance, issuer/audience/server expiry/minimal RBAC
    from actual originals with bounded I/O. This module does NOT supply it.
    The same original PlatformEvidence is returned, never fabricated from
    readback or a user declaration. No default CLI/runtime wiring is added.
    """
    def __init__(self, *, reader, issuer_resolver, wall=time.time):
        need(type(reader) is PlatformObservationReader and callable(issuer_resolver)
             and callable(wall), 'PLATFORM_ISSUER_RESOLVER_REQUIRED')
        self.reader, self.resolver, self.wall = reader, issuer_resolver, wall
        self._anchor = self._last = None

    def check(self, request):
        self.reader.check_request(request)
        w, m = self.wall(), self.reader.now()
        need(core.finite(w) and (self._last is None or w >= self._last),
             'PLATFORM_READBACK_CLOCK_INVALID')
        self._last = w
        if self._anchor is None:
            self._anchor = w, m
        return max(w, self._anchor[0] + m - self._anchor[1])

    def issued(self, request):
        self.check(request)
        value = self.resolver(request)
        now = self.check(request)
        need(type(value) is PlatformEvidence and value.request is request
             and value.binding == self.reader._binding and value.credential is self.reader.credential
             and value.credential_fingerprint == self.reader.credential.fingerprint
             and value.endpoint_ip == self.reader._ip and value.ca_pem == self.reader.ca_pem,
             'PLATFORM_ISSUER_EVIDENCE_INVALID')
        provenance_checked(value.provenance)
        need(value.provenance.principal == self.reader.subject.username and plain(value.issuer)
             and plain(value.audience) and type(value.raw_receipt) is bytes
             and 0 < len(value.raw_receipt) <= wire.MAX_BODY
             and core.digest(value.raw_receipt) == value.binding.platform_receipt_sha256,
             'PLATFORM_ISSUER_EVIDENCE_INVALID')
        need(type(value.requested_seconds) is int and value.requested_seconds == 900
             and core.finite(value.issued_at) and core.finite(value.expires_at)
             and value.issued_at <= now < value.expires_at
             and 0 < value.expires_at - value.issued_at <= 900,
             'PLATFORM_ISSUER_LIFETIME_INVALID')
        return value

    def __call__(self, request):
        try:
            before = self.issued(request)
            now = self.check(request)
            end = min(request.deadline, self.reader.now() + before.expires_at - now)
            observed = self.reader.inspect(request, read_deadline=end)
            need(type(observed) is PlatformReadback and observed.request is request
                 and observed.credential is self.reader.credential and observed.subject == self.reader.subject,
                 'PLATFORM_READBACK_INVALID')
            after = self.issued(request)
            need(before == after, 'PLATFORM_ISSUER_CHANGED')
            need(self.check(request) < after.expires_at, 'PLATFORM_ISSUER_LIFETIME_INVALID')
            return after
        except PlatformReadbackError:
            raise
        except Exception:
            raise PlatformReadbackError('PLATFORM_ISSUER_UNVERIFIED') from None

    def close(self):
        self.reader.close()
        self.resolver = None
