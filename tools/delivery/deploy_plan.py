"""Pre-dispatch plan and detached approval binding; no network or deployment.

v1 remains strict about a known Run ID. v2 approves a workflow SHA and exact
plan bytes before dispatch, then records the trusted Run/Attempt separately.
Supplied approval bytes are not independent approval verification.
"""
import re

import deploy_binding as b
import delivery_gate as gate

WORKFLOW_FIELDS = {'repository', 'ref', 'workflow_sha'}
PLAN_FIELDS = {'schema', 'template_only', 'request_id', 'workflow', 'delivery',
               'push_execution', 'push_receipt_sha256', 'kubernetes', 'targets'}
APPROVAL_FIELDS = {'schema', 'template_only', 'plan_sha256', 'workflow',
                   'reference', 'approved_by', 'not_before', 'expires_at',
                   'allow_rollback'}
KUBERNETES_FIELDS = {'api_server', 'service_account', 'token_audience',
                     'token_ttl_seconds', 'token_policy_reference'}
TARGET_FIELDS = {'namespace', 'deployment', 'container', 'uid', 'previous_image'}
RECOVERY_SCHEMA = 'P1-CICD-recovery-plan-1'
RECOVERY_FIELDS = {'origin_plan', 'origin_approval', 'origin_plan_sha256',
                   'origin_approval_sha256', 'origin_execution', 'evidence_reference'}


def workflow(obj):
    b.need(type(obj) is dict and set(obj) == WORKFLOW_FIELDS,
           'WORKFLOW_FIELDS_INVALID')
    normalized = b.identity(dict(obj, run_id=1, attempt=1))
    return {key: normalized[key] for key in WORKFLOW_FIELDS}


def expected_hash(raw, expected, code):
    b.need(type(expected) is str and gate.SHA256.fullmatch(expected),
           'EXPECTED_HASH_INVALID')
    result = b.read(raw)
    b.need(b.sha(raw) == expected, code)
    return result


def validate(plan_raw, approval_raw, push_raw, expected_plan_sha256,
             expected_approval_sha256, execution, now):
    """Validate bytes and scope; does not authorize API calls or acquire a claim."""
    try:
        return _validate(plan_raw, approval_raw, push_raw, expected_plan_sha256,
                         expected_approval_sha256, execution, now)
    except b.Invalid:
        raise
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError):
        raise b.Invalid('RECORD_INVALID') from None


def recovery_basis(plan, push_raw, actual, now):
    """Bind an earlier forward plan; historical bytes are NOT current authority.

    The fresh detached approval covers these exact bytes and the recovery schema.
    Never recursively accept a recovery plan or infer an arbitrary desired image.
    """
    origin = plan['recovery']
    b.need(type(origin) is dict and set(origin) == RECOVERY_FIELDS,
           'RECOVERY_FIELDS_INVALID')
    b.need(all(type(origin[k]) is str for k in ('origin_plan', 'origin_approval')),
           'ORIGIN_BYTES_REQUIRED')
    plan_raw, approval_raw = (origin[k].encode('utf-8')
                             for k in ('origin_plan', 'origin_approval'))
    prior = expected_hash(plan_raw, origin['origin_plan_sha256'], 'ORIGIN_PLAN_HASH_MISMATCH')
    old_approval = expected_hash(approval_raw, origin['origin_approval_sha256'],
                                'ORIGIN_APPROVAL_HASH_MISMATCH')
    b.need(prior.get('schema') == 'P1-CICD-deploy-plan-2' and set(prior) == PLAN_FIELDS,
           'ORIGIN_FORWARD_PLAN_REQUIRED')
    b.need(prior['request_id'] != plan['request_id'], 'ORIGIN_REQUEST_REUSED')
    previous_execution = b.identity(origin['origin_execution'])
    b.need(previous_execution['run_id'] != actual['run_id'], 'ORIGIN_RUN_REUSED')
    b.need(type(origin['evidence_reference']) is str and
           b.REFERENCE.fullmatch(origin['evidence_reference']), 'ORIGIN_REFERENCE_INVALID')
    # This checks the old contract at its declared window start only. It does not
    # assert when/if it ran, or fetch/renew the old approval. The current approval
    # and fresh baseline GETs remain mandatory at execution time.
    old_start = b.instant(old_approval['not_before'])
    b.need(old_start < now, 'ORIGIN_TIME_INVALID')
    historical = _validate(plan_raw, approval_raw, push_raw,
                           origin['origin_plan_sha256'], origin['origin_approval_sha256'],
                           previous_execution, old_start)
    b.need(historical['allow_rollback'] is True, 'ORIGIN_ROLLBACK_NOT_APPROVED')
    for key in ('delivery', 'push_execution', 'push_receipt_sha256', 'kubernetes', 'targets'):
        b.need(plan[key] == prior[key], 'ORIGIN_SCOPE_MISMATCH')
    return {
        'origin_plan_sha256': origin['origin_plan_sha256'],
        'origin_approval_sha256': origin['origin_approval_sha256'],
        'origin_execution': previous_execution,
        'evidence_reference': origin['evidence_reference'],
        'historical_approval_verification': 'SUPPLIED_BYTES_ONLY',
        'historical_runtime': 'NOT_VERIFIED',
    }


