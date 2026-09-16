# Trivy Runner Job 검증

## 목적과 현재 근거

2026-09-15 Runner 로그인 세션에서 Trivy 0.74.0 설치, 공식 GHCR DB 다운로드,
테스트용 Django 2.2.0 탐지(21건, CRITICAL 6 / HIGH 15, rc=10),
빈 캐시의 DB 누락 오류(rc=1)를 확인했다. 양성/음성 시험 첨부 파일은 각각 8/8,
5/5 체크섬이 일치했다. 상세 기록은 [PR #2](https://github.com/321Team/infra-cicd/pull/2)와
[P1-CICD-01 #16](https://github.com/321Team/fds-project-baseline/issues/16)에 있다.

이 Workflow는 같은 검사를 실제 GitHub Actions 서비스 환경에서 수행하고
Workflow SHA·Run URL·Attempt·계정·DB 해시를 연결한다.
2026-09-16 [Run 35047422631 / Attempt 1](https://github.com/321Team/infra-cicd/actions/runs/35047422631)은
merge SHA `434a6ddf0d475ffb9857a01dfa2b229eaf637526`에서 PASS였다.
수신한 원시 파일 11개의 체크섬, HIGH 15/CRITICAL 6, rc=10 및 DB 누락 rc=1,
Run/SHA/Attempt를 대조했다. 앱 이미지/Push/Deploy 차단은 미검증이다.

## 실행 조건

- 승인된 변경이 main에 병합된 뒤 Actions → **P1 Trivy tool check** → Run workflow → main.
- 작성자 외 기술 리뷰와 병합 판단은 [운영 기준](governance.md)을 따른다.
- workflow_dispatch만 허용하고 main에서만 Job을 실행한다. PR 코드를 내부 Runner에서 자동 실행하지 않는다.
- cicd-runner01 / server01_cicd-runner / UID 1000, SELinux Enforcing, rootless Podman을 확인한다.
- Python 3.11 이상, Git, Podman, getenforce와 기존 Trivy 0.74.0이 필요하다.
- 설치 바이너리는 앞서 검증한 SHA-256을 고정한다. 다른 버전으로 변경할 때 코드·오류 계약을 다시 검토한다.
- `$HOME/.cache/trivy/db`의 trivy.db와 metadata.json이 필요하다. Job은 DB를 자동 다운로드하지 않는다.

## 검증 단계의 명령 경로

9월 16일 수집 결과, 서비스 PATH와 실제 Job PATH가 달랐다. Job에는 관측 당시
존재하지 않던 Runner HOME의 `.local/bin`과 `bin`이 시스템 경로보다 먼저 포함돼 있었다.
현재 git/podman/getenforce는 root 소유 RPM 파일로 해석됐지만, 홈에 같은 이름의
실행파일이 추가되면 이후 탐색 결과가 바뀔 수 있다.

검증 step은 PATH를 `/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin`으로 고정한 뒤
`/usr/bin/python3`로 판정기를 실행한다. 이 PATH는 판정기가 실행하는 자식 프로세스에도
상속되며 `TOOLCHECK_PATH` 로그로 확인한다. checkout action이나 Runner 서비스 전역 PATH를
변경하는 설정은 아니다. ACL·전체 부모 경로·동시 바이너리 교체까지 보증하지 않는다.

Trivy의 버전/해시 고정은 수동 반입한 릴리스와 오류 계약을 유지하기 위한 조건이다.
OS 패키지 도구 전체의 바이너리 해시 고정 정책을 추가하지 않는다.
사전 점검에서 Trivy 해시는 기존 값과 같았고 조사한 설정에 직접 갱신 참조는 없었다.
다만 간접 스크립트·외부 구성관리까지 포함한 자동 갱신 부재는 미확인이다.
이 PATH 변경은 별도 리뷰·병합 후 실제 Job 로그로 확인해야 하며,
위 성공 Run은 변경 전 결과다.

## 검사 동작과 판정

1. 검토한 전체 SHA에 고정된 checkout으로 이벤트 Commit을 받고, 실제 checkout SHA를 대조한다.
2. 원본 DB를 RUNNER_TEMP의 임시 캐시에 복사하고 복사본 해시를 원본과 대조한다.
3. requirements.txt 텍스트만 생성한다. 테스트용 Django를 설치하거나 실행하지 않는다.
4. 실제 스캐너로 HIGH/CRITICAL을 검사하고 rc=10과 원시 JSON의 패키지·버전·타깃·탐지 결과를 확인한다.
5. 별도 빈 캐시에서 rc=1과 특정 DB 누락 오류를 함께 확인한다. 다른 오류나 빈 결과는 PASS가 아니다.
6. 실패 시 Job을 실패로 종료하며, 성공 시 `TRIVY_CI_TOOLCHECK=PASS`를 기록한다.
   항상 `DELIVERY_GATE=NOT_VERIFIED`를 표시한다.

탐지 건수는 사용한 DB에 따라 달라질 수 있어 21건으로 고정하지 않는다.
이 도구 시험은 DB 메타데이터와 해시를 기록하며 갱신 기한 정책을 새로 정하지 않는다.
실제 이미지 배포 파이프라인의 DB 갱신/오류 차단 정책은 별도 구현 대상이다.

## 증적과 보관

Job 로그와 Summary에 `EVIDENCE_DIR`를 남긴다. Runner HOME 아래 고유한
`trivy-ci-<run>-<attempt>-*` 디렉터리에 다음 파일을 보존한다.

- context.json: Run URL·Attempt·Workflow SHA·호스트/계정·시작 시각·기대 바이너리 해시.
- installed-binary.sha256: 실제 설치 바이너리에서 계산한 해시.
- fixture/requirements.txt, findings.json/log/exit-code.txt, missing-db.log/exit-code.txt.
- metadata.json, DB_SHA256SUMS, result.json, SHA256SUMS.
- 오류 시 생성된 부분 결과도 해당 디렉터리에 남긴다.

원시 파일은 GitHub Artifact로 자동 업로드하지 않는다. 기존 SFTP 증적 인계 방식으로
수신한 뒤 SHA256SUMS를 검증한다. 임시 DB 사본은 정상 종료/처리된 오류에서 정리되며,
강제 프로세스 종료 시 잔여물이 있을 수 있다. HOME의 증적은 수신·보관 확인 후 정리한다.
컨테이너 Build, Registry 인증/Push, Kubernetes 접근, 시스템 설정 변경은 이 Workflow에 없다.

## 소스 검증

`python3 -m unittest discover -s tools/trivy -p 'test_*.py' -v`는 결과 판정기의
양성/빈 결과/잘못된 타깃·버전/종료코드/다른 오류 처리만 검증한다.
이 단위시험을 실제 Trivy 실행 결과로 세지 않는다. source-validation은 GitHub 제공 Runner에서
이를 실행하며, 실제 내부 Runner 검사는 수동 Workflow의 Run으로 확인한다.
