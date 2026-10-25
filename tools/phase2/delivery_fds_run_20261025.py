"""One explicitly adopted normal FDS RUN, using existing native/issuer paths.

Import is inert. Actual credentials/nonce/native run/TokenRequest/finish are not
stored here. The ordinary default delivery_job_site.SITE stays unconfigured;
the reviewed FDS workflow explicitly selects declare_site for this finite RUN.
"""
from dataclasses import replace
import argparse
from datetime import datetime, timezone, timedelta
import getpass
import json
import os
import socket
import subprocess
import sys
import time
import warnings

import delivery_adapter as core
import delivery_fds_observed_baseline as baseline
import delivery_fds_operating as operating
import delivery_fds_operating_policy as policy
import delivery_fds_observed_connection as connection
import delivery_fds_prepared_environment as prepared
import delivery_fds_producer_inputs as producer
import delivery_fds_site_inputs as profile
import delivery_job as job
from delivery_fds_run_inputs import run_input_root
from delivery_adopted_authority import AdoptedGrant
from delivery_crosshost_ingress import PeerIdentity, SourceRoute, _token
from delivery_github_source import BlobPin, CommentPin
from delivery_platform_readback import ExpectedSubject
from delivery_terminal_login import read_login_provider
from delivery_token_materializer import NativePlanSelection
from delivery_token_request import TokenTarget

RUN_ID = 'FDS-P2-DELIVERY-20261026-A3'
OPERATION_SECONDS = 600
HANDOFF_SECONDS = 600
CLEANUP_SECONDS = 60
SOURCE_REPOSITORIES = (baseline.METADATA_PIN.repository,
                       policy.CONTROL_PIN.repository, producer.SUPPLY_PIN.repository)
EXPECTED_PEER = PeerIdentity('cicd-runner01:server01_cicd-runner:1000',
    'ssh-key:' + connection.SSH_ED25519_FINGERPRINT + ':same-account-AF_UNIX')
KST = timezone(timedelta(hours=9))

# Actual owner-recorded scope, not a preparation/baseline comment.
APPROVAL_PIN = CommentPin(
    baseline.METADATA_PIN.repository, 46, 6401264722, 277643063,
    '2026-10-25T18:09:22Z', '73ec00dc2e94e12bc383a699755159286c64340eac35517f2dc984f4903e7132')
ADOPTION_REFERENCE = 'https://github.com/321Team/fds-project-baseline/issues/46#issuecomment-6401264722'
STARTS_AT = datetime.fromisoformat('2026-10-26T03:20:00+09:00').timestamp()
EXPIRES_AT = datetime.fromisoformat('2026-10-26T05:20:00+09:00').timestamp()
PLATFORM_PIN = BlobPin(
    producer.SUPPLY_PIN.repository, 'ae4f3e3501330be9e7fc3949092b08c266db10ba', 'evidence/P2-ROSA-01/infra-rosa-5-bootstrap-20261019/rosa5-run4-7-20261019.tgz',
    'b7ed9d6b15c55001e06bfe217ee014100c7096eb6699cdbd2b11523373e6901e', 'heads/evidence/P2-ROSA-01-infra-rosa-5-bootstrap-20261019', 'ae4f3e3501330be9e7fc3949092b08c266db10ba')
