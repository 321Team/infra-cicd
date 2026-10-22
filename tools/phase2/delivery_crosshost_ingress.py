"""One-use, memory-only ingress core for a reviewed cross-host transport.

No SSH/socket listener, credential issuer, configuration loader, actual peer or
default SITE is installed here. The trusted transport must authenticate its
peer, bind to this waiting job, enforce read deadlines, and avoid secret logs.
Nonce/job matching is correlation, not transport/issuer/GO authentication.
"""
from dataclasses import asdict, dataclass, field
import hmac
import json
import re
import secrets
import struct
import threading
import time

import delivery_adapter as core
import delivery_job as job
import delivery_job_handoff as handoff
import delivery_site as site
from delivery_credential_channel import TOKEN, MAX_TOKEN_BYTES
from delivery_github_source import RepositoryPin, repository_checked, NAME

REQUEST_SCHEMA = 'P2-CICD-ingress-request-1'
MESSAGE_SCHEMA = 'P2-CICD-ingress-message-1'
MAX_MESSAGE_BYTES = 96 * 1024
CHUNK_BYTES = 4096
HEX64 = re.compile(r'[a-f0-9]{64}')
SAFE_CODES = frozenset('''
INGRESS_CONFIGURATION_INVALID INGRESS_CLOCK_INVALID INGRESS_DEADLINE_EXCEEDED
INGRESS_CONSUMED INGRESS_CHANNEL_INVALID INGRESS_PEER_REJECTED
INGRESS_FRAME_INVALID INGRESS_MESSAGE_INVALID INGRESS_TARGET_REJECTED
INGRESS_SOURCE_REJECTED INGRESS_CREDENTIAL_REJECTED INGRESS_RECEIVE_FAILED
INGRESS_CLEANUP_FAILED INGRESS_INVOCATION_REJECTED
'''.split())


class IngressError(RuntimeError):
    """Fixed public codes only; no callback exception, token or message text."""


class _NoCopy:
    def __copy__(self):
        raise IngressError('INGRESS_CONFIGURATION_INVALID')

    def __deepcopy__(self, memo):
        raise IngressError('INGRESS_CONFIGURATION_INVALID')

    def __reduce_ex__(self, protocol):
        raise IngressError('INGRESS_CONFIGURATION_INVALID')


def need(ok, code):
    if not ok:
        raise IngressError(code)


def _text(value, maximum=512):
    return (type(value) is str and 0 < len(value) <= maximum
            and all(32 < ord(c) < 127 for c in value))


def _token(value, source=False):
    need(type(value) is str and (20 if source else 1) <= len(value)
         <= (4096 if source else MAX_TOKEN_BYTES), 'INGRESS_CREDENTIAL_REJECTED')
    try:
        raw = value.encode('ascii')
    except UnicodeError:
        raise IngressError('INGRESS_CREDENTIAL_REJECTED') from None
    need(TOKEN.fullmatch(raw) is not None, 'INGRESS_CREDENTIAL_REJECTED')
    return raw


def _target(value):
    keys = {'repository', 'event', 'ref', 'workflow_sha', 'run_id', 'attempt', 'binding_key'}
    need(type(value) is dict and set(value) == keys
         and type(value['attempt']) is int
         and all(type(value[k]) is str for k in keys - {'attempt'}),
         'INGRESS_TARGET_REJECTED')
    context = job.JobContext(**{k: value[k] for k in keys - {'binding_key'}})
    try:
        context.check()
    except Exception:
        raise IngressError('INGRESS_TARGET_REJECTED') from None
    need(HEX64.fullmatch(value['binding_key']) is not None, 'INGRESS_TARGET_REJECTED')


def _repositories(values):
    need(type(values) is list and 0 < len(values) <= 16
         and all(type(v) is str and len(v.split('/')) == 2
                 and all(NAME.fullmatch(p) for p in v.split('/')) for v in values)
         and len(set(values)) == len(values), 'INGRESS_SOURCE_REJECTED')


def _request(value):
    need(type(value) is dict and set(value) == {'schema', 'target', 'nonce', 'repositories'}
         and value['schema'] == REQUEST_SCHEMA and type(value['nonce']) is str
         and HEX64.fullmatch(value['nonce']) is not None, 'INGRESS_MESSAGE_INVALID')
    _target(value['target'])
    _repositories(value['repositories'])


