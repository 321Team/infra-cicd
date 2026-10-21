"""Reviewed-code site composition. No production defaults or authority loader.

All facts are adopted from reviewed actual originals by trusted site code.
Source readers are borrowed; this module owns only the per-invocation adapters.
No token issuance, source publication, environment discovery or auto-retry.
"""
from dataclasses import dataclass, field
import time

import delivery_adapter as core
import delivery_cli as cli
import delivery_site as site
from delivery_adopted_authority import AdoptedGrant, CodeAdoptedGrantResolver, pin_identity
from delivery_approval_source import ReviewedApprovalAdapter
from delivery_baseline import FirstDeploymentBaseline
from delivery_credential_channel import PipeCredentialReceiver
from delivery_evidence_source import ReviewedEvidenceSource
from delivery_github_source import GitHubOriginalAdapter
from delivery_private_source import PrivateGitHubOriginalAdapter
from delivery_object_reader import ObjectReader, BearerCredential, Observation
from delivery_objects import normalized, decode
from delivery_platform_readback import (ExpectedSubject, PlatformObservationReader,
                                       ReadbackCheckedPlatformAdapter)
from delivery_provider import AuthorityAdapters, AuthorityBundle, AdmittedBaseline
from delivery_semantic_authority import (CodeAdoptedEvidenceResolver, IssuerFacts,
                                         ProducerFacts, FinishFacts, terminal_checked)
from delivery_verifier import Progress, Provenance, ReadRequest
from delivery_actions_source import ActionsRunSource, ActionsCheckedProducer, policy_checked


class RuntimeSiteError(RuntimeError):
    """Public errors contain only fixed codes."""


def need(ok, code):
    if not ok:
        raise RuntimeSiteError(code)


@dataclass(frozen=True)
class SourceReaders:
    original: object = field(repr=False)
    control: object = field(repr=False)

    def check(self):
        need(all(type(x) in (GitHubOriginalAdapter, PrivateGitHubOriginalAdapter)
                 for x in (self.original, self.control)), 'RUNTIME_SITE_READERS_INVALID')


@dataclass(frozen=True)
class EvidenceAdoption:
    grant: AdoptedGrant = field(repr=False)
    facts: object = field(repr=False)


@dataclass(frozen=True)
class BaselineAdoption:
    """Reviewed existing objects/owners from an actual original, not fresh-GET adoption."""
    grant: AdoptedGrant = field(repr=False)
    objects: tuple = field(repr=False)


@dataclass(frozen=True)
class RuntimeAuthorityPlan:
    approval: AdoptedGrant = field(repr=False)
    issuer: EvidenceAdoption = field(repr=False)
    producer: EvidenceAdoption = field(repr=False)
    subject: ExpectedSubject = field(repr=False)
    baseline: BaselineAdoption | None = field(default=None, repr=False)


class CodeAdoptedBaseline:
    """Fresh existing-object admission for an explicitly approved deploy/restore.

    Uses the existing conservative normalized comparison (status/RV volatility
    only). A fresh GET cannot replace the code-adopted object/UID/owner baseline.
    The original's permitted owner/spec meaning is the reviewed code's trust root.
    """
    def __init__(self, record, source_adapter, reader, progress):
        need(type(record) is BaselineAdoption and type(record.grant) is AdoptedGrant
             and type(record.objects) is tuple, 'RUNTIME_BASELINE_POLICY_INVALID')
        binding=record.grant.binding
        keys=core.objects_for(binding)
        need(len(record.objects)==len(keys), 'RUNTIME_BASELINE_POLICY_INVALID')
        if binding.action=='restore':
            need(all(raw is not None for raw in record.objects), 'RUNTIME_RESTORE_BASELINE_REQUIRED')
        self.expected=tuple(None if raw is None else core.encoded(normalized(decode(raw),key))
                            for key,raw in zip(keys,record.objects))
        self.record,self.source_adapter,self.reader,self.progress=record,source_adapter,reader,progress

    def __call__(self, request):
        need(self.reader is not None and request.binding==self.record.grant.binding
             and request.progress==Progress() and self.progress()==request.progress,
             'RUNTIME_BASELINE_REQUEST_INVALID')
        self.reader.check_request(request)
        before=self.source_adapter(request)
        for key,expected in zip(core.objects_for(request.binding),self.expected):
            self.reader.check_request(request)
            need(self.progress()==request.progress,'RUNTIME_BASELINE_PROGRESS_CHANGED')
            observation=self.reader.read(request,key)
            need(type(observation) is Observation and observation.key==key,
                 'RUNTIME_BASELINE_OBSERVATION_INVALID')
            actual=None if observation.status==404 else core.encoded(normalized(decode(observation.raw),key))
            need(type(observation.status) is int and observation.status in (200,404)
                 and actual==expected,'RUNTIME_BASELINE_DRIFT')
        after=self.source_adapter(request)
        self.reader.check_request(request)
        need(before==after and self.progress()==request.progress,'RUNTIME_BASELINE_AUTHORITY_CHANGED')
        return AdmittedBaseline(request,request.binding,after.provenance,self.record.objects)

    def close(self):
        self.record=self.source_adapter=self.reader=self.progress=self.expected=None


