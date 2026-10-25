# #46 실제 native RUN 입력 파일 배치

2026-10-25 구현. 코드·오프라인 준비이며 실제 Runner 파일 배치·자격 발급·앱 배포는 미실행이다.

## 환경과 입력

- 실행 위치: 기존 `cicd-runner01 / server01_cicd-runner / UID1000`. 기존 driver가 신원·revision을 검사한 뒤 build를 호출한다.
- 부모: `/home/server01_cicd-runner/fds-p2-delivery/inputs`, 이미 준비한 mode0700 디렉터리다. 없거나 안전하지 않으면 새로 만들거나 chmod하지 않고 중단한다.
- 원본 template: `inputs/prepared-69e28c494a120c388afe18d901043d05333d7c7c`의 기존 `rendered.yaml`·`contract.yaml`. 파일은0600·자기 UID·정확 hash여야 한다. 이 template 자체는 실제 RUN 입력이 아니다.
- baseline: 권욱이 실제 인계 후 받은 파일을 Runner의 명시 private staging root에 전달한 뒤, `RunInputFiles(baseline_source_root, PrivateBaselineFile)`로 그 실제 basename/hash를 코드에서 선택한다. 현재 새 원본·staging 값은 채우지 않았다. 공유 UNC를 탐색하거나 파일 본문에서 설정을 읽지 않는다.
- 준비 근거: 비공개 공유 `02차 프로젝트/p46-host-preparation-20261025-0244.zip`, SHA256 `a9c3d7d952c6d267e1f75fbe3a7ec5403cb59a4a6097ee3fb1620c72faacfe86`. 기존 수신/해시 검토를 재사용한다. 나중에 수정되는 구성 댓글은 이 준비 원본의 참조일 뿐 실행 원본으로 사용하지 않는다.

파일·경로·context는 권위를 부여하지 않는다. 아래 선택은 검토된 Python site 코드에서만 하며
새 승인 파일·JSON loader·환경 자격 탐색을 추가하지 않는다.

## 호출과 실제 순서

1. `select_inputs(context, command)`는 실제 driver의 `JobContext`를 받는다.
   기존 `build_configuration(..., input_root=run_input_root(context), ...)`로 대상 경로를 선택하고
   실제 grant·원본/control·baseline metadata 등 기존 조립 인자를 반환한다. selector는 I/O를 하지 않는다.
2. `declare_observed_native_job(select_inputs=..., input_files=RunInputFiles(...))`로 기존 JobSite를 선언한다.
   선언·status·hosted gate는 파일·clock·selector·발급·READY를 호출하지 않는다. 옵션을 생략한 기존 동작은 유지한다.
3. 실제 build에서 기존 operating assembly가 Binding·역할·원본 metadata를 먼저 검사한다.
   `input_files.baseline_file`이 기존 deferred 선택의 `baseline_file`과 같아야 한다.
   실제 원문/control 인증은 뒤의 credential handoff에서 수행하며 metadata 검사로 이를 대체하지 않는다.
4. `prepare_run_inputs`는 실제 context의 `inputs/{run_id}-{attempt}`와 config 경로를 정확히 대조한다.
   native attempt는 기존처럼 **1만 허용**한다. 미래 RUN을 지금 만들어 넣거나 attempt2 자동 재실행을 추가하지 않는다.
5. 기존 dir-FD reader로 template2개·명시 baseline1개를 읽는다. 부모/원본 root0700·UID,
   파일0600·regular·nlink1·NOFOLLOW/NONBLOCK·1MiB 상한·전후 metadata/inode·정확 SHA와 root 안정성을 검사한다.
6. 기존 inputs 부모 FD 아래 새 RUN 디렉터리만 mkdir0700으로 만들고 부모를 fsync한다.
   target은 부모 FD에 대해 상대 open·inode 대조 후 유지한다. 기존 `create_file`의 O_EXCL0600·short-write loop·file/dir fsync·바이트/inode 검증으로3개를 쓴다.
   마지막에 파일3개 전체의 hash·정확 이름 집합·source/target/parent 안정성을 확인한다. tar는 추출하지 않는다.
7. 준비가 성공해야 기존 owned builder가 READY를 연다. 이후 단회 메모리 자격 handoff,
   원문/control 전후 인증·baseline 해석/owner/spec/previous·native producer·claim/fence·sender close 후 finish 검증을 그대로 거친다.

