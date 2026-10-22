"""Linux local peer-UID -> existing job ingress, not an SSH authenticator.

Explicitly adopts exclusive socket objects supplied by reviewed job transport.
No socket/listener/path creation, host lookup, SSH, issuer, credential loader,
default peer, configured SITE, process signalling or filesystem cleanup.
"""
import json
import os
import re
import socket
import struct
import sys
import threading

import delivery_adapter as core
import delivery_crosshost_ingress as ingress

_SOCKET_TYPE = socket.socket


class UnixIngressError(RuntimeError):
    """Fixed codes; never underlying exception, peer/path or secret bytes."""


def need(ok, code='P2_UNIX_CONFIGURATION_INVALID'):
    if not ok:
        raise UnixIngressError(code)


def _shape(sock):
    # Ownership is not taken for a foreign object, TCP socket or wrong platform.
    need(sys.platform == 'linux' and hasattr(socket, 'SO_PEERCRED')
         and type(sock) is _SOCKET_TYPE and sock.family == socket.AF_UNIX
         and sock.type == socket.SOCK_STREAM)


class _Budget:
    def __init__(self, deadline, clock):
        need(core.finite(deadline) and callable(clock))
        self.limit, self.clock, self.last = deadline, clock, None

    def remaining(self, deadline):
        need(core.finite(deadline))
        self.limit = min(self.limit, deadline)  # No renewal after accept/auth/read.
        now = self.clock()
        need(core.finite(now) and (self.last is None or now >= self.last), 'P2_UNIX_CLOCK_INVALID')
        self.last = now
        need(now < self.limit, 'P2_UNIX_DEADLINE_EXCEEDED')
        return self.limit - now


def _configuration(request, uid, peer):
    need(type(uid) is int and 0 < uid < 0xFFFFFFFF and type(peer) is ingress.PeerIdentity)
    peer.check(); ingress._request(request)
    return json.loads(json.dumps(request))  # Value-bound, caller cannot mutate it.


def _overflow_uid():
    # Local Linux kernel identity setting, not a supplied policy/approval file.
    fd = os.open('/proc/sys/kernel/overflowuid', os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        raw = os.read(fd, 32)
    finally:
        os.close(fd)
    need(re.fullmatch(rb'[0-9]{1,10}\n?', raw) is not None, 'P2_UNIX_PEER_REJECTED')
    value = int(raw)
    need(value < 0xFFFFFFFF, 'P2_UNIX_PEER_REJECTED')
    return value


def _peer_credentials(sock):
    # Unmapped namespace IDs collapse to this current setting; never authorize
    # that ambiguous UID. No guessed 65534 default or /proc configuration write.
    before = _overflow_uid()
    raw = sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize('iII'))
    pid, uid, gid = struct.unpack('iII', raw)  # pid_t signed, uid_t/gid_t unsigned.
    need(before == _overflow_uid() and pid > 0 and uid != before
         and uid < 0xFFFFFFFF and gid < 0xFFFFFFFF, 'P2_UNIX_PEER_REJECTED')
    return pid, uid, gid


class _Connection:
    def __init__(self, sock, request, uid, peer, budget):
        self.sock, self.request, self.uid, self.peer, self.budget = sock, request, uid, peer, budget
        self.lock = threading.RLock()
        self.auth_used = self.authenticated = self.closed = False
        self.cleanup_ok = True

    def authenticate(self, request, deadline):
        with self.lock:
            need(not self.closed and not self.auth_used, 'P2_UNIX_CONSUMED')
            self.auth_used = True  # Failure consumes authentication too.
            try:
                self.budget.remaining(deadline)
                ingress._request(request)
                need(request == self.request, 'P2_UNIX_TARGET_REJECTED')
                pid, uid, gid = _peer_credentials(self.sock)
                need(pid > 0 and uid == self.uid and gid >= 0, 'P2_UNIX_PEER_REJECTED')
                self.budget.remaining(deadline)
                self.authenticated = True
                return self.peer  # Reviewed local UID mapping, NOT SSH/issuer/GO proof.
            except UnixIngressError:
                raise
            except BaseException as exc:
                _interrupt(exc)
                raise UnixIngressError('P2_UNIX_AUTHENTICATION_FAILED') from None

    def read(self, maximum, deadline):
        with self.lock:
            need(not self.closed and self.authenticated, 'P2_UNIX_NOT_AUTHENTICATED')
            need(type(maximum) is int and 0 < maximum <= ingress.CHUNK_BYTES)
            try:
                self.sock.settimeout(self.budget.remaining(deadline))
                raw = self.sock.recv(maximum)
                self.budget.remaining(deadline)
                return raw
            except UnixIngressError:
                raise
            except BaseException as exc:
                _interrupt(exc)
                raise UnixIngressError('P2_UNIX_READ_FAILED') from None

    def close(self):
        with self.lock:
            if self.closed:
                return self.cleanup_ok
            self.closed = True
            sock, self.sock = self.sock, None
            self.request = self.peer = None
            self.cleanup_ok = _close(sock)
            return self.cleanup_ok


