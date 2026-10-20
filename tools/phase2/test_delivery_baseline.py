"""Synthetic HTTP/TLS and identities only; real socket/DNS and writes forbidden."""
from dataclasses import replace
import unittest
from unittest.mock import patch

import delivery_adapter as core
from delivery_baseline import FirstDeploymentBaseline
from delivery_object_reader import Observation
from delivery_provider import AdmittedBaseline
from delivery_verifier import Progress, VerificationError
import test_delivery_object_reader as reader_tests
from test_delivery_object_reader import absent
from test_delivery_operational import ORIGIN
from test_delivery_transport import response, TOKEN
import test_delivery_provider as provider_tests


class BaselineTests(unittest.TestCase):
    # Reuse the established fake TLS socket harness without inheriting/repeating
    # its complete test suite. No network or existing runtime fixture is used.
    make_socket = reader_tests.ReaderTests.make_socket

    def setUp(self):
        reader_tests.ReaderTests.setUp(self)
        self.admit = FirstDeploymentBaseline(self.reader,
            progress=lambda: self.progress, provenance=ORIGIN)

    def queue_absence(self):
        self.queue = [response(core.encoded(absent(k)), 404) for k in core.OBJECTS]

    def test_fresh_exact_absence_admits_same_request_six_nones_without_writes(self):
        self.queue_absence()
        result = self.admit(self.request)
        self.assertIs(type(result), AdmittedBaseline)
        self.assertIs(result.request, self.request)
        self.assertEqual(result.binding, self.b)
        self.assertEqual(result.objects, (None,) * len(core.OBJECTS))
        self.assertEqual(len(self.sockets), len(core.OBJECTS))
        self.assertTrue(all(bytes(s.sent).startswith(b'GET ') and s.closed for s in self.sockets))
        self.assertNotIn(TOKEN, repr(self.admit))

    def test_existing_object_at_any_position_is_not_silently_adopted(self):
        for index, raw in enumerate(self.raws):
            with self.subTest(index=index):
                self.queue_absence()
                self.queue[index] = response(raw, 200)
                before = len(self.sockets)
                with self.assertRaisesRegex(VerificationError, '^BASELINE_OBJECT_PRESENT$'):
                    self.admit(self.request)
                self.assertEqual(len(self.sockets) - before, index + 1)

    def test_auth_http_failures_and_generic_404_never_mean_empty(self):
        for status, body in [(401, absent(core.OBJECTS[0])), (403, absent(core.OBJECTS[0])),
                             (500, absent(core.OBJECTS[0])), (404, {'message': 'not found'})]:
            with self.subTest(status=status):
                self.queue = [response(core.encoded(body), status)]
                with self.assertRaises(VerificationError):
                    self.admit(self.request)
                self.assertEqual(self.queue, [])

    def test_wrong_credential_binding_and_restore_fail_before_any_get(self):
        cases = [replace(self.request, credential=replace(self.credential)),
                 replace(self.request, binding=replace(self.b, run_id='OTHER')),
                 replace(self.request, binding=replace(self.b, action='restore'))]
        for request in cases:
            with self.subTest(request=request):
                with self.assertRaises(VerificationError):
                    self.admit(request)
        self.assertEqual(self.sockets, [])

    def test_even_reader_pinned_to_restore_cannot_admit_new_empty_baseline(self):
        binding = replace(self.b, action='restore')
        with patch.object(self.reader, '_binding', binding):
            with self.assertRaisesRegex(VerificationError, '^BASELINE_REQUEST_INVALID$'):
                self.admit(replace(self.request, binding=binding))
        self.assertEqual(self.sockets, [])

    def test_journal_intent_or_reply_prevents_baseline_read(self):
        for progress in [Progress(journal=b'pending'), Progress(responses=(b'reply',))]:
            self.progress = progress
            with self.assertRaisesRegex(VerificationError, '^BASELINE_INITIAL_PROGRESS_REQUIRED$'):
                self.admit(replace(self.request, progress=progress))
        self.assertEqual(self.sockets, [])

    def test_progress_change_during_get_stops_and_keeps_original_progress(self):
        self.queue_absence()
        original = self.reader.read
        def read(request, key):
            result = original(request, key)
            self.progress = Progress(journal=b'pending')
            return result
        with patch.object(self.reader, 'read', side_effect=read):
            with self.assertRaisesRegex(VerificationError, '^BASELINE_PROGRESS_CHANGED$'):
                self.admit(self.request)
        self.assertEqual(len(self.sockets), 1)
        self.assertEqual(self.progress.journal, b'pending')

    def test_all_reads_share_absolute_deadline_and_late_reply_is_denied(self):
        self.queue_absence()
        original = self.reader.read
        def read(request, key):
            result = original(request, key)
            if len(self.sockets) == len(core.OBJECTS):
                self.clock = request.deadline
            return result
        with patch.object(self.reader, 'read', side_effect=read):
            with self.assertRaisesRegex(VerificationError, '^OBJECT_READ_DEADLINE$'):
                self.admit(self.request)
        self.assertEqual(len(self.sockets), len(core.OBJECTS))

    def test_last_progress_callback_cannot_return_admission_after_deadline(self):
        self.queue_absence()
        complete_reads_checks = 0
        def progress():
            nonlocal complete_reads_checks
            if len(self.sockets) == len(core.OBJECTS):
                complete_reads_checks += 1
                if complete_reads_checks == 2:
                    self.clock = self.request.deadline
            return self.progress
        self.admit.progress = progress
        with self.assertRaisesRegex(VerificationError, '^OBJECT_READ_DEADLINE$'):
            self.admit(self.request)
        self.assertEqual(len(self.sockets), len(core.OBJECTS))

    def test_failed_read_is_not_cached_as_absence_on_next_call(self):
        self.queue = [response(core.encoded(absent(core.OBJECTS[0])), 403)]
        with self.assertRaises(VerificationError):
            self.admit(self.request)
        self.queue_absence()
        result = self.admit(self.request)
        self.assertEqual(result.objects, (None,) * len(core.OBJECTS))
        self.assertEqual(len(self.sockets), 7)

    def test_wrong_classified_key_status_or_object_type_is_denied(self):
        for value in [None, Observation(core.OBJECTS[1], 404, b'{}'),
                      Observation(core.OBJECTS[0], True, b'{}'),
                      Observation(core.OBJECTS[0], 204, b'{}')]:
            with patch.object(self.reader, 'read', return_value=value):
                with self.assertRaisesRegex(VerificationError, '^BASELINE_OBSERVATION_INVALID$'):
                    self.admit(self.request)

    def test_unexpected_failure_does_not_echo_exception_or_token(self):
        with patch.object(self.reader, 'read', side_effect=ValueError(TOKEN)):
            with self.assertRaisesRegex(VerificationError, '^BASELINE_ADMISSION_FAILED$') as caught:
                self.admit(self.request)
        self.assertNotIn(TOKEN, repr(caught.exception))


