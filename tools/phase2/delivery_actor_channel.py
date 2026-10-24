"""Explicit in-memory named OAuth actor -> existing TokenRequest/SSH supplier.

Construction/close never log in, issue, connect, revoke, read ambient credentials
or write kubeconfig. send_over_bridge is a future Runtime operation, not GO.
Only modern sha256~ OAuth tokens are supported; no legacy token-in-URL fallback.
"""
import base64
import hashlib
import re
import threading

import delivery_adapter as core
import delivery_crosshost_ingress as ingress
import delivery_transport as wire
import delivery_token_request as issuance
from delivery_object_reader import BearerCredential

WHOAMI = '/apis/user.openshift.io/v1/users/~'


class ActorChannelError(RuntimeError):
    """Fixed public codes only; server bodies and token names remain private."""


def need(ok, code='ACTOR_CHANNEL_INVALID'):
    if not ok:
        raise ActorChannelError(code)


def public_error(error, fallback):
    if isinstance(error, KeyboardInterrupt):
        return KeyboardInterrupt()
    if isinstance(error, SystemExit):
        return SystemExit(130)
    codes = {'ACTOR_CHANNEL_INVALID', 'ACTOR_CHANNEL_CONSUMED',
             'ACTOR_IDENTITY_UNCONFIRMED', 'ACTOR_CLOCK_INVALID',
             'ACTOR_DEADLINE_INVALID', 'ACTOR_REVOCATION_UNCONFIRMED'}
    value = error.args[0] if type(error) is ActorChannelError and len(error.args) == 1 else None
    return ActorChannelError(value if type(value) is str and value in codes else fallback)


