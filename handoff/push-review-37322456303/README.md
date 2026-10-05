# Run 37322456303 Push 검토 초안

AI usage: CODEX_ASSISTED

## 실행과 검증
- APP source: 9658255ce9e39167cc8876a38b98aeb1f0311694.
- 중앙 Workflow: 966a268bdf002f537d6d21d1eec87dfd116f27a5 / Run 37322456303 / Attempt 1.
- https://github.com/321Team/infra-cicd/actions/runs/37322456303
- 2026-10-05 23:09:50~23:11:29 KST, cicd-runner01 / UID1000.
- APP_WORKFLOW/BUILD_SCAN/두 IMAGE_SCAN PASS. 각 HIGH0/CRITICAL0.
- engine Unit, 양쪽 Contract/Build 종료 코드0. API Unit은 APP 승인된 N/A.
- 사용자 서버 collect: EVIDENCE_CHECK=PASS / BINDING=COMPLETE / RAW_PAYLOAD_CHECK=LOCAL_BYTES / SOURCE_BINDING=MATCH / COLLECT_RC=0.
- 원본: /home/server01_cicd-runner/FDS_CICD_AppWorkflow.x8atnwzq
- 회수본: /home/server01_cicd-runner/FDS_CICD_Evidence37322456303.ckgu_vrz/collected/app-evidence.tar.gz
- 회수본 SHA256: 90c0e390adadb8c1e2dcaad65b9674ed441edb4235b9e3b3e95aef93c79b9ce7.
- 수신 측 archive/sidecar/사용자 별도 출력 해시 일치, 영수증6개·원본payload목록69개 연결 일치. GitHub Run 및 exact config 원본 해시 독립 대조.
- 수신 회수본에는 raw logs/OCI/reports/DB 바이트가 없으므로 해당 바이트 검사는 서버 collect 결과에 의존한다.
- 기존 app_evidence verify CLI는 수신 Windows 환경의 pwd 모듈 부재로 시작되지 못했다. 수신 측 PASS는 별도 오프라인 archive/receipt/context/payload목록 연결 검사이며 canonical verify 실행 성공으로 표시하지 않는다.
- DB_PREFLIGHT는 Trivy DB 검증이다. 앱 DB sslmode/Harbor TLS 회귀 PASS가 아니다.

## 두 서비스 식별
| 서비스 | registry manifest digest |
|---|---|
| transaction-api | sha256:fa18dc36db8de35fb29ae9f5ec7bea7b7a6a0d0cd293649a79ed0d4dbb093b50 |
| fds-engine | sha256:d77b80954fec5c408fb0bb91995d5faf3155ef2b74c76bc4f7e95cf319249e5b |

위 값은 로컬 OCI/Scan의 manifest digest이며 아직 원격 Harbor에 존재한다고 주장하지 않는다. Push 후 원격 raw manifest를 대조한다.

## 입력 상태와 승인 경계
push.draft.json은 template_only=true, PENDING_REVIEW_AND_NEW_PUSH_AUTHORIZATION이다.
credential_reference, push_authorization.reference/expires_at은 null이며 실행 입력이 아니다.
Harbor TLS/AC02 PASS 및 handoff_reference는 기존 독립 검토 입력 infra-cicd#50와 HBR Lead 기록(5947371286)의 역사적 근거를 유지한다. 이번 회차 재시험이나 새 credential 권한·만료 확인을 수행했다는 뜻이 아니다. 설정 변경 근거가 없는 상태에서 기존 AC02를 반복 요구하지 않는다.
삭제한 Actions Secret 두 개가 다시 등록됐다고 가정하지 않는다.

기존 Push 승인 2026-10-05 22:21:09 KST 만료 및 이전 claim/실패증적을 보존한다.
새 입력은 새 source/APP run/result hash/digest에 연결한다. 과거 Run5 결과를 덮어쓰지 않는다.
Push 실행 main은 현시점 966a268b... 후보이며 최종 승인 직전 재조회한다.
이 PR은 검토용 Draft이며 실행 전 병합하지 않는다. 병합하면 main이 변경되어 승인SHA를 다시 고정해야 한다.

## 남은 순서
1. 새 source/Run/두 digest/증적을 독립 검토한다. 기존 소스 코드 승인을 반복 요청하는 것이 아니다.
2. 이권욱이 두 앱의 fresh scan 및 digest 보존 Push/원격검증 범위, 시작·만료, 기존 robot 사용, Secret 임시 등록·회수, Free/Private 관리자 접근 잔여위험을 포함한 새 실행 승인을 확정한다.
3. 승인 링크·만료·credential reference와 최종 status/template 값을 반영하고 최종 JSON SHA256을 독립 검토한다. 승인값은 작성자가 대신 만들지 않는다.
4. 새 Runner 입력 파일 배치 → execute=false → 조건 충족 시 execute=true 1회.
5. 원격 두 digest, fresh scan, credential cleanup, dispatch-result 봉인 확인.
6. Deploy/Health/E2E/rollback과 서비스별 4/4는 별도 인계/승인 범위다.

PUSH=NOT_EXECUTED / DEPLOY=NOT_EXECUTED / DELIVERY_GATE=NOT_VERIFIED.
