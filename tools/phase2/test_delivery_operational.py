"""Offline contract harness. All authorities, transport and clocks are FAKE.

MemoryStore models fences/records, not fsync durability. Separate POSIX tests
below connect the actual store; they skip on Windows without weakening checks.
"""
import contextlib
import copy
from dataclasses import replace
import io
import json
import os
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch

import delivery_adapter as core
import delivery_cli as cli
import delivery_recovery as recovery
import delivery_session as session
import delivery_store as store
import delivery_verifier as v
from test_delivery_adapter import Harness, SENSITIVE, validator


PLATFORM_RAW = b'SYNTHETIC_PLATFORM_RECEIPT'
SUPPLY_RAW = b'SYNTHETIC_P2_ADOPTION_AND_GENERATION_RECEIPT'
CA_RAW = b'SYNTHETIC_CA_NOT_A_CERTIFICATE'
ORIGIN = v.Provenance('synthetic:origin', 'revision-1', 'synthetic-lead', 'synthetic:authority')


class FakeProviders:
    def __init__(self, h, credential):
        self.h, self.credential = h, credential
        self.calls, self.progresses, self.deadlines = [], [], []
        self.hooks = {}
        self.saved = {}
        self.before = lambda name, request: None

    def call(self, name, request, result):
        self.calls.append(name)
        self.deadlines.append(request.deadline)
        self.before(name, request)
        value = self.hooks.get(name, lambda r, value: value)(request, result)
        self.saved[name] = value
        return value

    def approval(self, r):
        return self.call('approval', r, v.ApprovalEvidence(
            r, self.h.binding, ORIGIN, 'ACTIVE', 900, 1900))

    def platform(self, r):
        return self.call('platform', r, v.PlatformEvidence(
            r, self.h.binding, ORIGIN, self.credential, 'f' * 64,
            'synthetic-issuer', 'synthetic-audience', 900, 1900, 1000,
            CA_RAW, '192.0.2.10', PLATFORM_RAW))

    def producer(self, r):
        b = self.h.binding
        return self.call('producer', r, v.ProducerEvidence(
            r, b, ORIGIN, b.source_commit, b.contract_sha256,
            b.rendered_sha256, v.CHECKER_SHA256, SUPPLY_RAW))

    def objects(self, r):
        # Only a protocol model: no actual object/RBAC/ownership verification.
        latest = None if r.progress.journal is None else json.loads(r.progress.journal)
        if latest is not None:
            acknowledgements = latest['acknowledged']
            assert len(acknowledgements) <= len(r.progress.responses)
            for name, raw in zip(acknowledgements, r.progress.responses):
                assert name == '/'.join(core.identity(json.loads(raw)))
        self.progresses.append(r.progress)
        return self.call('objects', r, v.ObjectEvidence(r, r.progress.key))

    def finish(self, r):
        return self.call('finish', r, v.CompletionEvidence(
            r, ORIGIN, r.progress.key, 'EXCLUSIVE_ACCESS_ENDED'))


class MemoryStore:
    """Persistent only for the lifetime of this fake object, never disk evidence."""
    def __init__(self):
        self.active = None
        self.consumed = set()
        self.claims, self.journals = [], []
        self.fail = set()
        self.complete_calls = 0
        self.before_checkpoint = lambda receipt: None

    def acquire(self, root, binding):
        if self.active is not None or binding.run_id in self.consumed:
            raise RuntimeError(SENSITIVE)
        self.active = binding
        self.consumed.add(binding.run_id)
        claim = FakeClaim(self, binding)
        self.claims.append(claim)
        return claim

    def journal(self, claim):
        if 'journal_init' in self.fail:
            raise RuntimeError(SENSITIVE)
        journal = FakeJournal(claim)
        self.journals.append(journal)
        return journal


class FakeClaim:
    def __init__(self, owner, binding):
        self.owner, self.binding = owner, binding
        self.closed = False

    def check(self):
        if self.closed or self.owner.active != self.binding:
            raise RuntimeError(SENSITIVE)

    def complete(self, journal):
        self.owner.complete_calls += 1
        self.check()
        journal.check()
        assert journal.latest['status'] == 'ROLLOUT_OBSERVED'
        if 'complete' in self.owner.fail:
            raise RuntimeError(SENSITIVE)
        self.owner.active = None

    def close(self):
        self.closed = True
        if 'claim_close' in self.owner.fail:
            raise RuntimeError(SENSITIVE)


