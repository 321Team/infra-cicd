"""Observed public pins + explicitly SYNTHETIC per-run facts; offline only.

Synthetic grants/groups/endpoints/receipts are NOT operational originals or GO.
No actual credential, TokenRequest, runtime access or input synthesis is done.
"""
from dataclasses import replace
import hashlib
import importlib
from pathlib import Path
import socket
import ssl
import unittest
from unittest.mock import Mock, patch

import delivery_adapter as core
import delivery_crosshost_ingress as ingress
import delivery_fds_site_inputs as fds
import delivery_job_parent as parent
import delivery_job_site as declaration
import delivery_runtime_site as runtime
import delivery_site as site
from delivery_adopted_authority import AdoptedGrant
from delivery_github_source import RepositoryPin, BlobPin
from delivery_platform_readback import ExpectedSubject
from delivery_semantic_authority import IssuerFacts, ProducerFacts
from producer_bridge import CHECKER_SHA256
from test_delivery_job import CONTEXT


class ObservedSiteTests(unittest.TestCase):
    def setUp(self):
        for name in ('socket', 'getaddrinfo', 'create_connection'):
            block = patch.object(socket, name, side_effect=AssertionError('NETWORK_FORBIDDEN'))
            block.start(); self.addCleanup(block.stop)
        # Cross-platform absolute paths; existence and safety stay with the
        # existing POSIX loader. No directory or source checkout is created.
        root = Path.cwd().anchor
        self.args = dict(run_id='SYNTHETIC_REVIEW_ONLY', platform_receipt_sha256='a'*64,
            supply_receipt_sha256='b'*64, input_root=Path(root)/'synthetic-inputs',
            rendered_name='synthetic-app.yaml', contract_name='synthetic-contract.yaml',
            source_root=Path(root)/'synthetic-source', store_root=Path(root)/'synthetic-store',
            budget_seconds=30, finalize=False)
        self.config = fds.build_configuration(**self.args)
        b = self.config.binding
        repo = RepositoryPin('synthetic-owner', 'synthetic-repo', 123, 321)
        control = BlobPin(repo, 'c'*40, 'control', 'd'*64, 'heads/synthetic', 'c'*40)
        def grant(role, sha, principal):
            pin = replace(control, path=role, sha256=sha)
            return AdoptedGrant(b, pin, control, principal, 'synthetic:adoption', 1000, 1900)
        self.issuer = IssuerFacts('e'*64, fds.ISSUER, fds.AUDIENCE, 900, 1000, 1900,
            fds.CA_PEM, '192.0.2.10', 'synthetic:rbac', 'synthetic:route')
        self.producer = ProducerFacts(fds.IMAGES, None, fds.SOURCE_COMMIT,
            fds.CONTRACT_SHA256, fds.RENDERED_SHA256, CHECKER_SHA256,
            *('synthetic:'+x for x in ('test', 'build', 'scan', 'push', 'render', 'adoption')))
        self.plan = runtime.RuntimeAuthorityPlan(grant('approval', 'f'*64, 'synthetic:approver'),
            runtime.EvidenceAdoption(grant('platform', 'a'*64, fds.SUBJECT), self.issuer),
            runtime.EvidenceAdoption(grant('producer', 'b'*64, 'synthetic:producer'), self.producer),
            ExpectedSubject(fds.SUBJECT, fds.SUBJECT_UID, ('synthetic:observed-group',)))
        self.callbacks = [Mock() for _ in range(4)]
        self.parent_args = dict(config=self.config, plan=self.plan,
            finish_slot=runtime.FinishAdoptionSlot(b),
            expected_peer=ingress.PeerIdentity('synthetic:peer', 'synthetic:authenticated-transport'),
            routes=(ingress.SourceRoute(repo, 1050, fds.CA_PEM, '192.0.2.11'),), deadline=1005,
            wire=self.callbacks[0], publish_ready=self.callbacks[1],
            open_channel=self.callbacks[2], monotonic=self.callbacks[3])

    def test_explicit_values_connect_to_existing_configuration_without_io(self):
        with patch.object(Path, 'open', side_effect=AssertionError('FILE_IO_FORBIDDEN')):
            config = fds.build_configuration(**self.args)
            self.assertIs(self.plan, fds.validate_plan(config, self.plan))
            builder = fds.connect_parent(**self.parent_args)
        self.assertIs(type(config), site.SiteConfiguration)
        self.assertIs(type(builder), parent.ParentJobBuilder)
        self.assertEqual(fds.CLUSTER_UUID, config.binding.cluster_id)
        self.assertEqual(6, len(core.objects_for(config.binding)))
        self.assertIsNone(declaration.SITE)
        for callback in self.callbacks: callback.assert_not_called()

    def test_import_is_inert_and_explicit_ca_is_exact_public_root(self):
        with patch.object(Path, 'open', side_effect=AssertionError('FILE_IO_FORBIDDEN')):
            importlib.reload(fds)
        self.assertEqual(fds.CA_PEM_SHA256, hashlib.sha256(fds.CA_PEM).hexdigest())
        der = ssl.PEM_cert_to_DER_cert(fds.CA_PEM.decode('ascii'))
        self.assertEqual(fds.CA_DER_SHA256, hashlib.sha256(der).hexdigest())
        self.assertFalse(hasattr(fds, 'ENDPOINT_IP'))
        self.assertIsNone(declaration.SITE)

    def test_missing_or_invalid_dynamic_inputs_do_not_become_defaults(self):
        for field in self.args:
            values = self.args.copy(); values.pop(field)
            with self.subTest(field=field), self.assertRaises(TypeError):
                fds.build_configuration(**values)
        for change in ({'run_id':''}, {'platform_receipt_sha256':'PENDING'},
                       {'supply_receipt_sha256':'B'*64}, {'finalize':'yes'},
                       {'budget_seconds':float('nan')}, {'input_root':self.args['store_root']}):
            with self.subTest(change=change), self.assertRaises(site.SiteError):
                fds.build_configuration(**(self.args | change))

    def test_ocm_id_namespace_source_ca_route_and_render_drift_stop_before_parent(self):
        changes = ({'cluster_id':'2th4q1q4qf7ra35862s1u9q7f6n0vnmc'},
            {'namespace_uid':'other-namespace'}, {'source_commit':'1'*40},
            {'ca_sha256':'2'*64}, {'route_host':'unapproved.example.com'},
            {'rendered_sha256':'3'*64}, {'contract_sha256':'4'*64},
            {'api_origin':'https://unapproved.example.com:443'}, {'action':'restore'},
            {'images':tuple(reversed(fds.IMAGES))})
        with patch.object(parent, 'ParentJobBuilder') as build:
            for change in changes:
                config = replace(self.config, binding=replace(self.config.binding, **change))
                with self.subTest(change=change), self.assertRaises((fds.FdsSiteInputError, site.SiteError)):
                    fds.connect_parent(**(self.parent_args | {'config':config}))
            build.assert_not_called()

    def test_subject_issuer_audience_ca_and_producer_mismatch_rejected(self):
        bad = []
        for field, value in (('issuer','synthetic:wrong'), ('audience','synthetic:wrong'),
                              ('ca_pem', fds.CA_PEM+b'\n')):
            bad.append(replace(self.plan, issuer=replace(self.plan.issuer,
                facts=replace(self.issuer, **{field:value}))))
        for change in ({'uid':'other-uid'}, {'username':'hayeoung'}):
            bad.append(replace(self.plan, subject=replace(self.plan.subject, **change)))
        for field, value in (('source_commit','1'*40), ('contract_sha256','2'*64),
                             ('rendered_sha256','3'*64), ('images',fds.IMAGES[:-1])):
            bad.append(replace(self.plan, producer=replace(self.plan.producer,
                facts=replace(self.producer, **{field:value}))))
        for plan in bad:
            with self.subTest(plan=plan), self.assertRaisesRegex(fds.FdsSiteInputError, '^FDS_SITE_INPUT_MISMATCH$'):
                fds.validate_plan(self.config, plan)

    def test_unknown_facts_preserved_not_reissued_or_inferred(self):
        plan = replace(self.plan, issuer=replace(self.plan.issuer, facts=replace(self.issuer,
            endpoint_ip='192.0.2.99', credential_fingerprint='9'*64, issued_at=2000, expires_at=2500)),
            subject=replace(self.plan.subject, groups=('synthetic:different-actual-group',)))
        self.assertIs(plan, fds.validate_plan(self.config, plan))
        self.assertEqual(('synthetic:different-actual-group',), plan.subject.groups)
        self.assertEqual((2000, 2500), (plan.issuer.facts.issued_at, plan.issuer.facts.expires_at))
        # Acceptance here is a fixed-profile comparison, NOT token/current-RBAC
        # authentication. Existing runtime authenticators retain that decision.

    def test_manual_c_existing_baseline_or_invented_previous_not_auto_adopted(self):
        for plan in (replace(self.plan, baseline=runtime.BaselineAdoption(self.plan.approval, ())),
                     replace(self.plan, producer=replace(self.plan.producer,
                         facts=replace(self.producer, previous_images=fds.IMAGES)))):
            with self.assertRaises(fds.FdsSiteInputError):
                fds.connect_parent(**(self.parent_args | {'plan':plan}))
        for callback in self.callbacks: callback.assert_not_called()

    def test_plan_grant_slot_and_transport_missing_rejected_without_callbacks(self):
        for change in ({'plan':None}, {'plan':replace(self.plan, issuer=None)},
            {'plan':replace(self.plan, approval=replace(self.plan.approval,
                binding=replace(self.config.binding, run_id='other')))},
            {'finish_slot':runtime.FinishAdoptionSlot(replace(self.config.binding, run_id='other'))},
            {'expected_peer':None}, {'routes':()}, {'deadline':float('inf')},
            {'wire':None}, {'finish_capture':True}):
            with self.subTest(fields=tuple(change)), self.assertRaisesRegex(fds.FdsSiteInputError, '^FDS_SITE_INPUT_MISMATCH$'):
                fds.connect_parent(**(self.parent_args | change))
        for callback in self.callbacks: callback.assert_not_called()

    def test_parent_still_requires_native_context_before_publishing_or_receiving(self):
        builder = fds.connect_parent(**self.parent_args)
        with self.assertRaisesRegex(parent.ParentBuildError, '^P2_PARENT_BUILD_FAILED$'):
            builder(replace(CONTEXT, event='pull_request'), 'deploy')
        for callback in self.callbacks: callback.assert_not_called()


if __name__ == '__main__': unittest.main()
