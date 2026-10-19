"""Private, create-once diagnostic persistence; never an ACK or authority.

Uses the SAME configured secure POSIX root as the run store. No directory
creation, overwrite, fallback, token/env loading, replay or fence modification.
"""
import base64
import os

import delivery_adapter as core
import delivery_store as store
from delivery_object_reader import FailureDiagnostics, Observation, SAFE_CODES
from delivery_verifier import Progress, require


def diagnostic_bytes(binding, progress, observed_at, diagnostic):
    core.binding_checked(binding)
    require(type(progress) is Progress and core.finite(observed_at), 'DIAGNOSTIC_INVALID')
    require(type(diagnostic) is FailureDiagnostics and type(diagnostic.code) is str
            and diagnostic.code in SAFE_CODES
            and (diagnostic.failed_key is None or
                 (type(diagnostic.failed_key) is tuple and diagnostic.failed_key in core.OBJECTS))
            and type(diagnostic.observations) is tuple
            and len(diagnostic.observations) <= len(core.OBJECTS), 'DIAGNOSTIC_INVALID')
    rows, seen = [], set()
    for o in diagnostic.observations:
        require(type(o) is Observation and o.key in core.OBJECTS and o.key not in seen
                and type(o.status) is int and o.status in (200, 404)
                and type(o.raw) is bytes and 0 < len(o.raw) <= 1024 * 1024,
                'DIAGNOSTIC_INVALID')
        seen.add(o.key)
        rows.append({'key': o.key, 'status': o.status, 'sha256': core.digest(o.raw),
                     'body_base64': base64.b64encode(o.raw).decode('ascii')})
    return core.encoded({'schema': 'P2-CICD-private-diagnostic-1',
        'purpose': 'DIAGNOSTIC_ONLY', 'execution_authorized': False,
        'binding_key': binding.key, 'run_id': binding.run_id, 'action': binding.action,
        'observed_at': observed_at, 'progress_key': progress.key,
        'code': diagnostic.code, 'failed_key': diagnostic.failed_key, 'observations': rows})


def persist(root, binding, progress, observed_at, diagnostic):
    """0600/O_EXCL/no-follow/fsync; may leave a partial file on failure.

    One terminal diagnostic per RUN. Existing or unsafe files cause failure;
    they are never silently overwritten. Raw objects stay private even on 403.
    The caller must not print exceptions or diagnostic bytes.
    """
    fd = None
    try:
        raw = diagnostic_bytes(binding, progress, observed_at, diagnostic)
        path, fd = store.root_open(root)
        expected = store.create_file(fd, binding.key + '.diagnostic.json', raw)
        require(store.inode(store.directory(fd)) == store.inode(path.lstat()),
                'DIAGNOSTIC_ROOT_CHANGED')
        store.file_check(fd, binding.key + '.diagnostic.json', raw, expected)
    except Exception:
        raise store.StoreError('DIAGNOSTIC_PERSIST_FAILED') from None
    finally:
        if fd is not None:
            os.close(fd)
