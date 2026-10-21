"""P2 POSIX single-host claim and immutable write-ahead journal.

Uses one fixed private root shared by ALL P2 executions on that Runner.
Not an approval verifier or a distributed lock. No automatic stale-lock cleanup,
resume, credential storage, or P1 imports. Interrupted files remain for review.
"""
import copy
import errno
import hashlib
import json
import os
import stat
from pathlib import Path

import delivery_adapter as core

ERRORS = frozenset('''BINDING_INVALID ACTION_INVALID IDENTITY_INVALID HASH_INVALID
SOURCE_INVALID API_INVALID ROUTE_HOST_INVALID IMAGES_INVALID OBJECT_SCOPE_INVALID SERVICE_EXPOSURE_FORBIDDEN
NAMESPACE_INVALID RENDER_METADATA_INVALID ROUTE_CONTRACT_MISMATCH IMAGE_CONTRACT_MISMATCH
POD_IDENTITY_INVALID EGRESS_LABEL_INVALID END_PAGE_INPUT_FORBIDDEN INPUT_SIZE_INVALID INPUT_HASH_MISMATCH
RENDER_VALIDATOR_FAILED RENDER_CHECK_FAILED OBJECT_SET_INVALID DUPLICATE_OBJECT INPUT_INVALID
CHECKPOINT_FAILED CLOCK_UNAVAILABLE CLOCK_INVALID AUTHORIZATION_DENIED TOKEN_REQUEST_INVALID
CREDENTIAL_INVALID AUTH_WINDOW_INVALID AUTHORITY_CHANGED CLOCK_REVERSED AUTH_EXPIRED
SESSION_INACTIVE METHOD_INVALID REQUEST_SCOPE_INVALID FIRST_WRITE_WINDOW_TOO_SHORT
API_REJECTED WRITE_OUTCOME_UNKNOWN READ_FAILED READ_DEADLINE_EXCEEDED WRITE_RESPONSE_INVALID
READ_RESPONSE_INVALID DEPLOYMENT_RESPONSE_INVALID ROLLOUT_TIMEOUT DEPLOYMENT_DRIFT
PROGRESS_DEADLINE_EXCEEDED WAIT_FAILED ADAPTER_ALREADY_USED CORE_INPUT_OR_OBSERVATION_INVALID'''.split())
FIELDS = frozenset(('schema', 'status', 'binding_key', 'acknowledged', 'pending', 'rollout',
                    'error_code', 'delivery_gate', 'health_e2e', 'pod_image_id'))
IDENTITIES = tuple('/'.join(key) for key in core.OBJECTS)


class StoreError(RuntimeError):
    """Static codes only; preserve failed files, never include paths or raw bytes."""


def need(ok, code):
    if not ok:
        raise StoreError(code)


def regular(fd):
    info = os.fstat(fd)
    need(stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid()
         and stat.S_IMODE(info.st_mode) == 0o600 and info.st_nlink == 1, 'FILE_UNSAFE')
    return info


def directory(fd):
    info = os.fstat(fd)
    need(stat.S_ISDIR(info.st_mode) and info.st_uid == os.geteuid()
         and stat.S_IMODE(info.st_mode) == 0o700, 'DIRECTORY_UNSAFE')
    return info


def inode(info):
    return info.st_dev, info.st_ino


def root_open(root):
    path = Path(os.path.abspath(root))
    for node in (path, *path.parents):
        info = node.lstat()
        need(stat.S_ISDIR(info.st_mode) and info.st_uid in (0, os.geteuid())
             and not info.st_mode & 0o022, 'ROOT_PARENT_UNSAFE')
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        need(inode(directory(fd)) == inode(path.lstat()), 'ROOT_CHANGED')
        os.fsync(fd)  # Unsupported durability semantics fail BEFORE any claim.
        return path, fd
    except Exception:
        os.close(fd)
        raise


def file_check(parent, name, raw, expected=None):
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent)
    try:
        info = regular(fd)
        need(inode(info) == inode(os.stat(name, dir_fd=parent, follow_symlinks=False)),
             'FILE_CHANGED')
        need(expected is None or inode(info) == expected, 'FILE_CHANGED')
        need(os.pread(fd, len(raw) + 1, 0) == raw, 'FILE_BYTES_CHANGED')
        return inode(info)
    finally:
        os.close(fd)


