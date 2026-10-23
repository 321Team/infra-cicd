"""FDS 2026-10-22 observed, non-secret inputs wired to the existing parent.

This is a source/target profile, NOT authority adoption, a token issuer or a
configured SITE. Runtime originals, current identity, transport and expiry
remain explicit caller responsibilities. No file/network/environment I/O.
"""
import delivery_adapter as core
import delivery_crosshost_ingress as ingress
import delivery_job_parent as parent
import delivery_runtime_site as runtime
import delivery_site as site
from delivery_adopted_authority import AdoptedGrant
from delivery_platform_readback import ExpectedSubject
from delivery_semantic_authority import IssuerFacts, ProducerFacts

API_ORIGIN = 'https://api.fds-rosa-hcp.khht.p3.openshiftapps.com:443'
# spec.clusterID, NOT the OCM/ROSA identifier 2th4q1q4qf7ra35862s1u9q7f6n0vnmc.
CLUSTER_UUID = '37eecf03-b7bc-45e1-8089-5a14a47fccd9'
NAMESPACE_UID = '222bf961-73d9-4743-972f-2d44da216af2'
SUBJECT = 'system:serviceaccount:ci-cd:fds-deployer'
SUBJECT_UID = '6a4c1f9f-1291-4f1d-b999-b4dbc5d179c2'
ISSUER = AUDIENCE = 'https://oidc.op1.openshiftapps.com/2te9vlkhgbi43b770ni4f7nhiasie0fh'
SOURCE_COMMIT = '69e28c494a120c388afe18d901043d05333d7c7c'
CONTRACT_SHA256 = '65059dcbf6cfb6e6a6a2e65d92cfb95ea5efe3637265eeb79918c631faa6089c'
RENDERED_SHA256 = '3c8b6fe17d9a4705fd57158ec426294fe2a24413c83091aa3a568912b7263516'
ROUTE_HOST = 'fds-api.apps.rosa.fds-rosa-hcp.khht.p3.openshiftapps.com'
IMAGES = (
    ('transaction-api', '10.1.93.54/fds/transaction-api@sha256:'
     '338b8a2871507ce8b42deedc916b26e350577ee6efd18df23d7ff2dd4b17dff0'),
    ('fds-engine', '10.1.93.54/fds/fds-engine@sha256:'
     '8b257adb08b6d381f72b66d3386efea6112a8f072ecfafcbcd463bcd1c3be89d'),
)
CA_PEM_SHA256 = '22b557a27055b33606b6559f37703928d3e4ad79f110b407d04986e1843543d1'
CA_DER_SHA256 = '96bcec06264976f37460779acf28c5a7cfe8a3c0aae11a8ffcee05c0bddf08c6'
# Exact public ISRG Root X1 PEM; no ambient trust store or mutable LB IP default.
CA_PEM = b'''-----BEGIN CERTIFICATE-----
MIIFazCCA1OgAwIBAgIRAIIQz7DSQONZRGPgu2OCiwAwDQYJKoZIhvcNAQELBQAw
TzELMAkGA1UEBhMCVVMxKTAnBgNVBAoTIEludGVybmV0IFNlY3VyaXR5IFJlc2Vh
cmNoIEdyb3VwMRUwEwYDVQQDEwxJU1JHIFJvb3QgWDEwHhcNMTUwNjA0MTEwNDM4
WhcNMzUwNjA0MTEwNDM4WjBPMQswCQYDVQQGEwJVUzEpMCcGA1UEChMgSW50ZXJu
ZXQgU2VjdXJpdHkgUmVzZWFyY2ggR3JvdXAxFTATBgNVBAMTDElTUkcgUm9vdCBY
MTCCAiIwDQYJKoZIhvcNAQEBBQADggIPADCCAgoCggIBAK3oJHP0FDfzm54rVygc
h77ct984kIxuPOZXoHj3dcKi/vVqbvYATyjb3miGbESTtrFj/RQSa78f0uoxmyF+
0TM8ukj13Xnfs7j/EvEhmkvBioZxaUpmZmyPfjxwv60pIgbz5MDmgK7iS4+3mX6U
A5/TR5d8mUgjU+g4rk8Kb4Mu0UlXjIB0ttov0DiNewNwIRt18jA8+o+u3dpjq+sW
T8KOEUt+zwvo/7V3LvSye0rgTBIlDHCNAymg4VMk7BPZ7hm/ELNKjD+Jo2FR3qyH
B5T0Y3HsLuJvW5iB4YlcNHlsdu87kGJ55tukmi8mxdAQ4Q7e2RCOFvu396j3x+UC
B5iPNgiV5+I3lg02dZ77DnKxHZu8A/lJBdiB3QW0KtZB6awBdpUKD9jf1b0SHzUv
KBds0pjBqAlkd25HN7rOrFleaJ1/ctaJxQZBKT5ZPt0m9STJEadao0xAH0ahmbWn
OlFuhjuefXKnEgV4We0+UXgVCwOPjdAvBbI+e0ocS3MFEvzG6uBQE3xDk3SzynTn
jh8BCNAw1FtxNrQHusEwMFxIt4I7mKZ9YIqioymCzLq9gwQbooMDQaHWBfEbwrbw
qHyGO0aoSCqI3Haadr8faqU9GY/rOPNk3sgrDQoo//fb4hVC1CLQJ13hef4Y53CI
rU7m2Ys6xt0nUW7/vGT1M0NPAgMBAAGjQjBAMA4GA1UdDwEB/wQEAwIBBjAPBgNV
HRMBAf8EBTADAQH/MB0GA1UdDgQWBBR5tFnme7bl5AFzgAiIyBpY9umbbjANBgkq
hkiG9w0BAQsFAAOCAgEAVR9YqbyyqFDQDLHYGmkgJykIrGF1XIpu+ILlaS/V9lZL
ubhzEFnTIZd+50xx+7LSYK05qAvqFyFWhfFQDlnrzuBZ6brJFe+GnY+EgPbk6ZGQ
3BebYhtF8GaV0nxvwuo77x/Py9auJ/GpsMiu/X1+mvoiBOv/2X/qkSsisRcOj/KK
NFtY2PwByVS5uCbMiogziUwthDyC3+6WVwW6LLv3xLfHTjuCvjHIInNzktHCgKQ5
ORAzI4JMPJ+GslWYHb4phowim57iaztXOoJwTdwJx4nLCgdNbOhdjsnvzqvHu7Ur
TkXWStAmzOVyyghqpZXjFaH3pO3JLF+l+/+sKAIuvtd7u+Nxe5AW0wdeRlN8NwdC
jNPElpzVmbUq4JUagEiuTDkHzsxHpFKVK7q4+63SM1N95R1NbdWhscdCb+ZAJzVc
oyi3B43njTOQ5yOf+1CceWxG1bQVs5ZufpsMljq4Ui0/1lvh+wjChP4kqKOJ2qxq
4RgqsahDYVvTH9w7jXbyLeiNdd8XM2w9U/t7y0Ff/9yi0GE44Za4rF2LN9d11TPA
mRGunUHBcnWEvgJBQl9nJEiU0Zsnvgc/ubhPgXRR4Xq37Z0j4r7g1SgEEzwxA57d
emyPxgcYxn/eR44/KJ4EBs+lVDR3veyJm+kXQ99b21/+jh5Xos1AnX5iItreGCc=
-----END CERTIFICATE-----
'''


