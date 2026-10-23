"""Explicit OpenSSH memory bridge. Inert configuration; no configured SITE.

Only send_from_supplier starts SSH. The remote helper runs in the existing job's
network namespace. No key creation, issuance implementation or operational values.
"""
from dataclasses import dataclass, field
import copy
import ipaddress
import json
import math
import os
import re
import socket
import stat
import struct
import subprocess
import sys
import threading
import time

import delivery_adapter as core
import delivery_crosshost_ingress as ingress
import delivery_crosshost_pipe as pipes
import delivery_job_rendezvous as rendezvous
import delivery_unix_ingress as unix
import delivery_ssh_stdio as stdio

PUBLIC_MAX = 8192
SCHEMA = 'P2-SSH-bridge-open-1'


class BridgeError(RuntimeError):
    """Fixed codes only; never native errors, command, output or credentials."""


def need(ok, code='P2_SSH_CONFIGURATION_INVALID'):
    if not ok:
        raise BridgeError(code)


def _path(value):
    # OpenSSH expands percent/tilde tokens even without a local shell. Reject
    # them and all remote-shell syntax, rather than trying to quote arbitrary code.
    return (type(value) is str and len(value) <= 1024 and value.startswith('/')
            and re.fullmatch(r'/[A-Za-z0-9_./-]+', value) is not None
            and all(p not in ('', '.', '..') for p in value.split('/')[1:]))


@dataclass(frozen=True, repr=False)
class SshBridgeConfig:
    ssh_binary: str = field(repr=False)
    host_ip: str = field(repr=False)
    port: int
    user: str = field(repr=False)
    host_key_alias: str = field(repr=False)
    known_hosts: str = field(repr=False)
    identity_file: str = field(repr=False)
    remote_python: str = field(repr=False)
    remote_helper: str = field(repr=False)
    listener_uid: int
    remote_timeout_seconds: int

    def check(self):
        need(all(_path(v) for v in (self.ssh_binary, self.known_hosts,
            self.identity_file, self.remote_python, self.remote_helper)))
        need(type(self.host_ip) is str)
        try:
            address = ipaddress.ip_address(self.host_ip)
            need(str(address) == self.host_ip and '%' not in self.host_ip)
        except ValueError:
            raise BridgeError('P2_SSH_CONFIGURATION_INVALID') from None
        need(type(self.port) is int and 0 < self.port <= 65535
             and type(self.user) is str and self.user != 'root'
             and re.fullmatch(r'[a-z_][a-z0-9_-]{0,63}', self.user)
             and type(self.host_key_alias) is str
             and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,252}', self.host_key_alias)
             and type(self.listener_uid) is int and 0 < self.listener_uid < 0xFFFFFFFF
             and type(self.remote_timeout_seconds) is int
             and 0 < self.remote_timeout_seconds <= 2147483)

    def argv(self, remote_budget=None):
        self.check()
        if remote_budget is None:
            remote_budget = self.remote_timeout_seconds
        need(type(remote_budget) is int and 0 < remote_budget <= self.remote_timeout_seconds)
        options = ('BatchMode=yes', 'IdentitiesOnly=yes', 'StrictHostKeyChecking=yes',
            'GlobalKnownHostsFile=/dev/null', 'UpdateHostKeys=no', 'VerifyHostKeyDNS=no',
            'CanonicalizeHostname=no', 'IdentityAgent=none', 'ForwardAgent=no',
            'ForwardX11=no', 'ClearAllForwardings=yes', 'ProxyCommand=none',
            'ProxyJump=none', 'ControlMaster=no', 'ControlPath=none', 'ControlPersist=no',
            'PermitLocalCommand=no', 'RequestTTY=no', 'LogLevel=QUIET',
            'PasswordAuthentication=no', 'KbdInteractiveAuthentication=no',
            'PreferredAuthentications=publickey', 'ConnectionAttempts=1')
        args = [self.ssh_binary, '-F', '/dev/null', '-T', '-a', '-x']
        for option in options:
            args += ['-o', option]
        args += ['-o', 'UserKnownHostsFile=' + self.known_hosts,
                 '-o', 'HostKeyAlias=' + self.host_key_alias,
                 '-i', self.identity_file, '-p', str(self.port), '-l', self.user,
                 '--', self.host_ip, self.remote_python, '-I', self.remote_helper,
                 str(self.listener_uid), str(remote_budget)]
        return args


