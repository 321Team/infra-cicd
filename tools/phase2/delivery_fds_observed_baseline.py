"""Explicit FDS baseline/previous CODE selection, not a Runtime grant.

The fixed archive covers the exact six observed UID/spec/owner bytes. The two
previous images below are deliberately selected from approved supply + manual
P2 deployment + observed Ready/imageID evidence; never inferred from an arbitrary
current snapshot or aliased from the next deployment's images.
"""
from dataclasses import asdict, replace

import delivery_fds_baseline_archive as archive
import delivery_fds_site_inputs as profile
from delivery_runtime_site import RuntimeAuthorityPlan
from delivery_github_source import RepositoryPin, CommentPin
from delivery_fds_private_baseline import PrivateBaselineFile, private_metadata_checked
from delivery_fds_run_inputs import RunInputFiles
from delivery_fds_prepared_environment import ROOT as PREPARED_ROOT

# Actual post-handoff observation selected by the owner. No archive bytes or
# credentials are published here; old observations are never fallback inputs.
SNAPSHOT_SHA256 = '70d81d8f5e4798de8aa70bfe3ed3354c114904f5f7420ab5fb271c5bd6d99cbf'
SNAPSHOT_ROOT = 'baseline6-20261025T091504'
SNAPSHOT_REFERENCE = 'https://github.com/321Team/fds-project-baseline/issues/46#issuecomment-6395884121'
METADATA_PIN = CommentPin(
    RepositoryPin('321Team', 'fds-project-baseline', 1357126372, 315682664),
    46, 6395884121, 277643063, '2026-10-25T09:21:29Z',
    'a1541e8bd6584745f5d5c8760237323b0871c5306601df2ef19bdabd8aebdcf1')
SNAPSHOT_FILE = PrivateBaselineFile(SNAPSHOT_ROOT + '.tgz', SNAPSHOT_SHA256)
# Actual 2026-10-25 18:53 KST custody readback, NOT an actual native RUN path.
# The native builder copies this explicit input only after receiving its context.
INPUT_FILES = RunInputFiles(
    PREPARED_ROOT + '/baseline-staging-20261025T091504', SNAPSHOT_FILE)
READY_REFERENCE = 'https://github.com/321Team/fds-project-baseline/issues/47#issuecomment-6361606970'
READY_ORIGINAL_SHA256 = 'f0c6fe2444362bc64d55d7dfaa1e0043222b508b0e57d0a96787329acf22628e'
PREVIOUS_IMAGES = (
    ('transaction-api', '10.1.93.54/fds/transaction-api@sha256:338b8a2871507ce8b42deedc916b26e350577ee6efd18df23d7ff2dd4b17dff0'),
    ('fds-engine', '10.1.93.54/fds/fds-engine@sha256:8b257adb08b6d381f72b66d3386efea6112a8f072ecfafcbcd463bcd1c3be89d'),
)


def private_selection_checked(config, grant, root_name, selected):
    """Adopt exact observed metadata <-> private bytes under an explicit grant.

    Generic shape/hash checks alone do not establish this association. No body
    parser, file read, authority/period generation or caller-value repair. The
    existing connector still authenticates original/control before and after
    reading, and checks the actual same-Binding plan/owners/previous afterward.
    """
    invalid = False
    try:
        binding = profile._binding_checked(config)
        private_metadata_checked(binding, grant, root_name, selected)
        archive.need(grant.original_pin == METADATA_PIN
                     and root_name == SNAPSHOT_ROOT and selected == SNAPSHOT_FILE)
    except Exception:
        invalid = True
    if invalid:
        raise archive.BaselineArchiveError('FDS_OBSERVED_BASELINE_REJECTED') from None
    return selected


def connect_selected_baseline(config, plan, *, received_archive, grant):
    """Use this deliberate snapshot/previous selection in the existing plan.

    No caller override of digest/root/previous, no replacement baseline and no
    approval/control creation. Existing authenticators/fresh object checks still
    run. Selecting observed owners never authorizes forcing field conflicts.
    """
    try:
        archive.need(type(plan) is RuntimeAuthorityPlan and plan.baseline is None)
        profile._source_profile(config, plan)
        private_selection_checked(config, grant, SNAPSHOT_ROOT, SNAPSHOT_FILE)
        previous = plan.producer.facts.previous_images
        archive.need(previous is None or previous == PREVIOUS_IMAGES)
        producer = replace(plan.producer, facts=replace(plan.producer.facts,
                                                      previous_images=PREVIOUS_IMAGES))
        selected = replace(plan, producer=producer)
        return archive.connect_baseline_archive(config, selected, archive=received_archive,
            expected_sha256=SNAPSHOT_SHA256, root_name=SNAPSHOT_ROOT, grant=grant)
    except Exception:
        raise archive.BaselineArchiveError('FDS_OBSERVED_BASELINE_REJECTED') from None


def selection_status():
    """Expected code selection only. No artifact, host or authority is queried."""
    return {'snapshot_sha256': SNAPSHOT_SHA256, 'snapshot_reference': SNAPSHOT_REFERENCE,
        'observed_kst': '2026-10-25T18:15:04+09:00',
        'metadata_pin': asdict(METADATA_PIN), 'snapshot_file': SNAPSHOT_FILE.name,
        'staging_root': INPUT_FILES.baseline_source_root,
        'staging_readback_kst': '2026-10-25T18:53:10+09:00',
        'replicas_handoff_observed': True, 'observed_replicas': 0,
        'observed_replicas_owner': 'fds-deployer/Apply/apps/v1/main',
        'previous_images': dict(PREVIOUS_IMAGES), 'ready_reference': READY_REFERENCE,
        'ready_original_sha256': READY_ORIGINAL_SHA256,
        'current_live_checked': False, 'rollback_verified': False,
        'execution_authorized': False,
        'write_issue': None, 'force_authorized': False}
