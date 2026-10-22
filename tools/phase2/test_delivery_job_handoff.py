"""SYNTHETIC borrowed credentials only; no actual input adoption or network.

Pure ownership cases run on Windows too. Linux cases use real local pipes and
the existing driver/factory/session with memory APIs; never operational Runtime.
"""
import contextlib
from dataclasses import replace
import errno
import io
import json
import os
import socket
import sys
import unittest
from unittest.mock import Mock, patch

import delivery_job as job
import delivery_job_handoff as handoff
import delivery_job_site as declaration
import delivery_runtime_site as runtime
import delivery_site as site
import delivery_source_session as source_session
import test_delivery_source_session as fixture
from test_delivery_job import CONTEXT, RUNNER


class HandoffTests(unittest.TestCase):
    def setUp(self):
        self.s = fixture.SourceSessionTests('runTest'); self.s.setUp()
        self.addCleanup(self.s.doCleanups)
        self.f = self.s.f
        self.args = dict(config=self.f.config(), plan=self.f.plan(), finish_slot=self.f.slot,
            rosa_token=self.f.credential.token,
            sources=(handoff.SourceHandoff(self.s.repo, fixture.TOKEN,
                self.f.h.mono + 50, fixture.CA, '192.0.2.10'),),
            wire=self.s.wire, handoff_deadline=self.f.h.mono + 5,
            monotonic=lambda: self.f.h.mono)
        self.created = []
        for name in ('socket', 'getaddrinfo', 'create_connection'):
            p = patch.object(socket, name, side_effect=AssertionError('NETWORK_FORBIDDEN'))
            p.start(); self.addCleanup(p.stop)

    def call(self, command='deploy', context=CONTEXT, **changes):
        return handoff.build_owned_invocation(context, command, **(self.args | changes))

    def fake_buffer(self, raw, clock, deadline):
        # Ownership tests only: no fcntl/receive emulation is claimed as Linux.
        read, write = os.pipe(); os.close(write)
        self.created.append(read)
        return read, handoff._identity(read)

    def assert_closed(self):
        for fd in self.created:
            with self.assertRaises(OSError): os.fstat(fd)

    def test_bad_inputs_never_allocate_or_leak_exception_text(self):
        source = self.args['sources'][0]
        cases = [dict(context=replace(CONTEXT, attempt=2)), dict(command='status'),
            dict(config=None), dict(plan=Mock()), dict(rosa_token='SYNTHETIC\nSECRET'),
            dict(rosa_token='비밀'), dict(rosa_token='x'*16385), dict(sources=()),
            dict(sources=[source]), dict(sources=(replace(source, token='short'),)),
            dict(sources=(replace(source, token='x'*4097),)),
            dict(sources=(source, source)),
            dict(sources=(replace(source, local_use_until=self.f.h.mono),)),
            dict(sources=(replace(source, ca_pem=b''),)), dict(wire=None),
            dict(handoff_deadline=float('nan'))]
        with patch.object(handoff, '_prebuffer') as allocate:
            for changes in cases:
                with self.subTest(changes=list(changes)), self.assertRaises(handoff.HandoffError) as error:
                    self.call(**changes)
                self.assertIn(str(error.exception), handoff.SAFE_CODES)
            allocate.assert_not_called()

    def test_alias_denied_before_any_pipe_even_if_source_scope_valid(self):
        source = replace(self.args['sources'][0], token=self.args['rosa_token'])
        with patch.object(handoff, '_prebuffer') as allocate, \
                self.assertRaisesRegex(handoff.HandoffError, 'TOKEN_ALIASED'):
            self.call(sources=(source,))
        allocate.assert_not_called()

    def test_repr_redacts_borrowed_tokens_ca_and_endpoint(self):
        text = repr(self.args['sources'][0])
        for secret in (fixture.TOKEN, '192.0.2.10', 'BEGIN CERTIFICATE'):
            self.assertNotIn(secret, text)

    def test_linux_only_and_elapsed_deadline_precede_pipe_allocation(self):
        with patch.object(handoff, '_prebuffer') as allocate:
            with self.assertRaisesRegex(handoff.HandoffError, 'DEADLINE_EXCEEDED'):
                self.call(handoff_deadline=self.f.h.mono)
            with patch.object(sys, 'platform', 'win32'), \
                    self.assertRaisesRegex(handoff.HandoffError, 'LINUX_REQUIRED'):
                self.call()
        allocate.assert_not_called()

    def test_transfer_to_driver_close_is_idempotent_borrowed_inputs_unchanged(self):
        with patch.object(sys, 'platform', 'linux'), \
                patch.object(handoff, '_prebuffer', side_effect=self.fake_buffer):
            invocation = self.call()
        self.assertEqual(fixture.TOKEN, self.args['sources'][0].token)
        self.assertEqual(self.f.credential.token, self.args['rosa_token'])
        self.assertTrue(all(os.fstat(fd) for fd in self.created))
        self.assertTrue(invocation.close()); self.assertTrue(invocation.close())
        self.assert_closed()

    def test_second_source_allocation_failure_closes_all_previous_owned_fds(self):
        source = self.args['sources'][0]
        other = replace(source, repository=replace(source.repository, name='synthetic-other', repository_id=124))
        calls = []
        def allocate(*args):
            calls.append(1)
            if len(calls) == 3: raise OSError(errno.EMFILE, fixture.TOKEN)
            return self.fake_buffer(*args)
        with patch.object(sys, 'platform', 'linux'), \
                patch.object(handoff, '_prebuffer', side_effect=allocate), \
                self.assertRaisesRegex(handoff.HandoffError, '^P2_HANDOFF_PREPARATION_FAILED$'):
            self.call(sources=(source, other))
        self.assertEqual(2, len(self.created)); self.assert_closed()

    def test_constructor_failure_closes_pending_fd_without_masking_secret_error(self):
        with patch.object(sys, 'platform', 'linux'), \
                patch.object(handoff, '_prebuffer', side_effect=self.fake_buffer), \
                patch.object(handoff, 'GitHubPipeCredentialReceiver', side_effect=ValueError(fixture.TOKEN)), \
                self.assertRaisesRegex(handoff.HandoffError, '^P2_HANDOFF_PREPARATION_FAILED$'):
            self.call()
        self.assert_closed()

    def test_reused_fd_number_is_not_closed_again(self):
        with patch.object(handoff, '_identity', return_value=(4, 5)), \
                patch.object(os, 'close') as close:
            self.assertTrue(handoff._close_fd(111, (1, 2)))
        close.assert_not_called()

    def test_clock_regression_after_first_allocation_closes_every_owned_fd(self):
        count = []
        def allocate(*args):
            result = self.fake_buffer(*args); count.append(1)
            if len(count) == 1: self.f.h.mono -= 1
            return result
        with patch.object(sys, 'platform', 'linux'), \
                patch.object(handoff, '_prebuffer', side_effect=allocate), \
                self.assertRaises(handoff.HandoffError):
            self.call()
        self.assert_closed()

    def test_shared_original_deadline_not_reset_per_pipe_or_session(self):
        deadlines = []
        start = self.f.h.mono
        def allocate(raw, clock, deadline):
            deadlines.append(deadline)
            result = self.fake_buffer(raw, clock, deadline)
            self.f.h.mono += 1
            return result
        with patch.object(sys, 'platform', 'linux'), \
                patch.object(handoff, '_prebuffer', side_effect=allocate):
            invocation = self.call(handoff_deadline=start+100)
        try:
            self.assertEqual([start+10, start+10], deadlines)
            self.assertEqual(start+10, invocation.handoff_deadline)
        finally: invocation.close()
        self.assert_closed()

    def test_expiry_at_end_of_preparation_closes_every_owned_fd(self):
        def allocate(*args):
            result = self.fake_buffer(*args); self.f.h.mono += 3
            return result
        with patch.object(sys, 'platform', 'linux'), \
                patch.object(handoff, '_prebuffer', side_effect=allocate), \
                self.assertRaisesRegex(handoff.HandoffError, 'DEADLINE_EXCEEDED'):
            self.call()
        self.assert_closed()

    def test_interrupt_closes_owned_handles_preserves_borrowed_pipe(self):
        borrowed, writer = os.pipe(); self.addCleanup(os.close, borrowed); os.close(writer)
        count = []
        def allocate(*args):
            count.append(1)
            if len(count) == 2: raise KeyboardInterrupt()
            return self.fake_buffer(*args)
        with patch.object(sys, 'platform', 'linux'), \
                patch.object(handoff, '_prebuffer', side_effect=allocate), self.assertRaises(KeyboardInterrupt):
            self.call()
        self.assert_closed(); self.assertIsNotNone(os.fstat(borrowed))

    def test_same_source_token_across_distinct_repositories_is_allowed(self):
        source = self.args['sources'][0]
        other = replace(source, repository=replace(source.repository, name='synthetic-other', repository_id=124))
        with patch.object(sys, 'platform', 'linux'), \
                patch.object(handoff, '_prebuffer', side_effect=self.fake_buffer):
            invocation = self.call(sources=(source, other))
        self.assertEqual(3, len(self.created)); invocation.close(); self.assert_closed()

    def test_driver_status_never_invokes_parent_builder_or_pipe(self):
        builder = Mock(side_effect=AssertionError('BUILDER_FORBIDDEN'))
        with patch.object(handoff, '_prebuffer') as allocate, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(0, job.main(['status'], declaration=job.JobSite(RUNNER, builder)))
        builder.assert_not_called(); allocate.assert_not_called(); self.assertIsNone(declaration.SITE)


