"""Bounded six-object GET adapter; construction and tests perform no I/O.

Not an approval/platform/credential authenticator or a baseline admission gate.
An integrating trusted provider must establish those before calling objects().
No CLI wiring, token issuance, retries, listing, writes or P1 fallback.
"""
from dataclasses import dataclass, field
import ssl
import time

import delivery_adapter as core
from delivery_objects import ObjectReconciler
import delivery_transport as transport
from delivery_verifier import ObjectEvidence, Progress, ReadRequest, VerificationError, require


# Exact source-controlled codes only. A provider can raise VerificationError
# with arbitrary text, so matching its type or an uppercase pattern is unsafe.
SAFE_CODES = frozenset('''
TIMEOUT_INVALID CLOCK_INVALID DEADLINE_EXCEEDED RESPONSE_TOO_LARGE
RESPONSE_JSON_INVALID HTTP_NOT_ACCEPTED HTTP_UNAUTHORIZED HTTP_FORBIDDEN
HTTP_RATE_LIMITED HTTP_HEADERS_INVALID HTTP_CONTENT_TYPE_INVALID
RESPONSE_SIZE_INVALID REQUEST_BINDING_INVALID TLS_POLICY_INVALID
REQUEST_BODY_INVALID CONNECT_FAILED TLS_PROTOCOL_INVALID SEND_FAILED
TLS_CERTIFICATE_FAILED IO_TIMEOUT OBJECT_CLOCK_INVALID OBJECT_READ_BINDING_INVALID
OBJECT_READ_DEADLINE OBJECT_READ_SCOPE_INVALID OBJECT_READ_IDENTITY_INVALID
OBJECT_ABSENCE_UNCONFIRMED OBJECT_READ_FAILED OBJECT_PROGRESS_INVALID
OBJECT_PROGRESS_CHANGED OBJECT_REVISION_INVALID OBJECT_JSON_INVALID OBJECT_RAW_INVALID
OBJECT_IDENTITY_MISMATCH OBJECT_METADATA_INVALID OBJECT_OWNERSHIP_UNKNOWN
OBJECT_OWNERSHIP_INVALID OBJECT_SUBRESOURCE_UNKNOWN OBJECT_STATUS_OWNERSHIP_INVALID
OBJECT_REVISION_OWNERSHIP_INVALID OBJECT_OWNERSHIP_DUPLICATE OBJECT_SET_INVALID
OBJECT_BINDING_MISMATCH OBJECT_RUN_STOPPED OBJECT_REPLY_ACK_MISMATCH OBJECT_UID_REPLACED
OBJECT_APPLY_MISMATCH OBJECT_APPLY_OWNERSHIP_MISSING OBJECT_REVISION_REVERSED
OBJECT_DRIFT OBJECT_RECONCILIATION_FAILED OBJECT_PROVIDER_FAILED
'''.split())


def safe_code(error, fallback):
    if isinstance(error, ssl.SSLCertVerificationError):
        return 'TLS_CERTIFICATE_FAILED'
    if isinstance(error, TimeoutError):
        return 'IO_TIMEOUT'
    if type(error) in (VerificationError, transport.TransportError):
        # Inspect a plain argument, never call a custom exception __str__.
        value = error.args[0] if len(error.args) == 1 else None
        if type(value) is str and value in SAFE_CODES:
            return value
    return fallback


@dataclass(frozen=True)
class BearerCredential:
    """In-memory handle only; having this object does not prove authenticity."""
    token: str = field(repr=False)

    @property
    def fingerprint(self):
        return core.digest(self.token.encode('ascii'))


@dataclass(frozen=True)
class Observation:
    key: tuple
    status: int
    raw: bytes = field(repr=False)

    @property
    def object_raw(self):
        # This type is constructed only after exact 200/404 classification.
        return self.raw if self.status == 200 else None


@dataclass(frozen=True)
class FailureDiagnostics:
    """Private in-memory readback for manual recovery, never ObjectEvidence.

    At most six already-classified replies; invalid HTTP bodies are excluded.
    A complete tuple after DRIFT still does not authorize success or replay.
    """
    code: str
    failed_key: tuple | None
    observations: tuple = field(repr=False)
    purpose: str = field(default='DIAGNOSTIC_ONLY', init=False)
    execution_authorized: bool = field(default=False, init=False)


