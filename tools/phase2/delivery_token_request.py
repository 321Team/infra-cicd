"""Explicit one-shot TokenRequest supplier. Import/construction never issues.

Only issue() performs the exact TLS POST. No CLI, ambient credential, retry,
renewal, file receipt, SITE default or authority adoption. Response claims are
consistency checks on a TLS-authenticated issuance, NOT JWT signature checking.
"""
import base64
import copy
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import re
import threading

import delivery_adapter as core
import delivery_crosshost_ingress as ingress
import delivery_transport as wire
from delivery_object_reader import BearerCredential


class IssuanceError(RuntimeError):
    """Fixed public error only; failed POST may still have issued a token."""


def need(ok, code='TOKEN_REQUEST_INVALID'):
    if not ok:
        raise IssuanceError(code)


def text(value):
    return type(value) is str and 0 < len(value) <= 2048 and all(32 < ord(c) < 127 for c in value)


@dataclass(frozen=True, repr=False)
class TokenTarget(ingress._NoCopy):
    namespace: str
    name: str
    uid: str
    issuer: str
    audience: str

    def check(self):
        label = r'[a-z0-9](?:[a-z0-9-]*[a-z0-9])?'
        need(type(self.namespace) is str and len(self.namespace) <= 63
             and re.fullmatch(label, self.namespace) is not None)
        need(type(self.name) is str and len(self.name) <= 253
             and all(len(p) <= 63 and re.fullmatch(label, p) for p in self.name.split('.')))
        need(all(text(v) for v in (self.uid, self.issuer, self.audience)))

    def __repr__(self): return '<TokenTarget: private code-configured target>'


@dataclass(frozen=True, repr=False)
class IssuanceReceipt(ingress._NoCopy):
    """PRIVATE response bytes include the token. Never publish this object.

    Contains issuer response, not RBAC/GO/control/authority proof. A later
    original adapter must authenticate storage and adopt its actual semantics.
    """
    credential: BearerCredential = field(repr=False)
    raw: bytes = field(repr=False)
    target: TokenTarget = field(repr=False)
    issued_at: int
    expires_at: int
    requested_seconds: int = field(default=900, init=False)

    def __repr__(self): return '<IssuanceReceipt: private in-memory response>'


def _json(raw):
    return json.loads(raw.decode('utf-8'), object_pairs_hook=wire.unique_object,
        parse_constant=lambda _: (_ for _ in ()).throw(IssuanceError('TOKEN_RESPONSE_INVALID')))


def _part(value):
    need(type(value) is str and re.fullmatch(r'[A-Za-z0-9_-]+', value) is not None,
         'TOKEN_RESPONSE_INVALID')
    raw = base64.urlsafe_b64decode(value + '=' * (-len(value) % 4))
    need(base64.urlsafe_b64encode(raw).decode().rstrip('=') == value, 'TOKEN_RESPONSE_INVALID')
    return raw


def _stamp(value):
    need(type(value) is str and re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z', value),
         'TOKEN_RESPONSE_INVALID')
    return int(datetime.strptime(value, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc).timestamp())


def _receipt(raw, target, now):
    """Internal only: raw must come from this call's pinned TLS 201 response."""
    need(type(raw) is bytes and 0 < len(raw) <= wire.MAX_BODY, 'TOKEN_RESPONSE_INVALID')
    obj = _json(raw)
    need(type(obj) is dict and obj.get('apiVersion') == 'authentication.k8s.io/v1'
         and obj.get('kind') == 'TokenRequest', 'TOKEN_RESPONSE_INVALID')
    meta, spec, status = (obj.get(k) for k in ('metadata', 'spec', 'status'))
    need(all(type(x) is dict for x in (meta, spec, status)), 'TOKEN_RESPONSE_INVALID')
    need(meta.get('name') == target.name and meta.get('namespace') == target.namespace
         and meta.get('deletionTimestamp') is None, 'TOKEN_RESPONSE_INVALID')
    created = _stamp(meta.get('creationTimestamp')); expiry = _stamp(status.get('expirationTimestamp'))
    seconds = spec.get('expirationSeconds')
    need(spec.get('audiences') == [target.audience] and type(seconds) is int
         and 0 < seconds <= 900 and spec.get('boundObjectRef') is None,
         'TOKEN_RESPONSE_INVALID')
    token = status.get('token'); ingress._token(token)
    parts = token.split('.'); need(len(parts) == 3, 'TOKEN_RESPONSE_INVALID')
    header, claims = _json(_part(parts[0])), _json(_part(parts[1]))
    need(bool(_part(parts[2])) and type(header) is dict and text(header.get('alg'))
         and header['alg'].lower() != 'none' and not header.get('crit')
         and header.get('b64', True) is True and type(claims) is dict, 'TOKEN_RESPONSE_INVALID')
    need(claims.get('iss') == target.issuer and claims.get('aud') == [target.audience]
         and claims.get('sub') == f'system:serviceaccount:{target.namespace}:{target.name}',
         'TOKEN_SUBJECT_MISMATCH')
    k = claims.get('kubernetes.io')
    need(type(k) is dict and k.get('namespace') == target.namespace
         and k.get('serviceaccount') == {'name': target.name, 'uid': target.uid}
         and not any(key in k for key in ('pod', 'secret', 'node', 'warnafter')),
         'TOKEN_SUBJECT_MISMATCH')
    iat, exp, nbf = (claims.get(k) for k in ('iat', 'exp', 'nbf'))
    need(all(type(x) is int for x in (iat, exp, nbf)) and created <= iat <= now < expiry <= exp
         and nbf == iat and 0 < exp - iat <= 900 and expiry - created == seconds,
         'TOKEN_LIFETIME_INVALID')
    return IssuanceReceipt(BearerCredential(token), raw, target, iat, expiry)


