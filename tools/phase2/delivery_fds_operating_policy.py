"""User-adopted FDS preparation/control selection. NOT an execution GO/loader.

Actual native RUN, finite grant terms, role originals and baseline are required
from reviewed site code. This module neither manufactures them nor opens SITE.
The adopted scheduling policy is always-open; 240 minutes is guidance only.
"""
from datetime import datetime

import delivery_adapter as core
import delivery_actions_source as actions
import delivery_fds_existing_inputs as existing
import delivery_fds_site_inputs as profile
import delivery_fds_observed_connection as connection
import delivery_job as job
from delivery_adopted_authority import AdoptedGrant, pin_identity
from delivery_github_source import RepositoryPin, BlobPin, CommentPin, repository_checked
from delivery_token_materializer import NativePlanSelection
from dataclasses import replace

ACTOR = 'gwonuk'
ADOPTION_REFERENCE = 'https://github.com/321Team/fds-project-baseline/issues/46#issuecomment-6385867126'
# A real preparation-only comment. Its identity is denied as execution approval
# even if its update timestamp/body hash changes; other source pins stay explicit.
PREPARATION_COMMENT = ('321Team', 'fds-project-baseline', 46, 6385867126)
PREPARATION_COMMENTS = (PREPARATION_COMMENT,
    ('321Team', 'fds-project-baseline', 46, 6394784788))
# Real post-handoff custody/adoption metadata, explicitly NOT execution/finish
# permission. Its source identity is usable only in the baseline role.
BASELINE_ONLY_COMMENT = ('321Team', 'fds-project-baseline', 46, 6395884121)
CONTROL_PIN = BlobPin(
    RepositoryPin('321Team', 'infra-cicd', 1362221024, 315682664),
    '6531ba8e301858c0e180c377d06908f895158a70',
    'docs/phase2/operating-handoff.md',
    '4bbac06d284a7d8c68ff867a5fb6f69ead4881ba56973bd7bcbb6f20c4e9311b',
    'heads/codex/p46-operating-control-20261025',
    '6531ba8e301858c0e180c377d06908f895158a70')
CLEANUP_AT = '2026-10-26T12:00:00+09:00'
CLEANUP_END = datetime.fromisoformat(CLEANUP_AT).timestamp()
# Owner's current recall PLAN (#8 6378716458, reaffirmed by PR142 reviews),
# not proof of actual revocation or a new execution grant. Keep global cleanup
# 12:00 separate; app0/claim completion must precede recall starting at 08:30.
RECALL_PLAN_REFERENCE = 'https://github.com/321Team/infra-rosa/issues/8#issuecomment-6378716458'
CONSUMPTION_END_AT = '2026-10-26T08:30:00+09:00'
CONSUMPTION_END = datetime.fromisoformat(CONSUMPTION_END_AT).timestamp()


class OperatingPolicyError(RuntimeError):
    """Fixed public code; no source bodies, credentials or foreign exceptions."""


def need(ok):
    if not ok:
        raise OperatingPolicyError('FDS_OPERATING_SELECTION_INVALID')


