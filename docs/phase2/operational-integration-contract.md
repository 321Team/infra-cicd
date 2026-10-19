# #46 운영 검증기·CLI 구현 전 인계

10/16 추가 후속: [TLS sender/session 결속](sender-binding.md)을 오프라인 라이브러리로 연결하고 [객체 조회 실패 진단](object-reader.md)을 보강했다. authenticated provider·운영 CLI는 계속 잔여이며 문서 아래의 초기 설계를 Runtime 완료로 읽지 않는다.

10/16 후속: [고정6객체 조회 adapter](object-reader.md)를 별도 검토본으로 구현했다. bounded TLS GET·404분류·진행 상태 대조가 추가됐으며, 아래 최초 설계의 객체 조회 미구현 부분에 해당한다. 승인/플랫폼 인증·baseline admission·완료 provider와 운영 배포 CLI는 계속 잔여다.

10/15 후속 로컬 검토본: [provider 비교·수명주기 오프라인 구현](operational-offline-implementation.md). 내부 타입/비교층·주입형 session과 fail-closed CLI 경계를 추가했으며 실제 승인/플랫폼/생산/object provider와 배포 CLI는 여전히 미구현이다. 아래 PR84 작성 당시 설계와 후속 구현·시험 범위를 구분한다.

2026-10-15 KST · 이권욱 · CODEX_ASSISTED. 기준 main `c373f1d7a8e6e84548180839098c34e9a1262eef`. 10/15 09:42 KST fetch 후 P2 경로 변경 없음을 재확인했다. `tools/phase2`와 `docs/phase2`는 PR80 HEAD `eeadfd256b5360fb414569e15f0e32c2efd9ea39`에서 변경 없음(git diff 확인). **현재는 인터페이스·실패 시험 설계 완료, 운영 구현·새 MOCK·Runtime 미실행**이다. 기존 [소비 계약](delivery-adapter.md), [생산자 수신](producer-intake-20261014.md), [읽기 전용 복구](recovery-review.md)를 확장하며 AC·권한·Workflow를 변경하지 않는다.

## 구현 전 실제로 필요한 인계

이미 제출한 자료를 다시 만들지 않고 아래 미결만 기존 receipt에 연결한다. 비밀값 대신 reference와 실제 검증 결과를 사용한다.

| 인계 | 생산 책임 | 소비 및 STOP 조건 |
|---|---|---|
| 플랫폼 receipt | 하영 #38: 실제 API origin/cluster identity/`fds-app` UID, CA 원본/hash, 승인 Route host, 지원 credential issuer/audience/권한 | 권욱: 승인 대상과 fresh 조회를 결속. `rosa-app` overlay를 namespace로 쓰거나 P1 identity 복사, UNKNOWN/권한 조회 실패는 STOP |
| 공급 receipt | 재환 #43: 앱별 P2 current 채택·previous-approved, source/build/scan/push 원본, clean render 생성 이력 | 권욱: pinned checker/contract/raw bytes 및 app별 digest 연결. 해시 일치만으로 clean 생성·P2 채택을 추정하지 않음 |
| 승인 원본 | 해당 단계 Lead: exact RUN/action/target/source/render/receipt/image와 창 | 권욱: 원문 버전·주체·권한·철회/대체 상태 확인. 로컬 `approved=true`, 과거 PR 승인·만료된 창은 실행 승인으로 변환하지 않음 |
| 운영 저장 경로 | #46 권욱: 한 Runner의 모든 P2 job이 쓰는 POSIX 영구 root·소유·접근 범위 | 기존 store의 0700/0600·no-follow·fsync 요구를 검증. 임시 root/서로 다른 root로 잠금 우회 금지. 이 준비로 Runner를 변경하지 않음 |
| credential 전달 | 하영 지원 방식 + 권욱 소비 연결: 승인된 실제 credential, 만료·갱신/폐기 책임 | token 원문을 CLI 인자/JSON/journal에 넣지 않음. 실제 전달 방식 미확정이므로 환경변수·파일경로를 임의로 운영 표준으로 선택하지 않음 |

## 기존 API에 연결할 인터페이스

