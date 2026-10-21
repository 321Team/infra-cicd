"""SYNTHETIC native originals/policies only; socket/DNS prohibited.

In-process worker stand-ins isolate policy/frame tests on Windows. Explicit
Linux tests below exercise real process/pipe supervision and actual session
composition, without touching AWS/ROSA/Runner or real credentials.
"""
from dataclasses import replace
import contextlib
import io
import json
import os
from pathlib import Path
import signal
import socket
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

import delivery_adapter as core
import delivery_finish_collector as collector
import delivery_finish_worker as worker
import delivery_runtime_site as runtime
from delivery_github_source import CommentPin, SourceOriginal
from delivery_object_reader import BearerCredential
from delivery_credential_channel import PipeCredentialReceiver
from delivery_semantic_authority import FinishFacts
from delivery_verifier import Progress
from test_delivery_adapter import Harness, SENSITIVE
from test_delivery_semantic_authority import SemanticFixture
import test_delivery_runtime_site as prior


class CollectorTests(SemanticFixture, unittest.TestCase):
    def setUp(self):
        for name in ('socket', 'getaddrinfo', 'create_connection'):
            p = patch.object(socket, name, side_effect=AssertionError('NETWORK_FORBIDDEN'))
            p.start(); self.addCleanup(p.stop)
        self.context(Harness(), BearerCredential(SENSITIVE))
        self.terminal()
        g = self.grants['finish']
        self.policy = collector.FinishCollectionPolicy(g.binding, collector.BlobLocation(
            g.original_pin.repository, g.original_pin.path, g.original_pin.anchor_ref),
            g.control_pin, g.principal, g.adoption_reference, g.starts_at, g.expires_at)
        self.record = runtime.EvidenceAdoption(g, self.finish)
        self.locations = []

    def locate(self, request):
        self.locations.append(request)
        return self.record

    def capture(self, **kwargs):
        return collector.AuthenticatedFinishCollector(policy=self.policy,
            readers=runtime.SourceReaders(self.reader, self.reader), credential=self.credential,
            progress=lambda:self.progress, locate=self.locate, max_seconds=2,
            wall=lambda:self.h.now, monotonic=lambda:self.h.mono, **kwargs)

    def in_process(self, capture, request=None):
        with patch.object(worker, 'run_once', side_effect=lambda work, **kw:work()) as run:
            record = capture(request or self.request())
        return record, run

    def test_native_original_and_control_are_authenticated_twice_before_metadata_handoff(self):
        r = self.request(); capture = self.capture()
        got, run = self.in_process(capture, r)
        self.assertEqual(self.record, got)
        self.assertEqual([r], self.locations)
        self.assertEqual(['finish', 'control', 'finish', 'control'], [p.path for _, p in self.read_calls])
        run.assert_called_once()
        self.assertEqual(2, run.call_args.kwargs['timeout'])
        self.assertNotIn(SENSITIVE, repr(got))
        with self.assertRaisesRegex(collector.FinishCollectorError, 'CONSUMED'):capture(r)

    def test_missing_raw_or_changed_control_cannot_be_replaced_by_pin_echo(self):
        for name in ('finish', 'control'):
            self.read_calls.clear(); capture = self.capture()
            self.read_hook = lambda r,p,o: replace(o, body=b'SYNTHETIC_FORGED') if p.path == name else o
            with self.subTest(name=name), self.assertRaises(collector.FinishCollectorError):
                self.in_process(capture)
            self.assertTrue(self.locations)
        self.read_hook = lambda r,p,o:o

    def test_partial_progress_and_foreign_request_do_not_start_worker_or_locator(self):
        original = self.progress
        for bad in ('partial', 'credential', 'binding', 'deadline'):
            self.progress = original
            r = self.request()
            if bad == 'partial':self.progress = Progress(); r = self.request()
            if bad == 'credential':r = replace(r, credential=BearerCredential('x'*30))
            if bad == 'binding':r = replace(r, binding=replace(r.binding, run_id='synthetic-other-run'))
            if bad == 'deadline':r = replace(r, deadline=self.h.mono)
            with self.subTest(bad=bad), patch.object(worker, 'run_once') as run:
                with self.assertRaises(collector.FinishCollectorError):self.capture()(r)
                run.assert_not_called()
        self.assertEqual([], self.locations); self.assertEqual([], self.read_calls)

    def test_exact_policy_cannot_be_broadened_by_locator(self):
        g = self.record.grant
        for field, value in (('principal', 'synthetic-other'), ('adoption_reference', 'synthetic:other'),
            ('expires_at', g.expires_at+1), ('starts_at', g.starts_at-1),
            ('control_pin', replace(g.control_pin, sha256='f'*64)),
            ('binding', replace(g.binding, action='restore'))):
            self.record = runtime.EvidenceAdoption(replace(g, **{field:value}), self.finish)
            with self.subTest(field=field), self.assertRaisesRegex(collector.FinishCollectorError, 'POLICY_CHANGED'):
                self.in_process(self.capture())
        self.assertEqual([], self.read_calls)

    def test_other_repo_path_ref_or_future_original_cannot_enter_allowed_location(self):
        record = self.record; p = record.grant.original_pin
        for changed in (replace(p, path='different'), replace(p, anchor_ref='heads/different'),
                        replace(p, repository=replace(p.repository, repository_id=999))):
            self.record = replace(record, grant=replace(record.grant, original_pin=changed))
            with self.assertRaisesRegex(collector.FinishCollectorError, 'SOURCE_SCOPE'):
                self.in_process(self.capture())
        self.record = record
        for facts in (replace(self.finish, observed_at=self.h.now+1), replace(self.finish, progress_key='f'*64),
                      replace(self.finish, action='restore'), replace(self.finish, run_id='synthetic-other'),
                      replace(self.finish, observed_at=float('nan')), replace(self.finish, exclusive_end_reference='PENDING')):
            self.record = replace(record, facts=facts)
            with self.assertRaisesRegex(collector.FinishCollectorError, 'FACTS_INVALID'):
                self.in_process(self.capture())
        self.assertEqual([], self.read_calls)

    def test_comment_account_is_identity_only_and_native_bytes_still_required(self):
        repo = self.policy.location.repository
        pin = CommentPin(repo, 10, 20, 30, '2026-01-01T00:00:00Z', core.digest(self.raws['finish']))
        self.policy = replace(self.policy, location=collector.CommentLocation(repo, 10, 30))
        self.record = replace(self.record, grant=replace(self.record.grant, original_pin=pin))
        from delivery_adopted_authority import pin_identity
        def read(r, p):
            ref, revision, repo_id, creator, _ = pin_identity(p)
            return SourceOriginal(r, ref, revision, repo_id, creator, self.raws['finish'], (b'SYNTHETIC_RESPONSE',))
        with patch.object(self.reader, 'comment', side_effect=read):
            got, _ = self.in_process(self.capture())
        self.assertEqual(pin, got.grant.original_pin)
        with patch.object(self.reader, 'comment', side_effect=lambda r,p:replace(read(r,p), body=b'OTHER')):
            with self.assertRaises(collector.FinishCollectorError):self.in_process(self.capture())

    def test_strict_private_frame_rejects_duplicate_extra_replay_credential_and_nan(self):
        r = self.request(); raw = collector._encode(self.record, r, 'synthetic-nonce')
        d = json.loads(raw)
        bad = [b'', b'{}', b'x'*(collector.MAX_FRAME+1), raw.replace(b'{', b'{"schema":"OTHER",', 1)]
        for key, value in (('nonce', 'old'), ('binding_key', 'f'*64), ('credential_fingerprint', 'f'*64),
                           ('progress_key', 'f'*64), ('unapproved', True)):
            bad.append(json.dumps(d | {key:value}).encode())
        changed = json.loads(raw); changed['facts']['observed_at'] = float('nan')
        bad.append(json.dumps(changed).encode())
        for frame in bad:
            with self.subTest(frame_size=len(frame)), self.assertRaisesRegex(collector.FinishCollectorError, 'FRAME_INVALID'):
                collector._decode(frame, self.policy, r, 'synthetic-nonce')
        self.assertNotIn(SENSITIVE.encode(), raw)
        self.assertNotIn(self.raws['finish'], raw)

    def test_parent_rechecks_progress_clock_window_and_frame_after_worker(self):
        for mode in ('progress', 'deadline', 'clock', 'future', 'frame', 'exception'):
            case = CollectorTests('runTest'); case.setUp()
            try:
                capture = case.capture(); r = case.request()
                def child(work, **kw):
                    raw = work()
                    if mode == 'progress':case.progress = Progress()
                    elif mode == 'deadline':case.h.mono = r.deadline
                    elif mode == 'clock':case.h.now -= 1
                    elif mode == 'future':
                        d = json.loads(raw); d['facts']['observed_at'] += 1; raw = json.dumps(d).encode()
                    elif mode == 'frame':raw = b'{"approved":true}'
                    else:raise RuntimeError(SENSITIVE)
                    return raw
                with self.subTest(mode=mode), patch.object(worker, 'run_once', side_effect=child):
                    with self.assertRaises(collector.FinishCollectorError) as error:capture(r)
                    self.assertNotIn(SENSITIVE, str(error.exception))
                    with self.assertRaisesRegex(collector.FinishCollectorError, 'CONSUMED'):capture(r)
            finally:case.doCleanups()

    def test_worker_budget_never_extends_request_or_authority_expiry(self):
        for remaining, authority in ((0.2, 4), (8, 0.3)):
            self.policy = replace(self.policy, expires_at=self.h.now+authority)
            self.record = replace(self.record, grant=replace(self.record.grant, expires_at=self.policy.expires_at))
            r = replace(self.request(), deadline=self.h.mono+remaining)
            _, run = self.in_process(self.capture(), r)
            self.assertAlmostEqual(min(remaining, authority), run.call_args.kwargs['timeout'])

    def test_close_releases_local_references_without_closing_borrowed_reader(self):
        capture = self.capture(); capture.close(); capture.close()
        self.assertIsNone(capture.credential); self.assertIsNone(capture.locate)
        self.assertIsNotNone(self.reader._transport)
        with self.assertRaisesRegex(collector.FinishCollectorError, 'CONSUMED'):capture(self.request())


