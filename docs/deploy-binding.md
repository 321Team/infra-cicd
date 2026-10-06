# Deploy 입력 결박 검증기 — Runtime 미연결

2026-10-06 KST / PR #53 / AI usage: CODEX_ASSISTED.

## 목적과 적용 범위

기존 delivery_gate의 pre_deploy는 deployment_approval을 Build workflow/run에 연결한다.
별도 Deploy 실행을 위해 Build identity를 덮어쓰지 않고 deploy_binding.py에서
Build 원본, Push 원본 영수증, Deploy 실행 context를 구분한다.
기존 Gate의 pre_push 검증은 그대로 활용하며 pre_deploy 승인을 합성하지 않는다.

이 변경은 순수 입력 검증이다. 네트워크/API 호출, 토큰 발급, 실제 승인 조회,
잠금·claim·증적 저장·workflow 연결은 없다. MATCH는 독립 승인이나 실행 허용이 아니다.
core와의 Runtime 연결도 아직 하지 않았다.

## 입력 인터페이스

validate(raw, push_raw, expected_sha256, execution, now)

- raw: P1-CICD-deploy-binding-1 JSON 원본 bytes, 최대1MiB. template_only=false.
- push_raw: 실제 push_images 결과 result.json 원본 bytes. 최대1MiB.
- expected_sha256: raw의 외부 확정 해시. raw 내부에서 추출한 해시로 대체 금지.
- execution: 신뢰된 Runtime 호출자가 확인한 repository/ref/workflow_sha/run_id/attempt.
  repository=321Team/infra-cicd, ref=refs/heads/main. run_id/attempt는 양의 정수.
- now: timezone-aware 실제 시각. 테스트 고정 시각을 운영에 주입하지 않는다.

raw 문서의 필드:
- delivery: 원본 Build/source/scan/Harbor 계약. 기존 pre_push 조건과 Build identity를 유지.
- execution: 위 별도 Deploy context와 정확히 일치해야 한다.
- push_receipt_sha256: Push 원본 bytes 해시.
- push_execution: Push result.execution_context의 workflow_sha/run_id/attempt.
  기존 Push 산출물과 맞춰 run_id는 숫자로 된 문자열, attempt는 양의 정수다.
- kubernetes: API/SA/audience/TTL/policy_reference.
  API=https://10.1.93.109:6443, SA=ci-cd/cicd-runner-sa,
  audience=https://kubernetes.default.svc.cluster.local, 기존 TTL3600 및 pinned policy.
- targets: 정확히 transaction-api/fds-engine. 각 namespace/deployment/container/uid/previous_image.
  previous_image는 승인된 registry의 전체 digest 참조이며 임의 정규화하지 않는다.
- approval: approved_by/reference/execution/push_receipt_sha256/targets/images/
  allow_rollback/not_before/expires_at. 두 방향 이미지와 Push 해시, Deploy 실행을 결박한다.
  reference는 321Team 저장소의 GitHub issue/PR URL만 허용한다.
  allow_rollback은 boolean이어야 하며 문자열이나 누락을 참으로 변환하지 않는다.

검증: JSON 중복 키·NaN/Infinity 리터럴·형식 오류 거부, 입력 및 Push bytes 해시,
Build 원본과 Push build_workflow 일치, Push 실행 context, 두 이미지의 저장소/digest/PASS,
Deploy Run과 Build/Push Run 분리, 승인 context/이미지/대상/기간 일치를 확인한다.
출력 remaining_seconds는 호출 당시 계산값이며 Adapter 콜백에 고정값으로 재사용하면 안 된다.
실행 연결 시 매 호출마다 신뢰된 시계와 검증된 만료시각에서 다시 계산한다.

## 검증 결과의 한계 및 다음 연결

SUPPLIED_BYTES_AND_DECLARED_APPROVAL / NOT_INDEPENDENTLY_VERIFIED /
runtime_enforcement=NOT_INTEGRATED / delivery_gate=NOT_VERIFIED를 명시한다.
외부 해시와 실행 context의 신뢰성, GitHub 독립 승인/참조 실재, 원본 Build·Scan 증적,
HPA 부재·Worker Pull·이전 이미지 재Pull/호환성은 이 함수가 외부에서 확인하지 않는다.
입력 문서에는 실제로 확인하지 않은 PASS나 승인자를 채우지 않는다.
원격 Harbor digest 재조회, TLS transport, 실제 token 수명/회수,
lock/claim·내구성 checkpoint, workflow, APP Health/E2E 연결은 잔여다.

## 시험

test_deploy_binding.py 17개 MOCK 시험을 기존 source-validation CI discovery에 추가한다.
정상 분리, SHA/Run/Attempt 변조, bytes 변경, Build identity 변조, Push 실패/digest 변조,
이전 image 오류, 만료/미래/naive 시각, audience/API/TTL/SA 오류,
중복키/JSON 오류, URL 유사 호스트, rollback 타입, 필수항목 누락을 검증한다.
실제 자격증명·운영 승인 입력·서버 명령은 포함하지 않는다.
