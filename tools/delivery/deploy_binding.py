"""Pure byte-binding validator for a separate Deploy execution.

No network, credentials, approval lookup or mutation. A matching document is
not independent approval verification. Runtime integration must verify the
references and secure the expected hash/execution context out of band.
"""
import datetime as dt
import hashlib
import json
import re

import delivery_gate as gate
from deploy_adapter import Target, targets_checked

AUDIENCE = 'https://kubernetes.default.svc.cluster.local'
FIELDS = {'workflow_sha', 'run_id', 'attempt', 'repository', 'ref'}
REFERENCE = re.compile(r'https://github\.com/321Team/[A-Za-z0-9_.-]+/(?:issues|pull)/[1-9][0-9]*(?:#[A-Za-z0-9_-]+)?')


class Invalid(ValueError):
    pass


def need(ok, code):
    if not ok:
        raise Invalid(code)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def read(raw):
    need(type(raw) is bytes and 0 < len(raw) <= 1024 * 1024, 'BYTES_INVALID')

    def pairs(items):
        result = {}
        for key, value in items:
            need(key not in result, 'DUPLICATE_JSON_KEY')
            result[key] = value
        return result

    def constant(_):
        raise Invalid('NONFINITE_JSON')

    try:
        result = json.loads(raw.decode('utf-8'), object_pairs_hook=pairs,
                            parse_constant=constant)
    except (UnicodeError, json.JSONDecodeError, RecursionError):
        raise Invalid('JSON_INVALID') from None
    need(type(result) is dict, 'OBJECT_REQUIRED')
    return result


def identity(obj):
    need(type(obj) is dict and set(obj) == FIELDS, 'EXECUTION_FIELDS_INVALID')
    need(obj['repository'] == gate.WORKFLOW_REPOSITORY and
         obj['ref'] == 'refs/heads/main', 'EXECUTION_SCOPE_INVALID')
    need(isinstance(obj['workflow_sha'], str) and
         gate.SHA.fullmatch(obj['workflow_sha']), 'EXECUTION_SHA_INVALID')
    for field in ('run_id', 'attempt'):
        need(type(obj[field]) is int and obj[field] > 0, 'EXECUTION_NUMBER_INVALID')
    return dict(obj)


def instant(text):
    try:
        need(type(text) is str, 'TIME_INVALID')
        result = dt.datetime.fromisoformat(text)
        need(result.tzinfo is not None and result.utcoffset() is not None, 'TIMEZONE_REQUIRED')
        return result.astimezone(dt.timezone.utc)
    except (ValueError, TypeError, OverflowError):
        raise Invalid('TIME_INVALID') from None


def validate(raw, push_raw, expected_sha256, execution, now):
    """Return normalized targets; no boolean here authorizes a real deployment."""
    try:
        return _validate(raw, push_raw, expected_sha256, execution, now)
    except Invalid:
        raise
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError):
        raise Invalid('RECORD_INVALID') from None


