"""Synthetic GitHub replies and injected sockets only. No original or Runtime."""
import base64
from dataclasses import replace
import errno
import hashlib
import json
import ssl
import unittest
from unittest.mock import Mock, patch

import delivery_adapter as core
import delivery_github_source as source
from delivery_object_reader import BearerCredential
from delivery_verifier import Progress, ReadRequest
from test_delivery_adapter import Harness, SENSITIVE
from test_delivery_transport import CA, FakeSocket, response


class SourceFixture(unittest.TestCase):
    def setUp(self):
        for name in ('socket', 'getaddrinfo', 'create_connection'):
            p = patch.object(source.socket, name, side_effect=AssertionError('NETWORK_FORBIDDEN'))
            p.start(); self.addCleanup(p.stop)
        self.context = Mock(verify_mode=ssl.CERT_REQUIRED, check_hostname=True, keylog_filename=None)
        with patch.object(source.ssl, 'SSLContext', return_value=self.context):
            self.tls = source.PublicGitHubTLS(ca_pem=CA, endpoint_ip='192.0.2.10', monotonic=lambda: 1)
        self.request = ReadRequest(Harness().binding, BearerCredential(SENSITIVE), Progress(), 8)
        self.repo = source.RepositoryPin('synthetic-owner', 'synthetic-repo', 123, 321)


