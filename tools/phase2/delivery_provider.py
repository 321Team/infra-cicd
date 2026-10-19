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
                 validator, adapters, diagnostic_sink, wall, monotonic):
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
        return self.read('finish', r, CompletionEvidence)

    def close(self):
        self.closed = True
        self.reader = self.object_provider = self.adapters = self.credential = None
        self.object_failure_code = None


@dataclass(frozen=True)
class RunInputs:
    binding: core.Binding
    rendered: bytes = field(repr=False)
    contract: bytes = field(repr=False)
    source_root: object = field(repr=False)
    store_root: object = field(repr=False)
    budget_seconds: float
    finalize: bool = False  # request only; fresh finish evidence remains mandatory


class OperationalApplication:
    """Single-use, code-configured entry point; no ambient credential discovery.

    Only reviewed site code may construct this with authenticating adapters and
    actual input bytes. The standalone CLI cannot load/construct an application.
    Clock/dependency keywords support isolated tests, never CLI configuration.
    """
    def __init__(self, inputs, credential, adapters, *, wall=time.time,
                 monotonic=time.monotonic, sleep=time.sleep):
        require(type(inputs) is RunInputs and type(credential) is BearerCredential
                and type(adapters) is AuthorityAdapters, 'OPERATIONAL_CONFIGURATION_INVALID')
        core.binding_checked(inputs.binding)
        require(core.finite(inputs.budget_seconds) and inputs.budget_seconds > 0
                and type(inputs.finalize) is bool, 'OPERATIONAL_CONFIGURATION_INVALID')
        self.inputs, self._credential, self._adapters = inputs, credential, adapters
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
        self._providers = OperationalProviders(binding, credential, progress,
            rendered=self.inputs.rendered, contract=self.inputs.contract,
            validator=self._validator, adapters=self._adapters,
            diagnostic_sink=lambda b, p, t, d: diagnostics.persist(
                self.inputs.store_root, b, p, t, d, phase=self._diagnostic_phase),
            wall=self.wall, monotonic=self.monotonic)
        return self._providers

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
                    finalize=self.inputs.finalize))
        except Exception as error:
            # Exact reviewed codes only; never stringify provider exceptions.
            fallback = result['error_code']
            if self._providers is not None and self._providers.object_failure_code is not None:
                fallback = self._providers.object_failure_code
            result['error_code'] = safe_code(error, fallback)
        finally:
            if self._providers is not None:
                result['diagnostics'] = self._providers.diagnostic_status
                self._providers.close()
            self._credential = self._adapters = None
        return result