class CollectorSessionTests(unittest.TestCase):
    def setUp(self):
        self.base = prior.RuntimeSiteTests('runTest'); self.base.setUp(); self.addCleanup(self.base.doCleanups)

    def config(self, locate=None):
        b = self.base; g = b.grants['finish']
        policy = collector.FinishCollectionPolicy(g.binding, collector.BlobLocation(
            g.original_pin.repository, g.original_pin.path, g.original_pin.anchor_ref),
            g.control_pin, g.principal, g.adoption_reference, g.starts_at, g.expires_at)
        def native(r):
            self.assertEqual(1, b.p.c.sender_closes)
            return runtime.EvidenceAdoption(g, FinishFacts(r.binding.run_id, r.binding.action,
                r.progress.key, b.h.now, 'synthetic:followup', 'synthetic:exclusive-end'))
        return collector.FinishCollectorConfig(policy, runtime.SourceReaders(b.reader, b.reader),
                                                locate or native, 0.4)

    def app(self, locate=None):
        b=self.base
        return b.app(install=False, factory=b.factory(b.slot, finish_collection=self.config(locate)))

    def test_real_session_collector_auth_and_parent_reauth_release_only_after_close(self):
        captures=[];original=collector.AuthenticatedFinishCollector.close
        def close(handle):captures.append(handle);original(handle)
        with patch.object(worker, 'run_once', side_effect=lambda work, **kw:work()),patch.object(
            collector.AuthenticatedFinishCollector,'close',new=close):
            out = self.app().run('deploy')
        self.assertEqual('SESSION_COMPLETED', out['status'], out)
        self.assertEqual('RELEASED', out['fence'])
        self.assertEqual(4, sum(p.path == 'finish' for _,p in self.base.read_calls))
        self.assertEqual(1,len(captures)); self.assertTrue(captures[0].closed)
        self.assertIsNone(captures[0].credential)

    def test_source_changed_between_worker_handoff_and_parent_auth_retains_fence(self):
        def child(work, **kw):
            raw = work(); self.base.raws['finish'] = b'SYNTHETIC_CHANGED_AFTER_CAPTURE'; return raw
        with patch.object(worker, 'run_once', side_effect=child):out = self.app().run('deploy')
        self.assertEqual('COMPLETION_DENIED', out['error_code'], out)
        self.assertEqual('RETAINED', out['fence']); self.assertEqual(0, self.base.p.c.storage.complete_calls)

    def test_status_and_preflight_do_not_start_worker_or_capture(self):
        for command in ('status', 'preflight'):
            case = CollectorSessionTests('runTest'); case.setUp()
            try:
                app = case.app()
                with self.subTest(command=command), patch.object(worker, 'run_once') as run:
                    app.run(command); run.assert_not_called()
                self.assertFalse(case.base.slot.installed)
                self.assertEqual(0, case.base.p.c.sender_calls)
            finally:case.doCleanups()

    def main(self, command):
        b=self.base; reads=tuple(runtime.SourceReaders(b.reader,b.reader) for _ in range(4))
        original=runtime.RuntimeAuthorityFactory; output=io.StringIO()
        read,write=os.pipe();os.close(write);receiver=PipeCredentialReceiver(read)
        def factory(*args,**kw):
            return original(*args,**kw,wall=lambda:b.h.now,monotonic=lambda:b.h.mono)
        def build(config,*,credential,authority_factory):
            self.assertIs(credential,b.credential)
            return b.app(install=False,factory=authority_factory)
        with patch.object(receiver,'receive',return_value=b.credential) as receive, patch.object(
            runtime,'RuntimeAuthorityFactory',side_effect=factory), patch.object(runtime.site,
            'build_application',side_effect=build), contextlib.redirect_stdout(output):
            rc=runtime.main([command],config=b.config(),plan=b.plan(),readers=reads,finish_slot=b.slot,
                receiver=receiver,handoff_deadline=b.h.mono+10,finish_collection=self.config())
        self.assertIsNone(receiver._fd)
        return rc,json.loads(output.getvalue()),receive.call_count

    def test_actual_main_binds_collector_after_receipt_to_same_credential_and_progress(self):
        with patch.object(worker,'run_once',side_effect=lambda work,**kw:work()):
            rc,out,received=self.main('deploy')
        self.assertEqual(0,rc,out);self.assertEqual('SESSION_COMPLETED',out['status'])
        self.assertEqual(1,received);self.assertEqual(1,self.base.p.c.storage.complete_calls)

    def test_main_status_no_credential_receipt_worker_source_or_claim(self):
        with patch.object(worker,'run_once') as run:
            rc,out,received=self.main('status');run.assert_not_called()
        self.assertEqual(0,rc);self.assertEqual('CONFIGURED_NOT_VERIFIED',out['status'])
        self.assertEqual(0,received);self.assertEqual([],self.base.read_calls)
        self.assertEqual([],self.base.p.c.storage.claims)

    def test_collection_config_rejects_binding_reader_mismatch_and_callback_overlap_before_io(self):
        b=self.base;config=self.config()
        bad=replace(config,policy=replace(config.policy,binding=replace(config.policy.binding,action='restore')))
        foreign=runtime.SourceReaders(b.reader,Mock())
        for kwargs in ({'finish_collection':bad}, {'finish_collection':replace(config,readers=foreign)},
                       {'finish_collection':config,'finish_capture':lambda r:None}):
            with self.subTest(kwargs=list(kwargs)),self.assertRaises(runtime.RuntimeSiteError):b.factory(b.slot,**kwargs)
        self.assertEqual([],b.read_calls)

    @unittest.skipUnless(sys.platform == 'linux', 'Linux fork integration only')
    def test_linux_real_collector_child_and_session_complete_without_live_network(self):
        out = self.app().run('deploy')
        self.assertEqual('SESSION_COMPLETED', out['status'], out)
        self.assertEqual('RELEASED', out['fence'])
        self.assertEqual(2, sum(p.path == 'finish' for _,p in self.base.read_calls))  # Child calls are COW.

    @unittest.skipUnless(sys.platform == 'linux', 'Linux cancellation only')
    def test_hung_locator_is_killed_and_session_keeps_durable_fence(self):
        def hung(r):time.sleep(30)
        started = time.monotonic(); out = self.app(hung).run('deploy')
        self.assertLess(time.monotonic()-started, 2)
        self.assertEqual('COMPLETION_DENIED', out['error_code'], out)
        self.assertEqual('RETAINED', out['fence']); self.assertEqual(0, self.base.p.c.storage.complete_calls)
        self.assertFalse(self.base.slot.installed)


