"""SYNTHETIC values only; fake SSH -> real local Linux helper/IPC, never SSH."""
import copy
from dataclasses import replace
import json
import os
from pathlib import Path
import socket
import struct
import subprocess
import sys
import threading
import time
import unittest
from unittest.mock import Mock, patch

import delivery_crosshost_ingress as ingress
import delivery_crosshost_pipe as pipes
import delivery_job_rendezvous as rendezvous
import delivery_job_site as declaration
import delivery_ssh_bridge as bridge
import delivery_unix_ingress as unix
import test_delivery_unix_ingress as fixture

REAL_POPEN = subprocess.Popen
HELPER = Path(__file__).with_name('delivery_ssh_bridge_helper.py')
TOKEN = 'SYNTHETIC_ROSA_TOKEN_NOT_REAL'


def config():
    return bridge.SshBridgeConfig('/synthetic/ssh', '192.0.2.1', 22, 'synthetic',
        'SYNTHETIC-runner', '/synthetic/known_hosts', '/synthetic/key',
        '/synthetic/python', '/synthetic/helper.py', 1001, 3)


def credentials(request, deadline):
    return TOKEN, [{'repository':r, 'token':'SYNTHETIC_source_token_NOT_REAL'} for r in request['repositories']]


class BridgePolicyTests(unittest.TestCase):
    def test_configuration_is_inert_no_clock_process_network_or_site_assignment(self):
        clock=Mock(side_effect=AssertionError('NO_CLOCK'))
        with patch.object(subprocess,'Popen', side_effect=AssertionError('NO_PROCESS')), \
             patch.object(socket,'socket', side_effect=AssertionError('NO_SOCKET')):
            value=bridge.SshCredentialBridge(config(), deadline=10, monotonic=clock)
        clock.assert_not_called();self.assertIsNone(declaration.SITE)
        self.assertFalse(value.supply_attempted);self.assertNotIn('192.0.2.1',repr(value))

    def test_exact_ssh_policy_disables_config_agent_proxy_mux_forwarding_and_password(self):
        args=config().argv()
        for option in ('StrictHostKeyChecking=yes','BatchMode=yes','IdentityAgent=none',
            'ProxyCommand=none','ProxyJump=none','ClearAllForwardings=yes',
            'ControlPath=none','ControlMaster=no','UpdateHostKeys=no',
            'PasswordAuthentication=no','KbdInteractiveAuthentication=no','PermitLocalCommand=no'):
            self.assertIn(option,args)
        self.assertEqual(['/synthetic/ssh','-F','/dev/null','-T','-a','-x'],args[:6])
        self.assertEqual(['--','192.0.2.1','/synthetic/python','-I','/synthetic/helper.py','1001','3'],args[-7:])
        self.assertNotIn(TOKEN, ' '.join(args));self.assertNotIn('\0',' '.join(args))

    def test_no_shell_tokens_options_dns_scoped_ip_root_or_invalid_numbers(self):
        for name,values in {'ssh_binary':['ssh','/a/../b','/a b','/%h','/~user/x','/a;id'],
            'identity_file':['/a$(id)','/a\nkey'], 'user':['-oProxyCommand=x','root','a;id'],
            'host_ip':['runner.example','192.0.2.01','fe80::1%eth0','-x'],
            'host_key_alias':['-x','x y','x%h'], 'port':[True,0,65536],
            'listener_uid':[True,0,0xFFFFFFFF], 'remote_timeout_seconds':[True,0,2147484]}.items():
            for value in values:
                with self.subTest(name=name,value=value), self.assertRaises(bridge.BridgeError):
                    replace(config(),**{name:value}).argv()

    def test_public_receipts_bind_entire_request_and_do_not_contain_credentials(self):
        request=fixture.request();before=bridge._receipt('ready',request)
        for field in ('nonce','binding_key','run_id','repositories'):
            changed=copy.deepcopy(request)
            if field=='nonce':changed[field]='3'*64
            elif field=='repositories':changed[field]=['SYNTHETIC/other']
            else:changed['target'][field]='4'*64 if field=='binding_key' else '555'
            self.assertNotEqual(before,bridge._receipt('ready',changed))
        self.assertNotIn(TOKEN.encode(),before)
        self.assertNotEqual(before,bridge._receipt('forwarded',request))

    def test_failure_consumes_client_without_supplier_or_spawn(self):
        value=bridge.SshCredentialBridge(config(),deadline=10,monotonic=lambda:10)
        supplier=Mock()
        with patch.object(subprocess,'Popen') as start:
            with self.assertRaises(bridge.BridgeError):value.send_from_supplier(fixture.request(),provide_credentials=supplier)
            with self.assertRaisesRegex(bridge.BridgeError,'CONSUMED'):
                value.send_from_supplier(fixture.request(),provide_credentials=supplier)
        supplier.assert_not_called();start.assert_not_called();self.assertFalse(value.supply_attempted)

    def test_cleanup_interrupt_during_terminate_or_wait_still_kills_and_reaps_exact_child(self):
        for where in ('terminate','wait'):
            for exc in (KeyboardInterrupt(TOKEN),SystemExit(TOKEN),OSError(TOKEN)):
                proc=Mock();proc.poll.side_effect=[None,0]
                if where=='terminate':proc.terminate.side_effect=exc
                else:proc.wait.side_effect=[exc,0]
                result=bridge._reap_owned(proc,unix._Budget(20,lambda:10))
                self.assertEqual(isinstance(exc,Exception),result)
                proc.kill.assert_called_once();self.assertEqual(1,proc.wait.call_args.kwargs['timeout'])
        for exc in (KeyboardInterrupt(TOKEN),OSError(TOKEN)):
            value=Mock();value.close.side_effect=exc
            self.assertFalse(bridge._clean(value))

    def test_remote_ceiling_can_only_shrink_without_renewing_original_budget(self):
        self.assertEqual('1',config().argv(1)[-1])
        for v in (True,0,4):
            with self.assertRaises(bridge.BridgeError):config().argv(v)


