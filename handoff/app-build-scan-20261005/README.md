# 새 apko 공급물 인계 — 2026-10-05

source/supply_tool: 9658255ce9e39167cc8876a38b98aeb1f0311694
공급물 Run: https://github.com/321Team/fds-msa/actions/runs/37317542246
artifact: 11348745659
manifest SHA256: 3c792ed983e785980a0fa3fc8cb45d08b54bdef57ccbe45288a9adbc169a46ee

## 실제 검증
사용자 PC 다운로드 폴더의 SHA256SUMS 125개 항목 및 manifest 해시 일치. profiles/requirements는 이전 공급물과 동일.
ZIP 해시는 GitHub API/업로드 로그 근거이며 로컬 ZIP 해시 검증은 미실행.

## 승인과 변경
APP Lead 이재환의 새 source 시험 범위 확인: https://github.com/321Team/infra-cicd/issues/11#issuecomment-5995840347
contract 각 3파일, engine unit tests/test_rules.py, transaction-api Unit NOT_APPLICABLE을 유지한다. 새 base로 Test→Build→Scan 결과는 새로 판정한다.
config/app-build-scan.json과 app-build-scan.candidate.json은 같은 전환안이다. APPROVED_FOR_TEST_BUILD_SCAN은 APP 인계 범위이며 이 PR의 독립 코드 리뷰, 병합, Runner 배치 또는 현장 실행을 완료했다는 뜻이 아니다.

## 현장 선행조건
새 공급 경로: /home/server01_cicd-runner/FDS_CICD_Runtime/FDS_CICD_ApkoSupply.9658255ce9e3.3c792ed983e7
아직 Runner 배치/생성/권한 검증은 미실행이다. 원본을 새 경로에 배치하고 해시·소유자·권한·링크를 검증한다. 기존 공급 폴더는 보존한다.
최신 PR HEAD 독립 리뷰·병합과 현장 조건 확인 전에는 app-test-build-scan workflow를 dispatch하지 않는다. 해당 workflow에는 Push/Deploy가 없다.
온프렘 Test→Build→fresh scan 후 새 Push 입력과 별도 승인을 준비한다. 기존 22:21:09 KST 만료 승인은 재사용하지 않는다.
DB sslmode/Harbor TLS 및 OpenSSL 라이브러리 변경 영향은 해당 Runtime 회귀에서 확인한다.

## CI 정합
기존 test_push_images의 malformed digest 시험은 gate가 안전하게 BLOCK을 반환하는 현재 동작과 기대 코드가 달랐다. 운영 gate는 변경하지 않고 DELIVERY_GATE_BLOCK과 PUSH=NOT_EXECUTED, 스캔/registry 호출 0을 검사하도록 정합화했다.

## 복구
미병합 상태는 main/Runner 영향 없음. 병합 후 실행 전 문제 발견 시 dispatch를 중단하고 config 전환 커밋을 revert한다. 새 공급물/로그는 보존한다. 실제 Push/Deploy/기존 이미지 삭제는 이 변경에 없다.
