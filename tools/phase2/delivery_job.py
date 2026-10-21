"""Manual P2 job -> reviewed site -> existing runtime, with no default authority.

Native Actions metadata is trace/routing, never a grant. No token/env/JSON/file
authority loader or credential broker is installed. Trusted site code owns
partial construction; this driver closes only transferred exact owned handles.
"""
import contextlib
from dataclasses import dataclass, field
import io
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys

import delivery_cli as cli
import delivery_runtime_site as runtime
import delivery_site as site
from delivery_credential_channel import PipeCredentialReceiver
from delivery_source_session import PrivateSourceSession

REPOSITORY = '321Team/infra-cicd'
CODE_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT = object()
MAX_OUTPUT = 128 * 1024
COMMANDS = ('status', 'gate', 'preflight', 'deploy', 'restore')


class JobError(RuntimeError):
    """Fixed codes only; no exception text or source/credential paths."""


def need(ok, code):
    if not ok:
        raise JobError(code)


@dataclass(frozen=True)
class JobContext:
    repository: str
    event: str
    ref: str
    workflow_sha: str
    run_id: str
    attempt: int

    def check(self):
        need(self.repository == REPOSITORY and self.event == 'workflow_dispatch'
             and self.ref == 'refs/heads/main' and type(self.workflow_sha) is str
             and re.fullmatch(r'[a-f0-9]{40}', self.workflow_sha)
             and type(self.run_id) is str and re.fullmatch(r'[1-9][0-9]{0,19}', self.run_id)
             and type(self.attempt) is int and self.attempt == 1, 'P2_JOB_CONTEXT_REJECTED')


def native_context():
    # These native metadata keys contain no credential and confer no authority.
    attempt = os.environ.get('GITHUB_RUN_ATTEMPT', '')
    need(attempt == '1', 'P2_JOB_CONTEXT_REJECTED')
    result = JobContext(os.environ.get('GITHUB_REPOSITORY', ''),
        os.environ.get('GITHUB_EVENT_NAME', ''), os.environ.get('GITHUB_REF', ''),
        os.environ.get('GITHUB_SHA', ''), os.environ.get('GITHUB_RUN_ID', ''), 1)
    result.check()
    return result


def code_revision():
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith('GIT_')}
    env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull,
               GIT_OPTIONAL_LOCKS='0', GIT_TERMINAL_PROMPT='0', GIT_NO_LAZY_FETCH='1')
    result = subprocess.run(['git', '--no-replace-objects', '-C', str(CODE_ROOT),
        'rev-parse', '--verify', 'HEAD'], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, env=env, timeout=10, check=False)
    need(result.returncode == 0, 'P2_JOB_CODE_REVISION_REJECTED')
    return result.stdout.decode('ascii').strip()


@dataclass(frozen=True)
class RunnerIdentity:
    hostname: str
    account: str
    uid: int

    def check(self):
        need(type(self.hostname) is str and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.-]{0,62}', self.hostname)
             and type(self.account) is str and re.fullmatch(r'[a-z_][a-z0-9_-]{0,63}', self.account)
             and type(self.uid) is int and self.uid > 0, 'P2_JOB_RUNNER_REJECTED')


def local_runner():
    need(sys.platform == 'linux', 'P2_JOB_RUNNER_REJECTED')
    import pwd
    uid = os.geteuid()
    return RunnerIdentity(socket.gethostname().split('.')[0], pwd.getpwuid(uid).pw_name, uid)


@dataclass(frozen=True)
class JobSite:
    runner: RunnerIdentity = field(repr=False)
    build: object = field(repr=False)

    def check(self):
        need(type(self.runner) is RunnerIdentity and callable(self.build), 'P2_JOB_SITE_REJECTED')
        self.runner.check()