class ActorCredentialChannel(wire.Sender, ingress._NoCopy):
    """Own references to a caller-supplied DEDICATED actor credential.

    The caller separately authorizes use and revocation of that actor's session.
    Checking the username proves neither Runtime GO nor the issued SA's RBAC.
    All source/target pins and receiver original/control/finish checks remain.
    The declared cleanup deadline is independent of the delivery deadline.
    No retry after an uncertain request. Local release is NOT memory zeroization,
    OAuth server acknowledgement, SA-token revocation or resource cleanup.
    """
    def __init__(self, binding, *, actor, expected_username, target, ca_pem,
                 endpoint_ip, operation_deadline, cleanup_deadline, monotonic, wall):
        self._lock = threading.RLock()
        self._closed = self._used = False
        self._receipt = None
        self._phase = None
        self._authentication = 'NOT_ATTEMPTED'
        self._revocation = 'NOT_ATTEMPTED'
        self._issuance_attempted = self._forwarded = False
        self._last = None
        error = None
        try:
            need(type(actor) is BearerCredential and type(target) is issuance.TokenTarget)
            target.check()
            # Avoid putting a legacy bearer token (or SA JWT) in a request URL.
            need(type(actor.token) is str and
                 re.fullmatch(r'sha256~[A-Za-z0-9_-]{32,256}', actor.token) is not None)
            need(type(expected_username) is str and
                 re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.@-]{0,252}', expected_username) is not None)
            need(core.finite(operation_deadline) and core.finite(cleanup_deadline)
                 and operation_deadline < cleanup_deadline
                 and callable(monotonic) and callable(wall))
            super().__init__(binding, ca_pem=ca_pem, token=actor.token, endpoint_ip=endpoint_ip)
            name = 'sha256~' + base64.urlsafe_b64encode(
                hashlib.sha256(actor.token[len('sha256~'):].encode('ascii')).digest()
            ).decode('ascii').rstrip('=')
            self._delete_path = '/apis/oauth.openshift.io/v1/oauthaccesstokens/' + name
            self._name = name
            self._expected = expected_username
            self._target, self._ca = target, ca_pem
            self._operation_end, self._cleanup_end = operation_deadline, cleanup_deadline
            self._clock, self._wall = monotonic, wall
        except BaseException as exc:
            error = public_error(exc, 'ACTOR_CHANNEL_INVALID')
        if error is not None:
            self._release()
            actor = None
            name = None
            raise error from None

    def __repr__(self):
        return '<ActorCredentialChannel: private, explicit single use>'

    def _now(self):
        now = self._clock()
        need(core.finite(now) and (self._last is None or now >= self._last),
             'ACTOR_CLOCK_INVALID')
        self._last = now
        return now

    def _check_scope(self, request):
        need(not self._closed and self._token is not None)
        if self._phase == 'AUTHENTICATE':
            need(request.method == 'GET' and request.path == WHOAMI
                 and request.content_type == '' and request.body == b'')
        elif self._phase == 'REVOKE':
            need(request.method == 'DELETE' and request.path == self._delete_path
                 and request.content_type == 'application/json'
                 and request.body == core.encoded({'apiVersion':'v1','kind':'DeleteOptions'}))
        else:
            raise ActorChannelError('ACTOR_CHANNEL_INVALID')

    def __call__(self, request):
        raise ActorChannelError('ACTOR_EXPLICIT_SEND_REQUIRED')

    def _read_response(self, sock, request, deadline):
        # 202/204/401/403/404 never mean confirmed deletion in this adapter.
        return wire.read_json_response(sock, request, deadline, (200,))[2]

    def _request(self, method, path, body, end):
        seconds = min(10, end - self._now())
        need(seconds > 0, 'ACTOR_DEADLINE_INVALID')
        budget = wire.Deadline(seconds, self._now)
        budget.end = min(budget.end, end)
        request = core.Request(self._binding.key, self._fingerprint,
            self._binding.api_origin, self._binding.ca_sha256, method, path,
            'application/json' if body else '', body, seconds)
        result = self._exchange(request, budget)
        budget.left()
        need(not self._closed, 'ACTOR_CHANNEL_CONSUMED')
        return result

    def _authenticate(self):
        self._authentication = 'UNCONFIRMED'
        self._phase = 'AUTHENTICATE'
        obj = self._request('GET', WHOAMI, b'', self._operation_end)
        meta = obj.get('metadata') if type(obj) is dict else None
        need(type(meta) is dict and obj.get('apiVersion') == 'user.openshift.io/v1'
             and obj.get('kind') == 'User' and meta.get('name') == self._expected
             and issuance.text(meta.get('uid')) and meta.get('deletionTimestamp') is None,
             'ACTOR_IDENTITY_UNCONFIRMED')
        self._authentication = 'CONFIRMED'

    def _revoke(self):
        self._revocation = 'UNCONFIRMED'
        self._phase = 'REVOKE'
        obj = self._request('DELETE', self._delete_path,
            core.encoded({'apiVersion':'v1','kind':'DeleteOptions'}), self._cleanup_end)
        need(type(obj) is dict, 'ACTOR_REVOCATION_UNCONFIRMED')
        # OpenShift's OAuth store returns the deleted OAuthAccessToken object.
        # Also accept the Kubernetes successful Status response, never arbitrary 200 JSON.
        if obj.get('kind') == 'OAuthAccessToken':
            meta = obj.get('metadata')
            need(obj.get('apiVersion') == 'oauth.openshift.io/v1'
                 and type(meta) is dict and meta.get('name') == self._name,
                 'ACTOR_REVOCATION_UNCONFIRMED')
        else:
            details = obj.get('details')
            need(obj.get('apiVersion') == 'v1' and obj.get('kind') == 'Status'
                 and obj.get('status') == 'Success'
                 and (obj.get('code') is None or type(obj['code']) is int and obj['code'] == 200)
                 and (details is None or type(details) is dict
                      and details.get('name', self._name) == self._name),
                 'ACTOR_REVOCATION_UNCONFIRMED')
        self._revocation = 'ACKNOWLEDGED'  # Server response, NOT global cache invalidation proof.

    def send_over_bridge(self, *, bridge, request, source_tokens):
        """Authenticate -> existing single-use issue/forward -> server cleanup.

        Retain the private issuance receipt even if handoff or cleanup fails.
        Never revoke an actor whose identity could not be confirmed. In that
        case stop and report unresolved server cleanup instead of guessing.
        Local references are always released; there is no implicit retry.
        """
        from delivery_ssh_bridge import SshCredentialBridge
        with self._lock:
            need(not self._used and not self._closed, 'ACTOR_CHANNEL_CONSUMED')
            self._used = True
            supplier = issuer = error = cleanup_error = None
            try:
                need(type(bridge) is SshCredentialBridge
                     and bridge._budget.clock is self._clock
                     and bridge._budget.limit <= self._operation_end)
                ingress._request(request)
                need(request['target']['binding_key'] == self._binding.key)
                # Reuse supplier validation before any network operation.
                issuer = issuance.TokenRequestIssuer(self._binding,
                    target=self._target, actor=BearerCredential(self._token),
                    ca_pem=self._ca, endpoint_ip=self._ip, deadline=self._operation_end,
                    monotonic=self._clock, wall=self._wall)
                supplier = issuance.RequestBoundSupplier(issuer=issuer,
                    request=request, source_tokens=source_tokens)
                self._authenticate()
                need(supplier.send_over_bridge(bridge) is True)
                self._forwarded = True
            except BaseException as exc:
                error = public_error(exc, 'ACTOR_HANDOFF_FAILED')
            finally:
                try:
                    if supplier is not None:
                        self._issuance_attempted = supplier.issuance_attempted
                        self._receipt = supplier.take_receipt()
                        supplier.close()
                    elif issuer is not None:
                        issuer.close()
                except BaseException as exc:
                    cleanup_error = public_error(exc, 'ACTOR_LOCAL_CLEANUP_FAILED')
                if self._authentication == 'CONFIRMED':
                    try:
                        self._revoke()
                    except BaseException as exc:
                        cleanup_error = public_error(exc, 'ACTOR_REVOCATION_UNCONFIRMED')
                self._release()
                source_tokens = request = bridge = supplier = issuer = None
            # Do not swallow cancellation; status preserves uncertain cleanup.
            if isinstance(error, (KeyboardInterrupt, SystemExit)):
                raise error from None
            if cleanup_error is not None:
                raise cleanup_error from None
            if error is not None:
                raise error from None
            return self.status()

    def _release(self):
        self._closed = True
        self._token = self._name = self._delete_path = self._phase = None

    def status(self):
        with self._lock:
            return {'actor_identity':self._authentication,
                    'issuance_attempted':self._issuance_attempted,
                    'credential_forwarded':self._forwarded,
                    'actor_server_revocation':self._revocation,
                    'local_references_released':self._closed,
                    'sa_token_revoked':False, 'job_accepted':False,
                    'resource_cleanup_completed':False}

    def take_receipt(self):
        with self._lock:
            value, self._receipt = self._receipt, None
            return value  # PRIVATE token-bearing original; never automatically publish/adopt.

    def close(self):
        with self._lock:
            self._used = True
            self._receipt = None
            self._release()
        return True  # Local references only; no network.
