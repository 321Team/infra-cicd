# P1-CICD-01 Runner SELinux 실행 차단 검토·복구 v1.1

## 최신 판정 — 복구 결과 수신

**2026-09-09 17:48:25 KST 제출 증적 기준, 서비스 복구와 GitHub 연결·Job 대기 상태는 PASS다.** 아래 v1.0 적용 명령은 이번 장애의 이력·동일 조건 재발 대응용이며 정상 동작 중인 현재 Runner에서 반복 실행하지 않는다.

`P1-CICD-01_Runner_SELinux_Result.tar.gz`의 외부 sidecar와 내부 SHA256SUMS에 기재된 일반 파일 4개를 검증했다. 압축파일에는 그 4개와 SHA256SUMS, 총 5개의 일반 파일만 있다.

| 검증 대상 | SHA-256 |
|---|---|
| 결과 archive | 8af569a10468ad0681634e0c0ff4e7076ded63ac0efc93953244665cce700f1a |
| result.txt | 9b7f6262d720f2813b874393d47b6d03df9954a924b1192336e7fa4abaf53f54 |
| before.txt | 2c3a65fb55db18558287a277061f5672f7081913a96f0645e6aff4bee0da924f |
| fcontext-before.txt | e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 |
| added-rule.txt | 63ffa281ad56144f65ae2b5e918124264c76238b0995408c6d8fa1575bd7b0b8 |

| 항목 | 복구 후 관측 | 판정 범위 |
|---|---|---|
| SELinux | Enforcing | 시스템 설정 유지 |
| 서비스 진입점 | system_u:object_r:bin_t:s0, 0755, 기존 사용자 소유 | 해당 파일 라벨 변경 확인 |
| 서비스 | active/running, Result=success, enabled, NRestarts=0, MainPID=12907 | 약 10초 관측. 재부팅·장시간 지속성은 미검증 |
| 실행 사용자 설정 | User=server01_cicd-runner | systemd 설정 확인. ps 사용자 열은 server0+로 잘려 숫자 UID까지 독립 확인한 것은 아님 |
| 실행 도메인 | system_u:system_r:unconfined_service_t:s0 | 예상한 일반 서비스 도메인 관측. Runner 전용 격리 정책 적용 판정은 아님 |
| GitHub 연결 | 17:48:17 KST Connected to GitHub | 제출된 서비스 로그 기준 |
| Runner 대기 | 17:48:18 KST, version 2.337.0, Listening for Jobs | Job 대기 상태 도달. 실제 Job 수신·성공은 미검증 |

fcontext-before.txt는 빈 파일이며 그 빈 바이트의 해시가 일치한다. added-rule.txt는 이 Runbook의 단일 파일 선택자와 일치한다. 규칙 추가와 matchpathcon 검증 성공은 제출 실행 로그에 기록됐으나, 변경 후 전체 규칙 목록이나 재부팅 후 재적용 증적은 아직 없다. 체크섬은 제출 바이트의 무결성을 검증하며 원격 실행 전체를 독립 증명하지 않는다.

콘솔 앞부분의 명령 텍스트는 일부 섞여 보이지만 결과 archive가 정상 검증되므로 이 표시만으로 Python 또는 전체 복구 실행 실패를 판정하지 않는다. 결과 파일에 남은 17:29:47의 203/EXEC는 PID 12646의 과거 실패이며, 17:48:15에 시작된 새 PID 12907의 복구 결과와 구분한다.

현재 상태: `RUNNER_SERVICE_RECOVERY=PASS_FROM_SUBMITTED_EVIDENCE`, `RUNNER_JOB_WAIT=OBSERVED`, `RUNNER_JOB_EXECUTION=NOT_VERIFIED`, `REBOOT_RECOVERY=NOT_VERIFIED`, `DELIVERY_GATE=NOT_VERIFIED`.

