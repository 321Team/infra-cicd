# 새 apko 공급물 인계 후보 — 2026-10-05

## 현재 판정
실제 공급물 manifest와 SHA256SUMS 125개 항목을 사용자 PC의 다운로드 폴더에서 검증했다. manifest SHA256은 3c792ed983e785980a0fa3fc8cb45d08b54bdef57ccbe45288a9adbc169a46ee이다. ZIP 원본 해시는 GitHub API/업로드 로그 근거이며 로컬 ZIP 검증은 수행하지 않았다.

source/supply_tool: 9658255ce9e39167cc8876a38b98aeb1f0311694
공급물 Run: https://github.com/321Team/fds-msa/actions/runs/37317542246
앱 소스 PR: https://github.com/321Team/fds-msa/pull/17 (독립 APPROVED 후 병합)
후보 이미지 CI: https://github.com/321Team/fds-msa/actions/runs/37316299351 (PR HEAD 92f2f236 기준, 두 서비스 HIGH/CRITICAL 0)
병합 후 source CI: https://github.com/321Team/fds-msa/actions/runs/37317543208

## 파일과 실행 경계
- app-build-scan.candidate.json: 실행용 config의 제안. status=PENDING이고 승인자/승인 링크를 비워 승인 승계를 방지한다.
- SUPPLY_SOURCE.json: artifact 식별값과 검증 범위.
- 현재 config/app-build-scan.json과 workflow는 변경하지 않았다. 본 후보를 복사하거나 dispatch하지 않는다.
- 새 Runner 공급 경로는 후보이며 디렉터리 생성/배치/권한 검증은 미실행이다. 기존 공급 폴더를 덮어쓰지 않는다.
- 새 Python/OpenSSL/glibc와 이미지 해시를 반영했다. profiles와 requirements 해시는 기존과 모두 동일하다. 서비스별 시험 명령은 유지 제안이다.

## 남은 순서
1. APP Owner 이재환이 exact source의 시험 범위 및 transaction-api Unit NOT_APPLICABLE 유지 여부를 확인한다: https://github.com/321Team/infra-cicd/issues/11#issuecomment-5995562351
2. 회신 근거를 후보에 반영하고 실제 config 전환 diff를 완성한다. 작성자 외 독립 리뷰 전에는 APPROVED_FOR_TEST_BUILD_SCAN으로 선언하거나 main 실행 설정을 바꾸지 않는다.
3. 실제 Runner 계정 server01_cicd-runner/UID1000의 새 공급 폴더에 원본을 배치하고 125개 해시 및 안전한 경로/권한을 검증한다.
4. 최신 config/코드의 리뷰·병합과 현장 실행 조건 확인 후 중앙 app-test-build-scan workflow로 Test→Build→fresh scan을 실행한다.
5. 새 Push 입력/승인은 별도다. 22:21:09 KST에 만료된 기존 승인은 재사용하지 않는다.

실패 시 중단하고 원본 및 로그를 보존한다. 현재는 handoff 후보만 추가하므로 main/Runner의 원복 작업은 없다.
REVIEW=PENDING / RUNTIME=NOT_EXECUTED / PUSH=NOT_EXECUTED / DEPLOY=NOT_EXECUTED.
