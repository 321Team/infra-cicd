# #46 GitHub 종료 원본 locator — 실제 버전 탐색과 의미 해석 분리

종료 원본의 미래 commit·hash를 미리 정할 수 없는 문제를 처리한다. `delivery_finish_locator.py`의 `NativeFinishLocator`는 검토한 저장소·경로 또는 이미 존재하는 정확한 댓글 ID에서 GitHub 응답을 읽고, **실제로 관측한 버전과 본문 해시**를 기존 pin reader로 다시 검증한다.

이 구현은 GitHub 원본의 위치·버전을 찾는 연결 코드다. 후속 관측·독점 접근 종료를 나타내는 **실제 생산자 본문 형식과 그 의미·판정권 채택은 아직 미연결**이다. 실제 원본을 확인하기 전에 새 제출 양식이나 기본 decoder를 만들지 않았다. 해시 일치·API 성공·댓글 최초 생성 계정은 종료 판정이나 실행 GO가 아니다.

## 구성과 호출 위치

검토된 Python 사이트 구성에서 다음을 전달한다. 운영값을 JSON·환경변수·CLI로 자동 탐색하거나 로드하지 않는다.

- 기존 `FinishCollectionPolicy`: 같은 Binding, 정확한 위치, 채택 control pin, 주체·근거·기간. locator가 권한 범위나 유효기간을 새로 만들지 않는다.
- 기존 공개/비공개 `GitHubOriginalAdapter`: CA·hostname 검증, 지정 숫자 endpoint, 허용된 GET 경로, 응답·시간 상한을 재사용한다. 비공개 원본에는 해당 저장소로 한정한 기존 자격 reader가 필요하다. ROSA 자격을 GitHub에 보내지 않는다.
- 필수 `decode(request, original)`: **실제 본문 형식과 의미를 검토한 코드**이며 `FinishFacts`를 반환한다. 본문의 실제 RUN/action/progress·관측시각·후속 관측/독점 종료 근거를 읽어야 한다. 없는 값을 현재 request에서 복사하여 사실로 만들면 안 된다. 본문 의미와 권위의 채택은 이 검토 코드의 책임이며 API가 대신 증명하지 않는다.
- 댓글 방식은 실제로 확보된 `comment_id`도 명시한다. 댓글 목록의 최신 항목이나 작성자 이름으로 자동 추정하지 않는다. 기존 source 계약과 같이 issue 댓글 URL만 사용하며 PR 댓글 URL로 확대하지 않았다.

`NativeFinishLocator` 인스턴스를 기존 `FinishCollectorConfig(..., locate=...)`에 연결한다. main/factory가 실제 credential과 progress를 아는 시점에 collector를 bind하며, sender close 뒤 terminal 진행에서만 호출한다. **status/preflight에는 원본 수집을 추가하지 않는다.** 부모가 미래 진행 증적을 만들거나 sender.close를 바꿔 넣을 필요가 없다. 실제 `decode`·원본 위치·권위가 준비되기 전에는 운영 연결 완료로 표시하지 않는다.

## 실제 탐색과 재검증

| 방식 | 탐색·고정·기존 검증 연결 |
|---|---|
| Blob | 저장소 numeric ID/owner ID/공개 여부 → 지정 ref의 실제 commit → commit tree → 각 경로 노드 → 실제 blob의 base64·size·Git SHA-1 → 본문 SHA256 계산 → ref 재조회 → 기존 `reader.blob(new_pin)`의 소속/경로/본문/anchor fresh 검증 |
| 댓글 | 저장소 식별 → 정확한 댓글 ID의 API/issue/html URL·creator numeric ID·created_at≤updated_at·UTF-8 본문 상한 → 실제 updated_at/본문 SHA256 pin → 기존 `reader.comment(new_pin)` fresh 검증 |

본문 크기는 기존 256KiB 상한을 따른다. 잘린 tree·중복 경로·symlink/submodule·잘못된 Git object/크기/인코딩은 거부한다. Git blob object ID는 SHA-1이고 본문 무결성 pin은 SHA256이다. 두 값을 같은 것으로 표현하지 않는다. 상세 원본과 조회 응답은 repr/고정 오류문에 넣지 않는다.

댓글 `updated_at`과 blob을 담은 Git commit의 `committer.date`는 명시적 UTC 초 단위로 해석해 `policy.starts_at ≤ metadata 시각 ≤ effectiveNow < expires_at`을 확인한다. local timezone을 사용하지 않으며 author.date를 대신 쓰지 않는다. 시각 누락/잘못된 형식·과거/미래 원본은 기존 pinned reader와 decoder 호출 전에 거부한다. metadata를 읽는 실제 GET 자체가 0회라는 뜻은 아니다. 기존 reader 재조회와 본문의 실제 관측시각·RUN/progress 검증은 유지한다.

이 조건은 명백히 오래되거나 미래인 envelope의 추가 거부 조건이다. **commit 시각은 게시 시각 증명이나 서명·종료 권위가 아니며 작성자가 지정할 수 있다.** 초 단위 경계의 불확실성도 자동 완화하지 않는다. 실제 생산자가 옛 committer 시각을 보존하는 흐름이라면 이 조건과의 호환을 원본 채택 때 검토하고, 그 전에는 자동 fallback이나 수집 완료를 주장하지 않는다. 이번 코드 리뷰용 보완을 기존 AC의 새 승인 절차로 확대하지 않는다.

버전 변경 시 새 pin으로 재탐색하거나 이전 본문으로 대체하지 않는다. locator 호출은 성공·실패 모두 1회 소비된다. 기한 초과·시계 역행·다른 Binding·불완전 진행·원본 변경·해석 실패는 STOP이다. `discover`의 내부 탐색 결과에도 `execution_authorized=False`, `body_meaning_authenticated=False`를 유지하며 그것만으로 완료를 설치하지 않는다.

