# P1-CICD-01 — Wolfi/apko schema v3 config handoff

## 현재 판정

이 디렉터리는 active config 자체가 아니라 schema-v3 config를 생성하기 위한 승인 입력 handoff다.
Runtime Run #4의 builder timezone DB 결함 remediation 이후 새 exact source/supply로 재고정했다.
`config/app-build-scan.json`은 이 변경에서 수정하지 않는다.

## 고정 입력

- Approved App Source: `321Team/fds-msa@368786c1e36b2bfb5424c4da472c1cc319c5c8c3`
- Source/Test Scope approval: 이재환(`supreme33-kr`), infra-cicd#11 comment `5882412875`
- Supply tool/source commit: `321Team/fds-msa@368786c1e36b2bfb5424c4da472c1cc319c5c8c3`
- Supply delta independent review: 이하영(`hayeounglee`), fds-msa PR #12 formal APPROVED
- Artifact ID: `10968220151`
- Artifact ZIP SHA256: `2f4e8455401e14a89acb662b12f265c30911b7e56127bae9329763d56c72aa10`
- `SUPPLY_MANIFEST.json` SHA256: `24ee3192ecff5c3b8ee7a7038ebd931ee28c6b86dc655850ae0f83b57dddcbce`
- Artifact retention: `2026-10-05T11:29:18Z`
- Runner intake: `cicd-runner01/server01_cicd-runner`, sidecar `sha256sum -c` PASS

`SUPPLY_SOURCE.json`이 위 값을 기계 판독 가능한 형태로 고정한다.

## 시험범위 Gate

기존 승인 source `1c7f365...`에서 신규 source `368786c1...`까지의 compare는 apko/offline-supply tooling 6파일만 변경되며
`transaction-api/`·`fds-engine/` application code, tests, requirements 변경은 0건이다.
APP Lead가 이를 직접 재확인하고 기존 시험범위 유지에 동의했다.

- transaction-api Unit: NOT_APPLICABLE
- transaction-api Contract: dependency upgrade + health + metrics
- fds-engine Unit: `tests/test_rules.py`
- fds-engine Contract: dependency upgrade + health + metrics

## materializer 경계

`tools/delivery/materialize_app_build_scan_v3.py`는 artifact ZIP size/SHA256, 내부 SHA256SUMS,
manifest hash, source/supply-tool approval binding, test-scope APPROVED 상태를 모두 fail-closed로 검증한 뒤
schema-v3 config를 생성한다.

materialization 성공은 DB preflight, Test→Build→Scan, Harbor Push, Kubernetes Deploy 또는 Delivery PASS가 아니다.

## 후속 순서

1. 본 handoff 재고정 PR 독립 리뷰/병합
2. Runner의 신규 ZIP을 materializer로 검증·추출
3. generated v3 config SHA 및 staged supply identity 검증
4. active config 전환 PR 독립 리뷰/병합
5. on-prem Runtime Run #5: DB preflight + Test→Build→Scan
6. HIGH=0 / CRITICAL=0 확인 후 후속 Harbor/Kubernetes Gate

기존 `1c7f365...` 공급물과 config는 새 Runtime 재실행 입력으로 사용하지 않는다.
