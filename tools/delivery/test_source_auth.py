"""Real subprocess output isolation and Git URL-scoped header selection; no network/token."""
import base64
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import app_workflow as app
import source_auth as auth


class SourceAuthTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.token = 'fixture_private_token_0123456789'
        self.env = app.git_environment(self.root, {})

    def test_real_child_echoing_credentials_cannot_write_fetch_logs(self):
        original = auth.flow.run_command
        observed = []

        def echo(argv, cwd, env, output, timeout):
            observed.append((argv[:], env.copy()))
            child = [sys.executable, '-c', 'import os,sys; value=os.environ["CICD_SOURCE_AUTH_HEADER"]; '
                     'print(value); print(value, file=sys.stderr); sys.exit(128)']
            return original(child, cwd, env, output, timeout)

        steps = []
        with patch.object(auth.flow, 'run_command', side_effect=echo):
            with self.assertRaisesRegex(auth.flow.PreflightError, 'SOURCE_PRIVATE_FETCH_FAILED'):
                auth.fetch(self.root / 'source.git', 'a' * 40, self.token, self.env, self.root, steps)
        encoded = base64.b64encode(('x-access-token:' + self.token).encode())
        self.assertEqual(steps[0]['exit_code'], 128)
        self.assertNotIn(auth.HEADER_ENV, self.env)
        for path in self.root.rglob('*'):
            if path.is_file():
                self.assertNotIn(self.token.encode(), path.read_bytes())
                self.assertNotIn(encoded, path.read_bytes())
        self.assertNotIn(self.token, ' '.join(observed[0][0]))

    def test_real_git_header_matches_only_exact_repository_url_prefix(self):
        captured = []

        def inspect(argv, cwd, env, output, timeout):
            captured.append(env)
            options = argv[:argv.index('-C')]
            self.assertIn('http.sslVerify=true', options)
            self.assertIn('http.followRedirects=false', options)
            self.assertIn('credential.helper=', options)
            for url in (auth.PRIVATE_ORIGIN, auth.PRIVATE_ORIGIN + '/info/refs'):
                result = subprocess.run(options + ['config', '--get-urlmatch', 'http.extraheader', url],
                    env=env, cwd=cwd, capture_output=True, check=True)
                self.assertEqual(result.stdout.decode().strip(), env[auth.HEADER_ENV])
            for url in ('https://github.com/other/fds-msa.git',
                        'https://github.com/321Team/fds-msa.git-evil',
                        'https://evil.invalid/321Team/fds-msa.git',
                        'http://github.com/321Team/fds-msa.git'):
                result = subprocess.run(options + ['config', '--get-urlmatch', 'http.extraheader', url],
                    env=env, cwd=cwd, capture_output=True)
                self.assertNotIn(b'AUTHORIZATION', result.stdout)
            return 0

        with patch.object(auth.flow, 'run_command', side_effect=inspect):
            auth.fetch(self.root / 'source.git', 'a' * 40, self.token, self.env, self.root, [])
        self.assertEqual(captured, [{}])  # Child environment cleared even on success.

    def test_quiet_process_timeout_kills_group_and_clears_auth_environment(self):
        original = auth.flow.run_command
        captured = []

        def timeout(argv, cwd, env, output, limit):
            captured.append(env)
            return original([sys.executable, '-c', 'import time; time.sleep(20)'],
                            cwd, env, output, 0.05)

        with patch.object(auth.flow, 'run_command', side_effect=timeout):
            with self.assertRaises(subprocess.TimeoutExpired):
                auth.fetch(self.root / 'source.git', 'a' * 40, self.token, self.env, self.root, [])
        self.assertEqual(captured, [{}])
        self.assertIn('EXIT=-1', (self.root / 'source-fetch.log').read_text())


if __name__ == '__main__':
    unittest.main()