def _validate(plan_raw, approval_raw, push_raw, expected_plan_sha256,
              expected_approval_sha256, execution, now):
    plan = expected_hash(plan_raw, expected_plan_sha256, 'PLAN_HASH_MISMATCH')
    approval = expected_hash(approval_raw, expected_approval_sha256,
                             'APPROVAL_HASH_MISMATCH')
    recovering = plan.get('schema') == RECOVERY_SCHEMA
    b.need(set(plan) == (PLAN_FIELDS | {'recovery'} if recovering else PLAN_FIELDS),
           'PLAN_FIELDS_INVALID')
    b.need(plan['schema'] in ('P1-CICD-deploy-plan-2', RECOVERY_SCHEMA) and
           plan['template_only'] is False, 'PLAN_SCHEMA_OR_TEMPLATE_INVALID')
    b.need(type(plan['request_id']) is str and re.fullmatch(
        r'[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}', plan['request_id']),
        'REQUEST_ID_INVALID')
    actual = b.identity(execution)
    b.need(actual['attempt'] == 1, 'RETRY_ATTEMPT_NOT_APPROVED')
    selected = workflow(plan['workflow'])
    b.need({key: actual[key] for key in WORKFLOW_FIELDS} == selected,
           'DEPLOY_WORKFLOW_MISMATCH')
    b.need(type(plan['kubernetes']) is dict and
           set(plan['kubernetes']) == KUBERNETES_FIELDS,
           'KUBERNETES_FIELDS_INVALID')
    b.need(type(plan['targets']) is dict and
           set(plan['targets']) == set(gate.SERVICES), 'EXACT_TARGETS_REQUIRED')
    for target in plan['targets'].values():
        b.need(type(target) is dict and set(target) == TARGET_FIELDS,
               'TARGET_FIELDS_INVALID')
    build, targets = b.payload_checked(plan, push_raw, actual)
    b.need(int(plan['push_execution']['run_id']) != build['run_id'],
           'BUILD_PUSH_RUN_REUSED')
    b.need(set(approval) == APPROVAL_FIELDS, 'APPROVAL_FIELDS_INVALID')
    b.need(approval['schema'] == 'P1-CICD-deploy-approval-2' and
           approval['template_only'] is False, 'APPROVAL_SCHEMA_OR_TEMPLATE_INVALID')
    b.need(approval['plan_sha256'] == expected_plan_sha256,
           'APPROVAL_PLAN_MISMATCH')
    b.need(workflow(approval['workflow']) == selected,
           'APPROVAL_WORKFLOW_MISMATCH')
    b.need(type(approval['reference']) is str and
           b.REFERENCE.fullmatch(approval['reference']), 'APPROVAL_REFERENCE_INVALID')
    approver = approval['approved_by']
    b.need(type(approver) is str and 0 < len(approver.strip()) <= 200 and
           not any(ord(c) < 32 for c in approver), 'APPROVER_INVALID')
    b.need(type(approval['allow_rollback']) is bool, 'ROLLBACK_SCOPE_INVALID')
    start, end = b.instant(approval['not_before']), b.instant(approval['expires_at'])
    b.need(isinstance(now, b.dt.datetime) and now.tzinfo is not None and
           now.utcoffset() is not None, 'NOW_INVALID')
    b.need(start < end and start <= now < end, 'APPROVAL_WINDOW_INVALID')
    origin = recovery_basis(plan, push_raw, actual, now) if recovering else None
    if recovering:
        targets = b.targets_checked(b.Target(t.service, t.uid, t.image, t.previous_image)
                                    for t in targets)
    return {
        'schema': 'P1-CICD-deploy-plan-binding-2',
        'binding': 'MATCH', 'basis': 'SUPPLIED_BYTES_AND_DECLARED_APPROVAL',
        'approval_verification': 'NOT_INDEPENDENTLY_VERIFIED',
        'runtime_enforcement': 'NOT_INTEGRATED', 'delivery_gate': 'NOT_VERIFIED',
        'claim': 'NOT_ACQUIRED',
        'operation': 'RESTORE_PREVIOUS' if recovering else 'DEPLOY_PUSHED',
        'recovery_basis': origin,
        'plan_sha256': expected_plan_sha256,
        'approval_sha256': expected_approval_sha256,
        'push_receipt_sha256': b.sha(push_raw),
        'request_id': plan['request_id'], 'workflow': selected,
        'execution': actual, 'build_workflow': build,
        'push_execution': dict(plan['push_execution']),
        'targets': targets, 'approval_reference': approval['reference'],
        'not_before': start, 'expires_at': end,
        'remaining_seconds': (end - now).total_seconds(),
        'allow_rollback': approval['allow_rollback'],
    }
