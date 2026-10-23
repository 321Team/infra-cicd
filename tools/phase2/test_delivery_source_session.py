"""SYNTHETIC channels, sources and APIs only; no Runtime or outbound network.

Reuse real factory/session/reconciler and MemoryStore. Separate Linux cases
exercise real anonymous pipes. No fake token is operational input/evidence.
"""
import contextlib
import copy
import io
import json
import os
import pickle
import sys
import time
import unittest
from dataclasses import replace
from unittest.mock import patch, Mock

import delivery_source_session as source
import delivery_runtime_site as runtime
import delivery_site as site
from delivery_credential_channel import PipeCredentialReceiver
from delivery_github_source import RepositoryPin
from delivery_private_source import GitHubPipeCredentialReceiver, GitHubReadCredential
from delivery_object_reader import BearerCredential
from test_delivery_transport import CA
import test_delivery_runtime_site as fixture

TOKEN = 'SYNTHETIC_PRIVATE_SOURCE_TOKEN_0123456789'


class SourceSessionTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture.RuntimeSiteTests('runTest'); self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.repo = self.f.pins['approval'].repository
        self.made, self.receivers, self.transports, self.blobs = [], [], [], []
        self.rosa = self.receiver()
        self.session = source.PrivateSourceSession((self.channel(),), wire=self.wire,
            monotonic=lambda: self.f.h.mono)
        self.addCleanup(self.session.close)
        # Pure semantic/ownership tests: Windows anonymous fstat has zero IDs.
        # Linux-specific cases below use REAL fstat/pipe identity, without this patch.
        p = patch.object(source, 'pipe_identity', side_effect=lambda fd: (1, fd))
        p.start(); self.addCleanup(p.stop)

    def receiver(self, repo=None, token=TOKEN):
        read, write = os.pipe(); os.close(write)
        if repo is None:
            obj = PipeCredentialReceiver(read)
            returned = self.f.credential
        else:
            obj = GitHubPipeCredentialReceiver(read, repository=repo,
                local_use_until=self.f.h.mono + 50, monotonic=lambda: self.f.h.mono)
            returned = GitHubReadCredential(token, repo, self.f.h.mono + 50)
            self.made.append(returned)
        p = patch.object(obj, 'receive', return_value=returned)
        p.start(); self.addCleanup(p.stop); self.addCleanup(obj.close)
        self.receivers.append(obj)
        return obj

    def channel(self, repo=None, token=TOKEN):
        return source.SourceChannel(self.receiver(repo or self.repo, token), CA, '192.0.2.10')

    def wire(self, session):
        reader = session.reader(self.repo)
        self.transports.append(reader._transport)
        def blob(request, pin):
            self.assertFalse(reader._transport._closed)
            self.assertFalse(reader._transport._credential._closed)
            self.blobs.append(pin.path)
            return self.f.reader.blob(request, pin)
        reader.blob = blob  # Native server boundary only; resolver/session remain real.
        return {'readers': tuple(runtime.SourceReaders(reader, reader) for _ in range(4))}

    def call(self, args=('deploy',), **changes):
        kwargs = dict(config=self.f.config(), plan=self.f.plan(), finish_slot=self.f.slot,
            receiver=self.rosa, handoff_deadline=self.f.h.mono + 5, source_session=self.session)
        kwargs.update(changes)
        original = runtime.RuntimeAuthorityFactory
        def factory(*a, **kw):
            return original(*a, **kw, wall=lambda: self.f.h.now, monotonic=lambda: self.f.h.mono)
        def build(config, *, credential, authority_factory):
            self.assertIs(credential, self.f.credential)
            return self.f.app(factory=authority_factory)
        out = io.StringIO()
        with patch.object(runtime, 'RuntimeAuthorityFactory', side_effect=factory), \
                patch.object(site, 'build_application', side_effect=build), contextlib.redirect_stdout(out):
            rc = runtime.main(list(args), **kwargs)
        text = out.getvalue()
        self.assertEqual(1, len(text.strip().splitlines()))
        self.assertNotIn(TOKEN, text)
        return rc, json.loads(text)

    def closed(self):
        self.assertTrue(all(r._fd is None for r in self.receivers))
        self.assertTrue(all(c._closed and c.token == '' for c in self.made))
        self.assertTrue(all(t._closed and t._credential is None for t in self.transports))

    def prepared(self, callback=None):
        from test_delivery_job import CONTEXT
        prepared = runtime.PreparedAuthorityPlan(binding=self.f.config().binding,
            materialize=callback or (lambda *args: self.f.plan()))
        prepared.bind_context(CONTEXT)
        self.addCleanup(prepared.close)
        return prepared

    def test_prepared_materializes_after_both_receipts_with_real_factory_authentication(self):
        from test_delivery_job import CONTEXT
        calls = []
        expected_deadline = self.f.h.mono + 5
        def materialize(context, credential, session, roles, deadline):
            self.assertEqual(CONTEXT, context)
            self.assertIs(credential, self.f.credential)
            self.assertIs(session, self.session)
            self.assertEqual('CONSUMED', session._state)
            for receiver in self.receivers: receiver.receive.assert_called_once()
            with self.assertRaises(TypeError): roles['readers'] = ()
            calls.append(deadline)
            return self.f.plan()
        prepared = self.prepared(materialize)
        rc, result = self.call(plan=prepared)
        self.assertEqual(0, rc, result)
        self.assertEqual('SESSION_COMPLETED', result['status'])
        self.assertEqual('RELEASED', result['fence'])
        self.assertEqual([expected_deadline], calls)
        self.assertIn('control', self.blobs); self.assertIn('platform', self.blobs)
        self.assertIsNone(prepared._materialize); self.assertIsNone(prepared._context)
        self.closed()

    def test_prepared_status_never_receives_or_materializes(self):
        callback = Mock(side_effect=AssertionError('MATERIALIZE_FORBIDDEN'))
        prepared = self.prepared(callback)
        rc, result = self.call(('status',), plan=prepared)
        self.assertEqual((0, 'NOT_RUN'), (rc, result['runtime']))
        callback.assert_not_called()
        for receiver in self.receivers: receiver.receive.assert_not_called()
        self.assertEqual([], self.blobs); self.assertEqual([], self.f.p.c.storage.claims)
        self.assertTrue(prepared._closed)

    def test_materializer_error_drops_private_exception_chain_and_owned_frame_references(self):
        deadline = self.session.prepare(self.rosa, self.f.h.mono + 5)
        roles = self.session.open(self.rosa.receive(deadline))
        for kind in (ValueError, runtime.RuntimeSiteError, KeyboardInterrupt, SystemExit):
            def materialize(*args, error_type=kind):
                private_original = TOKEN
                raise error_type(private_original)
            prepared = self.prepared(materialize)
            expected = runtime.RuntimeSiteError if issubclass(kind, Exception) else kind
            try:
                prepared.resolve(credential=self.f.credential, source=self.session,
                                 roles=roles, deadline=deadline)
            except expected as caught:
                self.assertIsNone(caught.__context__)
                self.assertIsNone(caught.__cause__)
                self.assertNotIn(TOKEN, str(caught))
                names = []
                tb = caught.__traceback__
                while tb:
                    names.append(tb.tb_frame.f_code.co_name)
                    if tb.tb_frame.f_code is runtime.PreparedAuthorityPlan.resolve.__code__:
                        for name in ('materialize', 'credential', 'source', 'roles', 'plan'):
                            self.assertIsNone(tb.tb_frame.f_locals[name], name)
                    tb = tb.tb_next
                self.assertNotIn('materialize', names)
            else:
                self.fail('materialization failure must stop')
            self.assertIsNone(prepared._materialize)
            # resolve borrows this session; its owner still closes it.
            self.assertEqual('CONSUMED', self.session._state)
            self.assertFalse(self.made[0]._closed)
            self.assertEqual([], self.f.p.c.storage.claims)
            self.assertEqual([], self.f.h.requests)

    def test_rejected_materializer_result_drops_returned_plan_from_error_frame(self):
        deadline = self.session.prepare(self.rosa, self.f.h.mono + 5)
        roles = self.session.open(self.rosa.receive(deadline))
        prepared = self.prepared(lambda *unused: {'private_original': TOKEN})
        try:
            prepared.resolve(credential=self.f.credential, source=self.session,
                             roles=roles, deadline=deadline)
        except runtime.RuntimeSiteError as caught:
            self.assertIsNone(caught.__context__)
            tb = caught.__traceback__
            while tb:
                if tb.tb_frame.f_code is runtime.PreparedAuthorityPlan.resolve.__code__:
                    self.assertIsNone(tb.tb_frame.f_locals['plan'])
                tb = tb.tb_next
        else:
            self.fail('untyped plan cannot be adopted')
        self.assertEqual([], self.f.p.c.storage.claims)

    def test_materialized_plan_still_requires_fresh_original_control_before_claim(self):
        def withdrawn(request, pin, original):
            return replace(original, body=b'SYNTHETIC_WITHDRAWN_CONTROL') if pin.path == 'control' else original
        self.f.read_hook = withdrawn
        materialize = Mock(side_effect=lambda *args: self.f.plan())
        rc, result = self.call(plan=self.prepared(materialize))
        self.assertEqual(2, rc)
        materialize.assert_called_once()
        self.assertEqual([], self.f.p.c.storage.claims)
        self.assertEqual([], self.f.h.requests)
        self.assertEqual(0, self.f.p.c.storage.complete_calls)
        self.closed()

    def test_materializer_failure_never_reaches_claim_or_echoes_secret_and_consumes_plan(self):
        prepared = self.prepared(Mock(side_effect=ValueError(TOKEN)))
        rc, result = self.call(plan=prepared)
        self.assertEqual((2, 'SOURCE_SESSION_SITE_REJECTED'), (rc, result['error_code']))
        self.assertEqual([], self.f.p.c.storage.claims)
        with self.assertRaisesRegex(runtime.RuntimeSiteError, 'CONSUMED'):
            prepared.resolve(credential=self.f.credential, source=self.session, roles={}, deadline=105)
        self.closed()

    def test_late_materialization_does_not_renew_deadline_or_start_application(self):
        expected_deadline = self.f.h.mono + 5
        def late(*args):
            self.f.h.mono = expected_deadline
            return self.f.plan()
        rc, result = self.call(plan=self.prepared(late))
        self.assertEqual((2, 'NOT_RUN'), (rc, result['runtime']))
        self.assertEqual([], self.f.p.c.storage.claims)
        self.assertEqual(expected_deadline, self.session.deadline)
        self.closed()

    def test_received_token_fingerprint_and_binding_must_match_materialized_plan(self):
        for index, field in enumerate(('credential', 'binding')):
            plan = self.f.plan()
            if field == 'credential':
                plan = replace(plan, issuer=replace(plan.issuer,
                    facts=replace(plan.issuer.facts, credential_fingerprint='0'*64)))
            else:
                plan = replace(plan, approval=replace(plan.approval,
                    binding=replace(plan.approval.binding, run_id='SYNTHETIC_OTHER_RUN')))
            # Independent source/FD ownership for each failure case.
            with self.subTest(field=field):
                if index:
                    self.rosa = self.receiver()
                    self.session = source.PrivateSourceSession((self.channel(),), wire=self.wire,
                        monotonic=lambda: self.f.h.mono)
                rc, result = self.call(plan=self.prepared(lambda *args, p=plan: p))
                self.assertEqual((2, 'NOT_RUN'), (rc, result['runtime']))
                self.assertEqual([], self.f.p.c.storage.claims)
        self.closed()

    def test_preparation_cannot_be_rebound_copied_or_used_without_source_receipt(self):
        prepared = self.prepared()
        with self.assertRaisesRegex(runtime.RuntimeSiteError, 'CONTEXT_INVALID'):
            prepared.bind_context(replace(prepared._context, run_id='456'))
        for operation in (copy.copy, copy.deepcopy, pickle.dumps):
            with self.assertRaises(runtime.RuntimeSiteError): operation(prepared)
        with self.assertRaisesRegex(runtime.RuntimeSiteError, 'MATERIALIZATION_FAILED'):
            prepared.resolve(credential=self.f.credential, source=self.session,
                             roles={}, deadline=105)
        self.assertEqual([], self.f.p.c.storage.claims)

    def test_callback_cannot_close_preparation_then_return_a_plan(self):
        prepared = None
        def closing(*args):
            prepared.close()
            return self.f.plan()
        prepared = self.prepared(closing)
        rc, result = self.call(plan=prepared)
        self.assertEqual((2, 'NOT_RUN'), (rc, result['runtime']))
        self.assertEqual([], self.f.p.c.storage.claims); self.closed()

    def test_materializer_interrupts_are_sanitized_and_all_handles_closed(self):
        for index, error in enumerate((KeyboardInterrupt(TOKEN), SystemExit(TOKEN))):
            with self.subTest(kind=type(error).__name__):
                if index:
                    self.rosa = self.receiver()
                    self.session = source.PrivateSourceSession((self.channel(),), wire=self.wire,
                        monotonic=lambda: self.f.h.mono)
                with self.assertRaises(type(error)) as caught:
                    self.call(plan=self.prepared(Mock(side_effect=error)))
                self.assertEqual(() if type(error) is KeyboardInterrupt else (130,), caught.exception.args)
        self.closed()

    def test_real_factory_session_complete_owns_sources_not_borrowed_reader(self):
        borrowed = self.f.reader
        with patch.object(borrowed, 'blob', wraps=borrowed.blob):
            rc, result = self.call()
        self.assertEqual(0, rc); self.assertEqual('SESSION_COMPLETED', result['status'], result)
        self.assertEqual('RELEASED', result['fence']); self.assertEqual(1, self.f.p.c.storage.complete_calls)
        self.assertIn('finish', self.blobs); self.assertIn('control', self.blobs)
        self.assertIsNotNone(borrowed._transport)
        self.closed()
        with self.assertRaises(source.SourceSessionError): self.session.reader(self.repo)

    def test_status_no_receipt_wire_tls_source_file_or_claim(self):
        with patch.object(self.session, 'wire', side_effect=AssertionError('WIRE_FORBIDDEN')), \
                patch.object(source, 'pipe_identity', side_effect=AssertionError('FSTAT_FORBIDDEN')), \
                patch.object(source, 'PrivateGitHubTLS', side_effect=AssertionError('TLS_FORBIDDEN')):
            rc, result = self.call(('status',))
        self.assertEqual(0, rc); self.assertEqual('NOT_RUN', result['runtime'])
        for r in self.receivers: r.receive.assert_not_called()
        self.assertEqual([], self.f.p.c.storage.claims)
        # Synthetic returns were not actually received, thus are caller test fixtures.
        self.assertTrue(all(r._fd is None for r in self.receivers))

    def test_source_token_identical_to_rosa_blocks_before_tls_and_claim(self):
        self.session.close()
        self.session = source.PrivateSourceSession((self.channel(token=self.f.credential.token),),
            wire=self.wire, monotonic=lambda: self.f.h.mono)
        with patch.object(source, 'PrivateGitHubTLS') as tls:
            rc, result = self.call()
        self.assertEqual('SOURCE_SESSION_TOKEN_ALIASED', result.get('error_code'))
        self.assertEqual(2, rc); tls.assert_not_called()
        self.assertEqual([], self.f.p.c.storage.claims)
        self.assertTrue(self.made[-1]._closed)

    def test_same_fd_or_pipe_identity_denied_before_any_receive(self):
        with patch.object(source, 'pipe_identity', return_value=(3, 7)):
            rc, result = self.call()
        self.assertEqual('SOURCE_SESSION_CHANNEL_ALIASED', result.get('error_code'))
        for r in self.receivers: r.receive.assert_not_called()
        self.assertEqual([], self.f.p.c.storage.claims)

    def test_partial_second_channel_failure_closes_received_tokens_and_remaining_channels(self):
        self.session.close()
        other = replace(self.repo, name='synthetic-other', repository_id=124)
        self.session = source.PrivateSourceSession((self.channel(), self.channel(other)),
            wire=self.wire, monotonic=lambda: self.f.h.mono)
        native = source.PrivateGitHubTLS
        count = []
        def tls(**kwargs):
            count.append(1)
            if len(count) == 2: raise ValueError('SYNTHETIC_SECRET')
            obj = native(**kwargs); self.transports.append(obj); return obj
        with patch.object(source, 'PrivateGitHubTLS', side_effect=tls): rc, result = self.call()
        self.assertEqual('SOURCE_SESSION_PREPARATION_FAILED', result['error_code'])
        self.assertEqual([], self.f.p.c.storage.claims)
        self.assertTrue(all(c._closed for c in self.made[-2:])); self.closed_receivers()
        self.assertTrue(self.transports[0]._closed)

    def closed_receivers(self):
        self.assertTrue(all(r._fd is None for r in self.receivers))

    def test_wiring_exception_and_keyboard_interrupt_close_without_arbitrary_callbacks(self):
        self.session.wire = Mock(side_effect=KeyboardInterrupt())
        with self.assertRaises(KeyboardInterrupt): self.call()
        self.closed(); self.assertEqual([], self.f.p.c.storage.claims)

    def test_arbitrary_role_or_invalid_readers_never_builds_application(self):
        self.session.wire = lambda _: {'readers': (), 'receiver': self.rosa}
        rc, result = self.call()
        self.assertEqual(2, rc); self.assertEqual('SOURCE_SESSION_ROLES_INVALID', result.get('error_code'))
        self.closed(); self.assertEqual([], self.f.p.c.storage.claims)

    def test_conflicting_roles_rejected_without_credential_receipt(self):
        rc, result = self.call(readers=tuple(runtime.SourceReaders(self.f.reader, self.f.reader) for _ in range(4)))
        self.assertEqual('SOURCE_SESSION_ROLE_CONFLICT', result['error_code'])
        for r in self.receivers: r.receive.assert_not_called()
        self.closed_receivers()

    def test_shared_deadline_includes_rosa_source_and_wiring_time(self):
        def receive(deadline):
            self.assertEqual(self.f.h.mono + 5, deadline)
            self.f.h.mono += 6
            return self.f.credential
        self.rosa.receive.side_effect = receive
        rc, result = self.call()
        self.assertEqual('SOURCE_SESSION_HANDOFF_EXPIRED', result.get('error_code'))
        self.receivers[1].receive.assert_not_called(); self.closed_receivers()

    def test_slow_wire_consumes_shared_deadline_no_claim(self):
        def slow(session):
            roles = self.wire(session); self.f.h.mono += 11; return roles
        self.session.wire = slow
        rc, result = self.call(handoff_deadline=self.f.h.mono + 100)
        self.assertEqual('SOURCE_SESSION_HANDOFF_EXPIRED', result.get('error_code'))
        self.assertEqual([], self.f.p.c.storage.claims); self.closed()

    def test_cleanup_failure_after_completion_preserves_released_fence_and_runtime(self):
        def fail(): raise ValueError('SYNTHETIC_SECRET')
        with patch.object(GitHubReadCredential, 'close', side_effect=fail):
            rc, result = self.call()
        self.assertEqual(2, rc); self.assertEqual('SOURCE_SESSION_CLEANUP_FAILED', result.get('error_code'))
        self.assertEqual('RELEASED', result['fence']); self.assertEqual('NOT_VERIFIED', result['runtime'])
        self.assertEqual(1, self.f.p.c.storage.complete_calls); self.closed_receivers()
        self.assertTrue(self.transports[0]._closed)

    def test_source_withdrawal_after_first_write_preserves_pending_and_fence(self):
        def changed(request, pin, original):
            return replace(original, body=b'SYNTHETIC_WITHDRAWN') if self.f.h.requests else original
        self.f.read_hook = changed
        rc, result = self.call()
        self.assertEqual(2, rc); self.assertEqual('RETAINED', result['fence'])
        self.assertEqual(1, len(self.f.h.requests)); self.assertEqual(0, self.f.p.c.storage.complete_calls)
        self.closed()

    def test_rosa_cleanup_exception_preserves_completed_result_and_source_cleanup(self):
        with patch.object(self.rosa, 'close', side_effect=ValueError('SYNTHETIC_SECRET')):
            rc, result = self.call()
        self.assertEqual(2, rc); self.assertEqual('SOURCE_SESSION_CLEANUP_FAILED', result.get('error_code'))
        self.assertEqual('RELEASED', result['fence']); self.assertEqual(1, self.f.p.c.storage.complete_calls)
        self.assertEqual('NOT_VERIFIED', result['runtime'])
        self.assertTrue(self.made[0]._closed); self.assertTrue(self.transports[0]._closed)

    def test_bad_arguments_close_owned_channels_without_echoing_secret(self):
        out = io.StringIO()
        with contextlib.redirect_stderr(out), self.assertRaises(SystemExit):
            self.call(('deploy', '--token', TOKEN))
        self.assertNotIn(TOKEN, out.getvalue()); self.closed_receivers()

    def test_wrong_source_type_no_arbitrary_close_but_closes_exact_rosa(self):
        fake = Mock()
        rc, result = self.call(source_session=fake)
        self.assertEqual('SOURCE_SESSION_REQUIRED', result['error_code'])
        fake.close.assert_not_called(); self.assertIsNone(self.rosa._fd)

    def test_callback_public_exception_type_does_not_expose_token_or_message(self):
        self.session.wire = Mock(side_effect=source.SourceSessionError(TOKEN))
        rc, result = self.call()
        self.assertEqual('SOURCE_SESSION_PREPARATION_FAILED', result.get('error_code'))
        self.closed(); self.assertEqual([], self.f.p.c.storage.claims)

    def test_wrong_repository_returned_by_receiver_rejected_before_tls(self):
        bad = GitHubReadCredential(TOKEN, replace(self.repo, name='synthetic-other'), self.f.h.mono + 50)
        self.receivers[1].receive.return_value = bad
        with patch.object(source, 'PrivateGitHubTLS') as tls:
            rc, result = self.call()
        self.assertEqual('SOURCE_SESSION_REPOSITORY_MISMATCH', result.get('error_code'))
        tls.assert_not_called(); self.assertTrue(bad._closed); self.closed_receivers()

    def test_invalid_repository_and_duplicate_scope_are_not_fallback(self):
        with self.assertRaises(source.SourceSessionError): self.session.reader(replace(self.repo, name='other'))
        a, b = self.channel(), self.channel()
        with self.assertRaisesRegex(source.SourceSessionError, 'REPOSITORY_DUPLICATE'):
            source.PrivateSourceSession((a, b), wire=self.wire)
        self.assertIsNone(a.receiver._fd); self.assertIsNone(b.receiver._fd)


