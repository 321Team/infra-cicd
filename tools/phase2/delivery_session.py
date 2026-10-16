"""Injectable P2 lifecycle candidate. Production adapters remain unconfigured.

No credential loading, transport construction, network, refresh or replay here.
Factories are trusted code and must clean up partial initialization themselves.
"""
from dataclasses import dataclass, field
import time

import delivery_adapter as core
from delivery_authorization import BoundAuthorizer
import delivery_store as store
from delivery_verifier import Progress, Verifier


@dataclass(frozen=True)
class BoundSender:
    credential: object = field(repr=False)
    binding_key: str
    credential_fingerprint: str
    ca_sha256: str
    endpoint_ip: str
    send: object = field(repr=False)
    close: object = field(repr=False)


def run_session(binding, rendered, contract, validator, *, root, providers, credential,
                sender_factory, budget_seconds, acquire=store.acquire,
                journal_factory=store.Journal, wall=time.time,
                monotonic=time.monotonic, sleep=time.sleep, finalize=False):
    """Single invocation; never auto-complete from rollout alone.

    finalize=True additionally requires a fresh trusted CompletionEvidence after
    sender.close. It is a caller request, NOT approval. On any error keep records
    and fence, close every acquired handle, never retry/refresh/clear/replay.
    Partial complete/close failures require read-only recovery inspection.
    """
    result = {'schema': 'P2-CICD-session-1', 'status': 'STOPPED',
              'error_code': None, 'receipt': None, 'completion': 'NOT_ATTEMPTED',
              'cleanup_errors': [], 'fence': 'NOT_ACQUIRED',
              'runtime': 'NOT_VERIFIED', 'delivery_gate': 'NOT_VERIFIED'}
    claim = journal = sender = None
    sender_close_attempted = False
    responses = []

    def progress():
        latest = None if journal is None or journal.latest is None else core.encoded(journal.latest)
        return Progress(latest, tuple(responses))

    stage = 'INPUT_PREPARATION_FAILED'
    try:
        core.prepare(binding, rendered, contract, validator)
        verifier = Verifier(providers=providers, credential=credential, progress=progress,
                            budget_seconds=budget_seconds, wall=wall, monotonic=monotonic)
        stage = 'PREFLIGHT_DENIED'
        verifier(binding)
        stage = 'CLAIM_ACQUIRE_FAILED'
        # Acquisition can fail AFTER creating durable files. Do not assert absence.
        result['fence'] = 'INSPECT_REQUIRED'
        claim = acquire(root, binding)
        result['fence'] = 'RETAINED'
        stage = 'JOURNAL_OPEN_FAILED'
        journal = journal_factory(claim)
        # Include preflight/provider elapsed monotonic time in the core's first
        # write budget even if the wall clock stalls (never extend the lease).
        effective_wall = lambda: verifier.clock()[0]
        authorizer = BoundAuthorizer(verify=verifier, claim=claim, journal=journal, wall=effective_wall)
        stage = 'AUTHORIZATION_DENIED'
        authorizer(binding)
        stage = 'SENDER_INITIALIZATION_FAILED'
        sender = sender_factory(binding, credential, verifier.platform)
        core.need(type(sender) is BoundSender and sender.credential is credential
                  and sender.binding_key == binding.key
                  and sender.credential_fingerprint == verifier.platform.credential_fingerprint
                  and sender.ca_sha256 == binding.ca_sha256
                  and sender.endpoint_ip == verifier.platform.endpoint_ip
                  and callable(sender.send) and callable(sender.close), 'SENDER_BINDING_MISMATCH')

        def send(request):
            # Preserve actual observed reply separately from durable ACK.
            reply = sender.send(request)
            if request.method == 'PATCH':
                raw = core.encoded(reply)
                core.need(len(raw) <= 1024 * 1024 and len(responses) < len(core.OBJECTS),
                          'RESPONSE_LIMIT_EXCEEDED')
                responses.append(raw)
            return reply

        stage = 'ADAPTER_FAILED'
        adapter = core.Adapter(authorize=authorizer, send=send, checkpoint=journal,
                               wall=effective_wall, monotonic=monotonic, sleep=sleep)
        receipt = adapter.run(binding, rendered, contract, validator)
        result['receipt'] = receipt
        if receipt['status'] != 'ROLLOUT_OBSERVED':
            result['error_code'] = 'ADAPTER_STOPPED'
        else:
            result['status'] = 'REVIEW_REQUIRED'
        # Close the sender even on STOP; a close failure blocks completion.
        stage = 'SENDER_CLOSE_FAILED'
        sender_close_attempted = True
        sender.close()
        if receipt['status'] == 'ROLLOUT_OBSERVED' and finalize is True:
            stage = 'COMPLETION_DENIED'
            claim.check()
            journal.check()
            verifier.finish(binding)
            claim.check()
            journal.check()
            stage = 'CLAIM_COMPLETE_FAILED'
            result['completion'] = 'ATTEMPTED'
            result['fence'] = 'INSPECT_REQUIRED'
            claim.complete(journal)
            result.update(status='SESSION_COMPLETED', completion='RECORDED', fence='RELEASED')
    except Exception:
        # Never include provider exception text, objects, raw receipts or locals.
        result.update(status='STOPPED', error_code=stage)
    finally:
        if sender is not None and not sender_close_attempted:
            try:
                sender.close()
            except Exception:
                result['cleanup_errors'].append('SENDER_CLOSE_FAILED')
        for handle, code in ((journal, 'JOURNAL_CLOSE_FAILED'), (claim, 'CLAIM_CLOSE_FAILED')):
            if handle is not None:
                try:
                    handle.close()
                except Exception:
                    result['cleanup_errors'].append(code)
        if result['cleanup_errors']:
            result['status'] = 'STOPPED'
            result['error_code'] = 'CLEANUP_FAILED'
            # complete might already have persisted; never assert fence retained.
            result['fence'] = 'INSPECT_REQUIRED'
    return result
