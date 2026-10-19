"""New site boundary only. SYNTHETIC inputs/authorities, socket/DNS forbidden.

POSIX cases use local private files under HOME, not cloud/Runner credentials.
Windows skips actual POSIX permission/link cases; hosted Linux runs them.
"""
import contextlib
from dataclasses import replace
import io
import json
import os
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import Mock, patch

import delivery_adapter as core
import delivery_site as site
from delivery_object_reader import BearerCredential
from delivery_provider import AuthorityAdapters, OperationalApplication
from test_delivery_adapter import Harness, SENSITIVE


class SiteFixture:
    def setUp(self):
        for name in ('socket', 'getaddrinfo', 'create_connection'):
            p = patch.object(socket, name, side_effect=AssertionError('NETWORK_FORBIDDEN'))
            p.start(); self.addCleanup(p.stop)
        self.h = Harness()
        self.config = site.SiteConfiguration(self.h.binding, Path.cwd() / 'NOT_A_REAL_INPUT_ROOT',
            'rendered.yaml', 'contract.yaml', Path.cwd() / 'NOT_A_REAL_GIT_ROOT',
            Path.cwd() / 'NOT_A_REAL_STORE_ROOT', 5)
        self.credential = BearerCredential('SYNTHETIC_NON_CREDENTIAL')
        self.adapters = AuthorityAdapters(*(Mock(side_effect=AssertionError('AUTHORITY_NOT_CALLED'))
                                             for _ in range(5)))


class SiteTests(SiteFixture, unittest.TestCase):
    def test_default_and_configured_status_do_not_build_or_read(self):
        with patch.object(site, 'build_application', side_effect=AssertionError('I/O_FORBIDDEN')):
            for config, code, status in ((None, 2, 'BLOCKED'),
                                        (self.config, 0, 'CONFIGURED_NOT_VERIFIED')):
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    self.assertEqual(code, site.main(['status'], config=config))
                report = json.loads(out.getvalue())
                self.assertEqual(status, report['status'])
                self.assertFalse(report['execution_authorized'])

    def test_unknown_arguments_are_not_echoed(self):
        out = io.StringIO()
        with contextlib.redirect_stderr(out), self.assertRaises(SystemExit) as e:
            site.main(['deploy', '--token', SENSITIVE])
        self.assertEqual(2, e.exception.code)
        self.assertNotIn(SENSITIVE, out.getvalue())
        self.assertIn('CLI_ARGUMENTS_REJECTED', out.getvalue())

    def test_action_mismatch_stops_before_loading(self):
        out = io.StringIO()
        with patch.object(site, 'build_application') as build, contextlib.redirect_stdout(out):
            self.assertEqual(2, site.main(['restore'], config=self.config))
            build.assert_not_called()
        self.assertEqual('SITE_ACTION_MISMATCH', json.loads(out.getvalue())['error_code'])

    def test_names_and_types_rejected_without_input_io(self):
        for changes in ({'rendered_name': '../escape'}, {'contract_name': '/absolute'},
                        {'contract_name': 'rendered.yaml'}, {'rendered_name': 'x/y'},
                        {'budget_seconds': float('inf')}, {'budget_seconds': 'invalid'},
                        {'finalize': 'yes'}, {'input_root': 'relative'},
                        {'source_root': Path('.')}, {'store_root': ''}):
            with self.subTest(changes=changes), patch.object(site.store, 'root_open') as opened:
                with self.assertRaises(site.SiteError):
                    site.build_application(replace(self.config, **changes),
                        credential=self.credential, adapters=self.adapters)
                opened.assert_not_called()

    def test_config_and_credential_repr_hide_private_values(self):
        self.assertNotIn('NOT_A_REAL_', repr(self.config))
        self.assertNotIn(self.credential.token, repr(self.credential))

    @unittest.skipIf(os.name == 'posix', 'non-POSIX guard only')
    def test_non_posix_execution_is_rejected(self):
        with self.assertRaisesRegex(site.SiteError, '^SITE_POSIX_REQUIRED$'):
            site.build_application(self.config, credential=self.credential, adapters=self.adapters)


