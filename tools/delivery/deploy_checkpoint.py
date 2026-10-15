"""POSIX immutable checkpoint journal under an already-held deploy Claim.

No automatic resume, overwrite, deletion or recovery. Files created before an
I/O failure are preserved and this writer is permanently failed.
"""
import copy
import hashlib
import json
import os
import re
import stat

import deploy_adapter as core
import deploy_approval
import deploy_binding as b
import deploy_claim as c
from deploy_execution import STATIC_ERRORS

CORE_ERRORS = frozenset({
    'NONE', 'TARGETS_INVALID', 'TARGET_SCOPE_INVALID', 'UID_INVALID', 'IMAGE_INVALID',
    'NO_IMAGE_CHANGE', 'DUPLICATE_UID', 'OBJECT_KIND_INVALID', 'TARGET_IDENTITY_DRIFT',
    'TARGET_DELETING', 'RESOURCE_VERSION_INVALID', 'GENERATION_INVALID',
    'REPLICAS_INVALID', 'DEPLOYMENT_PAUSED', 'CONTAINERS_INVALID',
    'CONTAINER_IDENTITY_INVALID', 'IMAGE_DRIFT', 'SPEC_DRIFT', 'GENERATION_DRIFT',
    'OBJECT_INVALID', 'STATUS_INVALID', 'CONDITIONS_INVALID',
    'PROGRESS_DEADLINE_EXCEEDED', 'GET_FAILED', 'READINESS_TIMEOUT',
    'CHECKPOINT_FAILED', 'AUTHORIZATION_DENIED', 'BASELINE_NOT_READY',
    'PATCH_REJECTED', 'PATCH_OUTCOME_UNKNOWN', 'FINAL_READINESS_LOST',
    'ROLLBACK_READINESS_LOST', 'INPUT_BINDING_REJECTED', 'EXECUTION_FAILED',
    'EXECUTION_HELPER_FAILED', 'AUTHORITY_CHECK_FAILED',
}) | STATIC_ERRORS

FIELDS = frozenset({
    'schema', 'ROLLOUT', 'ROLLBACK', 'DELIVERY_GATE', 'HEALTH_E2E', 'POD_IMAGE_ID',
    'images', 'error_code', 'execution', 'plan_sha256', 'approval_sha256',
    'push_receipt_sha256', 'authority_error_code', 'DEPLOY_APPROVAL_REFERENCE',
    'claim', 'approval_reference', 'CREDENTIAL_METADATA', 'SOURCE_APPROVAL',
})
OPERATION_FIELDS = frozenset({'operation', 'recovery_basis'})
DEPLOY_STATES = frozenset({
    'NOT_EXECUTED', 'PATCH_PENDING', 'REJECTED', 'UNKNOWN', 'APPLIED', 'READY',
    'NOT_SENT_AUTH_DENIED', 'NOT_SENT_CREDENTIAL_WINDOW', 'NOT_SENT_AUTH_WINDOW',
})


class CheckpointError(RuntimeError):
    """Only static codes, never filesystem exception strings."""


def need(ok, code='CHECKPOINT_RECORD_INVALID'):
    if not ok:
        raise CheckpointError(code)


def summary_checked(value, service):
    need(type(value) is dict and set(value) == {
        'uid', 'resource_version', 'generation', 'replicas', 'image', 'spec_sha256'})
    need(type(value['uid']) is str and re.fullmatch(
        r'[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}', value['uid']))
    rv = value['resource_version']  # Opaque API concurrency token.
    need(type(rv) is str and 0 < len(rv) <= 200 and all(ord(x) >= 32 for x in rv))
    need(all(type(value[k]) is int and value[k] > 0 for k in ('generation', 'replicas')))
    need(type(value['image']) is str and re.fullmatch(
        re.escape(core.REGISTRY_PREFIX + service) + r'@sha256:[0-9a-f]{64}', value['image']))
    need(type(value['spec_sha256']) is str and b.gate.SHA256.fullmatch(value['spec_sha256']))