def _tokens(values, repositories, rosa):
    need(type(values) is list and len(values) == len(repositories), 'INGRESS_SOURCE_REJECTED')
    result = []
    rosa_raw = _token(rosa)
    for value, repository in zip(values, repositories):
        need(type(value) is dict and set(value) == {'repository', 'token'}
             and type(value['repository']) is str and value['repository'] == repository,
             'INGRESS_SOURCE_REJECTED')
        raw = _token(value['token'], source=True)
        need(not hmac.compare_digest(rosa_raw, raw), 'INGRESS_CREDENTIAL_REJECTED')
        result.append(value['token'])
    return tuple(result)  # Same GitHub token across allowed repositories is valid.


def encode_frame(request, *, rosa_token, source_tokens):
    """Create private in-memory wire bytes, never an approval or send operation.

    The remote producer must authenticate the request/transport independently.
    Native metadata/nonce/repository names alone confer no issuance authority.
    """
    try:
        _request(request)
        _tokens(source_tokens, request['repositories'], rosa_token)
        raw = core.encoded({'schema': MESSAGE_SCHEMA, 'target': request['target'],
            'nonce': request['nonce'], 'rosa_token': rosa_token, 'source_tokens': source_tokens})
        need(0 < len(raw) <= MAX_MESSAGE_BYTES, 'INGRESS_FRAME_INVALID')
        return struct.pack('!I', len(raw)) + raw
    except IngressError:
        raise
    except Exception:
        raise IngressError('INGRESS_MESSAGE_INVALID') from None


@dataclass(frozen=True, repr=False)
class PeerIdentity:
    principal: str = field(repr=False)
    transport_reference: str = field(repr=False)

    def check(self):
        need(_text(self.principal, 256) and _text(self.transport_reference),
             'INGRESS_CONFIGURATION_INVALID')


@dataclass(frozen=True, repr=False)
class SourceRoute:
    repository: RepositoryPin = field(repr=False)
    local_use_until: float
    ca_pem: bytes = field(repr=False)
    endpoint_ip: str = field(repr=False)


class ReviewedIngressChannel(_NoCopy):
    """Explicit owned transport callbacks from trusted, reviewed site code.

    authenticate(request, deadline) must verify the real transport/peer, not echo
    a JSON identity. read(max_bytes, deadline) must return bounded bytes/EOF and
    enforce the supplied absolute deadline itself. This core cannot preempt a
    callback that never returns. No concrete adapter is configured by default.
    """
    def __init__(self, *, authenticate, read, close):
        need(all(callable(x) for x in (authenticate, read, close)), 'INGRESS_CHANNEL_INVALID')
        self.authenticate, self.read, self._close = authenticate, read, close
        self._closed, self._cleanup_ok = False, True
        self._lock = threading.Lock()

    def __repr__(self):
        return '<P2 reviewed ingress channel: transport not authenticated by construction>'

    def close(self):
        with self._lock:
            if self._closed:
                return self._cleanup_ok
            self._closed, self._cleanup_ok = True, False
            close, self._close = self._close, None
            self.authenticate = self.read = None
        # Detach before callback; reentrant/concurrent close never repeats it.
        try:
            ok = close() is True
        except BaseException:
            ok = False
        with self._lock:
            self._cleanup_ok = ok
        return self._cleanup_ok


