"""Optional code-adopted grant resolver; no policy loader or operational defaults.

The reviewed site policy, NOT source hashes/account IDs/Python types, is the
authority root. A fresh adopted control ref is a revocation fence only when the
site owner has explicitly adopted that control's update semantics. This module
does not create such a control or discover withdrawals in unrelated comments.
"""
from dataclasses import dataclass, field
from datetime import datetime
import re
import time

import delivery_adapter as core
from delivery_approval_source import AuthorityDecision
from delivery_github_source import (BlobPin, CommentPin, GitHubOriginalAdapter,
                                    SourceOriginal, repository_checked, positive,
                                    NAME, COMPONENT, SHA1, MAX_BLOB)
from delivery_private_source import PrivateGitHubOriginalAdapter
from delivery_verifier import ApprovalEvidence, Progress, Provenance, ReadRequest


class AdoptedAuthorityError(RuntimeError):
    """Only fixed codes; never policy bodies, credentials or callback messages."""


def need(ok, code):
    if not ok:
        raise AdoptedAuthorityError(code)


def text(value, limit):
    return (type(value) is str and 0 < len(value) <= limit
            and value.strip() == value and not any(ord(c) < 32 for c in value)
            and value.upper() not in ('PENDING', 'UNKNOWN', 'NOT_VERIFIED'))


def pin_identity(pin):
    """Validate and derive exact original identity, before any I/O."""
    need(type(pin) in (CommentPin, BlobPin), 'ADOPTED_POLICY_INVALID')
    repository_checked(pin.repository)
    prefix = f'https://github.com/{pin.repository.owner}/{pin.repository.name}'
    need(type(pin.body_sha256 if type(pin) is CommentPin else pin.sha256) is str,
         'ADOPTED_POLICY_INVALID')
    sha = pin.body_sha256 if type(pin) is CommentPin else pin.sha256
    need(core.HEX.fullmatch(sha), 'ADOPTED_POLICY_INVALID')
    if type(pin) is CommentPin:
        need(all(positive(x) for x in (pin.issue_number, pin.comment_id, pin.creator_id))
             and type(pin.updated_at) is str
             and re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z', pin.updated_at),
             'ADOPTED_POLICY_INVALID')
        datetime.strptime(pin.updated_at, '%Y-%m-%dT%H:%M:%SZ')
        return (f'{prefix}/issues/{pin.issue_number}#issuecomment-{pin.comment_id}',
                pin.updated_at + '/' + sha, pin.repository.repository_id,
                pin.creator_id, sha)
    need(type(pin.commit) is str and SHA1.fullmatch(pin.commit)
         and type(pin.anchor_commit) is str and SHA1.fullmatch(pin.anchor_commit)
         and type(pin.path) is str and type(pin.anchor_ref) is str
         and pin.anchor_ref.startswith('heads/'), 'ADOPTED_POLICY_INVALID')
    parts, refs = pin.path.split('/'), pin.anchor_ref.split('/')
    need(1 <= len(parts) <= 16 and all(COMPONENT.fullmatch(x) and x not in ('.', '..') for x in parts)
         and 2 <= len(refs) <= 16 and all(NAME.fullmatch(x) and '..' not in x
             and not x.endswith(('.', '.lock')) for x in refs), 'ADOPTED_POLICY_INVALID')
    return (f'{prefix}/blob/{pin.commit}/{pin.path}', pin.commit,
            pin.repository.repository_id, None, sha)


@dataclass(frozen=True)
class AdoptedGrant:
    """Reviewed site-code trust configuration, NEVER untrusted JSON or a GO.

    The integrator must authenticate/adopt the actual grant terms and the
    control's revocation/update semantics. Supplying this object is not proof
    that adoption occurred. There are no actual/default production values.
    """
    binding: core.Binding = field(repr=False)
    original_pin: object = field(repr=False)
    control_pin: BlobPin = field(repr=False)
    principal: str
    adoption_reference: str
    starts_at: float
    expires_at: float


