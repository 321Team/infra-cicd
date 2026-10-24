"""Optional owned private-source channels connected to the reviewed runtime CLI.

Explicit trusted Python wiring only. No target, grant, issuer, credential or
repository discovery. Local close is neither token revocation nor zeroization.
"""
from dataclasses import dataclass, field
import hmac
import json
import os
import threading
import time
from types import MappingProxyType

import delivery_adapter as core
import delivery_cli as cli
from delivery_credential_channel import PipeCredentialReceiver, MAX_HANDOFF_SECONDS
from delivery_object_reader import BearerCredential
from delivery_private_source import (GitHubPipeCredentialReceiver, GitHubReadCredential,
                                    PrivateGitHubTLS, PrivateGitHubOriginalAdapter)


class SourceSessionError(RuntimeError):
    """Fixed codes only; never credentials, endpoint, source or callback text."""


SAFE_CODES = frozenset('''
SOURCE_SESSION_CONFIGURATION_INVALID SOURCE_SESSION_REPOSITORY_DUPLICATE
SOURCE_SESSION_CLOCK_INVALID SOURCE_SESSION_CHANNEL_INVALID SOURCE_SESSION_CHANNEL_ALIASED
SOURCE_SESSION_HANDOFF_EXPIRED SOURCE_SESSION_CONSUMED SOURCE_SESSION_CREDENTIAL_INVALID
SOURCE_SESSION_REPOSITORY_MISMATCH SOURCE_SESSION_TOKEN_ALIASED SOURCE_SESSION_ROLES_INVALID
SOURCE_SESSION_REPOSITORY_UNCONFIGURED SOURCE_SESSION_REQUIRED SOURCE_SESSION_ROLE_CONFLICT
SOURCE_SESSION_NATIVE_ORIGINAL_INVALID SOURCE_SESSION_NATIVE_ORIGINAL_REQUIRED
RUNTIME_SITE_UNCONFIGURED RUNTIME_SITE_BINDING_MISMATCH RUNTIME_SITE_ACTION_MISMATCH
'''.split())
MAX_NATIVE_ORIGINAL_BYTES = 48 * 1024


def need(ok, code):
    if not ok:
        raise SourceSessionError(code)


@dataclass(frozen=True)
class SourceChannel:
    receiver: object = field(repr=False)
    ca_pem: bytes = field(repr=False)
    endpoint_ip: str = field(repr=False)


def pipe_identity(fd):
    info = os.fstat(fd)
    return info.st_dev, info.st_ino


@dataclass(frozen=True, repr=False)
class NativeIssuanceOriginal:
    """PRIVATE same-frame bytes/correlation, not source or grant authentication.

    The reviewed transport must authenticate its real peer/job before making
    this record. The native parser and existing authority checks still run.
    """
    context: object = field(repr=False)
    binding: object = field(repr=False)
    credential_fingerprint: str = field(repr=False)
    raw: bytes = field(repr=False)

    def check(self):
        from delivery_job import JobContext
        need(type(self.context) is JobContext, 'SOURCE_SESSION_NATIVE_ORIGINAL_INVALID')
        self.context.check(); core.binding_checked(self.binding)
        need(type(self.raw) is bytes and 0 < len(self.raw) <= MAX_NATIVE_ORIGINAL_BYTES
             and type(self.credential_fingerprint) is str
             and core.HEX.fullmatch(self.credential_fingerprint),
             'SOURCE_SESSION_NATIVE_ORIGINAL_INVALID')

    def _no_copy(self, *unused):
        raise SourceSessionError('SOURCE_SESSION_NATIVE_ORIGINAL_INVALID')

    __copy__ = __deepcopy__ = __reduce_ex__ = _no_copy


