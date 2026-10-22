"""SYNTHETIC native requests/credentials; local Linux IPC, never SSH/Runtime."""
import contextlib
import copy
import io
import json
import os
import pickle
import socket
import sys
import threading
import time
import unittest
from unittest.mock import Mock, patch

import delivery_crosshost_ingress as ingress
import delivery_job as job
import delivery_job_handoff as handoff
import delivery_job_parent as parent
import delivery_job_rendezvous as rendezvous
import delivery_job_site as declaration
import delivery_runtime_site as runtime
import delivery_site as site
import delivery_source_session as source
import delivery_unix_ingress as unix
import test_delivery_unix_ingress as fixture
from test_delivery_job import CONTEXT, RUNNER


class RendezvousPolicyTests(unittest.TestCase):
    def setUp(self):
        self.peer = ingress.PeerIdentity('SYNTHETIC-local', 'SYNTHETIC-uid-mapping')
        self.clock = lambda: 10

    def owner(self, **kw):
        return rendezvous.UnixJobRendezvous(**(dict(expected_uid=1001, peer=self.peer,
            deadline=20, monotonic=self.clock, publish_ready=Mock()) | kw))

    def builder(self, owner, **kw):
        return parent.ParentJobBuilder(**(dict(config=None, plan=None, finish_slot=None,
            expected_peer=self.peer, routes=(), deadline=20, wire=Mock(), monotonic=self.clock,
            publish_ready=owner.publish_ready, open_channel=owner.open_channel) | kw))

    def test_address_is_canonical_pure_and_bound_to_every_public_request_field(self):
        request = fixture.request()
        with patch.object(socket, 'socket', side_effect=AssertionError('NO_SOCKET')), \
             patch('builtins.open', side_effect=AssertionError('NO_FILE')):
            first = rendezvous.abstract_address(request)
            reordered = {k: request[k] for k in reversed(request)}
            self.assertEqual(first, rendezvous.abstract_address(reordered))
            self.assertTrue(first.startswith(b'\0fds-p2-')); self.assertLess(len(first), 108)
            for field in ('nonce', 'run_id', 'binding_key', 'repositories'):
                changed = copy.deepcopy(request)
                if field == 'nonce': changed[field] = 'f' * 64
                elif field == 'repositories': changed[field] = ['SYNTHETIC/other']
                else: changed['target'][field] = '456' if field == 'run_id' else '3' * 64
                self.assertNotEqual(first, rendezvous.abstract_address(changed))

    def test_constructor_and_wrapper_do_not_publish_open_read_clock_or_configure_site(self):
        clock = Mock(side_effect=AssertionError('CLOCK_NOT_YET'))
        owner = self.owner(monotonic=clock)
        builder = self.builder(owner, monotonic=clock)
        with patch.object(socket, 'socket', side_effect=AssertionError('NO_SOCKET')):
            wrapped = rendezvous.OwnedRendezvousBuilder(builder, owner)
        clock.assert_not_called(); owner._publish.assert_not_called()
        self.assertIsNone(declaration.SITE); self.assertIsNone(owner._listener)
        for obj in (wrapped, owner):
            for clone in (copy.copy, copy.deepcopy, pickle.dumps):
                with self.assertRaises(Exception): clone(obj)
        self.assertTrue(owner.close())

    def test_root_bool_invalid_uid_and_foreign_parent_owner_are_not_adopted(self):
        for uid in (0, True, -1, 0xFFFFFFFF):
            with self.assertRaises(rendezvous.RendezvousError): self.owner(expected_uid=uid)
        foreign = Mock()
        with self.assertRaises(rendezvous.RendezvousError): rendezvous.OwnedRendezvousBuilder(foreign, self.owner())
        foreign.close.assert_not_called()

    def test_parent_must_use_same_owner_bound_methods_clock_peer_and_deadline(self):
        owner = self.owner()
        for kw in (dict(publish_ready=Mock()), dict(open_channel=Mock()), dict(deadline=21),
                   dict(monotonic=lambda: 10), dict(expected_peer=ingress.PeerIdentity('other', 'other'))):
            with self.assertRaises(rendezvous.RendezvousError):
                rendezvous.OwnedRendezvousBuilder(self.builder(owner, **kw), owner)

    def test_status_and_gate_never_allocate_rendezvous(self):
        owner = self.owner(); wrapped = rendezvous.OwnedRendezvousBuilder(self.builder(owner), owner)
        declaration_value = job.JobSite(RUNNER, wrapped)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(0, job.main(['status'], declaration=declaration_value))
            self.assertEqual(0, job.main(['gate'], declaration=declaration_value, context=CONTEXT,
                revision=lambda: CONTEXT.workflow_sha))
        self.assertFalse(wrapped._used); self.assertFalse(owner._published)
        owner._publish.assert_not_called()

    def test_foreign_parent_return_is_denied_without_arbitrary_close(self):
        owner = self.owner(); wrapped = rendezvous.OwnedRendezvousBuilder(self.builder(owner), owner)
        foreign = Mock()
        with patch.object(parent.ParentJobBuilder, '__call__', return_value=foreign):
            with self.assertRaisesRegex(rendezvous.RendezvousError, 'BUILD_FAILED'): wrapped(CONTEXT, 'deploy')
        foreign.close.assert_not_called(); self.assertTrue(owner._closed)

    def test_check_shrinks_budget_used_by_callback_and_accept(self):
        owner = self.owner(); owner._check(15)
        self.assertEqual(15, owner._budget.limit)
        owner._check(20); self.assertEqual(15, owner._budget.limit)
        self.assertTrue(owner.close())


