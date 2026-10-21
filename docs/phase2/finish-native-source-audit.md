# #46 실제 종료 원본·생성 코드 추적

2026-10-21 15:15 KST 조회 당시 **확인 범위에는 P2 종료 본문 생산자·본문 해석기가 연결돼 있지 않았다.** 아래는 그 시점의 추적 기록이다. 이후 사용자 자체 구현 지시에 따른 [post-close 관측 생성부·해석기·직접 현장 연결](post-close-observation.md)을 추가했다. 실제 scope 의미 채택·운영 입력·Runtime 인수는 별도로 남아 있다. 팀원의 누락 제출로 표시하지 않는다.

## 확인한 원본과 생성점

| 확인 대상 | 실제 내용·판정 |
|---|---|
| [#46 최신 기록](https://github.com/321Team/fds-project-baseline/issues/46#issuecomment-6294821045) 및 댓글 30개 | site issuer/producer/finish 미연결이라는 기록이다. 후속 실제 종료 본문/생성 코드 회신은 확인되지 않았다. 과거 PR/리뷰 상태는 현재 병합 상태와 별개다. |
| infra-cicd main `4a764dd55ec3108df3b6a33e1ff5741db2411354`의 `delivery_session.py` | sender close → verifier.finish → claim.complete 순서다. `P2-CICD-session-1` 반환은 그 뒤이므로 같은 RUN의 finish 입력으로 사용할 수 없다. |
| 같은 HEAD의 `delivery_store.py:Claim.complete`·`delivery_recovery.py:decision` | 실제 `P2-CICD-complete-1` 생성기는 존재한다. 그러나 verifier.finish 통과 **후** 마지막 checkpoint 해시를 담아 완료 marker를 쓰고 fence를 제거하므로, 같은 RUN의 선행 finish 입력으로 쓰면 순환한다. journal·claim/active·apply 결과는 로컬 진행·잠금 기록이며 독점 접근 종료 권위가 아니다. recovery inspection도 runtime/authorization을 NOT_VERIFIED로 표시한다. 이번 구현은 이 알려진 실제 local 형식들을 종료 본문으로 승격하지 못하도록 거부한다. |
| 같은 HEAD의 `delivery_provider.py`, `delivery_finish_collector.py`, `delivery_semantic_authority.py`, `delivery_finish_locator.py` | provider/locator/collector는 기존 원본 조회·인증·파생 판정 코드다. typed `FinishFacts`·`CompletionEvidence`와 내부 IPC frame이 본문 생산자는 아니다. 실제 본문 decoder 기본값은 없고 필수 주입을 유지한다. |
| [P1 복원 관측 생성 코드](https://github.com/321Team/fds-project-evidence/blob/04464931ae70fc4d78fb2601d6cc2c384487b317/evidence/P1-CICD-01/ac09-restore-current-20261015/runtime/finish_observations.py) | Git blob `65ee12e56865bbded5a7886a26792c58c1e83204`, 5,130B. P1 Pod 6개·업무 지표·Runner.Worker 등을 관측해 `R2_MINIMUM_HTTP_METRICS.json`·`RECOVERY_HTTP_CONFIRMED.json`·`R2_STABLE_FINAL.json`을 만드는 실제 코드다. P2의 RUN/action/progress·ROSA 대상·종료 권위와 연결된 원본이 아니므로 전용하지 않는다. 코드만 읽었으며 실행하지 않았다. |
| [Ansible #82 실제 종료 보고](https://github.com/321Team/infra-ansible/issues/82#issuecomment-6283088910) | Ansible regression Job의 공급/Runner 회수 기록이다. P2 배포 종료의 증거로 바꾸지 않는다. |
| fds-project-evidence main `04464931ae70fc4d78fb2601d6cc2c384487b317` tree | 전체 18,034 blob·truncated=false. `evidence/P2-CICD*` 경로는 확인되지 않았다. 다른 위치·서버에도 없다는 증거는 아니다. |
| 고정 비공개 공유 폴더 | 최상위 파일/디렉터리 이름을 읽었다. P2 종료 원본으로 연결할 CICD/finish 후보는 확인하지 못했다. 하위 모든 파일이나 실제 서버를 전수 조회했다는 뜻이 아니다. |

추가로 infra-ansible main `7f04e18371f5d10aec678460f5dcd6e28d98d7d3`, fds-msa main `20ae1611102e0eed6138145039bcc863b64b6646`의 잘림 없는 tree를 확인했다. GitHub code 검색은 이미 존재하는 `exclusive_end_reference`도 0건을 반환해 원본 부재의 증거로 채택하지 않았다. 원격 서버 접속·토큰 발급·실제 Runtime은 하지 않았다.

## 구현 가능한 부분과 미연결 부분

- **이번 추가 구현:** 확인된 GitHub comment `updated_at`/Git commit `committer.date`의 엄격 UTC 해석·정책 창 대조, 오래된/미래/시각 누락 원본의 선행 STOP, locator 자체 guard별 고정 오류·pinned reader 호출0 시험. 실제 생성 코드가 확인된 local complete/recovery/journal 등의 순환·의미 불일치도 decoder 전에 거부한다. 이미 확인한 형식의 파서·거부 검사이며 새 생산자 양식이 아니다.
- **미연결:** P2 종료 본문 생산 함수/기존 원본과 그 의미·권위에 대응하는 decoder, 실제 site issuer/producer/finish 채택·자격/source 구성. 본문의 없는 RUN/progress를 현재 요청에서 복사하거나 P1/Ansible/진단/session 결과를 승격하지 않는다.
- **다음 구현 조건:** 기존 별도 생성 코드가 확인되면 같은 코드·원본 형식을 검토해 연결한다. 확인 범위 밖에서 찾지 못한 것과 확인된 P2 호출 순서에 생산자가 없는 것을 구분한다. 생성부가 새로 필요하면 #46 자체 설계·구현으로 명시하고 기존 승인 양식이나 실제 운영 증거처럼 만들지 않는다. 새 제출 양식·중복 자료·권한 회수·GO를 임의 요구하지 않는다.

오프라인 fixture는 명시적 합성 자료다. 이번 작업에서 실제 P2 종료 원본을 생성하거나 Runtime·AC 인수·티켓 종결을 수행하지 않았다. **원본 확인 미완료를 감추기 위해 추측 decoder를 PR에 넣지 않았다.**