class PrivateSourceSession:
    """Own exact private receivers from successful construction through close.

    wire(session) is trusted site code, not a plugin/JSON authority loader. It
    selects readers for existing runtime roles; those borrowed elsewhere are
    never closed here. One pipe/token scope per explicitly configured repository.
    """
    ROLES = frozenset(('readers', 'producer_run_reader', 'finish_collection', 'finish_observation'))

    def __init__(self, channels, *, wire, monotonic=time.monotonic, native_original=None):
        # Invalid shapes transfer no ownership, avoiding arbitrary close calls.
        need(type(channels) is tuple and 0 < len(channels) <= 16
             and all(type(c) is SourceChannel and type(c.receiver) is GitHubPipeCredentialReceiver
                     for c in channels), 'SOURCE_SESSION_CONFIGURATION_INVALID')
        self.channels, self.wire, self.clock = channels, wire, monotonic
        self._handles, self._readers, self._state, self._last = [], {}, 'NEW', None
        self.cleanup_ok = True
        self._native, self._native_lock = None, threading.RLock()
        try:
            need(callable(wire) and callable(monotonic), 'SOURCE_SESSION_CONFIGURATION_INVALID')
            need(native_original is None or type(native_original) is NativeIssuanceOriginal,
                 'SOURCE_SESSION_NATIVE_ORIGINAL_INVALID')
            if native_original is not None:
                native_original.check()
                self._native = native_original
            repos = tuple(c.receiver.repository for c in channels)
            need(len(set(repos)) == len(repos), 'SOURCE_SESSION_REPOSITORY_DUPLICATE')
        except BaseException:
            self.close()
            raise

    def __repr__(self):
        return '<P2 PrivateSourceSession: private, single-use>'

    def now(self):
        value = self.clock()
        need(core.finite(value) and (self._last is None or value >= self._last),
             'SOURCE_SESSION_CLOCK_INVALID')
        self._last = value
        return value

    def prepare(self, rosa_receiver, deadline):
        need(self._state == 'NEW' and type(rosa_receiver) is PipeCredentialReceiver
             and core.finite(deadline), 'SOURCE_SESSION_CHANNEL_INVALID')
        # Before either receive: also detect different FD numbers for one pipe.
        receivers = (rosa_receiver,) + tuple(c.receiver for c in self.channels)
        identities = []
        for r in receivers:
            need(type(r._fd) is int and r._fd >= 3, 'SOURCE_SESSION_CHANNEL_INVALID')
            identities.append(pipe_identity(r._fd))
        need(len(set(identities)) == len(identities), 'SOURCE_SESSION_CHANNEL_ALIASED')
        self._state = 'PREPARED'
        # Shared absolute cap includes ROSA and every source handoff, not 10s each.
        self.deadline = min(deadline, self.now() + MAX_HANDOFF_SECONDS)
        need(self.now() < self.deadline, 'SOURCE_SESSION_HANDOFF_EXPIRED')
        return self.deadline

    def open(self, rosa_credential):
        need(self._state == 'PREPARED' and type(rosa_credential) is BearerCredential,
             'SOURCE_SESSION_CONSUMED')
        self._state = 'CONSUMED'  # No retry after even partial/failed receipt.
        for channel in self.channels:
            need(self.now() < self.deadline, 'SOURCE_SESSION_HANDOFF_EXPIRED')
            credential = channel.receiver.receive(self.deadline)
            need(type(credential) is GitHubReadCredential, 'SOURCE_SESSION_CREDENTIAL_INVALID')
            self._handles.append(credential)
            need(credential.repository == channel.receiver.repository,
                 'SOURCE_SESSION_REPOSITORY_MISMATCH')
            need(not hmac.compare_digest(core.digest(credential.token.encode('ascii')),
                                         rosa_credential.fingerprint), 'SOURCE_SESSION_TOKEN_ALIASED')
            need(self.now() < self.deadline, 'SOURCE_SESSION_HANDOFF_EXPIRED')
            transport = PrivateGitHubTLS(credential=credential, ca_pem=channel.ca_pem,
                endpoint_ip=channel.endpoint_ip, monotonic=self.now)
            self._handles.append(transport)
            self._readers[credential.repository] = PrivateGitHubOriginalAdapter(transport)
        roles = self.wire(self)
        need(type(roles) is dict and 'readers' in roles and set(roles) <= self.ROLES
             and all(v is not None for v in roles.values()), 'SOURCE_SESSION_ROLES_INVALID')
        # Wiring callback time also consumes the same handoff budget.
        need(self.now() < self.deadline, 'SOURCE_SESSION_HANDOFF_EXPIRED')
        return roles

    def reader(self, repository):
        need(self._state == 'CONSUMED' and repository in self._readers,
             'SOURCE_SESSION_REPOSITORY_UNCONFIGURED')
        return self._readers[repository]

    def take_native_original(self, context, binding, credential, deadline):
        """One private take AFTER source handoff; no retry or authority promotion."""
        from delivery_job import JobContext
        with self._native_lock:
            value, self._native = self._native, None  # Bad takes consume too.
            raw = error = None
            try:
                need(type(value) is NativeIssuanceOriginal,
                     'SOURCE_SESSION_NATIVE_ORIGINAL_REQUIRED')
                need(self._state == 'CONSUMED' and type(context) is JobContext
                     and type(credential) is BearerCredential and value.context == context
                     and type(binding) is core.Binding and value.binding == binding
                     and core.finite(deadline) and deadline == self.deadline
                     and self.now() < deadline
                     and hmac.compare_digest(value.credential_fingerprint, credential.fingerprint),
                     'SOURCE_SESSION_NATIVE_ORIGINAL_INVALID')
                raw = value.raw
                current = self.now()
                need(current < deadline and self._state == 'CONSUMED',
                     'SOURCE_SESSION_NATIVE_ORIGINAL_INVALID')
            except BaseException as exc:
                if isinstance(exc, KeyboardInterrupt): error = KeyboardInterrupt()
                elif isinstance(exc, SystemExit): error = SystemExit(130)
                else:
                    code = ('SOURCE_SESSION_NATIVE_ORIGINAL_REQUIRED' if value is None
                            else 'SOURCE_SESSION_NATIVE_ORIGINAL_INVALID')
                    error = SourceSessionError(code)
            value = context = binding = credential = None
            if error is not None:
                raw = None
                raise error from None
            return raw

    def close(self):
        # Detach first; repeated cleanup cannot close a reused FD/handle.
        with self._native_lock:
            handles, self._handles = self._handles, []
            channels, self.channels = self.channels, ()
            self._state, self.wire, self._readers = 'CLOSED', None, {}
            self._native = None
        for handle in tuple(reversed(handles)) + tuple(c.receiver for c in channels):
            try:
                handle.close()
            except BaseException:
                self.cleanup_ok = False
        return self.cleanup_ok


