# P1-CICD-01 — GitHub Actions / Self-hosted Runner Delivery

> 작성일: 2026-09-08 · 문서 버전: v1.0  
> 착수 근거: 사용자의 `P1-CICD-01 착수` 지시  
> 작업 상태: **In Progress — 사전 점검·구현 준비 착수**  
> Runtime: **Not Verified** · 정본 반영: **미반영** · Notion 원격 갱신: **미실행**

## Notion 데이터 속성

기존 Record ID에 수동 Upsert한다. 아래 Start/End는 기존 계획을 보존한 값이며 실제 착수일은 Schedule Note에 별도 기록한다. 날짜를 바꾸어 지연 이력을 지우지 않는다.

| Property | 입력값 |
|---|---|
| Record ID | P1-CICD-01 |
| Phase | Phase 1 |
| Category | CI/CD / Supply Chain |
| Type | Task |
| Status | In Progress |
| Priority | P0 |
| Start | 2026-09-01 |
| End | 2026-09-09 |
| Calendar Days | 9 |
| Working Days | 7 |
| Lead | 이권욱 |
| Support | 이하영 |
| Exit Criteria | GitHub→Runner→Test→Build→Trivy→Harbor→Kubernetes Deploy→Health/E2E 흐름과 실패 시 Deploy 차단 Evidence 확보; 승인 Digest와 이전 Digest 복구 추적 |
| Related Decision IDs | D03; D04; D19; D34; D35 |
| Related Risk IDs | RISK-06; RISK-08 |
| Source | Baseline v3.1.17 §10/§15/§16; D-SEC-01 v1.0/D34; FDS_Notion_Remaining_Backlog_Manual_v1.9 §12; 사용자 2026-09-08 착수 지시 |
| Schedule Note | 2026-09-08 사전 점검·구현 준비 착수. 기존 계획 09-01~09-09 보존. G1 09-07 목표일은 경과했으나 Runner/Harbor 실제 완료 증적은 미확인. G2 09-11 유지. 일정 재산정은 선행 증적·앱 저장소 확인 후 변경관리로 처리. Ansible 연관 이슈 #5는 앱 Delivery AC를 대체하지 않음. |

속성의 기존 일정·담당·분류는 Manual v1.9 §12 원문에서 확인했다. v3.1.17 Ticket Update Pack에는 P1-CICD-01 변경 행이 없다. 최신 WBS XLSX의 셀 직접 대조는 이번 검토에서 완료하지 못했으므로 기존값을 보존한 Upsert 입력본으로 제공하며, 최신 Workbook 변경 완료라고 주장하지 않는다.

---

# 🎯 User Story

자동화 담당자로서 Commit부터 Kubernetes 배포까지 추적 가능한 Delivery Pipeline을 구축한다. 이를 통해 테스트 또는 보안 Gate 실패 시 잘못된 이미지를 배포하지 않는다.

---

# 📌 Scope

## In Scope

