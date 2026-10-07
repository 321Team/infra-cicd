# 새 APP 공급물의 CICD 소비 준비 — 2026-10-07

책임: 이권욱(P1-CICD-01), APP Source/test 인계 이재환, 기술 지원 이하영. CODEX_ASSISTED. 이 문서와 CONSUMER_INTAKE.json은 Review 기록이며 app_workflow.py가 소비하는 config가 아니다.

## 생산자와 소비자의 현재 상태

생산자 [fds-msa#21](https://github.com/321Team/fds-msa/pull/21)은 독립 리뷰 후 main 0494ca279882d4e0cab24fb5a25a136a2e8f46c1로 병합됐다. 리뷰 eea60c70과 merge0494ca의 tree 9a5f1a91c3428a4321ccc90ebf3999284f5b9f7b가 동일하다. actual main [source37569632325](https://github.com/321Team/fds-msa/actions/runs/37569632325)·[supply37569632028](https://github.com/321Team/fds-msa/actions/runs/37569632028)는 SUCCESS다.

현재 소비 config/app-build-scan.json은 source9658255ce9e39167cc8876a38b98aeb1f0311694와 이전 공급물을 사용한다. 새 Source/test scope의 APP Owner 확인은 아직 별도 기록이 없다. compare9658255→0494ca는 ahead9/behind0, 변경12파일이며 두 앱 디렉터리 변경0이다. 테스트 명령 유지가 합리적인 제안이지만 이전 SHA의 승인을 자동 복제하지 않는다.

## 실제 확인한 공급 연결

- artifact11460655012 / 109784510 bytes / ZIP digest348020907b8bed2dea34c886af0807ccd18a7d847bf4dd9b8fe5bf56d889b64d / Run37569632028·job112625094511.
- 이번 직접 확인은 Run/Job/Artifact API와 job 원본의 source pin·OFFLINE_SUPPLY_CONTRACT=PASS·builder/runtime archive hash다.
- [기존 공급 검수](https://github.com/321Team/fds-project-baseline/issues/16#issuecomment-6030778504)의 125파일·106wheels·OCI2 closure는 기존 기록을 연결했다. 이번 세션에서 ZIP을 로컬로 내려받아 내부를 다시 재해시한 결과가 아니다.
- 이번 로컬 전송은 환경의 네트워크 제한으로 완료하지 못했다. SUPPLY_MANIFEST.json hash·image identity·profile inventory 값을 추정하거나 이전 source 값으로 채우지 않아 null로 남겼다.
- exact eea candidate의 scan은 [기존 원본 검수](https://github.com/321Team/fds-msa/pull/21#issuecomment-6030626884)에 있다. Runner 새 앱 image의 Test/Build/Scan·Harbor digest·Pod imageID를 대신하지 않는다.

## 권장 처리 순서

1. APP Owner 이재환이 exact0494ca의 Source/test scope를 기존 승인 범위와 대조해 확인한다. 변경된 Wolfi lock에 대한 새 Test 결과는 Runner에서 별도 판정한다.
2. 동일 artifact의 SUPPLY_MANIFEST.json·파일 목록과 공개 검수 receipt를 수신해 두 image identity·profile/requirements/source_inputs hash를 채운다. OCI 본체가 필요한 Runner 전달본은 ZIP/API digest 및 내부 manifest를 대조한다.
3. 실제 config 소비 전환은 별도 실행 설정 변경으로 exact source·supply directory·identity를 결속하고 독립 리뷰/필수 CI 후 병합한다. 현재 active config를 이 문서로 변경하지 않는다.
4. cicd-runner01/server01_cicd-runner UID1000의 기존 보호 작업 경로에 신규 공급 경로를 준비한다. 기존 승인 staged directory를 덮어쓰지 않는다. fresh identity·hash·공간·source·Trivy DB 및 비중첩 창을 확인한 뒤 승인된 Test→Build→Scan만 실행한다.
5. 실패/identity 불일치/미확정 source·승인/기한이면 중단하고 해당 새 회차 결과만 보존한다. 기존 서비스·image·cache·source를 삭제하거나 공급 검사를 완화하지 않는다.
6. 성공해도 Push는 정확한 새 Build/Scan 증적·독립 검토된 입력과 별도 실행 판단으로 진행한다. 새 Harbor manifest digest 생산 후 APP/SEC/HBR·rollback 입력과 native 무발급 preflight→별도 발급→Deploy 선행조건을 연결한다.

## 기한과 복구 경계

artifact 보관 만료는 2026-10-14 13:03:34 KST다. 이는 DEC-P1-IMG-01 lock 신선도 허용기간이 아니다. [현재 lock의 72시간 경계](https://github.com/321Team/fds-project-baseline/issues/110#issuecomment-6030557822)는 2026-10-09 22:52:07 KST로 기록되어 있다. 소비 전에 exact source lock 이력·현재 producer Gate를 다시 확인하고, artifact retention만으로 사용 기간을 연장하지 않는다.

현재는 Review 기록 추가만 하므로 운영 복구 대상이 없다. 승인된 소비 config 전환을 되돌릴 때에는 source revert PR와 정확한 이전 공급 경로를 사용하고, 실제 운영 Deployment rollback은 별도 승인 plan의 이전 digest/호환성·기한·checkpoint 조건을 따른다.

## 단계 상태

STATIC: API/log/compare/tree 연결 완료. 기존 MOCK/CI 수치를 새 수행으로 재집계하지 않았다.
PRODUCER_CI: SUCCESS. CONSUMER_CONFIG_REVIEW/MERGE: 미수행.
RUNNER_STAGING/새 Test·Build·Scan/Push/native 발급/Deploy: 미실행.
Delivery AC·Status·Freeze·Close·위험수용·Due: 변경 없음.