class FakeJournal:
    def __init__(self, claim):
        self.claim, self.latest = claim, None
        self.records = []
        self.closed = self.failed = False

    def check(self):
        self.claim.check()
        if self.closed or self.failed:
            raise RuntimeError(SENSITIVE)

    def __call__(self, receipt):
        self.check()
        self.claim.owner.before_checkpoint(receipt)
        if 'ack' in self.claim.owner.fail and receipt['status'] == 'WRITE_ACKNOWLEDGED':
            self.failed = True
            raise RuntimeError(SENSITIVE)
        safe = store.receipt_checked(receipt, self.claim.binding.key)
        store.transition_checked(self.latest, safe)
        raw = core.encoded({'schema': 'P2-CICD-journal-1',
                            'sequence': len(self.records) + 1,
                            'binding_key': self.claim.binding.key,
                            'previous_sha256': core.digest(self.records[-1]) if self.records else None,
                            'receipt': safe})
        self.records.append(raw)
        self.latest = copy.deepcopy(safe)

    def close(self):
        self.closed = True
        if 'journal_close' in self.claim.owner.fail:
            raise RuntimeError(SENSITIVE)


class ContractHarness:
    def __init__(self):
        self.h = Harness()
        self.h.rebind(ca_sha256=core.digest(CA_RAW),
                      platform_receipt_sha256=core.digest(PLATFORM_RAW),
                      supply_receipt_sha256=core.digest(SUPPLY_RAW))
        self.credential = object()  # opaque fake; not a token
        self.providers = FakeProviders(self.h, self.credential)
        self.storage = MemoryStore()
        self.sender_calls = self.sender_closes = 0
        self.sender_fail = set()
        self.sender_change = lambda sender: sender
        self.budget = 5

    def sender(self, binding, credential, platform):
        self.sender_calls += 1
        if 'init' in self.sender_fail:
            raise RuntimeError(SENSITIVE)
        def close():
            self.sender_closes += 1
            if 'close' in self.sender_fail:
                raise RuntimeError(SENSITIVE)
        return self.sender_change(session.BoundSender(
            credential, binding.key, platform.credential_fingerprint, binding.ca_sha256,
            platform.endpoint_ip, self.h.send, close))

    def run(self, *, finalize=True, root='UNUSED_FAKE_ROOT', acquire=None, journal_factory=None):
        h = self.h
        return session.run_session(h.binding, h.render, h.contract, validator,
            root=root, providers=self.providers, credential=self.credential,
            sender_factory=self.sender, budget_seconds=self.budget,
            acquire=acquire or self.storage.acquire,
            journal_factory=journal_factory or self.storage.journal,
            wall=lambda: h.now, monotonic=lambda: h.mono, sleep=h.sleep, finalize=finalize)


