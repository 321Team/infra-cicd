"""SYNTHETIC comment/control/archive only; network and issuance forbidden.

Composes the real materializer/resolver/parser. POSIX file cases run on hosted
Linux; Windows never pretends to verify Unix permissions or descriptor safety.
"""
from dataclasses import replace
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import delivery_adapter as core
import delivery_fds_baseline_archive as archive
import delivery_fds_operating as operating
import delivery_fds_operating_policy as policy
import delivery_fds_private_baseline as private
import delivery_runtime_site as runtime
from delivery_adopted_authority import CodeAdoptedGrantResolver, pin_identity
from delivery_github_source import CommentPin, SourceOriginal
from delivery_object_reader import BearerCredential
import test_delivery_fds_deferred_baseline as fixture
import test_delivery_fds_baseline_archive as archive_fixture
from test_delivery_job import CONTEXT


class PrivateFixture:
    def setUp(self):
        self.d = fixture.DeferredBaselineTests('runTest'); self.d.setUp()
        self.addCleanup(self.d.doCleanups)
        self.metadata = b'SYNTHETIC_REVIEWED_ARCHIVE_ASSOCIATION'
        self.grant = replace(self.d.grant, original_pin=CommentPin(
            self.d.grant.original_pin.repository, 9, 123, 456,
            '2026-01-01T00:00:00Z', core.digest(self.metadata)))
        self.file = private.PrivateBaselineFile('synthetic-fresh.tgz', core.digest(self.d.raw))
        self.events = []
        self.hook = lambda original: original
        def comment(request, pin):
            self.events.append('metadata')
            reference, revision, repo, creator, _ = pin_identity(pin)
            return self.hook(SourceOriginal(request, reference, revision, repo, creator,
                                            self.metadata, ()))
        self.d.reader.comment = Mock(side_effect=comment)
        def blob(request, pin):
            self.events.append('control')
            return self.d.read(request, pin)
        self.d.reader.blob.side_effect = blob
        self.file_patch = patch.object(private, '_read_archive', side_effect=self.read_file)
        self.read_file_mock = self.file_patch.start(); self.addCleanup(self.file_patch.stop)

    def read_file(self, config, selected):
        self.events.append('file')
        self.assertIs(self.file, selected)
        return self.d.raw

    def args(self, changes):
        return {'baseline_grant': self.grant, 'baseline_file': self.file} | changes

    def build(self, **changes):
        return self.d.build(**self.args(changes))

    def materialize(self, **changes):
        return self.d.materialize(**self.args(changes))