@unittest.skipUnless(sys.platform=='linux' and hasattr(os,'geteuid') and os.geteuid()>0,
                     'real local helper/IPC requires nonroot Linux')
class BridgeLinuxTests(unittest.TestCase):
    def setUp(self):
        self.request=fixture.request();self.request['nonce']=os.urandom(32).hex()
        self.deadline=time.monotonic()+5;self.procs=[];self.seen=[]
        self.listener=fixture.REAL_SOCKET(socket.AF_UNIX,socket.SOCK_STREAM)
        self.listener.bind(rendezvous.abstract_address(self.request));self.listener.listen(16)
        self.listener.settimeout(3);self.addCleanup(self.listener.close)
        self.cfg=replace(config(),listener_uid=os.geteuid())

    def tearDown(self):
        for proc in self.procs:
            if proc.poll() is None:proc.kill();proc.wait(timeout=2)

    def start(self,args,**kwargs):
        self.seen.append((args,kwargs))
        self.assertEqual(self.cfg.argv(),args);self.assertFalse(kwargs['shell'])
        self.assertEqual({'LANG':'C','LC_ALL':'C'},kwargs['env'])
        self.assertEqual(subprocess.DEVNULL,kwargs['stderr']);self.assertTrue(kwargs['close_fds'])
        # The only launched child is this local Python helper, never OpenSSH.
        proc=REAL_POPEN([sys.executable,'-I',str(HELPER),str(self.cfg.listener_uid),str(self.cfg.remote_timeout_seconds)],**kwargs)
        self.procs.append(proc);return proc

    def client(self):return bridge.SshCredentialBridge(self.cfg,deadline=self.deadline,monotonic=time.monotonic)

    def test_actual_local_helper_ready_supplier_frame_eof_and_exit(self):
        value=self.client();supplier=Mock(side_effect=credentials)
        with patch.object(subprocess,'Popen',side_effect=self.start):
            self.assertTrue(value.send_from_supplier(self.request,provide_credentials=supplier))
        self.assertTrue(value.supply_attempted);supplier.assert_called_once()
        self.assertEqual(self.deadline,supplier.call_args.args[1]);self.assertEqual(1,len(self.procs))
        self.assertEqual(0,self.procs[0].returncode)
        sock,_=self.listener.accept()
        with sock:
            received=b''
            while True:
                chunk=sock.recv(4096)
                if not chunk:break
                received+=chunk
        self.assertEqual(ingress.encode_frame(self.request,rosa_token=credentials(self.request,0)[0],
            source_tokens=credentials(self.request,0)[1]),received)
        with self.assertRaisesRegex(bridge.BridgeError,'CONSUMED'):
            value.send_from_supplier(self.request,provide_credentials=supplier)

    def test_listener_uid_mismatch_before_any_supply(self):
        self.cfg=replace(self.cfg,listener_uid=os.geteuid()+1);value=self.client();supplier=Mock()
        with patch.object(subprocess,'Popen',side_effect=self.start), self.assertRaises(bridge.BridgeError):
            value.send_from_supplier(self.request,provide_credentials=supplier)
        supplier.assert_not_called();self.assertFalse(value.supply_attempted);self.assertIsNotNone(self.procs[0].poll())

    def test_supplier_exception_interrupt_or_late_return_consumed_and_child_reaped(self):
        for mode in ('exception','interrupt','late'):
            value=self.client()
            def supply(*args):
                if mode=='exception':raise ValueError(TOKEN)
                if mode=='interrupt':raise KeyboardInterrupt(TOKEN)
                value._budget.limit=time.monotonic()-1
                return credentials(*args)
            with self.subTest(mode=mode), patch.object(subprocess,'Popen',side_effect=self.start):
                with self.assertRaises(KeyboardInterrupt if mode=='interrupt' else bridge.BridgeError) as e:
                    value.send_from_supplier(self.request,provide_credentials=supply)
                self.assertNotIn(TOKEN,str(e.exception));self.assertTrue(value.supply_attempted)
                self.assertIsNotNone(self.procs[-1].poll())

    def test_spawn_failure_closes_all_four_pipes_and_sanitizes(self):
        created=[];original=os.pipe
        def pipe():
            result=original();created.extend(result);return result
        supplier=Mock();value=self.client()
        with patch.object(os,'pipe',side_effect=pipe), \
             patch.object(subprocess,'Popen',side_effect=OSError(TOKEN)):
            with self.assertRaises(bridge.BridgeError) as e:value.send_from_supplier(self.request,provide_credentials=supplier)
        self.assertEqual(4,len(created));supplier.assert_not_called();self.assertNotIn(TOKEN,str(e.exception))
        for fd in created:
            with self.assertRaises(OSError):os.fstat(fd)

    def malicious(self,code):
        def start(args,**kwargs):
            proc=REAL_POPEN([sys.executable,'-I','-c',code],**kwargs);self.procs.append(proc);return proc
        return start

    def test_banner_wrong_ready_partial_ready_or_eof_blocks_supplier(self):
        ready=bridge._receipt('ready',self.request)
        for output in (b'Warning: no',ready[:-1],bridge._receipt('ready',fixture.request()),b''):
            value=self.client();supplier=Mock()
            code='import os;os.read(0,8192);os.write(1,'+repr(output)+')'
            with self.subTest(output=output[:8]), patch.object(subprocess,'Popen',side_effect=self.malicious(code)):
                with self.assertRaises(bridge.BridgeError):value.send_from_supplier(self.request,provide_credentials=supplier)
            supplier.assert_not_called();self.assertFalse(value.supply_attempted)

    def test_missing_or_bad_ack_trailing_output_or_nonzero_rc_not_success(self):
        ready=bridge._receipt('ready',self.request);ack=bridge._receipt('forwarded',self.request)
        for tail,rc in ((b'',0),(ack+b'x',0),(ack,7),(ack[:-1],0)):
            value=self.client();supplier=Mock(side_effect=credentials)
            code='import os;os.read(0,8192);os.write(1,'+repr(ready)+');'+\
                '\nwhile os.read(0,4096): pass\nos.write(1,'+repr(tail)+');raise SystemExit('+str(rc)+')'
            with self.subTest(tail=tail[:8],rc=rc), patch.object(subprocess,'Popen',side_effect=self.malicious(code)):
                with self.assertRaises(bridge.BridgeError):value.send_from_supplier(self.request,provide_credentials=supplier)
            self.assertTrue(value.supply_attempted);supplier.assert_called_once()

    def test_silent_child_deadline_kills_and_reaps_without_supply(self):
        value=bridge.SshCredentialBridge(self.cfg,deadline=time.monotonic()+.15,monotonic=time.monotonic)
        supplier=Mock();start=time.monotonic()
        with patch.object(subprocess,'Popen',side_effect=self.malicious('import time;time.sleep(30)')):
            with self.assertRaises(bridge.BridgeError):value.send_from_supplier(self.request,provide_credentials=supplier)
        self.assertLess(time.monotonic()-start,2);supplier.assert_not_called();self.assertIsNotNone(self.procs[0].poll())

    def test_helper_invalid_configuration_closes_both_adopted_pipe_ends(self):
        r,w=os.pipe();r2,w2=os.pipe()
        try:
            with self.assertRaises(bridge.BridgeError):
                bridge.serve_owned_pipes(r,w2,listener_uid=0,timeout_seconds=2,monotonic=time.monotonic)
            for fd in (r,w2):
                with self.assertRaises(OSError):os.fstat(fd)
        finally:os.close(w);os.close(r2)

    def test_helper_no_eof_or_trailing_frame_never_forwards_secret(self):
        for tail in (b'extra',None):
            r,w=os.pipe();r2,w2=os.pipe();errors=[]
            def serve():
                try:bridge.serve_owned_pipes(r,w2,listener_uid=os.geteuid(),timeout_seconds=2,monotonic=time.monotonic)
                except BaseException as e:errors.append(e)
            t=threading.Thread(target=serve);t.start()
            os.write(w,bridge._packet({'schema':bridge.SCHEMA,'request':self.request}))
            self.assertEqual(bridge._receipt('ready',self.request),os.read(r2,8192))
            frame=ingress.encode_frame(self.request,rosa_token=TOKEN,source_tokens=credentials(self.request,0)[1])
            os.write(w,frame+(tail or b''))
            if tail is not None:os.close(w)
            t.join(3)
            if tail is None:os.close(w)
            os.close(r2)
            self.assertFalse(t.is_alive());self.assertEqual(1,len(errors))
            sock,_=self.listener.accept()
            with sock:self.assertEqual(b'',sock.recv(4096))

    def test_partial_io_exact_reads_and_no_eof_hold_original_deadline(self):
        class Chunks:
            def __init__(self):self.calls=0
            def read(self,n,deadline):self.calls+=1;return b'a'
        reader=Chunks();b=unix._Budget(time.monotonic()+1,time.monotonic)
        self.assertEqual(b'aaaa',bridge._exact(reader,4,b));self.assertEqual(4,reader.calls)
        with self.assertRaises(bridge.BridgeError):bridge._eof(reader,b)

    def test_full_local_helper_to_existing_core_parent_driver_and_finish(self):
        import contextlib,io
        import delivery_job as job
        import delivery_job_handoff as handoff
        import delivery_runtime_site as runtime
        import delivery_site as site
        import delivery_source_session as source
        import test_delivery_job_rendezvous as existing
        from test_delivery_job import CONTEXT,RUNNER
        f=existing.RendezvousLinuxTests('runTest');f.setUp();self.addCleanup(f.doCleanups)
        self.cfg=replace(self.cfg,listener_uid=os.geteuid())
        clients=[];threads=[];errors=[]
        def publish(request,deadline):
            # Real deployment must schedule independently: waiting synchronously
            # here could deadlock large socket writes before parent accept.
            def send():
                try:
                    value=self.client();clients.append(value)
                    value.send_from_supplier(request,provide_credentials=lambda *_:
                        (f.f.credential.token,[{'repository':f.s.repo.owner+'/'+f.s.repo.name,'token':fixture.fixture.TOKEN}]))
                except BaseException as exc:errors.append(exc)
            thread=threading.Thread(target=send);threads.append(thread);thread.start()
        wrapped,owner=f.wrapped(publish)
        original=runtime.RuntimeAuthorityFactory
        def factory(*args,**kw):return original(*args,**kw,wall=lambda:f.f.h.now,monotonic=f.clock)
        def build(config,*,credential,authority_factory):return f.f.app(factory=authority_factory)
        out=io.StringIO()
        with patch.object(subprocess,'Popen',side_effect=self.start), \
             patch.object(source,'pipe_identity',new=handoff._identity), \
             patch.object(runtime,'RuntimeAuthorityFactory',side_effect=factory), \
             patch.object(site,'build_application',side_effect=build),contextlib.redirect_stdout(out):
            rc=job.main(['deploy','--execute'],declaration=job.JobSite(RUNNER,wrapped),context=CONTEXT,
                revision=lambda:CONTEXT.workflow_sha,identity=lambda:RUNNER)
            for thread in threads:thread.join(6)
        self.assertTrue(threads);self.assertTrue(all(not t.is_alive() for t in threads));self.assertEqual([],errors)
        result=json.loads(out.getvalue())
        self.assertEqual((0,'SESSION_COMPLETED','RELEASED'),(rc,result['status'],result['result']['fence']),result)
        self.assertTrue(owner._closed);self.assertTrue(clients[0].supply_attempted)
        self.assertIsNone(declaration.SITE);self.assertNotIn(TOKEN,out.getvalue())


if __name__=='__main__':unittest.main()
