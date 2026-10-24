"""SYNTHETIC per-run grants/archive/source responses; network/issuance forbidden.

Exercises real prepared materialization + real resolver + real archive parser.
Readers return native SourceOriginal fixtures, never production approvals/raw.
"""
from dataclasses import replace
import unittest
from unittest.mock import Mock, patch

import delivery_adapter as core
import delivery_fds_baseline_archive as archive
import delivery_fds_operating as operating
import delivery_fds_operating_policy as policy
import delivery_fds_existing_inputs as existing
import delivery_runtime_site as runtime
import delivery_job_sources as sources
from delivery_finish_observation import FOLLOWUPS
from delivery_adopted_authority import pin_identity, CodeAdoptedGrantResolver
from delivery_github_source import GitHubOriginalAdapter, SourceOriginal
import test_delivery_fds_operating_policy as operating_fixture
import test_delivery_fds_baseline_archive as archive_fixture
from test_delivery_job import CONTEXT


class DeferredBaselineTests(unittest.TestCase):
    def setUp(self):
        self.f = operating_fixture.OperatingPolicyTests('runTest'); self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        data = dict(zip(archive.OBJECT_FILES, (x+b'\n' for x in self.f.selection.baseline.objects)))
        data.update({x: b'SYNTHETIC_NOT_AUTHORITY\n' for x in archive.NOTES})
        self.raw = archive_fixture.archive(data)
        self.control_raw = b'SYNTHETIC_CURRENT_CONTROL'
        self.control = replace(policy.CONTROL_PIN, sha256=core.digest(self.control_raw))
        self.patch = patch.object(policy, 'CONTROL_PIN', self.control)
        self.patch.start(); self.addCleanup(self.patch.stop)
        def g(value): return replace(value, control_pin=self.control)
        s = self.f.selection
        self.grant = replace(g(s.baseline.grant), original_pin=replace(
            s.baseline.grant.original_pin, path='synthetic-baseline.tgz', sha256=core.digest(self.raw)))
        self.selected = replace(s, baseline=None, approval=g(s.approval),
            issuer_grant=g(s.issuer_grant), producer=replace(s.producer, grant=g(s.producer.grant)))
        self.finish = g(self.f.finish)
        self.reader = object.__new__(GitHubOriginalAdapter)
        self.reader.blob = Mock(side_effect=self.read)
        self.readers = runtime.SourceReaders(self.reader, self.reader)
        self.other_readers = []
        for _ in range(4):
            reader = object.__new__(GitHubOriginalAdapter)
            reader.blob = Mock(side_effect=AssertionError('WRONG_ROLE_READER'))
            self.other_readers.append(runtime.SourceReaders(reader, reader))
        self.roles = {'readers': tuple(self.other_readers) + (self.readers,)}
        self.calls = []
        self.hook = lambda pin, result: result

    def read(self, request, pin):
        self.calls.append((request, pin))
        raw = self.raw if pin == self.grant.original_pin else self.control_raw
        reference, revision, repo_id, creator, _ = pin_identity(pin)
        original = SourceOriginal(request, reference, revision, repo_id, creator, raw, ())
        return self.hook(pin, original)

    def build(self, **changes):
        args = self.f.args | {'selection': self.selected, 'finish_scope': self.finish,
                             'baseline_grant': self.grant, 'baseline_root': archive_fixture.ROOT}
        value = operating.assemble_observed_native_job(**(args | changes))
        self.addCleanup(value.build._parent._plan.close)
        return value

    def materialize(self, **changes):
        value = self.build(**changes); prepared = value.build._parent._plan
        prepared.bind_context(CONTEXT); credential, source = self.f.native_input()
        result = prepared.resolve(credential=credential, source=source,
                                  roles=self.roles, deadline=source.deadline)
        return result, prepared, credential, source

    def test_inert_metadata_selection_and_real_private_blob_to_full_baseline_plan(self):
        value = self.build()
        self.reader.blob.assert_not_called()
        self.f.mono.assert_not_called(); self.f.wall.assert_not_called(); self.f.issue.assert_not_called()
        result, prepared, credential, source = self.materialize()
        self.assertIsNotNone(result.baseline)
        self.assertIs(result.baseline.grant, self.grant)
        self.assertEqual(tuple(x+b'\n' for x in self.f.selection.baseline.objects), result.baseline.objects)
        self.assertIs(result.approval, self.selected.approval)
        self.assertIs(result.producer, self.selected.producer)
        self.assertIs(result.subject, self.selected.subject)
        self.assertIs(result, existing.validate_existing_plan(self.f.config, result))
        wire = sources.SourceRoleWiring(binding=self.f.config.binding,
            routes=self.f.args['role_routes'], finish=sources.ObservationRoute(self.finish, FOLLOWUPS),
            producer_repository=self.f.repo, wall=self.f.wall, monotonic=self.f.mono)
        wire.check_plan(result)  # Full producer/baseline/finish route contract retained.
        self.assertEqual([self.grant.original_pin, self.control], [p for _, p in self.calls])
        request = self.calls[0][0]
        self.assertIs(credential, request.credential)
        self.assertIs(request, self.calls[1][0]); self.assertEqual(source.deadline, request.deadline)
        self.assertEqual('CONSUMED', source._state); self.assertIsNone(source._native)
        with self.assertRaises(runtime.RuntimeSiteError):
            prepared.resolve(credential=credential, source=source, roles=self.roles, deadline=source.deadline)
        self.assertEqual(2, self.reader.blob.call_count)
        for other in self.other_readers: other.original.blob.assert_not_called()

    def test_xor_missing_grant_root_and_malformed_root_stop_before_read_or_clock(self):
        for change in ({'baseline_grant': None}, {'baseline_root': None},
                       {'baseline_root': '../synthetic'}, {'baseline_grant': {}},
                       {'selection': self.f.selection}, {'selection': None},
                       {'baseline_grant': replace(self.grant, binding=replace(
                           self.grant.binding, run_id='SYNTHETIC_OTHER'))},
                       {'baseline_grant': replace(self.grant, control_pin=replace(
                           self.control, anchor_commit='e'*40))}):
            with self.subTest(change=tuple(change)), self.assertRaises(
                    (policy.OperatingPolicyError, archive.BaselineArchiveError)):
                self.build(**change)
        self.reader.blob.assert_not_called(); self.f.mono.assert_not_called(); self.f.wall.assert_not_called()

    def test_expired_baseline_grant_prevents_source_body_read(self):
        with self.assertRaises(runtime.RuntimeSiteError):
            self.materialize(baseline_grant=replace(self.grant, expires_at=1001))
        self.reader.blob.assert_not_called()

    def test_wrong_original_body_request_or_control_never_returns_an_admitted_plan(self):
        hooks = [lambda pin, result: replace(result, body=b'SYNTHETIC_FOREIGN_BYTES')
                 if pin == self.grant.original_pin else result,
                 lambda pin, result: replace(result, request=replace(result.request, deadline=1004))
                 if pin == self.grant.original_pin else result,
                 lambda pin, result: replace(result, body=b'SYNTHETIC_WITHDRAWN_CONTROL')
                 if pin == self.control else result]
        for hook in hooks:
            self.hook = hook
            with self.assertRaises(runtime.RuntimeSiteError): self.materialize()
        self.f.issue.assert_not_called()

    def test_reader_timeout_close_or_parse_expiry_never_extends_deadline(self):
        for mode in ('timeout', 'closed', 'expired', 'parse-expired', 'interrupt', 'exit'):
            self.reader.blob.reset_mock()
            value = self.build(); prepared = value.build._parent._plan
            prepared.bind_context(CONTEXT); credential, source = self.f.native_input()
            def hook(pin, result):
                if pin == self.grant.original_pin:
                    if mode == 'timeout': raise TimeoutError('SYNTHETIC_PRIVATE_READER_ERROR')
                    if mode == 'closed': source.close()
                    if mode == 'expired': self.f.mono.return_value = source.deadline
                    if mode == 'interrupt': raise KeyboardInterrupt('SYNTHETIC_PRIVATE_INTERRUPT')
                    if mode == 'exit': raise SystemExit(7)
                return result
            self.hook = hook
            real_connect = archive.connect_baseline_archive
            def parse(*args, **kwargs):
                result = real_connect(*args, **kwargs)
                if mode == 'parse-expired': self.f.mono.return_value = source.deadline
                return result
            with patch.object(archive, 'connect_baseline_archive', side_effect=parse):
                error = (KeyboardInterrupt if mode == 'interrupt' else
                         SystemExit if mode == 'exit' else runtime.RuntimeSiteError)
                with self.assertRaises(error) as caught:
                    prepared.resolve(credential=credential, source=source,
                                     roles=self.roles, deadline=source.deadline)
            self.assertIsNone(caught.exception.__context__)
            self.assertNotIn('SYNTHETIC_PRIVATE', repr(caught.exception))
            if mode == 'exit': self.assertEqual(130, caught.exception.code)
            if mode == 'expired': self.assertEqual(1, self.reader.blob.call_count)
            if mode == 'parse-expired': self.assertEqual(2, self.reader.blob.call_count)
            self.f.mono.return_value = 1000

    def test_roles_require_exact_five_readers_and_do_not_accept_another_route_kind(self):
        for readers in (None, (), [self.readers]*5, (self.readers,)*4, (self.readers,)*4+(Mock(),)):
            self.roles = {'readers': readers}
            with self.assertRaises(runtime.RuntimeSiteError): self.materialize()
        self.reader.blob.assert_not_called()

    def test_validly_pinned_malformed_archive_is_not_evidence_or_an_empty_baseline(self):
        self.raw = b'SYNTHETIC_NOT_GZIP'
        grant = replace(self.grant, original_pin=replace(self.grant.original_pin,
                                                       sha256=core.digest(self.raw)))
        self.grant = grant
        with self.assertRaises(runtime.RuntimeSiteError): self.materialize(baseline_grant=grant)
        self.f.issue.assert_not_called()

    def test_internal_resolver_closes_on_success_and_failure_without_closing_borrowed_readers(self):
        close = CodeAdoptedGrantResolver.close
        for failed in (False, True):
            self.hook = (lambda pin, result: replace(result, body=b'SYNTHETIC_BAD_CONTROL')
                         if failed and pin == self.control else result)
            with patch.object(CodeAdoptedGrantResolver, 'close', autospec=True,
                              side_effect=close) as own_close, \
                 patch.object(self.reader, 'close', create=True) as borrowed_close:
                if failed:
                    with self.assertRaises(runtime.RuntimeSiteError): self.materialize()
                else:
                    self.materialize()
            own_close.assert_called_once(); borrowed_close.assert_not_called()


if __name__ == '__main__': unittest.main()
