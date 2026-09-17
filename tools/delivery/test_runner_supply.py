import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import socket
import sys
import tarfile
import tempfile
import time
import unittest
from unittest.mock import patch, Mock
import zipfile

import build_supply_bundle as bundle
import collect_runner_supply as collect
import supply_transport as transport
import supply_wheels as wheels


def wheel(name='supply_fixture', version='1.0', dependency=None):
    out = io.BytesIO()
    folder = name + '-' + version + '.dist-info/'
    with zipfile.ZipFile(out, 'w') as archive:
        metadata = 'Metadata-Version: 2.1\nName: ' + name + '\nVersion: ' + version + '\n'
        if dependency:
            metadata += 'Requires-Dist: ' + dependency + '\n'
        archive.writestr(folder + 'METADATA', metadata)
        archive.writestr(folder + 'WHEEL', 'Wheel-Version: 1.0\nGenerator: fixture\nRoot-Is-Purelib: true\nTag: py3-none-any\n')
        archive.writestr(folder + 'RECORD', '')
    return out.getvalue()


class Reply:
    def __init__(self, raw=b'body', status=200, headers=None):
        self.raw, self.status, self.headers = io.BytesIO(raw), status, headers or {}

    def getheader(self, key, default=None):
        return self.headers.get(key, default)

    def read(self, size):
        return self.raw.read(size)