def _interrupt(exc):
    if isinstance(exc, KeyboardInterrupt):
        raise KeyboardInterrupt() from None
    if isinstance(exc, SystemExit):
        raise SystemExit(130) from None


def _close(sock):
    try:
        sock.close()
        return True
    except BaseException:
        return False


def _connected(sock, request, uid, peer, budget):
    try:
        need(sock.getsockopt(socket.SOL_SOCKET, socket.SO_ACCEPTCONN) == 0)
        sock.getpeername()  # Must be connected; an unconnected socket is not a channel.
        sock.set_inheritable(False)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack('ii', 0, 0))
        budget.remaining(budget.limit)
        owner = _Connection(sock, request, uid, peer, budget)
        return ingress.ReviewedIngressChannel(authenticate=owner.authenticate, read=owner.read, close=owner.close)
    except BaseException as exc:
        if not _close(sock):
            raise UnixIngressError('P2_UNIX_CLEANUP_FAILED') from None
        _interrupt(exc)
        raise UnixIngressError('P2_UNIX_CONNECTION_INVALID') from None


def unix_ingress_channel(sock, *, request, expected_uid, peer, deadline, monotonic):
    """Take exclusive ownership of an already connected, reviewed Linux socket.

    After socket shape qualification, invalid configuration also closes it.
    A socket alias/other thread using that socket violates exclusive ownership.
    Mapping a verified local UID does not prove how that local process logged in.
    """
    _shape(sock)
    try:
        request = _configuration(request, expected_uid, peer)
        budget = _Budget(deadline, monotonic)
    except BaseException as exc:
        if not _close(sock):
            raise UnixIngressError('P2_UNIX_CLEANUP_FAILED') from None
        _interrupt(exc)
        raise UnixIngressError('P2_UNIX_CONFIGURATION_INVALID') from None
    return _connected(sock, request, expected_uid, peer, budget)


def accept_unix_ingress_channel(listener, *, request, expected_uid, peer, deadline, monotonic):
    """One bounded accept for ParentJobBuilder.open_channel; no listener setup.

    Owns a qualified listener from entry and always closes that socket object.
    The caller still owns rendezvous publication/path permissions and pathname
    cleanup, including publish failures BEFORE this function was called.
    """
    _shape(listener)
    connection = result = None
    error = interrupted = None
    try:
        request = _configuration(request, expected_uid, peer)
        budget = _Budget(deadline, monotonic)
        need(listener.getsockopt(socket.SOL_SOCKET, socket.SO_ACCEPTCONN) == 1)
        listener.set_inheritable(False)
        listener.settimeout(budget.remaining(deadline))
        connection, _ = listener.accept()
        budget.remaining(deadline)
        result = _connected(connection, request, expected_uid, peer, budget)
        connection = None  # Channel now owns it; no peer-controlled FD number.
    except BaseException as exc:
        if type(exc) is UnixIngressError and exc.args == ('P2_UNIX_CLEANUP_FAILED',):
            error = 'P2_UNIX_CLEANUP_FAILED'  # A later no-op close cannot clear uncertainty.
        elif isinstance(exc, (KeyboardInterrupt, SystemExit)):
            interrupted = exc
        else:
            error = 'P2_UNIX_ACCEPT_FAILED'
    finally:
        ok = _close(listener)
        if connection is not None:
            ok = _close(connection) and ok
        if not ok:
            error, interrupted = 'P2_UNIX_CLEANUP_FAILED', None
        if error or interrupted is not None:
            if result is not None:
                if result.close() is not True:
                    error, interrupted = 'P2_UNIX_CLEANUP_FAILED', None
    if interrupted is not None:
        _interrupt(interrupted)
    if error:
        raise UnixIngressError(error) from None
    try:
        budget.remaining(deadline)  # Closing the listener cannot renew the budget.
    except BaseException as exc:
        if result.close() is not True:
            raise UnixIngressError('P2_UNIX_CLEANUP_FAILED') from None
        _interrupt(exc)
        raise UnixIngressError('P2_UNIX_DEADLINE_EXCEEDED') from None
    return result
