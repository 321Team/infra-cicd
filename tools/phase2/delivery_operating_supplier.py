"""Owned memory credentials -> public READY pull -> existing actor/SSH channel.

Input is explicit in-process BearerCredential/source tokens, never file/env/argv.
This supplies orchestration, NOT a login provider or an execution authorization.
"""
import copy
from dataclasses import replace
import threading

import delivery_actor_channel as actor
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

    The dedicated actor token is already owned by ActorCredentialChannel. Caller
    must release its own aliases. Python reference cleanup is not zeroization.
    Public READY failure occurs before actor use; server revocation is then NOT
    ATTEMPTED and remains an explicit operator cleanup task, never false success.
    """
    def __init__(self, *, ready_pull, bridge, actor_channel, source_tokens):
        try:
            need(type(ready_pull) is ready.SshReadyPull
                 and type(bridge) is ssh.SshCredentialBridge
                 and type(actor_channel) is actor.ActorCredentialChannel)
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
            ingress._tokens(source_tokens, selected['repositories'], actor_channel._token)
            sources = copy.deepcopy(source_tokens)
        except Exception:
            raise SupplyError('P2_MEMORY_SUPPLY_INVALID') from None
        self._ready, self._bridge, self._actor = ready_pull, bridge, actor_channel
        self._sources, self._receipt = sources, None
        self._status = {'public_ready_received':False, **actor_channel.status()}
        self._used, self._lock = False, threading.RLock()

    def __repr__(self):
        return '<P2 operating memory supplier: single-use, private, no Runtime GO>'

    def send(self):
        with self._lock:  # Serialize close/take_receipt with the bounded operation.
            if self._used:
                raise SupplyError('P2_MEMORY_SUPPLY_CONSUMED')
            self._used = True
            error = None; request = None
            try:
                request = self._ready.pull()
                ready._matches(request, self._ready._selected)
                self._status['public_ready_received'] = True
                self._actor.send_over_bridge(bridge=self._bridge, request=request,
                                             source_tokens=self._sources)
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
                    self._actor.close()
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

    def close(self):
        with self._lock:
            self._used = True
            if self._actor is not None:
                self._actor.close()
                self._status.update(self._actor.status())
            self._sources = self._actor = self._ready = self._bridge = self._receipt = None
            self._status['local_references_released'] = True
        return True  # Local refs only; not server revocation or resource cleanup.