class WorkerConfigurationTests(unittest.TestCase):
    def test_invalid_or_unsupported_configuration_never_invokes_work(self):
        work = Mock()
        for value in (True, 0, -1, float('nan'), 121):
            with self.subTest(value=value), self.assertRaises(worker.FinishWorkerError):worker.run_once(work, timeout=value)
        with patch.object(worker, 'supported', return_value=False):
            with self.assertRaisesRegex(worker.FinishWorkerError, 'PLATFORM_UNSUPPORTED'):worker.run_once(work, timeout=1)
        work.assert_not_called()


@unittest.skipUnless(sys.platform == 'linux', 'Linux process supervision only')
class LinuxWorkerTests(unittest.TestCase):
    def test_exact_pipe_boundary_and_no_descriptor_leak(self):
        before = len(os.listdir('/proc/self/fd'))
        self.assertEqual(b'x'*32768, worker.run_once(lambda:b'x'*32768, timeout=2))
        self.assertEqual(before, len(os.listdir('/proc/self/fd')))

    def test_parent_overflow_guard_rejects_a_child_violating_its_output_contract(self):
        def invalid_child(read_fd, write_fd, work, maximum):
            os.close(read_fd); os.setsid()
            data = b'x'*32769; offset = 0
            while offset < len(data):offset += os.write(write_fd, data[offset:])
            os.close(write_fd); os._exit(0)
        with patch.object(worker, '_child', new=invalid_child):
            with self.assertRaisesRegex(worker.FinishWorkerError, 'OVERFLOW'):
                worker.run_once(lambda:b'unused', timeout=1)

    def test_success_and_timeout_reap_the_exact_leader_without_zombie(self):
        real_fork = os.fork
        for hang in (False, True):
            pids = []
            def tracked_fork():
                pid = real_fork()
                if pid > 0:pids.append(pid)
                return pid
            def work():
                if hang:time.sleep(30)
                return b'ok'
            with patch.object(os, 'fork', new=tracked_fork):
                if hang:
                    with self.assertRaises(worker.FinishWorkerError):worker.run_once(work, timeout=0.3)
                else:self.assertEqual(b'ok', worker.run_once(work, timeout=1))
            self.assertEqual(1, len(pids))
            with self.assertRaises(ChildProcessError):os.waitpid(pids[0], os.WNOHANG)

    def test_hang_nonzero_empty_oversize_are_single_attempt_fixed_errors(self):
        def hung():time.sleep(30)
        def crash():os._exit(9)
        for work in (hung, crash, lambda:b'', lambda:b'x'*32769):
            before = len(os.listdir('/proc/self/fd')); started = time.monotonic()
            with self.subTest(work=work.__name__), self.assertRaises(worker.FinishWorkerError):
                worker.run_once(work, timeout=0.3)
            self.assertLess(time.monotonic()-started, 1.5)
            self.assertEqual(before, len(os.listdir('/proc/self/fd')))

    def test_nonzero_exit_cannot_be_hidden_by_writing_valid_bytes_first(self):
        def work():return b'valid'
        real_exit = os._exit
        def fail(code):real_exit(7 if code == 0 else code)
        with patch.object(os, '_exit', new=fail):
            with self.assertRaisesRegex(worker.FinishWorkerError, 'FAILED'):worker.run_once(work, timeout=1)

    def test_descendant_keeping_pipe_open_is_killed_with_worker_group(self):
        with tempfile.TemporaryDirectory() as d:
            pidfile = Path(d)/'synthetic-child-pid'
            def holds_pipe():
                pid = os.fork()
                if pid == 0:
                    time.sleep(30); os._exit(0)
                pidfile.write_text(str(pid)); return b'not-complete'
            with self.assertRaisesRegex(worker.FinishWorkerError, 'TIMEOUT'):
                worker.run_once(holds_pipe, timeout=0.4)
            pid = int(pidfile.read_text())
            for _ in range(50):
                status = Path(f'/proc/{pid}/stat')
                if not status.exists() or status.read_text().split()[2] == 'Z':break
                time.sleep(0.005)
            else:self.fail('SYNTHETIC_DESCENDANT_STILL_RUNNING')

    def test_worker_cannot_keep_inherited_store_descriptor(self):
        with tempfile.TemporaryFile() as f:
            inherited = f.fileno()
            def work():
                try:os.fstat(inherited)
                except OSError:return b'closed'
                return b'inherited'
            self.assertEqual(b'closed', worker.run_once(work, timeout=1))
            self.assertGreaterEqual(os.fstat(inherited).st_size, 0)

    def test_multithread_or_sigchld_handler_environment_is_rejected_before_fork(self):
        for target, value in ((worker.threading, 2),):
            with patch.object(target, 'active_count', return_value=value), patch.object(os, 'fork') as fork:
                with self.assertRaisesRegex(worker.FinishWorkerError, 'UNSUPPORTED'):worker.run_once(lambda:b'x', timeout=1)
                fork.assert_not_called()
        with patch.object(worker.signal, 'getsignal', return_value=signal.SIG_IGN), patch.object(os, 'fork') as fork:
            with self.assertRaisesRegex(worker.FinishWorkerError, 'UNSUPPORTED'):worker.run_once(lambda:b'x', timeout=1)
            fork.assert_not_called()


if __name__ == '__main__':unittest.main()