class FinishAdoptionSlot:
    """Trusted parent installs an ACTUAL terminal original once, never a future pin.

    install() is a code API, not a file/JSON loader or a grant. The parent must
    collect and review the actual follow-up/exclusive-end original first.
    Missing installation stops completion and leaves the existing durable fence.
    """
    def __init__(self, binding):
        core.binding_checked(binding)
        self.binding, self._record = binding, None

    @property
    def installed(self):
        return self._record is not None

    def install(self, record, progress):
        need(self._record is None, 'FINISH_ADOPTION_ALREADY_INSTALLED')
        need(type(record) is EvidenceAdoption and type(record.grant) is AdoptedGrant
             and record.grant.binding == self.binding and type(record.facts) is FinishFacts,
             'FINISH_ADOPTION_INVALID')
        terminal_checked(self.binding, progress)
        need(record.facts.progress_key == progress.key
             and record.facts.run_id == self.binding.run_id
             and record.facts.action == self.binding.action, 'FINISH_ADOPTION_PROGRESS_MISMATCH')
        pin_identity(record.grant.original_pin)
        self._record = record

    def get(self, request):
        need(type(request) is ReadRequest and request.binding == self.binding,
             'FINISH_ADOPTION_REQUEST_INVALID')
        terminal_checked(self.binding, request.progress)
        need(self._record is not None, 'FINISH_ADOPTION_NOT_INSTALLED')
        need(self._record.facts.progress_key == request.progress.key,
             'FINISH_ADOPTION_PROGRESS_MISMATCH')
        return self._record


