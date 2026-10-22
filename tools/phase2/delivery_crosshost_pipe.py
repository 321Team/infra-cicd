"""Owned Linux pipe adapters; no host, SSH process, listener or issuer defaults.

The trusted caller transfers exclusive pipe ends, with no aliases in another
thread/process. CLOEXEC/nonblocking are set on the transferred ends. Transport
authentication remains an explicit callback; an FD or receipt is not a grant.
"""
import errno
import math
import os
import select
import stat
import sys
import threading
import time

import delivery_adapter as core
import delivery_crosshost_ingress as ingress

SAFE_CODES = frozenset('''
P2_STREAM_CONFIGURATION_INVALID P2_STREAM_LINUX_REQUIRED P2_STREAM_FD_CHANGED
P2_STREAM_CLOSED P2_STREAM_CLOCK_INVALID P2_STREAM_DEADLINE_EXCEEDED
P2_STREAM_IO_FAILED P2_STREAM_PEER_REJECTED P2_STREAM_CONSUMED
P2_STREAM_SEND_FAILED P2_STREAM_CLEANUP_FAILED
'''.split())


class PipeStreamError(RuntimeError):
    """Fixed codes only; never an OS/callback exception, payload or native path."""


def need(ok, code):
    if not ok:
        raise PipeStreamError(code)


def identity(fd):
    import fcntl
    info = os.fstat(fd)
    return info.st_dev, info.st_ino, fcntl.fcntl(fd, fcntl.F_GETFL) & os.O_ACCMODE


class _PipeEnd(ingress._NoCopy):
    def __init__(self, fd, *, write, deadline, monotonic):
        self._fd, self._identity, self._last = None, None, None
        self._cleanup_ok = True
        self._lock = threading.Lock()
        need(sys.platform == 'linux', 'P2_STREAM_LINUX_REQUIRED')
        need(type(fd) is int and fd >= 3 and type(write) is bool, 'P2_STREAM_CONFIGURATION_INVALID')
        try:
            info, ident = os.fstat(fd), identity(fd)
            need(stat.S_ISFIFO(info.st_mode) and ident[2] == (os.O_WRONLY if write else os.O_RDONLY),
                 'P2_STREAM_CONFIGURATION_INVALID')
        except PipeStreamError:
            raise
        except Exception:
            raise PipeStreamError('P2_STREAM_CONFIGURATION_INVALID') from None
        # Ownership starts only after the exact pipe type/direction was checked.
        self._fd, self._identity, self._write = fd, ident, write
        try:
            need(core.finite(deadline) and callable(monotonic), 'P2_STREAM_CONFIGURATION_INVALID')
            self._deadline, self._clock = deadline, monotonic
            self.check_time()
            os.set_inheritable(fd, False)
            os.set_blocking(fd, False)
        except BaseException as exc:
            ok = self.close()
            if not ok:
                raise PipeStreamError('P2_STREAM_CLEANUP_FAILED') from None
            if isinstance(exc, KeyboardInterrupt):
                raise KeyboardInterrupt() from None
            if isinstance(exc, SystemExit):
                raise SystemExit(130) from None
            code = exc.args[0] if type(exc) is PipeStreamError and len(exc.args) == 1 else None
            raise PipeStreamError(code if type(code) is str and code in SAFE_CODES else 'P2_STREAM_CONFIGURATION_INVALID') from None

    def __repr__(self):
        return '<P2 owned pipe end: private, no transport authentication implied>'

    def check_time(self):
        try:
            now = self._clock()
            need(core.finite(now) and (self._last is None or now >= self._last), 'P2_STREAM_CLOCK_INVALID')
            self._last = now
            need(now < self._deadline, 'P2_STREAM_DEADLINE_EXCEEDED')
            return self._deadline - now
        except PipeStreamError:
            raise
        except Exception:
            raise PipeStreamError('P2_STREAM_CLOCK_INVALID') from None

    def _current(self):
        need(self._fd is not None, 'P2_STREAM_CLOSED')
        try:
            need(identity(self._fd) == self._identity, 'P2_STREAM_FD_CHANGED')
        except PipeStreamError:
            raise
        except Exception:
            raise PipeStreamError('P2_STREAM_FD_CHANGED') from None
        return self._fd

    def _wait(self):
        remaining = self.check_time()
        with self._lock:
            fd = self._current()
        try:
            poll = select.poll()  # No select() FD_SETSIZE limit on shared Runners.
            poll.register(fd, select.POLLOUT if self._write else select.POLLIN)
            milliseconds = min(math.ceil(min(remaining, 2147483.647) * 1000), 2147483647)
            events = poll.poll(milliseconds)
        except (InterruptedError, BlockingIOError):
            return False  # Same offset and absolute deadline; no message retry.
        except Exception:
            raise PipeStreamError('P2_STREAM_IO_FAILED') from None
        self.check_time()
        if not events:
            return False
        need(len(events) == 1 and events[0][0] == fd
             and not events[0][1] & select.POLLNVAL, 'P2_STREAM_FD_CHANGED')
        need(not events[0][1] & select.POLLERR, 'P2_STREAM_IO_FAILED')
        return bool(events[0][1] & (select.POLLOUT if self._write else select.POLLIN | select.POLLHUP))

    def read(self, size, deadline):
        need(not self._write and type(size) is int and 0 < size <= ingress.CHUNK_BYTES
             and core.finite(deadline) and deadline == self._deadline, 'P2_STREAM_CONFIGURATION_INVALID')
        while True:
            if not self._wait():
                continue
            with self._lock:
                fd = self._current()
                self.check_time()
                try:
                    raw = os.read(fd, size)
                except (InterruptedError, BlockingIOError):
                    continue
                except Exception:
                    raise PipeStreamError('P2_STREAM_IO_FAILED') from None
                self.check_time()
                return raw

    def write_all(self, frame):
        need(self._write and type(frame) is bytes and 4 < len(frame) <= ingress.MAX_MESSAGE_BYTES + 4,
             'P2_STREAM_CONFIGURATION_INVALID')
        offset = 0
        while offset < len(frame):
            if not self._wait():
                continue
            with self._lock:
                fd = self._current()
                self.check_time()
                try:
                    count = os.write(fd, frame[offset:offset + ingress.CHUNK_BYTES])
                except (InterruptedError, BlockingIOError):
                    continue
                except Exception:
                    raise PipeStreamError('P2_STREAM_IO_FAILED') from None
                need(type(count) is int and 0 < count <= min(ingress.CHUNK_BYTES, len(frame) - offset),
                     'P2_STREAM_IO_FAILED')
                offset += count
                self.check_time()

    def close(self):
        with self._lock:
            fd, self._fd = self._fd, None
            if fd is None:
                return self._cleanup_ok
            self._cleanup_ok = False
            try:
                if identity(fd) != self._identity:
                    self._cleanup_ok = True  # Never close a reused FD's new owner.
                else:
                    os.close(fd)
                    self._cleanup_ok = True
            except OSError as exc:
                self._cleanup_ok = exc.errno == errno.EBADF
            except BaseException:
                self._cleanup_ok = False
            return self._cleanup_ok


