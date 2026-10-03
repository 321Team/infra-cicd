#!/usr/bin/env python3
"""Fail-closed P1 delivery gate evaluation.

This module does not execute Podman, Harbor, kubectl, issue credentials, or
approve a deployment. It validates a reviewed delivery record and returns only
a deterministic pre-push/pre-deploy plan. Runtime adapters must consume the
plan without widening its scope.
"""
import copy
import re

SERVICES = ('transaction-api', 'fds-engine')
WORKFLOW_REPOSITORY = '321Team/infra-cicd'
SOURCE_REPOSITORY = '321Team/fds-msa'
HARBOR_ENDPOINT = 'https://10.1.93.54'
REGISTRY_PREFIX = '10.1.93.54/fds/'
KUBERNETES_API = 'https://10.1.93.109:6443'
NAMESPACE = 'fds-app'
SERVICE_ACCOUNT = 'ci-cd/cicd-runner-sa'
TOKEN_POLICY_REFERENCE = (
    'https://github.com/321Team/infra-kubernetes/blob/'
    '7329bf2eb988b552483af48ff660d6a3f1f1d4f5/scripts/create-secrets-safe.sh')

SHA = re.compile(r'[0-9a-f]{40}')
SHA256 = re.compile(r'[0-9a-f]{64}')
DIGEST = re.compile(r'sha256:[0-9a-f]{64}')
REFERENCE = re.compile(r'https://github\.com/321Team/[^\s]+')
CREDENTIAL_REFERENCE = re.compile(
    r'(?:actions-secret|runner-reference|handoff-reference):[A-Za-z0-9_.:/-]{1,200}')


def _obj(value):
    return value if isinstance(value, dict) else {}


def _text(value, limit=2000):
    return isinstance(value, str) and bool(value.strip()) and len(value) <= limit and \
        not any(ord(char) < 32 for char in value)


def _match(value, pattern):
    return isinstance(value, str) and pattern.fullmatch(value) is not None


def _positive_int(value):
    return type(value) is int and value > 0