@unittest.skipUnless(sys.platform == 'linux', 'real prebuffer/EOF/driver path requires Linux')
class LinuxHandoffTests(unittest.TestCase):
    call = HandoffTests.call

    def setUp(self):
        HandoffTests.setUp(self)
        # Replace only the Windows identity shim. Reinitializing the fixture
        # would split the invocation's finish slot from the app's writer slot.
        p = patch.object(source_session, 'pipe_identity', new=handoff._identity)
        p.start(); self.addCleanup(p.stop)

    def test_real_prebuffer_has_eof_noninherit_nonblock_distinct_pipes(self):
        invocation = self.call()
        rosa = invocation.receiver; source = invocation.source_session.channels[0].receiver
        try:
            self.assertNotEqual(handoff._identity(rosa._fd), handoff._identity(source._fd))
            for rec in (rosa, source):
                self.assertFalse(os.get_inheritable(rec._fd)); self.assertFalse(os.get_blocking(rec._fd))
            deadline = invocation.source_session.prepare(rosa, invocation.handoff_deadline)
            self.assertEqual(self.args['rosa_token'], rosa.receive(deadline).token)
            received = source.receive(deadline)
            self.assertEqual(fixture.TOKEN, received.token); received.close()
            with self.assertRaises(Exception): rosa.receive(deadline)
        finally: invocation.close()

    def test_capacity_shortwrite_and_os_write_errors_are_not_retried(self):
        import fcntl
        native = os.pipe2
        pairs = []
        def pipe(flags):
            result = native(flags); pairs.extend(result); return result
        cases = [('capacity', None), ('short', 1), ('eagain', BlockingIOError(errno.EAGAIN, fixture.TOKEN)),
                 ('eintr', InterruptedError(errno.EINTR, fixture.TOKEN))]
        for name, result in cases:
            pairs.clear()
            write = Mock(side_effect=result) if isinstance(result, Exception) else Mock(return_value=result)
            with self.subTest(name=name), patch.object(os, 'pipe2', side_effect=pipe), \
                    patch.object(fcntl, 'fcntl', return_value=1 if name == 'capacity' else 65536), \
                    patch.object(os, 'write', write), self.assertRaises(handoff.HandoffError):
                handoff._prebuffer(b'SYNTHETIC', lambda: 1, 5)
            self.assertEqual(0 if name == 'capacity' else 1, write.call_count)
            for fd in pairs:
                with self.assertRaises(OSError): os.fstat(fd)

    def test_receiver_constructor_closed_reused_fd_survives_parent_cleanup(self):
        borrowed = []
        def constructor(fd, **unused):
            os.close(fd)
            read, write = os.pipe(); borrowed.extend((read, write))
            self.assertEqual(fd, read)  # Linux normally takes the lowest free FD.
            raise ValueError(fixture.TOKEN)
        try:
            with patch.object(handoff, 'PipeCredentialReceiver', side_effect=constructor), \
                    self.assertRaises(ValueError):
                handoff._receiver(b'SYNTHETIC', lambda: 1, 5)
            self.assertTrue(all(os.fstat(fd) for fd in borrowed))
        finally:
            for fd in borrowed: os.close(fd)

    def test_prebuffer_interrupt_after_write_closes_both_owned_ends(self):
        native = os.pipe2; pairs = []
        def pipe(flags):
            result = native(flags); pairs.extend(result); return result
        clock = Mock(side_effect=[1, 1, KeyboardInterrupt()])
        with patch.object(os, 'pipe2', side_effect=pipe), self.assertRaises(KeyboardInterrupt):
            handoff._prebuffer(b'SYNTHETIC', clock, 5)
        for fd in pairs:
            with self.assertRaises(OSError): os.fstat(fd)

    def test_real_parent_driver_source_factory_receipt_and_cleanup(self):
        from delivery_source_roles import plan_source_roles
        def wire(pool):
            self.s.wire(pool)  # SYNTHETIC native API boundary only.
            return plan_source_roles(pool, plan=self.args['plan'],
                finish_repositories=(self.s.repo, self.s.repo))
        self.args['wire'] = wire
        invocation = self.call()
        owned = [invocation.receiver] + [c.receiver for c in invocation.source_session.channels]
        original = runtime.RuntimeAuthorityFactory
        def factory(*a, **kw):
            return original(*a, **kw, wall=lambda: self.f.h.now, monotonic=lambda: self.f.h.mono)
        def build(config, *, credential, authority_factory):
            self.assertEqual(self.args['rosa_token'], credential.token)
            return self.f.app(factory=authority_factory)
        out = io.StringIO()
        with patch.object(runtime, 'RuntimeAuthorityFactory', side_effect=factory), \
                patch.object(site, 'build_application', side_effect=build), contextlib.redirect_stdout(out):
            rc = job.main(['deploy', '--execute'], declaration=job.JobSite(RUNNER, lambda *a: invocation),
                context=CONTEXT, revision=lambda: CONTEXT.workflow_sha, identity=lambda: RUNNER)
        result = json.loads(out.getvalue())
        self.assertEqual((0, 'SESSION_COMPLETED', 'RELEASED'),
            (rc, result['status'], result['result']['fence']), result)
        self.assertEqual(1, self.f.p.c.storage.complete_calls)
        self.assertTrue(all(rec._fd is None for rec in owned))
        self.assertTrue(all(t._closed for t in self.s.transports))
        self.assertNotIn(fixture.TOKEN, out.getvalue())


if __name__ == '__main__': unittest.main()