다음 이름 중 `verify` 및 각 조회 provider는 **미구현 설계**다. 문자열 선언만 검사하는 `delivery_intake.evaluate()`를 provider로 사용하지 않는다.

| 경계 | 입력 → 출력 | 구현 책임 / 실패 |
|---|---|---|
| 승인 수신 provider | 원본 reference + Binding → 인증된 승인 Binding·expiry | 현재 원본/권한/버전/창을 읽는 방식 확정 후 구현. provenance 실패·조회 timeout·철회면 실패 |
| 플랫폼/credential provider | 같은 credential handle + 승인 target → 실제 identity/UID/CA/RBAC·expiry/fingerprint | token을 decode한 claim만으로 진위를 인정하지 않음. 신뢰된 지원 방식으로 확인; 다른 credential로 읽은 권한을 sender에 적용하지 않음 |
| 공급 provider | source/contract/render 원본과 생산 receipt → 검증된 동일 Binding 필드 | clean checkout·고정 도구/입력으로 생성됐는지 원본 연결. `CommittedProducerBridge`의 `render_generation=NOT_VERIFIED`를 자동 승격하지 않음 |
| `verify(binding)` | 위 fresh 결과 + 현재 claim/journal → `VerifiedInputs(approved, observed, lease)` | `BoundAuthorizer`가 이미 구현돼 있다. 반환값의 Binding/Lease는 같은 RUN 동안 고정, 관측 timestamp·동적 object 상태는 별도 증적으로 관리 |
| 배포 CLI | 실제 검증된 입력·선택된 저장소 → Adapter 단일 실행·비밀 없는 결과 | CLI 진입점은 미구현. 명시적으로 검증한 origin/root만 사용; ambient kubeconfig·P1 fallback·auto retry 금지 |

provider의 읽기 작업에도 deadline을 적용하고 그 경과 시간을 실제 token/승인 잔여에서 차감해야 한다. Adapter의 요청 timeout은 callback 바깥의 무한 대기를 중단시키지 못한다. deadline의 구체 수치는 플랫폼 계약과 실행 예산에 맞춰 구현 때 확정하며 임의로 새 AC를 만들지 않는다.

## 실행 수명주기와 복구 연결

1. **쓰기 전:** 승인 원본과 producer/platform receipt를 대조하고 `Binding`을 구성한다. `core.prepare(binding, raw_render, raw_contract, bridge)`로 6개 자원 범위를 확인한다. 이것은 저장소 claim 획득·네트워크 허가를 대신하지 않는다.
2. **독점 확인:** `acquire(root, binding)` → `Journal(claim)`. 이후 검증기는 해당 claim/journal을 매 호출 대조한다. 이전 active 또는 중복 RUN이 있으면 새 저장 경로로 우회하지 않는다.
3. **송신 연결:** 실제 동일 credential/CA/검증된 endpoint로 `Sender`를 구성하고, `BoundAuthorizer(verify=..., claim=claim, journal=journal)`와 `Adapter(authorize=..., send=sender, checkpoint=journal)`를 연결한다. 생성자 실행만으로 통신이나 인증 완료가 된 것은 아니다.
4. **단일 실행:** `Adapter.run(binding, raw_render, raw_contract, bridge)`. 첫 PATCH 직전 durable intent 저장 후 실제 token·승인 잔여 각각 660초 이상, 요청 TTL900 조건은 기존 코어 그대로다. 중간 갱신·자격증명 교체·추가 권한으로 계속하지 않는다.
5. **동적 객체 대조:** 적용 전 UID/spec/image/field ownership과 승인 렌더를 비교한다. 적용 후에는 성공한 응답과 journal 순서에 따른 예상 변화만 허용한다. 매번 “변경 전 spec과 같음”을 요구하면 정상 자기 쓰기도 거부된다. 반대로 pending 중단을 성공으로 가정하면 안 된다. 이 관측·receipt 연결 로직은 아직 미구현이고 별도 검토 대상이다.
6. **성공 후보:** `ROLLOUT_OBSERVED`와 정상 journal만으로 Delivery/업무 AC가 PASS는 아니다. 필요한 후속 관측·독점 접근 종료가 확인될 때만 `claim.complete(journal)`을 호출한다. 외부 health/Pod imageID/업무 결과는 기존 인수 계약으로 별도 연결한다.
7. **실패/예외:** journal 초기화·sender 생성·run·checkpoint·complete 어느 단계든 중단 시 원본과 active를 보존한다. `finally`에서는 열린 journal/claim의 `close()`만 수행하고 complete/삭제/잠금 해제를 호출하지 않는다. Python 메모리의 token 참조 수명 최소화는 보장된 메모리 소거와 다르며, 작업창 종료만으로 기존 검증권한을 반복 회수하지 않는다.
8. **재시작/복구:** 기존 `delivery_recovery.inspect()`는 읽기 진단이다. `WRITE_OUTCOME_UNCONFIRMED`, `INCOMPLETE_FENCE`, `TERMINAL_FENCE_RETAINED` 등은 자동 재개 권한이 아니다. 권욱이 exact target readback·기존 원본과 대조한 뒤 새 RUN/restore 승인 및 필요한 수동 fence 조정을 판단한다. 조정 도구는 아직 없으며 임의 파일 삭제로 구현하지 않는다.

