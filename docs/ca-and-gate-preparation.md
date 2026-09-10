# P1-CICD-01 CA 준비·배포 차단 로직 병행 작업 v1.1

착수일: 2026-09-10. 이권욱 담당의 CA 준비와 CI/CD 차단 로직을 먼저 구현했다. 소스 기준은 main `be92fd00b269ff7d7abc124bb17f6d3e4c007851`, 베이스라인 v3.1.17 Commit `2d93395a0e19ea67e5be199d7183a7edbaa6cf4c`다. 착수 시 조회한 정본 main도 같은 Commit이었다. 2026-09-10 사용자 지시 “기존 승인 CA는 폐기 신규 발급 필요”에 따라 재사용 선택을 철회하고 새 키·Root 발급으로 변경했다. [결정·전환·폐기 기록](decisions/ca-replacement-20260910.md)을 따르며 PR #2의 발급 절차·설정도 동기화한다.

이번 산출물은 입력 점검과 모의 시험이 가능한 준비 코드다. 실제 CA 자산·인증서 발급·Trivy 스캔·Push·Kubernetes 배포를 수행하지 않았다. **DELIVERY_GATE=NOT_VERIFIED**를 유지한다.

## 1. 병행 범위와 역할

| 작업 | 이권욱 | 다른 담당·선행 입력 |
|---|---|---|
| CA 준비 | 신규 Root 보호 환경·백업·접근통제 기록, 구 CA 전환·폐기 준비 | 실제 보호 환경·구 CA 지문과 배포 대상 미확인 |
| CA 서명 | 준비 확인 후 새 Root로 기존 CSR에 서명, 발급 기록·공개 인증서 전달 | 이하영: 기존 Harbor 개인키·CSR 유지, 인증서 설치·서비스 전환·복구 |
| CI/CD 차단 | Test·Scan·Digest·승인·대상 조건 검사와 음성시험 | 실행 어댑터·실제 Job 연동은 후속 단계 |
| 앱·도구 인계 | 실제 Source/Test·Trivy·K8s 입력 취합 및 검증 | 앱 저장소·승인 SHA·Dockerfile·테스트 명령·컨테이너명·Token 정책 필요 |

기존 [파이프라인 계약](pipeline-contract.md)과 [역할 기록](decisions/p1-cicd-01-roles.md)을 따른다. 내부 Runner는 infra-cicd 전용·승인 main·수동 실행, Podman rootless를 유지한다. 새 단위시험 Workflow는 GitHub 제공 ubuntu-24.04에서만 실행하며 PR 이벤트 또는 main push로 시작될 수 있다. contents: read, checkout 전체 SHA 고정, 자격증명 저장 해제, 5분 제한을 적용했다. 조직 정책·유료 예산·내부 Runner 설정을 변경하지 않는다.

## 2. CA에서 먼저 확인할 실제 정보

CA를 준비할 곳은 이권욱 관리의 별도 오프라인 관리자 환경이다. 서명 환경으로 서비스 VM을 재사용하지 않는다. 채택 목표는 암호화 볼륨 `/mnt/fds-ca`이며 장치·호스트·암호화 방식의 실제 값은 아직 없다. 실제 준비 상태를 확인하기 전에 일반 디렉터리에서 Root를 만들지 않는다.

| 현장 확인 | 입력·근거 |
|---|---|
| CA 선택 | REPLACE_WITH_NEW_ROOT 확정. 기존 CA 사용 종료·신규 발급 결정 참조 |
| 작업 환경 | 실제 호스트·보관 자산 참조, 관리자 접근통제와 오프라인 서명 절차 |
| 암호화·백업 | 실제 암호화 확인 근거, 별도 암호화 백업 매체·복구 절차 참조 |
| 구 CA 식별·폐기 | 지문·발급 대장·소비자·키 복제본/백업 조사. RETIREMENT_REQUIRED는 실행 완료가 아님 |
| 신규 자산 | 별도 신규 디렉터리·새 개인키·새 Root. 기존 키·발급 대장 재사용 금지 |

