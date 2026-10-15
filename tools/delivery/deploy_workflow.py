"""Main-only manual Deploy dispatch, with a separate authenticated SSH broker."""
import json
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import tempfile

import deploy_approval as approval
import deploy_binding as b
import deploy_cp1_access as access
import deploy_handoff as handoff
import deploy_native_probe as native
import deploy_plan
import deploy_session
from transfer_native_probe import CA_SHA, RUNNER_CA

WORKFLOW = '321Team/infra-cicd/.github/workflows/app-deploy.yml@refs/heads/main'


def context(env):
    b.need(env.get('GITHUB_EVENT_NAME') == 'workflow_dispatch'
           and env.get('GITHUB_WORKFLOW_REF') == WORKFLOW
           and env.get('GITHUB_WORKFLOW_SHA') == env.get('GITHUB_SHA'), 'DEPLOY_WORKFLOW_REQUIRED')
    for key in ('GITHUB_RUN_ID', 'GITHUB_RUN_ATTEMPT'):
        b.need(re.fullmatch(r'[1-9][0-9]{0,19}', env.get(key, '')), 'DEPLOY_RUN_INVALID')
    value = b.identity({'repository': env.get('GITHUB_REPOSITORY'),
                        'ref': env.get('GITHUB_REF'), 'workflow_sha': env.get('GITHUB_SHA'),
                        'run_id': int(env['GITHUB_RUN_ID']), 'attempt': int(env['GITHUB_RUN_ATTEMPT'])})
    b.need(value['attempt'] == 1, 'DEPLOY_RETRY_NOT_ALLOWED')
    head = subprocess.check_output(['/usr/bin/git', 'rev-parse', 'HEAD'], timeout=10,
                                   stderr=subprocess.DEVNULL).decode().strip()
    dirty = subprocess.check_output(['/usr/bin/git', 'status', '--porcelain', '--untracked-files=all'],
                                    timeout=10, stderr=subprocess.DEVNULL)
    b.need(head == value['workflow_sha'] and not dirty.strip(), 'DEPLOY_CHECKOUT_CHANGED')
    return value


def inputs(home, env, execution):
    name, digest = env.get('DEPLOY_RECORD_NAME', ''), env.get('DEPLOY_RECORD_SHA256', '')
    b.need(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}\.json', name)
           and re.fullmatch(r'[0-9a-f]{64}', digest), 'DEPLOY_RECORD_INVALID')
    root = home / 'FDS_CICD_DeployInputs'
    handoff.private_root(root)
    path = root / name
    info = access.path_checked(path)
    b.need(info.st_uid == os.geteuid() and stat.S_IMODE(info.st_mode) == 0o600,
           'DEPLOY_RECORD_PERMISSIONS')
    raw = access.read_checked(path)
    b.need(b.sha(raw) == digest, 'DEPLOY_RECORD_CHANGED')
    record = b.read(raw)
    b.need(set(record) == {'schema', 'plan', 'approval', 'push_receipt', 'sa_uid'}
           and record['schema'] == 'P1-CICD-deploy-inputs-1'
           and type(record['sa_uid']) is str and native.c.UID.fullmatch(record['sa_uid']),
           'DEPLOY_RECORD_FIELDS_INVALID')
    for key in ('plan', 'approval', 'push_receipt'):
        b.need(type(record[key]) is str, 'DEPLOY_RECORD_BYTES_REQUIRED')
    plan_raw, approval_raw, push_raw = (record[k].encode('utf-8')
                                      for k in ('plan', 'approval', 'push_receipt'))
    args = dict(plan_raw=plan_raw, approval_raw=approval_raw, push_raw=push_raw,
                expected_plan_sha256=b.sha(plan_raw), expected_approval_sha256=b.sha(approval_raw),
                execution=execution)
    binding = deploy_plan.validate(**args, now=native.utcnow())
    # The repository-scoped Workflow token need not read another private repo.
    b.need(binding['approval_reference'].startswith(
        'https://github.com/321Team/infra-cicd/issues/11#issuecomment-'), 'LOCAL_APPROVAL_REQUIRED')
    return args, binding, record['sa_uid']


