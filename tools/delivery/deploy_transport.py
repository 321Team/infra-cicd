"""Pinned Kubernetes TLS GET/JSON-Patch transport, no CLI or token issuance.

Providers must be nonblocking reads of independently verified metadata. A timeout
or ambiguous response permanently blocks this client; no automatic PATCH retry.
"""
import hashlib
import http.client
import io
import json
import math
import re
import socket
import ssl
import time

import deploy_adapter as core
import deploy_binding as b

HOST, PORT = '10.1.93.109', 6443
API_SERVER = 'https://10.1.93.109:6443'
MAX_RESPONSE = 1024 * 1024


class TransportError(RuntimeError):
    """Static codes only. Never expose token, response body or SSL exceptions."""


def need(ok, code):
    if not ok:
        raise TransportError(code)


def context_checked(ca_raw, expected_ca_sha256):
    need(type(ca_raw) is bytes and 0 < len(ca_raw) <= MAX_RESPONSE, 'CA_BYTES_INVALID')
    need(type(expected_ca_sha256) is str and b.gate.SHA256.fullmatch(expected_ca_sha256)
         and hashlib.sha256(ca_raw).hexdigest() == expected_ca_sha256, 'CA_HASH_MISMATCH')
    try:
        pem = ca_raw.decode('ascii')
        need(re.fullmatch(r'(?:\s*-----BEGIN CERTIFICATE-----\s+'
                          r'[A-Za-z0-9+/=\s]+-----END CERTIFICATE-----\s*)+', pem),
             'CA_PEM_INVALID')
        # Avoid create_default_context's SSLKEYLOGFILE environment handling.
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.check_hostname = True
        ctx.verify_mode = ssl.CERT_REQUIRED
        ctx.load_verify_locations(cadata=pem)  # Only the hash-pinned CA bundle.
        return ctx
    except TransportError:
        raise
    except Exception:
        raise TransportError('CA_LOAD_FAILED') from None


class DeadlineReader(io.RawIOBase):
    """HTTP parser reads pass through a deadline check at every socket read."""
    def __init__(self, sock, budget):
        super().__init__()
        self.sock, self.budget = sock, budget

    def readable(self):
        return True

    def readinto(self, buffer):
        self.sock.settimeout(self.budget())
        size = self.sock.recv_into(buffer)
        self.budget()  # Buffered data received after expiry is not accepted.
        return size


class ResponseSocket:
    def __init__(self, sock, budget):
        self.sock, self.budget = sock, budget

    def makefile(self, mode):
        need(mode == 'rb', 'HTTP_MODE_INVALID')
        return io.BufferedReader(DeadlineReader(self.sock, self.budget))


