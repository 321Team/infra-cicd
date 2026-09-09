# Runner 등록·서비스 Runbook v1.0

대상: `cicd-runner01 / 10.250.10.20 / server01_cicd-runner`. 선택값: infra-cicd 저장소 전용·중앙 실행·Podman rootless. 서버에서 실행하는 절차이며 저장소 등록만으로 설치 완료를 뜻하지 않는다.

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

`/home/server01_cicd-runner/actions-runner`가 이미 존재하면 덮어쓰지 말고 기존 설치인지 확인한다. 이번 수집은 두 후보 경로 미발견이었으므로 신규 설치를 기본으로 한다.

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

아래는 등록 토큰을 명령행에 넣지 않고 공식 대화형 입력으로 받는 등록 명령이다. GitHub 화면의 토큰은 해당 서버의 토큰 프롬프트에만 입력한다. 서비스 이름과 기본 작업 폴더는 이 설정값을 사용한다.

```bash
(
  set -euo pipefail
  umask 077
  test "$(hostname -s)" = cicd-runner01
  test "$(id -un)" = server01_cicd-runner
  cd "$HOME/actions-runner"
  test ! -e .runner
  ./config.sh --url https://github.com/321Team/infra-cicd \
    --name cicd-runner01 --labels p1-cicd,onprem,podman --work _work
  sudo ./svc.sh install server01_cicd-runner
  sudo ./svc.sh start
  sudo ./svc.sh status
)
```

의존성 오류가 발생하면 표시된 실제 누락 패키지를 OS 서명 저장소로 보완한다. config 실패 뒤에 서비스 설치를 계속하지 않는다. 계정·Runner를 root로 바꾸지 않으며 Runner 자동 갱신은 기본 설정을 유지한다. 등록 토큰은 단기 값이므로 만료되면 같은 GitHub 화면에서 새 값으로 갱신한다. [GitHub 등록](https://docs.github.com/en/actions/how-tos/manage-runners/self-hosted-runners/add-runners), [서비스 운영](https://docs.github.com/en/actions/how-tos/manage-runners/self-hosted-runners/configure-the-application)

## 4. 첫 Job과 이후 도구

`templates/runner-smoke.yml`을 기술 검토 후 `.github/workflows/runner-smoke.yml`로 반영·병합하고 main에서 수동 실행한다. 현재 키트는 템플릿만 제공하므로 자동 실행되지 않는다. 첫 Job은 비밀값 없이 host/account·도구·rootless를 확인한다. 재시작 뒤 Online과 Job 수신도 확인한다. rootless `podman info` 성공은 이미지 빌드·Push 성공과 별개다.

GitHub 통신은 기존 두 TLS 검사 외에 실제 등록·Job·Action 다운로드·로그/Artifact·업데이트 도메인을 기능별로 확인한다. 필요한 outbound 경로는 [공식 요구사항](https://docs.github.com/en/actions/reference/runners/self-hosted-runners#communication)을 따르며 임의 inbound 규칙이나 DNS/SNAT 변경을 추가하지 않는다.

Trivy는 공식 배포물의 고정 버전·체크섬으로 설치하고 DB 공급·갱신 기록을 남긴다. Podman 이미지의 로컬 archive를 스캔하는 흐름을 사용하며 CRITICAL 또는 검사 오류면 Push/Deploy를 중단한다. kubectl은 실제 API 서버 버전을 인계받아 호환 버전을 고정한다. 두 항목은 Runner 등록의 선행 필수값이 아니므로 준비를 병행한다. 토큰·Robot 인증정보는 Job별 임시 파일/표준입력으로 주입하고 작업 종료 시 정리한다.

공유할 결과: Runner 버전, Online 상태, 서비스의 실행 사용자, smoke Run URL/Commit/Attempt/결과, rootless 결과. 등록 토큰·`.credentials*`·전체 환경변수·개인키는 공유하지 않는다. DELIVERY_GATE는 종합 인수 전까지 NOT_VERIFIED다.