class ReceivedCredentials(_NoCopy):
    """Owned one-use memory references; no authenticated issuer/GO assertion."""
    execution_authorized = False

    def __init__(self, *, context, binding, rosa_token, sources, deadline, clock):
        self._context, self._binding = context, binding
        self._rosa, self._sources = rosa_token, sources
        self._deadline, self._clock, self._used = deadline, clock, False
        self._lock = threading.Lock()

    def __repr__(self):
        return '<P2 received credentials: private, single-use, issuer not authenticated>'

    def close(self):
        with self._lock:
            self._used = True
            self._rosa = self._sources = None  # Not secure memory erasure/server revocation.

    def build_invocation(self, context, command, *, config, plan, finish_slot, wire,
                         handoff_deadline, finish_capture=None, producer_pins=None):
        with self._lock:
            need(not self._used, 'INGRESS_CONSUMED')
            self._used = True  # Invalid/failed builds consume this result too.
            rosa, sources = self._rosa, self._sources
            self._rosa = self._sources = None
        try:
            need(type(context) is job.JobContext and context == self._context
                 and type(config) is site.SiteConfiguration and type(config.binding) is core.Binding
                 and config.binding == self._binding,
                 'INGRESS_INVOCATION_REJECTED')
            context.check()
            need(core.finite(handoff_deadline), 'INGRESS_INVOCATION_REJECTED')
            deadline = min(handoff_deadline, self._deadline)  # Never renew the outer deadline.
            need(self._clock() < deadline, 'INGRESS_DEADLINE_EXCEEDED')
            return handoff.build_owned_invocation(context, command, config=config, plan=plan,
                finish_slot=finish_slot, rosa_token=rosa, sources=sources, wire=wire,
                handoff_deadline=deadline, monotonic=self._clock,
                finish_capture=finish_capture, producer_pins=producer_pins)
        except IngressError:
            raise
        except handoff.HandoffError:
            raise  # Existing exact helper exposes fixed codes only.
        except KeyboardInterrupt:
            raise KeyboardInterrupt() from None
        except SystemExit:
            raise SystemExit(130) from None
        except Exception:
            raise IngressError('INGRESS_INVOCATION_REJECTED') from None


