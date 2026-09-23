# P1-CICD-01 — Runtime BLOCK 후속과 취약점 수집

현재 후속(2026-09-24 KST): 취약점 요약과 기존 Run의 app-evidence [수신·연결 대조가 완료](https://github.com/321Team/infra-cicd/issues/11#issuecomment-5797608952)됐다. [Draft 증적 PR22](https://github.com/321Team/fds-project-evidence/pull/22)는 독립 리뷰·편입 대기다. 기존 수집을 반복하지 않고 collect 종료 로그/시각·원 archive 검토자 접근만 보완한다. 다음 구현 입력은 [앱 호환 조합·base·새 공급물 후보](remediation-candidates-20260924.md)다. 앱 BLOCK·fds-engine NOT_SCANNED·Runtime AC1/10·Delivery NOT_VERIFIED는 유지한다.

점검일 2026-09-23 KST / Lead 이권욱. 기준 main `01d8013b589c3cf2fb996ea35c9b3b7f6fcdebcc`.
관련: [CICD #11](https://github.com/321Team/infra-cicd/issues/11), [책임 티켓 #16](https://github.com/321Team/fds-project-baseline/issues/16).

## 1. 확인한 단계와 한계

| 항목 | 확인 결과 |
|---|---|
| 앱 인계·config | APPROVED_FOR_TEST_BUILD_SCAN, 조직 Source ec69b67067decf35abbfed0a6a99f3073132787b. #19/#21/#24/#23 병합 완료 |
| Scanner 수정 | #26 병합. OCI layout 디렉터리 입력 수정 후 실제 Run 도구 오류 해소 |
| 원본 Actions 로그 | [Run35683010080/Attempt1/Job106603847854](https://github.com/321Team/infra-cicd/actions/runs/35683010080/job/106603847854), 09-22 12:24:04~12:25:34 KST, cicd-runner01, BLOCK/ERROR_CODE=NONE/exit10 |
| 서비스별 상세 | 수신 영수증·요약 결합 확인: transaction-api CRITICAL3/HIGH57·고유CVE24, fds-engine NOT_SCANNED. 원본 report 바이트는 미포함 |
| 남은 Delivery | Push/Deploy 미실행. Workflow에 아직 Push/Deploy 어댑터가 없어 배포 이미지 유지까지 실증한 것으로 해석하지 않음 |
| AC·증적 | Runtime AC1/10·Delivery NOT_VERIFIED. 증적 Draft PR22 준비, main 미편입·독립 리뷰 대기 |

이전 리뷰·저장소/SHA 불일치·API Unit 미인계는 해소됐다. 새로운 차단은 취약점 개선 입력과 신규 공급물이다. 기존 BLOCK을 해소하려고 동일 입력으로 재실행하거나 보안 게이트를 완화하지 않는다.

## 2. 담당별 후속

| 담당 | 현재 필요한 작업 | 인계·검증 |
|---|---|---|
| 이권욱/CICD | 기존 수신 완료. collect 로그/시각·원 archive 접근 보완과 증적 PR22 독립 검토 인계 | 새 수집 없이 원 작업 폴더 자료로 보완. 수신 raw payload는 미포함 |
| 이재환/앱 Lead | FastAPI·Starlette 호환 조합, requirements·환경별 lock4개, base digest 후보와 변경 영향 검토 | 앱 PR 독립 리뷰·최종 승인40자리 SHA·Unit/Contract 범위, API Unit N/A 유지 시 변경 후에도 타당한지 확인 |
| 이권욱/CICD + 공급 담당 | 승인된 위치·전송 경로에서 새 OCI/wheel 공급물 준비·인수 | 신규 공급 폴더·archive/manifest/config·roots/lock/inventory/requirements hash. 기존 공급 폴더 덮어쓰기 금지 |
| 이권욱/CICD | 새 앱 SHA·Dockerfile·공급물에 일치하는 config PR 작성 | 운영 입력 변경이므로 독립 리뷰 필요. 확인 전 값을 추측해 채우지 않음 |
| 해당 Lead | 재스캔 후 미패치 항목의 개별 처리 판단 | 예외가 필요하면 CVE·해당 Image Digest·사유·만료·승인자. 일괄 ignore 또는 미패치 자동 면제 금지 |
| 이하영/HBR·K8S 연계 | 향후 Push/Deploy에 필요한 CA·Robot·기존 Deployment·RBAC·Health/E2E 인계 | 앱 보안 차단 해소와 별도 준비. 이번 작업으로 새 계정/권한·운영 배포를 수행하지 않음 |

**의존성 수정 주의:** 두 앱의 승인 requirements는 FastAPI==0.115.5다. [해당 버전의 공식 pyproject.toml](https://github.com/fastapi/fastapi/blob/0.115.5/pyproject.toml)은 Starlette>=0.40.0,<0.42.0을 요구한다. 기존 보고의 수정 버전 후보로 Starlette lock만 상향하면 이 범위를 벗어난다. 앱 담당이 FastAPI와 함께 호환 조합을 선정하고 host/test 및 image/runtime lock을 재생성·검증해야 한다. [09-24 문서](remediation-candidates-20260924.md)에 격리 시험 후보를 제시했다. 최종 앱 버전·base digest·운영 입력 승인은 미완료다.

PR #18은 [09-22 Lead 종결](https://github.com/321Team/infra-cicd/pull/18#issuecomment-5772479406) 상태다. 기존 공급 생산 증적과 소비 코드를 보존하되 종결된 다운로드 경로를 자동 재활성화하지 않는다. 새 공급물 생산 위치·방식은 앱 개선 입력에 맞춰 인계한다.

## 3. 기존 Run의 외부 대조 기준

| 필드 | 값·근거 |
|---|---|
| workflow_sha | 7e939d7f77c38c13fd331b38f6649580bf0d05bd — Actions Job checkout |
| source_repository | 321Team/fds-msa |
| source_sha | ec69b67067decf35abbfed0a6a99f3073132787b — 실행 SHA의 승인 config |
| run_id / attempt | 35683010080 / 1 |
| input_sha256 | ea375a00811923cfb415e6c851ceed55227b5978835953eb2227b03552ab5728 |
| config Git blob | 0438f87045ff1a685385b2d2172289eafbc23138 — 원본 바이트의 Git blob 재계산 일치 |
| EVIDENCE_DIR | /home/server01_cicd-runner/FDS_CICD_AppWorkflow.log3xwta — 원본 Job 로그 |
| 원래 CICD checkout | /home/server01_cicd-runner/actions-runner/_work/infra-cicd/infra-cicd/cicd-app-35683010080-1 |

기존 [app_evidence.py collect/verify 절차](app-evidence.md)에 이 기준을 사용한다. 기준은 영수증 자기 선언에서 복사하지 않았다. collect는 기존 원본을 읽고 새 지정 출력에만 archive를 만든다. 기존 checkout이나 원본이 없으면 중단하고 복구/보존 위치를 확인한다. 재실행이나 영수증 재작성으로 대체하지 않는다.

원본 report는 이미지 환경변수 등 민감정보를 포함할 수 있어 그대로 이슈·채팅에 게시하지 않는다. 기본 app-evidence archive도 report 원본을 포함하지 않는다. 아래 별도 요약은 패키지별 분석을 위한 참고자료다.

## 4. 취약점 요약 수집기 — Pre-check 분류

`tools/precheck/summarize_trivy_findings.py`는 기존 report 한 파일(최대64MiB)을 읽고 새 출력 파일 하나(0600)를 작성한다. HIGH/CRITICAL의 CVE/GHSA·패키지·설치/수정버전·분류·상태만 허용 목록으로 추출한다. 이미지 환경변수·명령·Target 경로·설명·URL·원본 오류는 출력하지 않는다. 결과의 hash는 읽은 바이트의 식별값이며 무결성 승인이나 이미지 provenance 검증이 아니다.

동작: 1회 실행, stdlib만 사용, 네트워크/subprocess/Trivy/Podman/설치/상주 기능 없음. 원본·서비스·네트워크·권한은 바꾸지 않는다. 기존 출력은 덮어쓰지 않는다. symlink·FIFO·과대/잘못된 입력은 거부한다. 이 요약은 운영 config·보안 게이트·Release 판정의 입력으로 소비하지 않는다.

[중앙 정책 §3.2](https://github.com/321Team/fds-project-baseline/blob/22f369cc0165244828e3fadebf13eaad02609d70/governance/REVIEW_AUTHORITY_POLICY.md)에 따른 **정보 수집용 Pre-check**다. 보안 게이트/무결성 검사기를 변경하거나 대체하지 않으며 기존 app_evidence.py와 scan_images.py는 그대로다. 새 단위시험은 기존 GitHub-hosted 소스 CI가 발견하도록 tools/delivery에 둔다. 운영 Workflow는 이 수집기를 호출하지 않는다.

Lead 자체 코드 점검 기준: 기존 report 읽기·실행자 작업 경로의 새 요약 작성만 수행, 지속 설치 없음, 기존 Runner 계정·권한만 사용, 새 자격증명 없음, 부하/장애 주입 없음. 이 범위의 운영 서버 1회 실행은 순수 수집기로 판단한다. 실제 실행 SHA·계정·KST 시각·출력 경로는 실행 시 기록한다. **REVIEW=NOT PERFORMED (Pre-check)**이며 요약 수집은 09-23 21:23:34 KST에 COLLECTED, 수신 파일 hash 대조도 완료했다. app-evidence 수신 verify·Run/Source/Image/report hash 연결 대조는 09-24 완료했으며 서버 collect 종료 로그/시각은 미수신이다. Delivery 경로에 편입하면 일반 독립 코드 리뷰로 돌아간다.

## 5. 보존 기록: 최초 취약점 요약 수집 안내 — 현재 회차 재실행 불필요

환경: cicd-runner01, server01_cicd-runner, UID1000, Python3.11 이상. 병합된 수집기를 해당 계정 홈에 `summarize_trivy_findings_20260923.py`로 전송한다. 현재 소스 SHA256은 `b69db60bb5e87dac33cd33d50e022b01adbcf6dc468ed4f6c44182efc4139758`다. 다른 버전은 이 hash로 실행하지 않는다.

```bash
(
  set -euo pipefail
  umask 077
  test "$(hostname -s)" = cicd-runner01
  test "$(id -un)" = server01_cicd-runner
  test "$(id -u)" = 1000
  cd /home/server01_cicd-runner
  date --iso-8601=seconds
  printf '%s\n' 'b69db60bb5e87dac33cd33d50e022b01adbcf6dc468ed4f6c44182efc4139758  summarize_trivy_findings_20260923.py' | sha256sum -c -
  /usr/bin/python3 -I -B ./summarize_trivy_findings_20260923.py \
    --report /home/server01_cicd-runner/FDS_CICD_AppWorkflow.log3xwta/build-scan/scan/transaction-api/report.json \
    --output /home/server01_cicd-runner/cicd-findings-35683010080-1.json
  sha256sum /home/server01_cicd-runner/cicd-findings-35683010080-1.json
)
```

정상: `FINDINGS_SUMMARY=COLLECTED`, `ASSESSMENT=INFORMATION_ONLY`, exit0, 새 JSON 생성. 이는 Scan PASS가 아니다. 출력은 finding record 수와 고유 CVE/GHSA 수를 구분하며, FixedVersion은 원본 보고의 주장으로 표시한다. 일부 허용 필드에 운영자가 민감정보를 넣었을 가능성까지 자동 판별하지 않으므로 공유 전 확인한다.

실패: exit1/INPUT_OR_OUTPUT_ERROR이면 중단한다. 잘못된 입력·버전·이미지·새 Run으로 임의 대체하거나 report를 수정하지 않는다. 기존 결과가 있어 실패했다면 다른 새 출력 이름을 명시한다. 정상 출력 후 원본 report의 hash는 영수증 및 app_evidence 제출 기록과 별도로 대조한다. 임시 요약은 보존해도 서비스에 영향이 없으므로 자동 삭제·복구 명령을 두지 않는다.

회신: `cicd-findings-35683010080-1.json`, 그 SHA256, 실제 실행 시각. 기존 app-evidence archive와 sidecar는 수신·연결 대조가 완료됐으며 편입 초안은 PR22다. 남은 collect 기록·접근 보완은 09-24 후속 문서를 따른다. 독립 리뷰 전 편입 완료나 Runtime AC 상향을 선언하지 않는다.
