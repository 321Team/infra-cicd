"""Probe behavior with real temporary files and synthetic HTTP responses."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import image_smoke_probe as probe


class ProbeTests(unittest.TestCase):
    def test_reads_nested_app_files_and_requires_run_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'app/deep').mkdir(parents=True)
            (root / 'run.py').write_text('fixture')
            (root / 'app/deep/module.py').write_text('fixture')
            (root / 'app/.hidden').write_text('fixture')
            self.assertEqual(probe.readable_tree(root), 3)
            (root / 'run.py').unlink()
            with self.assertRaisesRegex(ValueError, 'APP_SOURCE_MISSING'):
                probe.readable_tree(root)

    def test_unreadable_source_fails_instead_of_mode_only_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'app').mkdir()
            (root / 'run.py').write_text('fixture')
            with patch.object(Path, 'open', side_effect=PermissionError('fixture')):
                with self.assertRaises(PermissionError):
                    probe.readable_tree(root)

    def response(self, _opener, port, path, _timeout):
        if port == 8000:
            return (200, 'application/json', b'{"status":"ok"}') if path == '/livez' else (404, '', b'')
        return ((200, 'text/plain; version=0.0.4', b'transaction_save_success_total 0\n')
                if path == '/metrics' else (404, '', b''))

    def test_listener_probe_checks_both_positive_and_negative_routes(self):
        with patch.object(probe, 'request', side_effect=self.response) as request:
            result = probe.listeners('transaction-api', duration=1)
        self.assertEqual(result, {'livez': 200, 'business_metrics': 404,
                                  'dedicated_metrics': 200, 'metrics_livez': 404})
        self.assertEqual([(c.args[1], c.args[2]) for c in request.call_args_list],
                         [(8000, '/livez'), (8000, '/metrics'), (9091, '/metrics'), (9091, '/livez')])

    def test_exposed_business_metrics_or_missing_app_counter_fail(self):
        for failure in ('exposed', 'missing-counter', 'redirect', 'wrong-health'):
            def response(opener, port, path, timeout):
                if failure == 'exposed' and (port, path) == (8000, '/metrics'):
                    return 200, 'text/plain', b'fixture'
                if failure == 'missing-counter' and (port, path) == (9091, '/metrics'):
                    return 200, 'text/plain', b'python_info 1'
                if failure == 'redirect' and path == '/metrics':
                    return 307, '', b''
                if failure == 'wrong-health' and (port, path) == (8000, '/livez'):
                    return 200, 'application/json', b'{"status":"not ready"}'
                return self.response(opener, port, path, timeout)
            with self.subTest(failure=failure), patch.object(probe, 'request', side_effect=response):
                with self.assertRaisesRegex(ValueError, 'IMAGE_STARTUP_OR_LISTENER_CONTRACT_FAILED'):
                    probe.listeners('transaction-api', duration=0.01)

    def test_unreachable_listener_is_bounded_failure(self):
        with patch.object(probe, 'request', side_effect=ConnectionRefusedError('fixture')):
            with self.assertRaises(ValueError):
                probe.listeners('fds-engine', duration=0.01)

    def test_redirect_handler_cannot_follow_an_external_location(self):
        self.assertIsNone(probe.NoRedirect().redirect_request(None, None, 302, '', {}, 'https://example.invalid'))


if __name__ == '__main__':
    unittest.main()
