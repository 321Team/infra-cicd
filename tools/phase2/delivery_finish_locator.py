"""GitHub native envelope discovery, NOT automatic completion interpretation.

Discover an actual blob/comment revision after sender close, then use the
existing pinned source reader. No future hash, native body format invention,
source publication, policy loader or default semantic decoder.
"""
import base64
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import re
import time

import delivery_adapter as core
from delivery_adopted_authority import AdoptedGrant, pin_identity
from delivery_finish_collector import (BlobLocation, CommentLocation, policy_checked,
                                        record_checked)
from delivery_github_source import (BlobPin, CommentPin, SourceOriginal, GitHubOriginalAdapter,
                                    SHA1, MAX_BLOB)
from delivery_private_source import PrivateGitHubOriginalAdapter
from delivery_object_reader import BearerCredential
from delivery_runtime_site import EvidenceAdoption
from delivery_semantic_authority import FinishFacts, terminal_checked
from delivery_verifier import Progress, ReadRequest


class FinishLocatorError(RuntimeError):
    """Only fixed codes; never raw metadata, originals or decoder errors."""


def need(ok, code='FINISH_LOCATOR_INVALID'):
    if not ok:
        raise FinishLocatorError(code)


@dataclass(frozen=True)
class LocatedOriginal:
    pin: object = field(repr=False)
    original: SourceOriginal = field(repr=False)
    discovery_receipts: tuple = field(repr=False)
    execution_authorized: bool = field(default=False, init=False)
    body_meaning_authenticated: bool = field(default=False, init=False)


def timestamp(value, code='FINISH_LOCATOR_COMMENT_INVALID'):
    need(type(value) is str and re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z', value),
         code)
    try:
        return datetime.strptime(value, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)
    except ValueError:
        raise FinishLocatorError(code) from None


def comment_payload(obj, location, comment_id, prefix):
    """Native REST envelope only. Creator ID is not body/editor authority."""
    need(type(obj) is dict and type(obj.get('user')) is dict,
         'FINISH_LOCATOR_COMMENT_INVALID')
    need(type(obj.get('id')) is int and obj['id'] == comment_id
         and type(obj['user'].get('id')) is int and obj['user']['id'] == location.creator_id,
         'FINISH_LOCATOR_COMMENT_INVALID')
    reference = (f'https://github.com/{location.repository.owner}/{location.repository.name}'
                 f'/issues/{location.issue_number}#issuecomment-{comment_id}')
    need(obj.get('url') == f'https://api.github.com{prefix}/issues/comments/{comment_id}'
         and obj.get('issue_url') == f'https://api.github.com{prefix}/issues/{location.issue_number}'
         and obj.get('html_url') == reference, 'FINISH_LOCATOR_COMMENT_INVALID')
    need(timestamp(obj.get('created_at')) <= timestamp(obj.get('updated_at')),
         'FINISH_LOCATOR_COMMENT_INVALID')
    need(type(obj.get('body')) is str, 'FINISH_LOCATOR_COMMENT_INVALID')
    body = obj['body'].encode('utf-8', 'strict')
    need(0 < len(body) <= MAX_BLOB, 'FINISH_LOCATOR_COMMENT_INVALID')
    return obj['updated_at'], body


def _not_completion(raw):
    # This rejection list is not an allowlist or evidence authenticator. It
    # merely prevents known circular/diagnostic/P1 formats from being promoted
    # even by a mistaken decoder. Unknown formats still require reviewed code.
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeError):
        return
    schema = value.get('schema') if type(value) is dict else None
    if type(schema) is str:
        need(not (schema.startswith(('P1-', 'P2-CICD-private-diagnostic-'))
             or schema in ('P2-CICD-session-1', 'P2-CICD-operational-1',
                'P2-CICD-complete-1', 'P2-CICD-recovery-inspection-1',
                'P2-CICD-post-close-observation-1',
                'P2-CICD-journal-1', 'P2-CICD-apply-core-1',
                'P2-CICD-claim-1', 'P2-CICD-active-1')),
             'FINISH_LOCATOR_NOT_COMPLETION_ORIGINAL')


