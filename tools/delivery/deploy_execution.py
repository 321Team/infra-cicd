"""v2 approval/claim/Adapter connection library, deliberately no runtime CLI.

The Kubernetes client and a durable checkpoint implementation are supplied by
the reviewed integrating caller. No credential issuance or cleanup occurs here.
"""
import copy
import datetime as dt
import math
import time

import deploy_adapter as core
import deploy_binding as b
import deploy_claim
import deploy_plan
from deploy_approval import ApprovalError


STATIC_ERRORS = frozenset({
    'ACTION_INVALID', 'BINDING_REQUIRED', 'NOW_INVALID', 'CLOCK_INVALID',
    'APPROVAL_EXPIRED', 'CREDENTIAL_EXPIRED', 'CLAIM_REQUIRED',
    'ROLLBACK_NOT_APPROVED', 'AUTH_WINDOW_UNAVAILABLE', 'AUTH_WINDOW_INVALID',
    'AUTH_WINDOW_TOO_SHORT', 'CREDENTIAL_WINDOW_UNAVAILABLE',
    'CREDENTIAL_WINDOW_INVALID', 'CREDENTIAL_WINDOW_TOO_SHORT',
    'CHECKPOINT_FAILED', 'APPROVAL_LOOKUP_INVALID',
    'APPROVAL_REFERENCE_UNSUPPORTED', 'APPROVAL_LOOKUP_BUDGET_INVALID',
    'GITHUB_CREDENTIAL_INVALID', 'APPROVAL_HTTP_REJECTED',
    'APPROVAL_LOOKUP_TIMEOUT', 'APPROVAL_LOOKUP_FAILED',
    'APPROVAL_HASH_INVALID', 'APPROVAL_HASH_MISMATCH', 'DEPLOY_APPROVAL_REQUIRED',
    'APPROVER_NOT_AUTHORIZED', 'APPROVAL_COMMENT_MISMATCH',
    'APPROVAL_AUTHOR_MISMATCH', 'APPROVAL_REFERENCE_CHANGED',
    'CLAIM_PLATFORM_UNSUPPORTED', 'CLAIM_HASH_INVALID', 'CLAIM_EXECUTION_INVALID',
    'CLAIM_RETRY_NOT_APPROVED', 'CLAIM_PARENT_UNSAFE', 'CLAIM_DIRECTORY_UNSAFE',
    'CLAIM_DIRECTORY_CHANGED', 'CLAIM_FILE_UNSAFE', 'DEPLOYMENT_LOCK_BUSY',
    'PLAN_ALREADY_CLAIMED', 'CLAIM_IO_FAILED', 'CLAIM_WRITE_FAILED',
    'CLAIM_CLOSED', 'CLAIM_FILE_CHANGED', 'CLAIM_BYTES_CHANGED', 'CLAIM_CHECK_FAILED',
})


def error_code(exc):
    # Injected helpers must not be able to put a credential in the error field.
    if isinstance(exc, b.Invalid):
        return 'INPUT_BINDING_REJECTED'
    text = str(exc)
    return text if text in STATIC_ERRORS else 'EXECUTION_HELPER_FAILED'


def clean_hash(value):
    return value if type(value) is str and b.gate.SHA256.fullmatch(value) else None