def selection_checked(config, context, selection, finish_scope, producer_repository,
                      producer_pins, *, baseline_grant=None):
    """Inert constraints on actual CODE selections, not authority discovery.

    A fresh control pin is a revocation fence only under the explicitly adopted
    update semantics. Grant meaning remains a reviewed-code responsibility; a
    matching hash/type/actor or this function's return does not authenticate it.
    The existing resolver still reads actual originals/control on each check.
    """
    invalid = False
    try:
        b = profile._binding_checked(config)  # Existing deploy-only FDS profile.
        need(type(context) is job.JobContext)
        context.check()
        need(type(selection) is NativePlanSelection)
        selection.check(b)
        if selection.baseline is None:
            # Exact deferred grant/root is required by the operating connector.
            # Raw owner/spec is checked after private source read, BEFORE plan admission.
            need(type(baseline_grant) is AdoptedGrant)
            previous = selection.producer.facts.previous_images
            need(type(previous) is tuple and len(previous) == len(core.services_for(b)))
            core.binding_checked(replace(b, images=previous))
        else:
            need(baseline_grant is None)
            existing.validate_existing_baseline(b, selection.baseline,
                                                selection.producer.facts.previous_images)
            baseline_grant = selection.baseline.grant
        _, _, namespace, name = profile.SUBJECT.split(':')
        need((selection.target.namespace, selection.target.name, selection.target.uid,
              selection.target.issuer, selection.target.audience) ==
             (namespace, name, profile.SUBJECT_UID, profile.ISSUER, profile.AUDIENCE)
             and (selection.subject.username, selection.subject.uid) ==
             (profile.SUBJECT, profile.SUBJECT_UID)
             and selection.ca_pem == profile.CA_PEM
             and selection.endpoint_ip == connection.API_ENDPOINT_IP)
        need(selection.approval.principal == ACTOR)
        grants = (selection.approval, selection.issuer_grant,
                  selection.producer.grant, baseline_grant, finish_scope)
        for role, grant in zip(('approval', 'platform', 'producer', 'baseline', 'finish'),
                               grants, strict=True):
            need(type(grant) is AdoptedGrant and grant.binding == b
                 and grant.control_pin == CONTROL_PIN
                 and core.finite(grant.starts_at) and core.finite(grant.expires_at)
                 and grant.starts_at < grant.expires_at)
            pin_identity(grant.original_pin)
            pin_identity(grant.control_pin)
            original = grant.original_pin
            if type(original) is CommentPin:
                identity = (original.repository.owner.casefold(),
                    original.repository.name.casefold(), original.issue_number, original.comment_id)
                need(identity not in tuple((p[0].casefold(), p[1].casefold(), *p[2:])
                     for p in PREPARATION_COMMENTS))
                baseline_only = (BASELINE_ONLY_COMMENT[0].casefold(),
                    BASELINE_ONLY_COMMENT[1].casefold(), *BASELINE_ONLY_COMMENT[2:])
                need(identity != baseline_only or role == 'baseline')
        # Scheduling is always open; neither a made-up start nor 240-min cap.
        # Only execution/finish validity is bounded by actual final cleanup.
        need(selection.approval.expires_at <= CLEANUP_END
             and finish_scope.expires_at <= CLEANUP_END
             and selection.approval.expires_at <= CONSUMPTION_END
             and finish_scope.expires_at <= CONSUMPTION_END)
        actions.policy_checked(producer_pins)
        repository_checked(producer_repository)
        refs = tuple(getattr(selection.producer.facts, x + '_reference')
                     for x in ('test', 'build', 'scan', 'push'))
        need(tuple(p.producer_reference for p in producer_pins) == refs
             and all(p.run.repository == producer_repository for p in producer_pins))
    except Exception:
        invalid = True
    if invalid:
        # Raise outside except so foreign callback/body errors cannot survive
        # as __context__. This function calls no readers/clocks/callbacks.
        raise OperatingPolicyError('FDS_OPERATING_SELECTION_INVALID') from None
    return selection


def selection_status():
    """Fresh non-secret CODE policy report, never a grant or live source check."""
    return {'adoption_reference': ADOPTION_REFERENCE,
        'scheduling': 'ALWAYS_OPEN', 'budget_minutes_reference_only': 240,
        'scope': {'normal_runs': 1, 'failed_gate_runs': 1, 'restore_runs': 1,
                  'end_replicas': 0, 'automatic_retry': False},
        'control': {'repository': '321Team/infra-cicd',
            'commit': CONTROL_PIN.commit, 'path': CONTROL_PIN.path,
            'sha256': CONTROL_PIN.sha256, 'ref': CONTROL_PIN.anchor_ref,
            'head': CONTROL_PIN.anchor_commit, 'live_checked': False},
        'cleanup_at': CLEANUP_AT, 'execution_authorized': False,
        'consumption_end_at': CONSUMPTION_END_AT,
        'consumption_end_basis': 'OWNER_PLANNED_RECALL_NOT_REVOCATION_PROOF',
        'recall_plan_reference': RECALL_PLAN_REFERENCE,
        'actual_run_grants_supplied': False}
