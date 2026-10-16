"""Read-only deterministic source pack for #43/#46 render preparation.

Reads nine fixed committed source files, never a worktree, renderer or cluster.
Integrity of these bytes is not source approval, dependency closure, a clean
render, a credential, an execution grant or producer/platform evidence.
"""
from dataclasses import dataclass, field
import gzip
import hashlib
import io
import re
import tarfile

import delivery_adapter as core
import producer_bridge as bridge

PATHS = (
    'deploy/base/fds-engine.yaml',
    'deploy/base/kustomization.yaml',
    'deploy/base/networkpolicy.yaml',
    'deploy/base/transaction-api.yaml',
    'deploy/components/openshift/kustomization.yaml',
    'deploy/components/openshift/route.yaml',
    bridge.CONTRACT_PATH,
    'deploy/overlays/rosa-app/kustomization.yaml',
    bridge.CHECKER_PATH,
)
MAX_OBJECT = bridge.MAX_BYTES
MAX_TOTAL = 16 * MAX_OBJECT


class SnapshotError(RuntimeError):
    """Fixed error codes only; never reveal source bytes, paths or git stderr."""


def need(ok, code):
    if not ok:
        raise SnapshotError(code)


@dataclass(frozen=True)
class SourceFile:
    path: str
    git_blob: str
    git_mode: str
    sha256: str
    raw: bytes = field(repr=False)


@dataclass(frozen=True)
class SourcePack:
    source_commit: str
    files: tuple
    manifest: bytes = field(repr=False)
    archive: bytes = field(repr=False)
    render_generation: str = field(default='NOT_RUN', init=False)
    source_approval: str = field(default='NOT_VERIFIED', init=False)
    execution_authorized: bool = field(default=False, init=False)

    @property
    def sha256(self):
        return core.digest(self.archive)


class Objects:
    def __init__(self, source):
        self.source, self.total, self.cache = source, 0, {}

    def read(self, kind, oid):
        need(kind in ('commit', 'tree', 'blob') and type(oid) is str
             and re.fullmatch(r'[0-9a-f]{40}', oid), 'SOURCE_OBJECT_INVALID')
        key = kind, oid
        if key in self.cache:
            return self.cache[key]
        size = self.source._git('-s', oid).strip()
        need(re.fullmatch(rb'[0-9]{1,8}', size), 'SOURCE_SIZE_INVALID')
        count = int(size)
        need(0 < count <= MAX_OBJECT and self.total + count <= MAX_TOTAL,
             'SOURCE_SIZE_INVALID')
        raw = self.source._git(kind, oid)
        need(len(raw) == count, 'SOURCE_SIZE_CHANGED')
        # Git's SHA-1 object identity plus SHA256 in the manifest bind the bytes;
        # neither establishes approval or authenticates the repository origin.
        header = (kind + ' ' + str(count)).encode('ascii') + b'\0'
        need(hashlib.sha1(header + raw).hexdigest() == oid, 'SOURCE_OBJECT_CHANGED')
        self.total += count
        self.cache[key] = raw
        return raw

    def entries(self, tree):
        raw, entries, offset = self.read('tree', tree), {}, 0
        while offset < len(raw):
            space = raw.index(b' ', offset)
            zero = raw.index(b'\0', space + 1)
            mode, name = raw[offset:space], raw[space + 1:zero]
            need(name and name not in entries and b'/' not in name and name not in (b'.', b'..')
                 and zero + 21 <= len(raw), 'SOURCE_TREE_INVALID')
            entries[name] = mode, raw[zero + 1:zero + 21].hex()
            offset = zero + 21
        return entries

    def file(self, root_tree, path):
        tree = root_tree
        pieces = path.split('/')
        for part in pieces[:-1]:
            entry = self.entries(tree).get(part.encode('ascii'))
            need(entry is not None and entry[0] == b'40000', 'SOURCE_PATH_UNSAFE')
            tree = entry[1]
        entry = self.entries(tree).get(pieces[-1].encode('ascii'))
        need(entry is not None and entry[0] in (b'100644', b'100755'), 'SOURCE_PATH_UNSAFE')
        raw = self.read('blob', entry[1])
        return SourceFile(path, entry[1], entry[0].decode('ascii'), core.digest(raw), raw)


def archive_bytes(files, manifest):
    out = io.BytesIO()
    # No current time, source filesystem metadata or caller filename in output.
    with gzip.GzipFile(fileobj=out, mode='wb', filename='', mtime=0, compresslevel=9) as zipped:
        with tarfile.open(fileobj=zipped, mode='w', format=tarfile.USTAR_FORMAT) as archive:
            items = [('SOURCE_SNAPSHOT.json', manifest)]
            items.extend(('source/' + f.path, f.raw) for f in files)
            for name, raw in items:
                info = tarfile.TarInfo(name)
                info.size, info.mode, info.mtime = len(raw), 0o644, 0
                info.uid = info.gid = 0
                info.uname = info.gname = ''
                archive.addfile(info, io.BytesIO(raw))
    return out.getvalue()


def capture(root, commit):
    """Return bytes only: no output file creation, extraction or tool execution.

    GitSnapshot supplies existing full-SHA/no-replace/no-lazy-fetch/no-protocol
    guards and per-command timeout. Missing objects fail without a remote fetch.
    The allowlist is this project's current source layout, not a general archive.
    """
    try:
        source = bridge.GitSnapshot(root, commit)
        objects = Objects(source)
        raw_commit = objects.read('commit', commit)
        first = raw_commit.split(b'\n', 1)[0]
        need(re.fullmatch(rb'tree [0-9a-f]{40}', first), 'SOURCE_TREE_INVALID')
        tree = first[5:].decode('ascii')
        files = tuple(objects.file(tree, path) for path in PATHS)
        checker = next(f for f in files if f.path == bridge.CHECKER_PATH)
        need(checker.sha256 == bridge.CHECKER_SHA256, 'CHECKER_NOT_REVIEWED')
        manifest = core.encoded({
            'schema': 'P2-source-snapshot-1', 'source_commit': commit,
            'source_tree': tree, 'source_integrity': 'MATCH',
            'source_approval': 'NOT_VERIFIED', 'dependency_closure': 'NOT_VERIFIED',
            'render_generation': 'NOT_RUN', 'execution_authorized': False,
            'files': [{'path': f.path, 'git_blob': f.git_blob, 'git_mode': f.git_mode,
                       'sha256': f.sha256, 'bytes': len(f.raw)} for f in files],
        })
        return SourcePack(commit, files, manifest, archive_bytes(files, manifest))
    except SnapshotError:
        raise
    except Exception:
        raise SnapshotError('SOURCE_CAPTURE_FAILED') from None