def receipt_checked(receipt, claim):
    need(type(receipt) is dict)
    keys = set(receipt) - {'rollback_error_code'}
    need(keys == FIELDS or keys == FIELDS | OPERATION_FIELDS)
    if keys == FIELDS | OPERATION_FIELDS:
        operation_checked(receipt)
    need(receipt['schema'] == 'P1-CICD-deploy-execution-2')
    for field in ('DELIVERY_GATE', 'HEALTH_E2E', 'POD_IMAGE_ID'):
        need(receipt[field] == 'NOT_VERIFIED')
    need(receipt['SOURCE_APPROVAL'] == 'NOT_INDEPENDENTLY_VERIFIED'
         and receipt['CREDENTIAL_METADATA'] == 'CALLER_SUPPLIED_NOT_VERIFIED_HERE')
    need(receipt['DEPLOY_APPROVAL_REFERENCE'] in (
        'NOT_VERIFIED', 'MATCHED_AT_LAST_SUCCESSFUL_LOOKUP'))
    need(receipt['ROLLOUT'] in ('NOT_EXECUTED', 'IN_PROGRESS', 'READY', 'FAILED'))
    need(receipt['ROLLBACK'] in (
        'NOT_EXECUTED', 'IN_PROGRESS', 'MANUAL_RECONCILIATION_REQUIRED',
        'NOT_AUTHORIZED_OR_NOT_REQUESTED', 'READY_HEALTH_NOT_VERIFIED'))
    for field in ('error_code', 'authority_error_code', 'rollback_error_code'):
        if field in receipt:
            need(type(receipt[field]) is str and receipt[field] in CORE_ERRORS)
    need(receipt['execution'] == claim.record['execution'])
    need(receipt['plan_sha256'] == claim.record['plan_sha256']
         and receipt['approval_sha256'] == claim.record['approval_sha256'])
    need(type(receipt['push_receipt_sha256']) is str
         and b.gate.SHA256.fullmatch(receipt['push_receipt_sha256']))
    need(receipt['claim'] == {
        'state': 'CLAIMED', 'sha256': claim.sha256, 'execution': claim.record['execution']})
    deploy_approval.endpoint(receipt['approval_reference'])
    images = receipt['images']
    need(type(images) is dict and set(images) <= set(core.SERVICES))
    for service, item in images.items():
        need(type(item) is dict and {'before', 'deploy', 'rollback'} <= set(item)
             and set(item) <= {'before', 'deploy', 'rollback', 'deploy_after',
                               'rollback_after', 'ready'})
        need(item['deploy'] in DEPLOY_STATES and item['rollback'] in DEPLOY_STATES)
        for field in ('before', 'deploy_after', 'rollback_after', 'ready'):
            if field in item:
                summary_checked(item[field], service)
    # Reject unknown/raw fields rather than writing env, kubeconfig or token data.
    return copy.deepcopy(receipt)


def operation_checked(receipt):
    """Allow only projected recovery metadata, never embedded plan/approval bytes."""
    operation, origin = receipt['operation'], receipt['recovery_basis']
    need(operation in ('DEPLOY_PUSHED', 'RESTORE_PREVIOUS'))
    if operation == 'DEPLOY_PUSHED':
        need(origin is None)
        return
    need(type(origin) is dict and set(origin) == {
        'origin_plan_sha256', 'origin_approval_sha256', 'origin_execution',
        'evidence_reference', 'historical_approval_verification', 'historical_runtime'})
    for field in ('origin_plan_sha256', 'origin_approval_sha256'):
        need(type(origin[field]) is str and b.gate.SHA256.fullmatch(origin[field]))
    need(origin['historical_approval_verification'] == 'SUPPLIED_BYTES_ONLY'
         and origin['historical_runtime'] == 'NOT_VERIFIED')
    need(type(origin['evidence_reference']) is str
         and b.REFERENCE.fullmatch(origin['evidence_reference']))
    try:
        previous = b.identity(origin['origin_execution'])
        current = b.identity(receipt['execution'])
    except b.Invalid:
        raise CheckpointError('CHECKPOINT_RECORD_INVALID') from None
    need(previous['attempt'] == 1 and previous['run_id'] != current['run_id'])


