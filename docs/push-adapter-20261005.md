# P1-CICD-01 Push adapter — 2026-10-05

## 상태·범위

Source 구현·로컬 fixture 검증. 독립 코드 리뷰/병합/Runner Runtime 미완료.
기존 main `07f7f26d306056203e057b43bdf5234a2de2b8ae`의 delivery_gate,
OCI 검사기와 scanner를 소비한다. #47 Gate hardening은 별도 PR로 유지하고
기존 Gate의 malformed-input 예외는 adapter 경계에서 fail-closed로 처리한다.
Workflow 변경·credential 발급·Deploy·Health/E2E·rollback은 이 PR에 없다.
새 Runtime 코드이므로 Pre-check 병합 예외가 아닌 독립 기술 리뷰 대상이다.

## 처리 순서

1. 외부에서 검토한 delivery record의 SHA256을 CLI 값과 대조한다. 해시는 승인 그 자체가 아니다.
2. 기존 pre_push 계약에 더해 승인 record의 `app_result_sha256`에서
   APP→BUILD→SCAN receipt 해시를 연결한다. Source/원 Workflow SHA/Run/Attempt/input SHA,
   서비스2개와 archive/manifest/config/report를 실제 바이트로 대조한다.
3. 원본은 변경하지 않고 private 임시 폴더로 OCI를 복사한다. 두 이미지 모두 통과하기 전
   scanner/Harbor 명령을 실행하지 않는다.
4. 실행 모드에서는 **새 Push 실행**의 SHA/Run/Attempt를 별도로 결박한다.
   과거 Run #5가 Push까지 수행했다고 다시 쓰지 않는다.
5. 기존 scan_pair로 fresh DB 검증·Scan을 수행한다. 과거 Scan은 provenance이며
   현재 취약점 판정이 아니다. 새 HIGH/CRITICAL, DB 문제, report 불일치 시 Push 중단.
   원본 Test/Build·CA 설치를 다시 수행하는 단계는 없다.
6. Skopeo `copy --preserve-digests --dest-tls-verify=true`로 고정 Harbor repository에
   고유 `delivery-<uuid>` tag를 만든다. podman image store나 실행 컨테이너는 변경하지 않는다.
7. 각 tag와 digest 주소를 TLS 검증으로 다시 inspect하고 raw manifest SHA256을 대조한다.
   전부 확인된 경우에만 PUSH=PASS. DEPLOY=NOT_EXECUTED, DELIVERY_GATE=NOT_VERIFIED 유지.

## 입력

기존 `delivery_gate.evaluate(..., 'pre_push')` 계약의 record를 사용한다.
필드 예시는 `tools/delivery/test_delivery_gate.py` fixture에 있으나 fixture 값은 운영 입력이 아니다.
추가 필드는 다음과 같다.

- `app_result_sha256`: 승인된 원본 APP result.json 실제 SHA256.
- `push_execution`: `workflow_sha`, `run_id`, `attempt`, `reference`.
  새 Push dispatch의 exact SHA·Run·Attempt 및 321Team 승인 링크다.
  이 값은 원본 `workflow`(Test/Build/Scan Run)와 별개다.
- CLI의 `--record-sha256`은 독립적으로 검토된 record의 정확한 해시.

기록 생성/승인자는 원본에서 값을 연결한다. PASS/승인 reference를 도구가 자동 생성하지 않는다.
Runtime 입력 승인 확인은 운영 절차의 책임이며 이 adapter는 GitHub 승인 진위를 조회하지 않는다.

원본 폴더는 기존 APP workflow 형식을 그대로 사용한다:
`result.json`, `build-scan/result.json`, `build-scan/<service>.oci.tar`,
`build-scan/scan/result.json`, `build-scan/scan/<service>/report.json`.
Run #5의 폴더는 사용자 보고상
`/home/server01_cicd-runner/FDS_CICD_AppWorkflow.1poajpf_`다.
운영 서버 원본을 이 PR 작성 환경으로 복사하거나 실제 재검증한 것은 아니다.

## 실행 환경과 방법

- rootless `cicd-runner01 / server01_cicd-runner / uid1000`.
- 기존 reviewed Trivy binary·fresh DB 접근, Python 및 `/usr/bin/skopeo` 필요.
  **현재 Runner에서 Skopeo 설치·버전·옵션 지원 미확인**. 이 PR은 설치하지 않는다.