class OriginalTests(SourceFixture):
    def setUp(self):
        super().setUp()
        self.adapter = source.GitHubOriginalAdapter(self.tls)
        self.prefix = '/repos/synthetic-owner/synthetic-repo'
        self.repository = {'id': 123, 'owner': {'id': 321},
                           'full_name': 'synthetic-owner/synthetic-repo', 'private': False}
        self.pin = source.CommentPin(self.repo, 7, 8, 9, '2026-01-01T01:02:03Z',
                                     core.digest(b'SYNTHETIC_NO_GO'))
        self.comment = {'id': 8, 'user': {'id': 9}, 'body': 'SYNTHETIC_NO_GO',
            'updated_at': self.pin.updated_at,
            'url': 'https://api.github.com' + self.prefix + '/issues/comments/8',
            'issue_url': 'https://api.github.com' + self.prefix + '/issues/7',
            'html_url': 'https://github.com/synthetic-owner/synthetic-repo/issues/7#issuecomment-8'}

    def replies(self, *objects):
        return patch.object(self.tls, 'get', side_effect=[(core.encoded(o), o) for o in objects])

    def test_comment_same_request_exact_source_version_and_no_authorization(self):
        with self.replies(self.repository, self.comment) as get:
            value = self.adapter.comment(self.request, self.pin)
        self.assertIs(value.request, self.request)
        self.assertEqual(9, value.principal_id)
        self.assertEqual(b'SYNTHETIC_NO_GO', value.body)
        self.assertEqual(2, len(value.receipts))
        self.assertFalse(value.execution_authorized)
        self.assertNotIn('SYNTHETIC_NO_GO', repr(value))
        self.assertEqual([8, 8], [call.args[1] for call in get.call_args_list])

    def test_comment_replacement_edit_wrong_account_or_cross_issue_denied(self):
        for change in ({'id': True}, {'user': {'id': 10}}, {'user': {'id': True}},
                       {'body': 'changed'}, {'updated_at': '2026-01-01T01:02:04Z'},
                       {'issue_url': 'https://api.github.com' + self.prefix + '/issues/99'},
                       {'url': 'https://evil.invalid'}, {'minimized': {'reason': 'OUTDATED'}}):
            with self.subTest(change=change), self.replies(self.repository, {**self.comment, **change}):
                with self.assertRaisesRegex(source.SourceError, '^SOURCE_COMMENT_UNVERIFIED$'):
                    self.adapter.comment(self.request, self.pin)

    def test_repository_transfer_recreation_or_private_is_denied_before_comment(self):
        for change in ({'id': 124}, {'id': True}, {'owner': {'id': 322}},
                       {'full_name': 'other/repo'}, {'private': True}):
            with self.subTest(change=change), self.replies({**self.repository, **change}) as get:
                with self.assertRaises(source.SourceError):
                    self.adapter.comment(self.request, self.pin)
                self.assertEqual(1, get.call_count)

    def test_invalid_pin_before_any_io_and_no_exception_text(self):
        for pin in (replace(self.pin, comment_id=True), replace(self.pin, updated_at='bad'),
                    replace(self.pin, body_sha256='bad'),
                    replace(self.pin, repository=replace(self.repo, owner='..'))):
            with patch.object(self.tls, 'get') as get, self.assertRaises(source.SourceError):
                self.adapter.comment(self.request, pin)
            get.assert_not_called()
        with patch.object(self.tls, 'get', side_effect=OSError(SENSITIVE)), \
                self.assertRaisesRegex(source.SourceError, '^SOURCE_COMMENT_UNVERIFIED$') as e:
            self.adapter.comment(self.request, self.pin)
        self.assertNotIn(SENSITIVE, str(e.exception))

    def test_no_cache_and_late_result_is_rejected(self):
        with self.replies(self.repository, self.comment, self.repository) as get:
            self.adapter.comment(self.request, self.pin)
            with self.assertRaises(source.SourceError):
                self.adapter.comment(self.request, self.pin)
            self.assertEqual(4, get.call_count)
        with self.replies(self.repository) as get, \
                patch.object(self.tls, 'now', side_effect=[1, 9]), self.assertRaises(source.SourceError):
            self.adapter.comment(self.request, self.pin)
        self.assertEqual(1, get.call_count)

    def blob_fixture(self):
        body = b'SYNTHETIC_BLOB_NOT_GENERATION_PROOF'
        sha = hashlib.sha1(b'blob ' + str(len(body)).encode() + b'\0' + body).hexdigest()
        pin = source.BlobPin(self.repo, 'a' * 40, 'docs/item.json', core.digest(body))
        replies = [self.repository, {'sha': pin.commit, 'tree': {'sha': 'b' * 40}},
            {'sha': 'b' * 40, 'truncated': False, 'tree': [
                {'path': 'docs', 'type': 'tree', 'mode': '040000', 'sha': 'c' * 40}]},
            {'sha': 'c' * 40, 'truncated': False, 'tree': [
                {'path': 'item.json', 'type': 'blob', 'mode': '100644', 'sha': sha}]},
            {'sha': sha, 'encoding': 'base64', 'size': len(body),
             'content': base64.b64encode(body).decode() + '\n'}]
        return pin, body, replies

    def test_blob_commit_tree_path_git_hash_and_sha256(self):
        pin, body, replies = self.blob_fixture()
        with self.replies(*replies) as get:
            value = self.adapter.blob(self.request, pin)
        self.assertEqual(body, value.body)
        self.assertEqual(pin.commit, value.revision)
        self.assertIsNone(value.principal_id)
        self.assertFalse(value.execution_authorized)
        self.assertEqual(5, get.call_count)
        self.assertNotIn('SYNTHETIC_BLOB', repr(value))

    def test_blob_wrong_commit_truncated_tree_link_submodule_size_or_hash_denied(self):
        for mutation in ('commit', 'truncated', 'symlink', 'submodule', 'git-hash', 'size', 'content'):
            pin, body, replies = self.blob_fixture()
            if mutation == 'commit': replies[1]['sha'] = 'd' * 40
            elif mutation == 'truncated': replies[2]['truncated'] = True
            elif mutation == 'symlink': replies[3]['tree'][0]['mode'] = '120000'
            elif mutation == 'submodule': replies[2]['tree'][0]['type'] = 'commit'
            elif mutation == 'git-hash':
                replies[3]['tree'][0]['sha'] = replies[4]['sha'] = 'd' * 40
            elif mutation == 'size': replies[4]['size'] = len(body) + 1
            else: replies[4]['content'] = base64.b64encode(b'changed').decode()
            with self.subTest(mutation=mutation), self.replies(*replies), self.assertRaises(source.SourceError):
                self.adapter.blob(self.request, pin)
        pin, _, replies = self.blob_fixture()
        with self.replies(*replies), self.assertRaises(source.SourceError):
            self.adapter.blob(self.request, replace(pin, sha256='f' * 64))

    def test_blob_traversal_mutable_ref_and_invalid_path_before_io(self):
        pin, _, _ = self.blob_fixture()
        for changes in ({'path': '../x'}, {'path': 'x//y'}, {'path': '/x'}, {'path': 'x?y'},
                        {'commit': 'main'}, {'sha256': 'bad'}, {'path': '/'.join(['x'] * 17)}):
            with self.subTest(changes=changes), patch.object(self.tls, 'get') as get, \
                    self.assertRaises(source.SourceError):
                self.adapter.blob(self.request, replace(pin, **changes))
            get.assert_not_called()