class FdsSiteInputError(RuntimeError):
    """Fixed public code; never caller values, originals, paths or credentials."""


def need(ok):
    if not ok:
        raise FdsSiteInputError('FDS_SITE_INPUT_MISMATCH')


def build_configuration(*, run_id, platform_receipt_sha256, supply_receipt_sha256,
                        input_root, rendered_name, contract_name, source_root,
                        store_root, budget_seconds, finalize):
    """Connect observed fixed facts; ALL per-run/file inputs remain explicit.

    Shape/hash comparison is not authentication or an execution grant. Source
    bytes and safe POSIX files are still checked by the existing site loader.
    """
    need(core.digest(CA_PEM) == CA_PEM_SHA256)
    binding = core.Binding(run_id, 'deploy', API_ORIGIN, CLUSTER_UUID,
        NAMESPACE_UID, CA_PEM_SHA256, SOURCE_COMMIT, CONTRACT_SHA256,
        RENDERED_SHA256, platform_receipt_sha256, supply_receipt_sha256,
        ROUTE_HOST, IMAGES)
    config = site.SiteConfiguration(binding, input_root, rendered_name, contract_name,
        source_root, store_root, budget_seconds, finalize)
    site.configuration_checked(config)
    return config


def _binding_checked(config):
    site.configuration_checked(config)
    b = config.binding
    expected = core.Binding(b.run_id, 'deploy', API_ORIGIN, CLUSTER_UUID,
        NAMESPACE_UID, CA_PEM_SHA256, SOURCE_COMMIT, CONTRACT_SHA256,
        RENDERED_SHA256, b.platform_receipt_sha256, b.supply_receipt_sha256,
        ROUTE_HOST, IMAGES)
    need(b == expected and core.digest(CA_PEM) == CA_PEM_SHA256)
    return b


