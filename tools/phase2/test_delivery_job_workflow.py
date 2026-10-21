"""Static manual workflow boundaries; never dispatches a GitHub job or Runner."""
import ast
import hashlib
from pathlib import Path
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / '.github/workflows/p2-rosa-delivery.yml'


class JobWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.text = WORKFLOW.read_text(encoding='utf-8')
        self.w = yaml.load(self.text, Loader=yaml.BaseLoader)

    def test_only_manual_request_default_status_and_execute_false(self):
        self.assertEqual({'workflow_dispatch'}, set(self.w['on']))
        inputs = self.w['on']['workflow_dispatch']['inputs']
        self.assertEqual({'command', 'execute'}, set(inputs))
        self.assertEqual('status', inputs['command']['default'])
        self.assertEqual(['status', 'preflight', 'deploy', 'restore'], inputs['command']['options'])
        self.assertEqual('false', inputs['execute']['default'])
        self.assertEqual('boolean', inputs['execute']['type'])

    def test_hosted_gate_and_job_level_if_prevent_unconfigured_runner_scheduling(self):
        prepare, delivery = self.w['jobs']['prepare'], self.w['jobs']['delivery']
        self.assertEqual('ubuntu-24.04', prepare['runs-on'])
        self.assertEqual('prepare', delivery['needs'])
        self.assertIn("needs.prepare.outputs.configured == 'true'", delivery['if'])
        self.assertIn('inputs.execute', delivery['if'])
        self.assertIn("inputs.command != 'status'", delivery['if'])
        for stage in (prepare, delivery):
            for guard in ("github.repository == '321Team/infra-cicd'", "github.ref == 'refs/heads/main'",
                          "github.event_name == 'workflow_dispatch'", 'github.run_attempt == 1'):
                self.assertIn(guard, stage['if'])
        gate = prepare['steps'][1]['run']
        self.assertIn('workflow_gate()', gate)
        self.assertIn("raise SystemExit(0 if result['dispatch_configured'] else 2)", gate)
        self.assertEqual('${{ steps.site.outputs.configured }}', prepare['outputs']['configured'])

    def test_site_none_is_literal_not_an_environment_loader(self):
        tree = ast.parse((ROOT/'tools/phase2/delivery_job_site.py').read_text(encoding='utf-8'))
        assignments = [n for n in tree.body if isinstance(n, ast.Assign)]
        self.assertEqual(1, len(assignments))
        self.assertEqual('SITE', assignments[0].targets[0].id)
        self.assertIsNone(ast.literal_eval(assignments[0].value))
        self.assertFalse(any(isinstance(n, (ast.Call, ast.Import, ast.ImportFrom)) for n in ast.walk(tree)))

    def test_exact_checkout_read_only_token_no_automatic_issuance_or_raw_artifact(self):
        self.assertEqual({'contents': 'read'}, self.w['permissions'])
        for stage in self.w['jobs'].values():
            for step in stage['steps']:
                if 'uses' in step:
                    self.assertEqual('actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803', step['uses'])
                    self.assertEqual('${{ github.sha }}', step['with']['ref'])
                    self.assertEqual('false', step['with']['persist-credentials'])
        for forbidden in ('secrets.', 'id-token:', 'GITHUB_TOKEN:', 'AWS_ACCESS_KEY', 'oc login',
                          'create token', 'upload-artifact', 'workflow_run:', 'pull_request_target:'):
            self.assertNotIn(forbidden, self.text)

    def test_runner_uses_registered_labels_fresh_owned_checkout_no_delete(self):
        delivery = self.w['jobs']['delivery']
        self.assertEqual(['self-hosted', 'Linux', 'X64', 'p1-cicd', 'onprem', 'podman'], delivery['runs-on'])
        self.assertEqual('30', delivery['timeout-minutes'])
        self.assertEqual('false', self.w['concurrency']['cancel-in-progress'])
        self.assertEqual({'P2_JOB_COMMAND', 'P2_CODE_CHECKOUT'}, set(delivery['env']))
        self.assertEqual('p2-job-${{ github.run_id }}-${{ github.run_attempt }}', delivery['env']['P2_CODE_CHECKOUT'])
        self.assertIn('test ! -e', delivery['steps'][0]['run'])
        self.assertIn('test ! -L', delivery['steps'][0]['run'])
        checkout = delivery['steps'][1]['with']
        self.assertEqual('${{ env.P2_CODE_CHECKOUT }}', checkout['path'])
        for name in ('clean', 'set-safe-directory', 'submodules', 'lfs'):
            self.assertEqual('false', checkout[name])
        self.assertNotIn('rm ', self.text)
        self.assertIn('/usr/bin/python3 -I -B', delivery['steps'][2]['run'])
        self.assertIn('delivery_job.main', delivery['steps'][2]['run'])

    def test_p1_workflow_bytes_unchanged_and_hosted_ci_exact_stack_base(self):
        digest = hashlib.sha256((ROOT/'.github/workflows/app-deploy.yml').read_bytes()).hexdigest()
        self.assertEqual('7aa4fda712021f414640c130cb9b45eb3ba1e036f749f9bafce0f7330c31009c', digest)
        validation = yaml.load((ROOT/'.github/workflows/p2-intake-validation.yml').read_text(encoding='utf-8'), Loader=yaml.BaseLoader)
        self.assertIn('codex/p2-source-session-20261021', validation['on']['pull_request']['branches'])
        self.assertIn('.github/workflows/p2-rosa-delivery.yml', validation['on']['pull_request']['paths'])
        self.assertEqual('ubuntu-24.04', validation['jobs']['p2-intake']['runs-on'])


if __name__ == '__main__': unittest.main()