class PrivateBaselineTests(PrivateFixture, unittest.TestCase):
    def test_inert_selection_then_authenticated_private_file_into_exact_six_plan(self):
        self.build()
        self.assertEqual([], self.events)
        self.d.f.mono.assert_not_called(); self.d.f.wall.assert_not_called()
        result, prepared, credential, source = self.materialize()
        self.assertEqual(['metadata', 'control', 'file', 'metadata', 'control'], self.events)
        self.assertEqual(tuple(x+b'\n' for x in self.d.f.selection.baseline.objects),
                         result.baseline.objects)
        self.assertIs(self.grant, result.baseline.grant)
        self.assertIs(self.d.selected.producer, result.producer)
        self.assertEqual(self.d.f.selection.producer.facts.previous_images,
                         result.producer.facts.previous_images)
        request = self.d.reader.comment.call_args_list[0].args[0]
        self.assertIs(request, self.d.reader.comment.call_args_list[1].args[0])
        self.assertIs(credential, request.credential)
        self.assertEqual(source.deadline, request.deadline)
        self.assertNotEqual(self.grant.original_pin.body_sha256, self.file.sha256)
        self.assertTrue(all(p == self.d.control for _, p in self.d.calls))
        with self.assertRaises(runtime.RuntimeSiteError):
            prepared.resolve(credential=credential, source=source,
                             roles=self.d.roles, deadline=source.deadline)
        self.assertEqual(1, self.read_file_mock.call_count)
        self.d.f.issue.assert_not_called()

    def test_malformed_or_mixed_modes_rejected_before_read_or_clock(self):
        for change in ({'baseline_file': None}, {'baseline_file': {}},
                {'baseline_file': replace(self.file, name='../x')},
                {'baseline_file': replace(self.file, name='x/y')},
                {'baseline_file': replace(self.file, name='x\\y')},
                {'baseline_file': replace(self.file, sha256='not-a-digest')},
                {'baseline_root': None}, {'baseline_grant': self.d.grant},
                {'baseline_grant': None, 'baseline_root': None},
                {'selection': self.d.f.selection}):
            with self.subTest(change=tuple(change)), self.assertRaises(
                    (policy.OperatingPolicyError, archive.BaselineArchiveError)):
                self.build(**change)
        self.assertEqual([], self.events)
        self.read_file_mock.assert_not_called(); self.d.f.wall.assert_not_called()

    def test_private_selection_repr_does_not_expose_name_or_digest(self):
        self.assertNotIn(self.file.name, repr(self.file))
        self.assertNotIn(self.file.sha256, repr(self.file))

    def test_memory_baseline_cannot_silently_ignore_a_file_without_deferred_grant(self):
        memory = replace(self.d.selected, baseline=replace(self.d.f.selection.baseline,
            grant=replace(self.d.f.selection.baseline.grant, control_pin=self.d.control)))
        self.d.build(selection=memory, baseline_grant=None, baseline_root=None)
        with self.assertRaises(policy.OperatingPolicyError):
            self.d.build(selection=memory, baseline_grant=None, baseline_root=None,
                         baseline_file=self.file)
        self.assertEqual([], self.events)
        self.read_file_mock.assert_not_called()

    def test_invalid_input_store_overlap_prevents_source_or_file_access(self):
        # Exercise the new boundary directly; normal operating policy already
        # rejects the invalid configuration before prepared materialization.
        with self.assertRaises(archive.BaselineArchiveError):
            private.connect_authenticated_private_baseline(
                replace(self.d.f.config, input_root=self.d.f.config.store_root),
                replace(self.d.f.plan, baseline=None), grant=self.grant,
                root_name=archive_fixture.ROOT, selected=self.file, readers=self.d.readers,
                credential=BearerCredential('SYNTHETIC_NON_CREDENTIAL'), deadline=1005,
                wall=self.d.f.wall, monotonic=self.d.f.mono)
        self.assertEqual([], self.events)
        self.read_file_mock.assert_not_called()

    def test_expiry_metadata_or_control_rejection_prevents_private_file_read(self):
        with self.assertRaises(runtime.RuntimeSiteError):
            self.materialize(baseline_grant=replace(self.grant, expires_at=1001))
        self.assertEqual([], self.events)
        self.hook = lambda value: replace(value, body=b'SYNTHETIC_FOREIGN_METADATA')
        with self.assertRaises(runtime.RuntimeSiteError): self.materialize()
        self.hook = lambda value: value
        self.d.hook = lambda pin, value: replace(value, body=b'SYNTHETIC_WITHDRAWN_CONTROL')
        with self.assertRaises(runtime.RuntimeSiteError): self.materialize()
        self.read_file_mock.assert_not_called()

    def test_comment_edit_or_control_withdrawal_after_parse_rejects_plan(self):
        for mode in ('edit', 'control'):
            self.events.clear()
            self.hook = lambda value: (replace(value, revision='2026-01-02T00:00:00Z')
                if mode == 'edit' and self.events.count('metadata') == 2 else value)
            self.d.hook = lambda pin, value: (replace(value, body=b'SYNTHETIC_WITHDRAWAL')
                if mode == 'control' and self.events.count('control') == 2 else value)
            with self.subTest(mode=mode), self.assertRaises(runtime.RuntimeSiteError):
                self.materialize()
            self.assertIn('file', self.events)
        self.d.f.issue.assert_not_called()

    def test_wrong_request_or_binding_metadata_cannot_authorize_file_access(self):
        for change in ({'request': None}, {'repository_id': 987}, {'comment_creator_id': 987},
                       {'body_authorship_verified': True}):
            if 'body_authorship_verified' in change:
                def hook(value):
                    object.__setattr__(value, 'body_authorship_verified', True)
                    return value
                self.hook = hook
            else:
                self.hook = lambda value: replace(value, **change)
            with self.subTest(change=change), self.assertRaises(runtime.RuntimeSiteError):
                self.materialize()
        self.read_file_mock.assert_not_called()

    def test_file_or_parse_time_counts_against_same_deadline(self):
        real = archive.connect_baseline_archive
        for mode in ('file', 'parse'):
            self.events.clear()
            def read(config, selected):
                raw = self.read_file(config, selected)
                if mode == 'file': self.d.f.mono.return_value = 1005
                return raw
            def parse(*args, **kwargs):
                result = real(*args, **kwargs)
                if mode == 'parse': self.d.f.mono.return_value = 1005
                return result
            self.read_file_mock.side_effect = read
            with patch.object(archive, 'connect_baseline_archive', side_effect=parse), \
                 self.assertRaises(runtime.RuntimeSiteError):
                self.materialize()
            self.assertEqual(1, self.events.count('metadata'))
            self.d.f.mono.return_value = 1000

    def test_missing_bad_digest_or_archive_has_no_old_blob_fallback(self):
        for failure in (FileNotFoundError('SYNTHETIC_PRIVATE_PATH'),
                        ValueError('SYNTHETIC_HASH_MISMATCH'), b'SYNTHETIC_NOT_TAR'):
            self.events.clear()
            self.read_file_mock.side_effect = failure if isinstance(failure, Exception) else None
            self.read_file_mock.return_value = failure
            with self.assertRaises(runtime.RuntimeSiteError) as caught: self.materialize()
            self.assertIsNone(caught.exception.__context__)
            self.assertNotIn('SYNTHETIC_', str(caught.exception))
            self.assertEqual(['metadata', 'control'], self.events)
        self.assertTrue(all(p == self.d.control for _, p in self.d.calls))

    def test_owned_resolver_closes_borrowed_readers_stay_open_and_interrupts_are_clean(self):
        close = CodeAdoptedGrantResolver.close
        for failure, error in ((None, None), (RuntimeError('SYNTHETIC_PRIVATE'), runtime.RuntimeSiteError),
                               (KeyboardInterrupt('SYNTHETIC_PRIVATE'), KeyboardInterrupt),
                               (SystemExit(7), SystemExit)):
            self.read_file_mock.side_effect = failure
            self.read_file_mock.return_value = self.d.raw
            with patch.object(CodeAdoptedGrantResolver, 'close', autospec=True,
                    side_effect=close) as owned, patch.object(self.d.reader, 'close', create=True) as borrowed:
                if error is None: self.materialize()
                else:
                    with self.assertRaises(error) as caught: self.materialize()
                    self.assertIsNone(caught.exception.__context__)
                    if error is SystemExit: self.assertEqual(130, caught.exception.code)
                owned.assert_called_once(); borrowed.assert_not_called()

    def test_cleanup_failure_redacts_body_and_preserves_original_interrupt(self):
        for fail_read, fail_close, error in ((None, OSError('PRIVATE_PATH'), runtime.RuntimeSiteError),
                (None, SystemExit(9), SystemExit), (KeyboardInterrupt('RAW'), OSError('RAW'), KeyboardInterrupt)):
            self.read_file_mock.side_effect = fail_read
            self.read_file_mock.return_value = self.d.raw
            with patch.object(CodeAdoptedGrantResolver, 'close', side_effect=fail_close), \
                 self.assertRaises(error) as caught:
                self.materialize()
            self.assertIsNone(caught.exception.__context__)
            self.assertNotIn('PRIVATE', str(caught.exception))
            self.assertNotIn('RAW', str(caught.exception))
            if error is SystemExit: self.assertEqual(130, caught.exception.code)