Linux CA 후보 환경에서는 다음 조회로 마운트·장치·호스트 정보를 확인할 수 있다. 디렉터리를 만들거나 볼륨을 포맷하는 명령이 아니다. `findmnt`가 실패하면 마운트가 확인되지 않은 상태로 기록한다. `mountpoint`나 `findmnt` 성공만으로 암호화·오프라인 상태를 입증하지 못한다. 실제 암호화 방식·잠금/해제·백업 확인은 관리자 기록과 대조한다.

```bash
(
  set -euo pipefail
  hostname -s
  id -un
  date --iso-8601=seconds
  findmnt --mountpoint /mnt/fds-ca --output TARGET,SOURCE,FSTYPE,OPTIONS
  lsblk --output NAME,TYPE,FSTYPE,MOUNTPOINTS
)
```

[CA 입력 양식](../templates/ca-preparation.example.json)에 실제 확인값과 기록 참조를 채운다. 개인키·암호·인증 Token은 입력하지 않는다. 현장 확인하지 못한 값은 null로 둔다. 신규 발급 선택은 확정값으로 유지한다. v1.0의 재사용/부재 조건은 거부하며 v1.1 양식으로 옮겨 실제 근거를 입력한다. 실제 확인 후에만 `template_only`를 false로 변경한다. 검사기는 명시한 JSON 하나만 읽고, 자산이나 개인키를 탐색하지 않는다. `COMPLETE_FOR_REVIEW`는 기재된 조건의 충족 여부이며 독립 자산 확인·승인·발급 완료가 아니다. 구 지문이 null이면 LEGACY_CA_IDENTIFICATION_PENDING을 남기지만, 구 CA 부재나 폐기 완료를 신규 준비의 선행조건으로 요구하지 않는다. 기존 CA 지문·배포 현황 확인은 실제 신뢰 제거·폐기 인수 전까지 완료해야 한다. CA 후보 이름이 알려진 서비스 호스트와 다른 것만으로 오프라인 전용 환경이 입증되지는 않는다.

이미 검토한 요청을 계속 사용한다.