class DeferredFinish:
    def __init__(self, binding, credential, progress, slot, readers, wall, monotonic, capture=None):
        need(capture is None or callable(capture),'FINISH_CAPTURE_CONFIGURATION_INVALID')
        self.binding, self.credential, self.progress = binding, credential, progress
        self.slot, self.readers, self.wall, self.monotonic = slot, readers, wall, monotonic
        self.capture,self.capture_called,self._last,self._anchor=capture,False,None,None

    def check(self,request):
        need(self.credential is not None and type(request) is ReadRequest
             and request.binding==self.binding and request.credential is self.credential
             and self.progress()==request.progress,'FINISH_ADOPTION_REQUEST_INVALID')
        wall,mono=self.wall(),self.monotonic()
        need(core.finite(wall) and core.finite(mono) and (self._last is None or
             wall>=self._last[0] and mono>=self._last[1]),'FINISH_CAPTURE_CLOCK_INVALID')
        self._last=wall,mono
        if self._anchor is None:self._anchor=wall,mono
        need(core.finite(request.deadline) and mono<request.deadline,'FINISH_CAPTURE_DEADLINE_EXCEEDED')
        return max(wall,self._anchor[0]+mono-self._anchor[1])

    def __call__(self, request):
        self.check(request)
        terminal_checked(self.binding,request.progress)
        self.check(request)  # Terminal validation time also consumes the deadline.
        if not self.slot.installed and self.capture is not None:
            need(not self.capture_called,'FINISH_CAPTURE_CONSUMED')
            self.capture_called=True  # A failed/late capture is never retried.
            try:
                record=self.capture(request)
            except Exception:
                raise RuntimeSiteError('FINISH_CAPTURE_FAILED') from None
            now=self.check(request)
            need(type(record) is EvidenceAdoption and type(record.facts) is FinishFacts
                 and core.finite(record.facts.observed_at) and record.facts.observed_at<=now,
                 'FINISH_CAPTURE_RESULT_INVALID')
            # Actual progress, then the unchanged source/control authentication.
            self.slot.install(record,request.progress)
        record = self.slot.get(request)  # Missing/partial completion performs no source I/O.
        resolver = port = None
        try:
            resolver = CodeAdoptedEvidenceResolver(role='finish', grant=record.grant,
                facts=record.facts, control_reader=self.readers.control,
                credential=self.credential, progress=self.progress,
                wall=self.wall, monotonic=self.monotonic)
            port = ReviewedEvidenceSource(role='finish', reader=self.readers.original,
                pin=record.grant.original_pin, resolver=resolver, binding=self.binding,
                credential=self.credential, progress=self.progress,
                wall=self.wall, monotonic=self.monotonic)
            return port(request)
        finally:
            # Finish has not released the claim yet. Any local cleanup failure
            # denies completion, while every remaining handle is still closed.
            failed = False
            for handle in (port, resolver):
                if handle is not None:
                    try:
                        handle.close()
                    except BaseException:
                        failed = True
            need(not failed, 'FINISH_ADOPTION_CLEANUP_FAILED')

    def close(self):
        self.credential = self.progress = self.slot = self.readers = self.capture = None


