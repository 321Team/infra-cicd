"""Actual-original collector/authentication, with a bounded Linux worker.

The locator is reviewed site code interpreting existing native evidence, NOT a
JSON approval loader. It runs only AFTER terminal progress/sender close. No
default locator, actual values, future hash, token issuance or publication.
The IPC frame is internal transport; it is not a new producer/GO document.
"""
from dataclasses import asdict, dataclass, field
import json
import secrets
import time

import delivery_adapter as core
import delivery_finish_worker as worker
from delivery_adopted_authority import AdoptedGrant, pin_identity, text
from delivery_evidence_source import ReviewedEvidenceSource
from delivery_github_source import (BlobPin, CommentPin, RepositoryPin,
                                    repository_checked, COMPONENT, NAME)
from delivery_object_reader import BearerCredential
from delivery_runtime_site import EvidenceAdoption, SourceReaders
from delivery_semantic_authority import (CodeAdoptedEvidenceResolver, FinishFacts,
                                         terminal_checked)
from delivery_verifier import Progress, ReadRequest

MAX_FRAME = 32768
SCHEMA = 'P2_FINISH_CAPTURE_IPC_1'


class FinishCollectorError(RuntimeError):
    """Fixed errors only, never native bodies, locations, credentials or facts."""


def need(ok, code='FINISH_COLLECTOR_INVALID'):
    if not ok:
        raise FinishCollectorError(code)


@dataclass(frozen=True)
class BlobLocation:
    repository: RepositoryPin
    path: str
    anchor_ref: str


@dataclass(frozen=True)
class CommentLocation:
    repository: RepositoryPin
    issue_number: int
    creator_id: int  # Account identity is NOT body authorship or authority.


@dataclass(frozen=True)
class FinishCollectionPolicy:
    binding: core.Binding = field(repr=False)
    location: object = field(repr=False)
    control_pin: BlobPin = field(repr=False)
    principal: str = field(repr=False)
    adoption_reference: str = field(repr=False)
    starts_at: float
    expires_at: float


def policy_checked(policy):
    need(type(policy) is FinishCollectionPolicy)
    core.binding_checked(policy.binding)
    need(type(policy.control_pin) is BlobPin)
    pin_identity(policy.control_pin)
    need(text(policy.principal, 128) and text(policy.adoption_reference, 512)
         and core.finite(policy.starts_at) and core.finite(policy.expires_at)
         and policy.starts_at < policy.expires_at)
    loc = policy.location
    need(type(loc) in (BlobLocation, CommentLocation))
    repository_checked(loc.repository)
    if type(loc) is BlobLocation:
        need(type(loc.path) is str and type(loc.anchor_ref) is str)
        parts, refs = loc.path.split('/'), loc.anchor_ref.split('/')
        need(1 <= len(parts) <= 16 and all(COMPONENT.fullmatch(p) and p not in ('.', '..') for p in parts)
             and loc.anchor_ref.startswith('heads/') and 2 <= len(refs) <= 16
             and all(NAME.fullmatch(p) and '..' not in p and not p.endswith(('.', '.lock')) for p in refs))
    else:
        need(all(type(x) is int and x > 0 for x in (loc.issue_number, loc.creator_id)))


def record_checked(policy, request, record, now):
    need(type(record) is EvidenceAdoption and type(record.grant) is AdoptedGrant
         and type(record.facts) is FinishFacts, 'FINISH_COLLECTOR_RECORD_INVALID')
    g, f, loc = record.grant, record.facts, policy.location
    need(core.finite(g.starts_at) and core.finite(g.expires_at)
         and text(g.principal, 128) and text(g.adoption_reference, 512)
         and (g.binding, g.control_pin, g.principal, g.adoption_reference, g.starts_at, g.expires_at)
         == (policy.binding, policy.control_pin, policy.principal, policy.adoption_reference,
             policy.starts_at, policy.expires_at), 'FINISH_COLLECTOR_POLICY_CHANGED')
    pin_identity(g.original_pin)
    p = g.original_pin
    need(p.repository == loc.repository, 'FINISH_COLLECTOR_SOURCE_SCOPE')
    if type(loc) is BlobLocation:
        need(type(p) is BlobPin and (p.path, p.anchor_ref) == (loc.path, loc.anchor_ref),
             'FINISH_COLLECTOR_SOURCE_SCOPE')
    else:
        need(type(p) is CommentPin and (p.issue_number, p.creator_id) == (loc.issue_number, loc.creator_id),
             'FINISH_COLLECTOR_SOURCE_SCOPE')
    need((f.run_id, f.action, f.progress_key) ==
         (request.binding.run_id, request.binding.action, request.progress.key)
         and core.finite(f.observed_at) and policy.starts_at <= f.observed_at <= now
         and f.observed_at < policy.expires_at
         and text(f.follow_up_reference, 2048) and text(f.exclusive_end_reference, 2048),
         'FINISH_COLLECTOR_FACTS_INVALID')


