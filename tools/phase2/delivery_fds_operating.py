"""Code-selected FDS operating inputs; partial configuration, NOT Runtime GO.

Expected Runner is adopted from the existing workflow and owner review, not a
fresh host observation. Other fixed target/source inputs use the existing FDS
profile. No discovery, credentials, filesystem or callback invocation here.
"""
import delivery_fds_job_site as assembly
import delivery_fds_site_inputs as profile
import delivery_fds_observed_baseline as baseline
import delivery_fds_management_route as management
import delivery_fds_observed_connection as connection
import delivery_job as job
import delivery_job_sources as sources
from dataclasses import replace
from delivery_finish_observation import FOLLOWUPS


EXPECTED_RUNNER = management.EXPECTED_RUNNER
# Assignment only. Never treat this name or historical can-i as authentication
# of a BearerCredential. The existing issuer API still needs the actual actor.
ISSUANCE_OPERATOR = 'gwonuk'
ISSUANCE_ASSIGNMENT = 'https://github.com/321Team/fds-project-baseline/issues/46#issuecomment-6379184704'
ISSUER_HOST = management.SOURCE_HOST
ISSUER_ACCOUNT = management.SOURCE_ACCOUNT
HELPER_ENTRYPOINT = 'tools/phase2/delivery_ssh_bridge_helper.py'
READY_ENTRYPOINT = 'tools/phase2/delivery_ready_pull_helper.py'
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


def observed_login_inputs():
    """Dated OAuth trust plus adopted username; no input or login is performed."""
    return connection.login_inputs() | {'username': ISSUANCE_OPERATOR}


def build_observed_login_supplier(config, *, login_provider, repositories,
        source_tokens, context, workspace, remote_timeout_seconds,
        operation_deadline, cleanup_deadline, monotonic, wall):
    """Use read-only observed transport inputs in the EXISTING supplier builder.

    Current native job workspace/context, credentials and operation budgets must
    still be supplied. No historical workspace default or helper installation.
    Provider ownership/error handling is exactly build_login_supplier's contract.
    The dated endpoint/SSH selection must be rechecked when the environment
    changes; construction does not discover, log in, connect or configure SITE.
    """
    return build_login_supplier(config, login_provider=login_provider,
        repositories=repositories, source_tokens=source_tokens, context=context,
        workspace=workspace, remote_timeout_seconds=remote_timeout_seconds,
        operation_deadline=operation_deadline, cleanup_deadline=cleanup_deadline,
        monotonic=monotonic, wall=wall, **connection.supplier_inputs())


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