class RuntimeAuthorityFactory:
    """Concrete deployment wiring, instantiated at the REAL progress closure.

    Restore/existing-object admission requires explicit reviewed BaselineAdoption.
    No baseline means first deployment only, with fresh classified absence.
    Readers are borrowed. Caller owns their credentials/lifecycle.
    """
    def __init__(self, plan, readers, finish_slot, *, wall=time.time, monotonic=time.monotonic,
                 finish_capture=None,producer_pins=None,producer_run_reader=None,finish_collection=None):
        need(type(plan) is RuntimeAuthorityPlan and type(plan.approval) is AdoptedGrant
             and type(plan.issuer) is EvidenceAdoption and type(plan.producer) is EvidenceAdoption
             and type(plan.issuer.facts) is IssuerFacts and type(plan.producer.facts) is ProducerFacts
             and type(plan.subject) is ExpectedSubject and type(finish_slot) is FinishAdoptionSlot
             and type(readers) is tuple and len(readers) == (4 if plan.baseline is None else 5)
             and all(type(x) is SourceReaders for x in readers)
             and callable(wall) and callable(monotonic), 'RUNTIME_SITE_PLAN_INVALID')
        need(finish_capture is None or callable(finish_capture),'FINISH_CAPTURE_CONFIGURATION_INVALID')
        need((producer_pins is None)==(producer_run_reader is None),'ACTIONS_CONFIGURATION_INVALID')
        if producer_pins is not None:
            policy_checked(producer_pins)
            need(type(producer_run_reader) in (GitHubOriginalAdapter,PrivateGitHubOriginalAdapter),
                 'ACTIONS_CONFIGURATION_INVALID')
            f=plan.producer.facts
            need(tuple(p.producer_reference for p in producer_pins)==(
                f.test_reference,f.build_reference,f.scan_reference,f.push_reference),
                'ACTIONS_PRODUCER_REFERENCE_MISMATCH')
        binding = plan.approval.binding
        core.binding_checked(binding)
        need(binding.action == 'deploy' or plan.baseline is not None, 'RUNTIME_SITE_FIRST_DEPLOY_ONLY')
        need(plan.issuer.grant.binding == plan.producer.grant.binding == finish_slot.binding == binding,
             'RUNTIME_SITE_BINDING_MISMATCH')
        if finish_collection is not None:
            # Local import avoids a module cycle; config has no live credential
            # or progress until this factory is invoked by the actual session.
            from delivery_finish_collector import FinishCollectorConfig
            need(type(finish_collection) is FinishCollectorConfig and finish_capture is None,
                 'FINISH_COLLECTION_CONFIGURATION_INVALID')
            try:
                finish_collection.check()
            except Exception:
                raise RuntimeSiteError('FINISH_COLLECTION_CONFIGURATION_INVALID') from None
            need(finish_collection.policy.binding == binding and finish_collection.readers == readers[3],
                 'FINISH_COLLECTION_BINDING_MISMATCH')
        need(plan.issuer.grant.principal == plan.subject.username,
             'RUNTIME_SITE_SUBJECT_MISMATCH')
        need(plan.baseline is None or (type(plan.baseline) is BaselineAdoption
             and type(plan.baseline.grant) is AdoptedGrant and plan.baseline.grant.binding == binding),
             'RUNTIME_BASELINE_POLICY_INVALID')
        for r in readers:
            r.check()
        self.plan, self.readers, self.slot = plan, readers, finish_slot
        self.wall, self.monotonic, self.used = wall, monotonic, False
        self.finish_capture,self.producer_pins,self.producer_run_reader=finish_capture,producer_pins,producer_run_reader
        self.finish_collection=finish_collection

    def __call__(self, binding, credential, progress):
        need(not self.used and type(credential) is BearerCredential
             and binding == self.plan.approval.binding and callable(progress),
             'RUNTIME_SITE_FACTORY_CONSUMED')
        self.used = True
        owned, transferred = [], False
        try:
            plan = self.plan
            grant_resolver = CodeAdoptedGrantResolver(grant=plan.approval,
                control_reader=self.readers[0].control, credential=credential,
                progress=progress, wall=self.wall, monotonic=self.monotonic)
            owned.append(grant_resolver)
            approval = ReviewedApprovalAdapter(reader=self.readers[0].original,
                pin=plan.approval.original_pin, resolver=grant_resolver,
                binding=binding, credential=credential, progress=progress,
                wall=self.wall, monotonic=self.monotonic)
            owned.append(approval)
            ports = []
            for index, role, record in ((1, 'platform', plan.issuer), (2, 'producer', plan.producer)):
                resolver = CodeAdoptedEvidenceResolver(role=role, grant=record.grant,
                    facts=record.facts, control_reader=self.readers[index].control,
                    credential=credential, progress=progress, wall=self.wall, monotonic=self.monotonic)
                owned.append(resolver)
                port = ReviewedEvidenceSource(role=role, reader=self.readers[index].original,
                    pin=record.grant.original_pin, resolver=resolver, binding=binding,
                    credential=credential, progress=progress, wall=self.wall, monotonic=self.monotonic)
                owned.append(port); ports.append(port)
            producer=ports[1]
            if self.producer_pins is not None:
                gate=ActionsRunSource(reader=self.producer_run_reader,pins=self.producer_pins,
                    binding=binding,credential=credential,progress=progress,monotonic=self.monotonic)
                owned.append(gate)
                f=plan.producer.facts
                producer=ActionsCheckedProducer(source=producer,gate=gate,references=(
                    f.test_reference,f.build_reference,f.scan_reference,f.push_reference))
                owned.append(producer)
            facts = plan.issuer.facts
            observer = PlatformObservationReader(binding, credential=credential,
                expected_subject=plan.subject, progress=progress, ca_pem=facts.ca_pem,
                endpoint_ip=facts.endpoint_ip, monotonic=self.monotonic)
            owned.append(observer)
            platform = ReadbackCheckedPlatformAdapter(reader=observer, issuer_resolver=ports[0], wall=self.wall)
            owned[-1] = platform  # Wrapper now owns observer; no double ownership.
            object_reader = ObjectReader(binding, credential=credential, ca_pem=facts.ca_pem,
                                         endpoint_ip=facts.endpoint_ip, monotonic=self.monotonic)
            owned.append(object_reader)
            ref, revision, *_ = pin_identity(plan.issuer.grant.original_pin)
            baseline = FirstDeploymentBaseline(object_reader, progress=progress,
                provenance=Provenance(ref, revision, plan.issuer.grant.principal,
                                      plan.issuer.grant.adoption_reference))
            if plan.baseline is not None:
                grant=plan.baseline.grant
                baseline_resolver=CodeAdoptedGrantResolver(grant=grant,control_reader=self.readers[4].control,
                    credential=credential,progress=progress,wall=self.wall,monotonic=self.monotonic)
                owned.append(baseline_resolver)
                baseline_source=ReviewedApprovalAdapter(reader=self.readers[4].original,pin=grant.original_pin,
                    resolver=baseline_resolver,binding=binding,credential=credential,progress=progress,
                    wall=self.wall,monotonic=self.monotonic)
                owned.append(baseline_source)
                baseline=CodeAdoptedBaseline(plan.baseline,baseline_source,object_reader,progress)
                owned.append(baseline)
            capture=self.finish_capture
            if self.finish_collection is not None:
                capture=self.finish_collection.bind(binding,credential,progress,
                    wall=self.wall,monotonic=self.monotonic)
                owned.append(capture)
            finish = DeferredFinish(binding, credential, progress, self.slot,
                                     self.readers[3], self.wall, self.monotonic,capture)
            owned.append(finish)
            bundle = AuthorityBundle(AuthorityAdapters(approval, platform, producer, baseline, finish), tuple(owned))
            transferred = True
            return bundle
        except Exception:
            raise RuntimeSiteError('RUNTIME_SITE_COMPOSITION_FAILED') from None
        finally:
            if not transferred:
                for handle in reversed(owned):
                    try:
                        handle.close()
                    except BaseException:
                        pass
            # Do not retain site plans/readers after transferring ownership.
            self.plan = self.readers = self.slot = None
            self.finish_capture=self.producer_pins=self.producer_run_reader=None
            self.finish_collection=None


