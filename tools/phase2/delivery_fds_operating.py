"""Code-selected FDS operating inputs; partial configuration, NOT Runtime GO.

Expected Runner is adopted from the existing workflow and owner review, not a
fresh host observation. Other fixed target/source inputs use the existing FDS
profile. No discovery, credentials, filesystem or callback invocation here.
"""
import delivery_fds_job_site as assembly
import delivery_fds_site_inputs as profile
import delivery_fds_observed_baseline as baseline
import delivery_fds_management_route as management
import delivery_job as job
import delivery_job_sources as sources
from delivery_finish_observation import FOLLOWUPS


EXPECTED_RUNNER = management.EXPECTED_RUNNER
# Assignment only. Never treat this name or historical can-i as authentication
# of a BearerCredential. The existing issuer API still needs the actual actor.
ISSUANCE_OPERATOR = 'gwonuk'
ISSUANCE_ASSIGNMENT = 'https://github.com/321Team/fds-project-baseline/issues/46#issuecomment-6379184704'
ISSUER_HOST = management.SOURCE_HOST
ISSUER_ACCOUNT = management.SOURCE_ACCOUNT
HELPER_ENTRYPOINT = 'tools/phase2/delivery_ssh_bridge_helper.py'
# Historical references for handoff/status ONLY. Neither is a default argument
# or a current host observation; the trusted caller still supplies native inputs.
RECORDED_WORKSPACE = '/home/server01_cicd-runner/actions-runner/_work/infra-cicd/infra-cicd'
RECORDED_REMOTE_PYTHON = '/usr/bin/python3'

# Reuse the fixed, reviewed source/target profile directly; no copied hash table,
# alternate input parser or implicit current-object -> previous-image fallback.
build_configuration = profile.build_configuration
build_token_issuer = profile.build_token_issuer
connect_baseline = baseline.connect_selected_baseline
build_management_bridge_configuration = management.build_configuration


def build_actor_channel(config, *, actor, endpoint_ip, operation_deadline,
                        cleanup_deadline, monotonic, wall):
    """Connect adopted personal actor and SA profile to an inert memory channel.

    Credentials, current numeric endpoint and both deadlines remain explicit.
    This never creates credentials, connects a host or configures global SITE.
    """
    from delivery_actor_channel import ActorCredentialChannel
    from delivery_token_request import TokenTarget
    binding = profile._binding_checked(config)
    _, _, namespace, name = profile.SUBJECT.split(':')
    return ActorCredentialChannel(binding, actor=actor, expected_username=ISSUANCE_OPERATOR,
        target=TokenTarget(namespace, name, profile.SUBJECT_UID, profile.ISSUER, profile.AUDIENCE),
        ca_pem=profile.CA_PEM, endpoint_ip=endpoint_ip,
        operation_deadline=operation_deadline, cleanup_deadline=cleanup_deadline,
        monotonic=monotonic, wall=wall)


def build_job_bridge_configuration(*, workspace, context, port, host_key_alias,
                                   remote_python, remote_timeout_seconds):
    """Select the existing native job's exact-checkout helper, without I/O.

    workspace is an explicit actual GITHUB_WORKSPACE supplied by trusted site
    code, NOT a guessed root or an environment read here. Native context and
    lexical path validation do not prove revision, installation, ownership,
    current trust or an authenticated READY. Those existing checks remain.
    No path normalization, discovery, helper override or layout fallback.
    """
    try:
        if (type(context) is not job.JobContext or type(workspace) is not str
                or not workspace.startswith('/')):
            raise ValueError()
        context.check()
        helper = (workspace + '/p2-job-' + context.run_id + '-' + str(context.attempt)
                  + '/' + HELPER_ENTRYPOINT)
        return management.build_configuration(port=port, host_key_alias=host_key_alias,
            remote_python=remote_python, remote_helper=helper,
            remote_timeout_seconds=remote_timeout_seconds)
    except Exception:
        raise assembly.SiteAssemblyError('P2_FDS_JOB_HELPER_CONFIGURATION_INVALID') from None


def assemble_observed_job(*, config, finish_slot, expected_peer, routes, role_routes,
        finish_scope, wall, deadline, monotonic, publish_ready, plan=None,
        materialize_plan=None, producer_repository=None, producer_pins=None):
    """Adopt the same-account helper and post-close observation finish policy.

    Actual scope/control and five repository roles stay explicit, not copied
    from deploy approval or created from status. No alternate finish capture or
    caller wiring can replace the selected two followups. Construction invokes
    no callback. Existing assembly retains plan validation/ownership and native
    identity, receipt, original/control, sender-close, claim/fence checks.
    """
    try:
        wire = sources.SourceRoleWiring(binding=config.binding, routes=role_routes,
            finish=sources.ObservationRoute(finish_scope, FOLLOWUPS),
            producer_repository=producer_repository, wall=wall, monotonic=monotonic)
    except Exception:
        raise assembly.SiteAssemblyError('P2_FDS_SITE_CONFIGURATION_INVALID') from None
    return assembly.assemble_existing_job_site(runner=EXPECTED_RUNNER,
        config=config, finish_slot=finish_slot, expected_peer=expected_peer,
        routes=routes, wire=wire, expected_helper_uid=management.EXPECTED_HELPER_UID,
        deadline=deadline, monotonic=monotonic, publish_ready=publish_ready,
        plan=plan, materialize_plan=materialize_plan, producer_pins=producer_pins)


