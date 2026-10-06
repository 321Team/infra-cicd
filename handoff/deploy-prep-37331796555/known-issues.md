# Deploy 운영 인계: 미확인과 Known Issue

2026-10-06 KST / AI usage: CODEX_ASSISTED.
범위 P1-CICD-01 #11 / baseline #16. 실제 Deploy/Runtime 승인·AC 인수 기록이 아니다.

| 항목 | 현재 근거와 영향 | 운영 전에 필요한 확인 |
|---|---|---|
| GitHub 조회 장애/429 | PR56→57의 검토된 선택: 매 GET/PATCH fresh lookup, 단일 조회 실패 시 추가 쓰기·자동 rollback 중단. 첫 PATCH 뒤 장애면 부분 배포·MANUAL_RECONCILIATION_REQUIRED 가능 | 실제 read token의 rate budget, poll interval/timeout, 승인 원문 수정·삭제/만료, 부분 배포 수동 대조 절차를 운영 호출자에 고정 |
| 409/422 error details | PR57 리뷰5425436098 비차단1. 실제 failed JSON test가 details 없는 422를 반환할 수 있음. 현재 보수적으로 outcome unknown을 유지 | CP1 실제 dryRun=All test-only 요청의 Status body를 인증값 없이 수집해 fixture 대조. 수신 전 판정 완화 금지 |
| Runner API TLS | PR57 리뷰 비차단2. hash-pinned CA와 IP hostname10.1.93.109 검증 코드는 준비됨 | 실제 Runner handshake/IP SAN·공개 CA 지문. CP1 조회 성공이나 Mock context를 Runner TLS PASS로 확대하지 않음 |
| credential | actual TokenReview backend·발급/주입·원본/임시 입력 정리 경로 미연결 | 검토된 관리 경로와 token 유효기간·SA UID·audience·Runner 인증. 로컬 참조 제거와 서버 취소/만료를 구분 |
| 실행 안전성 | 단일 POSIX Runner/고정 보호 root 및 파일 claim/journal. 상태 파일·Directory의 실제 provisioning 미실행 | 하나의 고정 경로, 로컬 fsync/flock 지원, 신뢰 UID만 접근, 기존 consumed claim/partial checkpoint 보존 |
| 실제 배포 선행 | CP1 과거 Deployment/RBAC 관측과 Push PASS는 존재. 최신 HPA/Deployment·병행 K8S 시험 종료·이전 image 호환성·Worker Pull·APP DB TLS/Health/E2E는 현재 proof가 아님 | 실행 직전 최신 원본/승인·계획/Workflow·복구 입력을 대조 |

기존 claim/partial 파일을 삭제해 재실행하거나, 관리자 kubeconfig를 Runner에 주입하거나,
ROLE 확대·SA 재생성을 실패 우회로 수행하지 않는다.
PR 코드 승인은 실제 Deploy·DELIVERY_GATE PASS의 승인이 아니다.

[연결 문서](../../docs/deploy-execution-connection.md) /
[TLS·checkpoint](../../docs/deploy-transport-checkpoint.md) /
[credential 계약](../../docs/deploy-credential-lifecycle.md)
