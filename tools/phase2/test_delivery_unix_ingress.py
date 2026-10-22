"""SYNTHETIC frames + Linux local sockets only, no SSH/issuer/remote Runtime."""
import contextlib
import copy
from dataclasses import asdict
import io
import json
import os
import socket
import struct
import sys
import threading
import time
import unittest
import uuid
from unittest.mock import Mock, patch

import delivery_crosshost_ingress as ingress
import delivery_job as job
import delivery_job_handoff as handoff
import delivery_job_parent as parent
import delivery_job_site as declaration
import delivery_runtime_site as runtime
import delivery_site as site
import delivery_source_session as source
import delivery_unix_ingress as unix
import test_delivery_source_session as fixture
from test_delivery_job import CONTEXT, RUNNER

REAL_SOCKET = socket.socket


def request():
    return {'schema': ingress.REQUEST_SCHEMA,
            'target': asdict(CONTEXT) | {'binding_key': '1' * 64},
            'nonce': '2' * 64, 'repositories': ['SYNTHETIC/source']}


class UnixPolicyTests(unittest.TestCase):
    def test_foreign_objects_never_get_closed_or_authenticated(self):
        foreign = Mock()
        with self.assertRaises(unix.UnixIngressError):
            unix.unix_ingress_channel(foreign, request=request(), expected_uid=1001,
                peer=ingress.PeerIdentity('synthetic', 'synthetic-local'), deadline=20, monotonic=lambda: 10)
        foreign.close.assert_not_called(); foreign.getsockopt.assert_not_called()
        self.assertIsNone(declaration.SITE)

    def test_budget_keeps_original_and_shorter_deadline(self):
        b = unix._Budget(20, lambda: 10)
        self.assertEqual(10, b.remaining(30))
        self.assertEqual(2, b.remaining(12))
        self.assertEqual(2, b.remaining(50))

    def test_clock_rollback_or_nonfinite_is_rejected(self):
        for values in ((10, 9), (10, float('nan'))):
            it = iter(values); b = unix._Budget(20, lambda: next(it))
            b.remaining(20)
            with self.assertRaisesRegex(unix.UnixIngressError, 'CLOCK_INVALID'): b.remaining(20)

    def test_configuration_does_not_accept_root_bool_wire_identity_or_bad_request(self):
        peer = ingress.PeerIdentity('synthetic', 'synthetic-local')
        for uid in (0, -1, True, None, '1001', 0xFFFFFFFF, 0x100000000):
            with self.assertRaises(unix.UnixIngressError): unix._configuration(request(), uid, peer)
        with self.assertRaises(unix.UnixIngressError):
            unix._configuration(request(), 1001, {'principal': 'synthetic'})
        bad = request(); bad['uid'] = 1001
        with self.assertRaises(ingress.IngressError): unix._configuration(bad, 1001, peer)

    def test_stored_request_is_not_aliased_and_expiry_has_no_reset(self):
        original = request(); saved = unix._configuration(original, 1001,
            ingress.PeerIdentity('synthetic', 'synthetic-local'))
        original['target']['run_id'] = '999'
        self.assertNotEqual(original, saved)
        with self.assertRaisesRegex(unix.UnixIngressError, 'DEADLINE'):
            unix._Budget(10, lambda: 10).remaining(40)


@unittest.skipUnless(sys.platform == 'linux' and hasattr(os, 'geteuid') and os.geteuid() > 0,
                     'actual SO_PEERCRED requires a nonroot Linux test process')
