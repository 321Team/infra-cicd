# P1-CICD-01 역할 결정 기록

기록일: 2026-09-09. 상태: **기존 채택사항 문서화 / PR 기술 재검토 및 병합 대기**.

README와 운영 기준의 역할을 저장소 안에서 추적하기 위한 기록이다. 새로운 프로젝트 Decision ID를 발행하거나 D19·D34의 승인자 및 승인 이력을 변경하는 문서가 아니다.

## 근거

| 근거 | 확인 내용 |
|---|---|
| [베이스라인 v3.1.17](https://github.com/321Team/fds-project-baseline/blob/2d93395a0e19ea67e5be199d7183a7edbaa6cf4c/baseline/FDS_Project_Implementation_Baseline_v3.1.17.md) | 이권욱의 Automation/Runner, 이하영의 Kubernetes/Security/Registry 담당과 D19 TLS 구조 |
| [Kickoff User Story v1.0 원문](../references/P1-CICD-01_Notion_UserStory_v1.0.md) 및 [출처·SHA-256](../references/README.md) | P1-CICD-01 Lead 이권욱, Support 이하영, 독립 기술 리뷰와 최종 판정 구분 |
| [기존 README](https://github.com/321Team/infra-cicd/blob/7cdc9ca0a8af7eb4bc8696cc795e93ec451aea35/README.md) / [기존 운영 기준](https://github.com/321Team/infra-cicd/blob/7cdc9ca0a8af7eb4bc8696cc795e93ec451aea35/docs/governance.md) | CI/CD 책임과 CA 관리·Runner Trust / Harbor 개인키·CSR·서버 적용 역할의 선행 기록 |
| [채택한 구현 설정](https://github.com/321Team/infra-cicd/blob/790105e390c529ee2fd50b23e709efe160a059c6/config/p1-cicd-01.json) / [구현안](https://github.com/321Team/infra-cicd/blob/790105e390c529ee2fd50b23e709efe160a059c6/docs/baseline-implementation.md) | 사용자 채택 상태, CA 관리 이권욱, Harbor 키·CSR·적용 이하영, 저장소 전용 Runner와 중앙 실행 |

위 구현 설정은 PR #2의 기존 커밋을 고정 참조한다. 채택 기록의 존재와 해당 PR의 기술 승인·main 병합은 별개다. Git 커밋으로 추적되는 원문을 근거로 삼으며, 추적 불가능한 대화를 단독 근거로 인용하지 않는다.

## 역할과 책임 경계

| 작업 | 담당 | 책임 경계 |
|---|---|---|
| P1-CICD-01 구현 및 최종 판정 | 이권욱 | Runner 설치·서비스, Test→Build→Trivy→Push, 승인 Digest 갱신·복구와 실행 증적 |
| P1-CICD-01 지원 및 독립 기술 리뷰 | 이하영 | Harbor/Kubernetes 연계와 이권욱 작성 변경 검토. 작성자 본인의 독립 승인을 요구하지 않음 |
| CA 관리·발급 및 Runner Trust 검증 | 이권욱 | 승인 CA 자산 확인, 보호 환경의 발급·갱신 및 CA 공개 인증서 신뢰 검증 |
| Harbor 키·CSR·인증서 적용·복구 | 이하영 | 서버 개인키 보관, CSR 생성, Leaf/키 일치 확인, 서버 적용·원복과 Worker Trust 연계 |
| Application Source·테스트 계약 인계 | 해당 앱 담당과 조정 | 앱 정본 소유권은 앱 저장소에 유지. 이전 임시 Harbor 인증서 이력은 필요 시 이재환에게 확인 |

## 운영 조건

- 이권욱 작성 PR은 이하영이, 이하영 작성 PR은 이권욱이 검토한다. 이권욱이 해당 Commit의 독립 리뷰·검증·잔여위험을 확인해 최종 병합을 결정한다. 병합과 서버 실행·배포 승인은 구분한다.
- GitHub Admin/Write/Read 권한의 기존 제안은 유지한다. 이 기록은 초대나 실제 권한 부여 완료 증적이 아니다.
- Runner는 `321Team/infra-cicd` 전용이며 최초 실행 방식은 중앙 수동 Workflow다. Ansible 관련 CI 추적만으로 `infra-ansible`을 Runner 허용 저장소에 추가하지 않는다.
- D19의 프로젝트 Private Root CA·Harbor/edge01 별도 Leaf·Consumer Trust 구조를 유지한다. CA 개인키는 Runner/Harbor/Git에 전달하지 않으며 Harbor 개인키는 서버의 보호 위치에 보관한다.
- P1-HBR의 Registry/Robot 운영과 P1-SEC의 RBAC·단기 Token 정책은 해당 소유 범위를 유지한다. 기존 두 Deployment의 승인 Digest 갱신 범위를 확대하지 않는다.
- 역할 문서화는 인증서 발급·서비스 전환·Job·Push/Pull·배포 성공을 의미하지 않는다. `DELIVERY_GATE=NOT_VERIFIED`를 유지한다.

이후 실제 담당·권한·범위 변경이 필요하면 기존 티켓 및 변경관리 기록에 구체적인 변경을 남기고 관련 문서를 함께 갱신한다.
