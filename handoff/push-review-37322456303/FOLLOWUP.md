# 리뷰 대기 중 후속 작업과 재개 순서

범위: P1-CICD-01 새 source 9658255 / APP Run37322456303의 후속 작업.
프로젝트 전체 Phase1 전수 현황이 아니다. 새 AC/예외 승인/기한은 추가하지 않는다.
기준 조회: 2026-10-05 23:34 KST 이후, main966a268b / PR52 독립 리뷰0.
재환님의 실제 부재/취침 여부는 미확인이다.

| 순서 | 작업 | 담당 | 상태·완료조건 |
|---|---|---|---|
| 1 | 만료 Run5 입력 검토 정리 | 이권욱 | PR50 병합 없이 종료·PR52로 대체. 원본/claim/실패증적 보존 |
| 2 | 새 Build/Scan 식별값 1차 인계 준비 | 이권욱 | BUILD_SCAN_HANDOFF.json 작성. 서비스별 OCI/manifest/config/report와 Run/Source/DB 연결 |
| 3 | 증적·초안 독립 리뷰 | 이재환(요청됨) | PR52 최신 HEAD 검토. 필요 시 기존 governance상 적격 대체 리뷰어 절차 적용; 부재만으로 생략 불가 |
| 4 | 새 Push 실행안 확정 | 이권욱 | 실제 재개 직전 main, 두 digest, 기존 robot 권한·만료, 승인 시작/종료와 링크 확정 |
| 5 | 최종 입력 독립 검토·로컬 검증 | 이권욱 / 독립 리뷰어 | 확정 JSON 바이트/SHA256 검토 후 새 Runner 입력 배치 및 execute=false |
| 6 | 실제 Push와 정리 | 이권욱 | 유효 승인 시간 내 Secret2개 임시 등록, fresh scan→digest보존 Push→두 remote manifest 확인→임시 auth 및 Secret 회수 |
| 7 | Harbor 2차 인계 | 이권욱 생산 / 이하영 인수 | 원격 repository/digest, Push·정리 receipt, Harbor Scanner 연결. 기존 HBR test 이미지와 혼용 금지 |
| 8 | 앱/TLS 회귀 및 배포·복구 준비 | 이재환(APP), 이권욱(CICD), 관련 인프라 담당 | DB sslmode/라이브러리 영향, 배포 이전 digest/RBAC/worker pull 조건 정리. 실제 Deploy는 별도 승인 |
| 9 | Deploy·Health/E2E·rollback·최종 인수 | 각 기존 담당 | 별도 유효 승인과 실행 증적 후 각 서비스 CI→Harbor→Manifest→Pod 4/4 및 rollback 결과 확인 |

## 지금 유지할 상태
- main 및 실행 config는 변경하지 않는다. PR52는 검토용 Draft, 실행 전 병합 보류.
- Push JSON 초안과 이미 확보한 두 이미지 바이트를 유지한다. 승인/기간을 대신 채우지 않는다.
- Harbor Actions Secret은 이전 회수 PASS 이후 이번 세션 재등록하지 않았다. 리뷰 대기 중 등록할 이유가 없다.
- 새 시간 제한 승인은 실제 실행 가능 시점에 정한다. 밤새 유효기간을 임의 연장하지 않는다.
- 원본 Run 폴더·OCI·Trivy report·DB·수집 회수본을 보존한다. 저장공간 정리가 필요해도 현재 증적 경로는 삭제 대상에서 제외한다.
- 앱 source/입력이 그대로면 기다렸다는 이유만으로 Test/Build를 반복하지 않는다. Push adapter가 실행 직전 fresh DB/Scan을 수행한다.
- 새 취약점, main 변경, 원본 해시 변경 또는 승인 만료 시 중단한다. claim 삭제나 과거 승인 재사용으로 재시도하지 않는다.

## 새 실행 승인에 넣을 구체적 범위
대상은 Run37322456303에서 만들어진 transaction-api/fds-engine 두 OCI이다.
예정 repository는 10.1.93.54/fds/transaction-api 및 10.1.93.54/fds/fds-engine.
동작은 fresh Scan, Skopeo digest 보존 전송, tag/digest raw manifest 대조, 자격증명 정리까지다.
기존 robot만 사용하며 신규 권한·TLS 완화·Deploy·원격 삭제는 포함하지 않는다.
Repository Secret과 기존 관리자 접근의 잔여위험을 다시 범위에 명시하고 실제 승인 링크와 시작/만료를 받는다.
실패 시 원격 부분 성공 가능성을 result/dispatch-result로 대조하고 중단한다. 자동 삭제·자동 재시도하지 않는다.

## 인계 한계
BUILD_SCAN_HANDOFF.json의 remote/Manifest/Pod/rollback 항목은 아직 미생성이다.
인계표 준비를 담당자 수신·인수 완료로 표시하지 않는다.
별도 증적 저장소 발행/인수와 P1-CICD-01 티켓 종결은 미수행이다.
AI usage: CODEX_ASSISTED.
