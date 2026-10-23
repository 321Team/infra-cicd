"""SYNTHETIC local IPC only. No SSH server, issuance or operating SITE."""
import errno,os,socket,subprocess,sys,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch
import delivery_crosshost_ingress as ingress
import delivery_crosshost_pipe as pipes
import delivery_job_site as declaration
import delivery_ssh_bridge as bridge
import delivery_ssh_stdio as stdio
import test_delivery_ssh_bridge as fixture
REAL_SOCKET=socket.socket

@unittest.skipUnless(sys.platform=='linux' and hasattr(os,'geteuid') and os.geteuid()>0,
                     'nonroot Linux local stdio/socketpair checks')
class StdioLinuxTests(unittest.TestCase):
    def pair(self):
        a,b=socket.socketpair();self.addCleanup(a.close);self.addCleanup(b.close);return a,b
    def dups(self,s):return os.dup(s.fileno()),os.dup(s.fileno())
    def closed(self,fd):
        with self.assertRaises(OSError) as e:os.fstat(fd)
        self.assertEqual(errno.EBADF,e.exception.errno)
    def test_same_endpoint_two_dups_and_probe_no_leak(self):
        a,b=self.pair();fds=self.dups(a);before=len(os.listdir('/proc/self/fd'))
        self.assertEqual('socketpair',stdio.classify_pair(*fds))
        self.assertEqual(before,len(os.listdir('/proc/self/fd')))
        for fd in fds:os.close(fd)
        os.fstat(a.fileno());os.fstat(b.fileno());self.assertIsNone(declaration.SITE)
    def test_foreign_shapes_stay_borrowed(self):
        a,b=self.pair()
        for kind in ('different','dgram','tcp','file','pty','mixed','listener','named','abstract'):
            with self.subTest(kind=kind),tempfile.TemporaryDirectory() as temp:
                fds=[];owned=[];socks=[]
                try:
                    if kind=='different':fds=[os.dup(a.fileno()),os.dup(b.fileno())]
                    elif kind in ('dgram','tcp'):
                        socks=list(socket.socketpair(socket.AF_UNIX,socket.SOCK_DGRAM)) if kind=='dgram' else [REAL_SOCKET(socket.AF_INET,socket.SOCK_STREAM)]
                        fds=list(self.dups(socks[0]))
                    elif kind=='file':
                        fd=os.open(os.devnull,os.O_RDWR);owned.append(fd);fds=list(self.dups_fd(fd))
                    elif kind=='pty':owned=list(os.openpty());fds=list(self.dups_fd(owned[0]))
                    elif kind=='mixed':owned=list(os.pipe());fds=[os.dup(owned[0]),os.dup(a.fileno())]
                    else:
                        server=REAL_SOCKET(socket.AF_UNIX,socket.SOCK_STREAM);socks.append(server)
                        path=('\x00synthetic-'+str(os.getpid())+'-'+str(time.monotonic_ns())) if kind=='abstract' else str(Path(temp)/'synthetic.sock')
                        server.bind(path);server.listen(1)
                        if kind=='listener':fds=list(self.dups(server))
                        else:
                            client=REAL_SOCKET(socket.AF_UNIX,socket.SOCK_STREAM);socks.append(client);client.connect(path)
                            accepted,_=server.accept();socks.append(accepted);fds=list(self.dups(client))
                    with self.assertRaises(pipes.PipeStreamError):stdio.classify_pair(*fds)
                    for fd in fds:os.fstat(fd)
                finally:
                    for fd in fds+owned:os.close(fd)
                    for s in socks:s.close()
    def dups_fd(self,fd):return os.dup(fd),os.dup(fd)
    def test_default_timeout_rejected_without_shared_flag_change(self):
        import fcntl
        a,b=self.pair();fds=self.dups(a);flags=fcntl.fcntl(a,fcntl.F_GETFL);old=socket.getdefaulttimeout()
        try:
            socket.setdefaulttimeout(.25)
            with self.assertRaises(pipes.PipeStreamError):stdio.classify_pair(*fds)
            self.assertEqual(flags,fcntl.fcntl(a,fcntl.F_GETFL))
        finally:
            socket.setdefaulttimeout(old)
            for fd in fds:os.close(fd)
    def test_wrapper_flag_change_restored_probe_closed(self):
        import fcntl
        a,b=self.pair();a.setblocking(False);flags=fcntl.fcntl(a,fcntl.F_GETFL);before=len(os.listdir('/proc/self/fd'))
        def wrap(*args,**kw):os.set_blocking(kw['fileno'],True);return REAL_SOCKET(*args,**kw)
        with patch.object(socket,'socket',side_effect=wrap),self.assertRaises(pipes.PipeStreamError):stdio.socket_identity(a.fileno())
        self.assertEqual(flags,fcntl.fcntl(a,fcntl.F_GETFL));self.assertEqual(before,len(os.listdir('/proc/self/fd')))
    def test_dup_minimum_three_partial_failure_closes_only_own(self):
        import fcntl
        a,b=self.pair();made=os.dup(a.fileno());calls=[];real=fcntl.fcntl
        def allocate(fd,op,*args):
            if op==fcntl.F_DUPFD_CLOEXEC:
                calls.append((fd,args))
                if len(calls)==1:return made
                raise OSError(errno.EMFILE,'SYNTHETIC_SECRET')
            return real(fd,op,*args)
        with patch.object(fcntl,'fcntl',side_effect=allocate),self.assertRaises(OSError):stdio.duplicate_stdio()
        self.assertEqual([(0,(3,)),(1,(3,))],calls);self.closed(made);os.fstat(0);os.fstat(1);os.fstat(a.fileno())
    def test_owned_clock_or_constructor_failure_closes_both_dups(self):
        a,b=self.pair()
        for constructor in (False,True):
            fds=self.dups(a)
            def clock():raise ValueError('SYNTHETIC_SECRET')
            with self.subTest(constructor=constructor):
                if constructor:
                    with patch.object(stdio,'SocketStdioEnd',side_effect=OSError('SECRET')),self.assertRaises(bridge.BridgeError):
                        bridge.serve_owned_stdio(*fds,listener_uid=os.geteuid(),timeout_seconds=2,monotonic=lambda:10)
                else:
                    with self.assertRaises(bridge.BridgeError):bridge.serve_owned_stdio(*fds,listener_uid=os.geteuid(),timeout_seconds=2,monotonic=clock)
                for fd in fds:self.closed(fd)
                os.fstat(a.fileno())
    def test_constructor_closed_reused_fd_not_closed_as_own(self):
        a,b=self.pair();r,w=self.dups(a);foreign=[]
        def fail(fd,**kw):
            os.close(fd);new=os.open(os.devnull,os.O_RDONLY)
            if new!=fd:os.dup2(new,fd);os.close(new)
            foreign.append(fd);raise OSError('SYNTHETIC_SECRET')
        with patch.object(stdio,'SocketStdioEnd',side_effect=fail),self.assertRaises(bridge.BridgeError):
            bridge.serve_owned_stdio(r,w,listener_uid=os.geteuid(),timeout_seconds=2,monotonic=lambda:10)
        self.closed(w);os.fstat(foreign[0]);os.close(foreign[0]);os.fstat(a.fileno())
    def test_shared_nonblocking_io_eagain_and_original_deadline(self):
        a,b=self.pair();r,w=self.dups(a);deadline=time.monotonic()+3
        read=stdio.SocketStdioEnd(r,write=False,deadline=deadline,monotonic=time.monotonic)
        write=stdio.SocketStdioEnd(w,write=True,deadline=deadline,monotonic=time.monotonic)
        try:
            packet=bridge._packet({'synthetic':'public'});real=os.write;count=[0]
            def eagain(fd,data):
                count[0]+=1
                if count[0]==1:raise BlockingIOError()
                return real(fd,data)
            with patch.object(os,'write',side_effect=eagain):write.write_all(packet)
            self.assertEqual(packet,b.recv(8192));b.sendall(b'abc');self.assertEqual(b'abc',read.read(3,deadline))
            with self.assertRaises(pipes.PipeStreamError):read.read(3,deadline+1)
        finally:self.assertTrue(read.close());self.assertTrue(write.close())
    def helper(self,bad_uid=False,trailing=False):
        f=fixture.BridgeLinuxTests('runTest');f.setUp();self.addCleanup(f.doCleanups);self.addCleanup(f.tearDown)
        a,b=self.pair();a.settimeout(4)
        program='import os,runpy,sys; os.close(2); sys.argv=sys.argv[1:]; runpy.run_path(sys.argv[0],run_name="__main__")'
        uid=os.geteuid()+1 if bad_uid else os.geteuid()
        proc=fixture.REAL_POPEN([sys.executable,'-I','-c',program,str(fixture.HELPER),str(uid),'3'],stdin=b,stdout=b,stderr=subprocess.DEVNULL,close_fds=True)
        f.procs.append(proc);b.close();a.sendall(bridge._packet({'schema':bridge.SCHEMA,'request':f.request}))
        if bad_uid:self.assertEqual(b'',a.recv(8192));self.assertEqual(1,proc.wait(timeout=4))
        else:
            ready=bridge._receipt('ready',f.request);received=b''
            while len(received)<len(ready):
                part=a.recv(len(ready)-len(received));self.assertTrue(part);received+=part
            self.assertEqual(ready,received)
            frame=ingress.encode_frame(f.request,rosa_token=fixture.TOKEN,source_tokens=fixture.credentials(f.request,0)[1])
            a.sendall(frame+(b'x' if trailing else b''));a.shutdown(socket.SHUT_WR);response=b''
            while True:
                part=a.recv(8192)
                if not part:break
                response+=part
            self.assertEqual(b'' if trailing else bridge._receipt('forwarded',f.request),response)
            self.assertEqual(1 if trailing else 0,proc.wait(timeout=4))
        peer,_=f.listener.accept();peer.settimeout(3)
        with peer:
            payload=b''
            while True:
                part=peer.recv(8192)
                if not part:break
                payload+=part
        self.assertEqual(b'' if bad_uid or trailing else frame,payload);self.assertIsNone(declaration.SITE)
    def test_actual_child_socketpair_stdio_and_closed_stderr(self):self.helper()
    def test_actual_child_wrong_listener_uid_before_credential(self):self.helper(bad_uid=True)
    def test_actual_child_trailing_frame_never_forwarded(self):self.helper(trailing=True)

if __name__=='__main__':unittest.main()
