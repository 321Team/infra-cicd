"""Synthetic token/socket replies only; outbound sockets are forbidden."""
from dataclasses import FrozenInstanceError, replace
import os
import ssl
import sys
import time
import unittest
from unittest.mock import Mock, patch

import delivery_adapter as core
import delivery_github_source as public
from delivery_private_source import (GitHubReadCredential, PrivateGitHubTLS,
    PrivateGitHubOriginalAdapter, GitHubPipeCredentialReceiver)
from delivery_credential_channel import CredentialChannelError
from delivery_object_reader import BearerCredential
from delivery_verifier import Progress, ReadRequest
from test_delivery_adapter import Harness, SENSITIVE
import test_delivery_github_source as original_tests
from test_delivery_transport import CA, FakeSocket, response

TOKEN = 'SYNTHETIC_GITHUB_READ_TOKEN_0123456789'


class PrivateSourceTests(unittest.TestCase):
    def setUp(self):
        for name in ('socket', 'getaddrinfo', 'create_connection'):
            p = patch.object(public.socket, name, side_effect=AssertionError('NETWORK_FORBIDDEN'))
            p.start(); self.addCleanup(p.stop)
        self.repo = public.RepositoryPin('synthetic-owner', 'synthetic-repo', 123, 321)
        self.credential = GitHubReadCredential(TOKEN, self.repo, 7)
        self.context = Mock(verify_mode=ssl.CERT_REQUIRED, check_hostname=True, keylog_filename=None)
        with patch.object(public.ssl, 'SSLContext', return_value=self.context):
            self.tls = PrivateGitHubTLS(credential=self.credential, ca_pem=CA,
                endpoint_ip='192.0.2.10', monotonic=lambda: 1)
        self.adapter = PrivateGitHubOriginalAdapter(self.tls)
        self.request = ReadRequest(Harness().binding, BearerCredential(SENSITIVE), Progress(), 8)
        self.prefix = '/repos/synthetic-owner/synthetic-repo'
        self.repository = {'id': 123, 'owner': {'id': 321},
                           'full_name': 'synthetic-owner/synthetic-repo', 'private': True}
        self.pin = public.CommentPin(self.repo, 7, 8, 9, '2026-01-01T01:02:03Z', core.digest(b'SYNTHETIC_NO_GO'))
        self.comment = {'id': 8, 'user': {'id': 9}, 'body': 'SYNTHETIC_NO_GO',
            'updated_at': self.pin.updated_at,
            'url': 'https://api.github.com' + self.prefix + '/issues/comments/8',
            'issue_url': 'https://api.github.com' + self.prefix + '/issues/7',
            'html_url': 'https://github.com/synthetic-owner/synthetic-repo/issues/7#issuecomment-8'}

    def get(self, incoming=None, path=None):
        sock = FakeSocket(response() if incoming is None else incoming)
        self.context.wrap_socket.return_value = sock
        with patch.object(public.socket, 'socket', return_value=sock):
            value = self.tls.get(self.prefix if path is None else path, 8)
        return value, sock

    def test_explicit_github_header_and_pinned_origin_no_rosa_credential(self):
        (_, obj), sock = self.get()
        self.assertEqual({'ok': True}, obj)
        self.assertEqual(('192.0.2.10', 443), sock.connected)
        self.assertEqual(1, bytes(sock.sent).count(b'Authorization: Bearer ' + TOKEN.encode()))
        self.assertNotIn(SENSITIVE.encode(), bytes(sock.sent))
        self.assertNotIn(TOKEN, repr(self.credential))
        self.context.wrap_socket.assert_called_once_with(sock,
            server_hostname='api.github.com', do_handshake_on_connect=False)
        self.assertTrue(sock.closed)

    def test_only_exact_repository_boundary_before_socket(self):
        for path in ('/repos/synthetic-owner/synthetic-repo-evil', '/repos/other/repo', '/user',
                     self.prefix + '/git/ref/heads/../../other', self.prefix + '/%2e%2e/other',
                     self.prefix + '//other', self.prefix + '?redirect=other',
                     self.prefix + '\r\nInjected: true'):
            with self.subTest(path=path), patch.object(public.socket, 'socket') as create, \
                    self.assertRaises(public.SourceError): self.tls.get(path, 8)
            create.assert_not_called()

    def test_credential_type_header_injection_and_expired_use_cap(self):
        with self.assertRaises(public.SourceError):
            PrivateGitHubTLS(credential=self.request.credential, ca_pem=CA, endpoint_ip='192.0.2.10')
        for token in ('', TOKEN + '\r\nHeader: injected', 'é' * 20, TOKEN + ' token', 'x' * 4097):
            with self.subTest(token_length=len(token)), self.assertRaises(public.SourceError):
                GitHubReadCredential(token, self.repo, 7)
        with patch.object(self.tls, 'now', return_value=7), patch.object(public.socket, 'socket') as create, \
                self.assertRaises(public.SourceError): self.tls.get(self.prefix, 8)
        create.assert_not_called()

    def test_closed_handle_and_transport_cannot_emit_again(self):
        self.credential.close()
        self.assertNotIn(TOKEN, repr(self.credential))
        with patch.object(public.socket, 'socket') as create, self.assertRaises(public.SourceError):
            self.tls.get(self.prefix, 8)
        create.assert_not_called()
        self.tls.close()
        self.assertIsNone(self.tls._credential)
        with self.assertRaises(public.SourceError): self.tls.get(self.prefix, 8)

    def test_repository_pin_is_immutable_and_public_adapter_rejects_private_transport(self):
        with self.assertRaises(FrozenInstanceError): self.credential.repository = replace(self.repo, name='other')
        with self.assertRaises(public.SourceError): public.GitHubOriginalAdapter(self.tls)

    def replies(self, *values):
        return patch.object(self.tls, 'get', side_effect=[(core.encoded(o), o) for o in values])

    def test_private_original_still_does_not_authenticate_grant_or_body_author(self):
        with self.replies(self.repository, self.comment): value = self.adapter.comment(self.request, self.pin)
        self.assertIs(value.request, self.request)
        self.assertFalse(value.execution_authorized)
        self.assertFalse(value.body_authorship_verified)
        self.assertEqual(9, value.comment_creator_id)

    def test_private_metadata_requires_pin_and_private_true(self):
        for change in ({'private': False}, {'private': 1}, {'id': True}, {'id': 124},
                       {'owner': {'id': 999}}, {'full_name': 'other/repo'}):
            with self.subTest(change=change), self.replies({**self.repository, **change}), \
                    self.assertRaises(public.SourceError): self.adapter.comment(self.request, self.pin)
        with patch.object(self.tls, 'get') as get, self.assertRaises(public.SourceError):
            self.adapter.comment(self.request, replace(self.pin, repository=replace(self.repo, name='other')))
        get.assert_not_called()
        for malformed in ([], None, {'owner': None}, {'owner': 'bad'}):
            with self.subTest(malformed_type=type(malformed).__name__), self.replies(malformed), \
                    self.assertRaises(public.SourceError): self.adapter.repository(self.request, self.repo, [])

    def test_blob_uses_existing_commit_reachability_checks(self):
        pin, body, values = original_tests.OriginalTests.blob_fixture(self)
        values[0] = self.repository
        with self.replies(*values): result = self.adapter.blob(self.request, pin)
        self.assertEqual(body, result.body)
        values[2]['status'] = 'diverged'
        with self.replies(*values), self.assertRaises(public.SourceError): self.adapter.blob(self.request, pin)

    def test_no_public_fallback_retry_or_secret_in_error(self):
        for status in (301, 304, 401, 403, 404, 429, 500):
            sock = FakeSocket(response(status=status)); self.context.wrap_socket.return_value = sock
            with patch.object(public.socket, 'socket', return_value=sock) as create, \
                    self.assertRaises(public.SourceError) as error: self.tls.get(self.prefix, 8)
            self.assertEqual(1, create.call_count)
            self.assertEqual(1, bytes(sock.sent).count(b'GET '))
            self.assertNotIn(TOKEN, str(error.exception)); self.assertTrue(sock.closed)

    def test_local_use_cap_bounds_handshake_and_read_not_server_expiry(self):
        sock = FakeSocket(); self.context.wrap_socket.return_value = sock
        observed = []
        def call(tls, operation, timer):
            observed.append(timer.end)
            return operation()
        with patch.object(public.socket, 'socket', return_value=sock), \
                patch.object(public.wire, 'tls_call', side_effect=call): self.tls.get(self.prefix, 8)
        self.assertTrue(observed); self.assertEqual({7}, set(observed))
        self.tls._clock = lambda: 0
        with patch.object(public.socket, 'socket') as create, self.assertRaises(public.SourceError):
            self.tls.get(self.prefix, 8)
        create.assert_not_called()


