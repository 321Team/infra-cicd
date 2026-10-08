"""Workflow/receiver composition, synthetic credentials and mocked remote APIs."""
import datetime as dt
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

import deploy_binding as b
import deploy_workflow as w
import test_deploy_session as session_fixture
from test_deploy_binding import NOW, encode
from test_deploy_credential import SA_UID


@unittest.skipUnless(os.name == 'posix', 'POSIX Workflow/files required')
class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.fixture = session_fixture.SessionTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        f = self.fixture
        self.home = f.root
        self.root = self.home / 'FDS_CICD_DeployInputs'
        self.root.mkdir(mode=0o700)
        self.record = {'schema': 'P1-CICD-deploy-inputs-1', 'sa_uid': SA_UID,
                       'plan': f.inputs['plan_raw'].decode(),
                       'approval': f.inputs['approval_raw'].decode(),
                       'push_receipt': f.inputs['push_raw'].decode()}
        self.env = {'DEPLOY_RECORD_NAME': 'approved.json', 'DEPLOY_EXECUTE': 'false'}
        self.refresh()
        self.receiver = self
        self.opened = self.closed = 0
        real_session, real_window = w.deploy_session.Session, w.native.Window
        patches = [
            patch.object(w.access, 'host_checked'),
            patch.object(w, 'context', return_value=f.inputs['execution']),
            patch.object(w.access, 'ca_checked', return_value=(b'MOCK_CA', None)),
            patch.object(w.native, 'utcnow', return_value=NOW),
            patch.object(w.native, 'Window', side_effect=lambda s: real_window(s, now=lambda: NOW)),
            patch.object(w.handoff, 'Receiver', return_value=self.receiver),
            patch.object(w.deploy_session, 'Session', side_effect=lambda *a, **k: real_session(
                *a, **k, now=lambda: NOW, clock=lambda: 0, timeout=3, interval=1))]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def refresh(self):
        raw = encode(self.record)
        path = self.root / 'approved.json'
        path.write_bytes(raw)
        path.chmod(0o600)
        self.env['DEPLOY_RECORD_SHA256'] = b.sha(raw)

    def open(self):
        self.opened += 1

    def close(self):
        self.closed += 1

    def candidate(self, uid):
        self.assertEqual(uid, SA_UID)
        return dict(stage='candidate', token=self.fixture.token, sa_uid=uid,
                    status_expires_at=(NOW + dt.timedelta(seconds=900)).isoformat())

    def review(self, token, audience, timeout):
        return self.fixture.reviewer.review(token, audience, timeout)

    def run_flow(self):
        return w.run(dict(self.env), self.home, 'MOCK_GITHUB')

    def latest_result(self):
        folder = list(self.home.glob('FDS_CICD_Deploy.*'))[-1]
        return json.loads((folder / 'result.json').read_bytes())

    def test_default_mode_never_opens_channel_or_claims_or_calls_approval(self):
        self.assertEqual(self.run_flow(), 0)
        self.assertEqual(self.opened, 0)
        self.assertEqual(self.fixture.source.calls, [])
        self.assertEqual(self.fixture.reviewer.calls, [])
        self.assertFalse((self.home / 'FDS_CICD_DeployClaims').exists())
        self.assertEqual(self.latest_result()['DEPLOY'], 'NOT_EXECUTED')

    def test_execute_connects_existing_session_and_persists_safe_receipt(self):
        self.env['DEPLOY_EXECUTE'] = 'true'
        self.assertEqual(self.run_flow(), 0)
        self.assertEqual(self.opened, 1)
        self.assertEqual(self.closed, 1)
        self.assertEqual(len(self.fixture.client.patches), 2)
        self.assertEqual(self.latest_result()['execution_receipt']['ROLLOUT'], 'READY')
        for path in self.home.rglob('*.json'):
            self.assertNotIn(self.fixture.token.encode(), path.read_bytes())

    def test_invalid_hash_never_opens_receiver(self):
        self.env['DEPLOY_RECORD_SHA256'] = '0' * 64
        with self.assertRaises(b.Invalid):
            self.run_flow()
        self.assertEqual(self.opened, 0)

    def test_path_escape_rejected(self):
        self.env['DEPLOY_RECORD_NAME'] = '../approved.json'
        with self.assertRaises(b.Invalid):
            self.run_flow()

    def test_world_readable_input_rejected(self):
        (self.root / 'approved.json').chmod(0o644)
        with self.assertRaises(b.Invalid):
            self.run_flow()

    def test_approval_change_blocks_before_receiver(self):
        self.env['DEPLOY_EXECUTE'] = 'true'
        self.fixture.source.obj['body'] = 'changed'
        self.assertEqual(self.run_flow(), 1)
        self.assertEqual(self.opened, 0)
        self.assertEqual(self.latest_result()['error_code'], 'DEPLOY_HANDOFF_UNCONFIRMED')

    def test_handoff_loss_saves_uncertain_result_without_patch(self):
        self.env['DEPLOY_EXECUTE'] = 'true'
        with patch.object(self, 'candidate', side_effect=OSError(self.fixture.token)):
            self.assertEqual(self.run_flow(), 1)
        self.assertEqual(self.fixture.client.patches, [])
        self.assertEqual(self.closed, 1)
        self.assertNotIn(self.fixture.token, str(self.latest_result()))

    def test_socket_cleanup_failure_cannot_report_success(self):
        self.env['DEPLOY_EXECUTE'] = 'true'
        with patch.object(self, 'close', side_effect=OSError(self.fixture.token)):
            self.assertEqual(self.run_flow(), 1)
        self.assertEqual(self.latest_result()['status'], 'MANUAL_RECONCILIATION_REQUIRED')


