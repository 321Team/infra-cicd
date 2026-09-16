# 앱 시험 매핑과 공급 경로 인계 후보 — 2026-09-16

상태: **PREPARATION / PENDING_APP_HANDOFF**. 사용자 “착수”에 따른 준비 변경이다.
실제 앱 Test/Build/Scan, 서버 설정, Workflow 실행, Push/Deploy는 수행하지 않았다.
기존 P1-CICD-01 AC·담당·정본 v3.1.18을 변경하거나 실행 승인을 생성하지 않는다.

## 작성 전 대조

- CICD 원본: `01f370f843d39a81ca9420c4ef0cd52c6e59574c`, 변경 6개 파일과
  `tools/trivy/prepare_db.py`의 자식 환경, 승인 설정 결합, 실행 순서를 확인했다.
- 앱 관측본: `supreme33-kr/fds-msa@5b0049c2441b98196db6cd8cd4be9a85607568ea`.
  관측 SHA는 최종 실행 승인 SHA가 아니다.
- [앱 인계](https://github.com/321Team/infra-cicd/issues/11#issuecomment-5694905546)는
  서비스별 `pip install -r requirements-test.txt && pytest`와 edge01 26 PASS 보고다.
  원본 로그 재검증·CICD Runner Runtime PASS가 아니다.
- [PR #12](https://github.com/321Team/infra-cicd/pull/12)의 독립 리뷰는 작성 시작 시 0건이었다.
  아래 준비 변경을 포함한 새 HEAD를 이하영이 검토해야 한다.

## 중복 없는 시험 매핑 후보

| 서비스 | 단계 | 실제 파일 | 정적 함수 수 | 의미와 제한 |
|---|---|---|---:|---|
| fds-engine | Unit | `tests/test_rules.py` | 16 | 순수 함수 시험. 이 중 evaluate_all 2개는 함수 수준의 응답 형태·호환성 계약도 확인 |
| fds-engine | Contract | `tests/test_health.py` | 5 | TestClient로 health/metrics/evaluate/422 HTTP 계약 확인 |
| transaction-api | Unit | 인계 없음 | 0 | 성공 명령이나 같은 Contract suite를 복제하여 채우지 않음 |
| transaction-api | Contract | `tests/test_health.py` | 5 | health/metrics/422. DB ping은 `app.main.db_ping`을 mock 처리 |

총 26개는 AST에서 센 함수 정의 수다. pytest collection·실행 건수를 새로 검증한 수치가 아니다.
API의 DB 연결·거래 저장/조회·두 서비스 통합 성공을 위 결과로 주장하지 않는다.
각 파일은 한 단계에만 배정했다. 전체 pytest를 Unit/Contract에서 두 번 실행하지 않는다.

`config/app-build-scan.json`에는 실제 파일에 대응하는 argv와 service별 setup 후보를 반영했다.
작업 디렉터리는 각각 `fds-engine`, `transaction-api`다.

1. `/usr/bin/python3 -m venv .venv` — 해당 실행의 test 사본 내부, timeout 120초 후보.
2. `.venv/bin/python -m pip --disable-pip-version-check install --no-cache-dir -r requirements-test.txt`
   — timeout 600초 후보. 공급 경로·유효 pip 설정 확인 전 실행하지 않는다.
3. `.venv/bin/python -m pytest -q <위 단계의 파일>` — timeout 300초 후보.

이 timeout은 기대 수행시간이나 정본 AC가 아니라 CICD 준비용 제안이다.
셸의 `&&`는 순차 argv 단계로 분리했다. 앞 단계 nonzero/timeout이면 중단된다.
서비스별 venv는 전역 패키지 설치와 두 서비스 의존성 충돌을 피하기 위한 준비 선택이다.
빌드 사본은 별도로 생성되므로 이 venv를 이미지 context에 가져가지 않는다.

engine test requirements는 운영 requirements + pytest 8.3.3 + httpx 0.27.2다.
API test requirements는 운영 requirements + pytest 8.3.3이며 httpx 0.27.2는 운영 requirements에 있다.
전이 의존성의 완전한 lock/hash 고정까지 완료된 것으로 표현하지 않는다.
pip의 실제 index/extra-index, 시스템 설정 및 Dockerfile 내부 pip 다운로드 경로도 확인 대상이다.
Token/비밀번호를 포함할 수 있는 `pip config debug/list`, proxy 원문, auth.json을 증적에 복사하지 않는다.

**API Unit 매핑 확인:** 현재 호출기는 각 서비스에 unit/contract 명령을 요구하지만,
확인한 정본 §10은 파이프라인의 Test 흐름을 규정하며 서비스마다 별도 Unit suite를 새로 만들라는
조건은 확인되지 않았다. 따라서 이 공백을 새 필수 AC나 앱 담당의 확정 결함으로 분류하지 않는다.
앱 담당과 검토자는 기존 시험 의도·AC를 기준으로 파이프라인의 Unit/Contract 배정을 확인하고,
필요하면 CICD 입력 모델을 실제 승인 범위에 맞춘다. 이번 준비 변경은 확인되지 않은 범위를
임의로 확정하지 않으며 `true`/동일 시험 중복으로 빈 항목을 채우지 않는다.

## 공급 경로 조사 결과와 권고 후보

[정본 v3.1.18](https://github.com/321Team/fds-project-baseline/blob/docs-v3.1.18/baseline/FDS_Project_Implementation_Baseline_v3.1.18.md)의
IF-08은 Runner→GitHub:443이다. GitHub Job 수신·Trivy DB 다운로드 성공이 Docker Hub/PyPI 허용·성공을 증명하지 않는다.
정본 §15.1.2 OID-03(Bootstrap Package/Registry Egress)의 목적지·Owner·Expiry 인계와 §15.2의 미정의 인터페이스 기본 차단 원칙을 유지한다. OID-04에 따라 관리망 DNS를 임의로 .52/.53으로 바꾸지 않는다.
[DEC-P1-EGRESS-01 #78](https://github.com/321Team/fds-project-baseline/issues/78)의
`10.1.93.56:3128`은 Alertmanager→discord.com:443 전용이다. CICD 일반 프록시로 재사용하지 않는다.
검토한 정본·CICD 설정·#11·#78에서는 Runner의 Docker Hub/PyPI 승인 경로와 base digest를 찾지 못했다.

**권고 후보:** 현재 앱과 같은 Docker Official Image `docker.io/library/python`을 유지하고,
기존 Runner 외부 경로를 사용할 수 있는지 먼저 확인한다. 출처 tag 후보는 `3.12-slim`,
실행 reference는 검증·인계된 **linux/amd64 manifest digest**로 고정한다.
Docker Hub/PyPI 통신이 기존 허용 범위에 없으면 실제 통신 전에 기존 #11/#16에 구체적인 변경 범위를 기록·판정한다.
이는 경로 채택·방화벽 허용·이미지 안전성 승인 완료를 뜻하지 않는다.

Harbor proxy cache는 현재 구축·승인 근거가 없으므로 존재한다고 가정하지 않는다.
Harbor 미러가 실제 인계되는 경우에만 실제 repository와 미러 후 manifest 동일성·Trust·Pull 계정을 검토한다.
빈 authfile을 쓰는 현재 실행기에 private base를 넣으면 인증이 필요한 Pull을 처리할 수 없으므로
private mirror 선택은 코드/자격증명 변경 검토를 동반한다. 앱 담당에게 임의 Harbor 경로를 보내지 않는다.

## 실제 실행 전에 수집할 입력

아래 수집은 준비 범위다. #11에 공개 식별값만 기록하고 Secret은 기존 보호 인계 방식으로 다룬다.
새 네트워크 경로·DNS·방화벽·프록시를 임의 적용하거나 TLS 검증을 끄지 않는다.

| 책임 | 필요한 값 / 완료 조건 |
|---|---|
| CICD 이권욱 | Runner 현재 route/interface/DNS, 로그인 환경과 systemd Runner 환경 구분, 현재 승인 egress 근거 |
| CICD 이권욱 + 해당 네트워크 담당 | Docker Hub 인증/registry/blob 공급 대상과 PyPI index/package 공급 대상의 실제 승인 범위·TLS 경로; 신규 대상은 영향·복구·허용 범위 기록 |
| CICD 이권욱 | 승인 경로에서 tag의 index digest·linux/amd64 child manifest digest·원본 manifest 해시/조회 시각·도구 버전 확인. image config ID를 base manifest digest로 사용하지 않음 |
| 앱 이재환 | 확정된 전체 base reference로 두 Dockerfile의 단일 FROM 수정, 시험 범위 확인, 테스트·Dockerfile 포함 최종 40자리 Source SHA·승인자·근거 |
| CICD 이권욱 | service별 venv/pip 공급, Unit/Contract argv·timeout 최종 연결, Source checkout·Workflow SHA 결합 |
| 리뷰 이하영 | 최종 CICD HEAD와 앱 인계 범위 독립 기술 검토 |

현장 읽기 전용 확인 위치: **cicd-runner01 / server01_cicd-runner / 계정 홈**, sudo 불필요.
아래 명령은 네트워크 설정을 바꾸거나 외부 registry를 조회하지 않는다.
로그인 환경의 로컬 정보만 수집하며 Runner 서비스 환경의 동일성을 주장하지 않는다.

```bash
(
  set -euo pipefail
  test "$(hostname -s)" = cicd-runner01
  test "$(id -un)" = server01_cicd-runner
  test "$(id -u)" -eq 1000
  umask 077
  cd "$HOME"
  supply_dir="$(mktemp -d "$HOME/cicd-supply-local.XXXXXXXX")"
  printf 'EVIDENCE_DIR=%s\n' "$supply_dir"
  trap 'supply_rc=$?; printf "LOCAL_COLLECTION_RC=%s\n" "$supply_rc" > "$supply_dir/exit-code.txt"' EXIT
  date --iso-8601=seconds > "$supply_dir/time.txt"
  hostname -s > "$supply_dir/host.txt"
  id > "$supply_dir/identity.txt"
  /usr/bin/python3 --version > "$supply_dir/python-version.txt" 2>&1
  /usr/bin/podman --version > "$supply_dir/podman-version.txt" 2>&1
  ip -json address show > "$supply_dir/ip-address.json"
  ip -json route show table all > "$supply_dir/ip-route.json"
  cp -- /etc/resolv.conf "$supply_dir/resolv.conf"
  printf 'LOCAL_COLLECTION=PASS\nNETWORK_SUPPLY=NOT_TESTED\nDELIVERY_GATE=NOT_VERIFIED\n' > "$supply_dir/status.txt"
  (cd "$supply_dir"; sha256sum time.txt host.txt identity.txt python-version.txt podman-version.txt ip-address.json ip-route.json resolv.conf status.txt > SHA256SUMS)
  printf 'LOCAL_COLLECTION=PASS\nNETWORK_SUPPLY=NOT_TESTED\n'
)
```

성공: 모든 명령 rc0, status의 LOCAL_COLLECTION=PASS, SHA256SUMS 생성.
중단: 대상 불일치 또는 어느 명령이든 nonzero; 설정 변경으로 우회하지 않는다.
실패 시 홈의 EVIDENCE_DIR와 exit-code를 보존한다. 성공 시 디렉터리 전체를 전달할 수 있다.
서버 설정 변경이 없어 원복 작업은 없으며, 수집 디렉터리는 검토 전 삭제하지 않는다.
proxy 원문·Runner 프로세스 전체 environ·pip 인증 설정·registry auth는 수집하지 않는다.

## 실행 차단과 후속 순서

`status=PENDING_APP_HANDOFF`, source 승인 필드와 두 base_image, API unit은 비워 두었다.
기존 실행기는 status를 먼저 검사하므로 이 준비 설정으로 앱·Git·Podman·DB를 실행하지 않는다.
이 파일을 승인 상태로 바꾸는 것만으로 Source·공급 경로·사람의 승인이 생기지 않는다.

공급 입력 확인 → 앱 Dockerfile 수정·시험 범위/최종 Source 인수 → CICD 설정 완결·새 HEAD 검토 →
승인된 내부 Runner Test→Build→Scan 순서다. Harbor Push와 Kubernetes Deploy 조건은 이후 단계로 유지한다.

공식 참고: [venv와 pip](https://packaging.python.org/guides/installing-using-pip-and-virtual-environments/),
[pip 공급원 선택](https://pip.pypa.io/en/stable/cli/pip_install/).

## 이번 준비 변경 검증

- STATIC: 고정 CICD 원본 51개 Git blob 일치 확인 후 변경. JSON 파싱, 입력된 7개 argv 구조, 문서 현장 명령의 `bash -n`, `git diff --check` 통과.
- STATIC: 앱 3개 test 파일 Git blob 대조·AST 함수 16/5/5, 파일별 중복 함수명 0 확인. 실제 앱 pytest 실행은 하지 않음.
- LOCAL/MOCK: 기존 delivery 시험 18개 통과. command timeout·단계 실패·Source/설정 결합·Mock Build/Scan 차단 포함.
- CLI: 변경 config로 `APP_HANDOFF_PENDING`, exit 1 확인. 실제 실행 허용 상태로 전환하지 않음.
- CI: 새 HEAD 결과는 PR의 해당 Run에서 별도로 확인. 이 문서 작성 시 아직 미실행.
- RUNTIME/REVIEW/MERGE/PUBLICATION: 이번 변경으로 수행·승인·완료 처리하지 않음.
