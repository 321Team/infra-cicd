"""Optional CODE-ADOPTED interpretation of reviewed native originals.

The reviewed Python adoption is the trust root, not the fact types or hashes.
The integrator must review issuance/RBAC/Route and clean Test/Build/Scan/Push/
adoption meaning in those actual originals. No JSON/flags can load this policy.
This is NOT a JWT signature validator, token issuer, CI attestation verifier or
a discovery mechanism for revocations outside the explicitly adopted control.
"""
from dataclasses import dataclass, field, replace
import ipaddress
import time

import delivery_adapter as core
from delivery_adopted_authority import (AdoptedGrant, CodeAdoptedGrantResolver,
                                        pin_identity, text)
from delivery_evidence_source import OriginalDecision, terminal_progress_checked
from delivery_github_source import BlobPin
from delivery_object_reader import BearerCredential
from delivery_verifier import (PlatformEvidence, ProducerEvidence, CompletionEvidence,
                               Progress, ReadRequest)
from producer_bridge import CHECKER_SHA256


class SemanticAuthorityError(RuntimeError):
    """Fixed codes only; raw originals/facts/credential stay private."""


def need(ok, code):
    if not ok:
        raise SemanticAuthorityError(code)


@dataclass(frozen=True)
class IssuerFacts:
    credential_fingerprint: str = field(repr=False)
    issuer: str = field(repr=False)
    audience: str = field(repr=False)
    requested_seconds: int
    issued_at: float
    expires_at: float
    ca_pem: bytes = field(repr=False)
    endpoint_ip: str = field(repr=False)
    rbac_reference: str = field(repr=False)
    route_reference: str = field(repr=False)


@dataclass(frozen=True)
class ProducerFacts:
    images: tuple = field(repr=False)
    # Explicitly code-adopted absence for a FIRST empty-target deploy only;
    # never default/missing input, empty tuple, or copied current images.
    previous_images: tuple | None = field(repr=False)
    source_commit: str = field(repr=False)
    contract_sha256: str = field(repr=False)
    rendered_sha256: str = field(repr=False)
    checker_sha256: str = field(repr=False)
    test_reference: str = field(repr=False)
    build_reference: str = field(repr=False)
    scan_reference: str = field(repr=False)
    push_reference: str = field(repr=False)
    clean_render_reference: str = field(repr=False)
    adoption_reference: str = field(repr=False)


@dataclass(frozen=True)
class FinishFacts:
    run_id: str = field(repr=False)
    action: str
    progress_key: str = field(repr=False)
    observed_at: float
    follow_up_reference: str = field(repr=False)
    exclusive_end_reference: str = field(repr=False)


def terminal_checked(binding, progress):
    try:
        terminal_progress_checked(binding, progress)
    except Exception:
        raise SemanticAuthorityError('SEMANTIC_FINISH_NOT_TERMINAL') from None


