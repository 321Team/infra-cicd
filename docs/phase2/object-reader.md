# #46 객체 조회 adapter — 오프라인 구현·검토본

2026-10-16 KST · 이권욱 / CODEX_ASSISTED. 기준: PR87 병합 `223c3ff4825e2c828f0e695107171dbf4128ebf6`.
`delivery_object_reader.py`는 기존 [객체 대조](object-reconciliation.md)에 고정 대상 GET을 연결한다.
실제 클러스터·token·Runner에 연결하지 않았고 운영 CLI는 BLOCKED다. 새 승인 절차·AC·IAM 권한을 추가하지 않는다.

## 구현과 신뢰 경계

- 입력: 기존 Binding, 메모리의 `BearerCredential`, 해당 Binding에 결속된 CA 원본과 사전 검증 numeric endpoint, **이미 허용된 baseline**으로 만든 ObjectReconciler, 현재 Progress를 읽는 동기 함수.
- credential 객체는 전달 handle이며 진위 증거가 아니다. `ReadRequest.credential is reader.credential`를 강제하고 같은 token fingerprint로 전송한다. 동일 handle을 플랫폼 검증과 sender에도 사용해야 한다. 전달 파일/환경변수/발급 방식을 운영 표준으로 정하지 않는다.
- 기존 비차단 TLS socket/절대 deadline/CA·hostname 검증을 재사용한다. DNS·proxy·redirect·retry·ambient credential이 없다. GET 6개는 하나의 `ReadRequest.deadline`을 공유하며 각 GET 상한은 기존 transport의 10초다. 인증·승인 잔여가 더 짧으면 그 deadline에서 중단한다.
- 대상은 `fds-app`의 기존 Service2/Deployment2/Route2 exact path다. `rosa-app`은 overlay다. List/watch/query/subresource/다른 namespace·Secret·PATCH/DELETE는 거부한다. 기존 Sender의 PATCH/Deployment GET 범위는 확대하지 않았다.
- HTTP200은 exact apiVersion/kind/name/namespace를 확인하고 **받은 JSON body bytes**를 대조기에 넘긴다. 기존 UID/spec/ownership·ACK 규칙이 유지된다.
- HTTP404는 `v1 Status`, `Failure`, `NotFound`, 정수 code404 및 exact details.group/plural kind/name을 모두 확인한 경우만 absence(None)로 변환한다. core group의 빈 값 생략만 허용한다. 이 규칙은 [Kubernetes apimachinery v0.34.1 NewNotFound](https://github.com/kubernetes/apimachinery/blob/v0.34.1/pkg/api/errors/errors.go#L132-L144)를 참고한 구현 가정이며 실제 ROSA 원본 검증 완료를 뜻하지 않는다.
- 403/401/429/5xx/redirect/일반404/빈 응답/잘못된 TLS/JSON/timeout은 absence가 아니라 실패다. 중복 JSON key와 무한대/overflow 숫자도 거부한다. 응답 body1MiB와 wire 상한은 기존대로다.
- 각 GET 전후 전체 deadline과 Progress를 확인한다. 마지막 GET까지 성공하고 baseline+durable ACK 대조가 끝나야 ObjectEvidence를 반환한다. 성공한 최신6개 원문(404포함)은 `last_observations`에만 유지한다. 실패한 부분 집합을 증거로 승격하지 않는다. 원문·credential은 repr/오류 문구에 넣지 않는다.

6 GET은 원자적 snapshot이 아니다. 동일 namespace UID/cluster identity, 실제 credential issuer/audience/900초 수명/RBAC, 승인 철회 상태, exclusive access·admission 검증은 composite provider 책임으로 남는다. baseline을 fresh GET 값으로 자동 채택하지 않는다. 한 session에서 순차 사용하며 thread-safe 공유 객체로 사용하지 않는다. `progress()`는 기존 메모리/journal 상태 조회용이며 외부 I/O를 넣으면 그 대기를 이 adapter가 취소해 주지 않는다.

## 재현·실패 처리

실행 위치: infra-cicd 저장소 루트, 일반 사용자 Python3.10+ 표준 라이브러리. 실제 입력·AWS·ROSA·DNS·token 발급이 필요 없다.

```text
python -m unittest discover -s tools/phase2 -p test_delivery_object_reader.py -v
python -m unittest discover -s tools/phase2 -p "test_*.py" -v
```

새 MOCK은 fake TLS socket으로 정상6조회/기존 baseline/ACK 이후 변화/404분류/인증서 실패/권한 거부/부분 응답/시간초과·역행/Progress 변경/비밀 비노출 및 기존 session의6ACK→완료·실패시다음쓰기차단/기록·fence유지를 확인한다. TLS 암호화 실통신·실제 API/RBAC 증거가 아니다. 정상기준은 RC0·실패/오류0, SKIP 별도 기록이다.

실패 시 다음 쓰기를 멈추고 기존 ACK/pending/fence를 보존한다. 자동 재시도·토큰 갱신·fence 해제·복구 Apply는 없다. 기존 [복구 진단](recovery-review.md)으로 판정하며 이번 코드의 rollback은 revert다. 실제 서버에 반영한 변경이 없다.

## 잔여

권욱: 승인/철회 원본 인증 provider, 플랫폼 provider·credential 전달 연결, 생산 출처·baseline admission, 영구 POSIX root·완료 관측 후 배포 CLI 연결. 하영의 플랫폼 원본과 재환의 기존 공급 원본을 소비하며 중복 시험·양식을 요구하지 않는다. 독립 코드 검토와 변경 CI 후 별도 승인 Runtime에서 현재 지원 API의 응답을 확인한다. #46 Delivery 인수는 계속 미완료다. Phase1 설정/Workflow/AC 및 기존 Create GO는 변경하지 않는다.
