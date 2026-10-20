"""All adopted facts, credentials, sources and controls below are SYNTHETIC.

No actual environment values are generated. Socket/DNS are forbidden.
"""
from dataclasses import replace
import socket
import ssl
import unittest
from unittest.mock import Mock, patch

import delivery_adapter as core
import delivery_semantic_authority as semantic
import delivery_runtime_site as runtime
from delivery_adopted_authority import AdoptedGrant, pin_identity
from delivery_evidence_source import ReviewedEvidenceSource
from delivery_github_source import (GitHubOriginalAdapter, PublicGitHubTLS,
                                    RepositoryPin, BlobPin, SourceOriginal)
from delivery_object_reader import BearerCredential
from delivery_platform_readback import ExpectedSubject
from delivery_verifier import Progress, ReadRequest
from producer_bridge import CHECKER_SHA256
from test_delivery_adapter import Harness, SENSITIVE
from test_delivery_operational import PLATFORM_RAW, SUPPLY_RAW
from test_delivery_transport import CA


class SemanticFixture:
    def context(self, h, credential):
        self.h, self.credential, self.progress = h, credential, Progress()
        h.rebind(ca_sha256=core.digest(CA), platform_receipt_sha256=core.digest(PLATFORM_RAW),
                 supply_receipt_sha256=core.digest(SUPPLY_RAW))
        with patch.object(ssl, 'SSLContext', return_value=Mock()):
            self.reader = GitHubOriginalAdapter(PublicGitHubTLS(ca_pem=CA, endpoint_ip='192.0.2.10'))
        self.raws = {'approval': b'SYNTHETIC_APPROVAL', 'platform': PLATFORM_RAW,
            'producer': SUPPLY_RAW, 'finish': b'SYNTHETIC_POST_RUN_ORIGINAL',
            'control': b'SYNTHETIC_ADOPTED_CONTROL'}
        repo = RepositoryPin('synthetic-owner', 'synthetic-repo', 123, 321)
        self.pins = {name: BlobPin(repo, 'a'*40, name, core.digest(raw),
                                  'heads/synthetic', 'a'*40) for name, raw in self.raws.items()}
        self.grants = {name: AdoptedGrant(h.binding, self.pins[name], self.pins['control'],
            'synthetic-subject' if name == 'platform' else 'synthetic-'+name,
            'synthetic:reviewed-'+name, 900, 1900) for name in ('approval','platform','producer','finish')}
        self.issuer = semantic.IssuerFacts(credential.fingerprint, 'synthetic:issuer',
            'synthetic:audience', 900, 1000, 1900, CA, '192.0.2.10',
            'synthetic:least-privilege-original', 'synthetic:route-original')
        self.producer = semantic.ProducerFacts(h.binding.images, h.binding.images,
            h.binding.source_commit, h.binding.contract_sha256, h.binding.rendered_sha256,
            CHECKER_SHA256, *('synthetic:'+x for x in ('test','build','scan','push','clean-render','adoption')))
        self.read_calls = []
        self.read_hook = lambda request, pin, original: original
        def read(request, pin):
            self.read_calls.append((request, pin))
            ref, rev, repo_id, creator, _ = pin_identity(pin)
            return self.read_hook(request, pin, SourceOriginal(request, ref, rev, repo_id, creator,
                self.raws[pin.path], (b'SYNTHETIC_SOURCE_RESPONSE',)))
        p = patch.object(self.reader, 'blob', side_effect=read)
        p.start(); self.addCleanup(p.stop)

    def request(self):
        return ReadRequest(self.h.binding, self.credential, self.progress, self.h.mono+30)

    def resolver(self, role, facts=None):
        return semantic.CodeAdoptedEvidenceResolver(role=role, grant=self.grants[role],
            facts=facts or getattr(self, {'platform':'issuer','producer':'producer','finish':'finish'}[role]),
            control_reader=self.reader, credential=self.credential, progress=lambda:self.progress,
            wall=lambda:self.h.now, monotonic=lambda:self.h.mono)

    def port(self, role, facts=None):
        return ReviewedEvidenceSource(role=role, reader=self.reader, pin=self.pins[role],
            resolver=self.resolver(role, facts), binding=self.h.binding, credential=self.credential,
            progress=lambda:self.progress, wall=lambda:self.h.now, monotonic=lambda:self.h.mono)

    def terminal(self):
        receipt = self.h.run()
        self.assertEqual('ROLLOUT_OBSERVED', receipt['status'])
        self.progress = Progress(core.encoded(receipt), tuple(core.encoded(
            self.h.objects[core.path_for(key, self.h.binding)]) for key in core.objects_for(self.h.binding)))
        self.finish = semantic.FinishFacts(self.h.binding.run_id, self.h.binding.action,
            self.progress.key, self.h.now, 'synthetic:followup', 'synthetic:exclusive-end')

    def plan(self):
        return runtime.RuntimeAuthorityPlan(self.grants['approval'],
            runtime.EvidenceAdoption(self.grants['platform'], self.issuer),
            runtime.EvidenceAdoption(self.grants['producer'], self.producer),
            ExpectedSubject('synthetic-subject', 'synthetic-uid', ('system:authenticated',)))

    def factory(self, slot=None, **kw):
        return runtime.RuntimeAuthorityFactory(self.plan(),
            tuple(runtime.SourceReaders(self.reader, self.reader) for _ in range(4)),
            slot or runtime.FinishAdoptionSlot(self.h.binding),
            wall=lambda:self.h.now, monotonic=lambda:self.h.mono, **kw)