class OperationalTests(unittest.TestCase):
    def setUp(self):
        # Any accidental network path fails these tests, including DNS.
        for name in ('socket', 'getaddrinfo', 'create_connection'):
            guard = patch.object(socket, name, side_effect=AssertionError('NETWORK_FORBIDDEN'))
            guard.start()
            self.addCleanup(guard.stop)
        self.c = ContractHarness()

    def assert_safe(self, result):
        self.assertNotIn(SENSITIVE, json.dumps(result))
        self.assertEqual(result['runtime'], 'NOT_VERIFIED')
        self.assertEqual(result['delivery_gate'], 'NOT_VERIFIED')

    def assert_preflight_denied(self):
        result = self.c.run()
        self.assertEqual(result['error_code'], 'PREFLIGHT_DENIED', result)
        self.assertEqual(self.c.h.requests, [])
        self.assertEqual(self.c.sender_calls, 0)
        self.assertEqual(self.c.storage.claims, [])
        self.assert_safe(result)

    def test_normal_six_writes_two_reads_and_explicit_completion(self):
        r = self.c.run()
        self.assertEqual(r['status'], 'SESSION_COMPLETED', r)
        self.assertEqual([q.method for q in self.c.h.requests], ['PATCH'] * 6 + ['GET'] * 2)
        self.assertEqual(self.c.storage.complete_calls, 1)
        self.assertIsNone(self.c.storage.active)
        self.assertEqual(self.c.sender_closes, 1)
        self.assertTrue(self.c.storage.journals[0].closed)
        self.assertTrue(self.c.storage.claims[0].closed)
        self.assert_safe(r)

    def test_rollout_alone_retains_terminal_fence(self):
        r = self.c.run(finalize=False)
        self.assertEqual(r['status'], 'REVIEW_REQUIRED')
        self.assertEqual(self.c.storage.complete_calls, 0)
        self.assertNotIn('finish', self.c.providers.calls)
        d = recovery.decision(self.c.h.binding, self.c.storage.journals[0].records,
                              active=True, complete=None)
        self.assertEqual(d['state'], 'TERMINAL_FENCE_RETAINED')
        self.assertFalse(d['automatic_resume_allowed'])

    def test_unconfigured_provider_blocks_before_claim(self):
        self.c.providers = v.UnconfiguredProviders()
        self.assert_preflight_denied()

    def test_json_approved_or_bare_binding_is_not_evidence(self):
        for raw in ({'approved': True}, self.c.h.binding, None):
            with self.subTest(raw=type(raw).__name__):
                self.c.providers.hooks['approval'] = lambda r, value: raw
                self.assert_preflight_denied()

    def test_withdrawn_superseded_unknown_and_future_approval(self):
        for changes in ({'state': 'WITHDRAWN'}, {'state': 'SUPERSEDED'}, {'state': 'UNKNOWN'},
                        {'starts_at': 1001}, {'expires_at': 1000}, {'expires_at': float('nan')}):
            with self.subTest(changes=changes):
                self.c.providers.hooks['approval'] = lambda r, value: replace(value, **changes)
                self.assert_preflight_denied()

    def test_missing_provenance_is_not_replaced_by_flags(self):
        for name in ('approval', 'platform', 'producer'):
            self.c = ContractHarness()
            self.c.providers.hooks[name] = lambda r, value: replace(
                value, provenance=replace(ORIGIN, authority_reference='PENDING'))
            self.assert_preflight_denied()

    def test_every_provider_exception_is_redacted(self):
        for name in ('approval', 'platform', 'producer', 'objects'):
            self.c = ContractHarness()
            def fail(r, value):
                raise RuntimeError(SENSITIVE)
            self.c.providers.hooks[name] = fail
            self.assert_preflight_denied()

    def test_namespace_uid_api_and_ca_drift(self):
        for changes in ({'namespace_uid': 'other-uid'}, {'ca_sha256': '9' * 64},
                        {'cluster_id': 'other-cluster'}, {'api_origin': 'https://other.example.com'}):
            self.c = ContractHarness()
            self.c.providers.hooks['platform'] = lambda r, value: replace(
                value, binding=replace(value.binding, **changes))
            self.assert_preflight_denied()

    def test_ca_bytes_credential_handle_and_expiry_mismatch(self):
        for changes in ({'ca_pem': b'other'}, {'credential': object()}, {'expires_at': 1000},
                        {'requested_seconds': 1800}, {'requested_seconds': True},
                        {'endpoint_ip': 'api.example.com'}, {'audience': 'UNKNOWN'},
                        {'endpoint_ip': '127.0.0.1'}, {'endpoint_ip': '::ffff:127.0.0.1'},
                        {'endpoint_ip': '169.254.1.1'}, {'endpoint_ip': '224.0.0.1'},
                        {'endpoint_ip': '0.0.0.0'}, {'endpoint_ip': 3221225994},
                        {'endpoint_ip': 'fe80::1%eth0'}):
            self.c = ContractHarness()
            self.c.providers.hooks['platform'] = lambda r, value: replace(value, **changes)
            self.assert_preflight_denied()

    def test_raw_platform_and_supply_receipt_mismatch(self):
        for name in ('platform', 'producer'):
            self.c = ContractHarness()
            self.c.providers.hooks[name] = lambda r, value: replace(value, raw_receipt=b'wrong-run')
            self.assert_preflight_denied()

    def verifier(self):
        return v.Verifier(providers=self.c.providers, credential=self.c.credential,
                          progress=lambda: v.Progress(), wall=lambda: self.c.h.now,
                          monotonic=lambda: self.c.h.mono, budget_seconds=5)

    def test_actual_credential_lifetime_900_succeeds(self):
        result = self.verifier()(self.c.h.binding)
        self.assertEqual(result.lease.token_expires_at, 1900)

    def test_actual_long_lived_or_unknown_credential_blocks_before_sender(self):
        for changes in ({'expires_at': 2800},  # 1800 seconds despite requested 900
                        {'issued_at': 100},   # old 1800s token with only 900s left
                        {'issued_at': 1001}, {'issued_at': float('nan')},
                        {'issued_at': None}, {'issued_at': True}):
            self.c = ContractHarness()
            self.c.providers.hooks['platform'] = lambda r, value: replace(value, **changes)
            self.assert_preflight_denied()
        self.c = ContractHarness()
        self.c.providers.hooks['platform'] = lambda r, value: replace(value, expires_at=2800)
        with self.assertRaisesRegex(v.VerificationError, '^CREDENTIAL_LIFETIME_INVALID$'):
            self.verifier()(self.c.h.binding)

    def test_expired_credential_has_direct_error_without_downstream_reads(self):
        self.c.providers.hooks['platform'] = lambda r, value: replace(
            value, issued_at=100, expires_at=1000)
        with self.assertRaisesRegex(v.VerificationError, '^CREDENTIAL_EXPIRED$'):
            self.verifier()(self.c.h.binding)
        self.assertNotIn('producer', self.c.providers.calls)
        self.assertNotIn('objects', self.c.providers.calls)

    def test_producer_source_contract_render_checker_and_adoption_mismatch(self):
        for field, value in (('source_commit', '9' * 40), ('contract_sha256', '9' * 64),
                             ('rendered_sha256', '9' * 64), ('checker_sha256', '9' * 64),
                             ('binding', replace(self.c.h.binding, action='restore'))):
            self.c = ContractHarness()
            self.c.providers.hooks['producer'] = lambda r, result: replace(result, **{field: value})
            self.assert_preflight_denied()

    def test_callback_deadline_and_aggregate_budget(self):
        for slow in (('approval', 5), ('objects', 5), ('all', 1.5)):
            self.c = ContractHarness()
            def before(name, r):
                if slow[0] in (name, 'all'):
                    self.c.h.sleep(slow[1])
            self.c.providers.before = before
            self.assert_preflight_denied()

    def test_provider_deadline_after_first_ack_blocks_next_write(self):
        def delay(name, r):
            if self.c.h.requests and name == 'approval':
                self.c.h.sleep(5)
        self.c.providers.before = delay
        r = self.c.run()
        self.assertEqual(r['receipt']['error_code'], 'AUTHORIZATION_DENIED')
        self.assertEqual(len(self.c.h.requests), 1)
        self.assertEqual(self.c.storage.journals[0].latest['acknowledged'], [store.IDENTITIES[0]])
        self.assertEqual(self.c.storage.complete_calls, 0)

    def test_fresh_namespace_drift_after_ack_stops_next_write(self):
        def change(request, obj):
            self.c.providers.hooks['platform'] = lambda r, value: replace(
                value, binding=replace(value.binding, namespace_uid='recreated-namespace'))
        self.c.h.after_send = change
        r = self.c.run()
        self.assertEqual(r['receipt']['error_code'], 'AUTHORIZATION_DENIED')
        self.assertEqual(len(self.c.h.requests), 1)
        self.assertIsNotNone(self.c.storage.active)

    def test_stale_cached_evidence_rejected(self):
        original = self.c.providers.platform
        def cached(r):
            return self.c.providers.saved.get('platform') or original(r)
        self.c.providers.platform = cached
        r = self.c.run()
        self.assertEqual(r['error_code'], 'AUTHORIZATION_DENIED')
        self.assertEqual(self.c.sender_calls, 0)
        self.assertIsNotNone(self.c.storage.active)

    def test_approval_revision_change_or_renewal_does_not_continue(self):
        for name, changes in (('approval', {'provenance': replace(ORIGIN, revision='revision-2')}),
                              ('platform', {'expires_at': 2000}),
                              ('platform', {'credential_fingerprint': '9' * 64}),
                              ('platform', {'endpoint_ip': '192.0.2.11'})):
            self.c = ContractHarness()
            def change(request, obj):
                self.c.providers.hooks[name] = lambda r, value: replace(value, **changes)
            self.c.h.after_send = change
            result = self.c.run()
            self.assertEqual(len(self.c.h.requests), 1)
            self.assertEqual(result['receipt']['error_code'], 'AUTHORIZATION_DENIED')
            self.assertEqual(self.c.storage.complete_calls, 0)

    def test_withdrawal_and_expiry_after_ack_block_next_patch(self):
        for expire in (False, True):
            self.c = ContractHarness()
            def change(request, obj):
                if expire:
                    self.c.h.sleep(900)
                else:
                    self.c.providers.hooks['approval'] = lambda r, value: replace(value, state='WITHDRAWN')
            self.c.h.after_send = change
            result = self.c.run()
            self.assertEqual(len(self.c.h.requests), 1)
            self.assertEqual(result['status'], 'STOPPED')
            self.assertEqual(self.c.storage.complete_calls, 0)

    def test_first_write_budget_rechecked_after_durable_intent(self):
        def delay(receipt):
            if receipt['status'] == 'WRITE_PENDING':
                self.c.h.sleep(241)
        self.c.storage.before_checkpoint = delay
        r = self.c.run()
        self.assertEqual(r['receipt']['error_code'], 'FIRST_WRITE_WINDOW_TOO_SHORT')
        self.assertEqual(self.c.h.requests, [])
        self.assertIsNotNone(self.c.storage.journals[0].latest['pending'])

    def test_preflight_elapsed_monotonic_time_cannot_extend_first_write_budget(self):
        self.c.budget = 300  # harness budget, not a selected production setting
        once = [True]
        def delay(name, r):
            if once[0]:
                self.c.h.mono += 241  # wall clock deliberately stalls
                once[0] = False
        self.c.providers.before = delay
        result = self.c.run()
        self.assertEqual(result['receipt']['error_code'], 'FIRST_WRITE_WINDOW_TOO_SHORT', result)
        self.assertEqual(self.c.h.requests, [])

    def test_clock_reversal_stops_before_sender(self):
        self.c.providers.before = lambda name, r: setattr(self.c.h, 'mono', 999)
        self.assert_preflight_denied()

    def test_journal_and_sender_initialization_failure_preserve_claim(self):
        for failure in ('journal_init', 'sender_init'):
            self.c = ContractHarness()
            if failure == 'journal_init':
                self.c.storage.fail.add(failure)
            else:
                self.c.sender_fail.add('init')
            result = self.c.run()
            self.assertEqual(result['status'], 'STOPPED')
            self.assertTrue(self.c.storage.claims[0].closed)
            self.assertIsNotNone(self.c.storage.active)
            self.assertEqual(self.c.storage.complete_calls, 0)
            self.assertEqual(self.c.h.requests, [])
            self.assert_safe(result)

    def test_sender_must_bind_same_handle_fingerprint_ca_endpoint(self):
        for changes in ({'credential': object()}, {'credential_fingerprint': '9' * 64},
                        {'ca_sha256': '9' * 64}, {'endpoint_ip': '192.0.2.11'},
                        {'binding_key': '9' * 64}):
            self.c = ContractHarness()
            self.c.sender_change = lambda sender: replace(sender, **changes)
            r = self.c.run()
            self.assertEqual(r['error_code'], 'SENDER_INITIALIZATION_FAILED')
            self.assertEqual(self.c.h.requests, [])
            self.assertEqual(self.c.sender_closes, 1)

    def test_dynamic_provider_sees_durable_acks_and_own_responses(self):
        self.assertEqual(self.c.run()['status'], 'SESSION_COMPLETED')
        points = self.c.providers.progresses
        self.assertTrue(any(p.journal is None for p in points))
        self.assertTrue(any(p.journal and len(json.loads(p.journal)['acknowledged']) == 6
                            and len(p.responses) == 6 for p in points))

    def test_dynamic_provider_mismatch_stops_without_static_baseline_fallback(self):
        self.c.providers.hooks['objects'] = lambda r, value: replace(value, progress_key='9' * 64)
        self.assert_preflight_denied()

    def test_claim_or_journal_change_during_provider_read_blocks_send(self):
        def change(name, request):
            if self.c.storage.claims:
                self.c.storage.active = None
        self.c.providers.before = change
        r = self.c.run()
        self.assertEqual(r['error_code'], 'AUTHORIZATION_DENIED')
        self.assertEqual(self.c.sender_calls, 0)
        self.assertEqual(self.c.storage.complete_calls, 0)

    def test_restore_after_completed_run_requires_separately_bound_previous_render(self):
        self.assertEqual(self.c.run()['status'], 'SESSION_COMPLETED')
        old_binding = self.c.h.binding
        old_records = list(self.c.storage.journals[0].records)
        docs = json.loads(self.c.h.render)
        images = []
        for obj in docs:
            if obj['kind'] == 'Deployment':
                container = obj['spec']['template']['spec']['containers'][0]
                container['image'] = container['image'].split('@')[0] + '@sha256:' + '9' * 64
                images.append((obj['metadata']['name'], container['image']))
        self.c.h.render = core.encoded(docs)
        self.c.h.rebind(run_id='synthetic-approved-restore', action='restore',
                        rendered_sha256=core.digest(self.c.h.render), images=tuple(images))
        self.assertEqual(self.c.run()['status'], 'SESSION_COMPLETED')
        self.assertNotEqual(old_binding.key, self.c.h.binding.key)
        self.assertEqual(self.c.storage.journals[0].records, old_records)
        self.assertEqual(len(self.c.h.requests), 16)
        for request in self.c.h.requests[8:14]:
            obj = json.loads(request.body)
            if obj['kind'] == 'Deployment':
                self.assertTrue(obj['spec']['template']['spec']['containers'][0]['image'].endswith('9' * 64))

    def test_unknown_write_recovery_has_pending_and_new_restore_cannot_bypass(self):
        self.c.h.fail_at = 1
        result = self.c.run()
        self.assertEqual(result['receipt']['error_code'], 'WRITE_OUTCOME_UNKNOWN')
        old = list(self.c.storage.journals[0].records)
        report = recovery.decision(self.c.h.binding, old, active=True, complete=None)
        self.assertEqual(report['state'], 'WRITE_OUTCOME_UNCONFIRMED')
        self.assertFalse(report['automatic_resume_allowed'])
        self.c.h.rebind(run_id='synthetic-new-restore', action='restore')
        again = self.c.run()
        self.assertEqual(again['error_code'], 'CLAIM_ACQUIRE_FAILED')
        self.assertEqual(len(self.c.h.requests), 1)
        self.assertEqual(self.c.storage.journals[0].records, old)

    def test_ack_failure_keeps_pending_instead_of_memory_success(self):
        self.c.storage.fail.add('ack')
        result = self.c.run()
        self.assertEqual(result['receipt']['error_code'], 'CHECKPOINT_FAILED')
        journal = self.c.storage.journals[0]
        self.assertEqual(journal.latest['status'], 'WRITE_PENDING')
        self.assertEqual(journal.latest['acknowledged'], [])
        self.assertEqual(len(self.c.h.requests), 1)
        self.assertEqual(self.c.storage.complete_calls, 0)

    def test_interrupt_after_response_before_ack_closes_and_keeps_pending(self):
        class Interrupted(BaseException):
            pass
        def crash(receipt):
            if receipt['status'] == 'WRITE_ACKNOWLEDGED':
                raise Interrupted()
        self.c.storage.before_checkpoint = crash
        with self.assertRaises(Interrupted):
            self.c.run()
        self.assertEqual(self.c.storage.journals[0].latest['status'], 'WRITE_PENDING')
        self.assertEqual(self.c.sender_closes, 1)
        self.assertTrue(self.c.storage.claims[0].closed)
        self.assertTrue(self.c.storage.journals[0].closed)
        self.assertIsNotNone(self.c.storage.active)

    def test_sender_close_failure_prevents_completion(self):
        self.c.sender_fail.add('close')
        r = self.c.run()
        self.assertEqual(r['error_code'], 'SENDER_CLOSE_FAILED')
        self.assertEqual(self.c.storage.complete_calls, 0)
        self.assertEqual(self.c.sender_closes, 1)
        self.assertIsNotNone(self.c.storage.active)

    def test_finish_failure_expiry_and_withdrawal_keep_terminal_fence(self):
        for mode in ('failure', 'expiry', 'withdrawal', 'wrong-progress'):
            self.c = ContractHarness()
            def finish(r, value):
                if mode == 'failure':
                    raise RuntimeError(SENSITIVE)
                if mode == 'expiry':
                    self.c.h.sleep(900)
                if mode == 'withdrawal':
                    self.c.providers.hooks['approval'] = lambda r, v: replace(v, state='WITHDRAWN')
                if mode == 'wrong-progress':
                    return replace(value, progress_key='9' * 64)
                return value
            self.c.providers.hooks['finish'] = finish
            r = self.c.run()
            self.assertEqual(r['error_code'], 'COMPLETION_DENIED')
            self.assertEqual(self.c.storage.complete_calls, 0)
            self.assertIsNotNone(self.c.storage.active)
            self.assert_safe(r)

    def test_complete_failure_never_claims_fence_absent_or_complete(self):
        self.c.storage.fail.add('complete')
        r = self.c.run()
        self.assertEqual(r['error_code'], 'CLAIM_COMPLETE_FAILED')
        self.assertEqual(r['completion'], 'ATTEMPTED')
        self.assertEqual(r['fence'], 'INSPECT_REQUIRED')
        self.assertTrue(self.c.storage.claims[0].closed)

    def test_cleanup_attempts_both_handles_and_reports_failure_after_complete(self):
        self.c.storage.fail.update(('journal_close', 'claim_close'))
        r = self.c.run()
        self.assertEqual(r['error_code'], 'CLEANUP_FAILED')
        self.assertEqual(r['completion'], 'RECORDED')
        self.assertEqual(r['fence'], 'INSPECT_REQUIRED')
        self.assertEqual(r['cleanup_errors'], ['JOURNAL_CLOSE_FAILED', 'CLAIM_CLOSE_FAILED'])
        self.assert_safe(r)

    def test_cli_status_deploy_restore_never_load_inputs_or_acquire(self):
        for command in ('status', 'deploy', 'restore'):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(cli.main([command]), 2)
            report = json.loads(out.getvalue())
            self.assertFalse(report['execution_authorized'])
            self.assertFalse(report['claim_acquired'])
            self.assertEqual(report['sender_calls'], 0)
            self.assertEqual(report['runtime'], 'NOT_RUN')

    def test_cli_unknown_arguments_are_not_echoed(self):
        out = io.StringIO()
        with contextlib.redirect_stderr(out), self.assertRaises(SystemExit) as caught:
            cli.main(['deploy', '--token', SENSITIVE])
        self.assertEqual(caught.exception.code, 2)
        self.assertNotIn(SENSITIVE, out.getvalue())
        self.assertEqual(out.getvalue(), 'CLI_ARGUMENTS_REJECTED\n')


