# P2 중단 RUN — 복구 검토 인계

2026-10-13 KST · 이권욱 · CODEX_ASSISTED. `delivery_store.py`와 코어의 **현재 구현을 읽어 정리한 준비 절차**다. 실제 서버 조회·active 해제·재개·복구를 실행하는 Runbook/CLI는 아니다. [배포 코어 계약](delivery-adapter.md)과 기존 #46 AC를 유지한다.

## 적용 범위와 현재 기술 제한

대상은 P2 `fds-app`의 단일 Runner 저장소와 해당 Binding이다. `rosa-app`은 overlay 이름이다. 기존 P1 코드·Runner 설정·공용 OCP·DB·DNS·IAM은 변경하지 않는다.

현재 `acquire()`는 active 파일이 있으면 `TARGET_RECONCILIATION_REQUIRED`로 중단한다. `Journal()`은 새 디렉터리만 만들며 기존 journal을 재개하지 않는다. `Claim.complete()`는 같은 살아 있는 claim/journal의 검증된 `ROLLOUT_OBSERVED`만 처리한다. 따라서 **프로세스 재시작 후 중단 RUN을 재구성해 해제하는 운영 기능은 없다**. PID 종료나 오래된 mtime은 해제 근거가 아니다.

이 제한은 준비 PR 병합 판단과 실제 운영 연결 판단을 구분해야 하는 항목이다. 기존 fence 보존은 실패 시 안전 동작이다. 운영 연결 전에는 아래 대조 결과를 소비하는 한정된 수동 조정 구현·권한·원본 보존 절차와 영향 시험/리뷰가 필요하다. 지금 파일 삭제 명령이나 강제 해제를 제공하지 않는다.

## 파일별 의미 — 원본을 유지하고 함께 대조

| 산출물 | 코드가 저장한 의미 | 이것만으로 알 수 없는 것 |
|---|---|---|
| `<target>.lock` | 검증된 cluster identity + namespace/UID에 대한 프로세스 mutex 파일 | 파일 존재만으로 실행 중/종료 판정 불가. PID/세션·Job 상태 별도 |
| `<run-key>.claim.json` | 대상/RUN 사용 이력과 binding key | 승인 진위·실제 API 적용·업무 성공. 정상 종료에도 영구 보존 |
| `<target>.active.json` | 미해결 대상 fence, binding key 및 claim 원본 hash | 살아 있는 프로세스 존재·권한 만료 여부. 자동 TTL 없음 |
| `<binding>.journal/0001.json…` | 최대64개, 순번·이전 원본 SHA·허용 receipt의 누적 기록 | 원본 출처·전체 수집 여부·실제 live 상태. hash 일치만으로 AC 인수 불가 |
| `<binding>.complete.json` | 정상 종료 경로가 저장한 마지막 checkpoint hash | health/E2E/imageID 성공, active 제거 완료, 전체 업무 종료 승인 |

원본 claim/active/journal/complete를 각각 다른 시점에서 모은 경우 일관된 snapshot으로 표시하지 않는다. 부분 파일·예상 밖 파일·누락 순번·hash 불일치도 삭제/보정하지 않고 그대로 보존한다. 원본 receipt에 없는 명령·정책 버전·실제 시각을 추정해 채우지 않는다.

## 상태별 복구 판단 준비표

| 수신한 마지막 기록/파일 상태 | 해석과 영향 | 다음 검토 / 재검증 조건 |
|---|---|---|
| claim만 있거나 journal이 없음/비어 있음 | claim/active/journal 생성 사이에 중단했을 수 있음. 파일 부재만으로 API 미전송 확정 불가 | 해당 실행 코드·호출 순서/Job 로그와 실제 대상 대조. 같은 RUN 재사용 불가 |
| `PREPARED` | durable 쓰기 의도 이전의 마지막 확인점 | snapshot 이후 쓰기가 있었는지 확인. 실제 상태와 원본 출처 미확인 시 fence 유지 |
| `WRITE_PENDING`, ack 없음 | 쓰기 의도는 기록됨. 전송 전·서버 적용 후 응답 유실·응답 후 ack 저장 실패 모두 가능 | 정확한 pending 객체 UID/spec/image/managedFields와 이후 변경 주체를 대조. 자동 PATCH 재전송 금지 |
| `WRITE_ACKNOWLEDGED` 일부/전부 | 해당 순서 응답을 확인했으나 뒤 객체/rollout/업무 완료는 보장 안 됨 | ack 목록과 pending·두 Deployment generation/replica·health/imageID를 각각 판단 |
| `STOPPED` 또는 checkpoint 오류/부분 tail | 오류 경로 종료 또는 durable 기록 실패. 메모리 receipt를 원본 성공으로 대체 불가 | 실패 코드·마지막 완전 기록·부분 파일 모두 보존. 새 RUN도 실제 대조/한정 해제 전 차단 |
| `ROLLOUT_OBSERVED`, active 남음 | 코어 rollout 관측 완료, 전체 독점 업무/complete 단계 이전 또는 실패 가능 | health/E2E/imageID 및 후속 업무와 claim 수명주기 확인. 재시작 후 현재 API로 이전 journal 재개 불가 |
| complete와 active가 함께 있음 | complete 저장 뒤 active 제거/fsync 사이 실패 가능 | complete의 마지막 기록 hash·snapshot 일관성·실제 상태 대조. marker만 보고 active를 지우지 않음 |
| complete 있고 active 없음 | 정상 종료 경로와 일치하는 후보 | 원본 결속·후속 업무·새 RUN 승인을 따로 확인. 같은 RUN claim은 계속 보존 |