class SemanticTests(SemanticFixture, unittest.TestCase):
    def setUp(self):
        for name in ('socket','getaddrinfo','create_connection'):
            p=patch.object(socket,name,side_effect=AssertionError('NETWORK_FORBIDDEN'))
            p.start();self.addCleanup(p.stop)
        self.context(Harness(), BearerCredential(SENSITIVE))

    def test_platform_producer_return_same_native_bytes_and_two_fresh_controls(self):
        for role in ('platform','producer'):
            self.read_calls.clear()
            r=self.request(); value=self.port(role)(r)
            self.assertIs(value.request,r)
            self.assertEqual(self.raws[role],value.raw_receipt)
            self.assertEqual([role,'control',role,'control'],[p.path for _,p in self.read_calls])
            self.assertNotIn(SENSITIVE,repr(value))

    def test_no_flags_or_partial_fact_policy_accepted_without_source_io(self):
        for facts in (None, {'approved':True}, self.producer):
            with self.assertRaises(semantic.SemanticAuthorityError):
                semantic.CodeAdoptedEvidenceResolver(role='platform',grant=self.grants['platform'],
                    facts=facts,control_reader=self.reader,credential=self.credential,
                    progress=lambda:self.progress)
        self.assertEqual([],self.read_calls)

    def test_issuer_fingerprint_ca_ip_refs_actual_ttl_are_mandatory(self):
        changes=({'credential_fingerprint':'f'*64},{'ca_pem':b'other'}, {'endpoint_ip':'127.0.0.1'},
            {'endpoint_ip':'fe80::1%eth0'}, {'issuer':'PENDING'}, {'audience':''},
            {'requested_seconds':True},{'requested_seconds':3600}, {'expires_at':1901},
            {'issued_at':float('nan')},{'rbac_reference':'UNKNOWN'},{'route_reference':'PENDING'})
        for change in changes:
            with self.subTest(change=change),self.assertRaises(semantic.SemanticAuthorityError):
                self.resolver('platform',replace(self.issuer,**change))
        self.assertEqual([],self.read_calls)

    def test_issuer_issued_future_and_expired_denied_not_inferred_from_request_ttl(self):
        for issued,expires,now in ((1001,1900,1000),(1000,1900,1900)):
            self.h.now=now
            with self.subTest(now=now),self.assertRaises(Exception):
                self.port('platform',replace(self.issuer,issued_at=issued,expires_at=expires))(self.request())

    def test_producer_all_lineage_and_previous_profile_validated(self):
        for field in ('source_commit','contract_sha256','rendered_sha256','checker_sha256',
                      'test_reference','build_reference','scan_reference','push_reference',
                      'clean_render_reference','adoption_reference'):
            with self.subTest(field=field),self.assertRaises(Exception):
                self.resolver('producer',replace(self.producer,**{field:'PENDING'}))
        for images in (tuple(reversed(self.producer.images)), self.producer.images[:-1],
                       tuple((name,'synthetic/image:latest') for name,_ in self.producer.images)):
            for field in ('images','previous_images'):
                with self.subTest(field=field),self.assertRaises(Exception):
                    self.resolver('producer',replace(self.producer,**{field:images}))
        self.assertEqual([],self.read_calls)

    def test_second_control_drift_denies_not_cached(self):
        n=0
        def hook(r,p,o):
            nonlocal n
            if p.path=='control':
                n+=1
                if n==2:return replace(o,body=b'SYNTHETIC_WITHDRAWN')
            return o
        self.read_hook=hook
        with self.assertRaises(Exception):self.port('producer')(self.request())
        self.assertEqual(2,n)

    def test_deadline_progress_change_and_sensitive_reader_failure_stop(self):
        for mode in ('deadline','progress','exception'):
            self.h.now=self.h.mono=1000;self.progress=Progress();a=self.port('platform');r=self.request()
            def hook(r,p,o):
                if mode=='deadline':self.h.mono=r.deadline
                elif mode=='progress':self.progress=Progress(responses=(b'changed',))
                else:raise RuntimeError(SENSITIVE)
                return o
            self.read_hook=hook
            with self.subTest(mode=mode),self.assertRaises(Exception) as result:a(r)
            self.assertNotIn(SENSITIVE,str(result.exception))

    def test_stalled_wall_does_not_extend_issuer_lifetime(self):
        a=self.port('platform');r=replace(self.request(),deadline=3000)
        def hook(r,p,o):
            if p.path=='control':self.h.mono=1901
            return o
        self.read_hook=hook
        with self.assertRaises(Exception):a(r)

    def test_receipt_hash_mismatch_is_not_semantic_acceptance(self):
        self.grants['producer']=replace(self.grants['producer'],binding=replace(
            self.h.binding,supply_receipt_sha256='f'*64))
        resolver=self.resolver('producer')
        r=replace(self.request(),binding=self.grants['producer'].binding)
        ref,rev,rid,creator,_=pin_identity(self.pins['producer'])
        original=SourceOriginal(r,ref,rev,rid,creator,SUPPLY_RAW,())
        with self.assertRaisesRegex(semantic.SemanticAuthorityError,'RECEIPT_MISMATCH'):
            resolver(r,original)

    def test_finish_requires_exact_terminal_progress_run_action_and_observation_time(self):
        self.terminal();value=self.port('finish')(self.request())
        self.assertEqual(self.progress.key,value.progress_key)
        self.assertEqual('EXCLUSIVE_ACCESS_ENDED',value.disposition)
        for change in ({'run_id':'synthetic-other'}, {'action':'restore'}, {'progress_key':'f'*64},
                       {'observed_at':self.h.now+1},{'follow_up_reference':'PENDING'}):
            with self.subTest(change=change),self.assertRaises(Exception):
                self.port('finish',replace(self.finish,**change))(self.request())
        self.progress=Progress();self.read_calls.clear()
        with self.assertRaises(Exception):self.port('finish')(self.request())
        self.assertEqual([],self.read_calls)

    def test_resolver_close_is_idempotent_and_reuse_denied_without_io(self):
        resolver=self.resolver('platform');resolver.close();resolver.close()
        with self.assertRaisesRegex(semantic.SemanticAuthorityError,'CLOSED'):
            resolver(self.request(),None)
        self.assertIsNone(resolver.credential);self.assertIsNone(resolver.authority)
        self.assertEqual([],self.read_calls)

    def test_invalid_facts_release_already_constructed_authority_handle(self):
        made=[];original=semantic.CodeAdoptedGrantResolver
        def capture(**kwargs):
            value=original(**kwargs);made.append(value);return value
        with patch.object(semantic,'CodeAdoptedGrantResolver',side_effect=capture):
            with self.assertRaises(semantic.SemanticAuthorityError):
                self.resolver('platform',replace(self.issuer,credential_fingerprint='f'*64))
        self.assertEqual(1,len(made));self.assertIsNone(made[0].credential)
        self.assertIsNone(made[0].reader)

    def test_finish_slot_missing_single_install_and_progress_change(self):
        slot=runtime.FinishAdoptionSlot(self.h.binding)
        self.terminal();record=runtime.EvidenceAdoption(self.grants['finish'],self.finish)
        with self.assertRaisesRegex(runtime.RuntimeSiteError,'NOT_INSTALLED'):slot.get(self.request())
        slot.install(record,self.progress)
        self.assertIs(record,slot.get(self.request()))
        with self.assertRaisesRegex(runtime.RuntimeSiteError,'ALREADY_INSTALLED'):slot.install(record,self.progress)
        self.progress=replace(self.progress,responses=tuple(reversed(self.progress.responses)))
        with self.assertRaises(Exception):slot.get(self.request())
        self.assertEqual([],self.read_calls)

    def test_finish_slot_cannot_install_on_partial_progress(self):
        self.terminal();record=runtime.EvidenceAdoption(self.grants['finish'],self.finish)
        for p in (Progress(),replace(self.progress,responses=self.progress.responses[:-1])):
            with self.assertRaises(semantic.SemanticAuthorityError):
                runtime.FinishAdoptionSlot(self.h.binding).install(record,p)


if __name__=='__main__':unittest.main()
