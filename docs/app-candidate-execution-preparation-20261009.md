# affbc69 후보의 현장 실행 준비

CODEX_ASSISTED · 이권욱 · 2026-10-09 KST. 상태는 **PREPARATION / PENDING_APP_HANDOFF**다. 이 문서는 승인 입력을 채운 실행 record나 서버에 배치할 config가 아니다. 기존 [후보 시험·공급 인계](app-metrics-handoff.md)의 다음 단계를 구체화한다.

## 기준과 작업창

- 조회한 CICD main: `04e49811e46ffcd3f35183c2cfb9493901f84dc5`. 현재 `config/app-build-scan.json`의 Source는 `adc90410f99cb7af5b40bc8ff92c45272e9f8bee`다.
- 검토할 APP 후보: `affbc69beef9967f7977d09a6fec14f7fb6c0e94`, [APP27](https://github.com/321Team/fds-msa/pull/27). 이 Source에 대한 독립 리뷰·시험 범위 확정은 아직 대기다.
- 사용자 단독 창: **2026-10-09 09:10~12:00 KST**, [등록 기록](https://github.com/321Team/fds-project-baseline/issues/19#issuecomment-6071495239). 09:00~09:10은 새 현장 시험을 시작하지 않는다. 리뷰 회수는 창 이후로 유지한다.
- 이 창만으로 Source 승인·config 변경·Runtime 실행이 승인되지 않는다. Harbor 자동기동은 타 세션이며 Push/Deploy도 이번 준비 범위 밖이다.

## 입력별 확보 상태와 다음 확인

| 입력 | 확보한 자료 | 남은 조건 |
|---|---|---|
| APP Source | APP27 후보와 증적178의 TCP/Scan | APP 담당 이재환의 정확한 Source 승인 원문. 과거 adc90410 승인 링크 재사용 금지 |
| 시험 범위 | engine Unit16/Contract15, API Contract20 제안 | API Unit의 실행 명령 또는 해당 Source의 NOT_APPLICABLE 사유·승인. `test_listener_logging.py`를 포함한 매핑 확정 |
| 공급 생산자 | tool `8eac4c982076e3c219840a13686b15c46879981f`, Run37859961937/Attempt1 | tool 승인 근거와 Source의 결속을 확인. workflow step 이름은 승인 증거가 아님 |
| 공급 원본 | artifact11585741989, ZIP109,786,915bytes | 담당자 수신·현장 배치·장기 보관 인수. 만료2026-10-16 08:32:03 KST 전에 원본 보존 책임 연결 |
| consumer 호환성 | 증적183의 실제 파일검증4함수·실패입력3건·원입력 재검증 | 전체 config 생성/형식 검사·실제 Source Git 인계 검사는 미실행 |
| Runner | 증적183의08:51~52 정상계정·권한·도구·Trivy hash 관측 | 실행 직전 등록/Job·병렬 작업·공급 전체 bytes·공간·rootless 및 승인 경로 확인 |
| 실행 config | 현재 main은 옛 Source/config | 새 config의 전체 diff·독립 리뷰·main 반영 및 실제 dispatch Commit 확인 |

공급 ZIP SHA256은 `2f13771909415439bc5a4c83036284e665b47d023b6447b37e3e18c24ea2e3fd`, SUPPLY_MANIFEST SHA256은 `6ab02000437a7b5f7d3c99e5202d40b663056a998018a1894360c58de68b73d4`다. 세부 원본은 [증적183 고정 Commit](https://github.com/321Team/fds-project-evidence/tree/16731c8ce8832d7e63e168da446dbd6aa35f7bd1/evidence/P1-CICD-01/app-offline-supply-37859961937)에 연결한다. hash 일치는 Source 승인이나 실행 성공이 아니다.

## 승인된 입력 수신 후의 순서

1. **입력 확정:** 인계 원문에서 Source/tool 승인자·근거와 시험 범위를 각각 확인한다. `materialize_app_build_scan_v3.py`가 요구하는 handoff의 artifact/source/supply_tool/policy, test-scope의 Source/승인/services 필드를 그 원문으로 채운다. 지금은 PENDING인 부분을 임의 값으로 채우거나 APPROVED 파일을 생성하지 않는다.
2. **독립 준비 경로:** 기존 정상 `server01_cicd-runner` 계정의 검증된 private 작업 경로에 원 ZIP과 승인 입력을 전달하고 수신 hash를 대조한다. 기존 adc90410 공급·기존 config를 덮어쓰지 않는다. 새 출력과 공급 디렉터리가 없음을 확인한 뒤 검토된 materializer를 1회 사용한다. 도구가 선택하는 공급 폴더명은 Source 앞12자리와 manifest 앞12자리로 정해지며 실제 반환된 경로를 기록한다.
3. **설정 검증:** 반환 config의 Source·승인·시험 argv/cwd/timeout·공급 경로·images/profiles/requirements/source_inputs를 원본과 대조한다. API Contract에 logging 시험을 포함했는지 확인한다. 기존 `app_workflow.py --config <확정 config> --source-dir <정확한 Git Source>`의 비실행 경로로 Git 객체 인계를 검사한다. 결과 폴더/hash/rc를 보존하며 HANDOFF_CHECK_PASS를 앱 시험 PASS로 쓰지 않는다.
4. **리뷰 및 main 반영:** 생성 config 전체 diff를 별도 PR에서 검토한다. 이 준비 문서의 리뷰는 config의 독립 리뷰를 대체하지 않는다. 새 config가 main에 반영되고 실행 조건이 충족되기 전에는 dispatch하지 않는다.
5. **회차 고정:** `.github/workflows/app-test-build-scan.yml`은 입력 override 없이 main의 config만 사용한다. 실행 직전 main SHA/config bytes, 승인 Source, 공급물, Runner 정상 계정, 다른 작업과의 비중첩, 복구 인수자를 고정한다. dispatch 후 실제 Run/Attempt/headSha가 확인된 값과 같은지 재확인한다. 다르면 취소·중단하고 성공으로 처리하지 않는다. 수동 환경변수로 GitHub context를 만들어 로컬 `--execute`를 우회 호출하지 않는다.
6. **결과 수신:** 해당 Run 로그의 EVIDENCE_DIR와 상위 result/handoff → build-scan → scan → DB의 연결, 단계 rc·이미지 identity·원본 hash를 [기존 회수 절차](app-evidence.md)에 따라 검사한다. 새 Run/Attempt의 외부 expected 값만 사용한다. 원본/이미지/DB/보고서에 민감정보가 포함될 수 있으므로 무검토 Git 커밋·artifact 업로드를 하지 않는다.

## 시간 제한과 fresh DB

현재 workflow의 `timeout-minutes: 180`은09:10~12:00의170분 창보다 길다. workflow에는12:00 KST 벽시계 종료 장치가 없으며 concurrency/로컬 lock도 다른 종류의 Job·Harbor 수동 작업을 배제하지 않는다. **180분 timeout을 작업창 종료 보장으로 사용하지 않는다.** 실제 실행을 제안할 때 시작 시각·최종 취소 판단 시각·증적 회수/정리 여유·중단 담당자를 회차 기록에 정하고, 창 안에서 마무리할 근거가 없으면 dispatch하지 않는다. 현재는 리뷰 대기이므로 이 창 안의 앱 Runtime 실행을 약속하지 않는다.09시가 만료인 이전 임시 스크립트도 시간만 바꿔 재사용하지 않는다.

`scan_images.scan_pair()`는 실행별 새 DB 경로에 `prepare_db.prepare()`를 호출하고 각 이미지 검사 전후 DB hash·metadata 유효성을 확인한다. 따라서 지금 과거 DB를 읽거나 따로 갱신해도 미래 회차의 fresh DB PASS가 되지 않는다. 실제 Scan에서 새 DB 취득·유효성·TLS/승인된 통신 경로를 확인하고 실패하면 STOP한다. 오래된 cache 복사, expiry 무시, CA 검증 해제를 대안으로 사용하지 않는다.

## 실패 시 중단·보존·복귀

| 실패 시점 | 행동과 확인 |
|---|---|
| 승인/Source/hash/만료·비중첩 불일치 | 실행 전 STOP. 누락 입력과 관측 시각을 기록. 과거 승인·공급·PASS로 대체하지 않음 |
| materialize 실패 | rc/출력과 실제 경로를 확인. rename 후 config 생성/쓰기 실패라면 새 공급 디렉터리가 남을 수 있음. 자동 전체 원복으로 가정하거나 재실행을 위해 기존 경로를 삭제하지 않음 |
| main config 채택 전 | 새 준비 경로만 영향 범위. 기존 공급/config는 유지. 보존/정리 결정 전 경로·소유자·hash·사용 중 여부 확인 |
| main config 채택 후 실행 실패 | 정확한 Run/Attempt 중단 및 증적 보존. 되돌림이 필요하면 채택한 config diff만 리뷰된 revert로 복귀. 옛 config 복귀를 옛 공급/취약점 상태의 재실행 승인으로 보지 않음 |
| Test/Build/Scan nonzero | 다음 단계 진행 금지. 실제 rc·error_code·남은 결과 보존. 취약점 BLOCK/10과 도구/인증/DB FAIL을 구분. 미검사 이미지를 PASS로 표시하지 않음 |
| 취소·SIGKILL·전원 단절 | GitHub 종료 상태만으로 자식 프로세스/컨테이너 종료를 단정하지 않음. 정상 계정에서 해당 Run 전용 저장소·프로세스의 잔존을 확인하고 담당자가 회수. 최종 result가 없으면 불완전 증적으로 보존 |

실행기의 증적 디렉터리와 전용 Podman storage는 자동 삭제되지 않는다. 원본 수신 검증·진단·후속 전달 필요 여부가 정해진 뒤 해당 Run 자원만 대상으로 정리안을 확정한다. 기존 Runner store의 prune/reset, 광범위 디렉터리 삭제, 운영 서비스 재시작은 이 절차에 없다. 새 공급 디렉터리는 후속 실행 입력일 수 있으므로 단순 임시파일로 삭제하지 않는다.

## 이번 준비의 완료 범위

문서의 동작 설명은 위 고정 CICD main의 `materialize_app_build_scan_v3.py`, `app_workflow.py`, `build_scan.py`, `scan_images.py`, `prepare_db.py` 및 workflow와 대조했다. 입력·실행·실패 보존의 연결을 작성한 **STATIC 준비**이며 새 config 생성, 앱 실행, DB 다운로드, 서버 전달, Runtime 복구 시험은 수행하지 않았다. 기존 증적183의 CI/consumer/Runner 관측 단계와 분리한다.
