# #46 P2 배포 코어 — 독립 검토용 준비 구현

2026-10-13 KST · Lead 이권욱 · CODEX_ASSISTED.

`tools/phase2/delivery_adapter.py`는 검증된 입력을 받았을 때 **앱 6개 자원 적용 → 두 Deployment rollout 관측**을 수행하는 상태 기계다. 이번 변경에는 네트워크 구현, 실제 인증/권한 검증기, 운영 CLI/Workflow가 없다. 외부 함수를 주입하는 Python 라이브러리이며 현재 시험은 전부 MOCK이다. 기존 `delivery_intake.py`의 문자열 일관성 결과를 실행 허가로 연결하지 않는다.

## 기준과 Phase 1 경계

- 소비 계약: [#46 6116254976](https://github.com/321Team/fds-project-baseline/issues/46#issuecomment-6116254976), [#43 생산 계약](https://github.com/321Team/fds-msa/blob/6e7acf1053b1e44164d38851e477a2d2d64b9c3a/deploy/DELIVERY-CONTRACT-rosa.md), [기계 판독 계약](https://github.com/321Team/fds-msa/blob/6e7acf1053b1e44164d38851e477a2d2d64b9c3a/deploy/delivery/rosa.yaml).
- infra-cicd 시작 main: `2d8a14c55ea4b61f53b7ac6d682cd5e97575dda7`.
- fds-msa 조회 main: `6e7acf1053b1e44164d38851e477a2d2d64b9c3a`. 기계 판독 계약 blob `f670f6b968e2bfaf36119e6693b02f265d9017d6`은 기존 수용 merge `a7e62ffd0090112c1211a8cdfdd75ed38aba982b`와 동일하다. source main 전체가 같다는 뜻은 아니다.
- **namespace는 `fds-app`; `rosa-app`은 overlay 이름이다.** #46 댓글의 `rosa-app` namespace 표기와 차이가 있으므로 실행 인계에서 actual namespace UID와 함께 정합화해야 한다. 이 구현이 namespace를 새로 만들거나 변경하지 않는다.
- 기존 `tools/delivery/*`는 P1의 API/namespace/Registry 계약을 사용하므로 import·수정하지 않았다. Runner·Workflow·Harbor/CA·Robot·공용 OCP·DB·DNS·route·AC를 변경하지 않았다.

## 구현한 동작

| 단계 | 코어의 검사·동작 | 시험의 한계 |
|---|---|---|
| 입력 | source·raw contract/render SHA·platform/supply receipt SHA·cluster/API·namespace UID·CA·승인 host·두 이미지·run/action을 하나의 Binding으로 결속 | 해시는 무결성이다. 출처 진위·clean checkout·승인은 외부 검증기의 책임 |
| 렌더 | 주입한 생산자 검사기를 동일 raw bytes에 호출. app PASS, placeholder 허용=false, 계약/렌더 hash와 승인 host 일치 필요. 자원 누락/추가/중복 거부 | 저장된 PASS JSON만 읽는 함수를 연결하면 안 됨. 생산자 검사·파싱 bridge의 운영 연결은 잔여 |
| 범위 | `fds-app` Service2·Deployment2·Route2. SA/NP/Secret/권한·삭제 불가. 앱 이름·SA·이미지·onprem-egress 라벨·두 Route host/TLS 고정 | CI 경로의 제한이며 Kubernetes admission 통제가 아님 |
| 첫 쓰기 | 요청 TTL=900초, **실제 token 및 실행 승인 잔여시간 모두 660초 이상**인지 durable intent 저장 이후 재확인 | 실제 issuer/audience/TokenRequest·RBAC는 코어가 검증하거나 발급하지 않음 |
| 통신 요청 | 대상 origin·CA hash·credential fingerprint를 요청에 결속. Server-Side Apply PATCH, fieldManager=fds-deployer, 강제 덮어쓰기/자동 재시도 없음 | TLS·HTTP·자격증명 전달은 주입한 sender의 책임. 이 변경에 실제 sender 없음 |
| 중단 | 매 요청과 응답 처리 후 승인·만료 확인. credential/승인 교체, 시계 역행, 응답 지연, 거부, 상태 불명확, checkpoint 실패 시 다음 쓰기 중단 | 외부 함수의 실제 마감시간 강제·내구성은 추후 독립 검증 필요 |
| rollout | 두 Deployment 각 최대 300초. UID/generation/spec 불변, observedGeneration·replica 수 확인 | Pod imageID·Route/업무 health·DB/E2E·AC 성공은 별도 |
| 복구 | `action=restore`와 별도 승인된 이전 렌더·새 RUN의 Binding으로 동일 6개 적용 절차 사용 | 자동 rollback/rollout undo/삭제·데이터 복구 없음. 복구 입력·실행 허가는 별도 |

적용 요청은 Kubernetes [Server-Side Apply](https://kubernetes.io/docs/reference/using-api/server-side-apply/)의 PATCH·`application/apply-patch+yaml`·fieldManager 방식이다. JSON 객체 payload는 YAML의 부분집합으로 전달한다. field ownership 충돌을 강제로 덮어쓰지 않는다.

현재 계약의 Deployment별 앱 container 1개를 지원하며 init/ephemeral container를 추가로 허용하지 않는다. 향후 producer 계약이 변경되면 이 제한을 포함한 소비자 변경을 같은 범위로 검토한다. 렌더가 object 6개 범위를 벗어나면 권한을 넓혀 처리하지 않는다.

## 주입 함수의 필수 계약 — 아직 Runtime 연결 아님

1. **validator(raw_render, raw_contract)**: 고정·검토된 #43 `check_delivery_render.py`를 `mode=app`, `allow_placeholders=False`로 같은 bytes에 실행한다. `(report, parsed_documents)`를 반환한다. 원본 bytes/파싱 객체 결속, 승인 소스의 검사기 버전과 YAML parser 선택은 bridge가 보장해야 한다. 코어는 YAML dependency를 새로 설치하거나 저장소 코드를 동적으로 import하지 않는다.
2. **authorize(binding)**: 원본 승인·실행 창, ROSA identity/API/namespace UID/CA, 실제 issuer/audience/만료·최소 RBAC, clean source/렌더, 같은 회차의 플랫폼/Build/Scan/Push 증적을 검증한다. 현재 앱 객체의 UID/spec/image와 field ownership도 기존 실행 계약에 따라 대조해야 한다. 외부의 **영속 실행 claim/lock**을 유지하고 중단 RUN의 임의 재사용을 거부한 경우에만 `Lease`를 반환한다. 사용자 입력의 문자열/boolean을 그대로 Lease로 변환하면 안 된다. token 원문은 Lease에 넣지 않는다.
3. **send(Request)**: 같은 credential fingerprint의 자격증명으로 해당 origin/CA를 검증하고 고정 path에만 요청한다. 환경 kubeconfig/context·P1 API·프록시·redirect로 대체하지 않는다. request timeout을 TLS 연결·전송·응답 전체에 강제하고 응답 크기를 제한한다. `Rejected`는 변경이 없다고 확정된 응답에만 사용한다. timeout/5xx/부분 응답은 `Rejected`로 바꾸지 않는다. 이 코어는 callback 자체가 영원히 block하는 것을 중단시킬 수 없다.
4. **checkpoint(receipt)**: 비밀이 없는 최소 receipt를 원자적으로 내구성 있게 저장하고 성공 후에만 반환한다. 코어의 메모리 receipt를 영속 증적이라고 표시하지 않는다. `pending`은 저장한 쓰기 의도이며 crash 이후에는 전송 여부/실제 적용을 재조정해야 한다. `acknowledged`는 확인한 응답 수이지 전체 인수가 아니다.

`Binding.key`와 `Lease.binding_key` 일치는 위 외부 검증을 대체하지 않는다. 현재 MOCK은 합성 authorizer/sender/checkpoint를 주입하므로 **실제 IAM/RBAC·TLS·동시 실행 lock·디스크 내구성 검증이 아니다**. 운영 함수를 연결하려면 해당 구현까지 포함한 독립 코드 리뷰가 필요하다.

동일 Adapter 인스턴스는 재사용을 거부한다. 프로세스 재시작/새 인스턴스/다른 Runner의 중복 실행 방지는 외부 claim 저장소의 책임이며 이번 구현으로 완료되지 않았다. `ROLLOUT_OBSERVED`도 `delivery_gate/health_e2e/pod_image_id=NOT_VERIFIED`를 유지한다.

## 로컬 확인과 중단·복구

대상: 일반 사용자 권한의 작업 PC 또는 GitHub-hosted CI, Python 3 표준 라이브러리. 저장소 루트에서 다음 합성 시험을 실행한다. AWS/ROSA/Runner 접속·credential·kubectl이 필요 없다.

```text
python -m unittest discover -s tools/phase2 -p "test_*.py" -v
```

정상 판정은 unittest의 테스트 수·실패/오류/skip과 RC를 함께 확인한다. 새 adapter 시험과 기존 intake 회귀를 포함한다. 기존 P2 CI가 이 경로를 자동 탐색하므로 운영 Workflow 수정은 없다. 실패하면 코드/합성 입력을 수정하고 영향 시험을 재실행한다. 운영 원복 대상은 없다.

생산자 호환 시험은 별도 명령이다. 고정된 clean fds-msa checkout과 **시험용 Python 환경의 PyYAML 6.0.3**이 있을 때 실행한다. 서버 패키지를 설치하지 않는다. 검사기는 import 전에 위 producer HEAD와 tracked/untracked 변경 없음을 확인한다.

```text
python tools/phase2/verify_producer_compatibility.py --producer <fds-msa-checkout>
```

동일 생산자 검사기에 합성 승인 host/registry를 메모리로 주입한 정상 경로와 실제 저장소의 `PENDING` 계약 차단 경로를 확인한다. producer 파일을 변경하지 않으며 실제 값/승인을 만들지 않는다. 이 별도 시험은 표준 라이브러리만 사용하는 기본 CI 45건에 포함하지 않는다. 실행 결과 `MOCK_ONLY`, `actual_pending_sender_calls=0`, RC0을 확인하며 실제 ROSA 호환성으로 확대하지 않는다.

Runtime 연결 후에는 어떤 STOP도 자동 재시작·token 갱신·force apply의 근거가 아니다. 확인된 적용 목록과 pending intent를 실제 API 상태·field ownership·source 및 claim/checkpoint에 대조한 뒤 승인된 새 회차나 복구를 판단한다. 토큰이 만료되면 복구 쓰기도 하지 않는다. 이전 렌더에 없는 자원 정리는 이 코어의 범위 밖이다.

## 후속과 현재 단계

- 권욱: 실제 producer bridge, 원본/승인 검증·영속 claim/checkpoint·TLS sender 연결, Runner CA 사용 출처와 Build/Push 원본 결속. 코어 독립 리뷰 후에도 이 통합 구현의 리뷰/시험이 남는다.
- 하영: actual ROSA/API/namespace UID/CA/승인 host 및 플랫폼·권한 receipt.
- 재환: 승인 Registry/current·previous digest, clean source·app/prereq 렌더 결속과 복구 입력.
- 그 뒤 별도 허용된 RUN-A/RUN-B/복구 및 Pod imageID/health 증적을 검증한다. 이미 완료한 P1 Push 시험을 이 준비를 위해 반복하지 않는다.

STATIC/MOCK 결과만 기록하며 CI는 해당 PR의 실제 검사 결과로 별도 판단한다. 독립 REVIEW/MERGE/RUNTIME/정본 PUBLICATION·#46 종결은 이 문서 작성으로 완료되지 않는다. 실측 입력이 없는 동안에는 실제 권한·token 발급·Deploy를 시작하지 않는다.
