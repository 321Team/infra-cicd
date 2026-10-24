# #46 로그인 공급·로컬 helper·원본/control/finish 연결 시험

기존 두 시험 경계 사이를 연결한다. `test_delivery_login_to_finish`는 HTTP와 SSH/READY를
모의했고, `ReadyNativeIntegrationTests`는 실제 로컬 helper를 사용하되 준비한 시험 자격을
직접 전달했다. 새 `test_delivery_login_native_finish`는 **기존 운영 코드를 변경하지 않고**
deferred login 공급부와 로컬 helper 두 개를 같은 회차에 연결한다.

## 실제로 실행하는 코드와 모의 경계

- 실제 로컬 실행: `BasicChallengeLogin`, `LoginActorChannel`, `MemoryOperatingSupplier`,
  공개 READY mailbox/pull, Python helper 두 프로세스, AF_UNIX·익명 pipe, native job,
  prepared plan, private source/session, 5역할 원본/control·기존6객체 baseline, 종료 관측 코드.
- 모의: OAuth/TLS·users/~·TokenRequest·OAuth DELETE의 HTTP 응답, 원본 서버,
  객체 API·메모리 저장소, native job 메타데이터·기대 Runner. 모든 자격·원본·승인은 SYNTHETIC.
- OpenSSH 호출 경계에서 정확한 검토 helper를 로컬 `python -I`로 실행한다.
  실제 SSH·DNS/TCP·인증서 검증·실제 로그인/자격 발급·배포·운영 인수를 시험한 것이 아니다.
- Windows는 Linux 전용 네 시험을 SKIP한다. nonroot Linux CI에서 실제 실행 여부를 확인한다.

## 검증한 계약

1. **동일 발급 자격:** 기존 `TokenRequestIssuer.issue`가 파싱한 receipt를 변형하지 않고
   반환 직전에 관측한다. 이 반환 뒤에만 bridge가 자격 frame을 만들 수 있고, job은 frame을
   읽은 뒤 materializer를 호출한다. 임의 sleep이나 materializer 지연으로 순서를 만들지 않는다.
   job이 받은 token과 그 receipt token을 대조한다. 종료 뒤 supplier가 인수하는 receipt도
   같은 객체이며 1회 인수다. receipt를 grant·fence·실행 승인으로 만들지 않는다.
2. **공개 READY 뒤 로그인:** 실제 공개 helper의 ACK/EOF/RC0 뒤 공급부가 움직인다.
   공개 helper가 같은 프로토콜을 마친 뒤 RC1이면 로그인 HTTP와 발급은 0회이며
   claim·완료 원본이 생기지 않는다. child 프로세스의 실제 RC1도 대조한다.
3. **원본/control 유지:** 독립된 fixture grant/pin을 그대로 쓴다. 시험 자격과 맞추는 것은
   명시 platform principal/issuer facts뿐이다. control 철회는 claim 전 중단하고 write는 0회다.
4. **실제 sender close 뒤 종료 처리:** 정상은 관측6건·finish 저장1회·complete1회·fence 해제다.
   finish 저장 실패는 sender close가 있었음을 먼저 확인하고 complete0·fence 유지다.
   job 실패와 공급측 자기 OAuth 회수 모의 성공은 별도로 기록한다.
5. **소유권·비밀:** 모든 helper를 wait/reap하고 supplier 스레드를 join하며 자기 owner를 닫는다.
   source transport 정리, 공개 결과·helper argv/env의 password/token 비노출을 확인한다.

합성 시험 결과를 비공개 현장 원본이나 실제 승인으로 편입하지 않는다. job 전용 checkout·
인증된 READY·현재 UID/netns, 메모리 자격 공급, 실제 원본/control·baseline·finish scope는
여전히 운영 연결의 잔여다. 기존 `SITE=None`·실행 workflow·권한·10/26 정리 결정은 변경하지 않는다.