위 표는 가능한 중단 지점의 해석이다. 실제 발생 원인이나 서버 변경 여부를 파일 상태만으로 확정하지 않는다. “ack 없음”은 “변경 없음”이 아니고, “완료 marker 있음”은 Delivery Gate PASS가 아니다.

## 실행 전에 준비할 대조 순서

1. **이권욱/#46:** 중복 실행을 막는 운영 제어와 단일 Runner/root 사용 여부를 먼저 확인하는 절차를 고정한다. 현재 claim/fence를 우회해 다른 root·다른 RUN을 만들지 않는다. 실제 실행 Job/프로세스 종료 여부는 승인된 운영 확인 범위에서 확보한다.
2. **원본 보유 실행자 → 이권욱:** Binding/source·RUN·대상과 해당 claim/active/journal/complete·Job 로그를 같은 회차로 인계한다. 공유·수신·재해시·내용 검토를 분리한다. 공개 GitHub에는 token/credential·Plan/state·비공개 raw를 싣지 않는다.
3. **이권욱 자체 검토:** 순번/이전 SHA·Binding·허용 receipt 전이·ack/pending/rollout 대응을 확인한다. 손상 시 원본 복구를 추측하지 않고 보류한다. 이 문서 작성에서 실제 원본이나 자동 journal 재검증 도구를 실행한 것은 아니다.
4. **플랫폼 이하영 + 앱/Registry 생산 이재환의 입력 소비:** 승인된 실제 조회창에서 cluster/API/namespace UID·객체 UID/spec/image·field ownership·rollout/health/현재·이전 digest 및 다른 변경 여부를 대조한다. 실제 조회 명령은 운영 통합 SHA와 인계된 대상에 한정해 검토한다. 이 문서가 조회 권한·작업창을 새로 승인하지 않는다.
5. **이권욱/#46 판단:** 정상 완료 확인, 별도 승인된 이전 렌더로 restore, 또는 불명확하여 계속 중단 중 하나를 근거와 함께 기록한다. restore는 새 RUN·새 Binding·유효 권한으로 한정하며 DB 데이터 원복을 포함하지 않는다. token 만료·actual 불일치면 복구 쓰기도 중단한다.
6. **운영 통합 구현 후 별도 리뷰:** 해제 대상이 대조한 exact target/binding인지, 다른 active 변경이 없는지, 원본·결정 기록의 내구성이 확보되는지 확인한 한정 조정만 허용한다. 원본 claim/journal 보존, 실패 시 fence 유지, 중복 조정/재실행 차단을 검증해야 한다. 이 단계의 도구/실제 실행은 현재 미구현·미실행이다.

현재 승인된 검증 최소권한은 결함 재시험·증적 반출/검토 완료까지 유지한다. file 전송/작업창 종료마다 반복 회수하지 않으며, 이 결정은 분리 정책 재부착이나 새 권한 부여를 허용하지 않는다. 검증 전용 권한의 종료와 필요한 운영 최소권한을 구분한다.

## 기존 시험 재사용과 남은 재검증

`test_delivery_integration.py`의 기존 정상/restore·HTTP409·timeout·권한 상실·ack 쓰기 실패·ack 전 예외 주입을 재사용한다. 이번 문서 보완 때문에 같은 MOCK을 다시 실행하지 않는다. 실제 프로세스 종료·전원 상실·운영 저장장치 내구성 인수를 했다는 뜻이 아니다.

수동 조정 코드가 생기면 **새 변경 부분**에 대해 active/Binding mismatch, live Job 충돌, snapshot 변경, partial/corrupt journal, complete+active, decision 기록 실패, 중복 조정, 새 RUN 진입의 실패/복구 경로를 검증한다. 현재 자동 해제/재개가 구현되었다고 표시하지 않는다. 기존 실패 fence를 없애는 우회 수정은 허용 범위 밖이다.

현재 판정: **복구 검토 절차 준비 / 운영 조정 통합 미완료 / Runtime NOT_RUN**. 문서 복구는 해당 커밋 revert이며 서버 원복과 별개다.
