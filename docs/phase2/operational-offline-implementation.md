# #46 운영 provider 비교·수명주기 오프라인 검토본

2026-10-15 KST · 이권욱 · CODEX_ASSISTED. 시작 main `fddc76e1c261cd938f06211b7ed035fdcb32e606`.
PR84의 [운영 연결 계약](operational-integration-contract.md)을 코드 경계와 합성 시험으로 확장한다.
**실제 승인/플랫폼/생산 원본을 인증하는 운영 adapter와 실제 배포 CLI는 미완성이다.**
아래 구현은 로컬 검토본이며 독립 리뷰·변경 CI·Runtime GO·#46 인수를 뜻하지 않는다.

10/16 추가: [객체 대조 helper와 합성 통합 시험](object-reconciliation.md)을 구현했다.
baseline/ACK/UID/spec/소유 필드 비교는 가능하지만 실제 `objects()` reader의 인증·freshness·deadline·admission 검증은 미구현이다.
기존 아래의 동적 객체 미구현 표기는 실제 운영 provider에 대한 설명이며, 이 순수 helper와 구분한다.

## 구현한 범위

| 파일 | 동작 |
|---|---|
| `delivery_verifier.py` | provider Protocol 및 내부 전용 evidence 타입, 승인 상태·창·provenance 고정, 같은 Binding/credential handle·fingerprint·CA 원본·namespace UID·endpoint·expiry, source/contract/render/checker pin·raw receipt hash 대조 |
| `delivery_session.py` | prepare → preflight → acquire/Journal → BoundAuthorizer → sender → Adapter 연결. 생성/기록/전송/complete/close 실패 처리, pending·fence 보존과 복구 인계 |
| `delivery_cli.py` | status/deploy/restore 모두 `OPERATIONAL_PROVIDER_UNCONFIGURED`, RC2. 실제 입력·token·kubeconfig·환경 변수·저장소·네트워크를 읽거나 생성하지 않는 fail-closed 경계 |
| `test_delivery_operational.py` | fake authority/provider/sender/clock/store contract harness 및 실제 POSIX store와 연결하는 별도 조건부 시험 |

기존 core·BoundAuthorizer·TLS Sender·store·recovery·producer bridge는 수정하지 않았다.
P1 파일·Workflow·Runner 설정·권한·AC 변경은 없다. 새 시험은 기존 P2 CI의 `test_*.py` 자동 탐색 대상이다.
기존 PR75/80의 승인과 CI는 이 새 코드의 승인·CI를 대신하지 않는다.

## 진위 검증과 대조의 경계

`ApprovalEvidence` 등은 **신뢰된 Python adapter의 내부 반환 타입**이다. 사용자가 입력할 승인 JSON 양식이 아니다.
타입/문자열/해시가 맞는 것만으로 실제 승인을 증명하지 않는다. 반환 객체를 만들어 주는 운영 구현이 아직 없다.
`UnconfiguredProviders`는 모든 조회를 거부하고, CLI에는 provider 선택·동적 import·credential 전달 옵션이 없다.
`approved=true`, JWT decode, 과거 PR 승인, 기존 intake PASS를 실행 허가로 변환하지 않는다.

- 승인: 현재 원본·revision·principal/authority reference와 ACTIVE 상태, exact run/action/Binding 및 창을 대조하고 실행 중 원본/창 변경을 거부한다. 원본 인증·권한 판정·철회 조회는 provider 책임이다.
- 플랫폼: 독립적으로 구성된 관측 Binding, **동일 credential 객체**, fingerprint, 요청900초와 실제 expiry, CA 원본/hash, 숫자 endpoint를 대조한다. issuer/audience의 존재 검사는 인증이 아니며, 지원되는 issuer/audience·최소 RBAC 검증과 DNS 출처 인증은 실제 provider가 수행해야 한다.
- 생산자: 같은 Binding/source/contract/render/checker pin, 원본 supply/platform receipt bytes의 hash를 대조한다. clean 생성·P2 current/previous 채택·Build/Scan/Push 진위는 provider 책임이다. `CommittedProducerBridge.render_generation=NOT_VERIFIED`를 승격하지 않는다.
- 동적 객체: durable journal snapshot과 관측된 PATCH 응답을 별도 전달한다. 각각의 SHA는 결속 용도다. 실제 UID/spec/image/field ownership 대조, 최초 baseline 및 자기 쓰기 후 변화의 허용 판정은 `objects()` provider의 **미구현 업무**다. harness는 전달 순서와 ACK/응답 대응만 검사한다.

