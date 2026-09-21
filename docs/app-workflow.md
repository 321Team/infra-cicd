# 중앙 수동 App Test → Build → Scan

정본 [v3.1.18 §10](https://github.com/321Team/fds-project-baseline/blob/docs-v3.1.18/baseline/FDS_Project_Implementation_Baseline_v3.1.18.md)의 Source 추적과 Test → Build → Trivy 및 실패 시 Delivery 중단을 연결한다. Push/Deploy는 이번 Workflow에 없다. 기존 공급 수집과 base digest 최소 Build는 제출 증적 기준 PASS이며 같은 작업을 반복하지 않는다.

## 현재 준비와 실행 조건

`.github/workflows/app-test-build-scan.yml`과 `tools/delivery/app_workflow.py`를 준비했다. **기본 config는 여전히 PENDING_APP_HANDOFF**이며, 승인 Source·승인자·근거·API Unit 범위를 임의로 채우지 않았다. PR 작성/수정으로 내부 Runner의 이 Workflow를 실행하지 않는다. 독립 리뷰와 main 병합, 최종 앱 인계가 끝난 뒤 main의 수동 실행을 사용한다.

중앙 Workflow는 dispatch 입력으로 Source나 명령을 받지 않는다. 실행 시점 main Commit에 있는 config만 읽으며, Repository/main/workflow_dispatch/Runner host·user·UID/checkout HEAD를 기존 실행기 기준으로 확인한다. `GITHUB_WORKFLOW_REF`도 이 Workflow의 main 경로여야 하고 `GITHUB_WORKFLOW_SHA`와 `GITHUB_SHA`가 같아야 한다. config 원본 바이트를 해당 Commit의 Git 객체와 대조한다. 이 검사는 **사람의 독립 리뷰나 승인 진위를 자동 인증하지 않는다**. 최종 config 변경도 포함한 최신 HEAD의 검토가 필요하다.

## Source 접근 결정

허용 Source는 기존 `supreme33-kr/fds-msa`의 익명 HTTPS 읽기와 이관된 Private `321Team/fds-msa`의 제한된 인증 읽기 두 경로다. config의 repository가 경로를 선택한다. 두 경로 모두 고정 GitHub URL의 승인40자리 SHA만 수신하며 다른 저장소·branch/tag·입력 URL을 받지 않는다. API 연결 권한을 실제 Runner의 Git 접근 성공으로 간주하지 않는다.

현재 config는 기존 저장소/PENDING_APP_HANDOFF를 유지한다. 조직 Source 채택은 최종 앱 인계와 검토한 config 변경으로 수행한다. Private 경로의 자격증명 주입·최소 권한·실패 처리·증적 범위는 [Private Source 수신](private-app-source.md)을 따른다. infra-cicd의 기본 GITHUB_TOKEN을 다른 Private 저장소 권한으로 가정하지 않는다. Secret의 실제 등록·유효 권한·Runner 접근 성공은 아직 확인하지 않았다.

근거: [actions/checkout 고정 버전의 교차 저장소 인증 설명](https://github.com/actions/checkout/blob/d23441a48e516b6c34aea4fa41551a30e30af803/README.md#checkout-multiple-repos-private), [GitHub Token 최소 권한](https://docs.github.com/en/actions/tutorials/authenticate-with-github_token).

## 실행 순서와 보존

1. `cicd-app-<Run>-<Attempt>`가 없는지 검사하고 새 경로에 CICD Workflow Commit을 checkout한다. 기존 경로가 있으면 중단한다. 고정 actions/checkout 버전, `persist-credentials:false`, `clean:false`, `set-safe-directory:false`, submodule/LFS 비활성화를 사용한다. 다른 checkout·HOME·전역 Git 설정을 정리하지 않는다.
2. config 완결성을 확인한다. **현재 PENDING 설정은 앱 fetch 전에 중단**한다. CLI에서 실행 입력 경로나 사전 Source를 덮어쓸 수 없다.
3. 기존 Runner HOME의 소유자·0700과 최소 여유4GiB를 확인한다. `FDS_CICD_AppWorkflow.lock`으로 이 도구의 중복 실행을 막고, 새 `FDS_CICD_AppWorkflow.*` 디렉터리를 만든다. 기존 파일/권한은 바꾸지 않는다. 이 lock과 Workflow concurrency는 다른 종류의 Job이나 수동 작업까지 예약하는 장치가 아니다. 4GiB는 시작 하한이며 실행 중 디스크 고갈 방지는 아니다.
4. 새 bare Git 저장소에 `--depth=1 --no-tags`로 승인40자리 SHA만 fetch한다. 실제 FETCH_HEAD의 Commit을 대조하고 HEAD를 고정한다. worktree checkout·앱 import·필터·hook·submodule/LFS를 실행하지 않는다. 빈 Git template, 새 HOME, system/global Git config 비활성화, credential helper 초기화, TLS 검증, redirect 차단을 사용한다. 상속된 GitHub Token·askpass·GIT_CONFIG override를 버리고 기존 proxy 경로만 보존한다. Private 경로에서는 전용 읽기 Token을 고정 URL의 fetch 자식 프로세스에만 전달한다. fetch 제한300초이며 인증 실패를 다른 경로로 우회하지 않는다.
5. Git Commit 객체에서 전체 tree의 link/submodule/잘못된 경로/기존 `.venv`를 거부한다. Dockerfile2개의 인계 후보 바이트, lock4개의 공급 hash, 기존 requirements4개의 hash, 시험 cwd를 대조한다. Source의 dirty/untracked 내용은 사용하지 않는다. Dockerfile 주석만 바꾼 경우도 현재 중앙 인계의 정확한 후보 대조에서 실패한다. 이는 이 구현이 지원하는 인계 형식이며 새 업무 AC를 뜻하지 않는다.
6. `handoff.json`에 Source 승인 선언, 검사10파일 hash, Unit/Contract 배정·명시적 Unit 해당 없음과 config hash를 기록한다. 이 단계는 시험 실행 PASS가 아니다.
7. 같은 디렉터리 아래 새 `build-scan/`에서 기존 schema2 실행기를 호출한다. 공급 사본 검증, rootless base import, offline venv 설치, Unit/Contract, 새 committed Build 사본, 두 이미지 Build, Trivy 최신 DB/Scan을 수행한다. `handoff.json` hash를 Run/Attempt/config hash와 함께 하위 context에 결합한다. 반환값 PASS/0, 취약점 BLOCK/10, 오류 FAIL/nonzero를 보존한다.

Workflow 전체 제한은180분이다. 정상 오류/timeout/SIGTERM·SIGINT의 직접 자식 프로세스 그룹 처리는 기존 실행기를 사용한다. 강제종료·전원 단절·동일 UID 악성 코드에 대한 격리/완전한 정리를 보장하지 않는다. 새 Source Git·raw log·Build 증적은 남기며 자동 artifact 업로드·이미지 prune·기존 폴더 삭제를 하지 않는다. 콘솔에는 상태 코드와 새 EVIDENCE_DIR만 출력한다. Trivy DB의 승인된 온라인 취득 경로가 있으므로 전체 파이프라인을 완전한 오프라인 실행으로 표현하지 않는다.

## 앱 담당 최종 인계가 오면

- [후보6파일](../handoff/offline-app-20260917/), 기존 requirements4개와 최종40자리 SHA·승인 근거를 대조한다. API Unit은 실행 명령 또는 명시적 해당 없음 사유/승인 근거가 있어야 한다.
- 확정값을 config에 반영하고 로컬의 committed Source로 다음 읽기 전용 검사를 수행할 수 있다. 이 경로는 Git 객체만 읽으며 fetch/앱 설치/pytest/Podman/Trivy를 호출하지 않는다. 별도 config를 쓴 검사는 main 실행 승인이 아니다.

```bash
python3 tools/delivery/app_workflow.py --config /path/to/reviewed-config.json --source-dir /path/to/app-repository
```

- 최신 config/코드/Workflow 변경을 독립 리뷰하고 main에 반영한 뒤 중앙 Workflow를 수동 실행한다. 실제 Runner GitHub HTTPS 읽기와 앱 Test/Build/Scan의 Runtime 증적은 이 실행에서 처음 판정한다.

## 검증 범위

실제 실행 종료 후 [앱 증적 회수·대조](app-evidence.md)로 Run/Attempt·Source·config·Build/Scan/DB 연결을 검사한다. 도구의 증적 검사 성공과 원래 앱 실행 결과, 독립 리뷰·Delivery 판정을 구분한다.

새 시험은 실제 임시 Git Commit·bare fetch·고정 SHA 수신·10파일 검사, dirty/untracked 제외, 변경/누락/잘못된 Source·승인 입력 차단, credential 환경 제거, 기존 디렉터리 보존, downstream 호출 차단과 PASS/BLOCK/FAIL 반환을 확인한다. fetch 시험의 transport만 로컬 fixture로 치환한다. 실제 GitHub 네트워크, Runner Job, 앱 시험, Podman/Trivy는 이 시험으로 검증됐다고 주장하지 않는다.

**APP_TEST_BUILD_SCAN NOT_EXECUTED / DELIVERY_GATE NOT_VERIFIED.** 공급 수집·base probe의 기존 Runtime PASS와 분리한다.
