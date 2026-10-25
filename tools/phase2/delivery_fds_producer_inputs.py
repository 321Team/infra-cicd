"""Reviewed existing image supply -> FDS producer/native stage selections.

Historical receipt/CI success is NOT a deployment grant or a current scan.
Image build source affbc69 and render source 69e28c49 are distinct. The adopted
contract reuses these exact digests; it does not claim a rebuild at render SHA.
No I/O, credentials, grant/previous inference, new receipt or global SITE here.
"""
from dataclasses import replace

import delivery_adapter as core
import delivery_actions_source as actions
import delivery_fds_operating_policy as policy
import delivery_fds_site_inputs as profile
from delivery_adopted_authority import AdoptedGrant
from delivery_github_source import BlobPin, RepositoryPin
from delivery_runtime_site import EvidenceAdoption
from delivery_semantic_authority import ProducerFacts
from producer_bridge import CHECKER_SHA256

IMAGE_SOURCE_COMMIT = 'affbc69beef9967f7977d09a6fec14f7fb6c0e94'
RENDER_SOURCE_COMMIT = '69e28c494a120c388afe18d901043d05333d7c7c'
# Independent receipt facts: a future profile edit must not silently associate
# different digests/source with this old, immutable supply receipt.
SUPPLIED_IMAGES = (
    ('transaction-api', '10.1.93.54/fds/transaction-api@sha256:'
     '338b8a2871507ce8b42deedc916b26e350577ee6efd18df23d7ff2dd4b17dff0'),
    ('fds-engine', '10.1.93.54/fds/fds-engine@sha256:'
     '8b257adb08b6d381f72b66d3386efea6112a8f072ecfafcbcd463bcd1c3be89d'),
)
# These subtrees were read at BOTH actual commits. This records the observed
# relationship; it does not prove a reproducible rebuild or all external inputs.
UNCHANGED_TREES = (
    ('transaction-api', '7351c35ece23f5807787af9f156b1ce4fa5e7cc8'),
    ('fds-engine', '89f203edf408b8358f644c49362b5a7bc94d073e'),
    ('ci', 'f27523fe7f81cb4e6fbfc637d385ff146460d183'),
    ('ci/apko', 'd95202cce53b93fc12c85b692b8e810b222f9f82'),
)
SUPPLY_SHA256 = '79f4f056290b953912dcb229604ea0fc20d3a2412ed8a66ff13e764ef33996f1'
SUPPLY_PIN = BlobPin(
    RepositoryPin('321Team', 'fds-project-evidence', 1359735179, 315682664),
    '9647a83724dea442c9e8238e60d68bfe2602dc8d',
    'evidence/P1-CICD-01/push-run-38404449622-20261013/NEXT_DEPLOY_HANDOFF.json',
    SUPPLY_SHA256, 'heads/evidence/p1-push-readiness-20261013-2150',
    '9647a83724dea442c9e8238e60d68bfe2602dc8d')
# Same blob601da841 is independently merged by evidence PR218/7e6bb13d.
PRODUCER_REPOSITORY = RepositoryPin('321Team', 'infra-cicd', 1362221024, 315682664)
TBS = actions.RunPin(PRODUCER_REPOSITORY, 38395350748, 1, 363199301,
    '147f760298dd4a683ea58f2a887483769352b97d',
    '.github/workflows/app-test-build-scan.yml', 'workflow_dispatch', 'main')
PUSH = actions.RunPin(PRODUCER_REPOSITORY, 38404449622, 1, 375319885,
    '147f760298dd4a683ea58f2a887483769352b97d',
    '.github/workflows/app-push.yml', 'workflow_dispatch', 'main')
TBS_REFERENCE = 'https://github.com/321Team/infra-cicd/actions/runs/38395350748/job/115289040431'
PUSH_REFERENCE = 'https://github.com/321Team/infra-cicd/actions/runs/38404449622/job/115320270242'
RENDER_REFERENCE = 'https://github.com/321Team/fds-msa/pull/42#issuecomment-6337648856'
ADOPTION_REFERENCE = ('https://github.com/321Team/fds-msa/blob/' + RENDER_SOURCE_COMMIT
                      + '/deploy/DELIVERY-CONTRACT-rosa.md#9-앱별-currentprevious-채택-43-2026-10-15')


class ProducerInputError(RuntimeError):
    """Fixed public code only; never caller grant/receipt/source text."""


def connect_reviewed_producer(config, *, grant, previous_images):
    """Select existing EvidenceAdoption + exact native StagePin4, without I/O.

    Caller MUST supply the actual same-Binding grant and explicitly adopted
    previous profile. The raw handoff says NOT_DEPLOY_AUTHORIZATION and never
    supplies either. Runtime still authenticates original/control before/after
    native ActionsRunSource inspection. One aggregate TBS step is not by itself
    proof of three internal stages; existing reviewed supply meaning is retained.
    """
    invalid = False
    try:
        b = profile._binding_checked(config)
        policy.need(b.images == SUPPLIED_IMAGES and b.source_commit == RENDER_SOURCE_COMMIT
            and type(grant) is AdoptedGrant and grant.binding == b
            and grant.original_pin == SUPPLY_PIN and grant.control_pin == policy.CONTROL_PIN
            and b.supply_receipt_sha256 == SUPPLY_SHA256
            and core.finite(grant.starts_at) and core.finite(grant.expires_at)
            and grant.starts_at < grant.expires_at
            and type(previous_images) is tuple and len(previous_images) == len(b.images))
        core.binding_checked(replace(b, images=previous_images))
        policy.need(tuple(x[0] for x in previous_images) == core.services_for(b))
        # The immutable receipt and adopted contract contain these two exact
        # pushed digests. No digest/contract/source override or latest selection.
        facts = ProducerFacts(b.images, previous_images, b.source_commit,
            b.contract_sha256, b.rendered_sha256, CHECKER_SHA256,
            TBS_REFERENCE, TBS_REFERENCE, TBS_REFERENCE, PUSH_REFERENCE,
            RENDER_REFERENCE, ADOPTION_REFERENCE)
        pins = tuple(actions.StagePin(stage, TBS_REFERENCE, TBS, 115289040431, 'app',
            ((4, 'Validate handoff and run the approved application'),))
            for stage in ('test', 'build', 'scan')) + (
            actions.StagePin('push', PUSH_REFERENCE, PUSH, 115320270242, 'push',
                            ((5, 'Execute approved Push'),)),)
        actions.policy_checked(pins)
    except Exception:
        invalid = True
    if invalid:
        raise ProducerInputError('FDS_REVIEWED_PRODUCER_INPUT_INVALID') from None
    return EvidenceAdoption(grant, facts), pins