다음 단계는 [Runner Runbook의 첫 Job 절차](runner-bootstrap.md#4-첫-job과-이후-도구)다. 복구 문서를 포함한 PR과 수동 smoke Workflow를 검토·승인·main 병합한 뒤 첫 Job을 실행하고 Run URL, Commit, Attempt, 실제 사용자·UID, rootless 검사 결과를 제출한다.

## 확인 결과

2026-09-09 17:32:40 KST에 수집된 `P1_CICD_Runner_EXEC.oJUsWqwU.txt`와 제출 sidecar의 SHA-256 검증은 PASS다. 파일 SHA-256은 `3eb522095ca9ceccb9422a62bfda97eda67f38a71977194ba79fb613269c9ebf`다. 이는 첨부 바이트의 일치를 확인한 것이며 원격 서버를 직접 조사한 결과는 아니다.

| 항목 | 제출 증적 | 판정 |
|---|---|---|
| 서비스 | actions.runner.321Team-infra-cicd.cicd-runner01.service | 설치·enable 로그 수신, 실행 failed/203 |
| 실행 사용자 | server01_cicd-runner | 일반 사용자 서비스 설정 |
| 실행 파일 | /home/server01_cicd-runner/actions-runner/runsvc.sh | 해당 사용자 소유, 0755 |
| 상위 경로 | 홈과 actions-runner 모두 해당 사용자 소유, 0700 | 실행 사용자에게 경로 접근 권한 있음 |
| 마운트 | /home XFS, noexec 없음 | 수집 시점의 마운트 정보 |
| systemd 격리 설정 | ProtectHome=no, NoExecPaths/ExecPaths/InaccessiblePaths/RootDirectory 비어 있음 | 조회한 설정에서 차단 근거 없음 |
| SELinux | Enforcing, runsvc.sh=user_home_t | 서비스 진입점의 실행 거부 확인 |
| 감사 로그 | PID 12646, 17:29:47.305 KST, init_t → user_home_t, file { execute }, permissive=0 | 제출된 203/EXEC 실패와 동일 PID·시각 |
| 감사·실행 제어 | auditd active, fapolicyd not-found | 수집 시점 |

**확인된 직접 원인은 SELinux의 실행 거부다.** 동일 감사 이벤트의 `faccessat2`는 EACCES를 반환했다. 기존 `Relabeled ... systemd_unit_file_t` 메시지는 유닛 파일 라벨 복구만 의미한다. [Red Hat의 동일 증상 설명](https://access.redhat.com/solutions/6302881)

앞선 화면에서 공식 Linux x64 Runner 2.337.0의 체크섬 성공, 버전 실행, Connected to GitHub가 확인됐다. 해당 릴리스 SHA-256은 `70920811a4f8ad4328818682bca5c6469c1c942fab52448868071d0063816613`으로 공식 릴리스·API asset digest·사용자 Download 화면과 대조했다. 패키지 자체를 이 환경에서 다운로드하여 검증한 것은 아니다. [공식 릴리스](https://github.com/actions/runner/releases/tag/v2.337.0)

## 수정 범위와 판단 한계

기존 홈 설치와 등록을 유지하며 **서비스 진입점 runsvc.sh 한 일반 파일**에 `bin_t` 영구 파일 라벨 규칙을 추가하고 해당 파일에만 적용한다. 규칙의 점은 이스케이프하며 디렉터리 전체 또는 하위 파일을 대상으로 삼지 않는다. 현재 프로세스가 failed/inactive인 경우에만 시작한다.

`bin_t`는 일반 실행 파일 타입이며 Runner 전용 격리 정책은 아니다. 시스템의 SELinux는 Enforcing을 유지하나 실행 프로세스는 배포 정책에 따라 `unconfined_service_t` 등 일반 서비스 도메인으로 동작할 수 있다. 아래에서 실제 프로세스 도메인과 실행 사용자를 수집한다. 일반 사용자·승인 main·신뢰된 수동 Workflow 범위를 계속 적용한다. [Red Hat의 bin_t·서비스 도메인 설명](https://docs.redhat.com/en/documentation/red_hat_enterprise_linux/7/html/selinux_users_and_administrators_guide/sect-security-enhanced_linux-targeted_policy-unconfined_processes)

영구 라벨 규칙 등록 후 `restorecon`으로 적용하는 방식은 RHEL 10 SELinux 문서를 따른다. 소유자·파일 모드, SELinux Enforcing 설정, 등록 자격증명, 서비스 ExecStart를 변경하는 명령은 포함하지 않는다. [영구 파일 라벨과 문제 해결](https://docs.redhat.com/en/documentation/red_hat_enterprise_linux/10/html/using_selinux/troubleshooting-problems-related-to-selinux)

## 적용 및 재검증

`cicd-runner01`의 `server01_cicd-runner` 계정에서 실행한다. 기존 등록이 있으므로 신규 다운로드·압축 해제·config.sh 등록·svc.sh install은 반복하지 않는다. 이번 복구는 기존 파일의 메타데이터 수정이므로 추가 ZIP이 필요하지 않다. `semanage` 패키지가 없으면 기존 OS 저장소에서 정상 dnf 설치 확인을 거친다.

```bash
(
  set -euo pipefail
  umask 077
  runner_phase="대상 확인"
  trap 'runner_rc=$?; printf "FAILED_PHASE=%s RC=%s\n" "$runner_phase" "$runner_rc" >&2' ERR
  test "$(hostname -s)" = cicd-runner01
  test "$(id -un)" = server01_cicd-runner
  test "$HOME" = /home/server01_cicd-runner
  test "$(getenforce)" = Enforcing
  runner_unit="actions.runner.321Team-infra-cicd.cicd-runner01.service"
  runner_exec="$HOME/actions-runner/runsvc.sh"
  runner_rule='/home/server01_cicd-runner/actions-runner/runsvc\.sh'
  test "$(readlink -f "$runner_exec")" = "$runner_exec"
  test -f "$runner_exec"
  test -x "$runner_exec"
  test "$(stat -c %u "$runner_exec")" = "$(id -u)"
  test "$(stat -c %h "$runner_exec")" = 1
  test "$(stat -c %C "$runner_exec" | cut -d: -f3)" = user_home_t
  test "$(systemctl show "$runner_unit" -p User --value)" = server01_cicd-runner
  case "$(systemctl show "$runner_unit" -p ActiveState --value)" in
    failed|inactive) ;;
    *) printf 'SERVICE_STATE_CHANGED: 현재 상태 확인이 필요합니다.\n' >&2; exit 2 ;;
  esac

  runner_phase="관리 도구 확인"
  if ! rpm -q policycoreutils-python-utils >/dev/null 2>&1; then
    sudo dnf install policycoreutils-python-utils
  fi
  runner_work="$(mktemp -d "$HOME/P1_CICD_Runner_SELinux.XXXXXXXX")"
  printf 'EVIDENCE_DIR=%s\n' "$runner_work"
  stat -c '%C %a %u:%g %n' "$runner_exec" > "$runner_work/before.txt"
  sudo semanage fcontext -l -C > "$runner_work/fcontext-before.txt"

  runner_phase="기존 규칙 확인"
  python3 - "$runner_work/fcontext-before.txt" "$runner_rule" "$runner_exec" <<'PY'
import pathlib, sys
selectors = {sys.argv[2], sys.argv[3]}
for line in pathlib.Path(sys.argv[1]).read_text().splitlines():
    if line.split() and line.split()[0] in selectors:
        raise SystemExit("EXISTING_LOCAL_RULE: 기존 단일 경로 규칙 확인이 필요합니다.")
PY

  runner_phase="영구 라벨 적용"
  sudo semanage fcontext -a -f f -t bin_t "$runner_rule"
  printf '%s\n' "$runner_rule" > "$runner_work/added-rule.txt"
  sudo restorecon -Fv "$runner_exec"
  matchpathcon -V "$runner_exec"
  test "$(stat -c %C "$runner_exec" | cut -d: -f3)" = bin_t

  runner_phase="서비스 시작"
  sudo systemctl reset-failed "$runner_unit"
  sudo systemctl start "$runner_unit"
  sleep 10

  runner_phase="결과 수집"
  {
    date --iso-8601=seconds
    getenforce
    ls -lZ "$runner_exec"
    systemctl show "$runner_unit" -p User -p ActiveState -p SubState -p Result -p MainPID -p NRestarts -p UnitFileState
    sudo journalctl -b -u "$runner_unit" -n 50 --no-pager --all -o short-iso
    runner_pid="$(systemctl show "$runner_unit" -p MainPID --value)"
    if [[ "$runner_pid" =~ ^[1-9][0-9]*$ ]]; then
      ps -p "$runner_pid" -o user=,pid=,label=,comm= || printf 'PROCESS_QUERY=UNAVAILABLE\n'
    fi
  } > "$runner_work/result.txt" 2>&1
  cat "$runner_work/result.txt"
  cd "$runner_work"
  sha256sum before.txt fcontext-before.txt added-rule.txt result.txt > SHA256SUMS
  tar -czf P1-CICD-01_Runner_SELinux_Result.tar.gz before.txt fcontext-before.txt added-rule.txt result.txt SHA256SUMS
  sha256sum P1-CICD-01_Runner_SELinux_Result.tar.gz > P1-CICD-01_Runner_SELinux_Result.tar.gz.sha256
  printf 'ARCHIVE=%s/P1-CICD-01_Runner_SELinux_Result.tar.gz\nSIDECAR=%s/P1-CICD-01_Runner_SELinux_Result.tar.gz.sha256\n' "$runner_work" "$runner_work"
  runner_phase="サービス 상태 판정"
  systemctl is-active --quiet "$runner_unit"
)
```

`ARCHIVE`와 `SIDECAR`, GitHub Runners 화면의 상태를 제출한다. 보고서의 기존 17:29:47 오류는 이력이며 재시작 이후 시각·PID와 구분한다. 정상 후보는 Enforcing, bin_t, 지정 실행 사용자, enabled, active/running, 새 로그의 Listening for Jobs, GitHub Online/Idle다. 10초 관측은 장기 안정성·재부팅 후 복구·첫 Job 성공을 입증하지 않는다.

서비스 시작 명령 자체가 실패하거나 규칙 검사에서 멈추면 후속 명령은 실행하지 않는다. 표시된 FAILED_PHASE와 오류, 생성됐다면 EVIDENCE_DIR의 파일을 제출한다. 재실행 때 라벨이 이미 bin_t면 첫 상태 검사에서 멈추도록 했으므로 새 규칙을 반복 등록하지 않는다. 별도 실행 차단이 남으면 새 PID의 AVC를 확인하며 변경 범위를 자동 확대하지 않는다.

## 원복

아래는 **이번 실행이 성공적으로 새 규칙을 추가했으며 해당 EVIDENCE_DIR의 added-rule.txt가 존재하는 경우**에만 사용하는 수동 원복이다. 기존 작업 수행 중인 Runner를 중단할 수 있으므로 원복 시 진행 중 Job이 없는지 확인한다. 이번 복구 대상 서비스는 원래 failed였으므로 원복 후에는 stopped 상태로 둔다. 원래 단일 파일의 타입 user_home_t를 복원한다. 새로 설치한 관리 패키지는 공유 의존성을 고려해 자동 제거하지 않는다.

```bash
(
  set -euo pipefail
  test "$(hostname -s)" = cicd-runner01
  test "$(id -un)" = server01_cicd-runner
  runner_exec='/home/server01_cicd-runner/actions-runner/runsvc.sh'
  runner_rule='/home/server01_cicd-runner/actions-runner/runsvc\.sh'
  test "$(readlink -f "$runner_exec")" = "$runner_exec"
  test -f "$runner_exec"
  test "$(stat -c %h "$runner_exec")" = 1
  sudo systemctl stop actions.runner.321Team-infra-cicd.cicd-runner01.service
  sudo semanage fcontext -d -f f "$runner_rule"
  sudo restorecon -Fv "$runner_exec"
  ls -lZ "$runner_exec"
  test "$(stat -c %C "$runner_exec" | cut -d: -f3)" = user_home_t
)
```

원복 후 전체 SELinux user/role 값은 현재 기본 규칙을 따른다. before.txt의 원래 전체 context와도 비교한다. 실행 파일 내용·등록 정보를 삭제하거나 서비스 등록을 해제하지 않는다.

## 유지보수 및 검증 범위

Runner 자동 업데이트·svc.sh 재설치가 진입점을 재생성하면 영구 규칙이 있어도 새 파일의 라벨이 자동 적용된다고 보장할 수 없다. 해당 작업 후 `matchpathcon -V`와 서비스 상태를 확인하고, 필요 시 위에서 정한 **동일 파일**에 `restorecon -Fv`를 적용한다. 승인된 배포 소스와 실행 파일의 일치도 다시 확인한다.

v1.0 배포 시 이 문서의 Bash 블록은 `bash -n`, 내장 Python은 구문 검사를 수행했다. 이 작업 환경에서 원격 정책 변경·서비스 복구를 실행한 것은 아니며, 이후 사용자가 실행한 복구 결과를 v1.1 상단에 검증·기록했다. v1.1은 판정 문구 갱신으로 적용·원복 코드 블록은 v1.0과 동일하다. 첫 Job·재부팅 후 복구·종합 Delivery gate는 계속 미검증이다.