각 read에는 해당 호출의 고유 request 객체와 하나의 절대 monotonic deadline을 전달한다. 다른 호출의 캐시 객체를 재사용하면 거부한다. 이는 네트워크 관측의 진위 증명이 아니며, 악의적인 Python provider를 격리하는 장치도 아니다. provider 코드는 독립 리뷰 대상 신뢰 경계다.

## 시간과 credential

`budget_seconds`는 신뢰된 통합 설정에서 명시적으로 받아야 한다. 시험의5초/300초는 합성 값이며 운영 수치나 새 AC가 아니다.
승인·credential 만료가 더 이르면 그 시점으로 읽기 deadline을 줄인다. 조회 전후와 결과 대조 후 시간을 검사하며 preflight 소요도 실제 잔여시간에서 차감한다.
10/16 리뷰5544978490 반영: platform은 같은 credential의 인증된 `issued_at`도 전달하며 `issued_at <= now < expires_at`, `0 < expires_at - issued_at <= 900`을 검증한다. 요청만900초이고 실제 발급수명이1800초인 토큰은 잔여가900초 이하가 되어도 거부한다. 임의60초 여유나 자동 refresh는 추가하지 않았다. 발급시각도 회차 중 고정한다. unverified JWT decode나 호출자가 임의로 채운 시각은 증거가 아니며 기존 provider의 issuer/audience·credential 인증 경로에서 읽는다. 새 수동 양식·별도 팀원 승인·토큰 재발급 절차를 요구하지 않는다.
wall clock이 정지해도 monotonic 경과로 수명이 늘어나지 않는다. 첫 PATCH의660초 가드는 기존 core를 재사용한다.

**동기 Python callback이 반환하지 않는 경우 이 비교층이 강제로 중단시키지는 못한다.**
운영 provider의 제한시간 있는 I/O·취소·종료 시험이 연결 전 필수다. post-call timeout 시험 통과를 무한 대기 차단으로 표시하지 않는다.
credential 파일·환경 변수·발급/갱신 방식은 선택하지 않았다. credential handle의 생성/폐기 책임은 앞으로 확정할 운영 계약에 남는다.
sender.close는 송신 경로 종료이며, 후속 읽기와 승인 재검증에 필요한 동일 credential을 다른 것으로 바꾸지 않는다.

## 종료와 복구

- session 함수는 한 번만 실행한다. 자동 재시도·refresh·replay·force apply·다른 root fallback이 없다.
- sender factory는 동일 handle/Binding/CA/fingerprint/endpoint를 확인한 `BoundSender`를 반환해야 한다. 실제 TLS Sender를 credential 전달 경로와 연결하는 factory는 미구현이다. 초기화 도중 실패한 factory는 자신이 연 자원을 닫아야 한다.
- `Progress.responses`는 최대6개의 PATCH 응답(각1MiB 제한)만 메모리에 보관한다. GET은 기존 core의 rollout 대조에 쓰이며 응답을 journal에 복사하지 않는다. 응답 관측과 durable ACK를 구분하므로 ACK 저장 실패는 pending으로 남는다.
- 기본 `finalize=False`이면 rollout 뒤에도 `REVIEW_REQUIRED`, fence 유지다. `finalize=True`는 완료 요청일 뿐 승인이 아니다. sender.close 성공, fresh 권한 재검증, provider의 별도 `CompletionEvidence`, 완료 확인 후 재검증과 claim/journal 재확인을 모두 거친 때에만 complete한다.
- `SESSION_COMPLETED`는 저장소 종료 기록을 뜻한다. Delivery/health/Pod imageID/업무 AC는 `NOT_VERIFIED`다.
- complete 실패는 일부 파일이 이미 바뀌었을 수 있으므로 `INSPECT_REQUIRED`다. close 실패도 전체 완료로 보고하지 않는다. complete 성공 후 close 실패라면 `completion=RECORDED`를 보존하고 fence의 실제 상태를 확인하도록 인계한다.
- finally는 열린 sender/journal/claim의 close를 시도한다. complete·삭제·잠금 강제 해제를 수행하지 않는다. BaseException 중단도 finally를 거치며 기록은 남는다. 프로세스 강제 종료/전원 상실 시험은 아니다.
- 기존 `delivery_recovery.inspect(root, binding)` / CLI로 읽기 진단한다. `WRITE_OUTCOME_UNCONFIRMED`, `INCOMPLETE_FENCE`, `TERMINAL_FENCE_RETAINED`는 자동 재개 권한이 아니다. 사람의 실제 target readback·원본 대조 후 별도 새 RUN/restore 승인과 제한된 fence 조정이 필요하며, 조정 도구는 만들지 않았다.

