"""Explicit local hidden input -> existing memory login provider.

No import-time I/O, credential file/env/argv, login, token issue or SSH. The
caller must already have reviewed OAuth trust and an interactive terminal.
This is a local input step, not a Runtime grant or a configured operating SITE.
"""
import getpass
import sys
import warnings

import delivery_adapter as core
from delivery_oauth_login import BasicChallengeLogin


class TerminalInputError(RuntimeError):
    """Public fixed code; input and underlying exceptions never escape."""


def read_login_provider(*, origin, ca_pem, ca_sha256, endpoint_ip, username,
                        deadline, monotonic):
    """Prompt once explicitly; own the password only in the returned provider.

    Non-TTY and getpass echo fallback are refused. Input waiting is interactive
    and not forcibly interrupted at deadline; the original deadline is checked
    before and after input, never renewed. No retry or automatic send follows.
    Python reference release is not memory zeroization.
    """
    password = provider = error = None
    try:
        if sys.stdin is None or not sys.stdin.isatty():
            raise TerminalInputError('P2_LOGIN_TERMINAL_REQUIRED')
        before = monotonic()
        if not (core.finite(deadline) and core.finite(before) and before < deadline):
            raise TerminalInputError('P2_LOGIN_INPUT_EXPIRED')
        with warnings.catch_warnings():
            warnings.simplefilter('error', getpass.GetPassWarning)
            password = getpass.getpass('ROSA 본인 전용 비밀번호: ')
        after = monotonic()
        if not (core.finite(after) and before <= after < deadline):
            raise TerminalInputError('P2_LOGIN_INPUT_EXPIRED')
        provider = BasicChallengeLogin(origin=origin, ca_pem=ca_pem,
            ca_sha256=ca_sha256, endpoint_ip=endpoint_ip, username=username,
            password=password, deadline=deadline, monotonic=monotonic)
    except BaseException as exc:
        if isinstance(exc, KeyboardInterrupt):
            error = KeyboardInterrupt()
        elif isinstance(exc, SystemExit):
            error = SystemExit(130)
        else:
            # No arbitrary exception text, username or prompt input in reports.
            error = TerminalInputError('P2_LOGIN_TERMINAL_INPUT_FAILED')
    finally:
        password = None
    if error is not None:
        provider = None
        raise error from None
    return provider
