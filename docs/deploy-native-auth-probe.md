# CP1 native SA → Runner 인증 진단

P1-CICD-01의 다음 진단 후보다. CP1의 기존 사용자 인증으로 짧은 ServiceAccount 토큰을 요청하고, 같은 후보를 CP1의 fresh TokenReview와 Runner의 직접 TLS API 요청으로 확인한다. 현재 코드는 독립 리뷰 대상이며 현장 실행·토큰 발급·배포 완료 기록이 아니다.

## 기존 관측과 이번 범위

- PR #60의 승인 source `25d35ff693301bc48292daaa7ec278b6321f4d84`에 대해 사용자가 제공한 2026-10-06 20:20:01 KST CP1 콘솔 결과는 두 Deployment의 failed-UID server dry-run PATCH가 HTTP 422/Invalid를 반환하고 두 read의 spec/RV가 일치했다고 기록한다. 원본 증적을 이 진단이 독립 검증한 것은 아니다.
- Runner → `https://10.1.93.109:6443` CA/호스트명 TLS 검증은 이전 콘솔 결과에서 PASS다. SA 토큰으로 Runner 인증은 별도 미검증 상태다.
- 이번 진단은 기존 SA/Role/RoleBinding, kubeconfig, SSH 신뢰, CA, 이미지 Digest를 변경하지 않는다. 실제 Deployment PATCH/apply, rollout/rollback, Runner kubectl 설치, Workflow 편입, claim/journal 작성, AC 인수는 포함하지 않는다.
- CP1 TokenRequest/TokenReview/SSAR는 API 인증·인가 진단 요청이다. 새 SA, Role, Secret, 클러스터 오브젝트를 영구 등록하지 않는다. 토큰 자체는 서버에서 발급되므로 순수 읽기 Pre-check로 분류하지 않는다.

[governance.md](governance.md)의 보안 통제 코드 독립 리뷰 및 운영 적용 기준을 적용한다. 새 두 모듈·두 시험 파일·이 문서 전체가 리뷰 범위다. 기존 CP1Access/Transport allowlist를 변경하지 않고 별도 subclass와 진단 전용 HTTP client로 추가한다.

## 고정 대상

| 역할 | 호스트와 계정 | 기존 경로 |
|---|---|---|
| 조정 | ansible01 / server01_ansible | 공개 source bundle 및 broker; 기존 SSH trust |
| 발급·fresh TokenReview | 10.1.93.102 / k8s-control01 / server02 | 기존 보호된 사용자 kubeconfig |
| 실제 SA API 인증 | 10.250.10.20 / cicd-runner01 / server01_cicd-runner UID 1000 | 기존 공개 CA; kubectl 불필요 |

- API: `https://10.1.93.109:6443`
- SA: `ci-cd/cicd-runner-sa`, fresh UID 확인, `automountServiceAccountToken=false`
- issuer 및 단일 audience: `https://kubernetes.default.svc.cluster.local`
- 요청 TTL: 900초. 실제 TokenRequest metadata/spec/status와 JWT를 검증한다.
- CA SHA-256: `5e4462b48b67aa3c452119f02578feb5b2ace358782c9b050ba0ad2ea951a49c`
- CP1 공개 CA 기본 경로: `/home/server02/FDS_CICD_PublicCA.uua3ecs6/kubernetes-ca.pem`
- Runner 공개 CA 기본 경로: `/home/server01_cicd-runner/runner-tls-a9c89a77-20261006/kubernetes-ca.pem`

CP1 → Runner 직접 SSH 경로를 새로 요구하지 않는다. Ansible이 기존 두 SSH 연결의 private pipe를 중계한다. 각 연결은 기존 known_hosts의 엄격 검증, agent forwarding 금지, 연결 공유·포워딩 금지를 사용한다. 기존 trust/key가 없으면 중단한다. 새 키나 known_hosts 등록을 자동 수행하지 않는다. `--cp1-key`, `--runner-key`에는 대상별로 이미 승인된 개인키 경로만 지정한다. Ansible 서비스 계정 키를 다른 계정에 자동 재사용하지 않는다.

## Source bundle 계약

외부에서 독립 리뷰 완료 commit을 확인하고 broker 원본 및 아래 일곱 모듈을 **같은 source SHA**로 준비한다. branch 최신 파일을 혼합하지 않는다.

```text
deploy_native_probe.py
deploy_cp1_access.py
deploy_binding.py
deploy_credential.py
deploy_transport.py
deploy_adapter.py
delivery_gate.py
```

Bundle 형식은 UTF-8 JSON 한 줄에 마지막 LF 하나다. 실제 파일 전체(마지막 LF 포함)의 SHA-256을 외부 인계 기록에 고정한다.