- GitHub Private Repo와 외부 VM `cicd-runner01(10.250.10.20)`의 GitHub Actions Runner 설치·등록·Service 운영
- 승인 Source의 Unit/Contract Test, Image Build, Trivy, Harbor Push
- 사전 생성된 `fds-app/transaction-api`, `fds-app/fds-engine`의 승인 Digest 갱신
- P1-SEC-01에서 제공하는 단기 Token의 안전한 주입·만료·갱신
- Health/E2E, 실패 차단, 재실행, 이전 승인 Digest 복구 및 증적
- [infra-ansible #5](https://github.com/321Team/infra-ansible/issues/5)의 CI 기반 연계; 별도 완료 추적

## Out of Scope

- 미승인 거래 보류/해제, 독립 frontend/BFF 및 신규 Workload 자동 편입
- Runner의 Deployment 최초 생성·삭제, Secret 읽기, Cluster Admin 사용
- P1-SEC의 SA/Role/RoleBinding 정책 또는 P1-HBR의 Robot/CA 설계를 대신 변경하는 일
- Jenkins/Argo CD/Runner의 Kubernetes 내부 이관, P2 ROSA Delivery
- 운영 Ansible apply·자동 병합, 임시 k3s를 정본 Kubernetes 완료 증적으로 대체
- IF-13M Management DNS 및 미정의 경로를 착수만으로 허용

기존 포괄 범위의 `kubectl/Helm Deploy`는 D34 이후 확정된 최소권한에 맞게 해석한다. 초기 구현은 **기존 두 Deployment에 kubectl로 Digest patch**한다. Helm 설치·일반적인 upgrade에 필요한 추가 리소스/Secret 권한은 현재 Runner에 없다.

---

# 📋 핵심 요구사항

- [ ] Workflow/대상 Source Commit SHA, Run ID/Attempt, Image Tag, Harbor manifest Digest와 배포 전후 Digest를 연결한다.
- [ ] Test/Build/Scan 실패·검사 오류·미승인 예외 시 Deploy가 실행되지 않는다. 재실행도 동일 검토 대상을 확인한다.
- [ ] 외부 Action은 검토한 전체 Commit SHA로 고정하고 최소 GITHUB_TOKEN 권한과 credential 정리 절차를 적용한다.
- [ ] PR 코드를 실행하는 Job에 운영 SSH 키·Harbor Push·Kubernetes 배포 자격증명을 전달하지 않는다. 같은 영속 Runner를 재사용하는 경우 작업 간 침해 지속 위험까지 평가한다.
- [ ] IF-08 GitHub outbound, IF-09 Harbor Push, IF-10 Worker Pull, IF-11 API 접근을 각각 검증한다. 사내 경로는 도착지 관측으로 Source `.20` 보존을 확인한다.
- [ ] Harbor CA와 Kubernetes CA를 구분하고 TLS 검증 우회 없이 동작한다. Pull/Push Robot을 분리한다.
- [ ] P1-SEC의 승인 TokenRequest 정책으로 발급 주체·audience·실제 만료·주입·회수/갱신을 기록한다. Runner에 admin.conf나 무제한 재발급 권한을 상주시켜 단기 Token을 무력화하지 않는다.
- [ ] `ci-cd/cicd-runner-sa`를 `fds-app`의 두 Deployment에만 연결한다. 최초 Deployment/Service/Secret 생성은 P1-APP/P1-SEC 인계 범위다.
- [ ] GitHub Free + Private 환경의 수동 승인 통제를 구현 전에 정한다. `workflow_dispatch`만으로 독립 승인이 강제된다고 표현하지 않는다.
- [ ] Health/E2E 실행 주체·경로를 지정한다. Runner→Worker NodePort 직접 접근이나 pods/exec 권한을 추가하지 않는다.
- [ ] 실패 시 승인된 이전 Digest로 복구하고, 두 서비스의 호환성과 DB Schema 영향도 확인 후 재검증한다.

---

# ✅ Acceptance Criteria

- [ ] CICD-AC-01: Runner 등록·Service 재시작 후 Online/Job 수신과 IF-08 실제 Run 증적이 있다.
- [ ] CICD-AC-02: Harbor 정상 Push와 Worker Pull/Pod Start, TLS·인증 음성시험이 대응된다.
- [ ] CICD-AC-03: 정상 Commit의 두 이미지 Test→Build→Scan→Push→Digest 갱신과 Health/E2E가 성공한다.
- [ ] CICD-AC-04: 고의 Unit Test 실패 Run에서 Deploy가 skip/차단되고 대상 Deployment의 이미지가 유지된다.
- [ ] CICD-AC-05: 취약점 Gate 실패와 스캐너 실행 오류를 각각 실패 처리하며 배포 이미지가 유지된다.
- [ ] CICD-AC-06: Commit→Run/Attempt→Image→Digest→Deployment/실제 Pod 증적을 역추적할 수 있다. 로컬 Image ID를 registry manifest Digest로 오인하지 않는다.
- [ ] CICD-AC-07: 실제 Runner 자격증명으로 두 Deployment 권한은 허용되고, 임의 Deployment·create/delete·Secret·RBAC·exec 권한은 거부된다.
- [ ] CICD-AC-08: 만료 Token의 인증 실패 및 새 단기 Token으로 복구를 확인한다. 로그/Artifact/작업 디렉터리에 자격증명이 남지 않는다.
- [ ] CICD-AC-09: 승인 Digest와 승인자가 고정된 배포 기록, 재실행·동시 실행 통제, 이전 Digest 복구 결과가 있다.
- [ ] CICD-AC-10: 이하영의 독립 기술 리뷰와 이권욱의 최종 판정, 미해결 항목 및 정본 인계 기록이 있다.

체크박스는 모두 미검증 상태다. 설계·문서 작성, TCP 또는 TLS 성공을 위 Runtime AC의 PASS로 대체하지 않는다.

---

# 🧪 검증 및 증적

- Workflow YAML, Source/Workflow SHA, 버전 고정표, Runner Service/Network 결과
- 성공 Run 및 Test/Scan/Token 실패 Run, 실패 전후 이미지 비교
- Harbor Digest·Worker Pull·Kubernetes 배포 및 Health/E2E 결과
- Role 정책과 실제 SA can-i 결과, 단기 Token 수명/갱신 기록 — Token 값 제외
- 승인·복구·동시 배포 통제 및 Known Issue
- 논리 Evidence Path: `docs/evidence/P1-CICD-01/`
- 실행 원본 인계 저장소: `321Team/fds-project-evidence`; 실제 수신 경로/Commit은 인수 때 기록
- 파일 SHA-256, 생성 시각, 실행 Host, Run/Attempt, 판정자·근거를 함께 보존

---

# 🔗 Dependency

- **Blocked By:** 준비 작업은 착수 가능. 실배포는 P1-K8S-01 Platform Ready, P1-LB-01 Stage B/인증 API, P1-HBR-01 CA·Robot·Push/Pull, P1-SEC-01 SA·RBAC·Token, P1-APP-01 Source/Test/기존 Deployment 인계에 의존
- **Blocks / Next:** P1-E2E-01, G2의 CI/CD 완료 판정
- **Related Ticket:** DEC-P1-TLS-01(Done), ICR-D-NET-01-01, infra-ansible #5; 조건부 DEC-P1-FDS/TXN/ADMIN-01
- **Milestone:** G1 Runner/Registry 중간검증, G2 전체 Delivery

---

# ⚖️ Decision / Risk Control

- **Related Decision IDs:** D03; D04; D19; D34; D35
- **Related Risk IDs:** RISK-06; RISK-08
- **Decision Control:** 이미 정한 Actions/외부 Runner/Harbor/TLS/두 Deployment 최소권한은 재결정하지 않는다.
- **착수 확인 항목:** 앱 저장소, Runner 현황/빌더, 외부 DNS·dependency egress, Token 발급/주입 상세, 수동 배포 승인 절차, 승인 Trivy 기준.
- **변경 발생 시:** 기존 OID/ICR 및 P1-SEC/P1-HBR 결정과 연결한다. 새로운 DNS 경로·권한·Workload·일정 변경은 해당 변경관리 기록에 구체적 Delta를 남긴다. 중복 Decision ID는 생성하지 않는다.
- **Residual Risk:** Deployment patch의 Secret 우회 가능성은 D34의 기존 잔여위험이다. RBAC가 image 필드만 강제 제한한다고 주장하지 않는다.
- **일정 위험:** 09-08 착수와 기존 09-09 종료 간 가용 작업시간이 제한적이다. 미확인 선행조건을 PASS로 바꾸거나 G2를 자동 연장하지 않는다.

---

# 📝 Completion Record

- **Result:** STARTED — 사전 점검·구현 준비 / Runtime Not Verified
- **Status:** In Progress
- **Actual Start:** 2026-09-08
- **Exit Criteria:** 전체 Delivery·실패 차단·권한/Token·추적성·복구 증적과 독립 리뷰 확보
- **Completed Date:** 미입력
- **Reviewer / Approver:** 이하영(기술 리뷰 예정) / 이권욱(최종 판정 예정); 완료 서명 아님
- **Baseline Integration:** 미반영 — 다음 정본 후보에서 상태·Actual Start·근거만 우선 동기화
- **Remote Ticket Update:** 미실행 — 기존 Notion Record ID 수동 Upsert용

## 근거

- [정본 v3.1.17 §10·§15·§16](https://github.com/321Team/fds-project-baseline/blob/2d93395a0e19ea67e5be199d7183a7edbaa6cf4c/baseline/FDS_Project_Implementation_Baseline_v3.1.17.md)
- [v3.1.17 Ticket Update Pack](https://github.com/321Team/fds-project-baseline/blob/2d93395a0e19ea67e5be199d7183a7edbaa6cf4c/tickets/FDS_v3.1.17_Canonical_Ticket_Update_Pack_v1.0.md)
- FDS_Notion_Remaining_Backlog_Manual_v1.9.md §12 — 원문 대조
- D-SEC-01_Kubernetes_Security_Design_Baseline_v1.0.md §4~5, Handoff — 원문 대조
- [GitHub Environment의 플랜 제약](https://docs.github.com/en/actions/how-tos/deploy/configure-and-manage-deployments/manage-environments)
- [GitHub Self-hosted Runner 보안](https://docs.github.com/en/actions/reference/security/secure-use)
