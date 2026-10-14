#!/usr/bin/env python3
"""Offline comparison of one negative Run and its independently collected GETs."""
import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re

SERVICES = ('fds-engine', 'transaction-api')


def need(condition, code):
    if not condition:
        raise ValueError(code)


def timestamp(value):
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    need(parsed.tzinfo is not None and parsed.utcoffset() is not None, 'TIMEZONE_REQUIRED')
    return parsed


def identity(data):
    need(data['host'] == 'k8s-control01' and data['mutation'] is False and
         data['secret_or_env_values_collected'] is False, 'OBSERVATION_SCOPE_INVALID')
    start, finish = timestamp(data['started_at_utc']), timestamp(data['finished_at_utc'])
    need(0 <= (finish-start).total_seconds() <= 60, 'OBSERVATION_INTERVAL_INVALID')
    queries = {q['kind']: q for q in data['queries']}
    need(len(queries) == len(data['queries']) == 4 and
         set(queries) == {'deployments','pods','services','endpointslices'}, 'GET_SET_INVALID')
    for kind, q in queries.items():
        need(type(q['rc']) is int and q['rc'] == 0 and
             start <= timestamp(q['at_utc']) <= finish and
             re.fullmatch('[0-9a-f]{64}', q['raw_stdout_sha256']), 'GET_RECEIPT_INVALID')
        expected = {'deployments':'/apis/apps/v1/namespaces/fds-app/deployments',
                    'pods':'/api/v1/namespaces/fds-app/pods',
                    'services':'/api/v1/namespaces/fds-app/services',
                    'endpointslices':'/apis/discovery.k8s.io/v1/namespaces/fds-app/endpointslices'}
        need(q['argv'] == ['/usr/bin/kubectl','--kubeconfig=/etc/kubernetes/admin.conf',
                           '--request-timeout=10s','get','--raw='+expected[kind]], 'GET_COMMAND_INVALID')
    deployments, pods = {}, {}
    for item in queries['deployments']['items']:
        m, status = item['metadata'], item['status']
        name = m['name']
        need(name in SERVICES and name not in deployments and m['namespace']=='fds-app' and
             bool(m['uid']) and m['deletionTimestamp'] is None, 'DEPLOYMENT_IDENTITY_INVALID')
        need(type(m['generation']) is int and m['generation'] > 0 and item['replicas']==3 and
             status['observedGeneration']==m['generation'] and
             all(status[k]==3 for k in ('readyReplicas','updatedReplicas','availableReplicas')),
             'DEPLOYMENT_NOT_READY')
        need(re.fullmatch('[0-9a-f]{64}', item['spec_sha256']), 'SPEC_HASH_INVALID')
        need(len(item['containers'])==1 and item['containers'][0]['name']==name and
             re.fullmatch('10[.]1[.]93[.]54/fds/'+name+'@sha256:[0-9a-f]{64}',
                          item['containers'][0]['image']), 'DEPLOYMENT_IMAGE_INVALID')
        deployments[name] = {k:item[k] for k in ('spec_sha256','replicas','containers','status')}
        deployments[name]['identity'] = {k:m[k] for k in ('name','namespace','uid','generation')}
    need(set(deployments)==set(SERVICES), 'EXACT_TWO_DEPLOYMENTS_REQUIRED')
    counts = dict.fromkeys(SERVICES, 0)
    for item in queries['pods']['items']:
        m = item['metadata']
        need(bool(m['uid']) and m['uid'] not in pods and m['namespace']=='fds-app' and
             m['deletionTimestamp'] is None and item['phase']=='Running', 'POD_IDENTITY_INVALID')
        need(len(item['containers'])==len(item['statuses'])==1, 'POD_CONTAINER_SET_INVALID')
        c, state = item['containers'][0], item['statuses'][0]
        name = c['name']
        need(name in SERVICES and m['name'].startswith(name+'-') and
             c==deployments[name]['containers'][0] and state['name']==name and
             state['ready'] is True and state['started'] is True and
             state['imageID']==c['image'] and bool(state['containerID']) and
             type(state['restartCount']) is int and state['restartCount']>=0, 'POD_IMAGE_OR_READY_INVALID')
        owners = item['owners']
        need(len(owners)==1 and owners[0]['kind']=='ReplicaSet' and owners[0]['controller'] is True and
             owners[0]['name'].startswith(name+'-') and bool(owners[0]['uid']), 'POD_OWNER_INVALID')
        counts[name] += 1
        pods[m['uid']] = {k:item[k] for k in ('node','owners','phase','containers','statuses')}
        pods[m['uid']]['identity'] = {k:m[k] for k in ('name','namespace','uid')}
    need(all(n==3 for n in counts.values()), 'THREE_PODS_PER_DEPLOYMENT_REQUIRED')
    return {'deployments':deployments,'pods':pods}


def compare(before, after, result, run_id, workflow_sha, case):
    need(re.fullmatch('[1-9][0-9]*', run_id) and re.fullmatch('[0-9a-f]{40}', workflow_sha)
         and case in ('vulnerability','scanner-error'), 'EXPECTED_RUN_IDENTITY_INVALID')
    context = result['context']
    need(context['run_id']==run_id and context['workflow_sha']==workflow_sha and
         type(context['attempt']) is int and context['attempt']==1 and
         result['case']==context['negative_case']==case and
         context['purpose']=='SCAN_NEGATIVE_NOT_DELIVERABLE', 'RUN_IDENTITY_MISMATCH')
    need(result['SCAN_NEGATIVE_WORKFLOW']=='FAIL' and result['NEGATIVE_CASE']=='EXPECTED_FAILURE_OBSERVED' and
         result['PUSH']==result['DEPLOY']=='NOT_EXECUTED', 'EXPECTED_SCANNER_FAILURE_REQUIRED')
    start, finish = timestamp(result['started_at']), timestamp(result['finished_at'])
    need(0 < (finish-start).total_seconds() <= 1800, 'RUN_INTERVAL_INVALID')
    need(0 <= (start-timestamp(before['finished_at_utc'])).total_seconds() <= 300 and
         0 <= (timestamp(after['started_at_utc'])-finish).total_seconds() <= 300,
         'FRESH_BEFORE_AFTER_WINDOW_REQUIRED')
    first, last = identity(before), identity(after)
    need(first==last, 'DEPLOYMENT_OR_POD_DRIFT')
    return {'IMAGE_PRESERVATION':'OBSERVED_UNCHANGED', 'run_id':run_id, 'workflow_sha':workflow_sha,
            'case':case, 'deployments':2, 'pods':6, 'new_runtime':False,
            'limitation':'Offline receipt comparison; verify source hashes, GitHub Run, no-overlap and independent acceptance separately'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('before','after','result'):
        parser.add_argument('--'+name, required=True, type=Path)
    for name in ('run-id','workflow-sha','case'):
        parser.add_argument('--'+name, required=True)
    args = parser.parse_args()
    try:
        raw = {}
        for name in ('before','after','result'):
            path = getattr(args,name)
            need(path.stat().st_size <= 4*1024**2, 'RECEIPT_TOO_LARGE')
            raw[name] = path.read_bytes()
        result = compare(*(json.loads(raw[k]) for k in ('before','after','result')),
                         args.run_id, args.workflow_sha, args.case)
        result['input_sha256'] = {k:hashlib.sha256(v).hexdigest() for k,v in raw.items()}
        print(json.dumps(result, sort_keys=True))
        return 0
    except (ValueError, TypeError, KeyError, OSError, AttributeError):
        print('IMAGE_PRESERVATION=NOT_CONFIRMED')
        return 1


if __name__=='__main__':
    raise SystemExit(main())
