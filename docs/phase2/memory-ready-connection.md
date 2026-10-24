# #46 메모리 공급·READY 왕복 연결

이 문서는 코드 연결 계약이다. **로그인 provider와 실제 배치 입력은 미공급이며 SITE=None, Runtime NOT_RUN을 유지한다.** 기존 제출 자료와 미확정 값은 [운영 인계표](operating-handoff.md)를 기준으로 한다.

## 연결 순서

1. Runner의 검토된 native job은 실제 context/revision과 기존 6객체 설정을 검증한다. `assemble_observed_job`에 `ready_context`를 명시하면 기존 5역할 original/control과 post-close finish 구성을 유지하며 `ReadyMailbox`를 연결한다. 기존 명시 `publish_ready` 방식과 동시에 사용할 수 없다.
2. job의 자격 listener를 먼저 연다. 공개 mailbox는 context·Binding·repository 순서의 digest로 만든 abstract UNIX 주소만 사용한다. nonce는 실제 ingress가 생성한다. 파일이나 상시 daemon을 만들지 않는다.
3. 발급측 trusted caller는 같은 context/Binding/repository selector와 `build_ready_pull_configuration`을 `SshReadyPull`에 전달한다. 기존 ansible01→Runner 관리 SSH 방향이다. 고정 helper 이름 이외의 SSH trust·Python·UID·기한은 자격 bridge와 같아야 한다.
4. remote helper는 현재 job과 같은 netns에서 mailbox에 접속하고 양쪽 SO_PEERCRED를 대조한다. canonical request → digest ACK → EOF·helper RC0를 확인한다. 공개 ACK까지만 기다리므로 다음 자격 전달을 기다리는 교착을 피한다. 같은 UID 전체를 신뢰하는 기존 호스트 경계이며 악성 동일 UID 프로세스 격리를 보장하지 않는다.
5. `MemoryOperatingSupplier`는 이미 메모리에 있는 전용 actor 채널과 허용 repository별 source 자격을 복사 인수한다. 공개 request가 selector와 일치하면 기존 users/~ 본인 확인→별도 SSH helper READY→TokenRequest 1회→전달→자기 OAuth 회수 1회를 호출한다. 파일·환경·argv·로그로 자격을 읽거나 로그인 세션을 탐색하지 않는다.
6. job은 기존 private source 인증과 original/control 5역할을 통해 계획을 조립한다. 실제 materializer와 grant/control pin은 명시 입력이다. sender close 뒤 관측·control 검증을 재사용하고 철회/누락이면 fence를 유지한다. 미래 원본·승인을 만들어 넣지 않는다.

helper 둘은 **같은 검토 SHA의 job checkout** 아래 둔다. 실제 port/host alias·설치·netns가 확인되지 않으면 실행 구성이 아니다. 발급측과 job은 각 호스트의 monotonic 기한을 따로 사용하며 remote helper는 제한된 duration으로 자기 clock 예산을 시작한다. 공개 mailbox 연결 실패를 자동 재시도하지 않는다. job이 준비된 뒤 발급측을 한 번 시작한다.

## 정상과 실패 인수

| 경우 | 결과·조치 |
|---|---|
| 공개 READY 성공 | 자격 없는 request의 일치·전달만 확인. 실행 승인·actor 인증·job 수용 완료가 아님 |
| 공개 READY 실패/만료/peer 불일치 | 발급/API 호출 전 중단. 전용 actor 서버 회수는 NOT_ATTEMPTED이므로 권욱의 수동 회수 인수 대상 |
| 본인 인증/발급/전달 실패 | 기존 PR133 계약 유지. 자동 재발급·재전달 없음. 발급됐으면 비공개 receipt 1회 인수 가능 |
| 취소와 서버 회수 실패 동시 | 취소 의미 유지, 회수 UNCONFIRMED. 로컬 close는 서버 회수·SA 만료·자원 정리 성공이 아님 |
| source/control 철회 또는 관측 없음 | 기존 claim/fence 유지. 마지막 원본·progress를 인수하고 무조건 완료로 변경하지 않음 |
| 구성 철회 | 미실행 연결은 폐기하고 SITE=None 유지. 실행 중이었다면 승인된 자원 복구 절차가 별도로 필요 |

Python 참조 해제는 zeroization이 아니다. caller는 자신이 가진 사본도 정리해야 한다. `take_receipt()`에는 SA token이 들어 있으므로 비공개 인수하며 공개 PR/댓글/CI에 넣지 않는다. OAuth 수명은 미기록이며 추정하지 않는다. 실제 수동 회수에는 하영 회신의 본인 `useroauthaccesstokens` 조회/만료 확인·삭제 경로를 참고하되, 현재 권한과 대상 일치를 따로 확인한다.

## 검증 범위

오프라인 시험은 SYNTHETIC 데이터만 쓴다. 메모리 공급 시험은 HTTP 소켓을 fixture로 대체하고 SSH 실행을 차단한다. Linux 시험은 OpenSSH 대신 로컬 Python helper를 실행하여 공개 request·ACK·UID·EOF·RC와 기존 자격 bridge·native job·원본/control·finish 연결을 검사한다. 실제 클러스터·SSH·로그인·발급·삭제·배포 검증으로 확대하지 않는다.

정상 외에 잘못된 UID/selector/nonce, 비정상 ACK/EOF/child RC, 기한 만료, mailbox 선점, 중복 호출, 실패·취소 후 receipt/회수 불명확을 시험한다. 실제 grant/endpoint/SSH trust/현재 baseline이 없으면 offline PASS여도 운영 SITE는 미완료다.
