# #46 기존 관리 SSH 설정 연결

2026-10-24. 기존 경로를 코드에 선택하는 단계다. 실제 접속·helper 설치·발급 위치 채택이나
Runtime GO가 아니다. `delivery_job_site.SITE=None`과 원본/control/finish 인증 계약을 유지한다.

## 연결한 기존값

`delivery_fds_management_route`가 아래 기대값을 기존 `SshBridgeConfig`에 넣는다.
`delivery_fds_operating.build_management_bridge_configuration`은 이 함수의 직접 참조다.

| 기대값 | 원본 근거 / 한계 |
|---|---|
| `ansible01 / server01_ansible` → `10.250.10.20 / server01_cicd-runner` | [10/20 실제 B→A 관리 전송](https://github.com/321Team/infra-ansible/issues/82#issuecomment-6283088910), [Runner 인증 참조](https://github.com/321Team/fds-project-baseline/blob/6219aa4a0246c6d2ce631006e1b022e775ed28d7/governance/handoffs/phase1-execution-20261010/DEPLOY_AFTER_PUSH_INPUTS_20261013.json#L17). 과거 기록이며 현재 연결 성공이나 역방향 허용을 뜻하지 않는다 |
| `/usr/bin/ssh`, `/home/server01_ansible/.ssh/p1_ansible_ed25519`, `/home/server01_ansible/.ssh/known_hosts` | [기존 관리 코드](https://github.com/321Team/infra-ansible/blob/7f04e18371f5d10aec678460f5dcd6e28d98d7d3/tools/ntp-onepass.py). 파일 내용·현재 host key·권한을 새로 확인하지 않았다. CP1의 id_ed25519·임시 ansible-isolated Runner와 구별한다 |
| job `cicd-runner01 / server01_cicd-runner / UID1000` | 기존 P1 workflow의 실제 identity 검사와 PR126의 선택. 기존 운영 상수를 이 모듈로 옮겨 한 곳에서 재사용한다 |
| 이 관리 경로의 helper UID 기대값 `1000` | **같은 일반 SSH 로그인 계정에서 sudo/setns 없이 helper를 실행할 경로를 명시 선택**한 것이다. 기존 계정·UID 근거를 사용한 기대값이며 현재 helper의 UID 관측값은 아니다. 다른 경로의 helper UID 기본값으로 쓰지 않는다 |

host/account/key/known_hosts를 caller가 임의로 덮어쓰는 옵션은 없다. 이 함수가 반환한
Python 객체나 경로 문자열 자체가 인증 경계·원문 권위인 것은 아니며 trusted site 코드는
계속 검토 대상이다. 생성·형식 검사에서는 file/network/process/clock/credential I/O가 없다.

## 명시 입력으로 남긴 값

| 입력 | 이유 |
|---|---|
| `port`, `host_key_alias` | 과거 ssh 명령은 사용자/시스템 설정을 참조할 수 있었다. 새 bridge는 `-F /dev/null`과 strict host-key 검증을 사용하므로 실제 값을 확인해 명시해야 한다. 22나 IP 별칭으로 추정하지 않는다 |
| `remote_python`, `remote_helper` | P2 helper의 검토된 실제 절대 경로. P1 임시 kit 또는 다른 회차 경로를 복사하지 않는다 |
| `remote_timeout_seconds` | 실제 원래 deadline·token 수명·운영 범위 안의 예산. 생성 시 시계를 읽거나 수명을 갱신하지 않는다 |

없는 값은 필수 인자 누락/기존 bridge 형식 검사로 차단한다. placeholder, 환경변수,
새 key 생성·host key 자동 수집·패스워드 fallback·발급 callback을 추가하지 않았다.

선택한 경로로 조립할 때 부모의 `expected_helper_uid`에는 이 경로의
`EXPECTED_HELPER_UID`를 **명시 전달**한다. 기존 일반 `assemble_job`은 여전히 이 인자를
필수로 받으므로 다른 helper를 Runner UID에서 자동 추론하지 않는다. bridge가 검사하는
job listener UID와 부모가 검사하는 helper peer UID는 별도 항목으로 유지한다.

둘 다1000이라는 설정은 현재 identity·같은 netns·같은 job 확인이나 격리 보장이 아니다.
Runtime에서는 기존 native Runner identity, 양쪽 SO_PEERCRED, request/Binding/nonce,
strict SSH trust와 READY·기한 검사를 독립적으로 통과해야 한다. 같은 UID의 다른 프로세스에
대한 강한 격리는 보장하지 않으며 기존 독점 작업창·신뢰한 host/account 경계를 유지한다.
틀린 UID/netns·미확인 READY에는 sudo·namespace 이동·다른 계정 재시도를 하지 않는다.

## issuer·control/finish 연결 경계

관리 SSH 출발지는 **ROSA token issuer 위치가 아니다**. 기존 `hayeoung` 담당 지정·
CloudShell 원본 수집 기록으로 ansible01에 ROSA 자격이 있다고 판단하지 않는다.
실제 issuer 위치와 그 위치에서 이 관리 경로를 사용할 수 있는 방식이 정해져야 한다.
현재 actor/endpoint와 기존 TokenRequest 공급 인가·원본 인증은 그대로 필요하다.

원본 5역할·control·보관 인증·materializer·finish의 기존 `SourceRoleWiring`/authority API를
사용한다. 이 SSH 선택으로 새 `AdoptedGrant`, control pin, source credential, finish 완료를
만들지 않는다. SSH ready나 code merge를 기존 배포/종료 승인의 대용으로 쓰지 않는다.
finish는 실제 실행 후 생성하며 미래 hash·현재값·승인값을 합성하지 않는다.

남은 연결은 issuer 위치·helper 배치/현재 UID/netns·인증된 READY·input/source/store와
실제 original/control/finish 범위다. replicas 인계와 새 baseline 검토는
[PR130 처리안](replicas-ownership-plan.md)을 따른다. SSH 선택만으로 그 충돌이 해소되지 않는다.

## 검증·복구

새 오프라인 시험5건은 실제 FDS 경로 선택과 SYNTHETIC 설치 인자를 기존 bridge/부모에
연결해 무I/O 생성·고정값·누락/다른 경로 거부·양쪽 기대 UID의 명시 연결·상태 불변을
확인한다. 실제 SSH/helper/발급 성공을 뜻하지 않는다. PR130 리뷰의 비차단 후속으로
동일 manager의 `subresource: scale` entry도 전체 provider 입구에서 claim·sender·PATCH
전에 거부되는지 1건 추가했다. 기존 정규화가 scale을 먼저 거부하므로 narrow replicas
조건만의 변형 검출을 주장하지 않는다.

코드 원복은 이 변경 revert이며 서버 변경은 발생하지 않았다. source·credential·SSH
정책·실행 workflow를 수정하지 않았다. DB/Robot 유지·10/26 12:00 KST 최종정리 기한 불변.
