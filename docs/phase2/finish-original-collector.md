# #46 종료 원본 collector — 수집·인증·기한 내 중단

PR102의 `finish_capture` 실행 지점에 연결할 collector 구현이다. 실제 세션이 sender를 닫은 뒤의 terminal progress를 받아 기존 native 원본을 읽고 인증한다. callback 반환 전후의 시계 검사만 있던 한계에 **별도 Linux 프로세스의 단회 실행·hard timeout·kill/reap**을 추가한다. 실제 원본·운영값·자격·GO를 생성하거나 실제 Runtime을 실행하지 않는다.

## 구성과 실제 수집 경로

`delivery_finish_collector.py`의 `AuthenticatedFinishCollector`는 검토한 Python 구성에서 다음을 명시적으로 받는다.

- `FinishCollectionPolicy`: 정확한 Binding, 허용 원본 위치, 기존 채택 control pin, 판단 주체·채택 근거·유효기간.
- 원본 위치는 `BlobLocation(repository, path, anchor_ref)` 또는 `CommentLocation(repository, issue_number, creator_id)`이다. 미래 commit/hash/comment ID를 예측하지 않는다. 실제 원본을 확보한 뒤 locator가 반환하는 pin을 허용 위치와 대조한다. `creator_id`는 최초 계정 식별자이며 본문 작성·종료 권한의 증거가 아니다.
- `SourceReaders`: 기존 공개/비공개 GitHub 원본·control reader. 기존 CA/hostname/숫자 endpoint·GET-only·응답/시간 상한을 재사용한다. GitHub 자격은 기존 별도 저장소 범위·로컬 사용 상한을 따른다. ROSA 자격을 GitHub 인증에 쓰지 않는다.
- `credential`·`progress`: 실제 session/factory와 같은 credential 객체와 실제 durable progress closure.
- `locate(request)`: 기존 native 원본의 의미와 권위를 채택한 **검토된 사이트 코드**. 실제 종료 이후 확보한 `EvidenceAdoption`을 반환한다. 파일/JSON/CLI/env에서 임의 권한을 로드하는 callback이 아니며, 실제 원본 없이 예측 facts·해시를 만들 수 없다. 기존 원본 형식을 해석하는 역할이므로 새 생산자 양식·새 GO·별도 서명 키를 요구하지 않는다.
- `max_seconds`: 원본 조회와 프로세스 회수에 쓸 시간 상한. 0.05~120초 범위의 명시적 코드 설정이며 실제 승인의 기간을 늘리지 않는다.

실제 main 경로에는 자격·progress를 아직 갖지 않은 `FinishCollectorConfig(policy, readers, locate, max_seconds)`를 `runtime.main(..., finish_collection=...)`으로 전달한다. `RuntimeAuthorityFactory`가 자격 수신 뒤 실제 session progress closure를 알게 되는 시점에 collector를 bind하고 bundle 소유로 등록한다. 따라서 부모가 미래 progress를 만들거나 sender.close를 교체해 설치할 필요가 없다. policy Binding과 원본/control reader가 해당 factory의 finish 설정과 같아야 하며 기존 `finish_capture`와 동시 지정은 거부한다.

기존 `finish_capture` callback 경로는 유지한다. core/기본 CLI에 기본 collector나 환경 탐색·자동 설정은 추가하지 않았다. factory가 만든 collector는 성공·실패·부분 구성 후 bundle이 로컬 참조를 정리하고, borrowed reader의 수명은 부모가 관리한다. collector의 `close()`는 공유 reader·실제 토큰·운영 권한을 회수하지 않는다. status에서는 자격 수신·bind·worker가 모두 0회이고 preflight도 worker를 호출하지 않는다.

실제 호출 순서는 다음과 같다.

1. 같은 request/Binding/credential/progress·유효기간·terminal journal/응답을 검사한다. 불완전 진행이나 다른 요청은 worker와 원본 I/O 전에 거부한다.
2. `min(max_seconds, request 잔여시간, 채택 권한 잔여시간)` 안에서 Linux worker를 한 번 시작한다. 원본 수집 전에 같은 capture를 소비 처리하므로 실패·지연 후 재시도하지 않는다.
3. worker가 실제 종료 원본을 locator로 선택·해석한다. 결과의 control·principal·채택 근거·기간·Binding이 코드 정책과 같아야 한다. RUN/action/progress·관측시각·후속 관측과 독점 종료 참조를 대조한다.
4. worker가 `ReviewedEvidenceSource`와 `CodeAdoptedEvidenceResolver`로 **원본 → control → 원본 → control**을 읽고 인증한다. 원본 없이 pin/facts만 반환하면 실패한다. hash·타입·API 성공·계정 식별자 자체를 종료 권위로 올리지 않는다. 실제 의미 채택 코드가 신뢰 루트이며 JWT/CI attestation 자동 검증기가 아니다.
5. 원문·자격을 넣지 않은 내부 JSON 메타데이터만 단일 writer 익명 pipe로 넘긴다. 32KiB 상한·정확한 키 집합·중복 키/NaN 거부·이번 nonce/Binding/credential 지문/progress 대조를 적용한다. 이 frame은 외부 승인/생산자 제출 형식이나 보존용 원본이 아니다. 새 nonce는 IPC 재전송 방지용이며 승인·서명이 아니다.
6. 부모는 worker의 **정상 종료와 pipe EOF 모두**를 확인하고, 기한·시계·실제 진행과 정책을 다시 대조한다. 내부 frame만으로 완료하지 않는다. 기존 `DeferredFinish`가 원본/control을 다시 두 번 fresh 인증한 뒤에만 session의 완료·claim 해제를 시도한다. 반환 뒤 원본 변경·철회도 이 단계에서 거부한다.

