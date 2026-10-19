"""One-use Linux anonymous-pipe handoff. No credential issuance or validation.

The reviewed parent transfers ownership of an existing read FD, not a path,
environment value, command argument or stdin. Same-UID process compromise is
outside this channel's threat model. Returned strings cannot be securely wiped.
"""
import os
import re
import selectors
import stat
import sys
import time

import delivery_adapter as core
from delivery_object_reader import BearerCredential

MAX_TOKEN_BYTES = 16384
MAX_HANDOFF_SECONDS = 10
TOKEN = re.compile(rb'[A-Za-z0-9._~+/-]+=*')


class CredentialChannelError(RuntimeError):
    """Fixed codes only; no exception chains or token bytes in output."""


def need(ok, code):
    if not ok:
        raise CredentialChannelError(code)


def pipe_device():
    # Linux pipefs device, NOT st_nlink: anonymous pipes can have nlink == 1.
    # A named FIFO lives on its containing filesystem, even after unlinking it.
    read, write = os.pipe()
    try:
        return os.fstat(read).st_dev
    finally:
        os.close(read)
        os.close(write)


class PipeCredentialReceiver:
    """Owns a non-standard FD from construction; close/receive are single-use.

    Alters O_NONBLOCK on the owned open file description. The parent must not
    keep another consumer or log input bytes. This does not authenticate a token.
    """
    def __init__(self, fd, *, monotonic=time.monotonic):
        need(type(fd) is int and fd >= 3, 'CHANNEL_FD_INVALID')
        self._fd, self._clock, self._last = fd, monotonic, None

    def __repr__(self):
        return '<P2 PipeCredentialReceiver: private, single-use>'

    def now(self):
        try:
            value = self._clock()
            need(core.finite(value) and (self._last is None or value >= self._last),
                 'CHANNEL_CLOCK_INVALID')
            self._last = value
            return value
        except Exception:
            raise CredentialChannelError('CHANNEL_CLOCK_INVALID') from None

    def close(self):
        fd, self._fd = self._fd, None
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass

    def __enter__(self):
        return self

    def __exit__(self, *unused):
        self.close()

    def receive(self, deadline):
        fd, self._fd = self._fd, None
        need(fd is not None, 'CHANNEL_CONSUMED')  # Never touch a reused FD number.
        buffer = bytearray(MAX_TOKEN_BYTES + 1)
        used = 0
        try:
            need(sys.platform == 'linux', 'CHANNEL_LINUX_REQUIRED')
            import fcntl  # Module import remains safe on Windows.
            need(core.finite(deadline), 'CHANNEL_DEADLINE_INVALID')
            deadline = min(deadline, self.now() + MAX_HANDOFF_SECONDS)
            need(self.now() < deadline, 'CHANNEL_DEADLINE_EXCEEDED')
            info = os.fstat(fd)
            flags = fcntl.fcntl(fd, fcntl.F_GETFL)
            need(stat.S_ISFIFO(info.st_mode) and info.st_dev == pipe_device()
                 and not os.isatty(fd), 'CHANNEL_TYPE_INVALID')
            need(info.st_uid == os.geteuid(), 'CHANNEL_OWNER_INVALID')
            need(flags & os.O_ACCMODE == os.O_RDONLY, 'CHANNEL_ACCESS_INVALID')
            os.set_inheritable(fd, False)
            os.set_blocking(fd, False)
            view = memoryview(buffer)
            with selectors.DefaultSelector() as selector:
                selector.register(fd, selectors.EVENT_READ)
                while True:
                    left = deadline - self.now()
                    need(left > 0, 'CHANNEL_DEADLINE_EXCEEDED')
                    ready = selector.select(left)
                    need(self.now() < deadline, 'CHANNEL_DEADLINE_EXCEEDED')
                    if not ready:
                        continue
                    try:
                        count = os.readv(fd, [view[used:]])
                    except (BlockingIOError, InterruptedError):
                        continue
                    need(self.now() < deadline, 'CHANNEL_DEADLINE_EXCEEDED')
                    if not count:
                        break  # EOF is required even after a syntactically valid token.
                    used += count
                    need(used <= MAX_TOKEN_BYTES, 'CHANNEL_SIZE_INVALID')
            need(0 < used <= MAX_TOKEN_BYTES and TOKEN.fullmatch(view[:used]),
                 'CHANNEL_FORMAT_INVALID')
            token = str(view[:used], 'ascii')
            need(self.now() < deadline, 'CHANNEL_DEADLINE_EXCEEDED')
            return BearerCredential(token)
        except CredentialChannelError:
            raise
        except Exception:
            raise CredentialChannelError('CHANNEL_RECEIVE_FAILED') from None
        finally:
            buffer[:] = bytes(len(buffer))  # Scratch cleanup only, no memory-erasure guarantee.
            try:
                os.close(fd)
            except OSError:
                pass


def build_from_channel(config, *, receiver, deadline, adapters):
    """Optional reviewed-code connection; never called by the standalone CLI.

    Always closes the receiver, including invalid config or application build
    failure. The authenticating platform adapter still has to verify this exact
    credential's identity, issuer, audience, expiry and RBAC before any sender.
    """
    from delivery_provider import AuthorityAdapters
    import delivery_site as site
    need(type(receiver) is PipeCredentialReceiver, 'CHANNEL_RECEIVER_REQUIRED')
    with receiver:
        site.configuration_checked(config)
        need(type(adapters) is AuthorityAdapters and all(callable(getattr(adapters, name))
             for name in site.ADAPTERS), 'CHANNEL_AUTHENTICATORS_REQUIRED')
        credential = receiver.receive(deadline)
        return site.build_application(config, credential=credential, adapters=adapters)
