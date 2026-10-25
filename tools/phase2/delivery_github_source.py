"""Fresh public GitHub originals over bounded, explicitly pinned TLS.

Authenticates the source server/repository/account/revision, NOT the author's
authority to grant a deployment. No approval parser, token, cache or defaults.
The result cannot be passed as ApprovalEvidence/ProducerEvidence to Verifier.
"""
import base64
from dataclasses import dataclass, field
from datetime import datetime
import errno
import hashlib
import ipaddress
import re
import selectors
import socket
import ssl
import time
from types import SimpleNamespace

import delivery_adapter as core
import delivery_transport as wire
from delivery_verifier import ReadRequest

MAX_BLOB = 256 * 1024
NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,99}')
COMPONENT = re.compile(r'[A-Za-z0-9_.-]{1,100}')
SHA1 = re.compile(r'[0-9a-f]{40}')


class SourceError(RuntimeError):
    """Only fixed codes, never a response, URL, token or exception text."""


def need(ok, code):
    if not ok:
        raise SourceError(code)


def positive(value):
    return type(value) is int and value > 0


@dataclass(frozen=True)
class RepositoryPin:
    owner: str
    name: str
    repository_id: int
    owner_id: int


@dataclass(frozen=True)
class CommentPin:
    repository: RepositoryPin
    issue_number: int
    comment_id: int
    creator_id: int  # Account that created the comment, NOT its current editor.
    updated_at: str
    body_sha256: str


@dataclass(frozen=True)
class BlobPin:
    repository: RepositoryPin
    commit: str
    path: str
    sha256: str
    anchor_ref: str  # Reviewed heads/<branch>, not an inferred/default branch.
    anchor_commit: str


@dataclass(frozen=True)
class SourceOriginal:
    request: ReadRequest = field(repr=False)
    reference: str
    revision: str
    repository_id: int
    comment_creator_id: int | None  # Neither current body author nor an authority.
    body: bytes = field(repr=False)
    receipts: tuple = field(repr=False)
    execution_authorized: bool = field(default=False, init=False)
    body_authorship_verified: bool = field(default=False, init=False)


def repository_checked(pin):
    need(type(pin) is RepositoryPin and all(type(x) is str and NAME.fullmatch(x)
         and x not in ('.', '..') for x in (pin.owner, pin.name))
         and positive(pin.repository_id) and positive(pin.owner_id), 'SOURCE_PIN_INVALID')


def source_path_checked(path):
    """GET routes used by original readers and native Actions metadata only."""
    need(type(path) is str and re.fullmatch(
        r'/repos/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+'
        r'(?:/issues/comments/[1-9][0-9]*|/git/(?:commits|trees|blobs)/[0-9a-f]{40}'
        r'|/git/ref/heads/[A-Za-z0-9_.\-/]+'
        r'|/compare/[0-9a-f]{40}\.\.\.[0-9a-f]{40}\?per_page=1'
        r'|/actions/runs/[1-9][0-9]*(?:/attempts/[1-9][0-9]*(?:/jobs\?per_page=100&page=1)?)?)?', path),
        'SOURCE_PATH_INVALID')
    need(all(part not in ('.', '..') for part in path.split('/')), 'SOURCE_PATH_INVALID')


