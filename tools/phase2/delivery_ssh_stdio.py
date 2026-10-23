"""Linux SSH helper stdio shapes only. No host, key, listener or authority.

FIFO directions stay exact. The only additional shape is two aliases of one
connected unnamed AF_UNIX stream endpoint, as used by no-PTY SSH socketpairs.
The caller transfers exclusive dup FDs; original stdio stays borrowed. OFD
nonblocking flags are shared with aliases, just as with the existing pipe path.
"""
import errno
import os
import socket
import stat
import sys
import threading

import delivery_adapter as core
import delivery_crosshost_pipe as pipes


def need(ok, code='P2_STREAM_CONFIGURATION_INVALID'):
    pipes.need(ok, code)


def _fd(fd):
    need(sys.platform == 'linux' and type(fd) is int and fd >= 3)
    return os.fstat(fd), pipes.identity(fd)


def close_owned_fd(fd, ident):
    try:
        if pipes.identity(fd) == ident:
            os.close(fd)
        return True
    except OSError as exc:
        return exc.errno == errno.EBADF
    except BaseException:
        return False


def socket_identity(fd):
    info, ident = _fd(fd)
    need(stat.S_ISSOCK(info.st_mode) and ident[2] == os.O_RDWR
         and socket.getdefaulttimeout() is None)
    import fcntl
    temporary = fcntl.fcntl(fd, fcntl.F_DUPFD_CLOEXEC, 3)
    probe = None
    try:
        flags = fcntl.fcntl(temporary, fcntl.F_GETFL)
        probe = socket.socket(fileno=temporary)
        # Reject global timeout races too, without leaving an OFD flag change.
        now_flags = fcntl.fcntl(temporary, fcntl.F_GETFL)
        if now_flags != flags:
            fcntl.fcntl(temporary, fcntl.F_SETFL, flags)
            need(False)
        need(probe.family == socket.AF_UNIX and probe.type == socket.SOCK_STREAM
             and probe.getsockopt(socket.SOL_SOCKET, socket.SO_ACCEPTCONN) == 0
             and probe.getsockname() in ('', b'') and probe.getpeername() in ('', b'')
             and pipes.identity(fd) == ident)
        return ident
    finally:
        # One owner for the temporary FD even if wrapping failed or interrupted.
        if probe is not None:
            try:
                probe.close(); clean = True
            except BaseException:
                clean = False
        else:
            clean = close_owned_fd(temporary, ident)
        need(clean, 'P2_STREAM_CLEANUP_FAILED')


def classify_pair(read_fd, write_fd):
    need(type(read_fd) is int and type(write_fd) is int and read_fd != write_fd)
    r, ri = _fd(read_fd); w, wi = _fd(write_fd)
    if stat.S_ISFIFO(r.st_mode) and stat.S_ISFIFO(w.st_mode):
        need(ri[2] == os.O_RDONLY and wi[2] == os.O_WRONLY and ri[:2] != wi[:2])
        return 'pipes'
    need(stat.S_ISSOCK(r.st_mode) and stat.S_ISSOCK(w.st_mode)
         and ri[:2] == wi[:2])
    need(socket_identity(read_fd) == socket_identity(write_fd))
    return 'socketpair'


def duplicate_stdio():
    """Qualify our dups, then recheck their borrowed original 0/1 identities."""
    need(sys.platform == 'linux')
    import fcntl
    made = []
    try:
        for original in (0, 1):
            made.append(fcntl.fcntl(original, fcntl.F_DUPFD_CLOEXEC, 3))
        kind = classify_pair(*made)
        need(pipes.identity(0) == pipes.identity(made[0])
             and pipes.identity(1) == pipes.identity(made[1]))
        result = (kind, made[0], made[1]); made = []
        return result
    finally:
        clean = True
        for fd in made:
            try:
                os.close(fd)  # Only our not-yet-transferred duplicates.
            except BaseException:
                clean = False  # Attempt every owned duplicate, even after failure.
        need(clean, 'P2_STREAM_CLEANUP_FAILED')


class SocketStdioEnd(pipes._PipeEnd):
    """Reuse reviewed poll/deadline/read/write/identity/close; no core widening."""
    def __init__(self, fd, *, write, deadline, monotonic):
        self._fd, self._identity, self._last = None, None, None
        self._cleanup_ok = True; self._lock = threading.Lock()
        need(type(write) is bool)
        ident = socket_identity(fd)  # Invalid/foreign shape stays borrowed.
        self._fd, self._identity, self._write = fd, ident, write
        try:
            need(core.finite(deadline) and callable(monotonic))
            self._deadline, self._clock = deadline, monotonic
            self.check_time()
            os.set_inheritable(fd, False); os.set_blocking(fd, False)
        except BaseException as exc:
            need(self.close(), 'P2_STREAM_CLEANUP_FAILED')
            if isinstance(exc, KeyboardInterrupt):raise KeyboardInterrupt() from None
            if isinstance(exc, SystemExit):raise SystemExit(130) from None
            raise pipes.PipeStreamError('P2_STREAM_CONFIGURATION_INVALID') from None

    def __repr__(self):
        return '<P2 SSH stdio owned endpoint: private, not peer authority>'