def create_file(parent, name, raw):
    """O_EXCL and fsync; a partially created file is never removed or overwritten."""
    fd = os.open(name, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                 0o600, dir_fd=parent)
    try:
        regular(fd)
        offset = 0
        while offset < len(raw):
            size = os.write(fd, raw[offset:])
            need(size > 0, 'WRITE_FAILED')
            offset += size
        os.fsync(fd)
        expected = inode(regular(fd))
        file_check(parent, name, raw, expected)
        os.fsync(parent)
        return expected
    finally:
        os.close(fd)


def receipt_checked(receipt, binding_key, *, binding=None):
    # Without authenticated Binding context accept only the legacy six-object
    # shape. Never infer a larger scope from untrusted receipt/ACK contents.
    if binding is not None:
        core.binding_checked(binding)
        need(binding.key == binding_key, 'RECEIPT_BINDING_INVALID')
    objects = core.OBJECTS if binding is None else core.objects_for(binding)
    services = core.SERVICES if binding is None else core.services_for(binding)
    identities = tuple('/'.join(key) for key in objects)
    count = len(objects)
    need(type(receipt) is dict and (set(receipt) == FIELDS or
         set(receipt) == FIELDS | {'checkpoint_error'}), 'RECEIPT_FIELDS_INVALID')
    need(receipt['schema'] == 'P2-CICD-apply-core-1' and receipt['binding_key'] == binding_key,
         'RECEIPT_BINDING_INVALID')
    need(receipt['status'] in ('PREPARED', 'WRITE_PENDING', 'WRITE_ACKNOWLEDGED',
                              'ROLLOUT_OBSERVED', 'STOPPED'), 'RECEIPT_STATUS_INVALID')
    need(all(receipt[k] == 'NOT_VERIFIED' for k in ('delivery_gate', 'health_e2e', 'pod_image_id')),
         'RECEIPT_ACCEPTANCE_INVALID')
    need(receipt['error_code'] is None or (type(receipt['error_code']) is str
                                         and receipt['error_code'] in ERRORS), 'RECEIPT_ERROR_INVALID')
    need('checkpoint_error' not in receipt or receipt['checkpoint_error'] == 'CHECKPOINT_FAILED',
         'RECEIPT_ERROR_INVALID')
    ack, rollout = receipt['acknowledged'], receipt['rollout']
    need(type(ack) is list and len(ack) <= count and ack == list(identities[:len(ack)]),
         'RECEIPT_ACK_INVALID')
    need(type(rollout) is list and len(rollout) <= len(services)
         and rollout == list(services[:len(rollout)]), 'RECEIPT_ROLLOUT_INVALID')
    pending = receipt['pending']
    need(pending is None or (len(ack) < count and pending == identities[len(ack)]),
         'RECEIPT_PENDING_INVALID')
    if receipt['status'] == 'ROLLOUT_OBSERVED':
        need(len(ack) == count and len(rollout) == len(services) and pending is None
             and receipt['error_code'] is None and 'checkpoint_error' not in receipt,
             'RECEIPT_TERMINAL_INVALID')
    need(not rollout or len(ack) == count, 'RECEIPT_ROLLOUT_INVALID')
    if receipt['status'] == 'PREPARED':
        need(not ack and not rollout and pending is None, 'RECEIPT_PHASE_INVALID')
    if receipt['status'] == 'WRITE_PENDING':
        need(pending is not None and not rollout, 'RECEIPT_PHASE_INVALID')
    if receipt['status'] == 'WRITE_ACKNOWLEDGED':
        need(ack and pending is None, 'RECEIPT_PHASE_INVALID')
    need(receipt['status'] == 'STOPPED' or receipt['error_code'] is None, 'RECEIPT_PHASE_INVALID')
    return copy.deepcopy(receipt)


