# #46 교차 호스트 수신 core와 미확정 운영 입력

## 구현 범위

`delivery_crosshost_ingress.py`는 검토된 전송부가 전달하는 바이트를 정확한
대기 job에 결속하고, 수신한 자격을 기존 `build_owned_invocation`으로 넘긴다.
실제 SSH listener·job rendezvous·발급기·설정 로더는 설치하지 않는다.
`delivery_job_site.SITE=None`이며 이 모듈의 import와 생성자는 외부에 접속하지 않는다.

| 구분 | 현재 상태 |
| --- | --- |
| peer receipt 대조, job/Binding/nonce 결속, bounded frame 수신, 1회 소비 | 구현·오프라인 시험 |
| 메모리 bundle → 기존 Linux 익명 pipe → typed invocation/driver | 구현·Linux 오프라인 연결 시험 통과 |
| 명시한 pipe FD 전송/수신 adapter·JobSite.build 부모 helper | 구현·운영 설정과 분리한 오프라인 시험 |
| 실제 SSH 인증·현재 host key·정확한 job 부모와의 rendezvous | 미채택: 권욱·재환이 현장 운반부와 job 소유권을 연결 |
| 실제 issuer 위치·발급 권한·SA UID/만료·CA/IP와 자격 전달 | 미채택: 하영 플랫폼 입력과 권욱의 운영 구성 연결 |
| source/control·승인 원본·Binding·finish 범위/경로 | 기존 명시 입력을 재사용하며 미확정 값은 그대로 유지 |

nonce는 수신 상관키다. nonce·native job metadata·repository 이름은 발급 권한,
실제 SSH 인증, issuer 인증이나 실행 GO가 아니다. `PeerIdentity`는 검토된 전송부가
실제 인증 뒤 반환해야 할 receipt의 타입이며, 해당 타입을 생성한 것만으로 인증되지 않는다.
수신 JSON은 peer·CA·IP·경로·권한·승인 플래그나 운영 설정을 채택할 수 없다.

## 호출 위치와 소유권

검토된 `JobSite.build`의 부모 프로세스에서 다음 순서로 연결한다.
자동으로 이 경로를 선택하는 기본 builder는 없다.

1. 실제 job의 `JobContext`, 검토된 `Binding`, 예상 peer, 고정 repository pin/CA/IP/
   source별 local-use cap, 명시한 절대 monotonic 수신 마감으로 `CrossHostIngress`를 만든다.
   실행 중 생성되는 nonce는 운영 입력을 사전 합성하는 값이 아니다. 시험에서는 명시한 fixture로 교체한다.
2. `request()`의 공개 요청을 검토된 전송부/발급부에 전달한다. 발급부도 실제 요청과
   전송 신원을 독립적으로 확인해야 한다. 이 공개 요청을 익명 발급 API로 사용하지 않는다.
3. `ReviewedIngressChannel(authenticate, read, close)`를 명시적으로 넘겨 `receive()`한다.
   `authenticate(request, deadline)`가 반환한 정확한 peer receipt를 비밀 본문 읽기 전에 대조한다.
4. 결과의 `build_invocation(context, command, config=..., plan=..., finish_slot=...,
   wire=..., handoff_deadline=...)`로 기존 helper를 호출한다. 성공 반환 뒤 자원 소유권은
   기존 driver로 이전한다. 결과를 사용하지 않으면 `close()`로 자체 참조를 해제한다.

각 callback은 신뢰받은 검토 코드다. `read(max_bytes, deadline)`는 크기 이하의 `bytes`
또는 EOF를 반환하고 **같은 절대 마감을 실제로 강제**해야 한다. `close()`는 자신이
소유한 전송 자원만 한 번 정리하고 성공 시 정확히 `True`를 반환해야 한다.
동기 callback이 영원히 반환하지 않는 경우 core가 선점 중단할 수 없다.
정리 callback도 유한 시간 안에 반환해야 하며, 수신 마감 이후 정리가 끝나면 성공 bundle을 반환하지 않는다.

## 메시지 계약과 실패 처리