```json
{"schema":"P1-CICD-native-probe-source-1","source_sha":"<40 lowercase hex>","files":{"<exact module name>":{"git_blob":"<Git blob SHA-1 at that source>","content":"<exact UTF-8 source>"}}}
```

실제 bundle에는 일곱 파일이 모두 있어야 한다. Git blob은 `SHA1(b"blob " + decimal_byte_length + b"\\0" + UTF8_content)`다. source SHA와 각 blob의 저장소 결속 및 승인 여부는 **인계하는 사람이 독립 확인**한다. 도구의 자기 해시 검증은 승인 확인을 대신하지 않는다.

Broker는 같은 loader로 Ansible·CP1·Runner에 bundle SHA-256, 정확한 파일 집합, 각 Git blob, Python compile을 검증한 뒤 보호된 HOME의 새 `FDS_NativeProbeSource.*` 디렉터리에 공개 코드만 둔다. 디렉터리 0700 / 파일 0600, symlink·hardlink·과다 쓰기 권한·파일 변경을 거부한다. 공개 source 디렉터리는 조사 및 source 확인용으로 보존한다. CA, kubeconfig, 개인키를 bundle에 포함하지 않는다.

## 실행 순서

1. 독립 리뷰 완료 SHA, broker 파일 hash, bundle 전체 hash를 고정한다. 새 코드 변경이 있으면 바뀐 SHA를 다시 리뷰한다.
2. Ansible의 두 대상 SSH 로그인과 기존 known_hosts, 공개 CA 경로를 확인한다. CP1 사용자 kubeconfig를 Runner/Ansible로 복사하지 않는다.
3. 아래 **기본 preflight**를 수행한다. 공개 코드 staging과 CA·계정·source 확인, CP1 fresh SA UID와 TokenRequest/TokenReview 권한 두 건의 SSAR만 실행한다. 토큰을 발급하지 않는다.
4. preflight가 PASS인 뒤 Lead가 실제 단기 발급·중계·Runner 진단 범위와 UTC 종료 시각을 판단한다. 이 판단은 Deploy 승인이 아니다. `--execute`의 종료 시각은 실행 시작 기준 미래 1시간 이내로 제한한다.
5. 승인된 실행에서 CP1은 발급 직전 SA UID를 다시 확인한다. wrapper는 TokenRequest 명령을 한 번만 호출한다. 같은 실행에서 CP1 fresh TokenReview, Runner의 별도 fresh TokenReview RPC, 13건의 고정 SSAR와 두 named Deployment GET을 수행한다.
6. 양쪽 Credential close/입력 참조 해제 응답, SSH 정상 종료 및 추가 출력 없음까지 확인한 공개 결과만 보존한다. 한 단계라도 불명확하면 자동 재발급하지 않는다.

Ansible에서 사용자가 실행하는 진입점은 `transfer_native_probe.py` 하나다. endpoint 함수의 stdout은 토큰이 포함되는 **private SSH protocol**이므로 직접 실행하거나 tee/로그/리다이렉트로 수집하지 않는다.

기본 preflight 템플릿(placeholder는 검토된 인계 값으로 채운다):

```bash
python3 -I -B "$HOME/transfer_native_probe.py" \
  --bundle "$HOME/native-probe-source.json" \
  --bundle-sha256 '<bundle 전체 SHA-256>' \
  --source-sha '<독립 리뷰 완료 source SHA>'
probe_rc=$?
printf 'NATIVE_PROBE_RC=%s\n' "$probe_rc"
```

현장 발급 판단 **후에만** 위 명령에 `--execute --authorization-expires '<YYYY-MM-DDTHH:MM:SS+00:00>'`를 추가한다. 필요하면 이미 승인된 대상별 개인키를 `--cp1-key`와 `--runner-key`로 각각 지정한다. SSH 암호 인증은 기존 interactive terminal에서 수행한다. 새 경로의 현장 SSH 암호 입력 동작은 아직 확인되지 않았다.

기본 preflight PASS는 `TOKEN_ISSUED=false`, `RUNNER_TOKEN_AUTH=NOT_VERIFIED`다. 전체 성공은 `NATIVE_AUTH_PROBE=PASS` 및 `runner_observation.RUNNER_TOKEN_AUTH=PASS`다. 두 모드 모두 `DEPLOY=NOT_EXECUTED`, `DELIVERY_GATE=NOT_VERIFIED`를 유지한다.

## Runner 요청과 판정

| 검사 | 기대 |
|---|---|
| fds-app의 transaction-api / fds-engine 각각 get, update, patch | SSAR allow 6건 |
| fds-app Deployment create, transaction-api delete, 다른 Deployment patch, default namespace patch | SSAR deny 4건 |
| fds-app Secret get, Pod list, TokenReview create | SSAR deny 3건 |
| 두 named Deployment GET | TLS와 Bearer 인증 성공; 이름·namespace·UID·resourceVersion 확인 |

