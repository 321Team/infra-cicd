"""P2 bounded TLS sender library, not a CLI or an authorization provider.

No DNS lookup, proxy, redirect, retry, ambient credentials, or P1 imports.
Caller supplies an already resolved numeric endpoint and a verified credential.
Endpoint selection/expiry and target/credential authenticity remain caller duties.
"""
import errno
import http.client
import io
import ipaddress
import json
import re
import selectors
import socket
import ssl
import time
from urllib.parse import urlsplit

import delivery_adapter as core

MAX_BODY = 1024 * 1024
MAX_WIRE = MAX_BODY + 65536


class TransportError(RuntimeError):
    """Static codes only. No server body, token or raw exception text."""


def need(ok, code):
    if not ok:
        raise TransportError(code)


class Deadline:
    def __init__(self, seconds, clock=time.monotonic):
        need(core.finite(seconds) and 0 < seconds <= 10, 'TIMEOUT_INVALID')
        self.clock = clock
        self.last = clock()
        need(core.finite(self.last), 'CLOCK_INVALID')
        self.end = self.last + seconds

    def left(self):
        now = self.clock()
        need(core.finite(now) and now >= self.last, 'CLOCK_INVALID')
        self.last = now
        need(now < self.end, 'DEADLINE_EXCEEDED')
        return self.end - now


def wait_ready(sock, event, deadline):
    with selectors.DefaultSelector() as selector:
        selector.register(sock, event)
        ready = selector.select(deadline.left())
        deadline.left()
        need(bool(ready), 'DEADLINE_EXCEEDED')


def tls_call(sock, operation, deadline):
    while True:
        deadline.left()
        try:
            result = operation()
            deadline.left()
            return result
        except ssl.SSLWantReadError:
            wait_ready(sock, selectors.EVENT_READ, deadline)
        except ssl.SSLWantWriteError:
            wait_ready(sock, selectors.EVENT_WRITE, deadline)


class ResponseStream(io.RawIOBase):
    def __init__(self, sock, deadline):
        super().__init__()
        self.sock, self.deadline, self.total = sock, deadline, 0

    def readable(self):
        return True

    def readinto(self, buffer):
        raw = tls_call(self.sock, lambda: self.sock.recv(min(len(buffer), 16384)), self.deadline)
        self.total += len(raw)
        need(self.total <= MAX_WIRE, 'RESPONSE_TOO_LARGE')
        buffer[:len(raw)] = raw
        return len(raw)


class HTTPStream:
    """Only the makefile protocol needed by standard-library HTTPResponse."""
    def __init__(self, sock, deadline):
        self.sock, self.deadline = sock, deadline

    def makefile(self, mode):
        need(mode == 'rb', 'STREAM_MODE_INVALID')
        return io.BufferedReader(ResponseStream(self.sock, self.deadline), 16384)


def unique_object(pairs):
    result = {}
    for name, value in pairs:
        need(name not in result, 'RESPONSE_JSON_INVALID')
        result[name] = value
    return result


