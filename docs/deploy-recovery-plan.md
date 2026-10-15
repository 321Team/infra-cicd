# AC09 이전 digest 복구와 현행 이미지 복원 준비

2026-10-15 / P1-CICD-01 / 코드·실행안 검토 대상. Runtime 미실행.

AC09 정본은 승인 digest·승인자 고정, 재실행/동시 실행 통제, 이전 digest 복구 결과를 요구한다.
관련 기능은 실패 시 이전 digest로 복구하고 두 서비스 호환성과 DB Schema 영향을 재검증한다.
기존 실행기는 배포 중 실패한 경우의 자동 복귀만 제공한다. 이번 추가 경로는 인위적인 장애 없이
**현재 두 이미지 → 이전 승인 두 이미지**를 명시적으로 실행한다. 정상 회차의 결과를
자동 실패 처리 시험으로 표시하지 않으며, 본 PR 병합만으로 AC09를 인수하지 않는다.

## 계약과 승인 경계

- 기존 `P1-CICD-deploy-plan-2`는 Push receipt의 두 digest로 배포하는 의미를 유지한다.
- 새 `P1-CICD-recovery-plan-1`은 기존 plan 필드 전체와 `recovery` 필드를 요구한다.
  기존 `targets[*].previous_image`는 **과거 forward plan의 복귀 이미지** 의미를 유지한다.
  실행용 Target만 반전한다: baseline=검증된 Push digest, desired=과거 previous_image.
  문서의 필드 의미와 실행 receipt의 before/after를 혼동하지 않는다.
- `recovery`의 정확한 필드:

  | 필드 | 내용 |
  |---|---|
  | `origin_plan` / `origin_approval` | 과거 forward plan-2와 approval-2의 UTF-8 JSON 원문 문자열 |
  | `origin_plan_sha256` / `origin_approval_sha256` | 각각 **원문 바이트** SHA256 |
  | `origin_execution` | 과거 repository/ref/workflow_sha/run_id/attempt 객체 |
  | `evidence_reference` | 해당 결과를 추적할 321Team GitHub 이슈/PR URL |

- 과거 plan은 재귀 recovery를 허용하지 않는다. 새 request_id/실행 Run을 사용한다.
  origin의 delivery, Push Run/receipt, Kubernetes 범위, 두 UID/이전 이미지가 새 plan과 같아야 한다.
  origin 승인에 `allow_rollback=true`가 있어야 한다. 새 workflow SHA는 달라도 되지만
  새 plan·새 승인·실제 main checkout SHA가 일치해야 한다.
- origin 승인은 그 문서의 과거 not_before에서 형식/해시/범위를 검사할 뿐이다.
  origin의 실제 실행·작성자·당시 GitHub 승인 상태는 입력 바이트만으로 검증하지 않는다.
  리뷰어는 원본 승인과 병합된 실행 증적을 별도로 대조한다. 출력에도
  `historical_approval_verification=SUPPLIED_BYTES_ONLY`, `historical_runtime=NOT_VERIFIED`를 남긴다.
- **새 승인**은 기존 approval-2 형식을 사용해 recovery plan 전체 해시, 새 workflow,
  승인자·현재 comment URL·현재 창·실패 보상 허용을 묶는다. origin 승인의 만료시간을
  늘리거나 옛 승인문을 실행 권한으로 재사용하지 않는다. 매 GET/PATCH 직전 현재 승인 원문을 조회한다.
- 기존 단일 Runner mutex, plan별 영구 claim, attempt=1, Ready baseline, UID/spec/generation/RV
  검사, image 필드만의 JSON Patch, durable checkpoint, TLS/TokenReview/TTL900 통제를 그대로 쓴다.
- receipt의 `operation=RESTORE_PREVIOUS`가 명시적 복구를 구분한다. `ROLLOUT=READY`는
  이 방향의 Deployment Ready이다. `HEALTH_E2E`와 `DELIVERY_GATE`는 자동 승격하지 않는다.
  실패 보상이 승인됐다면 이 회차의 `ROLLBACK`은 **현행 이미지로 보상**하는 의미다.

## 실행 전 고정할 입력

