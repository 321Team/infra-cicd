"""Dispatch/secret/one-use tests with no external process or network."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import push_workflow as workflow
import test_push_images as fixtures


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.PushTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.home = self.fixture.root
        self.home.chmod(0o700)
        self.fixture.evidence.rename(self.home / 'FDS_CICD_AppWorkflow.fixture')
        self.fixture.evidence = self.home / 'FDS_CICD_AppWorkflow.fixture'
        self.fixture.evidence.chmod(0o700)
        self.inputs = self.home / 'FDS_CICD_PushInputs'
        self.inputs.mkdir(mode=0o700)
        self.fixture.record['source_evidence_directory'] = self.fixture.evidence.name
        self.incoming = {'PUSH_RECORD_NAME': 'approved.json', 'PUSH_EXECUTE': 'true',
                         'HARBOR_PUSH_USERNAME': 'robot$fixture', 'HARBOR_PUSH_TOKEN': 'secret-canary'}
        self.refresh()

    def refresh(self):
        self.incoming['PUSH_RECORD_SHA256'] = self.fixture.write(
            self.inputs / 'approved.json', self.fixture.record)

    def run_flow(self):
        with patch.object(workflow, 'context', return_value=self.fixture.context), \
             patch.object(workflow.push, 'SKOPEO', '/usr/bin/true'), \
             patch.object(workflow.push, 'scan_pair', side_effect=self.fixture.scanner), \
             patch.object(workflow.push, 'check_db'), \
             patch.object(workflow.push, 'command', side_effect=self.fixture.registry):
            return workflow.run(dict(self.incoming), self.home)

    def test_success_claim_and_credential_cleanup(self):
        self.assertEqual(self.run_flow(), 0)
        self.assertEqual(len(list((self.home / 'FDS_CICD_PushClaims').glob('*.json'))), 1)
        self.assertEqual(list(self.home.glob('FDS_CICD_PushAuth.*')), [])
        output = next(p for p in self.home.glob('FDS_CICD_Push.*') if p.is_dir())
        self.assertEqual(json.loads((output / 'result.json').read_text())['PUSH'], 'PASS')
        self.assertTrue((output / 'dispatch-SHA256SUMS').is_file())
        for file in output.rglob('*'):
            if file.is_file():
                self.assertNotIn('secret-canary', file.read_text())

    def test_replay_refused_before_second_registry_access(self):
        self.assertEqual(self.run_flow(), 0)
        count = len(self.fixture.calls)
        with self.assertRaises(FileExistsError):
            self.run_flow()
        self.assertEqual(len(self.fixture.calls), count)
        self.assertFalse(list(self.home.glob('FDS_CICD_PushAuth.*')))

    def test_validation_mode_needs_no_secrets_and_does_not_claim(self):
        self.incoming['PUSH_EXECUTE'] = 'false'
        self.incoming.pop('HARBOR_PUSH_USERNAME')
        self.incoming.pop('HARBOR_PUSH_TOKEN')
        self.assertEqual(self.run_flow(), 0)
        self.assertFalse((self.home / 'FDS_CICD_PushClaims').exists())
        self.assertEqual(self.fixture.calls, [])

    def test_path_traversal_refused(self):
        self.incoming['PUSH_RECORD_NAME'] = '../approved.json'
        with self.assertRaises(workflow.push.PreflightError):
            self.run_flow()
        self.assertEqual(self.fixture.calls, [])

    def test_record_hash_drift_refused(self):
        self.incoming['PUSH_RECORD_SHA256'] = '0' * 64
        with self.assertRaises(workflow.push.PreflightError):
            self.run_flow()
        self.assertEqual(self.fixture.calls, [])

    def test_expired_authorization_refused(self):
        self.fixture.record['push_authorization']['expires_at'] = (
            datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        self.refresh()
        with self.assertRaises(workflow.push.PreflightError):
            self.run_flow()
        self.assertEqual(self.fixture.calls, [])

    def test_missing_secret_leaves_no_claim_or_private_auth(self):
        self.incoming.pop('HARBOR_PUSH_TOKEN')
        with self.assertRaises(workflow.push.PreflightError):
            self.run_flow()
        self.assertFalse((self.home / 'FDS_CICD_PushClaims').exists())
        self.assertFalse(list(self.home.glob('FDS_CICD_PushAuth.*')))

    def test_different_workflow_rejected_before_runtime_context(self):
        with patch.object(workflow.push, 'runtime_context') as runtime:
            with self.assertRaises(workflow.push.PreflightError):
                workflow.context({'GITHUB_WORKFLOW_REF': 'wrong'})
            runtime.assert_not_called()

    def test_existing_marker_not_truncated(self):
        marker = workflow.claim(self.home, 'a' * 64, self.fixture.context)
        before = marker.read_bytes()
        with self.assertRaises(FileExistsError):
            workflow.claim(self.home, 'a' * 64, {'other': True})
        self.assertEqual(marker.read_bytes(), before)

    def test_workflow_sha_mismatch_before_checkout_status(self):
        with patch.object(workflow.push, 'runtime_context', return_value=self.fixture.context), \
             patch.object(workflow.subprocess, 'check_output') as git:
            with self.assertRaises(workflow.push.PreflightError):
                workflow.context({'GITHUB_WORKFLOW_REF': workflow.WORKFLOW,
                                  'GITHUB_WORKFLOW_SHA': 'f' * 40})
            git.assert_not_called()


if __name__ == '__main__':
    unittest.main()