def pipe_ingress_channel(fd, *, authenticate, deadline, monotonic=time.monotonic):
    """Take one owned read end; return the exact channel accepted by ingress.

    Invalid type/direction/platform leaves ownership with the caller. After
    pipe validation, construction failure closes that owned end. No FD cloning,
    file/stdin/env loader, process creation, peer discovery or authentication.
    """
    need(callable(authenticate), 'P2_STREAM_CONFIGURATION_INVALID')
    end = _PipeEnd(fd, write=False, deadline=deadline, monotonic=monotonic)
    try:
        return ingress.ReviewedIngressChannel(authenticate=authenticate, read=end.read, close=end.close)
    except BaseException:
        if not end.close():
            raise PipeStreamError('P2_STREAM_CLEANUP_FAILED') from None
        raise


class OwnedPipeWriter(ingress._NoCopy):
    """One frame to an explicitly transferred write end, then close for EOF.

    True means local write/close only, never remote acceptance or authorization.
    The trusted transport owner separately owns any SSH process and its other
    FDs; this class never launches, kills, waits for, or authenticates that host.
    """
    def __init__(self, fd, *, deadline, monotonic=time.monotonic):
        self._end = _PipeEnd(fd, write=True, deadline=deadline, monotonic=monotonic)
        self._used, self._lock = False, threading.Lock()

    def __repr__(self):
        return '<P2 owned frame writer: private, single-use, no remote receipt>'

    def close(self):
        with self._lock:
            self._used = True
        return self._end.close()

    def send(self, request, *, expected_peer, authenticate, rosa_token, source_tokens):
        with self._lock:
            need(not self._used, 'P2_STREAM_CONSUMED')
            self._used = True
        error = interrupted = None
        try:
            need(type(expected_peer) is ingress.PeerIdentity and callable(authenticate),
                 'P2_STREAM_CONFIGURATION_INVALID')
            expected_peer.check()
            ingress._request(request)
            # Give untrusted-to-the-core callback a fresh PUBLIC-only request.
            import copy
            expected = copy.deepcopy(request)
            self._end.check_time()
            peer = authenticate(copy.deepcopy(expected), self._end._deadline)
            self._end.check_time()
            need(type(peer) is ingress.PeerIdentity and peer == expected_peer, 'P2_STREAM_PEER_REJECTED')
            self._end.write_all(ingress.encode_frame(expected,
                rosa_token=rosa_token, source_tokens=source_tokens))
        except BaseException as exc:
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                interrupted = type(exc)
            code = exc.args[0] if type(exc) in (PipeStreamError, ingress.IngressError) and len(exc.args) == 1 else None
            error = code if type(code) is str and code in SAFE_CODES | ingress.SAFE_CODES else 'P2_STREAM_SEND_FAILED'
        finally:
            if not self._end.close():
                error, interrupted = 'P2_STREAM_CLEANUP_FAILED', None
            if error is None:
                try:
                    self._end.check_time()
                except PipeStreamError as exc:
                    error = exc.args[0]
        if interrupted is KeyboardInterrupt:
            raise KeyboardInterrupt() from None
        if interrupted is SystemExit:
            raise SystemExit(130) from None
        if error:
            raise PipeStreamError(error) from None
        return True