class PublicGitHubTLS:
    """Anonymous GET only; a ROSA credential is NEVER sent to GitHub.

    The reviewed caller supplies a trusted CA bundle and an accepted numeric
    endpoint. Hostname verification still requires api.github.com. No DNS,
    proxies, redirects, conditional requests, token discovery or automatic retry.
    """
    def __init__(self, *, ca_pem, endpoint_ip, monotonic=time.monotonic):
        try:
            need(type(ca_pem) is bytes and 0 < len(ca_pem) <= wire.MAX_BODY,
                 'SOURCE_TLS_CONFIGURATION_INVALID')
            need(type(endpoint_ip) is str and '%' not in endpoint_ip,
                 'SOURCE_TLS_CONFIGURATION_INVALID')
            ip = ipaddress.ip_address(endpoint_ip)
            effective = getattr(ip, 'ipv4_mapped', None) or ip
            need(not (effective.is_unspecified or effective.is_loopback or effective.is_link_local
                      or effective.is_multicast), 'SOURCE_TLS_CONFIGURATION_INVALID')
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            context.minimum_version = ssl.TLSVersion.TLSv1_2
            context.set_alpn_protocols(['http/1.1'])
            context.load_verify_locations(cadata=ca_pem.decode('ascii'))
            self._context, self._ip = context, str(ip)
            self._family = socket.AF_INET if ip.version == 4 else socket.AF_INET6
            self._clock, self._last = monotonic, None
        except Exception:
            raise SourceError('SOURCE_TLS_CONFIGURATION_INVALID') from None

    def now(self):
        value = self._clock()
        need(core.finite(value) and (self._last is None or value >= self._last),
             'SOURCE_CLOCK_INVALID')
        self._last = value
        return value

    def _request_headers(self, path, deadline):
        # Public transport remains anonymous. Reviewed private transport may
        # supply explicitly scoped credentials without changing GET/TLS rules.
        return b''

    def get(self, path, deadline):
        raw = tls = None
        try:
            # Only the exact public REST operations used by the adapter below.
            source_path_checked(path)
            need(core.finite(deadline) and self.now() < deadline, 'SOURCE_DEADLINE_EXCEEDED')
            timer = wire.Deadline(min(10, deadline - self.now()), self.now)
            timer.end = min(timer.end, deadline)
            extra_headers = self._request_headers(path, deadline)
            c = self._context
            need(c.verify_mode == ssl.CERT_REQUIRED and c.check_hostname
                 and c.minimum_version >= ssl.TLSVersion.TLSv1_2
                 and c.keylog_filename is None, 'SOURCE_TLS_POLICY_INVALID')
            raw = socket.socket(self._family, socket.SOCK_STREAM)
            raw.setblocking(False)
            address = (self._ip, 443) if self._family == socket.AF_INET else (self._ip, 443, 0, 0)
            code = raw.connect_ex(address)
            need(code in (0, errno.EINPROGRESS, errno.EWOULDBLOCK, errno.EALREADY,
                          getattr(errno, 'WSAEWOULDBLOCK', errno.EWOULDBLOCK)), 'SOURCE_CONNECT_FAILED')
            if code:
                wire.wait_ready(raw, selectors.EVENT_WRITE, timer)
                need(raw.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR) == 0, 'SOURCE_CONNECT_FAILED')
            tls = c.wrap_socket(raw, server_hostname='api.github.com', do_handshake_on_connect=False)
            wire.tls_call(tls, tls.do_handshake, timer)
            need(tls.selected_alpn_protocol() in (None, 'http/1.1'), 'SOURCE_TLS_POLICY_INVALID')
            message = (f'GET {path} HTTP/1.1\r\nHost: api.github.com\r\n'
                'User-Agent: FDS-P2-original-reader\r\nAccept: application/vnd.github+json\r\n'
                'X-GitHub-Api-Version: 2026-03-10\r\nAccept-Encoding: identity\r\n'
                'Cache-Control: no-cache\r\nConnection: close\r\n').encode('ascii') + extra_headers + b'\r\n'
            offset = 0
            while offset < len(message):
                sent = wire.tls_call(tls, lambda: tls.send(message[offset:]), timer)
                need(sent > 0, 'SOURCE_SEND_FAILED')
                offset += sent
            _, original, obj = wire.read_json_response(tls, SimpleNamespace(method='GET'), timer, (200,))
            timer.left()
            return original, obj
        except Exception:
            # 304/redirect/404/403/429/TLS/deadline/parse all deny; no stale fallback.
            raise SourceError('SOURCE_READ_FAILED') from None
        finally:
            for connection in (tls, raw):
                if connection is not None:
                    try:
                        connection.close()
                    except OSError:
                        pass


