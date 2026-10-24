# #46 READY 이후 메모리 로그인 공급 연결

**코드 구현과 실제 운영 구성을 구분한다.** `BasicChallengeLogin`과 `LoginActorChannel`을 추가했지만 실제 OAuth 주소·CA·endpoint·로그인 입력은 아직 공급하지 않았다. `SITE=None`, dispatch false, Runtime NOT_RUN이다. 이전에 제출된 SA issuer URL을 OAuth 로그인 주소로 사용하지 않는다.

## 연결 범위

기존 `MemoryOperatingSupplier`의 직접 actor 입력은 유지한다. 새 경로는 아래 순서다.

1. ansible01의 신뢰하는 caller가 실제 OAuth origin·CA bytes/해시·숫자 endpoint·본인 사용자·메모리 password·monotonic 기한을 `BasicChallengeLogin`에 명시한다. 파일·환경변수·argv·kubeconfig·기존 세션 탐색·대화형 입력을 수행하지 않는다. 기존 본인 `gwonuk` 선택은 `build_login_actor_channel`이 대조한다.
2. 현재 job checkout의 공개 helper를 관리 SSH로 실행한다. 동일 context/Binding/repository selector, UID, canonical frame, ACK/EOF, helper RC0를 확인한 **뒤에만** 로그인을 시작한다. job listener가 늦게 열리면 기존 helper 기한 안에서 `ECONNREFUSED/ENOENT`인 connect만 짧게 기다린다. 연결 후 peer·내용·ACK 실패, SSH 실패 또는 자격 전달 실패는 재시도하지 않는다.
3. 명시 OAuth origin의 `/oauth/authorize`에 `openshift-challenging-client`, `response_type=token`, 정확한 implicit return URI, 무작위 state와 X-CSRF-Token을 보낸다. Basic challenge를 받은 경우 같은 URL에 자격을 **한 번만** 보낸다. TLS 체인·호스트·SNI와 명시 CA를 확인하고, 시스템 CA·proxy·DNS·redirect·insecure fallback을 사용하지 않는다.
4. 302 응답의 Location은 따라가지 않는다. 같은 origin의 `/oauth/token/implicit` fragment에서 state 일치, Bearer, modern `sha256~` token, 양의 `expires_in`과 중복 없는 필드를 확인한다. 브라우저/SSO/외부 challenge redirect/legacy token/만료 미제공 응답은 현재 지원하지 않으며 자동 전환하지 않는다.
5. `LoginActorChannel`이 기존 users/~ 본인 확인 → SA TokenRequest 1회 → 기존 credential bridge 전달 → 자기 OAuth DELETE 경로에 연결한다. 로그인 성공만으로 실제 사용자·SA 권한·job 수용을 확정하지 않는다. 발급 전 검증과 기존 receipt·control·finish 경계를 유지한다.
6. caller는 `take_receipt()`의 SA token 포함 원본과 `take_login_metadata()`의 회수 메타데이터를 **각각 1회 비공개 인수**한다. 메타데이터는 hashed OAuth 객체 이름과 서버가 반환한 `expires_in`이며, 원문 token·password가 아니다. 서버의 설정상 최대 수명이나 실제 EXPIRES 조회 기록으로 확대하지 않는다. 공개 CI/PR/댓글에는 넣지 않는다.

구성 객체 생성·close는 통신하지 않는다. 실제 `send()`/`login()`은 별도 Runtime 승인과 현장 입력 확인 뒤 호출할 작업이다. 단회 객체는 재사용하지 않는다. 두 호스트의 monotonic 절대값을 공유하지 않으며, 발급측 login/READY/bridge/actor는 같은 clock 및 operation 기한을 사용하고 cleanup 기한은 별도로 확보한다.

## SITE 조립 연결 — `build_login_supplier`

발급측은 `delivery_fds_operating.build_login_supplier`로 기존 공개 READY pull·로그인 채널·SSH bridge·메모리 공급부를 한 번에 조립할 수 있다. job 측은 기존 `assemble_observed_job`을 유지한다. 두 구성에 **같은 native context·Binding·순서가 같은 repository 목록**을 연결한다. repository pin의 형식 대조는 실제 원본/control 인증을 대신하지 않으며, 기존 job reader가 실제 ID·원본·권한을 검증한다.

| 입력·동작 | 연결·소유 기준 |
|---|---|
| `config`, `context`, `repositories` | 실제 선택한 job 구성과 각 `SourceRoute.repository`의 순서를 재사용. 미래 run/attempt·grant·nonce를 만들지 않음 |
| `workspace`, `port`, `host_key_alias`, `remote_python`, `remote_timeout_seconds` | 전부 필수 입력. 기존 관리 방향·key/known_hosts와 exact-checkout helper 두 개에 같은 설정을 적용. 22·IP 별칭·기록 경로를 현재값 기본값으로 사용하지 않음 |
| `login_provider`, API `endpoint_ip` | 명시 OAuth trust/본인 메모리 입력을 가진 미사용 provider. API endpoint와 OAuth endpoint는 구분. 생성 때 로그인·파일 읽기·SSH·clock 호출 없음 |
| `source_tokens` | job에 필요한 repository와 정확히 대조하고 내부에 참조를 복사. caller 사본의 정리는 별도 책임 |
| 기한·clock | READY/bridge는 같은 operation 기한·clock, 로그인 기한은 그 이하, OAuth 회수는 별도 cleanup 기한. 다른 호스트 monotonic 값을 가져오지 않음 |
| 성공 반환 | provider 소유권을 반환된 supplier에 넘기고 caller는 별도로 사용하지 않음. 아직 로그인·배치·실행 승인·SITE 활성화는 아님 |
| 조립 실패·취소 | provider를 소비하거나 닫지 않음. caller가 수정 후 재조립 또는 close를 결정. 비밀 포함 원래 예외는 외부에 전달하지 않음 |

