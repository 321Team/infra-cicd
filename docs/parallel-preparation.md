# P1-CICD-01 TLS·Runner 병행 준비 v1.0

작성일: 2026-09-09. 상태: 준비 도구 구현 / 서버 실행 결과 수신 전. DELIVERY_GATE: NOT_VERIFIED.

## 이번 작업과 완료 범위

| 구분 | 수행 위치·담당 | 이번 산출물 | 다음 적용 조건 |
|---|---|---|---|
| TLS 준비 | harbor01 / 이하영 | 실제 컨테이너의 설치 경로 라벨·마운트, 후보 harbor.yml/NGINX의 인증서 경로 | 정식 CA 존재·기존 배포 범위, 설치 정본·교체 경로·복구 계획 확인 |
| Runner 준비 | cicd-runner01 10.250.10.20 / 이권욱 | 설치 후보·서비스·도구 버전, 두 GitHub 주소의 TLS 연결 결과 | 호출 저장소와 Runner 등록 범위, 빌더·앱 계약·자격증명 처리 방식 확인 |

승인된 역할은 이권욱 CA 관리·발급·Runner 검증, 이하영 Harbor 개인키·CSR·적용·복구다. 이 작업을 위해 역할 승인을 반복하지 않는다. 개인키 생성·인증서 적용·Runner 설치는 해당 경로와 자산 현황을 확인한 뒤 구체화한다. 현재 서버에 직접 접속해 적용한 상태는 아니다.

2026-09-09 제출 증적에서 Harbor 9개 컨테이너 복구와 Runner TCP 연결을 확인했다. 같은 날 12:45 KST 수집의 TLS 오류 18은 자체 서명 CA:TRUE 인증서와 관련되며, 승인 CA로 서명한 별도 Leaf 및 Consumer Trust가 아직 검증되지 않았다. 기존 공개 인증서 수집만 반복하지 않고 설치 정본·발급 선행조건을 확인한다.

## 실행 방법

배포 파일 `P1-CICD-01_Parallel_Preparation_Kit_v1.0.zip`과 `.zip.sha256`을 각각 두 서버의 로그인 계정 홈에 복사한다. 다음 블록은 두 서버에서 동일하게 실행할 수 있다. 호스트와 계정을 확인한 뒤 압축을 풀고 서버 역할에 맞는 수집기를 실행한다.

```bash
(
  set -euo pipefail
  umask 077
  case "$(hostname -s)" in
    cicd-runner01) test "$(id -un)" = "server01_cicd-runner"; prep_role=runner ;;
    harbor01) test "$(id -un)" = "server08"; prep_role=harbor ;;
    *) printf 'ERROR: 대상 서버가 아닙니다.\n' >&2; exit 3 ;;
  esac
  cd "$HOME"
  sha256sum --check P1-CICD-01_Parallel_Preparation_Kit_v1.0.zip.sha256
  prep_work="$(mktemp -d "$HOME/P1_CICD_Parallel_Prep.XXXXXXXX")"
  python3 -m zipfile -e P1-CICD-01_Parallel_Preparation_Kit_v1.0.zip "$prep_work"
  cd "$prep_work/P1-CICD-01_Parallel_Preparation_Kit_v1.0"
  sha256sum --check SHA256SUMS
  mkdir "$prep_work/results"
  check_rc=0
  if [ "$prep_role" = runner ]; then
    python3 tools/parallel-prep/collect_parallel_prep.py \
      --role runner --github-network \
      --output-dir "$prep_work/results" || check_rc=$?
  else
    sudo python3 tools/parallel-prep/collect_parallel_prep.py \
      --role harbor --output-dir "$prep_work/results" || check_rc=$?
  fi
  case "$check_rc" in
    0|2) printf 'CHECK_RC=%s\n' "$check_rc" ;;
    *) exit "$check_rc" ;;
  esac
)
```