provider 예외의 원문·raw receipt·credential은 결과에 넣지 않는다. CLI 잘못된 인자도 그대로 출력하지 않는다.
traceback locals/객체 전체 repr·HTTP debug·환경 덤프를 운영 로깅에 사용하지 않는 기존 경계를 유지한다. 메모리 소거 보장은 아니다.

## 재현과 판정

실행 위치: 이 변경이 있는 infra-cicd 저장소 루트. Python3.10 이상 표준 라이브러리; 실제 확인 환경은 Windows/Python3.12.14/Git2.54.0이다.
일반 사용자 작업 PC에서 다음 명령을 실행한다. AWS/ROSA/Runner/DB·token·실제 네트워크가 필요하지 않다.

```text
python -m unittest discover -s tools/phase2 -p test_delivery_operational.py -v
python -m unittest discover -s tools/phase2 -p "test_*.py" -v
python tools/phase2/delivery_cli.py status
python tools/phase2/delivery_cli.py deploy
python tools/phase2/delivery_cli.py restore
```

정상 기준: unittest RC0, 실패/오류0, PASS와 SKIP 수 별도 기록. CLI 세 명령은 **예상 RC2**, `execution_authorized=false`, `sender_calls=0`, `claim_acquired=false`, `runtime=NOT_RUN`이다. RC0 배포 명령은 없다.
시험 실패 시 검토본 사용을 중단하고 로그·원본을 보존한다. 서버에 적용한 변경이 없으므로 서버 복구 대상은 없다. 로컬 새 파일만 검토 후 수정/제외하며 기존 코어/P1 설정을 되돌리지 않는다.

Windows에서 새 POSIX 통합3건과 기존 POSIX 시험은 SKIP이다. fake MemoryStore 시험은 디스크 내구성 검증이 아니다.
Linux에서 실제 store 시험을 실행할 때 저장소와 상위 경로는 root/현재 사용자 소유이며 group/other 쓰기 불가여야 한다. `/tmp`에서 실행하거나 store 보안 검사를 낮추지 않는다.
기존 선택적 bridge9건은 PyYAML6.0.3이 있어야 한다. 이번 변경은 parser/bridge 코드를 변경하지 않으며 패키지를 설치하지 않았다.

## 10/16 후속 객체 조회 구현

[객체 조회 adapter](object-reader.md)는 고정6객체의 bounded TLS GET·정확404분류·Progress/deadline 대조를 기존 ObjectReconciler에 연결한다. 실제 운영 입력/인증 provider·baseline admission·CLI는 미완성이며 Runtime는 미실행이다. 아래 객체 provider 잔여 중 조회 연결만 구현됐고 독립 검토·실제 원본 확인은 남는다.

## 연결 전 남은 담당·완료조건

| 담당 | 남은 작업 | 완료조건 |
|---|---|---|
| 권욱 #46 + 해당 단계 승인 주체 | 승인 원본 저장소·신원/권한/철회 조회 및 deadline 있는 provider | 실제 원본으로 정상/철회/개정/권한상실/무응답 검증, 독립 리뷰 |
| 하영 #38 + 권욱 소비 | 지원 credential 전달/수명/폐기, 플랫폼/endpoint/RBAC provider | 실제 동일 credential·API/UID/CA·expiry와 bound sender 연결. 현재 미생성 값을 요구하지 않음 |
| 재환 #43 + 권욱 소비 | P2 current/previous·clean generation/공급 receipt 인증 | 기존 원본과 exact source/render/digest/회차 결속. 재빌드·Push 중복 요구 없음 |
| 권욱 #46 | 동적 object readback/ownership 및 완료 판단 provider | baseline·자기 쓰기·타인 변경·pending 구분과 후속 관측 확인 |
| 권욱 #46 | 공유 POSIX 영구 root·실제 factory/CLI 연결·복구 조정 절차 | POSIX 새 통합시험, 독립 코드 리뷰·변경 CI 후 별도 승인 Runtime |

이 미결을 채우기 위해 실제 접속·token 발급·Deploy·P1 재실행을 수행하지 않았다.