공개 요청은 `schema=P2-CICD-ingress-request-1`, `target`, `nonce`, `repositories`만 가진다.
`target`은 repository/event/ref/workflow SHA/run ID/attempt/Binding key다.
전송 본문은 `encode_frame(request, rosa_token=..., source_tokens=...)`가 만드는
4바이트 network-order 길이와 UTF-8 JSON이다. 프레임 뒤에는 EOF가 필요하다.

- 본문 schema는 `P2-CICD-ingress-message-1`, 나머지는 정확한 target/nonce/
  `rosa_token`/순서가 고정된 `source_tokens`뿐이다. source 항목은 repository/token 두 키다.
- 본문 최대 96 KiB, read chunk 최대 4096바이트, source 1~16개다.
  ROSA token은 ASCII 1~16384바이트, source token은 20~4096바이트이며 기존 token 문법을 따른다.
- 중복·추가 키, 잘못된 타입/attempt, NaN, 잘림, 추가 프레임/바이트, repository 누락·추가·순서 변경,
  다른 job·Binding·nonce는 거부한다. 동일 GitHub token의 허용 repository 간 공유는 가능하다.
  ROSA token과 source token의 byte 재사용은 거부한다.
- 첫 수신 시도와 첫 bundle build는 실패해도 소비된다. 동시 두 수신, 복사·직렬화로 재시도하지 않는다.
  실패하면 소유한 channel을 정리하고 부분 bundle을 닫는다. 임의로 넘긴 borrowed 객체는 닫지 않는다.
- callback 오류/인터럽트 인자는 공개하지 않는다. 고정 오류 코드만 제공하며 정리 실패를 우선한다.
  자동 재발급·재연결·재전송·대상 확대는 없다. 실패 원인과 실제 정리 상태를 확인한 뒤
  유효한 기존 범위 안에서 새 receiver를 준비한다. 정리 불명확이면 실행하지 않는다.

bundle은 최초 외부 수신 마감과 caller handoff 마감 중 이른 값을 기존 helper로 넘긴다.
helper의 내부 pipe 인계 최대 10초도 유지한다. 이 마감은 token의 실제 900초 만료,
승인 유효기간, 첫 write 예산을 새로 시작하거나 durable claim을 자동 반납하는 값이 아니다.
실제 credential·issuer·source 권위·철회·기간·실행 가능 여부는 기존 runtime factory가 재검증한다.

`repr`과 공개 요청에 자격을 넣지 않고 bundle 참조는 소비 시 분리한다.
이는 Python 문자열 복사본의 보안 삭제, 같은 UID/root 격리, 서버 측 revoke를 보장하지 않는다.
실제 transport는 packet 파일·환경변수·argv·로그를 자격 운반 경로로 사용하지 않아야 한다.
기존 #82의 SSH/SCP 관측은 현재 채널 가용성이나 메모리 수신 검증의 대체 증거가 아니다.

## 오프라인 검증과 실행 경계

CPython 3.12와 기존 hash-pinned 시험 의존성을 준비한 저장소에서 실행한다.

```text
python -m unittest discover -s tools/phase2 -p 'test_delivery_crosshost_ingress.py' -v
python -m unittest discover -s tools/phase2 -p 'test_*.py' -v
```

시험은 SYNTHETIC token·reserved IP·fake clock·메모리 API를 사용하고 socket을 차단한다.
Linux 시험은 실제 로컬 익명 pipe의 인계와 driver/factory/finish 정리를 함께 검증한다.
Windows에서는 이 Linux 시험을 SKIP으로 표시한다. 실제 SSH·발급·서버/Runner 접속·배포는 하지 않는다.
hosted offline CI의 정확한 stack base만 추가하며 운영 workflow·P1 workflow·권한은 변경하지 않는다.
STATIC/MOCK/CI의 성공은 RUNTIME·독립 REVIEW·MERGE·최종 인수와 별개다.

## 전송 스트림과 job 부모 helper