def save(folder, name, value):
    raw = (json.dumps(value, sort_keys=True, allow_nan=False, indent=2) + '\n').encode()
    fd = os.open(folder / name, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    fd = os.open(folder, os.O_DIRECTORY | os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def run(env, home, github_token):
    access.host_checked('runner')
    handoff.private_root(home)
    execution = context(env)
    b.need(env.get('DEPLOY_EXECUTE') in ('true', 'false'), 'DEPLOY_MODE_INVALID')
    args, binding, uid = inputs(home, env, execution)
    ca_raw, _ = access.ca_checked(RUNNER_CA, CA_SHA)
    scope = {'execution': execution, 'record_sha256': env['DEPLOY_RECORD_SHA256'],
             'plan_sha256': args['expected_plan_sha256'], 'sa_uid': uid}
    folder = Path(tempfile.mkdtemp(prefix='FDS_CICD_Deploy.', dir=home))
    print('EVIDENCE_DIR=' + str(folder), flush=True)
    save(folder, 'dispatch.json', {'binding': scope, 'mode': env['DEPLOY_EXECUTE'],
                                  'operation': binding['operation'],
                                  'recovery_basis': binding['recovery_basis'],
                                  'SOURCE_APPROVAL': 'NOT_INDEPENDENTLY_VERIFIED'})
    if env['DEPLOY_EXECUTE'] == 'false':
        save(folder, 'result.json', {'PREFLIGHT': 'SUPPLIED_BYTES_MATCH', 'TOKEN_ISSUED': False,
                                    'operation': binding['operation'],
                                    'recovery_basis': binding['recovery_basis'],
                                    'APPROVAL_REFERENCE': 'NOT_VERIFIED', 'DEPLOY': 'NOT_EXECUTED',
                                    'DELIVERY_GATE': 'NOT_VERIFIED'})
        return 0
    candidate = result = receiver = None
    try:
        b.need(type(github_token) is str and github_token, 'GITHUB_CREDENTIAL_REQUIRED')
        window = native.Window(binding['expires_at'].isoformat())
        verifier = approval.Verifier(approval.GitHubComments(lambda: github_token))
        verifier.verify(args['approval_raw'], args['expected_approval_sha256'], min(10, window.remaining()))
        for name in ('FDS_DeploySockets', 'FDS_CICD_DeployClaims'):
            (home / name).mkdir(mode=0o700, exist_ok=True)
            handoff.private_root(home / name)
        receiver = handoff.Receiver(home, scope, binding['expires_at'].isoformat(), window.remaining)
        receiver.open()
        candidate = receiver.candidate(uid)
        session = deploy_session.Session(home / 'FDS_CICD_DeployClaims', lambda: github_token, receiver)
        result = session.run(**args, token=candidate['token'], expected_sa_uid=uid,
                             token_request_expires_at=candidate['status_expires_at'],
                             ca_raw=ca_raw, expected_ca_sha256=CA_SHA)
    except (Exception, KeyboardInterrupt):
        result = {'status': 'MANUAL_RECONCILIATION_REQUIRED', 'error_code': 'DEPLOY_HANDOFF_UNCONFIRMED',
                  'DEPLOY': 'NOT_CONFIRMED', 'DELIVERY_GATE': 'NOT_VERIFIED'}
    finally:
        candidate = None
        if result is None:
            result = {'status': 'MANUAL_RECONCILIATION_REQUIRED', 'DELIVERY_GATE': 'NOT_VERIFIED'}
        try:
            if receiver is not None:
                receiver.close()
            result['handoff_socket_cleanup'] = 'OWNED_SOCKET_CLOSED_OR_NOT_CREATED'
        except Exception:
            result['handoff_socket_cleanup'] = 'NOT_CONFIRMED'
            result['status'] = 'MANUAL_RECONCILIATION_REQUIRED'
        result['workflow_input_reference_cleanup'] = 'LOCAL_REFERENCE_DROPPED'
        result['operation'] = binding['operation']
        result['recovery_basis'] = binding['recovery_basis']
        save(folder, 'result.json', result)
    receipt = result.get('execution_receipt') or {}
    return 0 if (result.get('status') == 'EXECUTION_RETURNED'
                 and receipt.get('ROLLOUT') == 'READY' and receipt.get('error_code') == 'NONE') else 1


def main():
    os.umask(0o077)
    github_token = os.environ.pop('DEPLOY_GITHUB_TOKEN', None)
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    try:
        return run(dict(os.environ), Path.home(), github_token)
    except (Exception, KeyboardInterrupt):
        print('DEPLOY=NOT_CONFIRMED\nERROR_CODE=DEPLOY_ENTRYPOINT_REJECTED')
        return 1
    finally:
        github_token = None


if __name__ == '__main__':
    raise SystemExit(main())
