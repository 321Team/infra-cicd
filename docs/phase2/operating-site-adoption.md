# #46 운영 구성 채택 — 2026-10-23

**확인된 기대 설정을 `delivery_fds_operating`에 채택했다. 전체 운영 SITE와 실행 승인은 아직 완료되지 않았다.** `delivery_job_site.SITE=None`을 유지하고 self-hosted job 예약을 차단한다. 새 입력 양식·서명·중복 승인 절차는 추가하지 않는다.

## 이번에 연결한 설정

| 설정 | 실제 채택·연결 | 원본과 한계 |
|---|---|---|
| Runner | `cicd-runner01 / server01_cicd-runner / UID 1000`을 `assemble_job`의 고정 기대값으로 사용 | [기존 workflow](https://github.com/321Team/infra-cicd/blob/aa64731c0265d6f0cdc1e14d012f43dafa28616b/.github/workflows/app-test-build-scan.yml)와 PR109 재환 담당 리뷰. 현재 프로세스 실측은 아니다. native driver의 실제 host/account/UID 대조는 유지한다 |
| 배포 대상·소스 | 기존 `delivery_fds_site_inputs`의 API·클러스터/namespace UID·CA·SA/UID·source `69e28c49`·rendered hash·2개 digest를 직접 재사용 | [제출 원본 정정](https://github.com/321Team/fds-project-baseline/issues/46#issuecomment-6339313808), 기존 MSA PR42 배포 묶음. 값 복사나 새 현재값 추정 없음. 대상은 기존 6객체이며 first-empty로 폴백하지 않는다 |
| 발급 담당 | `hayeoung`과 정정 댓글을 담당 지정 근거로 기록. 기존 TokenRequest issuer 진입점을 그대로 사용 | 10/22 `create serviceaccounts/token` 및 특정 SA token 권한 `yes`, `hayeoung-admin` RoleBinding 대조. **이름·과거 can-i는 현재 credential 인증이 아니다.** API 호출·토큰 발급·권한 추가 없음 |
| 조립 진입점 | `build_configuration`·`build_token_issuer`는 기존 구현을 직접 참조하고, `assemble_job`만 고정 Runner를 기존 5역할/6객체 조립에 연결 | caller의 Runner 덮어쓰기를 받지 않는다. helper UID는 별도 명시 입력이며 1000으로 복사하지 않는다. 생성 중 callback·시계·소켓·원본 읽기 없음 |
| 상태 확인 | `delivery_job.py status`가 채택된 설정·미확정 연결·실행 중 생성값을 분리 출력 | `BLOCKED / dispatch_configured=false / execution_authorized=false / runtime=NOT_RUN` 유지. 보고서 수정으로 gate가 열리지 않는다 |

운영 설정의 선택 근거와 실제 실행 증적을 구분한다. source/rendered 값은 검토한 배포 입력의 선택이며 현재 live spec의 증명이나 최근 수동 변경 수용은 아니다. 실행 때 native revision·원본/control·baseline drift 검증을 그대로 거친다.

## SSH·배치 원본 대조 — PR125·126 병합 후

PR125 `d20b5ee7`, PR126 `c2afae72` 병합 뒤 기존 자료를 다시 대조했다.
아래는 **기존 배치 근거**이며 P2에서 접속·설치·발급을 새로 확인한 결과가 아니다.

| 기존 자료에서 확인한 것 | #46에 재사용할 범위 / 아직 채택하지 않는 것 |
|---|---|
| [P1 native probe](https://github.com/321Team/infra-cicd/blob/c2afae721159224f501e72f43b54fecd7fbe3621/docs/deploy-native-auth-probe.md)의 Runner `10.250.10.20`, `cicd-runner01`, `server01_cicd-runner / UID1000` | host/account/UID 기대값은 PR126에서 이미 연결했다. IP는 기존 경로의 후보 주소다. P2 발급측에서 도달 가능하거나 같은 SSH trust를 사용한다는 증거로 확대하지 않는다 |
| [기존 broker 코드](https://github.com/321Team/infra-cicd/blob/c2afae721159224f501e72f43b54fecd7fbe3621/tools/delivery/transfer_native_probe.py)의 ansible01→CP1 및 ansible01→Runner 2개 SSH 연결 | ROSA의 `hayeoung` 발급 세션과 CP1의 `server02` 자격은 별개다. CP1 API/SA/CA·kubeconfig를 P2 issuer에 넣지 않는다. 기존 broker 키 위치는 호출자가 명시하며 실제 키 파일명은 해당 자료만으로 확정되지 않는다 |
| [P1 workflow 배치](https://github.com/321Team/infra-cicd/blob/c2afae721159224f501e72f43b54fecd7fbe3621/docs/deploy-workflow-handoff.md)의 Runner HOME 및 P1 입력 폴더 | HOME 위치의 근거로만 사용한다. P1 입력·claim/store를 P2에 공유하지 않는다. P2 helper·input/source/store 경로는 중앙이 결정할 배치 항목이며, 이미 설치된 사실로 만들지 않는다 |
| infra-ansible inventory의 `svc_ansible`, 엄격한 known_hosts 설정 및 임시 `ansible-isolated` Runner 운영 문서 | 해당 inventory에는 CICD Runner 항목이 없다. 임시 Runner와 장기 `cicd-runner01`을 혼용하지 않으며 `svc_ansible`을 helper 계정으로 추정하지 않는다 |

권욱은 발급측 실행 위치·기존 SSH 중계 경로의 선택, READY 전달 방식과 P2 전용 배치를
완성한다. 재환·하영에게는 **기존 자료에 없는 배치 사실만** 대조받는다. 실제 SSH
port/key/known_hosts, helper 설치 위치·실행 UID/netns는 현재 미확정이다. 새 키 생성이나
기존 계정 권한 변경을 이번 작업에서 수행하지 않았다. Runtime 전 현재값 대조는 이후
승인된 연결에서 수행하며, 이 문서를 작성하기 위한 현장 재수집은 요청하지 않는다.

## 원본·control·finish 연결의 현재 경계

| 역할 | 재사용하는 원본 / 남은 중앙 결정 |
|---|---|
| 발급·platform | [하영 정정 6339313808](https://github.com/321Team/fds-project-baseline/issues/46#issuecomment-6339313808)과 이미 수신한 `p46-inputs-20261022T190046.tgz`. 과거 can-i·CA·OIDC 자료를 실제 발급 응답으로 대체하지 않는다. 현재 actor와 endpoint, 사전 원본과 사후 receipt의 의미 연결은 권욱 담당 |
| producer | 이미 채택한 MSA 소스·rendered·2개 digest와 기존 공급 원본. native Run 성공만으로 앱 source/이미지 계보를 만들지 않는다. 기존 `ActionsRunSource`와 원본 인증을 함께 결속한다 |
| approval/control | 댓글 locator와 저장소 ID만으로 `AdoptedGrant`를 만들지 않는다. 실행 대상·기간·철회 의미와 변경을 감지할 control 원본/참조의 운영 채택은 권욱 담당. 현재 control pin이 없다는 사실을 담당자의 새 토큰 미제출로 돌리지 않는다 |
| baseline | 창 C UID/RV 표와 SCC Pod JSON은 Deployment/Service/Route 6객체 전체 원본이 아니다. 10/23 22:39:26 KST 수집한 별도 full raw를 공유 사본으로 수신·검증했다. 아래 연결부에 원본 바이트를 전달할 수 있으며, previous 근거와 허용 owner/spec 운영 채택은 계속 별도다 |
| finish | 기존 `SourceRoleWiring`의 명시적 observation 또는 collection 모드를 사용한다. 권욱이 해당 실행의 종료 범위·control·보관 위치를 정한다. 실제 종료 progress와 관측 원본은 실행 후 생성되므로 사전 해시를 요구하거나 합성하지 않는다 |

원본 위치 목록은 조회 근거이지 실행 설정이나 승인 loader가 아니다. 운영 의미가
미확정인 상태에서 새 forwarding wrapper나 JSON `approved=true`를 추가하지 않았다.
PR127에서는 생산 코드를 변경하지 않았고 **연결 시험과 배치 대조를 보강**했다.
이후 수신한 full raw의 해석·계획 연결 구현은 아래 후속 절에 구분한다.

PR125 시험에 native TokenRequest 모의 응답→로컬 helper→익명 pipe→실제 source session을
통과한 credential로 finish 원본/control을 인증하는 경우를 추가했다. 종료 자료 미설치 시
읽기 없이 거부하고, 두 번째 control이 바뀌면 종료 판정을 거부한다. 이 시험의 terminal
progress는 메모리 fixture이며 실제 sender 종료·배포·claim 해제 증명이 아니다.
정상·실패 세 경로의 source credential/transport·pipe·prepared plan 정리를 단언하고,
안전 청소에 helper 강제 종료가 필요하면 시험을 실패시킨다. 기존 child rc=0 단언은 유지한다.

## 미확정 입력과 남은 중앙 연결

아래 세 묶음은 새 제출 양식이 아니다. 이미 제출한 자료를 먼저 재사용하고, 없는 사실 또는 아직 미구현인 연결만 보완한다.

| 묶음·담당 | 아직 필요한 것 | 완료조건 |
|---|---|---|
| **1. 전송·배치 — 권욱**, 재환/하영은 기존 배치 사실 확인 | SSH 실제 방향·host/key/known_hosts 경로·원격 helper 경로와 UID/netns, 인증된 READY 전달, input/source/persistent store 위치, 현재 endpoint | 실제 기존 배치 근거를 `SshBridgeConfig`, source route 및 job 조립에 명시한다. 새로운 계정·키·권한·파일 위치를 이미 존재하는 값으로 기입하지 않는다. 양 호스트 monotonic 값을 직접 공유하지 않는다 |
| **2. 원본/control/finish — 권욱 구현·채택**, 담당자 기존 원본 재사용 | approval/platform/producer/finish/baseline 5역할의 원본·control pin, 비공개 보관 인증·materializer, 실행 및 종료 범위 | 기수신 10/10 플랫폼 원본·생산자 원본과 실제 승인 의미를 연결한다. 역사적 창 C 수동 GO를 #46 자동 배포 GO로 전환하지 않는다. 원본의 해시 일치만으로 의미·권위를 채택하지 않는다 |
| **3. 기존 6객체 기준선 — 권욱 채택**, 하영 원본·재환 previous 근거 | full raw 수신·9/9 manifest 대조 완료. 남은 것은 허용 owner/spec 및 이전 이미지의 명시적 채택 | `connect_baseline_archive`에 별도 grant·기존 producer.previous_images를 전달하고 기존 fresh GET/원본/control 검증을 통과해야 한다. 수신 원본을 승인이나 previous 값으로 자동 변환하지 않는다 |

누락값을 책임자에게 모두 새로 생산시키는 요청이 아니다. 2번의 코드/의미 연결은 권욱 업무이고, 1·3번은 기존 자료에 해당 사실이 있는지 담당자 확인이 필요한 부분이다. 새 API/SSH/수집을 이 PR에서 실행하지 않는다.

다음은 **사전 누락 입력이 아니다**: 미래 native run/attempt, READY nonce, 발급 응답·fingerprint·iat/exp, 실제 finish 관측 원본. 기존 코드가 해당 실행 때 생성·검증한다. 반면 생성 위치·범위·원본 해석 방식과 control 정책은 실행 전에 연결해야 한다. 사후 발급 응답 해시를 사전 `Binding.platform_receipt_sha256`에 역대입하지 않는다.

## 확인 방법과 인수 범위

저장소 루트에서 오프라인 상태만 확인한다.

```sh
python3 -B tools/phase2/delivery_job.py status
```

현재 정상 결과는 종료 코드 **2**, `P2_JOB_UNCONFIGURED`와 `operating_configuration.selection=PARTIAL`이다. 미확정 세 묶음이 보고되며 자격·실제 원본·환경변수·호스트를 조회하지 않는다. `--execute`를 붙여도 현재 기본 SITE는 활성화되지 않는다. 이 문서는 Runtime 실행 명령이나 GO가 아니다.

시험은 기존 SYNTHETIC 계획으로 실제 조립 타입, 기대 Runner 고정 및 native mismatch 차단, helper UID 분리, foreign target 거부, deferred materializer 무호출, 상태보고 무I/O 및 gate 불변을 확인한다. 기존 workflow/보안 경계·발급 권한·6객체 검증·claim/fence/finish 계약은 유지한다. 원복은 이 코드 변경을 되돌리는 것으로 충분하며 서버 자원 원복은 발생하지 않는다.

Claude Code·Grok·Antigravity에는 일반 구성 연결/상태 계약만 역할별 전달했다. 프로젝트 운영 메타데이터 전송은 자동 검토에서 거부돼 제공하지 않았다. 세 도구의 응답은 일반 설계 자문이며 이 소스·실행 원본의 외부 심층 검토나 독립 사람 승인이 아니다. Codex가 원본·코드·시험을 대조했다.

PR 병합 뒤에도 세 묶음 연결과 실제 실행 승인이 별도로 남는다. DB·Robot 유지 조건 및 **10/26 12:00 KST 최종 정리** 결정은 변경하지 않는다.

## 수신한 full raw의 실제 형식 연결 — 2026-10-23 22:5x KST

[생산자 수집](https://github.com/321Team/fds-project-baseline/issues/46#issuecomment-6366234985)과
[공유 반출](https://github.com/321Team/fds-project-baseline/issues/46#issuecomment-6366379247)을 대조했다.
`baseline6-20261023T133926.tgz`의 공유 사본 SHA-256은
`e102270877a63a810d99de0567e4c95f3d24c1669d9e6bfefdde2aeab66c2a6f`로 일치한다.
내부 SHA256SUMS의 JSON6·time/target/summary3 파일 **9/9**도 대조했고,
00-time의 `2026-10-23 13:39:26 UTC`는 **22:39:26 KST**다. 원본은 비공개 보관하며 Git에 넣지 않는다.

`delivery_fds_baseline_archive.connect_baseline_archive`는 받은 tar.gz 바이트와
검토한 외부 hash/root, 이미 채택한 grant를 명시적으로 받는다. 파일을 풀거나 API를
호출하지 않고, 외부 hash를 먼저 확인한 뒤 제한된 메모리에서 정확한 파일 집합·각 hash·
중복/링크/경로·JSON 및 owner 형식을 검증한다. 여섯 원본은 **재직렬화하지 않은 bytes**로
기존 BaselineAdoption에 넣고 `validate_existing_plan`에 직접 연결한다. 기존 baseline을
덮어쓰거나 현재 이미지에서 previous를 만들지 않으며 기존 plan의 다른 역할도 그대로 유지한다.

실제 원본에는 두 Deployment replicas0, transaction-api의 `restartedAt` annotation,
`fds-deployer` Apply와 `kubectl-patch`/`kubectl-rollout`/controller Update가 있다.
이 관측을 렌더의 replicas3 또는 임의 owner로 바꾸지 않았다. baseline 허용 owner/spec,
previous 이미지 근거와 그 grant/control의 의미 채택은 **권욱 잔여**다. 최초 P2 배포 때의
`previous 없음` 기록만으로 현재 redeploy의 previous를 확정하지 않는다.

Runtime의 `CodeAdoptedBaseline`은 이후에도 원본/control을 앞뒤로 재인증하고 여섯 fresh
GET의 UID/spec/owner를 비교한 뒤에만 claim/write를 허용한다. raw 수신·형식 통과는 현재
클러스터 무변경 증명·실행 GO가 아니다. control/finish 범위 및 SSH·READY/helper 배치는
기존 잔여이며 `SITE=None`을 유지한다. 이번 중앙 작업은 로컬 수신·오프라인 구현/검증뿐이다.
