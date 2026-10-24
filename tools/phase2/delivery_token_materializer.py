"""Native TokenRequest bytes -> deferred plan facts, NEVER source authority.

Trusted code must select actual grants/controls and supply an authenticated,
same-job private original reader. No discovery, issuance, storage or transport
is installed here. Parsing a supplied response does not authenticate its TLS
origin or JWT signature. Existing source/control/current API checks still run.
"""
from dataclasses import dataclass, field, replace
import hmac
import ipaddress
import threading
from types import MappingProxyType

import delivery_adapter as core
import delivery_crosshost_ingress as ingress
import delivery_job as job
import delivery_runtime_site as runtime
import delivery_token_request as issuance
from delivery_adopted_authority import AdoptedGrant, pin_identity, text
from delivery_github_source import BlobPin
from delivery_object_reader import BearerCredential
from delivery_platform_readback import ExpectedSubject
from delivery_semantic_authority import IssuerFacts, ProducerFacts
from delivery_source_session import PrivateSourceSession
from producer_bridge import CHECKER_SHA256


class MaterializationError(RuntimeError):
    """Fixed codes only; originals, credentials and reader exceptions stay private."""


def need(ok):
    if not ok:
        raise MaterializationError('TOKEN_MATERIALIZER_INVALID')


@dataclass(frozen=True, repr=False)
class NativePlanSelection:
    """Explicit CODE selection, not a loader or proof of adoption/authorization."""
    approval: AdoptedGrant = field(repr=False)
    issuer_grant: AdoptedGrant = field(repr=False)
    producer: runtime.EvidenceAdoption = field(repr=False)
    baseline: runtime.BaselineAdoption | None = field(repr=False)
    subject: ExpectedSubject = field(repr=False)
    target: issuance.TokenTarget = field(repr=False)
    ca_pem: bytes = field(repr=False)
    endpoint_ip: str = field(repr=False)
    rbac_reference: str = field(repr=False)
    route_reference: str = field(repr=False)

    def check(self, binding):
        need(type(self.approval) is AdoptedGrant and type(self.issuer_grant) is AdoptedGrant
             and type(self.producer) is runtime.EvidenceAdoption
             and type(self.producer.grant) is AdoptedGrant
             and type(self.producer.facts) is ProducerFacts
             and type(self.subject) is ExpectedSubject and type(self.target) is issuance.TokenTarget)
        self.target.check()
        grants = (self.approval, self.issuer_grant, self.producer.grant)
        if self.baseline is not None:
            need(type(self.baseline) is runtime.BaselineAdoption
                 and type(self.baseline.grant) is AdoptedGrant
                 and type(self.baseline.objects) is tuple
                 and all(raw is None or type(raw) is bytes for raw in self.baseline.objects))
            grants += (self.baseline.grant,)
        for grant in grants:
            need(grant.binding == binding and type(grant.control_pin) is BlobPin
                 and text(grant.principal, 128) and text(grant.adoption_reference, 512)
                 and core.finite(grant.starts_at) and core.finite(grant.expires_at)
                 and grant.starts_at < grant.expires_at)
            pin_identity(grant.original_pin); pin_identity(grant.control_pin)
        need(type(self.issuer_grant.original_pin) is BlobPin
             and self.issuer_grant.original_pin.sha256 == binding.platform_receipt_sha256
             and type(self.producer.grant.original_pin) is BlobPin
             and self.producer.grant.original_pin.sha256 == binding.supply_receipt_sha256)
        username = f'system:serviceaccount:{self.target.namespace}:{self.target.name}'
        need((self.subject.username, self.subject.uid) == (username, self.target.uid)
             and self.issuer_grant.principal == username
             and type(self.subject.groups) is tuple
             and all(text(group, 2048) for group in self.subject.groups)
             and type(self.ca_pem) is bytes and core.digest(self.ca_pem) == binding.ca_sha256
             and text(self.rbac_reference, 2048) and text(self.route_reference, 2048)
             and type(self.endpoint_ip) is str and '%' not in self.endpoint_ip)
        address = ipaddress.ip_address(self.endpoint_ip)
        address = getattr(address, 'ipv4_mapped', None) or address
        need(not (address.is_unspecified or address.is_loopback or address.is_link_local
                  or address.is_multicast))
        p = self.producer.facts
        need((p.images, p.source_commit, p.contract_sha256, p.rendered_sha256, p.checker_sha256)
             == (binding.images, binding.source_commit, binding.contract_sha256,
                 binding.rendered_sha256, CHECKER_SHA256)
             and all(text(value, 2048) for value in (p.test_reference, p.build_reference,
                 p.scan_reference, p.push_reference, p.clean_render_reference, p.adoption_reference)))
        if p.previous_images is not None:
            core.binding_checked(replace(binding, images=p.previous_images))


