"""Offline synthetic authority/source only. No actual approval or Runtime."""
from dataclasses import replace
import unittest
from unittest.mock import Mock, patch

import delivery_adapter as core
import delivery_github_source as source
from delivery_approval_source import AuthorityDecision, ReviewedApprovalAdapter, ApprovalSourceError
from delivery_object_reader import BearerCredential
from delivery_verifier import ApprovalEvidence, Progress, Provenance, ReadRequest
from test_delivery_adapter import Harness, SENSITIVE
from test_delivery_transport import CA


class ApprovalSourceTests(unittest.TestCase):
    def setUp(self):
        for name in ('socket', 'getaddrinfo', 'create_connection'):
            p = patch.object(source.socket, name, side_effect=AssertionError('NETWORK_FORBIDDEN'))
            p.start(); self.addCleanup(p.stop)
        with patch.object(source.ssl, 'SSLContext', return_value=Mock()):
            tls = source.PublicGitHubTLS(ca_pem=CA, endpoint_ip='192.0.2.10')
        self.reader = source.GitHubOriginalAdapter(tls)
        self.credential = BearerCredential(SENSITIVE)
        self.binding = Harness().binding
        self.progress = Progress()
        self.request = ReadRequest(self.binding, self.credential, self.progress, 20)
        repo = source.RepositoryPin('synthetic-owner', 'synthetic-repo', 123, 321)
        self.pin = source.CommentPin(repo, 7, 8, 9, '2026-01-01T01:02:03Z', core.digest(b'SYNTHETIC'))
        self.wall, self.mono = 100, 1
        self.resolver_calls = []
        self.adapter = ReviewedApprovalAdapter(reader=self.reader, pin=self.pin,
            resolver=self.resolve, binding=self.binding, credential=self.credential,
            progress=lambda: self.progress, wall=lambda: self.wall, monotonic=lambda: self.mono)
        self.read = patch.object(self.reader, 'comment', side_effect=self.original).start()
        self.addCleanup(patch.stopall)

    def original(self, request, pin):
        return source.SourceOriginal(request, 'https://github.com/synthetic-owner/synthetic-repo/issues/7#issuecomment-8',
            pin.updated_at + '/' + pin.body_sha256, 123, 9, b'SYNTHETIC', (b'SYNTHETIC_RESPONSE',))

    def resolve(self, request, original):
        self.resolver_calls.append(original)
        evidence = ApprovalEvidence(request, self.binding,
            Provenance(original.reference, original.revision, 'SYNTHETIC_AUTHORITY', 'synthetic-authority/revision-1'),
            'ACTIVE', 90, 110)
        return AuthorityDecision(original, evidence)

    def test_two_fresh_originals_and_two_authority_reads_same_request(self):
        result = self.adapter(self.request)
        self.assertIs(result.request, self.request)
        self.assertEqual(2, self.read.call_count)
        self.assertEqual(2, len(self.resolver_calls))
        self.assertIsNot(self.resolver_calls[0], self.resolver_calls[1])
        self.assertNotIn('SYNTHETIC_RESPONSE', repr(self.resolve(self.request, self.resolver_calls[0])))

    def test_connects_to_existing_operational_approval_port_only(self):
        from delivery_provider import AuthorityAdapters, OperationalProviders
        unavailable = Mock(side_effect=AssertionError('OTHER_AUTHORITY_NOT_PART_OF_THIS_TEST'))
        adapters = AuthorityAdapters(self.adapter, unavailable, unavailable, unavailable, unavailable)
        providers = OperationalProviders(self.binding, self.credential, lambda: self.progress,
            rendered=b'SYNTHETIC', contract=b'SYNTHETIC', validator=unavailable,
            adapters=adapters, diagnostic_sink=unavailable, wall=lambda: 100, monotonic=lambda: 1)
        evidence = providers.approval(self.request)
        self.assertIs(evidence.request, self.request)
        self.assertEqual(2, self.read.call_count)
        unavailable.assert_not_called()

    def test_unconfigured_or_json_declaration_is_not_authority(self):
        self.adapter.resolver = lambda r, o: {'state': 'ACTIVE'}
        with self.assertRaisesRegex(ApprovalSourceError, '^APPROVAL_AUTHORITY_DECISION_INVALID$'):
            self.adapter(self.request)
        with self.assertRaisesRegex(ApprovalSourceError, '^APPROVAL_RESOLVER_REQUIRED$'):
            ReviewedApprovalAdapter(reader=self.reader, pin=self.pin, resolver=None,
                binding=self.binding, credential=self.credential, progress=lambda: Progress())

    def test_changed_source_and_different_original_request_denied(self):
        first = self.original(self.request, self.pin)
        for other in (replace(first, body=b'EDITED'), replace(first, revision='new'),
                      replace(first, request=replace(self.request, deadline=19))):
            with self.subTest(other=other.revision):
                self.read.side_effect = [first, other]
                with self.assertRaises(ApprovalSourceError): self.adapter(self.request)

    def test_deleted_denied_or_unknown_source_masks_original_error(self):
        self.read.side_effect = OSError(SENSITIVE)
        with self.assertRaisesRegex(ApprovalSourceError, '^APPROVAL_SOURCE_UNVERIFIED$') as e:
            self.adapter(self.request)
        self.assertIsNone(e.exception.__cause__)
        self.assertNotIn(SENSITIVE, str(e.exception))

    def test_revocation_and_authority_revision_change_on_second_read(self):
        for kind in ('revoked', 'revision', 'window'):
            count = [0]
            def resolver(r, o):
                count[0] += 1
                value = self.resolve(r, o)
                if count[0] == 2:
                    e = value.evidence
                    if kind == 'revoked': e = replace(e, state='REVOKED')
                    elif kind == 'window': e = replace(e, expires_at=109)
                    else: e = replace(e, provenance=replace(e.provenance, authority_reference='synthetic/revision-2'))
                    value = AuthorityDecision(o, e)
                return value
            self.adapter.resolver = resolver
            with self.subTest(kind=kind), self.assertRaises(ApprovalSourceError): self.adapter(self.request)
            self.assertEqual(2, count[0])

    def test_cached_original_wrong_binding_credential_or_provenance_denied(self):
        saved = []
        def stale(r, o):
            saved.append(self.resolve(r, o))
            return saved[0]
        self.adapter.resolver = stale
        with self.assertRaisesRegex(ApprovalSourceError, '^APPROVAL_AUTHORITY_DECISION_INVALID$'):
            self.adapter(self.request)
        for field in ('binding', 'request', 'provenance'):
            def resolver(r, o):
                value = self.resolve(r, o); e = value.evidence
                if field == 'binding': e = replace(e, binding=replace(self.binding, run_id='SYNTHETIC_OTHER'))
                elif field == 'request': e = replace(e, request=replace(r, deadline=19))
                else: e = replace(e, provenance=replace(e.provenance, revision='unrelated'))
                return AuthorityDecision(o, e)
            self.adapter.resolver = resolver
            with self.subTest(field=field), self.assertRaises(ApprovalSourceError): self.adapter(self.request)

    def test_wrong_request_credential_progress_and_deadline_before_read(self):
        for request in (replace(self.request, credential=BearerCredential('different')),
                        replace(self.request, progress=Progress(responses=(b'x',))),
                        replace(self.request, deadline=1)):
            self.read.reset_mock()
            with self.assertRaises(ApprovalSourceError): self.adapter(request)
            self.read.assert_not_called()

    def test_resolver_can_consume_budget_change_progress_or_expire_window(self):
        for kind in ('deadline', 'progress', 'expiry'):
            self.progress, self.mono, self.wall = Progress(), 1, 100
            self.adapter._last = self.adapter._anchor = None
            def resolver(r, o):
                value = self.resolve(r, o)
                if kind == 'deadline': self.mono = 20
                elif kind == 'progress': self.progress = Progress(responses=(b'changed',))
                else: self.wall = 110
                return value
            self.adapter.resolver = resolver
            with self.subTest(kind=kind), self.assertRaises(ApprovalSourceError): self.adapter(self.request)

    def test_wall_stall_still_consumes_lifetime_and_clock_reversal_denied(self):
        def slow(r, o):
            value = self.resolve(r, o); self.mono = 12
            return value
        self.adapter.resolver = slow
        with self.assertRaisesRegex(ApprovalSourceError, '^APPROVAL_AUTHORITY_EXPIRED$'):
            self.adapter(self.request)
        self.adapter._last = (100, 12); self.mono = 11
        with self.assertRaisesRegex(ApprovalSourceError, '^APPROVAL_SOURCE_CLOCK_INVALID$'):
            self.adapter(self.request)


if __name__ == '__main__':
    unittest.main()