def main(argv=None, *, config=None, plan=None, readers=None, finish_slot=None,
         receiver=None, handoff_deadline=None,finish_capture=None,
         producer_pins=None,producer_run_reader=None,finish_collection=None):
    parser = cli.SafeParser(description='Reviewed-code runtime site entry point; no default target.')
    parser.add_argument('command', choices=('status', 'preflight', 'deploy', 'restore'))
    # Ownership transfers only for an exact receiver, never an arbitrary close callback.
    owned = receiver if type(receiver) is PipeCredentialReceiver else None
    credential = None
    try:
        args = parser.parse_args(argv)
        if config is None:
            return site.blocked('RUNTIME_SITE_UNCONFIGURED')
        site.configuration_checked(config)
        factory = RuntimeAuthorityFactory(plan, readers, finish_slot,finish_capture=finish_capture,
            producer_pins=producer_pins,producer_run_reader=producer_run_reader,finish_collection=finish_collection)
        need(config.binding == plan.approval.binding, 'RUNTIME_SITE_BINDING_MISMATCH')
        if args.command == 'status':
            # No file reads, authority calls, credential receipt, sockets or claim.
            import json
            print(json.dumps({'status': 'CONFIGURED_NOT_VERIFIED', 'execution_authorized': False,
                              'runtime': 'NOT_RUN'}, sort_keys=True))
            return 0
        need(args.command in ('preflight', config.binding.action), 'RUNTIME_SITE_ACTION_MISMATCH')
        need(owned is not None and core.finite(handoff_deadline), 'RUNTIME_SITE_CHANNEL_REQUIRED')
        credential = owned.receive(handoff_deadline)
        app = site.build_application(config, credential=credential, authority_factory=factory)
        return cli.main([args.command], application=app)
    except (RuntimeSiteError, site.SiteError) as error:
        return site.blocked(error.args[0])
    except Exception:
        return site.blocked('RUNTIME_SITE_PREPARATION_FAILED')
    finally:
        credential = None  # Reference release only, not secure memory erasure.
        if owned is not None:
            owned.close()


if __name__ == '__main__':
    raise SystemExit(main())