이번 버전은 점검이 완료되어도 수동 인계가 남으므로 `CHECK_RC=2`, `PREPARATION_STATUS=REVIEW_REQUIRED`를 출력한다. 이것은 설치 성공/실패 판정이 아니다. `COLLECTION_COMPLETE=true`는 수집기 종료를 뜻하며 각 항목의 `UNAVAILABLE` 등은 별도로 검토한다. 호스트/IP/계정 불일치는 코드 3으로 중단하며 결과 파일을 만들지 않는다. 예상 밖 예외는 0/2로 흡수하지 않는다.

결과는 `ARCHIVE=`와 `SIDECAR=`에 표시된다. Harbor 결과도 sudo 실행 사용자에게 소유권을 돌려주므로 해당 로그인 계정으로 가져올 수 있다.

| 서버 | 제출 파일 |
|---|---|
| cicd-runner01 | `P1-CICD-01_Parallel_runner_Result.tar.gz`와 `.tar.gz.sha256` |
| harbor01 | `P1-CICD-01_Parallel_harbor_Result.tar.gz`와 `.tar.gz.sha256` |

Archive에는 `preparation.json`과 그 SHA256SUMS만 포함한다. 기존 파일에 덮어쓰지 않고 매번 새 비공개 결과 디렉터리를 만든다. 환경변수, 프로세스 argv, Docker 전체 inspect, 인증정보, 로그, 인증서·개인키 파일 원문은 수집하지 않는다. 설정 파일은 지정한 호스트명/포트/경로만 추려 저장한다. 업로드 전 특이한 경로명 등에 비밀값이 없는지 소유자가 확인한다.

Runner 경로가 기본 후보인 계정 홈의 `actions-runner` 또는 `/opt/actions-runner`와 다르면 `--runner-dir /실제/설치경로`를 추가한다. Harbor 설정이 알려진 두 후보 경로와 컨테이너 라벨 경로 외에 있으면 `--harbor-config /실제/설치경로/harbor.yml`을 추가한다. 전체 디스크를 검색하지 않는다. 설정 파서는 일반 YAML 검증기가 아니므로 복잡한 구문·중복 필드는 `REVIEW_REQUIRED`로 보고 소유자가 확인한다. NGINX include와 실행 프로세스에 실제 반영된 설정까지 증명하지 않는다.

## TLS 담당자가 함께 확인할 내용

[인계 양식](../templates/parallel-preparation-handoff.json)을 사용한다. 계정 암호·Token·개인키 내용은 입력하지 않는다.

1. **정식 CA 존재 여부:** 승인된 CA 자산 기록과 공개 인증서 SHA-256 지문, 기존 Consumer 범위. 파일 미발견이나 과거 임시 인증서 이력만으로 CA가 없다고 결론내리지 않는다.
2. **CA 운영 환경:** 이권욱이 관리할 보호 환경과 백업·갱신 절차. CA 개인키를 Runner나 Actions Secrets로 옮기지 않는다.
3. **Harbor 설치 정본:** 서로 다른 Compose 경로 라벨이 관측된 이력이 있으므로 이하영이 실제 관리할 설정 파일·적용 경로를 확정한다. 라벨만으로 정본을 선택하거나 `docker compose up/down`을 실행하지 않는다.
4. **발급 프로파일·적용:** IP SAN 10.1.93.54, CA:FALSE, 서버 인증 목적을 유지한다. DNS SAN·유효기간·변경 시점·이전 인증서 복구 방법을 정한다.

기존 CA를 재사용하거나, 정식 CA가 없음을 확인한 뒤 보호 환경에서 최초 생성한다. Harbor에서 개인키와 CSR을 만들고 CSR만 이권욱에게 전달한다. 이권욱은 서명한 Leaf·CA 공개 인증서·지문·발급 기록을 전달하고, 이하영은 적용·복구를 확인한다. 그 다음 Runner·Worker Trust와 엄격한 TLS, 인증 Push/Pull을 시험한다. 기존 Root 교체는 영향받는 Consumer의 전환·복구 계획을 먼저 확인한다.

## Runner 등록 범위와 구현 준비

