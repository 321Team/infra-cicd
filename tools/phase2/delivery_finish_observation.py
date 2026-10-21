"""Opt-in post-close readback producer/decoder and code-adopted finish scope.

Raw observations are private evidence INPUT, not approval, revocation, health
or AC acceptance. A reviewed site must explicitly adopt an existing scope whose
remaining exclusive work is precisely this readback. There is no default scope,
external raw/JSON authority loader, credential issuance or GitHub publication.
"""
import base64
from dataclasses import dataclass, field
import json
import os
import time

import delivery_adapter as core
import delivery_store as store
from delivery_adopted_authority import AdoptedGrant, CodeAdoptedGrantResolver
from delivery_approval_source import ReviewedApprovalAdapter
from delivery_object_reader import BearerCredential, ObjectProvider, Observation
from delivery_objects import decode as object_decode
from delivery_semantic_authority import terminal_checked
from delivery_session import SenderClosed
from delivery_verifier import CompletionEvidence, Progress, ReadRequest

SCHEMA = 'P2-CICD-post-close-observation-1'
FOLLOWUPS = ('OBJECT_READBACK', 'ROLLOUT_READBACK')
MAX_ORIGINAL = 13 * 1024 * 1024  # At most nine 1-MiB bodies, base64 encoded.
KEYS = frozenset(('schema', 'binding_key', 'run_id', 'action',
    'credential_fingerprint', 'progress_key', 'journal_sha256', 'response_sha256',
    'sender_closed_at', 'sender_closed_monotonic', 'observed_at',
    'observed_monotonic', 'observations', 'execution_authorized'))


class FinishObservationError(RuntimeError):
    """Fixed public codes only; never native responses, paths or credentials."""


def need(ok, code='FINISH_OBSERVATION_INVALID'):
    if not ok:
        raise FinishObservationError(code)


def _unique(pairs):
    value = {}
    for key, item in pairs:
        need(key not in value, 'FINISH_OBSERVATION_JSON_INVALID')
        value[key] = item
    return value


def _rollout(raw):
    obj = object_decode(raw)
    if obj.get('kind') != 'Deployment':
        return
    desired, generation = obj['spec'].get('replicas'), obj['metadata'].get('generation')
    status = obj.get('status')
    need(type(desired) is int and desired > 0 and type(generation) is int and generation > 0
         and type(status) is dict, 'FINISH_OBSERVATION_ROLLOUT_INVALID')
    conditions = status.get('conditions', [])
    need(type(conditions) is list and all(type(c) is dict for c in conditions)
         and not any(c.get('type') == 'Progressing' and c.get('status') == 'False'
                     and c.get('reason') == 'ProgressDeadlineExceeded' for c in conditions),
         'FINISH_OBSERVATION_ROLLOUT_INVALID')
    need(type(status.get('observedGeneration')) is int and status['observedGeneration'] >= generation
         and all(type(status.get(k)) is int and status[k] == desired for k in
                 ('replicas', 'updatedReplicas', 'readyReplicas', 'availableReplicas'))
         and all(type(status.get(k, 0)) is int and status.get(k, 0) == 0 for k in
                 ('unavailableReplicas', 'terminatingReplicas')),
         'FINISH_OBSERVATION_ROLLOUT_NOT_READY')


def context_checked(request, closed, progress, now, mono):
    need(type(request) is ReadRequest and type(request.credential) is BearerCredential
         and type(request.progress) is Progress and progress() == request.progress,
         'FINISH_OBSERVATION_REQUEST_INVALID')
    terminal_checked(request.binding, request.progress)
    need(type(closed) is SenderClosed and closed.credential is request.credential
         and (closed.binding_key, closed.progress_key) == (request.binding.key, request.progress.key),
         'FINISH_OBSERVATION_CLOSE_REQUIRED')
    need(all(core.finite(x) for x in (now, mono, closed.wall, closed.monotonic, request.deadline))
         and closed.wall <= now and closed.monotonic <= mono < request.deadline,
         'FINISH_OBSERVATION_CLOCK_INVALID')


def original_bytes(request, closed, observations, *, now, mono):
    """Serialize actual context/event and untouched native GET bodies, no PASS."""
    b, p = request.binding, request.progress
    need(type(observations) is tuple and len(observations) == len(core.objects_for(b)))
    rows = []
    for key, o in zip(core.objects_for(b), observations):
        need(type(o) is Observation and o.key == key and type(o.status) is int and o.status == 200
             and type(o.raw) is bytes and 0 < len(o.raw) <= 1024 * 1024)
        rows.append({'key': list(key), 'status': o.status,
                     'body_base64': base64.b64encode(o.raw).decode('ascii')})
    raw = core.encoded({'schema': SCHEMA, 'binding_key': b.key, 'run_id': b.run_id,
        'action': b.action, 'credential_fingerprint': request.credential.fingerprint,
        'progress_key': p.key, 'journal_sha256': core.digest(p.journal),
        'response_sha256': [core.digest(x) for x in p.responses],
        'sender_closed_at': closed.wall, 'sender_closed_monotonic': closed.monotonic,
        'observed_at': now, 'observed_monotonic': mono, 'observations': rows,
        'execution_authorized': False})
    need(0 < len(raw) <= MAX_ORIGINAL, 'FINISH_OBSERVATION_SIZE_INVALID')
    return raw


