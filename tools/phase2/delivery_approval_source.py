"""Bind a fresh original to a separately authenticating approval authority.

This connector is NOT that authority's implementation. The reviewed resolver
must authenticate grant rights, withdrawal/supersession and exact run/action/
window from its originals, with bounded I/O. No default resolver, JSON loader,
signature-key issuance, comment parser or promotion of a source hash to a GO.
"""
from dataclasses import dataclass, field
import time

import delivery_adapter as core
from delivery_github_source import (BlobPin, CommentPin, GitHubOriginalAdapter,
                                    SourceOriginal)
from delivery_private_source import PrivateGitHubOriginalAdapter
from delivery_verifier import (ApprovalEvidence, Progress, ReadRequest,
                              provenance_checked)


class ApprovalSourceError(RuntimeError):
    """Fixed public codes only; no original, credential or resolver exception."""


def need(value, code):
    if not value:
        raise ApprovalSourceError(code)


@dataclass(frozen=True)
class AuthorityDecision:
    """Trusted resolver result, NOT a schema for untrusted declarations.

    `original` must be this invocation's exact object, not a cached equivalent.
    This object and its Python type prove no grant rights by themselves.
    """
    original: SourceOriginal = field(repr=False)
    evidence: ApprovalEvidence = field(repr=False)


class ReviewedApprovalAdapter:
    def __init__(self, *, reader, pin, resolver, binding, credential, progress,
                 wall=time.time, monotonic=time.monotonic):
        need(type(reader) in (GitHubOriginalAdapter, PrivateGitHubOriginalAdapter)
             and type(pin) in (CommentPin, BlobPin), 'APPROVAL_SOURCE_CONFIGURATION_INVALID')
        need(callable(resolver), 'APPROVAL_RESOLVER_REQUIRED')
        core.binding_checked(binding)
        need(credential is not None and callable(progress) and callable(wall)
             and callable(monotonic), 'APPROVAL_SOURCE_CONFIGURATION_INVALID')
        self.reader, self.pin, self.resolver = reader, pin, resolver
        self.binding, self.credential, self.progress = binding, credential, progress
        self.wall, self.monotonic = wall, monotonic
        self._last = self._anchor = None

    def check(self, request):
        need(type(request) is ReadRequest and request.binding == self.binding
             and request.credential is self.credential
             and type(request.progress) is Progress
             and self.progress() == request.progress, 'APPROVAL_SOURCE_REQUEST_INVALID')
        wall, mono = self.wall(), self.monotonic()
        need(core.finite(wall) and core.finite(mono)
             and (self._last is None or
                  wall >= self._last[0] and mono >= self._last[1]),
             'APPROVAL_SOURCE_CLOCK_INVALID')
        self._last = wall, mono
        if self._anchor is None:
            self._anchor = wall, mono
        need(core.finite(request.deadline) and mono < request.deadline,
             'APPROVAL_SOURCE_DEADLINE_EXCEEDED')
        return max(wall, self._anchor[0] + mono - self._anchor[1])

    def close(self):
        # Shared readers and server-side credentials belong to the caller.
        self.reader = self.resolver = self.credential = self.progress = None

    def original(self, request):
        self.check(request)
        method = self.reader.comment if type(self.pin) is CommentPin else self.reader.blob
        value = method(request, self.pin)
        self.check(request)
        need(type(value) is SourceOriginal and value.request is request
             and value.execution_authorized is False
             and value.body_authorship_verified is False
             and type(value.body) is bytes, 'APPROVAL_SOURCE_ORIGINAL_INVALID')
        return value

    def decision(self, request, original):
        self.check(request)
        value = self.resolver(request, original)
        now = self.check(request)
        need(type(value) is AuthorityDecision and value.original is original
             and type(value.evidence) is ApprovalEvidence,
             'APPROVAL_AUTHORITY_DECISION_INVALID')
        evidence = value.evidence
        need(evidence.request is request and evidence.binding == self.binding
             and evidence.state == 'ACTIVE', 'APPROVAL_AUTHORITY_DENIED')
        provenance_checked(evidence.provenance)
        need(evidence.provenance.reference == original.reference
             and evidence.provenance.revision == original.revision,
             'APPROVAL_AUTHORITY_SOURCE_MISMATCH')
        need(core.finite(evidence.starts_at) and core.finite(evidence.expires_at)
             and evidence.starts_at <= now < evidence.expires_at,
             'APPROVAL_AUTHORITY_EXPIRED')
        return evidence

    @staticmethod
    def identity(original):
        return (original.reference, original.revision, original.repository_id,
                original.comment_creator_id, core.digest(original.body))

    def __call__(self, request):
        try:
            first = self.original(request)
            before = self.decision(request, first)
            second = self.original(request)
            need(self.identity(first) == self.identity(second), 'APPROVAL_SOURCE_CHANGED')
            after = self.decision(request, second)
            # Re-read the authority as well as the source. A second source read
            # alone would not notice a separate revocation/supersession ledger.
            need(before == after, 'APPROVAL_AUTHORITY_CHANGED')
            need(self.check(request) < after.expires_at, 'APPROVAL_AUTHORITY_EXPIRED')
            return after
        except ApprovalSourceError:
            raise
        except Exception:
            raise ApprovalSourceError('APPROVAL_SOURCE_UNVERIFIED') from None
