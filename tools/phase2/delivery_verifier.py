"""P2 provider comparison layer, NOT an authenticated operational provider.

Only reviewed, trusted Python adapters may return these objects. They are NOT
JSON schemas and constructing them proves no authenticity. There is deliberately
no loader for user approvals, JWT claims, credentials or cached PASS reports.
"""
from dataclasses import dataclass, field
import ipaddress
import time
from typing import Protocol

import delivery_adapter as core
from delivery_authorization import VerifiedInputs
from producer_bridge import CHECKER_SHA256


# Fixed public diagnostics only. Never stringify an exception or emit a body.
SAFE_PROVIDER_DIAGNOSTICS = frozenset("""
SOURCE_READ_FAILED SOURCE_BLOB_UNVERIFIED SOURCE_COMMENT_UNVERIFIED
SOURCE_PRIVATE_READ_FAILED PRIVATE_GITHUB_READ_FAILED
ADOPTED_AUTHORITY_UNAVAILABLE ADOPTED_ORIGINAL_MISMATCH
SEMANTIC_AUTHORITY_UNAVAILABLE SEMANTIC_ISSUER_LIFETIME_INVALID
PLATFORM_READBACK_SUBJECT_INVALID PLATFORM_READBACK_SUBJECT_MISMATCH
PLATFORM_READBACK_ACCESS_INVALID PLATFORM_READBACK_ACCESS_DENIED
PLATFORM_READBACK_CLUSTER_MISMATCH PLATFORM_READBACK_NAMESPACE_MISMATCH
PLATFORM_READBACK_UNAVAILABLE PLATFORM_READBACK_DEADLINE_EXCEEDED
PLATFORM_ISSUER_UNVERIFIED PLATFORM_ISSUER_CHANGED PLATFORM_ISSUER_LIFETIME_INVALID
ACTIONS_RUN_NOT_SUCCESSFUL ACTIONS_RUN_PIN_MISMATCH ACTIONS_SOURCE_UNAVAILABLE
ACTIONS_SOURCE_UNVERIFIED ACTIONS_PRODUCER_UNVERIFIED ACTIONS_PRODUCER_CHANGED
ACTIONS_RECEIPT_LIMIT PROVIDER_UNAVAILABLE
OBJECT_DRIFT OBJECT_UID_REPLACED OBJECT_OWNERSHIP_INVALID
OBJECT_PROVIDER_FAILED OBJECT_READ_FAILED HTTP_UNAUTHORIZED HTTP_FORBIDDEN
CONNECT_FAILED IO_TIMEOUT TLS_CERTIFICATE_FAILED
""".split())


def public_provider_diagnostic(error):
    # Providers can put secrets or hostile objects in args. Only these literals
    # are ever returned, independent of the exception's name or __str__.
    try:
        args=error.args
        value=args[0] if type(args) is tuple and len(args)==1 else None
        return value if type(value) is str and value in SAFE_PROVIDER_DIAGNOSTICS else 'PROVIDER_UNAVAILABLE'
    except BaseException:
        return 'PROVIDER_UNAVAILABLE'


class VerificationError(RuntimeError):
    """Public errors contain fixed codes only."""


def require(condition, code):
    if not condition:
        raise VerificationError(code)


@dataclass(frozen=True)
class Provenance:
    reference: str
    revision: str
    principal: str
    authority_reference: str


@dataclass(frozen=True)
class Progress:
    # Canonical durable receipt and observed PATCH replies are separate. A reply is
    # never promoted to an ACK when journal persistence failed.
    journal: bytes | None = field(default=None, repr=False)
    responses: tuple = field(default=(), repr=False)

    @property
    def key(self):
        return core.digest(core.encoded([
            None if self.journal is None else core.digest(self.journal),
            [core.digest(raw) for raw in self.responses]]))


@dataclass(frozen=True)
class ReadRequest:
    binding: core.Binding
    credential: object = field(repr=False)
    progress: Progress
    deadline: float  # absolute monotonic deadline, shared by all four reads