앱 저장소가 `infra-cicd`의 재사용 Workflow를 호출하면 Runner 접근은 **호출한 앱 저장소의 문맥**을 따른다. `infra-cicd`에만 등록된 저장소 전용 Runner가 앱 저장소 호출에도 자동 사용되는 것은 아니다. 호출 저장소와 조직 설정의 실제 허용 범위를 확인한 뒤 등록한다. [GitHub 재사용 구성 규칙](https://docs.github.com/en/actions/reference/workflows-and-actions/reusing-workflow-configurations)

권장 준비 방향은 앱 저장소 목록을 먼저 확정하고, 조직 Runner를 쓸 경우 실제 설정에서 승인한 저장소만 접근하도록 구성 가능한지 확인하는 것이다. 이 단계에서는 조직 전체 접근이나 임의 저장소명을 승인 상태로 만들지 않는다. 조직 Runner를 사용할 수 없으면 호출 저장소 전용 Runner 또는 중앙 실행 방식을 비교해 기록한다.

등록 전 입력: 앱 저장소·승인 Commit·Dockerfile·Unit/Contract Test 명령, 두 Deployment·컨테이너명, 빌더 선택, Trivy 기존 Critical 차단 기준·DB 공급, Robot Push/Pull 권한, SA/RBAC·단기 Token 주입 방식. 현재 설치된 Podman 존재만으로 Docker 컨테이너 Action/서비스 컨테이너가 실행 가능하다고 판정하지 않는다. 이런 Job은 Docker 요구가 있으므로 실제 Workflow 형태와 함께 선택한다. [Runner 요구사항](https://docs.github.com/en/actions/reference/runners/self-hosted-runners)

이번 GitHub 검사는 직접 HTTPS로 `github.com`, `api.github.com`의 DNS·인증서 검증만 수행한다. `*.actions.githubusercontent.com`, 다운로드 CDN·Artifact 등은 실제 등록/Job으로 추가 검증한다. 프록시는 자동 사용하지 않으며 프록시 환경은 별도 확인한다. 기존 IF-13M 범위를 고려해 관리 DNS 주소가 보이거나 Resolver가 불명확하면 네트워크 검사를 건너뛴다. DNS·방화벽 변경은 수행하지 않는다.

등록 범위와 플랫폼 확인 후 GitHub 설정 화면에서 현재 패키지 버전·검증 해시·등록 절차를 얻어 설치하고 서비스로 실행한다. 등록 Token은 해당 서버에서만 입력하며 대화나 증적에 붙이지 않는다. 첫 Job은 자격증명 없는 동작 확인부터 진행하고, 승인된 호출·권한·작업 정리 기준에 따라 단계적으로 연결한다. [등록 절차](https://docs.github.com/en/actions/how-tos/manage-runners/self-hosted-runners/add-runners)

## 증적 수신 후 처리

- Runner 결과: 기존 설치·등록 여부와 부족 도구를 판정하고, 호환 버전·빌더·등록 범위에 맞는 설치 절차를 만든다.
- Harbor 결과와 인계: CA 재사용/최초 생성, CSR·서명·적용·복구 명령을 확정한다. TLS 설정 변경 전 동일 인증서 수집을 반복하지 않는다.
- 두 흐름이 합쳐지는 시점: 엄격한 TLS 성공 → Runner Push/Worker Pull → 승인 Digest 배포·Health/E2E → 실패 차단·복구 시험.
- 초기 구성 PR #1은 별도 기술 검토 후 이권욱이 병합 판단한다. 이 준비 도구의 실행이 병합 또는 배포 승인을 대신하지 않는다.

기준: [고정 베이스라인 v3.1.17](https://github.com/321Team/fds-project-baseline/blob/2d93395a0e19ea67e5be199d7183a7edbaa6cf4c/baseline/FDS_Project_Implementation_Baseline_v3.1.17.md), 사용자 확정 TLS 역할. Harbor HTTPS의 `hostname`·`https` 설정 구조는 [공식 문서](https://goharbor.io/docs/2.13.0/install-config/configure-https/)를 참고하며, 실제 적용은 수집된 설치 버전과 정본을 기준으로 한다. 해당 문서의 예시 수명·키 배포·재구성 명령을 프로젝트에 일괄 적용하지 않는다.
