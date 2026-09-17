# Runner 로그인 환경의 Git·자원 선행점검

사용자의 실서버 선행작업 1·2번 착수 지시에 따라 준비했다. 대상은 `cicd-runner01`, 로그인 계정 `server01_cicd-runner`, UID1000이다. 이 점검은 새 전용 디렉터리에 관측 Source를 수신하고 기존 공급물·용량을 읽는다. 실제 앱 실행과 Actions Job 인수는 별도다.

## 고정 범위

- 공개 원본: `https://github.com/supreme33-kr/fds-msa.git`.
- 관측 Commit: `254b82231f8af79a06419b581174647bbccbe2bc`.
- 관측 tree: `6ee93b5a5788288aa9be71c3c62589abc27dde0c`.
- 기준 CICD 입력: `dcf9fa232f0f4ec5f347dc5dc67a02668664d20b`의 `config/app-build-scan.json`.
- 기존 공급: `/home/server01_cicd-runner/FDS_CICD_Supply.20260917T010757Z.974fb972`.

위 Commit은 **통신·수신 대조용 관측값**이다. 기존 앱 승인이나 최종 pin/lock 인계를 대신하지 않는다. 앱 repo의 현재 공개 상태를 사용하는 점검이며 공개 설정을 변경/승인하지 않는다. Private 전환 시 익명 fetch 실패를 인증 우회로 해결하지 않는다.

Python 표준 라이브러리만 사용하는 단일 실행본 `tools/delivery/runner_git_preflight.py`를 홈으로 전달한다. Python3.11 이상과 `/usr/bin/git`이 필요하다. 자동 설치는 없다. 홈 소유자·0700, host/user/UID와 로그인 환경을 확인하며 기존 경로/권한을 수정하지 않는다.

```bash
(
  set -euo pipefail
  cd /home/server01_cicd-runner
  test -f runner_git_preflight_20260917.py
  printf '%s  %s\n' \
    'ffb7bfde5f122a664fe45d21bae362f32a0499724a5f1e6798aaa2f12c0f3c61' \
    'runner_git_preflight_20260917.py' | sha256sum --check -
  /usr/bin/python3 -I -B /home/server01_cicd-runner/runner_git_preflight_20260917.py --prepare
)
```

## 실제 동작과 판정

1. 계정 홈 아래 새 `FDS_CICD_GitPreflight.*`를 만들고 EVIDENCE_DIR를 즉시 출력한다.
2. 여유 bytes/inode를 관측한다. 기존 앱 실행기의 시작 하한4GiB와 비교하되 전체 앱 완료에 충분하다고 판정하지 않는다.
3. 공급의 roots/lock/inventory12파일 hash를 고정값과 대조하고, OCI·inventory에 나열된 wheel의 정규 파일/읽기 가능 여부와 wheel 집합을 확인한다. 대용량 OCI/wheel 내용 전체 hash와 의미 검증은 이번에 반복하지 않는다. `SUPPLY_ACCESS=PASS`는 기존 공급 Runtime PASS를 새로 발급한 결과가 아니다.
4. 별도 Git HOME, system/global config 비활성화, credential helper 제거, TLS 검증, redirect 차단, HTTPS-only로 새 bare repo에 고정 SHA를 depth1/no-tags/no-submodules fetch한다. Commit/tree/실제 bare 여부를 대조한다. 기존 proxy 환경값은 자식 프로세스에만 유지하며 출력하지 않는다. Git fetch 제한300초, 그 외 명령60초, 자식의 개별 출력/생성 파일 크기64MiB 제한이다. 총 디스크 용량 격리나 공격자에 대한 sandbox를 제공하는 것은 아니다.
5. checkout·hook·앱 import·pip·pytest·Podman·Build·Trivy·Push·Deploy를 수행하지 않는다. 기존 서비스·CA·네트워크·Git 설정·인증 파일·공급 원본·Podman 저장소를 변경하지 않는다.

용량/공급/수신 결과를 구분하므로 공급 누락이어도 Git 연결 결과를 회수할 수 있다. 모든 점검이 성공하고 기존 앱 시작 용량 하한을 만족할 때만 `GIT_PREFLIGHT=PASS`, exit0이다. 실패는 phase/error code와 nonzero이며 필요한 경우 원본 로그를 해당 서버에서 추가 진단한다. 로그인 환경 PASS는 Actions 서비스의 Job 환경 PASS가 아니다.

## 회수

출력된 EVIDENCE_DIR 안의 아래 두 파일을 회수한다.

- `git-preflight-evidence.tar.gz`
- `git-preflight-evidence.tar.gz.sha256`

archive에는 `result.json`과 그 checksum만 들어간다. 원본 Git 로그·bare Source·Git HOME은 서버에 보존하며 자동 첨부하지 않는다. 결과 JSON에는 Git 명령 rc·로그 hash, 도구 hash·관측 Commit/tree, 공급 메타데이터 hash·읽기 검사, 용량과 시각을 남긴다. 원본 생략을 독립적인 원본 바이트 검증이나 Source 승인으로 확대하지 않는다. 실패해도 export까지 완료되면 같은 두 파일을 제출한다. export 자체가 실패한 경우 이미 출력한 EVIDENCE_DIR의 result/log 존재 여부로 진단한다. 자료 수정·삭제 후 PASS로 재작성하지 않는다.

## 검증 범위

11개 시험으로 실제 임시 Git Commit/bare fetch/Commit·tree 대조(transport만 로컬 fixture), metadata 변조·누락·extra·symlink 거부, 인증 환경 제거, 기존 파일 보존, timeout, 실패 증적 회수·raw 제외, 용량 부족 시 PASS 금지, 단일 파일의 상대/절대 경로 `python -I -B` 호출을 확인했다. 실제 Runner 네트워크와 공급 접근 결과는 사용자 서버 실행 증적 수신 후 판정한다. 새 앱 실행 승인·PR 병합·Runtime AC 완료를 주장하지 않는다.
