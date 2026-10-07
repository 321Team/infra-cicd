# adc90410 Test / Build / Scan 실행 입력 전환안

CODEX_ASSISTED. 이 문서는 검토 가능한 준비안이며 실행 GO·APP 인계 승인·독립 코드 리뷰·병합 기록이 아니다.

## 현재 확보된 것

- APP source: `adc90410f99cb7af5b40bc8ff92c45272e9f8bee`.
- 공급물: artifact11466476253, ZIP SHA256 `2eaed6486b68f9260f735e362b45a167d7f01aa0fa819accf751ea8d74951ad6`.
- 원본 SUPPLY_MANIFEST SHA256 `f4d55db6ecbefe2057cdb7ca6539b6818549744eb8bfc127ba12dcfa4eb6143b`.
- Runner 공급 경로: `/home/server01_cicd-runner/FDS_CICD_Runtime/FDS_CICD_ApkoSupply.adc90410f99cb.f4d55db6ecbe`.
- 126파일 배치·해시·정상 계정 UID1000/파일0600/디렉터리0700 확인은 `STAGED_FILES_RECEIPT.json`에 연결된다. receipt SHA256 `072eb533486fc46d3e66233611f14c886145c01e0000c45b83c2ac6b24c8a64e`.

## 구체적인 변경안

`app-build-scan.adc90410.proposed.json`은 현재 main `1320e17e9d7dd66455e65276267dc2b92517c4b5`의 `config/app-build-scan.json`에서 파생했다. source, 공급 디렉터리, builder/runtime 이미지 식별, apko source input hash를 검수된 새 공급 manifest에 맞췄다. profile/requirements도 그 manifest에서 대조했다. `CONFIG_PROPOSAL.diff`가 실제 변경점이다.

현재 활성 config와 Runner 설정은 수정하지 않았다. 새 파일은 `handoff/` 아래의 비활성 준비 자료다. status는 `PENDING_APP_OWNER_CONFIRMATION`, source 및 transaction-api Unit 예외의 승인자·근거는 null이다. 이전9658255 승인을 새 adc90410 승인으로 전용하지 않는다.

APP Owner 이재환이 확인할 대상:
1. exact adc90410 source와 새 lock 기반 이미지로 새 Test/Build/Scan을 수행할 APP 인계 범위.
2. 각 앱 contract 3파일(`test_dependency_upgrade_contract.py`, `test_health.py`, `test_metrics.py`) 유지 여부.
3. fds-engine Unit `tests/test_rules.py` 유지 여부 및 transaction-api Unit NOT_APPLICABLE의 새 소스에 대한 근거·명시적 확인.

기존 시험 argv/timeout/setup은 변경하지 않았다. source 앱/시험 코드 변경 여부와 무관하게 새 lock의 OS/OpenSSL/TLS 구성 영향은 새 실행 결과로 판단한다. 이전 scan 결과를 새 이미지에 대입하지 않는다.

## 실행 전 남은 조건과 순서

1. APP Owner가 위 정확한 source/시험 범위에 대한 고정 참조를 남긴다. 반대 조건이 있으면 먼저 입력안을 수정한다.
2. 그 참조를 source와 Unit 예외에 결속하고 독립 consumer config 리뷰·필수 CI·권한 있는 병합을 진행한다. PR63은 기존 인계 문서 범위이며 이번 작업에서 변경하지 않았다.
3. 정상 실행 계정/Runner 작업 경로·공급 파일 hash/소유권·여유 공간과 exact workflow/source를 다시 확인한다. producer의 lock freshness 기준과 최신 Trivy DB·Harbor TLS도 실행 시점에 재확인한다. artifact 보관 기간은 lock 유효기간의 대체가 아니다.
4. 다른 운영 시험과 겹치지 않는 실행창 및 기존 runtime 조건을 고정한 뒤 main/manual Test/Build/Scan을 실행한다. 이 준비 파일을 활성 config에 수동 복사해 Gate를 우회하지 않는다.
5. 실행 시각·명령·rc·원본 Test/Build/Scan 로그·새 이미지 ID/digest·Trivy DB 식별·source/config SHA를 보존하고 필수 결과를 대조한다.
6. 새 Push 입력과 Harbor digest, native Deploy 및 최종 Pod imageID 결속은 별도 승인·실행 단계다.

## 이번 검증과 중단/복구

- JSON과 원본 공급 manifest 매핑, 기존 test argv 보존을 확인했다.
- 실제 main validator를 복사한 독립 임시 폴더에서 ansible01/server01_ansible/Python3.12.14로 비실행 모드를 수행했다. 예상대로 rc1/APP_HANDOFF_PENDING으로 거부했다. 원본 stdout/stderr와 validator source hash는 CONFIG_PROPOSAL_VALIDATION.json에 기록한다.
- 이는 승인 누락 시 중단 확인이다. 전체 승인 후 입력 검증, 패키지 설치·컨테이너 Build·취약점 Scan·Runtime 성공을 뜻하지 않는다.
- 현재는 비활성 준비 파일이므로 서버 config 원복은 필요 없다. 승인·freshness·identity/hash·경로 조건이 하나라도 불일치하면 dispatch 전에 중단한다. 실제 설정 반영 후 문제가 생기면 새 dispatch를 중지하고 별도 검토된 config 복귀 절차를 따른다. 기존 공급물/이미지/로그를 삭제하지 않는다.