class Execution:
    def __init__(self, client, approval_verifier, checkpoint, state_root, *,
                 credential_remaining_seconds, now=lambda: dt.datetime.now(dt.timezone.utc),
                 clock=time.monotonic, sleep=time.sleep, timeout=300, interval=2,
                 window_margin=30):
        self.verifier, self.checkpoint, self.root = approval_verifier, checkpoint, state_root
        self.now, self.clock = now, clock
        core.need(callable(credential_remaining_seconds), 'CREDENTIAL_WINDOW_PROVIDER_REQUIRED')
        self.credential_provider = credential_remaining_seconds
        self.claim = None
        self.used = False
        self.binding = None
        self.authority_error = 'NONE'
        self.approval_observation = None
        self.claim_metadata = None
        self.adapter = core.Adapter(
            client, self.authorize, self.save,
            remaining_seconds=self.remaining,
            credential_remaining_seconds=self.credential_window,
            timeout=timeout, interval=interval, window_margin=window_margin,
            clock=clock, sleep=sleep)
        core.need(callable(getattr(approval_verifier, 'verify', None)),
                  'APPROVAL_VERIFIER_REQUIRED')
        core.need(callable(checkpoint), 'CHECKPOINT_REQUIRED')
        core.need(callable(now) and callable(clock), 'CLOCK_PROVIDER_REQUIRED')

    def remaining(self):
        core.need(self.binding is not None, 'BINDING_REQUIRED')
        current = self.now()
        core.need(isinstance(current, dt.datetime) and current.tzinfo is not None and
                  current.utcoffset() is not None, 'NOW_INVALID')
        wall = (self.binding['expires_at'] - current).total_seconds()
        elapsed = self.clock()
        core.need(type(elapsed) in (int, float) and math.isfinite(elapsed),
                  'CLOCK_INVALID')
        # A backward wall-clock adjustment must not extend the original budget.
        return min(wall, self.monotonic_expiry - elapsed)

    def credential_window(self):
        try:
            value = self.credential_provider()
        except Exception:
            raise core.Stop('CREDENTIAL_WINDOW_UNAVAILABLE') from None
        core.need(type(value) in (int, float) and math.isfinite(value),
                  'CREDENTIAL_WINDOW_INVALID')
        return value

    def start_window(self):
        self.adapter.check_start_window()

    def lookup(self):
        remaining, credential = self.remaining(), self.credential_window()
        core.need(remaining > 0, 'APPROVAL_EXPIRED')
        core.need(credential > 0, 'CREDENTIAL_EXPIRED')
        self.approval_observation = self.verifier.verify(
            self.approval_raw, self.approval_hash, min(10, remaining, credential))
        core.need(type(self.approval_observation) is dict and
                  self.approval_observation.get('status') == 'MATCHED_CURRENT_COMMENT',
                  'APPROVAL_LOOKUP_INVALID')

    def authorize(self, action):
        try:
            core.need(action in ('deploy', 'rollback'), 'ACTION_INVALID')
            core.need(self.claim is not None, 'CLAIM_REQUIRED')
            self.claim.check()
            if action == 'rollback':
                core.need(self.binding['allow_rollback'] is True, 'ROLLBACK_NOT_APPROVED')
            self.lookup()  # Fresh lookup for every GET/PATCH, including rollback.
            self.claim.check()  # Drift during remote lookup also denies the request.
            core.need(self.remaining() > 0, 'APPROVAL_EXPIRED')
            core.need(self.credential_window() > 0, 'CREDENTIAL_EXPIRED')
            self.authority_error = 'NONE'
            return True
        except (core.Stop, deploy_claim.ClaimError, ApprovalError) as exc:
            self.authority_error = error_code(exc)
            return False
        except Exception:
            self.authority_error = 'AUTHORITY_CHECK_FAILED'
            return False

    def envelope(self, receipt):
        result = copy.deepcopy(receipt)
        result['schema'] = 'P1-CICD-deploy-execution-2'
        result['execution'] = copy.deepcopy(self.execution)
        result['plan_sha256'] = self.plan_hash
        result['approval_sha256'] = self.approval_hash
        result['push_receipt_sha256'] = self.push_hash
        result['authority_error_code'] = self.authority_error
        result['DEPLOY_APPROVAL_REFERENCE'] = (
            'MATCHED_AT_LAST_SUCCESSFUL_LOOKUP' if self.approval_observation else 'NOT_VERIFIED')
        result['claim'] = copy.deepcopy(self.claim_metadata)
        result['approval_reference'] = self.binding['approval_reference'] if self.binding else None
        result['CREDENTIAL_METADATA'] = 'CALLER_SUPPLIED_NOT_VERIFIED_HERE'
        # Do not upgrade upstream Source approval, Runtime health, or final gate.
        result['SOURCE_APPROVAL'] = 'NOT_INDEPENDENTLY_VERIFIED'
        result['DELIVERY_GATE'] = 'NOT_VERIFIED'
        return result

    def save(self, receipt):
        # Caller must fsync/atomically persist or raise before returning.
        self.checkpoint(self.envelope(receipt))

    def run(self, plan_raw, approval_raw, push_raw, expected_plan_sha256,
            expected_approval_sha256, execution):
        core.need(not self.used, 'EXECUTION_ALREADY_USED')
        self.used = True
        self.plan_hash, self.approval_hash = clean_hash(expected_plan_sha256), clean_hash(expected_approval_sha256)
        self.execution = b.identity(execution)
        self.push_hash = b.sha(push_raw) if type(push_raw) is bytes else None
        self.approval_raw = approval_raw
        claimed = False
        try:
            self.binding = deploy_plan.validate(
                plan_raw, approval_raw, push_raw, expected_plan_sha256,
                expected_approval_sha256, self.execution, self.now())
            initial = self.clock()
            core.need(type(initial) in (int, float) and math.isfinite(initial),
                      'CLOCK_INVALID')
            self.monotonic_expiry = initial + self.binding['remaining_seconds']
            # Metadata must already be independently verified by the caller.
            # A requested token TTL cannot substitute for actual remaining life.
            self.start_window()
            self.lookup()
            self.start_window()  # Recheck time spent fetching approval before claim.
            self.claim = deploy_claim.acquire(
                self.root, expected_plan_sha256, expected_approval_sha256, self.execution)
            claimed = True
            self.claim_metadata = {
                'state': 'CLAIMED', 'sha256': self.claim.sha256,
                'execution': copy.deepcopy(self.execution)}
            result = self.adapter.run(
                self.binding['targets'], rollback_on_failure=self.binding['allow_rollback'])
            return self.envelope(result)
        except (b.Invalid, core.Stop, deploy_claim.ClaimError, ApprovalError) as exc:
            code = error_code(exc)
            receipt = copy.deepcopy(self.adapter.receipt)
            receipt['ROLLOUT'] = 'FAILED' if claimed else 'NOT_EXECUTED'
            receipt['error_code'] = code
            if claimed:
                receipt['ROLLBACK'] = 'MANUAL_RECONCILIATION_REQUIRED'
            return self.envelope(receipt)
        except Exception:
            receipt = copy.deepcopy(self.adapter.receipt)
            receipt['ROLLOUT'] = 'FAILED' if claimed else 'NOT_EXECUTED'
            receipt['error_code'] = 'EXECUTION_FAILED'
            if claimed:
                receipt['ROLLBACK'] = 'MANUAL_RECONCILIATION_REQUIRED'
            return self.envelope(receipt)
        finally:
            if self.claim is not None:
                self.claim.close()