class CrossHostIngress(_NoCopy):
    """Fixed local target -> authenticated transport -> one frame -> memory.

    Import/constructor never contacts a host, issues a token, reads a file, or
    creates a claim. Nonce is generated per receiver after validating supplied
    code policy; no production targets/peers/routes/deadlines are defaulted.
    """
    def __init__(self, *, context, binding, expected_peer, routes, deadline,
                 monotonic=time.monotonic):
        self._used, self._last = False, None
        self._lock = threading.Lock()
        try:
            need(type(context) is job.JobContext and type(binding) is core.Binding
                 and type(expected_peer) is PeerIdentity and callable(monotonic),
                 'INGRESS_CONFIGURATION_INVALID')
            context.check(); core.binding_checked(binding); expected_peer.check()
            self._clock_read = monotonic
            self._context, self._binding, self._peer = context, binding, expected_peer
            self._deadline = deadline
            need(core.finite(deadline) and self._now() < deadline, 'INGRESS_DEADLINE_EXCEEDED')
            need(type(routes) is tuple and 0 < len(routes) <= 16
                 and all(type(x) is SourceRoute for x in routes), 'INGRESS_CONFIGURATION_INVALID')
            refs = []
            for route in routes:
                repository_checked(route.repository)
                need(core.finite(route.local_use_until) and self._now() < route.local_use_until
                     and type(route.ca_pem) is bytes and bool(route.ca_pem)
                     and _text(route.endpoint_ip, 128), 'INGRESS_CONFIGURATION_INVALID')
                refs.append(route.repository.owner + '/' + route.repository.name)
            _repositories(refs)
            need(len(set(x.repository for x in routes)) == len(routes), 'INGRESS_CONFIGURATION_INVALID')
            self._routes, self._refs = routes, tuple(refs)
            self._nonce = secrets.token_hex(32)  # Actual native execution only; tests replace it.
            need(type(self._nonce) is str and HEX64.fullmatch(self._nonce), 'INGRESS_CONFIGURATION_INVALID')
            self._check()
        except IngressError:
            raise
        except Exception:
            raise IngressError('INGRESS_CONFIGURATION_INVALID') from None

    def __repr__(self):
        return '<P2 cross-host ingress: private, unadopted transport, single-use>'

    def _now(self):
        try:
            value = self._clock_read()
            need(core.finite(value) and (self._last is None or value >= self._last),
                 'INGRESS_CLOCK_INVALID')
            self._last = value
            return value
        except IngressError:
            raise
        except Exception:
            raise IngressError('INGRESS_CLOCK_INVALID') from None

    def _check(self):
        need(self._now() < self._deadline, 'INGRESS_DEADLINE_EXCEEDED')

    def _public(self):
        return {'schema': REQUEST_SCHEMA, 'target': asdict(self._context) | {'binding_key': self._binding.key},
                'nonce': self._nonce, 'repositories': list(self._refs)}

    def request(self):
        with self._lock:
            need(not self._used, 'INGRESS_CONSUMED')
            self._check()
            return self._public()  # Fresh dict cannot rewrite the stored target.

    def close(self):
        with self._lock:
            self._used = True

    def _read(self, channel, size):
        self._check()
        need(not channel._closed, 'INGRESS_CHANNEL_INVALID')
        raw = channel.read(size, self._deadline)
        self._check()
        need(not channel._closed, 'INGRESS_CHANNEL_INVALID')
        need(type(raw) is bytes and len(raw) <= size, 'INGRESS_FRAME_INVALID')
        return raw

    def _exact(self, channel, size):
        raw = bytearray()
        while len(raw) < size:
            part = self._read(channel, min(CHUNK_BYTES, size - len(raw)))
            need(bool(part), 'INGRESS_FRAME_INVALID')
            raw.extend(part)
        return raw

    @staticmethod
    def _pairs(pairs):
        result = {}
        for key, value in pairs:
            need(key not in result, 'INGRESS_MESSAGE_INVALID')
            result[key] = value
        return result

    def _decode(self, raw):
        def constant(unused):
            raise IngressError('INGRESS_MESSAGE_INVALID')
        try:
            message = json.loads(raw.decode('utf-8'), object_pairs_hook=self._pairs, parse_constant=constant)
            need(type(message) is dict and set(message) ==
                 {'schema', 'target', 'nonce', 'rosa_token', 'source_tokens'}
                 and message['schema'] == MESSAGE_SCHEMA, 'INGRESS_MESSAGE_INVALID')
            _target(message['target'])
            need(message['target'] == self._public()['target']
                 and type(message['nonce']) is str and HEX64.fullmatch(message['nonce'])
                 and hmac.compare_digest(message['nonce'], self._nonce), 'INGRESS_TARGET_REJECTED')
            tokens = _tokens(message['source_tokens'], list(self._refs), message['rosa_token'])
            sources = tuple(handoff.SourceHandoff(route.repository, token, route.local_use_until,
                            route.ca_pem, route.endpoint_ip) for route, token in zip(self._routes, tokens))
            return ReceivedCredentials(context=self._context, binding=self._binding,
                rosa_token=message['rosa_token'], sources=sources, deadline=self._deadline, clock=self._now)
        except IngressError:
            raise
        except Exception:
            raise IngressError('INGRESS_MESSAGE_INVALID') from None

    def receive(self, channel):
        with self._lock:
            need(not self._used, 'INGRESS_CONSUMED')
            self._used = True  # Peer failures and timeouts consume the receiver too.
        owned = type(channel) is ReviewedIngressChannel
        result = raw = None
        error = interrupted = None
        try:
            need(owned and not channel._closed, 'INGRESS_CHANNEL_INVALID')
            self._check()
            peer = channel.authenticate(self._public(), self._deadline)
            self._check()
            need(type(peer) is PeerIdentity and peer == self._peer, 'INGRESS_PEER_REJECTED')
            size = struct.unpack('!I', self._exact(channel, 4))[0]
            need(0 < size <= MAX_MESSAGE_BYTES, 'INGRESS_FRAME_INVALID')
            raw = self._exact(channel, size)
            need(self._read(channel, 1) == b'', 'INGRESS_FRAME_INVALID')  # One frame AND EOF.
            result = self._decode(raw)
            self._check()
        except BaseException as exc:
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                interrupted = type(exc)
            code = exc.args[0] if type(exc) is IngressError and len(exc.args) == 1 else None
            error = code if type(code) is str and code in SAFE_CODES else 'INGRESS_RECEIVE_FAILED'
        finally:
            if raw is not None:
                raw[:] = bytes(len(raw))  # Scratch only; Python copies are not securely erased.
            if owned and channel.close() is not True:
                error, interrupted = 'INGRESS_CLEANUP_FAILED', None
            if not error and interrupted is None:
                try:
                    self._check()  # Closing the transport does not renew the deadline.
                except IngressError as exc:
                    error = exc.args[0]
            if error and result is not None:
                result.close()
        if interrupted is KeyboardInterrupt:
            raise KeyboardInterrupt() from None
        if interrupted is SystemExit:
            raise SystemExit(130) from None
        if error:
            raise IngressError(error) from None
        return result
