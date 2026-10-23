"""SYNTHETIC in-memory handoff faults; real local FDs, never Runtime/network."""
import os
import unittest
from unittest.mock import Mock, patch

import delivery_crosshost_ingress as ingress
import delivery_job as job
import delivery_job_handoff as handoff
import delivery_job_parent as parent
import delivery_runtime_site as runtime
from delivery_credential_channel import PipeCredentialReceiver
import test_delivery_job_parent as fixture
from test_delivery_job import CONTEXT

SECRET = 'SYNTHETIC_PRIVATE_FAILURE_TEXT'


class ParentCleanupTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture.ParentTests('runTest'); self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.prepared = runtime.PreparedAuthorityPlan(binding=self.f.f.h.binding,
            materialize=Mock(side_effect=AssertionError('NO_MATERIALIZE')))
        self.addCleanup(self.prepared.close)
        self.builder = self.f.builder(plan=self.prepared)
        self.seen = []
        close = ingress.CrossHostIngress.close
        def receiver_close(obj):
            self.seen.append(obj)
            return close(obj)
        p = patch.object(ingress.CrossHostIngress, 'close', new=receiver_close)
        p.start(); self.addCleanup(p.stop)

    def invocation(self):
        read, write = os.pipe(); os.close(write)
        receiver = PipeCredentialReceiver(read); self.addCleanup(receiver.close)
        value = job.RuntimeInvocation(CONTEXT, self.f.f.config(), self.prepared,
            self.f.f.slot, receiver, self.f.deadline, source_session=self.f.s.session)
        self.addCleanup(value.close)
        return value

    def closed(self, value=None):
        self.assertEqual(1, len(self.seen))
        self.assertTrue(self.seen[0]._used)
        self.assertTrue(self.prepared._closed)
        self.assertIsNone(self.prepared._context)
        if value is not None:
            self.assertTrue(value._closed)
            self.assertIsNone(value.receiver._fd)
            self.assertEqual('CLOSED', value.source_session._state)
        self.assertEqual([], self.f.f.h.requests)
        self.assertEqual([], self.f.f.p.c.storage.claims)

    def test_bundle_cleanup_failure_after_build_closes_untransferred_invocation(self):
        value = self.invocation()
        with patch.object(handoff, 'build_owned_invocation', return_value=value), \
             patch.object(ingress.ReceivedCredentials, 'close', side_effect=ValueError(SECRET)):
            with self.assertRaisesRegex(parent.ParentBuildError, '^P2_PARENT_CLEANUP_FAILED$') as caught:
                self.builder(CONTEXT, 'deploy')
        self.assertNotIn(SECRET, str(caught.exception)); self.closed(value)
        with self.assertRaisesRegex(parent.ParentBuildError, 'CONSUMED'):
            self.builder(CONTEXT, 'deploy')

    def test_failed_build_and_bundle_cleanup_still_close_receiver_and_prepared(self):
        with patch.object(handoff, 'build_owned_invocation', side_effect=ValueError(SECRET)), \
             patch.object(ingress.ReceivedCredentials, 'close', side_effect=ValueError(SECRET)):
            with self.assertRaisesRegex(parent.ParentBuildError, '^P2_PARENT_CLEANUP_FAILED$'):
                self.builder(CONTEXT, 'deploy')
        self.closed()

    def test_cleanup_interrupt_is_sanitized_after_all_owned_cleanup(self):
        for exception, args in ((KeyboardInterrupt(SECRET), ()), (SystemExit(SECRET), (130,))):
            # Each subcase has a distinct one-use parent and prepared plan.
            f = ParentCleanupTests('runTest'); f.setUp(); self.addCleanup(f.doCleanups)
            value = f.invocation()
            with patch.object(handoff, 'build_owned_invocation', return_value=value), \
                 patch.object(ingress.ReceivedCredentials, 'close', side_effect=exception):
                with self.assertRaises(type(exception)) as caught: f.builder(CONTEXT, 'deploy')
            self.assertEqual(args, caught.exception.args); f.closed(value)
            f.doCleanups()

    def test_success_transfers_invocation_after_temporary_cleanup(self):
        value = self.invocation()
        with patch.object(handoff, 'build_owned_invocation', return_value=value):
            self.assertIs(value, self.builder(CONTEXT, 'deploy'))
        self.assertEqual(1, len(self.seen))
        self.assertFalse(value._closed); self.assertIsNotNone(value.receiver._fd)
        self.assertFalse(self.prepared._closed)

    def test_receiver_cleanup_failure_does_not_transfer_invocation(self):
        value = self.invocation()
        close = ingress.CrossHostIngress.close
        def broken(obj):
            close(obj)
            raise ValueError(SECRET)
        with patch.object(handoff, 'build_owned_invocation', return_value=value), \
             patch.object(ingress.CrossHostIngress, 'close', new=broken):
            with self.assertRaisesRegex(parent.ParentBuildError, '^P2_PARENT_CLEANUP_FAILED$'):
                self.builder(CONTEXT, 'deploy')
        self.closed(value)

    def test_cleanup_failure_never_closes_foreign_return_or_borrowed_full_plan(self):
        borrowed = self.f.f.plan()
        builder = self.f.builder(plan=borrowed)
        foreign = Mock()
        with patch.object(handoff, 'build_owned_invocation', return_value=foreign), \
             patch.object(ingress.ReceivedCredentials, 'close', side_effect=ValueError(SECRET)), \
             patch.object(type(borrowed), 'close', create=True) as close:
            with self.assertRaisesRegex(parent.ParentBuildError, '^P2_PARENT_CLEANUP_FAILED$'):
                builder(CONTEXT, 'deploy')
        close.assert_not_called(); foreign.close.assert_not_called()
        self.assertTrue(self.seen[0]._used)


if __name__ == '__main__': unittest.main()
