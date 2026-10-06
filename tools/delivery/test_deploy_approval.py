"""Mock GitHub responses only; no real token, request or approval posted."""
import copy
import json
import ssl
import unittest
from unittest.mock import patch

import deploy_approval as a
import deploy_binding as b
from test_deploy_binding import encode
from test_deploy_plan import plan_inputs


def approval_fixture():
    _, approval, _, _ = plan_inputs()
    approval['reference'] = 'https://github.com/321Team/infra-cicd/issues/11#issuecomment-123'
    approval['approved_by'] = 'GWONUK'
    raw = encode(approval)
    return raw, {
        'id': 123, 'html_url': approval['reference'], 'body': raw.decode(),
        'user': {'login': 'GWONUK', 'type': 'User'},
    }


class Source:
    def __init__(self, obj):
        self.obj, self.calls, self.hook = copy.deepcopy(obj), [], None

    def read(self, reference, timeout):
        self.calls.append((reference, timeout))
        if self.hook:
            self.hook()
        return copy.deepcopy(self.obj)


class Response:
    def __init__(self, raw, url, status=200):
        self.raw, self.url, self.status = raw, url, status

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def geturl(self):
        return self.url

    def read(self, limit):
        return self.raw[:limit]


class Opener:
    def __init__(self, response):
        self.response, self.calls = response, []

    def open(self, request, timeout):
        self.calls.append((request, timeout))
        return self.response


