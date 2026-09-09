# infra-cicd

P1-CICD-01 GitHub Actions·Self-hosted Runner 기반 CI/CD 구축 및 검증 저장소. Test·Build·Trivy·Harbor Push·Kubernetes 배포와 롤백, 승인 Digest 및 실행 증적을 관리합니다.

## 목적과 상태

Commit부터 배포까지 추적 가능한 Delivery Pipeline을 구축하고, 테스트·보안 검사·인증 실패 시 잘못된 이미지의 배포를 차단한다.

- 담당: **이권욱(Lead)** / **이하영(Support·독립 기술 리뷰)** — [역할 결정 기록](https://github.com/321Team/infra-cicd/blob/081b3dd947bbf04fbafe00f15d7cfdba25f3c0d1/docs/decisions/p1-cicd-01-roles.md)
- 티켓: **P1-CICD-01 — In Progress**
- Runtime / Delivery Gate: **NOT_VERIFIED**
- 이 초기 구성에는 문서·기록 양식만 포함한다. 실행 Workflow와 Runner 설치·배포 스크립트는 선행조건 확인 후 별도 PR로 구현한다.

## 관리 범위

| 영역 | 관리 내용 |
|---|---|
| Runner | cicd-runner01 설치·등록·Service 운영 절차, 버전·권한·작업 정리 |
| CI | 승인 Source의 Unit/Contract Test, Image Build, Trivy 검사 및 실패 차단 |
| Registry 연계 | 승인 CA Trust, CI Robot 인증, Harbor Push와 manifest Digest 추적 |
| CD | 사전 생성된 두 Deployment의 승인 Digest 갱신, Health/E2E, 재실행·동시 배포 통제 |
| 복구 | 이전 승인 Digest 복구, 서비스 호환성·DB Schema 영향 확인 |
| 증적 | Source/Workflow Commit → Run/Attempt → Image Digest → 배포·복구 결과 연결 |

## 승인된 환경과 경계

| 항목 | 기준 |
|---|---|
| Runner | 외부 VM cicd-runner01 / 10.250.10.20 |
| Harbor | https://10.1.93.54:443 |
| Kubernetes API | 10.1.93.109:6443, TLS Pass-through API LB |
| 배포 대상 | fds-app/transaction-api, fds-app/fds-engine |
| ServiceAccount | ci-cd/cicd-runner-sa |
| 배포 방식 | 승인된 기존 Deployment의 이미지 Digest 갱신; 최초 리소스 생성은 P1-APP/P1-SEC 인계 |

이 저장소가 생겼다는 사실로 Runner 등록이나 Delivery Pipeline이 동작하는 것은 아니다. 업무 애플리케이션 Source·Dockerfile·테스트는 각 앱 저장소에 둔다. [이미 채택한 구현값](https://github.com/321Team/infra-cicd/blob/790105e390c529ee2fd50b23e709efe160a059c6/config/p1-cicd-01.json)에 따라 초기 구성은 `321Team/infra-cicd` 전용 Runner와 이 저장소에서 실행하는 중앙 수동 Workflow다. 승인된 main Workflow 및 앱 Source SHA를 사용하며, 다른 비공개 앱 저장소의 읽기 권한은 별도 인계한다. Ansible 관련 CI 추적을 이유로 `infra-ansible`에 Runner 접근 권한을 추가하지 않는다.

## 다른 저장소와의 관계

| 저장소 | 정본 범위 |
|---|---|
| [fds-project-baseline](https://github.com/321Team/fds-project-baseline) | 승인 아키텍처·WBS·보안 설계 및 변경 결정 |
| infra-cicd | Runner·파이프라인·배포/롤백 자동화·CI/CD 검증 기준 |
| [infra-harbor-trivy](https://github.com/321Team/infra-harbor-trivy) | Harbor 서버·Trivy 운영, Robot 권한, 서버 인증서 적용·복구 |
| [infra-ansible](https://github.com/321Team/infra-ansible) | Ansible 코드·인프라 자동화; 앱 Delivery AC와 별도 추적 |
| 앱 저장소 — 확인 필요 | 업무 Source·Dockerfile·테스트·최초 Application Manifest 정본 |
| [fds-project-evidence](https://github.com/321Team/fds-project-evidence) | 실행 원본·독립 검증 증적; 이 저장소에서는 Commit·경로·해시로 연결 |

TLS는 CI/CD 선행조건으로 관리한다. 이미 채택한 역할에 따라 이권욱이 CA 관리·발급과 Runner Trust 검증을, 이하영이 Harbor 개인키·CSR 생성과 서버 적용을 담당한다. 이 저장소 생성으로 별도 PKI 저장소 생성이나 CA 발급 완료를 선언하지 않는다.

## 문서와 기록

- [운영·검토 기준](docs/governance.md)
- [파이프라인 구현 조건과 인수 기준](docs/pipeline-contract.md)
- [현재 관측 결과와 선행조건](docs/readiness.md)
- [배포 기록 양식](templates/delivery-record.example.json)

## 기본 운영 원칙

- 구현은 feature/docs/fix 브랜치와 PR로 진행한다. 작성자 외 리뷰 결과와 해당 Commit을 확인한 뒤 이권욱이 병합을 결정한다.
- 테스트·Build·Trivy 실패 및 검사 오류는 배포 차단으로 처리한다. 승인되지 않은 취약점 예외를 자동 허용하지 않는다.
- 이미지 Tag만으로 배포 대상을 판단하지 않고 Harbor manifest Digest를 기록한다.
- PR 코드를 실행하는 Job에 Harbor Push·Kubernetes 배포 자격증명을 전달하지 않는다. 같은 영속 Runner 재사용에 따른 작업 간 영향도 검토한다.
- CA 개인키·Robot Token·Runner 등록 Token·실제 kubeconfig·SSH 개인키를 Git, 로그, Evidence에 저장하지 않는다.
- TLS 우회, cluster-admin/admin.conf 사용, Runner에 Deployment 최초 생성·삭제나 Secret 읽기 권한을 추가하지 않는다.
- GitHub Free 조직 Private 저장소의 제한을 반영하며 수동 승인 절차를 기술적으로 강제된 보호 규칙으로 표현하지 않는다.

## 근거

- [검토 기준 베이스라인 v3.1.17](https://github.com/321Team/fds-project-baseline/blob/2d93395a0e19ea67e5be199d7183a7edbaa6cf4c/baseline/FDS_Project_Implementation_Baseline_v3.1.17.md)
- [P1-CICD-01 Kickoff User Story v1.0](https://github.com/321Team/infra-cicd/blob/081b3dd947bbf04fbafe00f15d7cfdba25f3c0d1/docs/references/P1-CICD-01_Notion_UserStory_v1.0.md) — 배포 당시 원문. [출처·SHA-256·현재 문서와의 관계](https://github.com/321Team/infra-cicd/blob/081b3dd947bbf04fbafe00f15d7cfdba25f3c0d1/docs/references/README.md)
- [P1-CICD-01 역할 결정 기록](https://github.com/321Team/infra-cicd/blob/081b3dd947bbf04fbafe00f15d7cfdba25f3c0d1/docs/decisions/p1-cicd-01-roles.md) — 기존 역할과 채택 구현의 출처 및 책임 경계

착수 원문의 일정·상태를 현재 Runtime 완료 판정으로 사용하지 않는다. 이후 정본 변경은 영향 검토 후 동기화하며, 다른 티켓의 완료 상태를 임의 갱신하지 않는다.
