"""Mock Podman lifecycle tests; no claim of actual image startup."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

import image_smoke as smoke


class ImageSmokeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.cid = 'c' * 64
        self.identity = {'archive_sha256': 'a' * 64, 'config_digest': 'sha256:' + 'b' * 64,
                         'manifest_digest': 'sha256:' + 'd' * 64}
        self.result, self.events = {}, {}
        self.fault, self.mutate = None, lambda data: None

    def probe(self):
        return {'schema': 'P1-final-image-probe-1', 'service': 'transaction-api',
                'uid': 10001, 'gid': 999, 'readable_files': 4, 'rootfs': 'READ_ONLY',
                'checks': {'livez': 200, 'business_metrics': 404,
                           'dedicated_metrics': 200, 'metrics_livez': 404}}

    def command(self, label, argv, timeout):
        suffix = label.removeprefix('transaction-api-image-smoke-')
        self.events[suffix] = (argv, timeout)
        output = self.root / (label + '.log')
        output.write_text('fixture\n')
        if suffix == 'create':
            self.owner = argv[argv.index('--name') + 1]
            Path(argv[argv.index('--cidfile') + 1]).write_text(self.cid)
        if suffix == self.fault:
            raise subprocess.TimeoutExpired(argv, timeout)
        if suffix in ('created', 'running', 'after'):
            data = {'id': self.cid, 'name': self.owner, 'owner': self.owner,
                    'image': self.identity['config_digest'], 'user': '10001:999',
                    'workdir': '/app', 'path': '/venv/bin/python3', 'args': ['run.py'],
                    'running': suffix != 'created', 'readonly': True,
                    'network': 'none', 'mounts': []}
            self.mutate(data)
            output.write_text(json.dumps(data))
        if suffix == 'probe':
            output.write_text(json.dumps(self.probe()))
        if suffix == 'removed':
            output.write_text('')
        return output

    def run_probe(self):
        smoke.check_image(self.command, ['/usr/bin/podman', '--root', '/private/storage'],
                          'transaction-api', self.identity, self.root, self.result)

    def test_bound_image_default_entrypoint_isolated_and_removed(self):
        self.run_probe()
        self.assertEqual(self.result['status'], 'PASS')
        self.assertEqual(self.result['cleanup'], 'REMOVED')
        self.assertEqual(self.result['identity'], self.identity)
        self.assertEqual(self.result['APP_E2E'], 'NOT_VERIFIED')
        argv, _ = self.events['create']
        for option in ('--pull=never', '--network=none', '--read-only=true',
                       '--read-only-tmpfs=false', '--user=10001:999',
                       '--cap-drop=ALL', '--security-opt=no-new-privileges'):
            self.assertIn(option, argv)
        self.assertNotIn('--entrypoint', argv)
        self.assertNotIn('--privileged', argv)
        self.assertFalse(any('label=disable' in a or a.startswith('--publish') for a in argv))
        self.assertEqual(argv[-1], self.identity['config_digest'])
        self.assertEqual(self.events['probe'][0][-1], 'transaction-api')
        self.assertEqual(self.events['remove'][0][-1], self.cid)
        self.assertEqual(set(self.events), {'create', 'created', 'start', 'running',
                                          'probe', 'after', 'logs', 'remove', 'removed'})

    def test_create_timeout_after_cid_allocation_still_removes_own_container(self):
        self.fault = 'create'
        with self.assertRaises(subprocess.TimeoutExpired):
            self.run_probe()
        self.assertEqual(self.result['status'], 'FAIL')
        self.assertEqual(self.result['cleanup'], 'REMOVED')
        self.assertNotIn('start', self.events)

    def test_start_and_probe_timeouts_cannot_leave_pass_or_skip_cleanup(self):
        for fault in ('start', 'probe'):
            with self.subTest(fault=fault), tempfile.TemporaryDirectory() as temporary:
                self.root = Path(temporary)
                self.result, self.events, self.fault = {}, {}, fault
                with self.assertRaises(subprocess.TimeoutExpired):
                    self.run_probe()
                self.assertEqual(self.result['status'], 'FAIL')
                self.assertEqual(self.result['cleanup'], 'REMOVED')

    def test_log_and_cleanup_failures_block_success(self):
        for fault in ('logs', 'remove', 'removed'):
            with self.subTest(fault=fault), tempfile.TemporaryDirectory() as temporary:
                self.root = Path(temporary)
                self.result, self.events, self.fault = {}, {}, fault
                with self.assertRaisesRegex(smoke.SmokeError, 'SMOKE_LOG_OR_CLEANUP_FAILED'):
                    self.run_probe()
                self.assertEqual(self.result['status'], 'FAIL')
                self.assertTrue(self.result['cleanup_errors'])
                self.assertIn('remove', self.events)

    def test_wrong_image_or_runtime_profile_stops_before_start(self):
        for field, value in (('image', 'a' * 64), ('user', '0'), ('args', ['other.py']),
                             ('readonly', False), ('network', 'host'),
                             ('mounts', [{'Destination': '/tmp', 'RW': True}])):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as temporary:
                self.root = Path(temporary)
                self.result, self.events = {}, {}
                self.mutate = lambda data: data.update({field: value})
                with self.assertRaises(smoke.SmokeError):
                    self.run_probe()
                self.assertNotIn('start', self.events)
                self.assertEqual(self.result['cleanup'], 'REMOVED')

    def test_exited_after_probe_is_not_pass(self):
        self.mutate = lambda data: data.update(running=False) if 'after' in self.events else None
        with self.assertRaisesRegex(smoke.SmokeError, 'SMOKE_ISOLATION_OR_STATE'):
            self.run_probe()
        self.assertEqual(self.result['status'], 'FAIL')
        self.assertEqual(self.result['cleanup'], 'REMOVED')

    def test_probe_does_not_accept_wrong_identity_or_listener_result(self):
        for field, value in (('uid', 0), ('gid', 0), ('rootfs', 'WRITABLE'),
                             ('service', 'fds-engine'), ('readable_files', 0)):
            data = self.probe()
            data[field] = value
            with self.subTest(field=field), self.assertRaises(smoke.SmokeError):
                smoke.check_probe(json.dumps(data), 'transaction-api')
        data = self.probe()
        data['checks']['business_metrics'] = 200
        with self.assertRaises(smoke.SmokeError):
            smoke.check_probe(json.dumps(data), 'transaction-api')

    def test_missing_or_invalid_cid_never_deletes_by_name(self):
        original = self.command

        def no_cid(label, argv, timeout):
            result = original(label, argv, timeout)
            if label.endswith('-create'):
                Path(argv[argv.index('--cidfile') + 1]).write_text('invalid')
            return result

        self.command = no_cid
        with self.assertRaises(smoke.SmokeError):
            self.run_probe()
        self.assertNotIn('remove', self.events)
        self.assertEqual(self.result['cleanup'], 'NOT_CONFIRMED')

    def test_existing_probe_directory_is_not_reused(self):
        (self.root / 'transaction-api-image-smoke').mkdir()
        with self.assertRaises(FileExistsError):
            self.run_probe()
        self.assertEqual(self.events, {})


if __name__ == '__main__':
    unittest.main()