@unittest.skipUnless(os.name == 'posix', 'real POSIX file semantics; Linux hosted CI required')
class PosixSiteTests(SiteFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory(prefix='p46-site-', dir=Path.home())
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for name in ('inputs', 'source', 'store'):
            (self.root / name).mkdir(mode=0o700)
        self.input = self.root / 'inputs'
        for name, raw in (('rendered.yaml', self.h.render), ('contract.yaml', self.h.contract)):
            p = self.input / name; p.write_bytes(raw); p.chmod(0o600)
        self.config = replace(self.config, input_root=self.input,
                              source_root=self.root / 'source', store_root=self.root / 'store')
        snapshot = Mock()
        self.checker = b'SYNTHETIC_CHECKER_BLOB'
        snapshot.blob.side_effect = lambda name: self.checker if name == site.CHECKER_PATH else self.h.contract
        p = patch.object(site, 'GitSnapshot', return_value=snapshot)
        self.git = p.start(); self.addCleanup(p.stop)
        p = patch.object(site, 'CHECKER_SHA256', core.digest(self.checker))
        p.start(); self.addCleanup(p.stop)

    def build(self):
        return site.build_application(self.config, credential=self.credential, adapters=self.adapters)

    def rejected(self, code=None):
        with patch.object(site, 'OperationalApplication') as app:
            with self.assertRaises(site.SiteError) as e:
                self.build()
            app.assert_not_called()
        if code is not None:
            self.assertEqual(code, str(e.exception))
        self.assertNotIn(str(self.root), str(e.exception))
        for n in site.ADAPTERS:
            getattr(self.adapters, n).assert_not_called()
        self.assertEqual([], list((self.root / 'store').iterdir()))

    def test_local_bytes_and_exact_handles_reach_existing_application(self):
        app = self.build()
        self.assertIs(type(app), OperationalApplication)
        self.assertIs(app._credential, self.credential)
        self.assertIs(app._adapters, self.adapters)
        self.assertEqual(self.h.render, app.inputs.rendered)
        self.assertEqual(self.h.contract, app.inputs.contract)
        self.git.assert_called_once_with(self.root / 'source', self.h.binding.source_commit)
        self.assertEqual([], list((self.root / 'store').iterdir()))

    def test_main_prepares_before_dispatch_and_does_not_run_a_real_app(self):
        with patch.object(site.cli, 'main', return_value=2) as dispatch:
            self.assertEqual(2, site.main(['preflight'], config=self.config,
                credential=self.credential, adapters=self.adapters))
        app = dispatch.call_args.kwargs['application']
        self.assertIs(type(app), OperationalApplication)
        self.assertIs(app._credential, self.credential)
        for n in site.ADAPTERS:
            getattr(self.adapters, n).assert_not_called()

    def test_hash_mismatch_stops_before_source_read_or_application(self):
        (self.input / 'rendered.yaml').write_bytes(b'SYNTHETIC_TAMPER')
        self.rejected('SITE_INPUT_HASH_MISMATCH')
        self.git.assert_not_called()

    def test_symlink_hardlink_fifo_are_rejected(self):
        p = self.input / 'rendered.yaml'; original = p.read_bytes()
        for kind in ('symlink', 'hardlink', 'fifo'):
            with self.subTest(kind=kind):
                p.unlink()
                if kind == 'symlink': p.symlink_to(self.input / 'contract.yaml')
                elif kind == 'hardlink': os.link(self.input / 'contract.yaml', p)
                else: os.mkfifo(p, mode=0o600)
                self.rejected()
                p.unlink(); p.write_bytes(original); p.chmod(0o600)

    def test_oversize_empty_and_unsafe_file_mode(self):
        p = self.input / 'rendered.yaml'
        for raw in (b'', b'x' * (site.MAX_BYTES + 1)):
            with self.subTest(size=len(raw)):
                p.write_bytes(raw); self.rejected('SITE_INPUT_SIZE_INVALID')
        p.write_bytes(self.h.render); p.chmod(0o640); self.rejected()

    def test_unsafe_root_and_source_symlink(self):
        self.input.chmod(0o770); self.rejected()
        self.input.chmod(0o700)
        link = self.root / 'source-link'; link.symlink_to(self.root / 'source', target_is_directory=True)
        self.config = replace(self.config, source_root=link)
        self.rejected('SITE_SOURCE_UNSAFE')

    def test_unsafe_store_is_rejected_without_claim_or_repair(self):
        (self.root / 'store').chmod(0o770)
        self.rejected()
        self.assertEqual(0o770, (self.root / 'store').stat().st_mode & 0o777)

    def test_input_directory_replacement_is_rejected(self):
        original = os.read
        changed = False
        def raced(fd, size):
            nonlocal changed
            raw = original(fd, size)
            if not changed:
                changed = True
                self.input.rename(self.root / 'old-inputs')
                self.input.mkdir(mode=0o700)
                for name, data in (('rendered.yaml', self.h.render), ('contract.yaml', self.h.contract)):
                    p = self.input / name; p.write_bytes(data); p.chmod(0o600)
            return raw
        with patch.object(site.os, 'read', side_effect=raced):
            self.rejected('SITE_INPUT_CHANGED')

    def test_source_mismatch_and_dirty_worktree_is_not_used(self):
        (self.root / 'source' / 'untrusted.py').write_text(SENSITIVE)
        self.git.return_value.blob.side_effect = lambda name: b'wrong'
        self.rejected('SITE_SOURCE_MISMATCH')

    def test_input_replacement_during_read_rejected(self):
        original = os.read
        changed = False
        def raced(fd, size):
            nonlocal changed
            raw = original(fd, size)
            if not changed:
                changed = True
                p = self.input / 'rendered.yaml'
                p.unlink(); p.write_bytes(self.h.render); p.chmod(0o600)
            return raw
        with patch.object(site.os, 'read', side_effect=raced):
            self.rejected('SITE_INPUT_CHANGED')

    def test_main_does_not_print_underlying_exception_or_path(self):
        out = io.StringIO()
        with patch.object(site.store, 'root_open', side_effect=OSError(SENSITIVE)), contextlib.redirect_stdout(out):
            self.assertEqual(2, site.main(['deploy'], config=self.config,
                credential=self.credential, adapters=self.adapters))
        self.assertNotIn(SENSITIVE, out.getvalue())
        self.assertNotIn(str(self.root), out.getvalue())
        report = json.loads(out.getvalue())
        self.assertEqual('SITE_INPUT_PREPARATION_FAILED', report['error_code'])
        self.assertFalse(report['claim_acquired'])


if __name__ == '__main__':
    unittest.main()
