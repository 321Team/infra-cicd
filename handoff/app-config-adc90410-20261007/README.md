# adc90410 앱 Test / Build / Scan 설정 인계

CODEX_ASSISTED · 2026-10-07 KST. APP Owner의 [소스·시험 범위 확인](https://github.com/321Team/infra-cicd/issues/11#issuecomment-6036043243)을 `config/app-build-scan.json`에 결속한다. `APPROVED_FOR_TEST_BUILD_SCAN`은 이 APP 인계 상태이며 PR 독립 승인·실행창·Runtime 성공을 뜻하지 않는다.

## 변경과 원본 연결

- source: `adc90410f99cb7af5b40bc8ff92c45272e9f8bee`.
- 사전 준비안: [329f835a](https://github.com/321Team/infra-cicd/tree/329f835ac7ed6059807d26e80e83c481f2989be8/handoff/app-supply-staging-20261007). APP Owner가 확인한 공급 디렉터리·이미지 식별·입력 hash를 그대로 사용하며 source/Unit 예외의 승인 참조와 이유만 확정한다.
- 공급물 인계: [PR63](https://github.com/321Team/infra-cicd/pull/63), merge `3f3e27d5612fb20f1478174195ddcc9be633c807`의 `handoff/app-supply-consumer-20261007/`.
- artifact11466476253, 원 ZIP SHA256 `2eaed6486b68f9260f735e362b45a167d7f01aa0fa819accf751ea8d74951ad6`, SUPPLY_MANIFEST SHA256 `f4d55db6ecbefe2057cdb7ca6539b6818549744eb8bfc127ba12dcfa4eb6143b`.
- Runner 배치 경로: `/home/server01_cicd-runner/FDS_CICD_Runtime/FDS_CICD_ApkoSupply.adc90410f99cb.f4d55db6ecbe`. 126파일 배치 기록은 위 PR63 인계 자료와 연결된다.
- contract 각 3파일, fds-engine Unit `tests/test_rules.py`, transaction-api Unit NOT_APPLICABLE, cwd/timeout/setup은 기존 범위를 유지한다. 새 이미지의 Test/Scan은 새로 판정한다.

## 수행한 검증

정상 로그인 ansible01/server01_ansible의 독립 임시 폴더에서 Python3.12로 실제 consumer validator를 `--execute` 없이 실행했다. INPUT_SHAPE=VALID/rc0이며 출력의 SOURCE_APPROVAL=DECLARED_NOT_INDEPENDENTLY_VERIFIED, DELIVERY_GATE=NOT_VERIFIED를 그대로 보존한다. 이는 형식 검증기 출력이며 APP 확인의 원문은 위 댓글로 추적한다.

기존 `test_build_scan.py` 28개와 `test_offline_supply_v3.py` 7개가 모두 PASS/rc0이다. 이들은 MOCK 회귀이며 실제 이미지 load/Test/Build/Scan/Push/Deploy가 아니다. config와 원 공급 manifest의 images/profiles/requirements/source_inputs를 대조했고 시험 명령 보존을 확인했다. `VALIDATION.json`과 원 stdout/stderr를 함께 보관한다. GitHub PR CI 결과는 해당 PR의 exact HEAD check에서 별도 확인한다.

## 다음 실행 조건과 중단·복구

1. 이 config 변경의 독립 코드 리뷰·필수 CI·권한 있는 병합을 먼저 완료한다. 공급물 또는 APP 인계 승인을 이 단계의 승인으로 대신하지 않는다.
2. `cicd-runner01`의 정상 계정 `server01_cicd-runner`/UID1000, 공급 경로의 바이트·권한·여유 공간, reviewed main/workflow SHA와 APP source SHA를 실행 직전에 대조한다. 최신 lock/freshness·Trivy DB·Harbor TLS 및 다른 Runtime과 비중첩인 실행창을 확인한다. 10/7 HBR·SEC 시험창과 겹쳐 dispatch하지 않는다.
3. 기존 main 전용 수동 workflow `.github/workflows/app-test-build-scan.yml`을 해당 실행 허용 범위에서만 사용한다. 준비 브랜치를 서버 활성 config에 수동 복사하거나 PR 코드에서 Runtime을 시작하지 않는다.
4. source/config SHA, Run/Attempt, 정상·실패 명령의 시각/rc/원본 로그, 새 이미지 ID와 scan/DB 정보를 기록한다. 실패 또는 승인·신선도·hash·경로 불일치 시 중단하고 기존 공급물·이미지·로그를 보존한다. 실패했다고 자동 재실행·보안 예외를 만들지 않는다.
5. 새 Push 입력→Harbor digest→Deploy→Pod imageID 연결은 별도 단계다. glibc/OpenSSL/libexpat 변경의 DB sslmode·Harbor TLS Runtime 회귀는 #20과 연결한다.

병합 전에는 main/서버 설정 변경이 없다. 병합 후 config 복귀가 필요하면 이 PR의 config diff만 검토된 revert로 되돌린다. 이전 9658255 설정으로의 복귀 자체가 오래된 공급물의 재실행 승인은 아니므로 freshness/보안 조건을 다시 판단한다. 토큰 발급·Robot 교체·운영 배포·AC 인수는 이번 변경에 포함하지 않는다.