기존 workflow는 dispatch 입력의 명령1개만 driver에 넘기고 선언도 단회 소비한다.
같은 RUN에서 별도 preflight build와 deploy build를 연속 수행하는 경로를 추가하지 않았다.
preflight/배포를 각각 실행할 때는 기존 별도 native RUN 경로를 사용하며 입력 디렉터리를 재사용하지 않는다.

`input_root` 생성과 파일 복사는 grant 승인·현재 baseline 채택·Runtime 인수 증거가 아니다.
native metadata도 실행 GO가 아니다. `SITE=None`·기본 dispatch 차단을 유지한다.

## 실패와 복구

| 조건 | 동작·인수 |
|---|---|
| 경로/context·파일 선택 불일치, 파일명 중복 | source I/O 또는 target 생성 전에 중단. 다른 경로·이전 원본으로 대체하지 않음 |
| 기존 완성/부분 디렉터리·동명 symlink | 재사용·덮어쓰기·자동 삭제·권한 수리 없이 중단 |
| hash/권한/link/FIFO·원본/root 변경 | 기존 reader가 거부. opaque baseline의 배치 성공을 manifest/의미 검토 성공으로 확대하지 않음 |
| 짧은 write·ENOSPC·fsync 실패·중단 | 기존 writer는 짧은 write를 끝까지 처리하고 나머지 오류는 실패. 열린 FD를 모두 정리 시도하며 공개 오류에 원문·경로를 남기지 않음. KeyboardInterrupt/exit130 구분 유지 |
| 부분·완성 입력 뒤 후속 실패 | READY 전 실패를 해당 declaration에서 소비하고 내부 plan/owner를 정리. 디스크 입력은 보존하며 자동으로 재사용하지 않음. 원인·부분 상태를 비공개 인수한 뒤 별도 복구 또는 새 승인 RUN으로 처리. 같은 디렉터리 재시도/attempt2는 허용하지 않음 |

`store.root_open`이 FD를 연 뒤 초기 fsync에서 interrupt를 받아도 자기 FD를 닫도록 보완했다.
같은 UID의 다른 프로세스를 격리하는 구현은 아니다. 기존 job 비중첩 조건을 유지한다.
POSIX 권한/FD 시험은 hosted Linux에서, Windows에서는 무I/O 조립·거부 경계만 확인한다.

최종 입력 파일 회수는 권욱 담당이다. 성공한 RUN과 실패 후 남은 `inputs/{run_id}-{attempt}` 부분 디렉터리를
모두 #55의 권욱 P2 자기 파일 회수에 포함한다. 마지막 소비·앱/claim/fence·메모리 세션 상태와 필요한 증적 보존/반출을
확인한 뒤 P2 자기 파일만 회수한다. 소비 종료 계획10/26 08:30·정리/복구/반출/보고12:00은 유지한다.
ROSA/RoleBinding 회수와 나중 Runner 등록 해제→VM 폐기는 별개다.

## 실제 RUN grant·baseline·인증·finish 연결

`build_observed_run_inputs(context, command, ...)`는 기존 selector에서 사용할 무I/O 조립 함수다.
검토된 site 코드가 명시한 `NativePlanSelection`, 별도 baseline grant/root와 `RunInputFiles`,
별도 finish scope, 실제 source 채널·peer·clock/deadline·budget/finalize를 받는다.
원문·환경변수·파일 본문에서 정책을 읽거나 새 grant를 만들지 않는다. 기본 `SITE=None`은 유지한다.

1. 실제 driver context를 확인하고, 기존 prepared source/state·파일명과 native RUN 입력 경로를 연결한다.
   **논리 Binding.run_id는 실제 grant에 명시한 값을 보존**한다. Actions의 숫자 RUN ID와 같다고 추정하거나
   이를 덮어쓰지 않는다. Actions RUN/attempt는 입력 경로와 기존 native context 결속에 사용한다.
2. 기존 `connect_reviewed_producer`로 이미 채택한 공급/native4를 연결한다. 명시한 previous가 필요하며
   caller의 producer facts가 다르면 정정·대체하지 않고 거부한다. source/공급 원문 의미 검증은 그대로 남는다.
3. 기존 private metadata/policy 검사를 통해 같은 Binding·control·기간·SA/target·baseline 파일 선택을 확인한다.
   이 함수는 **인계 후 새 private baseline 파일 경로**만 사용하며 old raw/기존 full plan으로 자동 전환하지 않는다.
