# SITE 원본·권한·기간 대조 — 확인된 값과 실행 승인 구분

**10/25 채택 반영:** [범위·control 연결](adopted-scope-control.md)의 상시 시간대와
실제 control snapshot/ref 선택을 우선한다. 아래 미선택 설명은 당시 원문 대조 이력이다.
실제 RUN/action·유한 grant·발급/종료 원본은 합성하지 않으며, 자원/RBAC 기한을
자동 배포 GO로 사용하지 않는 구분은 그대로 유지한다.

2026-10-24 KST. baseline #34/#37/#42/#43/#45/#46/#47과 ROSA #5의 기존 댓글 원본,
현재 main 및 비공개 공유폴더의 관련 파일 목록을 대조했다. 실제 발급·권한 변경·배포를 하지 않았다.

## 확인되어 운영 준비에 편입한 기간

| 근거 | 확인한 사실 | #46 자동 배포에 적용되는 한계 |
|---|---|---|
| [ROSA #5 6379184539](https://github.com/321Team/infra-rosa/issues/5#issuecomment-6379184539), [#46 인계 갱신](https://github.com/321Team/fds-project-baseline/issues/46#issuecomment-6379184704) | 담당자 보고상 `gwonuk-p2-admin-temp`가 User `gwonuk`에 `fds-app`·`ci-cd` admin을 부여했고, annotation의 기한은 **2026-10-26 12:00 KST**다. 두 RoleBinding UID도 기록되어 있다. | 선언된 권한 회수기한이다. 실제 부재/자동 만료나 현재 RBAC를 중앙이 재조회한 결과가 아니며, 원문은 **#46 배포 시험 GO가 아니라고 명시**한다. 보고 게시 시각을 grant 시작 시각으로 사용하지 않는다. |
| [자원 유지 결정 6338609757](https://github.com/321Team/fds-project-baseline/issues/37#issuecomment-6338609757) | 클라우드·VPN/route/SG 등 최종 정리·복구·반출·보고 기한은 **10/26 12:00**이다. | 같은 원문이 새 Apply·배포·권한/자격 재발급 GO가 아님을 명시한다. 자원 보관 마감과 배포 허가 창을 같게 두지 않는다. |
| [수동 C 변경 GO 6339564113](https://github.com/321Team/fds-project-baseline/issues/47#issuecomment-6339564113) | 10/22 시작 1회에 D·C-STOP을 하영 named admin의 수동 실행으로 대체했다. | **#46 도구 실행·SITE 채택·도구 AC는 미완료로 유지**한다. 다른 날의 자동 job에 승계하지 않는다. |
| [DB 유지 6342339344](https://github.com/321Team/fds-project-baseline/issues/47#issuecomment-6342339344), [주말 관측 변경 6356939564](https://github.com/321Team/fds-project-baseline/issues/47#issuecomment-6356939564) | 동일 앱/이미지/Secret·NP와 무인 앱0 조건으로 반복 사용·관측을 간소화했다. | 원문은 전체 장애시험·#46 도구 Runtime 인수를 제외하며, 주말 변경도 #46 Runtime 승인이 아니라고 명시한다. |
| 기존 TokenRequest parser·PR112/139/140 검토 계약 | 요청은 **900초**, 실제 응답 수명은 900초 이하이며 서버가 단축한 수명을 사용한다. | `iat/exp`는 실제 발급 후 원본에서 읽는다. 임시 RBAC 기한·자원 보관 기한이나 실제 배포 GO 기간을 대신하지 않는다. |

ROSA #5 원문의 creator ID `95111439`, updated_at `2026-10-24T03:14:59Z`,
body SHA256 `0979aef9d7b3dc36c28023edf37959e656aabde7b1cc0bd2b48a3e6765aa9ca6`를
조회 시점 원문 식별로 고정했다. `configuration_status`의 `reported_actor_permission`은 이
담당자 보고만 편입한다. 런타임의 grant/resolver는 이 진단값을 읽거나 권위로 사용하지 않는다.

공유폴더의 `fds-approved-grant-mafyuau8.tar.gz`도 이름만 보고 #46 grant로 사용하지 않았다.
멤버 목록은 `execution-20261009`의 IAM role/policy·attachment 수집이며 기존 P2-IAC 증적 경로와
연결된다. 파일 이름의 approved-grant가 ROSA 자동 배포의 RUN/action/기간을 뜻하지 않는다.
credential·Plan/state·정책 원문은 이번 분류를 위해 열거나 공개하지 않았다.

## SITE 연결에서 그대로 재사용하는 것

API/CA·OAuth·관리 SSH·기대 Runner·명시적 원본 포함 전달 및 reader/materializer 코드는 기존
선택과 PR137~140을 재사용한다. 확인된 source/contract/render·6객체 snapshot·previous 선택도
다시 제출받지 않는다. 각 선택의 현재성·미실행 replicas 인계·실제 채널 검증 한계는 유지한다.

실제 `AdoptedGrant`/운영 SITE 활성화 전에 남는 항목은 다음 세 묶음이다.

1. **실제 실행 범위·기간:** 자동 job RUN/action/Binding·허용 창·주체. 확인한 원문은 이 결정을
   제공하지 않으며, 코드 승인·수동 GO·권한 annotation·정리 기한에서 합성하지 않는다.
2. **지정 control 근거:** 기존 원문의 철회/갱신을 어느 정확한 source/ref/version으로 확인할지
   채택해야 한다. 원문 식별 pin과 실제 control 권위를 구분하며 임의 main/과거 IAM grant를 쓰지 않는다.
3. **역할별 의미·실행 후 생산:** producer 전체 lineage·replicas 인계 baseline·finish scope를 연결한다.
   미래 발급 응답·native run/nonce·종료 관측을 현재 완료 원본으로 만들지 않는다.

이것은 새 팀 제출 양식·별도 ledger·매 단계 새 GO를 요구하는 결정이 아니다. 기존 #46 기록과
운영 코드 선택에 한 번에 연결하되, 이번 요청의 발급·권한 변경·배포 금지 범위를 유지한다.
전체 SITE 활성화·Runtime 인수는 아직 미완료이며, 이미 확인된 코드·원본을 미구현으로 되돌리지 않는다.
