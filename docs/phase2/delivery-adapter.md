# #46 P2 배포 코어 — 독립 검토용 준비 구현

10/15 후속 로컬 검토본: [운영 provider 비교·session 수명주기·차단 CLI](operational-offline-implementation.md)를 추가했다. 기존 코어를 주입형 harness에서 연결한 단계이며, 아래에 미구현으로 표시한 실제 승인/플랫폼/생산 원본 인증과 운영 배포 연결은 여전히 잔여다.

2026-10-13 KST · Lead 이권욱 · CODEX_ASSISTED.

`tools/phase2/delivery_adapter.py`는 검증된 입력을 받았을 때 **앱 6개 자원 적용 → 두 Deployment rollout 관측**을 수행하는 상태 기계다. `delivery_store.py`는 POSIX 단일 Runner의 실행 claim·불변 journal, `delivery_transport.py`는 CA·hostname 검증과 전체 I/O 제한시간을 적용하는 TLS sender 라이브러리다. `producer_bridge.py`는 지정 커밋의 계약·검사기 원본을 읽어 같은 raw 렌더를 검사한다. `delivery_recovery.py`는 재시작 후 기존 journal을 읽어 검증하는 오프라인 CLI다. 실제 인증/권한·승인 검증기, 배포 CLI/Workflow와 모듈의 실제 연결은 없다. 배포 및 socket 시험은 MOCK이다. 기존 `delivery_intake.py`의 문자열 일관성 결과를 실행 허가로 연결하지 않는다.

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
| 렌더 | 지정 source commit의 검토된 검사기와 계약 원본을 동일 raw bytes에 결속. app PASS, placeholder 허용=false, 계약/렌더 hash와 승인 host 일치 필요. 자원 누락/추가/중복 거부 | 저장된 PASS JSON은 소비하지 않음. 렌더가 해당 clean source에서 생성됐는지·승인 진위·운영 연결은 잔여 |
| 범위 | `fds-app` Service2·Deployment2·Route2. SA/NP/Secret/권한·삭제 불가. 앱 이름·SA·이미지·onprem-egress 라벨·두 Route host/TLS 고정 | CI 경로의 제한이며 Kubernetes admission 통제가 아님 |
| 첫 쓰기 | 요청 TTL=900초, **실제 token 및 실행 승인 잔여시간 모두 660초 이상**인지 durable intent 저장 이후 재확인 | 실제 issuer/audience/TokenRequest·RBAC는 코어가 검증하거나 발급하지 않음 |
| 통신 요청 | 대상 origin·CA hash·credential fingerprint를 요청에 결속. Server-Side Apply PATCH, fieldManager=fds-deployer, 강제 덮어쓰기/자동 재시도 없음 | TLS sender 라이브러리와 socket MOCK 추가. actual API/CA/credential 검증·연결은 미실행 |
| 중단 | 매 요청과 응답 처리 후 승인·만료 확인. credential/승인 교체, 시계 역행, 응답 지연, 거부, 상태 불명확, checkpoint 실패 시 다음 쓰기 중단 | 외부 함수의 실제 마감시간 강제·내구성은 추후 독립 검증 필요 |
| rollout | 두 Deployment 각 최대 300초. UID/generation/spec 불변, observedGeneration·replica 수 확인 | Pod imageID·Route/업무 health·DB/E2E·AC 성공은 별도 |
| 복구 | `action=restore`와 별도 승인된 이전 렌더·새 RUN의 Binding으로 동일 6개 적용 절차 사용 | 자동 rollback/rollout undo/삭제·데이터 복구 없음. 복구 입력·실행 허가는 별도 |