4. 명시 grant 5개(approval/platform/producer/finish/baseline)의 original/control 저장소를 기존 role routing으로
   연결하고, 비어 있는 `FinishAdoptionSlot`을 만든 뒤 기존 native assembly 인자만 반환한다.
   grant는 검토된 site 코드의 선택이고 routing은 원문 승인 파서나 독립적인 권위 근거가 아니다.
   기존 `SourceRoleWiring`의 실제 채널·repository 검사와 원문/control의 사전·사후 인증을 생략하지 않는다.
5. selector가 반환한 인자는 `declare_observed_native_job`의 기존 조립→파일 배치→READY 순서로 진행한다.
   발급측 `build_observed_login_supplier`에도 **같은 반환 config와 context**, routes에서 선택한 동일 repository
   순서 및 실제 source 자격을 전달한다. 본인 메모리 로그인·현재 실효 권한·인증된 READY는 실제 실행 때 확인한다.
   자격을 파일·환경변수로 쓰거나 과거 workspace·하영 token으로 대체하지 않는다.
6. finish scope는 deploy grant에서 복사하지 않는다. sender close 뒤 기존 객체/rollout 관측이 실제 원본을
   생산·검증해 slot을 설치한다. 미래 finish hash를 제출하거나 지금 성공 기록을 만들지 않는다.

| 현재 상태 | 남은 행동·담당 |
|---|---|
| 준비된 실제 값 | 기존 Runner/관리 SSH·API/OAuth 입력, source/template/private interpreter, 공급/native4 선택을 재사용 |
| 권욱의 실제 입력 필요 | RUN/action별 grant의 원문 의미·기간/control, replicas 인계 뒤 새 full baseline metadata·원본·staging, 별도 finish scope를 기존 조립에 명시 |
| 실제 실행 때 생성·검증 | native context·nonce·본인 인증·TokenRequest 응답·인증된 READY·fresh drift·finish 원본. 미리 합성하거나 담당자 사전 제출 대상으로 만들지 않음 |

상시 실행 시간대는 무기한 grant가 아니다. 실제 grant 기간·예산은 기존 policy/실행 경로가 검사하며
wall-clock 만료를 monotonic deadline과 직접 비교하거나 240분을 새 기본 예산으로 넣지 않는다.
이 연결의 오프라인 시험·코드 리뷰·병합을 실제 파일 배치·발급·배포 또는 Runtime 인수로 확대하지 않는다.

## PR150 통합 구현과 한 번의 인수 검토

후속 구현은 이 PR에 모았다. 새 선언 wrapper·grant 형식·설정 loader·승인 절차는 추가하지 않았다.

- `build_observed_login_supplier`는 native SITE가 요구하는 TokenRequest 원본 전달을 필수로 선택한다.
  이 공급부의 `send()`는 기존 v2 메모리 프레임을 사용하며 `include_original=False`로 빠뜨릴 수 없다.
  일반 `build_login_supplier`·`MemoryOperatingSupplier`의 기존 v1 기본값은 유지한다.
  v2 프레임 예산은 로그인 전에 확인하고 실제 원본 parser/동일 context·Binding 검사를 유지한다.
- 구성 생성 실패는 고정 오류/취소로 정제한다. **성공한 반환 전까지 login provider는 caller 소유**이므로
  빌더가 실패한 provider를 임의로 닫지 않는다. caller는 자기 `finally`에서 이를 닫아야 한다.
- 성공한 공급부가 소유한 actor/provider는 상태·receipt·metadata 수집 중 일부가 실패해도 각 독립 수집과
  로컬 close를 시도한다. close 실패·취소를 성공으로 바꾸지 않으며 이후 close 호출도 실패 상태를 보존한다.
  실제 OAuth 회수 응답과 로컬 참조 해제는 계속 구분한다. UNKNOWN을 ACK로 만들지 않는다.
- 공급 실패 후에도 실제 발급 receipt와 로그인 복구 metadata가 수신됐다면 각각 단회 `take_*`로 인수할 수 있다.
  **소유 공급부를 명시적으로 close하기 전에** 두 private handoff를 가져와 비공개 복구 담당에게 넘긴다.
  명시 close는 남은 private handoff 참조를 버린다. 원문·토큰·비밀번호는 공개 상태·argv·환경·로그에 넣지 않는다.
