"""P2-only bounded apply core. No CLI, network, credentials or P1 imports.

The integrating caller must supply trusted render validation, authorization,
exclusive durable claim/checkpoints and an authenticated, deadline-bound sender.
These providers are NOT implemented here. See docs/phase2/delivery-adapter.md.
"""
import copy
import hashlib
import json
import math
import re
import time
from dataclasses import asdict, dataclass
from urllib.parse import urlsplit

NAMESPACE = 'fds-app'  # rosa-app is the producer OVERLAY name, not a namespace.
SERVICES = ('transaction-api', 'fds-engine')
OBJECTS = (('v1', 'Service', 'transaction-api'), ('v1', 'Service', 'fds-engine'),
           ('apps/v1', 'Deployment', 'transaction-api'),
           ('apps/v1', 'Deployment', 'fds-engine'),
           ('route.openshift.io/v1', 'Route', 'transaction-api-api'),
           ('route.openshift.io/v1', 'Route', 'transaction-api-readyz'))
TLS = {'termination': 'edge', 'insecureEdgeTerminationPolicy': 'Redirect'}
HEX = re.compile(r'[0-9a-f]{64}')
HOST = re.compile(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?'
                  r'(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+')


class Stop(Exception):
    """Internal static codes only. Provider exception messages are never copied."""


class Rejected(Exception):
    """Sender guarantees this request did NOT mutate anything (e.g. SSA 409).

    Timeout, disconnect, 5xx, unreadable response or uncertain outcome MUST NOT
    use this exception. Exception text is never propagated to the receipt.
    """


def need(ok, code):
    if not ok:
        raise Stop(code)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      allow_nan=False).encode('utf-8')


def finite(value):
    return type(value) in (float, int) and math.isfinite(value)


def host(value):
    return (type(value) is str and len(value) <= 253 and HOST.fullmatch(value)
            and not value.endswith('.invalid') and not value.replace('.', '').isdigit())


@dataclass(frozen=True)
class Binding:
    run_id: str
    action: str  # deploy or restore; restore MUST bind a separately approved render.
    api_origin: str
    cluster_id: str
    namespace_uid: str
    ca_sha256: str
    source_commit: str
    contract_sha256: str
    rendered_sha256: str
    platform_receipt_sha256: str
    supply_receipt_sha256: str
    route_host: str
    images: tuple  # ((deployment, exact approved image@sha256), ...) in SERVICES order

    @property
    def key(self):
        return digest(encoded(asdict(self)))


@dataclass(frozen=True)
class Lease:
    """Trusted authorizer output, NEVER a receipt loaded directly from user JSON.

    Returning this means the caller freshly verified target/namespace UID, CA,
    token issuer/audience/minimal RBAC, clean source, supply/platform receipts,
    action approval and exclusive durable run claim against Binding.key.
    Actual token validity MUST be verified, not inferred from requested TTL.
    """
    binding_key: str
    credential_fingerprint: str
    requested_seconds: int
    token_expires_at: float
    approval_expires_at: float


@dataclass(frozen=True)
class Request:
    """Sender must enforce this deadline and origin; no redirect/retry/proxy drift.

    Return a decoded API object after authenticated TLS verification. No token
    is carried here. The sender/authorizer must bind the SAME credential.
    """
    binding_key: str
    credential_fingerprint: str
    api_origin: str
    ca_sha256: str
    method: str
    path: str
    content_type: str
    body: bytes
    timeout_seconds: float


def binding_checked(b):
    need(type(b) is Binding, 'BINDING_INVALID')
    need(b.action in ('deploy', 'restore'), 'ACTION_INVALID')
    for value in (b.run_id, b.cluster_id, b.namespace_uid):
        need(type(value) is str and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}', value),
             'IDENTITY_INVALID')
    for value in (b.ca_sha256, b.contract_sha256, b.rendered_sha256,
                  b.platform_receipt_sha256, b.supply_receipt_sha256):
        need(type(value) is str and HEX.fullmatch(value), 'HASH_INVALID')
    need(type(b.source_commit) is str and re.fullmatch(r'[0-9a-f]{40}', b.source_commit),
         'SOURCE_INVALID')
    need(type(b.api_origin) is str, 'API_INVALID')
    u = urlsplit(b.api_origin)
    need(u.scheme == 'https' and host(u.hostname) and u.port in (None, 443, 6443)
         and not (u.username or u.password or u.path or u.query or u.fragment)
         and b.api_origin == f'https://{u.hostname}' + (f':{u.port}' if u.port else ''),
         'API_INVALID')
    need(host(b.route_host), 'ROUTE_HOST_INVALID')
    need(type(b.images) is tuple and len(b.images) == 2, 'IMAGES_INVALID')
    for name, pair in zip(SERVICES, b.images):
        need(type(pair) is tuple and len(pair) == 2 and pair[0] == name,
             'IMAGES_INVALID')
        image = pair[1]
        need(type(image) is str and re.fullmatch(r'[^\s@]+@sha256:[0-9a-f]{64}', image)
             and '.invalid' not in image and '__' not in image, 'IMAGES_INVALID')


