"""Explicit Linux abstract rendezvous ownership for the existing native parent.

No filesystem pathname, SSH process, key, issuer, default UID or configured SITE.
Public native request/nonce selects a local address, never execution authority.
"""
import copy
import socket
import sys
import threading

import delivery_adapter as core
import delivery_crosshost_ingress as ingress
import delivery_job as job
import delivery_job_parent as parent
import delivery_job_handoff as handoff
import delivery_unix_ingress as unix

SAFE_CODES = frozenset('''P2_RENDEZVOUS_CONFIGURATION_INVALID P2_RENDEZVOUS_CONSUMED
P2_RENDEZVOUS_PUBLISH_FAILED P2_RENDEZVOUS_OPEN_FAILED P2_RENDEZVOUS_CLEANUP_FAILED
P2_RENDEZVOUS_BUILD_FAILED P2_RENDEZVOUS_DEADLINE_EXCEEDED'''.split())


class RendezvousError(RuntimeError):
    """Fixed codes, no callback text, address, native details or credentials."""


def need(ok, code='P2_RENDEZVOUS_CONFIGURATION_INVALID'):
    if not ok:
        raise RendezvousError(code)


def abstract_address(request):
    """Canonical public request -> Linux abstract address; pure, not a grant.

    An authenticated SSH remote helper must derive this locally. A NUL address
    cannot be passed as an OpenSSH argv/path string or used as a remote endpoint.
    """
    try:
        ingress._request(request)
        return b'\0fds-p2-' + core.digest(core.encoded(request)).encode('ascii')
    except Exception:
        raise RendezvousError('P2_RENDEZVOUS_CONFIGURATION_INVALID') from None


def _raise(exc, fallback):
    if isinstance(exc, KeyboardInterrupt):
        raise KeyboardInterrupt() from None
    if isinstance(exc, SystemExit):
        raise SystemExit(130) from None
    if type(exc) is RendezvousError and len(exc.args) == 1 and exc.args[0] in SAFE_CODES:
        raise RendezvousError(exc.args[0]) from None
    raise RendezvousError(fallback) from None


def _close_owner(owner):
    try:
        return owner.close() is True
    except BaseException:
        return False


class UnixJobRendezvous(ingress._NoCopy):
    """One explicit public publish/listen/accept, paired with owned builder.

    Constructor is inert. publish callback remains trusted and deadline-bounded;
    it must authenticate recipients and clean its own partial external resources.
    Local socket owner never turns publication or a UID match into issuer/GO proof.
    """
    def __init__(self, *, expected_uid, peer, deadline, monotonic, publish_ready):
        need(type(expected_uid) is int and 0 < expected_uid < 0xFFFFFFFF
             and type(peer) is ingress.PeerIdentity and callable(monotonic)
             and callable(publish_ready) and core.finite(deadline))
        peer.check()
        self._uid, self._peer, self._deadline = expected_uid, peer, deadline
        self._clock, self._publish = monotonic, publish_ready
        self._budget = unix._Budget(deadline, monotonic)
        self._listener = self._request = None
        self._published = self._ready = self._opened = self._closed = False
        self._cleanup_ok = True
        self._lock = threading.RLock()

    def __repr__(self):
        return '<P2 job rendezvous: local, private, unconfigured SSH, single-use>'

    def _check(self, deadline):
        try:
            self._budget.remaining(deadline)
        except Exception:
            raise RendezvousError('P2_RENDEZVOUS_DEADLINE_EXCEEDED') from None

    def publish_ready(self, request, deadline):
        with self._lock:
            need(not self._published and not self._closed, 'P2_RENDEZVOUS_CONSUMED')
            self._published = True  # Even failed publication is not retried.
        try:
            with self._lock:
                need(sys.platform == 'linux' and hasattr(socket, 'SO_PEERCRED'))
                self._check(deadline)
                address = abstract_address(request)
                self._request = copy.deepcopy(request)
                sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                self._listener = sock  # Own before any subsequent operation fails.
                unix._shape(sock)
                sock.set_inheritable(False)
                sock.bind(address)
                need(sock.getsockname() == address)
                sock.listen(1)
                self._check(deadline)
                public = copy.deepcopy(self._request)
            self._publish(public, self._budget.limit)  # Listen BEFORE public readiness.
            with self._lock:
                need(not self._closed, 'P2_RENDEZVOUS_CONSUMED')
                self._check(deadline)
                self._ready = True
            return True
        except BaseException as exc:
            if not _close_owner(self):
                raise RendezvousError('P2_RENDEZVOUS_CLEANUP_FAILED') from None
            _raise(exc, 'P2_RENDEZVOUS_PUBLISH_FAILED')

    def open_channel(self, request, deadline):
        with self._lock:
            need(not self._opened and not self._closed, 'P2_RENDEZVOUS_CONSUMED')
            self._opened = True
            try:
                self._check(deadline)
                need(self._ready and self._request is not None and request == self._request)
                ingress._request(request)
                public = copy.deepcopy(self._request)  # Build arguments before transfer.
                listener, self._listener = self._listener, None
                # serialize close with bounded accept; helper now owns listener.
                return unix.accept_unix_ingress_channel(listener, request=public,
                    expected_uid=self._uid, peer=self._peer, deadline=self._budget.limit,
                    monotonic=self._clock)
            except BaseException as exc:
                if not _close_owner(self) or (type(exc) is unix.UnixIngressError
                        and exc.args == ('P2_UNIX_CLEANUP_FAILED',)):
                    self._cleanup_ok = False
                    raise RendezvousError('P2_RENDEZVOUS_CLEANUP_FAILED') from None
                _raise(exc, 'P2_RENDEZVOUS_OPEN_FAILED')

    def close(self):
        with self._lock:
            if self._closed:
                return self._cleanup_ok
            self._closed = True
            sock, self._listener = self._listener, None
            self._request, self._ready = None, False
            if sock is not None:
                self._cleanup_ok = unix._close(sock)
            return self._cleanup_ok