@dataclass(frozen=True)
class RuntimeInvocation:
    context: JobContext = field(repr=False)
    config: object = field(repr=False)
    plan: object = field(repr=False)
    finish_slot: object = field(repr=False)
    receiver: object = field(repr=False)
    handoff_deadline: float = field(repr=False)
    readers: object = field(default=None, repr=False)
    source_session: object = field(default=None, repr=False)
    finish_capture: object = field(default=None, repr=False)
    producer_pins: object = field(default=None, repr=False)
    producer_run_reader: object = field(default=None, repr=False)
    finish_collection: object = field(default=None, repr=False)
    finish_observation: object = field(default=None, repr=False)
    _used: bool = field(default=False, init=False, repr=False)
    _closed: bool = field(default=False, init=False, repr=False)
    _cleanup_ok: bool = field(default=True, init=False, repr=False)

    def check(self, context, command):
        need(not self._used and not self._closed and type(self.context) is JobContext
             and self.context == context, 'P2_JOB_INVOCATION_REJECTED')
        need(type(self.config) is site.SiteConfiguration and type(self.plan) is runtime.RuntimeAuthorityPlan
             and type(self.receiver) is PipeCredentialReceiver
             and (self.source_session is None or type(self.source_session) is PrivateSourceSession),
             'P2_JOB_INVOCATION_REJECTED')
        site.configuration_checked(self.config)
        need(self.plan.approval.binding == self.config.binding
             and command in ('preflight', self.config.binding.action), 'P2_JOB_ACTION_REJECTED')

    def kwargs(self):
        return {name: getattr(self, name) for name in ('config', 'plan', 'finish_slot', 'receiver',
            'handoff_deadline', 'readers', 'source_session', 'finish_capture', 'producer_pins',
            'producer_run_reader', 'finish_collection', 'finish_observation')}

    def close(self):
        if self._closed:
            return self._cleanup_ok
        object.__setattr__(self, '_closed', True)
        for handle, exact in ((self.source_session, PrivateSourceSession),
                              (self.receiver, PipeCredentialReceiver)):
            if type(handle) is exact:
                try:
                    result = handle.close()
                    if exact is PrivateSourceSession and result is not True:
                        object.__setattr__(self, '_cleanup_ok', False)
                except BaseException:
                    object.__setattr__(self, '_cleanup_ok', False)
        return self._cleanup_ok


def configured(declaration):
    if declaration is _DEFAULT:
        from delivery_job_site import SITE
        declaration = SITE
    need(type(declaration) is JobSite, 'P2_JOB_UNCONFIGURED')
    declaration.check()
    return declaration


def context_checked(context, revision):
    need(type(context) is JobContext, 'P2_JOB_CONTEXT_REJECTED')
    context.check()
    need(revision() == context.workflow_sha, 'P2_JOB_CODE_REVISION_REJECTED')


def blocked(code, *, started=False):
    return {'schema': 'P2-CICD-job-1', 'status': 'BLOCKED', 'error_code': code,
            'execution_authorized': False, 'runtime': 'NOT_VERIFIED' if started else 'NOT_RUN',
            'dispatch_configured': False, 'fence': 'INSPECT_REQUIRED' if started else 'NOT_ACQUIRED'}


def safe_error(error):
    # Only our exact source-controlled literals, not a regex or arbitrary args.
    codes = {'P2_JOB_CONTEXT_REJECTED', 'P2_JOB_CODE_REVISION_REJECTED', 'P2_JOB_RUNNER_REJECTED',
        'P2_JOB_SITE_REJECTED', 'P2_JOB_UNCONFIGURED', 'P2_JOB_INVOCATION_REJECTED',
        'P2_JOB_ACTION_REJECTED', 'P2_JOB_EXECUTE_REQUIRED', 'P2_JOB_OUTPUT_REJECTED'}
    value = error.args[0] if type(error) is JobError and len(error.args) == 1 else None
    return value if type(value) is str and value in codes else 'P2_JOB_PREPARATION_FAILED'


def workflow_gate(*, declaration=_DEFAULT, context=None, revision=code_revision):
    """Hosted declaration gate. Never builds site/reads credentials/calls servers.

    dispatch_configured is code-routing metadata, never approval/readiness.
    Missing actual SITE deliberately prevents scheduling the self-hosted job.
    """
    try:
        context = native_context() if context is None else context
        context_checked(context, revision)
        configured(declaration)
        return {'schema': 'P2-CICD-job-1', 'status': 'CONFIGURED_NOT_VERIFIED',
                'execution_authorized': False, 'runtime': 'NOT_RUN', 'dispatch_configured': True}
    except Exception as error:
        return blocked(safe_error(error))


class BoundedOutput(io.StringIO):
    def __init__(self):
        super().__init__()
        self.size = 0
        self.overflow = False

    def write(self, value):
        # Observability must not inject an exception into an in-flight writer.
        if type(value) is not str:
            self.overflow = True
            return 0
        if not self.overflow:
            encoded = value.encode('utf-8', errors='replace')
            room = MAX_OUTPUT - self.size
            self.overflow = len(encoded) > room
            kept = encoded[:room].decode('utf-8', errors='ignore')
            self.size += len(kept.encode('utf-8'))
            super().write(kept)
        return len(value)


