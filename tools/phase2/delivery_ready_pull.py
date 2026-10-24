"""One-use public READY pull over the existing management SSH trust boundary.

No credentials, files, default host/port or configured SITE. The public selector
correlates native context/Binding/repositories; it is not execution authority.
"""
import copy
import errno
from dataclasses import asdict
import json
import math
import os
import socket
import sys
import threading
import time

import delivery_adapter as core
import delivery_crosshost_ingress as ingress
import delivery_crosshost_pipe as pipes
import delivery_job as job
import delivery_ssh_bridge as bridge
import delivery_ssh_stdio as stdio
import delivery_unix_ingress as unix

SCHEMA = 'P2-ready-pull-1'


class ReadyError(RuntimeError):
    """Fixed code only, no native text, request, credential or callback detail."""


def need(ok, code='P2_READY_INVALID'):
    if not ok:
        raise ReadyError(code)


def _raise(exc, fallback='P2_READY_FAILED'):
    if isinstance(exc, KeyboardInterrupt):
        raise KeyboardInterrupt() from None
    if isinstance(exc, SystemExit):
        raise SystemExit(130) from None
    raise ReadyError(fallback) from None


def selector_for(context, binding, repositories):
    """Explicit reviewed identity, pure. No nonce or generated operating value."""
    try:
        need(type(context) is job.JobContext and type(binding) is core.Binding)
        context.check(); core.binding_checked(binding)
        value = {'schema': SCHEMA, 'target': asdict(context) | {'binding_key': binding.key},
                 'repositories': list(repositories)}
        _selector(value)
        return value
    except Exception:
        raise ReadyError('P2_READY_INVALID') from None


def _selector(value):
    need(type(value) is dict and set(value) == {'schema', 'target', 'repositories'}
         and value['schema'] == SCHEMA)
    ingress._target(value['target']); ingress._repositories(value['repositories'])
    need(len(core.encoded(value)) <= bridge.PUBLIC_MAX)


def _matches(request, selected):
    ingress._request(request); _selector(selected)
    need(request['target'] == selected['target']
         and request['repositories'] == selected['repositories'], 'P2_READY_MISMATCH')


def address(selected):
    _selector(selected)
    return b'\0fds-p2-ready-' + core.digest(core.encoded(selected)).encode('ascii')


def _json_frame(raw):
    value = json.loads(raw[4:].decode('utf-8'))
    need(bridge._packet(value) == raw)  # canonical, duplicate/trailing JSON rejected
    return value


class _SocketReader:
    def __init__(self, sock, budget):
        self.sock, self.budget = sock, budget

    def read(self, count, deadline):
        self.sock.settimeout(self.budget.remaining(deadline))
        data = self.sock.recv(count)
        self.budget.remaining(deadline)
        return data


class ReadyMailbox(ingress._NoCopy):
    """Job publish callback: bounded READY transfer, never credential completion.

    The job's credential listener must already be listening. This callback waits
    only for the public helper's digest ACK, closes its mailbox, then returns so
    the job can accept the separate credential bridge. No thread/background task.
    """
    def __init__(self, selected, *, expected_uid, deadline, monotonic):
        _selector(selected)
        need(type(expected_uid) is int and 0 < expected_uid < 0xFFFFFFFF
             and core.finite(deadline) and callable(monotonic))
        self._selected = copy.deepcopy(selected)
        self._uid, self._budget = expected_uid, unix._Budget(deadline, monotonic)
        self._used, self._lock = False, threading.Lock()

    def __repr__(self):
        return '<P2 public READY mailbox: single-use, not authority>'

    def __call__(self, request, deadline):
        with self._lock:
            need(not self._used, 'P2_READY_CONSUMED'); self._used = True
        listener = peer = None
        error = None; clean = True
        try:
            need(sys.platform == 'linux' and hasattr(socket, 'SO_PEERCRED'))
            _matches(request, self._selected)
            request = copy.deepcopy(request)
            raw = bridge._packet(request)
            b = self._budget; b.remaining(deadline)
            listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            listener.set_inheritable(False)
            listener.bind(address(self._selected)); listener.listen(1)
            listener.settimeout(b.remaining(b.limit))
            peer, _ = listener.accept()
            peer.set_inheritable(False)
            pid, uid, _ = unix._peer_credentials(peer)
            need(pid > 0 and uid == self._uid, 'P2_READY_PEER_REJECTED')
            peer.settimeout(b.remaining(b.limit)); peer.sendall(raw)
            b.remaining(b.limit); peer.shutdown(socket.SHUT_WR)
            reader = _SocketReader(peer, b)
            expected = bridge._receipt('public-ready-received', request)
            need(bridge._exact(reader, len(expected), b) == expected, 'P2_READY_MISMATCH')
            bridge._eof(reader, b)
        except BaseException as exc:
            error = exc
        finally:
            for sock in (peer, listener):
                if sock is not None:
                    clean = unix._close(sock) and clean
        if error is not None:
            _raise(error, 'P2_READY_FAILED' if clean else 'P2_READY_CLEANUP_FAILED')
        need(clean, 'P2_READY_CLEANUP_FAILED')
        self._budget.remaining(self._budget.limit)
        return True


