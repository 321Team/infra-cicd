# Deploy adapter core — Runtime 미연결

기준: 2026-10-06 KST. main `966a268bdf002f537d6d21d1eec87dfd116f27a5`.
책임: P1-CICD-01 / infra-cicd #11, baseline #16. 관련 준비·실측: PR #53.
AI usage: CODEX_ASSISTED.

## 이번 구현

`tools/delivery/deploy_adapter.py`는 주입된 client/authorize/checkpoint에 대해 동작하는
조건부 변경·준비 상태 확인·복구 상태 처리 라이브러리다.
네트워크 클라이언트, CLI, workflow, TokenRequest, 서버 설치·배포는 추가하지 않는다.
이 모듈을 단독 실행해도 클러스터에 연결하지 않는다.
**완성된 운영 Deploy 실행기 또는 Runtime 승인으로 사용할 수 없다.**

- 고정된 fds-app의 transaction-api, fds-engine만 허용한다. 정확한 UID와
  같은 서비스 저장소의 이전/신규 sha256 digest를 요구한다.
- 두 Deployment의 기준 상태를 모두 확인한 뒤 첫 변경을 시작한다.
- 변경 직전 재조회로 전체 spec·generation·UID·현재 이미지를 대조한다.
  resourceVersion은 opaque 값으로 취급하며 JSON Patch test에 그대로 사용한다.
- JSON Patch는 UID/RV/container name/image를 test하고 해당 image 하나만 replace한다.
  env, Secret 참조, SA, securityContext 등을 재적용하지 않는다.
- get 기반 제한시간 polling으로 observedGeneration, updated/ready/available/total
  replica 일치와 unavailable/terminating 0을 확인한다. Pod list/watch 권한은 필요 없다.
- 각 변경 전 PATCH_PENDING을 checkpoint하고 성공 응답을 검증한 뒤 APPLIED를 기록한다.
- 실패하면 기본적으로 자동 복구하지 않는다. rollback_on_failure와 각 요청 직전
  별도 rollback 권한 검사가 모두 통과한 경우에만 적용 확인된 대상을 역순 복구한다.
- 복구 시 UID/전체 spec/generation drift를 거부한다. 이전 digest만 명시 적용한다.
- PATCH timeout/연결 유실/불완전 응답은 UNKNOWN이다. 늦게 반영될 수 있으므로
  자동 재시도나 복구를 하지 않고 MANUAL_RECONCILIATION_REQUIRED를 기록한다.
- journal 실패도 추가 쓰기를 중단한다. transport 예외 원문, 전체 spec/env,
  자격증명을 영수증에 넣지 않는다.
- 첫 서비스의 readiness를 두 번째 완료 후 다시 확인한다. 복구 완료도 전체 재확인한다.

ROLLOUT=READY는 Deployment 상태만 의미한다.
HEALTH_E2E/POD_IMAGE_ID/DELIVERY_GATE는 항상 NOT_VERIFIED다.
ROLLBACK=READY_HEALTH_NOT_VERIFIED도 앱·DB 호환성이나 E2E 복구 성공이 아니다.
시험의 합성 객체는 실제 배포 증적과 분리한다.

## 호출자 계약과 미완료 통합

다음은 코드 리뷰 및 Runtime 연결 전 구현·검증할 항목이다. 단순 문자열 PASS나
`authorize=lambda _: True`를 운영 승인으로 사용해서는 안 된다.

1. **Gate/실행 식별 분리**: 기존 delivery_gate.py의 workflow/run은 Build Run에 묶여 있고
   deployment_approval도 같은 Run을 요구한다. 실제 Deploy는 별도 Run이므로
   Build identity를 바꾸지 않는 별도 실행 승인 schema가 필요하다.
   source/Build/Push 원본 바이트·해시·digest, Deploy workflow SHA/Run/Attempt,
   승인 참조/만료, 이전 이미지 복구 범위 및 독립 리뷰를 검증해야 한다.
   이번 core는 기존 평가기를 변경하거나 DECLARED 입력을 실제 승인으로 간주하지 않는다.
