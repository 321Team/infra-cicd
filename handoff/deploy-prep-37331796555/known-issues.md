# Deploy 운영 인계: 미확인과 Known Issue

2026-10-06 KST / AI usage: CODEX_ASSISTED.
범위 P1-CICD-01 #11 / baseline #16. 실제 Deploy/Runtime 승인·AC 인수 기록이 아니다.

| 항목 | 현재 근거와 영향 | 운영 전에 필요한 확인 |
|---|---|---|
| GitHub 조회 장애/429 | PR56→57의 검토된 선택: 매 GET/PATCH fresh lookup, 단일 조회 실패 시 추가 쓰기·자동 rollback 중단. 첫 PATCH 뒤 장애면 부분 배포·MANUAL_RECONCILIATION_REQUIRED 가능 | 실제 read token의 rate budget, poll interval/timeout, 승인 원문 수정·삭제/만료, 부분 배포 수동 대조 절차를 운영 호출자에 고정 |
| 409/422 error details | PR57 리뷰5425436098 비차단1. 실제 failed JSON test가 details 없는 422를 반환할 수 있음. 현재 보수적으로 outcome unknown을 유지 | CP1 실제 dryRun=All test-only 요청의 Status body를 인증값 없이 수집해 fixture 대조. 수신 전 판정 완화 금지 |
| CP1 dry-run discovery 잘림 | PR59 독립 리뷰5426333521 비차단1. 기존 parser는 discovery body의 잘림에도 STOP하여 현장 수집이 실패할 수 있음 | 후속 parser의 고정 PATCH URL→HTTP status→body 결속을 독립 리뷰한 SHA에서 사용. 완료 discovery만 제외하고 target Status 잘림/로그 모호성은 STOP 유지 |
| CP1 klog 여러 줄 본문 | [PR60 리뷰5426647103](https://github.com/321Team/infra-cicd/pull/60#pullrequestreview-5426647103) B1: 실제 Status JSON은 trailing LF로 body=< 여러 줄이 될 수 있어 단일 행 parser가 수집을 중단함. 같은 PR에서 전체 블록 소비·strict TAB/닫힘·정규화 hash basis 및 회귀8개로 보완 | 최신 수정 HEAD 독립 리뷰 후 현장 kubectl 버전/로그 형식을 확인. hash basis=KLOG_NORMALIZED_TRAILING_LF는 원 HTTP 바이트 해시 증거가 아님. target 잘림/미완료 블록은 STOP 유지 |
| CP1 인증 경로 | PR59 리뷰 비차단2. server02가 sudo 없이 읽는 보호된 client-cert kubeconfig의 존재·지원 여부 미확인 | metadata부터 로컬 읽기 전용 수집. 기존 root0600 admin.conf 권한 변경/관리 인증 복사로 우회하지 않음. 지원 경로 없는 결과면 backend 실행 중단 |
| Runner API TLS | PR57 리뷰 비차단2. hash-pinned CA와 IP hostname10.1.93.109 검증 코드는 준비됨 | 실제 Runner handshake/IP SAN·공개 CA 지문. CP1 조회 성공이나 Mock context를 Runner TLS PASS로 확대하지 않음 |
| credential | CP1 기존 인증 backend·사전 확인 CLI를 후속 코드로 준비. 실제 실행·발급/주입·원본/임시 입력 정리 경로 미연결 | 검토된 관리 경로와 token 유효기간·SA UID·audience·Runner 인증. 로컬 참조 제거와 서버 취소/만료를 구분 |
| Worker별 Ready endpoint | [baseline #15 관측](https://github.com/321Team/fds-project-baseline/issues/15#issuecomment-6012339851): 2026-10-06 17:21:11 KST에 두 서비스 6 Pod가 worker02에 집중, transaction-api NodePort의 Ready endpoint도 worker02에만 있음. 현재 시각 실조회/복구 proof가 아님 | 배포 전후 Worker별 Pod/Ready endpoint 수·nodeName·EndpointSlice 및 externalTrafficPolicy를 재확인. worker01/03 local endpoint0 영향과 3 upstream/HA 조건을 APP/K8S 담당과 해결 |
| APP/K8S placement 변경 | [infra-kubernetes PR65](https://github.com/321Team/infra-kubernetes/pull/65) 2026-10-06 후속 조회 HEAD 07f2de4a226d5c5026a383e03a2c00abddf485aa는 OPEN. PR 본문에서 server dry-run/실제 적용 NOT RUN으로 보고됨. 별도 담당 작업 | 적용/Pod 분산 확인 후 최신 전체 Deployment/HPA·spec/UID/RV·이전 image를 다시 수집하고 계획/승인을 갱신. 기존 계획 재사용 금지 |
| 실행 안전성 | 단일 POSIX Runner/고정 보호 root 및 파일 claim/journal. 상태 파일·Directory의 실제 provisioning 미실행 | 하나의 고정 경로, 로컬 fsync/flock 지원, 신뢰 UID만 접근, 기존 consumed claim/partial checkpoint 보존 |
| 실제 배포 선행 | CP1 과거 Deployment/RBAC 관측과 Push PASS는 존재. 최신 HPA/Deployment·병행 K8S 시험 종료·이전 image 호환성·Worker Pull·APP DB TLS/Health/E2E는 현재 proof가 아님 | 실행 직전 최신 원본/승인·계획/Workflow·복구 입력을 대조 |

기존 claim/partial 파일을 삭제해 재실행하거나, 관리자 kubeconfig를 Runner에 주입하거나,
ROLE 확대·SA 재생성을 실패 우회로 수행하지 않는다.
PR 코드 승인은 실제 Deploy·DELIVERY_GATE PASS의 승인이 아니다.

[연결 문서](../../docs/deploy-execution-connection.md) /
[TLS·checkpoint](../../docs/deploy-transport-checkpoint.md) /
[credential 계약](../../docs/deploy-credential-lifecycle.md) /
[CP1·Runner 사전 확인](../../docs/deploy-access-precheck.md)
