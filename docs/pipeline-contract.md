# 파이프라인 구현 조건과 인수 기준

## 구현 전 인계

| 항목 | 필요한 입력 |
|---|---|
| 앱 Source | 저장소, 승인 Commit, Dockerfile·Unit/Contract Test 명령 및 두 서비스 호환성 |
| Runner | 실제 등록 상태, 설치 버전, Job 수신, 빌더·kubectl·Trivy 버전, 자격증명 분리·작업 정리 |
| Harbor | 승인 CA Trust, CI Push+Pull Robot, Worker Pull 계정, Registry repository 경로 |
| Trivy | 기존 승인 Critical 차단 원칙, DB 공급·갱신 경로, 검사 오류 처리, 예외 승인·만료 |
| Kubernetes | API 인증 연결, 기존 두 Deployment 및 컨테이너명, SA·RBAC, 단기 Token 정책 |
| Health/E2E | 실행 주체·승인 경로·명령·예상 결과; 임의 Runner→NodePort 경로를 추가하지 않음 |
| 승인·복구 | 승인 대상 SHA/Digest, 재실행·동시 배포 통제, 이전 Digest·DB Schema 영향 |

현재 미확인 값을 임의로 채워 실행 Workflow를 활성화하지 않는다. 특히 Trivy 기준을 임의로 HIGH까지 확대하거나 승인 예외 없이 완화하지 않는다.

## 구현 순서

승인 Source 확인 → Unit/Contract Test → Build → Trivy → Harbor Push와 manifest Digest 확인 → 승인 Digest의 기존 Deployment 갱신 → Health/E2E → 증적 연결 순서로 구현한다. 중간 실패·검사 오류·권한/Token 실패 때 Deploy가 실행되지 않아야 한다. 이전 Digest 복구는 호환성·Schema 영향을 확인한 뒤 수행한다.

외부 Action과 재사용 Workflow는 검토한 전체 Commit SHA로 고정한다. GITHUB_TOKEN은 Job별 최소권한으로 설정하고 호출 저장소와 자격증명 전달 범위를 명시한다. 신뢰하지 않은 PR 코드를 운영 자격증명이 있는 영속 Runner에서 실행하지 않도록 구현 경계를 검토한다.

## 최소권한 경계

`ci-cd/cicd-runner-sa`는 `fds-app`의 `transaction-api`, `fds-engine` 기존 Deployment에 대해 승인된 get/update/patch 범위를 사용한다. 최초 Deployment·Service·Secret 생성은 앱/보안 담당 인계다. 일반적인 Helm install/upgrade에 필요한 추가 권한을 자동으로 부여하지 않는다. RBAC가 image 필드만 제한한다고 주장하지 않는다.

## 인수 기준

- [ ] Runner 재시작 후 Online·Job 수신 및 GitHub 통신 확인.
- [ ] 엄격한 TLS, Runner Push·Worker Pull/Pod Start와 권한 음성시험 확인.
- [ ] 정상 Source의 Test→Build→Scan→Push→두 Deployment 갱신→Health/E2E 성공.
- [ ] 고의 테스트 실패, 취약점 차단, 스캐너 오류 시 배포 이미지 유지.
- [ ] Source/Workflow SHA→Run/Attempt→Harbor Digest→실제 배포 추적.
- [ ] 허용된 두 Deployment 외 권한, Secret·create/delete·exec 등이 거부됨.
- [ ] 단기 Token 만료 실패와 승인된 갱신 후 복구, 자격증명 잔존 없음.
- [ ] 승인 Digest·재실행·동시 배포 통제와 이전 Digest 복구 증적 확보.
- [ ] 독립 기술 리뷰와 최종 판정, 잔여위험·정본 인계 기록.

문서 등록·TCP 연결·TLS 성공 중 하나를 전체 Delivery Gate PASS로 대체하지 않는다.