class GitHubOriginalAdapter:
    def __init__(self, transport):
        need(type(transport) is PublicGitHubTLS, 'SOURCE_TRANSPORT_REQUIRED')
        self._transport = transport

    def read(self, request, path, receipts):
        need(type(request) is ReadRequest and core.finite(request.deadline)
             and self._transport.now() < request.deadline, 'SOURCE_REQUEST_INVALID')
        raw, obj = self._transport.get(path, request.deadline)
        need(self._transport.now() < request.deadline, 'SOURCE_DEADLINE_EXCEEDED')
        receipts.append(raw)
        return obj

    def repository(self, request, pin, receipts):
        repository_checked(pin)
        prefix = f'/repos/{pin.owner}/{pin.name}'
        obj = self.read(request, prefix, receipts)
        need(obj.get('id') == pin.repository_id and type(obj.get('id')) is int
             and obj.get('full_name') == f'{pin.owner}/{pin.name}'
             and obj.get('private') is False and obj.get('owner', {}).get('id') == pin.owner_id
             and type(obj['owner'].get('id')) is int, 'SOURCE_REPOSITORY_CHANGED')
        return prefix

    def comment(self, request, pin):
        try:
            need(type(pin) is CommentPin and all(positive(x) for x in
                 (pin.issue_number, pin.comment_id, pin.creator_id))
                 and type(pin.body_sha256) is str and core.HEX.fullmatch(pin.body_sha256)
                 and type(pin.updated_at) is str
                 and re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z', pin.updated_at),
                 'SOURCE_PIN_INVALID')
            datetime.strptime(pin.updated_at, '%Y-%m-%dT%H:%M:%SZ')
            receipts = []
            prefix = self.repository(request, pin.repository, receipts)
            path = f'{prefix}/issues/comments/{pin.comment_id}'
            obj = self.read(request, path, receipts)
            reference = f'https://github.com/{pin.repository.owner}/{pin.repository.name}/issues/{pin.issue_number}#issuecomment-{pin.comment_id}'
            need(type(obj.get('id')) is int and obj['id'] == pin.comment_id
                 and type(obj.get('user', {}).get('id')) is int and obj['user']['id'] == pin.creator_id
                 and obj.get('issue_url') == f'https://api.github.com{prefix}/issues/{pin.issue_number}'
                 and obj.get('url') == f'https://api.github.com{path}'
                 and obj.get('html_url') == reference and obj.get('updated_at') == pin.updated_at
                 and type(obj.get('body')) is str,
                 'SOURCE_COMMENT_CHANGED')
            body = obj['body'].encode('utf-8')
            need(core.digest(body) == pin.body_sha256, 'SOURCE_COMMENT_CHANGED')
            return SourceOriginal(request, reference, pin.updated_at + '/' + pin.body_sha256,
                pin.repository.repository_id, pin.creator_id, body, tuple(receipts))
        except Exception:
            raise SourceError('SOURCE_COMMENT_UNVERIFIED') from None

    def blob(self, request, pin):
        try:
            need(type(pin) is BlobPin and type(pin.commit) is str and SHA1.fullmatch(pin.commit)
                 and type(pin.sha256) is str and core.HEX.fullmatch(pin.sha256)
                 and type(pin.path) is str and type(pin.anchor_ref) is str
                 and pin.anchor_ref.startswith('heads/')
                 and type(pin.anchor_commit) is str and SHA1.fullmatch(pin.anchor_commit),
                 'SOURCE_PIN_INVALID')
            ref_parts = pin.anchor_ref.split('/')
            need(2 <= len(ref_parts) <= 16 and all(NAME.fullmatch(x) and '..' not in x
                 and not x.endswith(('.', '.lock')) for x in ref_parts), 'SOURCE_PIN_INVALID')
            parts = pin.path.split('/')
            need(1 <= len(parts) <= 16 and all(COMPONENT.fullmatch(x) and x not in ('.', '..') for x in parts),
                 'SOURCE_PIN_INVALID')
            receipts = []
            prefix = self.repository(request, pin.repository, receipts)
            self.anchor(request, prefix, pin, receipts)
            comparison = self.read(request,
                f'{prefix}/compare/{pin.anchor_commit}...{pin.commit}?per_page=1', receipts)
            need(comparison.get('status') in ('identical', 'behind')
                 and comparison.get('base_commit', {}).get('sha') == pin.anchor_commit
                 and comparison.get('merge_base_commit', {}).get('sha') == pin.commit,
                 'SOURCE_COMMIT_NOT_REACHABLE')
            commit = self.read(request, f'{prefix}/git/commits/{pin.commit}', receipts)
            need(commit.get('sha') == pin.commit, 'SOURCE_COMMIT_CHANGED')
            tree = commit.get('tree', {}).get('sha')
            for index, part in enumerate(parts):
                need(type(tree) is str and SHA1.fullmatch(tree), 'SOURCE_TREE_INVALID')
                obj = self.read(request, f'{prefix}/git/trees/{tree}', receipts)
                need(obj.get('sha') == tree and obj.get('truncated') is False
                     and type(obj.get('tree')) is list, 'SOURCE_TREE_INVALID')
                matches = [x for x in obj['tree'] if type(x) is dict and x.get('path') == part]
                need(len(matches) == 1, 'SOURCE_TREE_INVALID')
                node = matches[0]
                final = index == len(parts) - 1
                need(node.get('type') == ('blob' if final else 'tree')
                     and node.get('mode') in (('100644', '100755') if final else ('040000',)),
                     'SOURCE_TREE_INVALID')
                tree = node.get('sha')
            need(type(tree) is str and SHA1.fullmatch(tree), 'SOURCE_BLOB_INVALID')
            obj = self.read(request, f'{prefix}/git/blobs/{tree}', receipts)
            need(obj.get('sha') == tree and obj.get('encoding') == 'base64'
                 and type(obj.get('size')) is int and 0 <= obj['size'] <= MAX_BLOB
                 and type(obj.get('content')) is str, 'SOURCE_BLOB_INVALID')
            body = base64.b64decode(obj['content'].replace('\n', '').replace('\r', ''), validate=True)
            need(len(body) == obj['size'] and core.digest(body) == pin.sha256
                 and hashlib.sha1(b'blob ' + str(len(body)).encode('ascii') + b'\0' + body).hexdigest() == tree,
                 'SOURCE_BLOB_INVALID')
            reference = f'https://github.com/{pin.repository.owner}/{pin.repository.name}/blob/{pin.commit}/{pin.path}'
            self.anchor(request, prefix, pin, receipts)
            return SourceOriginal(request, reference, pin.commit, pin.repository.repository_id,
                                  None, body, tuple(receipts))
        except Exception:
            raise SourceError('SOURCE_BLOB_UNVERIFIED') from None

    def anchor(self, request, prefix, pin, receipts):
        # Objects from another fork can resolve under this repo's Git API.
        # Require membership in this repo's explicitly pinned ref history.
        obj = self.read(request, f'{prefix}/git/ref/{pin.anchor_ref}', receipts)
        need(obj.get('ref') == 'refs/' + pin.anchor_ref
             and obj.get('object', {}).get('type') == 'commit'
             and obj['object'].get('sha') == pin.anchor_commit, 'SOURCE_ANCHOR_CHANGED')