class BaselineCompositionTests(unittest.TestCase):
    setUp = provider_tests.ProviderTests.setUp
    baseline = provider_tests.ProviderTests.baseline
    app = provider_tests.ProviderTests.app
    safe = provider_tests.ProviderTests.safe

    def connected_app(self):
        self.admission_calls = 0
        def admit(request):
            self.admission_calls += 1
            providers = app._providers
            return FirstDeploymentBaseline(providers.reader,
                progress=providers.progress, provenance=ORIGIN)(request)
        app = self.app(adapters=replace(self.adapters, baseline=admit))
        return app

    def test_session_uses_admission_once_then_existing_reconciliation_after_writes(self):
        app = self.connected_app()
        out = app.run('deploy')
        self.assertEqual(out['status'], 'SESSION_COMPLETED', out)
        self.assertEqual(self.admission_calls, 1)
        self.assertEqual([r.method for r in self.c.h.requests], ['PATCH'] * 6 + ['GET'] * 2)
        self.assertGreater(self.reads, 12)
        self.safe(out)

    def test_partial_get_failure_never_reaches_writer_or_completion(self):
        self.fail_read = 3
        out = self.connected_app().run('deploy')
        self.assertEqual(out['status'], 'STOPPED', out)
        self.assertEqual(self.admission_calls, 1)
        self.assertEqual(self.c.sender_calls, 0)
        self.assertEqual(self.c.h.requests, [])
        self.assertEqual(self.c.storage.complete_calls, 0)
        self.safe(out)


if __name__ == '__main__':
    unittest.main()
