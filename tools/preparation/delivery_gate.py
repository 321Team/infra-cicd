#!/usr/bin/env python3
"""Local pre-push/pre-deploy decision prototype; no production command adapters."""
import argparse
import copy
import json
import re

from input_json import read_record

DEPLOYMENTS = ('transaction-api', 'fds-engine')
REPO = '321Team/infra-cicd'


def matches(value, pattern):
    return isinstance(value, str) and re.fullmatch(pattern, value) is not None


def digest(value):
    return matches(value, r'sha256:[0-9a-f]{64}')


def sha(value):
    return matches(value, r'[0-9a-f]{40}')


def reference(value):
    return isinstance(value, str) and 0 < len(value.strip()) <= 500


def positive_int(value):
    return type(value) is int and value > 0


def obj(value):
    return value if isinstance(value, dict) else {}


def evaluate(record, phase):
    reasons = []
    def need(ok, code):
        if not ok:
            reasons.append(code)
    record = obj(record)
    need(phase in ('pre_push', 'pre_deploy'), 'PHASE')
    need(record.get('schema') == 'P1-CICD-01-gate-input-1.0', 'SCHEMA')
    need(record.get('template_only') is False, 'TEMPLATE_OR_UNCONFIRMED_INPUT')
    workflow = obj(record.get('workflow'))
    need(workflow.get('repository') == REPO, 'WORKFLOW_REPOSITORY')
    need(workflow.get('ref') == 'refs/heads/main', 'WORKFLOW_MAIN_ONLY')
    need(sha(workflow.get('commit')) and workflow.get('commit') == workflow.get('approved_commit'),
         'WORKFLOW_COMMIT_APPROVAL')
    need(reference(workflow.get('approval_reference')), 'WORKFLOW_APPROVAL_REFERENCE')
    need(positive_int(workflow.get('run_id')), 'WORKFLOW_RUN_ID')
    need(positive_int(workflow.get('run_attempt')), 'WORKFLOW_RUN_ATTEMPT')
    runtime = obj(record.get('runtime'))
    for field in ('runner_job', 'builder_rootless', 'harbor_tls'):
        need(runtime.get(field) == 'PASS', 'RUNTIME_' + field.upper())
    images = record.get('images')
    images = images if isinstance(images, list) else []
    names = [obj(image).get('deployment') for image in images]
    need(len(images) == 2 and all(names.count(name) == 1 for name in DEPLOYMENTS),
         'EXACT_TWO_DEPLOYMENTS')
    plans = []
    # Do not echo arbitrary identifiers or input values in error reports.
    for name in DEPLOYMENTS:
        candidates = [obj(image) for image in images if obj(image).get('deployment') == name]
        image = candidates[0] if len(candidates) == 1 else {}
        def check(ok, code):
            need(ok, name + ':' + code)
        source = obj(image.get('source'))
        check(matches(source.get('repository'), r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+')
              and source.get('repository') == source.get('approved_repository'), 'SOURCE_REPOSITORY')
        check(sha(source.get('commit')) and source.get('commit') == source.get('approved_commit'),
              'SOURCE_COMMIT_APPROVAL')
        check(reference(source.get('approval_reference')), 'SOURCE_APPROVAL_REFERENCE')
        tests = obj(image.get('tests'))
        check(tests.get('unit') == 'PASS', 'UNIT_TEST')
        check(tests.get('contract') == 'PASS', 'CONTRACT_TEST')
        build = obj(image.get('build'))
        scan = obj(image.get('scan'))
        check(build.get('result') == 'PASS', 'BUILD')
        built = build.get('manifest_digest')
        check(digest(built), 'BUILT_MANIFEST_DIGEST')
        check(scan.get('result') == 'PASS', 'SCAN_RESULT')
        check(type(scan.get('exit_code')) is int and scan.get('exit_code') == 0, 'SCAN_EXIT_CODE')
        check(type(scan.get('critical_count')) is int and scan.get('critical_count') == 0,
              'CRITICAL_FINDINGS_OR_INVALID_COUNT')
        check(digest(scan.get('manifest_digest')) and scan.get('manifest_digest') == built,
              'SCAN_MANIFEST_MISMATCH')
        check(matches(scan.get('report_sha256'), r'[0-9a-f]{64}'), 'SCAN_REPORT_HASH')
        check(reference(scan.get('tool_version')) and reference(scan.get('db_reference')), 'SCAN_TOOL_AND_DB')
        registry = image.get('registry_repository')
        check(matches(registry, r'10\.1\.93\.54/fds/[a-z0-9]+(?:[._/-][a-z0-9]+)*')
              and registry == image.get('approved_registry_repository'), 'REGISTRY_REPOSITORY')
        if phase == 'pre_deploy':
            push = obj(image.get('push'))
            check(push.get('result') == 'PASS', 'PUSH')
            check(digest(push.get('manifest_digest')) and push.get('manifest_digest') == built,
                  'PUSH_MANIFEST_MISMATCH')
            check(digest(image.get('approved_manifest_digest')) and image.get('approved_manifest_digest') == built,
                  'DEPLOY_APPROVED_MANIFEST_MISMATCH')
            target = obj(image.get('target'))
            check(target.get('namespace') == 'fds-app', 'TARGET_NAMESPACE')
            check(target.get('service_account') == 'ci-cd/cicd-runner-sa', 'TARGET_SERVICE_ACCOUNT')
            check(matches(target.get('container'), r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?'),
                  'CONTAINER_NAME')
            check(digest(target.get('previous_manifest_digest')), 'ROLLBACK_DIGEST')
            plans.append({'deployment': name, 'namespace': 'fds-app',
                          'container': target.get('container'), 'image': str(registry) + '@' + str(built),
                          'previous_manifest_digest': target.get('previous_manifest_digest')})
        else:
            plans.append({'deployment': name, 'registry_repository': registry, 'manifest_digest': built})
    if phase == 'pre_deploy':
        for field in ('kubernetes_access', 'token_valid', 'rbac_scope', 'worker_trust', 'deployment_lock'):
            need(runtime.get(field) == 'PASS', 'RUNTIME_' + field.upper())
        approval = obj(record.get('deployment_approval'))
        need(reference(approval.get('approved_by')) and reference(approval.get('reference')), 'DEPLOY_APPROVAL')
        need(type(approval.get('run_id')) is int and approval.get('run_id') == workflow.get('run_id'),
             'APPROVAL_RUN_BINDING')
        need(type(approval.get('run_attempt')) is int and approval.get('run_attempt') == workflow.get('run_attempt'),
             'APPROVAL_ATTEMPT_BINDING')
        need(sha(approval.get('workflow_commit')) and approval.get('workflow_commit') == workflow.get('commit'),
             'APPROVAL_WORKFLOW_BINDING')
    return {'decision': 'BLOCK' if reasons else 'CONDITIONS_MATCH', 'reasons': reasons,
            'plan': None if reasons else {'phase': phase, 'images': plans},
            'basis': 'DECLARED_INPUTS_ONLY', 'runtime_enforcement': 'NOT_INTEGRATED',
            'delivery_gate': 'NOT_VERIFIED'}


class GateBlocked(RuntimeError):
    pass


def guarded_call(record, phase, action):
    """For trusted adapters/tests only. CLI never calls an action or a shell."""
    decision = evaluate(copy.deepcopy(record), phase)
    if decision['decision'] != 'CONDITIONS_MATCH':
        raise GateBlocked('PRECONDITIONS_NOT_MET')
    return action(decision['plan'])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--record', required=True)
    parser.add_argument('--phase', required=True, choices=('pre_push', 'pre_deploy'))
    args = parser.parse_args(argv)
    try:
        result = evaluate(read_record(args.record), args.phase)
    except (OSError, ValueError, TypeError, RecursionError):
        print(json.dumps({'decision': 'INPUT_ERROR', 'delivery_gate': 'NOT_VERIFIED'}))
        return 3
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['decision'] == 'CONDITIONS_MATCH' else 2


if __name__ == '__main__':
    raise SystemExit(main())
