"""Received six-object archive -> existing plan, without authority discovery.

No file/network access, extraction, current->previous inference or configured
SITE. Expected digest/root and the existing grant are reviewed CODE inputs,
never values trusted from the archive. Raw object bytes are retained unchanged.
"""
from dataclasses import replace
import gzip
import io
import re
import tarfile

import delivery_adapter as core
import delivery_fds_existing_inputs as existing
import delivery_runtime_site as runtime
from delivery_adopted_authority import AdoptedGrant
from delivery_objects import decode, normalized

MAX_ARCHIVE = 1024 * 1024
MAX_TAR = 7 * 1024 * 1024
MAX_OBJECT = 1024 * 1024
MAX_NOTE = 16384
# Received source/custody facts only, not a grant, policy or current live proof.
RECEIVED_ARCHIVE_SHA256 = 'e102270877a63a810d99de0567e4c95f3d24c1669d9e6bfefdde2aeab66c2a6f'
RECEIVED_ROOT = 'baseline6-20261023T133926'
OBJECT_FILES = tuple(kind.lower() + '-' + name + '.json'
                     for _, kind, name in core.OBJECTS)
NOTES = ('00-time.txt', '00-target.txt', '10-summary.txt')
DATA_FILES = frozenset(OBJECT_FILES + NOTES)


class BaselineArchiveError(RuntimeError):
    """Fixed public code only; never raw object, path or native exception text."""


def need(ok):
    if not ok:
        raise BaselineArchiveError('FDS_BASELINE_ARCHIVE_INVALID')


def decode_baseline_archive(raw, *, expected_sha256, root_name):
    """Check bounded native bundle + manifest, return six exact ordered bytes.

    Hash/shape proves custody/format only, NOT permitted owner policy, fresh live
    equality, approval, prior images or completion. Notes remain evidence, not
    an actor/API/namespace authority parser. Nothing is extracted to disk.
    """
    try:
        need(type(raw) is bytes and 0 < len(raw) <= MAX_ARCHIVE
             and type(expected_sha256) is str and core.HEX.fullmatch(expected_sha256)
             and core.digest(raw) == expected_sha256
             and type(root_name) is str
             and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,127}', root_name))
        with gzip.GzipFile(fileobj=io.BytesIO(raw), mode='rb') as compressed:
            payload = compressed.read(MAX_TAR + 1)
        need(0 < len(payload) <= MAX_TAR)
        files, root_seen, count = {}, False, 0
        with tarfile.open(fileobj=io.BytesIO(payload), mode='r:') as bundle:
            for member in bundle:
                count += 1
                need(count <= len(DATA_FILES) + 2 and not member.pax_headers
                     and not member.issparse())
                if member.isdir():
                    need(not root_seen and member.name.rstrip('/') == root_name)
                    root_seen = True
                    continue
                prefix = root_name + '/'
                need(member.isfile() and member.name.startswith(prefix))
                name = member.name[len(prefix):]
                need(name in DATA_FILES | {'SHA256SUMS'} and name not in files)
                limit = MAX_OBJECT if name in OBJECT_FILES else MAX_NOTE
                need(0 < member.size <= limit)
                stream = bundle.extractfile(member)
                need(stream is not None)
                with stream:
                    value = stream.read(limit + 1)
                need(len(value) == member.size)
                files[name] = value
        need(set(files) == DATA_FILES | {'SHA256SUMS'})
        manifest = {}
        for line in files['SHA256SUMS'].decode('ascii').splitlines():
            match = re.fullmatch(r'([0-9a-f]{64})  ([A-Za-z0-9_.-]+)', line)
            need(match is not None)
            digest, name = match.groups()
            need(name in DATA_FILES and name not in manifest)
            manifest[name] = digest
        need(set(manifest) == DATA_FILES
             and all(core.digest(files[name]) == manifest[name] for name in DATA_FILES))
        objects = tuple(files[name] for name in OBJECT_FILES)
        uids = set()
        for key, value in zip(core.OBJECTS, objects):
            obj = decode(value)  # Existing duplicate-key/nonfinite/size rejection.
            normalized(obj, key)  # Validate on a copy; never return normalized bytes.
            uid = obj['metadata']['uid']
            need(uid not in uids)
            uids.add(uid)
        return objects
    except Exception:
        raise BaselineArchiveError('FDS_BASELINE_ARCHIVE_INVALID') from None


def connect_baseline_archive(config, plan, *, archive, expected_sha256, root_name, grant):
    """Explicit received raw + existing adopted grant -> fixed FDS plan checks.

    The caller supplies independently adopted previous_images in producer facts.
    Existing baseline cannot be silently replaced. This constructor authenticates
    no grant/control: Runtime still rechecks originals/control and fresh six GETs
    before claim/write. Same source/control/finish ownership contract is retained.
    """
    try:
        need(type(plan) is runtime.RuntimeAuthorityPlan and plan.baseline is None
             and type(grant) is AdoptedGrant and grant.binding == config.binding)
        objects = decode_baseline_archive(archive, expected_sha256=expected_sha256,
                                          root_name=root_name)
        connected = replace(plan, baseline=runtime.BaselineAdoption(grant, objects))
        return existing.validate_existing_plan(config, connected)
    except Exception:
        raise BaselineArchiveError('FDS_BASELINE_CONNECTION_INVALID') from None
