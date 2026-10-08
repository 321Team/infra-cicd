#!/usr/bin/env python3
"""Offline P2-CICD-01 intake diagnostics. Never a deployment authorization.

Only reads one local JSON file. Does not fetch references, read credentials,
import the P1 executor, call APIs, write files, or produce an executable plan.
"""
import argparse
import json
import re
from pathlib import Path
from urllib.parse import urlsplit

SERVICES = ('transaction-api', 'fds-engine')
SHA = r'[0-9a-f]{40}'
HASH = r'[0-9a-f]{64}'
NAME = r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?'
REF = r'(?:actions-secret|runner-reference|handoff-reference):[A-Za-z0-9_.:/-]{1,200}'
MAX_BYTES = 131072


def match(value, pattern):
    return isinstance(value, str) and re.fullmatch(pattern, value) is not None


def https_url(value, origin=False):
    if not isinstance(value, str) or len(value) > 2048:
        return False
    if any(ord(c) <= 32 or ord(c) >= 127 for c in value) or '\\' in value:
        return False
    try:
        url = urlsplit(value)
        port = url.port
        return bool(url.scheme == 'https' and url.hostname and
                    re.fullmatch(r'[a-zA-Z0-9.-]+', url.hostname) and
                    url.username is None and url.password is None and
                    not url.query and not url.fragment and
                    (port is None or 1 <= port <= 65535) and
                    (not origin or url.path in ('', '/')))
    except ValueError:
        return False


def image(value, registry):
    if not isinstance(value, str) or not https_url(registry, origin=True):
        return False
    authority = urlsplit(registry).netloc
    return match(value, re.escape(authority) +
                 r'/[a-z0-9]+(?:[._-][a-z0-9]+)*(?:/[a-z0-9]+(?:[._-][a-z0-9]+)*)*'
                 r'@sha256:' + HASH)


