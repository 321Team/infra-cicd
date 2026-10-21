# #46 이미 발급된 자격의 부모 전달 연결부

## 범위

`delivery_job_handoff.build_owned_invocation`은 검토된 `JobSite.build`에서 선택해 호출하는 Python 연결 함수다. 이미 메모리에 받은 ROSA 토큰과 명시적인 저장소별 원본 읽기 토큰을 각각 별도의 Linux 익명 파이프에 담아 기존 `RuntimeInvocation`으로 넘긴다. 자격 발급·로그인·환경변수/파일/argv/stdin 로더·대상 탐색·실제 설정 채택을 하지 않는다. 기본 `delivery_job_site.SITE=None`은 그대로이며 실행 가능한 예제 값이나 운영 GO를 추가하지 않았다.

## 환경·호출 전제와 순서

1. 기존 driver가 native context·코드 HEAD·일반 Runner identity와 명령을 검증한 뒤 검토된 builder를 호출한다. 이 함수는 그 builder의 자원 전달 부분이다. 실행 위치는 승인된 Linux Runner의 검토된 checkout이어야 한다. 지금은 운영 구성과 경로 검증 전이므로 실제 호출하지 않는다.
2. builder가 실제 근거로 채택한 `config`, `plan`, `finish_slot`, `wire`와 이미 발급받은 in-memory 토큰을 명시적으로 넘긴다. 저장소마다 정확한 `RepositoryPin`, CA bytes, endpoint IP, 로컬 사용 상한을 `SourceHandoff`에 지정한다. `SourceHandoff`는 인증 결과가 아니다. 실제 issuer/UID·RBAC·원본·producer·finish 판정은 기존 factory가 맡는다.
3. ROSA/source 바이트 재사용과 중복 저장소를 파이프 생성 전에 거부한다. 최소 권한으로 허용된 여러 저장소에 같은 GitHub 토큰을 쓰는 것은 허용한다. 저장소별 새 토큰 발급을 강제하지 않는다.
4. 부모는 각 파이프를 `CLOEXEC|NONBLOCK`으로 만들고 실제 용량을 읽어 토큰 전체가 들어갈 때 한 번만 쓴다. ROSA는 1~16384 ASCII bytes, source는 20~4096 bytes이며 **실제 파이프 용량이 더 작으면 용량이 우선**이다. 토큰을 자르거나 pipe 크기를 늘리거나 streaming/retry로 우회하지 않는다. short write·EAGAIN·EINTR은 실패다. writer를 닫아 EOF를 만든 뒤 read FD를 기존 수신기에 넘긴다.
5. 전달부터 모든 원본 수신·wire까지 같은 절대 monotonic 마감을 사용한다. 호출자가 준 원래 마감을 보존하며 함수 시작 후 최대 10초로 더 제한한다. 저장소마다 10초를 새로 주지 않는다. 이미 받은 자격을 모두 준비한 뒤 호출해야 하며 사람 입력·발급 대기를 이 예산에 넣지 않는다. 로컬 사용 상한은 서버 만료/회수를 증명하지 않는다.
6. 성공적으로 반환하기 전 생성한 FD·수신기·session은 이 함수가 정리한다. 정확한 invocation 반환 후에는 기존 driver가 소유한다. caller의 문자열·외부 FD·borrowed reader는 닫거나 변경하지 않는다. 수신기 생성 도중 실패해 FD 번호가 재사용되면 dev/inode가 다른 새 소유자의 FD를 닫지 않는다.

이 전달은 같은 프로세스의 trusted Python 코드용이다. 토큰은 argv/env/log에 쓰지 않지만 Python 문자열·복사본·traceback/local 변수의 보안 삭제나 디버거로부터의 격리를 보장하지 않는다. `from None`은 일반 오류 출력에서 원본 예외 연결을 숨기는 용도이며 메모리 보호 수단이 아니다. 임의 바이트코드 사이의 비동기 신호·SIGKILL·프로세스 중단까지 사용자 공간 정리 완료를 보장하지 않는다. 명시적인 실패·callback 중단은 정리를 시도하며 프로세스 종료 시 OS가 FD를 닫아도 서버 토큰이 회수됐다는 뜻은 아니다.