class NativeTokenMaterializer(ingress._NoCopy):
    """One-use PreparedAuthorityPlan callback using explicit native original bytes.

    read_original(context, credential, source, roles, deadline) returns PRIVATE
    exact TokenRequest response bytes. The reader must authenticate the actual
    original/transport, bind it to this invocation and honor the same deadline.
    No live reader is supplied by default; the credential-only SSH frame does
    not currently carry this original. This class does not fill that gap.
    """
    def __init__(self, *, binding, context, selection, read_original, wall):
        error = None
        try:
            core.binding_checked(binding)
            need(type(context) is job.JobContext)
            context.check()
            need(type(selection) is NativePlanSelection and callable(read_original) and callable(wall))
            selection.check(binding)  # Shape/selected digest only; no callbacks, I/O or clocks.
        except BaseException as exc:
            error = _error(exc)
        if error is not None:
            binding = context = selection = read_original = wall = None
            raise error from None
        self._binding, self._selection = binding, selection
        self._context = context
        self._read, self._wall = read_original, wall
        self._closed, self._used, self._lock = False, False, threading.Lock()

    def __repr__(self):
        return '<P2 native TokenRequest materializer: private, unadopted, single-use>'

    def __call__(self, context, credential, source, roles, deadline):
        with self._lock:
            need(not self._closed and not self._used)
            self._used = True  # Invalid requests and failed reads consume this attempt.
            selected, read, wall = self._selection, self._read, self._wall
            self._selection = self._read = self._wall = None
        error = raw = receipt = plan = None
        try:
            need(type(context) is job.JobContext and type(credential) is BearerCredential
                 and context == self._context
                 and type(source) is PrivateSourceSession and source._state == 'CONSUMED'
                 and type(roles) is MappingProxyType and core.finite(deadline)
                 and source.deadline == deadline and source.now() < deadline)
            context.check()
            raw = read(context, credential, source, roles, deadline)
            need(source.now() < deadline)
            now = wall(); need(core.finite(now))
            # Reuse the issuer's exact native parser. Unlike issue(), this read
            # has NOT proved a TLS 201 origin. It is only consistency checking.
            receipt = issuance._receipt(raw, selected.target, now)
            need(hmac.compare_digest(receipt.credential.token, credential.token)
                 and receipt.requested_seconds == 900)
            facts = IssuerFacts(credential.fingerprint, selected.target.issuer,
                selected.target.audience, receipt.requested_seconds, receipt.issued_at,
                receipt.expires_at, selected.ca_pem, selected.endpoint_ip,
                selected.rbac_reference, selected.route_reference)
            plan = runtime.RuntimeAuthorityPlan(selected.approval,
                runtime.EvidenceAdoption(selected.issuer_grant, facts), selected.producer,
                selected.subject, selected.baseline)
            need(source.now() < deadline)
            with self._lock:
                need(not self._closed)
        except BaseException as exc:
            error = _error(exc)
        # Do not retain callback frames through __context__ or private inputs in
        # this callback's failure traceback. Borrowed source/credential stay owned
        # by the invocation. Reference release is not memory zeroization.
        raw = receipt = read = wall = selected = context = credential = source = roles = None
        if error is not None:
            plan = None
            raise error from None
        return plan

    def close(self):
        with self._lock:
            self._closed = self._used = True
            self._selection = self._read = self._wall = None
            self._context = None
        return True


def _error(exc):
    if isinstance(exc, KeyboardInterrupt):
        return KeyboardInterrupt()
    if isinstance(exc, SystemExit):
        return SystemExit(130)
    return MaterializationError('TOKEN_MATERIALIZATION_FAILED')
