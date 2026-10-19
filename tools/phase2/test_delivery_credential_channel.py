"""Offline synthetic credentials only; Linux anonymous pipes, no API/token issue."""
from dataclasses import replace
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

import delivery_credential_channel as channel
import delivery_site as site
from delivery_provider import AuthorityAdapters
from test_delivery_site import SiteFixture

TOKEN = b'SYNTHETIC_NON_CREDENTIAL.a-b_c+de/f=='
LINUX = sys.platform == 'linux'


class ChannelCommonTests(unittest.TestCase):
    def test_refuse_standard_descriptors_and_invalid_types_without_owning_them(self):
        with patch.object(channel.os, 'close') as close:
            for value in (0, 1, 2, -1, True, 3.0, '3', None):
                with self.subTest(value=value), self.assertRaises(channel.CredentialChannelError):
                    channel.PipeCredentialReceiver(value)
        close.assert_not_called()

    def test_close_and_context_exit_are_idempotent(self):
        read, write = os.pipe(); os.close(write)
        receiver = channel.PipeCredentialReceiver(read)
        with receiver:
            pass
        receiver.close()
        with self.assertRaises(OSError): os.fstat(read)
        with patch.object(channel.os, 'close') as close, self.assertRaises(channel.CredentialChannelError):
            receiver.receive(1)
        close.assert_not_called()

    @unittest.skipIf(LINUX, 'unsupported platform guard only')
    def test_unsupported_platform_closes_without_read(self):
        read, write = os.pipe(); os.close(write)
        with patch.object(channel.os, 'read') as read_call, self.assertRaisesRegex(
                channel.CredentialChannelError, '^CHANNEL_LINUX_REQUIRED$'):
            channel.PipeCredentialReceiver(read).receive(time.monotonic() + 1)
        read_call.assert_not_called()
        with self.assertRaises(OSError): os.fstat(read)


