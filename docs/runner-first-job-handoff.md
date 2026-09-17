# P1-CICD-01 첫 Runner Job 인계 v1.0

**2026-09-17 상태 대조:** 정본은 docs-v3.1.18, v3.1.19는 수집 후보다. Runner의 Trivy DB 사전검증과 공급·base probe는 실행 증적을 확보했으며 Runtime AC는 1/10이다. 최종 앱 Source/시험 범위 인계와 최신 독립 리뷰가 남아 앱 Test→Build→Scan은 미실행, 전체 Delivery는 NOT_VERIFIED다. [후속 상태와 근거](implementation-state-20260915.md#2026-09-17-후속-상태)를 따른다.

**2026-09-15 후속 상태 이력:** PR #2 최신 HEAD는 이하영 APPROVED 후 main `ffaa764623c5941b14fe12384e9ba5629bd60a6f`에 병합됐다. Runner 로그인 사전점검·GitHub Idle·필요 라벨 6개를 확인했다. 09-15 당시 다음 작업은 §2의 main 수동 smoke 실행과 실제 Run 수신이었다. 아래 09-09 표·미수신/리뷰 대기는 당시 기록이며 [09-15 실제 구성](implementation-state-20260915.md)과 구분한다. 같은 로그인 점검을 다시 수행할 필요는 없고, 재시작 후 Job 수신은 별도 미검증이다.

최초 실행 절차 이력(현재 재실행 지시 아님): [P1 Runner smoke](https://github.com/321Team/infra-cicd/actions/workflows/runner-smoke.yml) → Run workflow → main → 한 번 실행. Run URL·Workflow Commit·Attempt와 `JOB_RECEPTION=VERIFIED`, `BUILDER_ROOTLESS=VERIFIED`를 확인한다. 대기/실패 시 해당 Job 결과로 후속 조치하며 임시 PR 트리거로 우회하지 않는다.

작성일: 2026-09-09. 아래 본문은 당시 기록이다. **사전 소스 점검 완료 / 독립 기술 리뷰 및 첫 Job 실행 대기**.

PR #1은 main에 병합됐다. 다음 대상은 [PR #2](https://github.com/321Team/infra-cicd/pull/2)의 독립 기술 검토와 승인 후 첫 수동 Job이다. 이 문서의 사전 점검은 작성 측 검증이며 이하영님의 독립 승인을 대체하지 않는다.

## 09-09에 확인한 소스와 단계

| 항목 | 확인 결과 |
|---|---|
| main | PR #1 병합 Commit `be92fd00b269ff7d7abc124bb17f6d3e4c007851` |
| 점검한 PR #2 구현 | Commit `009dd4530e10aa1427471df1bad88e3c77e75aa7` |
| 결합 확인 | main 12개 + PR #2 추가 18개 파일의 Git blob 일치, 추가 경로 중복 0개 |
| 결합 소스 점검 | UTF-8/LF, JSON/Python 구문, 상대 문서 링크 PASS |
| 수동 Workflow | infra-cicd/main 조건, 지정 6개 라벨, permissions={}, 5분 제한, 템플릿 동일성 및 Bash 구문 PASS |
| 기존 단위시험 | 수집기 15건·CSR 8건의 기존 검증 기록 유지. 소스가 같으므로 이번에 반복 실행하지 않음 |
| Runner 제출 증적 | 17:48:25 KST 기준 서비스 active/running, SELinux Enforcing, GitHub 연결·Listening for Jobs. [당시 검토](https://github.com/321Team/infra-cicd/blob/009dd4530e10aa1427471df1bad88e3c77e75aa7/docs/runner-selinux-recovery.md) |
| 현재 서버의 Git/rootless | 새 결과 미수신. 아래 로그인 세션 점검과 실제 Job에서 구분해 확인 |
| 독립 리뷰·첫 Job | 작성 시점 PR #2 리뷰·댓글 없음. Job 실행 미완료 |
| Harbor | 기존 CSR 검토 PASS, CA 자산 확인·Leaf 발급 및 정식 적용 대기 |

이 인계 문서만 후속 커밋으로 추가한다. 실행 Workflow·CSR·수집기·프로파일은 위 구현 Commit과 동일하다. 결합 확인은 소스의 구성·구문 검증이며 원격 VM이나 실제 GitHub Job의 실행 시험이 아니다.

## 1. 지금 실행 가능한 로그인 세션 점검

실행 위치는 `cicd-runner01`의 `server01_cicd-runner` 계정 홈이다. Git 설치 여부와 Podman rootless 기본 동작을 먼저 확인한다.

```bash
(
  set -euo pipefail
  prep_phase="대상 확인"
  trap 'prep_rc=$?; printf "FAILED_PHASE=%s RC=%s\n" "$prep_phase" "$prep_rc" >&2' ERR
  test "$(hostname -s)" = cicd-runner01
  test "$(id -un)" = server01_cicd-runner
  test "$(id -u)" -ne 0
  cd "$HOME"

  prep_phase="Runner 서비스 상태"
  prep_unit="actions.runner.321Team-infra-cicd.cicd-runner01.service"
  test "$(systemctl show "$prep_unit" -p User --value)" = server01_cicd-runner
  systemctl is-active --quiet "$prep_unit"

  prep_phase="Git"
  git --version
  prep_phase="Podman"
  podman --version
  prep_phase="로그인 세션 rootless"
  test "$(podman info --format '{{.Host.Security.Rootless}}')" = true

  date --iso-8601=seconds
  printf 'LOGIN_PRECHECK=PASS\nRUNNER_JOB=NOT_EXECUTED\nDELIVERY_GATE=NOT_VERIFIED\n'
)
```

서비스 상태 조회는 서비스를 재시작하지 않는다. `podman info`는 사용자 로컬 저장소 초기화가 발생할 수 있다. 이 결과는 로그인 세션에서의 기본 동작 확인이며 systemd 서비스 환경에서 실행된 실제 Job 성공을 뜻하지 않는다.

| 실패 단계 | 후속 처리 |
|---|---|
| 대상 확인 | 올바른 호스트·계정에서 실행 |
| Runner 서비스 상태 | 현재 서비스 오류 확인. 기존 등록 파일 삭제·강제 재등록을 하지 않음 |
| Git | [등록 Runbook 2절](runner-bootstrap.md)의 기존 서명 OS 저장소 설치 절차 사용 |
| Podman / 로그인 세션 rootless | 해당 오류와 subordinate UID/GID·사용자 로컬 저장소 확인. sudo Podman으로 성공 처리하지 않음 |

공유할 것은 위 명령의 버전·마지막 상태 또는 실패 단계·오류다. 등록 Token, 인증 파일, 전체 환경변수는 포함하지 않는다.

## 2. 독립 리뷰와 첫 Job 실행

[main의 운영 기준](https://github.com/321Team/infra-cicd/blob/be92fd00b269ff7d7abc124bb17f6d3e4c007851/docs/governance.md)에 따라 이하영의 독립 기술 리뷰 후 이권욱이 해당 Commit의 최종 병합을 판단한다. PR #1의 승인은 PR #2의 승인으로 확대하지 않는다.

1. 이하영은 PR #2의 현재 HEAD와 검토 결과를 연결한다. 새 문서의 로그인 점검 및 실행 판정 범위도 확인한다.
2. 이권욱은 리뷰·검증·잔여위험을 확인해 PR #2 병합을 결정한다.
3. 첫 Job 실행 시 Settings → Actions → Runners에서 `cicd-runner01`의 Online/Idle 상태와 `self-hosted, Linux, X64, p1-cicd, onprem, podman` 라벨을 확인한다.
4. Actions → **P1 Runner smoke** → **Run workflow** → **main**으로 한 번 실행한다.
5. Run URL과 로그의 Workflow Commit, Run Attempt 및 결과를 제출한다.

수동 실행 Workflow는 기본 브랜치에 있어야 한다. PR #2를 병합하기 전에 다른 브랜치나 임시 Workflow로 승인 절차를 우회해 실행하지 않는다. [GitHub 수동 실행](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/manually-run-a-workflow)

## 3. 첫 Job 판정과 제출

성공 기준은 Job conclusion=success와 다음 결과의 동시 확인이다.

- 실제 호스트 `cicd-runner01`, 사용자 `server01_cicd-runner`, 숫자 UID가 0이 아님.
- Git/Podman 버전 조회 및 rootless 검사 성공.
- `JOB_RECEPTION=VERIFIED`, `BUILDER_ROOTLESS=VERIFIED`.
- Run URL·Workflow Commit·Attempt가 해당 승인 실행과 일치.

이 Job은 실제 서비스 환경에서 작업 수신과 기본 도구 실행을 검증한다. 이미지 Build, Trivy Scan, Harbor 인증 Push/Pull, Kubernetes 배포 및 재부팅 후 복구는 각각 별도 증적이 필요하다. `DELIVERY_GATE=NOT_VERIFIED`를 유지한다.

대기 상태면 Runner Online/Idle·라벨과 Job 예약 상태를, 실행 실패면 해당 명령 오류를 확인한다. 로그인 세션 PASS만으로 서비스 환경의 실패를 무시하지 않는다.

## 4. 후속 연결

첫 Job이 성공하면 앱 Source SHA·Dockerfile·테스트 계약 인계, Trivy 버전·DB 공급, Harbor CA/Robot, Kubernetes 버전·단기 Token을 갖춰 Build→Scan→Push→승인 Digest 갱신을 구현한다. 실제 값은 담당 인계로 채우며 임의 이름·성공 명령을 넣지 않는다.

Harbor는 기존 키/CSR과 발급된 Leaf를 보존한다. CA 발급·암호화 백업/파일 복구·공개 인계는 완료했으며 Harbor 서비스 적용과 Consumer Trust가 남는다. 개인키 전달·CSR 재생성·재발급을 요구하지 않는다. 첫 smoke Job은 Harbor 서비스 전환 완료를 선행조건으로 요구하지 않는다.
