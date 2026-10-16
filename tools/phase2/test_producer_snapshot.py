"""Local synthetic Git objects only; no renderer, network, credentials or Runtime."""
import gzip
import hashlib
import io
import json
import socket
import tarfile
import unittest
from unittest.mock import patch

import producer_bridge as bridge
import producer_snapshot as snapshot
from test_producer_bridge import RepositoryFixture, FAKE_CHECKER


class SnapshotPackTests(RepositoryFixture):
    def setUp(self):
        super().setUp()
        for path in snapshot.PATHS:
            target = self.root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            if path not in (bridge.CHECKER_PATH, bridge.CONTRACT_PATH):
                target.write_bytes(('SYNTHETIC SOURCE ' + path + '\n').encode())
        self.git('add', '--', *snapshot.PATHS)
        self.git('commit', '-qm', 'Synthetic fixed render source tree')
        self.commit = self.git('rev-parse', 'HEAD').decode().strip()
        self.pin = patch.object(bridge, 'CHECKER_SHA256', hashlib.sha256(FAKE_CHECKER).hexdigest())
        self.pin.start()
        self.addCleanup(self.pin.stop)
        for name in ('socket', 'create_connection', 'getaddrinfo'):
            guard = patch.object(socket, name, side_effect=AssertionError('NETWORK_FORBIDDEN'))
            guard.start()
            self.addCleanup(guard.stop)

    def capture(self, commit=None):
        return snapshot.capture(self.root, self.commit if commit is None else commit)

    def test_pack_exact_nine_files_original_bytes_and_no_approval(self):
        pack = self.capture()
        manifest = json.loads(pack.manifest)
        self.assertEqual(tuple(f.path for f in pack.files), snapshot.PATHS)
        self.assertEqual(manifest['source_integrity'], 'MATCH')
        self.assertEqual(manifest['render_generation'], 'NOT_RUN')
        self.assertEqual(manifest['dependency_closure'], 'NOT_VERIFIED')
        self.assertFalse(pack.execution_authorized)
        self.assertEqual(pack.source_approval, 'NOT_VERIFIED')
        with tarfile.open(fileobj=io.BytesIO(pack.archive), mode='r:gz') as arc:
            self.assertEqual(len(arc.getmembers()), 10)
            self.assertEqual(arc.extractfile('SOURCE_SNAPSHOT.json').read(), pack.manifest)
            for f in pack.files:
                member = arc.getmember('source/' + f.path)
                self.assertTrue(member.isfile())
                self.assertEqual((member.mode, member.mtime, member.uid, member.gid), (0o644, 0, 0, 0))
                self.assertEqual(arc.extractfile(member).read(), f.raw)
                self.assertEqual(f.raw, self.git('show', self.commit + ':' + f.path))

    def test_deterministic_and_ignores_worktree_dirty_and_untracked_files(self):
        first = self.capture()
        (self.root / snapshot.PATHS[0]).write_bytes(b'DIRTY_PRIVATE_SOURCE')
        (self.root / 'never-collect-private.txt').write_bytes(b'PRIVATE_OUTSIDE_SOURCE_ALLOWLIST')
        second = self.capture()
        self.assertEqual(first.archive, second.archive)
        self.assertEqual(first.sha256, second.sha256)
        self.assertNotIn(b'DIRTY_PRIVATE_SOURCE', gzip.decompress(second.archive))
        self.assertFalse(b'PRIVATE_OUTSIDE_SOURCE_ALLOWLIST' in gzip.decompress(second.archive))
        self.assertNotIn('SYNTHETIC SOURCE', repr(second))

    def test_new_commit_changes_pack_but_old_commit_is_reproducible(self):
        old = self.capture()
        (self.root / snapshot.PATHS[0]).write_bytes(b'NEW COMMITTED INPUT\n')
        self.git('add', '--', snapshot.PATHS[0])
        self.git('commit', '-qm', 'New synthetic source')
        new_commit = self.git('rev-parse', 'HEAD').decode().strip()
        new = self.capture(new_commit)
        self.assertNotEqual(old.sha256, new.sha256)
        self.assertEqual(self.capture().archive, old.archive)

    def test_replace_refs_cannot_substitute_new_source_tree(self):
        old = self.capture()
        (self.root / snapshot.PATHS[0]).write_bytes(b'REPLACEMENT\n')
        self.git('add', '--', snapshot.PATHS[0])
        self.git('commit', '-qm', 'Replacement')
        new = self.git('rev-parse', 'HEAD').decode().strip()
        self.git('replace', self.commit, new)
        self.assertEqual(self.capture().archive, old.archive)

    def test_tracked_p1_and_private_files_are_not_exported(self):
        private = self.root / 'deploy/overlays/p1-onprem/private.yaml'
        private.parent.mkdir(parents=True)
        private.write_bytes(b'PRIVATE_OUTSIDE_SOURCE_ALLOWLIST')
        self.git('add', '--', 'deploy/overlays/p1-onprem/private.yaml')
        self.git('commit', '-qm', 'Unrelated source')
        pack = self.capture(self.git('rev-parse', 'HEAD').decode().strip())
        self.assertEqual(len(pack.files), 9)
        self.assertFalse(b'PRIVATE_OUTSIDE_SOURCE_ALLOWLIST' in gzip.decompress(pack.archive))

    def test_unreviewed_checker_stops_without_partial_pack(self):
        new = self.publish(b'raise RuntimeError("SYNTHETIC_SECRET")', b'host: pending\n')
        with self.assertRaisesRegex(snapshot.SnapshotError, '^CHECKER_NOT_REVIEWED$'):
            self.capture(new)

    def test_missing_input_stops_without_using_worktree_fallback(self):
        self.git('rm', '--cached', '--', snapshot.PATHS[0])
        self.git('commit', '-qm', 'Missing committed input')
        self.assertTrue((self.root / snapshot.PATHS[0]).exists())
        with self.assertRaisesRegex(snapshot.SnapshotError, '^SOURCE_PATH_UNSAFE$'):
            self.capture(self.git('rev-parse', 'HEAD').decode().strip())

    def test_git_symlink_mode_is_rejected_even_when_blob_is_valid(self):
        blob = self.git('rev-parse', self.commit + ':' + snapshot.PATHS[0]).decode().strip()
        self.git('update-index', '--cacheinfo', '120000', blob, snapshot.PATHS[0])
        self.git('commit', '-qm', 'Synthetic symlink index')
        with self.assertRaisesRegex(snapshot.SnapshotError, '^SOURCE_PATH_UNSAFE$'):
            self.capture(self.git('rev-parse', 'HEAD').decode().strip())

    def test_parent_gitlink_is_rejected(self):
        self.git('rm', '-r', '--cached', '--', 'deploy/base')
        self.git('update-index', '--add', '--cacheinfo', '160000', self.commit, 'deploy/base')
        self.git('commit', '-qm', 'Synthetic gitlink index')
        with self.assertRaisesRegex(snapshot.SnapshotError, '^SOURCE_PATH_UNSAFE$'):
            self.capture(self.git('rev-parse', 'HEAD').decode().strip())

    def test_oversized_blob_stops_before_payload_read(self):
        (self.root / snapshot.PATHS[0]).write_bytes(b'x' * (snapshot.MAX_OBJECT + 1))
        self.git('add', '--', snapshot.PATHS[0])
        self.git('commit', '-qm', 'Oversized synthetic source')
        with self.assertRaisesRegex(snapshot.SnapshotError, '^SOURCE_SIZE_INVALID$'):
            self.capture(self.git('rev-parse', 'HEAD').decode().strip())

    def test_object_corruption_cannot_be_reported_as_integrity_match(self):
        original = bridge.GitSnapshot._git
        def corrupt(source, *args):
            value = original(source, *args)
            return value[:-1] + b'X' if args[0] == 'blob' else value
        with patch.object(bridge.GitSnapshot, '_git', corrupt):
            with self.assertRaisesRegex(snapshot.SnapshotError, '^SOURCE_OBJECT_CHANGED$'):
                self.capture()

    def test_git_failure_sanitized_and_no_renderer_command(self):
        with patch.object(bridge.GitSnapshot, '_git', side_effect=RuntimeError('SYNTHETIC_SECRET')):
            with self.assertRaisesRegex(snapshot.SnapshotError, '^SOURCE_CAPTURE_FAILED$'):
                self.capture()
        original = bridge.subprocess.run
        commands = []
        def observe(command, **kwargs):
            commands.append(command)
            return original(command, **kwargs)
        with patch.object(bridge.subprocess, 'run', observe):
            self.capture()
        self.assertTrue(commands)
        self.assertTrue(all(c[0] == 'git' and ('cat-file' in c or '--version' in c) for c in commands))
        self.assertTrue(all('--no-replace-objects' in c and '--no-lazy-fetch' in c for c in commands))


if __name__ == '__main__':
    unittest.main()
