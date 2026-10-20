"""Fresh original-to-evidence ports, NOT issuer/producer/finish trust roots.

Reviewed site resolvers must authenticate original meaning and authority. A
GitHub body, receipt digest, Python type or local sender.close is not that proof.
No resolver/default site/JSON policy loader, token issuance or Runtime is here.
"""
from dataclasses import dataclass, field
import ipaddress
import time

import delivery_adapter as core
import delivery_store as store
from delivery_adopted_authority import pin_identity, text
from delivery_github_source import (BlobPin, CommentPin, GitHubOriginalAdapter,
                                    SourceOriginal, MAX_BLOB)
from delivery_private_source import PrivateGitHubOriginalAdapter
from delivery_objects import decode
from delivery_object_reader import BearerCredential
from delivery_verifier import (PlatformEvidence, ProducerEvidence,
    CompletionEvidence, Progress, ReadRequest, provenance_checked)
from producer_bridge import CHECKER_SHA256


class EvidenceSourceError(RuntimeError):
    """Fixed public codes; never original bodies, tokens or resolver messages."""


def need(ok, code):
    if not ok:
        raise EvidenceSourceError(code)


@dataclass(frozen=True)
class OriginalDecision:
    """Trusted resolver output, never a declaration accepted from user JSON.

    The resolver must use this invocation's exact original and bound its I/O by
    request.deadline. This type itself authenticates neither facts nor authority.
    """
    original: SourceOriginal = field(repr=False)
    evidence: object = field(repr=False)