def _packet(value):
    raw = core.encoded(value)
    need(0 < len(raw) <= PUBLIC_MAX, 'P2_SSH_PROTOCOL_INVALID')
    return struct.pack('!I', len(raw)) + raw


def _receipt(stage, request):
    return _packet({'schema': SCHEMA, 'stage': stage,
                    'request_sha256': core.digest(core.encoded(request))})


def _exact(reader, count, budget):
    result = bytearray()
    while len(result) < count:
        budget.remaining(budget.limit)
        chunk = reader.read(min(ingress.CHUNK_BYTES, count - len(result)), budget.limit)
        need(type(chunk) is bytes and 0 < len(chunk) <= count - len(result),
             'P2_SSH_PROTOCOL_INVALID')
        result.extend(chunk)
    return bytes(result)


def _frame(reader, maximum, budget):
    header = _exact(reader, 4, budget)
    size = struct.unpack('!I', header)[0]
    need(0 < size <= maximum, 'P2_SSH_PROTOCOL_INVALID')
    return header + _exact(reader, size, budget)


def _eof(reader, budget):
    need(reader.read(1, budget.limit) == b'', 'P2_SSH_PROTOCOL_INVALID')
    budget.remaining(budget.limit)


def _clean(value):
    try:
        return value.close() is True
    except BaseException:
        return False


def _pair_shape(read_fd, write_fd):
    need(sys.platform == 'linux' and type(read_fd) is int and type(write_fd) is int
         and read_fd >= 3 and write_fd >= 3 and read_fd != write_fd)
    for fd, direction in ((read_fd, os.O_RDONLY), (write_fd, os.O_WRONLY)):
        need(stat.S_ISFIFO(os.fstat(fd).st_mode) and pipes.identity(fd)[2] == direction)


def _reap_owned(proc, budget):
    interrupted = None
    def remember(exc):
        nonlocal interrupted
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            interrupted = type(exc)
    try:
        if proc.poll() is not None:
            return True, None
        proc.terminate()
        proc.wait(timeout=budget.remaining(budget.limit))
    except BaseException as exc:
        # Interrupt during terminate/wait must not skip kill/reap of our child.
        remember(exc)
        try:
            proc.kill()
        except BaseException as kill_error:
            remember(kill_error)
        # Attempt reap even when kill was interrupted or the child already exited.
        try:
            proc.wait(timeout=1)  # Cleanup only, never renewed execution budget.
        except BaseException as wait_error:
            remember(wait_error)
    try:
        return proc.poll() is not None, interrupted
    except BaseException:
        return False, interrupted


def _raise(error):
    if isinstance(error, KeyboardInterrupt):
        raise KeyboardInterrupt() from None
    if isinstance(error, SystemExit):
        raise SystemExit(130) from None
    if type(error) is BridgeError and error.args and error.args[0] in {
            'P2_SSH_CONFIGURATION_INVALID', 'P2_SSH_PROTOCOL_INVALID',
            'P2_SSH_CONSUMED', 'P2_SSH_PEER_REJECTED', 'P2_SSH_CLEANUP_FAILED'}:
        raise BridgeError(error.args[0]) from None
    raise BridgeError('P2_SSH_BRIDGE_FAILED') from None