class TokenRequestIssuer(wire.Sender, ingress._NoCopy):
    """One explicit target/actor/endpoint, one attempt, original local deadline.

    The caller's existing authorization must permit this issuance. Merely
    constructing this client is not a GO. No GET/SSAR/TokenReview privileges
    are added. SA UID is checked in the returned token, not metadata.uid.
    """
    def __init__(self, binding, *, target, actor, ca_pem, endpoint_ip, deadline, monotonic, wall):
        error = None
        try:
            need(type(target) is TokenTarget and type(actor) is BearerCredential
                 and core.finite(deadline) and callable(monotonic) and callable(wall))
            target.check()
            super().__init__(binding, ca_pem=ca_pem, token=actor.token, endpoint_ip=endpoint_ip)
            self.target, self.deadline, self.monotonic, self.wall = target, deadline, monotonic, wall
            self._path = f'/api/v1/namespaces/{target.namespace}/serviceaccounts/{target.name}/token'
            self._body = core.encoded({'apiVersion':'authentication.k8s.io/v1','kind':'TokenRequest',
                'metadata':{'name':target.name,'namespace':target.namespace},
                'spec':{'audiences':[target.audience],'expirationSeconds':900}})
            self._lock = threading.Lock()
            self._used = self._closed = self._attempted = False
            self._last = self._anchor = self._last_wall = None
        except BaseException as e:
            error = _public_error(e, 'TOKEN_REQUEST_INVALID')
        if error is not None:
            actor = None
            self._token = None
            raise error from None

    def __repr__(self): return '<TokenRequestIssuer: private single-use, no Runtime GO>'

    @property
    def attempted(self): return self._attempted

    def _now(self):
        v = self.monotonic()
        need(core.finite(v) and (self._last is None or v >= self._last), 'TOKEN_CLOCK_INVALID')
        self._last = v
        return v

    def _wall(self):
        w, m = self.wall(), self._now()
        need(core.finite(w) and (self._last_wall is None or w >= self._last_wall), 'TOKEN_CLOCK_INVALID')
        self._last_wall = w
        if self._anchor is None: self._anchor = w, m
        return max(w, self._anchor[0] + m - self._anchor[1])

    def _check_scope(self, request):
        need(not self._closed and request.method == 'POST' and request.path == self._path
             and request.content_type == 'application/json' and request.body == self._body)

    def _read_response(self, sock, request, deadline):
        return wire.read_json_response(sock, request, deadline, (201,))[1]

    def __call__(self, request):
        raise IssuanceError('TOKEN_EXPLICIT_ISSUE_REQUIRED')

    def issue(self, deadline):
        with self._lock:
            need(not self._used and not self._closed, 'TOKEN_REQUEST_CONSUMED')
            self._used = True
        raw = receipt = error = None
        try:
            need(core.finite(deadline) and deadline <= self.deadline, 'TOKEN_DEADLINE_INVALID')
            self._wall()
            seconds = min(10, deadline - self._now()); need(seconds > 0, 'TOKEN_DEADLINE_INVALID')
            budget = wire.Deadline(seconds, self._now); budget.end = min(budget.end, deadline)
            req = core.Request(self._binding.key, self._fingerprint, self._binding.api_origin,
                self._binding.ca_sha256, 'POST', self._path, 'application/json', self._body, seconds)
            self._check(req); budget.left()
            self._attempted = True  # Even timeout/4xx after entry is not proof of no issuance.
            raw = self._exchange(req, budget)
            budget.left(); need(not self._closed, 'TOKEN_REQUEST_CONSUMED')
            receipt = _receipt(raw, self.target, self._wall())
            budget.left()
        except BaseException as e:
            error = _public_error(e, 'TOKEN_REQUEST_FAILED')
        finally:
            try: self.close()
            except BaseException as e:
                if not isinstance(error, (KeyboardInterrupt, SystemExit)):
                    error = _public_error(e, 'TOKEN_CLEANUP_FAILED') if isinstance(e, (KeyboardInterrupt, SystemExit)) else IssuanceError('TOKEN_CLEANUP_FAILED')
        if error is not None:
            raw = receipt = None
            raise error from None
        return receipt

    def close(self):
        with self._lock:
            self._closed = True; self._token = None
        return True  # Local reference release only, not server revocation.


