# 승인 입력을 소비하는 Test·Build·Scan 실행기

[후속 #11](https://github.com/321Team/infra-cicd/issues/11)의 첫 연결 단계다. `tools/delivery/build_scan.py`는 승인 입력과 지정 Source Commit의 사본으로 두 서비스의 Unit/Contract 시험, Podman rootless Build, [PR #10 Scan 어댑터](app-image-scan.md)를 순서대로 호출한다. 아직 실제 앱 시험/Build/Scan은 실행하지 않았다. Push/Deploy 코드는 포함하지 않으며 `DELIVERY_GATE=NOT_VERIFIED`를 유지한다.

## 기본 상태와 승인 경계

`config/app-build-scan.json`은 **PENDING_APP_HANDOFF** 상태다. 관측된 앱 main SHA를 승인값으로 복사하지 않았다. 승인 Commit·승인자·근거·base image digest와 transaction-api Unit은 null이다. 서비스별 setup/Contract 및 fds-engine Unit은 앱 파일에 맞춘 검토 후보를 입력했다. 그대로 실행하면 `APP_HANDOFF_PENDING`, exit 1로 중단한다. 이 경우 Git 명령·앱 코드·Podman·DB/Scan을 호출하지 않는다.

입력 채택은 기존 [운영 기준](governance.md)에 따른다. 앱 담당이 Source/Test 계약을 인계하고 CICD PR에서 그 근거·전체 설정과 실행 변경을 독립 검토한 후 이권욱이 최종 판단한다. 입력의 `APPROVED_FOR_TEST_BUILD_SCAN` 문자열이나 승인 링크 형식은 사람의 승인 진위를 자동 검증하는 장치가 아니다. CLI는 실제 실행에서 설정 바이트가 `GITHUB_SHA`의 Git 객체와 같은지 검사하며, 설정 변경 후 예전 코드 리뷰를 그대로 적용하지 않는다.

이번 변경에는 운영 Workflow를 추가하지 않는다. 기존 Source validation에 신규 Mock/로컬 시험 경로만 추가하므로 PR 생성/갱신과 main push는 GitHub-hosted 소스 CI를 실행한다. 내부 Runner의 Build/Scan Job이 자동으로 시작되지는 않는다.

## 최신 앱 시험 매핑 준비

[앱 시험 매핑과 공급 경로 인계 후보](app-handoff-mapping.md)에 2026-09-16 관측 Source의 중복 없는 16+5+5 함수 배정, API Unit 공백, service별 venv, 공급 경로 후보·현장 수집 절차를 정리했다. 이는 실제 실행 승인이나 시험 PASS가 아니다. config는 PENDING_APP_HANDOFF를 유지한다.

## 앱 담당에게 필요한 구체 입력

| 항목 | 요구 입력 |
|---|---|
| Source | `supreme33-kr/fds-msa`, 승인된 40자리 Commit, 승인자와 GitHub 기록 링크 |
| 서비스별 Build | repository 기준 context/Dockerfile 상대경로, 완전한 registry/repository@sha256 base image |
| 서비스별 Unit/Contract | repository 기준 cwd, argv 배열, timeout_seconds(1~1800), 정상 결과·실패 nonzero 의미와 승인 근거 |
| Setup | 필요한 경우 승인된 의존성 설치/fixture 준비 argv. 불필요하면 빈 배열 |
| 호환성 | 두 서비스 업무·실패/DB 계약의 기존 승인 범위와 시험 의미 |

각 `unit`/`contract`와 `setup` 원소의 구조는 다음과 같다. 아래는 **형식 예시**이며 실제 앱 실행 명령·승인값이 아니다.

```json
{
  "cwd": "transaction-api",
  "argv": [".venv/bin/python", "-m", "pytest", "tests/unit"],
  "timeout_seconds": 300
}
```

명령은 shell 문자열이 아닌 argv 배열로 전달한다. 환경 변수 치환, `&&`, pipe 등을 자동 해석하지 않는다. 필요한 복합 절차는 앱 담당이 승인 Source 안의 스크립트로 제공하고 전체 동작을 검토한다. exit 0만으로 시험의 의미/커버리지가 입증되는 것은 아니므로 `true`/시험 생략/실패를 숨기는 wrapper를 승인 명령으로 채택하지 않는다. Secret 값·운영 DB 접속정보를 argv나 이 설정에 넣지 않는다. 현재 실행기는 전용 test credential/provisioning 주입을 구현하지 않았으므로 그런 입력이 필요한 시험은 별도 검토 후 연결한다.

현재 관측 Source `01758583c03bcbf45c96eb9d7b868cee4a2ed9df`의 두 Dockerfile은 `FROM python:3.12-slim`이다. 이 실행기가 지원하는 것은 **단일 literal FROM과 승인 digest가 일치하는 Dockerfile**이다. 앱 담당과 base image 공급 경로·digest pin 변경을 검토해야 하며, CICD에서 Dockerfile을 몰래 치환하거나 임의 digest를 선택하지 않는다. multi-stage/ARG 기반 FROM·다른 architecture는 지원 확대 검토가 필요하다. 이 지원 범위는 새 업무 AC를 추가하는 것이 아니다.

## 실제 호출 순서

1. 입력 완결성 확인. 실제 실행에서는 기존 `runtime_context`의 infra-cicd/main, workflow_dispatch, cicd-runner01, UID 1000, 사용자, checkout SHA 조건과 설정 Commit 일치를 추가로 확인한다. Python 3.11 이상이 필요하다.
2. 사전 checkout된 앱의 HEAD와 승인 SHA, origin repository를 대조한다. checkout/인증 자체는 미래 중앙 Workflow에서 승인된 읽기 전용 수단으로 제공한다. Token·앱 승인값을 이 도구가 생성하지 않는다.
3. `git archive <승인 SHA>`로 Source 사본을 만들고 해시를 기록한다. working tree 수정/미추적 파일을 넣지 않고 Git replace object를 비활성화한다. submodule, symlink, 특수파일, 경로 이탈, 중복 경로를 거부한다. 현재 source archive 지원 한도는 10,000개 member/512MiB다.
4. 별도 test 사본에서 setup → 두 Unit → 두 Contract를 모두 성공시킨다. 각 step은 제한 시간·nonzero·SIGINT/TERM을 실패 처리한다. timeout/상위 중단 시 직접 시작한 프로세스 그룹을 종료한다. 로그는 private 파일로만 보관한다.
5. 같은 archive로 새 Build 사본을 만들어 test 생성 파일이 섞이지 않게 한다. Podman 6.1.0·실제 rootless=true 확인 후 각 승인 base digest를 TLS 검증으로 Pull한다. Run 전용 HOME, XDG_RUNTIME_DIR, storage/runroot와 빈 authfile을 사용한다. Runner의 기존 이미지 store·로그인 파일을 대상으로 prune/reset/delete를 실행하지 않는다.
6. `podman build --format oci --platform linux/amd64 --pull=never --iidfile ...`로 Build한다. `podman save --format oci-archive <image ID>`를 호출하고 실제 archive config digest와 iidfile을 비교한다. 실패하면 다음 단계로 넘어가지 않는다.
7. 두 archive가 모두 준비된 뒤 같은 Run/Attempt의 `scan_pair()`를 호출한다. PASS와 취약점 BLOCK 모두 Source/Run, 실제 검사한 이미지의 archive/config/manifest identity를 Build와 대조한다. PASS는 두 이미지 모두 PASS/rc0이어야 한다. BLOCK은 첫 이미지 BLOCK/rc10 또는 첫 이미지 PASS/rc0·두 번째 BLOCK/rc10만 허용하며, `HIGH_CRITICAL_FINDINGS` 사유를 확인한다. 조기 종료로 검사하지 않은 이미지를 PASS로 간주하지 않는다. 두 Build archive의 바이트 해시는 미검사 이미지까지 다시 확인한다. 일치하는 취약점 증적은 전체 BLOCK/rc10, 불일치·누락·scanner/DB 실패는 전체 FAIL/nonzero다. 성공해도 Push/Deploy 승인으로 사용하지 않는다.

Test/Build용 자식 환경에는 GitHub Token, kubeconfig, 상위 Trivy/Podman override 등을 넘기지 않는다. 기존 proxy 변수는 공급 경로 유지를 위해 허용한다. 실제 네트워크 정책을 우회하거나 rootful·host network·insecure TLS로 실패를 해결하지 않는다. 이미지/패키지 공급과 rootless Podman의 실제 환경 호환성은 운영 검증 대상이다.

이 방식은 신뢰된 승인 소스를 실행하는 절차이며 같은 UID의 악성 코드에 대한 보안 sandbox가 아니다. 실행 소스는 Runner HOME/공유 파일 접근 권한을 가진다. 따라서 영속 Runner에서 신뢰하지 않은 PR/미승인 Source를 실행하지 않는 기존 경계를 유지한다. SIGKILL·전원 단절·자체 session을 만든 외부 프로세스까지 정리를 보장하지 않는다.

## 증적과 후속

`result.json`은 실제 Source SHA/설정 해시·Workflow SHA·Run/Attempt, 단계 종료 코드/로그 해시, 두 이미지 config/manifest/archive 해시와 Scan 결과 해시를 연결한다. raw log/OCI/소스 사본에는 민감정보가 있을 수 있으므로 자동 artifact 업로드나 콘솔 출력 없이 private 디렉터리에 보존한다. 최상위 SHA256SUMS는 result.json을 검증하고, result 안의 해시가 각 단계 로그와 중첩 Scan/DB 증적을 연결한다. 이것은 서명·원격 승인 증명이나 배포 허가 토큰이 아니다.

검증을 통과한 Scan receipt에는 최상위 `scan_status`를 기록한다. 첫 서비스에서 취약점으로 중단하면 `transaction-api=BLOCK`, `fds-engine=NOT_SCANNED`다. 두 번째에서 중단하면 `transaction-api=PASS`, `fds-engine=BLOCK`이다. `images`는 Build한 이미지들의 identity이며 모든 이미지가 Scan을 마쳤다는 뜻이 아니다. receipt 검증에 실패하면 `BUILD_SCAN=FAIL`이며 검증된 `scan_status`를 기록하지 않는다.

별도 Podman store와 이미지는 후속 전달/오류 조사 때문에 자동 삭제하지 않는다. 실행 전 디스크 여유, 실행 후 잔여 Build 자원 및 본 Run 디렉터리만 대상으로 하는 정리·보존 절차를 운영 인계에서 확정한다. 실패 시 자원 정리 필요 여부를 별도 확인하며 기존 Runner store 전체를 정리하지 않는다.

실제 운영 Workflow 연결은 App 인계와 이 실행기 독립 리뷰 후 수행한다. 다음 단계의 Push에는 Harbor TLS/Runner trust·CI Robot, Deploy에는 K8s/RBAC/Token/Worker Pull·승인 Digest/복구 인계를 요구한다. 로컬 Test/Build/Scan 준비를 모든 Harbor/K8s 인수 완료까지 묶지는 않는다. 실제 Push/Deploy 음성시험과 AC 인수는 아직 수행하지 않았다.

입력만 점검하는 기본 명령(현재 설정에서는 의도적으로 실패):

```bash
python3 tools/delivery/build_scan.py
```

`--execute --source-dir <승인 SHA checkout>`는 향후 검토된 중앙 Workflow에서 사용하는 인터페이스다. 현재 설정/이번 PR만으로 내부 Runner에서 실행하라는 지시가 아니다.

검증: 로컬 Git fixture의 Commit snapshot, dirty/untracked/test-output 분리, 명령 프로세스·그룹 timeout, argv 비해석은 실제 임시 자원으로 검사한다. PASS/BLOCK의 Source·Run·세 identity 불일치, 잘못된 검사 범위/상태, 조기 종료 후 미검사 archive 변경을 회귀시험한다. 두 시험 모듈은 `tools/trivy/oci_test_fixtures.py`의 합성 OCI 생성기를 공유한다. Podman/Trivy 호출은 Mock이며 실제 앱 빌드 성공을 주장하지 않는다.

참고: [Podman Build 옵션](https://docs.podman.io/en/stable/markdown/podman-build.1.html), [Podman global root/runroot](https://docs.podman.io/en/stable/markdown/podman.1.html), [OCI archive 저장](https://docs.podman.io/en/stable/markdown/podman-save.1.html).
