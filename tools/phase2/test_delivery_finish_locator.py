"""SYNTHETIC GitHub envelopes and a TEST-ONLY body decoder, no live I/O.

The JSON schema in these fixtures is deliberately synthetic; it is NOT a
proposed producer format, operating configuration or actual completion proof.
"""
import base64
import copy
from dataclasses import replace
import hashlib
import json
import ssl
import unittest
from unittest.mock import Mock, patch

import delivery_adapter as core
import delivery_finish_collector as collector
import delivery_finish_locator as locator
import delivery_finish_worker as worker
import delivery_runtime_site as runtime
import delivery_github_source as source
from delivery_adopted_authority import pin_identity
from delivery_private_source import GitHubReadCredential, PrivateGitHubTLS, PrivateGitHubOriginalAdapter
from delivery_semantic_authority import FinishFacts
from delivery_verifier import Progress, ReadRequest
from test_delivery_adapter import Harness, SENSITIVE
from test_delivery_github_source import SourceFixture


class NativeLocatorTests(SourceFixture):
    def setUp(self):
        super().setUp()
        self.h = Harness()
        receipt = self.h.run()
        self.progress = Progress(core.encoded(receipt), tuple(core.encoded(self.h.objects[
            core.path_for(key,self.h.binding)]) for key in core.objects_for(self.h.binding)))
        self.request = ReadRequest(self.h.binding, self.request.credential, self.progress, self.h.mono + 30)
        self.adapter = source.GitHubOriginalAdapter(self.tls)
        self.prefix = '/repos/synthetic-owner/synthetic-repo'
        self.body_data = {'schema':'SYNTHETIC_FINISH_TEST_ONLY', 'run_id':self.h.binding.run_id,
            'action':self.h.binding.action, 'progress_key':self.progress.key, 'observed_at':self.h.now,
            'follow_up_reference':'synthetic:followup-native', 'exclusive_end_reference':'synthetic:exclusive-native'}
        self.body = core.encoded(self.body_data)
        self.control = source.BlobPin(self.repo,'d'*40,'control',core.digest(b'SYNTHETIC_CONTROL'),
                                      'heads/control','d'*40)
        self.policy = collector.FinishCollectionPolicy(self.h.binding,
            collector.BlobLocation(self.repo,'docs/item.json','heads/reviewed'), self.control,
            'synthetic-finish-owner','synthetic:reviewed-native-format',900,1900)
        self.calls=[];self.decoded=[]
        self.mutate=lambda path,obj:obj
        self.setup_api()

    def setup_api(self):
        sha=hashlib.sha1(b'blob '+str(len(self.body)).encode()+b'\0'+self.body).hexdigest()
        self.data={
            self.prefix:{'id':123,'owner':{'id':321},'full_name':'synthetic-owner/synthetic-repo','private':False},
            self.prefix+'/git/ref/heads/reviewed':{'ref':'refs/heads/reviewed','object':{'type':'commit','sha':'a'*40}},
            self.prefix+'/git/commits/'+'a'*40:{'sha':'a'*40,'tree':{'sha':'b'*40}},
            self.prefix+'/git/trees/'+'b'*40:{'sha':'b'*40,'truncated':False,'tree':[
                {'path':'docs','type':'tree','mode':'040000','sha':'c'*40}]},
            self.prefix+'/git/trees/'+'c'*40:{'sha':'c'*40,'truncated':False,'tree':[
                {'path':'item.json','type':'blob','mode':'100644','sha':sha}]},
            self.prefix+'/git/blobs/'+sha:{'sha':sha,'encoding':'base64','size':len(self.body),
                'content':base64.b64encode(self.body).decode()+'\n'},
            self.prefix+'/compare/'+'a'*40+'...'+'a'*40+'?per_page=1':{'status':'identical',
                'base_commit':{'sha':'a'*40},'merge_base_commit':{'sha':'a'*40}},
            self.prefix+'/issues/comments/8':{'id':8,'user':{'id':9},'body':self.body.decode(),
                'created_at':'2026-01-01T00:00:00Z','updated_at':'2026-01-01T01:02:03Z',
                'url':'https://api.github.com'+self.prefix+'/issues/comments/8',
                'issue_url':'https://api.github.com'+self.prefix+'/issues/7',
                'html_url':'https://github.com/synthetic-owner/synthetic-repo/issues/7#issuecomment-8'}}

    def api(self,path,deadline):
        self.assertEqual(self.request.deadline,deadline)
        self.calls.append(path)
        value=self.mutate(path,copy.deepcopy(self.data[path]))
        return core.encoded(value),value

    def decode(self,request,original):
        self.assertIs(request,self.request);self.assertIs(original.request,request)
        self.assertFalse(original.execution_authorized)
        self.assertFalse(original.body_authorship_verified)
        obj=json.loads(original.body)
        self.assertEqual('SYNTHETIC_FINISH_TEST_ONLY',obj.pop('schema'))
        self.decoded.append(original)
        return FinishFacts(**obj)  # Read TEST ONLY native fields; do not copy request into missing fields.

    def make(self,decode=None):
        return locator.NativeFinishLocator(policy=self.policy,reader=self.adapter,
            decode=decode or self.decode,comment_id=8 if type(self.policy.location)is collector.CommentLocation else None,
            wall=lambda:self.h.now,monotonic=lambda:self.h.mono)

    def comment_mode(self):
        self.policy=replace(self.policy,location=collector.CommentLocation(self.repo,7,9))

    def test_blob_native_discovery_then_existing_pin_reader_before_test_decoder(self):
        native=self.make()
        with patch.object(self.tls,'get',side_effect=self.api):record=native(self.request)
        pin=record.grant.original_pin
        self.assertEqual(('a'*40,'a'*40,core.digest(self.body)),(pin.commit,pin.anchor_commit,pin.sha256))
        self.assertEqual('docs/item.json',pin.path)
        self.assertEqual(self.body,self.decoded[0].body)
        self.assertEqual(self.body_data['progress_key'],record.facts.progress_key)
        self.assertEqual(4,self.calls.count(self.prefix+'/git/ref/heads/reviewed'))
        self.assertIn(self.prefix+'/compare/'+'a'*40+'...'+'a'*40+'?per_page=1',self.calls)
        self.assertTrue(all(path.startswith(self.prefix) for path in self.calls))
        self.assertNotIn(SENSITIVE,repr(record))
        with self.assertRaisesRegex(locator.FinishLocatorError,'CONSUMED'):native(self.request)

    def test_comment_native_envelope_rechecked_with_fresh_version(self):
        self.comment_mode()
        with patch.object(self.tls,'get',side_effect=self.api):record=self.make()(self.request)
        p=record.grant.original_pin
        self.assertEqual((7,8,9),(p.issue_number,p.comment_id,p.creator_id))
        self.assertEqual(core.digest(self.body),p.body_sha256)
        self.assertEqual(2,self.calls.count(self.prefix+'/issues/comments/8'))
        self.assertFalse(self.decoded[0].body_authorship_verified)

    def test_comment_wrong_issue_creator_identity_url_or_timestamp_stops_before_decode(self):
        self.comment_mode()
        for changed in ({'id':True},{'user':{'id':True}},{'user':{'id':10}},
            {'issue_url':'https://api.github.com'+self.prefix+'/issues/99'},
            {'html_url':'https://github.com/synthetic-owner/synthetic-repo/pull/7#issuecomment-8'},
            {'url':'https://evil.invalid'},{'created_at':'2026-02-01T00:00:00Z'},
            {'updated_at':'2026-99-01T00:00:00Z'},{'body':''},{'body':'x'*(source.MAX_BLOB+1)}):
            self.mutate=lambda p,o:o|changed if '/comments/' in p else o
            with self.subTest(fields=list(changed)),patch.object(self.tls,'get',side_effect=self.api):
                with self.assertRaises(locator.FinishLocatorError):self.make()(self.request)
        self.assertEqual([],self.decoded)

    def test_comment_edit_after_discovery_is_not_relocated_or_retried(self):
        self.comment_mode()
        def edit(path,obj):
            if '/comments/' in path and self.calls.count(path)>1:obj['body']='SYNTHETIC_EDIT'
            return obj
        self.mutate=edit
        with patch.object(self.tls,'get',side_effect=self.api):
            with self.assertRaises(locator.FinishLocatorError):self.make()(self.request)
        self.assertEqual(2,self.calls.count(self.prefix+'/issues/comments/8'))
        self.assertEqual([],self.decoded)

    def test_blob_ref_change_truncated_duplicate_link_and_malformed_native_data_stop(self):
        for mode in ('ref','truncated','duplicate','symlink','submodule','commit','size','base64','git-hash'):
            self.calls.clear()
            def mutate(path,obj):
                if mode=='ref' and '/git/ref/' in path and self.calls.count(path)>1:obj['object']['sha']='e'*40
                if mode=='truncated' and '/git/trees/' in path:obj['truncated']=True
                if mode=='duplicate' and '/git/trees/' in path:obj['tree'].append(obj['tree'][0])
                if mode=='symlink' and '/git/trees/' in path:obj['tree'][0]['mode']='120000'
                if mode=='submodule' and '/git/trees/' in path:obj['tree'][0]['type']='commit'
                if mode=='commit' and '/git/commits/' in path:obj['sha']='e'*40
                if '/git/blobs/' in path:
                    if mode=='size':obj['size']=True
                    if mode=='base64':obj['content']='not base64!'
                    if mode=='git-hash':obj['content']=base64.b64encode(b'x'*obj['size']).decode()
                return obj
            self.mutate=mutate
            with self.subTest(mode=mode),patch.object(self.tls,'get',side_effect=self.api):
                with self.assertRaises(locator.FinishLocatorError):self.make()(self.request)
        self.assertEqual([],self.decoded)

    def test_pinned_fresh_read_detects_branch_change_after_initial_ref_barrier(self):
        def move(path,obj):
            if '/git/ref/' in path and self.calls.count(path)>2:obj['object']['sha']='e'*40
            return obj
        self.mutate=move
        with patch.object(self.tls,'get',side_effect=self.api):
            with self.assertRaises(locator.FinishLocatorError):self.make()(self.request)
        self.assertEqual([],self.decoded)

    def test_p1_session_and_diagnostic_json_cannot_be_promoted_by_a_permissive_decoder(self):
        for schema in ('P1-CICD-deploy-session-1','P2-CICD-session-1',
                       'P2-CICD-operational-1','P2-CICD-private-diagnostic-4'):
            self.body=core.encoded({'schema':schema,'status':'PASS'});self.setup_api()
            with self.subTest(schema=schema),patch.object(self.tls,'get',side_effect=self.api):
                with self.assertRaisesRegex(locator.FinishLocatorError,'NOT_COMPLETION_ORIGINAL'):
                    self.make()(self.request)
        self.assertEqual([],self.decoded)

    def test_no_decoder_no_comment_id_and_bad_location_fail_before_io(self):
        for values in ({'decode':None},{'decode':True}, {'policy':replace(self.policy,
            location=replace(self.policy.location,path='../wrong'))}, {'comment_id':8}):
            args={'policy':self.policy,'reader':self.adapter,'decode':self.decode}|values
            with self.subTest(fields=list(values)),patch.object(self.tls,'get') as get:
                with self.assertRaises(locator.FinishLocatorError):locator.NativeFinishLocator(**args)
                get.assert_not_called()
        self.comment_mode()
        with self.assertRaises(locator.FinishLocatorError):
            locator.NativeFinishLocator(policy=self.policy,reader=self.adapter,decode=self.decode)

    def test_partial_progress_or_expiry_never_read_native_source(self):
        for r in (replace(self.request,progress=Progress()),replace(self.request,deadline=self.h.mono),
                  replace(self.request,binding=replace(self.request.binding,action='restore'))):
            with patch.object(self.tls,'get') as get,self.assertRaises(locator.FinishLocatorError):self.make()(r)
            get.assert_not_called()

    def test_missing_actual_native_fields_are_not_filled_from_current_request(self):
        self.body_data.pop('progress_key');self.body=core.encoded(self.body_data);self.setup_api()
        with patch.object(self.tls,'get',side_effect=self.api):
            with self.assertRaises(locator.FinishLocatorError):self.make()(self.request)

    def test_decoder_wrong_run_progress_future_or_error_is_consumed_without_secret_output(self):
        for mode in ('run','progress','future','untyped','exception','late'):
            def decode(r,o):
                f=self.decode(r,o)
                if mode=='run':return replace(f,run_id='synthetic-wrong')
                if mode=='progress':return replace(f,progress_key='f'*64)
                if mode=='future':return replace(f,observed_at=self.h.now+1)
                if mode=='untyped':return {'approved':True}
                if mode=='late':self.h.mono=r.deadline;return f
                raise ValueError(SENSITIVE)
            native=self.make(decode);old=self.h.mono
            with self.subTest(mode=mode),patch.object(self.tls,'get',side_effect=self.api):
                with self.assertRaises(locator.FinishLocatorError) as error:native(self.request)
                self.assertNotIn(SENSITIVE,str(error.exception))
                with self.assertRaisesRegex(locator.FinishLocatorError,'CONSUMED'):native(self.request)
            self.h.mono=old

    def test_native_io_exception_fixed_error_and_no_retry(self):
        native=self.make()
        with patch.object(self.tls,'get',side_effect=OSError(SENSITIVE)) as get:
            with self.assertRaisesRegex(locator.FinishLocatorError,'UNVERIFIED') as error:native(self.request)
            self.assertEqual(1,get.call_count);self.assertNotIn(SENSITIVE,str(error.exception))
            with self.assertRaisesRegex(locator.FinishLocatorError,'CONSUMED'):native(self.request)
            self.assertEqual(1,get.call_count)

    def test_private_repository_uses_its_existing_explicit_reader_and_denies_other_repo(self):
        credential=GitHubReadCredential('SYNTHETIC_GITHUB_SOURCE_TOKEN_000',self.repo,self.request.deadline + 1)
        with patch.object(ssl,'SSLContext',return_value=self.context):
            tls=PrivateGitHubTLS(credential=credential,ca_pem=b'CA',endpoint_ip='192.0.2.10',monotonic=lambda:1)
        self.adapter=PrivateGitHubOriginalAdapter(tls);self.data[self.prefix]['private']=True
        with patch.object(tls,'get',side_effect=self.api):record=self.make()(self.request)
        self.assertEqual(self.repo,record.grant.original_pin.repository)
        self.policy=replace(self.policy,location=replace(self.policy.location,
            repository=replace(self.repo,repository_id=999)))
        with patch.object(tls,'get') as get:
            with self.assertRaises(locator.FinishLocatorError):self.make()(self.request)
            get.assert_not_called()

    def test_locator_to_collector_to_parent_finish_authentication_without_new_raw_format(self):
        with patch.object(ssl,'SSLContext',return_value=self.context):
            control=source.GitHubOriginalAdapter(source.PublicGitHubTLS(ca_pem=b'CA',endpoint_ip='192.0.2.10',monotonic=lambda:1))
        def ctrl(r,p):
            ref,rev,repo_id,creator,_=pin_identity(p)
            return source.SourceOriginal(r,ref,rev,repo_id,creator,b'SYNTHETIC_CONTROL',())
        native=self.make();reads=runtime.SourceReaders(self.adapter,control)
        capture=collector.AuthenticatedFinishCollector(policy=self.policy,readers=reads,
            credential=self.request.credential,progress=lambda:self.progress,locate=native,max_seconds=2,
            wall=lambda:self.h.now,monotonic=lambda:self.h.mono)
        slot=runtime.FinishAdoptionSlot(self.h.binding)
        finish=runtime.DeferredFinish(self.h.binding,self.request.credential,lambda:self.progress,slot,reads,
            lambda:self.h.now,lambda:self.h.mono,capture)
        with patch.object(control,'blob',side_effect=ctrl) as controls,patch.object(self.tls,'get',side_effect=self.api),\
             patch.object(worker,'run_once',side_effect=lambda work,**kw:work()):
            value=finish(self.request)
        self.assertEqual('EXCLUSIVE_ACCESS_ENDED',value.disposition)
        self.assertEqual(self.progress.key,value.progress_key);self.assertTrue(slot.installed)
        self.assertEqual(4,controls.call_count);self.assertEqual(1,len(self.decoded))
        finish.close();capture.close();native.close()
        self.assertIsNone(native.reader);self.assertIsNotNone(self.adapter._transport)


if __name__=='__main__':unittest.main()
