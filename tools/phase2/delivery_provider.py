"""Opt-in provider/session composition for reviewed integration code.

Concrete GET/reconciliation/TLS/session wiring, NOT an authority authenticator.
Fresh approval, platform/credential, producer, baseline admission and completion
adapters MUST authenticate their originals and enforce I/O deadlines themselves.
There is no JSON/JWT/flag/plugin loader, token issuer or configured site default.
"""
from dataclasses import dataclass, field
import time

import delivery_adapter as core
import delivery_diagnostics as diagnostics
from delivery_object_reader import (BearerCredential, ObjectProvider, ObjectReader, safe_code)
from delivery_objects import ObjectReconciler
from delivery_sender_binding import bind_tls_sender
from delivery_verifier import (ApprovalEvidence, PlatformEvidence, ProducerEvidence,
    CompletionEvidence, Progress, Provenance, ReadRequest, VerificationError,
    Verifier, provenance_checked, require)
from producer_bridge import CommittedProducerBridge
from delivery_session import run_session


@dataclass(frozen=True)
class AuthorityAdapters:
    approval: object = field(repr=False)
    platform: object = field(repr=False)
    producer: object = field(repr=False)
    baseline: object = field(repr=False)
    finish: object = field(repr=False)


class AuthorityBundle:
    """Owned, single-use factory result. Shared source readers are NOT owned.

    A factory must clean partial construction itself before returning a bundle.
    close() releases local references only, never server-side authorization.
    """
    def __init__(self, adapters, owned):
        require(type(adapters) is AuthorityAdapters and type(owned) is tuple
                and all(callable(getattr(x, 'close', None)) for x in owned)
                and len({id(x) for x in owned}) == len(owned),
                'AUTHORITY_BUNDLE_INVALID')
        self.adapters, self.owned, self.closed = adapters, owned, False

    def close(self):
        if self.closed:
            return
        self.closed = True
        failed = False
        for handle in reversed(self.owned):
            try:
                handle.close()
            except Exception:
                failed = True
        self.adapters, self.owned = None, ()
        require(not failed, 'AUTHORITY_CLEANUP_FAILED')


@dataclass(frozen=True)
class AdmittedBaseline:
    """Trusted admission result, not a schema accepted from user JSON.

    None entries mean authenticated exact-object 404, never a failed read.
    The adapter must verify permitted existing owners/specs before returning.
    Hash equality and the existence of this Python object prove no admission.
    """
    request: ReadRequest = field(repr=False)
    binding: core.Binding
    provenance: Provenance
    objects: tuple = field(repr=False)


