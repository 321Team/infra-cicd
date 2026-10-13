# 이미지 소스 권한 수정 — 2026-10-13

책임: P1-CICD-01(구현 이권욱), P1-APP-01(최종 이미지 기동/회귀 소비). AI 사용: CODEX_ASSISTED.

[Deploy Run38364658401](https://github.com/321Team/infra-cicd/actions/runs/38364658401)은 새 transaction-api의 `/app/run.py` Permission denied로 READINESS_TIMEOUT 후 이전 이미지로 복귀했다. fds-engine은 배포하지 않았다. [증적213](https://github.com/321Team/fds-project-evidence/pull/213)의 두 후보 OCI archive는 run.py가 root:root/0600이며 이미지 및 실제 Deployment는10001:999로 실행함을 보여준다. 사후 기존 두 앱은 각Ready3이고 업무 E2E 인수는 별개다.

## 수정 범위

`unpack_source(..., for_image=True)`를 이미지 build context에만 사용한다. 검증된 Git archive의 일반 파일/디렉터리를 모두 추출한 뒤 context 내부 디렉터리와 실행 파일은0755, 일반 파일은0644로 정리한다. private context/evidence 루트0700, 원본 archive, 테스트 사본0600/0700, 자격증명은 그대로다. 일반 소스의 group/other 쓰기와 special bit를 허용하지 않는다. 잘못된 경로·symlink·특수 파일은 기존 검사에서 거절되며, 추출 검증 실패 시 부분 트리 권한을 공개하지 않는다. 권한 변경 실패 시 실제 이미지 Build/Scan 전에 중단한다.

APP Source/Dockerfile/승인 입력, Runtime UID·readOnlyRootFilesystem·SELinux·NetworkPolicy·기존 claim/이미지는 변경하지 않는다. 기존 두 후보 이미지는 이 코드 수정으로 바뀌지 않는다. build code 변경이므로 일반 코드 독립 리뷰가 필요하며 Pre-check 예외로 병합하지 않는다.

## 회귀검증

Linux의 저장소 루트에서 다음을 실행한다.

```sh
python3 -m unittest discover -s tools/delivery -p 'test_source_permissions.py' -v
python3 -m unittest discover -s tools/delivery -p 'test_build_scan.py' -v
```

실제 POSIX 파일에 umask077을 적용해 context0644/0755, 실행 비트, private 루트/테스트 사본 유지, 입력 archive 불변, 링크/경로탈출 거절·기존 경로 미수정과 chmod 실패 시 Build/Scan 중단을 확인한다. 빌드 경로 시험은 실제 Git/snapshot을 쓰지만 Podman/Trivy는 MOCK이다. 기존 `P1 Source validation`의 Ubuntu 전체 회귀시험에 자동 포함된다. Windows에서 POSIX 권한 시험의 SKIP을 PASS로 보고하지 않는다.

## 실제 이미지 시험·후속 실행

이 코드 PR의 정적/회귀시험·CI를 실제 이미지 기동으로 간주하지 않는다. 독립 리뷰/병합 후 새 TBS의 두 OCI archive에서 run.py 및 import 파일0644·디렉터리0755와 digest를 확인하고, 격리된 환경에서 UID10001/GID999·읽기 전용 파일시스템 조건으로 두 이미지의 실제 기동과 업무/metrics 포트 분리를 검증한다. 원본 build receipt, command/rc/log, OCI digest, 기동 결과를 연결한다. 실패하면 Push/Deploy로 진행하지 않는다.

새 Scan/Push 결과와 현재 두 Deployment의 UID/spec/이전 digest를 새 plan/approval/record에 결속한다. 승인 종료17:00이 지난 기존 창이나 이미 소진된 plan claim을 재사용/삭제하지 않는다. 실제 서버 적용 시간·지정 이전 image 복귀 범위를 구체화한 뒤 별도 승인 범위에서 수행한다. 성공 Deploy 이후 HA/복구·최종 APP 회귀·DB 최종 인수 판단 순서를 유지한다.

소스 수정의 철회는 이 PR commit revert로 한다. 이미 생성한 이미지의 소급 정정은 아니며 새 build 입력과 receipt가 필요하다. 서버 복구는 이번 PR에서 실행하지 않으며 기존16:50 이전 이미지 복귀 결과를 보존한다.
