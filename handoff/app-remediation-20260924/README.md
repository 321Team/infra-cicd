# 앱 개선 후보의 CICD 인계 초안 — 2026-09-24

## 현재 판정

**CONFIG PREPARATION 완료 / 실행 입력 미확정 / 원본 수신·CVE 세부 분류 보류.**

- 책임: [infra-cicd #11](https://github.com/321Team/infra-cicd/issues/11), [P1-CICD-01](https://github.com/321Team/fds-project-baseline/issues/16).
- 기준 infra main: `de6289f995da751ccfa1b343807ec1b3beb0a50c`.
- 관측 앱 후보: [PR7](https://github.com/321Team/fds-msa/pull/7), `bbe349c8815ed2ade3de87a1b312221c34f4361b`. 관측 SHA이며 최종 승인 SHA가 아니다.
- 본 디렉터리의 pending JSON은 현행 운영 config와 별도다. Workflow는 계속 `config/app-build-scan.json`을 사용한다.
- 신규 실행 설정 초안이므로 독립 코드 리뷰 대상으로 취급한다. Pre-check 병합 예외를 적용하지 않는다.
- 기존 증적 [PR22](https://github.com/321Team/fds-project-evidence/pull/22) Ready/독립 심사와 새 후보 [증적 PR26](https://github.com/321Team/fds-project-evidence/pull/26)을 구분한다.

## 실제 반영한 변경안

| 입력 | 초안 |
|---|---|
| Source 승인 | status=PENDING_APP_HANDOFF, commit/approved_by/approval_reference=null |
| API Contract | health + metrics + test_dependency_upgrade_contract.py |
| engine Contract | health + metrics + test_dependency_upgrade_contract.py |
| engine Unit | 기존 test_rules.py 유지 |
| API Unit | null. 이전 Source의 NOT_APPLICABLE 승인·사유를 자동 승계하지 않음 |
| base | 후보 Dockerfile의 linux/amd64 digest44ff437b…; 채택 승인 아님 |
| roots/lock/requirements | 후보 Source17파일의 Git blob 대조 후 원문 바이트로 산출 |
| 실제 공급 경로·OCI archive hash·inventory4종 hash | null. 실물 공급 수신·대조 후 확정 |

roots 계산은 기존 `offline_supply.validate_source`의 방식과 같다. runtime roots에 test requirements의 `-r requirements.txt`를 제외한 줄을 합치고 마지막 LF를 보존한다. 직접 내려받지 않은 wheel inventory 또는 OCI archive hash를 새 실측값으로 채우지 않는다.

## 수행한 검증과 한계

- infra/app 참조28파일 Git blob28/28 일치, 앱 선택17파일17/17 일치.
- 후보 Dockerfile2개와 기존 offline 공급 생성 함수의 출력 바이트2/2 일치.
- Contract 지정 파일6/6·engine Unit1/1 존재, 기존 test_step 입력 형식 검사 성공.
- 실제 main 검증 함수 `build_scan.validate_config`로 확인한6항목:
  1. 현행 승인 config는 입력 형식 검사 통과.
  2. pending 초안은 APP_HANDOFF_PENDING 차단.
  3. status만 바꿔도 APPROVED_SOURCE_SHA_REQUIRED 차단.
  4. 후보 SHA만 채우면 SOURCE_APPROVER_REQUIRED 차단.
  5. 앞선 항목을 메모리의 시험값으로만 채워도 API Unit 미확정은 TEST_STEP_REQUIRED 차단.
  6. API Unit도 메모리 시험값으로 채우면 실제 공급 경로 null은 OFFLINE_DIRECTORY_INVALID 차단.
- 위 시험값은 검증 함수의 경로 확인용이며 파일에 승인 입력으로 저장하지 않았다.
- **STATIC/INPUT VALIDATION 범위**다. 앱 시험·공급 바이트 소비·Podman·Build/Scan·실서버 변경은 수행하지 않았다. 현재 운영 config 바이트도 보존했다.

## 새 격리 Run과 원본 수신

[Run35921580081/1](https://github.com/321Team/fds-msa/actions/runs/35921580081)의 원문 로그는 두 Build PASS, API HIGH44/CRITICAL0·고유CVE8·exit10 BLOCK, engine NOT_SCANNED를 기록한다. DB 전후·driver 종료 후 checksum step은 해당 Job에서 성공했다.

다른 후속 작업이 [PR26의11파일](https://github.com/321Team/fds-project-evidence/tree/43f6c402959251df1c7a04ca778f6080361aeac8/evidence/P1-CICD-01/app-remediation-followup-20260924/github-hosted-run-35921580081-1)에 로그·metadata·로그 추출 result를 보존했으므로 새 로그 사본을 중복 편입하지 않았다.

이번 원본 전송 시도는 HTTP403·수신0bytes였다. **원본 ZIP/Trivy report 수신 checksum 검증은 미수행**이다. 따라서 CVE8개의 식별자·패키지·수정 버전 표는 아직 작성하지 않는다. 이전 base-only 보고서의8CVE나 불완전한 DB 회차를 이번 앱 보고서로 대체하지 않는다.

- [원본 artifact10777267517](https://github.com/321Team/fds-msa/actions/runs/35921580081/artifacts/10777267517)
- ZIP: 278,490,138bytes; GitHub 기록 SHA256 `22c0952ccafa9dc5d65506f821e5c125179d9bd4b7bcbfcd193dda36d5415eb8`
- report: Job 기록 SHA256 `621f420c0f70cca9d8c3919e39c38abe69472bd5c735303b1ebdeddeea0b2ef1`
- 실제 만료: **2026-10-01 06:18:11 KST** (요청14일과 달리 저장소 상한7일).

### 원본 전달 절차 — Windows

GitHub에 접근 가능한 사용자 PC에서 위 artifact를 내려받아 원본 ZIP 그대로 전달한다. 새 작업 폴더 예:

```powershell
$taskDir = Join-Path $env:USERPROFILE 'Downloads\P1_CICD_01_35921580081_1'
New-Item -ItemType Directory -Force -Path $taskDir | Out-Null
```

다운로드 위치를 위 폴더로 지정한다. 기본 ZIP 파일명은 아래와 같다. 실제 저장한 이름이 다르면 파일명만 맞춘다.

```powershell
$packagePath = Join-Path $taskDir 'candidate-images-bbe349c8815ed2ade3de87a1b312221c34f4361b-1.zip'
if (-not (Test-Path -LiteralPath $packagePath -PathType Leaf)) { throw 'ZIP 파일 경로를 확인하세요.' }
$expected = '22c0952ccafa9dc5d65506f821e5c125179d9bd4b7bcbfcd193dda36d5415eb8'
$actual = (Get-FileHash -LiteralPath $packagePath -Algorithm SHA256).Hash.ToLowerInvariant()
if ((Get-Item -LiteralPath $packagePath).Length -ne 278490138 -or $actual -ne $expected) { throw 'STOP: 원본 ZIP 크기/해시 불일치' }
$sidecarPath = $packagePath + '.sha256'
if (Test-Path -LiteralPath $sidecarPath) { throw '기존 sidecar가 있습니다. 덮어쓰지 말고 확인하세요.' }
$line = $actual + '  ' + (Split-Path -Leaf $packagePath) + [Environment]::NewLine
[IO.File]::WriteAllText($sidecarPath, $line, [Text.UTF8Encoding]::new($false))
'PASS: 원본 ZIP과 sha256 파일을 함께 전달하세요.'
```

이 sidecar는 사용자의 다운로드 후 계산값이다. 원본 Actions artifact 내부 파일이나 서버 생성 sidecar로 표시하지 않는다. 크기·hash 불일치면 후속 검증을 중단하고 원본을 보존한다. ZIP 내부 명령·스크립트를 실행할 필요는 없다.

전체 ZIP 전달이 어려우면 먼저 `transaction-api-report.json`, `result.json`, `SHA256SUMS`, `DB_SHA256SUMS`를 원문 그대로 전달할 수 있다. 이 경우 report 분류는 진행할 수 있으나 **전체 ZIP·이미지·DB 바이트 검증은 미완료**로 유지한다.

## 원본 수신 후 이어갈 작업

1. ZIP 외부 SHA256/size와 내부 파일 목록·경로·종류·checksum을 확인한다. report/result·image·DB·Run/Source 연결을 대조한다.
2. 기존 `tools/precheck/summarize_trivy_findings.py`로 report를 분석하고 HIGH44/CRITICAL0·고유CVE8과 대조한다. 반환하는 fixed_version_reported는 보고서 주장이다. 공식 배포판/upstream 자료와 확인한 뒤 수정 후보를 선택한다.
3. 수정 버전 있음·미기재·예외 심사 필요를 구분해 패키지/CVE별 조치표를 작성한다. 빈 FixedVersion을 영구적인 수정 불가 또는 면제로 해석하지 않는다.
4. 앱 Lead의 최종 Source·시험 범위와 실제 공급 수신 hash/path를 확보한다. 위 null을 사실에 근거해 채우고 후보 base/lock이 바뀌면 해당 해시·검증을 갱신한다.
5. 별도 실행 config 변경에서 현재 config와의 diff를 독립 리뷰한다. 최종 채택 SHA·Lead 판단을 기록한 뒤 on-prem 공급·Podman 검증과 두 이미지 Test→Build→Scan을 진행한다.

## 중단·복구와 다음 단계

누락된 승인·공급값, hash/platform 불일치, 설치/시험 실패, HIGH/CRITICAL 또는 scanner/DB 오류가 있으면 실행 인계는 보류한다. 이번 작업은 현행 입력을 변경하지 않아 운영 rollback이 없다. 초안은 보류하거나 새 PR에서 수정한다.

Push/Deploy·Harbor/Pod Digest·Health/E2E·실패 시 이미지 유지·Token·복구 검증은 별도 후속이다. **APP BLOCK / Runtime AC1/10 / Delivery NOT_VERIFIED 유지.**

