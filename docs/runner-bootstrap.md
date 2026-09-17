# Runner 등록·서비스 Runbook v1.0

대상: `cicd-runner01 / 10.250.10.20 / server01_cicd-runner`. 선택값: infra-cicd 저장소 전용·중앙 실행·Podman rootless. 서버에서 실행하는 절차이며 저장소 등록만으로 설치 완료를 뜻하지 않는다.

**2026-09-17 상태 대조:** 정본은 docs-v3.1.18, v3.1.19는 수집 후보다. Runner의 Trivy DB 사전검증과 공급·base probe는 실행 증적을 확보했으며 Runtime AC는 1/10이다. 최종 앱 Source/시험 범위 인계와 최신 독립 리뷰가 남아 앱 Test→Build→Scan은 미실행, 전체 Delivery는 NOT_VERIFIED다. [후속 상태와 근거](implementation-state-20260915.md#2026-09-17-후속-상태)를 따른다.

2026-09-09 17:48:25 KST 후속 증적: Runner 2.337.0의 서비스가 SELinux Enforcing 상태에서 active/running으로 복구됐고 GitHub 연결·Listening for Jobs가 관측됐다. [복구 증적 검토](runner-selinux-recovery.md)의 외부·내부 체크섬 검증은 PASS다. 09-09 당시 다음 작업은 4절의 첫 Job 검증이었다. 아래 신규 설치·등록 및 복구 명령은 현재 정상 Runner에서 반복하지 않는다. 이 문장의 미검증 표시는 09-09 기준이며 이후 결과는 위 후속 상태를 따른다.

## 1. 키트 검사와 압축 해제

ZIP과 sidecar를 로그인 계정 홈에 놓고 실행한다.

```bash
(
  set -euo pipefail
  umask 077
  test "$(hostname -s)" = cicd-runner01
  test "$(id -un)" = server01_cicd-runner
  cd "$HOME"
  sha256sum --check P1-CICD-01_Baseline_Application_Kit_v1.0.zip.sha256
  runner_work="$(mktemp -d "$HOME/P1_CICD_Runner_Bootstrap.XXXXXXXX")"
  python3 -m zipfile -e P1-CICD-01_Baseline_Application_Kit_v1.0.zip "$runner_work"
  cd "$runner_work/P1-CICD-01_Baseline_Application_Kit_v1.0"
  sha256sum --check SHA256SUMS
  printf 'KIT_DIR=%s\n' "$PWD"
)
```

## 2. Git 설치와 rootless 준비

이 단계는 Git 패키지를 설치한다. 활성화된 기존 서명 검증 OS 저장소를 사용하며 `--nogpgcheck`나 신규 외부 저장소를 추가하지 않는다. 설치 전 계획을 표시한다.

```bash
(
  set -euo pipefail
  test "$(hostname -s)" = cicd-runner01
  test "$(id -un)" = server01_cicd-runner
  sudo dnf install git --assumeno || dnf_plan_rc=$?
  case "${dnf_plan_rc:-0}" in 0|1) ;; *) exit "$dnf_plan_rc" ;; esac
  sudo dnf install git
  git --version
  podman --version
  awk -F: '$1=="server01_cicd-runner" {print}' /etc/subuid /etc/subgid
  test "$(podman info --format '{{.Host.Security.Rootless}}')" = true
)
```

실제 적용은 dnf가 표시하는 변경 목록을 확인하는 정상 패키지 관리 흐름으로 진행한다. UID/GID 범위가 없거나 rootless 조회가 실패하면 임의의 중복 범위를 추가하거나 `sudo podman`으로 바꾸지 않는다. 해당 OS의 기존 subordinate ID 할당과 로컬 저장소·pasta를 보완한 뒤 rootless로 재검증한다. [Podman rootless 조건](https://docs.podman.io/en/latest/markdown/podman.1.html#rootless-mode)

## 3. GitHub 패키지 설치·등록

[infra-cicd 설정](https://github.com/321Team/infra-cicd/settings/actions/runners/new)의 **Settings → Actions → Runners → New self-hosted runner → Linux / x64**에서 실제 패키지 버전과 SHA-256을 받는다. 등록 URL은 `https://github.com/321Team/infra-cicd`다.

`/home/server01_cicd-runner/actions-runner`가 이미 존재하면 기존 설치인지 확인한다. 최초 병행 수집에서는 두 후보 경로를 찾지 못했지만 이후 설치가 진행됐다. 다음 mkdir 블록은 신규 서버에만 적용한다.

```bash
(
  set -euo pipefail
  umask 077
  test "$(hostname -s)" = cicd-runner01
  test "$(id -un)" = server01_cicd-runner
  test ! -e "$HOME/actions-runner"
  test ! -L "$HOME/actions-runner"
  mkdir -m 700 "$HOME/actions-runner"
)
```

이어 `cd "$HOME/actions-runner"`에서 GitHub 화면 **Download**의 다운로드 → SHA-256 검사 → `tar xzf` 압축 해제 명령을 순서대로 실행한다. 버전과 해시는 같은 화면·같은 패키지의 것을 사용한다. 네트워크나 해시 오류가 나면 등록하지 않는다. 다운로드 URL·버전·SHA-256은 비밀이 아니므로 기록하되 등록 토큰은 기록하지 않는다.

아래는 등록 토큰을 명령행에 넣지 않고 공식 대화형 입력으로 받는 등록 명령이다. 줄 연결 문자가 복사 과정에서 빠지지 않도록 config.sh 호출은 한 줄로 제공한다. URL에는 꺾쇠괄호나 Markdown 링크 표기를 추가하지 않는다. GitHub 화면의 토큰은 해당 서버의 토큰 프롬프트에만 입력한다. 서비스 이름과 기본 작업 폴더는 이 설정값을 사용한다.

```bash
(
  set -euo pipefail
  umask 077
  runner_phase="대상 확인"
  trap 'runner_rc=$?; printf "FAILED_PHASE=%s RC=%s\n" "$runner_phase" "$runner_rc" >&2' ERR
  test "$(hostname -s)" = cicd-runner01
  test "$(id -un)" = server01_cicd-runner

  runner_phase="패키지 확인"
  cd "$HOME/actions-runner"
  test -x ./config.sh
  test -x ./bin/Runner.Listener
  if [ -e .runner ] || [ -L .runner ] || [ -e .service ] || [ -L .service ]; then
    printf 'EXISTING_RUNNER_STATE: 기존 등록 상태를 확인해야 합니다. 파일을 삭제하지 마세요.\n' >&2
    exit 2
  fi

  runner_phase="GitHub 등록"
  ./config.sh --url "https://github.com/321Team/infra-cicd" --name "cicd-runner01" --labels "p1-cicd,onprem,podman" --work "_work"

  runner_phase="등록 결과 파일 확인"
  test -f .runner
  test -x ./svc.sh
  runner_phase="서비스 설치"
  sudo ./svc.sh install server01_cicd-runner
  runner_phase="서비스 시작"
  sudo ./svc.sh start
  runner_phase="서비스 상태 확인"
  sudo ./svc.sh status
)
```

의존성 오류가 발생하면 표시된 실제 누락 패키지를 OS 서명 저장소로 보완한다. config 실패 뒤에 서비스 설치를 계속하지 않는다. 계정·Runner를 root로 바꾸지 않으며 Runner 자동 갱신은 기본 설정을 유지한다. 등록 토큰은 단기 값이므로 만료되면 같은 GitHub 화면에서 새 값으로 갱신한다. [GitHub 등록](https://docs.github.com/en/actions/how-tos/manage-runners/self-hosted-runners/add-runners), [서비스 운영](https://docs.github.com/en/actions/how-tos/manage-runners/self-hosted-runners/configure-the-application)

## 등록 명령 오류 처리 — 2026-09-09

사용자 제출 명령에서 URL 줄 끝의 `\`가 빠져 `--name`이 별도 명령으로 분리된 것을 확인했다. 성공하는 모의 config.sh로 이 구문을 재현하면 `--name: command not found`가 발생한다. 제출된 `bash: https://github.com/321Team/infra-cicd: 그런 파일이나 디렉터리가 없습니다`는 그 오류와 달라, 누락된 줄 연결만을 전체 원인으로 단정하지 않는다. URL이 별도 명령이나 리다이렉션 파일로 해석됐을 가능성은 있으나 실제 입력 문자를 확인하지 않았으므로 원인은 미확정이다. 이 로그만으로 패키지 다운로드·압축 해제·GitHub 인증 실패를 판정하지 않는다.

위 수정 블록은 기존 파일을 삭제하거나 등록을 강제 교체하지 않는다. config.sh와 Runner.Listener의 실행 가능 여부를 확인하고 기존 등록·서비스 표시 파일이 있으면 코드 2로 멈춘다. 등록에 성공한 뒤 생성된 svc.sh로 서비스 설치·시작을 진행한다. 서비스 파일을 등록 전에 필수 검사하지 않는다.

| 출력 | 다음 처리 |
|---|---|
| FAILED_PHASE=패키지 확인 | actions-runner 경로와 공식 패키지 다운로드·SHA256·압축 해제 완료 여부 확인 |
| EXISTING_RUNNER_STATE | 기존 등록 또는 부분 진행 상태 확인. .runner/.service를 삭제하거나 --replace로 덮어쓰지 않음 |
| FAILED_PHASE=GitHub 등록 | 토큰을 제외한 오류만 검토. config 실행 시 의존성·네트워크·인증 오류를 구분 |
| FAILED_PHASE=서비스 설치/시작/상태 확인 | 등록을 반복하지 말고 해당 서비스 단계의 오류 확인 |

재실행 시 GitHub 저장소의 Settings → Actions → Runners → New self-hosted runner 화면에서 유효한 등록 토큰을 받아 config.sh의 토큰 프롬프트에만 입력한다. 명령행·대화·첨부에는 토큰을 넣지 않는다. 공식 등록 토큰은 1시간 후 만료한다. [GitHub 등록 문서](https://docs.github.com/en/actions/how-tos/manage-runners/self-hosted-runners/add-runners)

등록 명령 수정 시 Bash 블록은 bash -n 구문 검사를 통과했다. 이후 서비스 복구 증적은 문서 상단과 연결된 검토 문서에 반영했다. 위 패키지 확인이 실패한 경우에만 실제 누락 상태에 맞춰 보완한다.

## 4. 첫 Job과 이후 도구

`.github/workflows/runner-smoke.yml`을 PR에 실행용 소스로 준비했다. 09-09 작성 당시 main에는 Workflow가 없고 PR은 미병합이었다. PR #2는 이후 병합됐으며 아래는 최초 Job 실행 절차 이력이다. GitHub Actions의 수동 실행은 Workflow가 기본 브랜치에 있어야 하므로 다음 순서로 진행한다. [GitHub 수동 Workflow 실행](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/manually-run-a-workflow)

1. 최초 실행 전 PR #1·#2의 독립 기술 리뷰와 최종 승인·main 병합이 필요했다. PR #2의 이후 승인/병합 기록은 위 상태 문서에 연결한다. 새 변경은 별도 리뷰 대상이다.
2. 저장소 Settings → Actions → Runners에서 cicd-runner01의 현재 Online/Idle 상태와 지정 라벨을 확인한다.
3. Actions → **P1 Runner smoke** → Run workflow → **main**을 선택해 한 번 수동 실행한다.
4. 성공한 Run URL·Commit·Attempt 및 Job 로그를 제출한다. Job이 대기하면 Runner 상태/라벨을 확인하고, 실패하면 해당 단계 오류를 검토한다.

Workflow는 수동 workflow_dispatch만 사용하고 infra-cicd/main에서만 Job을 실행한다. 권한은 빈 설정이며 checkout·외부 Action·Secret·Harbor·Kubernetes 작업은 없다. 호스트·전체 사용자명·숫자 UID를 확인하고 Git·Podman 버전 및 rootless podman info를 검사한다. 마지막 검사가 통과해야 JOB_RECEPTION=VERIFIED와 BUILDER_ROOTLESS=VERIFIED를 출력한다. 기존 배포용 templates/runner-smoke.yml은 실행용 파일과 동일하게 유지한다.

첫 Job은 Runner 작업 수신과 해당 도구의 기본 실행을 검증한다. 이미지 빌드·스캔·Push/Pull·배포, 외부 Action 다운로드, Artifact 업로드까지 검증한 결과는 아니다. Job 종료 후 별도 계획한 재부팅 시점에 Online 복귀와 추가 smoke Job 성공을 확인한다. 이 문서 갱신 자체로 재부팅이나 Workflow 실행을 수행하지 않는다.

GitHub 통신은 기존 두 TLS 검사 외에 실제 등록·Job·Action 다운로드·로그/Artifact·업데이트 도메인을 기능별로 확인한다. 필요한 outbound 경로는 [공식 요구사항](https://docs.github.com/en/actions/reference/runners/self-hosted-runners#communication)을 따르며 임의 inbound 규칙이나 DNS/SNAT 변경을 추가하지 않는다.

Trivy는 공식 배포물의 고정 버전·체크섬으로 설치하고 DB 공급·갱신 기록을 남긴다. Podman 이미지의 로컬 archive를 스캔하는 흐름을 사용하며 09-14 #13/#16 사용자 결정에 따라 미해결 CRITICAL/HIGH 또는 검사·DB 오류면 Push/Deploy를 중단한다. 예외는 CVE·Digest·사유·만료·승인자를 기록하며 실제 집행은 후속 Pipeline 검증 대상이다. kubectl은 실제 API 서버 버전을 인계받아 호환 버전을 고정한다. 두 항목은 Runner 등록의 선행 필수값이 아니므로 준비를 병행한다. 토큰·Robot 인증정보는 Job별 임시 파일/표준입력으로 주입하고 작업 종료 시 정리한다.

공유할 결과: Runner 버전, Online 상태, 서비스의 실행 사용자, smoke Run URL/Commit/Attempt/결과, rootless 결과. 등록 토큰·`.credentials*`·전체 환경변수·개인키는 공유하지 않는다. DELIVERY_GATE는 종합 인수 전까지 NOT_VERIFIED다.