@unittest.skipUnless(LINUX, 'Linux pipefs/select/readv tests run in hosted Linux CI')
class LinuxChannelTests(unittest.TestCase):
    def setUp(self):
        p = patch.object(socket, 'getaddrinfo', side_effect=AssertionError('DNS_FORBIDDEN'))
        p.start(); self.addCleanup(p.stop)

    def feed(self, data=TOKEN, *, eof=True):
        read, write = os.pipe()
        # A 16KiB fixture can exceed Linux pipe capacity. Feed concurrently so
        # the test itself cannot block before the receiver starts draining.
        def writer():
            try:
                offset = 0
                while offset < len(data):
                    offset += os.write(write, data[offset:])
            except BrokenPipeError:
                pass  # Expected if the receiver denies an oversized fixture.
            finally:
                if eof:
                    os.close(write)
        worker = threading.Thread(target=writer, daemon=True)
        worker.start()
        def cleanup():
            if not eof: os.close(write)
            worker.join(2)
            self.assertFalse(worker.is_alive(), 'synthetic writer did not stop')
        self.addCleanup(cleanup)
        return read

    def fail(self, fd, code, *, deadline=None, monotonic=time.monotonic):
        with self.assertRaisesRegex(channel.CredentialChannelError, '^' + code + '$'):
            channel.PipeCredentialReceiver(fd, monotonic=monotonic).receive(
                time.monotonic() + 1 if deadline is None else deadline)
        with self.assertRaises(OSError): os.fstat(fd)

    def test_anonymous_pipe_success_eof_close_and_repr_privacy(self):
        read = self.feed()
        receiver = channel.PipeCredentialReceiver(read)
        value = receiver.receive(time.monotonic() + 2)
        self.assertEqual(TOKEN.decode(), value.token)
        self.assertNotIn(TOKEN.decode(), repr(value))
        self.assertNotIn(str(read), repr(receiver))
        with self.assertRaises(OSError): os.fstat(read)

    def test_size_boundary_and_non_ascii_whitespace_never_stripped(self):
        read = self.feed(b'a' * channel.MAX_TOKEN_BYTES)
        value = channel.PipeCredentialReceiver(read).receive(time.monotonic() + 2)
        self.assertEqual(channel.MAX_TOKEN_BYTES, len(value.token))
        self.fail(self.feed(b'a' * (channel.MAX_TOKEN_BYTES + 1)), 'CHANNEL_SIZE_INVALID')
        for raw in (b'', TOKEN + b'\n', b' ' + TOKEN, b'a b', b'=abc', b'ab=c', b'\xff'):
            with self.subTest(raw=raw):
                self.fail(self.feed(raw), 'CHANNEL_FORMAT_INVALID')

    def test_data_without_eof_must_timeout_and_close(self):
        start = time.monotonic()
        self.fail(self.feed(eof=False), 'CHANNEL_DEADLINE_EXCEEDED', deadline=start + .08)
        self.assertLess(time.monotonic() - start, 1)

    def test_regular_file_named_fifo_and_unlinked_named_fifo_are_denied(self):
        with tempfile.TemporaryFile() as f:
            self.fail(os.dup(f.fileno()), 'CHANNEL_TYPE_INVALID')
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'fifo'; os.mkfifo(path, 0o600)
            self.fail(os.open(path, os.O_RDONLY | os.O_NONBLOCK), 'CHANNEL_TYPE_INVALID')
            fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK); path.unlink()
            self.fail(fd, 'CHANNEL_TYPE_INVALID')

    def test_socket_tty_write_end_and_wrong_owner_are_denied(self):
        a, b = socket.socketpair()
        try: self.fail(a.detach(), 'CHANNEL_TYPE_INVALID')
        finally: b.close()
        master, slave = os.openpty()
        try: self.fail(slave, 'CHANNEL_TYPE_INVALID')
        finally: os.close(master)
        read, write = os.pipe()
        try: self.fail(write, 'CHANNEL_ACCESS_INVALID')
        finally: os.close(read)
        with patch.object(channel.os, 'geteuid', return_value=os.geteuid() + 1):
            self.fail(self.feed(), 'CHANNEL_OWNER_INVALID')

    def test_expired_invalid_deadline_and_reversed_clock(self):
        self.fail(self.feed(), 'CHANNEL_DEADLINE_EXCEEDED', deadline=time.monotonic() - 1)
        for deadline in (float('nan'), float('inf'), True, '1'):
            self.fail(self.feed(), 'CHANNEL_DEADLINE_INVALID', deadline=deadline)
        for clock in (lambda: float('nan'), lambda: True, iter([5., 4.]).__next__, None):
            self.fail(self.feed(), 'CHANNEL_CLOCK_INVALID', deadline=99, monotonic=clock)

    def test_late_select_or_read_cannot_return_a_credential(self):
        # Arrival at EOF after expiry is not success, even with valid token data.
        for times in ([1, 1, 1, 1, 20], [1, 1, 1, 1, 1, 20]):
            self.fail(self.feed(), 'CHANNEL_DEADLINE_EXCEEDED', deadline=8,
                      monotonic=iter(times).__next__)

    def test_no_os_exception_text_and_second_receive_cannot_close_reused_fd(self):
        read = self.feed()
        receiver = channel.PipeCredentialReceiver(read)
        receiver.receive(time.monotonic() + 2)
        later = self.feed()
        try:
            with patch.object(channel.os, 'close') as close, \
                    self.assertRaisesRegex(channel.CredentialChannelError, '^CHANNEL_CONSUMED$'):
                receiver.receive(time.monotonic() + 2)
            close.assert_not_called()
            os.fstat(later)
        finally: os.close(later)
        read = self.feed()
        with patch.object(channel.os, 'readv', side_effect=OSError(TOKEN.decode())):
            self.fail(read, 'CHANNEL_RECEIVE_FAILED')

    def test_child_receives_by_inherited_fd_not_environment_or_token_argument(self):
        read = self.feed()
        script = ('import time; from delivery_credential_channel import PipeCredentialReceiver; '
                  f'c=PipeCredentialReceiver({read}).receive(time.monotonic()+2); '
                  'print(type(c).__name__); print(repr(c))')
        try:
            result = subprocess.run([sys.executable, '-c', script], pass_fds=(read,),
                                    capture_output=True, text=True, timeout=4)
        finally: os.close(read)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn('BearerCredential', result.stdout)
        self.assertNotIn(TOKEN.decode(), result.stdout + result.stderr + repr(result.args))


class ConnectionTests(SiteFixture, unittest.TestCase):
    def test_invalid_configuration_closes_channel_before_receive(self):
        read, write = os.pipe(); os.close(write)
        receiver = channel.PipeCredentialReceiver(read)
        with patch.object(receiver, 'receive') as receive, self.assertRaises(site.SiteError):
            channel.build_from_channel(replace(self.config, input_root='relative'),
                receiver=receiver, deadline=1, adapters=self.adapters)
        receive.assert_not_called()
        with self.assertRaises(OSError): os.fstat(read)

    def test_exact_handle_reaches_site_and_build_failure_closes_receiver(self):
        for fails in (False, True):
            read, write = os.pipe(); os.close(write)
            receiver = channel.PipeCredentialReceiver(read)
            with patch.object(receiver, 'receive', return_value=self.credential), \
                    patch.object(site, 'build_application', side_effect=RuntimeError('SYNTHETIC') if fails else None) as build:
                if fails:
                    with self.assertRaises(RuntimeError):
                        channel.build_from_channel(self.config, receiver=receiver, deadline=1, adapters=self.adapters)
                else:
                    channel.build_from_channel(self.config, receiver=receiver, deadline=1, adapters=self.adapters)
                self.assertIs(build.call_args.kwargs['credential'], self.credential)
                self.assertIs(build.call_args.kwargs['adapters'], self.adapters)
            with self.assertRaises(OSError): os.fstat(read)


if __name__ == '__main__':
    unittest.main()