class SshCredentialBridge(ingress._NoCopy):
    """Single SSH child, authenticated READY before trusted one-use supplier.

    READY authenticates no issuer/grant. Strict OpenSSH host authentication plus
    reviewed helper/UID/netns mapping is a precondition. Supplier must separately
    authorize and honor this host's unchanged absolute deadline. No auto retry.
    """
    def __init__(self, config, *, deadline, monotonic):
        need(type(config) is SshBridgeConfig and callable(monotonic) and core.finite(deadline))
        config.check()
        self._config, self._budget = config, unix._Budget(deadline, monotonic)
        self._used = self._supply_attempted = False
        self._lock = threading.Lock()

    def __repr__(self):
        return '<P2 SSH memory bridge: private, single-use, unconfigured SITE>'

    @property
    def supply_attempted(self):
        with self._lock:
            return self._supply_attempted

    def send_from_supplier(self, request, *, provide_credentials):
        with self._lock:
            need(not self._used, 'P2_SSH_CONSUMED')
            self._used = True
        proc = reader = writer = None
        raw_fds, error, cleanup = [], None, True
        credentials = frame = None
        try:
            need(sys.platform == 'linux' and callable(provide_credentials))
            ingress._request(request); expected = copy.deepcopy(request)
            b = self._budget; b.remaining(b.limit)
            public = _packet({'schema': SCHEMA, 'request': expected})
            # Register every raw pipe immediately. Only these child ends and our
            # exact child are owned; never process groups, paths or foreign PIDs.
            to_child = os.pipe(); raw_fds.extend(to_child)
            from_child = os.pipe(); raw_fds.extend(from_child)
            remote_budget = min(self._config.remote_timeout_seconds, math.ceil(b.remaining(b.limit)))
            proc = subprocess.Popen(self._config.argv(remote_budget), stdin=to_child[0],
                stdout=from_child[1], stderr=subprocess.DEVNULL, shell=False,
                close_fds=True, env={'LANG': 'C', 'LC_ALL': 'C'})
            for fd in (to_child[0], from_child[1]):
                raw_fds.remove(fd); os.close(fd)
            b.remaining(b.limit)
            raw_fds.remove(to_child[1])
            writer = pipes._PipeEnd(to_child[1], write=True, deadline=b.limit, monotonic=b.clock)
            raw_fds.remove(from_child[0])
            reader = pipes._PipeEnd(from_child[0], write=False, deadline=b.limit, monotonic=b.clock)
            writer.write_all(public)
            ready = _receipt('ready', expected)
            need(_exact(reader, len(ready), b) == ready, 'P2_SSH_PEER_REJECTED')
            b.remaining(b.limit)
            need(proc.poll() is None, 'P2_SSH_PEER_REJECTED')
            with self._lock:
                self._supply_attempted = True
            credentials = provide_credentials(copy.deepcopy(expected), b.limit)
            b.remaining(b.limit)
            need(type(credentials) is tuple and len(credentials) == 2)
            frame = ingress.encode_frame(expected, rosa_token=credentials[0], source_tokens=credentials[1])
            writer.write_all(frame)
            need(_clean(writer), 'P2_SSH_CLEANUP_FAILED'); writer = None  # EOF, no packet file.
            ack = _receipt('forwarded', expected)
            need(_exact(reader, len(ack), b) == ack, 'P2_SSH_PROTOCOL_INVALID')
            _eof(reader, b)
            need(proc.wait(timeout=b.remaining(b.limit)) == 0, 'P2_SSH_PROTOCOL_INVALID')
            b.remaining(b.limit)
        except BaseException as exc:
            error = exc
        finally:
            credentials = frame = None  # Reference release, not erasure/revocation.
            for end in (writer, reader):
                if end is not None:
                    cleanup = _clean(end) and cleanup
            for fd in raw_fds:
                try:
                    os.close(fd)
                except BaseException:
                    cleanup = False
            if proc is not None:
                reaped, interrupted = _reap_owned(proc, self._budget)
                cleanup = reaped and cleanup
                if interrupted is not None:
                    error = KeyboardInterrupt() if interrupted is KeyboardInterrupt else SystemExit(130)
            if not cleanup:
                error = BridgeError('P2_SSH_CLEANUP_FAILED')
            elif error is None:
                try:
                    self._budget.remaining(self._budget.limit)
                except BaseException as exc:
                    error = exc
        if error is not None:
            _raise(error)
        return True  # Transport forwarded/closed only, NOT job acceptance/GO.


def serve_owned_pipes(read_fd, write_fd, *, listener_uid, timeout_seconds, monotonic):
    """Remote helper core; explicit owned pipe ends (not global stdin/out).

    Use only inside reviewed SSH session in same existing job network namespace.
    Two hosts do not share monotonic clocks; remote uses its own one-shot ceiling.
    Sender's original deadline remains authoritative. Never enters another netns.
    """
    # Foreign descriptors stay borrowed. Once both qualify, own both even if the
    # configuration/clock/first endpoint constructor fails before the second.
    _pair_shape(read_fd, write_fd)
    return _serve_owned(read_fd, write_fd, listener_uid=listener_uid,
        timeout_seconds=timeout_seconds, monotonic=monotonic, socket_stdio=False)


