# #46 SSH 메모리 bridge·운영 SITE 연결 후보

기존6객체·5역할·native job 연결은 후속 [명시 SITE 조립 후보](existing-job-site.md)에서 묶는다. SSH source-host 시계/설정과 job-host 시계/설정은 분리한다.

`delivery_ssh_bridge.py`와 고정 launcher `delivery_ssh_bridge_helper.py`는 명시된 기존 OpenSSH 경로로 공개 job request와 자격 프레임을 메모리 전달하는 후보 코드다. 생성자는 프로세스·소켓·시계·자격을 호출하지 않는다. **SITE는 여전히 `None`이다.** 검증은 가짜 SSH가 로컬 Python helper를 호출하는 오프라인 시험이며 실제 SSH·서버 접속·발급·배포를 하지 않는다.

## 연결 순서

1. 기존 `UnixJobRendezvous`가 해당 job의 network namespace에서 listen한 뒤 인증된 발급측에 공개 request를 알린다. callback은 알림만 마치고 복귀한다. **bridge 완료를 callback 안에서 동기 대기하지 않는다.** 부모가 accept하기 전 큰 frame의 socket backpressure로 교착할 수 있다.
2. 발급측 `SshCredentialBridge(config, deadline=원래_절대_마감, monotonic=같은_시계)`가 `send_from_supplier(request, provide_credentials=실제_공급함수)`를 한 번 실행한다. 공급 함수는 기존 인가·현재 요청·수명을 독립 확인한다. 이미 발급된 자격의 공급과 새 발급을 구분하며, 기수신 원본10/10·기존 발급 인가를 재요구하지 않는다.
3. 고정 절대경로 OpenSSH를 `shell=False`로 실행한다. 숫자 IP/port·일반 사용자·host-key alias·기존 known_hosts/identity 파일·원격 Python/helper·listener UID·remote ceiling 모두 명시 입력이다. 사용자/시스템 SSH 설정, agent·암호·키보드 인증, proxy·multiplex·forwarding·TTY·local command는 끈다. host-key 검증은 **strict yes**, 자동 key 수집·갱신·accept-new는 없다.
4. 원격은 `python -I /검토된/고정/helper.py UID SEC`를 실행한다. launcher는 자신의 검토된 배치 디렉터리만 import 경로에 추가한다. cwd·PYTHONPATH·요청이 코드를 선택하지 않는다. sudo·setns·계정/권한/key 생성 없이 **기존 job과 같은 netns**여야 한다.
5. helper는 크기·canonical 형식을 검사한 공개 request로 abstract 주소를 로컬 도출한다. 실제 `SO_PEERCRED` listener UID를 명시한 job UID와 대조한 뒤 request 해시가 결속된 `ready`를 반환한다. root·현재 overflowuid 등 모호 UID는 거부한다. **listener job UID**와 부모 accept가 검사하는 **helper 송신 UID**를 구분한다.
6. SSH host 인증·고정 helper·`ready`가 맞은 뒤에만 trusted 공급 함수를 한 번 호출한다. 기존 `encode_frame` 자격 프레임을 발급측 stdin pipe에 쓰고 닫는다. 토큰을 argv·환경·파일·로그로 보내지 않는다. 원격 helper의 표준입출력은 아래의 제한된 pipe/socketpair 형식만 받는다. helper는 크기와 EOF를 확인한 뒤 같은 job socket으로 그대로 전달하고 shutdown/close한다. 추가 bytes/frame·다른 ready·무한 대기는 STOP이다.
7. `forwarded`·stdout EOF·SSH rc0·마감 내 자기 정리까지 확인한다. 이 결과는 **전송·close 결과**이며 job 자격 인수·원본/control·issuer·RBAC 인증·배포·회수·Runtime GO가 아니다. 부모는 기존 ingress/pipe/driver/factory/finish 계약을 그대로 수행한다.

두 호스트의 monotonic 값은 비교하거나 전송하지 않는다. 발급측은 원래 절대 마감, helper는 원격 로컬 시계의 명시 ceiling을 한 번만 사용한다. helper ceiling은 발급측 마감을 늘리지 않는다. 동기 공급 callback·프로세스 생성 자체를 선점하지 못하므로 검토된 함수/환경이 유한 복귀해야 한다. 실패 후 자기 child 종료·reap을 위한 최대 1초 강제 정리 대기는 실행 권한 연장이 아니다.

