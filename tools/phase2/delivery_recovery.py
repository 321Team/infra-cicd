"""Read-only P2 journal inspection after restart. No sender, token or unlock.

The JSON report is an offline diagnostic, NEVER a Lease/approval or proof of
cluster state. A supplied Binding identifies the record, not its authenticity.
"""
import argparse
from dataclasses import fields
import errno
import json
import os
import stat

import delivery_adapter as core
import delivery_store as store


class RecoveryError(RuntimeError):
    """Fixed diagnostic codes; no exception text or raw input is exposed."""


def need(ok, code):
    if not ok:
        raise RecoveryError(code)


def decode(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            need(key not in result, 'DUPLICATE_KEY')
            result[key] = value
        return result
    def constant(_):
        raise RecoveryError('JSON_INVALID')
    try:
        need(type(raw) is bytes and len(raw) <= 16384, 'INPUT_SIZE_INVALID')
        return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)
    except RecoveryError:
        raise
    except Exception:
        raise RecoveryError('JSON_INVALID') from None


def binding_from_bytes(raw):
    try:
        data = decode(raw)
        need(type(data) is dict and set(data) == {f.name for f in fields(core.Binding)},
             'BINDING_FIELDS_INVALID')
        need(type(data['images']) is list and all(type(p) is list for p in data['images']),
             'BINDING_INVALID')
        data['images'] = tuple(tuple(p) for p in data['images'])
        binding = core.Binding(**data)
        core.binding_checked(binding)
        return binding
    except RecoveryError:
        raise
    except Exception:
        raise RecoveryError('BINDING_INVALID') from None


def replay(records, binding_key, *, binding=None):
    """Reconstruct validated receipt state in memory, never reopen a writer."""
    need(type(records) is list and len(records) <= 64, 'JOURNAL_LIMIT_INVALID')
    previous_hash = latest = None
    for index, raw in enumerate(records, 1):
        record = decode(raw)
        need(type(record) is dict and set(record) == {
            'schema', 'sequence', 'binding_key', 'previous_sha256', 'receipt'}, 'RECORD_FIELDS_INVALID')
        need(record['schema'] == 'P2-CICD-journal-1'
             and type(record['sequence']) is int and record['sequence'] == index
             and record['binding_key'] == binding_key
             and record['previous_sha256'] == previous_hash, 'JOURNAL_CHAIN_INVALID')
        try:
            need(core.encoded(record) == raw, 'RECORD_ENCODING_INVALID')
            current = store.receipt_checked(record['receipt'], binding_key, binding=binding)
            store.transition_checked(latest, current, binding=binding)
        except RecoveryError:
            raise
        except Exception:
            raise RecoveryError('RECEIPT_OR_TRANSITION_INVALID') from None
        latest, previous_hash = current, core.digest(raw)
    return latest, previous_hash


def decision(binding, records, *, active, complete):
    """Content-only inspection shared by the POSIX reader and offline tests."""
    latest, last_hash = replay(records, binding.key, binding=binding)
    if complete is not None:
        expected = {'schema': 'P2-CICD-complete-1', 'binding_key': binding.key,
                    'last_checkpoint_sha256': last_hash}
        need(complete == core.encoded(expected) and latest is not None
             and latest['status'] == 'ROLLOUT_OBSERVED', 'COMPLETE_INVALID')
    if latest is None:
        state = 'NO_DURABLE_INTENT' if active else 'INCOMPLETE_FENCE'
    elif not active:
        state = 'COMPLETED_RECORD' if complete is not None else 'INCOMPLETE_FENCE'
    elif latest['status'] == 'ROLLOUT_OBSERVED':
        state = 'TERMINAL_FENCE_RETAINED'
    elif latest['pending'] is not None:
        state = 'WRITE_OUTCOME_UNCONFIRMED'
    else:
        state = 'INTERRUPTED_RECORD'
    return {
        'schema': 'P2-CICD-recovery-inspection-1', 'binding_key': binding.key,
        'state': state, 'record_count': len(records), 'last_checkpoint_sha256': last_hash,
        'latest_receipt': latest, 'active_fence_present': active,
        'complete_marker_present': complete is not None,
        'automatic_resume_allowed': False, 'unlock_performed': False,
        'runtime_state': 'NOT_VERIFIED', 'authorization': 'NOT_VERIFIED',
        'delivery_gate': 'NOT_VERIFIED',
        'next_action': 'OWNER_REVIEW_WITH_EXACT_TARGET_READBACK',
    }