class OwnedRendezvousBuilder(ingress._NoCopy):
    """Same ParentJobBuilder plus listener cleanup before driver ownership.

    Bind only exact parent/owner and their actual bound methods, same clock,
    deadline and peer. Existing FDS full/prepared and 4/5-reader choices stay in
    their original connector; this wrapper supplies no plan or authority facts.
    """
    def __init__(self, builder, rendezvous):
        need(type(builder) is parent.ParentJobBuilder and type(rendezvous) is UnixJobRendezvous)
        need(builder._publish == rendezvous.publish_ready and builder._open == rendezvous.open_channel
             and builder._clock is rendezvous._clock and builder._deadline == rendezvous._deadline
             and builder._peer == rendezvous._peer)
        self._parent, self._owner = builder, rendezvous
        self._used, self._lock = False, threading.Lock()

    def __repr__(self):
        return '<P2 owned rendezvous builder: private, single-use, not authority>'

    def __call__(self, context, command):
        with self._lock:
            need(not self._used, 'P2_RENDEZVOUS_CONSUMED')
            self._used = True
        value = None
        error = None
        try:
            value = self._parent(context, command)
            need(type(value) is job.RuntimeInvocation, 'P2_RENDEZVOUS_BUILD_FAILED')
        except BaseException as exc:
            error = exc
        finally:
            if not _close_owner(self._owner):
                error = RendezvousError('P2_RENDEZVOUS_CLEANUP_FAILED')
        if error is None:
            try:
                self._owner._check(self._owner._deadline)
            except BaseException as exc:
                error = exc
        if error is None:
            return value  # Only now does the existing driver own the invocation.
        if type(value) is job.RuntimeInvocation:
            try:
                if value.close() is not True:
                    error = RendezvousError('P2_RENDEZVOUS_CLEANUP_FAILED')
            except BaseException:
                error = RendezvousError('P2_RENDEZVOUS_CLEANUP_FAILED')
        # Parent already redacts its own errors; preserve only its known codes.
        allowed = parent.SAFE_CODES | ingress.SAFE_CODES | handoff.SAFE_CODES
        if type(error) is parent.ParentBuildError and len(error.args) == 1 and error.args[0] in allowed:
            raise parent.ParentBuildError(error.args[0]) from None
        _raise(error, 'P2_RENDEZVOUS_BUILD_FAILED')
