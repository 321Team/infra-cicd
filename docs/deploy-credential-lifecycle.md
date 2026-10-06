# 주입된 Deploy credential: 서버 인증 확인·수명·로컬 정리

2026-10-06 KST / AI usage: CODEX_ASSISTED.
기준 main c3cb0311e6783ed54b93a782c0fb5f04e2360c7a(PR57 승인 병합).
기존 Adapter·Execution·transport·checkpoint·승인/claim 동작은 변경하지 않는다.

## 범위와 신뢰 경계

deploy_credential.Credential(token, expected_sa_uid, reviewer, now=..., clock=...)
는 이미 발급된 native ServiceAccount JWT를 메모리에서 받는 라이브러리다.
token 발급·파일 저장/삭제·서버 토큰 취소·Secret/RBAC 변경·Runtime CLI/Workflow는 없다.
아직 실제 token이나 API를 조회하지 않았다.

reviewer.review(token, audience, timeout)는 **별도로 검토된 호출자**가 제공해야 한다.
해당 backend는 고정 API https://10.1.93.109:6443의
POST /apis/authentication.k8s.io/v1/tokenreviews를 pin된 CA/TLS로 인증하여 호출하고,
같은 token/spec.audiences의 fresh v1 TokenReview 응답을 돌려줘야 한다.
cache·외부 파일·서명 검증 없는 전달 자료·임의 PASS dict로 대체할 수 없다.
이 라이브러리는 backend의 TLS나 반환 dict의 네트워크 출처를 스스로 증명하지 않는다.
실제 backend 구현/권한/보안 전달은 **미연결**이다. Mock을 붙여 운영 credential로 사용하지 않는다.

Runner SA에 TokenReview 권한이 있다고 가정하거나 그 권한을 추가하지 않는다.
검토된 발급/확인 경로는 기존 관리 권한을 가진 CP1 측이 수행할 수 있지만, CP1 관리자
kubeconfig/token을 Runner에 전달하는 구현은 채택하지 않는다.
그 경로와 승인된 단기 token 주입·관리 원본/임시 참조 정리는 Runtime 연결 PR에서 함께 검토한다.
TokenReview 인증 확인과 **Runner에서 해당 token으로 실제 API 인증**은 서로 다른 증거다.

공식 API의 TokenReview는 etcd에 저장되는 리소스가 아니다.
TokenReview spec에 candidate token 자체가 포함될 수 있으므로 요청·응답 원문과 예외
원문은 로그/증적/PR에 넣지 않는다. 원본 token을 argv나 환경 변수에 출력하지 않는다.

## 계산 계약

