# #46 job rendezvous 생성·부모 소유권 연결

후속 [SSH 메모리 bridge·운영 SITE 연결 후보](ssh-memory-bridge.md)는 별도 코드다. 기존 rendezvous가 SSH 인증·운영 채택을 대신하지 않는 경계는 유지한다.

`delivery_job_rendezvous.py`는 기존 parent와 Linux 로컬 peer UID 채널 사이에서 남아 있던 **listener 생성·준비 알림·1회 수신·정리**를 명시적으로 연결한다. 실제 SSH 프로세스·host key·계정·운영 경로·SITE를 채택하지 않는다. 이 코드는 기본 자동 선택이 아닌 검토용 후보이며 `SITE=None`과 실행 workflow는 유지한다.

## 경로와 실제 입력의 구분

- `abstract_address(request)`: 기존 core의 공개 native job/Binding/nonce/repository 목록 전체를 canonical JSON→SHA256으로 묶은 Linux abstract address를 계산하는 순수 함수다. 주소는 NUL prefix를 포함한72 bytes이며 token/비밀번호·실제 원본·새 권한은 들어가지 않는다. 없는 run/nonce를 설정값으로 합성하지 않고 실제 receiver가 실행 시 만든 공개 요청만 받는다.
- `UnixJobRendezvous`: 실제 nonroot `expected_uid`·검토된 로컬 peer 매핑·원래 deadline·동일 monotonic·인증된 공개 알림 callback을 **명시적으로** 받는다. constructor는 socket/파일/clock/callback을 실행하지 않는다. `publish_ready`가 Linux local listener를 bind/listen한 뒤에만 기존 서명의 공개 알림을 호출한다.
- `open_channel`: 같은 request 값·마감·소비 상태를 확인하고 기존116의 `accept_unix_ingress_channel`에 소켓 객체 소유권을 넘긴다. 기존 SO_PEERCRED/overflow UID·read 이전 인증·bounded recv/프레임/EOF 검증을 재사용한다. 새 원본·token·grant는 만들지 않는다.
- `OwnedRendezvousBuilder`: 기존 FDS `connect_parent` 또는 `connect_existing_parent`가 만든 정확한 ParentJobBuilder와 owner를 감싼다. bound publish/open·peer·clock·deadline이 같아야 한다. 기존 첫 빈 대상/기존6객체·full/prepared·4/5 reader profile 선택과 원본/control 의미는 원래 연결부가 검증하며 이 wrapper가 대신하지 않는다.

