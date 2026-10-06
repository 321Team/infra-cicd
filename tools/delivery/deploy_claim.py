"""POSIX local execution mutex and durable, single-use plan claim.

Not an approval verifier or runtime entry point. Use one fixed trusted directory
on the single Runner. Never delete/reuse a consumed or partially written claim.
"""
import datetime as dt
import errno
import hashlib
import json
import os
import stat
from pathlib import Path

import deploy_binding as b
import delivery_gate as gate


class ClaimError(RuntimeError):
    """Only static codes; filesystem exceptions must not leak into receipts."""


def need(ok, code):
    if not ok:
        raise ClaimError(code)


def regular(fd):
    info = os.fstat(fd)
    need(stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid() and
         info.st_nlink == 1 and stat.S_IMODE(info.st_mode) == 0o600,
         'CLAIM_FILE_UNSAFE')
    return info


def directory(fd):
    info = os.fstat(fd)
    need(stat.S_ISDIR(info.st_mode) and info.st_uid == os.geteuid() and
         stat.S_IMODE(info.st_mode) == 0o700, 'CLAIM_DIRECTORY_UNSAFE')
    return info


def open_root(root):
    path = Path(os.path.abspath(root))
    for node in (path, *path.parents):
        info = node.lstat()
        need(stat.S_ISDIR(info.st_mode) and
             info.st_uid in (0, os.geteuid()) and not info.st_mode & 0o022,
             'CLAIM_PARENT_UNSAFE')
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        opened = directory(fd)
        current = path.lstat()
        need((opened.st_dev, opened.st_ino) == (current.st_dev, current.st_ino),
             'CLAIM_DIRECTORY_CHANGED')
        return fd
    except Exception:
        os.close(fd)
        raise


def write_all(fd, raw):
    offset = 0
    while offset < len(raw):
        size = os.write(fd, raw[offset:])
        need(size > 0, 'CLAIM_WRITE_FAILED')
        offset += size


class Claim:
    def __init__(self, root_fd, lock_fd, claim_fd, filename, raw, record):
        self.root_fd, self.lock_fd, self.claim_fd = root_fd, lock_fd, claim_fd
        self.filename, self.raw, self.record = filename, raw, record
        self.sha256 = hashlib.sha256(raw).hexdigest()
        self.closed = False

    def check(self):
        """Check retained handles, named files and claim bytes before each operation."""
        try:
            need(not self.closed, 'CLAIM_CLOSED')
            directory(self.root_fd)
            for filename, fd in (('deploy.lock', self.lock_fd),
                                 (self.filename, self.claim_fd)):
                info = regular(fd)
                named = os.stat(filename, dir_fd=self.root_fd, follow_symlinks=False)
                need((info.st_dev, info.st_ino) == (named.st_dev, named.st_ino),
                     'CLAIM_FILE_CHANGED')
            need(os.pread(self.claim_fd, len(self.raw) + 1, 0) == self.raw,
                 'CLAIM_BYTES_CHANGED')
        except ClaimError:
            raise
        except Exception:
            raise ClaimError('CLAIM_CHECK_FAILED') from None

    def close(self):
        if not self.closed:
            self.closed = True
            # Closing the locked descriptor releases flock; the claim stays.
            for fd in (self.claim_fd, self.lock_fd, self.root_fd):
                os.close(fd)

    def __enter__(self):
        self.check()
        return self

    def __exit__(self, *_):
        self.close()
        return False


def acquire(root, plan_sha256, approval_sha256, execution):
    """Hold the global mutex until close; permanently consume this exact plan.

    Call only after independent approval and all input validation. A successful
    claim does not itself authorize a Deploy. No credentials are accepted.
    """
    need(os.name == 'posix', 'CLAIM_PLATFORM_UNSUPPORTED')
    import fcntl

    need(all(type(value) is str and gate.SHA256.fullmatch(value)
             for value in (plan_sha256, approval_sha256)), 'CLAIM_HASH_INVALID')
    try:
        actual = b.identity(execution)
    except b.Invalid:
        raise ClaimError('CLAIM_EXECUTION_INVALID') from None
    need(actual['attempt'] == 1, 'CLAIM_RETRY_NOT_APPROVED')
    root_fd = lock_fd = claim_fd = None
    try:
        root_fd = open_root(root)
        lock_fd = os.open('deploy.lock', os.O_RDWR | os.O_CREAT |
                          os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=root_fd)
        regular(lock_fd)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno in (errno.EAGAIN, errno.EACCES):
                raise ClaimError('DEPLOYMENT_LOCK_BUSY') from None
            raise
        filename = plan_sha256 + '.claim.json'
        try:
            claim_fd = os.open(filename, os.O_RDWR | os.O_CREAT | os.O_EXCL |
                               os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=root_fd)
        except FileExistsError:
            raise ClaimError('PLAN_ALREADY_CLAIMED') from None
        regular(claim_fd)
        record = {
            'schema': 'P1-CICD-deploy-claim-2', 'state': 'CLAIMED',
            'plan_sha256': plan_sha256, 'approval_sha256': approval_sha256,
            'execution': actual, 'claimed_at': dt.datetime.now(dt.timezone.utc).isoformat(),
            'DELIVERY_GATE': 'NOT_VERIFIED',
        }
        raw = (json.dumps(record, sort_keys=True, separators=(',', ':')) + '\n').encode()
        write_all(claim_fd, raw)
        os.fsync(claim_fd)
        os.fsync(root_fd)
        result = Claim(root_fd, lock_fd, claim_fd, filename, raw, record)
        result.check()
        root_fd = lock_fd = claim_fd = None
        return result
    except ClaimError:
        raise
    except Exception:
        raise ClaimError('CLAIM_IO_FAILED') from None
    finally:
        for fd in (claim_fd, lock_fd, root_fd):
            if fd is not None:
                os.close(fd)