restore는 앱별 previous-approved 렌더와 별도 action/승인을 사용한다. 새 승인/새 RUN만 갖추었다고 기존 active 충돌을 우회할 수는 없다. 먼저 기존 회차를 검토해야 한다.

## 통합 MOCK의 다음 검증 범위

이번에는 문서만 바꿨으므로 기존 135건을 다시 돌리지 않았다. 아래는 **새 통합 구현 이후의 시험 설계**이며 PASS 결과가 아니다. 동일 코어 시험은 재사용하고 provider 연결과 프로세스 수명주기만 추가한다. 시험은 합성 provider·fake sender/clock·격리 POSIX 임시 저장소에서 수행하며 실제 AWS/ROSA/DNS/token 발급은 호출하지 않는다.

| 새 통합 시험 | 예상 관측 / 완료조건 | 재사용하는 기존 시험 |
|---|---|---|
| 실제 인계 누락/PENDING/승인 원본 조회 거부 | sender 호출0, 실행 허가 생성0, 비밀없는 실패 코드 | `test_declared_json_or_bare_lease_cannot_authorize` |
| 승인 철회·원문 개정·provider timeout | 다음 PATCH0, active/기존 journal 보존 | `test_provider_failure_and_invalid_lease_fail_closed` |
| namespace UID/CA/API/credential가 fresh 조회에서 변경 | sender 호출 전 실패; 예상값 자동 갱신 없음 | `test_every_approval_and_observation_binding_field_must_match` |
| clean render 출처 누락/생산 receipt 다른 회차 | core에 진위 미검증 Binding 전달 안 함 | bridge 기존 hash/placeholder 검사 |
| 검증 소요로 첫 쓰기 예산 부족 | durable intent 후에도 PATCH0; 남은 기록 보존 | `test_lifetime_rechecked_after_durable_intent` |
| claim 획득 뒤 Journal/Sender 초기화 실패 | close 수행, active 보존, complete0 | store 기존 충돌·fsync 실패 검사 |
| 정상6 PATCH와 자기 쓰기 후 관측 변화 | 같은 binding/lease 유지, 승인 변화만 허용 | `test_six_scoped_applies_and_two_rollouts_are_not_delivery_acceptance` |
| PATCH 결과 불명/ACK 저장 실패 후 새 프로세스 | 자동 replay0, original pending/fence 유지 | `test_pending_is_unknown_even_when_not_acknowledged` |
| rollout 성공 뒤 complete 실패 | 완료라고 보고하지 않고 실제 journal/fence 상태를 읽기 인계 | `test_partial_complete_keeps_fence_for_manual_review` |
| restore/new RUN과 기존 미해결 active | 새 RUN이라도 차단, exact reconciliation 이후 별도 검토 | `test_live_mutex_and_interrupted_fence_block_other_runs` |

추가 완료조건은 실제 provider/CLI 코드의 독립 리뷰와 해당 변경 CI, 이후 별도 승인 Runtime에서 플랫폼·권한·수명주기 검증이다. 문서 작성자를 독립 승인자로 기록하지 않는다. Phase1 DB/Harbor/CA/Runner/공용 OCP/방화벽/DNS/LB/AC 변경은 없다.
