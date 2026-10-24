"""SYNTHETIC terminal boundary; no actual prompt, network or host execution."""
import io
import unittest
import warnings
from unittest.mock import Mock, patch

import delivery_adapter as core
import delivery_terminal_login as terminal
import delivery_oauth_login as login
import delivery_job_site as declaration
import test_delivery_transport as fixtures
import test_delivery_oauth_login as oauth


class TerminalInputTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.TransportTests('runTest'); self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.args = dict(origin=oauth.ORIGIN, ca_pem=fixtures.CA,
            ca_sha256=core.digest(fixtures.CA), endpoint_ip='192.0.2.20',
            username='synthetic-user', deadline=20, monotonic=lambda:10)
        self.tty = Mock(); self.tty.isatty.return_value = True
        p = patch.object(terminal.sys, 'stdin', self.tty); p.start(); self.addCleanup(p.stop)
        p = patch.object(login.ssl, 'SSLContext', return_value=self.f.context)
        p.start(); self.addCleanup(p.stop)

    def test_success_owns_one_password_reference_without_login_or_clock_renewal(self):
        with patch.object(terminal.getpass, 'getpass', return_value=oauth.PASSWORD) as prompt:
            value = terminal.read_login_provider(**self.args)
        self.addCleanup(value.close)
        self.assertIs(type(value), terminal.BasicChallengeLogin)
        self.assertEqual(oauth.PASSWORD, value._password)
        self.assertEqual('NOT_ATTEMPTED', value.status()['login'])
        self.assertEqual(20, value._end)
        prompt.assert_called_once_with('ROSA 본인 전용 비밀번호: ')
        self.assertNotIn(oauth.PASSWORD, repr(value))
        value.close(); self.assertIsNone(value._password)
        self.assertIsNone(declaration.SITE)

    def test_nonterminal_and_expired_budget_never_prompt(self):
        for stdin, clock in ((io.StringIO(oauth.PASSWORD), lambda:10),
                             (None, lambda:10), (self.tty, lambda:20)):
            with self.subTest(stdin=type(stdin).__name__), patch.object(terminal.sys, 'stdin', stdin), \
                 patch.object(terminal.getpass, 'getpass') as prompt:
                with self.assertRaises(terminal.TerminalInputError):
                    terminal.read_login_provider(**(self.args | {'monotonic':clock}))
                prompt.assert_not_called()

    def test_echo_fallback_warning_is_error_before_reading_input(self):
        fallback_read = Mock(side_effect=AssertionError('ECHO_INPUT_FORBIDDEN'))
        def fallback(*unused):
            warnings.warn('SYNTHETIC echo fallback', terminal.getpass.GetPassWarning)
            return fallback_read()
        with patch.object(terminal.getpass, 'getpass', side_effect=fallback):
            with self.assertRaises(terminal.TerminalInputError): terminal.read_login_provider(**self.args)
        fallback_read.assert_not_called()

    def test_input_time_consumes_budget_and_clock_reversal_is_rejected(self):
        for times in ([10,20], [10,9], [10,float('nan')]):
            with self.subTest(times=times), patch.object(terminal.getpass,'getpass',return_value=oauth.PASSWORD), \
                 patch.object(terminal,'BasicChallengeLogin') as create:
                with self.assertRaises(terminal.TerminalInputError):
                    terminal.read_login_provider(**(self.args|{'monotonic':Mock(side_effect=times)}))
                create.assert_not_called()

    def test_input_failure_cancel_and_constructor_failure_hide_private_context(self):
        cases=[(EOFError(oauth.PASSWORD),terminal.TerminalInputError),
               (KeyboardInterrupt(oauth.PASSWORD),KeyboardInterrupt),
               (SystemExit(oauth.PASSWORD),SystemExit)]
        for error, expected in cases:
            with self.subTest(error=type(error).__name__), patch.object(terminal.getpass,'getpass',side_effect=error):
                with self.assertRaises(expected) as caught: terminal.read_login_provider(**self.args)
            self.assertIsNone(caught.exception.__context__)
            self.assertNotIn(oauth.PASSWORD,str(caught.exception))
        for password in ('', '\n', oauth.PASSWORD):
            with patch.object(terminal.getpass,'getpass',return_value=password), \
                 patch.object(terminal,'BasicChallengeLogin',side_effect=ValueError(oauth.PASSWORD)):
                try: terminal.read_login_provider(**self.args)
                except terminal.TerminalInputError as exc:
                    self.assertIsNone(exc.__context__)
                    self.assertNotIn(oauth.PASSWORD,str(exc))
                    tb=exc.__traceback__
                    while tb:
                        if tb.tb_frame.f_code is terminal.read_login_provider.__code__:
                            self.assertIsNone(tb.tb_frame.f_locals['password'])
                            self.assertIsNone(tb.tb_frame.f_locals['provider'])
                        tb=tb.tb_next
                else: self.fail('constructor failure must stop')


if __name__ == '__main__': unittest.main()