def read_json_response(sock, request, deadline, accepted):
    with http.client.HTTPResponse(HTTPStream(sock, deadline), method=request.method) as response:
        response.begin()
        need(response.version == 11, 'HTTP_NOT_ACCEPTED')
        need(response.status in accepted, {
            401: 'HTTP_UNAUTHORIZED', 403: 'HTTP_FORBIDDEN',
            429: 'HTTP_RATE_LIMITED'}.get(response.status, 'HTTP_NOT_ACCEPTED'))
        for header in ('content-length', 'transfer-encoding', 'content-type', 'content-encoding'):
            need(len(response.headers.get_all(header, [])) <= 1, 'HTTP_HEADERS_INVALID')
        length = response.getheader('Content-Length')
        transfer = response.getheader('Transfer-Encoding')
        need(not (length is not None and transfer is not None), 'HTTP_HEADERS_INVALID')
        need(transfer is None or transfer.lower() == 'chunked', 'HTTP_HEADERS_INVALID')
        need(response.getheader('Content-Encoding') in (None, 'identity'), 'HTTP_HEADERS_INVALID')
        content_type = response.getheader('Content-Type', '').split(';')[0].strip().lower()
        need(content_type == 'application/json', 'HTTP_CONTENT_TYPE_INVALID')
        if length is not None:
            need(bool(re.fullmatch(r'[0-9]{1,10}', length)) and int(length) <= MAX_BODY,
                 'RESPONSE_TOO_LARGE')
        raw = response.read(MAX_BODY + 1)
        need(len(raw) <= MAX_BODY and (length is None or len(raw) == int(length)),
             'RESPONSE_SIZE_INVALID')
        # Reject duplicate keys and non-JSON NaN/Infinity rather than interpreting
        # a different object than the server or the independent reviewer.
        obj = json.loads(raw.decode('utf-8'), object_pairs_hook=unique_object,
                         parse_constant=lambda value: (_ for _ in ()).throw(
                             TransportError('RESPONSE_JSON_INVALID')))
        need(type(obj) is dict, 'RESPONSE_JSON_INVALID')
        core.encoded(obj)  # Also reject finite JSON literals that overflow a float.
        deadline.left()
        return response.status, raw, obj


def read_response(sock, request, deadline):
    return read_json_response(sock, request, deadline, (200, 201))[2]


