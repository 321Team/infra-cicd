"""Owned memory credentials -> public READY pull -> existing actor/SSH channel.

Input is an explicit in-process actor or deferred LoginActorChannel plus source
tokens, never file/env/argv. Orchestration does not imply execution authorization.
"""
import copy
from dataclasses import replace
import threading

import delivery_actor_channel as actor
import delivery_login_channel as login
import delivery_crosshost_ingress as ingress
import delivery_ready_pull as ready
import delivery_ssh_bridge as ssh


class SupplyError(RuntimeError):
    """Public fixed code; raw callback errors and credentials never escape."""


def need(ok):
    if not ok:
        raise SupplyError('P2_MEMORY_SUPPLY_INVALID')


class MemoryOperatingSupplier(ingress._NoCopy):
    """Take exclusive channel + copied source refs only after validation succeeds.

    A direct actor token or deferred login password is owned by the channel. Caller
    must release its own aliases. Python reference cleanup is not zeroization.
    Public READY failure occurs before actor use/login. Direct actor server cleanup
    remains explicit; deferred login creates no token before READY.
    """
    def __init__(self, *, ready_pull, bridge, actor_channel, source_tokens):
        try:
            need(type(ready_pull) is ready.SshReadyPull
                 and type(bridge) is ssh.SshCredentialBridge
                 and type(actor_channel) in (actor.ActorCredentialChannel, login.LoginActorChannel))
            selected = ready_pull._selected
            need(selected['target']['binding_key'] == actor_channel._binding.key)
            need(ready_pull._budget.clock is bridge._budget.clock is actor_channel._clock
                 and ready_pull._budget.limit <= actor_channel._operation_end
                 and bridge._budget.limit <= actor_channel._operation_end)
            # Same management SSH trust/peer/interpreter; only reviewed helper
            # entrypoint differs. This is CODE composition, not remote attestation.
            r, b = ready_pull._config, bridge._config
            need(replace(r, remote_helper=b.remote_helper) == b
                 and r.remote_helper.endswith('/delivery_ready_pull_helper.py')
                 and b.remote_helper.endswith('/delivery_ssh_bridge_helper.py')
                 and r.remote_helper.rsplit('/',1)[0] == b.remote_helper.rsplit('/',1)[0])
            if type(actor_channel) is login.LoginActorChannel:
                actor_channel.check_sources(source_tokens, selected['repositories'])
            else:
                ingress._tokens(source_tokens, selected['repositories'], actor_channel._token)
            sources = copy.deepcopy(source_tokens)
        except Exception:
            raise SupplyError('P2_MEMORY_SUPPLY_INVALID') from None
        self._ready, self._bridge, self._actor = ready_pull, bridge, actor_channel
        self._sources, self._receipt, self._login_metadata = sources, None, None
        self._status = {'public_ready_received':False, **actor_channel.status()}
        self._used, self._lock = False, threading.RLock()

    def __repr__(self):
        return '<P2 operating memory supplier: single-use, private, no Runtime GO>'

    def send(self, *, include_original=False):
        with self._lock:  # Serialize close/take_receipt with the bounded operation.
            if self._used:
                raise SupplyError('P2_MEMORY_SUPPLY_CONSUMED')
            self._used = True
            error = None; request = None
            try:
                need(type(include_original) is bool)
                request = self._ready.pull()
                ready._matches(request, self._ready._selected)
                self._status['public_ready_received'] = True
                if include_original:
                    ingress.native_frame_budget_checked(request, self._sources)
                options = {'include_original': True} if include_original else {}
                self._actor.send_over_bridge(bridge=self._bridge, request=request,
                                             source_tokens=self._sources, **options)
            except BaseException as exc:
                if isinstance(exc, KeyboardInterrupt):
                    error = KeyboardInterrupt()
                elif isinstance(exc, SystemExit):
                    error = SystemExit(130)
                else:
                    error = SupplyError('P2_MEMORY_SUPPLY_FAILED')
            finally:
                try:
                    self._status.update(self._actor.status())
                    self._receipt = self._actor.take_receipt()
                    if type(self._actor) is login.LoginActorChannel:
                        self._login_metadata = self._actor.take_login_metadata()
                    self._actor.close()
                    self._status.update(self._actor.status())
                    self._status['local_references_released'] = True
                except BaseException as exc:
                    if not isinstance(error, (KeyboardInterrupt, SystemExit)):
                        error = (KeyboardInterrupt() if isinstance(exc, KeyboardInterrupt)
                                 else SystemExit(130) if isinstance(exc, SystemExit)
                                 else SupplyError('P2_MEMORY_SUPPLY_CLEANUP_FAILED'))
                self._sources = self._actor = self._ready = self._bridge = request = None
            if error is not None:
                raise error from None
            return self.status()

    def status(self):
        with self._lock:
            return dict(self._status)

    def take_receipt(self):
        with self._lock:
            value, self._receipt = self._receipt, None
            return value  # Private token-bearing original; caller owns after take.

    def take_login_metadata(self):
        with self._lock:
            value, self._login_metadata = self._login_metadata, None
            return value  # Private recovery handoff; no token/password.

    def close(self):
        with self._lock:
            self._used = True
            if self._actor is not None:
                self._actor.close()
                self._status.update(self._actor.status())
            self._sources = self._actor = self._ready = self._bridge = self._receipt = None
            self._status['local_references_released'] = True
        return True  # Local refs only; not server revocation or resource cleanup.