def identity(obj):
    return obj['apiVersion'], obj['kind'], obj['metadata']['name']


def scope_checked(obj, b, *, response=False):
    need(type(obj) is dict and identity(obj) in OBJECTS, 'OBJECT_SCOPE_INVALID')
    m = obj['metadata']
    need(m['namespace'] == NAMESPACE and not m.get('deletionTimestamp'), 'NAMESPACE_INVALID')
    if not response:
        need(set(obj) <= {'apiVersion', 'kind', 'metadata', 'spec'}
             and set(m) <= {'name', 'namespace', 'labels', 'annotations'}, 'RENDER_METADATA_INVALID')
    if obj['kind'] == 'Route':
        need(obj['spec']['host'] == b.route_host and obj['spec']['tls'] == TLS,
             'ROUTE_CONTRACT_MISMATCH')
    if obj['kind'] == 'Deployment':
        pod = obj['spec']['template']['spec']
        containers = pod['containers']
        name = m['name']
        need(type(containers) is list and len(containers) == 1
             and containers[0]['name'] == name
             and containers[0]['image'] == dict(b.images)[name]
             and not pod.get('initContainers') and not pod.get('ephemeralContainers'),
             'IMAGE_CONTRACT_MISMATCH')
        need(pod['serviceAccountName'] == name + '-sa'
             and pod['automountServiceAccountToken'] is False, 'POD_IDENTITY_INVALID')
        labels = obj['spec']['template']['metadata'].get('labels', {})
        need(labels.get('onprem-egress') == ('true' if name == 'transaction-api' else None),
             'EGRESS_LABEL_INVALID')


def prepare(b, rendered, contract, validator):
    """Call a reviewed producer-validator bridge on exact raw bytes.

    validator(raw_render, raw_contract) returns (producer_report, parsed_docs).
    It must parse THESE bytes and run #43 check_delivery_render without allowing
    placeholders. It cannot load an old PASS JSON. Provider is trusted code;
    this is not proof of source authenticity/approval (authorizer handles that).
    """
    try:
        binding_checked(b)
        need(all(type(raw) is bytes and 0 < len(raw) <= 1024 * 1024
                 for raw in (rendered, contract)), 'INPUT_SIZE_INVALID')
        need(digest(rendered) == b.rendered_sha256 and digest(contract) == b.contract_sha256,
             'INPUT_HASH_MISMATCH')
        # Provider-specific messages, including YAML snippets, must not escape.
        try:
            report, docs = validator(rendered, contract)
        except Exception:
            raise Stop('RENDER_VALIDATOR_FAILED') from None
        need(report['result'] == 'PASS' and report['mode'] == 'app'
             and report['namespace'] == NAMESPACE and report['placeholders_allowed'] is False
             and report['placeholders'] == [] and report['errors'] == []
             and report['approved_route_host'] == b.route_host
             and report['rendered_sha256'] == b.rendered_sha256
             and report['contract_sha256'] == b.contract_sha256, 'RENDER_CHECK_FAILED')
        need(type(docs) in (list, tuple) and len(docs) == 6, 'OBJECT_SET_INVALID')
        by_id = {}
        for obj in docs:
            scope_checked(obj, b)
            need(identity(obj) not in by_id, 'DUPLICATE_OBJECT')
            by_id[identity(obj)] = encoded(obj)
        need(set(by_id) == set(OBJECTS), 'OBJECT_SET_INVALID')
        # Immutable payloads prevent mutation by caller/checkpoint after validation.
        return tuple(by_id[key] for key in OBJECTS)
    except Stop:
        raise
    except Exception:
        raise Stop('INPUT_INVALID') from None


def path_for(key):
    need(key in OBJECTS, 'OBJECT_SCOPE_INVALID')
    api, kind, name = key
    root = '/api/v1' if api == 'v1' else '/apis/' + api
    plural = {'Service': 'services', 'Deployment': 'deployments', 'Route': 'routes'}[kind]
    return f'{root}/namespaces/{NAMESPACE}/{plural}/{name}'


