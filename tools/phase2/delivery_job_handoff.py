"""Reviewed parent -> separate prebuffered pipes -> existing P2 invocation.

Only explicitly supplied, already-issued in-memory strings are copied. No
issuance, authentication, env/path/argv/stdin loader, target discovery, server
access or authority adoption. SITE remains unconfigured. Local close is neither
server revocation nor secure erasure of borrowed strings or Python copies.
"""
from dataclasses import dataclass, field
import errno
import hmac
import os
import sys
import time

import delivery_adapter as core
import delivery_job as job
import delivery_runtime_site as runtime
import delivery_site as site
from delivery_credential_channel import PipeCredentialReceiver, MAX_TOKEN_BYTES, MAX_HANDOFF_SECONDS, TOKEN
from delivery_github_source import repository_checked
from delivery_private_source import GitHubPipeCredentialReceiver
from delivery_source_session import PrivateSourceSession, SourceChannel


class HandoffError(RuntimeError):
    """Fixed codes only; never print a token, callback error or native path."""


SAFE_CODES = frozenset('''
P2_HANDOFF_CONFIGURATION_INVALID P2_HANDOFF_TOKEN_INVALID P2_HANDOFF_TOKEN_ALIASED
P2_HANDOFF_CLOCK_INVALID P2_HANDOFF_DEADLINE_EXCEEDED P2_HANDOFF_LINUX_REQUIRED
P2_HANDOFF_PIPE_CAPACITY P2_HANDOFF_PIPE_WRITE_FAILED P2_HANDOFF_PREPARATION_FAILED
P2_HANDOFF_CLEANUP_FAILED
'''.split())


def need(ok, code):
    if not ok:
        raise HandoffError(code)


@dataclass(frozen=True, repr=False)
class SourceHandoff:
    repository: object = field(repr=False)
    token: str = field(repr=False)
    local_use_until: float = field(repr=False)
    ca_pem: bytes = field(repr=False)
    endpoint_ip: str = field(repr=False)

    def __repr__(self):
        return '<P2 SourceHandoff: private, not authenticated>'


class _Clock:
    def __init__(self, read):
        self.read, self.last = read, None

    def __call__(self):
        try:
            value = self.read()
            need(core.finite(value) and (self.last is None or value >= self.last),
                 'P2_HANDOFF_CLOCK_INVALID')
            self.last = value
            return value
        except Exception:
            raise HandoffError('P2_HANDOFF_CLOCK_INVALID') from None


def _token(value, minimum, maximum):
    need(type(value) is str and minimum <= len(value) <= maximum, 'P2_HANDOFF_TOKEN_INVALID')
    try:
        raw = value.encode('ascii')
    except UnicodeError:
        raise HandoffError('P2_HANDOFF_TOKEN_INVALID') from None
    need(TOKEN.fullmatch(raw) is not None, 'P2_HANDOFF_TOKEN_INVALID')
    return raw


def _identity(fd):
    info = os.fstat(fd)
    return info.st_dev, info.st_ino


def _close_fd(fd, identity=None):
    if fd is None:
        return True
    try:
        if identity is not None and _identity(fd) != identity:
            return True  # The old owned FD was closed/reused; never close its new owner.
        os.close(fd)
        return True
    except OSError as error:
        return error.errno == errno.EBADF


def _prebuffer(raw, clock, deadline):
    """One bounded nonblocking write, then EOF, before handing over a read FD."""
    import fcntl  # Linux only, imported after the explicit platform gate.
    read = write = None
    read_id = write_id = None
    transferred = False
    try:
        need(clock() < deadline, 'P2_HANDOFF_DEADLINE_EXCEEDED')
        read, write = os.pipe2(os.O_CLOEXEC | os.O_NONBLOCK)
        need(type(read) is int and type(write) is int and read >= 3 and write >= 3
             and read != write, 'P2_HANDOFF_CONFIGURATION_INVALID')
        read_id, write_id = _identity(read), _identity(write)
        capacity = fcntl.fcntl(write, fcntl.F_GETPIPE_SZ)
        need(type(capacity) is int and capacity >= len(raw), 'P2_HANDOFF_PIPE_CAPACITY')
        need(clock() < deadline, 'P2_HANDOFF_DEADLINE_EXCEEDED')
        try:
            count = os.write(write, raw)
        except OSError:
            raise HandoffError('P2_HANDOFF_PIPE_WRITE_FAILED') from None
        need(type(count) is int and count == len(raw), 'P2_HANDOFF_PIPE_WRITE_FAILED')
        need(clock() < deadline, 'P2_HANDOFF_DEADLINE_EXCEEDED')
        need(_close_fd(write, write_id), 'P2_HANDOFF_CLEANUP_FAILED')
        write = None  # No retained writer hides EOF; failure still enters cleanup.
        transferred = True
        return read, read_id
    finally:
        ok = _close_fd(write, write_id)
        if not transferred:
            ok = _close_fd(read, read_id) and ok
        if not ok:
            raise HandoffError('P2_HANDOFF_CLEANUP_FAILED') from None