## 정상·실패 판정과 재실행

- 정상 전달은 정확한 typed invocation 반환과 driver의 기존 검사 통과까지다. 파이프 전달 성공·문법 검사·현지 close는 자격 인증·배포 성공·권한 회수·전체 AC 완료가 아니다.
- 구성/토큰/마감 오류는 파이프 생성 전 또는 생성된 부분의 정리 후 고정 `P2_HANDOFF_*` 오류로 중단한다. 부분 수신·후속 실패를 자동 재발급·재Apply·restore·unlock으로 연결하지 않는다. 일부 수신 후에는 같은 invocation을 재사용하지 않는다.
- Runtime에 진입한 뒤 실패하면 기존 driver의 `NOT_VERIFIED / INSPECT_REQUIRED`와 실제 receipt/fence를 따른다. `STOPPED`와 nested `RELEASED/RECORDED`가 함께 있으면 로컬 정리 오류가 배포를 되돌렸다는 뜻이 아니다. 먼저 원본과 영속 checkpoint를 검토한다.
- 첫 배포가 일부 객체를 남기고 중단됐다면 새 기준선을 합성하지 않는다. 지정 운영자가 해당 RUN의 소유 객체와 원본을 비교하고 별도 승인된 정확한 정리 후 404를 확인해 새 RUN을 준비하거나, 실제 채택·검토된 이전 상태를 연결한다. 자동 삭제·잠금 해제·원복은 없다.
- 단기 source 토큰은 허용 저장소의 원본 읽기에 필요한 권한만 사용한다. 로컬 문자열/파이프 close와 서버 만료·회수는 별도다. 이 함수가 서버 수명이나 기존 검증 권한 유지 결정을 바꾸지 않는다.

## 담당자와 실제 채택 전 잔여

| 담당 | 기존 확인 자료로 연결할 항목 |
|---|---|
| 권욱 | 실제 GO binding·source/control 채택·검토된 builder/고정 경로·finish 범위·통합 |
| 하영 | 실제 issuer/subject UID·CA/IP·최소 RBAC·이미 발급된 자격과 수명/전달 사실 |
| 재환 | producer Run/Attempt/Jobs/step·소스/render/digest·Runner 일반 계정/상태와 경로 근거 |

PR109 리뷰의 Runner 후보 `cicd-runner01` / `server01_cicd-runner` / UID 1000은 기존 P1 workflow의 소스상 검사 값이다. 새 현장 관측으로 표시하거나 자동 채택하지 않는다. Runner→ROSA API 443 및 GitHub API 경로는 **NOT VERIFIED**이며 ROSA→사내 DB/Harbor VPN 시험으로 대신할 수 없다. 해당 경로가 실제 채택되면 공유 Runner의 P2 작업은 P1 TBS/Push/Deploy와 겹치지 않게 배치한다. 이 문서를 위해 현장 시험을 실행하지 않았다. 기존 증적을 새 양식으로 다시 요구하지 않는다.

## 오프라인 확인

기존 CPython 3.12·hash-pinned PyYAML 6.0.3 시험 환경에서 checkout 루트의 `python -m unittest discover -s tools/phase2 -p 'test_delivery_job_handoff.py' -v`를 실행한다. SYNTHETIC 문자열·MemoryStore·가짜 API만 사용하며 socket 경계를 차단한다. Windows의 소유권/검증 시험과 Linux의 실제 로컬 파이프·EOF·부분 쓰기 실패·재사용 FD·driver→source→factory→receipt 연결 시험을 구분한다. Linux 전용 SKIP은 PASS로 세지 않는다. 통합 회귀는 기존 `test_*.py` 발견 명령을 사용한다. 실제 운영 Runtime은 별도 미실행이다.
