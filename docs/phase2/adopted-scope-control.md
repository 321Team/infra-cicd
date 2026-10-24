# #46 운영 범위·상시 시간대·control 연결 — 2026-10-25

**PR142 병합 후 후속:** 같은 RUN의 비공개 원본 채널에서 baseline archive를
읽는 경로를 기존 native 조립 함수에 추가했다. 메모리 원본 tuple 방식과 아래 명시적
archive 방식 중 하나만 선택한다. 실제 archive pin/root·RUN별 grant는 만들지 않는다.
PR142 비차단 시험 공백 3건도 이 변경에 묶어 보강했다.

[사용자 채택 기록6385867126](https://github.com/321Team/fds-project-baseline/issues/46#issuecomment-6385867126)을
기준으로 기존 운영 조립 함수에 실제 native 원본 reader/materializer를 연결했다.
이 기록과 이 코드는 **운영 구성 채택 및 구현/오프라인 준비**다. 이번 요청의
실제 발급·권한 변경·배포 금지를 유지하며, 구성 채택 댓글을 실행 approval로 쓰지 않는다.

## 채택된 것과 실제로 남는 것

| 구분 | 반영한 선택 / 연결 | 현재 경계 |
|---|---|---|
| 실행 시간대 | 상시. 과거 13~17시 후보 철회. 240분은 참고 예산이며 코드 상한이 아니다 | 상시 시간대를 무한 grant/token 또는 자동 재시도로 해석하지 않는다. 개별 grant는 실제 유한 기간·대상·RUN/action을 명시 |
| 검토할 실행 범위 | 기존 두 앱6객체·승인 digest의 정상1·실제 Test/Scan 실패차단1·승인 previous 복원1·Health/Rule·앱0·보고 | 회차 수는 채택된 인수 범위다. 상태 dict가 횟수를 집행하지 않는다. 실제 실행별 정확한 grant/Binding 및 기존 claim/fence가 필요하며 임의 RUN 추가 권리는 없음 |
| control | 기존 `operating-handoff.md`의 검토 snapshot + 전용 ref | 아래 정확한 pin을 코드 선택. 무관 main 병합을 control 변경으로 읽지 않으며 자동 repin 없음 |
| native 운영 조립 | `assemble_observed_native_job` → 기존 `assemble_observed_job` → PreparedAuthorityPlan → 기존 native reader/materializer | FDS **deploy 전용** 경로다. 복원은 별도 승인된 render·grant와 기존 restore 경로가 필요하며 deploy profile을 자동 재사용하지 않음 |
| baseline | 메모리 raw tuple 또는 실제 BlobPin grant/root의 XOR 선택. 후자는 같은 source의 5번째 역할 reader로 원본·control을 인증하고 기존 archive parser 연결 | 실제 baseline/previous는 여전히 필수. 미래 raw·fake IssuerFacts·현재 GET·과거 snapshot으로 인계 후 기준선을 만들지 않음 |

## 실제 선택한 control

- 저장소: `321Team/infra-cicd`, repository ID `1362221024`, owner ID `315682664`.
- snapshot commit: `6531ba8e301858c0e180c377d06908f895158a70`.
- 파일: `docs/phase2/operating-handoff.md`.
- 파일 SHA256: `4bbac06d284a7d8c68ff867a5fb6f69ead4881ba56973bd7bcbb6f20c4e9311b`.
- 전용 ref: `heads/codex/p46-operating-control-20261025`, 조회한 head는 같은 commit.

실제 생성·조회한 **Git 코드 ref**다. IAM/RBAC/자격/Runtime 변경은 없다.
원래 인계 문서의 옛 상태 설명이 이 ref 선택만으로 실행 허가가 되지는 않는다.
실행 grant의 원문 의미는 검토된 SITE 코드가 별도로 채택하며, 기존 resolver가 매 호출
실제 원문/control/ref·Binding·기간·credential·progress를 다시 검증한다.

철회/변경은 지정 control ref 갱신과 운영자의 writer 중단·복구 인수로 처리한다.
원문/control 변경·삭제·조회 불가나 anchor 이동은 다음 검사에서 STOP이다.
다른 댓글에만 남긴 철회나 마지막 조회 직후의 원자적 취소는 보장하지 않는다.
control 문서 snapshot과 pin을 저장하는 SITE 코드는 분리해 자기 해시 순환을 피했다.

## 연결 계약과 실패 처리

`delivery_fds_operating_policy.selection_checked`는 현재 FDS profile, 본인 `gwonuk`,
선택된 control 5역할, 실제 유한 approval/finish 기간과 최종 정리 상한,
실제 baseline/previous 및 producer Test/Build/Scan/Push native pins의 대응을 대조한다.
이는 순수 구성 검사다. 코드 타입·해시·계정 이름이 원문 권위/실제 현장 성공을 증명하지 않는다.

`assemble_observed_native_job`의 필수 입력은 기존 config, 실제 native `JobContext`,
`NativePlanSelection`, finish scope/slot, peer/source/role routes, producer repository/pins,
wall/monotonic 및 deadline이다. 누락을 기본값·JSON·환경 설정으로 채우지 않는다.

baseline 전달은 다음 두 방식 중 하나다.

- 기존 방식: `NativePlanSelection.baseline`에 명시 채택한 6개 원본 tuple이 있다.
  `baseline_grant`·`baseline_root` 추가 인자는 둘 다 없어야 한다.
- 후행 원본 방식: `NativePlanSelection.baseline=None`이며 실제 `baseline_grant`와
  `baseline_root`가 둘 다 있다. grant의 original은 정확한 `BlobPin`이어야 한다.
  원본 sha는 이 pin에서만 가져오며 archive 내부 note·manifest를 권위로 읽지 않는다.

후행 방식은 issuer 원본 해석 뒤, 같은 소비된 source의 `roles['readers'][4]`를 사용한다.
기존 `CodeAdoptedGrantResolver`로 기간·같은 Binding/credential/request/deadline 및
원문/control을 인증하고, `connect_baseline_archive`가 digest/manifest/6UID/spec/previous를
검사한다. parser 종료 뒤에도 같은 기한을 확인한다. 역할 reader는 소스 세션에서 빌리며
내부 resolver만 close한다. archive 추출·저장·새 API capture·재시도·과거 snapshot fallback은 없다.
완성된 plan은 이후 기존 `wire.check_plan`, producer native 검증, fresh baseline/현재 RBAC,
claim/fence/finish 검증을 그대로 거친다.

1. 생성 시 파일·socket·clock·원본 reader·issuer를 호출하지 않는다.
2. 기존 UID1000/helper/READY·5역할 private source 전달을 그대로 조립한다.
3. 소비된 source의 prepared callback 안에서만 기존 `native_original_reader(binding)`와
   `NativeTokenMaterializer`를 연결한다. 같은 실제 context/credential/source/roles와
   **같거나 더 짧아진 handoff deadline**을 사용하며 예산을 다시 시작하지 않는다.
4. 기존 parser가 실제 TokenRequest 응답에서 iat/exp·수명·대상·token 일치를 대조한다.
   새 approval/producer/baseline을 생성하거나 이전 증적의 의미를 바꾸지 않는다.
5. 성공·실패·중단 모두 내부 materializer 참조를 close하고, 빌린 source/credential은
   기존 invocation 소유자가 회수한다. 실패한 prepared 진입도 단회 소비된다.
6. 이후 기존 profile/raw baseline·source/control·현재 API/RBAC·SSA·claim/fence·finish 검증을 거친다.

구성 채택 댓글6385867126은 수정 시각/본문 해시가 바뀌어도 실행 approval 원본으로
받지 않는다. 다른 미검토 원문을 임의로 권위화하는 parser나 승인 loader도 만들지 않았다.

## 시험·남은 실행 입력

오프라인 시험은 새 정책/조립 모듈과 기존 baseline/native materializer 회귀를 함께 실행한다.
fixture는 합성이라고 명시하고 socket/DNS·실제 issuer 호출을 금지한다. 실제 native-shaped
응답을 기존 reader→prepared→parser→RuntimeAuthorityPlan까지 1회 연결하되 실제 발급하지 않는다.

실제 grant의 원문/유한 기간·RUN/action, 인계 후 새 baseline, producer 원본 의미와 native
stage pins, finish scope, 실제 배치/자격 공급은 실제 값만 명시 연결한다. 이미 확인된
API/CA/OAuth/SSH/UID/source/render/digest를 다시 제출받지 않는다.

현재 `SITE=None`과 self-hosted dispatch 차단을 유지한다. 현 단계는 STATIC/MOCK/CI
후 코드 리뷰 대상이며 RUNTIME/AC/최종 인수 완료가 아니다. 최종 자원 정리·복구·반출·보고는
기존 **10/26 12:00 KST**를 유지한다. source/기준선이 부족하면 해당 단계만 미완료로 남기며
이미 채택된 범위/control/시간대를 다시 묻거나 새 ledger·팀 제출양식·단계별 GO를 만들지 않는다.

## 마지막 소비와 회수 비중첩

[하영 회수 계획6378716458](https://github.com/321Team/infra-rosa/issues/8#issuecomment-6378716458)은
10/26 08:30 앱0 확인, 08:35 SA 참조, 08:45 pull Secret/Robot 회수 착수를 제안한다.
이는 계획·담당자 운영 방식이며 실제 회수 완료나 새 실행 GO의 증거가 아니다.
PR142 두 리뷰어의 권고에 따라 **실제 approval/finish 만료 상한을 현재 소비 종료 경계
10/26 08:30 KST 이하**로 대조한다. 전체 자원 정리 상한 12:00은 그대로 남긴다.

상시 실행 가능 시간대를 다시 고정 시간대로 제한하는 결정은 아니다. 현재 쓰는 자원의
회수와 시험이 겹치지 않게 하는 의존 조건이며, 08:30 직전 착수나 앱0·claim 해제의
자동 보증도 아니다. 실제 착수 때 종료/복구 여유를 확보하고, 해제되지 않은 fence나
잔여 앱/세션이 있으면 회수 담당에게 상태를 인계한다. 회수 계획이 변경되면 그 근거를
확인해 이 선택도 갱신하며 계획 시각을 새 grant/자격 만료로 합성하지 않는다.