class OperationalProviders:
    def __init__(self, binding, credential, progress, *, rendered, contract,
                 validator, adapters, diagnostic_sink, wall, monotonic, observation_sink=None):
        core.binding_checked(binding)
        require(type(credential) is BearerCredential and callable(progress)
                and type(adapters) is AuthorityAdapters
                and all(callable(getattr(adapters, n)) for n in
                        ('approval', 'platform', 'producer', 'baseline', 'finish'))
                and callable(diagnostic_sink), 'OPERATIONAL_CONFIGURATION_INVALID')
        self.binding, self.credential, self.progress = binding, credential, progress
        self.rendered, self.contract, self.validator = rendered, contract, validator
        self.adapters, self.diagnostic_sink = adapters, diagnostic_sink
        self.wall, self.monotonic = wall, monotonic
        self.reader = self.object_provider = self.pins = None
        self.diagnostic_status = 'NONE'
        self.object_failure_code = None
        self.closed = False
        self.observation_sink, self._sender_closed = observation_sink, None

    @property
    def wants_finish_observation(self):
        from delivery_finish_observation import ReadbackFinishAuthority
        return self.adapters is not None and type(self.adapters.finish) is ReadbackFinishAuthority

    def sender_closed(self, event):
        from delivery_session import SenderClosed
        require(self.wants_finish_observation and self._sender_closed is None
                and type(event) is SenderClosed and event.credential is self.credential
                and event.binding_key == self.binding.key and event.progress_key == self.progress().key,
                'FINISH_CLOSE_EVENT_INVALID')
        self._sender_closed = event

    def request_checked(self, r):
        require(not self.closed and type(r) is ReadRequest and r.binding == self.binding
                and r.credential is self.credential and type(r.progress) is Progress
                and self.progress() == r.progress, 'OPERATIONAL_REQUEST_INVALID')
        now = self.monotonic()
        require(core.finite(now) and core.finite(r.deadline) and now < r.deadline,
                'PROVIDER_DEADLINE_EXCEEDED')

    def read(self, name, r, kind):
        self.request_checked(r)
        try:
            value = getattr(self.adapters, name)(r)
        except Exception:
            raise VerificationError('AUTHORITY_ADAPTER_FAILED') from None
        self.request_checked(r)
        require(type(value) is kind and value.request is r
                and (name == 'finish' or value.binding == self.binding),
                'AUTHORITY_EVIDENCE_INVALID')
        provenance_checked(value.provenance)
        return value

    def approval(self, r):
        return self.read('approval', r, ApprovalEvidence)

    def platform(self, r):
        p = self.read('platform', r, PlatformEvidence)
        require(p.credential is self.credential
                and p.credential_fingerprint == self.credential.fingerprint,
                'PLATFORM_CREDENTIAL_MISMATCH')
        # Pin before any object GET; changes never rebuild a reader silently.
        pins = (p.binding, p.credential_fingerprint, p.ca_pem, p.endpoint_ip,
                p.issuer, p.audience, p.requested_seconds, p.issued_at,
                p.expires_at, p.raw_receipt, p.provenance)
        require(self.pins is None or self.pins == pins, 'PLATFORM_CONTEXT_CHANGED')
        if self.reader is None:
            self.reader = ObjectReader(self.binding, credential=self.credential,
                ca_pem=p.ca_pem, endpoint_ip=p.endpoint_ip, monotonic=self.monotonic)
            self.pins = pins
        return p

    def producer(self, r):
        return self.read('producer', r, ProducerEvidence)

    def objects(self, r):
        self.request_checked(r)
        require(self.reader is not None, 'PLATFORM_CONTEXT_REQUIRED')
        if self.object_provider is None:
            # Never adopt a new baseline after a write, ACK or recovery resume.
            require(r.progress == Progress(), 'BASELINE_ADMISSION_REQUIRED')
            baseline = self.read('baseline', r, AdmittedBaseline)
            reconciler = ObjectReconciler(self.binding, self.rendered, self.contract,
                                         self.validator, baseline.objects)
            self.object_provider = ObjectProvider(self.reader, reconciler, self.progress)
        try:
            result = self.object_provider.objects(r)
            self.request_checked(r)
            return result
        except Exception as error:
            # Verifier intentionally masks provider exceptions. Preserve only
            # an exact reviewed object code, never the exception or its text.
            self.object_failure_code = safe_code(error, None)
            d = self.object_provider.failure_diagnostics
            if d is not None:
                try:
                    self.diagnostic_sink(self.binding, r.progress, self.wall(), d)
                    self.diagnostic_status = 'RECORDED'
                except Exception:
                    self.diagnostic_status = 'FAILED'
                    raise VerificationError('DIAGNOSTIC_PERSIST_FAILED') from None
            raise

    def finish(self, r):
        if self.wants_finish_observation:
            self.request_checked(r)
            value = self.adapters.finish.finish_observed(r, self._sender_closed,
                self.object_provider, self.observation_sink)
            self.request_checked(r)
            require(type(value) is CompletionEvidence and value.request is r,
                    'AUTHORITY_EVIDENCE_INVALID')
            provenance_checked(value.provenance)
            return value
        return self.read('finish', r, CompletionEvidence)

    def close(self):
        self.closed = True
        if self.reader is not None:
            self.reader.close()
        self.reader = self.object_provider = self.adapters = self.credential = None
        self.pins = None
        self.object_failure_code = None
        self.observation_sink = self._sender_closed = None


@dataclass(frozen=True)
class RunInputs:
    binding: core.Binding
    rendered: bytes = field(repr=False)
    contract: bytes = field(repr=False)
    source_root: object = field(repr=False)
    store_root: object = field(repr=False)
    budget_seconds: float
    finalize: bool = False  # request only; fresh finish evidence remains mandatory
    local_time_checks: bool = False