class CodeAdoptedGrantResolver:
    def __init__(self, *, grant, control_reader, credential, progress,
                 wall=time.time, monotonic=time.monotonic):
        try:
            need(type(grant) is AdoptedGrant and type(grant.control_pin) is BlobPin
                 and type(control_reader) in (GitHubOriginalAdapter, PrivateGitHubOriginalAdapter)
                 and credential is not None and all(callable(x) for x in (progress, wall, monotonic)),
                 'ADOPTED_POLICY_REQUIRED')
            core.binding_checked(grant.binding)
            self.original_identity = pin_identity(grant.original_pin)
            self.control_identity = pin_identity(grant.control_pin)
            need(text(grant.principal, 128) and text(grant.adoption_reference, 512)
                 and core.finite(grant.starts_at) and core.finite(grant.expires_at)
                 and grant.starts_at < grant.expires_at, 'ADOPTED_POLICY_INVALID')
            self.grant, self.reader, self.credential = grant, control_reader, credential
            self.progress, self.wall, self.monotonic = progress, wall, monotonic
            self._last = self._anchor = None
        except AdoptedAuthorityError:
            raise
        except Exception:
            raise AdoptedAuthorityError('ADOPTED_POLICY_INVALID') from None

    def check(self, request):
        need(type(request) is ReadRequest and request.binding == self.grant.binding
             and request.credential is self.credential and type(request.progress) is Progress
             and self.progress() == request.progress, 'ADOPTED_REQUEST_INVALID')
        w, m = self.wall(), self.monotonic()
        need(core.finite(w) and core.finite(m) and
             (self._last is None or w >= self._last[0] and m >= self._last[1]),
             'ADOPTED_CLOCK_INVALID')
        self._last = w, m
        if self._anchor is None:
            self._anchor = w, m
        need(core.finite(request.deadline) and m < request.deadline,
             'ADOPTED_DEADLINE_EXCEEDED')
        now = max(w, self._anchor[0] + m - self._anchor[1])
        need(self.grant.starts_at <= now < self.grant.expires_at,
             'ADOPTED_GRANT_OUTSIDE_WINDOW')

    @staticmethod
    def original_checked(value, request, expected):
        need(type(value) is SourceOriginal and value.request is request
             and value.execution_authorized is False and value.body_authorship_verified is False
             and type(value.body) is bytes and len(value.body) <= MAX_BLOB,
             'ADOPTED_ORIGINAL_INVALID')
        identity = (value.reference, value.revision, value.repository_id,
                    value.comment_creator_id, core.digest(value.body))
        # Python bool equals int: reject it independently for numeric identities.
        need(type(value.repository_id) is int
             and (value.comment_creator_id is None or type(value.comment_creator_id) is int)
             and identity == expected, 'ADOPTED_ORIGINAL_MISMATCH')

    def __call__(self, request, original):
        try:
            self.check(request)
            self.original_checked(original, request, self.original_identity)
            # No cache or bare bool callback. The existing reader enforces the
            # pinned ref before AND after the blob read under the same deadline.
            control = self.reader.blob(request, self.grant.control_pin)
            self.check(request)
            self.original_checked(control, request, self.control_identity)
            authority = (self.grant.adoption_reference + '|control=' + control.reference
                         + '@' + control.revision + '|head=' + self.grant.control_pin.anchor_commit)
            need(text(authority, 2048), 'ADOPTED_POLICY_INVALID')
            evidence = ApprovalEvidence(request, self.grant.binding,
                Provenance(original.reference, original.revision, self.grant.principal, authority),
                'ACTIVE', self.grant.starts_at, self.grant.expires_at)
            self.check(request)
            return AuthorityDecision(original, evidence)
        except AdoptedAuthorityError:
            raise
        except Exception:
            raise AdoptedAuthorityError('ADOPTED_AUTHORITY_UNAVAILABLE') from None