class ApprovalTests(unittest.TestCase):
    def setUp(self):
        self.raw, self.remote = approval_fixture()
        self.source = Source(self.remote)
        self.verifier = a.Verifier(self.source)

    def verify(self, **kwargs):
        return self.verifier.verify(
            kwargs.get('raw', self.raw), kwargs.get('hash', b.sha(self.raw)),
            kwargs.get('timeout', 10))

    def test_exact_raw_comment_body_and_author_match(self):
        result = self.verify()
        self.assertEqual(result['status'], 'MATCHED_CURRENT_COMMENT')
        self.assertEqual(result['approval_sha256'], b.sha(self.raw))
        self.assertEqual(result['comment_id'], 123)

    def test_code_review_and_plain_approved_comment_do_not_authorize_deploy(self):
        self.source.obj['body'] = 'APPROVED'
        with self.assertRaisesRegex(a.ApprovalError, 'APPROVAL_REFERENCE_CHANGED'):
            self.verify()

    def test_wrong_author_or_bot_cannot_approve(self):
        for user in ({'login': 'supreme33-kr', 'type': 'User'},
                     {'login': 'GWONUK', 'type': 'Bot'}):
            self.source.obj['user'] = user
            with self.subTest(user=user), self.assertRaisesRegex(a.ApprovalError, 'APPROVAL_AUTHOR_MISMATCH'):
                self.verify()

    def test_declared_approver_does_not_replace_current_lead(self):
        declared = json.loads(self.raw)
        declared['approved_by'] = 'supreme33-kr'
        changed = encode(declared)
        with self.assertRaisesRegex(a.ApprovalError, 'APPROVER_NOT_AUTHORIZED'):
            self.verify(raw=changed, hash=b.sha(changed))
        self.assertEqual(self.source.calls, [])

    def test_reference_comment_id_parent_and_html_url_match_exactly(self):
        for field, value in (('id', True), ('id', 124),
                             ('html_url', self.remote['html_url'].replace('/11#', '/12#'))):
            self.source.obj = copy.deepcopy(self.remote)
            self.source.obj[field] = value
            with self.subTest(field=field), self.assertRaisesRegex(a.ApprovalError, 'APPROVAL_COMMENT_MISMATCH'):
                self.verify()

    def test_edited_revoked_and_reformatted_comment_blocks_without_cache(self):
        self.verify()
        for body in ('REVOKED', self.raw.decode() + '\n', '{}'):
            self.source.obj['body'] = body
            with self.subTest(body=body[:10]), self.assertRaisesRegex(a.ApprovalError, 'APPROVAL_REFERENCE_CHANGED'):
                self.verify()
        self.assertEqual(len(self.source.calls), 4)

    def test_lf_with_one_final_newline_matches_exact_bytes(self):
        obj = json.loads(self.raw)
        raw = (json.dumps(obj, sort_keys=True, indent=2) + '\n').encode()
        self.source.obj['body'] = raw.decode()
        self.assertEqual(self.verify(raw=raw, hash=b.sha(raw))['status'], 'MATCHED_CURRENT_COMMENT')

    def test_crlf_or_final_newline_change_is_not_normalized(self):
        raw = (json.dumps(json.loads(self.raw), sort_keys=True, indent=2) + '\n').encode()
        fence = chr(96) * 3
        for body in (raw.decode().replace('\n', '\r\n'), raw.decode().rstrip('\n'),
                     raw.decode() + '\n', fence + 'json\n' + raw.decode() + fence):
            self.source.obj['body'] = body
            with self.subTest(body=body[:10]), self.assertRaisesRegex(
                    a.ApprovalError, 'APPROVAL_REFERENCE_CHANGED'):
                self.verify(raw=raw, hash=b.sha(raw))

    def test_missing_deleted_or_forbidden_comment_is_sanitized(self):
        self.source.hook = lambda: (_ for _ in ()).throw(PermissionError('PRIVATE_TOKEN'))
        with self.assertRaisesRegex(a.ApprovalError, '^APPROVAL_LOOKUP_FAILED$'):
            self.verify()

    def test_hash_failure_prevents_remote_lookup(self):
        with self.assertRaisesRegex(a.ApprovalError, 'APPROVAL_HASH_MISMATCH'):
            self.verify(hash='0' * 64)
        self.assertEqual(self.source.calls, [])

    def test_only_pinned_issuecomment_references_are_supported(self):
        for reference in (
                'https://github.com/321Team/infra-cicd/pull/55#pullrequestreview-123',
                'https://github.com/321Team/infra-cicd/issues/11',
                'https://github.com/other/repo/issues/11#issuecomment-123',
                'https://github.com/321Team/infra-cicd/issues/12#issuecomment-123',
                'https://github.com/321Team/infra-cicd/issues/11#issuecomment-123?token=x'):
            with self.subTest(reference=reference), self.assertRaises(a.ApprovalError):
                a.endpoint(reference)
        url, number = a.endpoint(
            'https://github.com/321Team/fds-project-baseline/issues/16#issuecomment-123')
        self.assertEqual(number, 123)
        self.assertEqual(url, 'https://api.github.com/repos/321Team/fds-project-baseline/issues/comments/123')

    def test_invalid_budget_rejected_even_with_mock_source(self):
        for value in (0, -1, True, '10', float('nan'), float('inf'), 11):
            with self.subTest(value=value), self.assertRaises(a.ApprovalError):
                self.verify(timeout=value)
        self.assertEqual(self.source.calls, [])

    def client(self, response, provider=lambda: 'synthetic-token'):
        opener = Opener(response)
        with patch.object(a.urllib.request, 'build_opener', return_value=opener) as build:
            client = a.GitHubComments(provider)
        return client, opener, build.call_args.args

    def test_http_transport_uses_read_only_tls_no_proxy_or_redirect(self):
        url, _ = a.endpoint(self.remote['html_url'])
        client, opener, handlers = self.client(Response(encode(self.remote), url))
        self.assertEqual(client.read(self.remote['html_url'], 5)['id'], 123)
        request, timeout = opener.calls[0]
        self.assertEqual(request.get_method(), 'GET')
        self.assertEqual(request.full_url, url)
        self.assertEqual(timeout, 5)
        self.assertEqual(request.get_header('Accept'), 'application/vnd.github.raw+json')
        self.assertEqual(handlers[0].proxies, {})
        self.assertEqual(handlers[1]._context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(handlers[1]._context.check_hostname)
        self.assertIsInstance(handlers[2], a.NoRedirect)

    def test_redirect_changed_url_and_non_success_status_fail_closed(self):
        url, _ = a.endpoint(self.remote['html_url'])
        for status, destination in ((302, url), (403, url), (200, 'https://evil.example/')):
            client, _, _ = self.client(Response(encode(self.remote), destination, status))
            with self.subTest(status=status), self.assertRaisesRegex(a.ApprovalError, 'APPROVAL_HTTP_REJECTED'):
                client.read(self.remote['html_url'], 5)

    def test_socket_provider_error_never_leaks_token(self):
        url, _ = a.endpoint(self.remote['html_url'])
        client, _, _ = self.client(
            Response(encode(self.remote), url),
            provider=lambda: (_ for _ in ()).throw(ValueError('PRIVATE_TOKEN')))
        with self.assertRaisesRegex(a.ApprovalError, '^APPROVAL_LOOKUP_FAILED$'):
            client.read(self.remote['html_url'], 5)

    def test_invalid_token_cannot_inject_headers(self):
        url, _ = a.endpoint(self.remote['html_url'])
        client, opener, _ = self.client(Response(encode(self.remote), url),
                                         provider=lambda: 'bad\nheader')
        with self.assertRaisesRegex(a.ApprovalError, 'GITHUB_CREDENTIAL_INVALID'):
            client.read(self.remote['html_url'], 5)
        self.assertEqual(opener.calls, [])

    def test_oversized_or_non_json_response_is_not_trusted(self):
        url, _ = a.endpoint(self.remote['html_url'])
        for raw in (b' ' * (a.MAX_RESPONSE + 1), b'bad', b'{"x":1,"x":2}'):
            client, _, _ = self.client(Response(raw, url))
            with self.subTest(length=len(raw)), self.assertRaisesRegex(a.ApprovalError, 'APPROVAL_LOOKUP_FAILED'):
                client.read(self.remote['html_url'], 5)

    def test_elapsed_lookup_budget_is_checked_before_return(self):
        url, _ = a.endpoint(self.remote['html_url'])
        client, _, _ = self.client(Response(encode(self.remote), url))
        with patch.object(a.time, 'monotonic', side_effect=[0, 6]):
            with self.assertRaisesRegex(a.ApprovalError, 'APPROVAL_LOOKUP_TIMEOUT'):
                client.read(self.remote['html_url'], 5)


if __name__ == '__main__':
    unittest.main()
