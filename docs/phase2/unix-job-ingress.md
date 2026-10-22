# #46 Linux 로컬 peer UID와 job 수신부 연결

`delivery_unix_ingress.py`는 검토된 현장 전송부가 **이미 독점 소유하는 Linux Unix stream 소켓 객체**를 기존 `ReviewedIngressChannel`로 연결한다. socket/listener/경로/SSH 프로세스·정책·계정·키를 만들지 않으며 운영 `SITE=None`과 실행 workflow는 유지한다. 실제 SSH 인증·현재 host key·issuer·원본/control·GO를 이 모듈에서 채택하지 않는다.

## 실제값과 독립적인 구현

| API | 인수/확인 | 결과 |
|---|---|---|
| `unix_ingress_channel` | 이미 연결된 독점 소켓, 정확한 공개 request, 검토한 nonroot `expected_uid`와 로컬 UID→PeerIdentity 매핑, 절대 deadline·동일 monotonic | 기존 core에 전달할 채널. authenticate가 실제 커널 SO_PEERCRED를 읽기 전에는 비밀 read 불가 |
| `accept_unix_ingress_channel` | 이미 listen 중인 독점 소켓과 위 인자 | 같은 마감 안에서 accept 1회, listener FD 닫기, 연결된 채널만 전달. 잘못된 peer를 버리고 다음 peer를 자동 대기하지 않음 |

