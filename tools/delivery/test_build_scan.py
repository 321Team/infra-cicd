"""Real local Git/snapshots and command process tests; Podman/Trivy are mocked."""
import contextlib
import copy
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import build_scan as flow
from oci_test_fixtures import image_members, write_archive


class FlowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / 'app'
        self.repo.mkdir()
        self.base = 'docker.io/library/python@sha256:' + 'a' * 64
        self.config = {'schema': 'P1-CICD-app-build-scan-1', 'status': 'APPROVED_FOR_TEST_BUILD_SCAN',
                       'source': {'repository': flow.SOURCE_REPOSITORY, 'commit': None,
                                  'approved_by': 'fixture-reviewer',
                                  'approval_reference': 'https://github.com/321Team/example/issues/1'},
                       'services': {}}
        for name in flow.SERVICES:
            directory = self.repo / name
            directory.mkdir()
            (directory / 'Dockerfile').write_text('FROM ' + self.base + '\nCOPY . /app\n')
            (directory / 'tracked.txt').write_text('committed')
            step = {'cwd': name, 'argv': ['/fixture-test'], 'timeout_seconds': 60}
            self.config['services'][name] = {'context': name, 'dockerfile': name + '/Dockerfile',
                                             'base_image': self.base, 'setup': [],
                                             'unit': copy.deepcopy(step), 'contract': copy.deepcopy(step)}
        self.git('init', '--quiet')
        self.git('config', 'user.name', 'Fixture')
        self.git('config', 'user.email', 'fixture@example.invalid')
        self.git('remote', 'add', 'origin', 'https://github.com/' + flow.SOURCE_REPOSITORY + '.git')
        self.commit()
        self.fixture = self.root / 'image.tar'
        write_archive(self.fixture, image_members())
        self.image_id = flow.inspect_archive(self.fixture)['config_digest']
        self.context = {'workflow_sha': 'b' * 40, 'run_id': '123', 'attempt': 1}
        self.events, self.envs = [], []
        self.fault, self.scan_rc = None, 0
        self.blocked_service = 'transaction-api'
        self.receipt_mutator, self.mutated_archive = None, None
        self.evidence = self.root / 'evidence'
        self.evidence.mkdir()
        self.original_command = flow.run_command
        self.addCleanup(patch.stopall)
        self.commands = patch.object(flow, 'run_command', side_effect=self.fake_command).start()
        self.scanner = patch.object(flow, 'scan_pair', side_effect=self.fake_scan).start()

    def git(self, *args):
        return subprocess.check_output(['/usr/bin/git', '-C', str(self.repo), *args],
                                       stderr=subprocess.DEVNULL, text=True).strip()

    def commit(self):
        self.git('add', '.')
        self.git('commit', '-qm', 'fixture')
        self.config['source']['commit'] = self.git('rev-parse', 'HEAD')

    def fake_command(self, argv, cwd, env, output, timeout):
        label = output.stem
        self.events.append(label)
        self.envs.append(env.copy())
        if self.fault == label:
            output.write_text('fixture failure')
            return 42
        if self.fault == 'timeout' and label.endswith('-unit-0'):
            raise subprocess.TimeoutExpired(argv, timeout)
        if self.fault == 'signal' and label.endswith('-unit-0'):
            flow.interrupted(15, None)
        if argv[0] == '/usr/bin/git':
            return self.original_command(argv, cwd, env, output, timeout)
        if label == 'podman-version':
            output.write_text('podman version 6.1.0\n')
        elif label == 'podman-rootless':
            output.write_text('false\n' if self.fault == 'rootful' else 'true\n')
        else:
            output.write_text('fixture success\n')
        if label.endswith('-build'):
            source = Path(argv[-1])
            self.assertEqual((source / 'tracked.txt').read_text(), 'committed')
            self.assertFalse((source / 'untracked.txt').exists())
            self.assertFalse((source / 'generated.txt').exists())
            iid = Path(argv[argv.index('--iidfile') + 1])
            iid.write_text(('sha256:' + '0' * 64 if self.fault == 'iid' else self.image_id) + '\n')
        if label.endswith('-save'):
            Path(argv[argv.index('--output') + 1]).write_bytes(self.fixture.read_bytes())
        if label.endswith('-unit-0'):
            (cwd / 'generated.txt').write_text('test output')
        return 0

    def fake_scan(self, directory, context, source_sha, archives, env):
        self.events.append('scan-pair')
        images = {}
        for name in flow.SERVICES:
            blocked = self.scan_rc == 10 and name == self.blocked_service
            images[name] = {**flow.inspect_archive(archives[name]),
                            'scan': 'BLOCK' if blocked else 'PASS',
                            'exit_code': 10 if blocked else 0}
            if blocked:
                break  # Match scan_pair's first-vulnerability short circuit.
        data = {'IMAGE_SCAN': {0: 'PASS', 10: 'BLOCK'}.get(self.scan_rc, 'FAIL'),
                'context': copy.deepcopy(context), 'source_sha': source_sha, 'images': images}
        if self.scan_rc == 10:
            data['error_code'] = 'HIGH_CRITICAL_FINDINGS'
        if self.receipt_mutator:
            self.receipt_mutator(data)
        if self.mutated_archive:
            archives[self.mutated_archive].write_bytes(b'changed')
        (directory / 'result.json').write_text(json.dumps(data))
        return self.scan_rc, data

    def execute(self):
        return flow.execute(self.config, self.repo, self.evidence, self.context,
                            {'GITHUB_TOKEN': 'fixture-secret', 'KUBECONFIG': '/fixture/admin',
                             'TRIVY_SEVERITY': 'LOW', 'HTTP_PROXY': 'http://proxy.invalid'})

    def unit_scope_fixture(self):
        # Synthetic approval metadata; never a real app handoff approval.
        return {'status': 'NOT_APPLICABLE', 'reason': 'Fixture API scope has Contract only.',
                'approved_by': 'fixture-reviewer',
                'approval_reference': 'https://github.com/321Team/example/issues/1'}

    def test_explicit_unit_scope_runs_remaining_tests_without_fabricating_pass(self):
        scope = self.unit_scope_fixture()
        self.config['services']['transaction-api']['unit'] = scope
        rc, result = self.execute()
        self.assertEqual((rc, result['BUILD_SCAN']), (0, 'PASS'))
        self.assertEqual(result['not_applicable_tests'],
                         [{'service': 'transaction-api', 'phase': 'unit', **scope}])
        self.assertNotIn('transaction-api-unit-0', self.events)
        self.assertFalse((self.evidence / 'transaction-api-unit-0.log').exists())
        self.assertFalse(any(s['name'].startswith('transaction-api-unit') for s in result['steps']))
        ordered = ['fds-engine-unit-0', 'transaction-api-contract-0', 'fds-engine-contract-0',
                   'transaction-api-base-pull', 'transaction-api-build', 'fds-engine-build', 'scan-pair']
        self.assertEqual([name for name in self.events if name in ordered], ordered)
        self.assertEqual(result['source']['commit'], self.config['source']['commit'])
        self.assertEqual(result['DELIVERY_GATE'], 'NOT_VERIFIED')
        self.assertEqual(result['PUSH'], 'NOT_EXECUTED')
        self.assertEqual(result['DEPLOY'], 'NOT_EXECUTED')
        saved = json.loads((self.evidence / 'result.json').read_text())
        self.assertEqual(saved['not_applicable_tests'], result['not_applicable_tests'])
        for record in saved['not_applicable_tests']:
            self.assertNotIn('exit_code', record)
            self.assertNotIn('log_sha256', record)

    def test_incomplete_or_invalid_unit_scope_and_missing_phases_run_nothing(self):
        valid = self.unit_scope_fixture()
        cases = [('null', 'unit', None), ('empty', 'unit', {}),
                 ('implicit-string', 'unit', 'NOT_APPLICABLE'),
                 ('contract-exemption', 'contract', valid), ('setup-exemption', 'setup', [valid])]
        for key in valid:
            missing = valid.copy()
            missing.pop(key)
            cases.append(('missing-' + key, 'unit', missing))
        for key, value in [('status', 'SKIP'), ('reason', '  '), ('reason', 'line\nbreak'),
                           ('approved_by', ''), ('approved_by', None),
                           ('approval_reference', None), ('approval_reference', 'http://github.com/x')]:
            cases.append((f'invalid-{key}-{len(cases)}', 'unit', {**valid, key: value}))
        cases.append(('mixed-command', 'unit', {**valid, 'argv': ['/bin/true']}))
        original = copy.deepcopy(self.config)
        for name, phase, value in cases:
            self.config = copy.deepcopy(original)
            self.config['services']['transaction-api'][phase] = value
            self.evidence = self.root / name
            self.evidence.mkdir()
            with self.subTest(name=name):
                self.assertEqual(self.execute()[0], 1)
                self.commands.assert_not_called()
                self.scanner.assert_not_called()
        self.config = copy.deepcopy(original)
        for service in self.config['services'].values():
            service['unit'] = valid.copy()
        self.evidence = self.root / 'no-unit-phase'
        self.evidence.mkdir()
        rc, result = self.execute()
        self.assertEqual((rc, result['error_code']), (1, 'UNIT_PHASE_REQUIRED'))
        self.commands.assert_not_called()
        self.scanner.assert_not_called()
        self.assertEqual(result['not_applicable_tests'], [])

    def test_unit_scope_does_not_hide_required_test_failure_or_interruption(self):
        self.config['services']['transaction-api']['unit'] = self.unit_scope_fixture()
        for fault in ('fds-engine-unit-0', 'transaction-api-contract-0', 'fds-engine-contract-0',
                      'timeout', 'signal'):
            self.evidence = self.root / ('scope-failure-' + fault)
            self.evidence.mkdir()
            self.events = []
            self.fault = fault
            with self.subTest(fault=fault):
                rc, result = self.execute()
                self.assertEqual((rc, result['BUILD_SCAN']), (1, 'FAIL'))
                self.assertFalse(any(name.endswith('-build') or name.endswith('-base-pull')
                                     for name in self.events))
                self.scanner.assert_not_called()

    def test_unit_scope_preserves_required_source_directory_checks(self):
        self.config['services']['transaction-api']['unit'] = self.unit_scope_fixture()
        original = copy.deepcopy(self.config)
        for name, phase in (('fds-engine', 'unit'), ('transaction-api', 'contract'),
                            ('fds-engine', 'contract')):
            self.config = copy.deepcopy(original)
            self.config['services'][name][phase]['cwd'] = 'missing-directory'
            self.evidence = self.root / f'missing-{name}-{phase}'
            self.evidence.mkdir()
            self.events = []
            with self.subTest(service=name, phase=phase):
                rc, result = self.execute()
                self.assertEqual((rc, result['error_code']), (1, 'TEST_CWD_MISSING'))
                self.assertNotIn('podman-version', self.events)
                self.scanner.assert_not_called()

    def test_default_pending_input_runs_nothing(self):
        self.config = json.loads((flow.ROOT / flow.CONFIG).read_text())
        rc, data = self.execute()
        self.assertEqual(rc, 1)
        self.assertEqual(data['error_code'], 'APP_HANDOFF_PENDING')
        self.commands.assert_not_called()
        self.scanner.assert_not_called()

    def test_missing_approval_source_test_or_base_blocks_before_commands(self):
        cases = [('sha', lambda c: c['source'].update(commit=None)),
                 ('approval', lambda c: c['source'].update(approval_reference=None)),
                 ('unit', lambda c: c['services']['fds-engine'].update(unit=None)),
                 ('contract', lambda c: c['services']['transaction-api'].update(contract=None)),
                 ('base', lambda c: c['services']['transaction-api'].update(base_image='python:latest'))]
        original = copy.deepcopy(self.config)
        for name, mutate in cases:
            self.config = copy.deepcopy(original)
            mutate(self.config)
            self.evidence = self.root / name
            self.evidence.mkdir()
            with self.subTest(name=name):
                self.assertEqual(self.execute()[0], 1)
                self.commands.assert_not_called()

    def test_exact_commit_snapshot_and_test_build_scan_order(self):
        # Working-tree edits and untracked files must not contaminate the build.
        for name in flow.SERVICES:
            (self.repo / name / 'tracked.txt').write_text('dirty working copy')
            (self.repo / name / 'untracked.txt').write_text('untracked')
        rc, result = self.execute()
        self.assertEqual((rc, result['BUILD_SCAN']), (0, 'PASS'))
        self.assertEqual(result['PUSH'], 'NOT_EXECUTED')
        self.assertEqual(result['DEPLOY'], 'NOT_EXECUTED')
        self.assertEqual(result['DELIVERY_GATE'], 'NOT_VERIFIED')
        self.assertLess(self.events.index('fds-engine-contract-0'), self.events.index('transaction-api-base-pull'))
        self.assertLess(self.events.index('fds-engine-save'), self.events.index('scan-pair'))
        self.assertEqual(set(result['images']), set(flow.SERVICES))
        self.assertEqual(result.get('scan_status'), {name: 'PASS' for name in flow.SERVICES})
        for env in self.envs:
            self.assertNotIn('GITHUB_TOKEN', env)
            self.assertNotIn('KUBECONFIG', env)
            self.assertNotIn('TRIVY_SEVERITY', env)
            self.assertEqual(env['HOME'], str(self.evidence / 'home'))
            self.assertEqual(env['GIT_NO_REPLACE_OBJECTS'], '1')

    def test_test_failure_or_interruption_stops_before_build_and_scan(self):
        for fault in ('transaction-api-unit-0', 'fds-engine-contract-0', 'timeout', 'signal'):
            self.evidence = self.root / fault
            self.evidence.mkdir()
            self.events = []
            self.fault = fault
            with self.subTest(fault=fault):
                self.assertEqual(self.execute()[0], 1)
                self.assertFalse(any(name.endswith('-build') or name.endswith('-base-pull') for name in self.events))
                self.scanner.assert_not_called()

    def test_builder_failure_stops_before_scan(self):
        for fault in ('rootful', 'transaction-api-base-pull', 'transaction-api-build', 'fds-engine-save', 'iid'):
            self.evidence = self.root / fault
            self.evidence.mkdir()
            self.events = []
            self.fault = fault
            with self.subTest(fault=fault):
                self.assertEqual(self.execute()[0], 1)
                self.scanner.assert_not_called()

    def test_vulnerability_and_scanner_error_preserve_nonzero(self):
        for value in (10, 1):
            self.evidence = self.root / ('scan-' + str(value))
            self.evidence.mkdir()
            self.scan_rc = value
            with self.subTest(value=value):
                rc, data = self.execute()
                self.assertEqual(rc, value)
                self.assertEqual(data['PUSH'], 'NOT_EXECUTED')
                self.assertEqual(data['DEPLOY'], 'NOT_EXECUTED')

    def test_first_or_second_image_vulnerability_records_only_actual_scans(self):
        self.scan_rc = 10
        for blocked in flow.SERVICES:
            self.evidence = self.root / ('blocked-' + blocked)
            self.evidence.mkdir()
            self.blocked_service = blocked
            with self.subTest(blocked=blocked):
                rc, data = self.execute()
                self.assertEqual((rc, data['BUILD_SCAN']), (10, 'BLOCK'))
                expected = {'transaction-api': 'BLOCK', 'fds-engine': 'NOT_SCANNED'} if (
                    blocked == 'transaction-api') else {'transaction-api': 'PASS', 'fds-engine': 'BLOCK'}
                self.assertEqual(data.get('scan_status'), expected)
                self.assertNotIn('error_code', data)

    def test_pass_and_block_receipts_must_match_source_context_and_build_identity(self):
        for value, last in ((0, 'fds-engine'), (10, 'transaction-api'), (10, 'fds-engine')):
            self.scan_rc, self.blocked_service = value, last
            faults = [('source', lambda d: d.update(source_sha='f' * 40), 'SCAN_RECEIPT_MISMATCH'),
                      ('context', lambda d: d['context'].update(run_id='999'), 'SCAN_RECEIPT_MISMATCH')]
            for service in flow.SERVICES[:flow.SERVICES.index(last) + 1]:
                for key in ('archive_sha256', 'manifest_digest', 'config_digest'):
                    faults.append((service + '-' + key,
                                   lambda d, s=service, k=key: d['images'][s].update({k: 'wrong'}),
                                   'SCAN_BUILD_IDENTITY_MISMATCH'))
            for name, mutate, error in faults:
                self.evidence = self.root / f'receipt-{value}-{last}-{name}'
                self.evidence.mkdir()
                self.receipt_mutator = mutate
                with self.subTest(scan_rc=value, last=last, fault=name):
                    rc, data = self.execute()
                    self.assertEqual((rc, data['BUILD_SCAN'], data.get('error_code')), (1, 'FAIL', error))

    def test_receipt_service_coverage_and_status_must_match_exit_code(self):
        faults = [
            ('empty', lambda d: d.update(images={})),
            ('unknown', lambda d: d['images'].update(other=d['images']['transaction-api'])),
            ('missing-first', lambda d: d['images'].pop('transaction-api')),
            ('top-status', lambda d: d.update(IMAGE_SCAN='FAIL')),
            ('exit-code', lambda d: d['images']['fds-engine'].update(exit_code=1)),
            ('image-status', lambda d: d['images']['fds-engine'].update(scan='FAIL')),
        ]
        for value in (0, 10):
            self.scan_rc, self.blocked_service = value, 'fds-engine'
            for name, mutate in faults:
                self.evidence = self.root / f'coverage-{value}-{name}'
                self.evidence.mkdir()
                self.receipt_mutator = mutate
                with self.subTest(scan_rc=value, fault=name):
                    rc, data = self.execute()
                    self.assertEqual((rc, data['BUILD_SCAN'], data.get('error_code')),
                                     (1, 'FAIL', 'SCAN_RECEIPT_MISMATCH'))

    def test_block_requires_vulnerability_and_stops_at_first_blocked_service(self):
        self.scan_rc, self.blocked_service = 10, 'fds-engine'
        faults = [
            ('no-block', lambda d: d['images']['fds-engine'].update(scan='PASS', exit_code=0)),
            ('continued-after-block', lambda d: d['images']['transaction-api'].update(scan='BLOCK', exit_code=10)),
            ('scanner-error', lambda d: d.update(error_code='SCANNER_EXECUTION_ERROR')),
            ('missing-reason', lambda d: d.pop('error_code')),
        ]
        for name, mutate in faults:
            self.evidence = self.root / name
            self.evidence.mkdir()
            self.receipt_mutator = mutate
            with self.subTest(fault=name):
                rc, data = self.execute()
                self.assertEqual((rc, data['BUILD_SCAN'], data.get('error_code')),
                                 (1, 'FAIL', 'SCAN_RECEIPT_MISMATCH'))

    def test_built_archives_rechecked_even_when_second_service_is_not_scanned(self):
        for value in (0, 10):
            self.scan_rc = value
            for name in flow.SERVICES:
                self.evidence = self.root / f'mutated-{value}-{name}'
                self.evidence.mkdir()
                self.mutated_archive = name
                with self.subTest(scan_rc=value, archive=name):
                    rc, data = self.execute()
                    self.assertEqual((rc, data['BUILD_SCAN'], data.get('error_code')),
                                     (1, 'FAIL', 'BUILT_ARCHIVE_CHANGED'))

    def test_source_sha_mismatch_blocks_before_podman_and_tests(self):
        self.config['source']['commit'] = '0' * 40
        rc, data = self.execute()
        self.assertEqual(rc, 1)
        self.assertEqual(data['error_code'], 'SOURCE_HEAD_MISMATCH')
        self.assertEqual(self.events, ['source-head'])

    def test_git_replace_cannot_substitute_the_approved_commit(self):
        approved = self.config['source']['commit']
        (self.repo / 'transaction-api/tracked.txt').write_text('replacement contents')
        self.commit()
        replacement = self.config['source']['commit']
        self.git('replace', approved, replacement)
        self.git('checkout', '--detach', approved)
        self.config['source']['commit'] = approved
        # fake_command checks that Build sees the original committed bytes.
        self.assertEqual(self.execute()[0], 0)

    def test_wrong_remote_repository_blocks(self):
        self.git('remote', 'set-url', 'origin', 'https://github.com/other/repo.git')
        self.assertEqual(self.execute()[0], 1)
        self.assertNotIn('podman-rootless', self.events)

    def test_mutable_from_is_not_silently_rewritten(self):
        (self.repo / 'transaction-api/Dockerfile').write_text('FROM python:3.12-slim\n')
        self.commit()
        rc, data = self.execute()
        self.assertEqual(rc, 1)
        self.assertEqual(data['error_code'], 'DOCKERFILE_BASE_NOT_PINNED_OR_UNSUPPORTED')
        self.assertNotIn('podman-rootless', self.events)

    def test_tracked_symlink_rejected_before_tests(self):
        (self.repo / 'transaction-api/link').symlink_to('/etc/passwd')
        self.commit()
        rc, data = self.execute()
        self.assertEqual(rc, 1)
        self.assertEqual(data['error_code'], 'SOURCE_LINK_OR_SPECIAL_FILE')
        self.scanner.assert_not_called()

    def test_pending_cli_is_nonzero_and_does_not_enter_runtime(self):
        with patch.object(flow, 'runtime_context') as context, contextlib.redirect_stdout(io.StringIO()) as out:
            rc = flow.main([])
        self.assertEqual(rc, 1)
        context.assert_not_called()
        self.assertIn('APP_HANDOFF_PENDING', out.getvalue())
        self.assertNotIn('fixture-secret', out.getvalue())

    def test_local_approved_input_cannot_replace_workflow_commit_input(self):
        (self.root / 'config').mkdir()
        (self.root / flow.CONFIG).write_text(json.dumps(self.config))
        with patch.object(flow, 'ROOT', self.root), \
                patch.object(flow.os, 'chdir'), \
                patch.object(flow.signal, 'signal'), \
                patch.object(flow, 'runtime_context', return_value=self.context), \
                patch.object(flow.subprocess, 'check_output', return_value=b'{}'), \
                patch.object(flow, 'execute') as execute, \
                patch.object(flow.tempfile, 'mkdtemp') as mkdir, \
                contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(flow.main(['--execute', '--source-dir', str(self.repo)]), 1)
        execute.assert_not_called()
        mkdir.assert_not_called()
        self.assertIn('INPUT_NOT_FROM_WORKFLOW_COMMIT', output.getvalue())


