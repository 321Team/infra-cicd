# P1-CICD-01 — Wolfi/apko schema v3 config handoff

## 현재 판정

이 디렉터리는 **실행 가능한 active config가 아니라 다음 Gate의 입력 handoff**다.
기존 `config/app-build-scan.json`(schema v2)은 이 PR에서 변경하지 않는다.

## 고정 입력

- Approved App Source: `321Team/fds-msa@1c7f3651504097096c3a3fe4a86ac1973e169688`
- Source approval: `supreme33-kr`, fds-msa PR #9 exact-head APPROVE
- Supply tool: `321Team/fds-msa@252858fc838cede5de7c7c20da72082e647d5dd3`
- Supply approval: `hayeounglee`, fds-msa PR #11 exact-head APPROVE
- Artifact ID: `10930034690`
- Artifact ZIP SHA256: `8ba5e7f7a0cf62f149aae8821582dd3330c7d26c1b407a55a6107d464c872a12`
- `SUPPLY_MANIFEST.json` SHA256: `c0e395c78e539136f493ec313c5ffa6aa8b896c7bafc258942e28dfd7d97268f`
- Artifact retention: 2026-10-04

`SUPPLY_SOURCE.json`이 위 값을 기계 판독 가능한 형태로 고정한다.

## 시험범위 Gate

새 Source에는 두 서비스 모두 `tests/test_dependency_upgrade_contract.py`가 존재한다.
과거 P1-CICD-01 기록에는 새 Source에 transaction-api Unit NOT_APPLICABLE을 자동 승계하지
않는다고 명시되어 있으므로 `TEST_SCOPE_PROPOSAL.json`은
`PENDING_APP_LEAD_CONFIRMATION`으로 둔다.

제안 범위:

- transaction-api Unit: NOT_APPLICABLE 재확인 필요
- transaction-api Contract: dependency upgrade + health + metrics
- fds-engine Unit: `tests/test_rules.py`
- fds-engine Contract: dependency upgrade + health + metrics

App Lead가 승인하면 proposal의 `status`를 `APPROVED`로 바꾸고
`approval.approved_by`, `approval.approval_reference`를 실제 승인 근거로 채운다.

## materializer

`tools/delivery/materialize_app_build_scan_v3.py`는 다음을 모두 만족해야만
schema v3 config를 생성한다.

1. artifact ZIP size/SHA256 exact match
2. ZIP path/link/중복/expanded-size fail-closed
3. `SHA256SUMS`의 파일셋 및 각 바이트 hash exact match
4. `SUPPLY_MANIFEST.json` hash exact match
5. approved Source / supply tool / policy binding exact match
6. Test Scope status=APPROVED 및 승인자/reference 존재
7. 생성 결과가 `build_scan.validate_config()` 통과

실행 성공은 **config materialization PASS**일 뿐 DB preflight, Test→Build→Scan,
Harbor Push, Deploy 또는 Delivery PASS가 아니다.

## 후속 순서

1. App Lead test scope 승인
2. 본 tooling PR 독립 리뷰/병합
3. artifact를 `cicd-runner01/server01_cicd-runner`에 전달
4. materializer로 신규 supply directory + v3 config 생성
5. 생성 config SHA/원본 supply hash 수신 검증
6. active config 전환 PR 독립 리뷰
7. on-prem DB preflight + Test→Build→Scan

실패 시 기존 v2 active config와 기존 runner supply는 변경하지 않는다.
