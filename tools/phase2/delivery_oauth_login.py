"""Explicit, single-use Basic challenge login; no ambient session or kubeconfig.

Supports only the OpenShift implicit challenging-client flow at a caller-pinned
OAuth origin. No discovery, redirect following, fallback or automatic retry.
Construction/close are inert. login() is a future Runtime operation, not GO.
"""
import base64
import hashlib
import hmac
import http.client
import ipaddress
import re
import secrets
import socket
import ssl
import threading
from urllib.parse import parse_qsl, urlencode, urlsplit

import delivery_adapter as core
import delivery_crosshost_ingress as ingress
import delivery_transport as wire
from delivery_object_reader import BearerCredential


class LoginError(RuntimeError):
    """Fixed codes only, never password, token, Location or server body."""


def need(ok):
    if not ok:
        raise LoginError('P2_LOGIN_INVALID')


def _public(exc):
    if isinstance(exc, KeyboardInterrupt):
        return KeyboardInterrupt()
    if isinstance(exc, SystemExit):
        return SystemExit(130)
    return LoginError('P2_LOGIN_FAILED')


class BasicChallengeLogin(ingress._NoCopy):
    """Own one explicit password reference, return one dedicated OAuth token.

    Caller must release its aliases. Reference release is not zeroization.
    Metadata is a private handoff (hashed token object name, server expires_in),
    NOT proof of revocation, configured maximum lifetime or execution approval.
    A lost response after Basic submission can leave a live server token.
    """
    def __init__(self, *, origin, ca_pem, ca_sha256, endpoint_ip, username,
                 password, deadline, monotonic):
        error = None
        try:
            need(type(origin) is str)
            u = urlsplit(origin)
            need(u.scheme == 'https' and core.host(u.hostname)
                 and u.port in (None, 443, 6443)
                 and origin == 'https://' + u.hostname + (f':{u.port}' if u.port else '')
                 and not (u.username or u.password or u.path or u.query or u.fragment))
            need(type(ca_pem) is bytes and 0 < len(ca_pem) <= 1024*1024
                 and core.digest(ca_pem) == ca_sha256)
            need(type(endpoint_ip) is str and '%' not in endpoint_ip)
            ip = ipaddress.ip_address(endpoint_ip)
            if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
                ip = ip.ipv4_mapped
            need(not (ip.is_unspecified or ip.is_multicast or ip.is_loopback or ip.is_link_local))
            need(type(username) is str and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.@-]{0,252}', username))
            need(type(password) is str and 0 < len(password.encode('utf-8')) <= 4096
                 and all(ord(c) >= 32 and ord(c) != 127 for c in password))
            need(core.finite(deadline) and callable(monotonic))
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            ctx.minimum_version = ssl.TLSVersion.TLSv1_2
            ctx.set_alpn_protocols(['http/1.1'])
            ctx.load_verify_locations(cadata=ca_pem.decode('ascii'))
            ctx.verify_mode, ctx.check_hostname, ctx.keylog_filename = ssl.CERT_REQUIRED, True, None
        except BaseException as exc:
            error = _public(exc)
        if error is not None:
            password = None
            raise error from None
        self._origin, self._host, self._port = origin, u.hostname, u.port or 443
        self._ip, self._family = str(ip), socket.AF_INET if ip.version == 4 else socket.AF_INET6
        self._context, self._username, self._password = ctx, username, password
        self._end, self._clock, self._last = deadline, monotonic, None
        self._lock, self._used = threading.RLock(), False
        self._status = 'NOT_ATTEMPTED'
        self._metadata = None

    def __repr__(self):
        return '<BasicChallengeLogin: private, explicit, single-use>'

    def _now(self):
        now = self._clock()
        need(core.finite(now) and (self._last is None or now >= self._last))
        self._last = now
        return now

    def _exchange(self, path, authorization):
        # Reuse bounded nonblocking TLS primitives, not the bearer write Sender.
        raw = tls = None
        try:
            ctx = self._context
            need(ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname
                 and ctx.minimum_version >= ssl.TLSVersion.TLSv1_2
                 and ctx.keylog_filename is None)
            budget = wire.Deadline(min(10, self._end - self._now()), self._now)
            budget.end = min(budget.end, self._end)
            raw = socket.socket(self._family, socket.SOCK_STREAM); raw.setblocking(False)
            addr = (self._ip, self._port) if self._family == socket.AF_INET else (self._ip, self._port, 0, 0)
            code = raw.connect_ex(addr)
            need(code in (0, wire.errno.EINPROGRESS, wire.errno.EWOULDBLOCK,
                          wire.errno.EALREADY, getattr(wire.errno, 'WSAEWOULDBLOCK', wire.errno.EWOULDBLOCK)))
            if code:
                wire.wait_ready(raw, wire.selectors.EVENT_WRITE, budget)
                need(raw.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR) == 0)
            tls = ctx.wrap_socket(raw, server_hostname=self._host, do_handshake_on_connect=False)
            wire.tls_call(tls, tls.do_handshake, budget)
            need(tls.selected_alpn_protocol() in (None, 'http/1.1'))
            host = self._host + (f':{self._port}' if self._port != 443 else '')
            headers = [f'GET {path} HTTP/1.1', f'Host: {host}', 'X-CSRF-Token: 1',
                       'Accept-Encoding: identity', 'Connection: close', 'Content-Length: 0']
            if authorization is not None:
                headers.append('Authorization: Basic ' + authorization)
            packet = ('\r\n'.join(headers) + '\r\n\r\n').encode('ascii')
            offset = 0
            while offset < len(packet):
                sent = wire.tls_call(tls, lambda: tls.send(packet[offset:]), budget)
                need(sent > 0); offset += sent
            with http.client.HTTPResponse(wire.HTTPStream(tls, budget), method='GET') as resp:
                resp.begin()
                need(resp.version == 11 and not resp.headers.defects)
                pairs = resp.getheaders()
                need(sum(len(k)+len(v) for k,v in pairs) <= 16384
                     and all(not any(c in v for c in '\r\n\0') for _,v in pairs))
                # Body is irrelevant and never retained. The connection is closed,
                # not pooled, so no ambiguous body framing can reach another call.
                result = resp.status, resp.headers.get_all('WWW-Authenticate', []), resp.headers.get_all('Location', [])
                budget.left()
                return result
        finally:
            for conn in (tls, raw):
                if conn is not None:
                    try: conn.close()
                    except OSError: pass

    def _token(self, location, state):
        need(type(location) is str and len(location) <= 8192
             and all(32 < ord(c) < 127 for c in location)
             and not re.search(r'%(?![A-Fa-f0-9]{2})', location))
        u = urlsplit(location)
        # Exact implicit return URI; never follow even a same-origin redirect.
        need(location.split('#', 1)[0] == self._origin + '/oauth/token/implicit'
             and u.fragment and not u.query)
        pairs = parse_qsl(u.fragment, keep_blank_values=True, strict_parsing=True,
                          encoding='utf-8', errors='strict', max_num_fields=8)
        values = dict(pairs)
        need(len(values) == len(pairs)
             and set(values) in ({'access_token','token_type','expires_in','state'},
                                 {'access_token','token_type','expires_in','state','scope'})
             and hmac.compare_digest(values['state'], state)
             and values['token_type'].lower() == 'bearer'
             and re.fullmatch(r'[1-9][0-9]{0,9}', values['expires_in'])
             and re.fullmatch(r'sha256~[A-Za-z0-9_-]{32,256}', values['access_token']))
        token = values['access_token']
        name = 'sha256~' + base64.urlsafe_b64encode(hashlib.sha256(
            token.removeprefix('sha256~').encode('ascii')).digest()).decode().rstrip('=')
        self._metadata = {'oauth_object_name':name, 'expires_in':int(values['expires_in']),
                          'configured_max_lifetime_known':False, 'server_revocation_verified':False}
        return BearerCredential(token)

    def login(self):
        with self._lock:
            if self._used:
                raise LoginError('P2_LOGIN_CONSUMED')
            self._used = True
            error = credential = authorization = None
            try:
                need(self._password is not None and self._now() < self._end)
                state = secrets.token_hex(32)
                path = '/oauth/authorize?' + urlencode({'client_id':'openshift-challenging-client',
                    'response_type':'token', 'redirect_uri':self._origin+'/oauth/token/implicit', 'state':state})
                self._status = 'CHALLENGE_ATTEMPTED'
                status, challenges, locations = self._exchange(path, None)
                need(status == 401 and not locations and any(
                    re.match(r'(?i)^\s*basic(?:\s+|$)', v) for v in challenges))
                authorization = base64.b64encode((self._username+':'+self._password).encode('utf-8')).decode('ascii')
                self._password = None
                self._status = 'TOKEN_OUTCOME_UNCONFIRMED'
                status, _, locations = self._exchange(path, authorization)
                need(status == 302 and len(locations) == 1)
                credential = self._token(locations[0], state)
                # Preserve private metadata even if the deadline expires here.
                need(self._now() < self._end)
                self._status = 'TOKEN_RECEIVED'
            except BaseException as exc:
                error = _public(exc)
            finally:
                self._password = authorization = None
            if error is not None:
                credential = None
                raise error from None
            return credential

    def status(self):
        with self._lock:
            return {'login':self._status, 'password_reference_released':self._password is None}

    def take_metadata(self):
        with self._lock:
            value, self._metadata = self._metadata, None
            return value  # Private recovery handoff; no token/password.

    def close(self):
        with self._lock:
            self._used, self._password = True, None
        return True  # Local reference release only, no server operation.