def assemble_job(*, config, finish_slot, expected_peer, routes, wire,
        expected_helper_uid, deadline, monotonic, publish_ready, plan=None,
        materialize_plan=None, finish_capture=None, producer_pins=None):
    """Connect the adopted expected Runner to EXISTING-six-object assembly.

    Required unresolved inputs stay explicit. In particular the SSH helper UID
    is NOT inferred from the adopted Runner UID. No runner override, full-plan
    synthesis, credential loader, global SITE assignment or callback execution.
    The native driver compares this expected identity before calling the build.
    """
    return assembly.assemble_existing_job_site(runner=EXPECTED_RUNNER,
        config=config, finish_slot=finish_slot, expected_peer=expected_peer,
        routes=routes, wire=wire, expected_helper_uid=expected_helper_uid,
        deadline=deadline, monotonic=monotonic, publish_ready=publish_ready,
        plan=plan, materialize_plan=materialize_plan, finish_capture=finish_capture,
        producer_pins=producer_pins)


def configuration_status():
    """Fresh non-secret report of CODE selection only; never readiness/authority.

    Pending groups are handoff information, not another runtime validator. No
    user file/environment/status flag can enable the native workflow gate.
    """
    return {
        'schema': 'P2-FDS-operating-selection-1',
        'selection': 'PARTIAL', 'runtime_site': 'UNCONFIGURED',
        'execution_authorized': False, 'runtime': 'NOT_RUN',
        'selected': {
            'runner_expectation': {'hostname': EXPECTED_RUNNER.hostname,
                'account': EXPECTED_RUNNER.account, 'uid': EXPECTED_RUNNER.uid,
                'current_host_observed': False},
            'target_source': {'profile': 'delivery_fds_site_inputs',
                'mode': 'EXISTING_SIX_OBJECTS', 'source_commit': profile.SOURCE_COMMIT,
                'rendered_sha256': profile.RENDERED_SHA256},
            'issuance_assignment': {'operator': ISSUANCE_OPERATOR,
                'reference': ISSUANCE_ASSIGNMENT, 'credential_authenticated': False},
            'actor_channel': {'adapter': 'NAMED_OAUTH_TOKENREQUEST_SSH',
                'constructed_for_runtime': False, 'identity_verified': False,
                'server_revocation_verified': False, 'runtime_authorized': False},
            'issuer_location_expectation': {'hostname': ISSUER_HOST,
                'account': ISSUER_ACCOUNT, 'mode': 'DIRECT_ISSUE_AND_MANAGEMENT_SSH',
                'current_location_verified': False, 'api_reachability_verified': False},
            'helper_layout': {'mode': 'NATIVE_JOB_EXACT_CHECKOUT',
                'checkout': 'p2-job-{run_id}-{attempt}', 'entrypoint': HELPER_ENTRYPOINT,
                'workspace_selected': False, 'deployment_verified': False,
                'historical_workspace': RECORDED_WORKSPACE,
                'recorded_python': RECORDED_REMOTE_PYTHON,
                'current_paths_verified': False,
                'helper_uid_expectation': management.EXPECTED_HELPER_UID},
            'finish_policy': {'mode': 'POST_CLOSE_OBSERVATION', 'followups': list(FOLLOWUPS),
                'scope_control_connected': False, 'observation_produced': False,
                'includes_resource_cleanup': False},
            'baseline_expectation': baseline.selection_status(),
            'management_route_expectation': management.selection_status(),
        },
        'pending_before_run': [
            {'id': 'TRANSPORT_AND_LAYOUT', 'owner': '이권욱',
             'support': '재환: SSH port/별칭·기존 경로/실행 격리 변경 기록, 하영: 기존 메모리 자격 공급·OAuth 수명/회수 기록',
             'items': ['중앙 구현: 본인 전용 actor·repository 자격의 메모리 공급과 실제 caller 연결',
                       '중앙 연결: 인증된 READY 왕복·양 호스트 deadline·P2 input/source/store',
                       '실행 준비: 기존 workspace/Python의 현재 대조, 명시 SSH port/별칭·UID/netns·endpoint',
                       '실행 준비: 본인 actor 인증·API 도달·회수 adapter의 실응답 및 만료/수동 회수 인수']},
            {'id': 'ORIGINAL_CONTROL_AND_FINISH', 'owner': '이권욱',
             'support': '하영·재환: 이미 제출한 담당 영역 원본 재사용',
             'items': ['원본 5역할과 control pin·보관 인증·materializer 결속',
                       '사전 플랫폼 receipt와 사후 발급 receipt 구분',
                       '선택한 baseline의 실제 grant/control 및 fresh drift 검증',
                       '채택한 replicas0/3 인계의 실제 완료와 새 full baseline',
                       '선택한 post-close 관측 범위의 실제 scope/control 연결·별도 Runtime GO']},
        ],
        'produced_during_run': ['native job run/attempt', 'READY nonce',
            '실제 발급 응답·fingerprint·iat/exp', '실제 finish 관측 원본'],
    }