의미 해석 뒤 기존 collector는 원본/control을 두 번 인증하고, 부모 DeferredFinish는 완료 직전 다시 fresh 인증한다. 이번 locator의 탐색 hash만으로 claim/fence를 해제하지 않는다. 실패 시 기존 ACK/journal/fence를 보존하며 자동 재시도·replay·rollback·unlock하지 않는다. `close()`는 로컬 참조만 정리하며 borrowed reader나 실제 권한/토큰을 회수하지 않는다.

## 현재 원본 형식 확인 범위와 책임

2026-10-21 검토 시점의 #46 기록·현행 코드·증적 저장소 tree에서 P2 종료 의미를 해석할 실제 생산자 본문을 확인하지 못했다. 이 조회 범위를 넘어 해당 원본이 존재하지 않는다고 단정하지 않는다. 담당자의 기존 원본/생성 코드 확인이 진행 중이며 새 양식이나 중복 제출을 요구하지 않는다.

확인한 원본·생성 코드와 그 한계는 [추적표](finish-native-source-audit.md)에 기록한다. `P1-CICD`의 실제 복원 관측 생성 코드는 찾았으나 P2에 전용하지 않았다. 확인된 P2 호출 순서에는 종료 본문 생산자가 연결돼 있지 않아, 이를 팀원의 누락 제출로 돌리지 않고 **#46 자체 생성·연결 구현 잔여**로 분류한다. 지금 필수 decoder에 넣을 확인된 P2 본문 형식은 없다.

- `P2-CICD-session-1`은 같은 세션의 finish 이후 생성되므로 그 세션의 finish 입력으로 사용하면 순환 의존이다. `P2-CICD-operational-1`도 이 결과를 감싼다.
- `P2-CICD-private-diagnostic-*`는 진단 자료다. P1 receipt도 P2의 독점 접근 종료 권위가 아니다. 알려진 이 형식들은 잘못된 decoder가 승격하려 해도 거부한다. 이 거부 목록에 없다는 사실이 다른 본문 형식의 수용이나 인증을 뜻하지 않는다.
- 실제 `Claim.complete`가 만드는 `P2-CICD-complete-1`도 finish 통과 뒤의 로컬 marker여서 같은 RUN의 finish 입력이 될 수 없다. apply-core/journal/claim/active와 recovery-inspection은 로컬 진행/잠금/검사 기록이며 종료 의미·권위를 증명하지 않는다. 확인된 이 형식들도 거부한다.
- **권욱:** #46 collector/locator·실제 본문 decoder 연결 구현, 코드 검토 대응, 최종 인수 판정. 수동으로 종료 원본을 꾸며 작성하거나 별도 파일을 제출하는 선행 업무가 아니다.
- **실행/원본 생산 담당:** 현재 사용 중인 종료·후속 관측 기록과 그 생성 코드/위치를 확인한다. 구체적 담당자·경로는 기존 담당자 회신으로 확인하고 임의 확정하지 않는다.

남은 완료조건은 실제 원본/생성 코드와 권위 근거 확인 → 해당 형식 decoder 구현·실물의 비공개 검토 및 필요한 오프라인 시험 → 사이트 구성·credential/source 연결 검토 → 별도 승인 범위의 정상/실패 배포·복구·Health/Rule Smoke Runtime 인수다. 이번 PR은 운영 원본 확보·Runtime 성공·#46 AC 완료·티켓 종결을 뜻하지 않는다.

## 검증·이식성·복구

저장소 루트에서 다음은 합성 API/본문·자격과 로컬 시험 대역만 사용하는 오프라인 시험이다. 테스트 전용 JSON은 생산자 제출 형식이나 실제 승인값이 아니다. Windows는 정책·본문·pin/부모 연결 시험을, 기존 Linux CI는 전체 suite와 실제 fork/pipe 정리 시험을 수행한다. collector의 Linux main-thread/단일 thread/기본 SIGCHLD 전제는 [기존 설명](finish-original-collector.md#기한프로세스이식성)을 유지한다.

```text
python -m unittest discover -s tools/phase2 -p test_delivery_finish_locator.py -v
python -m unittest discover -s tools/phase2 -p test_*.py -v
```

정상 판정은 실제 버전·본문 고정 뒤 검토된 decoder/collector/부모 인증이 모두 일치하는 경우다. 실패 판정은 다른 issue/creator/repository, 버전 변경, 원본 누락·변조, 필수 의미 필드 누락, 현재 요청과 불일치, 만료·지연, 외부 예외이며 모두 무재시도 STOP/fence 보존이다. 복구는 기존 읽기 전용 상태·원본 대조와 해당 RUN의 인수 절차를 따른다. 새로운 Runtime·GO·원본 게시·키/권한 변경은 수행하지 않았다.

GitHub native 응답은 공식 [issue comment](https://docs.github.com/en/rest/issues/comments#get-an-issue-comment), [Git reference](https://docs.github.com/en/rest/git/refs#get-a-reference), [Git blob](https://docs.github.com/en/rest/git/blobs#get-a-blob) API 정의를 따른다. API envelope와 본문 의미 채택을 구분한다.

시각 필드는 공식 [Git commit](https://docs.github.com/en/rest/git/commits#get-a-commit)·[댓글](https://docs.github.com/en/rest/issues/comments#get-an-issue-comment) 응답에 근거한다. 실패 시험은 고정 locator 오류와 하위 `reader.blob/comment` 호출0을 단언하여, 하위 reader의 중복 방어가 locator 검사를 대신해 시험을 통과시키는 문제도 구분한다.
