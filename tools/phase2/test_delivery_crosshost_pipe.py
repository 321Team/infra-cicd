"""Linux local pipes only: SYNTHETIC bytes, no SSH/host/issuer/runtime."""
import contextlib
from dataclasses import replace
import errno
import io
import json
import os
import sys
import threading
import time
import unittest
from unittest.mock import Mock, patch

import delivery_crosshost_ingress as ingress
import delivery_crosshost_pipe as stream
import delivery_job as job
import delivery_job_handoff as handoff
import delivery_job_parent as parent
import delivery_runtime_site as runtime
import delivery_site as site
import delivery_source_session as sources
import test_delivery_crosshost_ingress as fixture
from test_delivery_job import CONTEXT, RUNNER


class PortablePlatformTests(unittest.TestCase):
    def test_nonlinux_never_inspects_or_takes_arbitrary_descriptor(self):
        with patch.object(stream.sys,'platform','win32'),patch.object(os,'fstat') as inspect:
            with self.assertRaisesRegex(stream.PipeStreamError,'LINUX_REQUIRED'):
                stream.OwnedPipeWriter(99,deadline=10,monotonic=lambda:1)
            inspect.assert_not_called()


@unittest.skipUnless(sys.platform=='linux','actual owned FD/poll integration is Linux only')
class PipeTests(unittest.TestCase):
    def setUp(self):
        fixture.IngressTests.setUp(self)
        p=patch.object(sources,'pipe_identity',new=handoff._identity)
        p.start();self.addCleanup(p.stop)
        self.events=[];self.threads=[]

    receiver = fixture.IngressTests.receiver

    def pair(self):
        result=os.pipe()
        for fd in result:
            ident=stream.identity(fd)
            def close(fd=fd,ident=ident):
                try:
                    if stream.identity(fd)==ident:os.close(fd)
                except OSError:pass
            self.addCleanup(close)
        return result

    def channel(self,fd,*,deadline=None,clock=None,authenticate=None):
        return stream.pipe_ingress_channel(fd,authenticate=authenticate or (lambda *a:self.peer),
            deadline=self.deadline if deadline is None else deadline,
            monotonic=clock or (lambda:self.f.h.mono))

    def send(self,writer,request,**changes):
        args=dict(expected_peer=self.peer,authenticate=lambda *a:self.peer,
            rosa_token=self.f.credential.token,
            source_tokens=[dict(repository=r,token=fixture.fixture.TOKEN) for r in request['repositories']])
        return writer.send(request,**(args|changes))

    def background(self,function):
        results=[]
        def run():
            try:results.append(function())
            except BaseException as exc:results.append(exc)
        t=threading.Thread(target=run,daemon=True);t.start();self.threads.append(t)
        self.addCleanup(lambda:t.join(3))
        return t,results

    def test_read_adapter_sets_flags_reads_fragments_and_eof_then_closes(self):
        read,write=self.pair();channel=self.channel(read)
        self.assertFalse(os.get_blocking(read));self.assertFalse(os.get_inheritable(read))
        os.write(write,b'SYNTHETIC');os.close(write)
        self.assertEqual(b'SYN',channel.read(3,self.deadline))
        self.assertEqual(b'THETIC',channel.read(4096,self.deadline));self.assertEqual(b'',channel.read(1,self.deadline))
        self.assertTrue(channel.close());self.assertTrue(channel.close())
        with self.assertRaises(OSError):os.fstat(read)

    def test_partial_stream_writes_large_frame_and_eof_are_received_exactly_once(self):
        r=self.receiver();request=r.request();read,write=self.pair();writer=stream.OwnedPipeWriter(write,deadline=self.deadline,monotonic=lambda:self.f.h.mono)
        native=os.write
        def partial(fd,raw):return native(fd,raw[:7])
        with patch.object(os,'write',side_effect=partial) as writing:
            thread,result=self.background(lambda:self.send(writer,request,rosa_token='SYNTHETIC_'+('X'*12000)))
            bundle=r.receive(self.channel(read));self.addCleanup(bundle.close);thread.join(3)
        self.assertFalse(thread.is_alive());self.assertEqual([True],result)
        self.assertEqual(12010,len(bundle._rosa));self.assertGreater(writing.call_count,2)
        with self.assertRaisesRegex(stream.PipeStreamError,'CONSUMED'):self.send(writer,request)
        for fd in (read,write):
            with self.assertRaises(OSError):os.fstat(fd)

    def test_wrong_sender_peer_or_bad_credentials_never_write_and_end_in_eof(self):
        for changes,code in ((dict(authenticate=lambda *a:replace(self.peer,principal='other')),'PEER_REJECTED'),
            (dict(rosa_token=fixture.fixture.TOKEN),'CREDENTIAL_REJECTED')):
            r=self.receiver();read,write=self.pair();writer=stream.OwnedPipeWriter(write,deadline=self.deadline,monotonic=lambda:self.f.h.mono)
            with patch.object(os,'write') as writing,self.assertRaisesRegex(stream.PipeStreamError,code):self.send(writer,r.request(),**changes)
            writing.assert_not_called();self.assertEqual(b'',os.read(read,1))

    def test_sender_auth_callback_cannot_mutate_encoded_job_request(self):
        r=self.receiver();read,write=self.pair();writer=stream.OwnedPipeWriter(write,deadline=self.deadline,monotonic=lambda:self.f.h.mono)
        def mutate(request,deadline):request['target']['run_id']='456';request['nonce']='0'*64;return self.peer
        thread,result=self.background(lambda:self.send(writer,r.request(),authenticate=mutate))
        bundle=r.receive(self.channel(read));self.addCleanup(bundle.close);thread.join(3)
        self.assertEqual([True],result);self.assertFalse(thread.is_alive())

    def test_invalid_pipe_direction_or_nonpipe_is_never_taken_or_closed(self):
        read,write=self.pair();regular=os.open(os.devnull,os.O_RDONLY);self.addCleanup(os.close,regular)
        for fd in (True,0,1,2,None,'3',regular,read):
            with self.assertRaises(stream.PipeStreamError):stream.OwnedPipeWriter(fd,deadline=self.deadline)
        for fd in (read,write,regular):self.assertTrue(os.fstat(fd))
        with self.assertRaises(stream.PipeStreamError):self.channel(write)
        self.assertTrue(os.fstat(write))

    def test_valid_pipe_configuration_failure_closes_owned_end(self):
        for deadline,clock in ((100,lambda:100),(float('nan'),lambda:100),(150,None)):
            read,write=self.pair()
            with self.assertRaises(stream.PipeStreamError):stream.OwnedPipeWriter(write,deadline=deadline,monotonic=clock)
            with self.assertRaises(OSError):os.fstat(write)
            self.assertTrue(os.fstat(read))

    def test_reused_fd_is_rejected_and_new_owner_survives_cleanup(self):
        read,write=self.pair();channel=self.channel(read);new_read,new_write=self.pair()
        os.dup2(new_read,read)
        self.addCleanup(os.close,read)
        with self.assertRaisesRegex(stream.PipeStreamError,'FD_CHANGED'):channel.read(1,self.deadline)
        self.assertTrue(channel.close());self.assertTrue(os.fstat(read));self.assertTrue(os.fstat(new_read))

    def test_high_fd_works_without_select_fd_setsize_limit(self):
        import fcntl,resource
        if resource.getrlimit(resource.RLIMIT_NOFILE)[0]<=1100:self.skipTest('high-FD fixture requires limit >1100')
        read,write=self.pair();high=fcntl.fcntl(read,fcntl.F_DUPFD_CLOEXEC,1050);self.addCleanup(lambda:self.close_if_open(high))
        os.close(read);channel=self.channel(high)
        os.write(write,b'X');os.close(write)
        self.assertEqual(b'X',channel.read(1,self.deadline));self.assertEqual(b'',channel.read(1,self.deadline))
        self.assertTrue(channel.close())

    @staticmethod
    def close_if_open(fd):
        try:os.close(fd)
        except OSError:pass

    def test_missing_eof_hits_absolute_deadline_and_closes_owned_read_end(self):
        read,write=self.pair();deadline=time.monotonic()+0.04
        r=ingress.CrossHostIngress(context=CONTEXT,binding=self.f.h.binding,expected_peer=self.peer,
            routes=(replace(self.route,local_use_until=deadline+30),),deadline=deadline)
        raw=ingress.encode_frame(r.request(),rosa_token=self.f.credential.token,
            source_tokens=[dict(repository=self.s.repo.owner+'/'+self.s.repo.name,token=fixture.fixture.TOKEN)])
        os.write(write,raw)  # Deliberately retain writer: complete bytes alone are not EOF.
        with self.assertRaises(ingress.IngressError):r.receive(self.channel(read,deadline=deadline,clock=time.monotonic))
        with self.assertRaises(OSError):os.fstat(read)
        self.assertTrue(os.fstat(write))

    def test_writer_backpressure_obeys_deadline_and_does_not_retry_message(self):
        import fcntl
        read,write=self.pair();fcntl.fcntl(write,fcntl.F_SETPIPE_SZ,4096)
        deadline=time.monotonic()+0.04;writer=stream.OwnedPipeWriter(write,deadline=deadline)
        request=self.receiver().request()
        with self.assertRaisesRegex(stream.PipeStreamError,'DEADLINE_EXCEEDED'):
            self.send(writer,request,rosa_token='SYNTHETIC_'+('Y'*12000))
        with self.assertRaises(OSError):os.fstat(write)
        partial=os.read(read,4096);self.assertTrue(partial);self.assertEqual(b'',os.read(read,4096))
        with self.assertRaisesRegex(stream.PipeStreamError,'CONSUMED'):self.send(writer,request)

    def test_eintr_eagain_poll_read_write_keep_same_deadline_and_offset(self):
        read,write=self.pair();r=self.receiver();writer=stream.OwnedPipeWriter(write,deadline=self.deadline,monotonic=lambda:self.f.h.mono)
        native_read,native_write,native_poll=os.read,os.write,stream.select.poll
        reads=writes=polls=0
        class PollOnce:
            def __init__(self):self.poller=native_poll()
            def register(self,*a):return self.poller.register(*a)
            def poll(self,milliseconds):
                nonlocal polls
                polls+=1
                if polls==1:raise InterruptedError(errno.EINTR,'SYNTHETIC')
                return self.poller.poll(milliseconds)
        def read_once(fd,size):
            nonlocal reads
            reads+=1
            if reads==1:raise BlockingIOError(errno.EAGAIN,'SYNTHETIC')
            return native_read(fd,size)
        def write_once(fd,raw):
            nonlocal writes
            writes+=1
            if writes==1:raise InterruptedError(errno.EINTR,'SYNTHETIC')
            return native_write(fd,raw)
        with patch.object(os,'read',side_effect=read_once),patch.object(os,'write',side_effect=write_once),patch.object(stream.select,'poll',side_effect=PollOnce):
            thread,result=self.background(lambda:self.send(writer,r.request()))
            bundle=r.receive(self.channel(read));self.addCleanup(bundle.close);thread.join(3)
        self.assertEqual([True],result);self.assertGreaterEqual(reads,2);self.assertGreaterEqual(writes,2)
        self.assertGreaterEqual(polls,2)

    def test_os_write_failure_after_partial_frame_is_not_retried_or_accepted(self):
        r=self.receiver();read,write=self.pair();writer=stream.OwnedPipeWriter(write,deadline=self.deadline,monotonic=lambda:self.f.h.mono)
        native=os.write;calls=0
        def failure(fd,raw):
            nonlocal calls
            calls+=1
            if calls==1:return native(fd,raw[:7])
            raise OSError(errno.EIO,fixture.fixture.TOKEN)
        with patch.object(os,'write',side_effect=failure):
            thread,result=self.background(lambda:self.send(writer,r.request()))
            with self.assertRaisesRegex(ingress.IngressError,'FRAME_INVALID'):r.receive(self.channel(read))
            thread.join(3)
        self.assertEqual(2,calls);self.assertEqual(1,len(result));self.assertIsInstance(result[0],stream.PipeStreamError)
        self.assertNotIn(fixture.fixture.TOKEN,str(result[0]));self.assertFalse(thread.is_alive())

    def test_interrupt_or_auth_error_is_redacted_and_closes_writer(self):
        for exc in (KeyboardInterrupt(fixture.fixture.TOKEN),SystemExit(fixture.fixture.TOKEN),ValueError(fixture.fixture.TOKEN)):
            read,write=self.pair();writer=stream.OwnedPipeWriter(write,deadline=self.deadline,monotonic=lambda:self.f.h.mono)
            expected=type(exc) if not isinstance(exc,ValueError) else stream.PipeStreamError
            with self.assertRaises(expected) as caught:self.send(writer,self.receiver().request(),authenticate=Mock(side_effect=exc))
            self.assertNotIn(fixture.fixture.TOKEN,str(caught.exception));self.assertEqual(b'',os.read(read,1))

    def test_clock_regression_or_read_error_fails_closed_without_body(self):
        for failure in ('clock','read'):
            self.f.h.mono=100;read,write=self.pair();r=self.receiver();channel=self.channel(read)
            os.write(write,b'1234');os.close(write);native=os.read
            def invalid(fd,size):
                if failure=='read':raise OSError(errno.EIO,fixture.fixture.TOKEN)
                raw=native(fd,size);self.f.h.mono-=1;return raw
            with patch.object(os,'read',side_effect=invalid),self.assertRaisesRegex(ingress.IngressError,'RECEIVE_FAILED'):r.receive(channel)
            with self.assertRaises(OSError):os.fstat(read)

    def test_local_close_failure_is_not_reported_as_success(self):
        read,write=self.pair();writer=stream.OwnedPipeWriter(write,deadline=self.deadline,monotonic=lambda:self.f.h.mono)
        native=os.close
        def fail(fd):
            if fd==write:raise OSError(errno.EIO,fixture.fixture.TOKEN)
            native(fd)
        with patch.object(os,'close',side_effect=fail),self.assertRaisesRegex(stream.PipeStreamError,'CLEANUP_FAILED'):
            self.send(writer,self.receiver().request())
        self.assertFalse(writer.close());self.assertFalse(writer.close())
        self.assertTrue(os.fstat(write))  # Caller fixture performs manual recovery; no false revoke/close claim.

    def test_real_parent_pipe_ingress_handoff_driver_factory_finish_and_cleanup(self):
        from delivery_source_roles import plan_source_roles
        sent=[];channels=[];ready=[];owned=[]
        def publish(request,deadline):ready.append(request)
        def open_channel(request,deadline):
            read,write=self.pair();owned.extend((read,write))
            writer=stream.OwnedPipeWriter(write,deadline=deadline,monotonic=lambda:self.f.h.mono)
            sent.append(self.background(lambda:self.send(writer,request)))
            channel=self.channel(read);channels.append(channel);return channel
        def wire(pool):
            self.s.wire(pool)
            return plan_source_roles(pool,plan=self.f.plan(),finish_repositories=(self.s.repo,self.s.repo))
        builder=parent.ParentJobBuilder(config=self.f.config(),plan=self.f.plan(),finish_slot=self.f.slot,
            expected_peer=self.peer,routes=self.routes,deadline=self.deadline,wire=wire,
            publish_ready=publish,open_channel=open_channel,monotonic=lambda:self.f.h.mono)
        original=runtime.RuntimeAuthorityFactory
        def factory(*a,**kw):return original(*a,**kw,wall=lambda:self.f.h.now,monotonic=lambda:self.f.h.mono)
        def build(config,*,credential,authority_factory):return self.f.app(factory=authority_factory)
        out=io.StringIO()
        with patch.object(runtime,'RuntimeAuthorityFactory',side_effect=factory),patch.object(site,'build_application',side_effect=build),contextlib.redirect_stdout(out):
            rc=job.main(['deploy','--execute'],declaration=job.JobSite(RUNNER,builder),
                context=CONTEXT,revision=lambda:CONTEXT.workflow_sha,identity=lambda:RUNNER)
        result=json.loads(out.getvalue());self.assertEqual((0,'SESSION_COMPLETED','RELEASED'),(rc,result['status'],result['result']['fence']),result)
        for thread,status in sent:thread.join(3);self.assertFalse(thread.is_alive());self.assertEqual([True],status)
        self.assertEqual(1,len(ready));self.assertNotIn(fixture.fixture.TOKEN,str(ready)+out.getvalue())
        self.assertTrue(all(t._closed for t in self.s.transports));self.assertTrue(channels[0].close())
        for fd in owned:
            with self.assertRaises(OSError):os.fstat(fd)


if __name__=='__main__':unittest.main()