- `credential_forwarded`는 전달 단계이고 `job_accepted`, SA 회수, 자원 정리, Runtime 인수를 뜻하지 않는다.
  예를 들어 전송 뒤 OAuth 회수가 불명확해도 job은 이미 프레임을 받았을 수 있다. 재전송하지 말고 job 결과,
  private receipt·OAuth object/lifetime metadata와 fence 상태를 각각 인수한다.

### 통합시험의 끝단과 한계

`test_delivery_fds_vertical`는 실제 `build_observed_run_inputs` → 선언 → 새 RUN 파일 배치 →
인증된 local READY/helper·UNIX/pipe → native 원본 reader/materializer → private baseline archive 해석 →
5역할 원문/control 및 native4 검사 → 기존 provider/session → sender close 이후 finish를 연결한다.
권위 사실을 발급 응답을 본 뒤 고치거나 materializer를 테스트용 성공 콜백으로 바꾸지 않는다.

| 모의 시나리오 | 완료조건 |
|---|---|
| 정상 흐름 | 동일 Binding·논리/native RUN 분리, 파일3개, native 원본, 5역할, 관측6개·완료·fence 해제·프로세스/소켓 정리 |
| baseline hash 변경·파일 부분 생성 실패 | READY·로그인·claim 전에 거부. 부분 파일은 보존하고 발급측 소유자는 미소비 자격 입력을 명시적으로 회수 |
| READY helper 실패 | 로그인·발급·claim0, 공급부 소유 password 참조 정리 |
| 본인 로그인 거부 | TokenRequest·claim·앱 쓰기0, 원문/토큰 없는 오류와 로컬 참조 정리 |
| 원문/control 변경·필수 native step 실패 | claim/앱 쓰기0. 발급된 자격/회수 상태는 별도 인수 |
| sender close 후 finish 보존 실패 | fence 유지·완료0. 같은 선언/attempt2/기존 deploy grant의 restore로 우회하지 않음 |
| 전송 뒤 OAuth 회수 불명확 | 실제 job 결과와 UNCONFIRMED를 분리, private receipt/metadata 단회 인수·자동 재전송0 |
| 상태·receipt/metadata 수집 또는 close 실패/취소 | 나머지 로컬 정리 시도, 비밀/외부 예외 비노출, 실패를 다음 close에서 성공으로 바꾸지 않음 |

이 시험은 **합성 입력·외부 HTTP/SSH/API mock·메모리 claim store**를 사용한다. Linux의 파일·권한/FD,
local helper subprocess·AF_UNIX·pipe는 실제 실행하지만 운영 호스트/원본/자격/배포 증거가 아니다.
application file-loader와 source API는 fixture 경계이며 실제 byte loader·POSIX claim 내구성은 기존 별도 시험에 연결한다.
Windows에서는 Linux 통합시험을 SKIP으로 남긴다. 기존 정상/실패/별도 restore Binding·fence 보존 시험도 함께 실행한다.

### 운영값 미확정 목록 — 같은 요청으로 한 번에 인수

| 담당 | 남은 실제 선택·원본 | 코드 완료와 구분할 완료조건 |
|---|---|---|
| 권욱 | RUN/action별 approval/platform/producer/baseline/finish grant의 원문 pin·의미·유한기간/control | 이미 받은 원본에서 실제 대상/행위를 채택. 구성 댓글·코드 승인·정리기한을 실행 GO로 대체하지 않음 |
| 권욱(기존 하영·재환 인계 재사용) | replicas 인계 후 새 full baseline metadata pin·실물 SHA/root·명시 staging | 현재6객체 UID/spec/owner·previous를 동일 RUN에 결속. 과거 archive/UID projection 대체0 |
| 권욱 | 실제 두 호스트의 동일 config/context·source repository 순서와 본인 메모리 자격 | 생성된 실제 native context와 같은 경로/교환. 자격은 환경/파일/argv로 공급하지 않음 |
| 권욱 | 실제 로그인·실효 권한·인증된 READY, post-close finish와 종료/복구 인수 | 실행 승인 후 현장에서 검증. nonce·receipt·finish hash를 사전 제출시키지 않음 |

확인한 SSH/OAuth·source/template/private interpreter·생산자/native4는 재사용한다.
위 운영값이 미확정인 동안 `SITE=None`·dispatch 차단·Runtime NOT_RUN을 유지한다.
새 실질 변경 없이 같은 사람 리뷰를 반복하거나, 이 표의 항목마다 새 기능 PR을 만들지 않는다.