class CodeAdoptedEvidenceResolver:
    """Concrete interpretation + fresh adopted-control authentication.

    Role facts must have been reviewed as the meaning of grant.original_pin's
    exact native bytes. No new producer receipt format or repeated GO is needed.
    The actual deployment grant is checked by the separate approval adapter.
    """
    def __init__(self, *, role, grant, facts, control_reader, credential, progress,
                 wall=time.time, monotonic=time.monotonic, first_deployment=False):
        self.authority, self.closed = None, False
        try:
            kind = {'platform': IssuerFacts, 'producer': ProducerFacts, 'finish': FinishFacts}.get(role)
            need(kind is not None and type(grant) is AdoptedGrant and type(facts) is kind
                 and type(credential) is BearerCredential, 'SEMANTIC_POLICY_INVALID')
            need(type(first_deployment) is bool and (role == 'producer' or not first_deployment),
                 'SEMANTIC_FIRST_DEPLOY_POLICY_INVALID')
            need(role == 'finish' or type(grant.original_pin) is BlobPin,
                 'SEMANTIC_RECEIPT_BLOB_REQUIRED')
            self.authority = CodeAdoptedGrantResolver(grant=grant, control_reader=control_reader,
                credential=credential, progress=progress, wall=wall, monotonic=monotonic)
            self.role, self.grant, self.facts = role, grant, facts
            self.credential, self.progress, self.closed = credential, progress, False
            self.first_deployment = first_deployment
            if role == 'platform':
                self.issuer_checked()
            elif role == 'producer':
                self.producer_checked()
            else:
                need(facts.run_id == grant.binding.run_id and facts.action == grant.binding.action
                     and type(facts.progress_key) is str and core.HEX.fullmatch(facts.progress_key)
                     and core.finite(facts.observed_at)
                     and grant.starts_at <= facts.observed_at < grant.expires_at
                     and text(facts.follow_up_reference, 2048)
                     and text(facts.exclusive_end_reference, 2048), 'SEMANTIC_FINISH_INVALID')
        except SemanticAuthorityError:
            self.close()
            raise
        except Exception:
            self.close()
            raise SemanticAuthorityError('SEMANTIC_POLICY_INVALID') from None

    def issuer_checked(self):
        f, b = self.facts, self.grant.binding
        need(f.credential_fingerprint == self.credential.fingerprint
             and type(f.requested_seconds) is int and f.requested_seconds == 900
             and text(f.issuer, 2048) and text(f.audience, 2048)
             and text(f.rbac_reference, 2048) and text(f.route_reference, 2048),
             'SEMANTIC_ISSUER_INVALID')
        need(core.finite(f.issued_at) and core.finite(f.expires_at)
             and 0 < f.expires_at - f.issued_at <= 900, 'SEMANTIC_ISSUER_LIFETIME_INVALID')
        need(type(f.ca_pem) is bytes and 0 < len(f.ca_pem) <= 1024 * 1024
             and core.digest(f.ca_pem) == b.ca_sha256
             and type(f.endpoint_ip) is str and '%' not in f.endpoint_ip,
             'SEMANTIC_ISSUER_TARGET_INVALID')
        try:
            address = ipaddress.ip_address(f.endpoint_ip)
            effective = getattr(address, 'ipv4_mapped', None) or address
            need(not (effective.is_unspecified or effective.is_loopback or effective.is_link_local
                      or effective.is_multicast), 'SEMANTIC_ISSUER_TARGET_INVALID')
        except ValueError:
            raise SemanticAuthorityError('SEMANTIC_ISSUER_TARGET_INVALID') from None

    def producer_checked(self):
        f, b = self.facts, self.grant.binding
        need((f.images, f.source_commit, f.contract_sha256, f.rendered_sha256, f.checker_sha256)
             == (b.images, b.source_commit, b.contract_sha256, b.rendered_sha256, CHECKER_SHA256),
             'SEMANTIC_PRODUCER_BINDING_MISMATCH')
        if f.previous_images is None:
            # #43 explicitly accepts no previous P2 digest on the first deploy.
            # Runtime site must additionally select FirstDeploymentBaseline,
            # which admits ONLY fresh exact-profile 404s before the first write.
            need(self.first_deployment and b.action == 'deploy',
                 'SEMANTIC_PRODUCER_FIRST_DEPLOY_REQUIRED')
        else:
            # Explicit full immutable profile, never a mutable/partial fallback.
            core.binding_checked(replace(b, images=f.previous_images))
            need(tuple(x[0] for x in f.previous_images) == core.services_for(b),
                 'SEMANTIC_PRODUCER_PREVIOUS_INVALID')
        need(all(text(x, 2048) for x in (f.test_reference, f.build_reference,
             f.scan_reference, f.push_reference, f.clean_render_reference, f.adoption_reference)),
             'SEMANTIC_PRODUCER_LINEAGE_MISSING')

    def __call__(self, request, original):
        try:
            need(not self.closed and type(request) is ReadRequest
                 and type(request.progress) is Progress, 'SEMANTIC_RESOLVER_CLOSED')
            if self.role == 'finish':
                terminal_checked(self.grant.binding, request.progress)
                need(request.progress.key == self.facts.progress_key,
                     'SEMANTIC_FINISH_PROGRESS_MISMATCH')
            # Authenticates SAME original, request/credential/progress, adopted
            # control ref+bytes and clocks/window before returning provenance.
            adopted = self.authority(request, original)
            now = self.authority.check(request)
            b, f, p = self.grant.binding, self.facts, adopted.evidence.provenance
            if self.role == 'platform':
                self.issuer_checked()
                need(core.digest(original.body) == b.platform_receipt_sha256,
                     'SEMANTIC_ISSUER_RECEIPT_MISMATCH')
                need(f.issued_at <= now < f.expires_at, 'SEMANTIC_ISSUER_LIFETIME_INVALID')
                evidence = PlatformEvidence(request, b, p, self.credential, f.credential_fingerprint,
                    f.issuer, f.audience, f.requested_seconds, f.expires_at, f.issued_at,
                    f.ca_pem, f.endpoint_ip, original.body)
            elif self.role == 'producer':
                self.producer_checked()
                need(core.digest(original.body) == b.supply_receipt_sha256,
                     'SEMANTIC_PRODUCER_RECEIPT_MISMATCH')
                evidence = ProducerEvidence(request, b, p, f.source_commit, f.contract_sha256,
                    f.rendered_sha256, f.checker_sha256, original.body)
            else:
                need(f.observed_at <= now, 'SEMANTIC_FINISH_FUTURE_OBSERVATION')
                evidence = CompletionEvidence(request, p, f.progress_key, 'EXCLUSIVE_ACCESS_ENDED')
            now = self.authority.check(request)
            if self.role == 'platform':
                need(f.issued_at <= now < f.expires_at, 'SEMANTIC_ISSUER_LIFETIME_INVALID')
            return OriginalDecision(original, evidence)
        except SemanticAuthorityError:
            raise
        except Exception:
            raise SemanticAuthorityError('SEMANTIC_AUTHORITY_UNAVAILABLE') from None

    def close(self):
        self.closed = True
        # Do not close borrowed source readers or claim server-side revocation.
        if self.authority is not None:
            self.authority.close()
        self.authority = self.grant = self.facts = self.credential = self.progress = None
