"""Private, create-once diagnostic persistence; never an ACK or authority.

Uses the SAME configured secure POSIX root as the run store. No directory
creation, overwrite, fallback, token/env loading, replay or fence modification.
"""
import base64
import os

import delivery_adapter as core
import delivery_store as store
from delivery_object_reader import FailureDiagnostics, Observation, SAFE_CODES
from delivery_objects import REVISION, decode, deployment_revision
from delivery_verifier import Progress, VerificationError, require

REDACTED = '<REDACTED>'
REDACTION_PROFILE = 'P2-K8S-DIAGNOSTIC-2'


def redacted_snapshot(observation):
    """Diagnostic copy only; preserve original digest, never original body bytes.

    This fixed profile removes known inline values and opaque annotation copies.
    It is not a general secret scanner or an admission/ownership verifier.
    ObjectReader/Reconciler continue to use the original bytes in memory.
    """
    obj = decode(observation.raw)
    counts = dict(route_tls=0, literal_env=0, annotation=0, command=0, header=0, message=0,
                  revision_preserved=0)
    if observation.status == 404:
        key = observation.key
        group = key[0].split('/')[0] if '/' in key[0] else ''
        plural = {'Service': 'services', 'Deployment': 'deployments', 'Route': 'routes'}[key[1]]
        details = obj.get('details')
        require(obj.get('apiVersion') == 'v1' and obj.get('kind') == 'Status'
                and obj.get('status') == 'Failure' and obj.get('reason') == 'NotFound'
                and type(obj.get('code')) is int and obj['code'] == 404
                and type(details) is dict and details.get('name') == key[2]
                and details.get('group', '') == group and details.get('kind') == plural,
                'DIAGNOSTIC_INVALID')
        # A validated absence needs no opaque server message or extra fields.
        obj = {'apiVersion': 'v1', 'kind': 'Status', 'status': 'Failure',
               'reason': 'NotFound', 'code': 404,
               'details': {'group': group, 'kind': plural, 'name': key[2]}}
    else:
        metadata = obj.get('metadata')
        require(set(obj) <= {'apiVersion', 'kind', 'metadata', 'spec', 'status'}
                and type(metadata) is dict
                and (obj.get('apiVersion'), obj.get('kind'), metadata.get('name')) == observation.key
                and metadata.get('namespace') == core.NAMESPACE
                and type(obj.get('spec', {})) is dict,
                'DIAGNOSTIC_INVALID')
        revision = None
        if observation.key[:2] == ('apps/v1', 'Deployment'):
            try:
                if deployment_revision(obj) > 0:
                    revision = metadata['annotations'][REVISION]
            except VerificationError:
                # An invalid value is still opaque: redact it, never preserve/hash it.
                pass
        if observation.key[1] == 'Route':
            tls = obj.get('spec', {}).get('tls', {})
            require(type(tls) is dict, 'DIAGNOSTIC_INVALID')
            for name in ('key', 'certificate', 'caCertificate', 'destinationCACertificate'):
                if name in tls:
                    tls[name] = REDACTED
                    counts['route_tls'] += 1

        def walk(value, depth=0):
            require(depth <= 64, 'DIAGNOSTIC_INVALID')
            if type(value) is list:
                for item in value:
                    walk(item, depth + 1)
            elif type(value) is dict:
                for name, child in value.items():
                    if name == 'annotations':
                        require(type(child) is dict, 'DIAGNOSTIC_INVALID')
                        # Includes kubectl last-applied JSON and nested copies.
                        for annotation in child:
                            if value is metadata and annotation == REVISION and revision is not None:
                                counts['revision_preserved'] += 1
                                continue
                            child[annotation] = REDACTED
                            counts['annotation'] += 1
                    elif name in ('env', 'httpHeaders'):
                        require(type(child) is list, 'DIAGNOSTIC_INVALID')
                        for item in child:
                            require(type(item) is dict, 'DIAGNOSTIC_INVALID')
                            if 'value' in item:
                                item['value'] = REDACTED
                                counts['literal_env' if name == 'env' else 'header'] += 1
                            walk(item, depth + 1)
                    elif name in ('command', 'args'):
                        require(type(child) is list and all(type(v) is str for v in child),
                                'DIAGNOSTIC_INVALID')
                        value[name] = [REDACTED for _ in child]
                        counts['command'] += len(child)
                    elif name == 'message':
                        value[name] = REDACTED
                        counts['message'] += 1
                    else:
                        walk(child, depth + 1)
        walk(obj)
    raw = core.encoded(obj)
    require(len(raw) <= 1024 * 1024, 'DIAGNOSTIC_INVALID')
    return raw, counts


def diagnostic_name(binding, phase):
    core.binding_checked(binding)
    require(type(phase) is str and phase in ('preflight', 'session'), 'DIAGNOSTIC_INVALID')
    return binding.key + '.' + phase + '.diagnostic.json'


def diagnostic_bytes(binding, progress, observed_at, diagnostic, *, phase='session'):
    core.binding_checked(binding)
    diagnostic_name(binding, phase)
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
        row = {'key': o.key, 'status': o.status, 'source_sha256': core.digest(o.raw)}
        try:
            snapshot, counts = redacted_snapshot(o)
        except VerificationError:
            # Keep a body-free integrity reference, not the rejected object or error text.
            row['redaction'] = 'REJECTED'
        else:
            row.update(redaction='APPLIED', snapshot_sha256=core.digest(snapshot),
                       snapshot_base64=base64.b64encode(snapshot).decode('ascii'),
                       redaction_counts=counts)
        rows.append(row)
    return core.encoded({'schema': 'P2-CICD-private-diagnostic-4', 'phase': phase,
        'redaction_profile': REDACTION_PROFILE, 'snapshot_is_original': False,
        'redaction_complete': all(row['redaction'] == 'APPLIED' for row in rows),
        'purpose': 'DIAGNOSTIC_ONLY', 'execution_authorized': False,
        'binding_key': binding.key, 'run_id': binding.run_id, 'action': binding.action,
        'observed_at': observed_at, 'progress_key': progress.key,
        'code': diagnostic.code, 'failed_key': diagnostic.failed_key, 'observations': rows})


def persist(root, binding, progress, observed_at, diagnostic, *, phase='session'):
    """0600/O_EXCL/no-follow/fsync; may leave a partial file on failure.

    One terminal diagnostic per phase per RUN. Existing or unsafe files cause failure;
    they are never silently overwritten. Only redacted snapshots are persisted;
    source hashes are integrity references, never proof of approval or success.
    The caller must not print exceptions or diagnostic bytes.
    """
    fd = None
    try:
        name = diagnostic_name(binding, phase)
        raw = diagnostic_bytes(binding, progress, observed_at, diagnostic, phase=phase)
        path, fd = store.root_open(root)
        expected = store.create_file(fd, name, raw)
        require(store.inode(store.directory(fd)) == store.inode(path.lstat()),
                'DIAGNOSTIC_ROOT_CHANGED')
        store.file_check(fd, name, raw, expected)
    except Exception:
        raise store.StoreError('DIAGNOSTIC_PERSIST_FAILED') from None
    finally:
        if fd is not None:
            os.close(fd)
