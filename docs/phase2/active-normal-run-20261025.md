# #46 실제 정상 A1 운영 연결 — 2026-10-25

이번 연결은 기존 조립·검증·발급·READY·private v2·종료 관측 구현을 그대로 사용한다.
추가한 것은 **실제 원본/기간을 선택한 구성과 발급측 운영 진입점**이다.
[사용자 지시를 반영한 A1 범위](https://github.com/321Team/fds-project-baseline/issues/46#issuecomment-6396835840)는
정상 logical RUN `FDS-P2-DELIVERY-20261025-A1`의 deploy 1회다. 첫 착수는10/25,
실제 T0부터120분 STOP/복구 인수 범위와10/26 08:30 소비 종료·12:00 정리를 유지한다.
이번 코드만으로 실제 dispatch·본인 로그인·발급·배포·Gate 인수를 완료한 것은 아니다.

## 명시적으로 연결한 값

| 항목 | 선택·실제 근거 |
|---|---|
| 코드 진입 | `delivery_fds_run_20261025.declare_site()`를 prepare와 delivery에서 **명시적으로** 전달. 일반 기본 `delivery_job_site.SITE=None`은 유지 |
| 승인·finish 범위 | 위 실제 A1 댓글의 creator·updated_at·본문 SHA256을 고정. 준비 댓글·baseline 관측 댓글을 실행 승인으로 사용하지 않음. post-close 독립 관측 허용을 같은 A1에 한정 |
| platform metadata | ev248의 `rosa5-run4-7-20261019.tgz`, SHA256 `b7ed9d6b15c55001e06bfe217ee014100c7096eb6699cdbd2b11523373e6901e`. 현재 원 브랜치 `ae4f3e3501330be9e7fc3949092b08c266db10ba`에서243537bytes 동일 확인. run7의 SA UID 원본을 대조한 **역사적 플랫폼 맥락**이며 새 TokenRequest 응답이 아님 |
| baseline | 기존 인계 후6395884121·70d81d8f…d99cbf와 실제 Runner private staging·metadata/file/root 결속을 재사용. 현재 live drift·UID·소유권 검사는 생략하지 않음 |
| producer | 이미 검토한 supply79f4f056…·기존 TBS/Push native StagePin4와 명시 previous2digest를 재사용. 새 빌드·Push·실패 원본을 합성하지 않음 |
| 원본/control 채널 | baseline·infra-cicd·evidence의 정확한 repository ID/owner ID/원본 pin·기존 control pin을 사용. GitHub 숫자 주소20.200.245.245와 공개 root SHA256 `808130157f570b7640069852c88e256738007811a64c3aa9a4c31038347dc19c`를 실제 TLS 검증 후 선택 |
| 발급 위치 | 실제 ansible01/server01_ansible UID1000·Python3.12.15·`/usr/bin/gh`의 기존 GWONUK 인증을10/25 읽기 조회. API13.125.150.237/OAuth52.78.218.60은 선택한 ISRG Root X1과 hostname으로 실제 TLS 검증 |
| 실제 발급 | 본인 숨김 비밀번호 입력 → 실제 READY 수신 뒤에만 OAuth/TokenRequest900초1회 → 같은 job/Binding에 private v2 원본 전달. fingerprint/iat/exp는 기존 native parser가 실제 응답에서 읽음 |

SA 그룹3개는 Kubernetes ServiceAccount에 대한 **기대 설정**이다. 지금 관측한 사실로
표현하지 않으며 실제 SelfSubjectReview·UID/RBAC/TokenReview가 일치해야 한다.
기존 metadata TGZ의 hash와 새 발급 응답 hash를 같다고 요구하지 않는다.

## 실행 위치·순서

1. 최종 HEAD의 독립 코드 리뷰·필수 CI를 확인한다. 다른 실제 쓰기 작업·Runner 소비와
   비중첩을 확인하고 #46에 실제 T0·인수 담당을 기록한다. 새 배포 사양·IAM/RBAC·DB/NP 변경은 없다.
2. **main의 검토된 코드에서** 기존 `P2 ROSA reviewed delivery` workflow를
   `command=deploy`, `execute=true`, attempt1로 **한 번** 요청한다.
   Actions가 만든 실제 run_id/workflow SHA와 delivery 단계의 상태를 조회한다.
   미래 ID를 미리 정하거나 attempt2를 사용하지 않는다.
3. 발급측에도 그 **동일 검토 코드**를 P2 전용 경로에 준비한다. 기존 ansible01 일반 계정의
   대화형 터미널에서 아래 진입점을 실행한다. `ACTUAL_RUN_ID`·`EXACT_WORKFLOW_SHA`는
   2번 실제 원본에서 확인한 값만 대입한다. 기존 준비된 Runner workspace를 재제출받지 않는다.

   ```text
   python3 -I -B -c 'import sys; sys.path.insert(0,"tools/phase2"); from delivery_fds_run_20261025 import issuer_main; raise SystemExit(issuer_main())' --run-id ACTUAL_RUN_ID --workflow-sha EXACT_WORKFLOW_SHA
   ```

   실행 디렉터리는 그 검토 코드의 root다. **미리 권한을 확인한 GitHub 원본 읽기 전용
   자격**, 그다음 본인 ROSA `gwonuk` 전용 비밀번호를 터미널의 숨김 입력에 직접 입력한다.
   채팅·명령줄·환경변수·일반 파일에 쓰지 않는다. 입력은 같은 최초600초 deadline을
   공유하며 non-TTY·getpass의 echo fallback·기한 초과는 로그인/발급 전에 거부한다.
   기존 `/usr/bin/gh`는 GWONUK·실제 run metadata의 로컬 GET에만 쓴다.
   `gh auth token`을 호출하거나 기존 광범위 OAuth 자격을 Runner에 자동 전달하지 않는다.
4. supplier는 기존 엄격한 SSH trust/key와 실제 job checkout의 helper를 사용한다.
   **READY가 실제로 일치하기 전 OAuth 로그인·SA 발급을 하지 않는다.**
   공급 출력 `FORWARDED_NOT_JOB_ACCEPTANCE`는 전달 결과이며 앱/Job 성공이 아니다.
5. native job 결과·정상 PATCH/rollout·sender-close 후 object/rollout 관측·실제 finish 원본과
   fence/claim 종료를 확인한다. 원본은 private 보존 후 metadata만 집계한다.
   최종 소비 후 앱2개를0으로 되돌리는 기존 본인 정리 범위와 실제 readback을 연결한다.
   이 앱0 조치나 전체 자원 회수는 공급 성공만으로 완료 처리하지 않는다.

## 시간·메모리·복구

- 실행 전 확인한 기존 gh OAuth scope는 `gist, read:org, repo, workflow`였다.
  이는3개 저장소 읽기 전용 자격이 아니므로 자동 전달을 제거했다. 별도로 준비할
  fine-grained 자격은 baseline·infra-cicd·evidence3개 저장소에만 Metadata/Contents/
  Issues/Actions 읽기, 계정/조직 쓰기 권한 없음, 최종 회수 전에 만료/폐기하는 안이다.
  실제 발급 화면의 권한·조직 승인 상태를 확인한 뒤 사용한다. 문자열 검사는 서버 권한을
  증명하지 않으며, 이 코드/PR은 해당 자격의 생성·준비 완료나 새 발급 승인이 아니다.
  클라이언트의3개 source route·local-use cap도 서버 권한 제한이나 폐기 증명이 아니다.

- 대화형 입력/인증·handoff는 같은 **최초600초 절대 deadline**을 유지한다.
  입력 후 supplier를 만들 때 갱신하지 않는다. consumer 애플리케이션 예산은600초,
  별도 close 여유60초다. GitHub 원본의 local use cap은 두 유한 단계를 덮지만
  원래 서버 자격의 만료나 회수를 증명하지 않는다. 이 단계 예산은 사용자120분을 연장하지 않는다.
- 원본/control 변경·잘못된 UID/owner·hash·범위·기간·실효 권한·READY·마감 불일치는 STOP한다.
  실제 실패 fence/부분 파일을 남기며 새 RUN/attempt2·원문 수정·강제 소유권 인계로 우회하지 않는다.
- 발급측은 constructor 성공 뒤에만 provider 소유권을 인수한다. 상태/receipt/metadata/close 중
  하나가 실패해도 뒤의 독립 정리 단계를 시도한다. 취소는130으로 보존한다.
  SA token-bearing receipt는 메모리 밖에 출력하지 않는다.
- 발급측 stdout은 **PRIVATE 운영 터미널 기록**이다. token/password/raw 발급 응답은 없지만
  token-free OAuth 복구 metadata를 포함할 수 있으므로 공개 Actions/PR에 복사하지 않는다.
  그 metadata의 `server_revocation_verified=false`는 발급 당시 원본 필드다.
  실제 본인 OAuth DELETE 응답은 별도의 `actor_server_revocation` 상태로 판정한다.
- OAuth 회수 불명확·로그인 응답 유실이면 STOP하고 실제 상태와 private metadata를 인수한다.
  일반 출력으로 서버 회수·SA token 취소를 주장하지 않는다. SA는900초 만료와 실제 권한 회수로 관리한다.
- 실패차단(B1)은 실제 negative producer 원본이 필요하다. 이 정상 경로의 SUCCESS 원본을
  바꾸어 만들지 않는다. 별도 restore(R1)도 실제 결과·별도 action Binding/원본·유효 권위로
  연결해야 한다. 이번 정상 deploy-only 선택은 preflight/restore를 거부하며 자동 실행하지 않는다.

## 검증과 완료 판정

오프라인 새 시험은 명시적 구성·기본 미구성 유지·시간/범위 거부·원본 deadline 비갱신·
실제 context 대조·반납/취소/불확실 인수·비밀 출력 금지를 검증한다.
HTTP/SSH/API 응답과 clock은 SYNTHETIC이며 실제 Runtime 증거가 아니다.
최종 Linux CI와 사람 독립 리뷰 후에만 Runtime을 요청한다.
#46 전체 완료에는 정상/실패차단/복구·실제 종료 인수가 남으며 G4/G5/G6·최종 Close는 별도다.