2. **실제 transport**: 고정 API https://10.1.93.109:6443, CA/hostname 검증,
   redirect/proxy 차단, named Deployment GET과 application/json-patch+json PATCH만.
   요청 제한시간, 응답 크기/JSON 검증, no PATCH retry, 승인 만료시간 내 요청을 적용한다.
   Rejected는 mutation이 없다는 확정 응답에만 매핑한다. timeout/5xx/통신 실패는
   일반 예외로 처리해 UNKNOWN으로 남겨야 한다.
3. **실제 인증**: ci-cd/cicd-runner-sa, [현행 정책](token-policy-900-20261010.md)의 요청900초·실제 수명 상한900초,
   기대 audience https://kubernetes.default.svc.cluster.local,
   토큰 발급/유효기간/인계 및 API 인증 확인. JWT payload decode를 서명 검증으로
   표시하지 않는다. 현재 cp1 impersonation 결과는 Runner token auth가 아니다.
4. **lock/claim/증적**: 실행 잠금과 1회 claim, host/UID/정확한 checkout 확인,
   private 경로/no symlink, 입력 SHA, 원본과 상태별 실행 시각·해시·종료코드,
   atomic write+fsync checkpoint, 실패/시그널/host 중단 후 수동 재대조를 연결한다.
   core는 주입된 checkpoint의 내구성 또는 process 간 잠금을 스스로 제공하지 않는다.
5. **인증자료 정리**: 로그/argv에 secret 노출 없이 사용, finally/시그널 정리,
   SIGKILL/host 실패 시 회수·만료 근거. 토큰 발급·회수 코드는 이번 변경에 없다.
6. **최종 인수**: APP/K8S 담당의 Pod imageID·Health/E2E·DB TLS 및 이전 이미지
   Pull/호환성 확인을 연결한다. 새 Runner 권한을 추가하지 않는다.

이상 미완료 항목이 해소되기 전 workflow_dispatch/실제 배포 명령을 제공하지 않는다.
독립 코드 리뷰와 별도 Runtime 실행·중단·복구 범위 승인은 실제 적용 전에 필요하다.

## 최초 구현 검증 (caaf8e8 이전)


로컬 Python 3.12 / Windows에서:
```text
python -m unittest discover -s tmp/deploy-adapter -p 'test_*.py' -q
Ran 43 tests
OK
```
신규 MOCK 28건 + 기존 delivery_gate 회귀15건. 리포지토리에서는 기존
source-validation.yml의 tools/delivery unittest discovery가 새 시험을 포함한다.
CI 결과는 실행 Run/HEAD에서 별도 확인한다. Runtime=NOT_EXECUTED.

핵심 검증: 두 대상 사전검증, UID/이름/digest/전체 spec/generation drift,
RV 경쟁, 일부/두 대상 역순 복구, 복구 권한 거부/경쟁/실패,
PATCH 응답 유실, readiness timeout, 최종 재확인, journal 실패,
비밀 원문 비노출, 단일 인스턴스 재실행 차단.
합성 대상·transport이므로 TLS/token/Admission controller/실제 네트워크 검증은 아니다.

공식 동작 근거:
- https://kubernetes.io/docs/reference/using-api/api-concepts/
- https://kubernetes.io/docs/concepts/workloads/controllers/deployment/

## 독립 리뷰 보완 — 2026-10-06

