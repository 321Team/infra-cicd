"""Read-only GitHub Deploy approval lookup; no posting, credentials in receipts.

An exact v2 approval JSON body must be posted by the current CICD Lead on the
responsible issue. A code review or a plain 'approved' comment cannot substitute.
"""
import math
import re
import ssl
import time
import urllib.request

import deploy_binding as b
import delivery_gate as gate


class ApprovalError(RuntimeError):
    pass


def need(ok, code):
    if not ok:
        raise ApprovalError(code)


REFERENCE = re.compile(
    r'https://github\.com/(321Team/infra-cicd/issues/11|'
    r'321Team/fds-project-baseline/issues/16)#issuecomment-([1-9][0-9]*)')
# Current P1-CICD Lead identity from docs/governance.md. No implicit reviewer authority.
LEAD = 'GWONUK'
MAX_RESPONSE = 1024 * 1024


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def endpoint(reference):
    need(type(reference) is str, 'APPROVAL_REFERENCE_UNSUPPORTED')
    match = REFERENCE.fullmatch(reference)
    need(match is not None, 'APPROVAL_REFERENCE_UNSUPPORTED')
    parent, number = match.groups()
    repository = parent.split('/issues/')[0]
    return (f'https://api.github.com/repos/{repository}/issues/comments/{number}',
            int(number))


class GitHubComments:
    """TLS/default CA, no proxy/redirect/cache/retry, GET exact comment endpoint.

    token_provider supplies an already authorized read credential in memory.
    GitHub permission/credential provisioning and Workflow injection are external.
    Timeout bounds socket operations; elapsed budget is checked before return.
    Callers recheck authorization time after lookup before starting a mutation.
    """
    def __init__(self, token_provider):
        need(callable(token_provider), 'GITHUB_TOKEN_PROVIDER_REQUIRED')
        self.token_provider = token_provider
        self.opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            urllib.request.HTTPSHandler(context=ssl.create_default_context()),
            NoRedirect())

    def read(self, reference, timeout):
        url, _ = endpoint(reference)
        need(type(timeout) in (int, float) and math.isfinite(timeout) and
             0 < timeout <= 10, 'APPROVAL_LOOKUP_BUDGET_INVALID')
        started = time.monotonic()
        try:
            token = self.token_provider()
            need(type(token) is str and 0 < len(token) <= 4096 and
                 all(33 <= ord(c) <= 126 for c in token), 'GITHUB_CREDENTIAL_INVALID')
            request = urllib.request.Request(url, method='GET', headers={
                'Authorization': 'Bearer ' + token,
                'Accept': 'application/vnd.github.raw+json',
                'Cache-Control': 'no-cache',
                'X-GitHub-Api-Version': '2026-03-10',
            })
            with self.opener.open(request, timeout=timeout) as response:
                need(response.status == 200 and response.geturl() == url,
                     'APPROVAL_HTTP_REJECTED')
                raw = response.read(MAX_RESPONSE + 1)
            need(time.monotonic() - started <= timeout, 'APPROVAL_LOOKUP_TIMEOUT')
            return b.read(raw)
        except ApprovalError:
            raise
        except Exception:
            raise ApprovalError('APPROVAL_LOOKUP_FAILED') from None


class Verifier:
    """Exact bytes, GitHub comment identity and current Lead; no cached approval."""
    def __init__(self, source):
        need(callable(getattr(source, 'read', None)), 'APPROVAL_SOURCE_REQUIRED')
        self.source = source

    def verify(self, approval_raw, expected_sha256, timeout):
        try:
            need(type(expected_sha256) is str and gate.SHA256.fullmatch(expected_sha256),
                 'APPROVAL_HASH_INVALID')
            need(type(timeout) in (int, float) and math.isfinite(timeout) and
                 0 < timeout <= 10, 'APPROVAL_LOOKUP_BUDGET_INVALID')
            approval = b.read(approval_raw)
            need(b.sha(approval_raw) == expected_sha256, 'APPROVAL_HASH_MISMATCH')
            need(approval.get('schema') == 'P1-CICD-deploy-approval-2' and
                 approval.get('template_only') is False, 'DEPLOY_APPROVAL_REQUIRED')
            reference = approval['reference']
            _, number = endpoint(reference)
            need(approval['approved_by'] == LEAD, 'APPROVER_NOT_AUTHORIZED')
            remote = self.source.read(reference, timeout)
            need(type(remote) is dict and type(remote.get('id')) is int and
                 remote['id'] == number and remote.get('html_url') == reference,
                 'APPROVAL_COMMENT_MISMATCH')
            user = remote.get('user')
            need(type(user) is dict and user.get('login') == LEAD and
                 user.get('type') == 'User', 'APPROVAL_AUTHOR_MISMATCH')
            body = remote.get('body')
            need(type(body) is str and body.encode('utf-8') == approval_raw,
                 'APPROVAL_REFERENCE_CHANGED')
            return {'status': 'MATCHED_CURRENT_COMMENT', 'reference': reference,
                    'comment_id': number, 'approved_by': LEAD,
                    'approval_sha256': expected_sha256}
        except ApprovalError:
            raise
        except Exception:
            raise ApprovalError('APPROVAL_LOOKUP_FAILED') from None
