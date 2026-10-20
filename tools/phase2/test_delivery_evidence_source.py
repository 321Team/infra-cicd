"""Offline source/resolver/session doubles only. No site facts or Runtime proof."""
from dataclasses import replace
import json
import socket
import ssl
import unittest
from unittest.mock import Mock, patch

import delivery_adapter as core
import delivery_evidence_source as port
import delivery_github_source as source
import delivery_session as session
from delivery_provider import AuthorityAdapters, OperationalProviders
from delivery_object_reader import BearerCredential
from delivery_verifier import (PlatformEvidence, ProducerEvidence, CompletionEvidence,
    Progress, Provenance, ReadRequest, UnconfiguredProviders)
from producer_bridge import CHECKER_SHA256
from test_delivery_adapter import Harness, SENSITIVE, validator
from test_delivery_operational import ContractHarness, PLATFORM_RAW, SUPPLY_RAW
from test_delivery_transport import CA


class EvidenceSourceTests(unittest.TestCase):
    def setUp(self):
        for name in ('socket', 'getaddrinfo', 'create_connection'):
            p = patch.object(socket, name, side_effect=AssertionError('NETWORK_FORBIDDEN'))
            p.start(); self.addCleanup(p.stop)
        p = patch.object(source.ssl, 'SSLContext', return_value=Mock(
            verify_mode=ssl.CERT_REQUIRED, check_hostname=True, keylog_filename=None))
        p.start(); self.addCleanup(p.stop)
        self.h = Harness()
        self.h.rebind(ca_sha256=core.digest(CA),
            platform_receipt_sha256=core.digest(PLATFORM_RAW),
            supply_receipt_sha256=core.digest(SUPPLY_RAW))
        self.credential = BearerCredential(SENSITIVE)
        self.progress = Progress()
        self.calls = []
        self.saved = []
        self.hook = lambda r, o, v: v
        self.reader = source.GitHubOriginalAdapter(source.PublicGitHubTLS(
            ca_pem=CA, endpoint_ip='192.0.2.10', monotonic=lambda: self.h.mono))
        p = patch.object(self.reader, 'blob', side_effect=self.original)
        self.read = p.start(); self.addCleanup(p.stop)
        self.repo = source.RepositoryPin('synthetic-owner', 'synthetic-repo', 123, 321)
        self.raw = {'platform': PLATFORM_RAW, 'producer': SUPPLY_RAW,
                    'finish': b'SYNTHETIC_EXCLUSIVE_END_ORIGINAL_NOT_REAL'}
        self.pins = {role: source.BlobPin(self.repo, 'a' * 40, role + '.txt',
            core.digest(raw), 'heads/synthetic', 'a' * 40) for role, raw in self.raw.items()}

    def original(self, r, pin):
        reference, revision, repo, creator, _ = port.pin_identity(pin)
        return source.SourceOriginal(r, reference, revision, repo, creator,
            self.raw[pin.path.split('.')[0]], (b'SYNTHETIC_TLS_RESPONSE',))

    def request(self):
        return ReadRequest(self.h.binding, self.credential, self.progress, self.h.mono + 40)

    def resolve(self, role, r, o):
        self.calls.append((role, r, o))
        p = Provenance(o.reference, o.revision, 'synthetic-authority', 'synthetic:adoption-v1')
        if role == 'platform':
            value = PlatformEvidence(r, self.h.binding, p, self.credential,
                self.credential.fingerprint, 'synthetic:issuer', 'synthetic:audience',
                900, 1900, 1000, CA, '192.0.2.10', o.body)
        elif role == 'producer':
            b = self.h.binding
            value = ProducerEvidence(r, b, p, b.source_commit, b.contract_sha256,
                b.rendered_sha256, CHECKER_SHA256, o.body)
        else:
            value = CompletionEvidence(r, p, r.progress.key, 'EXCLUSIVE_ACCESS_ENDED')
        return port.OriginalDecision(o, self.hook(r, o, value))

    def adapter(self, role, progress=None):
        return port.ReviewedEvidenceSource(role=role, reader=self.reader,
            pin=self.pins[role], resolver=lambda r, o: self.resolve(role, r, o),
            binding=self.h.binding, credential=self.credential,
            progress=progress or (lambda: self.progress),
            wall=lambda: self.h.now, monotonic=lambda: self.h.mono)

    def terminal(self):
        receipt = self.h.run()
        self.assertEqual('ROLLOUT_OBSERVED', receipt['status'])
        self.progress = Progress(core.encoded(receipt), tuple(core.encoded(
            self.h.objects[core.path_for(key)]) for key in core.OBJECTS))

    def test_platform_and_producer_each_read_twice_and_return_original_evidence(self):
        for role in ('platform', 'producer'):
            with self.subTest(role=role):
                self.read.reset_mock(); self.calls.clear()
                r = self.request(); value = self.adapter(role)(r)
                self.assertIs(value.request, r)
                self.assertEqual(value.raw_receipt, self.raw[role])
                self.assertEqual(2, self.read.call_count)
                self.assertEqual(2, len(self.calls))
                self.assertIsNot(self.calls[0][2], self.calls[1][2])
                self.assertNotIn('SYNTHETIC_TLS_RESPONSE', repr(value))

    def test_unconfigured_and_declaration_are_not_authenticators(self):
        with self.assertRaises(port.EvidenceSourceError):
            port.ReviewedEvidenceSource(role='platform', reader=self.reader,
                pin=self.pins['platform'], resolver=None, binding=self.h.binding,
                credential=self.credential, progress=lambda: self.progress)
        adapter = self.adapter('platform'); adapter.resolver = lambda r, o: {'active': True}
        with self.assertRaisesRegex(port.EvidenceSourceError, 'DECISION_INVALID'):
            adapter(self.request())
        with self.assertRaises(Exception): UnconfiguredProviders().platform(self.request())
        self.read.assert_called_once()

    def test_source_drift_bad_identity_and_false_authority_flags_are_rejected(self):
        r = self.request(); first = self.original(r, self.pins['producer'])
        variants = (replace(first, body=b'EDITED'), replace(first, revision='changed'),
            replace(first, repository_id=True), replace(first, comment_creator_id=9),
            replace(first, request=replace(r)), replace(first, body=b''))
        for other in variants:
            self.read.side_effect = [first, other]
            with self.subTest(other=other.revision), self.assertRaises(port.EvidenceSourceError):
                self.adapter('producer')(r)
        for flag in ('execution_authorized', 'body_authorship_verified'):
            other = replace(first); object.__setattr__(other, flag, True)
            self.read.side_effect = [other]
            with self.subTest(flag=flag), self.assertRaises(port.EvidenceSourceError):
                self.adapter('producer')(r)

    def test_cached_source_or_decision_object_rejected(self):
        r = self.request(); first = self.original(r, self.pins['producer'])
        self.read.side_effect = [first, first]
        with self.assertRaisesRegex(port.EvidenceSourceError, 'NOT_FRESH'):
            self.adapter('producer')(r)
        self.read.side_effect = self.original
        a = self.adapter('producer'); saved = []
        def stale(r, o):
            saved.append(self.resolve('producer', r, o)); return saved[0]
        a.resolver = stale
        with self.assertRaisesRegex(port.EvidenceSourceError, 'DECISION_INVALID'): a(r)

    def test_wrong_request_binding_handle_progress_denied_before_read(self):
        r = self.request()
        for bad in (replace(r, binding=replace(r.binding, run_id='synthetic-other')),
                    replace(r, credential=BearerCredential(SENSITIVE)),
                    replace(r, progress=Progress(responses=(b'x',))),
                    replace(r, deadline=self.h.mono)):
            self.read.reset_mock()
            with self.subTest(bad=bad.deadline), self.assertRaises(port.EvidenceSourceError):
                self.adapter('producer')(bad)
            self.read.assert_not_called()

    def test_wrong_evidence_request_binding_provenance_and_port_type_denied(self):
        changes = (lambda r, o, v: replace(v, request=replace(r)),
            lambda r, o, v: replace(v, binding=replace(v.binding, run_id='synthetic-other')),
            lambda r, o, v: replace(v, provenance=replace(v.provenance, revision='wrong')),
            lambda r, o, v: CompletionEvidence(r, v.provenance, r.progress.key, 'EXCLUSIVE_ACCESS_ENDED'))
        for change in changes:
            self.hook = change
            with self.assertRaises(port.EvidenceSourceError): self.adapter('producer')(self.request())

    def test_issuer_handle_fingerprint_ca_ip_and_receipt_mismatch_denied(self):
        changes = {'credential': BearerCredential(SENSITIVE), 'credential_fingerprint': 'f' * 64,
                   'ca_pem': b'other', 'endpoint_ip': '127.0.0.1', 'issuer': 'PENDING',
                   'audience': 'UNKNOWN', 'raw_receipt': b'other', 'requested_seconds': True}
        for key, value in changes.items():
            self.hook = lambda r, o, v: replace(v, **{key: value})
            with self.subTest(key=key), self.assertRaises(port.EvidenceSourceError):
                self.adapter('platform')(self.request())

    def test_requested_ttl_is_not_actual_issuer_lifetime(self):
        for changes in ({'issued_at': 999}, {'issued_at': 1001}, {'expires_at': 1000},
                        {'expires_at': float('inf')}, {'expires_at': 1901}):
            self.hook = lambda r, o, v: replace(v, **changes)
            with self.subTest(changes=changes), self.assertRaises(port.EvidenceSourceError):
                self.adapter('platform')(self.request())

    def test_binding_receipt_digest_does_not_substitute_for_original_bytes(self):
        for role, receipt in (('platform', PLATFORM_RAW), ('producer', SUPPLY_RAW)):
            self.raw[role] = b'SYNTHETIC_DIFFERENT_ORIGINAL'
            self.pins[role] = replace(self.pins[role], sha256=core.digest(self.raw[role]))
            self.hook = lambda r, o, v: replace(v, raw_receipt=receipt)
            with self.subTest(role=role), self.assertRaisesRegex(port.EvidenceSourceError, 'RECEIPT_MISMATCH'):
                self.adapter(role)(self.request())

    def test_producer_commit_contract_render_checker_and_raw_mismatch(self):
        for key in ('source_commit', 'contract_sha256', 'rendered_sha256', 'checker_sha256', 'raw_receipt'):
            self.hook = lambda r, o, v: replace(v, **{key: b'other' if key == 'raw_receipt' else 'f' * 64})
            with self.subTest(key=key), self.assertRaises(port.EvidenceSourceError):
                self.adapter('producer')(self.request())

    def test_resolver_replacement_revocation_or_auth_revision_drift_denied(self):
        for change in ('revision', 'denial'):
            a = self.adapter('producer'); calls = []
            def resolve(r, o):
                calls.append(1); value = self.resolve('producer', r, o)
                if len(calls) == 2:
                    if change == 'denial': raise ValueError(SENSITIVE)
                    return replace(value, evidence=replace(value.evidence, provenance=replace(
                        value.evidence.provenance, authority_reference='synthetic:withdrawn')))
                return value
            a.resolver = resolve
            with self.subTest(change=change), self.assertRaises(port.EvidenceSourceError): a(self.request())

    def test_deadline_progress_mutation_and_expiry_after_resolver_deny(self):
        for change in ('deadline', 'progress', 'expiry'):
            self.progress = Progress(); self.h.now = self.h.mono = 1000
            a = self.adapter('platform'); r = self.request()
            def resolve(r, o):
                value = self.resolve('platform', r, o)
                if change == 'deadline': self.h.mono = r.deadline
                elif change == 'progress': self.progress = Progress(responses=(b'changed',))
                else: self.h.now = 1900
                return value
            a.resolver = resolve
            with self.subTest(change=change), self.assertRaises(port.EvidenceSourceError): a(r)

    def test_stalled_wall_and_reversed_clock_do_not_extend_issuer_lifetime(self):
        a = self.adapter('platform'); r = replace(self.request(), deadline=3000)
        def slow(r, o):
            value = self.resolve('platform', r, o); self.h.mono = 1901; return value
        a.resolver = slow
        with self.assertRaisesRegex(port.EvidenceSourceError, 'LIFETIME_INVALID'): a(r)
        self.h.now = self.h.mono = 1000; a = self.adapter('producer'); a(self.request())
        self.h.now = 999
        with self.assertRaisesRegex(port.EvidenceSourceError, 'CLOCK_INVALID'): a(self.request())

    def test_finish_terminal_progress_and_separate_exclusive_end_required(self):
        self.terminal(); r = self.request(); a = self.adapter('finish')
        value = a(r)
        self.assertEqual(r.progress.key, value.progress_key)
        self.assertEqual('EXCLUSIVE_ACCESS_ENDED', value.disposition)
        self.hook = lambda r, o, v: replace(v, disposition='SENDER_CLOSED')
        with self.assertRaisesRegex(port.EvidenceSourceError, 'FINISH_DENIED'): a(r)
        self.hook = lambda r, o, v: replace(v, progress_key='f' * 64)
        with self.assertRaisesRegex(port.EvidenceSourceError, 'FINISH_DENIED'): a(r)

    def test_partial_stopped_unknown_or_duplicate_journal_denied_before_source(self):
        self.terminal(); p = self.progress; journal = json.loads(p.journal)
        variants = [Progress(), replace(p, responses=p.responses[:-1]),
                    replace(p, journal=p.journal.replace(b'"status":', b'"status":"STOPPED","status":'))]
        for fields in ({'status': 'WRITE_ACKNOWLEDGED'}, {'pending': 'unknown'},
                       {'acknowledged': journal['acknowledged'][:-1]},
                       {'checkpoint_error': 'CHECKPOINT_FAILED'}, {'delivery_gate': 'PASS'}):
            variants.append(replace(p, journal=core.encoded(journal | fields)))
        for bad in variants:
            self.progress = bad; self.read.reset_mock()
            with self.subTest(bad=bad.key), self.assertRaises(port.EvidenceSourceError):
                self.adapter('finish')(self.request())
            self.read.assert_not_called()

    def test_same_finish_original_cannot_override_fresh_resolver_progress_denial(self):
        self.terminal(); observed_key = self.progress.key
        a = self.adapter('finish')
        def resolve(r, o):
            # Synthetic authority models an end observation for one exact progress.
            if r.progress.key != observed_key: raise ValueError('SYNTHETIC_STALE_END')
            return self.resolve('finish', r, o)
        a.resolver = resolve
        a(self.request())
        changed = json.loads(self.progress.responses[0])
        changed['metadata']['resourceVersion'] = '999'
        self.progress = replace(self.progress, responses=(core.encoded(changed),) + self.progress.responses[1:])
        with self.assertRaisesRegex(port.EvidenceSourceError, '^EVIDENCE_SOURCE_UNVERIFIED$'):
            a(self.request())

    def test_finish_wrong_object_wrong_uid_and_malformed_response_denied(self):
        self.terminal(); p = self.progress
        for raw in (p.responses[1], b'{broken', core.encoded(json.loads(p.responses[0]) | {'kind':'Secret'})):
            self.progress = replace(p, responses=(raw,) + p.responses[1:]); self.read.reset_mock()
            with self.assertRaises(port.EvidenceSourceError): self.adapter('finish')(self.request())
            self.read.assert_not_called()
        reply = json.loads(p.responses[1]); reply['metadata']['uid'] = json.loads(p.responses[0])['metadata']['uid']
        self.progress = replace(p, responses=(p.responses[0], core.encoded(reply)) + p.responses[2:])
        with self.assertRaisesRegex(port.EvidenceSourceError, 'RESPONSE_INVALID'):
            self.adapter('finish')(self.request())

    def test_reader_timeout_forbidden_or_resolver_errors_are_redacted_and_no_retry(self):
        for fail in ('reader', 'resolver'):
            self.read.side_effect = self.original; self.read.reset_mock(); a = self.adapter('producer')
            if fail == 'reader': self.read.side_effect = OSError(SENSITIVE)
            else: a.resolver = Mock(side_effect=RuntimeError(SENSITIVE))
            with self.assertRaisesRegex(port.EvidenceSourceError, '^EVIDENCE_SOURCE_UNVERIFIED$') as e:
                a(self.request())
            self.assertEqual(1, self.read.call_count)
            self.assertNotIn(SENSITIVE, str(e.exception)); self.assertIsNone(e.exception.__cause__)

    def test_close_releases_own_references_and_denies_reuse_without_revocation(self):
        a = self.adapter('producer'); a.close(); self.read.reset_mock()
        self.assertIsNone(a.reader); self.assertIsNone(a.resolver); self.assertIsNone(a.credential)
        self.assertEqual(core.digest(SENSITIVE.encode()), self.credential.fingerprint)
        with self.assertRaises(port.EvidenceSourceError): a(self.request())
        self.read.assert_not_called()

    def test_three_ports_connect_to_existing_provider_methods(self):
        unavailable = Mock(side_effect=AssertionError('OUT_OF_SCOPE'))
        adapters = AuthorityAdapters(unavailable, self.adapter('platform'),
            self.adapter('producer'), unavailable, self.adapter('finish'))
        p = OperationalProviders(self.h.binding, self.credential, lambda:self.progress,
            rendered=self.h.render, contract=self.h.contract, validator=validator,
            adapters=adapters, diagnostic_sink=unavailable,
            wall=lambda:self.h.now, monotonic=lambda:self.h.mono)
        r = self.request(); self.assertIs(p.platform(r).request,r); self.assertIs(p.producer(r).request,r)
        self.terminal(); r = self.request(); self.assertIs(p.finish(r).request,r)
        unavailable.assert_not_called()

    def test_actual_session_progress_factory_and_completion_failure_preserves_fence(self):
        for deny in (False, True):
            c = ContractHarness(); self.h = c.h
            self.h.rebind(ca_sha256=core.digest(CA))
            c.credential = c.providers.credential = self.credential
            self.hook = lambda r, o, v: v
            self.read.side_effect = self.original
            def factory(b, credential, progress):
                c.providers.platform = self.adapter('platform',progress)
                c.providers.producer = self.adapter('producer',progress)
                finish = self.adapter('finish',progress)
                if deny: finish.resolver = Mock(side_effect=RuntimeError(SENSITIVE))
                c.providers.finish = finish
                return c.providers
            value = session.run_session(c.h.binding,c.h.render,c.h.contract,validator,
                root='SYNTHETIC_MEMORY_STORE', providers=None, credential=self.credential,
                provider_factory=factory,sender_factory=c.sender,budget_seconds=5,
                acquire=c.storage.acquire,journal_factory=c.storage.journal,
                wall=lambda:c.h.now,monotonic=lambda:c.h.mono,sleep=c.h.sleep,finalize=True)
            self.assertEqual(1,c.sender_closes)
            self.assertEqual('RETAINED' if deny else 'RELEASED',value['fence'],value)
            self.assertEqual('STOPPED' if deny else 'SESSION_COMPLETED',value['status'],value)
            self.assertEqual('NOT_VERIFIED', value['runtime'])


if __name__ == '__main__': unittest.main()
