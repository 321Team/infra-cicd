# Deploy session 연결부

관련: infra-cicd #11, fds-project-baseline #24. 기존 구성요소의 기준은
[execution](deploy-execution-connection.md), [credential](deploy-credential-lifecycle.md),
[transport/checkpoint](deploy-transport-checkpoint.md)를 유지한다.

## 구현 범위

`tools/delivery/deploy_session.py`의 `Session.run()`은 다음 경로를 연결한다.

1. plan/approval/push 원본 바이트와 기대 해시, 신뢰된 실행 식별자를 검증한다.
2. CA 핀과 TokenRequest 응답의 만료 상한을 검사한다.
3. 호출자가 인증한 별도 CP1 reviewer로 **현재 토큰**을 TokenReview한다.
4. 검증한 Credential의 토큰 공급 함수와 실제 잔여 수명을 고정 대상 TLS
   Client에 연결한다. JWT와 TokenRequest status 중 이른 만료를 적용하며,
   원격 검증 전부터 wall/monotonic 두 시계로 제한한다.
5. GitHubComments → Verifier → Execution → BoundCheckpoint를 연결한다.
   매 GET/PATCH의 승인 재조회, 고정 영속 claim과 재실행 차단, 두 Deployment의
   변경 전 전체 검사, image-only PATCH 및 기존 복구 범위를 그대로 적용한다.
6. 성공/실패 모두 checkpoint를 닫고 Credential의 로컬 참조를 해제한다.
   checkpoint close 실패도 자격증명 정리를 막지 않는다.

기존 receipt/checkpoint 스키마를 바꾸지 않는다. 별도의 session 결과에만
credential 관찰과 정리 결과를 붙인다. Execution 내부의
`CREDENTIAL_METADATA=CALLER_SUPPLIED_NOT_VERIFIED_HERE`는 그대로 유지한다.
`EXECUTION_RETURNED`는 함수 반환 상태이며 성공 판정이 아니다. 실제 결과는
`execution_receipt.ROLLOUT`, `ROLLBACK`, `error_code`를 확인한다.
HEALTH_E2E, SOURCE_APPROVAL, DELIVERY_GATE는 자동 승격하지 않는다.

## 호출자의 필수 전제와 남은 연결 작업

이 모듈은 내부 연결 API이며 실행 CLI/Workflow가 아니다. 운영 호출자는
아래 전제를 제공해야 한다. 이 PR이 전제를 구축하거나 승인한 것은 아니다.

- 검토된 소스와 Workflow SHA, 신뢰된 Run/Attempt, 승인 바이트의 외부 기대 해시.
- 인증된 기존 CP1 발급 채널에서 받은 토큰, 최신 SA UID와 TokenRequest
  `status.expirationTimestamp`. 저장된 TokenReview JSON을 reviewer로 대체 금지.
- CP1 권한을 유지한 인증 backend. Runner에 TokenReview 권한이나 CP1 관리자
  kubeconfig를 전달하지 않는다. 기존 native probe는 무배포 시험이며 이 변경으로
  배포 경로가 되지 않는다.
- 검증된 CA 원본/해시, 정확한 승인 코멘트를 읽을 수 있는 GitHub 자격증명 함수.
- 정상 Runner 계정과 Linux/POSIX 환경의 **고정 영속** private state_root.
  재실행마다 임시 디렉터리를 만들거나 기존 claim을 삭제하면 안 된다.
- 현재 Source 독립 리뷰, Push 증적, 실제 실행 승인, 다른 작업과의 비중첩 확인.

신뢰된 호출자는 `Session(state_root, github_token_provider, reviewer)`를 만들고
`run()`의 keyword 인자에 위 원본을 공급한다. 토큰은 argv/로그/증적 파일에
쓰지 않는다. 현재 공개 실행 명령은 제공하지 않으며 TokenRequest 발급,
SSH 전달, Actions Workflow와 신뢰된 실행 문맥 주입은 후속 구현 범위이다.

반환값의 최종 파일 저장 및 입력 토큰을 가진 호출자/전달 채널의 참조 정리는
호출자 책임이다. 모듈의 `LOCAL_REFERENCE_DROPPED`는 물리적 메모리 소거,
서버 토큰 폐기, 호출자 입력의 소거 또는 전체 cleanup 완료가 아니다.

## 중단과 복구

입력/CA/TokenReview 실패는 `NOT_EXECUTED`, `SESSION_SETUP_REJECTED`로 반환한다.
모듈은 원격 오류 본문이나 임의 예외 문자열을 결과에 복사하지 않는다.
Execution 시작 후 결과가 불명확하거나 checkpoint 닫기가 실패하면
`MANUAL_RECONCILIATION_REQUIRED`이다. 기존 claim과 journal은 삭제하지 말고
두 Deployment의 현재 UID/RV/image/상태와 마지막 기록을 대조한다.
기존 승인에 포함되지 않은 복구나 같은 계획의 재실행은 하지 않는다.

## 검증

Linux/POSIX 저장소 루트에서:

```sh
python3 -m unittest discover -s tools/delivery -p 'test_deploy_session.py' -v
python3 -m unittest discover -s tools/delivery -p 'test_*.py'
```

Session 시험은 GitHub/TokenReview/Kubernetes 통신을 MOCK 처리하고 실제 POSIX
claim/잠금/fsync/journal을 사용한다. 만료, 승인 변경, 저장 실패 전후, 정리 실패,
중복 실행, 민감값 유출 방지를 검증한다. Windows는 이 POSIX 시험을 skip하므로
PASS 근거로 사용하지 않는다. source-validation CI가 기존 discover 경로로 실행한다.
이 검증은 실제 TLS 접속, TokenRequest 발급, Kubernetes rollout, Harbor 변경,
E2E 인수 또는 운영 배포 승인을 의미하지 않는다.
