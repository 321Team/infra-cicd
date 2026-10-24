# #46 운영 구성 채택 — 2026-10-24

**현재 인계 기준은 [운영 구성 통합 인계](operating-handoff.md)를 먼저 읽는다.** 기존 자료·중앙 설계·담당자별 한 번의 확인·실행 준비·미래 생성값을 구분했다. 아래 기록은 원본 대조와 구현 이력으로 보존한다. 과거의 미확정 설명을 현재의 재제출 요청으로 사용하지 않는다.

**확인된 기대 설정을 `delivery_fds_operating`에 채택했다. 전체 운영 SITE와 실행 승인은 아직 완료되지 않았다.** `delivery_job_site.SITE=None`을 유지하고 self-hosted job 예약을 차단한다. 새 입력 양식·서명·중복 승인 절차는 추가하지 않는다.

## 이번에 연결한 설정

| 설정 | 실제 채택·연결 | 원본과 한계 |
|---|---|---|
| Runner | `cicd-runner01 / server01_cicd-runner / UID 1000`을 `assemble_job`의 고정 기대값으로 사용 | [기존 workflow](https://github.com/321Team/infra-cicd/blob/aa64731c0265d6f0cdc1e14d012f43dafa28616b/.github/workflows/app-test-build-scan.yml)와 PR109 재환 담당 리뷰. 현재 프로세스 실측은 아니다. native driver의 실제 host/account/UID 대조는 유지한다 |
| 배포 대상·소스 | 기존 `delivery_fds_site_inputs`의 API·클러스터/namespace UID·CA·SA/UID·source `69e28c49`·rendered hash·2개 digest를 직접 재사용 | [제출 원본 정정](https://github.com/321Team/fds-project-baseline/issues/46#issuecomment-6339313808), 기존 MSA PR42 배포 묶음. 값 복사나 새 현재값 추정 없음. 대상은 기존 6객체이며 first-empty로 폴백하지 않는다 |
| 발급 담당·위치 | `gwonuk` 본인 actor, `ansible01 / server01_ansible`에서 직접 issue+관리 SSH하는 기대 구성을 채택 | [10/24 하영 인계 회신](https://github.com/321Team/fds-project-baseline/issues/46#issuecomment-6379184704). 다른 사람의 token 공유 없음. **이름·위치 선택은 현재 credential·권한·API 도달의 인증이 아니다.** 원본 source-host UID는 추정하지 않음 |
| 조립 진입점 | 기존 profile·issuer 진입점을 재사용. `assemble_observed_job`이 같은 계정 helper의 별도 기대 UID1000과 5역할·post-close 관측을 고정 선택 | 일반 `assemble_job`의 helper UID 명시 계약은 유지한다. UID를 관측했다고 표시하지 않는다. 생성 중 callback·시계·소켓·원본 읽기 없음 |
| helper 경로 | `build_job_bridge_configuration`이 실제 workspace 명시 입력과 native job context로 기존 `p2-job-{run_id}-{attempt}/tools/phase2/delivery_ssh_bridge_helper.py` 규칙 연결 | 새 경로 탐색·설치·기본값 없음. native metadata와 경로 형식은 배치·현재 신뢰·revision 일치 증거가 아니며 기존 검증 유지 |
| 상태 확인 | `delivery_job.py status`가 채택된 설정·미확정 연결·실행 중 생성값을 분리 출력 | `BLOCKED / dispatch_configured=false / execution_authorized=false / runtime=NOT_RUN` 유지. 보고서 수정으로 gate가 열리지 않는다 |

운영 설정의 선택 근거와 실제 실행 증적을 구분한다. source/rendered 값은 검토한 배포 입력의 선택이며 현재 live spec의 증명이나 최근 수동 변경 수용은 아니다. 실행 때 native revision·원본/control·baseline drift 검증을 그대로 거친다.

**현재 결정·미확정 값은 문서 말미의 ‘10/24 운영 구성 결정’을 우선 적용한다. 아래 10/23~10/24 초기 대조 내용은 근거와 진행 이력이며 당시 미채택 설명을 현재의 미결정으로 되돌리지 않는다.**

## SSH·배치 원본 대조 — PR125·126 병합 후

PR125 `d20b5ee7`, PR126 `c2afae72` 병합 뒤 기존 자료를 다시 대조했다.
아래는 **기존 배치 근거**이며 P2에서 접속·설치·발급을 새로 확인한 결과가 아니다.

| 기존 자료에서 확인한 것 | #46에 재사용할 범위 / 아직 채택하지 않는 것 |
|---|---|
| [P1 native probe](https://github.com/321Team/infra-cicd/blob/c2afae721159224f501e72f43b54fecd7fbe3621/docs/deploy-native-auth-probe.md)의 Runner `10.250.10.20`, `cicd-runner01`, `server01_cicd-runner / UID1000` | host/account/UID 기대값은 PR126에서 이미 연결했다. IP는 기존 경로의 후보 주소다. P2 발급측에서 도달 가능하거나 같은 SSH trust를 사용한다는 증거로 확대하지 않는다 |
| [기존 broker 코드](https://github.com/321Team/infra-cicd/blob/c2afae721159224f501e72f43b54fecd7fbe3621/tools/delivery/transfer_native_probe.py)의 ansible01→CP1 및 ansible01→Runner 2개 SSH 연결 | ROSA의 `hayeoung` 발급 세션과 CP1의 `server02` 자격은 별개다. CP1 API/SA/CA·kubeconfig를 P2 issuer에 넣지 않는다. broker는 대상별 key를 명시 입력으로 받으며, **실제 기존 키 경로는 아래 저장소 교차 대조로 보완했다** |
| [P1 workflow 배치](https://github.com/321Team/infra-cicd/blob/c2afae721159224f501e72f43b54fecd7fbe3621/docs/deploy-workflow-handoff.md)의 Runner HOME 및 P1 입력 폴더 | HOME 위치의 근거로만 사용한다. P1 입력·claim/store를 P2에 공유하지 않는다. P2 helper·input/source/store 경로는 중앙이 결정할 배치 항목이며, 이미 설치된 사실로 만들지 않는다 |
| infra-ansible inventory의 `svc_ansible`, 엄격한 known_hosts 설정 및 임시 `ansible-isolated` Runner 운영 문서 | 해당 inventory에는 CICD Runner 항목이 없다. 임시 Runner와 장기 `cicd-runner01`을 혼용하지 않으며 `svc_ansible`을 helper 계정으로 추정하지 않는다 |

권욱은 발급측 실행 위치·기존 SSH 중계 경로의 선택, READY 전달 방식과 P2 전용 배치를
완성한다. 재환·하영에게는 **기존 자료에 없는 배치 사실만** 대조받는다. 기존 key/known_hosts
경로는 아래에서 확인했으며, P2 issuer 위치·helper 설치 위치/실행 UID/netns와 그 연결은 미확정이다. 새 키 생성이나
기존 계정 권한 변경을 이번 작업에서 수행하지 않았다. Runtime 전 현재값 대조는 이후
승인된 연결에서 수행하며, 이 문서를 작성하기 위한 현장 재수집은 요청하지 않는다.

## 원본·control·finish 연결의 현재 경계

| 역할 | 재사용하는 원본 / 남은 중앙 결정 |
|---|---|
| 발급·platform | [하영 정정 6339313808](https://github.com/321Team/fds-project-baseline/issues/46#issuecomment-6339313808)과 이미 수신한 `p46-inputs-20261022T190046.tgz`. 과거 can-i·CA·OIDC 자료를 실제 발급 응답으로 대체하지 않는다. 현재 actor와 endpoint, 사전 원본과 사후 receipt의 의미 연결은 권욱 담당 |
| producer | 이미 채택한 MSA 소스·rendered·2개 digest와 기존 공급 원본. native Run 성공만으로 앱 source/이미지 계보를 만들지 않는다. 기존 `ActionsRunSource`와 원본 인증을 함께 결속한다 |
| approval/control | 댓글 locator와 저장소 ID만으로 `AdoptedGrant`를 만들지 않는다. 실행 대상·기간·철회 의미와 변경을 감지할 control 원본/참조의 운영 채택은 권욱 담당. 현재 control pin이 없다는 사실을 담당자의 새 토큰 미제출로 돌리지 않는다 |
| baseline | 10/23 22:39:26 KST의 full raw를 정확한 owner/spec 기대 기준선으로 코드 선택하고, 승인 공급+실제 Ready/imageID 근거의 두 이미지를 다음 실행의 previous로 명시 선택했다. 코드 리뷰·실제 grant/control·fresh 비교는 별도이며 실행 GO가 아니다 |
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

기존 세 묶음 중 owner/spec·previous의 기대값 선택을 코드에 연결했다. 실행 전 남은 두 묶음은 아래와 같다. 이미 제출한 자료를 먼저 재사용하고 없는 사실만 보완한다.

| 묶음·담당 | 아직 필요한 것 | 완료조건 |
|---|---|---|
| **1. 전송·배치 — 권욱**, 재환/하영은 기존 배치 사실 확인 | SSH 실제 방향·host/key/known_hosts 경로·원격 helper 경로와 UID/netns, 인증된 READY 전달, input/source/persistent store 위치, 현재 endpoint | 실제 기존 배치 근거를 `SshBridgeConfig`, source route 및 job 조립에 명시한다. 새로운 계정·키·권한·파일 위치를 이미 존재하는 값으로 기입하지 않는다. 양 호스트 monotonic 값을 직접 공유하지 않는다 |
| **2. 원본/control/finish — 권욱 구현·채택**, 담당자 기존 원본 재사용 | 5역할 원본·control pin, 보관 인증·materializer, 선택한 baseline의 실제 grant·fresh 비교, replicas 필드 충돌 처리, 실행 및 종료 범위 | 기존 원본과 실제 승인 의미를 연결하고 아래 SSA 쟁점을 해소한다. 역사적 창 C 수동 GO·원본의 해시 일치를 #46 자동 배포 GO로 전환하지 않는다 |

누락값을 책임자에게 모두 새로 생산시키는 요청이 아니다. 2번의 코드/의미 연결은 권욱 업무이며, 1번은 기존 배치 사실을 담당자와 대조한다. full raw·SCC/Ready 원본 재수집을 요청하거나 새 API/SSH를 실행하지 않는다.

다음은 **사전 누락 입력이 아니다**: 미래 native run/attempt, READY nonce, 발급 응답·fingerprint·iat/exp, 실제 finish 관측 원본. 기존 코드가 해당 실행 때 생성·검증한다. 반면 생성 위치·범위·원본 해석 방식과 control 정책은 실행 전에 연결해야 한다. 사후 발급 응답 해시를 사전 `Binding.platform_receipt_sha256`에 역대입하지 않는다.

## 확인 방법과 인수 범위

저장소 루트에서 오프라인 상태만 확인한다.

```sh
python3 -B tools/phase2/delivery_job.py status
```

현재 정상 결과는 종료 코드 **2**, `P2_JOB_UNCONFIGURED`와 `operating_configuration.selection=PARTIAL`이다. baseline 기대값과 미확정 두 묶음이 보고되며 자격·실제 원본·환경변수·호스트를 조회하지 않는다. `--execute`를 붙여도 현재 기본 SITE는 활성화되지 않는다. 이 문서는 Runtime 실행 명령이나 GO가 아니다.

시험은 기존 SYNTHETIC 계획으로 실제 조립 타입, 기대 Runner 고정 및 native mismatch 차단, helper UID 분리, foreign target 거부, deferred materializer 무호출, 상태보고 무I/O 및 gate 불변을 확인한다. 기존 workflow/보안 경계·발급 권한·6객체 검증·claim/fence/finish 계약은 유지한다. 원복은 이 코드 변경을 되돌리는 것으로 충분하며 서버 자원 원복은 발생하지 않는다.

Claude Code·Grok·Antigravity에는 일반 구성 연결/상태 계약만 역할별 전달했다. 프로젝트 운영 메타데이터 전송은 자동 검토에서 거부돼 제공하지 않았다. 세 도구의 응답은 일반 설계 자문이며 이 소스·실행 원본의 외부 심층 검토나 독립 사람 승인이 아니다. Codex가 원본·코드·시험을 대조했다.

PR 병합 뒤에도 위 두 묶음 연결과 실제 실행 승인이 별도로 남는다. DB·Robot 유지 조건 및 **10/26 12:00 KST 최종 정리** 결정은 변경하지 않는다.

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
이 관측을 렌더의 replicas3 또는 임의 owner로 바꾸지 않았다. PR128 시점에는 owner/spec·
previous 선택이 잔여였으며, 다음 절의 명시적 코드 선택으로 연결한다. 최초 P2 배포 때의
`previous 없음` 기록만으로 현재 redeploy의 previous를 확정하지 않는다.

Runtime의 `CodeAdoptedBaseline`은 이후에도 원본/control을 앞뒤로 재인증하고 여섯 fresh
GET의 UID/spec/owner를 비교한 뒤에만 claim/write를 허용한다. raw 수신·형식 통과는 현재
클러스터 무변경 증명·실행 GO가 아니다. control/finish 범위 및 SSH·READY/helper 배치는
기존 잔여이며 `SITE=None`을 유지한다. 이번 중앙 작업은 로컬 수신·오프라인 구현/검증뿐이다.

## owner/spec·previous의 명시 선택과 실행 전 쟁점

`delivery_fds_observed_baseline`은 위 **정확한 묶음 hash/root**를 owner/spec 기대 기준선으로
선택한다. 전체 raw를 다시 쓰거나 managedFields/replicas/restartedAt을 제거하지 않는다.
다음 두 이미지를 다음 실행의 previous로 코드에 **명시 선택**했다.

- transaction-api: `10.1.93.54/fds/transaction-api@sha256:338b8a2871507ce8b42deedc916b26e350577ee6efd18df23d7ff2dd4b17dff0`
- fds-engine: `10.1.93.54/fds/fds-engine@sha256:8b257adb08b6d381f72b66d3386efea6112a8f072ecfafcbcd463bcd1c3be89d`

근거는 [승인 공급 계보/#43](https://github.com/321Team/fds-project-baseline/issues/43#issuecomment-6185877075),
기존 D22 FINAL·MSA42 source/render 인계, [실제 앱 SA Pod 6개 Ready/imageID 관측](https://github.com/321Team/fds-project-baseline/issues/47#issuecomment-6361606970),
[관측 JSON 반출](https://github.com/321Team/fds-project-baseline/issues/38#issuecomment-6366473628)과 수신 full raw다.
관측 JSON(`f0c6fe24…628e`)은 **Pod API 전체 raw가 아닌 6행 projection**이며 중앙에서
6/6 Ready·앱 SA·승인 image/imageID·restricted-v2·runAsUser를 대조했다. 이를 baseline으로 대체하지 않는다.

`connect_selected_baseline`은 기존 계획의 previous가 없을 때 이 코드 선택을 연결하고,
이미 같으면 수용하며 다르면 거부한다. 임의 현재 이미지에서 previous를 복사하지 않는다.
기존 다른 원본/생산자 사실은 보존하며 grant/control은 호출자가 기존 인증 경로로 공급한다.
`delivery_fds_operating.connect_baseline`은 이 선택 진입점을 직접 참조한다. 새 승인이나
rollback 성공은 만들지 않고 현재 live·rollback 상태는 미검증으로 표시한다. 코드는 독립 리뷰 대상이다.

**실행 전 실제 쟁점:** 수신 raw의 두 Deployment에서 `kubectl-patch`가 `f:spec/f:replicas`를
소유하고 값은 0이다. 다음 검토 렌더는 3이며 sender는 `fieldManager=fds-deployer`이고 force가 없다.
따라서 이 상태 그대로의 적용은 **SSA 409 충돌이 예상**된다. 이는 수신 아카이브 구현의
병합 차단이 아니라 이후 Runtime 준비의 쟁점이다. [Kubernetes 공식 충돌 규칙](https://kubernetes.io/docs/reference/using-api/server-side-apply/#conflicts)과 대조했다.
후속 승인된 운영 준비에서 현재 사용/중지 상태와 좁은 필드 인계 방식을 결정해야 한다.
전체 force 자동 추가·managedFields 제거·실제 replica 변경은 이번 구현에 포함하지 않는다.

PR129 병합 후 [replicas 소유권 처리안](replicas-ownership-plan.md)을 준비했다.
기존 ObjectReconciler가 알려진 replicas 충돌을 claim·sender 및 첫 Service PATCH보다
먼저 차단한다. 0 유지 중 협조적 필드 인계는 향후 운영 검토안이며 실행하지 않았다.
현재 previous와 target digest는 같으므로 다른 이미지로의 rollback 검증으로 확대하지 않는다.

## 저장소의 기존 SSH 값 교차 대조 — 2026-10-24

키 경로까지 전부 미확정이라는 앞선 설명을 아래 범위로 좁힌다. 코드·과거 실행 근거를
재사용하는 것이며 실제 private key/known_hosts 파일 내용을 조회하거나 새 SSH를 시도하지 않았다.

| 확인한 기존값 | 근거 / P2 채택의 경계 |
|---|---|
| 관리 출발지 `ansible01 / server01_ansible`, 대상 `10.250.10.20 / server01_cicd-runner` | [#82 실제 10/20 공급 보고](https://github.com/321Team/infra-ansible/issues/82#issuecomment-6283088910)의 B→A 관리 전송. 당시 release helper의 검토 사본도 대조했다. 역방향·CloudShell→Runner 성공이나 현재 가용성을 증명하지 않는다 |
| 기존 키 `/home/server01_ansible/.ssh/p1_ansible_ed25519` | [infra-ansible 코드](https://github.com/321Team/infra-ansible/blob/7f04e18371f5d10aec678460f5dcd6e28d98d7d3/tools/ntp-onepass.py#L39), [검증기](https://github.com/321Team/infra-ansible/blob/7f04e18371f5d10aec678460f5dcd6e28d98d7d3/scripts/verify-ssh-main.py#L82), [baseline Runner 인증 참조](https://github.com/321Team/fds-project-baseline/blob/6219aa4a0246c6d2ce631006e1b022e775ed28d7/governance/handoffs/phase1-execution-20261010/DEPLOY_AFTER_PUSH_INPUTS_20261013.json#L17). 10/20 release helper도 이 경로를 명시했다. **CP1의 `id_ed25519`와 구별**한다 |
| 기존 known_hosts `/home/server01_ansible/.ssh/known_hosts` | [동일 관리 코드](https://github.com/321Team/infra-ansible/blob/7f04e18371f5d10aec678460f5dcd6e28d98d7d3/tools/ntp-onepass.py#L40) 및 native broker의 실행자 HOME 경로. 경로 근거이며 현재 host key 항목·파일 권한의 직접 확인이 아니다 |
| ROSA 발급 담당 `hayeoung`; 과거 원본은 CloudShell의 `~/c-deploy/`에서 수집 | [#46 수집 기록](https://github.com/321Team/fds-project-baseline/issues/46#issuecomment-6366234985). 원본 수집 위치와 담당 지정이며 ansible01에 named-admin credential이 있다는 증거가 아니다 |

따라서 기존 관리 경로의 IP/계정/key/known_hosts를 다시 전부 제출받을 필요는 없다.
남은 연결 사실은 **ROSA issuer를 실제 어디에서 실행할지**, 그 위치에서 이 관리 SSH를
어떻게 사용할지, Runner의 **P2 helper 고정 경로·실행 UID·job과 같은 netns**와 인증된
READY의 왕복 방식이다. `inventory/hosts.yml`의 svc_ansible은 다른 관리 대상 계정이며
장기 Runner나 P2 helper의 계정으로 복사하지 않는다. #82 packet 파일 전송 코드를 #46의
메모리 자격 전달에 그대로 사용하지 않는다. 기존 소스/control/finish 인증 계약을 유지하고,
전체 `SshBridgeConfig`·`SITE`를 채우지는 않았다. 필요한 것은 위 남은 사실/중앙 배치 선택이며
새 key·신규 권한·원본 재수집이나 과거 Runner 시험의 전량 반복을 요구하는 절차가 아니다.

위 역사적 관리 경로를 [명시 bridge 설정](management-ssh-selection.md)에 연결했다.
같은 로그인 계정의 helper 기대 UID1000은 이 경로에만 명시 선택하며 현재 관측값으로
표시하지 않는다. 포트·host-key 별칭·remote Python/helper·예산은 필수 입력이고,
issuer 위치·helper 배치/netns·실제 원본/control/finish가 빠진 전체 SITE는 계속 차단한다.

## 10/24 운영 구성 결정

권욱이 10/24 권장안의 운영 구성 채택·연결 구현·오프라인 시험·검토 PR 준비를 지시했다.
이는 **설계 선택**이며 실제 배치·권한 변경·Runtime·새 GO를 승인한 것이 아니다.
기존 원본을 합성하거나 새 승인 파일·개별 파일 재승인 절차를 추가하지 않는다.

| 결정 | 채택·코드 연결 | 현재 확인 범위 |
|---|---|---|
| 발급 | `ansible01 / server01_ansible`에서 기존 직접 `issue`+SSH, `gwonuk` 본인 actor | 기존 직접 supplier와 관리 경로의 결합 선택. source-host 실제 UID·API 도달·현재 개인 credential은 미확인. 이름 문자열은 인증에 사용하지 않음 |
| helper 배치 | 현재 job exact workflow SHA checkout, 기존 entrypoint·일반 로그인 계정, 별도 기대 helper UID1000 | `build_job_bridge_configuration`은 명시 workspace+검증된 `JobContext`의 경로만 계산하고 기존 SSH 설정 검사를 사용. 빈값/상대/중복·dot·dotdot/명령문/다른 helper를 허용하지 않음. symlink·파일 소유권·현재 배치는 이 순수 함수가 확인하지 않음 |
| 권한 | 본인 actor 사용과 기존 배포 SA 검증 유지, 권한 조작 구현 없음 | 권고한 최소 권한과 담당자가 보고한 임시 admin을 구분. 아래 보고는 실효 권한 검증이나 새 포괄 승인으로 바꾸지 않음 |
| 자격 정리 | 메모리·소유 익명 pipe 전달, actor OAuth 서버 회수·발급 SA token 만료·로컬 참조 정리를 분리 | 기존 issuer API 재사용. 서버 회수 방식·실응답은 미연결. `oc logout` RC0는 서버 OAuth 회수 보장이 아님 |
| 원본/control | 기존 approval/platform/producer/finish/baseline 5역할과 repository-scoped reader/materializer 재사용 | 실제 scope·단일 control ref 의미/pin·인증 채널은 명시 공급해야 함. 댓글 링크·해시·typed grant만으로 승인 원본을 만들지 않음 |
| finish | `assemble_observed_job`이 기존 `ObservationRoute`와 `OBJECT_READBACK`·`ROLLOUT_READBACK`을 선택 | sender close 뒤 같은 credential·Binding·progress·기한으로 관측. 실제 completion scope grant는 caller가 공급하며 생성 시 읽지 않음. control 철회·관측 미확인은 claim/fence 유지. C-STOP·DB/Secret/Robot/VPN/클라우드 정리는 포함하지 않음 |
| replicas | 임시 manager의 한정된 replicas0 인계 → 기존 manager의 **완전 intent+0, force 없음** → 공동 소유 확인 → 임시 manager replicas 해제 → 새 full baseline | 이전 `fds-deployer` replicas-only force 권고는 철회. 실제 인계·새 baseline·SSA는 미실행. 이전 raw와 새 현재값을 자동 교체하지 않음 |

`assemble_observed_job`은 기존 `assemble_existing_job_site`에 위 정책을 연결한다.
일반 `assemble_job` API는 호환성을 위해 유지하고, 새 운영 진입점에는 runner/helper UID,
wire·별도 finish capture override를 받지 않는다. 기존 full plan 또는 수신 후 materializer
XOR·소유권·정리 계약을 그대로 사용한다. 생성은 clock/callback/nonce/자격/원본/소켓 I/O를
수행하지 않는다. 생성 성공은 후속 인증·실행 승인이 아니다.

### 새 담당자 회신과 권한의 구분

[하영 #5 기록 6379184539](https://github.com/321Team/infra-rosa/issues/5#issuecomment-6379184539)는
`gwonuk-p2-admin-temp` → ClusterRole `admin`의 RoleBinding을 `fds-app`·`ci-cd`에
추가했다고 보고했다. [#46 회신](https://github.com/321Team/fds-project-baseline/issues/46#issuecomment-6379184704)은
10/25 오전 하영 부재, 권욱 본인 계정 인계·월요일 하영 회수를 명시했다.
이 PR에서는 실제 권한 조회·추가·삭제·원복을 수행하지 않는다. 이를 권고한
Deployment2 get/patch+named SA TokenRequest 최소 권한이 적용됐다고 표시하지 않는다.
10/26 12:00은 회수 책임·기한이며 annotation은 자동 만료 장치가 아니다.

현재 actor의 patch/TokenRequest 가능 여부와 **발급된 배포 SA**의 CV/version·namespace
GET·SSR/SSAR·6객체 get/patch 검증은 별개다. 기존 verifier를 생략하거나 다른 credential로
대체하지 않는다. 실제 부족이 확인될 때만 해당 권한을 별도 검토한다.

[oc RunLogout 소스](https://github.com/openshift/oc/blob/main/pkg/cli/logout/logout.go)는
서버 token 삭제 실패와 로컬 config 정리 성공이 다를 수 있음을 보여 준다.
현장 oc 버전·메모리 전용 회수 경로와 서버 응답은 미확인이다. 이 PR은 kubeconfig를
생성하거나 `oc logout`을 호출하지 않으며, 로컬 참조 정리를 서버 회수 성공으로 보고하지 않는다.

### 미확정 배치값·담당·완료조건

| 묶음 | 담당·기존 사실 지원 | 남은 실제값 / 완료조건 |
|---|---|---|
| 발급측 | 권욱, 하영 기존 연결 사실 지원 | 선택한 host/account의 현재 실행 신원·API443 도달·본인 actor 인증/권한·메모리 전달·서버 회수 응답. source-host UID·credential을 추정하지 않음 |
| SSH/helper | 권욱, 재환 기존 Runner 배치 사실 지원 | 실제 `GITHUB_WORKSPACE`, SSH port/host-key alias·현재 신뢰, remote Python, helper 실제 UID·같은 job netns·검토 코드/소유권, 인증된 READY 전달. 기존 key/known_hosts 경로 재제출 불필요 |
| 파일/저장 | 권욱, 기존 호스트 자료 재사용 | 실제 input/source 위치와 checkout 밖의 P2 전용 영속 claim/checkpoint store. 이전 P1 store를 공유하거나 추정한 절대경로를 승인값으로 만들지 않음 |
| 원본/control/finish | 권욱 구현·의미 연결, 하영·재환의 기존 원본 재사용 | 실제 5역할 original/control·repository-scoped source 채널·materializer·선택한 finish scope. grant 형식 검사와 실제 원본 인증을 구분 |
| 기준선/운영 인수 | 권욱, 하영·재환 기존 인계 사실 지원 | 승인창 내 replicas 인계와 새 full baseline 명시 채택, fresh drift 확인 후 별도 Runtime/인수 판단. 미해소면 Runtime 미실행으로 남기고 10/26 12:00 최종 정리 우선 |

미래 run/nonce·TokenRequest 응답·iat/exp·finish 관측/hash는 실행 중 생성될 값이다.
지금 없는 배치값과 구분하며 사전 합성·제출을 요구하지 않는다. 같은 UID는 강한 프로세스
격리가 아니며 Runner/SSH/shell을 모두 없애라는 뜻도 아니다. 경쟁 job·미승인 자격 소비가
없는 승인 창과 기존 native context/revision/peer/nonce/기한·단회 소비 검증을 유지한다.

### 검증 범위

새 오프라인 시험은 SYNTHETIC 원본·grant·자격·객체만 사용한다. 경로/context 거부,
구성 무I/O, 정책 override 거부, full/prepared 검증·정리, 새 운영 조립이 만든 5역할 wiring의
private-source → 원본/control 인증 → sender close 뒤 관측을 확인한다. control 철회 시
claim/fence 유지·완료 원본 미생성을 확인한다. 실제 발급/SSH/설치·권한·SSA/배포 증명은 아니다.
`delivery_job_site.SITE=None`·기본 dispatch false·`runtime=NOT_RUN`을 유지한다.


## 10/24 본인 OAuth 인증·발급 전달·서버 회수 연결

PR132 병합본 `329fe5d8` 위에 `build_actor_channel`을 추가했다.
선택된 본인 actor와 기존 SA/CA/대상 profile을 `ActorCredentialChannel`에 연결한다.
현재 실제 자격·endpoint·SSH 배치·승인 원본이 공급된 전체 SITE는 아니며
`SITE=None`·dispatch false·Runtime NOT_RUN을 유지한다.

### 별도 실행 승인이 있을 때의 호출 계약

1. 발급 위치는 기존 결정인 ansible01/server01_ansible이다. 신뢰한 호출자가
   **본인 전용 OAuth token**을 메모리로 공급하고 현재 endpoint와 전달·회수 deadline을 명시한다.
   로그인·암호 입력·kubeconfig 로딩·환경 자격 탐색·권한 추가는 이 구현에 없다.
2. `build_actor_channel(config, actor=..., endpoint_ip=..., operation_deadline=...,
   cleanup_deadline=..., monotonic=..., wall=...)`는 무통신 생성이다.
   예상 사용자명은 채택한 `gwonuk`, SA/CA는 기존 profile이며 override를 받지 않는다.
3. caller가 이미 인증·채택한 native request와 source credential 목록, 검토된
   `SshCredentialBridge`를 `send_over_bridge`에 전달한다.
   같은 시계·명시 전달 기한을 대조하고, 고정 TLS GET users/~의 User 이름·UID 형식을 확인한 뒤
   기존 단회 TokenRequest 및 READY/peer/revision/기한 검증을 거쳐 전달한다.
   본인 이름 확인은 SA RBAC·실행 GO·현재 host 인증을 대신하지 않는다.
4. 인증이 확인된 actor는 전달 성공/실패와 별개로 **그 token 자신만 DELETE 1회**한다.
   전달 기한보다 뒤에 명시한 회수 기한 안에서 최대 10초 요청을 사용하며 자동 연장·재시도하지 않는다.
   인증 불일치/실패에서는 발급·타인 자격 삭제를 하지 않고 서버 정리 미확인으로 인계한다.
5. 정상 삭제 객체 또는 엄격한 성공 Status가 담긴 HTTP200만 서버 응답 확인으로 기록한다.
   202/204/401/403/404·비정상200·timeout·회수 기한 초과는 성공이 아니다.
   local close는 참조 해제만 하며 네트워크를 호출하지 않는다.
6. 발급 후 전달·회수가 실패해도 `take_receipt()`로 실제 비공개 발급 응답을 1회 가져올 수 있다.
   이 원본에는 SA token이 있으므로 공개 로그/PR에 넣지 않는다. receipt는 승인 원본으로 자동 채택하지 않는다.
   이미 발급된 SA token은 actor 회수와 별개로 만료·receiver 검증을 따르며 재발급하지 않는다.

| 보고 항목 | 의미 / 한계 |
|---|---|
| actor_identity=CONFIRMED | 같은 TLS endpoint의 users/~가 기대 본인 이름을 반환. 권한·GO는 별도 |
| issuance_attempted | POST 시도 진입. 실패하면 발급 여부가 불명확할 수 있음 |
| credential_forwarded | 기존 SSH 전달기가 완료. job 수용·배포 성공은 아님 |
| actor_server_revocation=ACKNOWLEDGED | 정확한 자기 DELETE의 성공 응답. 캐시 전파·모든 사본 무효 증명은 아님 |
| UNCONFIRMED / NOT_ATTEMPTED | 회수 확인 불가 / 미시도. 성공·삭제 안 됨으로 단정하지 않음 |
| local_references_released | 이 객체의 참조 정리. Python 문자열 zeroization·caller 사본 폐기를 보장하지 않음 |

동시 재호출은 단회 reservation으로 차단한다. KeyboardInterrupt/SystemExit의 취소 의미를
보존하며 정리 결과는 status에 따로 남긴다. SIGKILL·프로세스 소멸 때 finally 실행은 보장되지
않으므로 전용 actor의 외부 회수 책임과 SA 만료 확인이 필요하다. 자동 credential 공유나
다른 기존 로그인 세션을 재사용하지 않는다.

현대 `sha256~` OAuth token만 지원한다. 파생 object name은
[oc logout 구현](https://github.com/openshift/oc/blob/main/pkg/cli/logout/logout.go)의
prefix 제외 SHA256/base64url 규칙을 사용한다. token 원문을 URL에 넣는 legacy fallback은 없다.
[WhoAmI 구현](https://github.com/openshift/oc/blob/main/pkg/helpers/project/whoami.go)의
users/~ 경로만 사용하며, oc의 토큰 해시/로컬 사용자 fallback은 인증으로 채택하지 않는다.
[OAuth store](https://github.com/openshift/oauth-apiserver/blob/master/pkg/oauth/apiserver/registry/oauthaccesstoken/etcd/etcd.go)는
삭제 객체를 반환한다. 참고 소스 대조는 현장 oc/API 버전의 실동작 검증과 구분한다.

**미확정 잔여:** 실제 host/SSH workspace·Python/UID/netns/READY/store, 본인 전용 자격의
안전한 입력 경로·현재 endpoint 및 실행/회수 예산, 기존 5역할 원본/control·finish scope,
replicas 인계 뒤 새 full baseline. 현재 원본을 재작성하거나 미래 token/run/hash를 합성하지 않는다.

오프라인 확인: 전송·SSH를 fake/차단한 Windows 전체 시험 1,016건 중 854 PASS·162 SKIP.
본인 확인 실패, 발급/전달 실패, 회수 불명확·기한 초과, 중복 호출과 취소를 포함한다.
현장 로그인·TokenRequest·SSH·DELETE·배포는 실행하지 않았다.