- 요청: `harbor01-sn0wepv7`, 개인키는 Harbor 보호 영역에 보존.
- CSR SHA-256: `97473d44f37140dfe04c83a29f6fec17560bb6f7b29a543fd1fa06cce91ca890`.
- SPKI SHA-256: `636f85ce3ae0d5e3ea49e50fca2b8ec1e5c132f990d7867e2ad59d0a2e333638`.
- 보호 환경이 준비되면 [PR #2의 갱신된 CA 발급 절차](https://github.com/321Team/infra-cicd/blob/3a1a9a8d774eca2917ea62bbe184fea0e11724b2/docs/harbor-formalization.md)에 따라 새 Root로 서명한다. 새 Root RSA4096/1825일, Harbor Leaf RSA3072/365일·30일 전 갱신·CA:FALSE/serverAuth·기존 SAN을 유지한다. 본 브랜치는 새로운 인증서 프로파일을 발행하지 않는다.

## 3. 배포 차단 로직

`tools/preparation/delivery_gate.py`는 두 지점의 조건을 검사한다. 임의 셸 명령·Token·실제 접속 주소를 실행하지 않는다. `guarded_call`은 **신뢰된 후속 어댑터에서 통합할 함수 경계**이며 이번 시험에서는 Mock만 전달했다. CLI에는 실행 명령이나 `--apply` 기능이 없다.

| 조건 | pre_push | pre_deploy |
|---|---|---|
| infra-cicd/main, 승인 Workflow SHA, Run/Attempt | 필수 | 필수 |
| 두 앱의 승인 Source 저장소·SHA·검토 참조 | 필수 | 필수 |
| 실제 Job·rootless·Harbor strict TLS 보고값 | PASS 필수 | PASS 필수 |
| 두 앱의 Unit/Contract Test·Build | 모두 PASS | 모두 PASS |
| 스캔 결과·종료코드·CRITICAL 건수 | PASS / 정수 0 / 정수 0 | 동일 |
| 스캔 보고서 해시·도구 버전·DB 참조 | 필수 | 필수 |
| Build/Scan manifest Digest 일치 | 필수 | 필수 |
| Push 성공 및 Build/Scan/Push/승인 Digest 일치 | Push 전에는 검사하지 않음 | 필수 |
| 배포 승인자의 기록·Run/Attempt/Workflow 결합 | 배포 승인 전이므로 검사하지 않음 | 필수 |
| fds-app, 기존 두 Deployment·실제 container명·이전 Digest | 배포 단계 검사 | 필수 |
| API·Token·RBAC·Worker Trust·동시 배포 잠금 보고값 | 배포 단계 검사 | 모두 PASS |

CRITICAL 또는 검사 오류는 차단한다. HIGH만으로 새로 차단하지 않는다. CRITICAL 건수/exit code는 bool·문자열·null을 정수 0으로 간주하지 않는다. 승인 예외 적용 기능은 이번 단계에 넣지 않았으며 예외가 필요하면 기존 승인·만료 절차에 맞춰 별도 검토한다.

**중요한 의미 구분:** manifest Digest는 배포할 동일 OCI/Docker manifest 바이트의 SHA-256이다. 이미지 config ID, archive 전체 해시, scan JSON 해시를 manifest Digest로 대신 입력하지 않는다. `scan.report_sha256`는 보고서 파일의 해시이고 `scan.manifest_digest`는 스캔 대상을 특정하는 manifest Digest다. 후속 Build/Scan/Push 어댑터는 동일 manifest 연결을 직접 검증해야 한다. Push 중 manifest 변환으로 Digest가 달라지면 불일치를 무시하거나 숫자만 복사하지 않고 산출물/스캔 절차를 다시 확인한다. 단일 manifest/index·플랫폼 경계도 실제 빌드 방식 확정 시 명시한다.

Trivy는 기본 설정에서 취약점을 발견해도 exit 0일 수 있으므로 종료코드만으로 통과시키지 않는다. 본 검사기는 **정규화된 입력**을 검사하며 실제 Trivy JSON을 파싱·실행하는 어댑터는 아직 없다. 도구 버전·DB/갱신·전체 검사 범위·무시 설정·원본 보고서·대상 Digest를 검증하는 어댑터가 필요하다. [Trivy 종료코드](https://trivy.dev/docs/latest/configuration/others/#exit-code), [OCI descriptor](https://github.com/opencontainers/image-spec/blob/main/descriptor.md).

## 4. 이 코드가 보장하지 않는 것

`CONDITIONS_MATCH`는 입력에 기재된 조건의 일치다. 서명된 승인, 실시간 Token 유효성, RBAC·배포 잠금, 명령 실행 성공을 인증하지 않는다. JSON을 수동 편집해 PASS로 채운 결과로 운영 배포를 승인하지 않는다. **실제 Workflow는 운영자가 임의로 올린 JSON 대신 해당 승인 실행의 신뢰된 어댑터가 수집한 값과 독립 승인 기록을 사용해야 한다.** 소스·테스트·스캔 결과의 Run/Attempt 결합, 보고서 실물/해시 검증도 그 어댑터가 수행한다.

RBAC가 image 필드만 제한한다고 주장하지 않는다. 향후 Kubernetes 어댑터는 승인한 get/update/patch와 실제 컨테이너명으로 기존 두 Deployment만 갱신하며 create/delete/Secret/exec 권한을 추가하지 않는다. Podman·Trivy·Harbor·Kubernetes 어댑터, Health/E2E, 자격증명 정리, 실패 중 부분 적용 복구는 미통합이다. 두 Deployment 갱신은 원자적 트랜잭션으로 보장되지 않으므로 이전 Digest·스키마 호환성·부분 실패 복구를 후속 구현한다.

입력 오류·조건 불충족 시 callback 호출 0회를 모의 시험했다. 이는 실제 실패 상황에서 클러스터 이미지가 유지됨을 확인한 증적을 대체하지 않는다. `runtime_enforcement=NOT_INTEGRATED`, `delivery_gate=NOT_VERIFIED`를 항상 출력한다.

## 5. 파일 확보·압축 해제와 로컬 실행

실행 대상은 Python 3.10 이상이 있는 Linux 작업 환경이다. 실제 CA 조회는 CA 후보 환경에서만 한다. 아래 JSON 검사·단위시험 자체에는 sudo·Harbor·Kubernetes 접속이 필요 없다.

GitHub에서 이 후속 PR의 **검토할 전체 Commit 화면**을 열고 해당 Commit의 소스 ZIP을 내려받아 홈에 둔다. 이동하는 브랜치명으로 받은 ZIP 대신 Commit 기준 파일을 사용한다. 다음 블록에 그 전체 SHA를 입력한다. GitHub 소스 ZIP의 기본 이름은 `infra-cicd-<전체SHA>.zip`이며 내려받은 이름과 다르면 먼저 일치시킨다.

```bash
(
  set -euo pipefail
  umask 077
  cd "$HOME"
  read -r -p '후속 PR의 전체 Commit SHA: ' prep_commit
  [[ "$prep_commit" =~ ^[0-9a-f]{40}$ ]]
  prep_archive="infra-cicd-$prep_commit.zip"
  test -f "$prep_archive"
  prep_work="$(mktemp -d "$HOME/P1_CICD_CA_Gate.XXXXXXXX")"
  python3 -m zipfile -e "$prep_archive" "$prep_work"
  cd "$prep_work/infra-cicd-$prep_commit"
  sha256sum --check PREPARATION_SHA256SUMS
  python3 -m unittest discover -s tools/preparation -p 'test_*.py' -v
  prep_rc=0
  python3 tools/preparation/ca_readiness.py \
    --record templates/ca-preparation.example.json || prep_rc=$?
  test "$prep_rc" = 2
  prep_rc=0
  python3 tools/preparation/delivery_gate.py --phase pre_deploy \
    --record templates/gate-input.example.json || prep_rc=$?
  test "$prep_rc" = 2
  printf 'UNFILLED_TEMPLATES_BLOCKED=PASS\nWORK_DIR=%s\n' "$PWD"
)
```

내부 PREPARATION_SHA256SUMS는 내려받은 소스 파일 간 무결성 확인이며 다운로드 출처의 독립 인증은 아니다. GitHub의 고정 Commit과 파일 목록을 함께 확인한다. 기존 v1.0 키트는 소급 변경하지 않았다. 그 안의 CA 재사용·부재 조건은 이번 결정으로 대체되므로 신규 발급에는 갱신된 Runbook을 사용한다. 기존 Runner 등록은 반복하지 않는다.

두 양식의 초기 결과는 `INCOMPLETE` 또는 `BLOCK`, 종료코드 2가 정상이다. 실제 준비 정보를 입력하려면 위 출력의 WORK_DIR로 이동한 뒤 아래 명령으로 **새 파일**을 만들고 편집한다. 기존 파일이 있으면 이 명령은 실패하므로 다른 이름을 사용한다.

```bash
python3 - <<'PY'
from pathlib import Path
source = Path('templates/ca-preparation.example.json').read_bytes()
with (Path.home() / 'ca-preparation.json').open('xb') as destination:
    destination.write(source)
PY
```

수정한 파일은 `python3 tools/preparation/ca_readiness.py --record "$HOME/ca-preparation.json"`으로 검사한다.

| 종료코드 | 의미 |
|---|---|
| 0 | 입력 조건 일치/기재 완료. 운영 승인·발급·배포 성공이 아님 |
| 2 | 정보 부족 또는 조건 불충족. 미확인 값을 먼저 확인 |
| 3 | 입력 파일/JSON 오류. symlink·FIFO·과대 파일·중복 키·NaN/Infinity 등은 거부 |

## 6. 다음 인계

1. 이권욱: 실제 보호 환경·암호화/백업을 확인하고 신규 Root 준비 기록을 완성한다. 기존 CA 재사용 선택은 종료했다. 새 CA 발급과 함께 구 CA 식별·실제 Consumer 전환·신뢰 제거·키 폐기를 추적한다.
2. 앱 담당 입력: 두 앱의 실제 Source·승인 SHA·Dockerfile·Unit/Contract 명령·호환성 및 컨테이너명.
3. CI 어댑터 입력: Trivy 버전/배포물·DB 공급/갱신, 승인된 Harbor repository/Robot, K8s 버전·TokenRequest·실제 RBAC/잠금/Health 경로.
4. 독립 리뷰 후 별도 승인된 변경으로 실제 어댑터와 Workflow를 연결한다. 기존 PR #2의 첫 smoke 결과 및 Harbor TLS 인수와 결합한다.

로컬 시험은 [검증 기록](../tools/preparation/VALIDATION.md)에 기록하며 GitHub CI 결과는 실제 Run URL·Commit·Attempt와 함께 PR에 별도로 기록한다.
