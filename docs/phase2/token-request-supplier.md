# 메모리 TokenRequest 공급부

`delivery_token_request.py`는 검토된 발급 측 코드가 명시적으로 호출하는 1회 공급부다.
실제 운영 채택값이나 `SITE`를 자동으로 채우지 않는다. import·생성자는 연결·발급을 하지 않는다.

## 입력과 연결

- `TokenRequestIssuer`: 기존 Binding, 명시한 `TokenTarget(namespace/name/uid/issuer/audience)`,
  메모리 사용자 BearerCredential, 검토된 숫자 endpoint IP·CA, 해당 호스트의 절대 deadline과 시계.
- `delivery_fds_site_inputs.build_token_issuer`: 이미 수신한 FDS SA `ci-cd/fds-deployer`와 UID,
  issuer/audience·CA만 연결한다. 소비 namespace `fds-app`을 발급 SA namespace로 사용하지 않는다.
  실제 사용자 자격·현재 endpoint·job·마감은 기본값 없이 전달해야 한다.
- `RequestBoundSupplier`: 사전에 인증·채택한 정확 job/nonce/repository request와 source token 목록.
  기존 SSH bridge의 `provide_credentials`로 전달할 수 있다. request의 일치나 READY 자체는
  발급 허가·GO가 아니다. 실제 발급 측의 승인과 Kubernetes 인가는 계속 별개다.

발급은 정확 SA `/token` 경로에 audiences 1개·900초를 요청하는 POST 한 번이다.
기존 TLS sender의 CA/SNI 검증·숫자 endpoint·bounded HTTP/JSON을 사용하고 DNS·proxy·redirect·
retry·자동 갱신·기본 kubeconfig·`oc`·파일·환경변수 자격 로더를 추가하지 않는다.

## 정상 판정과 원본

정확 HTTP 201, TokenRequest 응답 종류·SA 이름/namespace, 실제 JWT의 SA UID·sub·issuer·audience,
서버 응답과 JWT의 유효기간을 확인한다. 서버가 요청보다 짧은 기간을 돌려주면 그 만료를 사용하고,
900초를 초과하거나 미래/만료·시계 역행이면 전달하지 않는다. 수신 시각에 900초를 더하지 않는다.

JWT 파싱은 **이번 pinned TLS 발급 응답과 실제 token의 정합성 확인**이다. JWKS 서명 검증이나
전체 RBAC·최소권한·배포 승인·플랫폼 인수의 증거로 확대하지 않는다. token의 대상 UID가 다르면
미전달로 중단하지만 이미 발급됐을 가능성은 남는다. metadata.uid를 원자적 UID 사전조건으로
간주하거나 별도 SA/Secret/Pod를 만들지 않는다.

`take_receipt()`는 실제 응답 raw와 token을 가진 비공개 메모리 receipt를 1회 넘긴다.
후속 전송이 실패해도 수신한 발급 receipt는 인계할 수 있다. **raw에 token이 있으므로 GitHub·
공유 폴더·표준 출력·일반 로그로 게시하면 안 된다.** 자동 원본 발행이나 `IssuerFacts`,
`PlatformEvidence`, original/control pin·RBAC/route 의미 채택을 수행하지 않는다.
future receipt hash·발급 시각·만료·token fingerprint를 미리 합성하지 않는다.
실제 원본 저장/인증과 현재 RuntimeAuthorityPlan 연결은 별도 잔여이며 본 모듈 성공만으로
운영 SITE 전체 연결 완료라고 판단하지 않는다.

## 실패·정리

POST 진입 시 `attempted=True`를 보존한다. 그 뒤 HTTP 오류·timeout·잘림·불일치·취소가 나면
토큰 미발급을 단정하지 않고 자동 재요청하지 않는다. 발급 인가 측에서 기존 기록·실제 수명에
따라 처리한다. 오류에는 raw/자격/서버 본문을 넣지 않으며 인터럽트도 정제한다.
client는 사용자 bearer 참조를 해제하고 supplier는 source 자격과 request 참조를 해제한다.
receipt의 소비/폐기는 소유자 책임이며 참조 해제는 보안 소거·서버 자격 회수가 아니다.

## 검증 범위

`test_delivery_token_request.py`는 SYNTHETIC JWT와 주입한 가짜 TLS socket으로 실제 HTTP
직렬화·201 파서·UID/수명/원문 비노출·기한/일회성·bridge frame 호환을 검증한다.
실제 네트워크·발급·SSH·Runner·ROSA·운영 원본은 사용하지 않는다. SITE는 `None`이다.

API 해석 근거:
- [Kubernetes v1.33 TokenRequest 서버](https://github.com/kubernetes/kubernetes/blob/v1.33.0/pkg/registry/core/serviceaccount/storage/token.go): SA 조회와 응답 생성, 이름/namespace 검사, 실제 만료.
- [Kubernetes v1.33 claims](https://github.com/kubernetes/kubernetes/blob/v1.33.0/pkg/serviceaccount/claims.go): SA UID·namespace 및 iat/nbf/exp 생성.
- [TokenRequest API 타입](https://github.com/kubernetes/api/blob/v0.33.0/authentication/v1/types.go): 요청 기간과 반환 기간은 다를 수 있다.

위 공개 코드 대조는 현재 운영 클러스터에 접속하거나 버전·동작을 실측한 결과가 아니다.