def _encode(record, request, nonce):
    pin = record.grant.original_pin
    frame = {'schema': SCHEMA, 'nonce': nonce, 'binding_key': request.binding.key,
        'credential_fingerprint': request.credential.fingerprint, 'progress_key': request.progress.key,
        'kind': 'blob' if type(pin) is BlobPin else 'comment', 'pin': asdict(pin),
        'facts': asdict(record.facts)}
    raw = json.dumps(frame, ensure_ascii=True, allow_nan=False, separators=(',', ':')).encode('ascii')
    need(0 < len(raw) <= MAX_FRAME, 'FINISH_COLLECTOR_FRAME_INVALID')
    return raw


def _object(pairs):
    out = {}
    for key, value in pairs:
        need(key not in out, 'FINISH_COLLECTOR_FRAME_INVALID')
        out[key] = value
    return out


def _keys(obj, keys):
    need(type(obj) is dict and set(obj) == set(keys), 'FINISH_COLLECTOR_FRAME_INVALID')


def _decode(raw, policy, request, nonce):
    """Called ONLY on this collector's private, sole-writer child pipe."""
    try:
        need(type(raw) is bytes and 0 < len(raw) <= MAX_FRAME, 'FINISH_COLLECTOR_FRAME_INVALID')
        d = json.loads(raw.decode('ascii'), object_pairs_hook=_object,
                       parse_constant=lambda _: need(False, 'FINISH_COLLECTOR_FRAME_INVALID'))
        _keys(d, ('schema', 'nonce', 'binding_key', 'credential_fingerprint', 'progress_key', 'kind', 'pin', 'facts'))
        need((d['schema'], d['nonce'], d['binding_key'], d['credential_fingerprint'], d['progress_key']) ==
             (SCHEMA, nonce, request.binding.key, request.credential.fingerprint, request.progress.key),
             'FINISH_COLLECTOR_FRAME_INVALID')
        fields = ('commit', 'path', 'sha256', 'anchor_ref', 'anchor_commit') if d['kind'] == 'blob' else (
            'issue_number', 'comment_id', 'creator_id', 'updated_at', 'body_sha256')
        need(d['kind'] in ('blob', 'comment'), 'FINISH_COLLECTOR_FRAME_INVALID')
        _keys(d['pin'], ('repository',) + fields)
        _keys(d['pin']['repository'], ('owner', 'name', 'repository_id', 'owner_id'))
        repo = RepositoryPin(**d['pin']['repository'])
        pin = (BlobPin if d['kind'] == 'blob' else CommentPin)(repo, **{k:d['pin'][k] for k in fields})
        _keys(d['facts'], ('run_id', 'action', 'progress_key', 'observed_at', 'follow_up_reference',
                            'exclusive_end_reference'))
        facts = FinishFacts(**d['facts'])
        grant = AdoptedGrant(policy.binding, pin, policy.control_pin, policy.principal,
                             policy.adoption_reference, policy.starts_at, policy.expires_at)
        return EvidenceAdoption(grant, facts)
    except Exception:
        raise FinishCollectorError('FINISH_COLLECTOR_FRAME_INVALID') from None