# Public trusted root and numeric endpoint observed with verified TLS, no credential.
GITHUB_ENDPOINT_IP = '20.200.245.245'
GITHUB_CA_SHA256 = '808130157f570b7640069852c88e256738007811a64c3aa9a4c31038347dc19c'
GITHUB_CA_PEM = b'-----BEGIN CERTIFICATE-----\nMIICOjCCAcGgAwIBAgIQQvLM2htpN0RfFf51KBC49DAKBggqhkjOPQQDAzBfMQsw\nCQYDVQQGEwJHQjEYMBYGA1UEChMPU2VjdGlnbyBMaW1pdGVkMTYwNAYDVQQDEy1T\nZWN0aWdvIFB1YmxpYyBTZXJ2ZXIgQXV0aGVudGljYXRpb24gUm9vdCBFNDYwHhcN\nMjEwMzIyMDAwMDAwWhcNNDYwMzIxMjM1OTU5WjBfMQswCQYDVQQGEwJHQjEYMBYG\nA1UEChMPU2VjdGlnbyBMaW1pdGVkMTYwNAYDVQQDEy1TZWN0aWdvIFB1YmxpYyBT\nZXJ2ZXIgQXV0aGVudGljYXRpb24gUm9vdCBFNDYwdjAQBgcqhkjOPQIBBgUrgQQA\nIgNiAAR2+pmpbiDt+dd34wc7qNs9Xzjoq1WmVk/WSOrsfy2qw7LFeeyZYX8QeccC\nWvkEN/U0NSt3zn8gj1KjAIns1aeibVvjS5KToID1AZTc8GgHHs3u/iVStSBDHBv+\n6xnOQ6OjQjBAMB0GA1UdDgQWBBTRItpMWfFLXyY4qp3W7usNw/upYTAOBgNVHQ8B\nAf8EBAMCAYYwDwYDVR0TAQH/BAUwAwEB/zAKBggqhkjOPQQDAwNnADBkAjAn7qRa\nqCG76UeXlImldCBteU/IvZNeWBj7LRoAasm4PdCkT0RHlAFWovgzJQxC36oCMB3q\n4S6ILuH5px0CMk7yn2xVdOOurvulGu7t0vzCAxHrRVxgED1cf5kDW21USAGKcw==\n-----END CERTIFICATE-----\n'


class ActiveRunError(RuntimeError):
    """Fixed public codes; no private CLI output, credentials or traceback text."""


def need(ok):
    if not ok:
        raise ActiveRunError('FDS_ACTIVE_RUN_REJECTED')


def selected_inputs(context, command, *, monotonic=time.monotonic, wall=time.time):
    """Reviewed CODE choices, same Binding on both hosts; clocks only at call."""
    need(type(context) is job.JobContext and command == 'deploy')
    context.check()
    now, mono = wall(), monotonic()
    need(core.finite(now) and core.finite(mono) and STARTS_AT <= now < EXPIRES_AT
         and datetime.fromtimestamp(now, KST).date().isoformat() == '2026-10-26'
         and now + HANDOFF_SECONDS + 900 + CLEANUP_SECONDS < EXPIRES_AT
         and core.digest(GITHUB_CA_PEM) == GITHUB_CA_SHA256)
    config = operating.build_configuration(run_id=RUN_ID,
        platform_receipt_sha256=PLATFORM_PIN.sha256,
        supply_receipt_sha256=producer.SUPPLY_SHA256,
        input_root=run_input_root(context),
        budget_seconds=OPERATION_SECONDS, finalize=True, **prepared.configuration_paths())
    config = replace(config, local_time_checks=True)
    b=config.binding
    def grant(pin, principal):
        return AdoptedGrant(b, pin, policy.CONTROL_PIN, principal,
                            ADOPTION_REFERENCE, STARTS_AT, EXPIRES_AT)
    reviewed, _ = producer.connect_reviewed_producer(config,
        grant=grant(producer.SUPPLY_PIN, 'GWONUK'),
        previous_images=baseline.PREVIOUS_IMAGES)
    selection = NativePlanSelection(
        grant(APPROVAL_PIN, policy.ACTOR), grant(PLATFORM_PIN, profile.SUBJECT),
        reviewed, None,
        ExpectedSubject(profile.SUBJECT, profile.SUBJECT_UID,
            ('system:authenticated','system:serviceaccounts','system:serviceaccounts:ci-cd')),
        TokenTarget('ci-cd','fds-deployer',profile.SUBJECT_UID,profile.ISSUER,profile.AUDIENCE),
        profile.CA_PEM, connection.API_ENDPOINT_IP,
        'https://github.com/321Team/infra-rosa/pull/43',
        producer.RENDER_REFERENCE)
    deadline=mono+HANDOFF_SECONDS
    # Source-use cap covers the bounded handoff AND application/finish phases;
    # it is monotonic local use only, never the server token's expiry.
    source_until=deadline+OPERATION_SECONDS+CLEANUP_SECONDS
    routes=tuple(SourceRoute(r,source_until,GITHUB_CA_PEM,GITHUB_ENDPOINT_IP)
                 for r in SOURCE_REPOSITORIES)
    return operating.build_observed_run_inputs(context, command,
        selection=selection, finish_scope=grant(APPROVAL_PIN,policy.ACTOR),
        baseline_grant=grant(baseline.METADATA_PIN,policy.ACTOR),
        baseline_root=baseline.SNAPSHOT_ROOT,input_files=baseline.INPUT_FILES,
        expected_peer=EXPECTED_PEER,routes=routes,budget_seconds=OPERATION_SECONDS,
        finalize=True,deadline=deadline,monotonic=monotonic,wall=wall,
        local_time_checks=True)


