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
