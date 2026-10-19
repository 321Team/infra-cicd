"""Explicit, code-configured POSIX input boundary for a reviewed site wrapper.

No configured target, ambient credential/JSON/plugin loader or authenticator.
Files and Git hashes establish bytes, never approval or render provenance.
The caller supplies the same in-memory credential and authenticating adapters.
"""
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
import stat

import delivery_adapter as core
import delivery_cli as cli
import delivery_store as store
from delivery_object_reader import BearerCredential
from delivery_provider import AuthorityAdapters, OperationalApplication, RunInputs
from producer_bridge import GitSnapshot, CHECKER_PATH, CONTRACT_PATH, CHECKER_SHA256

MAX_BYTES = 1024 * 1024
NAMES = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}')
ADAPTERS = ('approval', 'platform', 'producer', 'baseline', 'finish')


class SiteError(RuntimeError):
    """Fixed, body/path/credential-free public codes only."""


def need(ok, code):
    if not ok:
        raise SiteError(code)


@dataclass(frozen=True)
class SiteConfiguration:
    binding: core.Binding = field(repr=False)
    input_root: object = field(repr=False)
    rendered_name: str = field(repr=False)
    contract_name: str = field(repr=False)
    source_root: object = field(repr=False)
    store_root: object = field(repr=False)
    budget_seconds: float = field(repr=False)
    finalize: bool = field(default=False, repr=False)


def configuration_checked(config):
    need(type(config) is SiteConfiguration, 'SITE_CONFIGURATION_INVALID')
    try:
        core.binding_checked(config.binding)
    except Exception:
        raise SiteError('SITE_CONFIGURATION_INVALID') from None
    need(core.finite(config.budget_seconds) and config.budget_seconds > 0
         and type(config.finalize) is bool, 'SITE_CONFIGURATION_INVALID')
    for value in (config.input_root, config.source_root, config.store_root):
        need(isinstance(value, (str, Path)) and bool(str(value)), 'SITE_CONFIGURATION_INVALID')
    names = (config.rendered_name, config.contract_name)
    need(all(type(n) is str and NAMES.fullmatch(n) for n in names)
         and names[0] != names[1], 'SITE_INPUT_NAME_INVALID')


def metadata(info):
    return (info.st_dev, info.st_ino, info.st_uid, info.st_gid, info.st_mode,
            info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def root_stable(path, fd):
    # Reuse the existing root/parent policy; do not create or repair directories.
    current, other = store.root_open(path)
    try:
        need(store.inode(store.directory(fd)) == store.inode(store.directory(other))
             == store.inode(current.lstat()), 'SITE_INPUT_CHANGED')
    finally:
        os.close(other)


def read_input(root, name, expected):
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK,
                 dir_fd=root)
    try:
        first = store.regular(fd)  # mode0600, own UID, regular, one link
        need(0 < first.st_size <= MAX_BYTES, 'SITE_INPUT_SIZE_INVALID')
        chunks, remaining = [], MAX_BYTES + 1
        while remaining:
            chunk = os.read(fd, remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b''.join(chunks)
        last = store.regular(fd)
        named = os.stat(name, dir_fd=root, follow_symlinks=False)
        need(metadata(first) == metadata(last) == metadata(named), 'SITE_INPUT_CHANGED')
        need(0 < len(raw) <= MAX_BYTES and len(raw) == last.st_size, 'SITE_INPUT_SIZE_INVALID')
        need(core.digest(raw) == expected, 'SITE_INPUT_HASH_MISMATCH')
        return raw
    finally:
        os.close(fd)


def source_checked(root, binding, contract):
    # Repository/tool installation is trusted, as in the existing GitSnapshot.
    # Still reject symlink/writable path components for this site connection.
    path = Path(os.path.abspath(root))
    for node in (path, *path.parents):
        info = node.lstat()
        need(stat.S_ISDIR(info.st_mode) and info.st_uid in (0, os.geteuid())
             and not info.st_mode & 0o022, 'SITE_SOURCE_UNSAFE')
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        original = store.inode(os.fstat(fd))
        source = GitSnapshot(path, binding.source_commit)
        need(core.digest(source.blob(CHECKER_PATH)) == CHECKER_SHA256,
             'SITE_SOURCE_MISMATCH')
        need(source.blob(CONTRACT_PATH).replace(b'\r\n', b'\n') == contract.replace(b'\r\n', b'\n'),
             'SITE_SOURCE_MISMATCH')
        need(original == store.inode(os.fstat(fd)) == store.inode(path.lstat()),
             'SITE_SOURCE_CHANGED')
    finally:
        os.close(fd)


def build_application(config, *, credential, adapters):
    """Read only explicit immutable input bytes, then compose existing core.

    Does not invoke adapters, acquire a claim, send API requests or infer any
    authority. Authenticating adapters must themselves enforce I/O deadlines.
    """
    try:
        configuration_checked(config)
        need(os.name == 'posix', 'SITE_POSIX_REQUIRED')
        need(type(credential) is BearerCredential and type(adapters) is AuthorityAdapters
             and all(callable(getattr(adapters, n)) for n in ADAPTERS),
             'SITE_AUTHENTICATORS_REQUIRED')
        root, fd = store.root_open(config.input_root)
        try:
            rendered = read_input(fd, config.rendered_name, config.binding.rendered_sha256)
            contract = read_input(fd, config.contract_name, config.binding.contract_sha256)
            root_stable(root, fd)
        finally:
            os.close(fd)
        source_checked(config.source_root, config.binding, contract)
        _, fd = store.root_open(config.store_root)
        try:
            root_stable(config.store_root, fd)
        finally:
            os.close(fd)
        inputs = RunInputs(config.binding, rendered, contract, config.source_root,
                           config.store_root, config.budget_seconds, config.finalize)
        return OperationalApplication(inputs, credential, adapters)
    except SiteError:
        raise
    except Exception:
        # Never expose git stderr, OS paths, YAML, token or arbitrary __str__.
        raise SiteError('SITE_INPUT_PREPARATION_FAILED') from None


def blocked(code):
    print(json.dumps({'schema': 'P2-CICD-site-1', 'status': 'BLOCKED',
        'error_code': code, 'execution_authorized': False, 'runtime': 'NOT_RUN',
        'sender_calls': 0, 'claim_acquired': False}, sort_keys=True))
    return 2


def main(argv=None, *, config=None, credential=None, adapters=None):
    parser = cli.SafeParser(description='Code-configured site input boundary.')
    parser.add_argument('command', choices=('status', 'preflight', 'deploy', 'restore'))
    args = parser.parse_args(argv)
    if config is None:
        return blocked('SITE_UNCONFIGURED')
    try:
        configuration_checked(config)
        if args.command == 'status':
            print(json.dumps({'schema': 'P2-CICD-site-1', 'status': 'CONFIGURED_NOT_VERIFIED',
                'execution_authorized': False, 'runtime': 'NOT_RUN'}, sort_keys=True))
            return 0
        need(args.command in ('preflight', config.binding.action), 'SITE_ACTION_MISMATCH')
        app = build_application(config, credential=credential, adapters=adapters)
    except SiteError as error:
        return blocked(error.args[0])
    return cli.main([args.command], application=app)


if __name__ == '__main__':
    raise SystemExit(main())
