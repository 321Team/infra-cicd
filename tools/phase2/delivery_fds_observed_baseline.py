"""Explicit FDS baseline/previous CODE selection, not a Runtime grant.

The fixed archive covers the exact six observed UID/spec/owner bytes. The two
previous images below are deliberately selected from approved supply + manual
P2 deployment + observed Ready/imageID evidence; never inferred from an arbitrary
current snapshot or aliased from the next deployment's images.
"""
from dataclasses import replace

import delivery_fds_baseline_archive as archive
import delivery_fds_site_inputs as profile
from delivery_runtime_site import RuntimeAuthorityPlan

SNAPSHOT_SHA256 = 'e102270877a63a810d99de0567e4c95f3d24c1669d9e6bfefdde2aeab66c2a6f'
SNAPSHOT_ROOT = 'baseline6-20261023T133926'
SNAPSHOT_REFERENCE = 'https://github.com/321Team/fds-project-baseline/issues/46#issuecomment-6366234985'
READY_REFERENCE = 'https://github.com/321Team/fds-project-baseline/issues/47#issuecomment-6361606970'
READY_ORIGINAL_SHA256 = 'f0c6fe2444362bc64d55d7dfaa1e0043222b508b0e57d0a96787329acf22628e'
PREVIOUS_IMAGES = (
    ('transaction-api', '10.1.93.54/fds/transaction-api@sha256:338b8a2871507ce8b42deedc916b26e350577ee6efd18df23d7ff2dd4b17dff0'),
    ('fds-engine', '10.1.93.54/fds/fds-engine@sha256:8b257adb08b6d381f72b66d3386efea6112a8f072ecfafcbcd463bcd1c3be89d'),
)


def connect_selected_baseline(config, plan, *, received_archive, grant):
    """Use this deliberate snapshot/previous selection in the existing plan.

    No caller override of digest/root/previous, no replacement baseline and no
    approval/control creation. Existing authenticators/fresh object checks still
    run. Selecting observed owners never authorizes forcing field conflicts.
    """
    try:
        archive.need(type(plan) is RuntimeAuthorityPlan and plan.baseline is None)
        profile._source_profile(config, plan)
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
        'observed_kst': '2026-10-23T22:39:26+09:00',
        'previous_images': dict(PREVIOUS_IMAGES), 'ready_reference': READY_REFERENCE,
        'ready_original_sha256': READY_ORIGINAL_SHA256,
        'current_live_checked': False, 'rollback_verified': False,
        'execution_authorized': False,
        'write_issue': 'SSA_REPLICAS_CONFLICT_PENDING', 'force_authorized': False}
