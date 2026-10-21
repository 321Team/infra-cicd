# #46 실제 Run 결과·종료 관측 연결 구현

이 변경은 PR101의 현장 연결 위에 **GitHub Run/Attempt/job/step 결과 대조**와 **전송 종료 후 finish 관측을 인수하는 시점**을 추가한다. 실제 원본·자격·Run ID·운영값을 만들거나 실제 Runtime을 실행하지 않았다. #46은 In Progress이며 코드·MOCK·CI와 실제 정상/실패 배포 인수는 별도다.

## 공급 판정에 연결하는 native Run 결과

`delivery_actions_source.py`의 `ActionsRunSource`는 검토된 Python 코드의 고정 `RunPin`과 test/build/scan/push 순서의 `StagePin`을 받는다. 별도의 실제 GitHub source reader를 명시적으로 전달하며, ROSA 자격을 GitHub에 보내지 않는다. 비공개 저장소는 기존 별도 credential의 저장소 범위와 로컬 사용 상한을 그대로 적용한다. 해당 GitHub 자격의 **Actions 읽기 가능 여부는 실제 연결 때 확인할 항목**이며 이번 변경이 권한을 부여하지 않는다.

저장소 ID/owner/private 상태를 확인하고, 선택한 Run마다 다음을 같은 절대 기한 안에서 조회한다.

1. 최신 Run의 ID/Attempt/workflow ID·workflow SHA·path/event/branch·repository/head_repository와 completed/success.
2. 정확한 Attempt의 같은 값.
3. 해당 Attempt의 jobs 1페이지(100건 상한). total_count와 수신 개수가 같아야 하며 잘림·빈 집합·중복 ID는 거부한다. 허용한 job ID/name/run ID/workflow SHA와 필수 step의 번호/name이 각각 맞고 completed/success여야 한다.
4. 최신 Run을 다시 읽어 Attempt 변경·실패·재실행을 거부한다.

필수 step이 skipped/neutral/timed_out/in_progress이거나 없으면 job 전체가 성공이어도 거부한다. 요구하지 않은 optional job의 skip은 허용한다. 같은 Run/Attempt 안에서 Test/Build/Scan이 같은 검토된 wrapper step으로 수행되는 경우 조회를 합치며, **개별 단계의 실제 결과는 기존 공급 원본과 검토된 workflow 코드에서 확인해야 한다.** 조회를 합치는 키에서 저장소·Attempt·workflow 버전을 생략하지 않는다. 호출 간 캐시·폴링·재시도·rerun/dispatch·로그 다운로드는 없다.