def payload_checked(record, push_raw, actual):
    """Shared Build/Push/target checks; caller validates its own approval schema."""
    delivery = record['delivery']
    need(gate.evaluate(delivery, 'pre_push')['decision'] == 'CONDITIONS_MATCH',
         'BUILD_INPUT_BLOCKED')
    # Preserve Build identity. Never forge a pre_deploy approval bound to Build Run.
    build = delivery['workflow']
    need(actual['run_id'] != build['run_id'], 'BUILD_RUN_REUSED')
    push = read(push_raw)
    need(sha(push_raw) == record['push_receipt_sha256'], 'PUSH_HASH_MISMATCH')
    need(push.get('PUSH') == 'PASS' and push.get('error_code') == 'NONE',
         'PUSH_NOT_PASS')
    need(push['build_workflow'] == build, 'PUSH_BUILD_MISMATCH')
    expected_push = record['push_execution']
    need(type(expected_push) is dict and
         set(expected_push) == {'workflow_sha', 'run_id', 'attempt'},
         'PUSH_EXECUTION_FIELDS_INVALID')
    need(type(expected_push['workflow_sha']) is str and
         gate.SHA.fullmatch(expected_push['workflow_sha']), 'PUSH_SHA_INVALID')
    need(type(expected_push['run_id']) is str and
         re.fullmatch(r'[1-9][0-9]*', expected_push['run_id']) and
         type(expected_push['attempt']) is int and expected_push['attempt'] > 0,
         'PUSH_EXECUTION_INVALID')
    context = push['execution_context']
    need(all(type(context[k]) is type(v) and context[k] == v
             for k, v in expected_push.items()), 'PUSH_EXECUTION_MISMATCH')
    need(actual['run_id'] != int(expected_push['run_id']), 'PUSH_RUN_REUSED')
    kubernetes = record['kubernetes']
    need(kubernetes['api_server'] == gate.KUBERNETES_API and
         kubernetes['service_account'] == gate.SERVICE_ACCOUNT and
         kubernetes['token_audience'] == AUDIENCE and
         type(kubernetes['token_ttl_seconds']) is int and
         kubernetes['token_ttl_seconds'] == 3600 and
         kubernetes['token_policy_reference'] == gate.TOKEN_POLICY_REFERENCE,
         'KUBERNETES_SCOPE_INVALID')
    need(set(record['targets']) == set(gate.SERVICES) and
         set(push['images']) == set(gate.SERVICES), 'EXACT_TARGETS_REQUIRED')
    targets = []
    for name in gate.SERVICES:
        target = record['targets'][name]
        image = push['images'][name]
        registry = gate.REGISTRY_PREFIX + name
        digest = delivery['build_scan']['images'][name]['manifest_digest']
        need(image['result'] == 'PASS' and image['registry_repository'] == registry
             and image['manifest_digest'] == digest, 'PUSH_IMAGE_MISMATCH')
        need(target['namespace'] == gate.NAMESPACE and
             target['deployment'] == target['container'] == name, 'TARGET_SCOPE_INVALID')
        targets.append(Target(name, target['uid'], target['previous_image'],
                              registry + '@' + digest))
    try:
        targets = targets_checked(targets)
    except Exception:
        raise Invalid('TARGET_IMAGE_OR_UID_INVALID') from None
    return dict(build), targets


def _validate(raw, push_raw, expected_sha256, execution, now):
    need(type(expected_sha256) is str and gate.SHA256.fullmatch(expected_sha256),
         'EXPECTED_HASH_INVALID')
    record = read(raw)
    need(sha(raw) == expected_sha256, 'INPUT_HASH_MISMATCH')
    need(record.get('schema') == 'P1-CICD-deploy-binding-1' and
         record.get('template_only') is False, 'SCHEMA_OR_TEMPLATE_INVALID')
    actual = identity(execution)
    need(identity(record['execution']) == actual, 'DEPLOY_EXECUTION_MISMATCH')
    build, targets = payload_checked(record, push_raw, actual)
    approval = record['approval']
    need(type(approval['reference']) is str and REFERENCE.fullmatch(approval['reference']),
         'APPROVAL_REFERENCE_INVALID')
    need(type(approval['approved_by']) is str and
         0 < len(approval['approved_by'].strip()) <= 200, 'APPROVER_INVALID')
    need(identity(approval['execution']) == actual, 'APPROVAL_EXECUTION_MISMATCH')
    need(approval['push_receipt_sha256'] == record['push_receipt_sha256'] and
         approval['targets'] == record['targets'], 'APPROVAL_INPUT_MISMATCH')
    # Bind both directions explicitly, not just previous digests.
    approved_images = {t.service: t.image for t in targets}
    need(approval['images'] == approved_images, 'APPROVAL_IMAGE_MISMATCH')
    need(type(approval['allow_rollback']) is bool, 'ROLLBACK_SCOPE_INVALID')
    start, end = instant(approval['not_before']), instant(approval['expires_at'])
    need(isinstance(now, dt.datetime) and now.tzinfo is not None and
         now.utcoffset() is not None, 'NOW_INVALID')
    need(start < end and start <= now < end, 'APPROVAL_WINDOW_INVALID')
    return {'binding': 'MATCH', 'basis': 'SUPPLIED_BYTES_AND_DECLARED_APPROVAL',
            'approval_verification': 'NOT_INDEPENDENTLY_VERIFIED',
            'runtime_enforcement': 'NOT_INTEGRATED', 'delivery_gate': 'NOT_VERIFIED',
            'input_sha256': expected_sha256, 'push_receipt_sha256': sha(push_raw),
            'execution': actual, 'build_workflow': dict(build),
            'targets': targets, 'expires_at': end,
            'remaining_seconds': (end - now).total_seconds(),
            'allow_rollback': approval['allow_rollback']}
