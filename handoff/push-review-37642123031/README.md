# Run37642123031 새 Push 입력 준비

CODEX_ASSISTED · 2026-10-08 KST. 기존 handoff/push-review-37322456303 양식을 유지한 새 인계다.
공통 Build/Scan 기준 Run37642123031/1, source adc90410, workflow4e9324cd.
Push/Deploy Runtime 코드·Workflow·활성 config는 변경하지 않는다.

## 사실과 확인 범위

- 실제 Test/Build/Scan PASS 원본: [증적147](https://github.com/321Team/fds-project-evidence/pull/147), exact HEAD d54512ec4f7959e17b7cdc719d249196417369e7. 현재 독립 리뷰 대기.
- [실행 종료 기록](https://github.com/321Team/fds-project-baseline/issues/16#issuecomment-6041006145). 원본은 cicd-runner01 정상 계정 HOME/FDS_CICD_AppWorkflow._6zedcc5에 보존.
- BUILD_SCAN_HANDOFF.json은 수신 receipt에서 연결한 index다. 새 실행 증적이나 raw payload 대체본이 아니다. OCI/report/DB 실제 바이트는 서버 collector가 검증했고 수신 archive에는 포함되지 않는다.
- push.draft.json은 실제 source/config/Run/Attempt, 두 이미지 archive/manifest/config와 report hash를 입력에 연결한다. template_only=true, 승인 상태는 PENDING, credential·실행 승인·만료는 null이다. Harbor AC-02/현재 TLS·인계 reference도 최종 입력 확정 때 기존 근거와 실행 시점 확인을 연결하도록 null로 둔다. 기존 AC-02 실패 선언이나 재시험 요구가 아니다.
- reviewed main delivery_gate.py의 순수 로컬 검사: 의도한 미확정 6항목만 BLOCK, receipt 체인 hash MATCH. 이는 Push adapter의 execute=false 성공이나 Runtime 검증이 아니다.
- 00:26:57 KST 정상 Runner UID1000/HOME0700에서 Skopeo1.24.0-2.el10 설치와 rpm -V rc0, 필요한 copy/inspect 옵션을 확인했다. 설치/수정/인증/registry 접속은 하지 않았다.
- Repository Secret 이름 목록에는 Harbor Secret2개 없음. 값 조회·등록 없음. 조직/Environment Secret 유무나 기존 Robot 현재 상태는 이 조회로 판정하지 않는다.

## 남은 입력과 실행 순서

1. 증적147 독립 검토와 이 초안의 실제 식별값 대조. 기존 원본 Test/Build는 같은 입력으로 반복하지 않는다.
2. HBR 이하영: 사용할 CI Robot의 비민감 ID/세대, push/pull 권한 범위, 만료·disabled 상태, 기존 credential 사용 가능 여부와 안전한 전달 경로, 회수/복구 조건 인계. 토큰·비밀번호·token 해시는 GitHub에 올리지 않는다.
3. 이권욱: Secret 보호·수동 통제 잔여위험의 이번 회차 적용, exact main·두 이미지·비중첩 창·유효기간·승인 참조를 확정한다. 재발급 HOLD를 기존 Robot 사용 불가 판정으로 확대하지 않는다.
4. 최종 JSON 바이트/SHA256 독립 검토 → Runner HOME/FDS_CICD_PushInputs에 확정본 배치 → main app-push.yml execute=false → 승인 유효시간 내 execute=true 한 회차. 초안을 활성 입력 경로로 배치하지 않는다.
5. fresh Scan→Skopeo digest 보존 Push→원격 tag/digest manifest 대조→임시 auth 및 등록한 Actions Secret 회수. result/dispatch-result/SHA256SUMS와 부분 성공 여부를 모두 확인한다.
6. #16 생산 → #13 소비: 서비스별 원격 digest·Push/정리 receipt 2차 인계. Manifest/Pod imageID/rollback 값은 실제 별도 Deploy 후 추가한다. 지금은 null/NOT_EXECUTED를 유지한다.

## 승인과 중단·복구

[이전 승인5997038348](https://github.com/321Team/fds-project-baseline/issues/16#issuecomment-5997038348)은 source9658255/Run37322456303 한 회차이며 10/6 00:55:07 KST 만료했다. [이전 Push 완료](https://github.com/321Team/fds-project-baseline/issues/16#issuecomment-5997512352)도 새 이미지의 성공 증거가 아니다.
[HBR 후속 기록](https://github.com/321Team/fds-project-baseline/issues/13#issuecomment-6040273141)의 Robot 재발급은 HOLD, 자동기동 FAIL은 별도 잔여다. 임의 재발급·권한 확대·위험 수용은 수행하지 않는다.

현재 코드/원본 불일치, 승인 만료, fresh Scan HIGH/CRITICAL, TLS/인증 이상, 다른 Runtime과 중첩 시 STOP. 부분 Push 실패 시 원격 잔여와 claim을 보존하고 자동 재시도/claim 삭제/tag 삭제 없이 대조·처분 판단으로 넘긴다. credential 정리 실패 시 정상 완료로 보고하지 않는다.

STATIC_INPUT=EXPECTED_BLOCK / ADAPTER_VALIDATION=NOT_RUN / PUSH=NOT_EXECUTED / DEPLOY=NOT_EXECUTED / DELIVERY_GATE=NOT_VERIFIED.