@unittest.skipUnless(os.name == 'posix', 'actual POSIX file/FD checks require Linux')
class PosixPrivateBaselineTests(PrivateFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.file_patch.stop()
        self.tmp = tempfile.TemporaryDirectory(prefix='p46-private-baseline-', dir=Path.home())
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.path = self.root / self.file.name
        self.path.write_bytes(self.d.raw); self.path.chmod(0o600)
        self.config = replace(self.d.f.config, input_root=self.root)

    def test_actual_private_file_reaches_parser_and_all_fds_plan_checks(self):
        result, *_ = self.materialize(config=self.config)
        self.assertEqual(tuple(x+b'\n' for x in self.d.f.selection.baseline.objects),
                         result.baseline.objects)

    def test_missing_symlink_hardlink_fifo_and_permissive_file_reject(self):
        for mode in ('missing', 'symlink', 'hardlink', 'fifo', 'permissive'):
            if self.path.exists() or self.path.is_symlink(): self.path.unlink()
            other = self.root / 'other'
            if other.exists(): other.unlink()
            if mode == 'symlink':
                other.write_bytes(self.d.raw); other.chmod(0o600); self.path.symlink_to(other)
            elif mode == 'hardlink':
                other.write_bytes(self.d.raw); other.chmod(0o600); self.path.hardlink_to(other)
            elif mode == 'fifo': os.mkfifo(self.path, 0o600)
            elif mode == 'permissive': self.path.write_bytes(self.d.raw); self.path.chmod(0o644)
            with self.subTest(mode=mode), self.assertRaises(runtime.RuntimeSiteError):
                self.materialize(config=self.config)

    def test_directory_mode_and_actual_digest_mismatch_reject(self):
        self.root.chmod(0o755)
        with self.assertRaises(runtime.RuntimeSiteError): self.materialize(config=self.config)
        self.root.chmod(0o700)
        with self.assertRaises(runtime.RuntimeSiteError):
            self.materialize(config=self.config, baseline_file=replace(self.file, sha256='f'*64))

    def test_root_descriptor_closed_even_when_file_reader_fails(self):
        real_open = private.store.root_open
        opened = []
        def observe(*args):
            result = real_open(*args); opened.append(result[1]); return result
        with patch.object(private.store, 'root_open', side_effect=observe), \
             patch.object(private.site, 'read_input', side_effect=KeyboardInterrupt('PRIVATE')), \
             self.assertRaises(KeyboardInterrupt):
            self.materialize(config=self.config)
        self.assertTrue(opened)
        for fd in opened:
            with self.assertRaises(OSError): os.fstat(fd)

    def test_root_replacement_check_failure_never_returns_a_plan_and_closes_root_fd(self):
        real_open = private.store.root_open
        opened = []
        def observe(*args):
            result = real_open(*args); opened.append(result[1]); return result
        with patch.object(private.store, 'root_open', side_effect=observe), \
             patch.object(private.site, 'root_stable', side_effect=ValueError('PRIVATE_ROOT')), \
             self.assertRaises(runtime.RuntimeSiteError):
            self.materialize(config=self.config)
        self.assertEqual(1, len(opened))
        with self.assertRaises(OSError): os.fstat(opened[0])


if __name__ == '__main__': unittest.main()