def decode_result(raw):
    def unique(pairs):
        result = {}
        for k, v in pairs:
            need(k not in result, 'P2_JOB_OUTPUT_REJECTED')
            result[k] = v
        return result
    def invalid(*unused):
        raise JobError('P2_JOB_OUTPUT_REJECTED')
    try:
        result = json.loads(raw, object_pairs_hook=unique, parse_constant=invalid)
        need(type(result) is dict and type(result.get('status')) is str
             and result.get('execution_authorized', False) is False, 'P2_JOB_OUTPUT_REJECTED')
        return result
    except Exception:
        raise JobError('P2_JOB_OUTPUT_REJECTED') from None


def main(argv=None, *, declaration=_DEFAULT, context=None, revision=code_revision,
         identity=local_runner):
    parser = cli.SafeParser(description='Manual P2 job; code-configured, default BLOCKED.')
    parser.add_argument('command', choices=COMMANDS)
    parser.add_argument('--execute', action='store_true', help='request only, never a grant')
    args = parser.parse_args(argv)
    if args.command == 'gate':
        result = workflow_gate(declaration=declaration, context=context, revision=revision)
        print(json.dumps(result, sort_keys=True))
        return 0 if result['dispatch_configured'] else 2
    invocation, started, result, rc, runtime_rc = None, False, None, 2, None
    try:
        declaration = configured(declaration)
        if args.command == 'status':
            result = {'schema': 'P2-CICD-job-1', 'status': 'CONFIGURED_NOT_VERIFIED',
                      'execution_authorized': False, 'runtime': 'NOT_RUN', 'dispatch_configured': True}
            rc = 0
        else:
            need(args.execute, 'P2_JOB_EXECUTE_REQUIRED')
            context = native_context() if context is None else context
            context_checked(context, revision)
            actual = identity()
            need(type(actual) is RunnerIdentity and actual == declaration.runner,
                 'P2_JOB_RUNNER_REJECTED')
            actual.check()
            # Trusted builder diagnostics are not public job evidence either.
            diagnostics, diagnostics_errors = BoundedOutput(), BoundedOutput()
            with contextlib.redirect_stdout(diagnostics), contextlib.redirect_stderr(diagnostics_errors):
                value = declaration.build(context, args.command)
            if type(value) is RuntimeInvocation:
                invocation = value  # Retain before validation for cleanup on rejection.
            need(not diagnostics.overflow and not diagnostics_errors.overflow, 'P2_JOB_OUTPUT_REJECTED')
            need(invocation is not None, 'P2_JOB_INVOCATION_REJECTED')
            invocation.check(context, args.command)
            object.__setattr__(invocation, '_used', True)
            output, errors = BoundedOutput(), BoundedOutput()
            started = True  # Any exception from here cannot claim NOT_RUN/no fence.
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
                runtime_rc = runtime.main([args.command], **invocation.kwargs())
            need(not output.overflow and not errors.overflow, 'P2_JOB_OUTPUT_REJECTED')
            inner = decode_result(output.getvalue())
            result = {'schema': 'P2-CICD-job-1', 'status': inner['status'],
                'execution_authorized': False, 'runtime': inner.get('runtime', 'NOT_VERIFIED'),
                'job': {'repository': context.repository, 'run_id': context.run_id,
                        'attempt': context.attempt, 'workflow_sha': context.workflow_sha,
                        'command': args.command}, 'result': inner}
            rc = 0 if type(runtime_rc) is int and runtime_rc == 0 and inner['status'] in (
                'PREFLIGHT_OBSERVED', 'SESSION_COMPLETED') else 2
    except Exception as error:
        result = blocked(safe_error(error), started=started)
        if type(runtime_rc) is int and -128 <= runtime_rc <= 255:
            result['runtime_returncode'] = runtime_rc  # Not a reconstructed receipt.
    finally:
        if invocation is not None and not invocation.close() and result is not None:
            # Preserve the actual nested receipt/fence/completion, even RELEASED.
            result.update(status='STOPPED', error_code='P2_JOB_CHANNEL_CLEANUP_FAILED')
            rc = 2
    print(json.dumps(result, sort_keys=True))
    return rc


if __name__ == '__main__':
    raise SystemExit(main())