@dataclass(frozen=True)
class DecodedObservation:
    request: ReadRequest = field(repr=False)
    original: bytes = field(repr=False)
    observations: tuple = field(repr=False)
    observed_at: float
    original_sha256: str
    execution_authorized: bool = field(default=False, init=False)


def decode_original(raw, request, closed, *, progress, reconciler, now, mono):
    """Strict interpretation, NOT authentication of arbitrary stored JSON.

    Authoritative use is only on bytes generated in this invocation after fresh
    reads and scope authentication. Standalone decoding proves no live origin.
    """
    try:
        context_checked(request, closed, progress, now, mono)
        need(type(raw) is bytes and 0 < len(raw) <= MAX_ORIGINAL,
             'FINISH_OBSERVATION_SIZE_INVALID')
        d = json.loads(raw.decode('ascii'), object_pairs_hook=_unique,
                       parse_constant=lambda _: need(False, 'FINISH_OBSERVATION_JSON_INVALID'))
        need(type(d) is dict and set(d) == KEYS and d['schema'] == SCHEMA
             and d['execution_authorized'] is False, 'FINISH_OBSERVATION_FIELDS_INVALID')
        need(all(core.finite(d[k]) for k in ('observed_at', 'observed_monotonic',
             'sender_closed_at', 'sender_closed_monotonic'))
             and closed.wall <= d['observed_at'] <= now
             and closed.monotonic <= d['observed_monotonic'] <= mono,
             'FINISH_OBSERVATION_TIME_INVALID')
        rows = d['observations']
        need(type(rows) is list and len(rows) == len(core.objects_for(request.binding)))
        observations = []
        for key, row in zip(core.objects_for(request.binding), rows):
            need(type(row) is dict and set(row) == {'key', 'status', 'body_base64'}
                 and type(row['key']) is list and row['key'] == list(key)
                 and type(row['status']) is int and row['status'] == 200
                 and type(row['body_base64']) is str)
            body = base64.b64decode(row['body_base64'], validate=True)
            need(0 < len(body) <= 1024 * 1024)
            obj = object_decode(body)
            need(core.identity(obj) == key, 'FINISH_OBSERVATION_OBJECT_SCOPE')
            _rollout(body)
            observations.append(Observation(key, 200, body))
        observations = tuple(observations)
        need(raw == original_bytes(request, closed, observations,
             now=d['observed_at'], mono=d['observed_monotonic']),
             'FINISH_OBSERVATION_CONTEXT_MISMATCH')
        reconciler.check(request.binding, request.progress, tuple(o.raw for o in observations))
        need(progress() == request.progress, 'FINISH_OBSERVATION_PROGRESS_CHANGED')
        return DecodedObservation(request, raw, observations, d['observed_at'], core.digest(raw))
    except FinishObservationError:
        raise
    except Exception:
        raise FinishObservationError('FINISH_OBSERVATION_DECODE_FAILED') from None


def persist(root, binding, raw):
    """Private native original, 0600/create-once/fsync; not a public diagnostic.

    Bodies may contain sensitive application configuration. Never print/upload
    them to public GitHub. Failure preserves partial bytes and never releases a
    claim. No file reader here can turn persisted bytes into completion authority.
    """
    fd = None
    try:
        core.binding_checked(binding)
        need(type(raw) is bytes and 0 < len(raw) <= MAX_ORIGINAL)
        path, fd = store.root_open(root)
        name = binding.key + '.finish-observation.json'
        expected = store.create_file(fd, name, raw)
        need(store.inode(store.directory(fd)) == store.inode(path.lstat()))
        store.file_check(fd, name, raw, expected)
    except Exception:
        raise FinishObservationError('FINISH_OBSERVATION_PERSIST_FAILED') from None
    finally:
        if fd is not None:
            os.close(fd)