worker의 실제 인증과 부모의 완료 직전 인증은 경계가 다르다. 같은 시험·자료 재제출을 반복 요구하는 절차를 추가한 것이 아니다. 기존 이미 설치된 유효 slot은 PR102대로 새 collector 없이 재인증한다. status/preflight는 collector·worker를 호출하지 않는다.

## 기한·프로세스·이식성

`delivery_finish_worker.py`는 Linux main thread, Python thread 1개, 기본 SIGCHLD 처리 환경에서만 동작한다. 지원하지 않는 Windows·다중 스레드·사용자 SIGCHLD handler 환경은 고정 오류로 중단한다. thread/exec/shell/pickle fallback은 없다. 다른 운영체제 지원을 주장하지 않는다.

부모는 주입한 증적 시계와 별개인 `time.monotonic()`으로 pipe와 자식 종료를 감독한다. 전체 timeout 안쪽에 최대 1초(짧은 예산은 1/4)의 정리 여유를 예약하고, 그 전에 결과가 없으면 process group과 leader에 SIGKILL을 보내고 WNOHANG으로 직접 자식을 회수한다. 실패·EOF 지연·partial/과대 결과·비정상 exit도 같은 정리를 거친다. `waitid(WNOWAIT)`로 leader를 먼저 관측하고 PID를 예약한 상태에서 그룹 정리 후에만 reap하므로, 이미 재사용된 PID의 그룹을 정리하지 않는다.

자식은 새 session/process group을 만들고 stdin/stdout/stderr를 `/dev/null`로 돌린다. 내부 결과 pipe 외의 상속 FD를 닫아 부모 store/자격 채널의 FD가 남지 않게 한다. 부모 메모리의 검토된 설정·자격은 fork로 복사되며 argv/env/파일로 옮기지 않는다. fork는 자격 격리·메모리 소거·보안 sandbox가 아니다. 부모와 같은 OS 사용자로 실행하는 신뢰 코드이고, 외부 부작용을 되돌리거나 악의적인 `setsid` 탈출 코드를 격리한다고 주장하지 않는다. locator의 허용 행위는 기존 원본의 읽기·해석뿐이다. 새 파일 게시·자격 발급/회수·권한 변경·서버 실행은 포함하지 않는다.

kill/reap 자체가 기한 안에 확인되지 않으면 CLEANUP_FAILED이며 완료로 올리지 않는다. 커널의 무응답 상태까지 제어할 수 있다는 보장은 없다. 실패한 collector는 소비 상태로 남고, session의 ACK/journal/fence를 지우거나 자동 rollback/unlock/replay하지 않는다. 원본·fence 대조와 후속 복구는 기존 읽기 전용 recovery/인수 절차를 따른다.

Python 공식 문서의 [fork와 다중 스레드 주의](https://docs.python.org/3/library/os.html#os.fork), [waitid/WNOWAIT](https://docs.python.org/3/library/os.html#os.waitid), [selector의 pipe 지원 차이](https://docs.python.org/3/library/selectors.html)를 따른다. 운영 최소 Python/Linux와 실제 실행 위치·단일 스레드 여부는 사이트 연결 때 확인한다.

## 검증과 잔여

저장소 루트에서 다음 시험은 합성 원본·승인·자격·API와 로컬 일회용 프로세스만 사용한다.

```text
python -m unittest discover -s tools/phase2 -p test_delivery_finish_collector.py -v
python -m unittest discover -s tools/phase2 -p test_*.py -v
```

Windows는 동기 worker 대역으로 정책·strict frame·원본/control·상태/세션/fence를 시험한다. Linux CI는 실제 fork/pipe·32KiB·hang/exit/EOF·자식/그룹 정리·FD 보존 및 실제 session/provider와 collector를 모의 연결한다. 네트워크·DNS는 금지한다. Linux 로컬 프로세스 시험을 실제 ROSA/Runner Runtime으로 표시하지 않는다.

**남은 운영 연결:** 실제 native 종료 원본 형식의 locator 구현/채택과 실물 원본·권위 근거, 실제 source/credential/endpoint·권한·현장 구성, 승인된 정상/실패 배포·복구·Health/Rule Smoke 인수. collector/IPC 프레임을 만들었다는 이유로 이 입력이 확보됐거나 #46 AC가 충족됐다고 선언하지 않는다. 실제값 합성·Runtime·새 GO·티켓 종결은 수행하지 않았다.
