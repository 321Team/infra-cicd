# P2-CICD-01 배포 입력 사전점검

CODEX_ASSISTED. 책임 Lead 이권욱. [baseline #46](https://github.com/321Team/fds-project-baseline/issues/46)의 Target Adapter 입력을 **로컬에서 대조하는 도구**다. 실제 P2 adapter/transport/workflow 구현이나 Runtime Gate가 아니다. Phase 1 파일·코드·운영 작업은 변경하지 않는다.

기준: [Phase 2 입력 계약](https://github.com/321Team/fds-project-baseline/blob/aad579e47afb4f8edd45ebeb999523d7499062eb/phase2/preparation/20261008/delivery-test-contract.md), baseline 문서 PR153 merge `88f7f3206ad44f08a3225982fb700590161b1827`. 소비 기준 infra-cicd main `4e9324cd8acf0ba67b35f950b4c952db8064540e`. 발행 정본/AC는 변경하지 않는다.

## 실행 위치와 방법

대상은 권욱의 로컬 작업 PC와 이 저장소 checkout이다. 일반 사용자 권한·Python 3 표준 라이브러리만 사용한다. AWS/ROSA/Runner 접속이나 credential은 필요 없다.

1. [미입력 양식](../../templates/phase2/delivery-intake.pending.json)을 저장소 밖 작업 경로에 복사한다.
2. 실제 생산 자료가 도착한 항목만 채운다. 미수신 값은 null로 유지한다. 토큰/비밀번호/개인키/kubeconfig/인증 헤더는 입력하지 않는다. credential에는 기존 보관 위치의 reference만 넣는다.
3. 저장소 루트에서 아래 명령으로 읽기 점검한다. 출력은 고정된 진단 코드만 포함한다.

```text
python tools/phase2/delivery_intake.py <로컬-입력-JSON-경로>
python -m unittest discover -s tools/phase2 -p "test_*.py" -v
```

입력 파일 내용이나 URL의 증적을 자동 다운로드하지 않으며, 환경변수/credential store/P1 실행 모듈을 읽지 않는다. 파일을 수정하지 않으므로 서버 복구 절차는 없다. 도구 오류·형식 오류·누락은 중단하고 입력 생산자와 대조한다. 운영 변경은 이 도구로 수행할 수 없다.

## 진단 범위

| 입력 | 접수·대조 | 실제 검증은 별도 |
|---|---|---|
| target | ROSA 표시, cluster/CA 참조, HTTPS API origin, context/namespace, API health URL | 실제 identity·CA/TLS·API 권한·route. 알려진 P1 API를 ROSA로 재사용하지 않음 |
| source | APP SHA, portable manifest repo/SHA·base/overlay 경로·rendered hash | 원본 다운로드·렌더 재현·내용 승인 |
| registry | 선택 endpoint/D22 참조, 두 workload의 승인·원격·이전 digest | D22 Final·인증·cold pull·실제 Registry digest |
| credential | 보관 reference와 정책 참조만 | 발급자/audience/TTL/만료·최소 RBAC·안전 전달/회수 |
| entry | platform/App/실행 범위 참조 | 해당 링크의 실제 수용 및 최신 상태 |
| build/push | 독립 run ID/attempt/workflow SHA, 동일 APP source, PASS 선언 | Run 원본·서명/해시·Build/Scan/Push 결과와 연속성 |
| workloads | 두 개의 서로 다른 Deployment, container, immutable image·remote 일치, Test/Scan 선언·원본 참조, 복구·health 계약 참조 | 실제 UID/resourceVersion/current image·Pod imageID·Health/Rule·복구 호환성 |

`target.health_url`은 API의 승인 HTTPS health endpoint다. engine에 외부 Route를 요구하지 않는다. 각 `health_contract_reference`는 내부 engine 관측 방법을 포함한 #43 계약을 가리킨다. 이번 도구가 health probe를 실행하지 않는다.

Registry 서비스/ROSA namespace/실제 endpoint를 새로 선택하지 않는다. digest 형식과 문자열 결속만 확인하므로 공격자가 형식에 맞춘 허위 선언을 넣으면 일관성 판정이 나올 수 있다. 이를 실제 승인 검증으로 소비해서는 안 된다. 원본 scan 결과의 archive digest와 원격 manifest digest가 항상 같다고 가정하지 않으며, 원본 간 검증은 향후 공급 인수 단계에서 수행한다.

## 출력과 종료코드

10/14 보완: 사전점검에서도 소비 계약의 namespace `fds-app`을 고정 대조한다. `rosa-app`은 overlay 이름이므로 `P2_NAMESPACE_REQUIRED`로 거부한다. 이전 형식검사만으로 임의 namespace가 일관적이라고 표시되던 차이를 수정했다. `fds-app` 문자열 일치는 actual cluster/namespace UID 확인이나 배포 승인을 뜻하지 않으며, 미수신 template의 namespace는 계속 null이다.

- RC 2 / `INCOMPLETE_OR_INCONSISTENT`: 미수신·실패·불일치·잘못된 형식. 배포 준비 미충족 항목을 생산자에게 돌려보낸다. 양식은 의도적으로 이 결과를 낸다.
- RC 0 / `DECLARED_INPUTS_CONSISTENT`: 제출된 문자열/형식만 일관적. **실행 허가·Readiness PASS·Runtime PASS가 아니다.**
- 모든 결과에 `execution_authorized=false`, `external_references_verified=false`, `runtime=NOT_RUN`을 유지한다. 실행 계획·명령·credential을 출력하지 않는다.

unknown field/JSON 중복 key·NaN/Infinity·128KiB 초과·잘못된 UTF-8은 거부한다. 출력에는 입력값/unknown key 이름/예외 원문을 복사하지 않는다. 비밀 검출기는 아니므로 형식에 맞는 참조 문자열 속 비밀까지 검출한다고 보장하지 않는다. 민감 파일을 입력하지 않는 운영 경계를 유지한다.

## 검증·리뷰·후속

합성 단위/CLI 시험은 정상 선언의 비승인 유지, 필수 입력 누락, LAB/P1 혼용, 실패 Test/Scan/Push, 서로 다른 source·digest, rollback 누락, mutable/외부 Registry, 잘못된 URL·경로·JSON, 무수정 읽기와 오류 비노출을 확인한다. 합성 시험 값은 Runtime 증적이 아니다.

새 P2 CI는 GitHub-hosted Runner에서 이 시험만 실행하며 self-hosted Runner·Secrets·workflow_dispatch·배포 단계가 없다. 기존 source-validation CI가 PR에서 자동 실행되더라도 P1 Runtime 실행으로 표시하지 않는다. CI workflow를 포함하므로 이 PR은 Pre-check 무리뷰 병합 예외를 적용하지 않고 기존 독립 코드 리뷰 경로로 보낸다.

담당자가 실제 #38/#42/#43 자료를 인계하면 이 양식으로 누락/불일치를 먼저 확인한다. 이후 원본 진위·실제 target/credential 검증, exact SHA 독립 코드 리뷰, claim/lock/checkpoint·조건부 변경·UNKNOWN 조정·실제 transport 및 RUN-A/RUN-B/rollback 구현·실행이 남는다. 이번 일관성 진단을 배포 허가 판단에 연결하려면 별도 구현·독립 검토가 필요하다. 해당 변경 revert로 코드 제거 가능하며 실제 자원 변경은 없다.