@dataclass(frozen=True)
class FinishObservationConfig:
    """Reviewed code adoption of an EXISTING scope, never a new automatic GO.

    Integrator must verify that scope_grant.original_pin actually permits ending
    this RUN's exclusive work after precisely FOLLOWUPS. If further exclusive
    work remains, do not select this policy. No default from deployment approval.
    """
    scope_grant: AdoptedGrant = field(repr=False)
    readers: object = field(repr=False)
    required_followups: tuple

    def check(self, binding):
        from delivery_runtime_site import SourceReaders
        need(type(self.scope_grant) is AdoptedGrant and self.scope_grant.binding == binding
             and type(self.readers) is SourceReaders and self.required_followups == FOLLOWUPS
             and type(self.required_followups) is tuple, 'FINISH_OBSERVATION_SCOPE_INVALID')
        self.readers.check()

    def bind(self, binding, credential, progress, *, wall, monotonic):
        self.check(binding)
        return ReadbackFinishAuthority(self, credential, progress, wall=wall, monotonic=monotonic)


class ReadbackFinishAuthority:
    """Fresh scoped authority + actual observation; never finish from raw alone."""
    def __init__(self, config, credential, progress, *, wall=time.time, monotonic=time.monotonic):
        self.resolver = self.scope = None
        try:
            self.config, self.credential, self.progress = config, credential, progress
            self.wall, self.monotonic, self.used = wall, monotonic, False
            self._last = self._anchor = None
            self.resolver = CodeAdoptedGrantResolver(grant=config.scope_grant,
                control_reader=config.readers.control, credential=credential, progress=progress,
                wall=wall, monotonic=monotonic)
            self.scope = ReviewedApprovalAdapter(reader=config.readers.original,
                pin=config.scope_grant.original_pin, resolver=self.resolver,
                binding=config.scope_grant.binding, credential=credential, progress=progress,
                wall=wall, monotonic=monotonic)
        except BaseException:
            self.close()
            raise

    def __call__(self, request):
        raise FinishObservationError('FINISH_OBSERVATION_LIVE_INPUT_REQUIRED')

    def clock(self, request, closed):
        wall, mono = self.wall(), self.monotonic()
        need(core.finite(wall) and core.finite(mono) and (self._last is None or
             wall >= self._last[0] and mono >= self._last[1]), 'FINISH_OBSERVATION_CLOCK_INVALID')
        self._last = wall, mono
        if self._anchor is None:
            # Anchor to the session's actual effective-clock close event, not
            # a new post-close wall baseline (the wall clock may have stalled).
            need(type(closed) is SenderClosed and core.finite(closed.wall)
                 and core.finite(closed.monotonic), 'FINISH_OBSERVATION_CLOSE_REQUIRED')
            self._anchor = closed.wall, closed.monotonic
        now = max(wall, self._anchor[0] + mono - self._anchor[1])
        context_checked(request, closed, self.progress, now, mono)
        g = self.config.scope_grant
        need(request.binding == g.binding and request.credential is self.credential
             and g.starts_at <= now < g.expires_at, 'FINISH_OBSERVATION_SCOPE_EXPIRED')
        return now, mono

    def finish_observed(self, request, closed, object_provider, sink):
        need(not self.used and self.scope is not None, 'FINISH_OBSERVATION_CONSUMED')
        self.used = True  # Also consume invalid/failed invocations; no retry.
        try:
            self.clock(request, closed)
            need(type(object_provider) is ObjectProvider and callable(sink)
                 and object_provider.reader.credential is self.credential
                 and object_provider.progress is self.progress, 'FINISH_OBSERVATION_PROVIDER_INVALID')
            before = self.scope(request)  # Existing original/control, twice fresh.
            self.clock(request, closed)
            previous = object_provider.last_observations
            result = object_provider.objects(request)  # Exact 6/9 fresh GETs after close.
            now, mono = self.clock(request, closed)
            observations = object_provider.last_observations
            from delivery_verifier import ObjectEvidence
            need(type(result) is ObjectEvidence and result.request is request
                 and result.progress_key == request.progress.key and observations is not previous,
                 'FINISH_OBSERVATION_FRESH_READ_REQUIRED')
            raw = original_bytes(request, closed, observations, now=now, mono=mono)
            record = decode_original(raw, request, closed, progress=self.progress,
                reconciler=object_provider.reconciler, now=now, mono=mono)
            sink(request.binding, raw)  # Required create-once custody BEFORE release.
            self.clock(request, closed)
            after = self.scope(request)  # Withdrawal/expiry during read/write denies.
            self.clock(request, closed)
            need(before == after and record.request is request,
                 'FINISH_OBSERVATION_AUTHORITY_CHANGED')
            return CompletionEvidence(request, after.provenance, request.progress.key,
                                      'EXCLUSIVE_ACCESS_ENDED')
        except FinishObservationError:
            raise
        except Exception:
            raise FinishObservationError('FINISH_OBSERVATION_FAILED') from None

    def close(self):
        for obj in (self.scope, self.resolver):
            if obj is not None:
                obj.close()
        self.scope = self.resolver = self.config = self.credential = self.progress = None
