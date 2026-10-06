"""Deployment mutation core; deliberately not a runtime CLI or credential issuer.

Integration must authenticate the client, validate immutable evidence/approval,
hold the execution lock and provide durable checkpoints. This module implements
only the bounded GET/JSON-Patch state machine. See docs/deploy-adapter-core.md.
"""
import copy
import hashlib
import json
import math
import re
import time
from dataclasses import dataclass

from delivery_gate import SERVICES, NAMESPACE, REGISTRY_PREFIX


class Stop(Exception):
    """Only static error codes may be recorded; never transport exception text."""


class Rejected(Exception):
    """Transport contract: server definitively rejected PATCH without mutation.

    A timeout, disconnect, invalid success response or 5xx MUST NOT use this type.
    """


@dataclass(frozen=True)
class Target:
    service: str
    uid: str
    previous_image: str
    image: str


def need(value, code):
    if not value:
        raise Stop(code)


def targets_checked(targets):
    targets = tuple(targets)
    need(len(targets) == 2 and all(isinstance(t, Target) for t in targets),
         'TARGETS_INVALID')
    need(tuple(t.service for t in targets) == SERVICES, 'TARGET_SCOPE_INVALID')
    for t in targets:
        need(isinstance(t.uid, str) and re.fullmatch(
            r'[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}', t.uid), 'UID_INVALID')
        pattern = re.escape(REGISTRY_PREFIX + t.service) + r'@sha256:[0-9a-f]{64}'
        need(all(isinstance(i, str) and re.fullmatch(pattern, i)
                 for i in (t.previous_image, t.image)), 'IMAGE_INVALID')
        need(t.previous_image != t.image, 'NO_IMAGE_CHANGE')
    need(len({t.uid for t in targets}) == 2, 'DUPLICATE_UID')
    return targets


def inspect(obj, target, image, expected_spec=None, generation=None):
    """Check identity and complete spec; never serialize env/Secret data to receipts."""
    try:
        meta, spec = obj['metadata'], obj['spec']
        need(obj['apiVersion'] == 'apps/v1' and obj['kind'] == 'Deployment',
             'OBJECT_KIND_INVALID')
        need(meta['name'] == target.service and meta['namespace'] == NAMESPACE
             and meta['uid'] == target.uid, 'TARGET_IDENTITY_DRIFT')
        need(not meta.get('deletionTimestamp'), 'TARGET_DELETING')
        # resourceVersion is an opaque concurrency token, not an integer counter.
        rv = meta['resourceVersion']
        need(isinstance(rv, str) and 0 < len(rv) <= 200 and
             all(ord(c) >= 32 for c in rv), 'RESOURCE_VERSION_INVALID')
        need(type(meta['generation']) is int and meta['generation'] > 0,
             'GENERATION_INVALID')
        need(type(spec['replicas']) is int and spec['replicas'] > 0, 'REPLICAS_INVALID')
        need(spec.get('paused', False) is False, 'DEPLOYMENT_PAUSED')
        containers = spec['template']['spec']['containers']
        need(isinstance(containers, list) and containers, 'CONTAINERS_INVALID')
        names = [c['name'] for c in containers]
        need(len(set(names)) == len(names) and names.count(target.service) == 1,
             'CONTAINER_IDENTITY_INVALID')
        index = names.index(target.service)
        need(containers[index]['image'] == image, 'IMAGE_DRIFT')
        if expected_spec is not None:
            need(spec == expected_spec, 'SPEC_DRIFT')
        if generation is not None:
            need(meta['generation'] == generation, 'GENERATION_DRIFT')
        return index
    except (KeyError, TypeError, ValueError, AttributeError):
        raise Stop('OBJECT_INVALID') from None


