# #46 운영 구성 통합 인계 — 2026-10-24

**기존 구현·원본·담당자 확인을 이 문서로 묶는다. 설계와 오프라인 구현은 준비됐지만, 전체 실행 SITE의 실제 배치·자격 공급·원본/control 연결은 미완료다.** `SITE=None`, dispatch false, Runtime NOT_RUN을 유지한다. 아래 문서는 실행 GO나 현재 호스트 점검 결과가 아니다.

기준 코드는 PR132 병합 `329fe5d8`와 PR133 검토 HEAD `386f9f15`다. PR133 후속 변경은 이 인계표, 상태보고 정리와 비차단 시험 보완에 한정한다. 기존 두 사람 승인은 `386f9f15` 범위를 보존하고 후속 변경의 승인으로 확대하지 않는다.

## 1. 반복 요청 없이 재사용할 자료

| 항목 | 이미 확인한 기록·선택 | 적용 범위 |
|---|---|---|
| 발급 위치·본인 | ansible01 / server01_ansible, ROSA 본인 gwonuk | [운영 선택](operating-site-adoption.md). 현재 로그인·API 도달·실효 권한은 미확인 |
| 관리 경로 | ansible01 → 10.250.10.20 / server01_cicd-runner; 기존 key·known_hosts 경로 | [관리 SSH 연결표](management-ssh-selection.md), [10/20 공급 보고](https://github.com/321Team/infra-ansible/issues/82#issuecomment-6283088910). 키 원문 재제출 불필요 |
| Runner | cicd-runner01 / server01_cicd-runner / UID1000 | 기존 workflow identity 및 담당자 리뷰. 현재 helper identity와 같은 job/netns 여부는 실행 준비에서 대조 |
| workspace | `/home/server01_cicd-runner/actions-runner/_work/infra-cicd/infra-cicd` | [9/23 원본 인계](../runtime-block-followup-20260923.md), [후속 증적](../evidence-continuous-verification-20260923.md)의 역사적 경로. 실제 실행의 GITHUB_WORKSPACE를 대체하지 않음 |
| Python | `/usr/bin/python3` | [P2 workflow](../../.github/workflows/p2-rosa-delivery.yml)의 명시 실행 경로. 현재 설치·버전 점검과 구분 |
| helper | 실제 workspace의 `p2-job-{run_id}-{attempt}/tools/phase2/delivery_ssh_bridge_helper.py` | native job의 exact SHA checkout 규칙. 상시 별도 daemon·root 설치·P1 helper 재사용 없음 |
| API/CA/SA/소스 | `delivery_fds_site_inputs`의 고정 profile | 이미 수신한 [담당자 정정](https://github.com/321Team/fds-project-baseline/issues/46#issuecomment-6339313808), MSA42 source/render/두 digest. 새 값 복제·추정 없음 |
| 6객체 기준선·previous | 기존 full raw 및 명시 선택된 previous 두 이미지 | [원본 수집](https://github.com/321Team/fds-project-baseline/issues/46#issuecomment-6366234985), [공유 사본](https://github.com/321Team/fds-project-baseline/issues/46#issuecomment-6366379247), `delivery_fds_observed_baseline`. replicas 인계 뒤 새 현재값과 동일하다고 가정하지 않음 |
| 권한 인계 | 하영의 gwonuk 임시 RoleBinding2 부여 보고, 10/25 오전 부재·권욱 인계 | [6379184704](https://github.com/321Team/fds-project-baseline/issues/46#issuecomment-6379184704). 본인 can-i나 SA 검증의 성공 증거가 아님. 최종 소비 후 하영 회수·부재 확인, namespace 삭제 전에 처리 |

### P2 파일 배치의 중앙 설계 선택

기존 Runner HOME 아래 **새 P2 전용 설계**를 선택한다. 아래 경로가 실제 존재하거나 권한·소유권이 확인됐다는 뜻은 아니다. 이번 변경은 파일을 생성하거나 SITE의 기본값으로 주입하지 않는다.

- 비밀 없는 per-run 입력: `/home/server01_cicd-runner/fds-p2-delivery/inputs/{run_id}-{attempt}`.
- 검토 소스: `/home/server01_cicd-runner/fds-p2-delivery/sources/{source_sha}`. 실제 GitSnapshot의 source/revision 대조를 통과해야 한다.
- 영속 claim/checkpoint: `/home/server01_cicd-runner/fds-p2-delivery/state`. checkout과 per-run 입력 밖에서 회차를 넘어 보존한다.

각 경로는 기존 `SiteConfiguration`의 명시 인자로만 연결한다. P1 store·kubeconfig·기존 작업 세션의 자격은 공유하지 않는다. input과 store를 겹치게 하거나 자동 생성·chmod·소유권 변경으로 검사 실패를 숨기지 않는다. 실제 배치 때 기존 owner/mode/link 검사 결과를 확인한다. 이 설계 선택은 담당자에게 새 경로를 만들어 달라는 요청이 아니다.

## 2. 담당자별 한 번에 확인할 범위

[일괄 요청 6380386112](https://github.com/321Team/fds-project-baseline/issues/46#issuecomment-6380386112)에 한 번만 회신한다. 이미 있는 링크를 재사용하며 `변경 없음 / 기존 기록 링크·값 / 미기록·미배치`를 구분한다. 새 SSH·API 조회·권한 변경·자격 발급은 요청하지 않는다.

| 담당 | 필요한 기존 사실 | 답변 후 중앙 처리 |
|---|---|---|
| 재환 | SSH 명시 port·host-key alias/known_hosts 표기의 기록, 위 workspace/Python의 변경 기록, job과 SSH의 계정/container/netns 예외 기록 | 기록에 맞춰 기존 SshBridgeConfig를 채움. 미기록이면 실행 준비 점검으로 넘김. `22`나 IP alias를 추정하지 않음 |
| 하영 | 본인 전용 OAuth의 기존 메모리 공급 절차 유무, 실제 OAuth 수명/만료 확인·수동 회수 방법의 기존 기록 | 재사용 가능하면 연결, 없으면 권욱의 구현·실행 준비 항목으로 유지. 비밀번호·token 원문 요청 없음 |
| 권욱 | 새 P2 파일 배치와 trusted caller, 메모리 자격 공급, 인증된 READY 왕복, 5역할 original/control 및 finish scope의 의미 연결 | 아래 구현·실행 준비 구분대로 마무리. 담당자 미회신을 중앙 코드 작업의 전체 중단 사유로 사용하지 않음 |

미기록 회신은 완료나 안전의 증명이 아니다. 미해소 항목을 해당 단계에 남기되 같은 자료를 반복 요청하지 않는다. 변경된 사실 또는 새 결함이 있을 때만 그 차이를 추가 확인한다.

## 3. 기존 구현을 연결할 순서와 남은 범위

아래는 기존 API의 연결 계약이다. 구성 생성은 통신·자격 발급·callback 실행을 하지 않으며 별도 Runtime GO가 있는 호출자만 실행 단계로 넘어간다.

| 순서·실행 위치 | 기존 진입점 / 입력 | 정상 판정·남은 중앙 작업 |
|---|---|---|
| 1. 중앙 구성 | `delivery_fds_operating.build_configuration` + 기존 profile, 명시 경로·Binding·기한 | `SiteConfiguration` 생성은 준비만 의미. 원본을 인증하는 materializer와 실제 source/control pin을 연결해야 함 |
| 2. Runner job 준비 | `assemble_observed_job` → 기존 owned rendezvous + native job context/revision | 5역할·helper 기대 UID1000·post-close followup 2개 재사용. `publish_ready`의 **실제 인증된 왕복 caller**는 아직 연결 필요 |
| 3. ansible01 자격 전달 | `build_job_bridge_configuration` + `build_actor_channel` → `send_over_bridge` | 실제 port/alias/endpoint·본인 actor·repository-scoped 자격을 명시. 사용자 확인→단회 TokenRequest→검토된 SSH/READY 전달. 로그인/메모리 입력 구현과 native request 결속은 중앙 잔여 |
| 4. 원본과 계획 조립 | `SourceRoleWiring`, `PrivateSourceSession`, 기존 prepared plan/materializer | 회차 Binding의 원본/control 인증 후 기존 6객체·previous·owner 검증. `approved=true`나 댓글 URL만으로 grant를 만들지 않음 |
| 5. 적용·실패 차단 | 기존 job/provider·claim/fence·sender·baseline drift/SSA 검사 | 현재 단계에서는 미실행. replicas 인계와 새 기준선이 없으면 실행 준비 미완료. force/다른 계정/다른 endpoint 재시도 금지 |
| 6. 종료 관측 | 기존 `ObservationRoute`: OBJECT_READBACK + ROLLOUT_READBACK | sender close 뒤 같은 Binding·기한으로 관측. 실제 progress와 authenticated control을 사용; 원본 부재/철회면 완료를 만들지 않고 fence 유지 |
| 7. 자격·운영 인수 | actor DELETE 결과, SA 만료, 별도 자원 정리 책임 | 로컬 참조 해제·OAuth 회수·SA 만료·C-STOP·DB/Robot/클라우드 정리를 각각 기록 |

READY 게시 callback에서 SSH 전달 **완료까지 동기 대기하지 않는다**. listener가 준비된 뒤 별도 소유 실행 경로로 발급측 전달을 시작하고 job은 accept를 계속해야 한다. 실제 transport/caller 구현에서 기존 peer/nonce/revision·단회 소비·기한 검사를 재사용하며, 공유 파일의 단순 READY 문자열을 인증 근거로 삼지 않는다. 양 호스트 monotonic 절대값을 공유하지 않는다.

### 원본·control 5역할

| 역할 | 재사용·해석 책임 | 아직 생성/연결되지 않은 것을 다루는 방법 |
|---|---|---|
| approval | 권욱: 실제 #46 자동 배포의 대상·기간·철회 의미와 승인 원본 | 과거 창 C 수동 GO·코드 PR 승인으로 대신하지 않음. 현재 운영 GO 미발행 |
| platform | 기존 CA/대상/SA/사전 검증 묶음 + 실제 검증기 | 사전 platform receipt와 사후 TokenRequest issuance receipt를 혼합하거나 역대입하지 않음 |
| producer | 기존 MSA42 source/render/digest 및 공급 계보, native source verifier | job 성공만으로 앱 계보를 생성하지 않음 |
| baseline | 권욱: 받은 archive bytes·owner/spec·previous와 채택 grant/control | 원본은 유지. replicas 인계 뒤 새 archive는 승인된 실행 준비에서 생성·명시 채택 |
| finish | 권욱: 종료 관측 scope/control; 실행 코드: 실제 종료 원본 생성 | 미래 hash 제출 불필요. 실제 sender close/progress 이후만 생산. 자원 회수 범위로 확대하지 않음 |

각 역할은 기존 `RoleRoute(original, control)`과 실제 repository ID·원본 pin을 사용한다. 역할을 공유하는 동일 repository는 기존 reader를 빌려 쓰며 불필요한 토큰을 늘리지 않는다. 새 JSON 승인 loader·중복 승인 파일·별도 control 등록 절차를 만들지 않는다. 필요한 의미 채택과 기존 인증 계약의 연결은 권욱의 중앙 책임이다.

## 4. 실패·회수 인수

| 발생 조건 | 자동 코드 결과 / 인수할 조치 |
|---|---|
| 본인 인증 실패·다른 사용자 | 발급과 타인 자격 삭제 금지. 본인 자격의 외부 회수 상태는 별도 확인 |
| TokenRequest 성공 뒤 SSH/READY 실패 | 재발급·재전달하지 않음. 비공개 receipt 보존, 자기 actor 회수 결과와 이미 발급된 SA 수명을 각각 인수 |
| handoff 취소와 DELETE 실패가 동시 발생 | KeyboardInterrupt/SystemExit를 보존하고 회수는 UNCONFIRMED로 기록. 취소를 회수 오류로 덮어쓰거나 성공 처리하지 않음 |
| OAuth 회수 응답 불명확·프로세스 강제 소멸 | 권욱이 현재 사용자 전용 자격의 수동 회수 담당. 실제 설정 수명/만료 근거와 확인 결과를 비공개 인계. 값이 없으면 만료 미확인으로 남기며 추정한 기본값이나 `oc logout` RC0만으로 종료하지 않음 |
| source/control 철회·관측 누락·부분 적용 | 기존 fail-closed/claim/fence를 유지. 원본과 마지막 progress를 보존하고 실제 상태를 확인한 별도 복구 판단. 무조건 fence 해제·성공 finish 생성 금지 |
| 최종 자원 회수 | 본인 actor 회수와 별개로 하영의 임시 RoleBinding2 회수·부재 확인, 해당 자원 담당자의 최종 정리. **10/26 12:00 KST 완료 기한 유지** |

`take_receipt()`에는 SA token이 포함되므로 공개 댓글·PR·CI 로그에 넣지 않는다. 보고에는 비밀 없는 상태·시각·대상·참조만 남기고 회수 실패 원문도 비공개로 보관한다.

## 5. 지금 마무리할 것과 실행 때 생성할 것

- **이번 준비 묶음:** 기존 자료·선택·구현 진입점·담당자 질문·회수 조건 통합, PR133의 비차단 시험 2건 반영. 새 wrapper나 실행 gate 추가 없음.
- **권욱의 실제 코드 잔여:** 전용 actor/repository 자격의 메모리 공급 caller, 인증된 READY 왕복, 5역할 original/control materializer와 최종 SITE 조립. 이 문서 자체가 해당 구현을 완성하지는 않는다.
- **승인된 실행 준비 잔여:** 현재 SSH 신뢰·UID/netns·경로·endpoint/실효 권한, replicas 인계 뒤 새 baseline. 현재 보고/예상값과 실제 점검 결과를 구분한다.
- **실행 중 생성:** GitHub native run/attempt는 workflow가, READY nonce는 rendezvous가, 발급 receipt/fingerprint/iat/exp는 실제 issuer가, finish 원본은 종료 관측기가 생성한다. 사전 합성·담당자 제출 항목에서 제외한다.

변하지 않은 부분의 전체 리뷰와 시험을 다시 요구하지 않는다. 비차단 보완은 현재 PR133에 모아 처리하고, 안전·정확성에 영향을 주는 새 결함만 그 근거·변경분·재검증 조건을 분리한다. PR별 승인·Runtime·최종 인수는 서로 대체하지 않는다.

오프라인 상태 확인은 기존 `python3 -B tools/phase2/delivery_job.py status`다. **현재 기대 종료코드 2 / P2_JOB_UNCONFIGURED**가 정상 보고이며 PASS·실행 가능 상태가 아니다. 모든 원본·연결이 실제로 채워지기 전에는 이를 바꾸지 않는다.
