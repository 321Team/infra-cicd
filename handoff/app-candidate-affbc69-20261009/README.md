# affbc69 후보 공급·시험 입력 — 승인 대기

CODEX_ASSISTED · 이권욱 · 2026-10-09. **PREPARATION / PENDING_APP_HANDOFF**.

기존 [실행 준비안](../../docs/app-candidate-execution-preparation-20261009.md)과 [시험 매핑](../../docs/app-metrics-handoff.md)을 실제 검토 가능한 입력으로 구체화했다. `config/app-build-scan.json`은 변경하지 않았다. 이 폴더는 workflow에서 참조하지 않으며 여기 있는 파일을 승인된 실행 입력으로 사용하지 않는다.

## 파일과 확인 범위

| 파일 | 확정해 넣은 값 | 미확정 값 |
|---|---|---|
| `SUPPLY_SOURCE.pending.json` | 후보 Source affbc69, 실제 supply tool 8eac4c, Run37859961937·artifact11585741989·원 ZIP/manifest hash·크기·API 만료 | Source/tool 승인자·승인 참조는 모두 null |
| `TEST_SCOPE.pending.json` | 기존 명령 형태·cwd·300초 상한, engine Unit/Contract와 API Contract 후보 | status=PENDING_APP_LEAD_CONFIRMATION, 승인자·참조 null, API Unit null |
| `VALIDATION.json` | 고정 Source의 시험 파일8개 SHA256, 중복/누락 없는 매핑, 현재 validator의 대기 입력 거부 결과 | 실제 APP 승인·앱 시험·전체 config 검증·현장 실행이 아님 |

공급 원본 ZIP은 비공개 경로에 보존한다. 이번에 원 ZIP SHA256과 크기를 GitHub artifact API에 다시 대조하고 내부 `SUPPLY_MANIFEST.json`의 Source/tool 결속·hash를 확인했다. 전체125파일/이미지 계층 검증은 기존 [증적183](https://github.com/321Team/fds-project-evidence/pull/183)의 별도 기록이다. API 조회 시 expired=false, 만료는 **2026-10-16 08:32:03 KST**. 만료 이후에도 로컬 보존과 승인 유효성은 별도로 판단한다.

후보 매핑은 engine Unit `test_rules.py`, engine Contract `test_dependency_upgrade_contract.py`/`test_health.py`/`test_metrics.py`, API Contract에 동일3파일과 `test_listener_logging.py`를 포함한다. 기존 Source CI의 engine16+15/API20=51건에 대응한다. 이번에는 시험 파일 집합만 직접 대조했으며 앱51건을 재실행하지 않았다. API Unit에 이전 Source의 NOT_APPLICABLE을 복사하지 않았다.

기존 consumer `9617817e7c15a033259309922aedb675f3fceb8e`의 validator를 호출한 결과:

- `validate_handoff`: `HANDOFF_APPROVAL_INVALID`로 거부.
- `validate_scope`: `TEST_SCOPE_NOT_APPROVED`로 거부.
- 알려진3단계의 `test_step` 형식 검증 통과, 고정 Source의 앱 시험8파일을 중복·누락 없이 배정.

Windows에서는 Unix `pwd` 모듈의 import만 가능하도록 로컬 stub을 사용했고 모든 속성 접근은 실패하도록 했다. 계정 조회·원본 추출·config 생성·materializer main·SSH·Runner 실행을 하지 않았다. 일반 Source CI 통과는 이 대기 입력의 승인이나 전체 설정 검증을 의미하지 않는다.

## 담당자 결정 후 순서

1. 이재환이 [APP27](https://github.com/321Team/fds-msa/pull/27)/[APP28](https://github.com/321Team/fds-msa/pull/28)의 채택 Source와 이 시험 매핑, API Unit 명령 또는 NOT_APPLICABLE 사유를 확정한다. 정확한 SHA·승인자·참조를 연결한다. Source가 달라지면 기존 공급 manifest를 수정하지 말고 영향 및 재공급 필요성을 판단한다.
2. 실제 supply tool `8eac4c982076e3c219840a13686b15c46879981f` 승인 근거를 별도로 연결한다. Source 승인과 supply tool 승인을 혼동하지 않는다. null을 추정값으로 채우거나 status만 APPROVED로 바꾸지 않는다.
3. [PR71](https://github.com/321Team/infra-cicd/pull/71)의 링크 검사 수정 리뷰·병합을 확인하고, 사용할 CICD SHA를 새로 고정한다. 현재 준비 파일의 검증 기준은 위 main이며 PR71을 병합했다고 가정하지 않는다. 실행 상위 경로의 소유·쓰기 권한도 새로 확인한다.
4. 정상 실행 계정·비공개 원본·새 공급 대상과 승인 입력을 검토한 후 기존 materializer로 config를 생성한다. 생성된 config diff를 별도 PR로 검토·반영한다. 공급 폴더가 생성된 후 실패하면 정확한 회차 산출물을 보존하고, 포괄 삭제하거나 자동 재실행하지 않는다.
5. 검토된 main/config·Run/Attempt·freshness·비중첩 창·중단/복구 인수 조건을 확인한 뒤 Test/Build/Scan을 진행한다. 09:10~12:00 창 만료 전에 회수까지 완료할 수 없다면 새 Runtime을 시작하지 않는다. Push/Deploy는 별도 권한·정책·GO가 필요하다.

승인·Source·hash·만료·범위가 불일치하면 중단한다. 이 준비 PR은 실행 설정이나 서버 상태를 바꾸지 않아 Runtime 원복 대상이 없다. [CICD11의 결정 입력](https://github.com/321Team/infra-cicd/issues/11#issuecomment-6072585805)을 소비하며, 승인과 병합을 대신하지 않는다.
