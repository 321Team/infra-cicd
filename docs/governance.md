# 운영·검토 기준

## 역할

| 구분 | 담당 |
|---|---|
| P1-CICD-01 Lead, Runner·Pipeline 구현 및 최종 판정 | 이권욱 |
| Support, Harbor/Kubernetes 연계 및 독립 기술 리뷰 | 이하영 |
| Application Source·테스트·계약 인계 | 해당 앱 담당과 조정; 이전 임시 Harbor 인증서 이력은 필요 시 이재환에게 확인 |

GitHub 권한은 이권욱 Admin, 이하영 Write, 이재환 Read를 초기 권장안으로 한다. 해당 앱 담당이 이 저장소에 직접 구현할 필요가 생기면 작업 범위에 따라 권한을 조정한다. 실제 초대·권한 설정 완료 기록이 아니다. 조직 기본 권한과 팀 소속에 의한 상속 권한도 확인한다. [GitHub 저장소 역할](https://docs.github.com/en/organizations/managing-user-access-to-your-organizations-repositories/managing-repository-roles/repository-roles-for-an-organization)

## 검토와 승인

1. 작성자는 관련 티켓, 변경 이유, 영향 대상, 검증 결과와 미확인 사항을 PR에 기록한다.
2. 이권욱 작성 PR은 이하영이 독립 기술 검토한다. 이하영 작성 PR은 이권욱이 검토한다. 본인 PR에 본인이 GitHub APPROVE를 수행하는 절차를 요구하지 않는다.
3. Reviewer는 검토 대상 Commit과 변경 영향을 확인한다. 새 Commit이 추가되면 해당 변경과 영향을 받은 기존 부분을 다시 확인한다. 무관한 시험을 반복하지 않는다.
4. 이권욱이 독립 리뷰 결과·검증·잔여위험을 확인해 병합을 결정한다. 병합과 Runner 실행·애플리케이션 배포 승인은 별개다.
5. 실제 배포에는 승인 Source/Workflow SHA와 Image Digest, 승인자·근거를 고정하고 이전 Digest 및 복구 기록을 연결한다.

GitHub Free 조직의 Private 저장소에서는 브랜치 보호와 CODEOWNERS에 의한 승인 강제를 전제로 하지 않는다. Write 권한자는 기술적으로 main에 직접 쓰거나 병합할 수 있으므로 PR 절차는 팀 운영 수칙이다. `workflow_dispatch`도 독립 승인 자체를 보장하지 않는다. [브랜치 보호 지원 범위](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches), [CODEOWNERS 지원 범위](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/about-code-owners)

## 자격증명과 증적

`.gitignore`는 보조 수단이며 강제 추가 또는 이미 추적된 자료까지 보호하지 않는다. Push 전에 전체 변경 내용을 확인한다. Token·개인키·원본 Secret 및 kubeconfig를 커밋하거나 PR에 붙이지 않는다. CA Trust 증적에는 공개 인증서 지문과 검증 결과만 기록한다.

배포 자격증명은 P1-SEC의 승인 발급·주입·만료·회수 방식으로 처리한다. CA 개인키를 Runner나 Actions Secrets에 배포하지 않는다. 증적은 비밀값을 제거한 후 fds-project-evidence의 수신 Commit·경로·SHA-256으로 연결한다.