class SupplyTests(unittest.TestCase):
    def client(self):
        return transport.Client({'started_epoch': int(time.time()) - 1,
                                 'expires_epoch': int(time.time()) + 3600})

    def test_fixed_metadata_matches_pins_and_requirements(self):
        self.assertEqual('sha256:' + transport.sha(transport.MANIFEST_RAW), transport.MANIFEST_DIGEST)
        self.assertEqual('sha256:' + transport.sha(transport.CONFIG_RAW), transport.CONFIG_DIGEST)
        plan = json.loads((Path(__file__).parents[2] / 'docs/offline-supply-plan.json').read_text())
        for row in plan['requirements']:
            self.assertEqual(transport.sha(collect.REQUIREMENTS[row['path']].encode()), row['sha256'])
        self.assertEqual(sum(x['size'] for x in plan['base']['layers']), 46175163)

    def test_url_rejects_other_hosts_ports_userinfo_fragments_and_controls(self):
        for url in ('http://production.cloudfront.docker.com/x',
                    'https://production.cloudfront.docker.com.evil/x',
                    'https://production.cloudfront.docker.com:444/x',
                    'https://u@production.cloudfront.docker.com/x',
                    'https://production.cloudfront.docker.com//x',
                    'https://production.cloudfront.docker.com/x#f',
                    'https://production.cloudfront.docker.com/x\n'):
            with self.subTest(url=url), self.assertRaises(transport.Stop):
                transport.url_path(url, transport.CDN)

    @patch.object(transport, 'idle')
    def test_redirect_strips_bearer_and_redacts_signed_url(self, _):
        client = self.client()
        connections = []
        replies = [Reply(status=307, headers={'Location': 'https://' + transport.CDN + '/secret?token=secret'}), Reply()]
        def factory(*args, **kwargs):
            conn = Mock()
            conn.getresponse.return_value = replies[len(connections)]
            connections.append(conn)
            return conn
        with patch.object(transport.http.client, 'HTTPSConnection', side_effect=factory):
            self.assertEqual(client.get(transport.REGISTRY, next(iter(client.layers)), 10, 'token'), b'body')
        self.assertIn('Authorization', connections[0].request.call_args.kwargs['headers'])
        self.assertNotIn('Authorization', connections[1].request.call_args.kwargs['headers'])
        self.assertNotIn('secret', json.dumps(client.receipts))

    @patch.object(transport, 'idle')
    def test_second_redirect_is_not_followed(self, _):
        client = self.client()
        conn = Mock()
        conn.getresponse.side_effect = [Reply(status=307, headers={'Location': 'https://' + transport.CDN + '/x'}),
                                       Reply(status=307, headers={'Location': 'https://' + transport.CDN + '/y'})]
        with patch.object(transport.http.client, 'HTTPSConnection', return_value=conn) as factory:
            with self.assertRaisesRegex(transport.Stop, 'REDIRECT_STOPPED'):
                client.get(transport.REGISTRY, next(iter(client.layers)), 10, 'token')
        self.assertEqual(factory.call_count, 2)

    @patch.object(transport, 'idle')
    def test_unlisted_path_and_token_destination_stop_before_connection(self, _):
        client = self.client()
        with patch.object(transport.http.client, 'HTTPSConnection') as factory:
            for host, path, token in [(transport.REGISTRY, '/v2/library/python/manifests/latest', None),
                                      (transport.FILES, '/packages/arbitrary', None),
                                      (transport.PYPI, '/simple/foo/', 'token')]:
                with self.assertRaises(transport.Stop):
                    client.get(host, path, 10, token)
            factory.assert_not_called()

    @patch.object(transport, 'idle')
    def test_clock_expiry_stops_before_connection(self, _):
        client = self.client()
        client.deadline = time.monotonic() - 1
        with patch.object(transport.http.client, 'HTTPSConnection') as factory:
            with self.assertRaises(transport.Stop):
                client.get(transport.AUTH, transport.TOKEN_PATH, 10)
            factory.assert_not_called()

    @patch.object(transport, 'idle')
    def test_response_size_and_truncation_fail(self, _):
        for reply in (Reply(b'123456', headers={'Content-Length': '6'}),
                      Reply(b'123456'), Reply(b'1', headers={'Content-Length': '2'})):
            client, conn = self.client(), Mock()
            conn.getresponse.return_value = reply
            with patch.object(transport.http.client, 'HTTPSConnection', return_value=conn):
                with self.assertRaises(transport.Stop):
                    client.get(transport.AUTH, transport.TOKEN_PATH, 5)

    def test_window_retries_never_extend_or_rewrite(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(transport, 'HOME', Path(tmp)):
            first = transport.window(100)
            path = Path(tmp) / transport.LEASE
            before = path.read_bytes()
            real_fstat = os.fstat
            def owned(fd):
                obj = Mock(wraps=real_fstat(fd))
                obj.st_mode, obj.st_size, obj.st_uid = 0o100600, len(before), 1000
                return obj
            with patch.object(transport.os, 'fstat', side_effect=owned):
                self.assertEqual(transport.window(200), first)
                with self.assertRaisesRegex(transport.Stop, 'WINDOW_EXPIRED'):
                    transport.window(3700)
            self.assertEqual(path.read_bytes(), before)

    def test_write_new_never_overwrites_existing_or_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'existing'
            path.write_bytes(b'preserve')
            link = Path(tmp) / 'link'
            link.symlink_to(path)
            for dest in (path, link):
                with self.assertRaises(FileExistsError):
                    transport.write_new(dest, b'changed')
            self.assertEqual(path.read_bytes(), b'preserve')

    def test_completed_scope_cannot_restart_even_inside_original_window(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(transport, 'HOME', Path(tmp)):
            (Path(tmp) / (transport.LEASE + '.completed')).write_text('{}')
            with self.assertRaisesRegex(transport.Stop, 'SCOPE_ALREADY_COMPLETED'):
                transport.window(100)
            self.assertFalse((Path(tmp) / transport.LEASE).exists())

    def test_wheel_url_dependencies_fail_before_pip_can_read_them(self):
        for dependency in ('evil @ https://example.test/evil.whl', 'evil @ file:///tmp/file',
                           'evil @ git+https://example.test/repo'):
            with self.assertRaisesRegex(transport.Stop, 'DEPENDENCY_URL'):
                wheels.wheel_metadata(wheel(dependency=dependency))

    def test_wheel_traversal_and_duplicate_metadata_fail(self):
        for name in ('../escape', 'supply_fixture-1.0.dist-info/METADATA'):
            out = io.BytesIO(wheel())
            with zipfile.ZipFile(out, 'a') as archive:
                archive.writestr(name, 'bad')
            with self.assertRaises(transport.Stop):
                wheels.wheel_metadata(out.getvalue())

    def test_lock_keeps_extras_and_binds_every_wheel(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / 'supply_fixture-1.0-py3-none-any.whl').write_bytes(wheel())
            (folder / 'child-2.0-py3-none-any.whl').write_bytes(wheel('child', '2.0'))
            lock, inventory = wheels.make_lock(folder, ['supply-fixture[standard]==1.0'])
            self.assertIn(b'supply-fixture[standard]==1.0 --hash=sha256:', lock)
            self.assertIn(b'child==2.0 --hash=sha256:', lock)
            self.assertEqual(len(inventory), 2)
            with self.assertRaisesRegex(transport.Stop, 'ROOT_VERSION_MISMATCH'):
                wheels.make_lock(folder, ['supply-fixture==2.0'])

    def test_index_rewrites_links_and_does_not_offer_sdists_or_metadata_urls(self):
        client = Mock(allowed_files=set())
        raw = wheel()
        filename = 'supply_fixture-1.0-py3-none-any.whl'
        obj = {'name': 'supply-fixture', 'files': [
            {'filename': 'supply_fixture-1.0.tar.gz'},
            {'filename': filename, 'url': 'https://' + transport.FILES + '/packages/aa/' + filename,
             'hashes': {'sha256': transport.sha(raw)}, 'core-metadata': True, 'requires-python': '>=3.8'}]}
        client.get.return_value = transport.encode(obj)
        index = wheels.WheelIndex(client, Path('/unused'))
        page = index.page('supply-fixture')
        self.assertNotIn(b'https:', page)
        self.assertNotIn(b'.tar.gz', page)
        self.assertNotIn(b'core-metadata', page)
        self.assertIn(b'data-requires-python=', page)
        client.get.return_value = b'wrong'
        with self.assertRaisesRegex(transport.Stop, 'WHEEL_HASH_MISMATCH'):
            index.wheel(next(iter(index.files)))

    def test_containers_use_private_storage_no_network_and_no_remote_pull(self):
        commands = collect.Commands(Path('/home/server01_cicd-runner/new-only'))
        commands.client = self.client()
        argv = commands.container(['-c', 'pass'])
        self.assertIn('--network=none', argv)
        self.assertIn('--pull=never', argv)
        self.assertIn('--read-only', argv)
        self.assertNotIn('--privileged', argv)
        self.assertEqual(argv[argv.index('--root') + 1], '/home/server01_cicd-runner/new-only/storage')

    def test_bundle_is_deterministic_and_runs_under_isolated_python(self):
        with tempfile.TemporaryDirectory() as tmp:
            a, b = Path(tmp) / 'a.pyz', Path(tmp) / 'b.pyz'
            self.assertEqual(bundle.build(a), bundle.build(b))
            result = subprocess.run([sys.executable, '-I', '-B', a, '--help'], capture_output=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(b'--collect', result.stdout)

    def test_real_pip_download_through_unix_relay_uses_only_synthetic_upstream(self):
        # End-to-end local IPC + installed pip. No real PyPI/registry request.
        try:
            probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            probe.close()
        except PermissionError:
            self.skipTest('Execution environment denies socket creation; required in GitHub CI')
        filename, raw = 'supply_fixture-1.0-py3-none-any.whl', wheel()
        with tempfile.TemporaryDirectory() as tmp:
            folder, client = Path(tmp), Mock(allowed_files=set())
            obj = {'name': 'supply-fixture', 'files': [{'filename': filename,
                'url': 'https://' + transport.FILES + '/packages/aa/' + filename,
                'hashes': {'sha256': transport.sha(raw)}}]}
            def get(host, path, maximum):
                return transport.encode(obj) if host == transport.PYPI else raw
            client.get.side_effect = get
            dest = folder / 'wheels'
            with wheels.WheelIndex(client, folder / 'index.sock') as index:
                result = subprocess.run([sys.executable, '-I', '-B',
                    Path(__file__).with_name('supply_pip_bridge.py'), folder / 'index.sock',
                    'download', '--index-url', '@INDEX@', '--only-binary=:all:', '--retries=0',
                    '--dest', dest, 'supply-fixture==1.0'], capture_output=True, timeout=20)
                self.assertIsNone(index.error)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual((dest / filename).read_bytes(), raw)
            self.assertEqual([x.args[0] for x in client.get.call_args_list],
                             [transport.PYPI, transport.FILES])

    def test_base_archive_checks_diffids_and_preserves_exact_metadata(self):
        payload = b'synthetic-layer-content'
        raw = gzip.compress(payload)
        config_raw = transport.encode({'os': 'linux', 'architecture': 'amd64',
                        'rootfs': {'type': 'layers', 'diff_ids': ['sha256:' + transport.sha(payload)]}})
        manifest_raw = transport.encode({'schemaVersion': 2, 'mediaType': 'application/vnd.oci.image.manifest.v1+json',
            'config': {'digest': 'sha256:' + transport.sha(config_raw), 'size': len(config_raw),
                       'mediaType': 'application/vnd.oci.image.config.v1+json'},
            'layers': [{'digest': 'sha256:' + transport.sha(raw), 'size': len(raw),
                        'mediaType': 'application/vnd.oci.image.layer.v1.tar+gzip'}]})
        with tempfile.TemporaryDirectory() as tmp, patch.multiple(transport,
                MANIFEST_RAW=manifest_raw, CONFIG_RAW=config_raw,
                MANIFEST_DIGEST='sha256:' + transport.sha(manifest_raw),
                CONFIG_DIGEST='sha256:' + transport.sha(config_raw)):
            client, path = Mock(), Path(tmp) / 'base.tar'
            client.get.side_effect = [b'{"token":"fixture"}', raw]
            archive_sha = transport.base_archive(client, path)
            self.assertEqual(archive_sha, transport.sha(path.read_bytes()))
            with tarfile.open(path, 'r:') as archive:
                self.assertEqual(archive.extractfile('blobs/sha256/' + transport.sha(manifest_raw)).read(), manifest_raw)
            client.get.side_effect = [b'{"token":"fixture"}', b'bad']
            with self.assertRaisesRegex(transport.Stop, 'LAYER_HASH_OR_SIZE'):
                transport.base_archive(client, Path(tmp) / 'bad.tar')


if __name__ == '__main__':
    unittest.main()
