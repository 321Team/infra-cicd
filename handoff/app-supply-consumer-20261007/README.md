# 새 APP 공급물의 CICD 소비 준비 — 2026-10-07 후속

책임: 이권욱(P1-CICD-01), APP Source/test 인계 이재환, 기술 지원 이하영. CODEX_ASSISTED.
이 문서/CONSUMER_INTAKE.json은 Review 기록이며 실행 config가 아니다.

## 최신 생산자와 소비자 경계

최신 APP main은 `adc90410f99cb7af5b40bc8ff92c45272e9f8bee`이다. #22의 openssl4.0.3-r4/glibc2.44-r8 lock 갱신과 #14의 공급 PR 경로 감소가 편입됐다.
0494ca27→adc90410 직접 비교는4파일(workflow1/lock2/갱신문서1)이며 transaction-api/fds-engine 앱 및 시험 디렉터리 변경0이다. **기반 OS package lock은 달라졌으므로 이전 image identity·Scan을 새 공급물로 전용하지 않는다.** 시험 명령 유지가 합리적인 제안이지만 새 Source에 대한 APP Owner 확인은 PENDING이다.
current active config/app-build-scan.json의 source9658255ce9e39167cc8876a38b98aeb1f0311694와 이전 staged 공급물은 유지했다. 이2개 Review 기록만 갱신하며 현재 workflow/config/credential·보안 설정은 변경하지 않는다.

## 최신 직접 확인과 한계