def _public_error(error, fallback):
    # Return a new exception, then raise OUTSIDE except. Do not retain the
    # original traceback/context (which can contain raw response credentials).
    if isinstance(error, KeyboardInterrupt): return KeyboardInterrupt()
    if isinstance(error, SystemExit): return SystemExit(130)
    code = error.args[0] if type(error) is IssuanceError and len(error.args) == 1 else None
    allowed = {'TOKEN_REQUEST_INVALID','TOKEN_RESPONSE_INVALID','TOKEN_SUBJECT_MISMATCH',
        'TOKEN_LIFETIME_INVALID','TOKEN_REQUEST_CONSUMED','TOKEN_CLOCK_INVALID','TOKEN_DEADLINE_INVALID',
        'TOKEN_CLEANUP_FAILED'}
    return IssuanceError(code if type(code) is str and code in allowed else fallback)


class RequestBoundSupplier(ingress._NoCopy):
    """Bridge-compatible supplier for an already authenticated exact request.

    A request's equality/nonce is not authorization. Trusted site code must
    authenticate/adopt it BEFORE constructing this supplier or starting SSH.
    Existing source tokens remain memory-only; receiver validates their roles.
    """
    def __init__(self, *, issuer, request, source_tokens):
        error = None
        try:
            need(type(issuer) is TokenRequestIssuer)
            ingress._request(request)
            need(request['target']['binding_key'] == issuer._binding.key
                 and type(source_tokens) is list and len(source_tokens) == len(request['repositories']))
            for item, repo in zip(source_tokens, request['repositories']):
                need(type(item) is dict and set(item) == {'repository','token'} and item['repository'] == repo)
                ingress._token(item['token'], source=True)
            self._issuer, self._request, self._sources = issuer, copy.deepcopy(request), copy.deepcopy(source_tokens)
            self._lock = threading.Lock(); self._used = False; self._receipt = None
        except BaseException as e: error = _public_error(e, 'TOKEN_REQUEST_INVALID')
        if error is not None:
            source_tokens = None
            raise error from None

    def __repr__(self): return '<RequestBoundSupplier: private single-use>'

    def __call__(self, request, deadline):
        with self._lock:
            need(not self._used, 'TOKEN_REQUEST_CONSUMED'); self._used = True
        receipt = result = error = None
        try:
            ingress._request(request); need(request == self._request, 'TOKEN_REQUEST_INVALID')
            receipt = self._issuer.issue(deadline)
            self._receipt = receipt  # Retain actual issuance even if handoff later fails.
            ingress._tokens(self._sources, request['repositories'], receipt.credential.token)
            result = receipt.credential.token, self._sources
        except BaseException as e: error = _public_error(e, 'TOKEN_SUPPLY_FAILED')
        finally:
            self._sources = self._request = None
            try: self._issuer.close()
            except BaseException as e:
                if not isinstance(error, (KeyboardInterrupt, SystemExit)):
                    error = _public_error(e, 'TOKEN_CLEANUP_FAILED') if isinstance(e, (KeyboardInterrupt, SystemExit)) else IssuanceError('TOKEN_CLEANUP_FAILED')
        if error is not None:
            receipt = result = None
            raise error from None
        return result

    def take_receipt(self):
        with self._lock:
            value, self._receipt = self._receipt, None
        return value  # PRIVATE, including token; no automatic publication/adoption.

    def close(self):
        with self._lock:
            self._used = True; self._sources = self._request = self._receipt = None
        self._issuer.close()