`delivery_crosshost_pipe.py`는 **이미 검토된 transport 소유자가 명시적으로 넘긴
Linux pipe end**에만 접근한다. `pipe_ingress_channel(read_fd, authenticate=...,
deadline=..., monotonic=...)`는 기존 core의 정확한 `ReviewedIngressChannel`을 반환한다.
`OwnedPipeWriter(write_fd, deadline=..., monotonic=...).send(request, expected_peer=...,
authenticate=..., rosa_token=..., source_tokens=...)`는 같은 프레임을 한 번 보내고 writer를 닫는다.
송신 callback에는 공개 요청의 복사본만 주며 변조된 복사본으로 본문을 바꾸지 않는다.

- FD 0/1/2·bool·일반 파일·잘못된 pipe 방향은 인수하지 않는다. 실제 pipe 타입/방향을
  확인한 직후 소유권을 인수하며 이후 구성 실패도 그 end를 닫는다. 그 전 거부는 caller가 정리한다.
- caller는 별도 alias/다른 writer가 없는 독점 소유권을 넘겨야 한다. 같은 UID의 임의 코드가
  몰래 FD를 닫거나 alias를 만들 수 없다는 격리 보장은 없다. 복제한 borrowed FD를 조용히 채택하지 않는다.
- 인수한 FD에는 `CLOEXEC/NONBLOCK`을 설정한다. `poll`로 높은 FD 번호도 지원하고
  같은 절대 마감 안에서 부분 read/write·EAGAIN/EINTR을 처리한다. FD의 dev/inode/접근 방향이
  달라지면 I/O를 거부하고 재사용된 새 객체를 정리 대상으로 삼지 않는다. 락과 nonblocking I/O는
  이 객체를 통한 close와 I/O를 조율하며 외부 코드의 임의 FD 교체까지 원자적으로 막지 않는다.
- 부분 write 이후 실패는 writer를 닫아 수신자가 잘린 본문/EOF로 거부하도록 한다.
  같은 메시지를 새 FD에 자동으로 재전송하지 않는다. send 성공은 로컬 write/close이며
  원격 수용·자격 인증·서버 revoke나 배포 성공을 나타내는 ACK가 아니다.

monotonic 마감은 **각 프로세스가 속한 호스트의 로컬 시계**를 기준으로 명시한다.
이 숫자를 다른 호스트의 절대 시각으로 직렬화하지 않으며 자동 변환·새 수명 부여도 하지 않는다.
현장 transport가 기존 token 만료·승인/첫 write 예산을 고려한 각 측 마감과 정리 책임을 연결해야 한다.
같은 호스트의 시험 시계를 공유한 결과는 두 호스트의 시계·전송 시간 검증이 아니다.

`delivery_job_parent.ParentJobBuilder`는 검토된 `JobSite.build`에 명시적으로 넣을 수 있는
1회 callable이다. config/plan/finish slot·명령/context를 확인한 뒤 공개 `publish_ready` →
새 요청/마감 확인 → `open_channel` → core 수신 → 기존 handoff 순서로 연결한다.
publish callback이 받은 요청을 바꿔도 저장된 job/nonce는 바뀌지 않는다. publish/open 실패는
해당 receiver를 닫아 무효화한다. 부분 자원을 만들고 반환 전에 실패한 callback은 그 부분을 스스로 정리한다.
open이 정확한 channel을 반환하면 마감이 지났어도 core가 해당 channel 정리를 맡는다.
bundle은 부모가 참조를 정리하고, 성공 반환한 invocation만 기존 driver가 소유한다.
status/gate는 기존 driver 규칙대로 builder·전송부를 호출하지 않는다.

이 helper는 SSH 프로세스·rendezvous 경로·listener를 만들지 않고 임의 프로세스도 종료하지 않는다.
실제 전송 소유자가 SSH 인증·현재 host key·정확한 job 부모와 연결되는 pipe·프로세스 정리와
callback의 유한 복귀를 보장해야 한다. 실제 issuer/권한·운영 채택값·실행 GO는 계속 별개다.
오프라인에서는 스레드의 SYNTHETIC 송신 → 실제 로컬 pipe → 부모 helper → core →
기존 별도 pipe/driver/factory/finish를 검증하며 현장 연결 완료로 보고하지 않는다.
