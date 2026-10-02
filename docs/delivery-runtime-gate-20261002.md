# P1-CICD-01 Delivery Runtime Gate 착수 기록 — 2026-10-02

## 목적

P1-CICD-01의 남은 Harbor Push → Kubernetes Deploy → Health/E2E → rollback 경로를
기존 Test→Build→Scan provenance에 연결하기 전에, 실행 입력을 fail-closed로 고정한다.

이 문서와 `tools/delivery/delivery_gate.py`는 **운영 Runtime을 실행하지 않는다**.
Podman/Harbor/kubectl 명령, 자격증명 발급, Deployment 갱신, rollback은 포함하지 않는다.

## 현재 확인된 기준

- infra-cicd base main: `52cf494bec02ab4b814bed07e4cf66f37a3547b6`
- Runtime Run #5 `36521959454/1`: Test→Build→Scan PASS. Push/Deploy는 NOT_EXECUTED.
- Run #5 원본 evidence: fds-project-evidence PR #70 병합 완료.
- Harbor IF-09 AC-02: P1-HBR-01 #13의 공식 Review Run PASS를 handoff 입력으로 사용하되,
  HBR 시험 이미지 PASS를 앱 Delivery PASS로 확대하지 않는다.
- Kubernetes 최소권한 source:
  `infra-kubernetes@7329bf2eb988b552483af48ff660d6a3f1f1d4f5`
  `manifests/06-rbac.yaml`
  - `ci-cd/cicd-runner-sa`
  - namespace `fds-app`
  - 기존 Deployment `transaction-api`, `fds-engine`
  - verbs `get/update/patch`
  - create/delete/Secret/exec 권한은 부여하지 않음
- 컨테이너명은 같은 source의 `manifests/02-securitycontext.yaml` 기준
  `transaction-api`, `fds-engine`.

## 이번 Gate가 강제하는 것

### pre_push

- 승인된 `321Team/fds-msa` Source SHA와 infra-cicd main Workflow SHA/Run/Attempt 결합
- Build/Scan PASS와 두 이미지 archive/config/manifest identity
- 두 이미지 모두 HIGH=0, CRITICAL=0, Scan exit 0
- Scan manifest digest와 Build manifest digest 일치
- Harbor endpoint/repository 고정
- Runner TLS trust PASS와 HBR IF-09 AC-02 PASS
- Secret 본문이 아닌 **자격증명 reference만** 입력

### pre_deploy

pre_push 조건에 추가하여:

- Harbor Push receipt PASS 및 registry manifest digest 일치
- 대상 namespace/Deployment/container를 `fds-app/{transaction-api,fds-engine}`으로 고정
- 배포 직전 실제 이전 digest를 rollback input으로 요구
- API `https://10.1.93.109:6443`, SA `ci-cd/cicd-runner-sa`
- RBAC scope/Worker Pull/deployment lock PASS
- Token TTL 1~3600초
- 배포 승인 기록을 동일 Workflow SHA/Run/Attempt에 결합

## 의도적으로 남긴 미구현 범위

현재 PR에서는 아래를 구현하지 않는다.

1. Harbor login/push/manifest inspect 실제 adapter
2. TokenRequest/kubeconfig 임시 생성·폐기 adapter
3. `kubectl set image` 또는 patch, rollout/Health/E2E
4. 이전 digest rollback 실행과 복구 검증
5. Runtime evidence archive/수신 PR 자동화

이 항목은 HBR/K8S/SEC의 **현재 Runtime 입력을 재확인**한 뒤 별도 코드 변경으로 연결하며,
운영 경로에 편입하기 전 독립 기술 리뷰를 받는다.

## 2026-10-02 실행 경계

16:00 KST 이후 T19 보호를 위해 공유 Cluster/Worker/Network 변경을 중단하기로 한 조율을 따른다.
따라서 이번 변경은 Source/STATIC 준비만 수행하며 오늘 이 PR만으로 Push/Deploy Runtime을 시작하지 않는다.

## 판정

`STATIC_SOURCE_PREP=STARTED / RUNTIME=NOT_RUN / REVIEW=PENDING / MERGE=PENDING / DELIVERY_GATE=NOT_VERIFIED`