Linux `SO_PEERCRED`의 PID·UID·GID는 커널에서 직접 읽는다. request/JSON/argv가 주장한 사용자 값은 받지 않는다. uid_t/gid_t는 unsigned로 해석하며 root·uint32 sentinel은 인수하지 않는다. 인증 때 `/proc/sys/kernel/overflowuid`를 최대32 bytes로 읽어 peer 조회 전후 값이 같고 peer UID가 그 값이 아님을 확인한다. 매핑되지 않은 namespace UID가 공통 overflow UID로 축약되는 것을 권한으로 채택하지 않으며, 기본값을 추측하거나 sysctl을 변경하지 않는다. 조회 실패/변경은 비밀 read 전 STOP이다. 이 로컬 OS 조회는 원본/승인 파일 로더가 아니다. [Linux uidgid 정의](https://github.com/torvalds/linux/blob/v6.12/include/linux/uidgid.h), [user_namespaces(7)](https://man7.org/linux/man-pages/man7/user_namespaces.7.html).

PID 양수·UID 정확 일치 뒤에만 명시적으로 검토한 로컬 peer 매핑을 반환한다. 이 credential은 **연결 시점의 로컬 프로세스** 정보이며 SSH 로그인 방식·원격 개인/호스트·현재 sender의 계속된 동일성을 증명하지 않는다. 같은 UID/root·소켓 FD alias/다른 프로세스로의 FD 전달은 이 모듈의 격리 보장 밖이다. 현장 transport는 소유권·현재 SSH 인증과 실제 issuer/원본 권위를 별도로 연결한다. [Linux unix(7)](https://man7.org/linux/man-pages/man7/unix.7.html), [Python socket](https://docs.python.org/3/library/socket.html).

core는 같은 native job/Binding/nonce·repository 순서와 프레임/EOF를 계속 검사하고, 기존 factory가 실제 자격/원본/control/current-RBAC를 검증한다. nonce·socket 경로·타입 생성·UID 일치만으로 배포를 허용하지 않는다.

## job 부모에서 선택할 위치·실행 전제

1. 승인된 Linux job의 검토된 builder에서만 선택한다. 실제 일반 Runner 계정·UID, 현재 SSH 상대/host key, 독점 listener와 실제 job rendezvous, 원본·issuer·finish 구성은 여전히 운영 채택 잔여다. 없는 값은 입력하지 않는다.
2. `ParentJobBuilder.publish_ready` 소유자가 listener의 실제 생성·경로 권한·정확 job 연결 및 공개 요청의 인증된 전달을 책임진다. public request에 자격은 없으며 issuer는 원격 요청을 독립적으로 인증해야 한다. 이 모듈은 익명 발급 API가 아니다.
3. `open_channel(request, deadline)`에서 명시적으로 `accept_unix_ingress_channel`을 호출한다. 또는 검토된 transport가 이미 연결한 소켓을 `unix_ingress_channel`로 넘긴다. 부모와 같은 호스트의 monotonic을 사용하고 다른 호스트의 monotonic 숫자를 전송하지 않는다.
4. socket 객체는 실제 Linux AF_UNIX/SOCK_STREAM 형태를 확인한 지점부터 소유한다. 이후 설정/연결 오류도 자신이 받은 그 socket 객체만 닫는다. foreign 객체/TCP/다른 플랫폼으로 형태 자격이 거부되면 caller가 계속 소유한다. raw FD 번호를 받아 닫거나 복제하지 않는다.
5. listener 인수 전 발생하는 publish 실패·중단은 원래 listener 소유자의 `finally`가 정리해야 한다. helper가 닫는 것은 인수한 socket 객체뿐이며, **파일시스템 socket pathname·상위 경로·다른 프로세스는 삭제/종료하지 않는다.** 경로/등록 자료의 정리 결과는 transport 소유자가 확인한다. 새 일반 callback 정리나 상태 필드를 기존 부모에 추가하지 않는다.

정확한 RuntimeInvocation 반환 전/후 기존 부모·driver의 소유권 이전은 동일하다. status/gate 또는 미구성 SITE를 이 helper가 우회하지 않는다. 실제 환경에서 실행하는 명령·정책·GO는 이번 코드/오프라인 시험에 포함되지 않는다.

## 마감·실패·복구

- accept와 매 recv는 최초 절대 deadline의 **남은 시간**을 timeout으로 사용한다. 더 긴 deadline 인자로 수명을 늘릴 수 없고 한 번 줄인 상한은 복구하지 않는다. 시계 역행/NaN·늦은 auth/read·listener 정리 뒤 만료도 거부한다. [Python timeout 동작](https://docs.python.org/3/library/socket.html#notes-on-socket-timeouts)을 기준으로 실제 Linux 시험을 구분한다.
- read는 인증 성공 뒤에만 최대4096 bytes/EOF를 반환한다. 기존 core의 단일 프레임·잘림/추가 바이트 거부를 그대로 사용한다. 실제 kernel UID 불일치/다른 request는 비밀 read 전에 중단한다. 최초 인증 실패도 소비된다.
- cleanup 불명확이면 살아 있는 채널을 반환하지 않는다. 연결 초기화에서 발생한 cleanup 실패도 accept의 일반 실패로 낮추지 않으며 뒤의 no-op close로 성공 처리하지 않는다. 예외·인터럽트의 원문은 숨기고 고정 코드 또는 정제된 인터럽트만 올린다. 기존 ParentJobBuilder를 통해 발생한 새 adapter 오류는 부모의 기존 고정 실패 코드로 보고되며 runtime 진입이나 GO가 아니다.
- listener accept는 1회이며 실패한 listener는 닫힌다. 자동 재연결/발급/배포·현재 객체 삭제·fence 해제는 없다. 운영 재시도 전 transport 소유자는 실제 소켓/경로·이미 발급한 자격/서버 회수·driver/claim 상태를 확인한다. 소켓 close나 Python 참조 해제는 token revoke/보안 소거가 아니다.

## 검증 범위

`test_delivery_unix_ingress.py`는 SYNTHETIC token/원본/API와 Linux **로컬** Unix socket만 사용한다. kernel peer UID·read 전 거부, 마감·프레임/EOF·잘림, accept/정리 실패·지연, 실제 overflow 설정/합성 namespace 변환·unsigned UID, 기존 ParentJobBuilder→로컬 pipe→native driver→source factory/finish 연결을 검사한다. Windows에서는 Linux16건을 명시적으로 SKIP하고 순수 구성/마감5건을 실행한다. 실제 namespace/계정/UID를 변경하지 않는다. SKIP을 통과로 세지 않으며 Linux hosted CI의 해당 HEAD 로그를 별도로 확인한다.

설계 중 제안한 pathname listener 전체 구현은 실제 경로·소유권 입력과 분리하기 위해 추가하지 않았다. filesystem 경로 없이 Linux abstract listener 생성/부모 정리를 연결하는 명시적인 후속 후보는 [job rendezvous 소유권](owned-job-rendezvous.md)에 정리한다. 실제 listener/rendezvous·SSH bridge·current host key·운영 SITE의 채택은 계속 잔여이며, 이 코드는 연결 가능한 한 부분의 STATIC/MOCK/CI 결과다. 실제값 합성·SSH/서버 접속·자격 발급·Runtime·AC 인수를 뜻하지 않는다.
