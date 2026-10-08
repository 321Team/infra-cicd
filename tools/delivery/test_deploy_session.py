"""Composed MOCK network + real POSIX claim/checkpoints; no live deployment."""
import datetime as dt
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import deploy_binding as b
import deploy_session as s
from test_deploy_adapter import Fake, fixtures
from test_deploy_approval import Source, approval_fixture
from test_deploy_binding import NOW, encode
from test_deploy_credential import SA_UID, Reviewer, fixture
from test_deploy_plan import plan_inputs


@unittest.skipUnless(os.name == 'posix', 'Session uses real POSIX claims/checkpoints')
class SessionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='session-test-', dir=Path.cwd())
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.wall, self.mono = NOW, 0
        plan_obj, _, push_raw, execution = plan_inputs()
        plan_raw = encode(plan_obj)
        approval_raw, remote = approval_fixture()
        _, self.token, response = fixture()
        self.reviewer, self.source = Reviewer(response), Source(remote)
        _, objects = fixtures()
        self.client = Fake(objects)
        self.inputs = dict(
            plan_raw=plan_raw, approval_raw=approval_raw, push_raw=push_raw,
            expected_plan_sha256=b.sha(plan_raw),
            expected_approval_sha256=b.sha(approval_raw), execution=execution,
            token=self.token, expected_sa_uid=SA_UID,
            token_request_expires_at=(NOW + dt.timedelta(seconds=600)).isoformat(),
            ca_raw=b'MOCK_CA', expected_ca_sha256='a' * 64)
        self.credentials, self.writers = [], []
        real_credential, real_writer = s.credentials.Credential, s.checkpoint.BoundCheckpoint

        def credential(*args, **kwargs):
            obj = real_credential(*args, **kwargs)
            self.credentials.append(obj)
            return obj

        def writer(*args, **kwargs):
            obj = real_writer(*args, **kwargs)
            self.writers.append(obj)
            return obj

        for target, name, kwargs in (
                (s.transport, 'context_checked', {'return_value': None}),
                (s.transport, 'Client', {'return_value': self.client}),
                (s.approval, 'GitHubComments', {'return_value': self.source}),
                (s.credentials, 'Credential', {'side_effect': credential}),
                (s.checkpoint, 'BoundCheckpoint', {'side_effect': writer})):
            mocked = patch.object(target, name, **kwargs)
            value = mocked.start()
            self.addCleanup(mocked.stop)
            if name == 'Client':
                self.client_factory = value

    def session(self):
        return s.Session(self.root, lambda: 'MOCK_GITHUB', self.reviewer,
                         now=lambda: self.wall, clock=lambda: self.mono,
                         sleep=lambda _: None, timeout=3, interval=1)

    def run_session(self):
        return self.session().run(**self.inputs)

    def assert_clean(self, result):
        self.assertTrue(all(c.closed for c in self.credentials))
        self.assertTrue(all(w.closed for w in self.writers))
        self.assertNotIn(self.token, json.dumps(result))
        self.assertNotIn('never-in-receipt', json.dumps(result))
        self.assertEqual(result['DELIVERY_GATE'], 'NOT_VERIFIED')

    def test_composed_success_durable_chain_fresh_approval_and_cleanup(self):
        seen = []
        def before_patch(client, name):
            args, kwargs = self.client_factory.call_args
            seen.append((args[3](), kwargs['credential_remaining_seconds'](),
                         kwargs['remaining_seconds']()))
        self.client.patch_hook = before_patch
        result = self.run_session()
        receipt = result['execution_receipt']
        self.assertEqual(receipt['ROLLOUT'], 'READY')
        self.assertEqual(len(self.client.patches), 2)
        self.assertEqual(len(self.source.calls), 1 + self.client.gets + 2)
        self.assertTrue(all(token == self.token and seconds == 600 and approval > 0
                            for token, seconds, approval in seen))
        records = sorted(self.root.glob('**/000*.json'))
        self.assertGreater(len(records), 2)
        previous = None
        for path in records:
            raw = path.read_bytes()
            record = json.loads(raw)
            self.assertEqual(record['previous_sha256'], previous)
            previous = b.sha(raw)
            self.assertNotIn(self.token.encode(), raw)
        self.assertEqual(receipt['CREDENTIAL_METADATA'], 'CALLER_SUPPLIED_NOT_VERIFIED_HERE')
        self.assertEqual(result['credential']['local_credential_cleanup'], 'LOCAL_REFERENCE_DROPPED')
        self.assert_clean(result)

    def test_invalid_plan_precedes_token_review_and_network(self):
        self.inputs['expected_plan_sha256'] = '0' * 64
        result = self.run_session()
        self.assertEqual(result['status'], 'NOT_EXECUTED')
        self.assertEqual(self.reviewer.calls, [])
        self.client_factory.assert_not_called()
        self.assertEqual(list(self.root.iterdir()), [])

    def test_bad_ca_precedes_remote_review(self):
        with patch.object(s.transport, 'context_checked', side_effect=ValueError(self.token)):
            result = self.run_session()
        self.assertEqual(self.reviewer.calls, [])
        self.assert_clean(result)

    def test_denied_review_never_creates_transport_or_claim(self):
        self.reviewer.obj['status']['authenticated'] = False
        result = self.run_session()
        self.client_factory.assert_not_called()
        self.assertEqual(list(self.root.iterdir()), [])
        self.assert_clean(result)

    def test_transport_construction_failure_closes_reviewed_credential(self):
        self.client_factory.side_effect = ValueError(self.token)
        result = self.run_session()
        self.assertEqual(len(self.credentials), 1)
        self.assertEqual(result['status'], 'NOT_EXECUTED')
        self.assertEqual(list(self.root.iterdir()), [])
        self.assert_clean(result)

    def test_invalid_status_expiry_rejected_before_review(self):
        for seconds in (-1, 4000):
            with self.subTest(seconds=seconds):
                self.inputs['token_request_expires_at'] = (NOW + dt.timedelta(seconds=seconds)).isoformat()
                result = self.run_session()
                self.assertEqual(result['status'], 'NOT_EXECUTED')
                self.assertEqual(self.reviewer.calls, [])
                self.assert_clean(result)

    def test_tokenrequest_bound_shorter_than_jwt_blocks_before_claim(self):
        self.inputs['token_request_expires_at'] = (NOW + dt.timedelta(seconds=1)).isoformat()
        result = self.run_session()
        self.assertEqual(result['execution_receipt']['error_code'], 'CREDENTIAL_WINDOW_TOO_SHORT')
        self.assertEqual(self.client.gets, 0)
        self.assertEqual(list(self.root.iterdir()), [])
        self.assert_clean(result)

    def test_review_time_does_not_reset_tokenrequest_budget(self):
        self.inputs['token_request_expires_at'] = (NOW + dt.timedelta(seconds=38)).isoformat()
        self.reviewer.hook = lambda: setattr(self, 'mono', 9)
        result = self.run_session()
        self.assertEqual(result['execution_receipt']['error_code'], 'CREDENTIAL_WINDOW_TOO_SHORT')
        self.assertEqual(self.client.gets, 0)
        self.assert_clean(result)

    def test_changed_approval_denies_claim_and_cleans_credential(self):
        self.source.obj['body'] = 'changed'
        result = self.run_session()
        self.assertEqual(result['execution_receipt']['error_code'], 'APPROVAL_REFERENCE_CHANGED')
        self.assertEqual(self.client.gets, 0)
        self.assertEqual(list(self.root.iterdir()), [])
        self.assert_clean(result)

    def test_expiry_after_first_patch_prevents_second_patch(self):
        self.client.patch_hook = lambda *_: setattr(self, 'mono', 601)
        result = self.run_session()
        self.assertEqual(len(self.client.patches), 1)
        self.assertNotEqual(result['execution_receipt']['ROLLOUT'], 'READY')
        self.assert_clean(result)

    def test_checkpoint_failure_before_patch_is_fail_closed(self):
        with patch.object(s.checkpoint.Journal, '__call__', side_effect=OSError(self.token)):
            result = self.run_session()
        self.assertEqual(self.client.patches, [])
        self.assertEqual(result['execution_receipt']['error_code'], 'CHECKPOINT_FAILED')
        self.assert_clean(result)

    def test_checkpoint_failure_after_patch_stops_next_write(self):
        real = s.checkpoint.Journal.__call__
        def save(obj, receipt):
            if self.client.patches:
                raise OSError(self.token)
            return real(obj, receipt)
        with patch.object(s.checkpoint.Journal, '__call__', save):
            result = self.run_session()
        self.assertEqual(len(self.client.patches), 1)
        receipt = result['execution_receipt']
        self.assertEqual(receipt['error_code'], 'CHECKPOINT_FAILED')
        self.assertEqual(receipt['ROLLBACK'], 'MANUAL_RECONCILIATION_REQUIRED')
        self.assert_clean(result)

    def test_close_failure_still_drops_local_credential(self):
        real = s.checkpoint.Journal.close
        def close(obj):
            real(obj)
            raise OSError(self.token)
        with patch.object(s.checkpoint.Journal, 'close', close):
            result = self.run_session()
        self.assertEqual(result['status'], 'MANUAL_RECONCILIATION_REQUIRED')
        self.assertEqual(result['error_code'], 'SESSION_CHECKPOINT_CLOSE_FAILED')
        self.assert_clean(result)

    def test_same_session_cannot_return_stale_success_or_retry(self):
        session = self.session()
        session.run(**self.inputs)
        before = len(self.reviewer.calls), len(self.source.calls), self.client.gets
        result = session.run(**self.inputs)
        self.assertEqual(result['error_code'], 'SESSION_ALREADY_USED')
        self.assertIsNone(result['execution_receipt'])
        self.assertEqual(before, (len(self.reviewer.calls), len(self.source.calls), self.client.gets))

    def test_new_session_same_plan_cannot_bypass_persistent_claim(self):
        self.run_session()
        gets = self.client.gets
        result = self.run_session()
        self.assertEqual(result['execution_receipt']['error_code'], 'PLAN_ALREADY_CLAIMED')
        self.assertEqual(self.client.gets, gets)
        self.assertEqual(len(self.client.patches), 2)
        self.assert_clean(result)


if __name__ == '__main__':
    unittest.main()