공식 조회 계약은 GitHub의 [Run Attempt 조회](https://docs.github.com/en/rest/actions/workflow-runs#get-a-workflow-run-attempt), [Attempt별 jobs 조회](https://docs.github.com/en/rest/actions/workflow-jobs#list-jobs-for-a-workflow-run-attempt)를 따른다. 기존 bounded TLS/GET-only reader와 hostname/CA 검증을 재사용한다.

`ActionsCheckedProducer`는 기존 공급 원본 판정 → native Run 관측 → 공급 원본 재판정 순으로 연결하고 두 원본 판정이 같아야 한다. `RuntimeAuthorityFactory`의 선택적 `producer_pins`/`producer_run_reader`를 함께 전달하면 이 경로를 구성한다. 단계별 기존 producer reference가 정확히 연결돼야 하며, 불일치는 credential 수신이나 I/O 전에 거부한다. 지정이 없으면 PR101의 기존 명시적 원본 채택 경로를 유지한다.

**workflow 저장소의 SHA와 앱 저장소의 source SHA는 다른 값일 수 있다.** 예를 들어 infra-cicd workflow와 fds-msa 앱 소스의 계보를 같은 SHA라고 가정하지 않는다. native API는 해당 workflow/Attempt/job/step의 관측이다. 앱 Commit·clean render·Build/Scan/Push의 실제 내용·이미지 digest·P2 채택은 기존 공급 원본 인증에 계속 연결한다. API success나 workflow pin만으로 Registry digest·실제 배포·Health·GO를 생성하지 않는다. `ActionsObservation`도 execution_authorized/app_lineage_authenticated=false이며 raw 응답은 repr에서 숨긴다. 실제 증적 반출은 기존 승인된 비공개 수집 절차를 따른다.

## 정상 실행에서 종료 원본을 인수하는 시점

PR101의 빈 `FinishAdoptionSlot`을 실제로 채울 시점이 main 경로에는 노출되지 않았다. 이전 정상 모의시험은 fake sender.close에서 설치했다. 새 경로는 **기존 run_session이 sender.close에 성공한 뒤** `DeferredFinish`가 `finish_capture(request)`를 한 번 호출하도록 한다. 실제 session progress·Binding·동일 credential·절대 deadline을 전달하므로 미래 progress key나 원본 해시를 미리 정할 필요가 없다.

검토된 Python 부모/collector는 실제 후속 관측과 독점 접근 종료 원본을 확보·검증한 뒤 기존 `EvidenceAdoption`을 반환한다. wrapper는 반환 후 기한·시계·동일 progress·미래가 아닌 관측 시각을 검사하고 한 번 설치한다. 이어서 PR101의 **원본·control 2회 인증을 그대로 수행**한다. callback 반환 자료나 타입만으로 완료·권한 회수·GO가 되지 않는다.

`runtime.main(..., finish_capture=..., producer_pins=..., producer_run_reader=...)`에서 두 연결을 명시적으로 전달할 수 있다. status는 callback·source 조회·pipe 수신을 하지 않는다. preflight도 finish를 호출하지 않으며 실제 적용·종료의 승인이 아니다. 이미 유효한 slot이 있으면 새 capture 없이 기존 원본을 재검증한다. capture 미설정/실패/초과/진행 변경/미래 시각/잘못된 자료는 STOP, fence 유지이고 실패한 capture를 자동 재시도하지 않는다. 기본 단독 CLI STOP은 유지한다.

collector의 I/O는 **그 구현이 request.deadline 안에서 취소·완료해야 한다.** wrapper의 호출 전후 시계 검사만으로 무한 대기하는 Python callback을 강제 취소하지 않는다. 후속 [종료 원본 collector](finish-original-collector.md)는 Linux 별도 프로세스의 실제 원본/control 인증과 단회 hard timeout·kill/reap을 구현한다. 실제 native 원본 형식의 locator·권위/운영 채택은 별도 입력이며 이를 합성해 실행 가능 상태로 만들지 않는다. collector와 source reader는 부모 소유다. adapter close는 로컬 참조 정리이며 실제 토큰 회수가 아니다.

## 실패 경로와 오프라인 시험

- native Test/Scan/필수 step 실패: preflight에서 claim·write sender·capture 없이 중단.
- 배포 중 Run의 Attempt/성공 상태 변경: 다음 인증에서 중단, 실제 ACK/pending/journal과 fence 유지. 자동 rollback/unlock/refresh가 없다.
- 종료 관측 실패: 이미 성공한 적용 결과를 지우지 않고 완료·claim 해제만 차단한다. 기존 읽기 전용 recovery와 원본 대조를 따른다.
- 원본/control 오류·403/404/429/timeout·과도한 jobs/응답·다른 credential/progress는 고정 오류로 중단하며 원문·자격을 예외에 출력하지 않는다.

```text
python -m unittest discover -s tools/phase2 -p test_delivery_actions_source.py -v
python -m unittest discover -s tools/phase2 -p test_delivery_run_finish.py -v
python -m unittest discover -s tools/phase2 -p test_*.py -v
```

시험의 API·Run ID·자격·승인·원본·시계·store는 명시적 합성 fixture이며 socket/DNS는 금지한다. 실제 client/session/provider/adapter/main 함수를 모의 연결한다. 새 정상 경로 시험은 sender.close를 교체해 finish를 설치하지 않는다. Windows main 시험의 로컬 파일 build 경계는 mock이며, 기존 POSIX 입력·pipe·store 시험은 Linux CI 범위다. MemoryStore나 fake TLS를 실제 환경 검증으로 표시하지 않는다.

Claude Code는 job/step 순수 판정 함수 후보, Grok은 Run/앱 SHA·실패 경계, Gemini는 finish lifecycle을 병행 검토했다. 중앙이 실제 코드·시험으로 대조했다. 새 실제 source call·자격 발급/권한 변경·Runner workflow 실행·배포/복구·인수·병합·GO는 수행하지 않았다. 실제값 입력과 운영 채택, 사람 독립 리뷰, 승인된 Runtime 시험·증적 편입이 남아 있다.