@unittest.skipUnless(os.name == 'posix', 'Actual POSIX store integration requires Linux')
class OperationalPosixTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='p2-operational-', dir=Path.cwd())
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.root.chmod(0o700)
        self.c = ContractHarness()
        for name in ('socket', 'getaddrinfo', 'create_connection'):
            guard = patch.object(socket, name, side_effect=AssertionError('NETWORK_FORBIDDEN'))
            guard.start()
            self.addCleanup(guard.stop)

    def run_actual_store(self, **kwargs):
        return self.c.run(root=self.root, acquire=store.acquire, journal_factory=store.Journal, **kwargs)

    def inspect(self):
        return recovery.inspect(self.root, self.c.h.binding)

    def test_actual_journal_normal_completion(self):
        self.assertEqual(self.run_actual_store()['status'], 'SESSION_COMPLETED')
        self.assertEqual(self.inspect()['state'], 'COMPLETED_RECORD')

    def test_actual_pending_blocks_new_run_after_restart(self):
        self.c.h.fail_at = 1
        self.assertEqual(self.run_actual_store()['status'], 'STOPPED')
        self.assertEqual(self.inspect()['state'], 'WRITE_OUTCOME_UNCONFIRMED')
        self.c.h.rebind(run_id='new-restore', action='restore')
        self.assertEqual(self.run_actual_store()['error_code'], 'CLAIM_ACQUIRE_FAILED')
        self.assertEqual(len(self.c.h.requests), 1)

    def test_actual_partial_complete_requires_readonly_inspection(self):
        original = store.create_file
        def fail(parent, name, raw):
            result = original(parent, name, raw)
            if name.endswith('.complete.json'):
                raise OSError(SENSITIVE)
            return result
        with patch.object(store, 'create_file', side_effect=fail):
            r = self.run_actual_store()
        self.assertEqual(r['error_code'], 'CLAIM_COMPLETE_FAILED')
        report = self.inspect()
        self.assertEqual(report['state'], 'TERMINAL_FENCE_RETAINED')
        self.assertTrue(report['complete_marker_present'])
        self.assertTrue(report['active_fence_present'])


if __name__ == '__main__':
    unittest.main()