def ready(obj):
    """Deployment readiness only; not Pod identity, TLS, DB health or E2E."""
    status = obj.get('status', {})
    need(isinstance(status, dict), 'STATUS_INVALID')
    observed = status.get('observedGeneration')
    if type(observed) is not int or observed < obj['metadata']['generation']:
        return False
    conditions = status.get('conditions', [])
    need(isinstance(conditions, list) and all(isinstance(c, dict) for c in conditions),
         'CONDITIONS_INVALID')
    need(not any(c.get('type') == 'Progressing' and c.get('status') == 'False'
                 and c.get('reason') == 'ProgressDeadlineExceeded' for c in conditions),
         'PROGRESS_DEADLINE_EXCEEDED')
    for field in ('replicas', 'updatedReplicas', 'readyReplicas', 'availableReplicas'):
        if type(status.get(field)) is not int or status[field] != obj['spec']['replicas']:
            return False
    for field in ('unavailableReplicas', 'terminatingReplicas'):
        if type(status.get(field, 0)) is not int or status.get(field, 0) != 0:
            return False
    return True


def summary(obj, target):
    meta, spec = obj['metadata'], obj['spec']
    index = next(i for i, c in enumerate(spec['template']['spec']['containers'])
                 if c['name'] == target.service)
    return {'uid': meta['uid'], 'resource_version': meta['resourceVersion'],
            'generation': meta['generation'], 'replicas': spec['replicas'],
            'image': spec['template']['spec']['containers'][index]['image'],
            'spec_sha256': hashlib.sha256(json.dumps(
                spec, sort_keys=True, separators=(',', ':')).encode()).hexdigest()}


def image_patch(obj, target, old_image, new_image):
    index = inspect(obj, target, old_image)
    base = f'/spec/template/spec/containers/{index}'
    return [
        {'op': 'test', 'path': '/metadata/uid', 'value': target.uid},
        {'op': 'test', 'path': '/metadata/resourceVersion',
         'value': obj['metadata']['resourceVersion']},
        {'op': 'test', 'path': base + '/name', 'value': target.service},
        {'op': 'test', 'path': base + '/image', 'value': old_image},
        {'op': 'replace', 'path': base + '/image', 'value': new_image},
    ]


