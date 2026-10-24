"""SYNTHETIC public requests. Local helper/Unix IPC only; never real SSH."""
import copy
from dataclasses import replace
import json
import os
from pathlib import Path
import pickle
import socket
import subprocess
import sys
import threading
import time
import unittest
from unittest.mock import Mock, patch

import delivery_ready_pull as ready
import delivery_ssh_bridge as bridge
import delivery_unix_ingress as unix
import test_delivery_unix_ingress as fixtures
from test_delivery_ssh_bridge import config
from test_delivery_job import CONTEXT

REAL_POPEN = subprocess.Popen
REAL_SOCKET = fixtures.REAL_SOCKET
HELPER = Path(__file__).with_name('delivery_ready_pull_helper.py')


def selected(request):
    return {'schema': ready.SCHEMA, 'target': copy.deepcopy(request['target']),
            'repositories': list(request['repositories'])}


class ReadyPolicyTests(unittest.TestCase):
    def test_only_not_yet_listening_connect_retries_within_original_budget(self):
        value=selected(fixtures.request()); now=[10.0]
        budget=unix._Budget(11,lambda:now[0])
        first,second=Mock(),Mock()
        first.connect.side_effect=ConnectionRefusedError(ready.errno.ECONNREFUSED,'SYNTHETIC')
        def sleep(seconds):now[0]+=seconds
        with patch.object(socket,'AF_UNIX',1,create=True), \
             patch.object(socket,'socket',side_effect=[first,second]) as create, \
             patch.object(ready.time,'sleep',side_effect=sleep):
            result=ready._connect_mailbox(value,budget)
        self.assertIs(second,result);self.assertEqual(2,create.call_count)
        first.close.assert_called_once();second.close.assert_not_called()
        first.sendall.assert_not_called();second.sendall.assert_not_called()

    def test_connect_permission_failure_or_expiry_never_retries_protocol(self):
        value=selected(fixtures.request())
        for code in (ready.errno.EACCES,ready.errno.ECONNREFUSED):
            now=[10.0]
            # EACCES must fail immediately even with time for many retries.
            budget=unix._Budget(11 if code == ready.errno.EACCES else 10.01,lambda:now[0]);sock=Mock()
            sock.connect.side_effect=OSError(code,'SYNTHETIC')
            def sleep(seconds):now[0]+=seconds
            with patch.object(socket,'AF_UNIX',1,create=True), \
                 patch.object(socket,'socket',return_value=sock) as create, \
                 patch.object(ready.time,'sleep',side_effect=sleep),self.assertRaises(Exception):
                ready._connect_mailbox(value,budget)
            self.assertEqual(1,create.call_count);sock.close.assert_called_once()
            sock.recv.assert_not_called();sock.sendall.assert_not_called()

    def test_inert_single_ownership_and_value_bound_context(self):
        clock = Mock(side_effect=AssertionError('NO_CLOCK'))
        value = selected(fixtures.request())
        with patch.object(socket, 'socket', side_effect=AssertionError('NO_SOCKET')), \
             patch('builtins.open', side_effect=AssertionError('NO_FILE')):
            mailbox = ready.ReadyMailbox(value, expected_uid=1001, deadline=20, monotonic=clock)
            pull = ready.SshReadyPull(config(), value, deadline=20, monotonic=clock)
        value['target']['run_id'] = '999'
        self.assertNotEqual(value, mailbox._selected)
        self.assertNotEqual(value, pull._selected)
        clock.assert_not_called()
        for obj in (mailbox, pull):
            for clone in (copy.copy, copy.deepcopy, pickle.dumps):
                with self.assertRaises(Exception): clone(obj)

    def test_selector_scope_nonce_and_canonical_frame(self):
        request = fixtures.request(); value = selected(request)
        self.assertEqual(request, ready._json_frame(bridge._packet(request)))
        duplicate = b'{"schema":"x","schema":"x"}'
        with self.assertRaises(ready.ReadyError):
            ready._json_frame(len(duplicate).to_bytes(4,'big') + duplicate)
        for field in ('run_id', 'workflow_sha', 'binding_key', 'repositories'):
            other = copy.deepcopy(request)
            if field == 'repositories': other[field] = ['SYNTHETIC/other']
            else: other['target'][field] = ('999' if field == 'run_id' else
                                            'e'*40 if field == 'workflow_sha' else 'f'*64)
            with self.assertRaises(Exception): ready._matches(other, value)
            self.assertNotEqual(ready.address(value), ready.address(selected(other)))
        for nonce in ('', 'f'*63, 'G'*64):
            with self.assertRaises(Exception): ready._matches(request | {'nonce':nonce}, value)
        self.assertLess(len(ready.address(value)), 108)

    def test_expired_operation_spawns_nothing_and_is_not_retried(self):
        pull = ready.SshReadyPull(config(), selected(fixtures.request()), deadline=10, monotonic=lambda:10)
        with patch.object(ready.sys, 'platform', 'linux'), patch.object(subprocess,'Popen') as spawn:
            with self.assertRaises(ready.ReadyError): pull.pull()
            with self.assertRaisesRegex(ready.ReadyError,'CONSUMED'): pull.pull()
        spawn.assert_not_called()

    def test_mailbox_mismatch_stops_before_listener_and_consumes(self):
        request = fixtures.request()
        mailbox = ready.ReadyMailbox(selected(request), expected_uid=1001, deadline=20, monotonic=lambda:10)
        changed = copy.deepcopy(request); changed['target']['run_id']='999'
        with patch.object(socket,'socket') as create:
            with self.assertRaises(ready.ReadyError): mailbox(changed,20)
            with self.assertRaisesRegex(ready.ReadyError,'CONSUMED'): mailbox(request,20)
        create.assert_not_called()


