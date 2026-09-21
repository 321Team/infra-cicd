"""Real committed Git intake and six-file checks; transport redirect is fixture-only."""
import contextlib
import base64
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import app_workflow as app
from test_offline_supply import fixture


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        supply, record, identity = fixture(self.root)
        self.config = json.loads((app.flow.ROOT / app.flow.CONFIG).read_text())
        self.config['status'] = 'APPROVED_FOR_TEST_BUILD_SCAN'
        # Pin the fixture's own source repository regardless of what the committed
        # config currently holds; the private-repository path is opted into explicitly
        # by the handful of tests that override this, not inherited from disk.
        self.config['source'].update(repository=app.flow.SOURCE_REPOSITORY, approved_by='fixture only',
            approval_reference='https://github.com/fixture/fixture/issues/1')
        self.config['offline_supply'] = record
        self.config['services']['transaction-api']['unit'] = {
            'status': 'NOT_APPLICABLE', 'reason': 'fixture scope', 'approved_by': 'fixture only',
            'approval_reference': 'https://github.com/fixture/fixture/issues/1'}
        self.repo = self.root / 'app'
        self.repo.mkdir()
        for name, service in self.config['services'].items():
            service['base_image'] = 'docker.io/library/python@' + identity['manifest_digest']
            folder = self.repo / name
            folder.mkdir()
            (folder / 'Dockerfile').write_text(app.flow.offline.dockerfile(name, service['base_image']))
            for profile in ('runtime', 'test'):
                (folder / ('requirements-' + profile + '.lock')).write_bytes(
                    (supply / (name + '-' + profile) / 'requirements.lock').read_bytes())
            (folder / 'requirements.txt').write_text('fixture-pkg==1.0\n')
            (folder / 'requirements-test.txt').write_text('-r requirements.txt\n')
        self.git('init', '--quiet')
        self.git('config', 'user.name', 'Fixture')
        self.git('config', 'user.email', 'fixture@example.invalid')
        self.git('remote', 'add', 'origin', app.auth.origin(self.config['source']['repository']))
        self.commit()
        self.folder = self.root / 'evidence'
        self.folder.mkdir()
        self.context = {'workflow_sha': 'b' * 40, 'run_id': '123', 'attempt': 1,
                        'input_sha256': 'c' * 64}

    def git(self, *args):
        return subprocess.check_output(['/usr/bin/git', '-C', str(self.repo), *args],
                                       text=True, stderr=subprocess.DEVNULL).strip()

    def commit(self):
        self.git('add', '.')
        self.git('commit', '-qm', 'fixture')
        self.config['source']['commit'] = self.git('rev-parse', 'HEAD')

    def check(self):
        return app.run(self.config, self.folder, self.context, {}, source=self.repo)

    def test_committed_handoff_and_hashes_without_app_execution(self):
        with patch.object(app.flow, 'execute') as execute:
            rc, result = self.check()
        self.assertEqual(rc, 0)
        execute.assert_not_called()
        self.assertEqual(result['APP_TEST_BUILD_SCAN'], 'NOT_EXECUTED')
        handoff = json.loads((self.folder / 'handoff.json').read_text())
        self.assertEqual(len(handoff['files']), 10)
        self.assertEqual(handoff['test_scope']['transaction-api']['unit']['status'], 'NOT_APPLICABLE')
        self.assertEqual(result['handoff_sha256'], app.flow.sha256(self.folder / 'handoff.json'))
        self.assertEqual((self.folder / 'SHA256SUMS').read_text(),
                         app.flow.sha256(self.folder / 'result.json') + '  result.json\n')

    def test_dirty_worktree_and_untracked_files_do_not_supply_handoff(self):
        (self.repo / 'transaction-api/Dockerfile').write_text('FROM unapproved\n')
        (self.repo / 'untracked.py').write_text('raise RuntimeError("must not execute")')
        self.assertEqual(self.check()[0], 0)

    def test_pending_config_stops_before_git_or_build(self):
        self.config['status'] = 'PENDING_APP_HANDOFF'
        with patch.object(app, 'command_runner') as git, patch.object(app.flow, 'execute') as execute:
            rc, result = app.run(self.config, self.folder, self.context, {}, execute=True)
        self.assertEqual(rc, 1)
        self.assertEqual(result['error_code'], 'APP_HANDOFF_PENDING')
        git.assert_not_called()
        execute.assert_not_called()

    def test_missing_source_approval_or_unit_scope_stops_before_git(self):
        faults = [('source', 'commit'), ('source', 'approved_by'), ('source', 'approval_reference'),
                  ('transaction-api', 'unit')]
        for i, (section, field) in enumerate(faults):
            config = copy.deepcopy(self.config)
            (config['source'] if section == 'source' else config['services'][section])[field] = None
            folder = self.root / ('fault-' + str(i))
            folder.mkdir()
            with self.subTest(field=field), patch.object(app, 'command_runner') as git:
                self.assertEqual(app.run(config, folder, {}, {}, execute=True)[0], 1)
                git.assert_not_called()

    def test_wrong_source_sha(self):
        self.config['source']['commit'] = 'a' * 40
        self.assertEqual(self.check()[1]['error_code'], 'SOURCE_HEAD_MISMATCH')

    def test_wrong_origin(self):
        self.git('remote', 'set-url', 'origin', 'https://example.invalid/app.git')
        self.assertEqual(self.check()[1]['error_code'], 'SOURCE_ORIGIN_MISMATCH')

    def test_all_six_handoff_files_are_bound(self):
        for name in app.flow.SERVICES:
            for filename in ('Dockerfile', 'requirements-runtime.lock', 'requirements-test.lock'):
                path = self.repo / name / filename
                raw = path.read_bytes()
                path.write_bytes(raw + b'# changed\n')
                self.commit()
                folder = self.root / (name + '-' + filename)
                folder.mkdir()
                with self.subTest(service=name, file=filename):
                    result = app.run(self.config, folder, {}, {}, source=self.repo)[1]
                    self.assertEqual(result['error_code'], 'HANDOFF_DOCKERFILE_MISMATCH'
                                     if filename == 'Dockerfile' else 'SOURCE_LOCK_MISMATCH')
                path.write_bytes(raw)
                self.commit()

    def test_missing_lock(self):
        (self.repo / 'transaction-api/requirements-runtime.lock').unlink()
        self.commit()
        self.assertTrue(self.check()[1]['error_code'].startswith('HANDOFF_FILE_MISSING:'))

    def test_original_requirements_change_requires_new_supply(self):
        (self.repo / 'fds-engine/requirements.txt').write_text('another==2\n')
        self.commit()
        self.assertEqual(self.check()[1]['error_code'], 'SOURCE_REQUIREMENTS_CHANGED')

    def test_source_symlink_rejected_before_reading_blobs(self):
        (self.repo / 'link').symlink_to('/etc/passwd')
        self.commit()
        self.assertEqual(self.check()[1]['error_code'], 'SOURCE_LINK_OR_SUBMODULE')

    def test_anonymous_bare_fetch_uses_only_exact_commit(self):
        calls = []
        original = app.flow.run_command

        def transport(argv, cwd, env, output, timeout):
            calls.append((argv, env))
            if 'fetch' in argv:
                # Only this fixture redirects HTTPS origin to an existing local test repository.
                self.assertEqual(argv[-1], self.config['source']['commit'])
                argv = argv[:]
                argv[argv.index('origin')] = str(self.repo)
                argv[1:1] = ['-c', 'protocol.file.allow=always']
            return original(argv, cwd, env, output, timeout)

        with patch.object(app.flow, 'run_command', side_effect=transport):
            command = app.command_runner(self.folder, {'GH_TOKEN': 'must-not-leak',
                'GIT_CONFIG_COUNT': '1', 'GIT_ASKPASS': '/bad', 'HOME': '/wrong'}, [])
            bare = self.folder / 'source.git'
            app.fetch_source(self.config, bare, command)
            handoff = app.validate_handoff(self.config, bare, command)
        self.assertEqual(handoff['HANDOFF'], 'PASS')
        self.assertFalse((bare / 'transaction-api').exists())
        self.assertTrue((bare / 'objects').is_dir())
        for argv, env in calls:
            self.assertNotIn('GH_TOKEN', env)
            self.assertNotIn('GIT_CONFIG_COUNT', env)
            self.assertNotIn('GIT_ASKPASS', env)
            self.assertIn('http.sslVerify=true', argv)
            self.assertIn('http.followRedirects=false', argv)

    def test_existing_source_target_is_preserved(self):
        target = self.folder / 'source.git'
        target.mkdir()
        (target / 'keep').write_text('keep')
        with patch.object(app, 'command_runner') as command:
            with self.assertRaisesRegex(app.flow.PreflightError, 'SOURCE_TARGET_ALREADY_EXISTS'):
                app.fetch_source(self.config, target, command)
            command.assert_not_called()
        self.assertEqual((target / 'keep').read_text(), 'keep')

    def test_private_missing_or_invalid_token_stops_before_git_and_build(self):
        self.config['source']['repository'] = app.auth.PRIVATE_REPOSITORY
        for i, token in enumerate(('', 'short', 'fixture_token_0123456789\n', 'x' * 513)):
            folder = self.root / ('token-' + str(i))
            folder.mkdir()
            incoming = {app.auth.TOKEN_ENV: token, 'GITHUB_TOKEN': 'no-fallback'}
            with self.subTest(i=i), patch.object(app, 'command_runner') as git, \
                    patch.object(app.flow, 'execute') as build:
                rc, result = app.run(self.config, folder, {}, incoming, execute=True)
            self.assertEqual(rc, 1)
            self.assertIn(result['error_code'], ('SOURCE_READ_TOKEN_REQUIRED', 'SOURCE_READ_TOKEN_INVALID'))
            self.assertNotIn(app.auth.TOKEN_ENV, incoming)
            git.assert_not_called()
            build.assert_not_called()

    def test_unapproved_repository_rejected_before_token_use(self):
        for i, repository in enumerate(('other/fds-msa', '321Team/fds-msa.git',
                                        '321Team/fds-msa@evil.invalid', ['321Team/fds-msa'])):
            self.config['source']['repository'] = repository
            folder = self.root / ('repository-' + str(i))
            folder.mkdir()
            with patch.object(app, 'command_runner') as git:
                rc, result = app.run(self.config, folder, {}, {}, execute=True)
            self.assertEqual((rc, result['error_code']), (1, 'SOURCE_REPOSITORY_INVALID'))
            git.assert_not_called()

    def test_private_fetch_secret_only_in_transport_env_and_never_persisted(self):
        self.config['source']['repository'] = app.auth.PRIVATE_REPOSITORY
        token = 'fixture_private_token_0123456789'
        encoded = base64.b64encode(('x-access-token:' + token).encode()).decode()
        incoming = {app.auth.TOKEN_ENV: token, 'GITHUB_TOKEN': 'fixture-unrelated-secret',
                    'GIT_TRACE_CURL': '1', 'GIT_CONFIG_PARAMETERS': 'unsafe',
                    'GIT_ASKPASS': '/bad', 'GIT_CONFIG_COUNT': '1'}
        calls = []
        original = app.flow.run_command

        def transport(argv, cwd, env, output, timeout):
            calls.append((argv[:], env.copy(), output))
            if 'fetch' in argv:
                self.assertEqual(argv[-2:], [app.auth.PRIVATE_ORIGIN, self.config['source']['commit']])
                self.assertIsNone(output)
                self.assertEqual(env[app.auth.HEADER_ENV], 'AUTHORIZATION: basic ' + encoded)
                # Only fixture transport redirects to local Git objects, preserving exact SHA.
                argv = [arg for arg in argv if not arg.startswith('--config-env=')]
                argv[-2] = str(self.repo)
                argv[1:1] = ['-c', 'protocol.file.allow=always']
            else:
                self.assertNotIn(app.auth.HEADER_ENV, env)
            self.assertNotIn(token, ' '.join(argv))
            self.assertNotIn(encoded, ' '.join(argv))
            for key in incoming:
                self.assertNotIn(key, env)
            return original(argv, cwd, env, output, timeout)

        def build(config, source, folder, context, env):
            self.assertNotIn(app.auth.TOKEN_ENV, incoming)
            self.assertFalse(any(token in value or encoded in value for value in env.values()))
            self.assertNotIn('GITHUB_TOKEN', env)
            result = {'BUILD_SCAN': 'PASS'}
            (folder / 'result.json').write_text(json.dumps(result))
            return 0, result

        with patch.object(app.flow, 'run_command', side_effect=transport), \
                patch.object(app.flow, 'execute', side_effect=build) as build_mock:
            rc, result = app.run(self.config, self.folder, self.context, incoming, execute=True)
        self.assertEqual((rc, result['APP_WORKFLOW']), (0, 'PASS'))
        build_mock.assert_called_once()
        self.assertEqual(sum('fetch' in argv for argv, _, _ in calls), 1)
        for path in self.folder.rglob('*'):
            if path.is_file():
                self.assertNotIn(token.encode(), path.read_bytes(), str(path))
                self.assertNotIn(encoded.encode(), path.read_bytes(), str(path))
        self.assertIn('RAW_OUTPUT=DISCARDED', (self.folder / 'source-fetch.log').read_text())
        self.assertNotIn('extraheader', (self.folder / 'app.git/config').read_text().lower())

    def test_private_transport_failure_or_timeout_never_retries_or_builds(self):
        self.config['source']['repository'] = app.auth.PRIVATE_REPOSITORY
        original = app.flow.run_command
        for fault in ('denied', 'timeout'):
            folder = self.root / fault
            folder.mkdir()
            fetched = []

            def transport(argv, cwd, env, output, timeout):
                if 'fetch' in argv:
                    fetched.append(argv)
                    self.assertIsNone(output)
                    if fault == 'timeout':
                        raise subprocess.TimeoutExpired(argv, timeout)
                    return 128  # Git authentication, redirect or transport refusal.
                return original(argv, cwd, env, output, timeout)

            with patch.object(app.flow, 'run_command', side_effect=transport), \
                    patch.object(app.flow, 'execute') as build:
                rc, result = app.run(self.config, folder, {},
                    {app.auth.TOKEN_ENV: 'fixture_private_token_0123456789'}, execute=True)
            self.assertEqual(rc, 1)
            self.assertEqual(len(fetched), 1)
            self.assertEqual(result['error_code'], 'SOURCE_PRIVATE_FETCH_FAILED'
                             if fault == 'denied' else 'SOURCE_COMMAND_TIMEOUT')
            self.assertEqual(result['APP_TEST_BUILD_SCAN'], 'NOT_EXECUTED')
            build.assert_not_called()

    def test_private_local_handoff_needs_exact_origin_without_any_token(self):
        self.config['source']['repository'] = app.auth.PRIVATE_REPOSITORY
        self.git('remote', 'set-url', 'origin', app.auth.PRIVATE_ORIGIN)
        self.assertEqual(self.check()[0], 0)

    def test_cli_removes_source_token_before_input_validation(self):
        with patch.dict(os.environ, {app.auth.TOKEN_ENV: 'fixture_private_token_0123456789'}):
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(app.main(['--execute']), 1)
            self.assertNotIn(app.auth.TOKEN_ENV, os.environ)

    def test_fetch_failure_never_calls_build(self):
        with patch.object(app, 'fetch_source', side_effect=app.flow.PreflightError('SOURCE_COMMAND_FAILED:source-fetch')), \
                patch.object(app.flow, 'execute') as execute:
            rc, result = app.run(self.config, self.folder, self.context, {}, execute=True)
        execute.assert_not_called()
        self.assertEqual(rc, 1)
        self.assertEqual(result['APP_TEST_BUILD_SCAN'], 'NOT_EXECUTED')

    def test_handoff_failure_never_calls_build(self):
        with patch.object(app, 'fetch_source'), \
                patch.object(app, 'validate_handoff', side_effect=app.flow.PreflightError('SOURCE_LOCK_MISMATCH')), \
                patch.object(app.flow, 'execute') as execute:
            rc, result = app.run(self.config, self.folder, self.context, {}, execute=True)
        execute.assert_not_called()
        self.assertEqual((rc, result['error_code']), (1, 'SOURCE_LOCK_MISMATCH'))

    def test_build_pass_block_and_failure_are_propagated(self):
        for code, status in ((0, 'PASS'), (10, 'BLOCK'), (1, 'FAIL')):
            def build(config, source, folder, context, incoming):
                self.assertIn('handoff_sha256', context)
                result = {'BUILD_SCAN': status}
                (folder / 'result.json').write_text(json.dumps(result))
                return code, result
            folder = self.root / status
            folder.mkdir()
            with self.subTest(status=status), patch.object(app, 'fetch_source'), \
                    patch.object(app, 'validate_handoff', return_value={'HANDOFF': 'PASS'}), \
                    patch.object(app.flow, 'execute', side_effect=build):
                rc, result = app.run(self.config, folder, self.context, {}, execute=True)
            self.assertEqual((rc, result['APP_WORKFLOW']), (code, status))
            self.assertEqual(result['PUSH'], 'NOT_EXECUTED')
            self.assertEqual(result['DEPLOY'], 'NOT_EXECUTED')
            self.assertEqual(result['DELIVERY_GATE'], 'NOT_VERIFIED')

    def test_workflow_and_committed_config_binding(self):
        env = {'GITHUB_WORKFLOW_REF': '321Team/infra-cicd/' + app.WORKFLOW + '@refs/heads/main',
               'GITHUB_WORKFLOW_SHA': self.context['workflow_sha']}
        with patch.object(app.flow, 'runtime_context', return_value=self.context), \
                patch.object(app.os, 'chdir'), patch.object(app.subprocess, 'check_output', return_value=b'committed'):
            self.assertEqual(app.bind_context(b'committed', env)['input_sha256'],
                             hashlib.sha256(b'committed').hexdigest())
            with self.assertRaisesRegex(app.flow.PreflightError, 'INPUT_NOT_FROM_WORKFLOW_COMMIT'):
                app.bind_context(b'changed', env)
            for field, code in [('GITHUB_WORKFLOW_REF', 'CENTRAL_WORKFLOW_REQUIRED'),
                                ('GITHUB_WORKFLOW_SHA', 'WORKFLOW_SHA_MISMATCH')]:
                with self.subTest(field=field), self.assertRaisesRegex(app.flow.PreflightError, code):
                    app.bind_context(b'committed', {**env, field: 'wrong'})

    def test_cli_pending_and_execution_override_block_without_fetch(self):
        for args in (['--execute'], ['--execute', '--config', '/any'],
                     ['--execute', '--source-dir', '/any']):
            with self.subTest(args=args), patch.object(app, 'run') as run, contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(app.main(args), 1)
                run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