def _source_profile(config, plan):
    """Common fixed FDS source/target comparison, never baseline adoption."""
    b = _binding_checked(config)
    need(type(plan) is runtime.RuntimeAuthorityPlan
         and type(plan.approval) is AdoptedGrant
         and type(plan.issuer) is runtime.EvidenceAdoption
         and type(plan.producer) is runtime.EvidenceAdoption
         and type(plan.issuer.grant) is AdoptedGrant
         and type(plan.producer.grant) is AdoptedGrant
         and type(plan.issuer.facts) is IssuerFacts
         and type(plan.producer.facts) is ProducerFacts
         and type(plan.subject) is ExpectedSubject)
    need(plan.approval.binding == plan.issuer.grant.binding
         == plan.producer.grant.binding == b)
    f, p = plan.issuer.facts, plan.producer.facts
    need(f.issuer == ISSUER and f.audience == AUDIENCE and f.ca_pem == CA_PEM
         and (plan.subject.username, plan.subject.uid) == (SUBJECT, SUBJECT_UID)
         and plan.issuer.grant.principal == SUBJECT)
    need((p.images, p.source_commit, p.contract_sha256, p.rendered_sha256)
         == (IMAGES, SOURCE_COMMIT, CONTRACT_SHA256, RENDERED_SHA256))
    return plan


def validate_plan(config, plan):
    """First-empty-target profile only; existing authenticators still run.

    No current facts are generated. Existing objects/previous are NOT adopted
    by this API; the explicit existing-target connector has its own checks.
    """
    _source_profile(config, plan)
    need(plan.producer.facts.previous_images is None and plan.baseline is None)
    return plan


def _connect_parent(validate, *, config, finish_slot, expected_peer, routes, deadline,
                    wire, publish_ready, open_channel, monotonic,
                    finish_capture=None, producer_pins=None, plan=None, materialize_plan=None):
    """Internal CODE composition, not an external validation/policy loader."""
    _binding_checked(config)
    from delivery_job_sources import SourceRoleWiring
    if type(wire) is SourceRoleWiring:
        need(wire.binding == config.binding)
    need((plan is None and callable(materialize_plan))
         or (plan is not None and materialize_plan is None))
    if plan is not None:
        validate(config, plan)
        if type(wire) is SourceRoleWiring:
            wire.check_plan(plan)
    need(type(finish_slot) is runtime.FinishAdoptionSlot
         and finish_slot.binding == config.binding
         and type(expected_peer) is ingress.PeerIdentity
         and type(routes) is tuple and bool(routes)
         and all(type(x) is ingress.SourceRoute for x in routes)
         and core.finite(deadline)
         and all(callable(x) for x in (wire, publish_ready, open_channel, monotonic)))
    need(finish_capture is None or callable(finish_capture))
    if plan is None:
        def materialize(context, credential, source, roles, handoff_deadline):
            return validate(config, materialize_plan(
                context, credential, source, roles, handoff_deadline))
        plan = runtime.PreparedAuthorityPlan(binding=config.binding, materialize=materialize)
    return parent.ParentJobBuilder(config=config, plan=plan, finish_slot=finish_slot,
        expected_peer=expected_peer, routes=routes, deadline=deadline, wire=wire,
        publish_ready=publish_ready, open_channel=open_channel, monotonic=monotonic,
        finish_capture=finish_capture, producer_pins=producer_pins)


def connect_parent(*, config, finish_slot, expected_peer, routes, deadline,
                   wire, publish_ready, open_channel, monotonic,
                   finish_capture=None, producer_pins=None, plan=None, materialize_plan=None):
    """First-empty-target full/prepared plan -> existing one-use parent.

    No callback/I/O at construction. Actual post-handoff facts are checked by
    this fixed profile and the unchanged factory/source/control authenticators.
    """
    from delivery_job_sources import SourceRoleWiring, ROLES
    if type(wire) is SourceRoleWiring:
        need(wire.roles == ROLES)
    return _connect_parent(validate_plan, config=config, finish_slot=finish_slot,
        expected_peer=expected_peer, routes=routes, deadline=deadline, wire=wire,
        publish_ready=publish_ready, open_channel=open_channel, monotonic=monotonic,
        finish_capture=finish_capture, producer_pins=producer_pins, plan=plan,
        materialize_plan=materialize_plan)
