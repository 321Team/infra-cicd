# Run37331796555 이후 Deploy 준비 — 실행 승인 아님

기준: 2026-10-06 KST, infra-cicd main966a268bdf002f537d6d21d1eec87dfd116f27a5.
사용자 후속 착수 범위의 정적 준비 문서. AC·담당자·권한·일정 변경 없음.

## 확보한 입력

source9658255ce9e39167cc8876a38b98aeb1f0311694 / Build37322456303/1 / Push37331796555/1.
Push/Scan/정리 PASS. 원본 영수증8파일 회수 SHA2569f937675b6bd96ff989ce36cfef7a20a816036888a3869a9a31d2fc7a467d2d4.
수신 검증 자료: https://github.com/321Team/infra-cicd/blob/9bbb7655e85a3579e7599497615cc83d078e0601/handoff/push-review-37322456303/RECEIPT_REVIEW.json
원본 OCI/report/DB 본체 미포함, 독립 Runtime 증적 인수 대기. Push 승인 잔여시간은 Deploy 승인으로 사용하지 않는다.

| 대상 | 신규 Harbor manifest digest | 이전 Runtime digest |
|---|---|---|
| fds-app/transaction-api / container transaction-api | sha256:fa18dc36db8de35fb29ae9f5ec7bea7b7a6a0d0cd293649a79ed0d4dbb093b50 | 미확인 |
| fds-app/fds-engine / container fds-engine | sha256:d77b80954fec5c408fb0bb91995d5faf3155ef2b74c76bc4f7e95cf319249e5b | 미확인 |

## 실제 코드 상태와 필수 해소 사항

1. 해당 main의 workflow 목록과 tools/delivery 전체에서 Deploy/rollback adapter는 없다. delivery_gate.py는 입력 평가기이며 kubectl mutation을 실행하지 않는다. 이미 구현된 기능인 것처럼 dispatch 명령을 만들지 않는다.
2. pre_deploy 계약은 Push receipt·현재 이전digest·RBAC·Worker Pull·배포 lock·Token TTL3600·Token audience·동일 실행 context 승인 연결을 요구한다. 미확인값을 PASS로 채우지 않는다.
3. 기존 정책 기준 infra-kubernetes@7329bf2eb988b552483af48ff660d6a3f1f1d4f5/manifests/06-rbac.yaml의 cicd-runner-sa는 fds-app의 두 Deployment get/update/patch만 허용한다. 배포 과정에서 create/delete/Secret/exec/Pod list·watch 권한을 새로 요구하지 않는다.
4. readiness는 기존 get 권한을 활용한 polling 설계를 우선 검토한다. Pod imageID/노드 증적은 기존 권한을 가진 K8S/APP 검증 주체와 생산 경로를 연결한다. 관측에 필요한 권한을 Runner에 임의 추가하지 않는다.
5. create-secrets-safe.sh는 --audience 인자를 요구하지만 정책에 특정 기대값이 고정되지 않았다는 기존 Gate 한계가 있다. 승인된 API audience 또는 동등 Runtime 인증 검증 근거를 K8S/SEC에서 확보하기 전 실제 토큰 사용을 승인한 것으로 처리하지 않는다.
6. 현재 Deployment UID/resourceVersion/generation·컨테이너명·image·replica 상태·API endpoint를 읽기 전용 수집한다. 현재 image가 tag라면 그 값을 rollback digest로 꾸미지 않고 실제 대응 manifest 근거를 확보한다.
7. 이전 digest는 신규 digest와 달라야 한다. 이전 이미지의 registry 보존/Pull 가능성, 앱·DB 호환성 및 복구 Health/E2E 기준을 APP Owner와 확인한다.
8. 코드 준비 후 독립 기술 리뷰/CI 및 별도 Runtime 실행 범위·변경창·중단·복구 승인을 받는다. 지금 kubectl patch/set image/rollout undo를 실행하지 않는다.

## 구현 방향 — 검토 제안, 아직 미구현

- 승인 source/Run/입력해시/Push receipt 및 원격digest를 고정하고 실제 API endpoint/namespace/Deployment/container/UID/이전 image를 사전 대조.
- 동시 배포 lock 및 변경 직전 UID/resourceVersion/image 검사로 drift를 거부. 임의 최신 상태 덮어쓰기 금지.
- exact container image만 digest로 변경. 승인된 보안 설정/환경변수/Secret/SA를 함께 재적용하지 않음.
- Deployment get 기반 observedGeneration·updated/available replica 확인 및 제한시간 적용. rollout 상태만으로 앱/DB TLS 성공 판정 금지.
- 단계별 사전·변경·readiness·외부 Health/E2E·rollback 증적과 종료코드를 분리. 부분 배포 실패는 서비스별 결과 보존.
- 복구는 승인된 previous digest를 명시적으로 적용하고 상태·Health/E2E를 재검증. 무조건 rollout undo에 의존하지 않음.
- 단기 인증자료는 승인된 임시 저장·회수 방식만 사용. 원문 토큰 로그 금지, SIGKILL/host failure 정리 한계 기록.

## 다음 담당·완료조건

- 이권욱/CICD: 읽기 전용 Runtime 기준선 확보, adapter 구현과 검증/리뷰, 실행안 준비.
- K8S/SEC 기존 담당: audience·실제 RBAC/API 인계 및 동시 변경창 확인. 새 권한 확대를 전제하지 않음.
- 이재환/APP: 새 Python/OpenSSL의 DB sslmode·라이브러리 영향, Health/E2E·이전digest 호환성 검증 범위.
- 이하영/HBR: 두 신규 앱 digest의 Harbor Artifact/Scanner 인수 연결. 기존 fds/test 증적을 앱에 재사용하지 않음.
- 실측 실행과 독립 인수 전 DEPLOY/rollback=NOT_EXECUTED, DELIVERY_GATE=NOT_VERIFIED 유지.

참조: docs/delivery-runtime-gate-20261002.md, tools/delivery/delivery_gate.py,
infra-kubernetes@7329bf2.../manifests/06-rbac.yaml 및 scripts/create-secrets-safe.sh.
AI usage: CODEX_ASSISTED.
