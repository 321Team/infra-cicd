# Deploy Workflow와 인증 전달 경로

관련: infra-cicd #11, baseline #24. PR66의 [Session](deploy-session.md)에 의존한다.
이 문서는 구축한 실행 경로와 운영 전제이며 현재 배포 GO가 아니다.
코드 병합, 독립 Source 승인, 새 Push 증적/입력 승인, 비중첩 실행창을 별도로
확보해야 한다. 기존 인증 probe의 성공이나 과거 Push 승인을 재사용하지 않는다.

## 실행 경로와 신뢰 경계

`app-deploy.yml`은 main의 수동 dispatch/attempt1만 허용한다. 새 checkout의
HEAD/Workflow SHA/Run/Attempt와 clean 상태를 검사한다. 입력 record 해시는
외부에서 검토한 값이며, record 내부 plan/approval/push 영수증의 바이트를 묶는다.
plan은 실제 dispatch 이후의 Run ID를 요구하지 않는 기존 v2 기준을 유지한다.

- `execute=false`(기본): 정상 Runner에서 입력/CA/시간/실행 결속만 검사한다.
  승인 GitHub 조회, 소켓 열기, TokenRequest, claim, API GET/PATCH는 하지 않는다.
- `execute=true`: GitHub #11 승인 원문을 먼저 대조하고 private Unix socket을 연다.
  기존 ansible01 broker가 CP1/Runner에 각각 기존 SSH 신뢰로 접속한다.
  CP1 `NativeAccess`가 최신 SA UID/권한을 확인하고 기존 TTL900 발급/TokenReview를 수행한다.
  Runner는 token과 review를 비공개 채널로 받아 Session의 Credential에서 재검증한다.
- broker는 Run/Attempt/Workflow SHA, record/plan hash, SA UID를 외부 기대값과 비교한
  후에만 발급을 요청한다. CP1 최신 UID가 다르거나 남은 승인/발급 창이 640초 미만이면
  발급하지 않는다. Session 기본 요구 여유는 기존 2×300+30초이며 실제 잔여 수명도 검사한다.
- 매 API GET/PATCH의 승인 재조회·만료·영속 claim·checkpoint 및 복구 통제는 Session이
  그대로 수행한다. TokenRequest status와 JWT 중 이른 만료를 적용한다.
- broker의 `REVIEW_RESPONSE_RECEIVED`는 토큰/검증 응답 전달 확인이다. CP1 연결은 이때
  정리하고 rollout 결과는 Workflow의 별도 result와 journal에서 확인한다.

SO_PEERCRED는 같은 정상 Runner UID만 허용하며 directory0700/socket0600이다.
이는 같은 UID의 악성 프로세스를 격리하는 보안 경계가 아니다. 해당 계정과
ansible01 broker 소스/세션은 신뢰된 실행 주체라는 기존 전제가 필요하다.
Runner는 CP1 관리자 kubeconfig나 TokenReview 권한을 받지 않는다.
토큰/TokenReview 원문은 pipe/socket 메모리에서만 중계하며 argv/파일/로그에 쓰지 않는다.
GitHub token은 실행 step만 사용하고 시작 시 환경에서 제거한다.

## 현장 준비 — 실행 전 확정할 항목

| 위치 | 계정과 전제 |
|---|---|
| Runner `cicd-runner01` | `server01_cicd-runner`, UID1000. 기존 Actions labels/정상 HOME0700 |
| ansible01 | `server01_ansible`, 검토된 새 소스 checkout 및 외부 source/bundle hash |
| CP1 `10.1.93.102` | `server02`, 기존 검증된 kubeconfig/CA와 TokenRequest/TokenReview 권한 |
| SSH | 기존 known_hosts/개인키 경로. 새 trust/키/RBAC를 자동으로 만들지 않음 |

CA 경로/해시는 기존 `transfer_native_probe.py`의 CP1_CA/RUNNER_CA/CA_SHA를 사용한다.
변경됐다면 해당 상수와 근거를 먼저 재검토한다. 기존 source bundle은 7개 파일의
정확한 Git blob과 source SHA를 포함하는 `P1-CICD-native-probe-source-1` 형식이다.
배포 broker/Workflow 소스도 동일 검토 commit에서 실행해야 한다. bundle 내부 source
SHA 문자열만으로 독립 Source 승인이나 Git commit 소속을 증명하지 않는다.

Runner HOME의 `FDS_CICD_DeployInputs/`는 0700, record는 소유자만 읽는 0600이다.
입력 형식(자리표시자는 실측/승인 원문으로 교체하고 전체 파일 해시를 독립 검토):