- 시스템 CA 신뢰를 사용하며 insecure TLS 옵션을 제공하지 않는다.
- 승인된 Harbor push/pull credential의 기존 파일을 호출자가 제공한다.
  uid 소유·0600·일반 파일·단일 hardlink, symlink 없는 경로여야 한다.
  JSON은 `auths` 아래 `10.1.93.54`의 `auth` 항목만 허용한다.
  helper·다른 registry credential은 거부한다. 발급·login·갱신 기능은 없다.
- 자격증명은 별도 임시 디렉터리에 복사하고 정상/오류/INT/TERM 종료 시 정리한다.
  호출자 소유의 원본 authfile은 삭제하지 않는다. 원본 회수·만료는 기존 소관 절차다.
- 입력 record/authfile/과거 증적은 checkout 밖에 둔다. execution은 clean checkout 및
  기존 runtime_context의 main/workflow_dispatch/current SHA 조건을 요구한다.

로컬 검증(네트워크·Push 없음; 새 증적 폴더와 잠금 파일은 생성):

```bash
python3 tools/delivery/push_images.py \
  --record /approved/path/delivery.json \
  --record-sha256 <검토한-record-SHA256> \
  --evidence-root /path/to/original-app-evidence
```

실행 시에는 같은 인자에 `--execute --authfile /private/path/auth.json`을 추가한다.
그러나 **본 PR은 dispatch Workflow를 추가하지 않는다**. 기존 Run #5 workflow에 몰래
Push를 연결하거나 환경변수를 수동 위조해 runtime_context를 통과시키지 않는다.
독립 리뷰 이후 승인 record 주입·current dispatch 연결·credential provisioning 경로를
확정해야 운영 실행할 수 있다. 예시의 placeholder를 임의 PASS 값으로 채우지 않는다.

## 실패·복구·증적

- 복사 전에 해당 target과 UNKNOWN 상태를 먼저 기록한다. 명령 실패/timeout/INT/TERM은
  원격 쓰기가 전혀 없었다는 뜻이 아니다. `PUSH=INCOMPLETE` 및 서비스별 상태로 보존한다.
- 첫 이미지 PASS 후 두 번째 실패는 첫 결과를 유지한다. Deploy로 진행하지 않는다.
- 원격 tag/blob 삭제나 rollback은 자동 실행하지 않는다. 실패 회차 원본과 registry 상태를
  대조하고 Lead가 잔여 artifact 처분·재시도 범위를 결정한다. 새 실행은 새 tag를 사용한다.
- tag 이름은 UUID로 충돌을 줄이지만 서버 측 create-only/immutable 정책을 강제하지 않는다.
  다른 writer와의 동시성 및 Harbor tag 정책은 Runtime handoff에서 확인한다.
- 결과는 `FDS_CICD_Push.<unique>/result.json`, `SHA256SUMS`, `fresh-scan/`에 남는다.
  raw 인증 명령 stdout/stderr는 저장하지 않는다. fresh scan report는 민감할 수 있으므로
  private 보존하고 그대로 Git/Actions에 업로드하지 않는다.
- 전체 result receipt hash는 실제 저장된 파일에서 계산한다. pre_deploy 입력 편입은 별도
  단계이며 이 PR은 Push 결과로 자동 Deploy 승인을 만들지 않는다.
- 동일 UID 악성 프로세스 방어 sandbox가 아니다. SIGKILL/전원 장애는 정리·최종 봉인을
  보장하지 못한다. private 임시 auth 잔여와 미완료 기록을 확인하고 기존 credential 회수
  절차로 처리한다. 봉인 실패를 PASS로 출력하지 않는다.

## 검증

```bash
PYTHONPATH=tools/delivery:tools/trivy python3 -m unittest \
  discover -s tools/delivery -p 'test_push_images.py' -v
```

정상 pair, 로컬검증 무네트워크, 원본·report·receipt·context 변조, symlink,
malformed digest, 실행 승인 binding, fresh Scan/보고서 실패, auth mode/helper 거부,
원격 manifest 불일치, 두 번째 복사 실패, 중단을 synthetic fixture로 확인한다.
실제 Skopeo·Harbor TLS/auth/copy·DB 다운로드는 이 시험에서 실행하지 않는다.
로컬 실행 결과: adapter 16/16, 기존 delivery Gate 13/13, scanner/OCI 20/20 통과(총49개).
실제 registry/scanner 호출은 fixture/mock이며 Runtime 성공이 아니다.

근거: [Skopeo copy](https://github.com/containers/skopeo/blob/main/docs/skopeo-copy.1.md),
[Skopeo inspect](https://github.com/containers/skopeo/blob/main/docs/skopeo-inspect.1.md),
[기존 delivery 계약](delivery-runtime-gate-20261002.md), [거버넌스](governance.md).