class ContextTests(unittest.TestCase):
    def environment(self):
        return {'GITHUB_EVENT_NAME': 'workflow_dispatch', 'GITHUB_WORKFLOW_REF': w.WORKFLOW,
                'GITHUB_WORKFLOW_SHA': 'a' * 40, 'GITHUB_SHA': 'a' * 40,
                'GITHUB_REPOSITORY': '321Team/infra-cicd', 'GITHUB_REF': 'refs/heads/main',
                'GITHUB_RUN_ID': '123', 'GITHUB_RUN_ATTEMPT': '1'}

    def test_clean_main_exact_head(self):
        with patch.object(w.subprocess, 'check_output', side_effect=[b'a' * 40 + b'\n', b'']):
            self.assertEqual(w.context(self.environment())['run_id'], 123)

    def test_branch_retry_wrong_workflow_or_sha_rejected(self):
        for field, value in [('GITHUB_RUN_ATTEMPT', '2'), ('GITHUB_REF', 'refs/heads/feature'),
                             ('GITHUB_WORKFLOW_REF', 'other'), ('GITHUB_WORKFLOW_SHA', 'b' * 40)]:
            env = self.environment()
            env[field] = value
            with self.subTest(field=field), self.assertRaises(b.Invalid):
                w.context(env)

    def test_wrong_checkout_or_dirty_tree_rejected(self):
        for outputs in ([b'b' * 40, b''], [b'a' * 40, b' M modified.py']):
            with patch.object(w.subprocess, 'check_output', side_effect=outputs), self.assertRaises(b.Invalid):
                w.context(self.environment())

    def test_workflow_is_dispatch_only_and_injects_no_cluster_secret(self):
        raw = (Path(__file__).resolve().parents[2] / '.github/workflows/app-deploy.yml').read_text()
        self.assertIn('default: false', raw)
        self.assertIn('cancel-in-progress: false', raw)
        self.assertNotIn('pull_request:', raw)
        self.assertNotIn('secrets.', raw)
        self.assertIn('issues: read', raw)
        self.assertIn('github.run_attempt == 1', raw)


if __name__ == '__main__':
    unittest.main()