```json
{
  "schema": "P1-CICD-deploy-inputs-1",
  "plan": "<정확한 v2 plan UTF-8 원문>",
  "approval": "<정확한 v2 approval UTF-8 원문>",
  "push_receipt": "<새 Push receipt UTF-8 원문>",
  "sa_uid": "<최신 승인된 cicd-runner-sa UID>"
}
```

approval은 `infra-cicd/issues/11#issuecomment-N`의 현재 Lead 원문이어야 한다.
Workflow token의 교차 저장소 권한을 가정하지 않는다. Source 승인과 전역 작업창
비중첩은 이 도구가 자동 확인하지 않는다. `p1-app-deploy` concurrency는 Deploy끼리만
직렬화하며 Harbor/타 저장소 작업창 잠금을 대체하지 않는다.

## 허용된 창의 실행 순서

1. source/실행 승인·새 Push 증적·원복 범위·비중첩을 확정한다. 기존 SSH 인증과 경로,
   CA 및 정상 계정 상태를 읽기 전용으로 확인한다. 토큰 발급은 아직 하지 않는다.
2. 병합 후 실제 main SHA에 맞는 record를 준비하고 `execute=false`로 dispatch한다.
   정상 기준은 `PREFLIGHT=SUPPLIED_BYTES_MATCH`, `TOKEN_ISSUED=false`, `DEPLOY=NOT_EXECUTED`이다.
3. 별도의 새 Run을 `execute=true`로 dispatch한다. 첫 attempt만 허용한다. Runner는
   출력한 `EVIDENCE_DIR`에 `dispatch.json`을 저장하고 최대120초간 broker를 기다린다.
4. ansible01에서 비민감 binding 파일을 준비한다. `dispatch.json`의 `binding` 객체와
   내용이 정확히 같아야 하며 Workflow Run과 record/plan hash/SA UID를 독립적으로 대조한다.
   binding 파일 해시도 외부 기대값으로 고정한다. 기존 보호된 bundle과 함께 로컬 검증:

```sh
python3 -I -B -c 'import sys; sys.path.insert(0,"tools/delivery"); import transfer_deploy_credential as t; raise SystemExit(t.main())' \
  --bundle "$BUNDLE" --bundle-sha256 "$BUNDLE_SHA256" --source-sha "$REVIEWED_SHA" \
  --binding "$BINDING" --binding-sha256 "$BINDING_SHA256"
```

5. 같은 검토 입력에 `--execute --authorization-expires "$ISSUANCE_WINDOW_END"`
   `--cp1-key "$EXISTING_CP1_KEY" --runner-key "$EXISTING_RUNNER_KEY"`를 명시해
   발급/전달을 한 번만 수행한다. 값들은 실제 승인된 창과 기존 파일 경로여야 한다.
   암호/토큰/개인키 내용을 인자에 넣지 않는다. 지금 이 문서 작성으로 실행을 승인하지 않는다.
6. broker 전달 확인과 별도로 Runner `result.json` 및 고정
   `FDS_CICD_DeployClaims/` journal을 보존한다. Workflow 성공은 `ROLLOUT=READY`,
   Session 정상 반환, 정리 확인이 모두 필요하다. HEALTH_E2E/Delivery 인수는 별도이다.

## 실패·정리·복구

타임아웃/UID·해시·승인 변경/SSH drift/검증 오류는 중단하며 자동 발급 재시도나 Workflow
rerun을 하지 않는다. 기존 native TokenRequest는 kubectl 명령 1회 호출이지만 client-go
내부 재시도까지 정확히 1회임을 보장하지 않는 한계가 동일하게 남는다.
전달 실패 후에도 토큰이 이미 발급됐거나 Workflow가 진행했을 수 있다. broker 상태만으로
DEPLOY 미실행을 단정하지 말고 해당 Run/result/claim 및 현재 Deployment를 확인한다.

소켓은 이번 실행이 만든 inode일 때만 제거하며 기존/교체된 소켓은 지우지 않는다.
claim/journal은 실패해도 유지한다. 부정확한 상태에서 claim 삭제나 임의 재시작을 하지 않는다.
로컬 참조 해제는 물리적 메모리 소거·서버 토큰 폐기와 다르며 토큰은 원래 만료까지
유효할 수 있다. 모든 실제 복구는 기존 승인 범위와 살아 있는 자격증명을 요구한다.

## 검증 범위

Linux `python3 -m unittest discover -s tools/delivery -p 'test_*.py'`.
새 시험은 실제 Unix socket/SO_PEERCRED, 별도 Python process의 bridge pipe 교환,
POSIX claim/checkpoint와 MOCK GitHub/CP1/Kubernetes를 결합한다. 승인 변경, 짧은 창,
다른 Run/SA/token, 입력 권한/경로/해시, 전달 중단, cleanup 실패를 포함한다.
현장 SSH, 실제 native token 발급, TLS/API 인증 및 rollout은 미실행이다.