supreme33-kr의 [코어 승인 리뷰](https://github.com/321Team/infra-cicd/pull/53#pullrequestreview-5422444114)는
caaf8e8287cdcd9f76d98e754ce58b40c8711617에 한정된다. 다음 변경은 재리뷰 대상이다.

1. 만료/권한 거부에는 리뷰 대안 **(b)**를 적용한다. polling을 포함한 authorize 거부는
   AUTHORIZATION_DENIED + MANUAL_RECONCILIATION_REQUIRED로 끝내고 자동 복구하지 않는다.
   별도 rollback 권한이 남아 있더라도 이 경로에서는 추가 쓰기를 하지 않는다.
   일반 readiness 실패/서버의 확정 PATCH 거부에 대한 기존 별도 승인 복구는 유지한다.
2. PATCH 직전 권한 거부는 NOT_SENT_AUTH_DENIED를 checkpoint한다.
   journal 이후 다시 권한을 검사하므로 journal 지연을 이유로 만료된 쓰기를 허용하지 않는다.
   이 기록도 저장 실패하면 CHECKPOINT_FAILED가 최종 오류이며 마지막 durable journal로 수동 대조한다.
3. remaining_seconds 콜백을 필수화했다. 두 대상 사전조회 후와 첫 PATCH 직전에
   남은 Deploy 승인 시간이 2 × timeout + window_margin 이상이어야 한다.
   margin 기본30초는 구현 기본값이며 팀 승인시간/Token TTL 정책 변경이 아니다.
   Runtime 연결자는 bounded API 호출·journal 저장시간을 포함하도록 margin을 산정해야 한다.
   콜백은 검증된 Deploy 승인 만료시각에서 계산하고 token TTL/고정 상수로 대체하지 않는다.
   bool/NaN/infinity/누락·조회실패는 시작을 차단한다. 시작 여유는 완료 보장이 아니므로
   이후 요청별 권한 검사도 계속 유지한다.
4. cp1-baseline.json에 이미 제출된 00:43:27 KST 콘솔의 두 current_image 원문을 복원했다.
   새 조회 또는 현재 live 값으로 표시하지 않는다. tag@digest/FQDN을 임의 정규화해
   승인된 이전 이미지와 같다고 처리하지 않으며, 실제 실행 직전에 원문을 다시 대조한다.
5. 대상 HPA 부재는 NOT_VERIFIED다. Runtime 사전조건에 **두 Deployment를 대상으로 하는
   HPA 없음 및 작업 중 replica 변경 없음**을 추가한다. HPA가 발견되면 끄거나 삭제하지 말고
   중단하여 별도 변경안을 검토한다. Runner Role에 HPA list 권한을 추가하지 않는다.

### cp1 읽기 전용 확인 절차 (아직 미실행)

대상 server02@k8s-control01, 기존 kubeconfig 및 조회 권한, 작업 경로는 사용자 홈.
먼저 기존 context의 API가 https://10.1.93.109:6443이고 TLS 검증이 유지되는지 확인한다.
다음 출력은 현재 image 원문과 HPA scaleTargetRef를 대조하기 위한 것이며 토큰을 출력하지 않는다.

```sh
kubectl --request-timeout=10s get deployment transaction-api fds-engine -n fds-app -o json
kubectl --request-timeout=10s get hpa -n fds-app -o json
```

정상: 두 UID/컨테이너/image 원문이 승인 입력과 일치하고,
HPA items 중 scaleTargetRef.kind=Deployment, name이 transaction-api/fds-engine인 항목이 없음.
API/TLS/조회 실패·대상 변경·관련 HPA 존재는 STOP이며 미확인을 PASS로 바꾸지 않는다.
조회만 하므로 설정 복구는 필요 없다. 실행 시각/호스트/RC/조회 원본을 남긴다.
current_image/UID 검사는 core의 실행 직전 GET에서도 다시 수행한다.

추가 MOCK7건: polling 중 만료 시 복구0건, 첫/두번째 PATCH 전 거부 시 미전송 기록,
시작 budget 부족/잘못된 값/정확한 경계, journal 지연 후 재검사, provider 실패 비밀 비노출.
이번 세션 로컬 shell이 실행 환경 오류로 열리지 않아 로컬 시험은 미실행이며,
GitHub source-validation CI 결과를 대상 HEAD에 연결한다.
