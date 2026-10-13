"""Real POSIX extraction/permission regressions; no container or deployment."""
import hashlib
import io
import os
from pathlib import Path
import stat
import tarfile
import tempfile
import unittest

import build_scan as flow


@unittest.skipUnless(os.name == 'posix', 'Requires POSIX permission semantics')
class SourcePermissionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.root.chmod(0o700)
        self.archive = self.root / 'source.tar'
        self.target = self.root / 'context'
        previous = os.umask(0o077)
        self.addCleanup(os.umask, previous)

    def fixture(self, invalid=None):
        with tarfile.open(self.archive, 'w') as archive:
            # Implicit parent directories must also be usable in the image.
            for name, mode in [('transaction-api/run.py', 0o644),
                               ('transaction-api/app/deep/module.py', 0o600),
                               ('fds-engine/bin/start', 0o4755)]:
                raw = b'fixture source\n'
                member = tarfile.TarInfo(name)
                member.mode, member.size = mode, len(raw)
                archive.addfile(member, io.BytesIO(raw))
            if invalid:
                member = tarfile.TarInfo(invalid)
                if invalid == 'transaction-api/link':
                    member.type, member.linkname = tarfile.SYMTYPE, '/etc/passwd'
                archive.addfile(member)

    def mode(self, path):
        return stat.S_IMODE(path.stat().st_mode)

    def test_image_files_are_readable_and_executable_bits_are_preserved(self):
        self.fixture()
        original = hashlib.sha256(self.archive.read_bytes()).hexdigest()
        guard = self.root / 'credential-fixture'
        guard.write_bytes(b'synthetic only')
        flow.unpack_source(self.archive, self.target, for_image=True)
        for name in ('transaction-api/run.py', 'transaction-api/app/deep/module.py'):
            self.assertEqual(self.mode(self.target / name), 0o644)
        self.assertEqual(self.mode(self.target / 'fds-engine/bin/start'), 0o755)
        for path in self.target.rglob('*'):
            if path.is_dir():
                self.assertEqual(self.mode(path), 0o755)
            self.assertEqual(self.mode(path) & 0o7022, 0)  # No special/group/other write bits.
        self.assertEqual(self.mode(self.target), 0o700)
        self.assertEqual(self.mode(self.root), 0o700)
        self.assertEqual(self.mode(guard), 0o600)
        self.assertEqual(hashlib.sha256(self.archive.read_bytes()).hexdigest(), original)

    def test_default_test_copy_stays_private(self):
        self.fixture()
        flow.unpack_source(self.archive, self.target)
        for path in self.target.rglob('*'):
            self.assertEqual(self.mode(path) & 0o077, 0)
        self.assertEqual(self.mode(self.target / 'transaction-api/run.py'), 0o600)
        self.assertEqual(self.mode(self.target / 'fds-engine/bin/start'), 0o700)

    def test_rejected_archive_does_not_publish_partially_extracted_permissions(self):
        for invalid in ('transaction-api/link', '../escape'):
            with self.subTest(invalid=invalid):
                self.fixture(invalid)
                target = self.root / ('bad-link' if 'link' in invalid else 'bad-path')
                with self.assertRaises(flow.PreflightError):
                    flow.unpack_source(self.archive, target, for_image=True)
                for path in target.rglob('*'):
                    self.assertEqual(self.mode(path) & 0o077, 0)
        self.assertFalse((self.root / 'escape').exists())

    def test_existing_context_is_not_modified(self):
        self.fixture()
        self.target.mkdir(mode=0o700)
        retained = self.target / 'retained'
        retained.write_bytes(b'original')
        with self.assertRaises(FileExistsError):
            flow.unpack_source(self.archive, self.target, for_image=True)
        self.assertEqual(retained.read_bytes(), b'original')
        self.assertEqual(self.mode(retained), 0o600)
        self.assertEqual(self.mode(self.target), 0o700)


if __name__ == '__main__':
    unittest.main()