@dataclass(frozen=True)
class ApprovalEvidence:
    request: ReadRequest = field(repr=False)
    binding: core.Binding
    provenance: Provenance
    state: str  # ACTIVE only; withdrawn/superseded/unknown all deny
    starts_at: float
    expires_at: float


@dataclass(frozen=True)
class PlatformEvidence:
    request: ReadRequest = field(repr=False)
    binding: core.Binding  # independently assembled observed target + receipt
    provenance: Provenance
    credential: object = field(repr=False)
    credential_fingerprint: str
    issuer: str
    audience: str
    requested_seconds: int
    expires_at: float
    issued_at: float  # authenticated issuance time, not an unverified JWT decode
    ca_pem: bytes = field(repr=False)
    endpoint_ip: str
    raw_receipt: bytes = field(repr=False)


@dataclass(frozen=True)
class ProducerEvidence:
    request: ReadRequest = field(repr=False)
    binding: core.Binding
    provenance: Provenance
    source_commit: str
    contract_sha256: str
    rendered_sha256: str
    checker_sha256: str
    raw_receipt: bytes = field(repr=False)


@dataclass(frozen=True)
class ObjectEvidence:
    request: ReadRequest = field(repr=False)
    progress_key: str


@dataclass(frozen=True)
class CompletionEvidence:
    request: ReadRequest = field(repr=False)
    provenance: Provenance
    progress_key: str
    disposition: str  # EXCLUSIVE_ACCESS_ENDED, never Delivery/AC acceptance


class Providers(Protocol):
    """Unimplemented trust boundary. Each method MUST enforce request.deadline.

    approval: authenticate source/version, principal authority, withdrawal and
      replacement status plus exact action/run/window/binding; never parse flags.
    platform: use request.credential itself to authenticate issuer/audience,
      actual expiry and minimal RBAC; verify fresh API/cluster/namespace UID/CA,
      approved Route host and numeric endpoint resolution. No token refresh.
    producer: authenticate clean-source/render generation, P2 current/previous
      adoption, Build/Scan/Push lineage and original raw receipts for this binding.
      Matching hashes or CommittedProducerBridge alone do not prove generation.
    objects: read fresh UID/spec/image/field ownership with that same credential;
      reconcile only durable ACKs and matching replies in request.progress.
      Permit expected own writes, never infer an ACK from a pending intent.
    finish: verify required follow-up observations and that exclusive access has
      ended. This is separate from rollout and from Delivery/health/AC acceptance.

    Returned request must be the SAME request object (no stale cached evidence).
    A provider is trusted code, not an adversarial plugin sandbox. These shapes
    do not perform cryptographic, network or operational object verification.
    """
    def approval(self, request: ReadRequest) -> ApprovalEvidence: ...
    def platform(self, request: ReadRequest) -> PlatformEvidence: ...
    def producer(self, request: ReadRequest) -> ProducerEvidence: ...
    def objects(self, request: ReadRequest) -> ObjectEvidence: ...
    def finish(self, request: ReadRequest) -> CompletionEvidence: ...


class UnconfiguredProviders:
    """No defaults, environment discovery, credential issuance or fallback."""
    def unavailable(self, request):
        raise VerificationError('OPERATIONAL_PROVIDER_UNCONFIGURED')

    approval = platform = producer = objects = finish = unavailable


def provenance_checked(value):
    require(type(value) is Provenance, 'PROVENANCE_INVALID')
    for text in (value.reference, value.revision, value.principal, value.authority_reference):
        require(type(text) is str and 0 < len(text) <= 2048
                and text.strip() == text and not any(ord(c) < 32 for c in text)
                and text.upper() not in ('PENDING', 'UNKNOWN', 'NOT_VERIFIED'),
                'PROVENANCE_INVALID')