def evaluate(record):
    """Check declared data only; no output can authorize any execution."""
    errors = []

    def need(ok, code):
        if not ok:
            errors.append(code)

    def obj(value, keys, path):
        if not isinstance(value, dict):
            errors.append(path + ':OBJECT_REQUIRED')
            return {}
        need(set(value) == set(keys.split()), path + ':FIELDS_MISMATCH')
        return value

    def pin(value, path):
        need(match(value, SHA), path + ':SHA_REQUIRED')

    def reference(value, path):
        need(https_url(value), path + ':HTTPS_REFERENCE_REQUIRED')

    root = obj(record, 'schema template_only environment target source registry credential '
               'entry build push workloads', 'record')
    need(root.get('schema') == 'P2-CICD-intake-1', 'SCHEMA_INVALID')
    need(root.get('template_only') is False, 'TEMPLATE_OR_UNCONFIRMED')
    need(root.get('environment') == 'ROSA', 'ROSA_ENVIRONMENT_REQUIRED')
    target = obj(root.get('target'), 'cluster_reference api_url ca_reference context namespace health_url', 'target')
    for field in ('cluster_reference', 'ca_reference'):
        reference(target.get(field), 'target.' + field)
    api = target.get('api_url')
    need(https_url(api, origin=True), 'TARGET_HTTPS_ORIGIN_REQUIRED')
    # The existing P1 fixed API must never silently become the P2 target.
    if https_url(api, origin=True):
        need(urlsplit(api).hostname != '10.1.93.109', 'P1_API_NOT_ROSA')
    need(match(target.get('namespace'), NAME), 'NAMESPACE_INVALID')
    need(match(target.get('context'), r'[A-Za-z0-9_.:/@-]{1,200}'), 'CONTEXT_INVALID')
    reference(target.get('health_url'), 'target.health_url')
    source = obj(root.get('source'), 'repository commit manifest_repository manifest_commit '
                 'base_path overlay_path rendered_sha256', 'source')
    need(source.get('repository') == '321Team/fds-msa', 'APP_REPOSITORY_INVALID')
    pin(source.get('commit'), 'source.commit')
    need(match(source.get('manifest_repository'), r'321Team/[A-Za-z0-9_.-]+'), 'MANIFEST_REPOSITORY_INVALID')
    pin(source.get('manifest_commit'), 'source.manifest_commit')
    for field in ('base_path', 'overlay_path'):
        path = source.get(field)
        need(match(path, r'[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*') and
             all(p not in ('.', '..') for p in path.split('/')), 'source.' + field + ':PATH_INVALID')
    need(match(source.get('rendered_sha256'), HASH), 'RENDERED_HASH_REQUIRED')
    registry = obj(root.get('registry'), 'endpoint decision_reference', 'registry')
    need(https_url(registry.get('endpoint'), origin=True), 'REGISTRY_HTTPS_ORIGIN_REQUIRED')
    reference(registry.get('decision_reference'), 'registry.decision')
    credential = obj(root.get('credential'), 'reference policy_reference', 'credential')
    need(match(credential.get('reference'), REF), 'CREDENTIAL_REFERENCE_REQUIRED')
    reference(credential.get('policy_reference'), 'credential.policy')
    entry = obj(root.get('entry'), 'platform_reference app_reference runtime_scope_reference', 'entry')
    for field in ('platform_reference', 'app_reference', 'runtime_scope_reference'):
        reference(entry.get(field), 'entry.' + field)
    for stage in ('build', 'push'):
        run = obj(root.get(stage), 'source_commit workflow_commit run_id attempt evidence_reference result', stage)
        pin(run.get('source_commit'), stage + '.source_commit')
        pin(run.get('workflow_commit'), stage + '.workflow_commit')
        need(run.get('source_commit') == source.get('commit'), stage + ':SOURCE_MISMATCH')
        for field in ('run_id', 'attempt'):
            need(type(run.get(field)) is int and run[field] > 0, stage + '.' + field + ':POSITIVE_INTEGER_REQUIRED')
        reference(run.get('evidence_reference'), stage + '.evidence')
        need(run.get('result') == 'PASS', stage + ':NOT_PASS')
    workloads = obj(root.get('workloads'), ' '.join(SERVICES), 'workloads')
    for service in SERVICES:
        workload = obj(workloads.get(service), 'deployment container approved_image pushed_image '
                       'previous_approved_image health_contract_reference test_result scan_result scan_evidence_reference '
                       'rollback_reference', service)
        for field in ('deployment', 'container'):
            need(match(workload.get(field), NAME), service + '.' + field + ':NAME_INVALID')
        for field in ('approved_image', 'pushed_image', 'previous_approved_image'):
            need(image(workload.get(field), registry.get('endpoint')), service + '.' + field + ':IMMUTABLE_IMAGE_REQUIRED')
        need(workload.get('approved_image') == workload.get('pushed_image'), service + ':PUSH_DIGEST_MISMATCH')
        for field in ('test_result', 'scan_result'):
            need(workload.get(field) == 'PASS', service + '.' + field + ':NOT_PASS')
        for field in ('health_contract_reference', 'scan_evidence_reference', 'rollback_reference'):
            reference(workload.get(field), service + '.' + field)
    deployments = [workloads.get(s, {}).get('deployment') for s in SERVICES
                   if isinstance(workloads.get(s), dict)]
    need(len(deployments) == 2 and len(set(str(x) for x in deployments)) == 2,
         'DISTINCT_DEPLOYMENTS_REQUIRED')
    return result(sorted(set(errors)))


def result(errors):
    return {'schema': 'P2-CICD-intake-report-1',
            'status': 'INCOMPLETE_OR_INCONSISTENT' if errors else 'DECLARED_INPUTS_CONSISTENT',
            'execution_authorized': False, 'runtime': 'NOT_RUN',
            'external_references_verified': False, 'errors': errors}


def unique_object(pairs):
    data = {}
    for key, value in pairs:
        if key in data:
            raise ValueError('duplicate key')
        data[key] = value
    return data


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', help='local non-secret P2 intake JSON')
    args = parser.parse_args(argv)
    try:
        # Bound memory even for oversized files; never print contents or paths.
        with Path(args.input).open('rb') as handle:
            raw = handle.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ValueError('oversized')
        record = json.loads(raw.decode('utf-8'), object_pairs_hook=unique_object,
                            parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite')))
        report = evaluate(record)
    except (OSError, ValueError, RecursionError):
        report = result(['INPUT_UNREADABLE_OR_INVALID_JSON'])
    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return 2 if report['errors'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
