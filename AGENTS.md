# Work · Codex 공통 기준 진입점

이 저장소: `321Team/infra-cicd`.
중앙 지침: [fds-project-baseline WORK_START](https://github.com/321Team/fds-project-baseline/blob/main/WORK_START.md).
도입 의존 PR: [baseline #71](https://github.com/321Team/fds-project-baseline/pull/71).

## 시작 순서

1. 중앙 저장소 main의 최신 Commit SHA를 GitHub에서 조회한다. 그 SHA에서
   `AGENTS.md`, `WORK_START.md`, `handoff/OPERATING_RULES.md`,
   `handoff/baseline.lock.json`을 함께 읽는다. 각 파일을 서로 다른 main 시점에서 읽지 않는다.
   중앙 PR #71이 독립 검토 후 병합되기 전에는 도입 준비·검증만 진행한다.
   파일/권한/현재 상태를 확인할 수 없으면 이전 사본으로 진행하지 않는다.
2. 중앙 Task 양식에 이 저장소와 main base, 한 작업의 목적·허용 경로·AC·검사·관련
   Issue/PR·증적을 지정한다. 중앙 검증기로 receipt를 만들고 Work와 Codex가 같은
   task SHA-256/context_id를 사용한다. 사양은 발행 Tag/Commit, 작업 상태는 새로 읽은
   GitHub Issue·댓글·PR HEAD·Review와 수신 증적 Commit에서 가져온다.
3. 수행 전과 인계 직전에 중앙 `scripts/shared_context.py check`를 실행한다.
   `--worktree`에는 이 저장소 checkout을 지정한다. base·사양·상태가 달라지면
   멈추고 Work에서 차이를 검토한 뒤 새 receipt로 재인계한다. SNAPSHOT_ONLY는
   실시간 확인을 대신하지 않는다. 지침이 변경되면 새 세션에서 읽는다.
4. docs/governance.md, docs/pipeline-contract.md 및 해당 역할·준비 기록의 승인 절차를 함께 따른다.
   중앙 공통 지침과 기존 기술/승인 기록이 충돌하면 임의로 덮어쓰지 않는다.
   변경은 별도 브랜치와 PR로 제출하고 정확한 HEAD 독립 검토와 수동 병합을 거친다.

## 이 저장소에서 지킬 경계

모의검증·설계 검토와 실제 Runner 실행·이미지 Push·애플리케이션 배포를 구분한다. 승인 Source/Workflow SHA, Image Digest 및 현장 Gate는 기존 절차대로 확인한다. CA 개인키·배포 자격증명을 Cloud 인계 파일에 넣지 않는다.

조회 당시 열린 PR #2와 #3은 작업 시 새로 확인하고 관련되면 watch_prs에 포함한다. 모의 결과를 Runtime PASS로 표시하지 않는다.

Work는 목적·일정·설계 판단·통합 검토를 유지하고 Cloud Codex는 구현·반복 검증을 맡는다.
교육장 실제 실행은 기존 현장 Gate에서 수행한다. 공용 PC에 개인키·장기 토큰·개인 원본을
저장하지 않는다. 결과는 STATIC/MOCK/CI/RUNTIME/REVIEW/MERGE/PUBLICATION을 구분한다.
이 파일은 기술 사양·티켓 상태·기존 승인 권한을 변경하지 않는다.