1. 디코드 단계는 아직 인증 확인이 아니다. JWT header/3개 canonical base64url 부분,
   RS256/ES256, issuer/audience, subject=system:serviceaccount:ci-cd:cicd-runner-sa,
   kubernetes.io namespace/SA명/**새로 확인한 SA UID**를 검사한다.
   다른 알고리즘은 현재 구현 범위 밖이며 필요 시 근거와 독립 코드 검토로 확장한다.
2. exp/iat/nbf는 실제 JWT의 유효한 정수 시각이어야 한다. 미래 iat/nbf와 만료 token,
   exp-iat>3600초는 거부한다. TTL3600 요청값으로 실제 exp를 합성하지 않는다.
3. reviewer를 새로 호출한다. 인증 결과 true, exact token/spec audience,
   status의 명시적인 동일 audience, SA username/UID와 정상 SA groups를 대조한다.
   empty status.audiences의 API 기본 audience 의미가 있더라도 이 실행 계약에서는
   명시적인 audience 확인을 요구해 거부한다.
4. 조회 예산은 min(10초, 디코드한 현재 만료 잔여시간)이며 완료 시 기한과 만료를 재검사한다.
   backend는 자기 호출 deadline을 지켜야 한다. 이 라이브러리의 사후 검사가 block된
   backend를 실시간 강제 중단하는 것은 아니다.
5. remaining()은 wall clock 잔여와 **조회 전부터 시작한 monotonic deadline** 중 작은 값이다.
   조회 시간을 수명에 더하지 않는다. wall clock 역행으로 수명이 늘지 않으며 monotonic
   역행/clock 실패는 로컬 token 참조를 버리고 중단한다. 현재 시각 신뢰/chrony도 실제 확인 대상이다.
6. 만료나 close() 후 remaining()=0이고 token()은 거부한다. 자동 refresh·재발급은 없다.

서명을 로컬 crypto 구현으로 검증했다고 표기하지 않는다.
authentication_basis=CALLER_AUTHENTICATED_FRESH_TOKEN_REVIEW와
jwt_signature_check=NOT_PERFORMED_LOCALLY는 **신뢰된 backend 전제 아래**의 관측이다.
토큰 취소/SA·bound object 삭제는 초기 관측 이후 생길 수 있으며 시간 계산만으로 감지되지 않는다.
매 실제 API 요청의 인증 거부는 기존 transport가 영구 중단한다.
native SA issuer/authenticator가 아닌 외부 webhook이 임의 JWT를 인증하는 구성을
이 계약의 수명 증거로 사용하지 않는다. 실제 backend 연결 시 native SA 발급 경로도 확인한다.

## 연결과 정리

Credential.token을 transport.token_provider에, Credential.remaining을 Execution과
transport의 credential_remaining_seconds에 전달하는 것이 후속 연결 계약이다.
Execution은 별도의 Deploy 승인 remaining을 유지한다. JWT 수명으로 승인 기간을 대신하지 않는다.
실제 코드 wiring은 아직 하지 않았다. 기존 Execution 영수증의
CREDENTIAL_METADATA=CALLER_SUPPLIED_NOT_VERIFIED_HERE와 checkpoint 계약도 유지한다.

with Credential(...) as credential: ... 또는 finally: credential.close()로
**이 객체가 가진 로컬 참조**를 정리한다. context 예외와 만료에서도 정리한다.
관측 local_credential_cleanup=LOCAL_REFERENCE_DROPPED는 입력 원본/다른 객체의 복사,
파일·Actions Secret 삭제 또는 물리적 메모리 zeroization을 보장하지 않는다.
server_revocation=NOT_PERFORMED를 유지한다. 동일 SA 삭제·재생성이나 bound object 삭제를
일반 cleanup으로 자동 실행하지 않는다. 실제 서버 취소/만료와 임시 입력 회수는 별도 증거다.

관측에는 token/JWT 원문·서명·TokenReview raw response를 넣지 않으며, SA/issuer/audience/
시각/검증 근거/정리 범위만 포함한다. runner_token_authentication과 DELIVERY_GATE는 NOT_VERIFIED다.
initial 서버 인증 관측을 지속적인 인증 유효성·배포 성공으로 확대하지 않는다.

## 검증과 후속 순서

24개 Synthetic/Mock 시험은 token/SA/audience/수명·서버 응답 결속, decode-only 거부,
오류 원문 비노출, 조회 지연·만료·시계 역행, local cleanup 후 사용 금지,
첫 PATCH 이후 실제 만료에 따른 추가 PATCH/자동 rollback 중단을 확인한다.
fixture 서명은 MOCK이며 실제 서명 검증·발급·TLS·Runtime 시험이 아니다.

운영 연결 전에 다음을 실제 증거로 확인한다.
1. 공개 CA bundle·외부 확정 SHA와 Runner의 10.1.93.109 IP SAN TLS handshake.
2. CP1의 실제 ?dryRun=All failed JSON test에 대한 409/422 Status 형태.
   body 수집은 인증값을 배제하며 검토된 실패 test-only 요청으로 수행한다.
   현재 보수적인 details 없는 422→outcome unknown 판정은 유지한다.
3. 검토된 CP1 발급/TokenReview·단기 주입·원본/임시 입력 정리 경로와 최신 SA UID,
   실제 expiry, Runner API 인증 및 기존 최소 권한.
4. 실제 Workflow SHA/계획/승인·Claim/Journal·credential wiring의 독립 코드 검토.
   GitHub polling budget/간격과 부분 배포 수동 대조 절차를 고정한다.
5. 최신 Deployment/HPA·이전 image pull/호환성·Worker Pull, K8S 병행 시험 종료,
   APP DB TLS/Health/E2E·복구를 확인한 뒤 별도 실행 범위로 판단한다.

참조:
- [TokenReview](https://kubernetes.io/docs/reference/kubernetes-api/definitions/token-review-v1-authentication/)
- [Managing Service Accounts](https://kubernetes.io/docs/reference/access-authn-authz/service-accounts-admin/)
- [Service Accounts](https://kubernetes.io/docs/concepts/security/service-accounts/)
