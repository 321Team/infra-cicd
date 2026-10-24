"""SYNTHETIC native originals; sockets/DNS/issuance are forbidden throughout."""
import copy
from dataclasses import replace
import pickle
from types import MappingProxyType
import unittest
from unittest.mock import Mock, patch

import delivery_adapter as core
import delivery_job_site as declaration
import delivery_runtime_site as runtime
import delivery_semantic_authority as semantic
import delivery_token_materializer as materializer
import delivery_token_request as issuance
from delivery_object_reader import BearerCredential
from delivery_platform_readback import ExpectedSubject
from delivery_source_session import PrivateSourceSession
from delivery_evidence_source import ReviewedEvidenceSource
from test_delivery_adapter import Harness
from test_delivery_job import CONTEXT
from test_delivery_semantic_authority import SemanticFixture
import test_delivery_token_request as token_fixture


class NativeMaterializerTests(SemanticFixture, unittest.TestCase):
    def setUp(self):
        self.tokens = token_fixture.TokenRequestTests('runTest'); self.tokens.setUp()
        self.addCleanup(self.tokens.doCleanups)
        self.context(Harness(), BearerCredential(self.tokens.token()))
        self.selected = materializer.NativePlanSelection(self.grants['approval'],
            replace(self.grants['platform'], principal=self.tokens.claims['sub']),
            runtime.EvidenceAdoption(self.grants['producer'], self.producer), None,
            ExpectedSubject(self.tokens.claims['sub'], self.tokens.target.uid,
                            ('system:authenticated',)), self.tokens.target,
            self.issuer.ca_pem, self.issuer.endpoint_ip,
            self.issuer.rbac_reference, self.issuer.route_reference)
        # No real private receiver/FD: an already consumed source-only fixture.
        self.source = object.__new__(PrivateSourceSession)
        self.source._state, self.source._last = 'CONSUMED', None
        self.source.clock = lambda: self.h.mono
        self.source.deadline = self.h.mono + 10
        self.roles = MappingProxyType({})
        self.read = Mock(return_value=core.encoded(self.tokens.response()))
        self.wall = Mock(return_value=self.tokens.wall)
        p = patch.object(issuance.TokenRequestIssuer, 'issue',
                         side_effect=AssertionError('ISSUANCE_FORBIDDEN'))
        p.start(); self.addCleanup(p.stop)

    def build(self, **changes):
        args = dict(binding=self.h.binding, context=CONTEXT, selection=self.selected,
                    read_original=self.read, wall=self.wall)
        args.update(changes)
        value = materializer.NativeTokenMaterializer(**args)
        self.addCleanup(value.close)
        return value

    def call(self, value):
        return value(CONTEXT, self.credential, self.source, self.roles, self.source.deadline)

    def test_native_parser_populates_only_actual_receipt_facts_without_grant_changes(self):
        value = self.build()
        self.read.assert_not_called(); self.wall.assert_not_called()
        plan = self.call(value)
        self.assertIs(plan.approval, self.selected.approval)
        self.assertIs(plan.issuer.grant, self.selected.issuer_grant)
        self.assertIs(plan.producer, self.selected.producer)
        self.assertIs(plan.subject, self.selected.subject)
        self.assertIsNone(plan.baseline)
        f = plan.issuer.facts
        self.assertEqual((self.credential.fingerprint,1000,1900,900),
                         (f.credential_fingerprint,f.issued_at,f.expires_at,f.requested_seconds))
        self.assertEqual((self.tokens.target.issuer,self.tokens.target.audience),
                         (f.issuer,f.audience))
        self.read.assert_called_once_with(CONTEXT,self.credential,self.source,
                                         self.roles,self.source.deadline)
        self.assertEqual([],self.read_calls)  # No original/control authentication claimed.
        self.assertIsNone(declaration.SITE)

    def test_existing_prepared_plan_calls_materializer_and_retains_borrowed_inputs(self):
        value = self.build()
        prepared = runtime.PreparedAuthorityPlan(binding=self.h.binding, materialize=value)
        self.addCleanup(prepared.close)
        prepared.bind_context(CONTEXT)
        plan = prepared.resolve(credential=self.credential,source=self.source,
                                roles={},deadline=self.source.deadline)
        self.assertEqual(self.credential.fingerprint,plan.issuer.facts.credential_fingerprint)
        self.assertEqual('CONSUMED',self.source._state)
        self.assertIsNotNone(self.credential.token)

    def test_materialized_facts_still_require_exact_original_and_fresh_control(self):
        plan=self.call(self.build())
        resolver=semantic.CodeAdoptedEvidenceResolver(role='platform',grant=plan.issuer.grant,
            facts=plan.issuer.facts,control_reader=self.reader,credential=self.credential,
            progress=lambda:self.progress,wall=lambda:self.tokens.wall,monotonic=lambda:self.h.mono)
        self.addCleanup(resolver.close)
        port=ReviewedEvidenceSource(role='platform',reader=self.reader,pin=self.pins['platform'],
            resolver=resolver,binding=self.h.binding,credential=self.credential,
            progress=lambda:self.progress,wall=lambda:self.tokens.wall,monotonic=lambda:self.h.mono)
        self.addCleanup(port.close)
        value=port(self.request())
        self.assertEqual(self.credential.fingerprint,value.credential_fingerprint)
        self.assertEqual(['platform','control','platform','control'],
                         [pin.path for _,pin in self.read_calls])
        self.raws['control']=b'SYNTHETIC_WITHDRAWAL'
        with self.assertRaises(Exception): port(self.request())

    def test_mutated_native_target_uid_audience_expiry_and_token_fail_without_fallback(self):
        for change in ('uid','audience','expiry','token','duplicate','oversize','missing'):
            obj = self.tokens.response()
            if change == 'uid': obj['metadata']['name'] = 'foreign'
            elif change == 'audience': obj['spec']['audiences'] = ['https://other.invalid']
            elif change == 'expiry': obj['status']['expirationTimestamp'] = '1970-01-01T00:15:00Z'
            elif change == 'token':
                self.tokens.claims['kubernetes.io']['serviceaccount']['uid'] = 'foreign'
                obj['status']['token'] = self.tokens.token()
                self.tokens.claims['kubernetes.io']['serviceaccount']['uid'] = self.tokens.target.uid
            raw = core.encoded(obj)
            if change == 'duplicate': raw = b'{"status":{},"status":{}}'
            if change == 'oversize': raw = b'x' * (1024 * 1024 + 1)
            if change == 'missing': raw = None
            self.read.return_value = raw
            with self.subTest(change=change), self.assertRaises(materializer.MaterializationError):
                self.call(self.build())

    def test_well_formed_original_for_other_credential_does_not_set_current_facts(self):
        value = self.build()
        other = BearerCredential('SYNTHETIC_OTHER_TOKEN')
        with self.assertRaises(materializer.MaterializationError):
            value(CONTEXT,other,self.source,self.roles,self.source.deadline)
        with self.assertRaises(materializer.MaterializationError): self.call(value)
        self.read.assert_called_once()

    def test_invalid_grant_pin_binding_and_unselected_placeholders_fail_before_reader(self):
        bad = (
            replace(self.selected, approval=None),
            replace(self.selected, issuer_grant=replace(self.selected.issuer_grant,
                control_pin=None)),
            replace(self.selected, issuer_grant=replace(self.selected.issuer_grant,
                original_pin=replace(self.selected.issuer_grant.original_pin,sha256='0'*64))),
            replace(self.selected, producer=replace(self.selected.producer,
                grant=replace(self.selected.producer.grant,binding=replace(self.h.binding,run_id='other')))),
            replace(self.selected, route_reference='PENDING'),
            replace(self.selected, endpoint_ip='127.0.0.1'),
            replace(self.selected, subject=replace(self.selected.subject,groups=['system:authenticated'])),
            replace(self.selected, producer=replace(self.selected.producer,
                facts=replace(self.producer, source_commit='0'*40))),
            replace(self.selected, subject=replace(self.selected.subject,uid='foreign')))
        for selection in bad:
            with self.subTest(selection=repr(selection)), self.assertRaises(materializer.MaterializationError):
                self.build(selection=selection)
        self.read.assert_not_called(); self.wall.assert_not_called()

    def test_foreign_native_context_fails_before_original_reader_and_is_consumed(self):
        value=self.build()
        with self.assertRaises(materializer.MaterializationError):
            value(replace(CONTEXT,run_id='987654'),self.credential,self.source,
                  self.roles,self.source.deadline)
        with self.assertRaises(materializer.MaterializationError): self.call(value)
        self.read.assert_not_called(); self.wall.assert_not_called()

    def test_each_pre_read_state_deadline_and_expiry_condition_stops_before_reader(self):
        end=self.source.deadline
        for case in ('state','deadline','expired'):
            self.source._state='CONSUMED';self.source._last=None;self.h.mono=end-10
            call_end=end
            if case=='state':self.source._state='PREPARED'
            if case=='deadline':call_end=end+1
            if case=='expired':self.h.mono=end
            self.read.reset_mock();self.wall.reset_mock()
            with self.subTest(case=case),self.assertRaises(materializer.MaterializationError):
                self.build()(CONTEXT,self.credential,self.source,self.roles,call_end)
            self.read.assert_not_called();self.wall.assert_not_called()

    def test_requested_900_and_actual_shorter_lifetime_are_distinct_from_invalid_901(self):
        for seconds in (600,901):
            response=self.tokens.response();response['spec']['expirationSeconds']=seconds
            response['status']['expirationTimestamp']=token_fixture.stamp(1000+seconds)
            self.read.return_value=core.encoded(response)
            if seconds==901:
                with self.assertRaises(materializer.MaterializationError):self.call(self.build())
            else:
                plan=self.call(self.build())
                self.assertEqual((900,1000,1600),(plan.issuer.facts.requested_seconds,
                                 plan.issuer.facts.issued_at,plan.issuer.facts.expires_at))

    def test_reader_failure_and_interrupt_have_no_private_exception_chain_or_retry(self):
        for error in (RuntimeError('PRIVATE_TOKEN_OR_BODY'),KeyboardInterrupt('PRIVATE'),SystemExit('PRIVATE')):
            self.read.reset_mock(); self.read.side_effect=error
            value=self.build()
            try: self.call(value)
            except BaseException as exc:
                self.assertIsNone(exc.__context__)
                self.assertNotIn('PRIVATE',str(exc))
                frame=exc.__traceback__
                while frame and frame.tb_frame.f_code.co_name!='__call__': frame=frame.tb_next
                self.assertIsNotNone(frame)
                for name in ('raw','receipt','read','selected','credential','source','roles'):
                    self.assertIsNone(frame.tb_frame.f_locals[name],name)
            else: self.fail('error must propagate')
            with self.assertRaises(materializer.MaterializationError): self.call(value)
            self.read.assert_called_once()

    def test_late_read_does_not_renew_deadline_or_produce_plan(self):
        def read(*args):
            self.h.mono=self.source.deadline
            return core.encoded(self.tokens.response())
        self.read.side_effect=read
        with self.assertRaises(materializer.MaterializationError): self.call(self.build())
        self.wall.assert_not_called()

    def test_close_during_read_and_reentrant_call_consume_materialization(self):
        value=self.build()
        def read(*args):
            with self.assertRaises(materializer.MaterializationError): self.call(value)
            value.close()
            return core.encoded(self.tokens.response())
        self.read.side_effect=read
        with self.assertRaises(materializer.MaterializationError): self.call(value)
        self.read.assert_called_once()

    def test_inert_constructor_no_copy_pickle_or_secret_repr(self):
        value=self.build()
        for operation in (copy.copy,copy.deepcopy,pickle.dumps):
            with self.assertRaises(Exception): operation(value)
        self.assertNotIn(self.credential.token,repr(value))
        self.assertNotIn(self.credential.token,repr(self.selected))
        self.read.assert_not_called(); self.wall.assert_not_called()


if __name__ == '__main__': unittest.main()
