import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch, Mock

spec = importlib.util.spec_from_file_location('prep', Path(__file__).with_name('collect_parallel_prep.py'))
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)


class PreparationSafetyTests(unittest.TestCase):
    def test_wrong_identity_stops_before_collection_and_writes(self):
        with patch.object(p, 'identity', return_value={'status': 'MISMATCH'}), patch.object(p, 'runner_inventory') as inventory, patch.object(p, 'save_report') as save:
            self.assertEqual(p.main(['--role', 'runner', '--output-dir', '/unused']), 3)
            inventory.assert_not_called()
            save.assert_not_called()

    def test_identity_requires_host_ip_and_account(self):
        rows = {'data': [{'addr_info': [{'family': 'inet', 'local': '10.250.10.20'}]}]}
        with patch.object(p, 'query', return_value=rows), patch.object(p.socket, 'gethostname', return_value='cicd-runner01'), patch.object(p.pwd, 'getpwuid', return_value=Mock(pw_name=p.RUNNER_USER)):
            self.assertEqual(p.identity('runner')['status'], 'MATCHED')
            with patch.object(p.socket, 'gethostname', return_value='harbor01'):
                self.assertEqual(p.identity('runner')['status'], 'MISMATCH')

    def test_yaml_projection_never_returns_passwords_or_other_sections(self):
        source = '''hostname: 10.1.93.54
https:
  port: 443
  certificate: /data/cert/harbor.crt
  private_key: /data/cert/harbor.key
harbor_admin_password: SENTINEL_ADMIN
database:
  password: SENTINEL_DB
  private_key: SENTINEL_UNRELATED
'''
        record = p.project_harbor_yaml(source)
        self.assertEqual(record['fields']['https.private_key'], '/data/cert/harbor.key')
        self.assertNotIn('SENTINEL', json.dumps(record))
        self.assertFalse(record['configuration_validated'])

    def test_complex_or_duplicate_yaml_requires_review(self):
        record = p.project_harbor_yaml('https:\n  certificate: &cert /tmp/a.crt\n  port: 443\n  port: 8443\n')
        self.assertEqual(record['projection_status'], 'REVIEW_REQUIRED')
        self.assertNotIn('https.certificate', record['fields'])

    def test_nginx_projection_omits_headers_and_key_contents(self):
        record = p.project_nginx('ssl_certificate /etc/cert/a.crt;\nssl_certificate_key /etc/cert/a.key;\nproxy_set_header Authorization "Basic SENTINEL";\n')
        self.assertEqual(len(record['directives']), 2)
        self.assertNotIn('SENTINEL', json.dumps(record))

    def test_failed_commands_do_not_archive_raw_stderr(self):
        completed = Mock(returncode=1, stdout='', stderr='SECRET_RAW_STDERR')
        with patch.object(p.subprocess, 'run', return_value=completed) as run:
            with patch.dict(os.environ, {'GH_TOKEN': 'SECRET_ENV'}, clear=False):
                result = p.query(['docker', 'version'])
        self.assertEqual(result['status'], 'UNAVAILABLE')
        self.assertNotIn('SECRET', json.dumps(result))
        self.assertNotIn('GH_TOKEN', run.call_args.kwargs['env'])
        self.assertNotIn('shell', run.call_args.kwargs)

    def test_timeout_has_no_partial_output_or_exception_message(self):
        err = subprocess.TimeoutExpired(['x'], 12, output='SENTINEL_SECRET')
        with patch.object(p.subprocess, 'run', side_effect=err):
            self.assertNotIn('SENTINEL', json.dumps(p.query(['x'])))

    def test_github_refuses_private_resolution(self):
        with patch.object(p, 'run', return_value={'rc': 0, 'text': '10.1.93.54 STREAM github.com\n'}), patch.object(p.socket, 'create_connection') as connect:
            self.assertEqual(p.github_probe('github.com')['status'], 'FAILED')
            connect.assert_not_called()

    def test_github_tls_verification_failure_is_not_pass(self):
        connection = Mock()
        connection.__enter__ = Mock(return_value=connection)
        connection.__exit__ = Mock(return_value=False)
        ctx = Mock()
        ctx.wrap_socket.side_effect = p.ssl.SSLCertVerificationError(1, 'certificate verify failed')
        with patch.object(p, 'run', return_value={'rc': 0, 'text': '140.82.114.4 STREAM github.com\n'}), patch.object(p.socket, 'create_connection', return_value=connection), patch.object(p.ssl, 'create_default_context', return_value=ctx):
            self.assertEqual(p.github_probe('github.com')['status'], 'FAILED')

    def test_runner_scoped_inventory_does_not_read_credentials(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / '.credentials').write_text('SENTINEL_CREDENTIAL')
            with patch.object(p, 'text_query', return_value={'status': 'UNAVAILABLE'}), patch.object(p, 'resolver_servers', return_value=['10.1.93.52']), patch.object(p, 'github_probe') as probe, patch.object(p.Path, 'open', side_effect=AssertionError('Do not open install files')):
                result = p.runner_inventory(tmp, True)
                self.assertEqual(result['github_probes'][0]['status'], 'SKIPPED_DNS_SCOPE_REVIEW')
                probe.assert_not_called()
                self.assertNotIn('SENTINEL', json.dumps(result))

    def test_harbor_docker_query_is_projected_and_local(self):
        calls = []
        def fake(argv):
            calls.append(argv)
            return {'status': 'COLLECTED', 'data': {'compose_working_dir': '/home/server08/harbor', 'compose_config_files': '/home/server08/harbor/docker-compose.yml', 'mounts': []}}
        with patch.object(p, 'query', side_effect=fake), patch.object(p, 'config_record', return_value={'status': 'NOT_FOUND'}), patch.object(p, 'metadata', return_value={'status': 'NOT_FOUND'}):
            result = p.harbor_inventory([])
        self.assertEqual(len(calls), 9)
        self.assertTrue(all(a[:3] == ['docker', '--host', 'unix:///var/run/docker.sock'] and '--format' in a for a in calls))
        self.assertNotIn('.Config.Env', p.FORMAT)
        self.assertNotIn('.State.Error', p.FORMAT)
        self.assertEqual(result['approved_project_ca_exists'], 'UNKNOWN')

    def test_archive_contains_only_sanitized_report_and_checksums(self):
        report = {'role': 'runner', 'secret': 'token=SENTINEL_SECRET', 'delivery_gate': 'NOT_VERIFIED'}
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {}, clear=True):
            doc, archive, side = p.save_report(report, tmp)
            self.assertNotIn('SENTINEL_SECRET', doc.read_text())
            self.assertEqual(side.read_text().split()[0], hashlib.sha256(archive.read_bytes()).hexdigest())
            with tarfile.open(archive) as bundle:
                self.assertEqual(bundle.getnames(), ['preparation.json', 'SHA256SUMS'])
                digest = bundle.extractfile('SHA256SUMS').read().decode().split()[0]
                self.assertEqual(digest, hashlib.sha256(bundle.extractfile('preparation.json').read()).hexdigest())
                self.assertTrue(all(x.mode == 0o600 for x in bundle.getmembers()))
            self.assertEqual(doc.stat().st_mode & 0o777, 0o600)
            self.assertEqual(archive.parent.stat().st_mode & 0o777, 0o700)

    def test_scrubber_covers_private_key_and_auth_patterns(self):
        inputs = ['-----BEGIN PRIVATE KEY-----\nSENTINEL', 'Bearer SENTINEL', 'https://user:SENTINEL@example.com', 'github_pat_SENTINEL']
        for source in inputs:
            self.assertNotIn('SENTINEL', p.scrub(source))

    def test_non_regular_config_is_rejected_without_blocking(self):
        with tempfile.TemporaryDirectory() as tmp:
            fifo = Path(tmp) / 'harbor.yml'
            os.mkfifo(fifo)
            with self.assertRaises(ValueError):
                p.read_config(fifo)

    def test_referenced_private_key_is_never_opened(self):
        with tempfile.TemporaryDirectory() as tmp:
            key = Path(tmp) / 'private.key'
            key.write_text('-----BEGIN PRIVATE KEY-----\nSENTINEL_KEY\n')
            config = Path(tmp) / 'harbor.yml'
            config.write_text('hostname: 10.1.93.54\nhttps:\n  certificate: /data/cert/harbor.crt\n  private_key: ' + str(key) + '\n')
            with patch.object(p, 'query', return_value={'status': 'UNAVAILABLE'}), patch.object(p, 'read_config', wraps=p.read_config) as read:
                result = p.harbor_inventory([str(config)])
                self.assertNotIn(str(key), [str(c.args[0]) for c in read.call_args_list])
                self.assertNotIn('SENTINEL_KEY', json.dumps(result))


if __name__ == '__main__':
    unittest.main()