def serve_owned_stdio(read_fd, write_fd, *, listener_uid, timeout_seconds, monotonic):
    """Owned qualified SSH stdio only; arbitrary sockets remain rejected."""
    kind = stdio.classify_pair(read_fd, write_fd)
    return _serve_owned(read_fd, write_fd, listener_uid=listener_uid,
        timeout_seconds=timeout_seconds, monotonic=monotonic, socket_stdio=kind == 'socketpair')


def _serve_owned(read_fd, write_fd, *, listener_uid, timeout_seconds, monotonic, socket_stdio):
    raw_fds = [(read_fd, pipes.identity(read_fd)), (write_fd, pipes.identity(write_fd))]
    b = None
    reader = writer = sock = None
    error = None; cleanup = True; frame = None
    try:
        need(os.geteuid() > 0 and type(listener_uid) is int and 0 < listener_uid < 0xFFFFFFFF
             and type(timeout_seconds) is int and 0 < timeout_seconds <= 2147483
             and callable(monotonic))
        now = monotonic(); need(core.finite(now))
        b = unix._Budget(now + timeout_seconds, monotonic)
        end = stdio.SocketStdioEnd if socket_stdio else pipes._PipeEnd
        reader = end(read_fd, write=False, deadline=b.limit, monotonic=monotonic)
        raw_fds.pop(0)
        writer = end(write_fd, write=True, deadline=b.limit, monotonic=monotonic)
        raw_fds.pop(0)
        packet = _frame(reader, PUBLIC_MAX, b)
        value = json.loads(packet[4:].decode('utf-8'))
        need(type(value) is dict and set(value) == {'schema', 'request'}
             and value['schema'] == SCHEMA and _packet(value) == packet, 'P2_SSH_PROTOCOL_INVALID')
        request = value['request']; ingress._request(request)
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.set_inheritable(False); sock.settimeout(b.remaining(b.limit))
        sock.connect(rendezvous.abstract_address(request))
        pid, uid, _ = unix._peer_credentials(sock)
        need(pid > 0 and uid == listener_uid, 'P2_SSH_PEER_REJECTED')
        b.remaining(b.limit)
        writer.write_all(_receipt('ready', request))
        frame = _frame(reader, ingress.MAX_MESSAGE_BYTES, b)
        _eof(reader, b)  # No partial or trailing message reaches the job.
        sock.settimeout(b.remaining(b.limit)); sock.sendall(frame)
        b.remaining(b.limit); sock.shutdown(socket.SHUT_WR)
        need(unix._close(sock), 'P2_SSH_CLEANUP_FAILED'); sock = None
        writer.write_all(_receipt('forwarded', request))
    except BaseException as exc:
        error = exc
    finally:
        frame = None
        if sock is not None:
            cleanup = unix._close(sock) and cleanup
        for end in (reader, writer):
            if end is not None:
                cleanup = _clean(end) and cleanup
        for fd, ident in raw_fds:
            cleanup = stdio.close_owned_fd(fd, ident) and cleanup
        if not cleanup:
            error = BridgeError('P2_SSH_CLEANUP_FAILED')
        elif error is None:
            try:
                b.remaining(b.limit)
            except BaseException as exc:
                error = exc
    if error is not None:
        _raise(error)
    return True


def remote_main(argv=None):
    """Fixed SSH helper entrypoint; qualify stdio dups, never config/secret load."""
    args = sys.argv[1:] if argv is None else argv
    fds = []
    try:
        need(len(args) == 2 and all(type(v) is str and re.fullmatch(r'[0-9]{1,10}', v) for v in args))
        _, read_fd, write_fd = stdio.duplicate_stdio()
        fds = [read_fd, write_fd]
        stdio.classify_pair(read_fd, write_fd)  # Before transferring both dups.
        read_fd, write_fd = fds; fds = []
        serve_owned_stdio(read_fd, write_fd, listener_uid=int(args[0]),
                          timeout_seconds=int(args[1]), monotonic=time.monotonic)
        return 0
    except BaseException:
        return 1  # No exception/output/token to stderr or stdout.
    finally:
        for fd in fds:
            try:
                os.close(fd)
            except BaseException:
                pass


if __name__ == '__main__':
    sys.exit(remote_main())