class Client:
    def __init__(self, targets, ca_raw, expected_ca_sha256, token_provider, *,
                 remaining_seconds, credential_remaining_seconds,
                 request_timeout=10, clock=time.monotonic):
        self.targets = {t.service: t for t in core.targets_checked(targets)}
        need(all(callable(p) for p in
                 (token_provider, remaining_seconds, credential_remaining_seconds, clock)),
             'TRANSPORT_PROVIDER_REQUIRED')
        need(type(request_timeout) in (int, float) and math.isfinite(request_timeout)
             and 0 < request_timeout <= 10, 'REQUEST_TIMEOUT_INVALID')
        self.context = context_checked(ca_raw, expected_ca_sha256)
        self.token_provider = token_provider
        self.remaining, self.credential, self.clock = (
            remaining_seconds, credential_remaining_seconds, clock)
        self.timeout, self.blocked = request_timeout, False

    def patch_checked(self, name, patch):
        target = self.targets[name]
        need(type(patch) is list and len(patch) == 5, 'PATCH_SCOPE_INVALID')
        need(all(type(p) is dict and set(p) == {'op', 'path', 'value'} for p in patch),
             'PATCH_SCOPE_INVALID')
        path = patch[2]['path']
        need(type(path) is str and re.fullmatch(
            r'/spec/template/spec/containers/(?:0|[1-9][0-9]{0,5})/name', path),
             'PATCH_SCOPE_INVALID')
        base = path[:-5]
        rv = patch[1]['value']
        need(type(rv) is str and 0 < len(rv) <= 200 and
             all(ord(c) >= 32 for c in rv), 'PATCH_SCOPE_INVALID')
        old, new = patch[3]['value'], patch[4]['value']
        need((old, new) in ((target.previous_image, target.image),
                           (target.image, target.previous_image)), 'PATCH_SCOPE_INVALID')
        expected = [
            {'op': 'test', 'path': '/metadata/uid', 'value': target.uid},
            {'op': 'test', 'path': '/metadata/resourceVersion', 'value': rv},
            {'op': 'test', 'path': base + '/name', 'value': name},
            {'op': 'test', 'path': base + '/image', 'value': old},
            {'op': 'replace', 'path': base + '/image', 'value': new},
        ]
        need(patch == expected, 'PATCH_SCOPE_INVALID')
        return json.dumps(expected, separators=(',', ':')).encode('utf-8')

    def window(self):
        try:
            values = (self.remaining(), self.credential())
            need(all(type(v) in (int, float) and math.isfinite(v) and v > 0
                     for v in values), 'REQUEST_AUTH_EXPIRED')
            return min(values)
        except TransportError:
            raise
        except Exception:
            raise TransportError('REQUEST_WINDOW_UNAVAILABLE') from None

    def budget(self, deadline):
        left = deadline - self.clock()
        need(type(left) in (int, float) and math.isfinite(left) and left > 0,
             'REQUEST_DEADLINE_EXCEEDED')
        return min(left, self.window())

    def request(self, method, name, patch=None):
        raw_sock = tls = response = None
        try:
            need(not self.blocked, 'TRANSPORT_BLOCKED')
            need(type(name) is str and name in self.targets, 'TARGET_SCOPE_INVALID')
            need(method in ('GET', 'PATCH'), 'METHOD_INVALID')
            body = self.patch_checked(name, patch) if method == 'PATCH' else b''
            initial = self.clock()
            need(type(initial) in (int, float) and math.isfinite(initial), 'CLOCK_INVALID')
            deadline = initial + min(self.timeout, self.window())
            budget = lambda: self.budget(deadline)
            token = self.token_provider()
            need(type(token) is str and 0 < len(token) <= 16384 and
                 all(33 <= ord(c) <= 126 for c in token), 'KUBE_CREDENTIAL_INVALID')
            path = '/apis/apps/v1/namespaces/fds-app/deployments/' + name
            headers = (
                f'{method} {path} HTTP/1.1\r\nHost: {HOST}:{PORT}\r\n'
                f'Authorization: Bearer {token}\r\nAccept: application/json\r\n'
                'Accept-Encoding: identity\r\nConnection: close\r\n'
                + ('Content-Type: application/json-patch+json\r\n' if method == 'PATCH' else '')
                + f'Content-Length: {len(body)}\r\n\r\n'
            ).encode('ascii')
            # Numeric fixed IPv4 endpoint: no DNS, proxy, redirect or tunnel.
            raw_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            raw_sock.settimeout(budget())
            raw_sock.connect((HOST, PORT))
            raw_sock.settimeout(budget())
            tls = self.context.wrap_socket(
                raw_sock, server_hostname=HOST, do_handshake_on_connect=False)
            tls.settimeout(budget())
            tls.do_handshake()
            tls.settimeout(budget())
            tls.sendall(headers + body)  # Exactly one attempt, finite total timeout.
            budget()
            response = http.client.HTTPResponse(ResponseSocket(tls, budget), method=method)
            response.begin()
            budget()
            lengths = response.headers.get_all('Content-Length', [])
            transfers = response.headers.get_all('Transfer-Encoding', [])
            need(len(lengths) <= 1 and len(transfers) <= 1 and not (lengths and transfers)
                 and (not transfers or transfers[0].lower() == 'chunked'),
                 'RESPONSE_FRAMING_INVALID')
            need(response.getheader('Content-Encoding', 'identity').lower() == 'identity',
                 'RESPONSE_ENCODING_INVALID')
            need(not lengths or re.fullmatch(r'[0-9]+', lengths[0].strip()),
                 'RESPONSE_FRAMING_INVALID')
            data = response.read(MAX_RESPONSE + 1)
            budget()
            need(len(data) <= MAX_RESPONSE, 'RESPONSE_TOO_LARGE')
            need(response.length is None or response.length == 0, 'RESPONSE_TRUNCATED')
            obj = b.read(data)
            budget()
            if response.status == 200:
                need(obj.get('apiVersion') == 'apps/v1' and obj.get('kind') == 'Deployment',
                     'RESPONSE_OBJECT_INVALID')
                return obj
            # Conservative proof of a non-mutating API rejection. Authentication
            # loss, redirects, 5xx, malformed errors and all timeout paths block.
            details = obj.get('details')
            known = (method == 'PATCH' and response.status in (409, 422)
                     and obj.get('apiVersion') == 'v1' and obj.get('kind') == 'Status'
                     and obj.get('status') == 'Failure'
                     and type(obj.get('code')) is int and obj['code'] == response.status
                     and obj.get('reason') == {409: 'Conflict', 422: 'Invalid'}[response.status]
                     and type(details) is dict and details.get('name') == name
                     and details.get('group') == 'apps' and details.get('kind') == 'deployments')
            if known:
                raise core.Rejected('PATCH_REJECTED')
            raise TransportError('HTTP_RESPONSE_UNTRUSTED')
        except core.Rejected:
            raise
        except TransportError:
            self.blocked = True
            raise
        except Exception:
            self.blocked = True
            raise TransportError('TRANSPORT_FAILED') from None
        finally:
            for item in (response, tls, raw_sock):
                if item is not None:
                    try:
                        item.close()
                    except Exception:
                        self.blocked = True

    def get(self, name):
        return self.request('GET', name)

    def patch(self, name, patch):
        return self.request('PATCH', name, patch)