Deny 검사는 SSAR로 인가 결과만 조회한다. Secret 조회나 금지된 변경 요청을 실제로 실행하지 않는다. SSAR의 evaluationError, 권한 불일치, SA UID 변경, 만료, 응답 결속 실패, CA/SSH trust 변경은 모두 중단한다. Runner HTTP client에는 PATCH 구현, proxy, redirect, refresh/retry가 없다.

Credential의 공통 메타데이터 `runner_token_authentication=NOT_VERIFIED`는 TokenReview만으로 Runner 실제 인증을 주장하지 않는 기존 계약이다. 실제 Runner 결과는 별도 `runner_observation.RUNNER_TOKEN_AUTH`에서 확인한다. `SOURCE_APPROVAL=OPERATOR_PINNED_NOT_VERIFIED_IN_TOOL`은 GitHub 리뷰를 도구가 검증하지 않았음을 뜻한다.

## 기한·정리·실패 한계

- Kubernetes TokenRequest status의 만료 시각은 JWT 생성보다 먼저 계산될 수 있다. 둘의 문자열 일치를 강요하지 않고 `iat < status expiry <= JWT exp`와 허용 TTL을 확인한다. Runner는 status/JWT/wall/monotonic/진단 승인 기한 중 이른 한계를 사용하며 재설정하지 않는다.
- wrapper는 한 번의 TokenRequest **명령 호출**만 허용한다. `kubectl`/client-go 내부 HTTP 재시도까지 제어하지 못하므로 서버 발급 횟수의 exactly-once를 보장하지 않는다. 타임아웃·응답 유실이면 발급 여부는 불명확하다. 기존 승인 기한·토큰 기한을 확인하고 원인을 조사한 뒤 별도 재실행 판단을 한다.
- 토큰과 raw TokenReview는 bounded 메모리/SSH pipe에만 전달하고 argv/env/파일/공개 결과/오류에 넣지 않는다. CP1 kubectl verbose 로그는 0, SSH stderr는 broker에서 폐기한다. 이 wrapper의 출력 통제는 API 서버 audit, OS swap/core dump, 같은 UID의 악성 프로세스나 물리 메모리 zeroization을 보장하지 않는다.
- 성공 결과의 `LOCAL_REFERENCE_DROPPED`는 도구가 보유한 논리 참조를 해제했다는 뜻이다. 서버 토큰 폐기나 전체 메모리 소거가 아니다. `server_revocation=NOT_PERFORMED`이며 SA/Role/Secret을 삭제해 회수하지 않는다. 토큰은 자연 만료한다.
- 실패 시 broker는 자기 SSH subprocess만 닫고 중단한다. 원격 정리 ACK가 없으면 `ENDPOINT_CLEANUP=NOT_CONFIRMED`다. issue 명령을 보낸 뒤 결과를 확인 못 하면 `TOKEN_ISSUANCE=ISSUE_COMMAND_SENT_UNCONFIRMED`로 표시하며 실제 발급을 확정하지 않는다.
- 진단 PASS는 Runtime 배포, claim/journal 충족, 배치 선행조건, 스모크 테스트, 증적 저장소 인수, Delivery gate PASS를 대신하지 않는다. 실제 Deploy는 해당 선행조건과 별도 Lead 판단을 고정한 후 진행한다.

## 검증 근거와 남은 현장 검사

시험은 synthetic JWT/TokenReview/API 응답과 POSIX pipe/mock SSH를 사용한다. 정상 흐름, 재발급 차단, SA UID·audience·TTL·실제 만료 불일치, 권한 불일치, framing/기한, source hash/파일 집합/권한, 비밀값 오류 출력 차단, 정리 ACK/프로세스 종료 누락을 확인한다. 시험 PASS는 실제 CP1 발급/Runner SA 인증을 뜻하지 않는다.

Kubernetes v1.36.4의 공식 구현을 참조한다:

- [TokenRequest / TokenReview 타입](https://github.com/kubernetes/api/blob/kubernetes-1.36.4/authentication/v1/types.go)
- [SelfSubjectAccessReview 타입](https://github.com/kubernetes/api/blob/kubernetes-1.36.4/authorization/v1/types.go)
- [TokenRequest 발급·status 만료 구현](https://github.com/kubernetes/kubernetes/blob/v1.36.4/pkg/registry/core/serviceaccount/storage/token.go)
- [client-go retry 구현](https://github.com/kubernetes/client-go/blob/v0.36.4/rest/with_retry.go)