def build_login_actor_channel(config, *, login_provider, endpoint_ip,
                             operation_deadline, cleanup_deadline, monotonic, wall):
    """Deferred login using the adopted username; OAuth origin/trust stay explicit.

    The API SA issuer is NOT an OAuth login origin. No endpoint/credential default,
    construction I/O, global SITE assignment or generated original/control input.
    """
    from delivery_login_channel import LoginActorChannel
    from delivery_token_request import TokenTarget
    binding = profile._binding_checked(config)
    _, _, namespace, name = profile.SUBJECT.split(':')
    return LoginActorChannel(binding, login_provider=login_provider,
        expected_username=ISSUANCE_OPERATOR,
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


def build_ready_pull_configuration(**inputs):
    """Same explicit management trust and checkout, public-only helper."""
    bridge = build_job_bridge_configuration(**inputs)
    return replace(bridge, remote_helper=bridge.remote_helper.removesuffix(
        HELPER_ENTRYPOINT) + READY_ENTRYPOINT)


def build_login_supplier(config, *, login_provider, endpoint_ip, repositories,
        source_tokens, context, workspace, port, host_key_alias, remote_python,
        remote_timeout_seconds, operation_deadline, cleanup_deadline, monotonic, wall):
    """Assemble the selected issuer side without starting it or configuring SITE.

    Use the SAME explicit native context, Binding and ordered RepositoryPins as
    the job-side routes. Repository identity is shape-checked here, authenticated
    by the existing job source readers later. No original/control is generated.
    Caller owns login_provider until successful return; failure never consumes
    or closes it. On success the single-use supplier owns it; source token refs
    are copied by the existing supplier. Constructors allocate no live channel,
    invoke no clock/callback and do not log in. No fallback for missing inputs.
    """
    from delivery_actor_channel import public_error
    from delivery_github_source import RepositoryPin, repository_checked
    from delivery_ready_pull import SshReadyPull, selector_for
    from delivery_ssh_bridge import SshCredentialBridge
    from delivery_operating_supplier import MemoryOperatingSupplier
    error = None
    try:
        binding = profile._binding_checked(config)
        if type(repositories) is not tuple or not repositories:
            raise ValueError()
        for repository in repositories:
            if type(repository) is not RepositoryPin:
                raise ValueError()
            repository_checked(repository)
        selected = selector_for(context, binding,
            [r.owner + '/' + r.name for r in repositories])
        bridge_config = build_job_bridge_configuration(workspace=workspace,
            context=context, port=port, host_key_alias=host_key_alias,
            remote_python=remote_python, remote_timeout_seconds=remote_timeout_seconds)
        ready_config = replace(bridge_config, remote_helper=
            bridge_config.remote_helper.removesuffix(HELPER_ENTRYPOINT) + READY_ENTRYPOINT)
        pull = SshReadyPull(ready_config, selected,
            deadline=operation_deadline, monotonic=monotonic)
        bridge = SshCredentialBridge(bridge_config,
            deadline=operation_deadline, monotonic=monotonic)
        channel = build_login_actor_channel(config, login_provider=login_provider,
            endpoint_ip=endpoint_ip, operation_deadline=operation_deadline,
            cleanup_deadline=cleanup_deadline, monotonic=monotonic, wall=wall)
        return MemoryOperatingSupplier(ready_pull=pull, bridge=bridge,
            actor_channel=channel, source_tokens=source_tokens)
    except BaseException as exc:
        error = public_error(exc, 'P2_FDS_LOGIN_SUPPLIER_INVALID')
    # No close: all constructors are inert and provider is still caller-owned.
    # Raise outside except so even __context__ cannot retain raw exception text.
    raise error from None


def assemble_observed_job(*, config, finish_slot, expected_peer, routes, role_routes,
        finish_scope, wall, deadline, monotonic, publish_ready=None, ready_context=None, plan=None,
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
        if ready_context is not None:
            if publish_ready is not None:
                raise ValueError()
            from delivery_ready_pull import ReadyMailbox, selector_for
            selected = selector_for(ready_context, config.binding,
                [r.repository.owner + '/' + r.repository.name for r in routes])
            publish_ready = ReadyMailbox(selected, expected_uid=management.EXPECTED_HELPER_UID,
                deadline=deadline, monotonic=monotonic)
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


def reported_actor_grant_status():
    """Owner-reported RBAC metadata, never a live check, Runtime grant or clock.

    This period comes from the actual #5 RoleBinding expiry annotation, NOT the
    resource-retention deadline. The source explicitly excludes #46 test GO.
    No consumer may turn this diagnostic into adopted approval/control policy.
    """
    return {
        'status': 'OWNER_REPORTED', 'subject': 'gwonuk',
        'assignment_matches_report': ISSUANCE_OPERATOR == 'gwonuk',
        'role_binding': 'gwonuk-p2-admin-temp', 'reported_role': 'admin',
        'reported_bindings': [
            {'namespace': 'fds-app', 'uid': 'c32620e3-8407-4871-a69b-3240553af1ee'},
            {'namespace': 'ci-cd', 'uid': 'cc39d661-b8f1-4132-9cbc-49d99db96939'},
        ],
        'declared_expires_at': '2026-10-26T12:00:00+09:00',
        'grant_starts_at': None, 'live_rbac_checked': False,
        'automatic_expiry_verified': False, 'runtime_go': False,
        'source': 'https://github.com/321Team/infra-rosa/issues/5#issuecomment-6379184539',
        'source_creator_id': 95111439,
        'source_updated_at': '2026-10-24T03:14:59Z',
        'source_body_sha256': '0979aef9d7b3dc36c28023edf37959e656aabde7b1cc0bd2b48a3e6765aa9ca6',
    }


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
            'reported_actor_permission': reported_actor_grant_status(),
            'actor_channel': {'adapter': 'NAMED_OAUTH_TOKENREQUEST_SSH',
                'constructed_for_runtime': False, 'identity_verified': False,
                'server_revocation_verified': False, 'runtime_authorized': False},
            'memory_ready_connection': {'supplier': 'MemoryOperatingSupplier',
                'assembly': 'build_login_supplier',
                'ready': 'SshReadyPull/ReadyMailbox', 'helper': READY_ENTRYPOINT,
                'login_provider_implementation': 'BasicChallengeLogin/LoginActorChannel',
                'login_provider_supplied': False, 'runtime_connected': False},
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
            'observed_readonly_connection': connection.selection_status(),
        },
        'pending_before_run': [
            {'id': 'TRANSPORT_AND_LAYOUT', 'owner': '이권욱',
             'support': '기존 회신과 10/24 읽기 OAuth/TLS·관리SSH 원본 대조. 실제 로그인·job 배치는 별개',
             'items': ['중앙 연결: 확인한 OAuth 입력과 본인 메모리 password, repository 자격 연결',
                       '중앙 연결: 입력·source/store 및 두 호스트 caller의 실제 배치',
                       '실행 준비: 실제 job의 exact checkout/helper·UID/netns와 확인 시점 이후 transport 변경 대조',
                       '실행 준비: 본인 actor 인증·API 도달·회수 adapter의 실응답 및 만료/수동 회수 인수']},
            {'id': 'ORIGINAL_CONTROL_AND_FINISH', 'owner': '이권욱',
             'support': '하영·재환: 이미 제출한 담당 영역 원본 재사용',
             'items': ['원본 5역할과 control pin·보관 인증·materializer 결속',
                       '실제 자동 실행 RUN/action/Binding·허가 창·control 근거 선택 (RBAC 회수기한과 분리)',
                       '사전 플랫폼 receipt와 사후 발급 receipt 구분',
                       '선택한 baseline의 실제 grant/control 및 fresh drift 검증',
                       '채택한 replicas0/3 인계의 실제 완료와 새 full baseline',
                       '선택한 post-close 관측 범위의 실제 scope/control 연결·별도 Runtime GO']},
        ],
        'produced_during_run': ['native job run/attempt', 'READY nonce',
            '실제 발급 응답·fingerprint·iat/exp', '실제 finish 관측 원본'],
    }
