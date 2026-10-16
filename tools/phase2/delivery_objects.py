"""Offline object reconciliation for a TRUSTED provider, not an API reader.

The caller must authenticate baseline, fresh observations and same-credential
PATCH replies. None means an authenticated 404, never a failed GET. This helper
does not prove freshness, ownership permission, admission safety or authority.
It neither constructs ObjectEvidence nor authorizes execution on its own.
"""
import json

import delivery_adapter as core
import delivery_store as store
from delivery_verifier import Progress, VerificationError, require

MAX_RAW = 1024 * 1024


def unique(pairs):
    obj = {}
    for key, value in pairs:
        require(key not in obj, 'OBJECT_JSON_INVALID')
        obj[key] = value
    return obj


def decode(raw):
    require(type(raw) is bytes and 0 < len(raw) <= MAX_RAW, 'OBJECT_RAW_INVALID')
    try:
        value = json.loads(raw, object_pairs_hook=unique,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        require(type(value) is dict, 'OBJECT_JSON_INVALID')
        # Reject non-finite values including JSON numbers overflowing a float.
        core.encoded(value)
        return value
    except Exception:
        raise VerificationError('OBJECT_JSON_INVALID') from None


def normalized(obj, key):
    """Preserve spec and non-status ownership; ignore only known volatile data."""
    require(type(obj) is dict and core.identity(obj) == key, 'OBJECT_IDENTITY_MISMATCH')
    obj = decode(core.encoded(obj))
    meta = obj['metadata']
    require(meta.get('namespace') == core.NAMESPACE
            and not meta.get('deletionTimestamp')
            and type(meta.get('uid')) is str and 0 < len(meta['uid']) <= 128
            and type(meta.get('resourceVersion')) is str
            and 0 < len(meta['resourceVersion']) <= 200
            and type(obj.get('spec')) is dict, 'OBJECT_METADATA_INVALID')
    entries = meta.get('managedFields')
    require(type(entries) is list and len(entries) <= 128, 'OBJECT_OWNERSHIP_UNKNOWN')
    owners = []
    for entry in entries:
        require(type(entry) is dict and set(entry) <= {
            'manager', 'operation', 'apiVersion', 'time', 'fieldsType', 'fieldsV1', 'subresource'}
            and type(entry.get('manager')) is str and bool(entry['manager'])
            and entry.get('operation') in ('Apply', 'Update')
            and entry.get('fieldsType') == 'FieldsV1'
            and type(entry.get('fieldsV1')) is dict
            and type(entry.get('apiVersion')) is str and bool(entry['apiVersion']),
            'OBJECT_OWNERSHIP_INVALID')
        subresource = entry.get('subresource', '')
        require(subresource in ('', 'status'), 'OBJECT_SUBRESOURCE_UNKNOWN')
        if subresource == 'status':
            require(set(entry['fieldsV1']) <= {'f:status'}, 'OBJECT_STATUS_OWNERSHIP_INVALID')
            continue
        entry.pop('time', None)
        entry.pop('subresource', None)
        owners.append(core.encoded(entry))
    require(len(owners) == len(set(owners)), 'OBJECT_OWNERSHIP_DUPLICATE')
    meta['managedFields'] = [decode(x) for x in sorted(owners)]
    meta.pop('resourceVersion')
    obj.pop('status', None)
    return obj


def includes(actual, desired):
    # Server-defaulted dict fields may be present. Lists remain ordered and exact
    # in length; this conservative check does not emulate Kubernetes list merging.
    if type(actual) is not type(desired):
        return False
    if type(desired) is dict:
        return all(k in actual and includes(actual[k], v) for k, v in desired.items())
    if type(desired) is list:
        return len(actual) == len(desired) and all(includes(a, d) for a, d in zip(actual, desired))
    return actual == desired


class ObjectReconciler:
    """Bind immutable baseline and desired payloads to one run.

    Baseline admission/allowed-owner review is a caller prerequisite; equality
    does not make an existing object safe. No API, clock, credential or file I/O.
    Fresh read/deadline and initial provenance belong to the operational provider.
    """
    def __init__(self, binding, rendered, contract, validator, baseline):
        try:
            self.binding = binding
            self.payloads = core.prepare(binding, rendered, contract, validator)
            require(type(baseline) is tuple and len(baseline) == len(core.OBJECTS),
                    'OBJECT_SET_INVALID')
            # Store private immutable bytes, not caller-owned dict references.
            self.baseline = tuple(None if raw is None else core.encoded(
                normalized(decode(raw), key)) for key, raw in zip(core.OBJECTS, baseline))
        except VerificationError:
            raise
        except Exception:
            raise VerificationError('OBJECT_BASELINE_INVALID') from None

    def check(self, binding, progress, observations):
        """Compare a fresh six-object set against baseline plus durable ACKs.

        WRITE_PENDING before send is legitimate only while observation still
        matches baseline and reply count equals ACK count. A reply without its
        ACK is unresolved and can never promote the expected state.
        """
        try:
            require(binding == self.binding, 'OBJECT_BINDING_MISMATCH')
            require(type(progress) is Progress and type(progress.responses) is tuple,
                    'OBJECT_PROGRESS_INVALID')
            require(type(observations) is tuple and len(observations) == len(core.OBJECTS),
                    'OBJECT_SET_INVALID')
            count = 0
            if progress.journal is not None:
                journal = decode(progress.journal)
                store.receipt_checked(journal, binding.key)
                require(journal['status'] != 'STOPPED' and 'checkpoint_error' not in journal,
                        'OBJECT_RUN_STOPPED')
                count = len(journal['acknowledged'])
            require(len(progress.responses) == count, 'OBJECT_REPLY_ACK_MISMATCH')
            for i, (key, raw) in enumerate(zip(core.OBJECTS, observations)):
                expected = self.baseline[i]
                if i < count:
                    reply = decode(progress.responses[i])
                    core.scope_checked(reply, binding, response=True)
                    after = normalized(reply, key)
                    if expected is not None:
                        require(after['metadata']['uid'] == decode(expected)['metadata']['uid'],
                                'OBJECT_UID_REPLACED')
                    desired = decode(self.payloads[i])
                    require(includes(after['spec'], desired['spec'])
                            and includes(after['metadata'].get('labels', {}),
                                         desired['metadata'].get('labels', {}))
                            and includes(after['metadata'].get('annotations', {}),
                                         desired['metadata'].get('annotations', {})),
                            'OBJECT_APPLY_MISMATCH')
                    require(any(e['manager'] == 'fds-deployer' and e['operation'] == 'Apply'
                                and e['apiVersion'] == key[0] and e['fieldsV1'].get('f:spec') is not None
                                for e in after['metadata']['managedFields']),
                            'OBJECT_APPLY_OWNERSHIP_MISSING')
                    expected = core.encoded(after)
                observed = None if raw is None else decode(raw)
                if i < count and observed is not None:
                    core.scope_checked(observed, binding, response=True)
                actual = None if observed is None else core.encoded(normalized(observed, key))
                require(actual == expected, 'OBJECT_DRIFT')
            return progress.key
        except VerificationError:
            raise
        except Exception:
            raise VerificationError('OBJECT_RECONCILIATION_FAILED') from None
