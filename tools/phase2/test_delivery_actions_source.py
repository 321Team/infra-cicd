"""Synthetic native API records only. No real run IDs or deployment values."""
import copy
from dataclasses import replace
import socket
import ssl
import unittest
from unittest.mock import Mock,patch

import delivery_adapter as core
import delivery_actions_source as actions
from delivery_actions_jobs import check_jobs
from delivery_github_source import GitHubOriginalAdapter,PublicGitHubTLS,RepositoryPin
from delivery_private_source import PrivateGitHubTLS,PrivateGitHubOriginalAdapter,GitHubReadCredential
from delivery_object_reader import BearerCredential
from delivery_verifier import ReadRequest,Progress,ProducerEvidence,Provenance
from test_delivery_adapter import Harness,SENSITIVE
from test_delivery_transport import CA
from producer_bridge import CHECKER_SHA256


class NativeApiFixture:
    def api_setup(self,h,credential):
        self.h,self.credential,self.progress=h,credential,Progress()
        self.repo=RepositoryPin('synthetic-owner','synthetic-actions',123,321)
        self.workflow_sha='e'*40 if self.h.binding.source_commit!='e'*40 else 'f'*40
        self.runs=(actions.RunPin(self.repo,101,1,201,self.workflow_sha,
            '.github/workflows/synthetic-build.yml','workflow_dispatch','synthetic'),
            actions.RunPin(self.repo,102,1,202,self.workflow_sha,
            '.github/workflows/synthetic-push.yml','workflow_dispatch','synthetic'))
        self.pins=tuple(actions.StagePin(stage,'synthetic:'+stage,self.runs[0 if i<3 else 1],
            301 if i<3 else 302,'synthetic-build' if i<3 else 'synthetic-push',
            ((3,'synthetic-test-build-scan'),) if i<3 else ((4,'synthetic-push'),))
            for i,stage in enumerate(actions.STAGES))
        self.prefix='/repos/synthetic-owner/synthetic-actions'
        self.calls=[];self.hook=lambda path,obj:obj
        context=Mock(verify_mode=ssl.CERT_REQUIRED,check_hostname=True,keylog_filename=None)
        with patch.object(ssl,'SSLContext',return_value=context):
            transport=PublicGitHubTLS(ca_pem=CA,endpoint_ip='192.0.2.10',monotonic=lambda:self.h.mono)
        self.api_reader=GitHubOriginalAdapter(transport)
        p=patch.object(transport,'get',side_effect=self.response)
        self.transport_read=p.start();self.addCleanup(p.stop)

    def run_object(self,pin):
        return {'id':pin.run_id,'run_attempt':pin.attempt,'workflow_id':pin.workflow_id,
            'head_sha':pin.workflow_commit,'path':pin.workflow_path,'event':pin.event,
            'head_branch':pin.branch,'status':'completed','conclusion':'success',
            'repository':{'id':self.repo.repository_id},'head_repository':{'id':self.repo.repository_id}}

    def jobs_object(self,pin):
        p=next(p for p in self.pins if p.run==pin)
        return {'total_count':1,'jobs':[{'id':p.job_id,'name':p.job_name,'run_id':pin.run_id,
            'head_sha':pin.workflow_commit,'status':'completed','conclusion':'success',
            'steps':[{'number':no,'name':name,'status':'completed','conclusion':'success'}
                     for no,name in p.steps]}]}

    def response(self,path,deadline):
        self.calls.append((path,deadline))
        if path==self.prefix:
            value={'id':123,'owner':{'id':321},'private':False,
                   'full_name':'synthetic-owner/synthetic-actions'}
        else:
            pin=next(p for p in self.runs if f'/runs/{p.run_id}' in path)
            allowed=(self.prefix+f'/actions/runs/{pin.run_id}',
                self.prefix+f'/actions/runs/{pin.run_id}/attempts/1',
                self.prefix+f'/actions/runs/{pin.run_id}/attempts/1/jobs?per_page=100&page=1')
            if path not in allowed:raise AssertionError('UNEXPECTED_API_PATH')
            value=self.jobs_object(pin) if '/jobs?' in path else self.run_object(pin)
        value=self.hook(path,copy.deepcopy(value))
        return core.encoded(value),value

    def request(self):return ReadRequest(self.h.binding,self.credential,self.progress,self.h.mono+30)

    def gate(self,pins=None):return actions.ActionsRunSource(reader=self.api_reader,
        pins=self.pins if pins is None else pins,binding=self.h.binding,credential=self.credential,
        progress=lambda:self.progress,monotonic=lambda:self.h.mono)


