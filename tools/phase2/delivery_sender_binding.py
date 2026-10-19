"""Opt-in TLS Sender factory for run_session; no operational provider or CLI.

Consume the SAME credential handle and an already verified PlatformEvidence.
The binding checks below cannot authenticate a fabricated evidence object.
Construction loads only the supplied CA; it does not open a socket or issue a
credential. The caller must use the existing session/authorizer lifecycle.
"""
import delivery_adapter as core
from delivery_object_reader import BearerCredential
from delivery_session import BoundSender
from delivery_transport import Sender, TransportError
from delivery_verifier import PlatformEvidence, ReadRequest


def bind_tls_sender(binding, credential, platform):
    """Explicit trusted-code injection, never a default or evidence loader.

    Close disables future sends and drops this closure's Sender reference.
    Existing per-request socket cleanup remains in Sender. This is sequential,
    not a concurrent cancellation primitive or guaranteed token memory erasure.
    """
    try:
        core.need(type(credential) is BearerCredential
                  and type(platform) is PlatformEvidence
                  and type(platform.request) is ReadRequest
                  and platform.binding == binding
                  and platform.request.binding == binding
                  and platform.credential is credential
                  and platform.request.credential is credential
                  and platform.credential_fingerprint == credential.fingerprint,
                  'SENDER_BINDING_INVALID')
        # Sender verifies CA bytes/hash, numeric endpoint and TLS policy itself.
        sender = Sender(binding, ca_pem=platform.ca_pem, token=credential.token,
                        endpoint_ip=platform.endpoint_ip)
    except Exception:
        raise TransportError('SENDER_BINDING_INVALID') from None

    def send(request):
        if sender is None:
            raise TransportError('SENDER_CLOSED')
        return sender(request)

    def close():
        nonlocal sender
        sender = None

    return BoundSender(credential, binding.key, credential.fingerprint,
                       binding.ca_sha256, platform.endpoint_ip, send, close)
