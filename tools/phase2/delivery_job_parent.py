"""Explicit one-use JobSite.build helper; no configured site or live transport."""
import threading
import time

import delivery_crosshost_ingress as ingress
import delivery_job as job
import delivery_job_handoff as handoff
import delivery_runtime_site as runtime
import delivery_site as site

SAFE_CODES = frozenset('P2_PARENT_CONFIGURATION_INVALID P2_PARENT_CONSUMED P2_PARENT_BUILD_FAILED P2_PARENT_CLEANUP_FAILED'.split())


class ParentBuildError(RuntimeError):
    """Fixed public codes only; no callback text, token, native path or body."""


def need(ok, code):
    if not ok:
        raise ParentBuildError(code)


class ParentJobBuilder(ingress._NoCopy):
    """Job driver -> public readiness -> reviewed channel -> owned invocation.

    publish_ready/open_channel are explicit trusted code. They must honor the
    supplied absolute deadline, avoid secret logs, and clean their own partial
    resources if raising before transferring an exact ReviewedIngressChannel.
    Native context/rendezvous metadata is never an execution grant.
    """
    def __init__(self, *, config, plan, finish_slot, expected_peer, routes,
                 deadline, wire, publish_ready, open_channel, monotonic=time.monotonic,
                 finish_capture=None, producer_pins=None):
        need(all(callable(x) for x in (wire, publish_ready, open_channel, monotonic)),
             'P2_PARENT_CONFIGURATION_INVALID')
        self._config, self._plan, self._slot = config, plan, finish_slot
        self._peer, self._routes, self._deadline = expected_peer, routes, deadline
        self._wire, self._publish, self._open, self._clock = wire, publish_ready, open_channel, monotonic
        self._capture, self._pins = finish_capture, producer_pins
        self._used, self._lock = False, threading.Lock()

    def __repr__(self):
        return '<P2 job parent builder: private, single-use, unconfigured transport>'

    def __call__(self, context, command):
        with self._lock:
            need(not self._used, 'P2_PARENT_CONSUMED')
            self._used = True
        receiver = bundle = value = None
        error = interrupted = None
        cleanup_failed = False
        try:
            need(type(context) is job.JobContext, 'P2_PARENT_CONFIGURATION_INVALID')
            context.check()
            site.configuration_checked(self._config)  # Shape only; no input file read.
            need(runtime.plan_binding(self._plan) == self._config.binding
                 and type(self._slot) is runtime.FinishAdoptionSlot
                 and self._slot.binding == self._config.binding
                 and type(command) is str and command in ('preflight', self._config.binding.action),
                 'P2_PARENT_CONFIGURATION_INVALID')
            if type(self._plan) is runtime.PreparedAuthorityPlan:
                self._plan.bind_context(context)
            from delivery_job_sources import SourceRoleWiring
            if type(self._wire) is SourceRoleWiring:
                self._wire.check_channels(self._config.binding, self._routes)
            receiver = ingress.CrossHostIngress(context=context, binding=self._config.binding,
                expected_peer=self._peer, routes=self._routes, deadline=self._deadline,
                monotonic=self._clock)
            self._publish(receiver.request(), self._deadline)  # Public request has no credentials.
            channel = self._open(receiver.request(), self._deadline)  # Fresh request + deadline recheck.
            # receive owns exact channels even if open consumed the remaining time.
            bundle = receiver.receive(channel)
            value = bundle.build_invocation(context, command, config=self._config, plan=self._plan,
                finish_slot=self._slot, wire=self._wire, handoff_deadline=self._deadline,
                finish_capture=self._capture, producer_pins=self._pins)
        except BaseException as exc:
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                interrupted = KeyboardInterrupt if isinstance(exc, KeyboardInterrupt) else SystemExit
            code = exc.args[0] if type(exc) in (ParentBuildError, ingress.IngressError, handoff.HandoffError) and len(exc.args) == 1 else None
            allowed = SAFE_CODES | ingress.SAFE_CODES | handoff.SAFE_CODES
            error = code if type(code) is str and code in allowed else 'P2_PARENT_BUILD_FAILED'
        finally:
            # A return does not transfer ownership until this cleanup succeeds.
            # An interrupt in one close must not skip the remaining owned state.
            for temporary in (bundle, receiver):
                if temporary is not None:
                    try:
                        temporary.close()
                    except BaseException as exc:
                        cleanup_failed = True
                        if interrupted is None and isinstance(exc, (KeyboardInterrupt, SystemExit)):
                            interrupted = KeyboardInterrupt if isinstance(exc, KeyboardInterrupt) else SystemExit
            if error is not None or interrupted is not None or cleanup_failed:
                plan_attempted = False
                if type(value) is job.RuntimeInvocation:
                    try:
                        ok = value.close()  # Attempts all its owned handles.
                        plan_attempted = value.plan is self._plan
                        cleanup_failed = ok is not True or cleanup_failed
                    except BaseException as exc:
                        cleanup_failed = True
                        if interrupted is None and isinstance(exc, (KeyboardInterrupt, SystemExit)):
                            interrupted = KeyboardInterrupt if isinstance(exc, KeyboardInterrupt) else SystemExit
                # Never call close on a foreign return or a borrowed full plan.
                if not plan_attempted and type(self._plan) is runtime.PreparedAuthorityPlan:
                    try:
                        cleanup_failed = self._plan.close() is not True or cleanup_failed
                    except BaseException as exc:
                        cleanup_failed = True
                        if interrupted is None and isinstance(exc, (KeyboardInterrupt, SystemExit)):
                            interrupted = KeyboardInterrupt if isinstance(exc, KeyboardInterrupt) else SystemExit
        if interrupted is KeyboardInterrupt:
            raise KeyboardInterrupt() from None
        if interrupted is SystemExit:
            raise SystemExit(130) from None
        if cleanup_failed:
            raise ParentBuildError('P2_PARENT_CLEANUP_FAILED') from None
        if error is not None:
            raise ParentBuildError(error) from None
        return value  # Successful invocation transfers to the caller only here.
