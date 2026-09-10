# P1-CICD-01 베이스라인 기반 구현안 v1.1

채택일: 2026-09-09. 근거: 사용자 지시 “우리가 설계한 베이스라인 기준으로 권장안 적용”. 상태: **구현 선택 채택 및 저장소 반영 / 서버 적용 미실행 / DELIVERY_GATE=NOT_VERIFIED**.

기준 정본은 [베이스라인 v3.1.17](https://github.com/321Team/fds-project-baseline/blob/2d93395a0e19ea67e5be199d7183a7edbaa6cf4c/baseline/FDS_Project_Implementation_Baseline_v3.1.17.md)이다. 2026-09-09 조회한 main에서 같은 Commit과 파일 Git blob `9090e23e62f9e59f74f1ce174d404fa6ead4db91`을 확인했다. 아래의 등록 범위·빌더·경로·수명은 이번에 채택한 구현값이며 베이스라인에 원래 명시된 값으로 소급하지 않는다. 공식 베이스라인·Notion 변경 또는 PR 병합을 뜻하지 않는다.

2026-09-10 후속 결정: 사용자 지시 “기존 승인 CA는 폐기 신규 발급 필요”에 따라 기존 CA 재사용 선택을 신규 키·Root 발급으로 변경했다. 기존 CA 폐기 완료와 신규 발급 완료는 아직 미검증이다.

## 채택한 값

| 항목 | 베이스라인 고정 조건 | 이번 구현값 |
|---|---|---|
| Runner | 외부 Self-hosted Runner, `.20`, 이권욱 담당 | `cicd-runner01`, `server01_cicd-runner`, 계정 홈 `actions-runner`, 비root 서비스 |
| 등록·실행 범위 | GitHub Private Repo / Actions | `321Team/infra-cicd` 저장소 전용 Runner. 중앙 Workflow에서 승인 앱 SHA를 가져와 실행 |
| 빌더 | Test → Build → Scan → Push | 기존 Podman 6.1.0을 rootless로 사용. shell/검토된 JS Action 중심, Docker container Action 및 GitHub `services:`는 사용하지 않음 |
| 실행 통제 | 실패 시 배포 금지, 승인 Source 추적 | 처음에는 수동 `workflow_dispatch`; 승인된 main Workflow만 실행. 임의 PR·포크 코드를 이 영속 Runner에서 실행하지 않음 |
| Harbor 정식화 | Harbor `.54:443`, Lead 이하영 | 원본 `/opt/harbor`, 신규 데이터 `/data/harbor-formal`. 임시 `/data`의 기존 DB·Redis·Registry·Secret은 보존하고 새 구성에 가져오지 않음 |
| Harbor 초기화 | 사용자 요청: 정식 구축 전 초기화·발급 | 신규 DB·Secret·관리자/Robot 자격증명·HTTPS 키로 정식 환경 시작. 기존 데이터를 즉시 지우는 방식은 채택하지 않음 |
| TLS 구조 | D19 프로젝트 Private Root CA, Harbor/edge01 별도 Leaf | CA는 이권욱 관리 오프라인 관리자 환경의 암호화 볼륨. Harbor 키·CSR·적용은 이하영 |
| 인증서 프로파일 | DNS/IP SAN, TLS 1.2+, 검증 우회 금지 | 신규 Root RSA4096/1825일, Harbor Leaf RSA3072/365일/만료 30일 전 갱신. CA:FALSE, serverAuth |
| 접속 이름 | 현재 IP `.54`, DNS 설계 `harbor.fds.internal` | IP SAN `10.1.93.54`; DNS SAN `harbor.fds.internal`, 로컬 식별 별칭 `harbor01`. 초기 endpoint는 IP 유지 |
| 신뢰 배포 | Consumer에 CA 공개 인증서만 | 승인 지문 대조 후 Runner·Worker trust. 개인키는 각 보호 위치에만 보관 |
| Registry 인증 | CI Push+Pull, Worker Pull 분리 | 신규 계정과 별도 자격증명. 발급 이름·Secret 참조는 P1-HBR/P1-SEC 인계 기록 사용 |
| 스캔·배포 | 기존 Critical 차단·실패 차단·최소권한 | Trivy CRITICAL 및 검사 오류 차단, 승인 digest로 기존 두 Deployment만 갱신 |

등록 범위는 저장소 단위로 확정했다. 앱 저장소에서 재사용 Workflow를 호출해도 이 전용 Runner에 자동 접근할 수 없으므로, 최초 구성은 **infra-cicd에서 Workflow가 실행되는 중앙 방식**이다. 앱 소스 접근은 승인된 저장소 목록과 읽기 전용 자격증명을 별도로 사용한다. 앱 저장소의 일반 GITHUB_TOKEN이 다른 비공개 저장소를 읽는다고 가정하지 않는다. [GitHub 등록 범위](https://docs.github.com/en/actions/how-tos/manage-runners/self-hosted-runners/add-runners)

Podman은 현재 설치된 빌더를 사용하는 구현 선택이다. 실제 사용자 namespace와 rootless build/scan/push는 실행 시험으로 확인한다. Docker container Action이나 GitHub service container가 필요해지면 빌더·권한 영향을 검토하여 구성 변경으로 다룬다. [GitHub Runner 요구사항](https://docs.github.com/en/actions/reference/runners/self-hosted-runners), [Podman rootless](https://docs.podman.io/en/latest/markdown/podman.1.html#rootless-mode)

## 병행 실행 순서

1. **Harbor:** 이미 검토한 `harbor01-sn0wepv7` 요청의 개인키·CSR을 유지한다. 새 CA에 동일 CSR을 제출하며 CA 교체를 이유로 CSR 생성 도구를 다시 실행하지 않는다.
2. **Runner:** [설치 Runbook](runner-bootstrap.md)으로 Git과 rootless 전제조건을 확인·보완하고, GitHub가 제시하는 Linux x64 패키지·해시로 다운로드·등록·서비스 설정을 진행한다.
3. **CA:** [발급·정식화 Runbook](harbor-formalization.md)에 따라 이권욱의 보호 환경을 준비한다. 기존 CA의 신규 서명을 중지하고 새 키로 신규 Root를 발급한다. 구 CA 식별·Consumer 전환·구 신뢰 제거·키 폐기를 기록하며 과거 키·발급 대장을 덮어쓰지 않는다.
4. **Harbor 전환:** 새 Leaf와 키 일치 → 임시 컨테이너 중지 → 일관된 보호 백업 → 신규 빈 데이터 경로를 사용하는 정식 구성 시작 → 엄격한 TLS와 복구 확인 순서다. 기존·신규 환경을 같은 포트와 컨테이너 이름으로 동시 기동하지 않는다.
5. **결합:** Runner/Worker CA Trust → Robot Push/Pull·권한 음성시험 → 승인 digest 배포 → Health/E2E·실패 차단·이전 digest 복구.

사용자에게 등록 범위·빌더·수명을 다시 선택해 달라고 요청하지 않는다. 다음 항목은 선택 문제가 아니라 현장에서 채울 실행 입력이다.

| 실행 입력 | 처리 기준 |
|---|---|
| 실제 CA 보관 장치/경로와 기존 승인 CA 기록 | 보호 환경과 새 CA 자산 ID를 기록하고 구 CA 지문·배포 대상·백업을 식별한다. 신규 발급 결정은 확정이며 실제 폐기는 별도 증적 필요 |
| Runner 패키지와 해시 | 실행 시 GitHub 저장소 설정 화면의 동일 버전 다운로드·검증 값 사용, 실행 기록에 실제 버전 기입 |
| 앱 저장소·Dockerfile·테스트 계약 | P1-APP 인계. 이름이나 성공 명령을 만들어 넣지 않음 |
| 실제 K8s 버전·컨테이너명·TokenRequest | P1-K8S/P1-SEC 인계에 맞춰 kubectl과 단기 Token 구성. 임의 만료시간·권한 확대 없음 |
| Harbor 설치판·중지 시점·백업 용량 | 이하영이 실제 실행 전에 확인. 관측 v2.13.1을 최신/보안 승인판으로 간주하지 않음 |

## 증적과 완료 기준

기존 Harbor archive `b7cdba9e63228c264997f63c7ecf9f482f351bd93378295e9cada5d6966e9bc4`, Runner archive `37aef2f95c208d41ff03a26e256422d320281bc017ecce531be9b95931d73e72`의 무결성 검토는 완료했다. 두 증적을 만든 수집기 Commit은 `2b534a94a2644f54f6a88d4cf3ac1f44bbf93226`이다. 이번 PR의 새 Commit과 혼동하지 않는다.

Runner의 GitHub 두 호스트 TLS 성공, 임시 Harbor의 9개 컨테이너 healthy는 수집 시점 사실이다. Root/Leaf 발급·정식 Harbor·Runner 등록·Job·Push/Pull·Kubernetes 배포는 아직 검증되지 않았다. 최종 인수는 기존 파이프라인 계약을 유지한다: Source/Workflow SHA→Run/Attempt→Harbor digest→실제 배포 연결, 실패/음성시험, 토큰 만료·갱신, 이전 digest 복구. P1-SEC 권한은 `ci-cd/cicd-runner-sa`에 `fds-app`의 `transaction-api`, `fds-engine` get/update/patch만 허용하며 RBAC가 image 필드만 제한한다고 주장하지 않는다.

현재 구현값은 [config/p1-cicd-01.json](../config/p1-cicd-01.json)에 기록했다. 기존 인계 템플릿은 빈 양식으로 유지하며 결정 기록과 구분한다.
