"""SYNTHETIC HTTP + real LOCAL helper/UNIX/pipe -> five-role job/finish.

OpenSSH is replaced at its process boundary; TCP/DNS are forbidden. No real
password, issuance, GitHub/API access, deployment or operating SITE is used.
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
import threading
import unittest
from unittest.mock import Mock, patch

import delivery_adapter as core
import delivery_actor_channel as actor
import delivery_finish_observation as observation
import delivery_job as job
import delivery_job_handoff as handoff
import delivery_job_parent as parent
import delivery_job_rendezvous as rendezvous
import delivery_job_site as declaration
import delivery_login_channel as login_channel
import delivery_oauth_login as login
import delivery_operating_supplier as supply
from delivery_platform_readback import ExpectedSubject
import delivery_ready_pull as ready
import delivery_runtime_site as runtime
import delivery_site as site
import delivery_source_session as source
import delivery_ssh_bridge as bridge
import delivery_token_request as issuance
import delivery_transport as wire
import test_delivery_actor_channel as actor_fixture
import test_delivery_fds_operating as source_fixture
import test_delivery_oauth_login as oauth
import test_delivery_token_request as tokens
import test_delivery_transport as http
from test_delivery_job import CONTEXT, RUNNER
from test_delivery_ready_pull import REAL_SOCKET, REAL_POPEN, HELPER
from test_delivery_ssh_bridge import config as ssh_config


@unittest.skipUnless(sys.platform=='linux' and hasattr(os,'geteuid') and os.geteuid()>0,
                     'nonroot Linux local helper/AF_UNIX test; no remote Runtime')
class LoginNativeFinishTests(unittest.TestCase):
    def exercise(self, mode='success'):
        f=source_fixture.AdoptedSourceConnectionTests('runTest'); f.setUp()
        self.addCleanup(f.doCleanups)
        t=tokens.TokenRequestTests('runTest'); t.setUp(); self.addCleanup(t.doCleanups)
        base=f.f.base
        clock=f.f.session.now
        end=base.h.mono+5
        context=t.f.context; context.wrap_socket.side_effect=lambda raw,**kw:raw
        provider=None
        with patch.object(wire.ssl,'SSLContext',return_value=context):
            provider=login.BasicChallengeLogin(origin=oauth.ORIGIN,ca_pem=http.CA,
                ca_sha256=core.digest(http.CA),endpoint_ip='192.0.2.20',
                username='synthetic-user',password=oauth.PASSWORD,
                deadline=end,monotonic=clock)
            channel=login_channel.LoginActorChannel(base.h.binding,login_provider=provider,
                expected_username='synthetic-user',target=t.target,ca_pem=http.CA,
                endpoint_ip='192.0.2.10',operation_deadline=end,
                cleanup_deadline=end+5,monotonic=clock,wall=lambda:base.h.now)
        selector=ready.selector_for(CONTEXT,base.h.binding,[f.f.repo.owner+'/'+f.f.repo.name])
        mailbox=ready.ReadyMailbox(selector,expected_uid=os.geteuid(),deadline=end,monotonic=clock)
        owner=rendezvous.UnixJobRendezvous(expected_uid=os.geteuid(),
            peer=f.selected.build._parent._peer,deadline=end,monotonic=clock,publish_ready=mailbox)
        self.addCleanup(owner.close)
        replies=[oauth.challenge(),oauth.granted(),http.response(core.encoded(actor_fixture.ActorChannelTests.user(self))),
                 http.response(core.encoded(t.response()),201),http.response(core.encoded(actor_fixture.ActorChannelTests.revoked(self)))]
        socks=[http.FakeSocket(raw) for raw in replies]
        sent=[]; captured=[]; processes=[]; errors=[]
        def sockets(family=socket.AF_INET,kind=socket.SOCK_STREAM,proto=0,fileno=None):
            if family==socket.AF_UNIX:return REAL_SOCKET(family,kind,proto,fileno=fileno)
            self.assertEqual(socket.AF_INET,family)
            self.assertTrue(socks,'UNEXPECTED_TCP_REQUEST')
            value=socks.pop(0);sent.append(value);return value
        # The hook returns the EXACT production-parsed receipt unchanged. It
        # records before RequestBoundSupplier can encode its credential frame.
        original_issue=issuance.TokenRequestIssuer.issue
        def capture(client,*args,**kwargs):
            receipt=original_issue(client,*args,**kwargs)
            captured.append(receipt)
            return receipt
        def materialize(context_,credential,*unused):
            self.assertEqual(CONTEXT,context_)
            self.assertEqual(1,len(captured),'RECEIPT_MUST_PRECEDE_CREDENTIAL_FRAME')
            receipt=captured[0]
            self.assertEqual(receipt.credential.token,credential.token)
            base.credential=base.p.credential=base.p.c.credential=credential
            base.p.c.providers.credential=credential
            facts=replace(f.plan.issuer.facts,credential_fingerprint=credential.fingerprint,
                issuer=receipt.target.issuer,audience=receipt.target.audience,
                issued_at=receipt.issued_at,expires_at=receipt.expires_at,
                requested_seconds=receipt.requested_seconds)
            subject=ExpectedSubject(t.claims['sub'],receipt.target.uid,('system:authenticated',))
            # All independent fixture grants/pins remain; only issuer's explicit
            # expected principal/facts are aligned with this synthetic credential.
            return replace(f.plan,subject=subject,issuer=replace(f.plan.issuer,
                grant=replace(f.plan.issuer.grant,principal=subject.username),facts=facts))
        plan=runtime.PreparedAuthorityPlan(binding=base.h.binding,materialize=materialize)
        self.addCleanup(plan.close)
        selected_parent=f.selected.build._parent
        builder=parent.ParentJobBuilder(config=base.config(),plan=plan,finish_slot=base.slot,
            expected_peer=selected_parent._peer,routes=selected_parent._routes,
            wire=selected_parent._wire,deadline=end,monotonic=clock,
            publish_ready=owner.publish_ready,open_channel=owner.open_channel)
        wrapped=rendezvous.OwnedRendezvousBuilder(builder,owner)
        cfg=replace(ssh_config(),listener_uid=os.geteuid(),remote_helper='/synthetic/delivery_ssh_bridge_helper.py')
        pull=ready.SshReadyPull(replace(cfg,remote_helper='/synthetic/delivery_ready_pull_helper.py'),
                               selector,deadline=end,monotonic=clock)
        forward=bridge.SshCredentialBridge(cfg,deadline=end,monotonic=clock)
        supplier=supply.MemoryOperatingSupplier(ready_pull=pull,bridge=forward,actor_channel=channel,
            source_tokens=[{'repository':f.f.repo.owner+'/'+f.f.repo.name,
                            'token':'SYNTHETIC_PRIVATE_SOURCE_TOKEN_0123456789'}])
        self.addCleanup(supplier.close)
        def launch(args,**kwargs):
            self.assertEqual(cfg.ssh_binary,args[0])
            public=args[-3].endswith('delivery_ready_pull_helper.py')
            helper=HELPER if public else HELPER.with_name('delivery_ssh_bridge_helper.py')
            if public and mode=='bad_ready_exit':
                # Run the actual local helper protocol, then change ONLY its RC.
                code='import runpy,sys;sys.argv=[sys.argv[1],sys.argv[2],sys.argv[3]];\ntry:runpy.run_path(sys.argv[0],run_name="__main__")\nexcept SystemExit:raise SystemExit(1)'
                command=[sys.executable,'-I','-c',code,str(helper),str(os.geteuid()),'3']
            else:command=[sys.executable,'-I',str(helper),str(os.geteuid()),'3']
            proc=REAL_POPEN(command,**kwargs);processes.append(proc);return proc
        if mode=='withdraw_control':
            base.read_hook=lambda req,pin,original:replace(original,body=b'SYNTHETIC_WITHDRAWN') if pin.path=='control' else original
        persist_calls=[]
        original_persist=observation.persist
        def persist(*args,**kwargs):
            persist_calls.append(base.p.c.sender_closes)
            if mode=='finish_store_failure':raise OSError('SYNTHETIC_STORE_FAILURE')
            return original_persist(*args,**kwargs)
        def issue():
            try:supplier.send()
            except BaseException as exc:errors.append(type(exc).__name__)
        original_factory=runtime.RuntimeAuthorityFactory
        def factory(*args,**kwargs):return original_factory(*args,**kwargs,wall=lambda:base.h.now,monotonic=clock)
        def build(config,*,credential,authority_factory):return base.app(factory=authority_factory)
        thread=threading.Thread(target=issue);stdout=io.StringIO()
        try:
            with patch.object(socket,'socket',side_effect=sockets),patch.object(subprocess,'Popen',side_effect=launch), \
                 patch.object(wire.ssl,'SSLContext',return_value=context), \
                 patch.object(login.secrets,'token_hex',return_value=oauth.STATE), \
                 patch.object(issuance.TokenRequestIssuer,'issue',new=capture), \
                 patch.object(source,'pipe_identity',new=handoff._identity), \
                 patch.object(runtime,'RuntimeAuthorityFactory',side_effect=factory), \
                 patch.object(site,'build_application',side_effect=build), \
                 patch.object(observation,'persist',side_effect=persist),contextlib.redirect_stdout(stdout):
                thread.start()
                rc=job.main(['deploy','--execute'],declaration=job.JobSite(RUNNER,wrapped),
                    context=CONTEXT,revision=lambda:CONTEXT.workflow_sha,identity=lambda:RUNNER)
                thread.join(7)
        finally:
            for proc in processes:
                if proc.poll() is None:proc.kill();proc.wait(timeout=2)
            owner.close()
            if thread.ident is not None:thread.join(7)
        self.assertFalse(thread.is_alive(),'SUPPLIER_THREAD_LEAK')
        self.assertTrue(all(p.poll() is not None for p in processes))
        result=json.loads(stdout.getvalue())
        for value in (oauth.PASSWORD,actor_fixture.TOKEN,t.token()):self.assertNotIn(value,stdout.getvalue())
        self.assertTrue(owner._closed);self.assertIsNone(declaration.SITE)
        if mode=='bad_ready_exit':
            self.assertEqual(2,rc,result);self.assertEqual(['SupplyError'],errors)
            self.assertEqual([],sent);self.assertEqual([],captured)
            self.assertIsNone(supplier.take_receipt())
            self.assertEqual('NOT_ATTEMPTED',supplier.status()['login'])
            self.assertEqual([],base.p.c.storage.claims)
        else:
            self.assertEqual([],errors,result)
            self.assertEqual(5,len(sent));self.assertEqual([],socks)
            self.assertEqual([b'GET',b'GET',b'GET',b'POST',b'DELETE'],[bytes(s.sent).split(b' ',1)[0] for s in sent])
            self.assertTrue(all(s.closed for s in sent))
            self.assertEqual(2,len(processes));self.assertTrue(all(p.returncode==0 for p in processes))
            self.assertIs(captured[0],supplier.take_receipt());self.assertIsNone(supplier.take_receipt())
            self.assertEqual('ACKNOWLEDGED',supplier.status()['actor_server_revocation'])
            if mode=='success':
                self.assertEqual((0,'RELEASED'),(rc,result['result']['fence']),result)
                self.assertEqual(1,base.p.c.storage.complete_calls)
                self.assertEqual(1,len(f.saved));self.assertTrue(all(x>0 for x in persist_calls))
                self.assertEqual(6,len(json.loads(f.saved[0])['observations']))
                for name in ('baseline','control','completion-scope'):self.assertIn(name,f.f.f.blobs)
            elif mode=='withdraw_control':
                self.assertEqual(2,rc,result);self.assertEqual([],base.p.c.storage.claims)
                self.assertEqual([],base.h.requests);self.assertEqual([],f.saved)
            elif mode=='finish_store_failure':
                self.assertEqual((2,'RETAINED'),(rc,result['result']['fence']),result)
                self.assertEqual(0,base.p.c.storage.complete_calls);self.assertEqual([],f.saved)
                self.assertTrue(persist_calls);self.assertTrue(all(x>0 for x in persist_calls))

    def test_login_real_local_helpers_five_role_originals_and_finish(self):self.exercise()
    def test_control_withdrawal_after_native_receipt_stops_before_claim(self):self.exercise('withdraw_control')
    def test_finish_store_failure_after_sender_close_retains_fence(self):self.exercise('finish_store_failure')
    def test_bad_ready_helper_exit_never_logs_in_or_issues(self):self.exercise('bad_ready_exit')


if __name__=='__main__':unittest.main()