class ReviewedEvidenceSource:
    """Opt-in connector for platform, producer or finish in AuthorityAdapters.

    Platform/producer pins refer to the original raw receipt blob, not a public
    success summary. Finish may refer to a reviewed blob or exact comment.
    This class never parses untrusted text into authority decisions. The separate
    resolver remains responsible for actual issuer/lineage/completion semantics.
    """
    def __init__(self, *, role, reader, pin, resolver, binding, credential,
                 progress, wall=time.time, monotonic=time.monotonic):
        try:
            need(role in ('platform', 'producer', 'finish')
                 and type(reader) in (GitHubOriginalAdapter, PrivateGitHubOriginalAdapter)
                 and type(pin) in ((CommentPin, BlobPin) if role == 'finish' else (BlobPin,))
                 and type(credential) is BearerCredential
                 and all(callable(x) for x in (resolver, progress, wall, monotonic)),
                 'EVIDENCE_SOURCE_CONFIGURATION_INVALID')
            core.binding_checked(binding)
            self.expected = pin_identity(pin)
            self.role, self.reader, self.pin, self.resolver = role, reader, pin, resolver
            self.binding, self.credential, self.progress = binding, credential, progress
            self.wall, self.monotonic = wall, monotonic
            self._last = self._anchor = None
            self._closed = False
        except EvidenceSourceError:
            raise
        except Exception:
            raise EvidenceSourceError('EVIDENCE_SOURCE_CONFIGURATION_INVALID') from None

    def check(self, request):
        need(not self._closed and type(request) is ReadRequest
             and request.binding == self.binding and request.credential is self.credential
             and type(request.progress) is Progress and self.progress() == request.progress,
             'EVIDENCE_SOURCE_REQUEST_INVALID')
        w, m = self.wall(), self.monotonic()
        need(core.finite(w) and core.finite(m) and
             (self._last is None or w >= self._last[0] and m >= self._last[1]),
             'EVIDENCE_SOURCE_CLOCK_INVALID')
        self._last = w, m
        if self._anchor is None:
            self._anchor = w, m
        need(core.finite(request.deadline) and m < request.deadline,
             'EVIDENCE_SOURCE_DEADLINE_EXCEEDED')
        return max(w, self._anchor[0] + m - self._anchor[1])

    def original(self, request):
        self.check(request)
        read = self.reader.comment if type(self.pin) is CommentPin else self.reader.blob
        original = read(request, self.pin)
        self.check(request)
        need(type(original) is SourceOriginal and original.request is request
             and original.execution_authorized is False
             and original.body_authorship_verified is False
             and type(original.body) is bytes and 0 < len(original.body) <= MAX_BLOB
             and type(original.repository_id) is int
             and (original.comment_creator_id is None or type(original.comment_creator_id) is int),
             'EVIDENCE_SOURCE_ORIGINAL_INVALID')
        need((original.reference, original.revision, original.repository_id,
              original.comment_creator_id, core.digest(original.body)) == self.expected,
             'EVIDENCE_SOURCE_ORIGINAL_MISMATCH')
        return original

    def platform_checked(self, value, original, request, now):
        need(value.credential is self.credential
             and value.credential_fingerprint == self.credential.fingerprint
             and type(value.requested_seconds) is int and value.requested_seconds == 900
             and text(value.issuer, 2048) and text(value.audience, 2048),
             'EVIDENCE_ISSUER_CREDENTIAL_MISMATCH')
        need(core.finite(value.issued_at) and core.finite(value.expires_at)
             and value.issued_at <= now < value.expires_at
             and 0 < value.expires_at - value.issued_at <= value.requested_seconds,
             'EVIDENCE_ISSUER_LIFETIME_INVALID')
        need(type(value.ca_pem) is bytes and 0 < len(value.ca_pem) <= 1024 * 1024
             and core.digest(value.ca_pem) == self.binding.ca_sha256,
             'EVIDENCE_ISSUER_CA_MISMATCH')
        need(type(value.endpoint_ip) is str and '%' not in value.endpoint_ip,
             'EVIDENCE_ISSUER_ENDPOINT_INVALID')
        address = ipaddress.ip_address(value.endpoint_ip)
        effective = getattr(address, 'ipv4_mapped', None) or address
        need(not (effective.is_unspecified or effective.is_loopback
                  or effective.is_link_local or effective.is_multicast),
             'EVIDENCE_ISSUER_ENDPOINT_INVALID')
        need(type(value.raw_receipt) is bytes and value.raw_receipt == original.body
             and core.digest(value.raw_receipt) == self.binding.platform_receipt_sha256,
             'EVIDENCE_ISSUER_RECEIPT_MISMATCH')

    def producer_checked(self, value, original):
        need(value.source_commit == self.binding.source_commit
             and value.contract_sha256 == self.binding.contract_sha256
             and value.rendered_sha256 == self.binding.rendered_sha256
             and value.checker_sha256 == CHECKER_SHA256,
             'EVIDENCE_PRODUCER_BINDING_MISMATCH')
        need(type(value.raw_receipt) is bytes and value.raw_receipt == original.body
             and core.digest(value.raw_receipt) == self.binding.supply_receipt_sha256,
             'EVIDENCE_PRODUCER_RECEIPT_MISMATCH')

    def finish_progress(self, request):
        # This is the session's checked durable receipt, not the journal envelope
        # or a self-reported PASS file. Filesystem durability and fresh object
        # reconciliation remain in the existing session/store/verifier.
        p = request.progress
        need(type(p.responses) is tuple and len(p.responses) == len(core.OBJECTS),
             'EVIDENCE_FINISH_PROGRESS_INCOMPLETE')
        journal = decode(p.journal)
        store.receipt_checked(journal, self.binding.key)
        need(journal['status'] == 'ROLLOUT_OBSERVED',
             'EVIDENCE_FINISH_PROGRESS_INCOMPLETE')
        uids = set()
        for raw, key in zip(p.responses, core.OBJECTS):
            reply = decode(raw)
            core.scope_checked(reply, self.binding, response=True)
            meta = reply['metadata']
            uid, rv = meta.get('uid'), meta.get('resourceVersion')
            need(core.identity(reply) == key and type(uid) is str and 0 < len(uid) <= 128
                 and uid not in uids and type(rv) is str and 0 < len(rv) <= 200,
                 'EVIDENCE_FINISH_RESPONSE_INVALID')
            uids.add(uid)

    def decision(self, request, original):
        self.check(request)
        result = self.resolver(request, original)
        now = self.check(request)
        kind = {'platform': PlatformEvidence, 'producer': ProducerEvidence,
                'finish': CompletionEvidence}[self.role]
        need(type(result) is OriginalDecision and result.original is original
             and type(result.evidence) is kind, 'EVIDENCE_SOURCE_DECISION_INVALID')
        value = result.evidence
        need(value.request is request, 'EVIDENCE_SOURCE_DECISION_INVALID')
        provenance_checked(value.provenance)
        need(value.provenance.reference == original.reference
             and value.provenance.revision == original.revision,
             'EVIDENCE_SOURCE_PROVENANCE_MISMATCH')
        if self.role != 'finish':
            need(value.binding == self.binding, 'EVIDENCE_SOURCE_BINDING_MISMATCH')
        if self.role == 'platform':
            self.platform_checked(value, original, request, now)
        elif self.role == 'producer':
            self.producer_checked(value, original)
        else:
            self.finish_progress(request)
            need(value.progress_key == request.progress.key
                 and value.disposition == 'EXCLUSIVE_ACCESS_ENDED',
                 'EVIDENCE_FINISH_DENIED')
        return value

    def __call__(self, request):
        try:
            self.check(request)
            if self.role == 'finish':
                self.finish_progress(request)  # No source I/O for partial/unknown writes.
            first = self.original(request)
            before = self.decision(request, first)
            second = self.original(request)
            # Never allow a cached first object to masquerade as a second read.
            need(second is not first, 'EVIDENCE_SOURCE_NOT_FRESH')
            after = self.decision(request, second)
            need(before == after, 'EVIDENCE_SOURCE_AUTHORITY_CHANGED')
            now = self.check(request)
            if self.role == 'platform':
                need(now < after.expires_at, 'EVIDENCE_ISSUER_LIFETIME_INVALID')
            return after
        except EvidenceSourceError:
            raise
        except Exception:
            raise EvidenceSourceError('EVIDENCE_SOURCE_UNVERIFIED') from None

    def close(self):
        # Reader/credential may be shared; their owner controls lifecycle. This
        # releases local references only, not server tokens, RBAC or exclusivity.
        self._closed = True
        self.reader = self.resolver = self.credential = self.progress = None
