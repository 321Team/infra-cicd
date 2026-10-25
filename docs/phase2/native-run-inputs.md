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

최종 입력 파일 회수는 권욱 담당이다. 마지막 소비·앱/claim/fence·메모리 세션 상태와 필요한 증적 보존/반출을
확인한 뒤 P2 자기 파일만 회수한다. 소비 종료 계획10/26 08:30·정리/복구/반출/보고12:00은 유지한다.
ROSA/RoleBinding 회수와 나중 Runner 등록 해제→VM 폐기는 별개다.
