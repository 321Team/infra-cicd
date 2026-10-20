"""Synthetic policy adoption only; no real grant, identity or Runtime values."""
from dataclasses import fields, replace
import unittest
from unittest.mock import Mock, patch

import delivery_adapter as core
import delivery_github_source as source
from delivery_adopted_authority import (AdoptedGrant, CodeAdoptedGrantResolver,
                                        AdoptedAuthorityError, pin_identity)
from delivery_approval_source import ReviewedApprovalAdapter, ApprovalSourceError
from delivery_object_reader import BearerCredential
from delivery_verifier import Progress, ReadRequest
from test_delivery_adapter import Harness, SENSITIVE
from test_delivery_transport import CA


class AdoptedAuthorityTests(unittest.TestCase):
    def setUp(self):
        for name in ('socket', 'getaddrinfo', 'create_connection'):
            p = patch.object(source.socket, name, side_effect=AssertionError('NETWORK_FORBIDDEN'))
            p.start(); self.addCleanup(p.stop)
        with patch.object(source.ssl, 'SSLContext', return_value=Mock()):
            wire = source.PublicGitHubTLS(ca_pem=CA, endpoint_ip='192.0.2.10')
        self.reader = source.GitHubOriginalAdapter(wire)
        self.binding = Harness().binding
        self.credential, self.progress = BearerCredential(SENSITIVE), Progress()
        self.request = ReadRequest(self.binding, self.credential, self.progress, 20)
        repo = source.RepositoryPin('synthetic-owner', 'synthetic-repo', 123, 321)
        self.pin = source.CommentPin(repo, 7, 8, 9, '2026-01-01T01:02:03Z', core.digest(b'SYNTHETIC_GRANT'))
        self.control_pin = source.BlobPin(repo, 'a'*40, 'synthetic/control',
            core.digest(b'SYNTHETIC_CONTROL'), 'heads/synthetic-control', 'b'*40)
        self.grant = AdoptedGrant(self.binding, self.pin, self.control_pin,
                                 'SYNTHETIC_AUTHORITY', 'synthetic:adoption', 90, 110)
        self.wall, self.mono = 100, 1
        self.resolver = self.make()
        self.control_read = patch.object(self.reader, 'blob', side_effect=lambda r,p:
            self.original(r, p, b'SYNTHETIC_CONTROL')).start()
        self.comment_read = patch.object(self.reader, 'comment', side_effect=lambda r,p:
            self.original(r, p, b'SYNTHETIC_GRANT')).start()
        self.addCleanup(patch.stopall)

    def make(self, **changes):
        args = dict(grant=self.grant, control_reader=self.reader, credential=self.credential,
                    progress=lambda:self.progress, wall=lambda:self.wall, monotonic=lambda:self.mono)
        args.update(changes)
        return CodeAdoptedGrantResolver(**args)

    @staticmethod
    def original(r, pin, body):
        ref, rev, repo, creator, _ = pin_identity(pin)
        return source.SourceOriginal(r, ref, rev, repo, creator, body, (b'SYNTHETIC_RECEIPT',))

    def resolve(self):
        return self.resolver(self.request, self.original(self.request, self.pin, b'SYNTHETIC_GRANT'))

    def test_adapter_integration_two_fresh_controls_and_exact_original(self):
        adapter = ReviewedApprovalAdapter(reader=self.reader, pin=self.pin,
            resolver=self.resolver, binding=self.binding, credential=self.credential,
            progress=lambda:self.progress, wall=lambda:self.wall, monotonic=lambda:self.mono)
        value = adapter(self.request)
        self.assertIs(value.request, self.request)
        self.assertEqual(2, self.comment_read.call_count)
        self.assertEqual(2, self.control_read.call_count)
        self.assertEqual('ACTIVE', value.state)
        self.assertIn('synthetic:adoption|control=', value.provenance.authority_reference)
        self.assertNotIn(SENSITIVE, repr(self.grant)+repr(value))

    def test_missing_json_or_incomplete_policy_rejected_before_io(self):
        for grant in (None, {'state':'ACTIVE'}, replace(self.grant, principal='PENDING'),
                      replace(self.grant, adoption_reference=''), replace(self.grant, control_pin=self.pin)):
            with self.subTest(grant=type(grant)), self.assertRaises(AdoptedAuthorityError): self.make(grant=grant)
        self.control_read.assert_not_called()

    def test_all_binding_fields_are_scope_not_just_run_id(self):
        original = self.original(self.request, self.pin, b'SYNTHETIC_GRANT')
        for f in fields(self.binding):
            value=getattr(self.binding,f.name)
            changed=tuple(reversed(value)) if type(value) is tuple else value+'x'
            r=replace(self.request,binding=replace(self.binding,**{f.name:changed}))
            with self.subTest(field=f.name), self.assertRaisesRegex(AdoptedAuthorityError,'^ADOPTED_REQUEST_INVALID$'):
                self.resolver(r,original)
        self.control_read.assert_not_called()

    def test_source_reference_revision_repository_creator_and_bytes_exact(self):
        original=self.original(self.request,self.pin,b'SYNTHETIC_GRANT')
        for change in ({'reference':original.reference+'x'},{'revision':'other'},
                       {'repository_id':124},{'repository_id':True},{'comment_creator_id':10},
                       {'body':b'ACTIVE'}, {'request':replace(self.request,deadline=19)}):
            with self.subTest(change=list(change)), self.assertRaises(AdoptedAuthorityError):
                self.resolver(self.request,replace(original,**change))
        self.control_read.assert_not_called()

    def test_control_revision_bytes_repository_and_request_changed(self):
        good=self.original(self.request,self.control_pin,b'SYNTHETIC_CONTROL')
        for change in ({'revision':'withdrawn'},{'body':b'SYNTHETIC_REVOKED'},
                       {'repository_id':124},{'request':replace(self.request,deadline=19)}):
            self.control_read.side_effect=None;self.control_read.return_value=replace(good,**change)
            with self.subTest(change=list(change)),self.assertRaises(AdoptedAuthorityError):self.resolve()

    def test_second_control_update_fails_whole_approval(self):
        good=self.original(self.request,self.control_pin,b'SYNTHETIC_CONTROL')
        self.control_read.side_effect=[good,replace(good,body=b'SYNTHETIC_REVOKED')]
        adapter=ReviewedApprovalAdapter(reader=self.reader,pin=self.pin,resolver=self.resolver,
            binding=self.binding,credential=self.credential,progress=lambda:self.progress,
            wall=lambda:self.wall,monotonic=lambda:self.mono)
        with self.assertRaises(ApprovalSourceError):adapter(self.request)
        self.assertEqual(2,self.control_read.call_count)

    def test_deleted_denied_control_or_exception_masks_sensitive_content(self):
        self.control_read.side_effect=RuntimeError(SENSITIVE)
        with self.assertRaisesRegex(AdoptedAuthorityError,'^ADOPTED_AUTHORITY_UNAVAILABLE$') as e:self.resolve()
        self.assertNotIn(SENSITIVE,str(e.exception));self.assertIsNone(e.exception.__cause__)

    def test_start_inclusive_expiry_exclusive_and_nonfinite_rejected(self):
        self.wall=90;self.assertEqual('ACTIVE',self.resolve().evidence.state)
        for now in (89,110,111):
            self.wall=now;self.resolver=self.make()
            with self.subTest(now=now),self.assertRaises(AdoptedAuthorityError):self.resolve()
        for v in (True,float('inf'),float('nan'),'110'):
            with self.subTest(value=str(v)),self.assertRaises(AdoptedAuthorityError):
                self.make(grant=replace(self.grant,expires_at=v))

    def test_deadline_progress_credential_and_clock_reversal(self):
        for r in (replace(self.request,deadline=1),replace(self.request,credential=BearerCredential('other')),
                  replace(self.request,progress=Progress(responses=(b'changed',)))):
            with self.assertRaises(AdoptedAuthorityError):self.resolver(r,self.original(r,self.pin,b'SYNTHETIC_GRANT'))
        self.control_read.assert_not_called()
        self.resolve();self.mono=0
        with self.assertRaisesRegex(AdoptedAuthorityError,'^ADOPTED_CLOCK_INVALID$'):self.resolve()

    def test_reader_spends_deadline_lifetime_or_changes_progress(self):
        for kind in ('deadline','lifetime','progress'):
            self.wall,self.mono,self.progress=100,1,Progress();self.resolver=self.make()
            def read(r,p):
                value=self.original(r,p,b'SYNTHETIC_CONTROL')
                if kind=='deadline':self.mono=20
                elif kind=='lifetime':self.mono=12
                else:self.progress=Progress(responses=(b'changed',))
                return value
            self.control_read.side_effect=read
            with self.subTest(kind=kind),self.assertRaises(AdoptedAuthorityError):self.resolve()

    def test_blob_grant_and_changed_anchor_are_not_a_new_default(self):
        pin=replace(self.control_pin,path='synthetic/grant',sha256=core.digest(b'SYNTHETIC_GRANT'))
        resolver=self.make(grant=replace(self.grant,original_pin=pin))
        value=resolver(self.request,self.original(self.request,pin,b'SYNTHETIC_GRANT'))
        self.assertIs(value.evidence.binding,self.binding)
        self.control_read.side_effect=source.SourceError('SOURCE_ANCHOR_CHANGED')
        with self.assertRaises(AdoptedAuthorityError):self.resolve()

    def test_invalid_pin_rejected_before_io(self):
        for pin in (replace(self.control_pin,path='../escape'),replace(self.control_pin,anchor_ref='tags/fixed'),
                    replace(self.pin,updated_at='2026-01-99T01:02:03Z'),replace(self.pin,body_sha256='pending')):
            with self.subTest(pin=type(pin)),self.assertRaises(AdoptedAuthorityError):
                self.make(grant=replace(self.grant,original_pin=pin))
        self.control_read.assert_not_called()


if __name__ == '__main__':
    unittest.main()