@unittest.skipUnless(sys.platform == 'linux' and hasattr(os, 'geteuid') and os.geteuid() > 0,
                     'local abstract rendezvous/peer UID needs nonroot Linux')
class RendezvousLinuxTests(unittest.TestCase):
    setUp = fixture.UnixSocketTests.setUp
    receiver = fixture.UnixSocketTests.receiver
    frame = fixture.UnixSocketTests.frame

    def owner(self, callback=None, **kw):
        return rendezvous.UnixJobRendezvous(**(dict(expected_uid=os.geteuid(), peer=self.peer,
            deadline=self.deadline, monotonic=self.clock, publish_ready=callback or Mock()) | kw))

    def client(self, request):
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sockets.append(client); client.settimeout(2)
        client.connect(rendezvous.abstract_address(request)); return client

    def absent(self, request):
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(0.1)
            with self.assertRaises(OSError): client.connect(rendezvous.abstract_address(request))

    def wrapped(self, callback, **changes):
        owner = self.owner(callback)
        kwargs = dict(config=self.f.config(), plan=self.f.plan(), finish_slot=self.f.slot,
            expected_peer=self.peer, routes=self.routes, deadline=self.deadline,
            wire=self.s.wire, monotonic=self.clock, publish_ready=owner.publish_ready, open_channel=owner.open_channel)
        kwargs.update(changes)
        builder = parent.ParentJobBuilder(**kwargs)
        return rendezvous.OwnedRendezvousBuilder(builder, owner), owner

    def sender(self, req, deadline):
        client = self.client(req); client.sendall(self.frame(req)); client.shutdown(socket.SHUT_WR)

    def test_listener_is_ready_before_callback_value_bound_channel_and_close(self):
        receiver = self.receiver(); request = receiver.request()
        owner = self.owner(self.sender)
        owner.publish_ready(copy.deepcopy(request), self.deadline)
        channel = owner.open_channel(copy.deepcopy(request), self.deadline)
        bundle = receiver.receive(channel); bundle.close()
        self.assertTrue(owner.close()); self.assertTrue(owner.close()); self.absent(request)

    def test_publish_exception_interrupt_and_late_result_close_their_listener(self):
        for exc in (ValueError(fixture.fixture.TOKEN), KeyboardInterrupt(fixture.fixture.TOKEN)):
            receiver = self.receiver(); request = receiver.request()
            def bad(*args): raise exc
            owner = self.owner(bad)
            expected = KeyboardInterrupt if type(exc) is KeyboardInterrupt else rendezvous.RendezvousError
            with self.assertRaises(expected) as caught: owner.publish_ready(request, self.deadline)
            self.assertNotIn(fixture.fixture.TOKEN, str(caught.exception)); self.absent(request)
            self.assertTrue(owner.close())
        receiver = self.receiver(); request = receiver.request()
        def late(*args): self.f.h.mono = self.deadline
        owner = self.owner(late)
        with self.assertRaisesRegex(rendezvous.RendezvousError, 'DEADLINE'): owner.publish_ready(request, self.deadline)
        self.absent(request)

    def test_public_callback_cannot_rewrite_stored_job_nonce_or_repositories(self):
        receiver = self.receiver(); request = receiver.request()
        def mutate(value, deadline):
            self.sender(copy.deepcopy(value), deadline)
            value['nonce'] = 'f' * 64; value['repositories'].append('SYNTHETIC/other')
        owner = self.owner(mutate); owner.publish_ready(request, self.deadline)
        bundle = receiver.receive(owner.open_channel(request, self.deadline)); bundle.close()
        self.assertTrue(owner.close())

    def test_open_mismatch_or_close_without_open_consumes_and_releases_abstract_name(self):
        request = self.receiver().request(); owner = self.owner()
        owner.publish_ready(request, self.deadline)
        bad = copy.deepcopy(request); bad['nonce'] = 'f' * 64
        with patch.object(unix, 'accept_unix_ingress_channel') as accept:
            with self.assertRaises(rendezvous.RendezvousError): owner.open_channel(bad, self.deadline)
            accept.assert_not_called()
        self.absent(request)
        with self.assertRaisesRegex(rendezvous.RendezvousError, 'CONSUMED'): owner.open_channel(request, self.deadline)
        request = self.receiver().request(); owner = self.owner()
        owner.publish_ready(request, self.deadline); self.assertTrue(owner.close()); self.absent(request)

    def test_bind_collision_never_closes_or_deletes_foreign_listener(self):
        request = self.receiver().request()
        foreign = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); self.sockets.append(foreign)
        foreign.bind(rendezvous.abstract_address(request)); foreign.listen(1)
        owner = self.owner()
        with self.assertRaises(rendezvous.RendezvousError): owner.publish_ready(request, self.deadline)
        self.assertGreaterEqual(foreign.fileno(), 0)
        self.client(request)  # Foreign listener is still there.
        self.assertTrue(owner.close())

    def test_no_client_accept_expires_and_listener_is_released(self):
        request = self.receiver().request(); deadline = time.monotonic() + 0.04
        owner = self.owner(deadline=deadline, monotonic=time.monotonic)
        owner.publish_ready(request, deadline)
        with self.assertRaises(rendezvous.RendezvousError): owner.open_channel(request, deadline)
        self.assertTrue(owner.close()); self.absent(request)

    def test_wrong_uid_still_denies_before_core_secret_read(self):
        receiver = self.receiver(); request = receiver.request()
        owner = self.owner(self.sender, expected_uid=os.geteuid() + 1)
        owner.publish_ready(request, self.deadline); channel = owner.open_channel(request, self.deadline)
        with patch.object(fixture.REAL_SOCKET, 'recv', side_effect=AssertionError('SECRET_READ_FORBIDDEN')) as read:
            with self.assertRaises(ingress.IngressError): receiver.receive(channel)
            read.assert_not_called()
        self.assertTrue(owner.close()); self.assertEqual([], self.f.p.c.storage.claims)

    def test_close_during_publication_makes_late_publication_unusable(self):
        entered = threading.Event(); release = threading.Event(); errors = []
        def publishing(*args): entered.set(); self.assertTrue(release.wait(2))
        request = self.receiver().request(); owner = self.owner(publishing)
        def call():
            try: owner.publish_ready(request, self.deadline)
            except BaseException as exc: errors.append(exc)
        t = threading.Thread(target=call); t.start(); self.assertTrue(entered.wait(2))
        try: self.assertTrue(owner.close())
        finally: release.set(); t.join(3)
        self.assertFalse(t.is_alive()); self.assertEqual(1, len(errors))
        self.assertIs(type(errors[0]), rendezvous.RendezvousError); self.absent(request)

    def test_parent_open_failure_after_publication_is_cleaned_by_owner_wrapper(self):
        wrapped, owner = self.wrapped(Mock()); seen = []
        owner._publish = lambda request, deadline: seen.append(copy.deepcopy(request))
        def fail(*args): raise ValueError(fixture.fixture.TOKEN)
        wrapped._parent._open = fail  # Trusted boundary fault after constructor binding check.
        with self.assertRaisesRegex(parent.ParentBuildError, '^P2_PARENT_BUILD_FAILED$'):
            wrapped(CONTEXT, 'deploy')
        self.assertTrue(owner._closed); self.absent(seen[0]); self.assertEqual([], self.f.h.requests)

    def test_shape_failure_after_creation_closes_owned_socket_once(self):
        made = []
        def create(*args, **kwargs):
            sock = fixture.REAL_SOCKET(*args, **kwargs); made.append(sock); return sock
        owner = self.owner(); request = self.receiver().request(); close = unix._close
        with patch.object(socket, 'socket', new=create), \
             patch.object(unix, '_shape', side_effect=OSError(fixture.fixture.TOKEN)), \
             patch.object(unix, '_close', wraps=close) as cleanup:
            with self.assertRaises(rendezvous.RendezvousError) as caught:
                owner.publish_ready(request, self.deadline)
            self.assertEqual(1, cleanup.call_count)
            self.assertTrue(owner.close()); self.assertEqual(1, cleanup.call_count)
        self.assertEqual(-1, made[0].fileno()); self.assertNotIn(fixture.fixture.TOKEN, str(caught.exception))

    def test_open_argument_copy_failure_happens_before_listener_transfer(self):
        owner = self.owner(); request = self.receiver().request(); owner.publish_ready(request, self.deadline)
        sock = owner._listener
        with patch.object(copy, 'deepcopy', side_effect=ValueError(fixture.fixture.TOKEN)), \
             patch.object(unix, 'accept_unix_ingress_channel') as accept:
            with self.assertRaises(rendezvous.RendezvousError) as caught:
                owner.open_channel(request, self.deadline)
            accept.assert_not_called()
        self.assertEqual(-1, sock.fileno()); self.assertTrue(owner.close()); self.absent(request)
        self.assertNotIn(fixture.fixture.TOKEN, str(caught.exception))

    def test_shorter_open_deadline_is_not_replaced_by_owner_original(self):
        request = self.receiver().request(); original = time.monotonic() + 10
        owner = self.owner(deadline=original, monotonic=time.monotonic)
        owner.publish_ready(request, original); end = time.monotonic() + 0.04; start = time.monotonic()
        with self.assertRaises(rendezvous.RendezvousError): owner.open_channel(request, end)
        self.assertLess(time.monotonic() - start, 2); self.assertEqual(end, owner._budget.limit)
        self.assertTrue(owner.close()); self.absent(request)

    def test_cleanup_uncertainty_or_lateness_closes_untransferred_actual_invocation(self):
        p = patch.object(source, 'pipe_identity', new=handoff._identity); p.start(); self.addCleanup(p.stop)
        for mode in ('uncertain', 'late', 'raise'):
            self.f.h.mono = 100; self.deadline = 105
            wrapped, owner = self.wrapped(self.sender); values = []
            original_call, original_close = parent.ParentJobBuilder.__call__, owner.close
            def capture(instance, *args):
                value = original_call(instance, *args); values.append(value); return value
            def cleanup():
                ok = original_close()
                if mode == 'raise': raise ValueError(fixture.fixture.TOKEN)
                if mode == 'late': self.f.h.mono = self.deadline
                return False if mode == 'uncertain' else ok
            with patch.object(parent.ParentJobBuilder, '__call__', new=capture), \
                 patch.object(owner, 'close', side_effect=cleanup):
                with self.assertRaises(rendezvous.RendezvousError): wrapped(CONTEXT, 'deploy')
            self.assertEqual(1, len(values)); self.assertTrue(values[0]._closed)
            self.assertIsNone(values[0].receiver._fd)
            self.assertEqual([], self.f.h.requests); self.assertEqual([], self.f.p.c.storage.claims)

    def test_actual_owned_rendezvous_parent_pipe_driver_factory_finish(self):
        p = patch.object(source, 'pipe_identity', new=handoff._identity); p.start(); self.addCleanup(p.stop)
        wrapped, owner = self.wrapped(self.sender)
        original = runtime.RuntimeAuthorityFactory
        def factory(*a, **kw): return original(*a, **kw, wall=lambda: self.f.h.now, monotonic=self.clock)
        def build(config, *, credential, authority_factory): return self.f.app(factory=authority_factory)
        out = io.StringIO()
        with patch.object(runtime, 'RuntimeAuthorityFactory', side_effect=factory), \
             patch.object(site, 'build_application', side_effect=build), contextlib.redirect_stdout(out):
            rc = job.main(['deploy', '--execute'], declaration=job.JobSite(RUNNER, wrapped), context=CONTEXT,
                revision=lambda: CONTEXT.workflow_sha, identity=lambda: RUNNER)
        result = json.loads(out.getvalue())
        self.assertEqual((0, 'SESSION_COMPLETED', 'RELEASED'), (rc, result['status'], result['result']['fence']), result)
        self.assertTrue(owner._closed); self.assertIsNone(owner._listener)
        self.assertNotIn(fixture.fixture.TOKEN, out.getvalue()); self.assertIsNone(declaration.SITE)
        self.assertTrue(all(t._closed for t in self.s.transports))
        with self.assertRaisesRegex(rendezvous.RendezvousError, 'CONSUMED'): wrapped(CONTEXT, 'deploy')


if __name__ == '__main__': unittest.main()