def transition_checked(previous, current, *, binding=None):
    count = len(core.OBJECTS if binding is None else core.objects_for(binding))
    if previous is None:
        need(current['status'] in ('PREPARED', 'STOPPED')
             and not current['acknowledged'] and not current['rollout']
             and current['pending'] is None, 'JOURNAL_INITIAL_INVALID')
        return
    need(previous['status'] not in ('STOPPED', 'ROLLOUT_OBSERVED'), 'JOURNAL_TERMINAL')
    before, after = previous['acknowledged'], current['acknowledged']
    rolls, next_rolls = previous['rollout'], current['rollout']
    need(after[:len(before)] == before and next_rolls[:len(rolls)] == rolls,
         'RECEIPT_REGRESSION')
    if current['status'] == 'STOPPED':
        need(after == before and next_rolls == rolls, 'JOURNAL_TRANSITION_INVALID')
    elif current['status'] == 'WRITE_PENDING':
        need(previous['status'] in ('PREPARED', 'WRITE_ACKNOWLEDGED')
             and after == before and next_rolls == rolls, 'JOURNAL_TRANSITION_INVALID')
    elif current['status'] == 'WRITE_ACKNOWLEDGED':
        need((previous['status'] == 'WRITE_PENDING' and len(after) == len(before) + 1
              and next_rolls == rolls)
             or (previous['status'] == 'WRITE_ACKNOWLEDGED' and len(after) == len(before) == count
                 and len(next_rolls) == len(rolls) + 1), 'JOURNAL_TRANSITION_INVALID')
    elif current['status'] == 'ROLLOUT_OBSERVED':
        need(previous['status'] == 'WRITE_ACKNOWLEDGED' and after == before
             and next_rolls == rolls, 'JOURNAL_TRANSITION_INVALID')
    else:
        raise StoreError('JOURNAL_TRANSITION_INVALID')


class Claim:
    def __init__(self, path, root_fd, lock_fd, lock_name, binding, files, active_name):
        self.path, self.root_fd, self.lock_fd = path, root_fd, lock_fd
        self.lock_name, self.binding, self.files = lock_name, binding, files
        self.lock_inode = inode(regular(lock_fd))
        self.active_name = active_name
        self.closed = self.completed = False

    def check(self):
        try:
            need(not self.closed and not self.completed, 'CLAIM_UNAVAILABLE')
            need(inode(directory(self.root_fd)) == inode(self.path.lstat()), 'ROOT_CHANGED')
            need(inode(regular(self.lock_fd)) == self.lock_inode
                 == inode(os.stat(self.lock_name, dir_fd=self.root_fd, follow_symlinks=False)),
                 'LOCK_CHANGED')
            for name, raw, expected in self.files:
                file_check(self.root_fd, name, raw, expected)
        except StoreError:
            raise
        except Exception:
            raise StoreError('CLAIM_CHECK_FAILED') from None

    def complete(self, journal):
        """Explicit normal completion only, never called by Adapter automatically.

        Integrator calls after its full workflow stops needing exclusive access.
        A stopped/ambiguous journal cannot release the active target fence here.
        Manual reconciliation is required; no recovery/force-unlock CLI exists.
        """
        try:
            self.check()
            need(type(journal) is Journal and journal.claim is self, 'JOURNAL_MISMATCH')
            journal.check()
            need(journal.records and journal.latest['status'] == 'ROLLOUT_OBSERVED',
                 'RECONCILIATION_REQUIRED')
            # Keep a durable terminal marker BEFORE removing the active fence.
            name = self.binding.key + '.complete.json'
            raw = core.encoded({'schema': 'P2-CICD-complete-1', 'binding_key': self.binding.key,
                                'last_checkpoint_sha256': core.digest(journal.records[-1][1])})
            create_file(self.root_fd, name, raw)
            self.check()
            os.unlink(self.active_name, dir_fd=self.root_fd)
            os.fsync(self.root_fd)
            self.completed = True
        except StoreError:
            raise
        except Exception:
            raise StoreError('CLAIM_COMPLETE_FAILED') from None

    def close(self):
        if not self.closed:
            self.closed = True
            # flock releases, but active fence and consumed RUN claim remain unless complete succeeded.
            os.close(self.lock_fd)
            os.close(self.root_fd)


