import io
import json
import os
from pathlib import Path
import ssl
import stat
import tarfile
import tempfile
import time
import unittest
from unittest.mock import patch

import collect_base_metadata as c


class Response:
    def __init__(self, raw=b'{}', status=200, headers=None):
        self.raw = io.BytesIO(raw)
        self.status = status
        self.headers = headers or {}

    def getheader(self, key, default=None):
        return self.headers.get(key, default)

    def read(self, count):
        return self.raw.read(count)


class Connection:
    responses = []
    calls = []

    def __init__(self, host, **kwargs):
        self.host = host
        assert kwargs['context'].verify_mode == ssl.CERT_REQUIRED
        assert kwargs['context'].check_hostname
        assert kwargs['context'].minimum_version >= ssl.TLSVersion.TLSv1_2

    def request(self, method, path, headers):
        self.calls.append((self.host, method, path, headers))

    def getresponse(self):
        return self.responses.pop(0)

    def close(self):
        pass


class CollectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        self.fd = os.open(self.home, os.O_RDONLY | os.O_DIRECTORY)
        self.now = int(time.time())
        Connection.calls, Connection.responses = [], []
        self.network = patch.object(c.http.client, 'HTTPSConnection', Connection)
        self.network.start()

    def tearDown(self):
        self.network.stop()
        os.close(self.fd)
        self.temp.cleanup()

    def client(self):
        return c.Client({'started_epoch': self.now - 1, 'expires_epoch': self.now + 3599})

    def fixture(self, index=True):
        config = c.encode({'os': 'linux', 'architecture': 'amd64',
                           'config': {'Env': ['MOCK_PRIVATE_VALUE=not-for-stdout']}})
        manifest = c.encode({'schemaVersion': 2,
                             'mediaType': 'application/vnd.oci.image.manifest.v1+json',
                             'config': {'mediaType': 'application/vnd.oci.image.config.v1+json',
                                        'digest': c.digest(config), 'size': len(config)},
                             'layers': [{'digest': 'sha256:' + 'a' * 64}]})
        entry = {'mediaType': 'application/vnd.oci.image.manifest.v1+json',
                 'digest': c.digest(manifest), 'size': len(manifest),
                 'platform': {'os': 'linux', 'architecture': 'amd64'}}
        root = c.encode({'schemaVersion': 2,
                         'mediaType': 'application/vnd.oci.image.index.v1+json',
                         'manifests': [entry]}) if index else manifest
        self.raws = (root, manifest, config)
        Connection.responses = [Response(c.encode({'token': 'SECRET_BEARER'})),
                                Response(root, headers={'Docker-Content-Digest': c.digest(root)})]
        if index:
            Connection.responses.append(Response(manifest, headers={'Docker-Content-Digest': c.digest(manifest)}))
        Connection.responses.append(Response(config))
        return entry

    def test_index_and_direct_collection_only_download_referenced_config(self):
        for indexed in (True, False):
            with self.subTest(index=indexed):
                self.fixture(indexed)
                Connection.calls = []
                client, files = self.client(), {}
                result = c.collect(client, files)
                self.assertEqual(result['root_digest'], c.digest(self.raws[0]))
                self.assertEqual(set(files), {'manifest.json', 'config.json'} | ({'index.json'} if indexed else set()))
                self.assertEqual(len(Connection.calls), 4 if indexed else 3)
                self.assertEqual(sum('/blobs/' in call[2] for call in Connection.calls), 1)
                self.assertNotIn('Authorization', Connection.calls[0][3])
                self.assertNotIn('SECRET_BEARER', json.dumps(client.receipts))
                self.assertNotIn('MOCK_PRIVATE_VALUE', json.dumps(client.receipts))

    def test_redirect_is_not_followed_and_query_never_recorded(self):
        self.fixture()
        Connection.responses[-1] = Response(status=307, headers={
            'Location': 'https://unapproved.example/blob?token=PRIVATE_QUERY'})
        client, files = self.client(), {}
        with self.assertRaisesRegex(c.Stop, 'REDIRECT_STOPPED'):
            c.collect(client, files)
        self.assertEqual(len(Connection.calls), 4)
        self.assertEqual(client.receipts[-1]['redirect_host'], 'unapproved.example')
        self.assertNotIn('PRIVATE_QUERY', json.dumps(client.receipts))
        self.assertNotIn('config.json', files)

    def test_bad_root_digest_blocks_child_requests(self):
        self.fixture()
        Connection.responses[1].headers['Docker-Content-Digest'] = 'sha256:' + '0' * 64
        with self.assertRaisesRegex(c.Stop, 'RESPONSE_DIGEST_MISMATCH'):
            c.collect(self.client(), {})
        self.assertEqual(len(Connection.calls), 2)

    def test_bad_config_digest_fails_collection(self):
        self.fixture()
        Connection.responses[-1] = Response(b'{}')
        with self.assertRaisesRegex(c.Stop, 'BLOB_MISMATCH'):
            c.collect(self.client(), {})

    def test_duplicate_platform_and_external_descriptor_stop_before_fetch(self):
        for variant in ('duplicate', 'external', 'size', 'traversal'):
            with self.subTest(variant=variant):
                entry = self.fixture()
                Connection.calls = []
                if variant == 'external':
                    entry['urls'] = ['https://unapproved.example/secret']
                elif variant == 'size':
                    entry['size'] = c.LIMIT + 1
                elif variant == 'traversal':
                    entry['digest'] = '../../outside'
                root = c.encode({'schemaVersion': 2,
                                 'mediaType': 'application/vnd.oci.image.index.v1+json',
                                 'manifests': [entry, entry] if variant == 'duplicate' else [entry]})
                Connection.responses[1] = Response(root, headers={'Docker-Content-Digest': c.digest(root)})
                with self.assertRaises(c.Stop):
                    c.collect(self.client(), {})
                self.assertEqual(len(Connection.calls), 2)

    def test_network_errors_do_not_fall_back(self):
        for status in (401, 403, 429, 500):
            Connection.calls = []
            Connection.responses = [Response(status=status)]
            with self.assertRaisesRegex(c.Stop, 'HTTP_STATUS_'):
                c.collect(self.client(), {})
            self.assertEqual(len(Connection.calls), 1)
        with patch.object(Connection, 'getresponse', side_effect=ssl.SSLError('secret-detail')):
            with self.assertRaises(ssl.SSLError):
                self.client().get(c.AUTH, c.TOKEN_PATH)

    def test_host_token_and_deadline_checks_before_connection(self):
        client = self.client()
        for host, path, token in [('other.example', '/', None), (c.AUTH, c.TOKEN_PATH, 'secret')]:
            with self.assertRaises(c.Stop):
                client.get(host, path, token)
        client.deadline = time.monotonic() - 1
        with self.assertRaises(c.Stop):
            client.get(c.AUTH, c.TOKEN_PATH)
        self.assertEqual(Connection.calls, [])

    def test_response_limits_and_invalid_json(self):
        for response in (Response(b'x' * 65537), Response(headers={'Content-Encoding': 'gzip'}),
                         Response(headers={'Content-Length': '999999999'}),
                         Response(b'{}', headers={'Content-Length': '4'})):
            Connection.responses = [response]
            with self.assertRaises(c.Stop):
                self.client().get(c.AUTH, c.TOKEN_PATH)
        for raw in (b'{"token":"a","token":"b"}', b'{"x":NaN}', b'[]', b'\xff'):
            with self.assertRaises(c.Stop):
                c.document(raw)

    def test_window_not_extended_and_expired_window_unchanged(self):
        first = c.window(self.fd, self.now)
        raw = (self.home / c.LEASE).read_bytes()
        self.assertEqual(c.window(self.fd, self.now + 300), first)
        for now in (self.now - 1, self.now + 3600):
            with self.assertRaises(c.Stop):
                c.window(self.fd, now)
        self.assertEqual((self.home / c.LEASE).read_bytes(), raw)

    def completion_fixture(self, redirect=True, platform='amd64'):
        raw = c.encode({'os': 'linux', 'architecture': platform,
                        'config': {'Env': ['MOCK_PRIVATE_VALUE=private']}})
        raw += b' ' * (c.CONFIG_BLOB_BYTES - len(raw))
        pin = patch.object(c, 'CONFIG_BLOB_DIGEST', c.digest(raw))
        pin.start()
        self.addCleanup(pin.stop)
        Connection.responses = [Response(c.encode({'token': 'SECRET_BEARER'}))]
        if redirect:
            Connection.responses.append(Response(status=307, headers={
                'Location': 'https://' + c.CDN + '/PRIVATE_PATH?Signature=PRIVATE_QUERY'}))
        Connection.responses.append(Response(raw))
        return c.Client({'started_epoch': self.now - 1, 'expires_epoch': self.now + 899}, True)

    def test_completion_retrieves_only_pinned_config_and_strips_token(self):
        for redirect in (True, False):
            with self.subTest(redirect=redirect):
                Connection.calls = []
                client, files = self.completion_fixture(redirect), {}
                result = c.complete_config(client, files)
                self.assertEqual(set(files), {'config.json'})
                self.assertEqual(result['config_digest'], c.CONFIG_BLOB_DIGEST)
                self.assertEqual(result['config_bytes'], 5664)
                self.assertEqual([call[0] for call in Connection.calls],
                                 [c.AUTH, c.REGISTRY] + ([c.CDN] if redirect else []))
                self.assertEqual(Connection.calls[1][2], c.config_path())
                self.assertEqual(Connection.calls[1][3]['Authorization'], 'Bearer SECRET_BEARER')
                if redirect:
                    self.assertNotIn('Authorization', Connection.calls[2][3])
                    with self.assertRaises(c.Stop):
                        client.get(c.CDN, Connection.calls[2][2])
                receipts = json.dumps(client.receipts)
                for secret in ('SECRET_BEARER', 'PRIVATE_PATH', 'PRIVATE_QUERY', 'MOCK_PRIVATE_VALUE'):
                    self.assertNotIn(secret, receipts)

    def test_completion_rejects_unapproved_redirect_authorities_and_url_forms(self):
        locations = [None, '', 'http://' + c.CDN + '/blob',
                     'https://' + c.CDN + '.evil.example/blob',
                     'https://user@' + c.CDN + '/blob',
                     'https://' + c.CDN + ':444/blob',
                     'https://' + c.CDN + './blob',
                     'https://' + c.CDN + '/blob#secret',
                     'https://' + c.CDN + '/blob\r\nX: secret',
                     'https://' + c.CDN + '/blob with space',
                     'https://' + c.CDN + '//other/blob',
                     'https://' + c.CDN + '/blob\\suffix',
                     'https://' + c.CDN, '//'+c.CDN+'/blob',
                     'https://[invalid/blob', 'https://' + c.CDN + '/' + 'x' * 16384]
        for location in locations:
            with self.subTest(location=location):
                Connection.calls = []
                client = self.completion_fixture()
                Connection.responses[1].headers['Location'] = location
                files = {}
                with self.assertRaisesRegex(c.Stop, 'CDN_LOCATION_INVALID'):
                    c.complete_config(client, files)
                self.assertEqual(len(Connection.calls), 2)
                self.assertEqual(files, {})

    def test_completion_accepts_only_https_default_or_explicit_443(self):
        for authority in (c.CDN, c.CDN + ':443', c.CDN.upper()):
            client = self.completion_fixture()
            Connection.responses[1].headers['Location'] = 'https://' + authority + '/blob?q=private'
            self.assertEqual(c.complete_config(client, {})['config_bytes'], 5664)

    def test_completion_blocks_second_redirect_and_other_redirect_statuses(self):
        for status in (301, 302, 303, 308):
            Connection.calls = []
            client = self.completion_fixture()
            Connection.responses[1].status = status
            with self.assertRaisesRegex(c.Stop, 'REDIRECT_STOPPED'):
                c.complete_config(client, {})
            self.assertEqual(len(Connection.calls), 2)
        Connection.calls = []
        client = self.completion_fixture()
        Connection.responses[-1] = Response(status=307, headers={
            'Location': 'https://' + c.CDN + '/another?SECRET_QUERY'})
        with self.assertRaisesRegex(c.Stop, 'REDIRECT_STOPPED'):
            c.complete_config(client, {})
        self.assertEqual(len(Connection.calls), 3)
        self.assertNotIn('SECRET_QUERY', json.dumps(client.receipts))

    def test_completion_has_no_tag_manifest_layer_or_arbitrary_cdn_permission(self):
        client = self.completion_fixture()
        for host, path, token in [(c.REGISTRY, c.ROOT_PATH, None),
                                  (c.REGISTRY, c.PREFIX + 'blobs/sha256:' + 'a' * 64, None),
                                  (c.CDN, '/arbitrary', None),
                                  (c.AUTH, c.TOKEN_PATH, 'secret')]:
            with self.assertRaises(c.Stop):
                client.get(host, path, token)
        for kind, value in [('manifests', c.REUSED_MANIFEST_DIGEST), ('blobs', 'sha256:' + 'a' * 64)]:
            with self.assertRaisesRegex(c.Stop, 'COMPLETION_SCOPE_VIOLATION'):
                client.allow_blob(kind, value)
        self.assertEqual(Connection.calls, [])

    def test_completion_rejects_wrong_hash_size_platform_and_oversized_response(self):
        for bad in (b'{}', b'x' * 5664, b'x' * 5665):
            client = self.completion_fixture()
            Connection.responses[-1] = Response(bad)
            files = {}
            with self.assertRaises(c.Stop):
                c.complete_config(client, files)
            self.assertEqual(files, {})
        client = self.completion_fixture(platform='arm64')
        with self.assertRaisesRegex(c.Stop, 'CONFIG_PLATFORM'):
            c.complete_config(client, {})

    def test_completion_expiry_before_cdn_stops_without_third_connection(self):
        client = self.completion_fixture()
        original = client.config_redirect

        def expire(location):
            target = original(location)
            client.deadline = time.monotonic() - 1
            return target

        with patch.object(client, 'config_redirect', side_effect=expire):
            with self.assertRaisesRegex(c.Stop, 'WINDOW_EXPIRED_OR_CLOCK_REVERSED'):
                c.complete_config(client, {})
        self.assertEqual(len(Connection.calls), 2)

    def test_completion_lease_is_separate_fifteen_minutes_and_cannot_renew(self):
        c.window(self.fd, self.now - 7200)
        old = (self.home / c.LEASE).read_bytes()
        current = c.window(self.fd, self.now, completion=True)
        raw = (self.home / c.CONFIG_LEASE).read_bytes()
        self.assertEqual(current['expires_epoch'] - current['started_epoch'], 900)
        self.assertEqual(current['config_digest'], c.CONFIG_BLOB_DIGEST)
        self.assertEqual(c.window(self.fd, self.now + 899, True), current)
        for moment in (self.now - 1, self.now + 900):
            with self.assertRaises(c.Stop):
                c.window(self.fd, moment, True)
        self.assertEqual((self.home / c.LEASE).read_bytes(), old)
        self.assertEqual((self.home / c.CONFIG_LEASE).read_bytes(), raw)

    def test_completion_changed_scope_or_symlink_stops(self):
        lease = c.window(self.fd, self.now, True)
        path = self.home / c.CONFIG_LEASE
        lease['config_digest'] = 'sha256:' + '0' * 64
        path.write_bytes(c.encode(lease))
        with self.assertRaisesRegex(c.Stop, 'WINDOW_SCOPE_MISMATCH'):
            c.window(self.fd, self.now, True)
        path.unlink()
        target = self.home / 'unchanged'
        target.write_text('KEEP')
        path.symlink_to(target)
        with self.assertRaises(OSError):
            c.window(self.fd, self.now, True)
        self.assertEqual(target.read_text(), 'KEEP')

    def test_completion_main_outputs_bound_archive_without_promoting_delivery(self):
        self.completion_fixture()
        with patch.object(c, 'open_home', return_value=os.dup(self.fd)), \
                patch('sys.stdout', new_callable=io.StringIO) as output:
            self.assertEqual(c.main(['--complete-config']), 0)
        report = json.loads(output.getvalue())
        self.assertEqual(report['COLLECTION'], 'PASS')
        self.assertEqual(report['BASE_MANIFEST_CHECK'], 'NOT_EXECUTED')
        self.assertEqual(report['DELIVERY_GATE'], 'NOT_VERIFIED')
        self.assertEqual(report['parent_archive_sha256'], c.PARENT_ARCHIVE_SHA256)
        self.assertNotIn('PRIVATE_QUERY', output.getvalue())
        self.assertFalse((self.home / c.LEASE).exists())
        folder = next(self.home.glob('FDS_CICD_Base_Config.*'))
        with tarfile.open(folder / 'base-config.tar.gz') as archive:
            self.assertEqual(set(archive.getnames()), {'config.json', 'result.json', 'SHA256SUMS'})
        self.assertTrue((folder / 'base-config.tar.gz.sha256').is_file())

    def test_completion_failure_retains_only_sanitized_receipts(self):
        self.completion_fixture()
        Connection.responses[-1] = Response(status=403)
        with patch.object(c, 'open_home', return_value=os.dup(self.fd)), \
                patch('sys.stdout', new_callable=io.StringIO) as output:
            self.assertEqual(c.main(['--complete-config']), 1)
        report = json.loads(output.getvalue())
        self.assertEqual(report['error_code'], 'HTTP_STATUS_403')
        folder = next(self.home.glob('FDS_CICD_Base_Config.*'))
        with tarfile.open(folder / 'base-config.tar.gz') as archive:
            self.assertEqual(set(archive.getnames()), {'result.json', 'SHA256SUMS'})
        for secret in ('SECRET_BEARER', 'PRIVATE_PATH', 'PRIVATE_QUERY'):
            self.assertNotIn(secret, (folder / 'result.json').read_text())

    def test_symlink_window_and_existing_output_preserved(self):
        target = self.home / 'important.txt'
        target.write_text('KEEP')
        (self.home / c.LEASE).symlink_to(target)
        with self.assertRaises(OSError):
            c.window(self.fd, self.now)
        with self.assertRaises(FileExistsError):
            c.write_new(self.fd, 'important.txt', b'changed')
        self.assertEqual(target.read_text(), 'KEEP')

    def test_evidence_is_private_bounded_archive_without_home_changes(self):
        before = stat.S_IMODE(self.home.stat().st_mode)
        c.save_evidence(self.fd, 'new-run', {'index.json': b'{}'}, {'COLLECTION': 'FAIL'})
        folder = self.home / 'new-run'
        self.assertEqual(stat.S_IMODE(self.home.stat().st_mode), before)
        self.assertEqual(stat.S_IMODE(folder.stat().st_mode), 0o700)
        with tarfile.open(folder / 'base-metadata.tar.gz') as archive:
            self.assertEqual(set(archive.getnames()), {'index.json', 'result.json', 'SHA256SUMS'})
            self.assertTrue(all(m.isfile() and m.mode == 0o600 for m in archive.getmembers()))
        for path in folder.iterdir():
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        with self.assertRaises(FileExistsError):
            c.save_evidence(self.fd, 'new-run', {}, {})

    def test_main_wrong_target_has_no_network_or_filesystem_writes(self):
        with patch.object(c, 'open_home', side_effect=c.Stop('TARGET_MISMATCH')), \
                patch.object(c, 'save_evidence') as save, \
                patch('sys.stdout', new_callable=io.StringIO) as output:
            self.assertEqual(c.main(['--collect']), 1)
        save.assert_not_called()
        self.assertEqual(Connection.calls, [])
        self.assertEqual(json.loads(output.getvalue())['error_code'], 'TARGET_MISMATCH')

    def test_main_tls_failure_has_no_secret_in_stdout_or_evidence(self):
        with patch.object(c, 'open_home', return_value=os.dup(self.fd)), \
                patch.object(Connection, 'getresponse', side_effect=ssl.SSLError('PRIVATE_TLS_DETAIL')), \
                patch('sys.stdout', new_callable=io.StringIO) as output:
            self.assertEqual(c.main(['--collect']), 1)
        self.assertNotIn('PRIVATE_TLS_DETAIL', output.getvalue())
        report = json.loads(output.getvalue())
        self.assertEqual(report['COLLECTION'], 'FAIL')
        self.assertEqual(report['DELIVERY_GATE'], 'NOT_VERIFIED')
        folder = next(self.home.glob('FDS_CICD_Base_Metadata.*'))
        self.assertNotIn('PRIVATE_TLS_DETAIL', (folder / 'result.json').read_text())

    def test_target_and_proxy_guards_do_not_open_home(self):
        account = type('Account', (), {'pw_name': c.ACCOUNT, 'pw_dir': c.HOME_PATH})()
        with patch.object(c.os, 'getuid', return_value=1000), \
                patch.object(c.os, 'geteuid', return_value=1000), \
                patch.object(c.pwd, 'getpwuid', return_value=account), \
                patch.object(c.socket, 'gethostname', return_value='cicd-runner01'), \
                patch.object(c.os, 'open') as open_file:
            with patch.dict(c.os.environ, {'HTTPS_PROXY': 'http://proxy.example'}, clear=True):
                with self.assertRaisesRegex(c.Stop, 'PROXY_OR_CA'):
                    c.open_home()
            with patch.object(c.os, 'geteuid', return_value=0):
                with self.assertRaisesRegex(c.Stop, 'TARGET_MISMATCH'):
                    c.open_home()
        open_file.assert_not_called()


if __name__ == '__main__':
    unittest.main()