class CommandAndInputTests(unittest.TestCase):
    def test_process_timeout_terminates_process_group(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pidfile = root / 'pid'
            program = ('import os,time,subprocess,sys;from pathlib import Path;'
                       'child=subprocess.Popen([sys.executable,"-c","import time;time.sleep(30)"]);'
                       'Path(' + repr(str(pidfile)) + ').write_text(str(os.getpid())+" "+str(child.pid));time.sleep(30)')
            with self.assertRaises(subprocess.TimeoutExpired):
                flow.run_command([sys.executable, '-c', program], root, os.environ.copy(), root / 'log', 1)
            self.assertTrue(pidfile.exists())
            parent, child = map(int, pidfile.read_text().split())
            with self.assertRaises(ProcessLookupError):
                os.kill(parent, 0)
            state = Path(f'/proc/{child}/stat')
            if state.exists():
                self.assertEqual(state.read_text().split()[2], 'Z')  # Exited; awaiting init reaping.

    def test_no_shell_interpolation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            rc = flow.run_command([sys.executable, '-c', 'import sys;print(sys.argv[1])', '$(touch forbidden)'],
                                  root, os.environ.copy(), root / 'log', 10)
            self.assertEqual(rc, 0)
            self.assertFalse((root / 'forbidden').exists())
            self.assertEqual((root / 'log').read_text().strip(), '$(touch forbidden)')

    def test_paths_cannot_escape_source_snapshot(self):
        for value in ('../x', '/tmp', 'a/../../b', '.git', 'a//b', 'a\\b', 'a/./b'):
            with self.subTest(value=value), self.assertRaises(flow.PreflightError):
                flow.relative(value)

    def test_duplicate_json_keys_are_rejected(self):
        with self.assertRaises(flow.PreflightError):
            flow.json_object(b'{"status":"PENDING","status":"APPROVED_FOR_TEST_BUILD_SCAN"}')


if __name__ == '__main__':
    unittest.main()