def declare_site():
    """No clock/files/network/credentials here; driver supplies native context."""
    return operating.declare_observed_native_job(select_inputs=selected_inputs,
                                                input_files=baseline.INPUT_FILES)


def build_supplier(context, *, login_provider, source_tokens, operation_deadline,
                   monotonic=time.monotonic, wall=time.time):
    """Existing observed memory supplier on the actual approved issuer host."""
    inputs=selected_inputs(context,'deploy',monotonic=monotonic,wall=wall)
    need(core.finite(operation_deadline))
    deadline=min(inputs['deadline'],operation_deadline)
    need(monotonic()<deadline)
    return operating.build_observed_login_supplier(inputs['config'],
        login_provider=login_provider,repositories=SOURCE_REPOSITORIES,
        source_tokens=source_tokens,context=context,
        workspace=connection.OBSERVED_WORKSPACE,remote_timeout_seconds=120,
        operation_deadline=deadline,cleanup_deadline=deadline+CLEANUP_SECONDS,
        monotonic=monotonic,wall=wall)


def _github(args):
    # Local metadata GETs only. The broader existing CLI credential is never
    # extracted or forwarded as a source credential to the Runner.
    need(args and args[0] == 'api')
    env={k:v for k,v in os.environ.items() if k not in ('GH_DEBUG','GH_FORCE_TTY','GH_PAGER')}
    env['GH_PROMPT_DISABLED']='1'
    p=subprocess.run(['/usr/bin/gh','api','--method','GET',*args[1:]],stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=15,env=env)
    need(p.returncode==0 and len(p.stdout)<=1024*1024)
    return p.stdout


def read_source_token(*, deadline, monotonic=time.monotonic):
    """Explicit hidden input; permission scope must be reviewed at its issuer.

    Shape validation cannot prove server permissions. Never fall back to gh's
    stored OAuth token. Same original input deadline covers source and login.
    Python reference release is not memory zeroization.
    """
    token = None
    try:
        need(sys.stdin is not None and sys.stdin.isatty())
        before = monotonic()
        need(core.finite(deadline) and core.finite(before) and before < deadline)
        with warnings.catch_warnings():
            warnings.simplefilter('error', getpass.GetPassWarning)
            token = getpass.getpass('검토된 GitHub 원본 읽기 전용 자격: ')
        after = monotonic()
        need(core.finite(after) and before <= after < deadline)
        _token(token, source=True)
        return token
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException:
        raise ActiveRunError('FDS_SOURCE_TERMINAL_INPUT_FAILED') from None
    finally:
        token = None