class OperationalApplication:
    """Single-use, code-configured entry point; no ambient credential discovery.

    Only reviewed site code may construct this with authenticating adapters and
    actual input bytes. The standalone CLI cannot load/construct an application.
    Clock/dependency keywords support isolated tests, never CLI configuration.
    """
    def __init__(self, inputs, credential, adapters=None, *, authority_factory=None, wall=time.time,
                 monotonic=time.monotonic, sleep=time.sleep):
        require(type(inputs) is RunInputs and type(credential) is BearerCredential
                and ((type(adapters) is AuthorityAdapters and authority_factory is None)
                     or (adapters is None and callable(authority_factory))),
                'OPERATIONAL_CONFIGURATION_INVALID')
        core.binding_checked(inputs.binding)
        require(core.finite(inputs.budget_seconds) and inputs.budget_seconds > 0
                and type(inputs.finalize) is bool, 'OPERATIONAL_CONFIGURATION_INVALID')
        self.inputs, self._credential, self._adapters = inputs, credential, adapters
        self._authority_factory, self._authority_bundle = authority_factory, None
        self.wall, self.monotonic, self.sleep = wall, monotonic, sleep
        self.used = False
        self._providers = None
        self._diagnostic_phase = 'session'

    def status(self):
        return {'schema': 'P2-CICD-operational-1',
                'status': 'CONSUMED' if self.used else 'CONFIGURED_NOT_VERIFIED',
                'execution_authorized': False,
                'runtime': 'NOT_VERIFIED' if self.used else 'NOT_RUN'}

    def factory(self, binding, credential, progress):
        require(self._providers is None, 'OPERATIONAL_APPLICATION_CONSUMED')
        if self._authority_factory is not None:
            bundle = self._authority_factory(binding, credential, progress)
            require(type(bundle) is AuthorityBundle and not bundle.closed,
                    'AUTHORITY_BUNDLE_INVALID')
            # Retain before provider construction, so its failure is cleaned too.
            self._authority_bundle = bundle
            self._adapters = bundle.adapters
        self._providers = OperationalProviders(binding, credential, progress,
            rendered=self.inputs.rendered, contract=self.inputs.contract,
            validator=self._validator, adapters=self._adapters,
            diagnostic_sink=lambda b, p, t, d: diagnostics.persist(
                self.inputs.store_root, b, p, t, d, phase=self._diagnostic_phase),
            observation_sink=lambda b, raw: self.persist_observation(b, raw),
            wall=self.wall, monotonic=self.monotonic)
        return self._providers

    def persist_observation(self, binding, raw):
        from delivery_finish_observation import persist
        persist(self.inputs.store_root, binding, raw)

    def run(self, command):
        result = {'schema': 'P2-CICD-operational-1', 'status': 'STOPPED',
                  'error_code': 'OPERATIONAL_PREPARATION_FAILED',
                  'execution_authorized': False, 'runtime': 'NOT_VERIFIED',
                  'delivery_gate': 'NOT_VERIFIED', 'diagnostics': 'NONE'}
        try:
            require(not self.used, 'OPERATIONAL_APPLICATION_CONSUMED')
            self.used = True
            require(command in ('preflight', self.inputs.binding.action),
                    'OPERATIONAL_ACTION_MISMATCH')
            self._diagnostic_phase = 'preflight' if command == 'preflight' else 'session'
            self._validator = CommittedProducerBridge(self.inputs.source_root, self.inputs.binding)
            if command == 'preflight':
                core.prepare(self.inputs.binding, self.inputs.rendered, self.inputs.contract,
                             self._validator)
                provider = self.factory(self.inputs.binding, self._credential, lambda: Progress())
                Verifier(providers=provider, credential=self._credential,
                    progress=lambda: Progress(), budget_seconds=self.inputs.budget_seconds,
                    wall=self.wall, monotonic=self.monotonic)(self.inputs.binding)
                result.update(status='PREFLIGHT_OBSERVED', error_code=None)
            else:
                result.update(run_session(self.inputs.binding, self.inputs.rendered,
                    self.inputs.contract, self._validator, root=self.inputs.store_root,
                    providers=None, credential=self._credential, provider_factory=self.factory,
                    sender_factory=bind_tls_sender, budget_seconds=self.inputs.budget_seconds,
                    wall=self.wall, monotonic=self.monotonic, sleep=self.sleep,
                    finalize=self.inputs.finalize, local_time_checks=self.inputs.local_time_checks))
        except Exception as error:
            # Exact reviewed codes only; never stringify provider exceptions.
            fallback = result['error_code']
            if self._providers is not None and self._providers.object_failure_code is not None:
                fallback = self._providers.object_failure_code
            result['error_code'] = safe_code(error, fallback)
        finally:
            if self._providers is not None:
                result['diagnostics'] = self._providers.diagnostic_status
                try:
                    self._providers.close()
                except Exception:
                    result.update(status='STOPPED', error_code='AUTHORITY_CLEANUP_FAILED')
            if self._authority_bundle is not None:
                try:
                    self._authority_bundle.close()
                except Exception:
                    result.update(status='STOPPED', error_code='AUTHORITY_CLEANUP_FAILED')
            self._authority_bundle = self._authority_factory = None
            self._credential = self._adapters = None
        return result