class Verifier:
    """Fresh comparisons for BoundAuthorizer; no runtime truth by itself.

    budget_seconds is explicitly supplied by trusted integration configuration.
    No production budget is selected here. Checks after callbacks reject late
    answers; they CANNOT interrupt an indefinitely blocked Python callback.
    Real adapters must provide independently tested bounded I/O/cancellation.
    """
    def __init__(self, *, providers, credential, progress, budget_seconds,
                 wall=time.time, monotonic=time.monotonic):
        require(core.finite(budget_seconds) and budget_seconds > 0, 'BUDGET_INVALID')
        require(credential is not None, 'CREDENTIAL_HANDLE_REQUIRED')
        self.providers, self.credential, self.progress = providers, credential, progress
        self.budget, self.wall, self.monotonic = budget_seconds, wall, monotonic
        self.initial = self.platform = None
        self.diagnostic_role = None
        self.diagnostic_code = None
        self.last_wall = self.last_mono = None
        self.anchor_wall = self.anchor_mono = None

    def clock(self):
        w, m = self.wall(), self.monotonic()
        require(core.finite(w) and core.finite(m), 'CLOCK_INVALID')
        require((self.last_wall is None or w >= self.last_wall)
                and (self.last_mono is None or m >= self.last_mono), 'CLOCK_REVERSED')
        self.last_wall, self.last_mono = w, m
        if self.anchor_wall is None:
            self.anchor_wall, self.anchor_mono = w, m
        # Time spent in preflight/providers consumes lifetime even if wall time stalls.
        return max(w, self.anchor_wall + m - self.anchor_mono), m

    def request(self, binding):
        core.binding_checked(binding)
        _, start = self.clock()
        progress = self.progress()
        require(type(progress) is Progress, 'PROGRESS_INVALID')
        deadline = start + self.budget
        if self.initial is not None:
            now, _ = self.clock()
            deadline = min(deadline, start + min(self.platform.expires_at,
                           self.initial[1]) - now)
        require(core.finite(deadline) and start < deadline, 'VERIFICATION_EXPIRED')
        return ReadRequest(binding, self.credential, progress, deadline)

    def read(self, method, kind, request):
        _, now = self.clock()
        require(now < request.deadline, 'PROVIDER_DEADLINE_EXCEEDED')
        try:
            result = method(request)
        except Exception as error:
            self.diagnostic_code = public_provider_diagnostic(error)
            raise VerificationError('PROVIDER_UNAVAILABLE') from None
        _, now = self.clock()
        require(now < request.deadline, 'PROVIDER_DEADLINE_EXCEEDED')
        require(type(result) is kind and result.request is request, 'EVIDENCE_INVALID')
        return result

    def __call__(self, binding):
        try:
            request = self.request(binding)
            self.diagnostic_role = 'approval'
            self.diagnostic_code = None
            a = self.read(self.providers.approval, ApprovalEvidence, request)
            provenance_checked(a.provenance)
            require(a.binding == binding and a.state == 'ACTIVE', 'APPROVAL_DENIED')
            now, _ = self.clock()
            require(core.finite(a.starts_at) and core.finite(a.expires_at)
                    and a.starts_at <= now < a.expires_at, 'APPROVAL_EXPIRED')
            # All remaining reads share both the provider and approval deadline.
            # Preserve progress and narrow the immutable challenge before reads.
            request = ReadRequest(binding, self.credential, request.progress,
                                  min(request.deadline, self.last_mono + a.expires_at - now))
            self.diagnostic_role = 'platform'
            self.diagnostic_code = None
            p = self.read(self.providers.platform, PlatformEvidence, request)
            provenance_checked(p.provenance)
            require(p.binding == binding and p.credential is self.credential,
                    'PLATFORM_BINDING_MISMATCH')
            require(type(p.credential_fingerprint) is str
                    and core.HEX.fullmatch(p.credential_fingerprint)
                    and type(p.requested_seconds) is int and p.requested_seconds == 900
                    and core.finite(p.expires_at) and core.finite(p.issued_at), 'CREDENTIAL_INVALID')
            for value in (p.issuer, p.audience):
                require(type(value) is str and value.strip()
                        and value.upper() not in ('PENDING', 'UNKNOWN'), 'CREDENTIAL_INVALID')
            require(type(p.ca_pem) is bytes and 0 < len(p.ca_pem) <= 1024 * 1024
                    and core.digest(p.ca_pem) == binding.ca_sha256, 'CA_MISMATCH')
            require(type(p.endpoint_ip) is str and '%' not in p.endpoint_ip, 'ENDPOINT_INVALID')
            address = ipaddress.ip_address(p.endpoint_ip)
            effective = getattr(address, 'ipv4_mapped', None) or address
            require(not (effective.is_unspecified or effective.is_multicast
                         or effective.is_loopback or effective.is_link_local), 'ENDPOINT_INVALID')
            self.receipt(p.raw_receipt, binding.platform_receipt_sha256)
            now, m = self.clock()
            require(now < p.expires_at, 'CREDENTIAL_EXPIRED')
            require(p.issued_at <= now and 0 < p.expires_at - p.issued_at <= p.requested_seconds,
                    'CREDENTIAL_LIFETIME_INVALID')
            request = ReadRequest(binding, self.credential, request.progress,
                                  min(request.deadline, m + p.expires_at - now))
            self.diagnostic_role = 'producer'
            self.diagnostic_code = None
            s = self.read(self.providers.producer, ProducerEvidence, request)
            provenance_checked(s.provenance)
            require(s.binding == binding and s.source_commit == binding.source_commit
                    and s.contract_sha256 == binding.contract_sha256
                    and s.rendered_sha256 == binding.rendered_sha256
                    and s.checker_sha256 == CHECKER_SHA256, 'PRODUCER_BINDING_MISMATCH')
            self.receipt(s.raw_receipt, binding.supply_receipt_sha256)
            self.diagnostic_role = 'objects'
            self.diagnostic_code = None
            o = self.read(self.providers.objects, ObjectEvidence, request)
            require(o.progress_key == request.progress.key, 'OBJECT_PROGRESS_MISMATCH')
            require(self.progress() == request.progress, 'PROGRESS_CHANGED')
            now, finished = self.clock()
            require(finished < request.deadline, 'PROVIDER_DEADLINE_EXCEEDED')
            require(now < min(a.expires_at, p.expires_at), 'VERIFICATION_EXPIRED')
            stable = (a.provenance, a.expires_at, a.starts_at, binding,
                      p.provenance, p.credential_fingerprint, p.issuer, p.audience,
                      p.expires_at, p.issued_at, p.endpoint_ip, s.provenance)
            require(self.initial is None or stable == self.initial, 'AUTHORITY_CHANGED')
            self.initial, self.platform = stable, p
            lease = core.Lease(binding.key, p.credential_fingerprint, p.requested_seconds,
                               p.expires_at, a.expires_at)
            return VerifiedInputs(a.binding, p.binding, lease)
        except VerificationError:
            raise
        except Exception:
            raise VerificationError('VERIFICATION_FAILED') from None

    @staticmethod
    def receipt(raw, expected):
        require(type(raw) is bytes and 0 < len(raw) <= 1024 * 1024
                and core.digest(raw) == expected, 'RAW_RECEIPT_MISMATCH')

    def finish(self, binding):
        """Only after sender.close, with a fresh authorization and terminal journal."""
        self(binding)
        try:
            request = self.request(binding)
            result = self.read(self.providers.finish, CompletionEvidence, request)
            provenance_checked(result.provenance)
            require(result.progress_key == request.progress.key
                    and result.disposition == 'EXCLUSIVE_ACCESS_ENDED'
                    and self.progress() == request.progress, 'COMPLETION_DENIED')
            # Re-read authority AFTER finish I/O too (withdrawal/expiry can race).
            self(binding)
        except VerificationError:
            raise
        except Exception:
            raise VerificationError('COMPLETION_DENIED') from None
