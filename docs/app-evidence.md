# 앱 Runtime 증적 회수·대조

`tools/delivery/app_evidence.py`는 중앙 앱 Workflow가 종료된 뒤 기존 실행 디렉터리를 읽는 도구다. 앱·Git fetch·Podman·Trivy를 실행하지 않는다. 기존 파일·서비스·권한을 수정하거나 자동 업로드하지 않는다. **도구 준비와 시험은 실제 앱 Runtime 성공이 아니다.**

## 대조 기준 준비

[expected 양식](../config/app-evidence.expected.example.json)을 새 파일로 복사하고 아래 다섯 값을 모두 채운다. null 양식은 실행되지 않는다. 수신 증적의 자기 선언만 복사하지 말고 검토한 Commit·config 원본과 실제 GitHub Run에서 독립적으로 확인한다.

| 필드 | 외부 대조 기준 |
|---|---|
| workflow_sha | 실제 실행한 infra-cicd main Workflow의 전체 40자리 Commit |
| source_sha | 최종 승인 config에 기록된 앱 전체 40자리 Commit |
| run_id | 실제 GitHub Actions Run ID, 숫자로 된 문자열 |
| attempt | 해당 Run의 실제 시도 번호, 양의 정수 |
| input_sha256 | 위 Workflow Commit의 `config/app-build-scan.json` 원본 바이트 SHA-256 |

증적 도구 자체도 검토한 CICD checkout에서 사용한다. 수신 manifest의 tool hash는 식별값이며 서명이나 사람의 승인 인증이 아니다. 이 도구는 schema2 중앙 앱 실행의 종료 영수증을 대상으로 하며 `--source-dir`의 사전 HANDOFF_CHECK 결과는 Runtime 증적이 아니다.

## 서버에서 회수

실제 앱 실행 종료 후 확인된 EVIDENCE_DIR를 사용한다. 아래 경로는 예시다. 현재의 미승인 앱 config로 실행을 시작하라는 지시가 아니다. `/usr/bin/python3` 3.11 이상과 저장소의 기존 Python 모듈을 사용한다.

```bash
(
  set -euo pipefail
  umask 077
  cd /path/to/reviewed-infra-cicd-checkout
  /usr/bin/python3 -B tools/delivery/app_evidence.py collect \
    --run-dir /home/server01_cicd-runner/FDS_CICD_AppWorkflow.ACTUAL_ID \
    --output-dir /home/server01_cicd-runner/AppEvidence.NEW_ID \
    --expected /path/to/confirmed-expected.json
)
```

출력 디렉터리는 새 경로여야 한다. 기존 경로가 있으면 덮어쓰지 않고 실패한다. 종료 result → handoff → Build → Scan → DB의 hash와 Run/Attempt/config/Source 연결을 검사한다. 연결된 원본 명령 로그·Source archive·OCI·Trivy report·DB 바이트도 서버에서 검사한다. OCI manifest/config/layer 연결 및 report의 이미지·검사 범위·HIGH/CRITICAL 수를 대조한다. DB 유효성은 수집 시각이 아닌 원래 검증/Scan 시각을 기준으로 검사한다.

기본 회수본은 `app-evidence.tar.gz`와 SHA-256 sidecar다. archive에는 허용한 result JSON·handoff·DB metadata 및 파일 hash/size 목록만 포함한다. raw 로그·Source·wheel·이미지·DB·Trivy report는 포함하지 않는다. **JSON의 승인 근거·시험 명령 등 메타데이터를 자동 비밀정보 제거한 파일로 취급하지 않는다.** 공유 전 내용을 확인하고, 원본은 서버에 보존한다.

## 수신 측 대조

전달 경로에서 별도로 확인한 archive hash와 동일한 외부 expected 파일로 검사한다. archive는 파일시스템에 압축 해제하지 않으며 비허용 경로·link·중복·과대 파일을 거부한다. 압축 바이트는 32MiB, 해제한 tar 전체는 32MiB+64KiB로 먼저 제한하여 tar metadata·padding도 제한을 우회하지 못하게 한다.

```bash
python3 -B tools/delivery/app_evidence.py verify \
  --archive /path/to/app-evidence.tar.gz \
  --sha256 CONFIRMED_64_HEX_SHA256 \
  --expected /path/to/confirmed-expected.json
```

| 출력 | 의미 |
|---|---|
| EVIDENCE_CHECK=PASS / exit 0 | 증적의 요구 연결과 대조 조건 충족. 앱 성공 판정과 별개 |
| RECORDED_APP_WORKFLOW=PASS/BLOCK/FAIL | 영수증에 기록된 원래 실행 결과. 유효한 실패 증적도 회수 가능 |
| BINDING=PARTIAL_FAILURE_RECORD | 실패 때 실제 남은 연결까지만 검사. 미수행 단계는 성공으로 만들지 않음 |
| RAW_PAYLOAD_CHECK=LOCAL_BYTES | 서버 collect에서 연결된 원본 바이트 검사 |
| RAW_PAYLOAD_CHECK=NOT_INCLUDED_SERVER_RECORD_ONLY | 수신 verify는 생략된 원본을 재검사하지 못하며 제출된 hash 목록을 대조 |
| SOURCE_APPROVAL=NOT_INDEPENDENTLY_VERIFIED | 승인 선언의 진위와 최신 독립 리뷰는 별도 확인 |
| DELIVERY_GATE=NOT_VERIFIED | Push·Deploy·전체 Delivery 완료를 판정하지 않음 |

첫 이미지가 취약점으로 BLOCK이면 두 번째는 NOT_SCANNED로 보존한다. PASS에는 두 이미지와 필수 Unit/Contract/Build/DB/Scan 연결이 모두 필요하다. 강제종료로 최종 result가 없거나 참조 파일이 훼손된 경우 회수를 거부한다. 실패 단계 원본은 서버에서 별도로 진단하며 누락 파일을 만들어 PASS로 바꾸지 않는다.

## 검증 범위

합성 실행 영수증과 실제 임시 파일/OCI/tar 바이트로 회수·수신 왕복, 정상/첫 번째·두 번째 BLOCK/초기 FAIL, 외부 기준 불일치, 하위 hash·Source·로그·이미지·DB·보고서 변조, symlink·경로 이탈, Unit 누락, 기존 출력 보존, 원래 DB 만료시각, 연결되지 않은 archive 내용을 시험한다. 이 시험은 실제 앱 Runtime 또는 원본 없는 수신 archive의 실행 진위를 보장하지 않는다.