class AuthenticatedFinishCollector:
    """Single-use native source collection + authentication under a hard timeout.

    Trusted locate(request) selects/interprets ACTUAL existing native evidence
    after close. It must not publish, revoke, mutate, issue tokens or run shell
    commands. Reviewed location/control/authority/window cannot be broadened by
    its result. Child source/control authentication precedes the private handoff;
    DeferredFinish STILL reauthenticates in the parent before releasing a fence.
    """
    def __init__(self, *, policy, readers, credential, progress, locate, max_seconds,
                 wall=time.time, monotonic=time.monotonic):
        try:
            policy_checked(policy)
            need(type(readers) is SourceReaders and type(credential) is BearerCredential
                 and all(callable(x) for x in (progress, locate, wall, monotonic))
                 and core.finite(max_seconds) and 0.05 <= max_seconds <= 120,
                 'FINISH_COLLECTOR_CONFIGURATION_INVALID')
            readers.check()
        except Exception:
            raise FinishCollectorError('FINISH_COLLECTOR_CONFIGURATION_INVALID') from None
        self.policy, self.readers, self.credential, self.progress = policy, readers, credential, progress
        self.locate, self.max_seconds, self.wall, self.monotonic = locate, max_seconds, wall, monotonic
        self.used, self.closed, self._last, self._anchor = False, False, None, None

    def check(self, request):
        need(not self.closed and type(request) is ReadRequest and request.binding == self.policy.binding
             and request.credential is self.credential and type(request.progress) is Progress
             and self.progress() == request.progress, 'FINISH_COLLECTOR_REQUEST_INVALID')
        w, m = self.wall(), self.monotonic()
        need(core.finite(w) and core.finite(m) and (self._last is None or
             w >= self._last[0] and m >= self._last[1]), 'FINISH_COLLECTOR_CLOCK_INVALID')
        self._last = w, m
        if self._anchor is None:
            self._anchor = w, m
        now = max(w, self._anchor[0] + m - self._anchor[1])
        need(core.finite(request.deadline) and m < request.deadline
             and self.policy.starts_at <= now < self.policy.expires_at, 'FINISH_COLLECTOR_OUTSIDE_WINDOW')
        return now, m

    def _collect(self, request, nonce):
        resolver = port = None
        try:
            self.check(request)
            record = self.locate(request)
            now, _ = self.check(request)
            record_checked(self.policy, request, record, now)
            resolver = CodeAdoptedEvidenceResolver(role='finish', grant=record.grant,
                facts=record.facts, control_reader=self.readers.control, credential=self.credential,
                progress=self.progress, wall=self.wall, monotonic=self.monotonic)
            port = ReviewedEvidenceSource(role='finish', reader=self.readers.original,
                pin=record.grant.original_pin, resolver=resolver, binding=request.binding,
                credential=self.credential, progress=self.progress, wall=self.wall, monotonic=self.monotonic)
            port(request)  # Actual native bytes + fresh adopted control, each twice.
            now, _ = self.check(request)
            record_checked(self.policy, request, record, now)
            return _encode(record, request, nonce)
        finally:
            clean = True
            for handle in (port, resolver):
                if handle is not None:
                    try:
                        handle.close()  # Child-local; borrowed readers remain owned by parent.
                    except BaseException:
                        clean = False
            need(clean, 'FINISH_COLLECTOR_CLEANUP_FAILED')

    def __call__(self, request):
        need(not self.closed and not self.used, 'FINISH_COLLECTOR_CONSUMED')
        self.used = True  # Invalid/failed/late requests are never retried.
        try:
            _, m = self.check(request)
            terminal_checked(request.binding, request.progress)
            now, m = self.check(request)
            nonce = secrets.token_hex(32)  # Private IPC correlation, NOT an approval/signature.
            timeout = min(self.max_seconds, request.deadline - m, self.policy.expires_at - now)
            raw = worker.run_once(lambda:self._collect(request, nonce), timeout=timeout, max_bytes=MAX_FRAME)
            self.check(request)
            record = _decode(raw, self.policy, request, nonce)
            now, _ = self.check(request)
            record_checked(self.policy, request, record, now)
            self.check(request)
            return record
        except FinishCollectorError:
            raise
        except Exception:
            raise FinishCollectorError('FINISH_COLLECTOR_FAILED') from None

    def close(self):
        self.closed = True
        self.policy = self.readers = self.credential = self.progress = self.locate = None