class ObjectReader(transport.Sender):
    """Same pinned TLS implementation as Sender, but GET-only on six paths.

    404 is absence only for a v1 Status/Failure/NotFound with matching resource
    group, plural kind and name. A 404 for an unserved API or proxy is an error.
    Server TLS authentication does not prove token issuer/audience/RBAC/expiry.
    """
    def __init__(self, binding, *, credential, ca_pem, endpoint_ip, monotonic=time.monotonic):
        require(type(credential) is BearerCredential, 'OBJECT_CREDENTIAL_INVALID')
        super().__init__(binding, ca_pem=ca_pem, token=credential.token, endpoint_ip=endpoint_ip)
        self.credential = credential
        self.monotonic = monotonic
        self._last = None

    def __repr__(self):
        return '<P2 ObjectReader: credential redacted>'

    def close(self):
        # Local references only. A subsequent read fails the credential check.
        self._token = self.credential = None

    def now(self):
        now = self.monotonic()
        require(core.finite(now) and (self._last is None or now >= self._last),
                'OBJECT_CLOCK_INVALID')
        self._last = now
        return now

    def check_request(self, request):
        require(self.credential is not None and type(request) is ReadRequest and request.binding == self._binding
                and request.credential is self.credential, 'OBJECT_READ_BINDING_INVALID')
        require(core.finite(request.deadline) and self.now() < request.deadline,
                'OBJECT_READ_DEADLINE')

    def _check_scope(self, request):
        require(request.method == 'GET' and request.body == b'' and request.content_type == ''
                and request.path in {core.path_for(k, self._binding) for k in core.objects_for(self._binding)},
                'OBJECT_READ_SCOPE_INVALID')

    def __call__(self, request):
        raise VerificationError('OBJECT_READ_REQUEST_REQUIRED')

    def _transport_error(self, error):
        # This GET-only path may preserve a safe cause without changing the
        # write Sender's conservative TRANSPORT_FAILED behavior.
        return transport.TransportError(safe_code(error, 'OBJECT_READ_FAILED'))

    def _read_response(self, sock, request, deadline):
        status, raw, obj = transport.read_json_response(sock, request, deadline, (200, 404))
        key = next(k for k in core.objects_for(self._binding) if core.path_for(k, self._binding) == request.path)
        if status == 200:
            require(core.identity(obj) == key and obj['metadata'].get('namespace') == core.NAMESPACE,
                    'OBJECT_READ_IDENTITY_INVALID')
        else:
            group = key[0].split('/')[0] if '/' in key[0] else ''
            plural = {'Service': 'services', 'Deployment': 'deployments', 'Route': 'routes'}[key[1]]
            details = obj.get('details')
            require(obj.get('apiVersion') == 'v1' and obj.get('kind') == 'Status'
                    and obj.get('status') == 'Failure' and obj.get('reason') == 'NotFound'
                    and type(obj.get('code')) is int and obj['code'] == 404
                    and type(details) is dict and details.get('name') == key[2]
                    and details.get('kind') == plural and details.get('group', '') == group,
                    'OBJECT_ABSENCE_UNCONFIRMED')
        return Observation(key, status, raw)

    def read(self, request, key):
        try:
            self.check_request(request)
            require(type(key) is tuple and key in core.objects_for(self._binding), 'OBJECT_READ_SCOPE_INVALID')
            seconds = min(10, request.deadline - self.now())
            deadline = transport.Deadline(seconds, self.now)
            deadline.end = min(deadline.end, request.deadline)
            wire = core.Request(self._binding.key, self._fingerprint, self._binding.api_origin,
                                self._binding.ca_sha256, 'GET', core.path_for(key, self._binding), '', b'', seconds)
            observation = self._exchange(wire, deadline)
            self.check_request(request)
            return observation
        except Exception as error:
            # Never return None for TLS, timeout, RBAC, API or parsing failure.
            raise VerificationError(safe_code(error, 'OBJECT_READ_FAILED')) from None


class ObjectProvider:
    """Objects member for a trusted composite provider; no other authority.

    Receives a previously admitted immutable baseline in ObjectReconciler.
    Reads are sequential and share ReadRequest.deadline, not six fresh budgets.
    They are NOT an atomic Kubernetes snapshot. Exclusive access plus fresh
    platform/approval validation remains required in the caller's session.
    """
    def __init__(self, reader, reconciler, progress):
        require(type(reader) is ObjectReader and type(reconciler) is ObjectReconciler
                and reconciler.binding == reader._binding and callable(progress),
                'OBJECT_PROVIDER_CONFIGURATION_INVALID')
        self.reader, self.reconciler, self.progress = reader, reconciler, progress
        self.last_observations = ()
        self.failure_diagnostics = None

    def objects(self, request):
        self.last_observations = ()
        self.failure_diagnostics = None
        observations, failed_key = [], None
        try:
            self.reader.check_request(request)
            require(type(request.progress) is Progress, 'OBJECT_PROGRESS_INVALID')
            expected = request.progress.key
            require(self.progress() == request.progress, 'OBJECT_PROGRESS_CHANGED')
            for key in core.objects_for(request.binding):
                failed_key = key
                require(self.progress() == request.progress, 'OBJECT_PROGRESS_CHANGED')
                observations.append(self.reader.read(request, key))
            failed_key = None  # Reconciliation failure may concern the whole set.
            self.reader.check_request(request)
            require(self.progress() == request.progress, 'OBJECT_PROGRESS_CHANGED')
            verified = self.reconciler.check(request.binding, request.progress,
                tuple(o.object_raw for o in observations))
            self.reader.check_request(request)
            require(verified == expected and self.progress() == request.progress,
                    'OBJECT_PROGRESS_CHANGED')
            # Retain only the latest successful complete set, including 404 raw.
            self.last_observations = tuple(observations)
            return ObjectEvidence(request, expected)
        except Exception as error:
            code = safe_code(error, 'OBJECT_PROVIDER_FAILED')
            self.failure_diagnostics = FailureDiagnostics(code, failed_key, tuple(observations))
            raise VerificationError(code) from None
