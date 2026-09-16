# 앱 이미지 Scan 구현과 인계

## 현재 범위

`tools/trivy/scan_images.py`는 실제 설치된 Trivy를 호출하는 두 이미지 검사 어댑터다. `transaction-api`와 `fds-engine`을 모두 검사하고 하나라도 실패하면 전체가 nonzero로 종료된다. 새 운영 Workflow, Test/Build 실행기, Harbor Push 또는 Kubernetes Deploy 명령은 이번 변경에 없다. 로컬 fixture/Mock 시험 성공을 실제 앱 Scan이나 배포 차단 증적으로 집계하지 않는다. `DELIVERY_GATE=NOT_VERIFIED`를 유지한다.

기준은 정본 [docs-v3.1.18 P1-CICD-01](https://github.com/321Team/fds-project-baseline/blob/docs-v3.1.18/tickets/v3.1.18/descriptions/P1-CICD-01.md), [09-14 HIGH·CRITICAL 결정](https://github.com/321Team/fds-project-baseline/issues/16#issuecomment-5659051242), [채택 DB 운영안](trivy-update-policy.md)이다. v3.1.19는 후보이며 발행 완료로 간주하지 않는다. `config/p1-cicd-01.json`의 나머지 과거 구성 상태 동기화는 기존 Draft PR #5 범위다.

## 검사 동작

1. 승인된 main의 수동 Run에서 기존 UID/사용자/호스트/checkout SHA 검사를 통과해야 CLI가 동작한다. Python 3.11 이상과 기존 고정 Trivy 0.74.0 root:root 0755·SHA를 사용한다.
2. 두 Build 산출물을 새 private 증적 디렉터리에 복사한다. 각각 단일 linux/amd64 OCI archive이어야 한다. 경로 이탈·링크·중복 member/JSON key·외부 blob·여러 이미지·혼합 Docker archive를 거부한다. manifest/config/layer blob의 descriptor 크기와 SHA-256을 검증한다. 압축된 tar 전체나 multiarch index는 이 어댑터의 지원 범위에 없다.
3. 같은 프로세스/Run/Attempt에서 `prepare_db.prepare()`를 호출해 새 전용 cache에 공식 DB를 받는다. 과거 preflight Run 35061666680의 cache/result를 입력으로 받지 않는다. DB 오류·해시 변경·`NextUpdate` 도달은 실패다. 두 Scan 전후와 최종 반환 전에 재검사한다.
4. OCI archive를 `trivy image --input`으로 검사한다. OS+library, HIGH+CRITICAL, `--exit-code 10`, `--list-all-pkgs`, DB/Java DB 자동 갱신 생략, offline 분석을 명시한다. 상위 환경의 `TRIVY_*`와 Actions 자격증명을 넘기지 않는다. 빈 config/ignore 파일을 사용하고 미수정 취약점도 포함한다. 프로젝트 `.trivyignore`나 승인되지 않은 예외를 상속하지 않는다.
5. JSON의 Trivy 버전·이미지 config ID·DiffIDs·OS/Python 패키지 목록을 확인한다. 현재 확인된 두 Python 앱에 맞춘 지원 범위다. 빈 결과, OS 또는 Python inventory 누락, malformed JSON, 수정/억제된 findings, 종료 코드와 findings 불일치는 실패다. 다른 언어·scratch/distroless 이미지는 별도 검토 후 지원한다.
6. 검사 전후 archive·DB와 두 report의 해시를 확인한다. HIGH/CRITICAL은 exit 10, 도구/DB/입력/timeout/신호 오류는 nonzero, 두 이미지 모두 정상이고 차단 finding이 없을 때만 exit 0이다. CLI의 SIGINT/TERM은 성공으로 복귀하지 않는다. 동일 UID의 악성 프로세스와 OS 관리자까지 격리하는 보안 경계는 아니므로 기존 전용 Runner·신뢰된 소스 원칙이 필요하다.

| 출력/식별자 | 의미 |
|---|---|
| `IMAGE_SCAN=PASS` | 이 실행에서 두 archive의 위 검사 조건 충족 |
| `archive_sha256` | OCI tar 파일 바이트의 해시 |
| `manifest_digest` | OCI manifest blob의 digest; 이후 Registry에서 동일성 확인 필요 |
| `config_digest` | 이미지 config ID; Registry manifest digest 대용 불가 |
| `source_sha`, `source_approval=NOT_VERIFIED` | 호출자가 선언한 SHA; Source 승인·Test·Build 입증 아님 |
| `PUSH/DEPLOY=NOT_EXECUTED` | 이 어댑터는 두 작업을 실행하지 않음 |
| `DELIVERY_GATE=NOT_VERIFIED` | 전체 Delivery Gate 인수 미완료 |

raw JSON에는 이미지 config의 환경값 등 민감정보가 포함될 수 있다. 원본 archive/report는 Runner의 umask 077 새 디렉터리에 유지하고 stdout/자동 artifact에 올리지 않는다. `result.json`, report 해시, 중첩 DB 증적을 이용해 비밀값 제거 후 별도 인계한다. scanner stdout/stderr도 출력하지 않으므로 상세 장애 분석은 운영자가 private 증적에서 수행한다. `result.json`이나 `SHA256SUMS`는 서명된 승인 토큰이 아니며 저장된 PASS만 읽어 Push/Deploy를 허용해서는 안 된다.

## 다음 연결 순서와 필요한 입력

2026-09-16 확인한 앱 main은 `supreme33-kr/fds-msa@01758583c03bcbf45c96eb9d7b868cee4a2ed9df`다. 이는 관측값이며 승인 Source SHA가 아니다. 두 Dockerfile은 `python:3.12-slim`과 각 requirements.txt를 사용한다. 기존 앱 CI는 HIGH 보고 전용, latest 태그, k3s/fds 및 광범위 apply를 사용하므로 그대로 연결하지 않는다.

| 인계 | 필요한 값/증거 | 담당 경계 |
|---|---|---|
| Source/Test/Build | 승인 저장소·40자리 SHA·검토 근거, 두 서비스 Unit/Contract 명령·예상 결과, Dockerfile/context, base image digest와 호환성 | 앱 담당 ↔ CICD |
| Harbor | 새 leaf 서비스 적용, Runner CA Trust, 승인 repository, Push+Pull Robot 유효기간/발급·회수, Worker Pull 증거 | Harbor Lead 이하영 / CICD 연계 이권욱 |
| K8s/배포 승인 | fds-app 두 기존 Deployment의 container명, SA/RBAC·Token 인계, 승인 Run/Attempt·Source/Workflow SHA·두 Digest·이전 Digest, Health/E2E·복구 경로 | K8s/SEC ↔ CICD |

공개 인증서와 키 일치 검증은 09-15 Harbor 서버에서 완료되었다. 서버 재전송을 선행 조건으로 되돌리지 않는다. 서비스 TLS 적용과 Runner/Worker Trust는 별도 런타임 증거가 필요하다.

입력이 인계되면 독립 리뷰한 중앙 Workflow에 **승인 Source 확인 → Unit/Contract → rootless Build → 이 Scan → Push → manifest 동일성 확인 → 승인된 기존 두 Deployment patch → Health/E2E** 순서로 연결한다. 원본 archive와 검사한 manifest가 달라지는 재빌드/태그 교체를 허용하지 않는다. Push 시 형식/압축 변환으로 manifest가 달라지면 배포를 차단하고 원인을 해결하며, config ID나 tar hash를 대용하지 않는다. 두 이미지 Scan 완료 전 어느 이미지도 Push하지 않는다.

Scan step에 `continue-on-error`, `|| true`, 실패를 덮는 exit 0을 사용하지 않는다. Push/Deploy job은 성공한 선행 job의 `needs`와 승인·동일성 검사를 모두 요구하고, `always()` 등으로 실행 경로를 우회하지 않는다. 원본 테스트 실패/취약점/DB·스캐너 오류/timeout/중단 상황에서 **실제 Push·Deploy 미실행 및 기존 배포 Digest 유지**를 시험해야 런타임 차단 검증이 된다. 이번 Mock 시험에는 이러한 실물 증거가 없다.

CVE·Digest·사유·만료·승인자를 묶는 예외 집행기가 구현되기 전에는 이 어댑터가 모든 HIGH/CRITICAL을 차단한다. 임의 ignore로 통과시키지 않는다. Draft PR #3의 입력 선언형 모의 Gate도 HIGH·DB·실제 산출물 연결을 보완하고 독립 재검토해야 한다.

## 소스 검증

기존 GitHub-hosted Source validation이 `tools/trivy/test_*.py`를 발견하므로 Workflow 트리거 변경 없이 시험한다. PR 생성/갱신은 GitHub-hosted CI를 실행할 수 있으나 내부 Runner·앱·Harbor·K8s를 실행하지 않는다.

```bash
python3 -m unittest discover -s tools/trivy -p 'test_*.py' -v
```

시험은 실제 tar member/blob 해시 검증과 Mock DB/Trivy subprocess를 조합한다. 두 이미지 성공, 첫/두 번째 HIGH 차단, CRITICAL, DB 실패·만료·변조, scanner 오류·timeout·신호, 빈/잘못된 report, 이미지/report 변경을 검증한다. 운영 VM 실물 테스트와 독립 리뷰는 별도다.

참조: [Trivy archive Scan CLI](https://trivy.dev/docs/latest/references/configuration/cli/trivy_image/), [Trivy 0.74.0 report 구조](https://github.com/aquasecurity/trivy/blob/v0.74.0/pkg/types/report.go), [Podman OCI archive 저장](https://docs.podman.io/en/stable/markdown/podman-save.1.html), [OCI layout v1.1.1](https://github.com/opencontainers/image-spec/blob/v1.1.1/image-layout.md).