def native_original_reader(binding):
    """Explicit trusted reader selection; no original/global/file/env discovery."""
    core.binding_checked(binding)
    def read(context, credential, source, roles, deadline):
        need(type(source) is PrivateSourceSession and type(roles) is MappingProxyType,
             'SOURCE_SESSION_NATIVE_ORIGINAL_INVALID')
        return source.take_native_original(context, binding, credential, deadline)
    return read


def run_site(argv, *, source_session, config, plan, readers, finish_slot, receiver,
             handoff_deadline, finish_capture, producer_pins, producer_run_reader,
             finish_collection, finish_observation):
    """Optional path only; all actual authority checks stay in existing factory.

    Print once, AFTER owned source cleanup, so a late cleanup failure retains
    the actual runtime/fence result rather than falsely reporting NOT_RUN.
    """
    import delivery_runtime_site as runtime
    import delivery_site as site
    source = source_session if type(source_session) is PrivateSourceSession else None
    owned = receiver if type(receiver) is PipeCredentialReceiver else None
    prepared = plan if type(plan) is runtime.PreparedAuthorityPlan else None
    result, credential, application = None, None, None
    try:
        parser = cli.SafeParser(description='Reviewed source-session runtime; no default target.')
        parser.add_argument('command', choices=('status', 'preflight', 'deploy', 'restore'))
        args = parser.parse_args(argv)
        need(source is not None, 'SOURCE_SESSION_REQUIRED')
        need(config is not None, 'RUNTIME_SITE_UNCONFIGURED')
        site.configuration_checked(config)
        need(config.binding == runtime.plan_binding(plan),
             'RUNTIME_SITE_BINDING_MISMATCH')
        need(readers is None and producer_run_reader is None and finish_collection is None
             and finish_observation is None, 'SOURCE_SESSION_ROLE_CONFLICT')
        if args.command == 'status':
            # Declaration only: no credential receipt, wiring callback, TLS, file
            # read, authority validation, source/API calls or claim acquisition.
            result = {'status': 'CONFIGURED_NOT_VERIFIED', 'execution_authorized': False,
                      'runtime': 'NOT_RUN'}
        else:
            need(args.command in ('preflight', config.binding.action), 'RUNTIME_SITE_ACTION_MISMATCH')
            deadline = source.prepare(owned, handoff_deadline)
            credential = owned.receive(deadline)
            roles = source.open(credential)
            if prepared is not None:
                plan = prepared.resolve(credential=credential, source=source, roles=roles,
                                        deadline=deadline)
                need(source.now() < deadline, 'SOURCE_SESSION_HANDOFF_EXPIRED')
            from delivery_job_sources import SourceRoleWiring
            if type(source.wire) is SourceRoleWiring:
                source.wire.check_plan(plan)  # Shape/routing, not source/control authentication.
            factory = runtime.RuntimeAuthorityFactory(plan, roles['readers'], finish_slot,
                finish_capture=finish_capture, producer_pins=producer_pins,
                producer_run_reader=roles.get('producer_run_reader'),
                finish_collection=roles.get('finish_collection'),
                finish_observation=roles.get('finish_observation'))
            application = site.build_application(config, credential=credential, authority_factory=factory)
            result = cli.execute(args.command, application)
    except SourceSessionError as error:
        code = error.args[0] if len(error.args) == 1 else None
        # Even a trusted wiring callback can accidentally raise a public error
        # type with secret text. Do not stringify it or accept an uppercase regex.
        code = code if type(code) is str and code in SAFE_CODES else 'SOURCE_SESSION_PREPARATION_FAILED'
        result = {'status': 'BLOCKED', 'error_code': code,
                  'execution_authorized': False,
                  'runtime': 'NOT_RUN' if application is None else 'NOT_VERIFIED'}
    except (runtime.RuntimeSiteError, site.SiteError):
        result = {'status': 'BLOCKED', 'error_code': 'SOURCE_SESSION_SITE_REJECTED',
                  'execution_authorized': False,
                  'runtime': 'NOT_RUN' if application is None else 'NOT_VERIFIED'}
    except Exception:
        result = {'status': 'BLOCKED', 'error_code': 'SOURCE_SESSION_PREPARATION_FAILED',
                  'execution_authorized': False,
                  'runtime': 'NOT_RUN' if application is None else 'NOT_VERIFIED'}
    except KeyboardInterrupt:
        raise KeyboardInterrupt() from None
    except SystemExit:
        raise SystemExit(130) from None
    finally:
        credential = None
        ok = True
        for handle in (source, owned, prepared):
            if handle is not None:
                try:
                    closed = handle.close()
                    if handle is source and closed is not True:
                        ok = False
                except BaseException:
                    ok = False  # Still attempt every other owned handle.
        if not ok and result is not None:
            # Do not rewrite fence/receipt/runtime when application already ran.
            result.update(status='STOPPED', error_code='SOURCE_SESSION_CLEANUP_FAILED')
    print(json.dumps(result, sort_keys=True))
    return 0 if result['status'] in ('CONFIGURED_NOT_VERIFIED', 'PREFLIGHT_OBSERVED', 'SESSION_COMPLETED') else 2