def _actual_context(run_id, revision):
    c=job.JobContext(job.REPOSITORY,'workflow_dispatch','refs/heads/main',revision,run_id,1)
    c.check()
    v=json.loads(_github(['api','--hostname','github.com',
        'repos/321Team/infra-cicd/actions/runs/'+run_id]))
    need(str(v.get('id'))==run_id and v.get('head_sha')==revision
         and v.get('head_branch')=='main' and v.get('event')=='workflow_dispatch'
         and type(v.get('run_attempt')) is int and v['run_attempt']==1
         and v.get('status')=='in_progress'
         and v.get('path')=='.github/workflows/p2-rosa-delivery.yml'
         and v.get('actor',{}).get('id')==277643063
         and v.get('repository',{}).get('id')==1362221024
         and v.get('repository',{}).get('owner',{}).get('id')==315682664)
    created=datetime.fromisoformat(v['created_at'].replace('Z','+00:00')).timestamp()
    now=time.time()
    need(STARTS_AT <= created <= now < EXPIRES_AT
         and now-created+HANDOFF_SECONDS+OPERATION_SECONDS+CLEANUP_SECONDS<7200)
    return c


def issuer_main(argv=None):
    """PRIVATE interactive operator entry; no API call until explicit invocation.

    Outputs fixed status and token-free PRIVATE login recovery metadata. Keep
    this terminal capture private; never copy it to public Actions/PR logs.
    """
    parser=argparse.ArgumentParser(description='FDS 본인 메모리 공급 1회 / 실제 native RUN 필요')
    parser.add_argument('--run-id',required=True)
    parser.add_argument('--workflow-sha',required=True)
    args=parser.parse_args(argv)
    supplier=provider=token=sources=receipt=metadata=None
    result={'status':'STOPPED','error_code':'FDS_ACTIVE_ISSUER_FAILED'}
    rc=2; cancelled=None
    try:
        import pwd
        need(sys.platform=='linux' and socket.gethostname().split('.')[0]=='ansible01'
             and os.geteuid()==1000 and pwd.getpwuid(os.geteuid()).pw_name=='server01_ansible')
        context=_actual_context(args.run_id,args.workflow_sha)
        selected_inputs(context,'deploy') # Time/scope/type checks before secret input.
        login=_github(['api','--hostname','github.com','user','--jq','.login']).decode().strip()
        need(login=='GWONUK')
        deadline=time.monotonic()+HANDOFF_SECONDS
        token=read_source_token(deadline=deadline,monotonic=time.monotonic)
        sources=[{'repository':r.owner+'/'+r.name,'token':token} for r in SOURCE_REPOSITORIES]
        provider=read_login_provider(**connection.login_inputs(),username=policy.ACTOR,
            deadline=deadline,monotonic=time.monotonic)
        supplier=build_supplier(context,login_provider=provider,source_tokens=sources,
                                operation_deadline=deadline)
        provider=None # Supplier owns it only after successful return.
        sources=token=None
        status=supplier.send(include_original=True)
        result={'status':'FORWARDED_NOT_JOB_ACCEPTANCE','supplier':status}
        rc=0
    except BaseException as exc:
        if isinstance(exc,(KeyboardInterrupt,SystemExit)):cancelled=type(exc)
    finally:
        # Each cleanup step is independent, preserving metadata before close.
        if supplier is not None:
            for step in ('status','receipt','metadata','close'):
                try:
                    if step=='status':result['supplier']=supplier.status()
                    elif step=='receipt':receipt=supplier.take_receipt()
                    elif step=='metadata':metadata=supplier.take_login_metadata()
                    elif supplier.close() is not True:rc=2
                except BaseException as exc:
                    rc=2
                    if cancelled is None and isinstance(exc,(KeyboardInterrupt,SystemExit)):
                        cancelled=type(exc)
        if provider is not None:
            try:
                if provider.close() is not True:rc=2
            except BaseException as exc:
                rc=2
                if cancelled is None and isinstance(exc,(KeyboardInterrupt,SystemExit)):
                    cancelled=type(exc)
        supplier=provider=token=sources=receipt=None
    # Private metadata is token-free, intentionally not claimed as revoke proof.
    if metadata is not None:result['private_login_recovery_metadata']=metadata
    metadata=None
    if rc!=0:
        result['status']='STOPPED'
        result.setdefault('error_code','FDS_ACTIVE_ISSUER_CLEANUP_FAILED')
    print(json.dumps(result,sort_keys=True))
    if cancelled is not None:return 130
    return rc


if __name__=='__main__':raise SystemExit(issuer_main())
