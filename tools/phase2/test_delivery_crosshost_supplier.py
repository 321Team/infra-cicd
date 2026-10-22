"""SYNTHETIC supplier ordering/failure tests; no issuer, SSH or network."""
from dataclasses import replace
import json
import threading
import unittest
from unittest.mock import Mock, patch

import delivery_crosshost_ingress as ingress
import delivery_crosshost_pipe as stream
import test_delivery_crosshost_ingress as fixture


class MemoryEnd:
    def __init__(self, owner):
        self.owner = owner
        self._lock = threading.Lock()
        self._deadline = owner.deadline
        self.open = True
        self.frames = []
        self.cleanup = True

    def _current(self):
        if not self.open:
            raise stream.PipeStreamError('P2_STREAM_CLOSED')
        return 99

    def check_time(self):
        if self.owner.f.h.mono >= self._deadline:
            raise stream.PipeStreamError('P2_STREAM_DEADLINE_EXCEEDED')

    def write_all(self, frame):
        self._current(); self.check_time()
        self.frames.append(frame)
        self.owner.events.append('write')

    def close(self):
        self.open = False
        self.owner.events.append('close')
        return self.cleanup


class SupplierTests(unittest.TestCase):
    setUp = fixture.IngressTests.setUp
    receiver = fixture.IngressTests.receiver

    def prepare(self):
        self.events = []
        end = MemoryEnd(self)
        with patch.object(stream, '_PipeEnd', return_value=end):
            writer = stream.OwnedPipeWriter(99, deadline=self.deadline)
        request = self.receiver().request()
        def auth(*_):
            self.events.append('auth')
            return self.peer
        def supplier(*_):
            self.events.append('supply')
            return (self.f.credential.token,
                [dict(repository=r, token=fixture.fixture.TOKEN) for r in request['repositories']])
        return writer, end, request, Mock(side_effect=auth), Mock(side_effect=supplier)

    def send(self, writer, request, auth, supplier):
        return writer.send_from_supplier(request, expected_peer=self.peer,
            authenticate=auth, provide_credentials=supplier)

    def test_peer_authentication_precedes_single_supply_and_unmodified_frame(self):
        w, end, request, auth, supplier = self.prepare()
        original = supplier.side_effect
        def mutate(copy, deadline):
            copy['nonce'] = '0'*64
            copy['target']['run_id'] = '456'
            return original()
        supplier.side_effect = mutate
        self.assertTrue(self.send(w, request, auth, supplier))
        self.assertEqual(['auth', 'supply', 'write', 'close'], self.events)
        self.assertEqual(1, supplier.call_count)
        self.assertTrue(w.supply_attempted)
        self.assertEqual(self.deadline, supplier.call_args.args[1])
        body = json.loads(end.frames[0][4:])
        self.assertEqual(request['nonce'], body['nonce'])
        self.assertEqual(request['target'], body['target'])
        self.assertNotIn(fixture.fixture.TOKEN, repr(w))
        self.assertFalse(end.open)

    def test_auth_rejection_error_lateness_and_closed_writer_never_supply(self):
        for failure in ('peer', 'error', 'late', 'closed'):
            with self.subTest(failure=failure):
                self.f.h.mono = 100
                w, end, request, auth, supplier = self.prepare()
                if failure == 'peer': auth.side_effect = None; auth.return_value = replace(self.peer, principal='other')
                if failure == 'error': auth.side_effect = ValueError(fixture.fixture.TOKEN)
                if failure == 'late': auth.side_effect = lambda *_: (setattr(self.f.h, 'mono', self.deadline) or self.peer)
                if failure == 'closed': auth.side_effect = lambda *_: (w.close() and self.peer)
                with self.assertRaises(stream.PipeStreamError): self.send(w, request, auth, supplier)
                supplier.assert_not_called(); self.assertEqual([], end.frames)
                self.assertFalse(w.supply_attempted)
                self.assertFalse(end.open)

    def test_auth_mutation_cannot_retarget_supplier_or_encoded_frame(self):
        w, end, request, auth, supplier = self.prepare()
        def mutate(copy, deadline):
            copy['target']['run_id'] = '456'; copy['nonce'] = '0'*64
            copy['repositories'].append('synthetic/other')
            return self.peer
        auth.side_effect = mutate
        self.assertTrue(self.send(w, request, auth, supplier))
        self.assertEqual(request, supplier.call_args.args[0])
        self.assertEqual(request['target'], json.loads(end.frames[0][4:])['target'])

    def test_prepared_send_and_supplier_send_share_the_same_consumed_state(self):
        w, end, request, auth, supplier = self.prepare()
        rosa, sources = supplier.side_effect()
        self.assertTrue(w.send(request, expected_peer=self.peer, authenticate=auth,
                               rosa_token=rosa, source_tokens=sources))
        with self.assertRaisesRegex(stream.PipeStreamError, 'CONSUMED'):
            self.send(w, request, auth, supplier)
        supplier.assert_not_called(); self.assertEqual(1, len(end.frames))

    def test_bad_request_or_noncallable_source_consumes_without_authentication(self):
        for malformed in ('request', 'source'):
            w, end, request, auth, supplier = self.prepare()
            if malformed == 'request': request['unexpected'] = True
            if malformed == 'source': supplier = None
            with self.assertRaises(stream.PipeStreamError): self.send(w, request, auth, supplier)
            auth.assert_not_called(); self.assertEqual([], end.frames)
            with self.assertRaisesRegex(stream.PipeStreamError, 'CONSUMED'):
                self.send(w, self.receiver().request(), auth, Mock())

    def test_late_supplier_no_frame_and_no_new_deadline_or_retry(self):
        w, end, request, auth, supplier = self.prepare()
        original = supplier.side_effect
        def late(*args):
            result = original(*args); self.f.h.mono = self.deadline
            return result
        supplier.side_effect = late
        with self.assertRaisesRegex(stream.PipeStreamError, 'DEADLINE_EXCEEDED'):
            self.send(w, request, auth, supplier)
        self.assertEqual([], end.frames); self.assertFalse(end.open)
        self.assertTrue(w.supply_attempted)
        with self.assertRaisesRegex(stream.PipeStreamError, 'CONSUMED'):
            self.send(w, request, auth, supplier)
        self.assertEqual(1, supplier.call_count)

    def test_invalid_supplier_result_and_repository_order_fail_without_write(self):
        invalid = [None, [], ('SYNTHETIC',), ('SYNTHETIC', {}, 'extra'),
                   ('SYNTHETIC', []), (fixture.fixture.TOKEN,
                   [dict(repository=self.s.repo.owner+'/'+self.s.repo.name, token=fixture.fixture.TOKEN)]),
                   ('SYNTHETIC', [dict(repository='synthetic/other', token=fixture.fixture.TOKEN)])]
        for result in invalid:
            w, end, request, auth, supplier = self.prepare()
            supplier.side_effect = None; supplier.return_value = result
            with self.assertRaises(stream.PipeStreamError): self.send(w, request, auth, supplier)
            self.assertEqual([], end.frames); self.assertFalse(end.open)

    def test_supplier_exceptions_interrupts_redacted_and_cleanup_failure_precedes(self):
        for error in (ValueError(fixture.fixture.TOKEN), KeyboardInterrupt(fixture.fixture.TOKEN), SystemExit(fixture.fixture.TOKEN)):
            for cleanup in (True, False):
                w, end, request, auth, supplier = self.prepare()
                end.cleanup = cleanup; supplier.side_effect = error
                expected = type(error) if cleanup and isinstance(error, (KeyboardInterrupt, SystemExit)) else stream.PipeStreamError
                with self.assertRaises(expected) as caught: self.send(w, request, auth, supplier)
                self.assertNotIn(fixture.fixture.TOKEN, str(caught.exception))
                if not cleanup: self.assertEqual(('P2_STREAM_CLEANUP_FAILED',), caught.exception.args)
                self.assertEqual([], end.frames); self.assertFalse(end.open)
                self.assertTrue(w.supply_attempted)

    def test_competing_sends_do_not_call_supplier_twice(self):
        w, end, request, auth, supplier = self.prepare()
        entered, release = threading.Event(), threading.Event()
        original = supplier.side_effect; results = []
        def wait(*args):
            entered.set(); self.assertTrue(release.wait(2)); return original(*args)
        supplier.side_effect = wait
        def send():
            try: results.append(self.send(w, request, auth, supplier))
            except BaseException as exc: results.append(exc)
        thread = threading.Thread(target=send)
        thread.start(); self.assertTrue(entered.wait(2))
        try:
            with self.assertRaisesRegex(stream.PipeStreamError, 'CONSUMED'):
                self.send(w, request, auth, supplier)
        finally: release.set(); thread.join(3)
        self.assertFalse(thread.is_alive()); self.assertEqual([True], results)
        self.assertEqual(1, supplier.call_count); self.assertEqual(1, len(end.frames))

    def test_cancel_during_supplier_reports_attempt_without_delivery_or_reissue(self):
        w, end, request, auth, supplier = self.prepare()
        entered, release = threading.Event(), threading.Event()
        original = supplier.side_effect; results = []
        def wait(*args):
            entered.set(); self.assertTrue(release.wait(2)); return original(*args)
        supplier.side_effect = wait
        def send():
            try: results.append(self.send(w, request, auth, supplier))
            except BaseException as exc: results.append(exc)
        thread = threading.Thread(target=send)
        thread.start(); self.assertTrue(entered.wait(2))
        try: self.assertTrue(w.close())
        finally: release.set(); thread.join(3)
        self.assertFalse(thread.is_alive()); self.assertTrue(w.supply_attempted)
        self.assertEqual(1, supplier.call_count); self.assertEqual([], end.frames)
        self.assertEqual(('P2_STREAM_CLOSED',), results[0].args)
        self.assertIsNone(results[0].__context__)
