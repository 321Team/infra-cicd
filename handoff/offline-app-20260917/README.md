# 앱 담당 검토용: 공급 완료에 따른 pin·lock 반영안

## 바로 적용할 수 있는 단일 패치

2026-09-17 최신 확인에서 앱 main은 아래 기준 SHA이고 이 연결 계정은 `pull=true / push=false`다. 따라서 앱 저장소에 직접 변경 브랜치/PR을 작성하지 않았다. 아래 패치는 기존6개 후보를 하나로 묶은 적용 준비물이며, 앱 승인·병합 또는 실행 완료를 의미하지 않는다.

- 파일: [offline-app-254b822.patch](offline-app-254b822.patch), 12,492bytes
- SHA256: `eee3869cb0135769d7da2f3929455aa46ac19b8e185b5770b4cf6e00b782bb02`
- sidecar: [offline-app-254b822.patch.sha256](offline-app-254b822.patch.sha256)
- 회신: [최종 SHA·Unit/Contract 판단 양식](APPROVAL_REPLY.md)

패치 검증은 GitHub 기준 Commit의 Dockerfile2개·requirements4개 원본 Git blob 일치 확인 → 격리 로컬 저장소의 `git apply --check`/적용 → 후보6파일 바이트 일치 및 기존 requirements4개 불변 → 중복 적용 차단 → 역적용 원상복귀 → Dockerfile이 다른 기준에서 적용 차단까지 PASS다. 앱 pytest/Build/Scan은 실행하지 않았다.

**앱 담당 적용 절차:** 앱 저장소의 기존 변경을 보존하도록 아래 검사는 dirty/untracked 파일이 있으면 중단한다. 먼저 패치 파일을 내려받고 `p1_patch`를 실제 절대경로로 지정한다. 아래는 앱 담당이 검토하여 수행할 절차이며 CICD Runner에서 수행하는 명령이 아니다.

```bash
(
  set -eu
  # 현재 디렉터리는 앱 담당의 fds-msa 저장소여야 한다.
  p1_patch=/absolute/path/offline-app-254b822.patch
  test "$(git rev-parse HEAD)" = 254b82231f8af79a06419b581174647bbccbe2bc
  test -z "$(git status --porcelain=v1 --untracked-files=all)"
  printf '%s  %s\n' 'eee3869cb0135769d7da2f3929455aa46ac19b8e185b5770b4cf6e00b782bb02' "$p1_patch" | sha256sum -c -
  git apply --check "$p1_patch"
  git switch -c cicd/offline-app-handoff-20260917
  git apply "$p1_patch"
  git add -- transaction-api/Dockerfile transaction-api/requirements-runtime.lock transaction-api/requirements-test.lock fds-engine/Dockerfile fds-engine/requirements-runtime.lock fds-engine/requirements-test.lock
  git diff --cached --check
  git diff --cached --stat
)
```

이후 앱 담당이 staged6파일을 검토하고 자신의 PR/Commit 절차로 반영한다. 최종 반영된40자리 SHA와 [회신 양식](APPROVAL_REPLY.md)의 실제 판단을 Issue11에 남긴다. 위 단계는 자동 Commit/Push/승인/병합을 수행하지 않는다. 기준 SHA가 달라지면 검사를 생략하지 말고 새 Source와 requirements 변경을 다시 대조한다.

## 기존 인계 후보의 근거

원본 requirements Source: `supreme33-kr/fds-msa@254b82231f8af79a06419b581174647bbccbe2bc`.
기존 승인 `01758583...`를 이 변경의 승인으로 승계하지 않는다. 이 폴더는 **앱 저장소에 아직 반영되지 않은 인계 후보**다.

| 파일 | 인계 내용 |
|---|---|
| 서비스별 Dockerfile | `docker.io/library/python@sha256:2fe5997d249a808b8eeea52c58a1dbffbba28754dc11699ef5c029f2d818ce79`, 추가 local context `python-wheels`, 오프라인 전체 hash 설치·pip check. 기존 app/run.py, UID10001, 두 포트 유지 |
| transaction-api/requirements-runtime.lock | 수집된25개 항목 원본, SHA256 `3a71d0d227415f3f818642b5370920f02a23a9bcb28f4dd31aac1d59d96c756e` |
| transaction-api/requirements-test.lock | 수집된29개 항목 원본, SHA256 `72137816616e16e2b88f86630c507800414c3b37c14c7f624b9042fd66a8ef85` |
| fds-engine/requirements-runtime.lock | 수집된18개 항목 원본, SHA256 `c9d6138da341b431f0b7fdaf4aee78d59f9207e9d3f2bf4636b0505681c1f80b` |
| fds-engine/requirements-test.lock | 수집된26개 항목 원본, SHA256 `3a51dbbb6dd56d09658f0d2343e4b4847394f006f1f2aa082886bc468e797b65` |

lock 원본은 [정본 후보 수신 기록](https://github.com/321Team/fds-project-baseline/tree/7636a609989a91d6f107353b199ebe0857396132/audit/v3.1.19/intake/p1-cicd-supply-20260917T010757Z)과 동일하다. 무작위로 새 버전을 resolve한 파일이 아니다. wheel 원본은 Runner의 기존 공급 폴더에 남아 있다.

앱 담당이 6개 파일과 Unit/Contract 범위를 검토해 반영한 최종40자리 SHA·승인 근거를 인계한다. API Unit은 새 테스트가 필요한지, 기존 인계대로 해당 없음인지 명시해야 한다. 기존 requirements4개가 바뀌면 현재 공급 lock을 그대로 승인하지 않는다.

runtime lock은 Python3.12.14/pip25.0.1/glibc2.41 base image 대상, test lock은 Python3.12.14/pip23.3.2/glibc2.39 Runner 대상에서 생산·설치 검증됐다. 실제 앱 Build/실행/스캔 성공은 별도이다. 새 Dockerfile의 실제 소비 연결은 [실행 안내](../../docs/offline-build-scan.md)를 따른다.