class NativeFinishLocator:
    """Exact code-configured location -> discovered pin -> verified original.

    decode(request, original) is REVIEWED native-body interpretation returning
    FinishFacts. It is mandatory and must read actual native RUN/action/progress
    and end authority, not copy the request into missing fields. No such actual
    production format/default decoder is invented here. The enclosing collector
    still authenticates raw/control twice and DeferredFinish does so again.
    """
    def __init__(self, *, policy, reader, decode, comment_id=None,
                 wall=time.time, monotonic=time.monotonic):
        try:
            policy_checked(policy)
            need(type(reader) in (GitHubOriginalAdapter, PrivateGitHubOriginalAdapter)
                 and all(callable(x) for x in (decode, wall, monotonic)),
                 'FINISH_LOCATOR_CONFIGURATION_INVALID')
            need((type(policy.location) is BlobLocation and comment_id is None)
                 or (type(policy.location) is CommentLocation and type(comment_id) is int and comment_id > 0),
                 'FINISH_LOCATOR_CONFIGURATION_INVALID')
        except Exception:
            raise FinishLocatorError('FINISH_LOCATOR_CONFIGURATION_INVALID') from None
        self.policy, self.reader, self.decode, self.comment_id = policy, reader, decode, comment_id
        self.wall, self.monotonic = wall, monotonic
        self.used, self.closed, self._last, self._anchor = False, False, None, None

    def check(self, request):
        need(not self.closed and type(request) is ReadRequest and request.binding == self.policy.binding
             and type(request.credential) is BearerCredential and type(request.progress) is Progress,
             'FINISH_LOCATOR_REQUEST_INVALID')
        w, m = self.wall(), self.monotonic()
        need(core.finite(w) and core.finite(m) and (self._last is None or
             w >= self._last[0] and m >= self._last[1]), 'FINISH_LOCATOR_CLOCK_INVALID')
        self._last = w, m
        if self._anchor is None:
            self._anchor = w, m
        now = max(w, self._anchor[0] + m - self._anchor[1])
        need(core.finite(request.deadline) and m < request.deadline
             and self.policy.starts_at <= now < self.policy.expires_at, 'FINISH_LOCATOR_OUTSIDE_WINDOW')
        return now

    def read(self, request, path, receipts):
        self.check(request)
        value = self.reader.read(request, path, receipts)
        self.check(request)
        need(type(value) is dict, 'FINISH_LOCATOR_METADATA_INVALID')
        return value

    def metadata_time(self, request, value):
        # GitHub envelope time is a rejection guard, NOT publication time,
        # body meaning, exclusive-end authority or a substitute for FinishFacts.
        observed = timestamp(value, 'FINISH_LOCATOR_METADATA_TIME_INVALID').timestamp()
        now = self.check(request)
        need(core.finite(observed) and self.policy.starts_at <= observed <= now,
             'FINISH_LOCATOR_METADATA_OUTSIDE_WINDOW')
        return observed

    def ref(self, request, prefix, location, receipts):
        obj = self.read(request, f'{prefix}/git/ref/{location.anchor_ref}', receipts)
        need(obj.get('ref') == 'refs/' + location.anchor_ref and type(obj.get('object')) is dict
             and obj['object'].get('type') == 'commit' and type(obj['object'].get('sha')) is str
             and SHA1.fullmatch(obj['object']['sha']), 'FINISH_LOCATOR_REF_INVALID')
        return obj['object']['sha']

    def blob_pin(self, request, prefix, receipts):
        location = self.policy.location
        commit = self.ref(request, prefix, location, receipts)
        obj = self.read(request, f'{prefix}/git/commits/{commit}', receipts)
        need(obj.get('sha') == commit and type(obj.get('tree')) is dict,
             'FINISH_LOCATOR_COMMIT_INVALID')
        need(type(obj.get('committer')) is dict, 'FINISH_LOCATOR_METADATA_TIME_INVALID')
        self.metadata_time(request, obj['committer'].get('date'))
        tree = obj['tree'].get('sha')
        parts = location.path.split('/')
        for index, part in enumerate(parts):
            need(type(tree) is str and SHA1.fullmatch(tree), 'FINISH_LOCATOR_TREE_INVALID')
            obj = self.read(request, f'{prefix}/git/trees/{tree}', receipts)
            need(obj.get('sha') == tree and obj.get('truncated') is False and type(obj.get('tree')) is list,
                 'FINISH_LOCATOR_TREE_INVALID')
            matches = [node for node in obj['tree'] if type(node) is dict and node.get('path') == part]
            need(len(matches) == 1, 'FINISH_LOCATOR_TREE_INVALID')
            node = matches[0]; final = index == len(parts)-1
            need(node.get('type') == ('blob' if final else 'tree') and
                 node.get('mode') in (('100644', '100755') if final else ('040000',)),
                 'FINISH_LOCATOR_TREE_INVALID')
            tree = node.get('sha')
        need(type(tree) is str and SHA1.fullmatch(tree), 'FINISH_LOCATOR_BLOB_INVALID')
        obj = self.read(request, f'{prefix}/git/blobs/{tree}', receipts)
        need(obj.get('sha') == tree and obj.get('encoding') == 'base64'
             and type(obj.get('size')) is int and 0 < obj['size'] <= MAX_BLOB
             and type(obj.get('content')) is str, 'FINISH_LOCATOR_BLOB_INVALID')
        raw = base64.b64decode(obj['content'].replace('\n', '').replace('\r', ''), validate=True)
        need(len(raw) == obj['size'] and hashlib.sha1(b'blob '+str(len(raw)).encode('ascii')+b'\0'+raw).hexdigest() == tree,
             'FINISH_LOCATOR_BLOB_INVALID')
        need(self.ref(request, prefix, location, receipts) == commit, 'FINISH_LOCATOR_REF_CHANGED')
        return BlobPin(location.repository, commit, location.path, core.digest(raw), location.anchor_ref, commit)

    def discover(self, request):
        self.check(request)
        receipts = []
        loc = self.policy.location
        prefix = self.reader.repository(request, loc.repository, receipts)
        self.check(request)
        if type(loc) is BlobLocation:
            pin = self.blob_pin(request, prefix, receipts)
            original = self.reader.blob(request, pin)
        else:
            obj = self.read(request, f'{prefix}/issues/comments/{self.comment_id}', receipts)
            updated_at, raw = comment_payload(obj, loc, self.comment_id, prefix)
            self.metadata_time(request, updated_at)
            pin = CommentPin(loc.repository, loc.issue_number, self.comment_id, loc.creator_id, updated_at, core.digest(raw))
            original = self.reader.comment(request, pin)
        self.check(request)
        need(type(original) is SourceOriginal and original.request is request
             and original.execution_authorized is False and original.body_authorship_verified is False
             and type(original.body) is bytes and 0 < len(original.body) <= MAX_BLOB,
             'FINISH_LOCATOR_ORIGINAL_INVALID')
        identity = (original.reference, original.revision, original.repository_id,
                    original.comment_creator_id, core.digest(original.body))
        need(type(original.repository_id) is int and (original.comment_creator_id is None
             or type(original.comment_creator_id) is int) and identity == pin_identity(pin),
             'FINISH_LOCATOR_ORIGINAL_CHANGED')
        return LocatedOriginal(pin, original, tuple(receipts))

    def __call__(self, request):
        need(not self.closed and not self.used, 'FINISH_LOCATOR_CONSUMED')
        self.used = True
        try:
            self.check(request)
            terminal_checked(request.binding, request.progress)
            self.check(request)
            located = self.discover(request)
            _not_completion(located.original.body)
            facts = self.decode(request, located.original)
            now = self.check(request)
            need(type(facts) is FinishFacts, 'FINISH_LOCATOR_DECODER_INVALID')
            p = self.policy
            grant = AdoptedGrant(p.binding, located.pin, p.control_pin, p.principal,
                                 p.adoption_reference, p.starts_at, p.expires_at)
            record = EvidenceAdoption(grant, facts)
            record_checked(p, request, record, now)
            self.check(request)
            return record
        except FinishLocatorError:
            raise
        except Exception:
            raise FinishLocatorError('FINISH_LOCATOR_UNVERIFIED') from None

    def close(self):
        self.closed = True
        self.policy = self.reader = self.decode = None