class Sender:
    """One pinned Binding + CA + token + numeric endpoint; no automatic renewal.

    Numeric endpoint must have been resolved and accepted in the authorized
    preparation step. It does not replace CA/hostname verification. This sender
    does not validate token issuer, audience, expiration, RBAC or approval.
    Every exception is uncertain to the core, preserving the pending intent.
    """
    def __init__(self, binding, *, ca_pem, token, endpoint_ip):
        try:
            core.binding_checked(binding)
            need(type(ca_pem) is bytes and 0 < len(ca_pem) <= MAX_BODY
                 and core.digest(ca_pem) == binding.ca_sha256, 'CA_BINDING_INVALID')
            need(type(token) is str and 0 < len(token) <= 16384
                 and re.fullmatch(r'[A-Za-z0-9._~+/-]+=*', token), 'TOKEN_INVALID')
            need(type(endpoint_ip) is str and '%' not in endpoint_ip, 'ENDPOINT_INVALID')
            address = ipaddress.ip_address(endpoint_ip)
            effective = getattr(address, 'ipv4_mapped', None) or address
            need(not (effective.is_unspecified or effective.is_multicast
                      or effective.is_loopback or effective.is_link_local), 'ENDPOINT_INVALID')
            # Deliberately do not use create_default_context: no system trust or
            # SSLKEYLOGFILE environment behavior. Only this CA bundle is loaded.
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            context.minimum_version = ssl.TLSVersion.TLSv1_2
            context.set_alpn_protocols(['http/1.1'])
            context.load_verify_locations(cadata=ca_pem.decode('ascii'))
            need(context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname
                 and context.keylog_filename is None, 'TLS_POLICY_INVALID')
            parsed = urlsplit(binding.api_origin)
            self._binding, self._context = binding, context
            self._token, self._fingerprint = token, core.digest(token.encode('ascii'))
            self._ip, self._family = str(address), socket.AF_INET if address.version == 4 else socket.AF_INET6
            self._host, self._port = parsed.hostname, parsed.port or 443
        except Exception:
            raise TransportError('TRANSPORT_CONFIGURATION_INVALID') from None

    def __repr__(self):
        return '<P2 Sender: credential redacted>'

    def _check(self, request):
        b = self._binding
        need(type(request) is core.Request and request.binding_key == b.key
             and request.api_origin == b.api_origin and request.ca_sha256 == b.ca_sha256
             and request.credential_fingerprint == self._fingerprint, 'REQUEST_BINDING_INVALID')
        need(self._context.verify_mode == ssl.CERT_REQUIRED and self._context.check_hostname
             and self._context.minimum_version >= ssl.TLSVersion.TLSv1_2
             and self._context.keylog_filename is None, 'TLS_POLICY_INVALID')
        need(type(request.body) is bytes and len(request.body) <= MAX_BODY, 'REQUEST_BODY_INVALID')
        self._check_scope(request)

    def _check_scope(self, request):
        b = self._binding
        paths = {core.path_for(key): key for key in core.OBJECTS}
        if request.method == 'GET':
            need(request.path in paths and paths[request.path][1] == 'Deployment'
                 and request.content_type == '' and request.body == b'', 'REQUEST_SCOPE_INVALID')
        elif request.method == 'PATCH':
            suffix = '?fieldManager=fds-deployer'
            need(type(request.path) is str and request.path.endswith(suffix), 'REQUEST_SCOPE_INVALID')
            path = request.path[:-len(suffix)]
            need(path in paths and request.content_type == 'application/apply-patch+yaml',
                 'REQUEST_SCOPE_INVALID')
            obj = json.loads(request.body, object_pairs_hook=unique_object)
            core.scope_checked(obj, b)
            need(core.identity(obj) == paths[path] and core.encoded(obj) == request.body,
                 'REQUEST_BODY_INVALID')
        else:
            raise TransportError('REQUEST_SCOPE_INVALID')

    def __call__(self, request):
        try:
            return self._exchange(request, Deadline(request.timeout_seconds))
        except Exception:
            raise TransportError('TRANSPORT_FAILED') from None

    def _read_response(self, sock, request, deadline):
        return read_response(sock, request, deadline)

    def _transport_error(self, error):
        # Writes remain uncertain for EVERY failure; never infer no mutation.
        return TransportError('TRANSPORT_FAILED')

    def _exchange(self, request, deadline):
        raw_socket = tls_socket = None
        try:
            self._check(request)
            deadline.left()
            raw_socket = socket.socket(self._family, socket.SOCK_STREAM)
            raw_socket.setblocking(False)
            address = (self._ip, self._port) if self._family == socket.AF_INET else (self._ip, self._port, 0, 0)
            code = raw_socket.connect_ex(address)  # numeric address: no DNS wait
            need(code in (0, errno.EINPROGRESS, errno.EWOULDBLOCK, errno.EALREADY,
                          getattr(errno, 'WSAEWOULDBLOCK', errno.EWOULDBLOCK)), 'CONNECT_FAILED')
            if code:
                wait_ready(raw_socket, selectors.EVENT_WRITE, deadline)
                need(raw_socket.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR) == 0, 'CONNECT_FAILED')
            tls_socket = self._context.wrap_socket(raw_socket, server_hostname=self._host,
                                                   do_handshake_on_connect=False)
            tls_call(tls_socket, tls_socket.do_handshake, deadline)
            need(tls_socket.selected_alpn_protocol() in (None, 'http/1.1'), 'TLS_PROTOCOL_INVALID')
            host = self._host + (f':{self._port}' if self._port != 443 else '')
            headers = [f'{request.method} {request.path} HTTP/1.1', f'Host: {host}',
                       'Accept: application/json', 'Accept-Encoding: identity', 'Connection: close',
                       'Authorization: Bearer ' + self._token, f'Content-Length: {len(request.body)}']
            if request.content_type:
                headers.append('Content-Type: ' + request.content_type)
            wire = ('\r\n'.join(headers) + '\r\n\r\n').encode('ascii') + request.body
            offset = 0
            while offset < len(wire):
                sent = tls_call(tls_socket, lambda: tls_socket.send(wire[offset:]), deadline)
                need(sent > 0, 'SEND_FAILED')
                offset += sent
            return self._read_response(tls_socket, request, deadline)
        except Exception as error:
            # The write Sender treats even 4xx as uncertain. GET-only subclasses
            # may retain fixed diagnostic codes without inferring write success.
            # Neither path automatically retries.
            raise self._transport_error(error) from None
        finally:
            for connection in (tls_socket, raw_socket):
                if connection is not None:
                    try:
                        connection.close()
                    except OSError:
                        pass  # Preserve the sanitized request outcome.