def _connect_mailbox(selected, budget):
    """Wait only for not-yet-listening UNIX address, inside the same budget.

    No bytes/ACK/credential have been sent on ECONNREFUSED/ENOENT. Once connect
    succeeds, peer/parse/ACK failures are never retried and no new run is made.
    """
    while True:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.set_inheritable(False); sock.settimeout(budget.remaining(budget.limit))
            sock.connect(address(selected))
            budget.remaining(budget.limit)
            return sock
        except OSError as exc:
            need(unix._close(sock), 'P2_READY_CLEANUP_FAILED')
            if exc.errno not in (errno.ECONNREFUSED, errno.ENOENT):
                raise
            time.sleep(min(0.05, budget.remaining(budget.limit)))
            budget.remaining(budget.limit)
        except BaseException:
            unix._close(sock)
            raise


def read_mailbox(selected, *, listener_uid, budget):
    """Remote SSH helper in job netns: UID check, canonical request, digest ACK."""
    sock = None; error = None; clean = True; request = None
    try:
        _selector(selected)
        need(type(listener_uid) is int and 0 < listener_uid < 0xFFFFFFFF)
        sock = _connect_mailbox(selected, budget)
        pid, uid, _ = unix._peer_credentials(sock)
        need(pid > 0 and uid == listener_uid, 'P2_READY_PEER_REJECTED')
        reader = _SocketReader(sock, budget)
        request = _json_frame(bridge._frame(reader, bridge.PUBLIC_MAX, budget))
        _matches(request, selected); bridge._eof(reader, budget)
        sock.settimeout(budget.remaining(budget.limit))
        sock.sendall(bridge._receipt('public-ready-received', request))
        budget.remaining(budget.limit); sock.shutdown(socket.SHUT_WR)
    except BaseException as exc:
        error = exc
    finally:
        if sock is not None:
            clean = unix._close(sock)
    if error is not None:
        request = None
        _raise(error, 'P2_READY_FAILED' if clean else 'P2_READY_CLEANUP_FAILED')
    need(clean, 'P2_READY_CLEANUP_FAILED')
    budget.remaining(budget.limit)
    return request