- [Source37585637220](https://github.com/321Team/fds-msa/actions/runs/37585637220) / [supply37585636640](https://github.com/321Team/fds-msa/actions/runs/37585636640) completed/success, Attempt1. supply Job112674987468의 실제 source/tool=adc90410, OFFLINE_SUPPLY_CONTRACT=PASS·업로드 성공 원문을 확인했다.
- 공급 artifact11466476253, name apko-offline-supply-adc90410f99cb7af5b40bc8ff92c45272e9f8bee-1,109786891bytes, GitHub API/업로드 로그 보고 ZIP SHA2562eaed6486b68f9260f735e362b45a167d7f01aa0fa819accf751ea8d74951ad6.
- 17:52 KST 원본 ZIP 109786891 bytes를 직접 수신했다. ZIP SHA256이 위 API digest와 일치하며, 내부 SHA256SUMS **125/125**, exact APP source 파일 **12/12**, 4개 profile의 wheel **106/106** hash·lock·METADATA name/version이 일치한다. ZIP entries126은 checksum 목록 자체1개를 포함한다.
- builder/runtime 이미지2개는 archive·manifest/index·config·압축 layer descriptor size/hash·압축 해제 diff_id와 linux/amd64를 직접 대조했다. 이미지 로드·패키지 설치·의존성 실행·취약점 검사는 수행하지 않았다. OCI 파일명 suffix와 달리 실제 묶음 형식은 `docker-archive`다.
- `SUPPLY_MANIFEST.json` 원본 SHA256은 `f4d55db6ecbefe2057cdb7ca6539b6818549744eb8bfc127ba12dcfa4eb6143b`. 새 full image identity/profile hashes/counts를 JSON에 채웠으며, 이전 공급물 값을 전용하지 않았다. **APP Owner 확인·독립 consumer 리뷰·Runner staging·새 Test/Build/Scan은 PENDING/NOT_RUN**이다.
- 직접 검수 기록: [LOCAL_INTAKE_REPORT.json](LOCAL_INTAKE_REPORT.json), 생산자 원본 [SUPPLY_MANIFEST.json](SUPPLY_MANIFEST.json), [재현 검사기](verify_artifact.py). 이는 독립 리뷰 승인이나 Delivery AC 인수가 아니다.
- 이전0494ca supplyRun37569632028/artifact11460655012와125파일/106wheel/OCI2 검수는 JSON의 previous_preparation에 **그 source의 이력**으로 보존한다. 신규adc 공급물의 직접 검수 완료로 표시하지 않는다.

## 입력 수신과 실제 다음 순서

1. APP Owner 이재환이 exactadc90410 Source/test scope와 기존 Source 실행 승인 범위를 확인한다. lock 갱신에 따른 실제 image 변경은 새 Build/Scan 입력으로 판단한다.
2. 로컬 artifact/manifest/identity 인수 검수는 위 기록으로 완료했다. 후속 독립 리뷰와 Runner 신규 전달본에서 같은 archive/내부 hash를 확인한다. 기존 승인 staged directory에 덮어쓰지 않는다. 검증된 로컬 원본의 존재를 Runner staging 완료로 표시하지 않는다.
3. 실제 config 전환은 별도 설정 변경으로 exact source/tool·supply 경로·identity를 결속하고 독립 리뷰/필수 CI 후 처리한다. Review JSON은 app_workflow.py 입력이 아니다.
4. cicd-runner01/server01_cicd-runner UID1000, 기존 보호 경로·신규 공급 디렉터리에서 identity/hash/공간·Trivy DB·비중첩 창을 확인한 뒤 승인된 Test→Build→Scan만 실행한다.
5. 실패/identity 불일치/승인 누락/기한 문제면 중단하고 새 회차 원본/RC/hash를 보존한다. 공급 검사를 완화하거나 기존 service/image/cache를 삭제하지 않는다.
6. Push/native 발급/Deploy는 각각 정확한 새 Build/Scan·Harbor digest·APP/SEC/HBR/rollback 및 단기 자격증명 Gate를 충족한 뒤 별도 단계로 처리한다.

## 기한·복구·상태

새 artifact retention 만료는10/14 16:09:38 KST이다. 이것은 lock 신선도 승인기한이 아니다.
두 lock의 latest change는 commit7bb1309e…(author2026-10-07 15:13:51 KST)다. 기존0494ca의10/9 22:52:07 경계를 새source에 그대로 옮기지 않고 소비 직전 source별/per-lock age·history·producer Gate를 재확인한다. 미래 author 시각·누락된 이력·정책의 age 초과는 차단하며 승인 정책을 완화하지 않는다.
현재는 Review 기록·원본 manifest·로컬 검증 자료 갱신이므로 운영 원복 대상이 없다. 후보 문서는 source revert로 회수한다. 실제 config/Deployment rollback은 기존 별도 계획의 이전 경로/digest·호환성·유효기간·checkpoint 조건을 따른다.

STATIC/LOCAL_ARTIFACT: source compare/API/Job log 연결, 새 원본 ZIP·내부125파일·source12파일·wheel106개·이미지2개 직접 검증 완료. MOCK/패키지 설치/이미지 load는 미실행이다.
PRODUCER_CI: 새 exactadc Source/supply SUCCESS. CONSUMER_CONFIG_REVIEW/MERGE: 미수행.
RUNNER_STAGING/새 Test·Build·Scan/Push/native 발급/Deploy: 미실행.
Delivery AC·Status·Freeze·Close·위험수용·Due·Owner는 변경하지 않았다.

## 로컬 무결성 검수 재현

Python 3.11+ 및 exact source 읽기 권한이 있는 `gh` 환경에서 artifact11466476253의 원본 ZIP을 새 로컬 경로에 받는다. 셸의 텍스트 변환 리다이렉션을 피하고 binary 그대로 저장한다. API digest와 ZIP SHA256은 위 고정값이어야 한다.

```text
python verify_artifact.py artifact-11466476253.zip LOCAL_INTAKE_REPORT.json
```

검사기는 ZIP을 파일시스템에 풀거나 패키지·이미지를 실행하지 않는다. 내부 bytes와 exact GitHub source12파일을 읽어 대조하고 종료코드0일 때만 report를 쓴다. 체크섬/identity/파일 목록/소스 불일치 또는 조회 실패면 중단하고 원본을 보존한다. 출력 report는 새 파일 경로를 사용한다. 운영 환경 변경이 없으므로 운영 원복은 없다. lock age·승인·Runtime·Trivy·Push·Deploy는 검사 범위 밖이다. `python -O`는 assert 검사를 제거하므로 사용하지 않는다.