@unittest.skipUnless(sys.platform == 'linux', 'actual anonymous pipe requires Linux')
class PrivatePipeTests(unittest.TestCase):
    def setUp(self):
        self.repo = public.RepositoryPin('synthetic-owner', 'synthetic-repo', 123, 321)

    def receiver(self, data, **config):
        read, write = os.pipe()
        os.write(write, data); os.close(write)
        return GitHubPipeCredentialReceiver(read, repository=self.repo,
            local_use_until=time.monotonic() + 5, **config), read

    def test_typed_separate_pipe_single_use_and_owned_fd_closed(self):
        receiver, fd = self.receiver(TOKEN.encode())
        credential = receiver.receive(time.monotonic() + 3)
        self.assertIs(type(credential), GitHubReadCredential)
        self.assertNotIsInstance(credential, BearerCredential)
        self.assertEqual(self.repo, credential.repository)
        with self.assertRaises(OSError): os.fstat(fd)
        with self.assertRaises(CredentialChannelError): receiver.receive(time.monotonic() + 1)
        credential.close()

    def test_invalid_source_token_or_deadline_closes_owned_fd(self):
        for data, deadline in ((b'bad token', time.monotonic() + 3),
                               (TOKEN.encode(), float('nan'))):
            receiver, fd = self.receiver(data)
            with self.assertRaises((CredentialChannelError, public.SourceError)): receiver.receive(deadline)
            with self.assertRaises(OSError): os.fstat(fd)

    def test_invalid_configuration_closes_owned_fd(self):
        read, write = os.pipe(); os.close(write)
        with self.assertRaises(public.SourceError):
            GitHubPipeCredentialReceiver(read, repository=self.repo, local_use_until=0)
        with self.assertRaises(OSError): os.fstat(read)


if __name__ == '__main__':
    unittest.main()