def read_file(fd, name, *, optional=False):
    try:
        handle = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK, dir_fd=fd)
    except FileNotFoundError:
        if optional:
            return None
        raise
    try:
        info = store.regular(handle)
        need(info.st_size <= 16384, 'INPUT_SIZE_INVALID')
        raw = os.pread(handle, 16385, 0)
        need(len(raw) == info.st_size, 'FILE_CHANGED')
        store.file_check(fd, name, raw, store.inode(info))
        return raw
    finally:
        os.close(handle)


def inspect(root, binding):
    """Lock EXISTING P2 target mutex nonblocking and only read stored artifacts.

    No O_CREAT, mkdir, unlink, checkpoint/claim completion or network access.
    Refuses Windows and an active writer before reading its journal.
    """
    need(os.name == 'posix', 'PLATFORM_UNSUPPORTED')
    import fcntl
    fd = lock = journal = None
    try:
        core.binding_checked(binding)
        path, fd = store.root_open(root)
        target = core.digest(core.encoded([binding.cluster_id, core.NAMESPACE, binding.namespace_uid]))
        run_key = core.digest(core.encoded([target, binding.run_id]))
        lock_name = target + '.lock'
        lock = os.open(lock_name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK, dir_fd=fd)
        lock_inode = store.inode(store.regular(lock))
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno in (errno.EAGAIN, errno.EACCES):
                raise RecoveryError('TARGET_BUSY') from None
            raise
        claim = core.encoded({'schema': 'P2-CICD-claim-1', 'binding_key': binding.key,
                              'target_key': target, 'run_key': run_key})
        need(read_file(fd, run_key + '.claim.json') == claim, 'CLAIM_MISMATCH')
        active_raw = read_file(fd, target + '.active.json', optional=True)
        if active_raw is not None:
            need(active_raw == core.encoded({'schema': 'P2-CICD-active-1',
                 'binding_key': binding.key, 'claim_sha256': core.digest(claim)}),
                 'ACTIVE_BINDING_MISMATCH')
        complete = read_file(fd, binding.key + '.complete.json', optional=True)
        records = []
        dirname = binding.key + '.journal'
        try:
            journal = os.open(dirname, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW |
                              os.O_CLOEXEC, dir_fd=fd)
        except FileNotFoundError:
            pass
        if journal is not None:
            journal_inode = store.inode(store.directory(journal))
            names = sorted(os.listdir(journal))
            need(len(names) <= 64 and names == [f'{n:04d}.json' for n in range(1, len(names) + 1)],
                 'JOURNAL_INVENTORY_INVALID')
            records = [read_file(journal, name) for name in names]
            need(sorted(os.listdir(journal)) == names, 'JOURNAL_CHANGED')
            need(journal_inode == store.inode(os.stat(dirname, dir_fd=fd, follow_symlinks=False)),
                 'JOURNAL_CHANGED')
        result = decision(binding, records, active=active_raw is not None, complete=complete)
        need(store.inode(store.directory(fd)) == store.inode(path.lstat()), 'ROOT_CHANGED')
        need(lock_inode == store.inode(store.regular(lock)) == store.inode(
            os.stat(lock_name, dir_fd=fd, follow_symlinks=False)), 'LOCK_CHANGED')
        return result
    except RecoveryError:
        raise
    except Exception:
        raise RecoveryError('STORE_INSPECTION_FAILED') from None
    finally:
        for handle in (journal, lock, fd):
            if handle is not None:
                os.close(handle)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True, help='Existing private P2 store, never a P1 store')
    parser.add_argument('--binding', required=True, help='Original non-secret Binding JSON; not approval')
    args = parser.parse_args(argv)
    try:
        # Bound read, even if a supplied file is unexpectedly large.
        handle = os.open(args.binding, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) |
                         getattr(os, 'O_NONBLOCK', 0) | getattr(os, 'O_BINARY', 0))
        try:
            need(stat.S_ISREG(os.fstat(handle).st_mode), 'BINDING_FILE_INVALID')
            binding = binding_from_bytes(os.read(handle, 16385))
        finally:
            os.close(handle)
        result = inspect(args.root, binding)
        print(json.dumps(result, sort_keys=True))
        return 0  # Inspection succeeded, NOT deployment/recovery authorization.
    except Exception as exc:
        code = str(exc) if type(exc) is RecoveryError else 'INSPECTION_FAILED'
        print(json.dumps({'schema': 'P2-CICD-recovery-inspection-1', 'state': 'BLOCKED',
                          'error_code': code, 'automatic_resume_allowed': False,
                          'runtime_state': 'NOT_VERIFIED', 'authorization': 'NOT_VERIFIED'}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