def evaluate(record, phase):
    """Return BLOCK or CONDITIONS_MATCH without executing any external command."""
    reasons = []

    def need(condition, code):
        if not condition:
            reasons.append(code)

    record = copy.deepcopy(_obj(record))
    need(phase in ('pre_push', 'pre_deploy'), 'PHASE_INVALID')
    need(record.get('schema') == 'P1-CICD-delivery-gate-1', 'SCHEMA_INVALID')
    need(record.get('template_only') is False, 'TEMPLATE_OR_UNCONFIRMED_INPUT')
    need(record.get('status') == 'APPROVED_FOR_DELIVERY', 'DELIVERY_INPUT_NOT_APPROVED')

    source = _obj(record.get('source'))
    need(source.get('repository') == SOURCE_REPOSITORY, 'SOURCE_REPOSITORY_INVALID')
    need(_match(source.get('commit'), SHA), 'SOURCE_COMMIT_INVALID')
    need(_match(source.get('approval_reference'), REFERENCE), 'SOURCE_APPROVAL_REFERENCE_INVALID')

    workflow = _obj(record.get('workflow'))
    need(workflow.get('repository') == WORKFLOW_REPOSITORY, 'WORKFLOW_REPOSITORY_INVALID')
    need(workflow.get('ref') == 'refs/heads/main', 'WORKFLOW_MAIN_ONLY')
    need(_match(workflow.get('commit'), SHA), 'WORKFLOW_COMMIT_INVALID')
    need(_positive_int(workflow.get('run_id')), 'WORKFLOW_RUN_ID_INVALID')
    need(_positive_int(workflow.get('run_attempt')), 'WORKFLOW_RUN_ATTEMPT_INVALID')

    build = _obj(record.get('build_scan'))
    need(build.get('result') == 'PASS', 'BUILD_SCAN_NOT_PASS')
    need(build.get('source_commit') == source.get('commit'), 'BUILD_SOURCE_BINDING_MISMATCH')
    need(build.get('workflow_commit') == workflow.get('commit'), 'BUILD_WORKFLOW_BINDING_MISMATCH')
    need(_positive_int(build.get('run_id')), 'BUILD_RUN_ID_INVALID')
    need(_positive_int(build.get('run_attempt')), 'BUILD_RUN_ATTEMPT_INVALID')
    need(build.get('run_id') == workflow.get('run_id'), 'BUILD_RUN_BINDING_MISMATCH')
    need(build.get('run_attempt') == workflow.get('run_attempt'), 'BUILD_ATTEMPT_BINDING_MISMATCH')
    need(_match(build.get('input_sha256'), SHA256), 'BUILD_INPUT_HASH_INVALID')
    need(_match(build.get('scan_result_sha256'), SHA256), 'SCAN_RESULT_HASH_INVALID')

    images = _obj(build.get('images'))
    need(set(images) == set(SERVICES), 'EXACT_TWO_BUILD_IMAGES_REQUIRED')
    scan = _obj(build.get('scan'))
    need(set(scan) == set(SERVICES), 'EXACT_TWO_SCAN_RESULTS_REQUIRED')

    harbor = _obj(record.get('harbor'))
    need(harbor.get('endpoint') == HARBOR_ENDPOINT, 'HARBOR_ENDPOINT_INVALID')
    need(harbor.get('runner_tls_trust') == 'PASS', 'RUNNER_TLS_TRUST_NOT_PASS')
    need(harbor.get('if09_ac02') == 'PASS', 'HARBOR_IF09_AC02_NOT_PASS')
    need(_match(harbor.get('handoff_reference'), REFERENCE), 'HARBOR_HANDOFF_REFERENCE_INVALID')
    need(_match(harbor.get('credential_reference'), CREDENTIAL_REFERENCE),
         'HARBOR_CREDENTIAL_REFERENCE_INVALID')

    plans = []
    manifest_digests = []
    pushed = _obj(record.get('push'))
    targets = _obj(record.get('targets'))
    for name in SERVICES:
        image = _obj(images.get(name))
        scanned = _obj(scan.get(name))

        def check(condition, code):
            need(condition, f'{name}:{code}')

        check(_match(image.get('archive_sha256'), SHA256), 'ARCHIVE_HASH_INVALID')
        manifest = image.get('manifest_digest')
        manifest_digests.append(manifest)
        check(_match(manifest, DIGEST), 'MANIFEST_DIGEST_INVALID')
        check(_match(image.get('config_digest'), DIGEST), 'CONFIG_DIGEST_INVALID')
        check(scanned.get('scan') == 'PASS', 'SCAN_NOT_PASS')
        check(type(scanned.get('exit_code')) is int and scanned.get('exit_code') == 0,
              'SCAN_EXIT_CODE_NOT_ZERO')
        counts = _obj(scanned.get('counts'))
        check(set(counts) == {'HIGH', 'CRITICAL'}, 'SCAN_COUNTS_INVALID')
        check(type(counts.get('HIGH')) is int and counts.get('HIGH') == 0,
              'HIGH_FINDINGS_OR_INVALID_COUNT')
        check(type(counts.get('CRITICAL')) is int and counts.get('CRITICAL') == 0,
              'CRITICAL_FINDINGS_OR_INVALID_COUNT')
        check(scanned.get('manifest_digest') == manifest, 'SCAN_MANIFEST_MISMATCH')
        check(_match(scanned.get('report_sha256'), SHA256), 'SCAN_REPORT_HASH_INVALID')

        registry = harbor.get('repositories', {}).get(name) if isinstance(
            harbor.get('repositories'), dict) else None
        check(registry == REGISTRY_PREFIX + name, 'REGISTRY_REPOSITORY_INVALID')

        if phase == 'pre_push':
            plans.append({'service': name, 'archive_sha256': image.get('archive_sha256'),
                          'manifest_digest': manifest, 'registry_repository': registry})
            continue

        push = _obj(pushed.get(name))
        check(push.get('result') == 'PASS', 'PUSH_NOT_PASS')
        check(push.get('registry_repository') == registry, 'PUSH_REPOSITORY_MISMATCH')
        check(push.get('manifest_digest') == manifest, 'PUSH_MANIFEST_MISMATCH')
        check(_match(push.get('receipt_sha256'), SHA256), 'PUSH_RECEIPT_HASH_INVALID')

        target = _obj(targets.get(name))
        check(target.get('namespace') == NAMESPACE, 'TARGET_NAMESPACE_INVALID')
        check(target.get('deployment') == name, 'TARGET_DEPLOYMENT_INVALID')
        check(target.get('container') == name, 'TARGET_CONTAINER_INVALID')
        previous = target.get('previous_manifest_digest')
        check(_match(previous, DIGEST), 'ROLLBACK_DIGEST_INVALID')
        check(previous != manifest, 'ROLLBACK_DIGEST_EQUALS_NEW_DIGEST')
        plans.append({'service': name, 'namespace': NAMESPACE, 'deployment': name,
                      'container': name, 'image': registry + '@' + str(manifest),
                      'previous_manifest_digest': previous})

    need(len(manifest_digests) == len(SERVICES) and
         len(set(manifest_digests)) == len(SERVICES),
         'BUILD_MANIFEST_DIGESTS_NOT_DISTINCT')

    if phase == 'pre_deploy':
        need(set(pushed) == set(SERVICES), 'EXACT_TWO_PUSH_RESULTS_REQUIRED')
        need(set(targets) == set(SERVICES), 'EXACT_TWO_DEPLOY_TARGETS_REQUIRED')
        kubernetes = _obj(record.get('kubernetes'))
        need(kubernetes.get('api_server') == KUBERNETES_API, 'KUBERNETES_API_INVALID')
        need(kubernetes.get('service_account') == SERVICE_ACCOUNT, 'KUBERNETES_SERVICE_ACCOUNT_INVALID')
        need(kubernetes.get('rbac_scope') == 'PASS', 'KUBERNETES_RBAC_SCOPE_NOT_PASS')
        need(kubernetes.get('worker_pull') == 'PASS', 'WORKER_PULL_NOT_PASS')
        need(kubernetes.get('deployment_lock') == 'PASS', 'DEPLOYMENT_LOCK_NOT_PASS')
        ttl = kubernetes.get('token_ttl_seconds')
        need(type(ttl) is int and ttl == 3600, 'TOKEN_TTL_POLICY_MISMATCH')
        need(_text(kubernetes.get('token_audience'), 500), 'TOKEN_AUDIENCE_REQUIRED')
        need(kubernetes.get('token_policy_reference') == TOKEN_POLICY_REFERENCE,
             'TOKEN_POLICY_REFERENCE_INVALID')
        approval = _obj(record.get('deployment_approval'))
        need(_text(approval.get('approved_by'), 200), 'DEPLOY_APPROVER_INVALID')
        need(_match(approval.get('reference'), REFERENCE), 'DEPLOY_APPROVAL_REFERENCE_INVALID')
        need(_positive_int(approval.get('run_id')), 'DEPLOY_APPROVAL_RUN_ID_INVALID')
        need(_positive_int(approval.get('run_attempt')), 'DEPLOY_APPROVAL_ATTEMPT_INVALID')
        need(approval.get('workflow_commit') == workflow.get('commit'),
             'DEPLOY_APPROVAL_WORKFLOW_MISMATCH')
        need(approval.get('run_id') == workflow.get('run_id'), 'DEPLOY_APPROVAL_RUN_MISMATCH')
        need(approval.get('run_attempt') == workflow.get('run_attempt'),
             'DEPLOY_APPROVAL_ATTEMPT_MISMATCH')

    return {
        'decision': 'BLOCK' if reasons else 'CONDITIONS_MATCH',
        'reasons': reasons,
        'plan': None if reasons else {'phase': phase, 'items': plans},
        'basis': 'DECLARED_REVIEWED_INPUTS_ONLY',
        'runtime_enforcement': 'NOT_INTEGRATED',
        'delivery_gate': 'NOT_VERIFIED',
        'token_audience_validation': 'PRESENCE_ONLY_NO_PINNED_APPROVED_VALUE',
    }
