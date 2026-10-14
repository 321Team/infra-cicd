"""P2 authorization comparison boundary; no verifier, CLI, token or network.

Only trusted integration code may construct VerifiedInputs. This class is NOT
an approval document schema, signature validator, or a JSON-to-Lease converter.
Real approval/identity/RBAC/provenance verification is still an external gate.
"""
from dataclasses import dataclass
import time

import delivery_adapter as core


@dataclass(frozen=True)
class VerifiedInputs:
    """Return only after fresh, authenticated verification, never from input flags.

    approved: exact action/run/target/source/render/receipts/images approved by
    the authorized Lead, with a still-valid approval window.
    observed: independently assembled from verified target/API/namespace UID/CA,
    clean source/render and platform/supply originals in this execution.
    lease: actual authenticated token issuer/audience/expiry/minimal RBAC and
    approval expiry, bound to the same credential held by the sender.

    The provider must also verify current object identity/field ownership and
    endpoint resolution. This comparison helper cannot prove these facts.
    """
    approved: core.Binding
    observed: core.Binding
    lease: core.Lease


class BoundAuthorizer:
    """Recheck trusted verification and durable exclusivity on EVERY invocation.

    claim/journal are the actual acquired P2 store objects. No cached PASS,
    renewal, claim release, network implementation or logging is provided here.
    Adapter retains the stricter first-write budget and monotonic-time guard.
    """
    def __init__(self, *, verify, claim, journal, wall=time.time):
        self.verify, self.claim, self.journal, self.wall = verify, claim, journal, wall
        self.initial = None

    def __call__(self, binding):
        try:
            core.binding_checked(binding)
            core.need(self.claim.binding == binding and self.journal.claim is self.claim,
                      'AUTHORIZATION_DENIED')
            self.claim.check()
            self.journal.check()
            verified = self.verify(binding)
            core.need(type(verified) is VerifiedInputs, 'AUTHORIZATION_DENIED')
            core.binding_checked(verified.approved)
            core.binding_checked(verified.observed)
            core.need(verified.approved == verified.observed == binding, 'AUTHORIZATION_DENIED')
            lease = verified.lease
            core.need(type(lease) is core.Lease and lease.binding_key == binding.key
                      and type(lease.requested_seconds) is int and lease.requested_seconds == 900
                      and type(lease.credential_fingerprint) is str
                      and core.HEX.fullmatch(lease.credential_fingerprint), 'AUTHORIZATION_DENIED')
            now = self.wall()
            core.need(core.finite(now) and core.finite(lease.token_expires_at)
                      and core.finite(lease.approval_expires_at)
                      and now < min(lease.token_expires_at, lease.approval_expires_at),
                      'AUTHORIZATION_DENIED')
            core.need(self.initial is None or self.initial == verified, 'AUTHORIZATION_DENIED')
            # Verification may take time or mutate integration state. Recheck
            # the durable claim/journal before allowing Adapter to proceed.
            self.claim.check()
            self.journal.check()
            self.initial = verified
            return lease
        except Exception:
            raise core.Stop('AUTHORIZATION_DENIED') from None
