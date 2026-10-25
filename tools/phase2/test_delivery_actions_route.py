"""Offline cross-layer native Actions route regression; no live credential."""
import unittest
from unittest.mock import patch
from delivery_github_source import source_path_checked,SourceError,PublicGitHubTLS
from delivery_fds_run_20261025 import GITHUB_CA_PEM,GITHUB_ENDPOINT_IP

class NativeActionsRouteTests(unittest.TestCase):
    def test_only_existing_native_run_attempt_and_first_jobs_page_are_allowed(self):
        root='/repos/example/infra/actions/runs/123'
        for path in (root,root+'/attempts/1',root+'/attempts/1/jobs?per_page=100&page=1'):
            source_path_checked(path)
    def test_other_actions_and_query_variants_are_rejected(self):
        root='/repos/example/infra/actions/runs/123'
        for suffix in ('/jobs','/logs','/artifacts','/cancel','/rerun','/attempts/0','/attempts/01',
            '/attempts/1/logs','/attempts/1/jobs?per_page=100&page=2',
            '/attempts/1/jobs?per_page=200&page=1','/attempts/1/jobs?page=1&per_page=100',
            '/attempts/1/jobs?per_page=100&page=1&extra=x'):
            with self.subTest(suffix=suffix),self.assertRaises(SourceError):source_path_checked(root+suffix)
        for path in ('https://api.github.com'+root,root.replace('/123','/0'),root.replace('/123','/0123'),'/repos/example/../actions/runs/1','/repos/example/infra/actions/workflows/x/dispatches'):
            with self.subTest(path=path),self.assertRaises(SourceError):source_path_checked(path)
    def test_original_routes_remain_allowed(self):
        base='/repos/example/infra'
        for path in (base,base+'/issues/comments/42',base+'/git/ref/heads/review/one',
             base+'/git/commits/'+'a'*40,base+'/compare/'+'a'*40+'...'+'b'*40+'?per_page=1'):
            source_path_checked(path)
    def test_get_reaches_bounded_socket_for_native_route_but_rejects_logs_before_socket(self):
        transport=PublicGitHubTLS(ca_pem=GITHUB_CA_PEM,endpoint_ip=GITHUB_ENDPOINT_IP,monotonic=lambda:1.0)
        with patch('socket.socket',side_effect=AssertionError('NO_NETWORK')) as sock:
            with self.assertRaises(SourceError):transport.get('/repos/example/infra/actions/runs/123/attempts/1/jobs?per_page=100&page=1',10.0)
            self.assertEqual(1,sock.call_count)
            sock.reset_mock()
            with self.assertRaises(SourceError):transport.get('/repos/example/infra/actions/runs/123/logs',10.0)
            sock.assert_not_called()
