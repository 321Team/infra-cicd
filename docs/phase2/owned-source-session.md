# #46 비공개 원본 자격·현장 CLI 소유 연결

이 변경은 운영 연결 **코드와 오프라인 시험**이다. 실제 운영 원본·승인 scope·자격·대상·경로를 채택하거나 Runtime을 실행하지 않는다. PR107의 최초 배포 입력 수정 위에 쌓은 별도 검토본이며 해당 PR의 사람 리뷰를 대신하지 않는다.

## 기존 경계에 추가한 연결

기존 `GitHubPipeCredentialReceiver`와 private reader는 구현돼 있었지만, 호출자가 ROSA 수신·source 수신·reader 생성·실행·정리를 개별 연결해야 했다. 이제 `delivery_runtime_site.main(..., source_session=...)`의 선택 경로가 그 소유 수명주기를 연결한다. 기존 `readers=` 직접 전달 경로는 유지한다.

- 검토된 Python 코드가 `SourceChannel(receiver, ca_pem, endpoint_ip)`와 `PrivateSourceSession(channels, wire=...)`를 구성한다. 각 receiver에는 기존 exact repository pin과 monotonic 로컬 사용 상한이 이미 명시돼 있어야 한다. 기본 저장소·인증·CA/IP 탐색은 없다.
- `wire(session)`은 `session.reader(repository)`로 만든 reader를 기존 `SourceReaders`에 결속하고 `readers` 및 필요한 `producer_run_reader`·`finish_collection`·`finish_observation`만 반환하는 **신뢰된 코드 함수**다. 새 파일/JSON/환경변수/CLI/plugin 로더가 아니다. 실제 facts·승인·종료 scope는 기존 factory가 계속 검증한다. 다른 공개 reader를 섞으면 그 reader는 호출자 소유다.
- 호출자는 `source_session`과 기존 `readers`/`producer_run_reader`/`finish_collection`/`finish_observation`를 동시에 지정하지 않는다. 해당 역할을 자동 덮어쓰지 않는다. 기존 `finish_capture`·`producer_pins`·`finish_slot`·`finalize` 선택과 상호배제 검사는 유지한다.

## 수신·실패·정리

1. 양쪽 자격을 받기 전에 모든 수신 FD의 `(st_dev, st_ino)`를 비교한다. 동일 FD와 `dup()`된 같은 파이프를 거부한다. 각 기존 receiver의 Linux/pipefs/소유자/읽기전용/EOF/크기 검사는 그대로다. 이 비교는 부모의 숨겨진 복제 FD나 같은 UID 공격을 차단하는 sandbox가 아니다.
2. ROSA와 모든 source 수신·wiring에 하나의 절대 monotonic 기한을 사용한다. 상한은 기존 채널 예산 10초이며 source마다 다시 10초를 주지 않는다. 실패 후 같은 session을 재소비하지 않는다.
3. source token이 ROSA token과 같은 바이트이면 TLS/원본 요청 전에 거부한다. 이 검사는 잘못된 재사용을 막을 뿐 issuer·권한·서버 만료를 증명하지 않는다. private TLS의 exact 저장소 경계·CA/hostname·로컬 cap·fresh 원본/authority 대조와 무재시도 정책은 유지한다.
4. source 소유 receiver/credential/transport는 status, 파서 오류, 부분 생성 실패, 인터럽트와 실제 application 반환 뒤에 모두 정리한다. 잘못된 타입의 임의 close 함수나 빌린 reader는 호출하지 않는다. 로컬 close는 서버 token 폐기·검증권한 회수·메모리 완전 소거가 아니다.
5. 결과는 source 정리 뒤 한 번 출력한다. 정리 실패는 `SOURCE_SESSION_CLEANUP_FAILED`/STOPPED이며 기존 receipt·runtime·fence를 보존한다. 완료된 `RELEASED`를 `RETAINED`로 바꾸거나 이미 실행한 작업을 `NOT_RUN`으로 표시하지 않는다. 기존 실패 journal/claim을 삭제·재개·자동 복구하지 않는다.

`status`는 구성 선언만 표시한다. 자격 수신, wire 호출, TLS 구성, 원본/파일/API 읽기와 claim은 없다. 의미·권한·운영 채택 검증 결과가 아니다. 구성된 `preflight`도 실제 사용하면 원본/API를 읽으므로 이번 검증에서는 합성 경계 밖에서 실행하지 않았다.

## 검증과 운영 잔여

`python -m unittest discover -s tools/phase2 -p test_delivery_source_session.py -v`로 합성 원본/자격·실제 factory/session/reconciler와 MemoryStore를 연결한다. socket/DNS는 금지한다. Linux 전용 시험은 실제 익명 파이프와 `dup` 식별을 확인하며 Windows SKIP을 성공으로 합산하지 않는다. MemoryStore는 POSIX 내구성 증명이 아니다. 기존 POSIX store 회귀 시험을 재사용한다.

실제 원본/의미 채택·종료 범위, issuer/audience·발급/만료·최소 RBAC, 공급/clean render·Registry/Secret 준비, 단일 운영 store/고정 경로와 실제 별도 source/ROSA 자격은 여전히 운영 입력이다. 임의 값·미래 hash·가짜 previous·GO는 만들지 않았다. 이 코드 병합도 Runtime 정상/실패·Health/Rule·복구 인수나 #46 종결이 아니다.