반환된 supplier는 명시 실행 단계에서만 `send()`한다. 성공·실패 뒤 private receipt와 로그인 메타데이터를 인수한 다음 로컬 참조를 정리한다. 공급 성공은 job의 원본/control 인증·finish 완료를 뜻하지 않는다. 실제 환경값이 없는 동안 `SITE=None`과 기존 workflow 차단은 그대로다. 새로운 승인 양식·담당자 재회신은 추가하지 않는다.

## 실패와 복구

| 조건 | 판정·인수 |
|---|---|
| READY 미수신·기한 만료 | 새 경로에서는 로그인 NOT_ATTEMPTED. password 참조 해제. OAuth를 미리 만들지 않아 이 경로가 생성한 토큰은 없음 |
| Basic 요청 후 응답 손실/해석 실패 | TOKEN_OUTCOME_UNCONFIRMED. 자동 재로그인 없음. 서버에 token이 생겼을 수 있어 권욱이 본인 자격 목록/실제 만료·수동 회수 인수 |
| token 수신 후 identity 실패 | 발급·타인 token 삭제 금지. 비공개 OAuth 객체 이름/수명 메타데이터를 보존하고 수동 회수 |
| 발급·전달 실패/취소 | 기존 private receipt와 자기 OAuth 회수 상태를 보존. 취소 의미 유지. 자동 재발급·재전달 없음 |
| OAuth DELETE 응답 불명확 | UNCONFIRMED. SA 만료·자원 정리·로그인 서버 회수는 각각 구분. 로컬 close를 서버 회수 성공으로 표시하지 않음 |
| 원본/control 철회·finish 미생성 | 기존 5역할 인증·claim/fence 유지. 실패를 완료로 바꾸거나 과거 GO를 현재 grant로 합성하지 않음 |

Python 문자열 참조 해제는 메모리 zeroization이 아니다. caller가 가진 사본과 비공개 receipt 보관 책임도 별도다. 기존 직접 actor 공급 경로의 READY 실패 시 수동 회수 조건은 그대로 유지한다. 새 경로도 프로세스 강제 종료/응답 손실의 고아 토큰 가능성을 완전히 제거하지 않는다.

## 실제 연결 잔여 — 중복 회신 요청 없음

| 담당 | 남은 값·행위 | 현재 상태 |
|---|---|---|
| 권욱 | OAuth origin·CA·숫자 endpoint 및 본인 password를 신뢰하는 메모리 caller에 연결; repository 자격도 메모리로 공급 | 코드 있음 / 실제 공급 없음. API SA issuer에서 추정 금지 |
| 권욱 | 기존 SSH key·known_hosts를 재사용하되 실제 port·HostKeyAlias, 현재 workspace/Python·UID/netns 확인 | 재환의 역사적 원본 수신 완료. 미기록 값은 현장 준비에서 확인하며 22/별칭을 임의 입력하지 않음 |
| 권욱 | 원본/control 5역할 pin·materializer, replicas 인계 뒤 baseline, 사후 finish scope 연결 | 기존 제출 원본 재사용. 미발행 grant·미래 관측 원본 생성 금지 |
| 하영/재환 | 담당 영역의 기존 사실이 달라졌을 때만 변경분 전달; 기존 역할·최종 회수 담당 유지 | 같은 질문 재요청 없음. 전체 실행 GO·10/26 12:00 정리 기한 변경 없음 |

## 검증과 근거

시험에서는 모든 자격/응답을 SYNTHETIC fixture로 제공하고 네트워크·OpenSSH를 차단한다. HTTP 직렬화/파싱, TLS 정책, READY 전 로그인 금지, 오류·취소·원본 인수, 중복 호출·bad state·redirect·만료·peer/EOF 경계를 검사한다. 조립의 필수 입력·소유권·오류 정리와 bridge clock/기한·request Binding·source 오류에서 **login 호출 0회**, 충분한 대기 예산의 EACCES에서 **소켓 생성 1회**를 고정한다. 기존 Linux 로컬 IPC/native job/5역할 source·control/finish 시험도 함께 실행한다. 로컬 helper 시험은 실제 SSH 배치 검증이 아니다.

프로토콜 근거: OpenShift 공식 [token request 구현](https://github.com/openshift/oc/blob/586088c46cba2cd87e1a128a304fe484cf678cd5/vendor/github.com/openshift/library-go/pkg/oauth/tokenrequest/request_token.go), [Basic challenge handler](https://github.com/openshift/oc/blob/586088c46cba2cd87e1a128a304fe484cf678cd5/vendor/github.com/openshift/library-go/pkg/oauth/tokenrequest/challengehandlers/basicauth.go). 이 구현은 명시 implicit flow만 지원하며 oc의 전체 discovery·code/PKCE·browser flow를 대체하지 않는다. 실제 클러스터의 지원 여부·OAuth 수명·도달성은 미검증이다.