class OriginalTLSTests(SourceFixture):
    def get(self, incoming=None, path='/repos/synthetic-owner/synthetic-repo', deadline=8):
        self.sock = FakeSocket(response() if incoming is None else incoming)
        self.context.wrap_socket.return_value = self.sock
        with patch.object(source.socket, 'socket', return_value=self.sock) as factory:
            result = self.tls.get(path, deadline)
        self.assertEqual(1, factory.call_count)
        return result

    def test_pinned_ca_sni_numeric_endpoint_partial_writes_and_no_credential(self):
        raw, obj = self.get()
        self.assertEqual({'ok': True}, obj)
        self.assertEqual(('192.0.2.10', 443), self.sock.connected)
        self.context.wrap_socket.assert_called_once_with(
            self.sock, server_hostname='api.github.com', do_handshake_on_connect=False)
        message = bytes(self.sock.sent)
        self.assertNotIn(b'Authorization:', message)
        self.assertNotIn(SENSITIVE.encode(), message)
        self.assertEqual(1, message.count(b'GET '))
        self.assertIn(b'Connection: close', message)
        self.assertTrue(self.sock.closed)

    def test_unsafe_or_non_numeric_endpoint_and_bad_ca(self):
        for endpoint in ('github.com', '127.0.0.1', '::ffff:127.0.0.1', '169.254.169.254', '0.0.0.0'):
            with self.subTest(endpoint=endpoint), self.assertRaises(source.SourceError):
                source.PublicGitHubTLS(ca_pem=CA, endpoint_ip=endpoint)

    def test_native_context_does_not_use_ambient_ca_or_key_logging(self):
        with patch.object(ssl.SSLContext, 'load_verify_locations') as load:
            tls = source.PublicGitHubTLS(ca_pem=CA, endpoint_ip='192.0.2.10')
        self.assertTrue(tls._context.check_hostname)
        self.assertEqual(ssl.CERT_REQUIRED, tls._context.verify_mode)
        self.assertIsNone(tls._context.keylog_filename)
        self.assertEqual(0, tls._context.cert_store_stats()['x509_ca'])
        load.assert_called_once_with(cadata=CA.decode())

    def test_http_redirect_304_error_malformed_json_and_no_retry(self):
        for reply in [response(status=s) for s in (301, 304, 401, 403, 404, 429, 500)] + [
                response(b'{"a":1,"a":2}'), response(b'not JSON'), response(b'{"a":NaN}')]:
            with self.subTest(reply=reply[:30]), self.assertRaisesRegex(source.SourceError, '^SOURCE_READ_FAILED$'):
                self.get(reply)
            self.assertEqual(1, bytes(self.sock.sent).count(b'GET '))
            self.assertTrue(self.sock.closed)

    def test_wrong_tls_policy_path_or_expired_budget_stops_before_socket(self):
        for path, deadline in (('/repos/x/y?redirect=1', 8), ('/user', 8),
                               ('/repos/../y', 8),
                               ('/repos/x/y\r\nInjected: true', 8), ('/repos/x/y', 1)):
            with patch.object(source.socket, 'socket') as factory, self.assertRaises(source.SourceError):
                self.tls.get(path, deadline)
            factory.assert_not_called()
        self.context.check_hostname = False
        with patch.object(source.socket, 'socket') as factory, self.assertRaises(source.SourceError):
            self.tls.get('/repos/x/y', 8)
        factory.assert_not_called()

    def test_connect_stall_consumes_shared_deadline_and_closes_socket(self):
        sock = FakeSocket(); sock.connect_code = errno.EINPROGRESS
        with patch.object(source.socket, 'socket', return_value=sock), \
                patch.object(source.wire, 'wait_ready', side_effect=TimeoutError('SYNTHETIC')) as wait, \
                self.assertRaisesRegex(source.SourceError, '^SOURCE_READ_FAILED$'):
            self.tls.get('/repos/x/y', 8)
        self.assertTrue(sock.closed)
        self.assertEqual(8, wait.call_args.args[2].end)
        self.assertEqual(b'', sock.sent)

    def test_tls_certificate_and_stall_close_without_success(self):
        sock = FakeSocket(); sock.handshake_error = ssl.SSLCertVerificationError(SENSITIVE)
        self.context.wrap_socket.return_value = sock
        with patch.object(source.socket, 'socket', return_value=sock), self.assertRaises(source.SourceError):
            self.tls.get('/repos/x/y', 8)
        self.assertTrue(sock.closed)
        self.assertEqual(b'', sock.sent)
        with patch.object(self.tls, 'now', return_value=9), self.assertRaises(source.SourceError):
            self.get()


if __name__ == '__main__':
    unittest.main()
