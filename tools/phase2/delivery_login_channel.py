"""Deferred login -> existing named actor/issuance/SSH/receipt/cleanup contract.

The operating supplier calls this only AFTER its public READY check. This is
code wiring, not a configured OAuth endpoint, deployed helper or Runtime GO.
"""
import ipaddress
import ssl
import threading

import delivery_adapter as core
import delivery_actor_channel as actor
import delivery_crosshost_ingress as ingress
import delivery_oauth_login as login
import delivery_token_request as issuance
import delivery_ssh_bridge as ssh


class LoginActorChannel(ingress._NoCopy):
    def __init__(self, binding, *, login_provider, expected_username, target,
                 ca_pem, endpoint_ip, operation_deadline, cleanup_deadline, monotonic, wall):
        error = None
        try:
            core.binding_checked(binding)
            actor.need(type(login_provider) is login.BasicChallengeLogin
                       and not login_provider._used
                       and login_provider._username == expected_username
                       and login_provider._clock is monotonic
                       and core.finite(operation_deadline) and core.finite(cleanup_deadline)
                       and login_provider._end <= operation_deadline < cleanup_deadline
                       and callable(wall))
            actor.need(type(target) is issuance.TokenTarget); target.check()
            actor.need(type(ca_pem) is bytes and core.digest(ca_pem) == binding.ca_sha256
                       and type(endpoint_ip) is str and '%' not in endpoint_ip)
            ip = ipaddress.ip_address(endpoint_ip)
            if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
                ip = ip.ipv4_mapped
            actor.need(not (ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_unspecified))
            # Validate API trust before login; OAuth trust is separately supplied.
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            ctx.load_verify_locations(cadata=ca_pem.decode('ascii'))
        except BaseException as exc:
            error = actor.public_error(exc, 'ACTOR_CHANNEL_INVALID')
        if error is not None:
            raise error from None
        self._binding, self._clock, self._operation_end = binding, monotonic, operation_deadline
        self._provider = login_provider
        self._args = dict(expected_username=expected_username, target=target, ca_pem=ca_pem,
            endpoint_ip=endpoint_ip, operation_deadline=operation_deadline,
            cleanup_deadline=cleanup_deadline, monotonic=monotonic, wall=wall)
        self._used, self._lock = False, threading.RLock()
        self._receipt = self._metadata = None
        self._status = {'actor_identity':'NOT_ATTEMPTED', 'issuance_attempted':False,
            'credential_forwarded':False, 'actor_server_revocation':'NOT_ATTEMPTED',
            'local_references_released':False, 'sa_token_revoked':False,
            'job_accepted':False, 'resource_cleanup_completed':False}
        self._status.update(login_provider.status())

    def __repr__(self):
        return '<LoginActorChannel: deferred, private, explicit single-use>'

    def check_sources(self, values, repositories):
        # The future actor token does not exist. Validate shape/repository now;
        # the existing issued-token supplier compares actual tokens before use.
        actor.need(type(values) is list and len(values) == len(repositories))
        for item, repository in zip(values, repositories):
            actor.need(type(item) is dict and set(item) == {'repository','token'}
                       and item['repository'] == repository)
            ingress._token(item['token'], source=True)

    def send_over_bridge(self, *, bridge, request, source_tokens, include_original=False):
        with self._lock:
            actor.need(not self._used, 'ACTOR_CHANNEL_CONSUMED'); self._used = True
            channel = credential = error = None
            try:
                ingress._request(request)
                actor.need(type(include_original) is bool and type(bridge) is ssh.SshCredentialBridge
                           and bridge._budget.clock is self._clock
                           and bridge._budget.limit <= self._operation_end
                           and request['target']['binding_key'] == self._binding.key)
                self.check_sources(source_tokens, request['repositories'])
                credential = self._provider.login()
                channel = actor.ActorCredentialChannel(self._binding, actor=credential, **self._args)
                credential = None
                options = {'include_original': True} if include_original else {}
                channel.send_over_bridge(bridge=bridge, request=request, source_tokens=source_tokens, **options)
            except BaseException as exc:
                error = actor.public_error(exc, 'ACTOR_HANDOFF_FAILED')
            finally:
                if channel is not None:
                    self._status.update(channel.status())
                    self._receipt = channel.take_receipt()
                    channel.close()
                self._status.update(self._provider.status())
                self._metadata = self._provider.take_metadata()
                self.close()
                channel = credential = source_tokens = request = bridge = None
            if error is not None:
                raise error from None
            return self.status()

    def status(self):
        with self._lock:
            return dict(self._status)

    def take_receipt(self):
        with self._lock:
            value, self._receipt = self._receipt, None
            return value  # PRIVATE SA token-bearing original; never auto-adopt.

    def take_login_metadata(self):
        with self._lock:
            value, self._metadata = self._metadata, None
            return value  # Private recovery identity/lifetime, no credential.

    def close(self):
        with self._lock:
            self._used = True
            if self._provider is not None:
                self._provider.close()
                self._status.update(self._provider.status())
                if self._metadata is None:
                    self._metadata = self._provider.take_metadata()
            self._provider = self._args = None
            self._status['local_references_released'] = True
        return True
