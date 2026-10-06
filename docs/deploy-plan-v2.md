# Deploy v2: 실행 전 승인과 일회성 claim

2026-10-06 KST / AI usage: CODEX_ASSISTED.
기준: PR53 독립 재리뷰의 Run ID 순서 지적과 PR54 토큰 수명 검사.
기존 v1 검증의 Run ID 조건은 그대로 유지한다.

## 결정과 입력 순서

Workflow dispatch 이전에는 새 Run ID를 알 수 없다. v2에서는 계획 원본과
분리된 승인 원본을 사용한다. 승인 기록 안에 Run ID를 미리 채우거나 Build Run으로
Deploy 실행을 대신하지 않는다.

1. 검토·병합된 main Workflow SHA와 정확한 계획 원본 bytes를 확정한다.
2. 계획 파일 SHA-256, Workflow SHA, 기간·복구 범위에 대한 승인 원본을 별도로 확보한다.
   승인 파일 자체의 외부 확정 SHA-256도 확보한다.
3. main이 같은 SHA인지 확인하여 dispatch한다. 실제 신뢰된 Workflow context에서
   repository/ref/workflow_sha/run_id/attempt를 확보한다.
4. 계획·승인·Push 원본 및 각각의 외부 해시를 검증하고, 실제 승인 참조를 독립 확인한다.
5. execute=true 경로에서만 고정 상태 디렉터리의 전역 mutex와 해당 계획의 claim을 확보한다.
   claim bytes와 실제 Run/Attempt를 fsync한 뒤에만 자격증명 발급·주입 단계로 간다.
6. API 호출마다 승인·토큰 수명과 lock/claim 소유권을 새로 확인한다.
   실제 실행 영수증에는 계획·승인·Push·claim 해시 및 실제 context를 결속한다.

위 3~6의 Workflow/API 연결은 이번 PR에 구현하지 않는다.
라이브러리 검증과 파일 claim은 준비됐으나 Runtime enforcement는 NOT_INTEGRATED다.

## 순환 해시가 없는 두 원본

deploy_plan.validate(plan_raw, approval_raw, push_raw,
expected_plan_sha256, expected_approval_sha256, execution, now)

| 원본 | 필드 |
|---|---|
| 계획 | schema=P1-CICD-deploy-plan-2, template_only=false, request_id, workflow, delivery, push_execution, push_receipt_sha256, kubernetes, targets |
| 분리 승인 | schema=P1-CICD-deploy-approval-2, template_only=false, plan_sha256, workflow, reference, approved_by, not_before, expires_at, allow_rollback |
| 실제 context | repository=321Team/infra-cicd, ref=refs/heads/main, workflow_sha, 양의 정수 run_id/attempt |

workflow는 repository/ref/workflow_sha만 포함한다. 계획에는 approval·execution·자기 해시가
없고, 승인에는 계획 bytes 해시가 있으므로 자기 해시를 계산하는 순환이 없다.
plan_sha256을 입력 안에서 읽어 외부 expected_plan_sha256으로 대신하지 않는다.
승인 bytes의 expected_approval_sha256도 승인 파일 자체에서 추출하지 않는다.

request_id는 새 실행 의도의 소문자 UUID 형식 식별자다. 이전 실행 실패나 불명확 결과가
있으면 먼저 수동 대조하고, 갱신된 현재 상태를 반영한 새 계획과 새 승인을 사용한다.
UUID를 바꾸는 것만으로 이전 승인이나 미검증 서버 상태를 재사용하지 않는다.

Build/source/scan 원본 identity, Push bytes/실행 context/두 이미지 digest,
정확한 두 Deployment UID·이전 이미지, API/SA/audience/TTL 정책은 v1과 같은 검사를
공유한다. 승인된 계획 해시가 모든 위 필드를 결속한다. 추가 대상·replica 변경·Kubernetes
PASS 선언 필드는 허용하지 않는다. 기존 pre_deploy 승인을 합성하지 않는다.

v2는 attempt=1만 허용한다. 새 Run ID는 dispatch 후 기록할 수 있으나 Build/Push Run과
달라야 한다. Run ID가 다르다는 사실만으로 동일 계획을 다시 실행할 수는 없다.
같은 계획을 다른 첫 Run으로 dispatch해도 지속 claim이 소비된 계획을 거부한다.
Workflow SHA가 변경되면 기존 승인 재사용은 불가하다.

MATCH는 공급된 bytes와 선언된 승인 필드의 정합이다.
approval_verification=NOT_INDEPENDENTLY_VERIFIED / claim=NOT_ACQUIRED /
delivery_gate=NOT_VERIFIED를 반환한다. URL 존재나 승인 문자열만으로 실제 승인자 권한,
독립 리뷰, 철회 여부를 확인했다고 보고하지 않는다. remaining_seconds는 호출 시점 값이며,
Adapter의 시간 콜백에 고정 숫자로 재사용하지 않는다.

