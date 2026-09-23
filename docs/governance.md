# 운영·검토 기준

2026-09-18 KST 사용자 결정에 따라 [코드 병합 독립 리뷰와 Lead 위임 정책](https://github.com/321Team/fds-project-baseline/blob/b2e4612cf2e417b10e7f145c5b1e5f58dbe1b948/governance/REVIEW_AUTHORITY_POLICY.md)을 적용한다. 아래 과거 역할 결정 기록의 일반적 승인 문구와 충돌하면 승인 절차에 한해 이번 변경을 우선한다. 기술 AC·증적·담당 범위는 유지한다. fds-project-evidence는 문서·로그·정책을 포함한 모든 PR에 독립 리뷰를 유지하는 예외다. Lead 자체 인수와 증적 저장소 편입 심사는 구분한다.

2026-09-23 개정: 사전 검증(Pre-check) 코드는 담당 Lead 권한으로 코드 리뷰 없이 병합할 수 있고, 운영 서버 Runtime 적용 전에 코드 리뷰가 필수다. 아래 [사전 검증 코드 예외](#사전-검증pre-check-코드-예외--2026-09-23)를 따른다.

## 역할

아래 담당과 CA/Harbor 역할의 출처·책임 경계는 [P1-CICD-01 역할 결정 기록](https://github.com/321Team/infra-cicd/blob/081b3dd947bbf04fbafe00f15d7cfdba25f3c0d1/docs/decisions/p1-cicd-01-roles.md)에 연결한다. 이 기록은 기존 채택사항을 문서화한 것이며 새 권한 부여나 PR 승인 기록이 아니다.

| 구분 | 담당 |
|---|---|
| P1-CICD-01 Lead, Runner·Pipeline 구현 및 최종 판정 | 이권욱 |
| Support, Harbor/Kubernetes 연계 및 코드 병합 시 독립 기술 리뷰 | 이하영 |
| Application Source·테스트·계약 인계 | 해당 앱 담당과 조정; 이전 임시 Harbor 인증서 이력은 필요 시 이재환에게 확인 |

GitHub 권한은 이권욱 Admin, 이하영 Write, 이재환 Read를 초기 권장안으로 한다. 해당 앱 담당이 이 저장소에 직접 구현할 필요가 생기면 작업 범위에 따라 권한을 조정한다. 실제 초대·권한 설정 완료 기록이 아니다. 조직 기본 권한과 팀 소속에 의한 상속 권한도 확인한다. [GitHub 저장소 역할](https://docs.github.com/en/organizations/managing-user-access-to-your-organizations-repositories/managing-repository-roles/repository-roles-for-an-organization)

## 검토와 승인

1. 작성자는 관련 티켓, 변경 이유, 영향 대상, 검증 결과와 미확인 사항을 PR에 기록한다.
2. 코드/실행 설정이 포함된 PR만 작성자 외 최소 1인의 독립 기술 검토를 거친다. 기존 경로는 이권욱 작성 시 이하영, 이하영 작성 시 이권욱이며 해당 역할 범위 내 대체 Reviewer를 사용할 수 있다. 문서·증적만 변경한 PR은 해당 Lead가 검토·병합을 결정하며 별도 독립 승인은 필요하지 않다. 본인 PR에 본인이 GitHub APPROVE를 수행하는 절차를 요구하지 않는다.
3. Reviewer는 검토 대상 Commit과 변경 영향을 확인한다. 새 Commit이 추가되면 해당 변경과 영향을 받은 기존 부분을 다시 확인한다. 무관한 시험을 반복하지 않는다.
4. Lead 이권욱이 코드 PR의 독립 리뷰 결과·검증·잔여위험을 확인해 병합을 결정한다. Runner 실행·애플리케이션 배포·영구 반영·증적 수용·AC 인수·티켓 종결도 해당 업무 Lead가 판단하며, 동일 코드에 독립 승인을 다시 요구하지 않는다. 병합·실행·인수 결과는 각각 기록한다.
5. 실제 배포에는 검토·병합된 Source/Workflow SHA와 Image Digest, Lead 판단·근거를 고정하고 이전 Digest 및 복구 기록을 연결한다. 필수 Test/Build/Scan·Runtime AC·선행조건은 유지한다. 새 코드/실행 설정 변경은 독립 코드 리뷰를 거친다.
6. 실행 입력이 아닌 문서·공개 지문·검증 결과 기록과 코드/자동화/의존성/실행 설정 변경을 실제 diff로 구분한다. 혼합 PR은 코드 기준이다. Lead가 수행한 업무도 필수 AC·원본 증적으로 자체 인수할 수 있다. 여러 단계의 Lead 판단은 대상·KST 시각·SHA/Digest·검증/증적·판정/잔여·복구를 한 티켓 기록에 함께 남길 수 있다.

GitHub Free 조직의 Private 저장소에서는 브랜치 보호와 CODEOWNERS에 의한 승인 강제를 전제로 하지 않는다. Write 권한자는 기술적으로 main에 직접 쓰거나 병합할 수 있으므로 PR 절차는 팀 운영 수칙이다. `workflow_dispatch`도 독립 승인 자체를 보장하지 않는다. [브랜치 보호 지원 범위](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches), [CODEOWNERS 지원 범위](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/about-code-owners)

## 사전 검증(Pre-check) 코드 예외 — 2026-09-23

적용 기준: 프로젝트 중간 결과 회고에 따른 2026-09-23 KST 사용자 정책 변경 안내. 상세 기준은 [중앙 리뷰 권한 정책 §3.2](https://github.com/321Team/fds-project-baseline/blob/ea2488b33a870d35c16bd303af568c5fe48c2e44/governance/REVIEW_AUTHORITY_POLICY.md)([PR #93](https://github.com/321Team/fds-project-baseline/pull/93))를 따른다. 중앙 PR 병합 전까지 이 절은 제안 상태다.

| 단계 | 검토와 병합 |
|---|---|
| Pre-check 코드 병합 | 사전 검증 단계의 점검·수집·판정용 스크립트·검증기와 그 시험 코드는 별도 코드 리뷰 없이 책임 티켓의 담당 Lead 권한으로 즉시 병합할 수 있다. |
| 운영(Production) 서버 Runtime 적용 | 운영 서버에서 실행·설치·배포·영구 반영하거나 운영 배포 경로에 편입하는 시점부터 코드 리뷰가 필수다. 읽기 전용 실행도 포함한다. |

- **Pre-check 병합 기록.**
  - PR은 계속 사용한다. main 직접 push·force push·자동 병합은 금지다.
  - PR에 `Pre-check` 분류, 책임 티켓, 병합 SHA, 담당 Lead, KST 시각을 남긴다.
  - 리뷰 상태는 `REVIEW=NOT PERFORMED (Pre-check)`로 표기한다. 이 병합을 리뷰 완료, Runtime PASS, AC 인수로 표시하지 않는다.
- **운영 적용 전 절차.**
  - 적용할 SHA를 고정한다.
  - 직전 독립 리뷰 SHA 이후 전체 diff를 작성자 외 적격 검토자가 리뷰한다. 리뷰 이력이 없으면 적용 대상 코드 전체가 범위다.
  - Lead는 리뷰 완료 SHA와 적용 SHA의 일치를 실행 기록에 남긴다.
  - 리뷰 후 수정 PR은 일반 코드 PR 절차를 따른다.
- **대상 외(기존 독립 리뷰 유지).**
  - Pre-check 코드와 운영 Runtime 코드·manifest·IaC·배포 설정이 섞인 혼합 PR
  - 병합·Release 게이트 CI workflow
  - 시크릿·무결성 등 보안 통제 코드
  - fds-project-evidence의 모든 PR
- **유지 통제.** 비밀정보 커밋 금지, 필수 AC·시험·보안 기준, 운영 적용 시 선행조건·중단·복구 기준은 완화하지 않는다.

이 저장소에서 Runner Job·애플리케이션 배포·Delivery Workflow로 운영 서버에 반영되는 코드는 Pre-check 코드가 아니다. Pre-check로 병합된 코드를 이 경로에 편입하는 것은 운영 Runtime 적용이다.

## 자격증명과 증적

`.gitignore`에는 `*.pem`을 포함한다. `.pem`은 공개 CSR·인증서에도 쓰이므로 이 규칙은 저장소에 실수로 추가하는 것을 줄이는 조치이며, 승인된 공개 CSR·인증서의 발급·전달 절차를 금지하지 않는다. 현재 저장소에는 공개 지문·검증 결과·인계 참조를 기록하고 실물 인증서·CSR은 기존 인계 절차로 다룬다.

`.gitignore`는 보조 수단이며 강제 추가 또는 이미 추적된 자료까지 보호하지 않는다. Push 전에 전체 변경 내용을 확인한다. [Git 공식 문서](https://git-scm.com/docs/gitignore) Token·개인키·원본 Secret 및 kubeconfig를 커밋하거나 PR에 붙이지 않는다. CA Trust 증적에는 공개 인증서 지문과 검증 결과만 기록한다.

배포 자격증명은 P1-SEC의 승인 발급·주입·만료·회수 방식으로 처리한다. CA 개인키를 Runner나 Actions Secrets에 배포하지 않는다. 증적은 비밀값을 제거한 후 fds-project-evidence의 수신 Commit·경로·SHA-256으로 연결한다.

## 줄바꿈

`.gitattributes`에서 `*.sh`, `*.yml`, `*.yaml`에 `text eol=lf`를 지정한다. 셸 스크립트와 Workflow YAML의 Git 정규화·체크아웃 줄바꿈을 LF로 유지하며, 기존 파일 전체를 일괄 재정규화하지 않는다. [Git 공식 문서](https://git-scm.com/docs/gitattributes)
