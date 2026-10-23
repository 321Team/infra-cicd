"""Code-selected FDS operating inputs; partial configuration, NOT Runtime GO.

Expected Runner is adopted from the existing workflow and owner review, not a
fresh host observation. Other fixed target/source inputs use the existing FDS
profile. No discovery, credentials, filesystem or callback invocation here.
"""
import delivery_fds_job_site as assembly
import delivery_fds_site_inputs as profile
import delivery_fds_observed_baseline as baseline
import delivery_fds_management_route as management


EXPECTED_RUNNER = management.EXPECTED_RUNNER
# Assignment only. Never treat this name or historical can-i as authentication
# of a BearerCredential. The existing issuer API still needs the actual actor.
ISSUANCE_OPERATOR = 'hayeoung'
ISSUANCE_ASSIGNMENT = 'https://github.com/321Team/fds-project-baseline/issues/46#issuecomment-6339313808'

# Reuse the fixed, reviewed source/target profile directly; no copied hash table,
# alternate input parser or implicit current-object -> previous-image fallback.
build_configuration = profile.build_configuration
build_token_issuer = profile.build_token_issuer
connect_baseline = baseline.connect_selected_baseline
build_management_bridge_configuration = management.build_configuration


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
            'baseline_expectation': baseline.selection_status(),
            'management_route_expectation': management.selection_status(),
        },
        'pending_before_run': [
            {'id': 'TRANSPORT_AND_LAYOUT', 'owner': '이권욱',
             'support': '재환: 기존 Runner/SSH 배치 사실, 하영: 발급측 연결 사실',
             'items': ['issuer 실행 위치·기존 관리 SSH와의 결합',
                       'SSH port/host-key 별칭·remote Python/helper 경로·현재 UID/netns',
                       '인증된 READY 전달·양 호스트 deadline 연결',
                       'input/source/persistent store 위치·현재 endpoint']},
            {'id': 'ORIGINAL_CONTROL_AND_FINISH', 'owner': '이권욱',
             'support': '하영·재환: 이미 제출한 담당 영역 원본 재사용',
             'items': ['원본 5역할과 control pin·보관 인증·materializer 결속',
                       '사전 플랫폼 receipt와 사후 발급 receipt 구분',
                       '선택한 baseline의 실제 grant/control 및 fresh drift 검증',
                       'replicas0/3 SSA 충돌 처리와 실행·finish 범위 채택']},
        ],
        'produced_during_run': ['native job run/attempt', 'READY nonce',
            '실제 발급 응답·fingerprint·iat/exp', '실제 finish 관측 원본'],
    }