## 전역 mutex와 계획별 지속 claim

deploy_claim.acquire(root, plan_sha256, approval_sha256, execution)

이 API는 POSIX flock/dir_fd/O_NOFOLLOW/fsync를 사용하는 파일 라이브러리다.
운영 CLI·승인 검증·토큰 발급·API 호출은 없다. Windows는 명시적으로 거부한다.
시험은 hosted Linux의 격리된 임시 디렉터리를 사용하며 실제 Runner 상태 경로는 만들지 않는다.

- 단일 Runner가 쓰는 **하나의 고정된 신뢰 경로**를 Runtime 호출자가 사용해야 한다.
  예시 예정 경로: /home/server01_cicd-runner/FDS_CICD_DeployState (현장 미생성).
  요청별 임시 root·Job별 상태 경로로 바꾸면 중복 방지가 성립하지 않는다.
  여러 Runner/호스트나 공유/NFS 파일시스템을 지원하는 분산 lock은 아니다.
- root는 실행 UID 소유 0700, 모든 상위 경로는 링크가 없는 디렉터리이며 root/실행 UID 소유,
  group/world writable이 없어야 한다. lock/claim은 실행 UID 소유 0600 정규 파일·nlink=1이다.
- deploy.lock에 nonblocking exclusive flock을 잡아 서로 다른 계획도 동시에 실행하지 않는다.
  경쟁 프로세스는 DEPLOYMENT_LOCK_BUSY로 중단한다. busy를 우회하는 별도 디렉터리·재시도 루프는 없다.
- 계획 SHA 파일을 O_CREAT|O_EXCL로 생성한다. 승인 SHA·실제 context·CLAIMED 시각을 적고
  파일과 디렉터리를 fsync한다. 이미 파일이 있으면 PLAN_ALREADY_CLAIMED다.
  승인 파일 교체·새 Run·rerun·프로세스 재시작이 claim을 초기화하지 않는다.
- 생성 후 write/fsync 실패도 이미 생성된 파일을 **보존**한다. 빈 파일·부분 파일은 자동 복구,
  덮어쓰기·삭제·재사용하지 않는다. 저장 성공이 불명확하면 CLAIM_IO_FAILED로 중단한다.
- 반환 Claim 객체는 lock FD를 실행 종료까지 보유한다. check()는 경로/inode·권한·nlink·claim bytes를
  확인하며 교체·변조·삭제가 있으면 중단한다. 실제 authorize callback이 API 호출 전에 이를 호출해야 한다.
- close()/예외/프로세스 종료는 mutex를 해제하지만 claim 파일은 유지한다.
  정상 종료만으로 동일 계획을 다시 실행 가능하게 만들지 않는다.
  lock 소유자의 장기 작업·정지 상태는 현장 수동 대조 대상이며 kill·claim 삭제를 자동화하지 않는다.

Claim은 동시 실행/동일 계획 재사용을 막는 조건이며 실행 승인 자체가 아니다.
다른 프로그램이 같은 UID로 이 신뢰 경로를 바꾸거나 삭제하는 악의적 행위를 격리하는
샌드박스가 아니다. Runtime은 외부 입력·비밀값·비신뢰 Job이 이 경로를 쓰지 못하도록 해야 한다.
현재 함수는 실제 승인 lookup, claim과 Adapter 연결, durable 실행 checkpoint,
토큰·API transport·cleanup·workflow를 제공하지 않는다.

## 검증과 운영 잔여

v1 17개 회귀와 v2 18개 계약 시험을 실행한다. 별도 POSIX 파일 시험17개는 다른 프로세스의
mutex 경쟁·프로세스 종료·새 Run/승인 재사용·부분 write/fsync·경로/권한/내용 drift·symlink/hardlink
거부를 확인한다. 실제 Deployment·자격증명·승인 lookup·서버 변경은 시험하지 않는다.

다음 연결 PR: 실제 승인 참조 검증, 고정 상태 경로와 claim/checkpoint 결속,
TLS API client·요청 deadline·실제 credential metadata·회수, workflow.
운영 선행으로 최신 HPA/Deployment 기준선, 이전 image 재Pull/호환성, Worker Pull,
APP DB TLS/Health/E2E 및 K8S 시험 종료/보호 상태를 확인한다.
이 코드는 K8S 시험 실패를 해소하거나 Deploy·Runtime 승인/Phase1 인수를 부여하지 않는다.