현재 준비 기준은 evidence [PR219](https://github.com/321Team/fds-project-evidence/pull/219)의
배포와 [PR235](https://github.com/321Team/fds-project-evidence/pull/235)의 AC08 사전 관측이다.
아래 값은 10/15 실행 직전 상태를 대신하지 않는다.

| 대상 | 현재 / 복원할 digest | 이전 / 복구할 digest |
|---|---|---|
| transaction-api | `338b8a2871507ce8b42deedc916b26e350577ee6efd18df23d7ff2dd4b17dff0` | `a7093136d12640da00570c18ed278eacbb8e9ba3637c7d509571346eac45cc33` |
| fds-engine | `8b257adb08b6d381f72b66d3386efea6112a8f072ecfafcbcd463bcd1c3be89d` | `5e2a081cd51a957c3fad038b3eab07884c446ea3c2a814a905e3f41c24f67369` |

이미지는 `10.1.93.54/fds/<service>@sha256:<digest>`만 사용한다.
대상 UID는 API `8a2af808-ec33-41f2-80b5-addde4e28910`, engine `bf825dec-465b-4603-8b71-f768d195531c`.
다르면 계획을 고치기 전에 변경 원인과 승인 범위를 다시 확인한다.

origin plan SHA256: `ed7787673f5b8f8f1f97e49e78a9359ec1c09f8835acda19cc93d7e130ee2ad4`.
origin approval SHA256: `fb1458a22db9fa91397810293cbbb0f66198b08786e6e365150cef18e0bebb4d`.
origin workflow SHA: `147f760298dd4a683ea58f2a887483769352b97d`;
Run `38412128227`/Attempt1. [과거 승인](https://github.com/321Team/infra-cicd/issues/11#issuecomment-6147190360)은
당시 창에만 유효하다. 원문은 evidence의 `deploy-after-push38404449622-20261013/` 및
`deploy-authorization-20261013-2315/`에 있다. origin/Push 원문을 새 문서로 재직렬화하지 않는다.

복구와 현행 복원은 별도 request_id/plan 해시/승인 해시/입력 해시/Run/토큰/claim으로 준비한다.
복구용은 위 recovery schema, 현행 복원용은 기존 plan-2(같은 Push receipt와 이전 baseline)를 쓴다.
현행 복원 계획을 origin 계획으로 그대로 재사용하면 영구 claim에 걸리므로 새 요청이 필수다.
새 workflow SHA/승인 원문/창/실행 담당/콘솔 복구 인수가 확정되기 전 실행 가능한 승인 JSON을 만들지 않는다.

## APP/DB 호환성 및 알려진 영향

이전 이미지의 9/29 회귀는 HTTP201 22건과 DB22행을 대조했다. [PR213](https://github.com/321Team/fds-project-evidence/pull/213)은
API만 이전 이미지로 복귀한 이력이며 engine은 미변경, Health/E2E 미검증이다.
현행 이미지의 회귀 [PR224](https://github.com/321Team/fds-project-evidence/pull/224),
부하 [PR228](https://github.com/321Team/fds-project-evidence/pull/228)는 새 복구 검증을 대신하지 않는다.
9/25·10/14 기록의 transactions 열 이름/자료형 11개가 같지만 최신 제약·권한·revision 및
이전 이미지 내부 DB 코드 전체 provenance는 미확인이다. 실행 전 변경 이력과 현재 스키마를
기존 DB 권한으로 읽어 차이를 확인한다. migration/stamp/upgrade/downgrade·권한 변경은 범위 밖이다.

이전 두 이미지에는 **업무 포트 `/metrics`가 다시 노출**된다. 과거 한시 허용은 새 위험 수용이 아니다.
노출 시간 상한, 시험 경로, 현행 이미지 복원 종료 기준을 포함한 새 승인에 이 영향을 명시한다.
호환성·허용 경로를 확인할 수 없으면 Runtime은 시작하지 않는다.

## 권장 실행 순서 — 아직 승인·예약하지 않음

1. 코드 독립 리뷰/CI/병합 후 해당 main SHA로 두 계획과 공급 번들을 고정한다.
   APP·DB·Harbor·Ansible/Runner 작업 비중첩, 관리 콘솔 인수자, 종료/복원 시간을 정한다.
   새 승인에는 복구·현행 복원 각 execute=true 1회, 각 TokenRequest 1회, 필요 preflight=false 회수,
   실패 보상 방향, 최소 업무 확인 요청 수를 정확히 명시한다.
2. 읽기 전용 fresh 검사: 두 UID/전체 spec/현재 digest/Pod imageID/각 Ready3,
   worker 배치/Local Endpoint, DB 스키마·시험 prefix0행, metrics baseline, Harbor의 두 쌍 digest 존재와
   worker pull/인증서 상태. source·RBAC·Secret·NetworkPolicy·Service 변경을 함께 실행하지 않는다.
3. 정상 계정 `server01_cicd-runner@cicd-runner01`에서 기존 [workflow 전달 절차](deploy-workflow-handoff.md)를 따른다.
   입력은 `~/FDS_CICD_DeployInputs`의 소유자 전용0600 JSON, 승인된 파일명/바이트 해시를 사용한다.
   main-only `app-deploy.yml`의 execute=false는 입력 검증만 하며 원격 승인/Runtime PASS가 아니다.
   검증 후 승인된 Run의 execute=true와 기존 CP1→Runner SSH broker를 연결한다. 토큰은 파일/로그에 저장하지 않는다.
4. 복구 1회: transaction-api 다음 fds-engine을 이전 digest로 바꾼다.
   두 서비스 모두 Ready여야 아래 확인으로 진행한다. 한 서비스만 반영되면 두 앱 복구 성공으로 집계하지 않는다.
5. 이전 상태 검증: 기존 허용 경로의 `/livez`·`/readyz`200, 관측 구간 restart/CrashLoop 없음,
   고유 prefix의 정상 deposit10000 거래1건 후보(201, detected=false, skipped=false), 응답ID↔DB1행,
   안정화 후 같은 Pod 집합의 save_success/evaluated +1 및 오류 counter 증가0를 대조한다.
   이 POST는 **제안**이며 새 실행 승인에 포함돼야 한다. 경로는 기존 Worker NodePort/API·engine 경로로
   고정하고 Runner에 NodePort 또는 pods/exec 권한을 추가하지 않는다.
6. 별도 새 승인 plan-2로 현행 digest 복원 1회. 두 상태의 시험을 동시에 돌리지 않는다.
   새 prefix 거래1건 후보로 같은 Health/DB/계측을 확인한다. 업무 포트 `/metrics`404,
   전용 metrics9091/9090 200·target6 정상까지 확인해야 현행 복원 종료다.
   edge nginx의404만으로 앱 업무 포트404를 대체하지 않는다.
7. 회차별 원본·해시·checkpoint/claim·Run·승인·before/after·토큰 만료/참조 정리·소켓/임시 프로세스를
   연결해 증적 PR을 작성하고 독립 리뷰 후 AC09 인수를 판단한다. 작업창은 종료 확인 후 해제한다.

시험 요청은 두 상태 각1건, 총2건을 최소안으로 제안한다. 전체 Rule/부하/모든 backend 거래/Client→LB 경로
재검증으로 확대하지 않는다. 배포 전후 Pod가 다르므로 이전 Pod counter에서 새 Pod counter를 빼지 않는다.

## 실패·중단·복원

- UID/image/spec drift, 승인/토큰 만료, claim 충돌, 검증 불가이면 새 요청을 중단한다.
  모든 API 호출 전 새 승인·권한·남은 시간을 확인하며 자동 재발급·재실행하지 않는다.
- 명확한 PATCH 거부/rollout 실패의 보상은 **현재 새 승인**의 allow_rollback 범위에서만 한다.
  정상 완료 후 업무 확인 실패는 기존 실행기의 자동 보상 트리거가 아니므로 별도 현행 복원 회차로 처리한다.
- PATCH 결과 불명, 승인 철회/권한 상실, checkpoint 실패는 자동 복귀를 포함한 추가 쓰기를 하지 않는다.
  claim/checkpoint/원본을 보존하고 현상·마지막 확인 이미지·미확인 대상을 콘솔 인수자에게 전달한다.
  혼합 이미지/Ready 불량이면 정상 두 baseline을 요구하는 plan-2를 강행할 수 없다.
  새 상태에 맞춘 복구 계획과 유효 권한이 필요하다.
- POST timeout/응답 유실은 미저장으로 가정하지 않고 DB와 대조한다. 자동 POST 재시도·시험행 삭제를 하지 않는다.
  Service/DB/RBAC/방화벽/SELinux/인증서 검증 완화, 전체 리소스 삭제, 물리 호스트 종료는 하지 않는다.
- 복구에 성공해도 현행 복원이 확인되지 않으면 창을 해제하거나 AC09 완료 처리하지 않는다.
  Runtime/독립 검토/최종 인수는 별도 단계다.