@unittest.skipUnless(sys.platform=='linux' and hasattr(os,'geteuid') and os.geteuid()>0,
                     'local helper/peer credentials require nonroot Linux')
class ReadyLinuxTests(unittest.TestCase):
    def setUp(self):
        self.request=fixtures.request()
        self.request['target']['run_id']=str(int.from_bytes(os.urandom(6),'big'))
        self.selection=selected(self.request);self.deadline=time.monotonic()+4
        self.event=threading.Event();self.errors=[];self.procs=[];self.threads=[];self.seen=[]
        event=self.event
        class LocalSocket(REAL_SOCKET):
            def __init__(self,family=socket.AF_UNIX,*args,**kw):
                if family!=socket.AF_UNIX:raise AssertionError('NO_OUTBOUND')
                super().__init__(family,*args,**kw)
            def listen(self,*args):
                result=super().listen(*args)
                if self.getsockname().startswith(b'\0fds-p2-ready-'):event.set()
                return result
        p=patch.object(socket,'socket',LocalSocket);p.start();self.addCleanup(p.stop)
        self.cfg=replace(config(),listener_uid=os.geteuid())

    def tearDown(self):
        for t in self.threads:t.join(5)
        for p in self.procs:
            if p.poll() is None:p.kill();p.wait(timeout=2)
        self.assertTrue(all(not t.is_alive() for t in self.threads))

    def start(self,args,**kwargs):
        self.seen.append((args,kwargs))
        self.assertFalse(kwargs['shell']);self.assertTrue(kwargs['close_fds'])
        self.assertEqual({'LANG':'C','LC_ALL':'C'},kwargs['env'])
        self.assertEqual(subprocess.DEVNULL,kwargs['stderr'])
        p=REAL_POPEN([sys.executable,'-I',str(HELPER),str(self.cfg.listener_uid),'3'],**kwargs)
        self.procs.append(p);return p

    def mailbox(self,uid=None,deadline=None):
        end=self.deadline if deadline is None else deadline
        value=ready.ReadyMailbox(self.selection, expected_uid=os.geteuid() if uid is None else uid,
            deadline=end, monotonic=time.monotonic)
        def run():
            try:value(self.request,end)
            except BaseException as exc:self.errors.append(exc)
        t=threading.Thread(target=run);self.threads.append(t);t.start()
        self.assertTrue(self.event.wait(1));return value,t

    def pull(self):
        return ready.SshReadyPull(self.cfg,self.selection,deadline=self.deadline,monotonic=time.monotonic)

    def absent(self):
        with REAL_SOCKET(socket.AF_UNIX,socket.SOCK_STREAM) as s:
            with self.assertRaises(OSError):s.connect(ready.address(self.selection))

    def test_actual_helper_one_request_ack_eof_rc_and_no_credential_arguments(self):
        mailbox,t=self.mailbox();value=self.pull()
        with patch.object(subprocess,'Popen',side_effect=self.start):
            self.assertEqual(self.request,value.pull())
        t.join(2);self.assertEqual([],self.errors);self.absent()
        self.assertEqual(0,self.procs[0].returncode)
        self.assertNotIn(self.request['nonce'],repr(self.seen))
        with self.assertRaisesRegex(ready.ReadyError,'CONSUMED'):value.pull()
        with self.assertRaisesRegex(ready.ReadyError,'CONSUMED'):mailbox(self.request,self.deadline)

    def test_wrong_helper_uid_denied_before_public_data(self):
        _,t=self.mailbox(uid=os.geteuid()+1)
        with patch.object(subprocess,'Popen',side_effect=self.start):
            with self.assertRaises(ready.ReadyError):self.pull().pull()
        t.join(2);self.assertEqual(1,len(self.errors));self.absent()
        self.assertIsNotNone(self.procs[0].poll())

    def test_wrong_job_uid_denied_by_remote_helper(self):
        self.cfg=replace(self.cfg,listener_uid=os.geteuid()+1)
        _,t=self.mailbox()
        with patch.object(subprocess,'Popen',side_effect=self.start):
            with self.assertRaises(ready.ReadyError):self.pull().pull()
        t.join(2);self.assertEqual(1,len(self.errors));self.absent()

    def test_wrong_ack_and_timeout_close_only_owned_mailbox(self):
        _,t=self.mailbox()
        with REAL_SOCKET(socket.AF_UNIX,socket.SOCK_STREAM) as sock:
            sock.settimeout(1);sock.connect(ready.address(self.selection));sock.recv(8192)
            sock.sendall(b'WRONG');sock.shutdown(socket.SHUT_WR)
        t.join(2);self.assertEqual(1,len(self.errors));self.absent()
        self.event.clear();self.errors.clear()
        _,t=self.mailbox(deadline=time.monotonic()+0.06)
        t.join(2);self.assertEqual(1,len(self.errors));self.absent()

    def test_foreign_bound_mailbox_not_deleted_or_closed(self):
        with REAL_SOCKET(socket.AF_UNIX,socket.SOCK_STREAM) as foreign:
            foreign.bind(ready.address(self.selection));foreign.listen(1)
            mailbox=ready.ReadyMailbox(self.selection,expected_uid=os.geteuid(),deadline=self.deadline,monotonic=time.monotonic)
            with self.assertRaises(ready.ReadyError):mailbox(self.request,self.deadline)
            self.assertGreaterEqual(foreign.fileno(),0)
            with REAL_SOCKET(socket.AF_UNIX,socket.SOCK_STREAM) as peer:peer.connect(ready.address(self.selection))

    def test_valid_public_frame_with_bad_exit_or_trailing_bytes_is_not_ready(self):
        for tail,rc in ((b'',3),(b'x',0)):
            def bad_helper(args,**kw):
                code='import sys;sys.stdin.buffer.read();sys.stdout.buffer.write('+repr(bridge._packet(self.request)+tail)+');sys.stdout.buffer.flush();sys.exit('+str(rc)+')'
                proc=REAL_POPEN([sys.executable,'-I','-c',code],**kw)
                self.procs.append(proc);return proc
            with self.subTest(rc=rc),patch.object(subprocess,'Popen',side_effect=bad_helper):
                with self.assertRaises(ready.ReadyError):self.pull().pull()
            self.assertIsNotNone(self.procs[-1].poll())