class SshReadyPull(ingress._NoCopy):
    """One management SSH child for public request. No credential provider here."""
    def __init__(self, config, selected, *, deadline, monotonic):
        need(type(config) is bridge.SshBridgeConfig and callable(monotonic)
             and core.finite(deadline))
        config.check(); _selector(selected)
        self._config, self._selected = config, copy.deepcopy(selected)
        self._budget = unix._Budget(deadline, monotonic)
        self._used, self._lock = False, threading.Lock()

    def __repr__(self):
        return '<P2 SSH READY pull: single-use, no credentials>'

    def pull(self):
        with self._lock:
            need(not self._used, 'P2_READY_CONSUMED'); self._used = True
        proc = reader = writer = None
        raw_fds = []; error = None; clean = True; request = None
        try:
            need(sys.platform == 'linux')
            b = self._budget; b.remaining(b.limit)
            public = bridge._packet(self._selected)
            to_child = os.pipe(); raw_fds.extend(to_child)
            from_child = os.pipe(); raw_fds.extend(from_child)
            seconds = min(self._config.remote_timeout_seconds, math.ceil(b.remaining(b.limit)))
            proc = bridge.subprocess.Popen(self._config.argv(seconds), stdin=to_child[0],
                stdout=from_child[1], stderr=bridge.subprocess.DEVNULL, shell=False,
                close_fds=True, env={'LANG': 'C', 'LC_ALL': 'C'})
            for fd in (to_child[0], from_child[1]):
                raw_fds.remove(fd); os.close(fd)
            raw_fds.remove(to_child[1])
            writer = pipes._PipeEnd(to_child[1], write=True, deadline=b.limit, monotonic=b.clock)
            raw_fds.remove(from_child[0])
            reader = pipes._PipeEnd(from_child[0], write=False, deadline=b.limit, monotonic=b.clock)
            writer.write_all(public)
            need(bridge._clean(writer), 'P2_READY_CLEANUP_FAILED'); writer = None
            request = _json_frame(bridge._frame(reader, bridge.PUBLIC_MAX, b))
            _matches(request, self._selected); bridge._eof(reader, b)
            need(proc.wait(timeout=b.remaining(b.limit)) == 0, 'P2_READY_HELPER_FAILED')
            b.remaining(b.limit)
        except BaseException as exc:
            error = exc
        finally:
            for end in (writer, reader):
                if end is not None:
                    clean = bridge._clean(end) and clean
            for fd in raw_fds:
                try: os.close(fd)
                except BaseException: clean = False
            if proc is not None:
                reaped, interrupted = bridge._reap_owned(proc, self._budget)
                clean = reaped and clean
                if interrupted is not None:
                    error = KeyboardInterrupt() if interrupted is KeyboardInterrupt else SystemExit(130)
        if error is not None:
            request = None
            _raise(error, 'P2_READY_FAILED' if clean else 'P2_READY_CLEANUP_FAILED')
        need(clean, 'P2_READY_CLEANUP_FAILED')
        self._budget.remaining(self._budget.limit)
        return request  # Correlation + authenticated SSH peer, never issuer/GO proof.


def serve_owned_stdio(read_fd, write_fd, *, listener_uid, timeout_seconds, monotonic):
    kind = stdio.classify_pair(read_fd, write_fd)  # foreign FDs stay borrowed
    raw = [(read_fd, pipes.identity(read_fd)), (write_fd, pipes.identity(write_fd))]
    reader = writer = None; error = None; clean = True
    try:
        need(os.geteuid() > 0 and type(timeout_seconds) is int
             and 0 < timeout_seconds <= 2147483 and callable(monotonic))
        now = monotonic(); need(core.finite(now))
        budget = unix._Budget(now + timeout_seconds, monotonic)
        end = stdio.SocketStdioEnd if kind == 'socketpair' else pipes._PipeEnd
        reader = end(read_fd, write=False, deadline=budget.limit, monotonic=monotonic); raw.pop(0)
        writer = end(write_fd, write=True, deadline=budget.limit, monotonic=monotonic); raw.pop(0)
        selected = _json_frame(bridge._frame(reader, bridge.PUBLIC_MAX, budget))
        _selector(selected); bridge._eof(reader, budget)
        request = read_mailbox(selected, listener_uid=listener_uid, budget=budget)
        writer.write_all(bridge._packet(request))
        budget.remaining(budget.limit)
    except BaseException as exc:
        error = exc
    finally:
        for end in (reader, writer):
            if end is not None:
                clean = bridge._clean(end) and clean
        for fd, ident in raw:
            clean = stdio.close_owned_fd(fd, ident) and clean
    if error is not None:
        _raise(error, 'P2_READY_FAILED' if clean else 'P2_READY_CLEANUP_FAILED')
    need(clean, 'P2_READY_CLEANUP_FAILED')
    return True


def remote_main(argv=None):
    import re
    args = sys.argv[1:] if argv is None else argv
    fds = []
    try:
        need(len(args) == 2 and all(type(v) is str and re.fullmatch(r'[0-9]{1,10}', v) for v in args))
        _, r, w = stdio.duplicate_stdio()
        fds = [r, w]; stdio.classify_pair(r, w); fds = []
        serve_owned_stdio(r, w, listener_uid=int(args[0]), timeout_seconds=int(args[1]),
                          monotonic=time.monotonic)
        return 0
    except BaseException:
        return 1
    finally:
        for fd in fds:
            try: os.close(fd)
            except BaseException: pass