적용 요청은 Kubernetes [Server-Side Apply](https://kubernetes.io/docs/reference/using-api/server-side-apply/)의 PATCH·`application/apply-patch+yaml`·fieldManager 방식이다. JSON 객체 payload는 YAML의 부분집합으로 전달한다. field ownership 충돌을 강제로 덮어쓰지 않는다.

현재 계약의 Deployment별 앱 container 1개를 지원하며 init/ephemeral container를 추가로 허용하지 않는다. 향후 producer 계약이 변경되면 이 제한을 포함한 소비자 변경을 같은 범위로 검토한다. 렌더가 object 6개 범위를 벗어나면 권한을 넓혀 처리하지 않는다.

## 주입 함수의 필수 계약 — 아직 Runtime 연결 아님

10/15 추가: 아래 계약을 실제 기존 함수와 연결한 [운영 검증기·CLI 구현 전 인계](operational-integration-contract.md)를 준비했다. 이 문서는 인터페이스·시험 설계이며 verifier/배포 CLI 구현 또는 실행 승인이 아니다. 기존 helper/TLS/store/recovery 구현과 시험은 재사용한다.

10/14 후속 구현: `delivery_authorization.BoundAuthorizer`는 신뢰된 verifier가 매 호출 반환하는 `VerifiedInputs`의 승인 Binding·실제 대조 Binding·Lease를 요청 Binding 및 동일 claim/journal에 대조한다. 검증 전후 claim/journal을 검사하고 승인/관측 불일치·만료·묵시 갱신·provider 실패에서 차단한다. 이 타입은 외부 JSON 스키마가 아니며, `approved=true`나 기존 PASS 파일을 실제 승인으로 변환하지 않는다. 실제 승인 진위·fresh identity/UID/CA·RBAC·clean render·증적 원본을 확인하는 verifier 및 운영 연결은 여전히 미구현이다. 이 helper의 MOCK 성공을 실제 승인 검증 완료로 표시하지 않는다.

Service는 생산자 검사기와 별도로 코어와 sender에서 `ClusterIP`(생략 시 기본값)만 허용한다. NodePort/LoadBalancer/ExternalName, 비어 있지 않은 externalIPs, nodePort 및 LB 전용 필드를 거부한다. 응답에 외부 IP/LB ingress가 남아 있으면 pending을 보존하고 중단하며 자동 재시도/정리를 하지 않는다. 지정 Route 2개와 TLS 계약은 기존대로다.

1. **validator(raw_render, raw_contract)**: 고정·검토된 #43 `check_delivery_render.py`를 `mode=app`, `allow_placeholders=False`로 같은 bytes에 실행한다. `(report, parsed_documents)`를 반환한다. 원본 bytes/파싱 객체 결속, 승인 소스의 검사기 버전과 YAML parser 선택은 bridge가 보장해야 한다. 코어는 YAML dependency를 새로 설치하거나 저장소 코드를 동적으로 import하지 않는다.
2. **authorize(binding)**: 원본 승인·실행 창, ROSA identity/API/namespace UID/CA, 실제 issuer/audience/만료·최소 RBAC, clean source/렌더, 같은 회차의 플랫폼/Build/Scan/Push 증적을 검증한다. 현재 앱 객체의 UID/spec/image와 field ownership도 기존 실행 계약에 따라 대조해야 한다. 외부의 **영속 실행 claim/lock**을 유지하고 중단 RUN의 임의 재사용을 거부한 경우에만 `Lease`를 반환한다. 사용자 입력의 문자열/boolean을 그대로 Lease로 변환하면 안 된다. token 원문은 Lease에 넣지 않는다.
3. **send(Request)**: 같은 credential fingerprint의 자격증명으로 해당 origin/CA를 검증하고 고정 path에만 요청한다. 환경 kubeconfig/context·P1 API·프록시·redirect로 대체하지 않는다. request timeout을 TLS 연결·전송·응답 전체에 강제하고 응답 크기를 제한한다. `Rejected`는 변경이 없다고 확정된 응답에만 사용한다. timeout/5xx/부분 응답은 `Rejected`로 바꾸지 않는다. 이 코어는 callback 자체가 영원히 block하는 것을 중단시킬 수 없다.
4. **checkpoint(receipt)**: 비밀이 없는 최소 receipt를 원자적으로 내구성 있게 저장하고 성공 후에만 반환한다. 코어의 메모리 receipt를 영속 증적이라고 표시하지 않는다. `pending`은 저장한 쓰기 의도이며 crash 이후에는 전송 여부/실제 적용을 재조정해야 한다. `acknowledged`는 확인한 응답 수이지 전체 인수가 아니다.

`Binding.key`와 `Lease.binding_key` 일치는 위 외부 검증을 대체하지 않는다. 배포 MOCK은 합성 authorizer/sender를 주입하므로 **실제 IAM/RBAC·TLS 검증이 아니다**. POSIX store 시험은 CI 임시 로컬 경로의 실제 파일 연산·실패 주입이며 운영 Runner 저장장치의 내구성 인수가 아니다. 운영 함수를 연결하려면 해당 구현까지 포함한 독립 코드 리뷰가 필요하다.

동일 Adapter 인스턴스는 재사용을 거부한다. 프로세스 재시작/새 인스턴스의 중복 실행에는 아래 store 연결이 필요하며, 다른 Runner를 포함한 분산 잠금은 구현하지 않았다. `ROLLOUT_OBSERVED`도 `delivery_gate/health_e2e/pod_image_id=NOT_VERIFIED`를 유지한다.

## 생산자 원본 bridge — 원본 결속과 승인 검증의 경계

`CommittedProducerBridge(producer_repository, binding)`을 Adapter의 validator로 주입할 수 있다. 설치된 Git/Python과 소비자 Python 환경은 신뢰된 준비 환경이어야 한다.

- full source commit의 `tools/check_delivery_render.py`와 `deploy/delivery/rosa.yaml` 두 Git 객체만 읽는다. working tree·HEAD 변경, Git replace ref, ambient GIT_DIR를 원본으로 소비하지 않는다. Git protocol·lazy fetch를 금지하여 local object가 없으면 차단한다.
- 저장소 읽기 전에 실제 Git 바이너리의 `--no-replace-objects --no-lazy-fetch --version`을 실행해 옵션 지원을 검사한다. 실패/timeout/미설치면 `GIT_LOCAL_ONLY_UNAVAILABLE`로 차단하고 옵션을 빼서 재시도하지 않는다. Git 2.43 미지원은 PR75 리뷰에서 보고됐다. 배포판 backport도 있어 버전 문자열만으로 지원을 추정하지 않는다. 준비 PC/후속 실행 환경에서 기능 검사 성공이 필요하며 이번 변경으로 Runner Git을 설치·갱신하지 않는다.
- 실행할 검사기는 fds-msa `6e7acf1053b1e44164d38851e477a2d2d64b9c3a`의 원본 SHA256 `b889d6123fe0d566424a17ab88a6e41e49696bb1bb572b16ed88f22a654df2e1`에 고정했다. 다른 검사기 코드는 새 소스 검토·소비자 변경 없이 실행하지 않는다.
- 전달 계약은 해당 commit의 계약과 일치해야 한다. CRLF/LF 차이만 허용하며 receipt hash는 전달된 원본 bytes 기준으로 유지한다. 승인 host를 Handoff 2 후 PR로 기록한다는 생산 계약 §4의 조건을 따른다. 현재 저장소의 `PENDING` 값을 메모리에서 운영 승인값으로 덮어쓰지 않는다.
- 입력당 1MiB와 YAML 깊이/토큰 수를 제한하고, 중복/non-string key·alias/anchor/tag/merge·비 JSON scalar·추가/누락 문서를 먼저 거부한다. 이후 검토된 원본 checker를 `app`, `allow_placeholders=False`로 다시 실행한다. 오류에는 YAML 원문·생산자 상세 오류·Git stderr를 노출하지 않는다.
- 소비자 parser는 별도 시험한 **PyYAML 6.0.3**에 고정한다. 생산자의 현재 source CI는 6.0.2를 사용하므로 동일 환경이라고 주장하지 않는다. 6.0.3에서 실제 checker의 합성 정상/PENDING 차단 호환성을 별도 확인했다. 버전 불일치/미설치 시 bridge는 차단한다.
- 이 기능은 두 원본 파일·raw 입력의 결속이다. **렌더 생성 이력과 source 승인 진위를 증명하지 않는다.** 결과도 `render_generation=NOT_VERIFIED`, `source_approval=NOT_VERIFIED`로 남긴다. 이 항목은 authorize가 실제 clean source/build·전달 회차·승인 증적에 연결해야 한다.

## POSIX 실행 저장소 — 준비 구현, 운영 경로 미연결

`delivery_store.py`는 P1 모듈을 import하지 않는다. Linux의 `flock`, `O_EXCL`, `O_NOFOLLOW`, 파일/디렉터리 `fsync`를 사용하며 Windows에서는 경로를 건드리기 전에 거부한다.

- 실제 연결 전, **한 Runner의 모든 P2 Job이 공유하는 하나의 영구 root**를 운영 담당자가 지정해야 한다. caller 소유 0700 디렉터리, 0600 일반 파일·단일 hardlink, root/caller 소유이고 group/other 쓰기가 없는 상위 경로를 요구한다. `/tmp`, symlink 경로·파일과 per-RUN 임시 root는 실행 저장소로 사용하지 않는다. 원본 준비·권한 검증 후 claim을 획득한다.
- 잠금 대상은 검증된 cluster identity + namespace/UID다. API 별칭 변경으로 같은 대상의 잠금을 우회하지 않는다. 비차단 `flock` 충돌이면 실행하지 않는다.
- 같은 대상/RUN은 렌더·action을 바꾸어도 재사용할 수 없다. claim 파일은 영구 보존한다. 원본 bytes·inode·권한과 journal의 파일 목록/각 레코드를 재검사한다.
- active 표시는 프로세스가 종료돼도 남는다. 중단·부분 기록·불명확한 쓰기 이후 다른 RUN도 자동 시작하지 못한다. 오래된 파일의 자동 삭제/TTL 탈취/강제 재개는 없다. **이 파일 존재는 권한 유지/회수 결정과 별개다.**
- journal은 허용된 receipt 필드·고정 오류코드만 받아 순번·이전 레코드 SHA로 연결한다. `PREPARED → WRITE_PENDING → WRITE_ACKNOWLEDGED → rollout → ROLLOUT_OBSERVED` 순서와 STOP을 검증한다. 원본 token/환경/서버 응답을 저장하지 않는다. 기록 실패 파일은 보존하고 writer를 영구 실패 상태로 둔다.
- 정상 종료 시 caller가 전체 업무의 독점 접근 필요가 끝났다고 판단한 뒤 `claim.complete(journal)`을 명시적으로 호출할 수 있다. 전체 적용/rollout의 내구성 있는 terminal 기록이 있을 때만 완료 marker를 보존하고 active 표시를 제거한다. STOP·결과 불명확은 이 경로로 해제할 수 없다. 실제 상태를 대조한 사람의 복구 절차는 Runtime 연결 전 잔여다.
- `close()`는 descriptor/프로세스 mutex만 해제하며 claim·미해결 active·원본 journal을 지우지 않는다. 같은 기록을 복구 자료로 재사용한다.

연결 순서: raw 입력 검사/승인 → `acquire(root, binding)` → `Journal(claim)` → authorizer에서 `claim.check()`/`journal.check()`와 실제 권한 확인 → Adapter에 journal callback 주입 → 결과 대조 → 필요 업무 종료 후 정상 complete → journal/claim close. 예외에서는 기록과 active를 보존한다. 실제 root 지정·credential/권한 검증·동일 Runner 제한·정리/수동 복구 통합은 미실행이다.

## TLS sender — 라이브러리 구현, 접속 미실행

`Sender(binding, ca_pem=..., token=..., endpoint_ip=...)`를 검증된 실행 통합 코드만 생성하고 Adapter의 `send`에 연결한다. CLI·환경 변수 기반 token·kubeconfig 탐색·자체 credential 발급은 없다. 실제 입력은 파일/명령행/로그에 복사하지 않고 기존 승인된 전달·검증 경로에서 소비해야 한다.

- CA 원본 hash와 token fingerprint를 Binding/Request와 대조한다. 고정 CA만 넣은 `SSLContext(PROTOCOL_TLS_CLIENT)`로 인증서 체인·API DNS hostname을 검증하고 TLS 1.2 이상을 사용한다. 시스템 기본 trust·환경 `SSLKEYLOGFILE`·HTTP proxy를 읽지 않는다. 검증 실패 시 인증 헤더를 보내지 않는다.
- `endpoint_ip`는 승인 준비 단계에서 실제 API DNS를 해석·검증한 숫자 주소 한 개다. 이 모듈은 DNS를 새로 해석하지 않아 이름 조회가 요청 제한시간을 넘기는 경로를 피한다. endpoint 주소와 해석 시각/출처를 실제 대상 검증에 연결하는 것은 후속 통합 책임이다. IP를 TLS hostname으로 대신 쓰거나 영구 hosts를 수정하지 않는다. 주소 변경은 자동 재시도 사유가 아니다.
- socket을 nonblocking으로 사용하고 connect·TLS handshake·부분 send·응답 읽기 전체에 동일 monotonic deadline(최대10초)을 전달한다. 응답이 조금씩 도착해도 제한시간을 초기화하지 않는다. HTTP 파싱은 표준 `http.client.HTTPResponse`를 사용하고 응답 body 1MiB, wire 1MiB+64KiB를 제한한다.
- 같은 6개 객체의 PATCH와 두 Deployment GET만 수용한다. 고정 Host/SNI·Content-Type·fieldManager를 사용한다. redirect·proxy·재연결·재시도·다음 IP fallback은 없다. 응답은 JSON 객체, 중복 key/NaN 금지, 모순된 length/encoding 거부다.
- HTTP 오류는 4xx를 포함해 코어에 **결과 불명확**으로 전달하고 pending을 보존한다. 현재 모듈은 서버 Status를 근거로 비변경을 확정하는 `Rejected` 분류를 구현하지 않았다. 더 보수적으로 중단하는 것으로, 자동 복구/재시도를 허용하지 않는다.
- 정적 검토와 주입 socket MOCK은 실제 TLS handshake 성공, 실제 CA의 적합성·token 진위·RBAC 또는 운영 DNS/ROSA 접속 성공을 증명하지 않는다. 실제 대상 없이 준비한 단계에서는 이를 미실행으로 유지한다.

기술 근거: Python 공식 [SSLContext 및 nonblocking TLS](https://docs.python.org/3.12/library/ssl.html), [HTTPResponse](https://docs.python.org/3.12/library/http.client.html), [selectors](https://docs.python.org/3.12/library/selectors.html). 모듈 구성만으로 승인·권한의 진위를 검증했다고 해석하지 않는다.

## 로컬 확인과 중단·복구

Linux store/연결 시험은 `/tmp` 아래에서 실행하지 않는다. 저장소 checkout 및 상위 경로가 root/현재 사용자 소유이고 group/other 쓰기 불가인 작업 디렉터리에서 실행한다. 시험은 그 아래 임시 0700 경로를 쓰지만 운영에서는 모든 P2 Job이 공유하는 영속 root가 필요하다. `ROOT_PARENT_UNSAFE`이면 실행 위치/소유·권한을 확인하며 보안 검사를 완화하지 않는다.

민감정보 로깅 경계: token은 sender 및 전송 중 지역변수에 존재한다. `raise ... from None`은 지역변수를 지우지 않는다. 운영 통합에서 traceback 지역변수 수집(`capture_locals=True`), 객체 전체 repr/vars, HTTP wire/debug, 환경 덤프 또는 Sentry류의 local-variable 수집을 활성화하지 않는다. receipt의 허용 필드·정적 오류코드만 기록한다. 실제 로깅 설정 점검은 운영 연결 전 담당자의 확인 항목이며, 이번 문서가 로거 설정을 변경한 것은 아니다.

대상: 일반 사용자 권한의 작업 PC 또는 GitHub-hosted CI, Python 3 표준 라이브러리. 저장소 루트에서 다음 합성 시험을 실행한다. AWS/ROSA/Runner 접속·credential·kubectl이 필요 없다.

```text
python -m unittest discover -s tools/phase2 -p "test_*.py" -v
```

정상 판정은 unittest의 테스트 수·실패/오류/skip과 RC를 함께 확인한다. 새 adapter 시험과 기존 intake 회귀를 포함한다. 기존 P2 CI가 이 경로를 자동 탐색하므로 운영 Workflow 수정은 없다. bridge parser 시험 9건은 PyYAML 6.0.3을 별도로 준비한 Python 환경에서 실행하며, 기본 환경에 없으면 명시적으로 SKIP한다. Git snapshot 시험은 표준 라이브러리와 위 옵션을 지원하는 Git으로 실행한다. 실패하면 코드/합성 입력을 수정하고 영향 시험을 재실행한다. 운영 원복 대상은 없다.

`test_delivery_integration.py`의 추가 6건은 코어·실제 POSIX journal·sender 코드의 연결을 확인한다. TLS/API·승인·validator는 합성이며 socket/DNS를 가로채므로 실제 네트워크는 호출하지 않는다. 정상 완료 후 별도 Binding의 복구, HTTP409·응답 timeout·권한 상실, 응답 직후 ack 기록 실패, ack 전 예외 주입 중단을 검증한다. 마지막 두 경우에는 메모리에서 응답을 관측했더라도 durable journal의 pending과 불완전 파일을 보존하고 새 RUN을 차단해야 한다. 실제 프로세스 강제 종료·전원 상실·운영 저장장치 내구성 시험은 아니다. Windows에서는 6건 모두 SKIP이며 Linux CI에서 확인한다.

생산자 호환 시험은 별도 명령이다. 고정된 clean fds-msa checkout과 **시험용 Python 환경의 PyYAML 6.0.3**이 있을 때 실행한다. 서버 패키지를 설치하지 않는다. 검사기는 import 전에 위 producer HEAD와 tracked/untracked 변경 없음을 확인한다.

```text
python tools/phase2/verify_producer_compatibility.py --producer <fds-msa-checkout>
```

검토된 실제 checker와 합성 host/registry·계약을 **임시 합성 Git commit**으로 만들어 bridge의 정상 경로를 확인한다. checker pin은 시험에서도 원래 값이다. 이어 실제 source commit의 `PENDING` 계약 차단 경로를 확인한다. producer 파일을 변경하지 않으며 실제 값/승인을 만들지 않는다. 이 별도 시험은 표준 라이브러리만 사용하는 기본 CI에 포함하지 않는다. 실행 결과 `MOCK_ONLY`, `positive_fixture_is_synthetic=true`, `actual_pending_sender_calls=0`, RC0을 확인하며 실제 ROSA 호환성으로 확대하지 않는다. 기본 suite의 POSIX filesystem 시험은 Windows에서 skip이며 Linux CI의 실제 성공 수와 구분한다.

Runtime 연결 후에는 어떤 STOP도 자동 재시작·token 갱신·force apply의 근거가 아니다. 확인된 적용 목록과 pending intent를 실제 API 상태·field ownership·source 및 claim/checkpoint에 대조한 뒤 승인된 새 회차나 복구를 판단한다. 토큰이 만료되면 복구 쓰기도 하지 않는다. 이전 렌더에 없는 자원 정리는 이 코어의 범위 밖이다.

## 중단 RUN의 복구 검토 인계

[복구 검토 준비표](recovery-review.md)에 파일별 의미, ack 없는 pending·부분 tail·complete와 active 공존 등 중단 상태별 해석과 담당자/완료조건을 연결했다. 10/14 추가한 `delivery_recovery.py`는 기존 파일을 검증해 마지막 receipt를 메모리에 복원한다. writer 재개·active 조정·API readback은 수행하지 않는다. 실제 상태 대조·한정 조정 구현/검토는 배포 연결 전 잔여다. [최신 공급 연결](producer-intake-20261014.md)의 P1 결과와 P2 실제 대상·승인을 분리한다.

## 후속과 현재 단계

- 권욱: 구현된 producer bridge·POSIX store·TLS sender의 운영 연결, clean render 생성/승인 검증, store의 실제 경로/수명주기, Runner CA 사용 출처와 Build/Push 원본 결속. 네 모듈의 독립 리뷰 후에도 이 통합 구현의 리뷰/시험이 남는다.
- 하영: actual ROSA/API/namespace UID/CA/승인 host 및 플랫폼·권한 receipt.
- 재환: 승인 Registry/current·previous digest, clean source·app/prereq 렌더 결속과 복구 입력.
- 그 뒤 별도 허용된 RUN-A/RUN-B/복구 및 Pod imageID/health 증적을 검증한다. 이미 완료한 P1 Push 시험을 이 준비를 위해 반복하지 않는다.

STATIC/MOCK 결과만 기록하며 CI는 해당 PR의 실제 검사 결과로 별도 판단한다. 독립 REVIEW/MERGE/RUNTIME/정본 PUBLICATION·#46 종결은 이 문서 작성으로 완료되지 않는다. 실측 입력이 없는 동안에는 실제 권한·token 발급·Deploy를 시작하지 않는다.