@unittest.skipUnless(sys.platform=='linux' and hasattr(os,'geteuid') and os.geteuid()>0,
                     'local native-job/Unix IPC integration requires nonroot Linux')
class ReadyNativeIntegrationTests(unittest.TestCase):
    def test_public_pull_credential_bridge_native_job_original_control_finish(self):
        import contextlib
        import io
        import delivery_job as job
        import delivery_job_site as declaration
        import delivery_job_handoff as handoff
        import delivery_runtime_site as runtime
        import delivery_site as site
        import delivery_source_session as source
        from test_delivery_job import RUNNER
        from test_delivery_job_rendezvous import RendezvousLinuxTests
        native=RendezvousLinuxTests('runTest');native.setUp();self.addCleanup(native.doCleanups)
        selector=ready.selector_for(CONTEXT,native.f.h.binding,
            [r.repository.owner+'/'+r.repository.name for r in native.routes])
        mailbox=ready.ReadyMailbox(selector,expected_uid=os.geteuid(),deadline=native.deadline,monotonic=native.clock)
        wrapped,owner=native.wrapped(mailbox);self.addCleanup(owner.close)
        event=threading.Event();procs=[];errors=[];responses=[]
        original_listen=REAL_SOCKET.listen
        def listen(sock,*args):
            # Keep the exact native socket type required by the existing
            # credential rendezvous; observe publication without substituting it.
            result=original_listen(sock,*args)
            if sock.getsockname().startswith(b'\0fds-p2-ready-'):event.set()
            return result
        def launch(args,**kw):
            helper=HELPER if args[-3].endswith('delivery_ready_pull_helper.py') else HELPER.with_name('delivery_ssh_bridge_helper.py')
            proc=REAL_POPEN([sys.executable,'-I',str(helper),str(os.geteuid()),'3'],**kw)
            procs.append(proc);return proc
        base=replace(config(),listener_uid=os.geteuid(),remote_helper='/synthetic/delivery_ssh_bridge_helper.py')
        pull=ready.SshReadyPull(replace(base,remote_helper='/synthetic/delivery_ready_pull_helper.py'),selector,deadline=native.deadline,monotonic=native.clock)
        sender=bridge.SshCredentialBridge(base,deadline=native.deadline,monotonic=native.clock)
        def issue():
            try:
                if not event.wait(2):raise AssertionError('MAILBOX_NOT_READY')
                request=pull.pull();responses.append(request)
                sender.send_from_supplier(request,provide_credentials=lambda *args:(native.f.credential.token,
                    [{'repository':native.s.repo.owner+'/'+native.s.repo.name,'token':fixtures.fixture.TOKEN}]))
            except BaseException as exc:errors.append(exc)
        original=runtime.RuntimeAuthorityFactory
        def factory(*args,**kw):return original(*args,**kw,wall=lambda:native.f.h.now,monotonic=native.clock)
        def build(config,*,credential,authority_factory):return native.f.app(factory=authority_factory)
        out=io.StringIO();thread=threading.Thread(target=issue)
        try:
            with patch.object(REAL_SOCKET,'listen',new=listen),patch.object(subprocess,'Popen',side_effect=launch), \
                 patch.object(source,'pipe_identity',new=handoff._identity), \
                 patch.object(runtime,'RuntimeAuthorityFactory',side_effect=factory), \
                 patch.object(site,'build_application',side_effect=build),contextlib.redirect_stdout(out):
                thread.start()
                rc=job.main(['deploy','--execute'],declaration=job.JobSite(RUNNER,wrapped),context=CONTEXT,
                    revision=lambda:CONTEXT.workflow_sha,identity=lambda:RUNNER)
                thread.join(6)
        finally:
            for proc in procs:
                if proc.poll() is None:proc.kill();proc.wait(timeout=2)
            if thread.ident is not None:thread.join(6)
        self.assertFalse(thread.is_alive());self.assertEqual([],errors,out.getvalue())
        result=json.loads(out.getvalue())
        self.assertEqual((0,'SESSION_COMPLETED','RELEASED'),(rc,result['status'],result['result']['fence']),result)
        self.assertEqual(2,len(procs));self.assertTrue(all(p.returncode==0 for p in procs))
        self.assertEqual(1,len(responses));self.assertTrue(owner._closed)
        self.assertTrue(all(t._closed for t in native.s.transports))
        self.assertNotIn(native.f.credential.token,out.getvalue());self.assertIsNone(declaration.SITE)


if __name__=='__main__':unittest.main()