class ActionsSourceTests(NativeApiFixture,unittest.TestCase):
    def setUp(self):
        for name in ('socket','getaddrinfo','create_connection'):
            p=patch.object(socket,name,side_effect=AssertionError('NETWORK_FORBIDDEN'))
            p.start();self.addCleanup(p.stop)
        self.api_setup(Harness(),BearerCredential(SENSITIVE))

    def test_native_success_deduplicates_shared_run_and_preserves_app_sha(self):
        r=self.request();out=self.gate().inspect(r)
        self.assertIs(out.request,r);self.assertEqual(2,len(out.stages));self.assertEqual(10,len(self.calls))
        self.assertEqual(1,sum('/runs/101/attempts/1/jobs?' in p for p,_ in self.calls))
        self.assertNotEqual(self.h.binding.source_commit,self.workflow_sha)
        self.assertEqual(self.workflow_sha,out.stages[0][2])
        self.assertFalse(out.execution_authorized);self.assertFalse(out.app_lineage_authenticated)
        self.assertNotIn(SENSITIVE,repr(out))
        self.assertTrue(all(end==r.deadline for _,end in self.calls))

    def test_every_run_identity_field_and_repository_must_match(self):
        changes=({'id':True},{'run_attempt':2},{'workflow_id':999},{'head_sha':'b'*40},
            {'path':'.github/workflows/other.yml'},{'event':'push'},{'head_branch':'other'},
            {'repository':{'id':True}},{'head_repository':{'id':999}})
        for change in changes:
            self.calls.clear()
            self.hook=lambda path,obj:obj|change if '/actions/runs/' in path and '/jobs?' not in path else obj
            with self.subTest(change=change),self.assertRaises(actions.ActionsSourceError):self.gate().inspect(self.request())
            self.assertEqual(2,len(self.calls))

    def test_failure_skipped_neutral_timeout_or_in_progress_never_pass(self):
        for conclusion,status in (('failure','completed'),('skipped','completed'),('neutral','completed'),
                                  ('timed_out','completed'),('success','in_progress')):
            self.hook=lambda path,obj:obj|{'status':status,'conclusion':conclusion} if '/runs/' in path else obj
            with self.subTest(conclusion=conclusion),self.assertRaises(actions.ActionsSourceError):
                self.gate().inspect(self.request())

    def test_latest_attempt_change_after_jobs_invalidates_old_success(self):
        latest=self.prefix+'/actions/runs/101';n=0
        def changed(path,obj):
            nonlocal n
            if path==latest:
                n+=1
                if n==2:obj['run_attempt']=2
            return obj
        self.hook=changed
        with self.assertRaisesRegex(actions.ActionsSourceError,'PIN_MISMATCH'):self.gate().inspect(self.request())
        self.assertEqual(2,n)

    def test_required_job_step_identity_and_success_are_independent(self):
        mutations=(lambda j:j.update(id=999),lambda j:j.update(run_id=True),
            lambda j:j.update(name='wrong'),lambda j:j.update(head_sha='f'*40),
            lambda j:j.update(conclusion='failure'),lambda j:j['steps'][0].update(conclusion='skipped'),
            lambda j:j['steps'][0].update(name='wrong'),lambda j:j['steps'][0].update(number=True),
            lambda j:j.update(steps=[]),lambda j:j['steps'].append(copy.deepcopy(j['steps'][0])))
        for mutate in mutations:
            def changed(path,obj):
                if '/jobs?' in path:mutate(obj['jobs'][0])
                return obj
            self.hook=changed
            with self.assertRaises(actions.ActionsSourceError):self.gate().inspect(self.request())

    def test_incomplete_or_paginated_or_duplicate_jobs_are_denied(self):
        for count in (True,0,2,101):
            self.hook=lambda path,obj:obj|{'total_count':count} if '/jobs?' in path else obj
            with self.subTest(count=count),self.assertRaises(actions.ActionsSourceError):self.gate().inspect(self.request())
        def duplicate(path,obj):
            if '/jobs?' in path:obj['jobs']*=2;obj['total_count']=2
            return obj
        self.hook=duplicate
        with self.assertRaises(actions.ActionsSourceError):self.gate().inspect(self.request())

    def test_optional_skipped_job_is_allowed_but_required_skipped_is_not(self):
        data=self.jobs_object(self.runs[0]);job=data['jobs'][0]
        data['jobs'].append({'id':999,'status':'completed','conclusion':'skipped'});data['total_count']=2
        gates=((job['id'],job['name'],((3,'synthetic-test-build-scan'),)),)
        self.assertEqual(1,len(check_jobs(data,101,self.workflow_sha,gates)))
        job['steps'][0]['conclusion']='skipped'
        with self.assertRaisesRegex(ValueError,'ACTIONS_JOBS_INVALID'):check_jobs(data,101,self.workflow_sha,gates)

    def test_policy_rejects_boolean_ids_missing_stage_or_conflicting_attempt_before_io(self):
        bads=(self.pins[:-1],self.pins[:1]+(replace(self.pins[1],stage='test'),)+self.pins[2:],
            (replace(self.pins[0],job_id=True),)+self.pins[1:],
            self.pins[:1]+(replace(self.pins[1],run=replace(self.runs[0],attempt=2)),)+self.pins[2:],
            self.pins[:1]+(replace(self.pins[1],job_name='different'),)+self.pins[2:],
            self.pins[:1]+(replace(self.pins[1],steps=((3,'different'),)),)+self.pins[2:])
        for pins in bads:
            with self.subTest(pins=len(pins)),self.assertRaises(actions.ActionsSourceError):self.gate(pins)
        self.assertEqual([],self.calls)

    def test_deadline_clock_progress_and_credential_changes_deny(self):
        for mode in ('deadline','progress','clock'):
            self.h.mono=1000;self.progress=Progress();a=self.gate();r=self.request()
            def changed(path,obj):
                if mode=='deadline':self.h.mono=r.deadline
                elif mode=='progress':self.progress=Progress(responses=(b'drift',))
                else:self.h.mono=999
                return obj
            self.hook=changed
            with self.subTest(mode=mode),self.assertRaises(actions.ActionsSourceError):a.inspect(r)
        self.hook=lambda p,o:o;self.h.mono=1000;self.progress=Progress();self.calls.clear()
        with self.assertRaises(actions.ActionsSourceError):self.gate().inspect(replace(
            self.request(),credential=BearerCredential(SENSITIVE)))
        self.assertEqual([],self.calls)

    def test_transport_error_is_redacted_and_not_retried(self):
        self.transport_read.side_effect=RuntimeError(SENSITIVE)
        with self.assertRaisesRegex(actions.ActionsSourceError,'SOURCE_UNVERIFIED') as error:
            self.gate().inspect(self.request())
        self.assertNotIn(SENSITIVE,str(error.exception));self.transport_read.assert_called_once()

    def test_private_reader_keeps_one_repository_credential_scope(self):
        other=replace(self.repo,name='other')
        credential=GitHubReadCredential('SYNTHETIC_GITHUB_ONLY_TOKEN',other,2000)
        with patch.object(ssl,'SSLContext',return_value=Mock()):
            transport=PrivateGitHubTLS(credential=credential,ca_pem=CA,endpoint_ip='192.0.2.10',
                monotonic=lambda:self.h.mono)
        reader=PrivateGitHubOriginalAdapter(transport)
        with patch.object(transport,'get') as read:
            a=actions.ActionsRunSource(reader=reader,pins=self.pins,binding=self.h.binding,
                credential=self.credential,progress=lambda:self.progress,monotonic=lambda:self.h.mono)
            with self.assertRaises(actions.ActionsSourceError):a.inspect(self.request())
            read.assert_not_called()
        transport.close();credential.close()

    def test_producer_authority_rechecked_after_native_observation(self):
        b=self.h.binding;r=self.request();calls=[]
        def original(r):
            calls.append(1)
            return ProducerEvidence(r,b,Provenance('synthetic:source','v1','synthetic:actor','synthetic:adoption'),
                b.source_commit,b.contract_sha256,b.rendered_sha256,CHECKER_SHA256,b'SYNTHETIC_RECEIPT')
        wrapped=actions.ActionsCheckedProducer(source=original,gate=self.gate(),
            references=tuple(p.producer_reference for p in self.pins))
        self.assertEqual(b.source_commit,wrapped(r).source_commit);self.assertEqual(2,len(calls))
        self.assertIsNotNone(wrapped.observation)
        saved=original
        def revoked(r):
            out=saved(r)
            if len(calls)%2==0:out=replace(out,provenance=replace(out.provenance,revision='withdrawn'))
            return out
        wrapped.source=revoked
        with self.assertRaisesRegex(actions.ActionsSourceError,'PRODUCER_CHANGED'):wrapped(r)

    def test_reference_mismatch_and_close_deny_without_native_io(self):
        with self.assertRaisesRegex(actions.ActionsSourceError,'REFERENCE_MISMATCH'):
            actions.ActionsCheckedProducer(source=lambda r:None,gate=self.gate(),references=('other',)*4)
        a=self.gate();a.close();a.close()
        with self.assertRaises(actions.ActionsSourceError):a.inspect(self.request())
        self.assertEqual([],self.calls)


if __name__=='__main__':unittest.main()
