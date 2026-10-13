# 최종 이미지 기동 검사 — 2026-10-13

P1-CICD-01 Lead 이권욱. 독립 기술 리뷰 이하영. AI 사용: CODEX_ASSISTED.

## 문제와 적용 범위

[Deploy 실패 증적213](https://github.com/321Team/fds-project-evidence/pull/213)의 `/app/run.py` 권한 오류는 Test/Build/Scan 뒤 실제 기동에서 드러났다. [권한 수정 PR76](https://github.com/321Team/infra-cicd/pull/76)은 소스 추출 문제를 수정한다. 이 후속 변경은 schema3에서 두 최종 이미지 각각의 저장·무결성 검사 직후, Scan 전에 격리 기동 검사를 추가한다. 두 서비스가 모두 PASS해야 Scan에 도달한다.

PR76과 별도 변경이다. 실제 새 빌드에는 두 PR이 독립 리뷰·병합된 main이 필요하다. 기존 archive나 승인·claim을 수정하거나 이전 시험 결과에 새 검증을 소급 부여하지 않는다. schema1/2의 과거 경로와 receipt는 변경하지 않는다. 이 PR은 CI/CD 판정 경로를 변경하므로 Pre-check 독립 리뷰 면제 대상이 아니다.

## 실행 위치·전제·순서

- 기존 cicd-runner01/server01_cicd-runner, UID1000, rootless Podman6.1.0 및 회차별 private storage를 그대로 사용한다. 기존 workflow의 `app_workflow.py --execute` 경로에 포함되며 별도 자동 dispatch는 추가하지 않는다.
- 승인된 Source/Dockerfile 프로필을 유지한다. 현재 입력의 앱 Source는 affbc69beef9967f7977d09a6fec14f7fb6c0e94이며 정상 API는 `/livez`, API/engine 업무 포트8000/8001, metrics9091/9090이다. 실행 시 최신 승인 입력과 이 계약이 같은지 확인한다.
- 새로 Build/save한 OCI의 config digest와 같은 로컬 image ID로 컨테이너를 생성한다. inspect로 CID·고유 소유 라벨·image ID·기본 ENTRYPOINT/CMD·작업 디렉터리를 대조한다. 이미지의 기본 실행 명령으로 시작한다.
- UID10001/GID999, read-only rootfs, `--read-only-tmpfs=false`, network=none, cap-drop=ALL, no-new-privileges. publish/host mount/secret/실제 DB 자격증명/보안 해제는 사용하지 않는다. 이미지 선언 volume도 자동 생성하지 않는다.
- DB URL은 실제 계정이 아닌 loopback port1의 합성 입력이다. `/readyz`·거래 생성·평가 POST·운영 DB는 호출하지 않는다. 네트워크는 컨테이너 loopback에 한정한다.
- 실행 중인 같은 컨테이너에서 `/venv/bin/python3` 표준 라이브러리 probe를 실행한다. 실제 UID/GID 및 `/`, `/app`, 존재하는 `/tmp`·`/run`·`/var/tmp`의 읽기 전용 상태를 확인한다. `/app` 전체 일반 파일(최대10000항목)을 실제 open/read하며 링크·특수 파일·접근 오류를 거절한다.
- 최대30초 동안 `/livez`=200/status ok, 업무 `/metrics`=404, 전용 `/metrics`=200/해당 앱 counter 존재, metrics `/livez`=404를 모두 확인한다. redirect와 proxy를 사용하지 않고 응답 크기를 제한한다. exec 명령 자체도45초로 제한한다.
- probe 뒤에도 컨테이너가 실행 중인지 확인하고, 마지막200줄 로그를 보존한 뒤 자신의 CID만 제거한다. 재조회에 CID가 남거나 로그/정리 실패가 있으면 PASS로 표시하지 않는다. 실행 중 archive SHA가 바뀌어도 Scan에 진행하지 않는다.

## 증적과 판정

`build-scan/result.json`의 `image_startup`에 서비스별 identity(archive/config/manifest), probe 코드 SHA256, CID, 시각, probe 결과, cleanup을 남긴다. 정상 명령의 rc/log hash는 기존 steps에 기록되며 원본 로그는 같은 private 회차 폴더에 보존한다. 명령 timeout에는 정상 rc를 꾸며 넣지 않는다. CID 파일은 `<service>-image-smoke/container.cid`에 남아 정리 식별에 사용된다.

`scope=ISOLATED_IMAGE_STARTUP_ONLY`, DB_READINESS/APP_E2E/KUBERNETES_RUNTIME=NOT_VERIFIED다. 실제 파일 읽기·앱 기동·listener 분리의 제한된 결과이며, 운영 DB 저장/조회·NetworkPolicy·HA·클러스터 배포·APP 최종 인수가 아니다. 다음 Push는 이 검사가 포함된 새 Build/Scan 결과와 새로운 승인 입력을 사용한다. 이 변경은 과거 receipt의 Push 자격을 소급 재평가하지 않는다.

## 실패·정리·복구

생성·기동·파일 읽기·HTTP·동일성·정리 중 하나라도 실패하면 BUILD_SCAN=FAIL이며 Scan/후속 Push/Deploy에 진행하지 않는다. 자동 재실행은 없다. create가 시간 초과되더라도 private CID 파일이 있으면 해당 CID 로그 수집·정리를 시도한다. CID가 없거나 잘못됐으면 이름만으로 다른 컨테이너를 삭제하지 않으며 cleanup=NOT_CONFIRMED를 남긴다. 이런 회차는 보존하고 다음 실행 전에 같은 private storage의 잔여 상태를 수동 확인한다.

복구는 검토된 commit revert다. 기존 서버·앱 이미지·claim을 바꾸지 않는다. 게이트 실패를 피하기 위해 읽기 전용·계정·SELinux·방화벽·인증서 검증을 완화하지 않는다. 예상치 못한 Podman6.1 inspect 형식이나 Runtime 제약은 원본 로그로 확인한 뒤 수정·재검토한다.

## 검증 단계

`python3 -m unittest discover -s tools/delivery -p 'test_image_smoke*.py' -v`는 임시 파일과 MOCK Podman/합성 HTTP 응답 시험이다. schema3 통합 회귀는 두 서비스 각각의 실패, PASS 누락, archive 변경이 Scan을 차단하는지 검사한다. 기존 offline probe zipapp의 import 회귀도 유지한다.

현재 실제 두 앱 이미지 재Build·Podman 기동은 NOT_RUN이다. 코드 CI 성공을 이미지 기동 성공으로 해석하지 않는다. 독립 리뷰/병합 후 승인된 후보 빌드에서 실제 두 이미지 결과·권한·로그·정리·해시를 확인해야 하며, 실제 서버 Deploy 시간창과 인수는 별도다.

공식 옵션 기준: [Podman6.1 run](https://docs.podman.io/en/v6.1.0/markdown/podman-run.1.html), [create](https://docs.podman.io/en/v6.1.0/markdown/podman-create.1.html). 앱 기준: [pinned run.py](https://github.com/321Team/fds-msa/blob/affbc69beef9967f7977d09a6fec14f7fb6c0e94/transaction-api/run.py), [health 계약](https://github.com/321Team/fds-msa/blob/affbc69beef9967f7977d09a6fec14f7fb6c0e94/transaction-api/app/main.py).
