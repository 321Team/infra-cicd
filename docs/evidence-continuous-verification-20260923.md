# P1-CICD-01 지속 검증과 기존 Run 증적 회수

작성 기준: 2026-09-23 KST / Lead 이권욱 / CICD main c6f461334c7d8f352e3671fc225d87ea5d0c868f.
책임: [CICD #11](https://github.com/321Team/infra-cicd/issues/11), [baseline #16](https://github.com/321Team/fds-project-baseline/issues/16).

## 현재 단계와 이번 변경

09-23 21:23:34 KST의 취약점 요약 수집·회수·hash 대조는 완료했다.
[수신 결과](https://github.com/321Team/infra-cicd/issues/11#issuecomment-5794924337):
CRITICAL3/HIGH57 기록·고유CVE24. 기존 Run35683010080/Attempt1의 transaction-api는 BLOCK, fds-engine은 NOT_SCANNED다.
이번에는 기존 app_evidence.py collect/verify 명령에 실제 Run의 외부 대조값·경로를 대입하고 반복 검증 순서를 문서화했다.
운영 코드·Workflow·config·보안 게이트 변경은 없다. 이 문서의 명령은 아직 서버에서 실행하지 않았다.

app_evidence.py를 포함한 실행 모듈 7개의 Git blob은 실제 Run SHA7e939d7과 위 main에서 동일하다.
이번 준비에서 해당 고정 소스의 기존 회귀시험18개를 로컬에서 통과했다.
시험은 합성 영수증·임시 OCI/tar 바이트를 사용한 검증이며 실서버 증적 검증이 아니다.
기존 병합/리뷰 이력은 #19/#21/#24/#26을 따르고, 이번 문서 준비를 새 독립 코드 리뷰라고 표시하지 않는다.

## 1. 이번 Run: 실서버에서 1회 수집

대상은 cicd-runner01 / server01_cicd-runner / UID1000 / Python3.11 이상이다.
기존 Run의 checkout 및 원본 EVIDENCE_DIR가 그대로 남아 있어야 한다.
아래 전체 블록을 해당 서버의 Bash 터미널에서 실행한다. 파일을 추가 전송할 필요는 없다.

기존 도구는 원본 receipt·로그·Source archive·OCI·report·DB의 연결과 바이트를 읽는다.
앱/Podman/Trivy/Git fetch·패키지 설치·서비스 변경은 하지 않는다.
원본 파일 검증에는 디스크 I/O가 있으므로 새 Run 또는 진단상 필요할 때 1회 수행하며 매시간 반복하지 않는다.

```bash
(
  set -euo pipefail
  umask 077
  test "$(hostname -s)" = cicd-runner01
  test "$(id -un)" = server01_cicd-runner
  test "$(id -u)" = 1000

  cicd_checkout=/home/server01_cicd-runner/actions-runner/_work/infra-cicd/infra-cicd/cicd-app-35683010080-1
  cicd_run_dir=/home/server01_cicd-runner/FDS_CICD_AppWorkflow.log3xwta
  cd "$cicd_checkout"
  test "$(git rev-parse HEAD)" = 7e939d7f77c38c13fd331b38f6649580bf0d05bd

  sha256sum --strict -c - <<'SHA256'
644362b79e89362dd6343cd8c973951d38523f39f8625c260e29ffc199e4308f  tools/delivery/app_evidence.py
138e627b391de3e5bdfded5e45837d6bc3666d1a0afe14d42a0530d841057511  tools/delivery/build_scan.py
433fca01ede1b1903d4b8302e7423b9db3649a9c4a0cf7d6faf197b25bf2e3ea  tools/delivery/offline_supply.py
8001c623a96acb3535e752843dedb31d2dc8f4ec17218ba5746c2bb38773abd9  tools/trivy/prepare_db.py
3c8701df879f1fc02494d1400515fef58f79adb1dbab32b4196f961c0fad3168  tools/trivy/scan_images.py
228cfbf9604ea67cafa751107b7f98b69002ff5b862737ed2bc64f44960948b5  tools/trivy/verify_runner.py
1518708335eac693c8564ae00bc1f88ba4ad562b5779e4a35f7d3f481e837602  tools/trivy/oci_archive.py
ea375a00811923cfb415e6c851ceed55227b5978835953eb2227b03552ab5728  config/app-build-scan.json
SHA256

  test -f "$cicd_run_dir/result.json"
  test -r "$cicd_run_dir/result.json"
  cicd_case_dir="$(mktemp -d /home/server01_cicd-runner/CICD_Evidence35683010080_1.XXXXXX)"
  printf 'WORK_DIR=%s\n' "$cicd_case_dir"
  date --iso-8601=seconds | tee "$cicd_case_dir/started-at.txt"

  cat > "$cicd_case_dir/expected.json" <<'JSON'
{
  "workflow_sha": "7e939d7f77c38c13fd331b38f6649580bf0d05bd",
  "source_sha": "ec69b67067decf35abbfed0a6a99f3073132787b",
  "run_id": "35683010080",
  "attempt": 1,
  "input_sha256": "ea375a00811923cfb415e6c851ceed55227b5978835953eb2227b03552ab5728",
  "source_repository": "321Team/fds-msa"
}
JSON

  sha256sum "$cicd_case_dir/expected.json"
  /usr/bin/python3 -E -s -B tools/delivery/app_evidence.py collect \
    --run-dir "$cicd_run_dir" \
    --output-dir "$cicd_case_dir/export" \
    --expected "$cicd_case_dir/expected.json" \
    | tee "$cicd_case_dir/collect.log"

  (
    cd "$cicd_case_dir/export"
    sha256sum --strict -c app-evidence.tar.gz.sha256
    sha256sum app-evidence.tar.gz
  )
  date --iso-8601=seconds | tee "$cicd_case_dir/finished-at.txt"
  printf 'RETURN_ARCHIVE=%s/export/app-evidence.tar.gz\n' "$cicd_case_dir"
  printf 'RETURN_SHA256=%s/export/app-evidence.tar.gz.sha256\n' "$cicd_case_dir"
)
```

-E/-s는 Python 환경 변수와 사용자 site 경로를 제외하고 -B는 bytecode 작성을 막는다.
기존 모듈은 스크립트 디렉터리 import를 사용하므로 이 명령에 -I를 추가하지 않는다.
새 작업 디렉터리의 expected·시각·출력 로그·export만 작성한다. checkout/원본 Run/기존 결과는 수정하지 않는다.

### 정상·실패·회신

정상 수집은 exit0이며 JSON 출력에 다음 값이 있어야 한다.

| 필드 | 이번 Run의 예상값 |
|---|---|
| EVIDENCE_CHECK | PASS |
| RECORDED_APP_WORKFLOW | BLOCK |
| BINDING | COMPLETE |
| SOURCE_BINDING | MATCH |
| RAW_PAYLOAD_CHECK | LOCAL_BYTES |
| SOURCE_APPROVAL | NOT_INDEPENDENTLY_VERIFIED |
| DELIVERY_GATE | NOT_VERIFIED |

유효한 BLOCK 증적도 EVIDENCE_CHECK=PASS다. 앱 취약점 해소 또는 Delivery PASS로 바꾸지 않는다.
다른 값은 원본과 대조해 조사하며, 예상값을 보고서에 맞춰 고치지 않는다.

명령 실패·hash 불일치·checkout/원본 누락·EVIDENCE_CHECK=FAIL이면 그대로 중단한다.
도구는 상세 입력값을 오류에 노출하지 않으므로 오류 화면과 WORK_DIR(생성된 경우)를 회신한다.
누락 파일/영수증을 새로 만들거나 원본 report를 수정하거나 앱을 재실행해 이 Run의 증적을 대체하지 않는다.
생성된 작업 디렉터리는 진단용으로 보존한다. 서비스 변경이 없어 서비스 복구 명령은 필요 없다.

정상 후 RETURN_ARCHIVE와 RETURN_SHA256 두 파일, 종료 화면을 전달한다.
수신자는 화면의 archive SHA256을 별도 기준으로 삼아 [verify 절차](app-evidence.md#수신-측-대조)를 실행한다.
archive는 raw 로그·Source·OCI·report·DB를 포함하지 않지만 승인 근거·시험 명령 등 JSON 메타데이터가 있으므로 공유 전에 내용을 확인한다.
원본 report 전체는 전달하지 않는다.

## 2. 수신 후 반드시 대조할 항목

- 서버 collect는 실제 원본 바이트를 확인하고, 수신 verify는 전송 archive 및 제출 hash 목록을 검사한다.
  수신 결과 RAW_PAYLOAD_CHECK=NOT_INCLUDED_SERVER_RECORD_ONLY는 정상적인 범위 제한이다.
- 수신된 실행 기준은 위 expected 6필드와 대조한다. 영수증 자기 선언을 새 외부 기준으로 복사하지 않는다.
- transaction-api report의 영수증/manifest hash를 요약의 source_report_sha256과 대조한다:
  a9a0c6eff7961d95af7fb3a5f51093568e0ce50ab03c86859733341b0260769c, 565430bytes.
- 이미 회수한 요약 JSON 자체 hash는 bd8a38006307e9a716826e8fa04be4d5be825548863266081c38d523b8f12f48, 18337bytes다.
- scan receipt의 counts와 요약의 CRITICAL3/HIGH57이 맞는지, build 이미지 digest와 scan 이미지 digest가 연결되는지 확인한다.
- 첫 이미지 BLOCK 뒤 fds-engine NOT_SCANNED를 보존한다. 이미지2개 Build와 이미지2개 Scan을 구분한다.
- DB 만료는 수집일이 아닌 원래 Scan 시각 기준이다. 최신 취약점 재검사에는 별도 새 Run과 그때의 DB 증적이 필요하다.
- 기존 SOURCE 승인 근거·해당 코드의 독립 리뷰·사람의 최종 판단은 별도 확인한다.
  수신 PASS만으로 fds-project-evidence 편입·AC 인수·티켓 Done을 선언하지 않는다.

## 3. 이후 매 Run의 반복 검증

| 시점 | 검증과 기록 |
|---|---|
| 앱/운영 입력 변경 | 새 승인 Source SHA, requirements·lock·Dockerfile·base digest·공급 archive/manifest hash 및 변경 범위 리뷰 |
| 실제 실행 시작 | GitHub의 Run/Attempt/workflow SHA와 그 Commit의 config 원본 bytes로 expected를 새로 확정 |
| 실행 종료 | 새 EVIDENCE_DIR에서 collect를 1회 수행. 새 작업 디렉터리 사용, PASS/BLOCK/FAIL 모두 실제 기록 보존 |
| 수신/대조 | 전송 hash·같은 expected·report/이미지/DB/시험 연결 확인. 생략된 raw payload 검증 한계 표시 |
| 인수/증적 | 관련 AC에 실제 충족 부분만 매핑하고 evidence PR의 독립 리뷰 및 Lead 판단을 기록 |

위 1절은 Run35683010080/Attempt1에만 적용한다. 다음 Run에서 이 expected를 재사용하지 않는다.
새 Run의 기준 6필드를 채우는 [기존 양식](../config/app-evidence.expected.example.json)을 사용하고 source_repository를 명시한다.
기존 검증 코드가 바뀌면 새 대상 SHA와 import 모듈의 검토·hash 범위를 다시 확인한다.
변경 없는 과거 성공 점검을 반복해 새 증적으로 집계하지 않는다.

## 4. 병행 준비: 앱·base·공급물

권장안은 앱 FastAPI/Starlette 호환 조합과 패치 base 후보를 함께 검토하고, 새 입력을 새 공급 디렉터리로 인수하는 것이다.
기존 공급물을 덮어쓰거나 현재 서버에서 즉석 패키지 업데이트로 Run 입력을 바꾸지 않는다.

| 책임 | 새 입력에 필요한 근거 |
|---|---|
| 앱 Lead 이재환 | 두 서비스의 requirements/lock4개, Dockerfile, Unit/Contract 결과와 범위, 최종 승인40자리 SHA |
| CICD·공급 담당 / 앱 협의 | base 공급 출처·linux/amd64·manifest/config digest·OCI hash, 네 프로필 wheel/lock/inventory/roots hash, Python/ABI 호환 |
| 해당 Lead | 미패치 8개 CVE의 실제 영향·불필요 패키지 축소 가능성·후보 이미지 결과, 필요 시 CVE/Image Digest/사유/만료/승인자의 개별 판단 |
| CICD | 승인 입력 config PR·독립 리뷰 후 두 이미지 실제 Test/Build/Scan, 증적 제출. Push/Deploy는 별도 구현·인계·검증 |

특정 base 또는 라이브러리 버전을 이번 문서로 승인하지 않는다.
수정 버전 미기재는 자동 면제 조건이 아니다. 운영 입력 수정과 보안 게이트 완화는 이 문서의 범위가 아니다.

## 5. GitHub 변경 점검

GitHub 원본을 매시간 읽기 전용으로 조회하고, 실질 변경·새 차단·후속 진행 조건이 달라질 때만 사용자에게 알리는 점검을 등록했다.
범위는 CICD #11·관련 PR/Run, 앱 CICD 입력, baseline #16·중앙 정책, P1-CICD-01 증적이다.
실서버 collect와 앱 Test/Build/Scan은 이 주기 점검에서 실행하지 않는다.
변경 알림은 실제 서버 검증이나 독립 리뷰를 대신하지 않는다.