## 원격 표준입출력 형식

OpenSSH의 [no-PTY 실행 소스](https://github.com/openssh/openssh-portable/blob/master/session.c)는 기본 `USE_PIPES` 경로와 별도의 AF_UNIX stream socketpair 경로를 갖는다. 실제 FDS sshd의 빌드·stdio 형식은 아직 미확인이다. 이 호환 코드는 운영 접속 시험이나 새로운 필수 선행검사를 추가하지 않는다.

- 기존 pipe는 서로 다른 inode의 읽기 전용·쓰기 전용 FIFO 한 쌍만 받는다. 기존 `serve_owned_pipes`와 공용 pipe adapter의 범위는 유지한다.
- SSH helper stdio에 한해서 **같은 연결된 무명 AF_UNIX/SOCK_STREAM endpoint의 별도 FD 두 개**를 허용한다. TCP·datagram·listener·이름 있는/abstract 주소·다른 endpoint·file·PTY·혼합 형식은 거부한다. 이것은 peer/issuer 인증값이 아니다.
- 빌린 stdin/stdout은 FD 3 이상으로 복제해 형식을 검사하고 원본 identity를 재대조한다. 임시 socket wrapper와 미이전 복제본만 정리하며 원본 FD를 직접 닫지 않는다. stderr가 닫혀 있어도 FD 2를 잘못 소유하지 않는다.
- 복제 FD의 nonblocking 상태는 같은 open file description의 원본/alias와 공유된다. 이 helper가 stdio를 독점하는 전제를 유지하며, 다른 thread/process의 alias 사용이나 같은 UID/root를 격리한다고 주장하지 않는다. 전역 socket timeout이 설정된 환경은 형식 검증에서 거부한다.
- READY 전 listener UID 대조, 입력 길이/EOF, 기존 절대 마감, ACK/rc, 자기 FD·child 정리 계약은 그대로다. 이 추가 형식으로 SSH 옵션·key·권한·SITE를 자동 채택하지 않는다.

## 운영 SITE의 실제 입력·담당

실행 시 helper에 전달하는 ceiling은 설정 상한과 현재 남은 초(정수 올림) 중 작은 값이다. 전송 지연이나 1초 미만 올림이 발급측 원래 절대 마감을 늘리지 않는다.

| 항목 | 현재 근거·구현 | 채택 담당·완료조건 |
|---|---|---|
| cluster/API·namespace/SA UID·issuer/CA·source/current 2 digest | PR113 고정 profile, 기수신 원본 | 권욱: 실제 원본/control·현재 인가와 materializer 연결. 하영의 기존 제공 값 재제출 불필요 |
| SSH 방향·IP/port/계정·current host key/known_hosts·identity 경로 | 과거 ansible01→Runner 관리 관측은 후보 근거. 현재 구성으로 채택하지 않음 | 권욱: 기존 전달 경로의 실제 구성 채택. 재환: 기존 Runner 정보 대조. 새 reverse SSH/root/key/권한 생성 요구 아님 |
| exact job/helper checkout·코드 버전·같은 netns | 공개 request/nonce 주소와 helper 코드 구현 | 권욱: job의 실제 고정 helper 경로/코드 연결. 재환: Runner 배치 대조. hash/주소가 실행 승인 아님 |
| listener job UID ↔ helper 송신 UID | 양방향 SO_PEERCRED 구현. 과거 Runner UID와 현재 송신 UID는 별개 | 권욱: 실제 매핑 명시. same UID/root·FD 전달 한계의 수용 범위 확인. 새 전용 계정 자동 생성 요구 없음 |
| 인증된 ready 전달·실 공급 함수·기존 마감 | 기존 callback/부모와 bridge API 연결 가능 | 권욱: 현재 인가의 실제 공급/전달 위치와 실패·불명확 상태 인수 담당 연결 |
| 기존 C 6객체 raw·허용 owner·full previous 2개 | PR115 `connect_existing_parent` | 권욱: 마지막 수동 변경 후 실제 raw/owner/control 채택. 재환: previous와 실제 Deployment·생산 근거 대조. current 자동 복사 금지 |
| approval/platform/producer/finish(+baseline) 원본·control·종료 관측 | PR114 역할 배선·기존 decoder/collector | 권욱: 기수신 원본의 의미·pin·scope·finish producer 연결. 준비 JSON/CI를 운영 인증값으로 채택 금지 |

`connect_existing_parent`에 실제 config/plan 또는 materializer, source wiring/finish, owner의 `publish_ready/open_channel`, 같은 peer/clock/deadline을 전달하고 `OwnedRendezvousBuilder`로 감싸 `JobSite`에 연결하는 순서는 유지한다. 문서/준비 자료를 동적으로 읽어 SITE를 활성화하지 않는다. 빈 대상 자동 폴백·fresh GET 기준선 합성·기존 객체 삭제는 없다.

## 실패·복구·한계

- READY 이전 오류에는 공급 함수를 호출하지 않는다. 공급 진입 후 예외·interrupt·마감·부분 송신·ACK/rc 불일치는 재시도하지 않고 실제 발급/인수 상태를 별도로 확인한다. `supply_attempted`는 진입 기록이며 발급·인수·회수 증거가 아니다.
- 자기 pipe/socket/SSH child만 정리한다. child kill은 원격 helper 종료나 token revoke를 보장하지 않는다. helper 자체 timeout/EOF와 원래 자격 수명·인수 절차를 유지한다. 다른 process group·PID·path·listener를 삭제하지 않는다.
- 같은 UID의 다른 프로세스·root, 변경 가능한 helper 코드/key 파일, FD alias, OS 강제 종료, swap/core dump의 비밀 보호는 이 bridge만으로 보장하지 않는다. 참조 해제는 보안 삭제가 아니다.
- abstract 주소는 파일 권한으로 보호되지 않고 선점·첫 연결 DoS가 가능하다. 같은 netns가 아니면 연결하지 못하며 helper가 자동 진입하지 않는다. 주소·UID·ready 해시는 상관관계이며 issuer·GO 증거가 아니다.
- stdout banner/경고는 허용 출력과 다르면 STOP한다. stderr는 저장하지 않는다. 실제 배치/버전·키 파일/코드 소유권 확인 후 운영 채택하며, 이 구현은 그 확인의 완료를 주장하지 않는다.

## 오프라인 시험·근거

SYNTHETIC request/자격과 로컬 Unix IPC만 사용한다. 가짜 SSH가 격리 Python launcher를 실행해 UID→READY→공급→frame/EOF→socket→기존 부모/driver/factory/finish를 연결한다. 인증 전 공급 금지, UID 오차, 출력 오염, 부분 I/O, 추가 bytes, ACK/rc 불일치, supplier 예외/interrupt/마감, child·pipe 정리를 확인한다. Linux 전용 Windows SKIP은 PASS가 아니다.

stdio 추가 시험은 같은 endpoint 복제본을 stdin/stdout으로 받은 로컬 자식 helper만 실행한다. 닫힌 stderr, 잘못된 형식·UID·추가 frame, 일시 EAGAIN, 전역 timeout/공유 flag, 부분 복제 실패·FD 재사용·정리와 소유권을 검증한다. 실제 SSH 서버·실제 자격·운영 SITE는 사용하지 않는다. 조립 후반 실패 시험은 호출자가 빌려준 full plan을 닫지 않고 고정 configuration 오류를 보존하는지도 확인한다.

기술 근거: 원격 인수는 공백 결합되므로 임의 shell 문자열을 받지 않는 [OpenSSH ssh(1)](https://man.openbsd.org/ssh.1), host-key/agent/config 규칙은 [ssh_config(5)](https://man.openbsd.org/ssh_config.5), 생성·timeout·환경 제한은 [Python subprocess](https://docs.python.org/3/library/subprocess.html), abstract namespace·SO_PEERCRED 한계는 [Linux unix(7)](https://man7.org/linux/man-pages/man7/unix.7.html)을 따른다.
