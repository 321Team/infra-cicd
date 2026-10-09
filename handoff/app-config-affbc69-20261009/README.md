# affbc69 실행 입력 확정에 따른 consumer config 검토안

CODEX_ASSISTED · 이권욱 · 2026-10-09 KST. [APP Lead 확정](https://github.com/321Team/infra-cicd/issues/11#issuecomment-6074515472)을 반영한 **설정 PR / 서버 공급 배치·현장 실행 미실행**이다. 기존 [pending 입력](../app-candidate-affbc69-20261009/README.md)은 작성 당시 이력으로 유지한다.

## 반영 내용과 승인 근거

- Source `affbc69beef9967f7977d09a6fec14f7fb6c0e94`: APP Lead 이재환 `supreme33-kr`의 [정확한 HEAD 승인](https://github.com/321Team/fds-msa/pull/27#pullrequestreview-5465278143). APP29 주석 커밋으로 교체하지 않는다.
- 공급 tool `8eac4c982076e3c219840a13686b15c46879981f`: 이하영 `hayeounglee`의 [APP14 승인030666e1](https://github.com/321Team/fds-msa/pull/14#pullrequestreview-5438824609), [APP22 승인7bb1309e](https://github.com/321Team/fds-msa/pull/22#pullrequestreview-5438558960)을 연결했다. API로 작성자·APPROVED·commit을 대조했고 실제 tool12파일 각각이 두 승인 Commit 중 하나와 byte 동일함을 기록했다. 전체 tool Commit을 새로 승인한 것으로 표시하지 않는다.
- engine Unit16/Contract15와 API Contract20 매핑을 확정 입력 그대로 적용한다. API Contract는 `test_listener_logging.py`를 포함한다. API Unit은 이번 Source 기준의 NOT_APPLICABLE 사유·승인자를 연결하며 과거 Source 예외를 복사하지 않는다. 실제 앱51건을 이번 준비에서 재실행한 것은 아니다.
- `config/app-build-scan.json`의 Source/공급 identity/시험 입력만 변경한다. workflows, delivery 도구, Push/Deploy 설정은 변경하지 않는다.

`SUPPLY_SOURCE.approved.json`, `TEST_SCOPE.approved.json`은 APP 입력 확정을 기록한다. config의 기존 schema 값 `APPROVED_FOR_TEST_BUILD_SCAN`은 Source 입력 계약이다. **새 consumer config의 독립 리뷰·병합 또는 해당 회차 Runtime GO가 이미 완료됐다는 뜻이 아니다.** 이 Draft PR을 서버 준비 완료로 보고 dispatch하지 않는다.

## 검증과 한계

기준 consumer main `bca5443aed89eff953b6f45072d9ec31471addc3`의 `validate_handoff`, `validate_scope`, `make_config` 및 `app_workflow.validate_handoff`를 사용했다. 원 ZIP은 크기109786915 bytes/SHA256 `2f13771909415439bc5a4c83036284e665b47d023b6447b37e3e18c24ea2e3fd`, manifest SHA256 `6ab02000437a7b5f7d3c99e5202d40b663056a998018a1894360c58de68b73d4`와 일치한다. ZIP을 추출하지 않고125개 내부 파일 해시를 대조했다.

- config 형식·승인 입력 validator PASS.
- 별도 bare Git에 정확한 Source HEAD/origin을 고정하고 Git 인계 검사 PASS: source inputs4·requirements4·Dockerfiles2·locks4 =14 blobs. 승인 진위 확인은 API 대조와 분리했으며, 검사기의 `SOURCE_APPROVAL=DECLARED_NOT_INDEPENDENTLY_VERIFIED` 원문은 그대로 유지한다.
- Windows에서는 `pwd`/`fcntl` import만 허용하고 모든 속성 접근을 실패시키는 stub을 사용했다. Git 읽기 명령의 실행 파일만 Windows Git으로 바꿨다. Linux Runtime·권한·rootless 검증이 아니다.
- materializer CLI, 공급 ZIP 추출/서버 배치, 앱 테스트/Build/Scan, DB 다운로드, Runner dispatch, Push/Deploy는 NOT_RUN.
- 결과와 config SHA는 `VALIDATION.json`, Git 인계 결과는 `GIT_HANDOFF_CHECK.json`에 있다. 초기 준비 시 이전 승인 Commit에 존재하지 않는 새 lock 기록 파일을 조회해 멈췄으며, 최종 검증은 Commit별 파일 존재를 구분해 두 승인 Commit의 합집합과 대조했다. 초기 로컬 임시 자료를 실행 성공으로 집계하지 않는다.

## 서버 배치와 실행의 다음 조건

계획 경로는 `/home/server01_cicd-runner/FDS_CICD_Runtime/FDS_CICD_ApkoSupply.affbc69beef9.6ab02000437a`다. 원래 materializer의 Source12자리/manifest12자리 명명 규칙으로 산출했으며 **존재·권한·배치 완료는 미확인**이다. 기존 adc90410 공급 경로는 변경하지 않았다.

1. consumer config와 승인 입력의 독립 리뷰를 받는다. 정상 계정의 대상 상위 경로·소유/쓰기 권한·링크·다른 세션·새 대상 부재와 보관/회수 조건을 fresh 확인하고 공급 배치 범위를 확정한다.
2. 검토된 materializer로 원 ZIP을 새 경로에 배치할 때 생성되는 config가 이 PR의 Git blob과 byte 동일한지 비교한다. 다르면 중단하고 원인/변경분을 재검토한다. 준비 경로가 남은 실패를 자동 삭제·재실행하지 않는다.
3. 배치 후 공급물/Source를 다시 검사하고 config 리뷰·main 반영·정확한 Run/Attempt·시간창·회수 조건을 확정한 뒤 Test/Build/Scan을 시작한다. 기존 workflow180분 timeout은 13:20~15:00 창 종료 보장이 아니다.
4. 이 APP 확정과 이 PR에는 Push/Deploy GO가 없다. Runtime 업무 포트404, 저장/FDS 호출·DB 회귀는 승인된 배포 회차의 후속이다.

## 원본 보관 인계

- 현재 보관: 이권욱 작업 PC의 승인된 비공개 작업 경로 `C:/Users/soldesk/.codex/.chatgpt-projects/g-p-6a85069945708191b56059da202e4461/phase1-work-20261009/private/app-supply-37859961937/supply.zip`.
- 현재 수집·보존 담당: 이권욱(CICD). 다른 담당자의 수신·장기 보관 인수는 아직 미확인이다. 새 공유 위치나 외부 업로드를 만들지 않았다.
- GitHub artifact11585741989 만료: **2026-10-16 08:32:03 KST**. 만료 전 팀의 승인된 장기 보관 경로·수신 담당·hash 영수증을 연결한다. 현재 로컬 원본에 같은 자동 만료가 설정됐다는 뜻은 아니다.
- 원 ZIP·이미지·wheel 원본은 이 PR에 포함하지 않는다. [증적183](https://github.com/321Team/fds-project-evidence/pull/183)의 기존 consumer 검증과 새 config 검증 범위는 구분한다.