새 filesystem socket pathname·chmod·계정/정책·key·listener 경로 삭제는 없다. Linux abstract namespace는 filesystem permission 통제가 없고 **같은 network namespace**의 프로세스에 보인다. 다른 UID의 연결 시도도 가능하며, 첫 잘못된 연결은 해당 시도를 소진하는 DoS가 될 수 있다. bind 충돌은 STOP하고 자동 이름 변경·재시도하지 않는다. 주소·native job metadata·nonce·로컬 UID 일치는 실행 GO나 원격 사용자/issuer 권위가 아니다. 같은 UID/root/FD alias·callback의 fork/dup에 대한 격리를 보장하지 않는다. [Linux unix(7)](https://man7.org/linux/man-pages/man7/unix.7.html).

## 실제 SSH 연결에 남은 경계

현재 기존 ansible01→Runner 관리 경로 관측은 재사용 가능한 근거이며 현재 연결 성공·새 job 소유권을 뜻하지 않는다. 실제 SSH 방향·일반 계정·current host key와 exact job checkout/helper·같은 netns/UID 매핑은 계속 실제 자료로 채택해야 한다. 새로운 reverse SSH·root·key·sudo·정책·host 수정 요구는 추가하지 않는다.

abstract address의 NUL을 SSH argv나 일반 filesystem path에 그대로 넣지 않는다. 향후 검토된 remote helper가 인증된 공개 request를 받아 **Runner 측에서** 같은 함수로 주소를 도출하고, listener의 실제 로컬 UID와 대상 job을 확인한 뒤 전달해야 한다. OpenSSH의 원격 명령·forwarding 기능 자체를 이 코드가 구성/검증한 것으로 표시하지 않는다. [OpenSSH ssh(1)](https://man.openbsd.org/ssh.1).

공개 요청 전달은 trusted callback의 책임이며 익명 발급 API로 사용하면 안 된다. 발급/공급부는 원격 SSH 인증·현재 권한·GO/issuer를 독립적으로 확인하고 원본/control·token 만료를 기존 factory가 재검증한다. 아직 이 remote helper·실제 발급/채널을 운영 채택하지 않았다. 같은 원본10/10·기존 발급 인가를 취소하거나 전체 재제출/새 승인 단계를 요구하지 않는다.

## 호출 순서와 소유권

1. 검토된 Linux native job의 site Python 구성에서 owner를 만든다. 실제 UID/peer/deadline/clock·공개 알림 전달 함수는 필수 실제 입력이며 기본값은 없다.
2. 기존 FDS connector의 `publish_ready`/`open_channel`에 owner의 해당 bound method를 넣는다. 그 정확한 parent와 owner를 `OwnedRendezvousBuilder`로 묶어 `JobSite.build`에 명시한다. 연결 코드만 준비된 현재는 운영 구성하지 않는다.
3. driver가 native context/일반 Runner·명령을 확인한 뒤 wrapper를1회 호출한다. 기존 parent가 실제 수신 request를 만들고 owner가 같은 주소를 bind/listen·CLOEXEC 설정 후 공개 알림을1회 호출한다. callback에 별도 copy를 주므로 caller의 nonce/repository 변경은 내부 주소/수신 대상에 반영되지 않는다.
4. 성공 알림 후에만 open을 허용하고 같은 값의 request·동일 절대 마감으로1회 accept한다. 실패도 소비된다. open이 동작하는 동안 close는 같은 lock으로 bounded accept 종료까지 기다리며 임의 FD를 중간에 닫지 않는다.
5. 부모의 기존 receive→메모리→pipe→RuntimeInvocation을 마친 뒤, wrapper `finally`가 owner를 정리하고 마감을 다시 확인한다. 정리가 불명확/실패/예외이거나 늦으면 **driver에 넘기기 전 자신의 정확한 invocation을 닫고 STOP**한다. foreign 반환값은 거부하지만 임의 close는 하지 않는다.
6. 정상 반환 뒤의 invocation만 기존 driver가 소유한다. `status/gate`는 builder/listener/공개 알림·자격 수신을 호출하지 않는다. 첫 빈 대상·기존6객체 baseline·issuer/원본 인증·영속 claim/checkpoint·finish 계약과 실제 GO는 그대로다.

## 실패·정리·재실행

공개 알림 전/중/후 예외·interrupt·만료 또는 open 미호출에도 owner의 listener는 close한다. wrapper가 필수 cleanup boundary이므로 임의 raw parent만 설치하지 않는다. callback이 자체 생성한 외부 부분 자원은 callback이 정리해야 하며 무한 대기를 helper가 선점하지 못한다. callback은 같은 마감 안에서 유한 복귀하고 listener를 fork/dup하지 않아야 한다.

close는 소유 socket 객체만 한 번 정리하고 결과를 보존한다. 생성/인수하지 않은 listener나 다른 프로세스·경로는 종료/삭제하지 않는다. abstract 이름은 커널이 open reference가 모두 없어지면 해제하므로 외부 FD alias/fork가 있으면 이 함수만으로 완전 해제를 보장하지 않는다. socket close/Python 참조 해제는 서버 token revoke·보안 삭제가 아니며 실제 발급·전달 상태는 별도로 인수한다.

bind 충돌·잘못된 peer/request·마감·cleanup 불명확이면 중단하며 새 이름·재접속·재발급·배포·rollback·unlock·fence 해제는 하지 않는다. 새 회차가 필요하면 기존 승인/현재 원본·실제 자원 상태에 따라 판단한다. `repr`/공개 오류에 callback text·자격·주소/운영 경로를 넣지 않는다.

## 오프라인 검증

`test_delivery_job_rendezvous.py`는 SYNTHETIC request/token/원본/API만 사용한다. 순수 구성/마감·address 결정성, Linux 실제 local abstract listener/UID, 알림 전 listen·변조·실패/interrupt·충돌·open 미호출·동시 close, wrapper cleanup 불명확/지연·예외 시 미이전 invocation 정리와 기존 driver/factory/finish 연결을 검증한다. Windows Linux-only SKIP과 hosted Linux 실행 결과를 구분한다. 실제 SSH·서버 접속·자격 발급·Runtime·운영 SITE/AC 인수를 대신하지 않는다.
