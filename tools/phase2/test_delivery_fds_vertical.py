"""SYNTHETIC full PR150 path; local POSIX files/helper/IPC only on Linux.

HTTP/source APIs and application file loader are fixture boundaries. No real
server, credentials, operational grant, remote SSH, dispatch or deployment.
Production selector, placement, native original materializer, archive parser,
source resolvers, actor/supplier, parent/session and finish observation execute.
"""
import contextlib
from dataclasses import replace
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

import delivery_adapter as core
import delivery_fds_baseline_archive as archive
import delivery_fds_management_route as management
import delivery_fds_operating as operating
import delivery_fds_operating_policy as policy
import delivery_fds_prepared_environment as prepared
import delivery_fds_private_baseline as private
import delivery_fds_producer_inputs as producer
import delivery_fds_run_inputs as placement
import delivery_fds_site_inputs as profile
import delivery_finish_observation as observation
import delivery_job as job
import delivery_job_handoff as handoff
import delivery_job_site as declaration
import delivery_oauth_login as login
import delivery_private_source as private_source
import delivery_runtime_site as runtime
import delivery_site as site
import delivery_source_session as source
import delivery_token_request as issuance
import delivery_transport as wire
from delivery_adopted_authority import pin_identity
from delivery_github_source import CommentPin, SourceOriginal
from delivery_platform_readback import ExpectedSubject
from delivery_token_materializer import NativePlanSelection
import test_delivery_actor_channel as actors
import test_delivery_fds_baseline_archive as archives
import test_delivery_fds_operating as originals
import test_delivery_oauth_login as oauth
import test_delivery_token_request as tokens
import test_delivery_transport as http
from test_delivery_job import CONTEXT
from test_delivery_ready_pull import REAL_SOCKET, REAL_POPEN, HELPER


@unittest.skipUnless(sys.platform=='linux' and hasattr(os,'geteuid') and os.geteuid()>0,
                     'nonroot Linux local files/helpers/IPC; no operational Runtime')