@unittest.skipUnless(sys.platform == 'linux', 'actual channel/pipe identity requires Linux')
class LinuxSourceSessionTests(unittest.TestCase):
    def setUp(self):
        self.repo = RepositoryPin('synthetic-owner', 'synthetic-repo', 123, 321)

    def pipe(self, token):
        read, write = os.pipe(); os.write(write, token.encode('ascii')); os.close(write)
        return read

    def test_actual_separate_anonymous_channels_and_one_use_no_socket(self):
        rosa = PipeCredentialReceiver(self.pipe('SYNTHETIC_ROSA_CREDENTIAL'))
        rec = GitHubPipeCredentialReceiver(self.pipe(TOKEN), repository=self.repo,
                                          local_use_until=time.monotonic() + 5)
        s = source.PrivateSourceSession((source.SourceChannel(rec, CA, '192.0.2.10'),),
            wire=lambda pool: {'readers': (pool.reader(self.repo),)})
        try:
            deadline = s.prepare(rosa, time.monotonic() + 3)
            with patch('socket.socket', side_effect=AssertionError('NETWORK_FORBIDDEN')), \
                    patch('ssl.SSLContext', return_value=Mock()):
                roles = s.open(rosa.receive(deadline))
            reader = roles['readers'][0]; cred = reader._transport._credential
            self.assertIs(type(cred), GitHubReadCredential)
            with self.assertRaises(source.SourceSessionError): s.open(BearerCredential('OTHER_SYNTHETIC'))
        finally:
            s.close(); rosa.close()
        self.assertTrue(cred._closed); self.assertIsNone(rec._fd); self.assertIsNone(rosa._fd)

    def test_actual_dup_pipe_rejected_before_receipt_all_owned_closed(self):
        fd = self.pipe(TOKEN); duplicate = os.dup(fd)
        rosa = PipeCredentialReceiver(fd)
        rec = GitHubPipeCredentialReceiver(duplicate, repository=self.repo,
                                          local_use_until=time.monotonic() + 5)
        s = source.PrivateSourceSession((source.SourceChannel(rec, CA, '192.0.2.10'),), wire=lambda _: {})
        try:
            with self.assertRaisesRegex(source.SourceSessionError, 'CHANNEL_ALIASED'):
                s.prepare(rosa, time.monotonic() + 3)
        finally:
            s.close(); rosa.close()
        for n in (fd, duplicate):
            with self.assertRaises(OSError): os.fstat(n)


if __name__ == '__main__': unittest.main()
