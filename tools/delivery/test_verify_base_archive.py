import io
import json
import os
from pathlib import Path
import stat
import tarfile
import tempfile
import unittest
from unittest.mock import patch

import verify_base_archive as v
from oci_test_fixtures import encoded, image_members, write_archive


class BaseArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / 'base.oci.tar'
        self.members = image_members()
        write_archive(self.path, self.members)
        desc = json.loads(self.members['index.json'])['manifests'][0]
        manifest = json.loads(self.members['blobs/sha256/' + desc['digest'][7:]])
        self.manifest, self.config = desc['digest'], manifest['config']['digest']
        self.transport = v.sha256(self.path)

    def check(self):
        return v.verify(self.path, self.transport, self.manifest, self.config)

    def args(self):
        return ['--archive', str(self.path), '--expected-archive-sha256', self.transport,
                '--expected-manifest', self.manifest, '--expected-config', self.config]

    def test_success_checks_layers_and_preserves_file_and_parent(self):
        before = self.path.read_bytes()
        modes = [stat.S_IMODE(x.stat().st_mode) for x in (self.root, self.path)]
        self.assertEqual(self.check()['REFERENCED_BLOB_HASHES'], 'PASS')
        self.assertEqual(self.check()['referenced_layer_count'], 1)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual([stat.S_IMODE(x.stat().st_mode) for x in (self.root, self.path)], modes)
        self.assertEqual(list(self.root.iterdir()), [self.path])

    def test_missing_or_malformed_identities_fail_before_archive_open(self):
        for bad in (None, '', 'pending', 'a' * 63, 'A' * 64, 'sha256:' + 'a' * 64):
            with self.subTest(value=bad), patch.object(v, 'inspect_archive') as inspect:
                with self.assertRaisesRegex(v.PreflightError, 'EXPECTED_IDENTITIES_REQUIRED'):
                    v.verify(self.path, bad, self.manifest, self.config)
                inspect.assert_not_called()

    def test_transport_mismatch_stops_before_parsing(self):
        self.transport = '0' * 64
        with patch.object(v, 'inspect_archive') as inspect:
            with self.assertRaisesRegex(v.PreflightError, 'ARCHIVE_TRANSPORT_HASH_MISMATCH'):
                self.check()
            inspect.assert_not_called()

    def test_other_valid_image_or_config_is_not_accepted(self):
        for field, code in [('manifest', 'BASE_MANIFEST_MISMATCH'), ('config', 'BASE_CONFIG_MISMATCH')]:
            with self.subTest(field=field), patch.object(self, field, 'sha256:' + '0' * 64):
                with self.assertRaisesRegex(v.PreflightError, code):
                    self.check()

    def test_transport_checksum_does_not_hide_corrupted_layer(self):
        name = next(n for n in self.members if n.startswith('blobs/'))
        self.members[name] = b'x' * len(self.members[name])
        write_archive(self.path, self.members)
        self.transport = v.sha256(self.path)
        with self.assertRaisesRegex(v.PreflightError, 'OCI_BLOB_HASH_MISMATCH'):
            self.check()

    def test_missing_layer_fails_even_with_matching_transport(self):
        del self.members[next(n for n in self.members if n.startswith('blobs/'))]
        write_archive(self.path, self.members)
        self.transport = v.sha256(self.path)
        with self.assertRaisesRegex(v.PreflightError, 'OCI_BLOB_MISSING'):
            self.check()

    def test_multiple_images_fail(self):
        index = json.loads(self.members['index.json'])
        index['manifests'] *= 2
        self.members['index.json'] = encoded(index)
        write_archive(self.path, self.members)
        self.transport = v.sha256(self.path)
        with self.assertRaisesRegex(v.PreflightError, 'OCI_SINGLE_IMAGE_REQUIRED'):
            self.check()

    def test_path_traversal_and_link_members_fail_without_extraction(self):
        for name, kind in [('../outside', tarfile.REGTYPE), ('link', tarfile.SYMTYPE)]:
            member = tarfile.TarInfo(name)
            member.type, member.linkname = kind, '/PRIVATE_TARGET'
            write_archive(self.path, self.members, member)
            self.transport = v.sha256(self.path)
            with self.assertRaises(v.PreflightError):
                self.check()
        self.assertEqual(list(self.root.iterdir()), [self.path])

    def test_symlink_fifo_directory_refused_before_hash(self):
        for kind in ('symlink', 'fifo', 'directory'):
            p = self.root / kind
            if kind == 'symlink':
                p.symlink_to(self.path)
            elif kind == 'fifo':
                os.mkfifo(p)
            else:
                p.mkdir()
            with patch.object(v, 'sha256') as hashed:
                with self.assertRaisesRegex(v.PreflightError, 'ARCHIVE_REGULAR_FILE_REQUIRED'):
                    v.verify(p, self.transport, self.manifest, self.config)
                hashed.assert_not_called()

    def test_size_limit_refused_before_hash(self):
        with patch.object(v, 'MAX_ARCHIVE_BYTES', 1), patch.object(v, 'sha256') as hashed:
            with self.assertRaisesRegex(v.PreflightError, 'ARCHIVE_SIZE_UNSUPPORTED'):
                self.check()
            hashed.assert_not_called()

    def test_mutation_during_inspection_is_rejected(self):
        original = v.inspect_archive

        def mutate(path):
            result = original(path)
            path.write_bytes(path.read_bytes() + b'x')
            return result

        with patch.object(v, 'inspect_archive', side_effect=mutate):
            with self.assertRaisesRegex(v.PreflightError, 'ARCHIVE_CHANGED'):
                self.check()

    def test_cli_pass_is_not_import_supply_or_delivery_pass(self):
        with patch('sys.stdout', new_callable=io.StringIO) as output:
            self.assertEqual(v.main(self.args()), 0)
        result = json.loads(output.getvalue())
        self.assertEqual(result['BASE_ARCHIVE_CHECK'], 'PASS')
        self.assertEqual(result['IMAGE_IMPORT'], 'NOT_EXECUTED')
        self.assertEqual(result['LAYER_DIFFID_CONTENT'], 'NOT_VERIFIED')
        self.assertEqual(result['SUPPLY_APPROVAL'], 'NOT_VERIFIED')
        self.assertEqual(result['DELIVERY_GATE'], 'NOT_VERIFIED')

    def test_cli_read_and_malformed_input_errors_do_not_leak_details(self):
        for error in (OSError('PRIVATE_PATH'), tarfile.ReadError('PRIVATE_TAR'),
                      ValueError('PRIVATE_JSON'), TypeError('PRIVATE_TYPE')):
            with patch.object(v, 'verify', side_effect=error), \
                    patch('sys.stdout', new_callable=io.StringIO) as output:
                self.assertEqual(v.main(self.args()), 1)
            self.assertNotIn('PRIVATE_', output.getvalue())
            self.assertEqual(json.loads(output.getvalue())['BASE_ARCHIVE_CHECK'], 'FAIL')


if __name__ == '__main__':
    unittest.main()
