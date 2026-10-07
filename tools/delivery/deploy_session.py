"""One-shot composition for a trusted, reviewed deployment caller.

No CLI, token issuance, SSH bootstrap, Workflow dispatch or source approval.
The caller authenticates the reviewer and TokenRequest response out of band.
Never pass credentials through argv, environment dumps or evidence files.
"""
import datetime as dt
import time

import deploy_binding as b
import deploy_approval as approval
import deploy_checkpoint as checkpoint
import deploy_credential as credentials
import deploy_execution as execution_module
import deploy_plan as plan
import deploy_transport as transport


class Session:
    """Own local credential/checkpoint lifetime; retain claims after any attempt.

    state_root is a fixed, private POSIX directory shared across invocations,
    not a per-run temporary directory. Clocks must be trusted runtime functions.
    """
    def __init__(self, state_root, github_token_provider, reviewer, *,
                 now=lambda: dt.datetime.now(dt.timezone.utc), clock=time.monotonic,
                 sleep=time.sleep, timeout=300, interval=2, window_margin=30):
        self.root, self.github_token_provider, self.reviewer = (
            state_root, github_token_provider, reviewer)
        self.now, self.clock, self.sleep = now, clock, sleep
        self.timeout, self.interval, self.margin = timeout, interval, window_margin
        self.used = False

    def run(self, *, plan_raw, approval_raw, push_raw, expected_plan_sha256,
            expected_approval_sha256, execution, token, expected_sa_uid,
            token_request_expires_at, ca_raw, expected_ca_sha256):
        """May PATCH only after the existing approval/claim/adapter gates pass.

        token, SA UID and status expiry must come from the authenticated native
        issuance channel. A saved TokenReview JSON is not a reviewer backend.
        Caller still owns input token references and final result persistence.
        """
        if self.used:
            # No stale success receipt and no helper/network invocation on reuse.
            return {'schema': 'P1-CICD-deploy-session-1', 'status': 'REJECTED',
                    'error_code': 'SESSION_ALREADY_USED', 'execution_receipt': None,
                    'DELIVERY_GATE': 'NOT_VERIFIED'}
        self.used = True
        credential = writer = engine = client = None
        result = {
            'schema': 'P1-CICD-deploy-session-1', 'status': 'NOT_EXECUTED',
            'error_code': 'NONE', 'execution_receipt': None, 'credential': None,
            'checkpoint_close': 'NOT_OPENED',
            'input_reference_cleanup': 'CALLER_RESPONSIBILITY',
            'SOURCE_APPROVAL': 'NOT_INDEPENDENTLY_VERIFIED',
            'DELIVERY_GATE': 'NOT_VERIFIED',
        }
        started = False
        try:
            # Reject malformed scope/CA before any remote credential review.
            binding = plan.validate(plan_raw, approval_raw, push_raw,
                                    expected_plan_sha256, expected_approval_sha256,
                                    execution, self.now())
            transport.context_checked(ca_raw, expected_ca_sha256)
            claims = credentials.claims_checked(token, expected_sa_uid)
            status_expiry = b.instant(token_request_expires_at)
            b.need(claims['iat'] < status_expiry.timestamp() <= claims['exp'],
                   'TOKEN_REQUEST_EXPIRY_INVALID')
            # Anchor BEFORE remote review, never reset either bound afterwards.
            status_left = (status_expiry - self.now()).total_seconds()
            b.need(status_left > 0, 'TOKEN_REQUEST_EXPIRED')
            status_deadline = self.clock() + status_left
            credential = credentials.Credential(
                token, expected_sa_uid, self.reviewer, now=self.now, clock=self.clock,
                review_timeout=min(10, status_left, binding['remaining_seconds']))
            token = None

            def remaining():
                # Credential also rejects backward monotonic-clock movement.
                return max(0, min(credential.remaining(),
                                  (status_expiry - self.now()).total_seconds(),
                                  status_deadline - self.clock()))

            def bearer():
                b.need(remaining() > 0, 'TOKEN_REQUEST_EXPIRED')
                return credential.token()

            client = transport.Client(
                binding['targets'], ca_raw, expected_ca_sha256, bearer,
                remaining_seconds=lambda: engine.remaining(),
                credential_remaining_seconds=remaining, clock=self.clock)
            writer = checkpoint.BoundCheckpoint(lambda: engine.claim)
            verifier = approval.Verifier(approval.GitHubComments(self.github_token_provider))
            engine = execution_module.Execution(
                client, verifier, writer, self.root,
                credential_remaining_seconds=remaining, now=self.now, clock=self.clock,
                sleep=self.sleep, timeout=self.timeout, interval=self.interval,
                window_margin=self.margin)
            started = True
            receipt = engine.run(plan_raw, approval_raw, push_raw,
                                 expected_plan_sha256, expected_approval_sha256, execution)
            result['execution_receipt'] = receipt
            result['status'] = 'EXECUTION_RETURNED'
            result['error_code'] = receipt['error_code']
        except Exception:
            # Never copy backend exceptions, HTTP bodies, JWTs or input strings.
            result['status'] = ('MANUAL_RECONCILIATION_REQUIRED' if started
                                else 'NOT_EXECUTED')
            result['error_code'] = 'SESSION_EXECUTION_UNCONFIRMED' if started else 'SESSION_SETUP_REJECTED'
        finally:
            # Cleanup is independent: checkpoint failure must not retain token.
            if writer is not None:
                try:
                    writer.close()
                    result['checkpoint_close'] = 'CLOSED'
                except Exception:
                    result['checkpoint_close'] = 'FAILED'
                    result['status'] = 'MANUAL_RECONCILIATION_REQUIRED'
                    result['error_code'] = 'SESSION_CHECKPOINT_CLOSE_FAILED'
            if credential is not None:
                credential.close()
                result['credential'] = credential.observation()
                result['credential']['token_request_expires_at'] = status_expiry.isoformat()
            token = None
            # Break closures and do not leave a usable client on Session.
            client = engine = writer = credential = None
        return result