class Adapter:
    """Single-use core. Provider contracts are mandatory, see module documentation.

    Checkpoint must persist atomically and durably BEFORE returning. Failure
    stops all further writes. Authorizer must hold a cross-process durable claim;
    constructing a new Adapter does NOT authorize replay of an interrupted run.
    """
    def __init__(self, *, authorize, send, checkpoint, wall=time.time,
                 monotonic=time.monotonic, sleep=time.sleep):
        self.authorize, self.send, self.checkpoint = authorize, send, checkpoint
        self.wall, self.monotonic, self.sleep = wall, monotonic, sleep
        self.used = False
        self.active = False
        self.payloads = {}
        self.lease = None
        self.last_wall = self.last_mono = None
        self.start_mono = self.initial_window = None
        self.receipt = {'schema': 'P2-CICD-apply-core-1', 'status': 'NOT_STARTED',
                        'binding_key': None, 'acknowledged': [], 'pending': None,
                        'rollout': [], 'error_code': None, 'delivery_gate': 'NOT_VERIFIED',
                        'health_e2e': 'NOT_VERIFIED', 'pod_image_id': 'NOT_VERIFIED'}

    def save(self):
        try:
            self.checkpoint(copy.deepcopy(self.receipt))
        except Exception:
            raise Stop('CHECKPOINT_FAILED') from None

    def tick(self):
        try:
            value = self.monotonic()
        except Exception:
            raise Stop('CLOCK_UNAVAILABLE') from None
        need(finite(value), 'CLOCK_INVALID')
        return value

    def window(self, b):
        try:
            try:
                lease = self.authorize(b)
            except Exception:
                raise Stop('AUTHORIZATION_DENIED') from None
            need(type(lease) is Lease and lease.binding_key == b.key, 'AUTHORIZATION_DENIED')
            need(type(lease.requested_seconds) is int and lease.requested_seconds == 900,
                 'TOKEN_REQUEST_INVALID')
            need(type(lease.credential_fingerprint) is str
                 and HEX.fullmatch(lease.credential_fingerprint), 'CREDENTIAL_INVALID')
            need(finite(lease.token_expires_at) and finite(lease.approval_expires_at),
                 'AUTH_WINDOW_INVALID')
            try:
                w, m = self.wall(), self.monotonic()
            except Exception:
                raise Stop('CLOCK_UNAVAILABLE') from None
            need(finite(w) and finite(m), 'CLOCK_INVALID')
            if self.lease is None:
                self.lease, self.start_mono = lease, m
                self.initial_window = min(lease.token_expires_at, lease.approval_expires_at) - w
            need(lease == self.lease, 'AUTHORITY_CHANGED')  # no automatic renewal
            need((self.last_wall is None or w >= self.last_wall)
                 and (self.last_mono is None or m >= self.last_mono), 'CLOCK_REVERSED')
            self.last_wall, self.last_mono = w, m
            left = min(lease.token_expires_at - w, lease.approval_expires_at - w,
                       self.initial_window - (m - self.start_mono))
            need(left > 0, 'AUTH_EXPIRED')
            return left
        except Stop:
            raise
        except Exception:
            raise Stop('AUTHORIZATION_DENIED') from None

    def request(self, b, key, method, body=b'', *, first=False, timeout=10):
        need(self.active, 'SESSION_INACTIVE')
        need(method in ('GET', 'PATCH'), 'METHOD_INVALID')
        need((method == 'PATCH' and key in self.payloads and body == self.payloads[key])
             or (method == 'GET' and key in OBJECTS and key[1] == 'Deployment' and body == b''),
             'REQUEST_SCOPE_INVALID')
        left = self.window(b)
        if first:
            need(left >= 660, 'FIRST_WRITE_WINDOW_TOO_SHORT')
        path = path_for(key)
        if method == 'PATCH':
            path += '?fieldManager=fds-deployer'  # no force/prune/delete
        req = Request(b.key, self.lease.credential_fingerprint, b.api_origin, b.ca_sha256,
                      method, path, 'application/apply-patch+yaml' if method == 'PATCH' else '',
                      body, min(timeout, left))
        sent_at = self.last_mono
        try:
            obj = self.send(req)
        except Rejected:
            if method == 'PATCH':
                self.receipt['pending'] = None
            raise Stop('API_REJECTED') from None
        except Exception:
            raise Stop('WRITE_OUTCOME_UNKNOWN' if method == 'PATCH' else 'READ_FAILED') from None
        elapsed = self.tick() - sent_at
        need(0 <= elapsed < req.timeout_seconds,
             'WRITE_OUTCOME_UNKNOWN' if method == 'PATCH' else 'READ_DEADLINE_EXCEEDED')
        try:
            obj = copy.deepcopy(obj)
            scope_checked(obj, b, response=True)
            need(identity(obj) == key, 'RESPONSE_IDENTITY_MISMATCH')
            meta = obj['metadata']
            need(type(meta['uid']) is str and 0 < len(meta['uid']) <= 128
                 and type(meta['resourceVersion']) is str and 0 < len(meta['resourceVersion']) <= 200,
                 'RESPONSE_IDENTITY_MISSING')
            return obj
        except Exception:
            raise Stop('WRITE_RESPONSE_INVALID' if method == 'PATCH' else 'READ_RESPONSE_INVALID') from None

    def wait_ready(self, b, key, applied):
        start = self.tick()
        desired = applied['spec']['replicas']
        generation = applied['metadata']['generation']
        need(type(desired) is int and desired > 0 and type(generation) is int and generation > 0,
             'DEPLOYMENT_RESPONSE_INVALID')
        while True:
            left = 300 - (self.tick() - start)
            need(finite(left) and left > 0, 'ROLLOUT_TIMEOUT')
            obj = self.request(b, key, 'GET', timeout=min(10, left))
            self.window(b)
            need(self.tick() - start < 300, 'ROLLOUT_TIMEOUT')
            need(obj['metadata']['uid'] == applied['metadata']['uid']
                 and obj['metadata']['generation'] == generation
                 and obj['spec'] == applied['spec'], 'DEPLOYMENT_DRIFT')
            status = obj.get('status', {})
            need(not any(c.get('type') == 'Progressing' and c.get('status') == 'False'
                         and c.get('reason') == 'ProgressDeadlineExceeded'
                         for c in status.get('conditions', [])), 'PROGRESS_DEADLINE_EXCEEDED')
            if (type(status.get('observedGeneration')) is int
                    and status['observedGeneration'] >= generation
                    and all(type(status.get(k)) is int and status[k] == desired for k in
                            ('replicas', 'updatedReplicas', 'readyReplicas', 'availableReplicas'))
                    and all(type(status.get(k, 0)) is int and status.get(k, 0) == 0 for k in
                            ('unavailableReplicas', 'terminatingReplicas'))):
                return
            delay = min(2, self.window(b), 300 - (self.tick() - start))
            need(delay > 0, 'ROLLOUT_TIMEOUT')
            try:
                self.sleep(delay)
            except Exception:
                raise Stop('WAIT_FAILED') from None

    def run(self, b, rendered, contract, validator):
        need(not self.used, 'ADAPTER_ALREADY_USED')
        self.used = True
        try:
            payloads = prepare(b, rendered, contract, validator)
            self.payloads = dict(zip(OBJECTS, payloads))
            self.active = True
            self.receipt['binding_key'] = b.key
            self.window(b)
            self.receipt['status'] = 'PREPARED'
            self.save()
            deployments = []
            for index, (key, payload) in enumerate(zip(OBJECTS, payloads)):
                self.window(b)
                self.receipt.update(status='WRITE_PENDING', pending='/'.join(key))
                self.save()  # durable intent before any send, including the first
                obj = self.request(b, key, 'PATCH', payload, first=(index == 0))
                self.receipt['acknowledged'].append('/'.join(key))
                self.receipt.update(status='WRITE_ACKNOWLEDGED', pending=None)
                self.save()
                self.window(b)
                if key[1] == 'Deployment':
                    deployments.append((key, obj))
            for key, applied in deployments:
                self.wait_ready(b, key, applied)
                self.receipt['rollout'].append(key[2])
                self.save()
            self.receipt['status'] = 'ROLLOUT_OBSERVED'
            self.save()
        except Stop as exc:
            # Only internal Stop codes reach here; provider Stop text was sanitized.
            self.receipt.update(status='STOPPED', error_code=str(exc))
        except Exception:
            self.receipt.update(status='STOPPED', error_code='CORE_INPUT_OR_OBSERVATION_INVALID')
        if self.receipt['status'] == 'STOPPED':
            try:
                self.save()
            except Stop:
                self.receipt['checkpoint_error'] = 'CHECKPOINT_FAILED'
        self.active = False
        return copy.deepcopy(self.receipt)