class Journal:
    def __init__(self, claim):
        need(os.name == 'posix', 'CHECKPOINT_PLATFORM_UNSUPPORTED')
        need(isinstance(claim, c.Claim), 'CHECKPOINT_CLAIM_REQUIRED')
        self.claim, self.fd = claim, None
        self.failed = self.closed = False
        self.records = []
        self.dirname = claim.record['plan_sha256'] + '.checkpoints'
        try:
            claim.check()
            os.mkdir(self.dirname, 0o700, dir_fd=claim.root_fd)
            self.fd = os.open(self.dirname, os.O_RDONLY | os.O_DIRECTORY |
                              os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=claim.root_fd)
            c.directory(self.fd)
            os.fsync(self.fd)
            os.fsync(claim.root_fd)  # Persist the new directory name.
            self.check()
        except Exception:
            self.failed = True
            if self.fd is not None:
                os.close(self.fd)
                self.fd = None
            raise CheckpointError('CHECKPOINT_OPEN_FAILED') from None

    def file_check(self, name, raw, expected_inode=None):
        fd = None
        try:
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=self.fd)
            opened = c.regular(fd)
            named = os.stat(name, dir_fd=self.fd, follow_symlinks=False)
            need((opened.st_dev, opened.st_ino) == (named.st_dev, named.st_ino),
                 'CHECKPOINT_FILE_CHANGED')
            need(expected_inode is None or (opened.st_dev, opened.st_ino) == expected_inode,
                 'CHECKPOINT_FILE_CHANGED')
            need(os.pread(fd, len(raw) + 1, 0) == raw, 'CHECKPOINT_BYTES_CHANGED')
            return (opened.st_dev, opened.st_ino)
        finally:
            if fd is not None:
                os.close(fd)

    def check(self):
        need(not self.closed and not self.failed, 'CHECKPOINT_WRITER_UNAVAILABLE')
        self.claim.check()
        opened = c.directory(self.fd)
        named = os.stat(self.dirname, dir_fd=self.claim.root_fd, follow_symlinks=False)
        need((opened.st_dev, opened.st_ino) == (named.st_dev, named.st_ino),
             'CHECKPOINT_DIRECTORY_CHANGED')
        for name, raw, inode in self.records:
            self.file_check(name, raw, inode)

    def __call__(self, receipt):
        fd = None
        try:
            self.check()
            need(len(self.records) < 64, 'CHECKPOINT_LIMIT_REACHED')
            safe = receipt_checked(receipt, self.claim)
            number = len(self.records) + 1
            previous = hashlib.sha256(self.records[-1][1]).hexdigest() if self.records else None
            record = {
                'schema': 'P1-CICD-deploy-checkpoint-1', 'sequence': number,
                'previous_sha256': previous, 'claim_sha256': self.claim.sha256,
                'receipt': safe,
            }
            raw = (json.dumps(record, sort_keys=True, separators=(',', ':'),
                              allow_nan=False) + '\n').encode('utf-8')
            need(len(raw) <= 1024 * 1024, 'CHECKPOINT_RECORD_TOO_LARGE')
            name = f'{number:04d}.json'
            fd = os.open(name, os.O_RDWR | os.O_CREAT | os.O_EXCL |
                         os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=self.fd)
            c.regular(fd)
            c.write_all(fd, raw)
            os.fsync(fd)
            inode = self.file_check(name, raw)
            self.check()
            os.fsync(self.fd)  # File bytes and name must both be durable.
            self.file_check(name, raw, inode)
            self.claim.check()
            self.records.append((name, raw, inode))
        except Exception:
            self.failed = True
            # A partial/new file stays; this object cannot retry the sequence.
            raise CheckpointError('CHECKPOINT_SAVE_FAILED') from None
        finally:
            if fd is not None:
                os.close(fd)

    def close(self):
        if not self.closed:
            self.closed = True
            if self.fd is not None:
                os.close(self.fd)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
        return False


class BoundCheckpoint:
    """Lazy checkpoint callback: lambda: execution.claim supplies the held Claim."""
    def __init__(self, claim_provider):
        need(callable(claim_provider), 'CHECKPOINT_CLAIM_PROVIDER_REQUIRED')
        self.provider, self.journal, self.claim = claim_provider, None, None
        self.closed = self.failed = False

    def __call__(self, receipt):
        try:
            need(not self.closed and not self.failed, 'CHECKPOINT_WRITER_UNAVAILABLE')
            claim = self.provider()
            need(isinstance(claim, c.Claim), 'CHECKPOINT_CLAIM_REQUIRED')
            if self.journal is None:
                receipt_checked(receipt, claim)
                self.claim, self.journal = claim, Journal(claim)
            need(claim is self.claim, 'CHECKPOINT_CLAIM_CHANGED')
            self.journal(receipt)
        except Exception:
            self.failed = True
            raise CheckpointError('CHECKPOINT_SAVE_FAILED') from None

    def close(self):
        self.closed = True
        if self.journal is not None:
            self.journal.close()