class VerticalOperatingTests(unittest.TestCase):
    def exercise(self,mode='success'):
        f=originals.AdoptedSourceConnectionTests('runTest');f.setUp();self.addCleanup(f.doCleanups)
        t=tokens.TokenRequestTests('runTest');t.setUp();self.addCleanup(t.doCleanups)
        base=f.f.base;b=base.h.binding;clock=f.f.session.now;end=base.h.mono+5
        def patched(obj,name,value):
            p=patch.object(obj,name,value);p.start();self.addCleanup(p.stop)
        facts=f.plan.issuer.facts
        values=dict(API_ORIGIN=b.api_origin,CLUSTER_UUID=b.cluster_id,NAMESPACE_UID=b.namespace_uid,
            SUBJECT=t.claims['sub'],SUBJECT_UID=t.target.uid,ISSUER=t.target.issuer,AUDIENCE=t.target.audience,
            CA_PEM=http.CA,CA_PEM_SHA256=b.ca_sha256,SOURCE_COMMIT=b.source_commit,
            CONTRACT_SHA256=b.contract_sha256,RENDERED_SHA256=b.rendered_sha256,
            ROUTE_HOST=b.route_host,IMAGES=b.images)
        for name,value in values.items():patched(profile,name,value)
        patched(policy,'ACTOR',f.plan.approval.principal)
        patched(policy,'CONTROL_PIN',f.plan.approval.control_pin)
        patched(operating,'ISSUANCE_OPERATOR','synthetic-user')
        patched(operating.connection,'API_ENDPOINT_IP','192.0.2.10')
        patched(management,'EXPECTED_HELPER_UID',os.geteuid())
        repo=f.f.repo
        for name,value in dict(SUPPLY_PIN=f.plan.producer.grant.original_pin,
            SUPPLY_SHA256=b.supply_receipt_sha256,SUPPLIED_IMAGES=b.images,
            RENDER_SOURCE_COMMIT=b.source_commit,PRODUCER_REPOSITORY=repo,
            TBS=replace(producer.TBS,repository=repo),PUSH=replace(producer.PUSH,repository=repo)).items():
            patched(producer,name,value)
        tmp=tempfile.TemporaryDirectory(prefix='p46-vertical-',dir=Path.home())
        self.addCleanup(tmp.cleanup);root=Path(tmp.name)
        (root/'inputs').mkdir(mode=0o700);template=root/'inputs'/'prepared-SYNTHETIC'
        template.mkdir(mode=0o700);staging=root/'staging';staging.mkdir(mode=0o700)
        patched(prepared,'ROOT',str(root));patched(prepared,'INPUT_TEMPLATE_ROOT',str(template))
        patched(prepared,'SOURCE_ROOT',str(root/'source'));patched(prepared,'STORE_ROOT',str(root/'state'))
        data=dict(zip(archive.OBJECT_FILES,f.plan.baseline.objects))
        data.update({n:b'SYNTHETIC_NOT_AUTHORITY\n' for n in archive.NOTES})
        raw=archives.archive(data);baseline_file=private.PrivateBaselineFile('SYNTHETIC-baseline.tgz',core.digest(raw))
        for path,content in ((template/'rendered.yaml',base.h.render),(template/'contract.yaml',base.h.contract),
                             (staging/baseline_file.name,raw)):
            path.write_bytes(content);path.chmod(0o600)
        metadata=b'SYNTHETIC_EXPLICIT_NEW_BASELINE_ASSOCIATION'
        baseline_grant=replace(f.plan.baseline.grant,original_pin=CommentPin(repo,46,1234,5678,
            '2026-01-01T00:00:00Z',core.digest(metadata)))
        config=replace(base.config(),input_root=placement.run_input_root(CONTEXT))
        adoption,pins=producer.connect_reviewed_producer(config,grant=f.plan.producer.grant,
            previous_images=f.plan.producer.facts.previous_images)
        selected=NativePlanSelection(f.plan.approval,
            replace(f.plan.issuer.grant,principal=t.claims['sub']),adoption,None,
            ExpectedSubject(t.claims['sub'],t.target.uid,('system:authenticated',)),
            t.target,http.CA,'192.0.2.10',facts.rbac_reference,facts.route_reference)
        files=placement.RunInputFiles(str(staging),baseline_file)
        supplied=operating.build_observed_run_inputs(CONTEXT,'deploy',selection=selected,
            finish_scope=f.f.session.wire._finish.scope_grant,baseline_grant=baseline_grant,
            baseline_root=archives.ROOT,input_files=files,expected_peer=f.selected.build._parent._peer,
            routes=f.selected.build._parent._routes,budget_seconds=5,finalize=True,
            deadline=end,monotonic=clock,wall=lambda:base.h.now)
        stages=[];inners=[];native_reads=[]
        def select(context,command):
            stages.append('SELECT');self.assertEqual(CONTEXT,context)
            return operating.build_observed_run_inputs(context,command,selection=selected,
                finish_scope=supplied['finish_scope'],baseline_grant=baseline_grant,
                baseline_root=archives.ROOT,input_files=files,expected_peer=supplied['expected_peer'],
                routes=supplied['routes'],budget_seconds=5,finalize=True,
                deadline=end,monotonic=clock,wall=lambda:base.h.now)
        def comment(reader,request,pin):
            self.assertEqual(baseline_grant.original_pin,pin);stages.append('BASELINE_METADATA')
            ref,rev,rid,creator,_=pin_identity(pin)
            return SourceOriginal(request,ref,rev,rid,creator,metadata,())
        patched(private_source.PrivateGitHubOriginalAdapter,'comment',comment)
        def native_get(transport,path,deadline):
            self.assertFalse(transport._closed);native_reads.append(path)
            if path==f'/repos/{repo.owner}/{repo.name}':
                obj={'id':repo.repository_id,'owner':{'id':repo.owner_id},'private':True,
                     'full_name':repo.owner+'/'+repo.name}
            else:
                pin=next(p for p in pins if f'/runs/{p.run.run_id}' in path);r=pin.run
                obj={'id':r.run_id,'run_attempt':r.attempt,'workflow_id':r.workflow_id,
                    'head_sha':r.workflow_commit,'path':r.workflow_path,'event':r.event,
                    'head_branch':r.branch,'status':'completed','conclusion':'success',
                    'repository':{'id':repo.repository_id},'head_repository':{'id':repo.repository_id}}
                if '/jobs?' in path:
                    obj={'total_count':1,'jobs':[{'id':pin.job_id,'name':pin.job_name,
                        'run_id':r.run_id,'head_sha':r.workflow_commit,'status':'completed',
                        'conclusion':'success','steps':[{'number':n,'name':name,'status':'completed',
                            'conclusion':'skipped' if mode=='native_failure' else 'success'} for n,name in pin.steps]}]}
            return core.encoded(obj),obj
        patched(private_source.PrivateGitHubTLS,'get',native_get)
        original_assembly=operating.assemble_observed_native_job
        def assemble(**kwargs):
            value=original_assembly(**kwargs);inners.append(value)
            stages.append('ASSEMBLE');return value
        original_copy=placement.prepare_run_inputs
        def copy(*args,**kwargs):
            original_copy(*args,**kwargs);stages.append('FILES_READY')
        output=Path(placement.run_input_root(CONTEXT))
        if mode=='bad_input':(staging/baseline_file.name).write_bytes(b'SYNTHETIC_CHANGED')
        context=t.f.context;context.wrap_socket.side_effect=lambda raw,**kw:raw
        with patch.object(wire.ssl,'SSLContext',return_value=context):
            provider=login.BasicChallengeLogin(origin=oauth.ORIGIN,ca_pem=http.CA,
                ca_sha256=core.digest(http.CA),endpoint_ip='192.0.2.20',username='synthetic-user',
                password=oauth.PASSWORD,deadline=end,monotonic=clock)
            self.addCleanup(provider.close)
            with patch.object(operating.connection,'supplier_inputs',return_value=dict(
                    endpoint_ip='192.0.2.10',port=2222,host_key_alias='synthetic-reviewed',remote_python='/usr/bin/python3')):
                supplier=operating.build_observed_login_supplier(supplied['config'],login_provider=provider,
                    repositories=tuple(r.repository for r in supplied['routes']),
                    source_tokens=[{'repository':repo.owner+'/'+repo.name,'token':'SYNTHETIC_PRIVATE_SOURCE_TOKEN_0123456789'}],
                    context=CONTEXT,workspace='/synthetic/work',remote_timeout_seconds=3,
                    operation_deadline=end,cleanup_deadline=end+5,monotonic=clock,wall=lambda:base.h.now)
        self.addCleanup(supplier.close)
        replies=[oauth.challenge(),oauth.granted(),http.response(core.encoded(actors.ActorChannelTests.user(self))),
                 http.response(core.encoded(t.response()),201),http.response(core.encoded(actors.ActorChannelTests.revoked(self)))]
        if mode=='revoke_uncertain':replies[-1]=http.response(b'{}',500)
        sockets=[http.FakeSocket(r) for r in replies];sent=[];processes=[];errors=[];captured=[]
        def socket_at_boundary(family=socket.AF_INET,kind=socket.SOCK_STREAM,proto=0,fileno=None):
            if family==socket.AF_UNIX:return REAL_SOCKET(family,kind,proto,fileno=fileno)
            self.assertEqual(socket.AF_INET,family);self.assertTrue(sockets,'UNEXPECTED_TCP')
            value=sockets.pop(0);sent.append(value);return value
        def launch(args,**kwargs):
            for secret in (oauth.PASSWORD,t.token(),'SYNTHETIC_PRIVATE_SOURCE_TOKEN_0123456789'):
                self.assertNotIn(secret,repr(args)+repr(kwargs.get('env')))
            public=args[-3].endswith('delivery_ready_pull_helper.py')
            helper=HELPER if public else HELPER.with_name('delivery_ssh_bridge_helper.py')
            command=[sys.executable,'-I',str(helper),str(os.geteuid()),'3']
            if public and mode=='bad_ready':
                code='import runpy,sys;sys.argv=sys.argv[1:];\ntry:runpy.run_path(sys.argv[0],run_name="__main__")\nexcept SystemExit:raise SystemExit(1)'
                command=[sys.executable,'-I','-c',code,str(helper),str(os.geteuid()),'3']
            proc=REAL_POPEN(command,**kwargs);processes.append(proc);return proc
        original_issue=issuance.TokenRequestIssuer.issue
        def spy(client,*args,**kwargs):
            result=original_issue(client,*args,**kwargs);captured.append(result);return result
        original_factory=runtime.RuntimeAuthorityFactory
        def factory(*args,**kwargs):return original_factory(*args,**kwargs,wall=lambda:base.h.now,monotonic=clock)
        def application(actual_config,*,credential,authority_factory):
            self.assertEqual(supplied['config'],actual_config)
            base.credential=base.p.credential=base.p.c.credential=credential
            base.p.c.providers.credential=credential
            return base.app(factory=authority_factory)
        original_persist=observation.persist;persist_calls=[]
        def persist(*args,**kwargs):
            persist_calls.append(base.p.c.sender_closes)
            if mode=='finish_failure':raise OSError('SYNTHETIC_PRIVATE_STORE_FAILURE')
            return original_persist(*args,**kwargs)
        if mode=='withdraw_control':
            base.read_hook=lambda req,pin,raw:replace(raw,body=b'SYNTHETIC_WITHDRAWN') if pin.path=='control' else raw
        def send():
            try:supplier.send()  # Concrete operating supplier must require v2.
            except BaseException as exc:errors.append(type(exc).__name__)
        thread=threading.Thread(target=send);stdout=io.StringIO()
        try:
            with patch.object(socket,'socket',side_effect=socket_at_boundary), \
                 patch.object(subprocess,'Popen',side_effect=launch), \
                 patch.object(wire.ssl,'SSLContext',return_value=context), \
                 patch.object(login.secrets,'token_hex',return_value=oauth.STATE), \
                 patch.object(issuance.TokenRequestIssuer,'issue',new=spy), \
                 patch.object(source,'pipe_identity',new=handoff._identity), \
                 patch.object(runtime,'RuntimeAuthorityFactory',side_effect=factory), \
                 patch.object(site,'build_application',side_effect=application), \
                 patch.object(observation,'persist',side_effect=persist), \
                 patch.object(operating,'assemble_observed_native_job',side_effect=assemble), \
                 patch.object(placement,'prepare_run_inputs',side_effect=copy), \
                 contextlib.redirect_stdout(stdout):
                declared=operating.declare_observed_native_job(select_inputs=select,input_files=files)
                if mode!='bad_input':thread.start()
                rc=job.main(['deploy','--execute'],declaration=declared,context=CONTEXT,
                    revision=lambda:CONTEXT.workflow_sha,identity=lambda:operating.EXPECTED_RUNNER)
                if thread.ident is not None:thread.join(7)
        finally:
            for proc in processes:
                if proc.poll() is None:proc.kill();proc.wait(timeout=2)
            for inner in inners:inner.build._owner.close()
            if thread.ident is not None:thread.join(7)
        self.assertFalse(thread.is_alive(),'SUPPLIER_THREAD_LEAK')
        self.assertTrue(all(proc.poll() is not None for proc in processes))
        result=json.loads(stdout.getvalue());self.assertIsNone(declaration.SITE)
        for secret in (oauth.PASSWORD,t.token(),'SYNTHETIC_PRIVATE_SOURCE_TOKEN_0123456789'):
            self.assertNotIn(secret,stdout.getvalue()+repr(supplier)+str(supplier.status()))
        self.assertTrue(all(i.build._owner._closed for i in inners))
        if mode=='bad_input':
            self.assertEqual(2,rc,result);self.assertFalse(output.exists())
            self.assertEqual([],sent);self.assertEqual([],processes);self.assertEqual([],base.p.c.storage.claims)
            self.assertTrue(inners[0].build._parent._plan._closed)
            self.assertFalse(provider._used);supplier.close();self.assertIsNone(provider._password)
            return
        self.assertEqual(['SELECT','ASSEMBLE','FILES_READY'],stages[:3])
        self.assertEqual({'rendered.yaml','contract.yaml',baseline_file.name},set(os.listdir(output)))
        self.assertEqual(raw,(output/baseline_file.name).read_bytes())
        if mode=='bad_ready':
            self.assertEqual(2,rc,result);self.assertEqual(['SupplyError'],errors)
            self.assertEqual([],sent);self.assertEqual([],captured);self.assertEqual([],native_reads)
            self.assertIsNone(provider._password);self.assertEqual([],base.p.c.storage.claims)
            return
        self.assertEqual(5,len(sent));self.assertFalse(sockets)
        self.assertEqual([b'GET',b'GET',b'GET',b'POST',b'DELETE'],[bytes(s.sent).split(b' ',1)[0] for s in sent])
        self.assertTrue(all(s.closed for s in sent));self.assertEqual(2,len(processes))
        self.assertTrue(supplier.status()['native_original_required']);self.assertIsNone(provider._password)
        receipt=supplier.take_receipt();self.assertIsNotNone(receipt);self.assertIsNone(supplier.take_receipt())
        self.assertEqual(captured[0].raw,receipt.raw);self.assertIsNotNone(supplier.take_login_metadata())
        self.assertIsNone(supplier.take_login_metadata());self.assertTrue(supplier.status()['local_references_released'])
        self.assertEqual('UNCONFIRMED' if mode=='revoke_uncertain' else 'ACKNOWLEDGED',
                         supplier.status()['actor_server_revocation'])
        if mode=='revoke_uncertain':self.assertEqual(['SupplyError'],errors)
        else:self.assertFalse(errors)
        if mode in ('success','revoke_uncertain'):
            self.assertEqual((0,'RELEASED'),(rc,result['result']['fence']),result)
            self.assertEqual(1,base.p.c.storage.complete_calls);self.assertEqual(1,len(f.saved))
            self.assertEqual(6,len(json.loads(f.saved[0])['observations']))
            self.assertTrue(all(n>0 for n in persist_calls));self.assertTrue(native_reads)
            self.assertIn('BASELINE_METADATA',stages)
            self.assertFalse(supplier.status()['job_accepted'])
        elif mode in ('native_failure','withdraw_control'):
            self.assertEqual(2,rc,result);self.assertEqual([],base.p.c.storage.claims)
            self.assertEqual([],base.h.requests);self.assertEqual([],f.saved)
        elif mode=='finish_failure':
            self.assertEqual((2,'RETAINED'),(rc,result['result']['fence']),result)
            self.assertEqual(0,base.p.c.storage.complete_calls);self.assertEqual([],f.saved)
            # Recovery boundary: no retry/attempt2/new RUN may clear this fence.
            count=len(base.h.requests);out=base.app().run('restore')
            self.assertEqual(('STOPPED','OPERATIONAL_PREPARATION_FAILED'),
                             (out['status'],out['error_code']))
            self.assertEqual(count,len(base.h.requests));self.assertIsNotNone(base.p.c.storage.active)
            stage_count=len(stages)
            fresh=operating.declare_observed_native_job(select_inputs=select,input_files=files)
            with self.assertRaises(policy.OperatingPolicyError):fresh.build(replace(CONTEXT,attempt=2),'deploy')
            self.assertEqual(stage_count,len(stages));self.assertIsNotNone(base.p.c.storage.active)
        self.assertTrue(all(tr._closed for tr in f.f.f.transports))

    def test_full_selector_file_native_original_to_finish(self):self.exercise()
    def test_changed_baseline_stops_before_ready_and_preserves_borrowed_provider(self):self.exercise('bad_input')
    def test_bad_ready_helper_never_logs_in(self):self.exercise('bad_ready')
    def test_native_required_step_failure_denies_before_claim(self):self.exercise('native_failure')
    def test_changed_control_denies_before_claim(self):self.exercise('withdraw_control')
    def test_finish_failure_retains_fence_and_forbids_unapproved_restore(self):self.exercise('finish_failure')
    def test_uncertain_actor_revocation_keeps_private_recovery_handoffs(self):self.exercise('revoke_uncertain')


if __name__=='__main__':unittest.main()