class UnixSocketTests(unittest.TestCase):
    def setUp(self):
        self.s = fixture.SourceSessionTests('runTest'); self.s.setUp(); self.addCleanup(self.s.doCleanups)
        # Existing runtime fixture blocks every socket. Permit only LOCAL Unix
        # sockets here; TCP/UDP creation and DNS/connection helpers stay blocked.
        def local_socket(family=socket.AF_INET, kind=socket.SOCK_STREAM, proto=0, fileno=None):
            if family != socket.AF_UNIX: raise AssertionError('OUTBOUND_NETWORK_FORBIDDEN')
            return REAL_SOCKET(family, kind, proto, fileno=fileno)
        p = patch.object(socket, 'socket', new=local_socket); p.start(); self.addCleanup(p.stop)
        self.f = self.s.f
        self.peer = ingress.PeerIdentity('synthetic-local-uid', 'synthetic-local-socket')
        self.deadline = self.f.h.mono + 5
        self.clock = lambda: self.f.h.mono
        self.routes = (ingress.SourceRoute(self.s.repo, self.f.h.mono + 50, fixture.CA, '192.0.2.10'),)
        self.sockets = []
        self.addCleanup(lambda: [s.close() for s in self.sockets])

    def pair(self):
        a, b = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sockets.extend((a, b)); return a, b

    def listener(self):
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        address = b'\0SYNTHETIC-P2-' + uuid.uuid4().hex.encode()
        s.bind(address); s.listen(1); self.sockets.append(s)
        return s, address

    def receiver(self):
        return ingress.CrossHostIngress(context=CONTEXT, binding=self.f.h.binding,
            expected_peer=self.peer, routes=self.routes, deadline=self.deadline, monotonic=self.clock)

    def frame(self, req):
        return ingress.encode_frame(req, rosa_token=self.f.credential.token,
            source_tokens=[{'repository': self.s.repo.owner + '/' + self.s.repo.name, 'token': fixture.TOKEN}])

    def channel(self, sock, req, **kw):
        return unix.unix_ingress_channel(sock, request=req, expected_uid=os.geteuid(),
            peer=self.peer, deadline=self.deadline, monotonic=self.clock, **kw)

    def test_actual_kernel_uid_then_one_frame_eof_into_existing_core(self):
        server, client = self.pair(); r = self.receiver(); channel = self.channel(server, r.request())
        client.sendall(self.frame(r.request())); client.shutdown(socket.SHUT_WR)
        bundle = r.receive(channel)
        self.assertIs(type(bundle), ingress.ReceivedCredentials); bundle.close()
        self.assertEqual(-1, server.fileno()); self.assertTrue(channel.close())
        self.assertGreaterEqual(client.fileno(), 0)  # Peer socket is not owned by this channel.

    def test_wrong_kernel_uid_stops_without_any_secret_read(self):
        server, client = self.pair(); r = self.receiver()
        channel = unix.unix_ingress_channel(server, request=r.request(), expected_uid=os.geteuid() + 1,
            peer=self.peer, deadline=self.deadline, monotonic=self.clock)
        client.sendall(self.frame(r.request()))
        with patch.object(REAL_SOCKET, 'recv', side_effect=AssertionError('SECRET_READ_FORBIDDEN')) as read:
            with self.assertRaises(ingress.IngressError): r.receive(channel)
            read.assert_not_called()
        self.assertEqual(-1, server.fileno()); self.assertEqual([], self.f.p.c.storage.claims)

    def test_read_requires_auth_and_auth_is_one_attempt_value_bound(self):
        server, client = self.pair(); req = self.receiver().request(); channel = self.channel(server, req)
        with self.assertRaisesRegex(unix.UnixIngressError, 'NOT_AUTHENTICATED'): channel.read(1, self.deadline)
        self.assertEqual(self.peer, channel.authenticate(copy.deepcopy(req), self.deadline))
        with self.assertRaisesRegex(unix.UnixIngressError, 'CONSUMED'): channel.authenticate(req, self.deadline)
        self.assertTrue(channel.close())

    def test_wrong_job_nonce_rejects_and_mutated_caller_request_is_not_adopted(self):
        server, client = self.pair(); req = self.receiver().request(); channel = self.channel(server, req)
        req['nonce'] = 'f' * 64
        with self.assertRaisesRegex(unix.UnixIngressError, 'TARGET_REJECTED'): channel.authenticate(req, self.deadline)
        with self.assertRaisesRegex(unix.UnixIngressError, 'CONSUMED'): channel.authenticate(req, self.deadline)
        self.assertTrue(channel.close())

    def test_wrong_configuration_closes_only_qualified_owned_socket(self):
        server, client = self.pair()
        with self.assertRaises(unix.UnixIngressError):
            unix.unix_ingress_channel(server, request=request(), expected_uid=True,
                peer=self.peer, deadline=self.deadline, monotonic=self.clock)
        self.assertEqual(-1, server.fileno()); self.assertGreaterEqual(client.fileno(), 0)
        unconnected = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); self.sockets.append(unconnected)
        with self.assertRaises(unix.UnixIngressError): self.channel(unconnected, request())
        self.assertEqual(-1, unconnected.fileno())

    def test_stalled_read_uses_initial_deadline_and_redacts_errors(self):
        server, client = self.pair(); req = self.receiver().request(); end = time.monotonic() + 0.04
        channel = unix.unix_ingress_channel(server, request=req, expected_uid=os.geteuid(),
            peer=self.peer, deadline=end, monotonic=time.monotonic)
        channel.authenticate(req, end); start = time.monotonic()
        with self.assertRaises(unix.UnixIngressError) as caught: channel.read(1, end + 100)
        self.assertLess(time.monotonic() - start, 2)
        self.assertNotIn(fixture.TOKEN, str(caught.exception)); self.assertTrue(channel.close())

    def test_core_rejects_truncated_or_trailing_payload_and_closes(self):
        for delta in ('truncated', 'trailing'):
            r = self.receiver(); server, client = self.pair(); channel = self.channel(server, r.request())
            raw = self.frame(r.request()); client.sendall(raw[:-1] if delta == 'truncated' else raw + b'x')
            client.shutdown(socket.SHUT_WR)
            with self.assertRaises(ingress.IngressError): r.receive(channel)
            self.assertEqual(-1, server.fileno())

    def test_bounded_accept_transfers_connection_and_closes_only_listener_object(self):
        listener, address = self.listener(); client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sockets.append(client); client.connect(address)
        req = self.receiver().request()
        channel = unix.accept_unix_ingress_channel(listener, request=req, expected_uid=os.geteuid(),
            peer=self.peer, deadline=self.deadline, monotonic=self.clock)
        self.assertEqual(-1, listener.fileno())
        self.assertEqual(self.peer, channel.authenticate(req, self.deadline))
        client.sendall(b'a'); self.assertEqual(b'a', channel.read(1, self.deadline))
        self.assertTrue(channel.close()); self.assertTrue(channel.close())

    def test_no_client_accept_expires_and_consumes_listener(self):
        listener, _ = self.listener(); end = time.monotonic() + 0.04
        with self.assertRaisesRegex(unix.UnixIngressError, 'ACCEPT_FAILED'):
            unix.accept_unix_ingress_channel(listener, request=request(), expected_uid=os.geteuid(),
                peer=self.peer, deadline=end, monotonic=time.monotonic)
        self.assertEqual(-1, listener.fileno())

    def test_accept_cleanup_uncertainty_never_returns_live_channel(self):
        listener, address = self.listener(); client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sockets.append(client); client.connect(address)
        close = unix._close
        def uncertain(sock):
            ok = close(sock)
            return False if sock is listener else ok
        with patch.object(unix, '_close', side_effect=uncertain):
            with self.assertRaisesRegex(unix.UnixIngressError, 'CLEANUP_FAILED'):
                unix.accept_unix_ingress_channel(listener, request=request(), expected_uid=os.geteuid(),
                    peer=self.peer, deadline=self.deadline, monotonic=self.clock)
        self.assertEqual(b'', client.recv(1))

    def test_peercred_or_clock_interrupt_does_not_leak_original_arguments(self):
        for error, args in ((KeyboardInterrupt(fixture.TOKEN), ()), (SystemExit(fixture.TOKEN), (130,))):
            server, client = self.pair(); req = self.receiver().request(); channel = self.channel(server, req)
            owner = channel.authenticate.__self__
            owner.budget.clock = Mock(side_effect=error)
            with self.assertRaises(type(error)) as caught: channel.authenticate(req, self.deadline)
            self.assertEqual(args, caught.exception.args); self.assertTrue(channel.close())

    def test_listener_cleanup_cannot_return_a_late_connection(self):
        listener, address = self.listener(); client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sockets.append(client); client.connect(address)
        close = unix._close
        def delayed(sock):
            ok = close(sock)
            if sock is listener: self.f.h.mono = self.deadline
            return ok
        with patch.object(unix, '_close', side_effect=delayed):
            with self.assertRaisesRegex(unix.UnixIngressError, 'DEADLINE'):
                unix.accept_unix_ingress_channel(listener, request=request(), expected_uid=os.geteuid(),
                    peer=self.peer, deadline=self.deadline, monotonic=self.clock)
        self.assertEqual(b'', client.recv(1))

    def test_actual_overflow_setting_is_read_and_ambiguous_peer_uid_is_rejected(self):
        current = unix._overflow_uid(); self.assertIs(type(current), int)
        server, client = self.pair(); req = self.receiver().request()
        uid = current if current > 0 else os.geteuid()
        channel = unix.unix_ingress_channel(server, request=req, expected_uid=uid,
            peer=self.peer, deadline=self.deadline, monotonic=self.clock)
        owner = channel.authenticate.__self__
        # Kernel conversion is modeled, not a new user namespace or real UID.
        with patch.object(unix, '_overflow_uid', return_value=uid), \
             patch.object(REAL_SOCKET, 'getsockopt', return_value=struct.pack('iII', 4242, uid, uid)):
            with self.assertRaisesRegex(unix.UnixIngressError, 'PEER_REJECTED'):
                channel.authenticate(req, self.deadline)
        self.assertFalse(owner.authenticated); self.assertTrue(channel.close())

    def test_unsigned_large_peer_uid_and_changed_overflow_setting(self):
        for changed in (False, True):
            server, client = self.pair(); req = self.receiver().request()
            channel = unix.unix_ingress_channel(server, request=req, expected_uid=3000000000,
                peer=self.peer, deadline=self.deadline, monotonic=self.clock)
            with patch.object(unix, '_overflow_uid', side_effect=[65534, 65533 if changed else 65534]), \
                 patch.object(REAL_SOCKET, 'getsockopt', return_value=struct.pack('iII', 4242, 3000000000, 3000000000)):
                if changed:
                    with self.assertRaisesRegex(unix.UnixIngressError, 'PEER_REJECTED'):
                        channel.authenticate(req, self.deadline)
                else: self.assertEqual(self.peer, channel.authenticate(req, self.deadline))
            self.assertTrue(channel.close())

    def test_inner_connection_cleanup_failure_cannot_become_generic_accept_failure(self):
        listener, address = self.listener(); client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sockets.append(client); client.connect(address)
        calls = []
        original = unix._connected
        def invalid(sock, *args):
            self.f.h.mono = self.deadline  # _connected fails and attempts close.
            return original(sock, *args)
        close = unix._close
        def uncertain_once(sock):
            ok = close(sock)
            if sock is not listener and not calls:
                calls.append(1); return False
            return ok
        with patch.object(unix, '_connected', side_effect=invalid), \
             patch.object(unix, '_close', side_effect=uncertain_once):
            with self.assertRaisesRegex(unix.UnixIngressError, '^P2_UNIX_CLEANUP_FAILED$'):
                unix.accept_unix_ingress_channel(listener, request=request(), expected_uid=os.geteuid(),
                    peer=self.peer, deadline=self.deadline, monotonic=self.clock)
        self.assertEqual(-1, listener.fileno()); self.assertEqual(b'', client.recv(1))

    def test_actual_unix_accept_parent_pipe_native_driver_source_factory_finish(self):
        from delivery_source_roles import plan_source_roles
        p = patch.object(source, 'pipe_identity', new=handoff._identity)
        p.start(); self.addCleanup(p.stop)
        listener, address = self.listener(); threads = []; errors = []
        self.addCleanup(listener.close)  # Publisher owns cleanup even when open is never called.
        def publish(req, deadline):
            def send():
                try:
                    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                        client.settimeout(2); client.connect(address); client.sendall(self.frame(req))
                        client.shutdown(socket.SHUT_WR)
                except BaseException as exc: errors.append(type(exc).__name__)
            t = threading.Thread(target=send); t.start(); threads.append(t)
        def open_channel(req, deadline):
            return unix.accept_unix_ingress_channel(listener, request=req, expected_uid=os.geteuid(),
                peer=self.peer, deadline=deadline, monotonic=self.clock)
        plan = self.f.plan()
        def wire(pool):
            self.s.wire(pool)
            return plan_source_roles(pool, plan=plan, finish_repositories=(self.s.repo, self.s.repo))
        builder = parent.ParentJobBuilder(config=self.f.config(), plan=plan, finish_slot=self.f.slot,
            expected_peer=self.peer, routes=self.routes, deadline=self.deadline, wire=wire,
            publish_ready=publish, open_channel=open_channel, monotonic=self.clock)
        original = runtime.RuntimeAuthorityFactory
        def factory(*a, **kw): return original(*a, **kw, wall=lambda: self.f.h.now, monotonic=self.clock)
        def build(config, *, credential, authority_factory): return self.f.app(factory=authority_factory)
        out = io.StringIO()
        with patch.object(runtime, 'RuntimeAuthorityFactory', side_effect=factory), \
             patch.object(site, 'build_application', side_effect=build), contextlib.redirect_stdout(out):
            rc = job.main(['deploy', '--execute'], declaration=job.JobSite(RUNNER, builder), context=CONTEXT,
                revision=lambda: CONTEXT.workflow_sha, identity=lambda: RUNNER)
        for t in threads: t.join(3); self.assertFalse(t.is_alive())
        self.assertEqual([], errors)
        value = json.loads(out.getvalue())
        self.assertEqual((0, 'SESSION_COMPLETED', 'RELEASED'), (rc, value['status'], value['result']['fence']), value)
        self.assertNotIn(fixture.TOKEN, out.getvalue()); self.assertIsNone(declaration.SITE)
        self.assertEqual(-1, listener.fileno()); self.assertTrue(all(t._closed for t in self.s.transports))


if __name__ == '__main__': unittest.main()