def _receiver(raw, clock, deadline, source=None):
    fd = identity = None
    try:
        fd, identity = _prebuffer(raw, clock, deadline)
        # Valid FD ownership transfers immediately to these exact constructors.
        if source is None:
            return PipeCredentialReceiver(fd, monotonic=clock)
        return GitHubPipeCredentialReceiver(fd, repository=source.repository,
            local_use_until=source.local_use_until, monotonic=clock)
    except BaseException:
        # The source constructor may have closed before raising. A reused FD
        # number must not make parent cleanup close an unrelated new handle.
        if not _close_fd(fd, identity):
            raise HandoffError('P2_HANDOFF_CLEANUP_FAILED') from None
        raise


def build_owned_invocation(context, command, *, config, plan, finish_slot,
        rosa_token, sources, wire, handoff_deadline, monotonic=time.monotonic,
        finish_capture=None, producer_pins=None, native_original=None):
    """A trusted JobSite.build helper, not a default builder or credential broker.

    Caller owns borrowed input strings. Before successful return this helper
    owns created pipes/session; afterwards the existing driver owns them. The
    fixed source repositories/CA/IP/caps and all runtime facts remain reviewed
    caller arguments. The original absolute handoff deadline is never renewed.
    """
    rosa = session = pending_receiver = None
    source_receivers = []
    try:
        need(type(context) is job.JobContext and command in ('preflight', 'deploy', 'restore')
             and callable(monotonic) and callable(wire), 'P2_HANDOFF_CONFIGURATION_INVALID')
        context.check()
        site.configuration_checked(config)
        need(runtime.plan_binding(plan) == config.binding
             and command in ('preflight', config.binding.action), 'P2_HANDOFF_CONFIGURATION_INVALID')
        if type(plan) is runtime.PreparedAuthorityPlan:
            plan.bind_context(context)
        need(type(sources) is tuple and 0 < len(sources) <= 16
             and all(type(x) is SourceHandoff for x in sources), 'P2_HANDOFF_CONFIGURATION_INVALID')
        clock = _Clock(monotonic)
        start = clock()
        need(core.finite(handoff_deadline), 'P2_HANDOFF_CONFIGURATION_INVALID')
        deadline = min(handoff_deadline, start + MAX_HANDOFF_SECONDS)
        need(start < deadline, 'P2_HANDOFF_DEADLINE_EXCEEDED')
        rosa_raw = _token(rosa_token, 1, MAX_TOKEN_BYTES)
        from delivery_source_session import NativeIssuanceOriginal
        native = None
        if native_original is not None:
            native = NativeIssuanceOriginal(context, config.binding, core.digest(rosa_raw), native_original)
            native.check()  # Before any pipe allocation; no parser or authority claim.
        repos, payloads = set(), []
        for source in sources:
            repository_checked(source.repository)
            need(source.repository not in repos and core.finite(source.local_use_until)
                 and start < source.local_use_until and type(source.ca_pem) is bytes
                 and bool(source.ca_pem) and type(source.endpoint_ip) is str
                 and bool(source.endpoint_ip), 'P2_HANDOFF_CONFIGURATION_INVALID')
            repos.add(source.repository)
            raw = _token(source.token, 20, 4096)
            need(not hmac.compare_digest(rosa_raw, raw), 'P2_HANDOFF_TOKEN_ALIASED')
            payloads.append(raw)
        need(sys.platform == 'linux', 'P2_HANDOFF_LINUX_REQUIRED')
        rosa = _receiver(rosa_raw, clock, deadline)
        channels = []
        for source, raw in zip(sources, payloads):
            pending_receiver = _receiver(raw, clock, deadline, source)
            source_receivers.append(pending_receiver)
            channels.append(SourceChannel(pending_receiver, source.ca_pem, source.endpoint_ip))
            pending_receiver = None
        need(clock() < deadline, 'P2_HANDOFF_DEADLINE_EXCEEDED')
        session = PrivateSourceSession(tuple(channels), wire=wire, monotonic=clock,
                                       native_original=native)
        invocation = job.RuntimeInvocation(context, config, plan, finish_slot,
            rosa, deadline, source_session=session, finish_capture=finish_capture,
            producer_pins=producer_pins)
        invocation.check(context, command)
        return invocation
    except BaseException as error:
        ok = True
        prepared = plan if type(plan) is runtime.PreparedAuthorityPlan else None
        for handle in (session, pending_receiver, *reversed(source_receivers), rosa, prepared):
            if handle is not None:
                try:
                    result = handle.close()
                    if type(handle) is PrivateSourceSession and result is not True:
                        ok = False
                except BaseException:
                    ok = False  # Try every owned handle; never close borrowed inputs.
        if isinstance(error, (KeyboardInterrupt, SystemExit)):
            raise
        code = error.args[0] if type(error) is HandoffError and len(error.args) == 1 else None
        code = code if type(code) is str and code in SAFE_CODES else 'P2_HANDOFF_PREPARATION_FAILED'
        raise HandoffError(code if ok else 'P2_HANDOFF_CLEANUP_FAILED') from None