class Adapter:
    """Single-use state machine over a caller-supplied, bounded transport.

    client.get(name) and client.patch(name, json_patch) MUST address only
    /apis/apps/v1/namespaces/fds-app/deployments/{name}. They must return decoded
    objects and enforce finite request deadlines; no retries of a PATCH.
    authorize(action) must freshly enforce reviewed inputs, lock ownership,
    execution identity and authorization expiry; rollback is a separate action.
    checkpoint(receipt) must durably save or raise *before* returning.
    remaining_seconds() must derive remaining deploy authorization from the
    verified expiry, not token TTL. The start budget is 2 * timeout + margin;
    margin must cover bounded requests/checkpoints in the integrating caller.
    credential_remaining_seconds() must derive current token lifetime from
    verified credential metadata, never from the requested TTL alone. Both
    windows must cover the start budget. Each GET/PATCH also rejects expired
    or unverifiable credentials, including rollback. The transport must bound
    individual requests by the remaining credential/approval lifetime.
    Authority denial stops all further writes, including automatic rollback.
    """

    def __init__(self, client, authorize, checkpoint, *, remaining_seconds,
                 credential_remaining_seconds,
                 window_margin=30, timeout=300, interval=2,
                 clock=time.monotonic, sleep=time.sleep):
        need(type(timeout) is int and 1 <= timeout <= 3600, 'TIMEOUT_INVALID')
        need(type(interval) is int and 1 <= interval <= timeout, 'INTERVAL_INVALID')
        need(callable(remaining_seconds), 'AUTH_WINDOW_PROVIDER_REQUIRED')
        need(type(window_margin) is int and window_margin > 0, 'WINDOW_MARGIN_INVALID')
        need(callable(credential_remaining_seconds), 'CREDENTIAL_WINDOW_PROVIDER_REQUIRED')
        self.credential_remaining_seconds = credential_remaining_seconds
        self.remaining_seconds = remaining_seconds
        self.required_window = 2 * timeout + window_margin
        self.client, self.authorize, self.checkpoint = client, authorize, checkpoint
        self.timeout, self.interval, self.clock, self.sleep = timeout, interval, clock, sleep
        self.used = False
        self.receipt = {'schema': 'P1-CICD-deploy-core-1', 'ROLLOUT': 'NOT_EXECUTED',
                        'ROLLBACK': 'NOT_EXECUTED', 'DELIVERY_GATE': 'NOT_VERIFIED',
                        'HEALTH_E2E': 'NOT_VERIFIED', 'POD_IMAGE_ID': 'NOT_VERIFIED',
                        'images': {}, 'error_code': 'NONE'}

    def save(self):
        try:
            self.checkpoint(copy.deepcopy(self.receipt))
        except Exception:
            raise Stop('CHECKPOINT_FAILED') from None

    def auth(self, action):
        try:
            need(self.authorize(action) is True, 'AUTHORIZATION_DENIED')
            need(self.credential_window() > 0, 'AUTHORIZATION_DENIED')
        except Exception:
            raise Stop('AUTHORIZATION_DENIED') from None

    def credential_window(self):
        try:
            value = self.credential_remaining_seconds()
        except Exception:
            raise Stop('CREDENTIAL_WINDOW_UNAVAILABLE') from None
        need(type(value) in (int, float) and math.isfinite(value),
             'CREDENTIAL_WINDOW_INVALID')
        return value

    def check_start_window(self):
        try:
            remaining = self.remaining_seconds()
        except Exception:
            raise Stop('AUTH_WINDOW_UNAVAILABLE') from None
        need(type(remaining) in (int, float) and math.isfinite(remaining),
             'AUTH_WINDOW_INVALID')
        need(remaining >= self.required_window, 'AUTH_WINDOW_TOO_SHORT')
        need(self.credential_window() >= self.required_window, 'CREDENTIAL_WINDOW_TOO_SHORT')

    def get(self, target, action):
        self.auth(action)
        try:
            return self.client.get(target.service)
        except Exception:
            raise Stop('GET_FAILED') from None

    def wait_ready(self, target, image, spec, generation, action):
        deadline = self.clock() + self.timeout
        while self.clock() < deadline:
            obj = self.get(target, action)
            inspect(obj, target, image, spec, generation)
            if self.clock() < deadline and ready(obj):
                return obj
            self.sleep(min(self.interval, max(0, deadline - self.clock())))
        raise Stop('READINESS_TIMEOUT')

    def mutate(self, target, before, old, new, action):
        item = self.receipt['images'][target.service]
        item[action] = 'PATCH_PENDING'
        self.save()
        try:
            self.auth(action)
            if action == 'deploy' and not any(
                    entry['deploy'] in ('APPLIED', 'READY')
                    for entry in self.receipt['images'].values()):
                self.check_start_window()
        except Stop as exc:
            item[action] = ('NOT_SENT_AUTH_DENIED' if str(exc) == 'AUTHORIZATION_DENIED'
                            else 'NOT_SENT_CREDENTIAL_WINDOW' if str(exc).startswith('CREDENTIAL_WINDOW_')
                            else 'NOT_SENT_AUTH_WINDOW')
            self.save()
            raise
        try:
            after = self.client.patch(target.service, image_patch(before, target, old, new))
        except Rejected:
            item[action] = 'REJECTED'
            self.save()
            raise Stop('PATCH_REJECTED') from None
        except Exception:
            item[action] = 'UNKNOWN'
            self.save()
            raise Stop('PATCH_OUTCOME_UNKNOWN') from None
        # An untrustworthy response cannot prove what the server actually committed.
        spec = copy.deepcopy(before['spec'])
        index = inspect(before, target, old)
        spec['template']['spec']['containers'][index]['image'] = new
        generation = before['metadata']['generation'] + 1
        try:
            inspect(after, target, new, spec, generation)
        except Stop:
            item[action] = 'UNKNOWN'
            self.save()
            raise Stop('PATCH_OUTCOME_UNKNOWN') from None
        item[action] = 'APPLIED'
        item[action + '_after'] = summary(after, target)
        self.save()
        return spec, generation

    def run(self, targets, *, rollback_on_failure=False):
        need(not self.used, 'ADAPTER_ALREADY_USED')
        self.used = True
        targets = targets_checked(targets)
        need(type(rollback_on_failure) is bool, 'ROLLBACK_OPTION_INVALID')
        baseline, applied = {}, {}
        try:
            # Check BOTH before any mutation. A bad second target cannot partially deploy.
            for target in targets:
                obj = self.get(target, 'deploy')
                inspect(obj, target, target.previous_image)
                need(ready(obj), 'BASELINE_NOT_READY')
                baseline[target.service] = copy.deepcopy(obj)
                self.receipt['images'][target.service] = {
                    'before': summary(obj, target), 'deploy': 'NOT_EXECUTED',
                    'rollback': 'NOT_EXECUTED'}
            self.check_start_window()
            self.receipt['ROLLOUT'] = 'IN_PROGRESS'
            self.save()
            for target in targets:
                initial = baseline[target.service]
                current = self.get(target, 'deploy')
                inspect(current, target, target.previous_image, initial['spec'],
                        initial['metadata']['generation'])
                state = self.mutate(target, current, target.previous_image, target.image, 'deploy')
                applied[target.service] = state
                obj = self.wait_ready(target, target.image, *state, 'deploy')
                self.receipt['images'][target.service]['deploy'] = 'READY'
                self.receipt['images'][target.service]['ready'] = summary(obj, target)
                self.save()
            # Re-check the first target after the second rollout, too.
            for target in targets:
                obj = self.get(target, 'deploy')
                inspect(obj, target, target.image, *applied[target.service])
                need(ready(obj), 'FINAL_READINESS_LOST')
            self.receipt['ROLLOUT'] = 'READY'
            self.save()
        except Stop as exc:
            code = str(exc)
            self.receipt['error_code'] = code
            self.receipt['ROLLOUT'] = 'FAILED'
            if code in ('PATCH_OUTCOME_UNKNOWN', 'CHECKPOINT_FAILED', 'AUTHORIZATION_DENIED'):
                # Authority loss must never trigger a recovery write, even if
                # rollback authority independently remains valid.
                self.receipt['ROLLBACK'] = 'MANUAL_RECONCILIATION_REQUIRED'
            elif rollback_on_failure and applied:
                self.rollback(targets, baseline, applied)
            elif applied:
                self.receipt['ROLLBACK'] = 'NOT_AUTHORIZED_OR_NOT_REQUESTED'
            # Failure to persist is fatal, never reported as successful recovery.
            self.save()
        return copy.deepcopy(self.receipt)

    def rollback(self, targets, baseline, applied):
        self.receipt['ROLLBACK'] = 'IN_PROGRESS'
        self.save()
        restored = {}
        try:
            for target in reversed(targets):
                if target.service not in applied:
                    continue
                current = self.get(target, 'rollback')
                inspect(current, target, target.image, *applied[target.service])
                state = self.mutate(target, current, target.image, target.previous_image, 'rollback')
                restored[target.service] = state
                self.wait_ready(target, target.previous_image, *state, 'rollback')
                self.receipt['images'][target.service]['rollback'] = 'READY'
                self.save()
            for target in targets:
                if target.service in restored:
                    spec, generation = restored[target.service]
                else:
                    spec = baseline[target.service]['spec']
                    generation = baseline[target.service]['metadata']['generation']
                current = self.get(target, 'rollback')
                inspect(current, target, target.previous_image, spec, generation)
                need(ready(current), 'ROLLBACK_READINESS_LOST')
            self.receipt['ROLLBACK'] = 'READY_HEALTH_NOT_VERIFIED'
        except Stop as exc:
            self.receipt['ROLLBACK'] = 'MANUAL_RECONCILIATION_REQUIRED'
            self.receipt['rollback_error_code'] = str(exc)
        self.save()