def acquire(root, binding):
    need(os.name == 'posix', 'PLATFORM_UNSUPPORTED')
    import fcntl
    try:
        core.binding_checked(binding)
    except Exception:
        raise StoreError('BINDING_INVALID') from None
    fd = lock = None
    try:
        path, fd = root_open(root)
        # API aliases must not create independent mutexes for the same cluster/namespace.
        target = core.digest(core.encoded([binding.cluster_id, core.NAMESPACE, binding.namespace_uid]))
        lock_name, active_name = target + '.lock', target + '.active.json'
        lock = os.open(lock_name, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC,
                       0o600, dir_fd=fd)
        regular(lock)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno in (errno.EAGAIN, errno.EACCES):
                raise StoreError('TARGET_BUSY') from None
            raise
        try:
            os.stat(active_name, dir_fd=fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise StoreError('TARGET_RECONCILIATION_REQUIRED')
        # Same RUN with altered render/action is still a consumed RUN on this target.
        run_key = core.digest(core.encoded([target, binding.run_id]))
        claim_name = run_key + '.claim.json'
        raw = core.encoded({'schema': 'P2-CICD-claim-1', 'binding_key': binding.key,
                            'target_key': target, 'run_key': run_key})
        try:
            claim_inode = create_file(fd, claim_name, raw)
        except FileExistsError:
            raise StoreError('RUN_ALREADY_CLAIMED') from None
        active_raw = core.encoded({'schema': 'P2-CICD-active-1', 'binding_key': binding.key,
                                   'claim_sha256': core.digest(raw)})
        active_inode = create_file(fd, active_name, active_raw)
        result = Claim(path, fd, lock, lock_name, binding,
                       [(claim_name, raw, claim_inode), (active_name, active_raw, active_inode)], active_name)
        result.check()
        fd = lock = None
        return result
    except StoreError:
        raise
    except Exception:
        raise StoreError('CLAIM_ACQUIRE_FAILED') from None
    finally:
        for descriptor in (lock, fd):
            if descriptor is not None:
                os.close(descriptor)


class Journal:
    def __init__(self, claim):
        need(os.name == 'posix', 'PLATFORM_UNSUPPORTED')
        need(type(claim) is Claim, 'CLAIM_REQUIRED')
        self.claim, self.fd = claim, None
        self.failed = self.closed = False
        self.records, self.latest = [], None
        self.dirname = claim.binding.key + '.journal'
        try:
            claim.check()
            os.mkdir(self.dirname, 0o700, dir_fd=claim.root_fd)
            self.fd = os.open(self.dirname, os.O_RDONLY | os.O_DIRECTORY |
                              os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=claim.root_fd)
            directory(self.fd)
            os.fsync(self.fd)
            os.fsync(claim.root_fd)
            self.check()
        except Exception:
            self.failed = True
            if self.fd is not None:
                os.close(self.fd)
                self.fd = None
            raise StoreError('JOURNAL_OPEN_FAILED') from None

    def check(self):
        try:
            need(not self.closed and not self.failed, 'JOURNAL_UNAVAILABLE')
            self.claim.check()
            need(inode(directory(self.fd)) == inode(os.stat(
                self.dirname, dir_fd=self.claim.root_fd, follow_symlinks=False)), 'JOURNAL_CHANGED')
            need(set(os.listdir(self.fd)) == {r[0] for r in self.records}, 'JOURNAL_INVENTORY_CHANGED')
            for name, raw, expected in self.records:
                file_check(self.fd, name, raw, expected)
        except StoreError:
            raise
        except Exception:
            raise StoreError('JOURNAL_CHECK_FAILED') from None

    def __call__(self, receipt):
        try:
            self.check()
            need(len(self.records) < 64, 'JOURNAL_LIMIT_REACHED')
            safe = receipt_checked(receipt, self.claim.binding.key, binding=self.claim.binding)
            transition_checked(self.latest, safe, binding=self.claim.binding)
            number = len(self.records) + 1
            raw = core.encoded({'schema': 'P2-CICD-journal-1', 'sequence': number,
                                'binding_key': self.claim.binding.key,
                                'previous_sha256': core.digest(self.records[-1][1]) if self.records else None,
                                'receipt': safe})
            need(len(raw) <= 16384, 'RECORD_TOO_LARGE')
            name = f'{number:04d}.json'
            expected = create_file(self.fd, name, raw)
            self.records.append((name, raw, expected))
            self.latest = safe
            self.check()
        except Exception:
            self.failed = True
            raise StoreError('JOURNAL_SAVE_FAILED') from None

    def close(self):
        if not self.closed:
            self.closed = True
            if self.fd is not None:
                os.close(self.fd)
