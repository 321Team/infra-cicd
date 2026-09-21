# Private 앱 Source 수신 준비 — 2026-09-18

앱 이관 PR [fds-msa#1](https://github.com/321Team/fds-msa/pull/1)은 main에 병합됐다.
후속 [fds-msa#2](https://github.com/321Team/fds-msa/pull/2)는 base digest와 네 offline lock을 인계한다.
기존 익명 fetch만으로는 조직 Private 저장소를 읽을 수 없어 소비자 인증 경로를 추가한다.
현재 config의 PENDING_APP_HANDOFF, 기존 repository, null Source SHA/승인/transaction-api Unit은 유지한다.
실제 Private fetch·앱 Test/Build/Scan·Push/Deploy는 수행하지 않았다.

## 코드와 검토 범위

- config는 `supreme33-kr/fds-msa` 또는 `321Team/fds-msa`만 허용한다.
  후자만 전용 Token을 요구한다. URL·branch·tag·SSH·대체 host를 입력받지 않는다.
- `source_auth.py`는 고정 HTTPS URL과40자리 SHA로 단 한 번 fetch한다.
  TLS 검증, redirect 거부, 빈 credential helper, system/global config 차단,
  빈 template, hook/submodule 차단을 유지하고 자동 maintenance도 끈다.
- `CICD_APP_SOURCE_TOKEN`은 CLI 시작 때 프로세스 환경에서 제거하고 run에서 소비한다.
  Git의 `--config-env`로 고정 저장소 URL에만 Basic 헤더를 적용한다.
  argv·remote URL·Git config·영수증에 Token/헤더/Token hash를 쓰지 않는다.
  앱 실행기로는 허용된 환경 항목만 전달한다.
- 인증 fetch의 stdout/stderr는 OS의 `/dev/null`로 보낸다. 서버가 Token의 다른 표현을
  출력하더라도 이를 저장한 뒤 치환하는 방식에 의존하지 않는다.
  `source-fetch.log`는 종료 코드와 `RAW_OUTPUT=DISCARDED`만 기록하며 기존 log hash 연결을 유지한다.
  따라서 이 로그만으로 401/403/404/TLS/redirect 원인을 세분화할 수 없다.
- Token 누락/형식 오류는 Git 시작 전에 중단한다. 인증/네트워크 실패와300초 timeout은
  기존 공개 경로·다른 Token·기존 checkout으로 재시도하지 않으며 Test/Build/Scan을 시작하지 않는다.
- Build의 origin 검사와 증적 검사의 repository 연결도 함께 보완한다.
  Private 증적 expected 파일에는 `source_repository: "321Team/fds-msa"`를 명시해야 한다.
  필드 생략은 기존 공개 저장소만 뜻하므로 같은 SHA라도 저장소를 바꿔 통과시킬 수 없다.

`--config-env`는 Token을 명령 인자/파일에 저장하지 않기 위한 수단이다. 동일 UID의 악성
프로세스, 호스트 관리자, Runner 서비스의 원래 Secret 환경, 프로세스 메모리/core dump까지
격리하거나 Python 문자열을 완전히 소거한다고 주장하지 않는다. 이 경로는 신뢰한 전용 Runner와
검토한 Source를 전제로 한다. 인증값을 가진 다른 Job/프로세스와의 격리는 별도 호스트 운영 범위다.

## 운영자가 준비할 자격증명

구현은 GitHub App 설치 Token 또는 fine-grained PAT의 HTTPS 읽기를 받을 수 있다.
이번 일회 준비의 최소 설정은 다음과 같으며, 이미 승인된 발급 경로가 있다면 그 경로를 따른다.

| 항목 | 설정/확인 |
|---|---|
| 대상 | Resource owner `321Team`, 선택 저장소 `fds-msa` 한 개 |
| 권한 | Contents: Read-only, 기본 Metadata: Read-only. Write/Admin/배포 권한 추가 없음 |
| 수명 | 유한 만료, 실행 창에 맞는 기간, 발급자와 회수 책임자 기록 |
| 저장 | infra-cicd Actions Secret `CICD_APP_SOURCE_READ_TOKEN` |
| 사용 | main/manual의 앱 실행 step에만 `CICD_APP_SOURCE_TOKEN`으로 주입 |
| 확인 기록 | Secret 이름·대상 저장소·읽기 권한·만료·담당자·근거만 공유. 값/화면/Token hash 공유 금지 |

정책상 GitHub App을 쓰는 경우 기존 승인된 발급기에서 생성한 제한 설치 Token을 같은 입력에
전달할 수 있다. 이 PR은 App 설치/개인키/자동 발급기를 만들지 않는다. PAT 사용 가능 여부,
조직 승인/SSO 정책, 실제 권한/만료와 Secret 존재는 담당자가 확인해야 하며 문자열 형식
검사로 증명하지 않는다. `GITHUB_TOKEN`이나 classic repo 전체 권한으로 자동 대체하지 않는다.
만료/권한 부족은 실패로 남기고 발급 담당자가 처리한다.

## 진행 순서와 담당

1. 앱 Lead 이재환: 앱 PR #2 코드와 Unit/Contract 범위를 확인한다. 채택 Source40자리 SHA와
   실행 범위를 Issue #11에 인계한다. transaction-api Unit은 명령 또는 해당 없음의 사유·판단 근거가 필요하다.
2. CICD Lead 이권욱: 이 소비자 코드/Workflow 변경을 독립 리뷰에 전달하고 읽기 Secret의
   담당자·최소 권한·만료 준비를 확인한다. 현재 Secret 등록/사용 가능 상태는 미확인이다.
3. 실제 인계값으로 config PR을 작성한다. 코드/설정 변경에는 독립 기술 리뷰를 적용한다.
   이번 PR에서 가짜 승인 SHA나 Unit 해당 없음 결정을 채우지 않는다.
4. Lead가 실행 범위·격리·복구 조건을 확인하고 main/manual 실행을 판단한다.
   공급·base probe·공개 Git 사전검증의 기존 PASS는 보존하되 Private 인증 성공으로 승계하지 않는다.
5. 새 Run에서 Source/handoff → Unit/Contract → Build → Scan 증적을 회수하고 Lead가 AC를 판정한다.
   인증 실패 시 config/Source를 자동 변경하지 않고 신규 실패 디렉터리를 보존한다.
   코드 복구는 직전 검토 Commit으로 되돌림 PR, 자격증명 복구는 발급 담당의 회수/재발급으로 분리한다.

최신 정책은 [정본 REVIEW_AUTHORITY_POLICY](https://github.com/321Team/fds-project-baseline/blob/38f682141c279807050c5b1ea1b4e2b58ee22d63/governance/REVIEW_AUTHORITY_POLICY.md)와
[CICD 운영 기준](https://github.com/321Team/infra-cicd/blob/0509e754259d505ceda72fd884babbbc9b5d082a/docs/governance.md)을 따른다.
독립 리뷰는 코드/런타임 설정 PR 병합에 적용한다. 같은 코드의 실행·증적 수용·AC마다 독립 승인을
중복 요구하지 않는다. 후보 SHA의 제한 시험 여부도 범위·격리·복구를 정한 Lead 판단 사항이다.
현재 CLI는 main/manual 결합을 유지하며 이 PR에서 임의 후보 실행 우회 기능을 추가하지 않는다.

## 검증과 한계

실제 임시 Git 저장소와 고정 SHA bare 수신을 사용한다. 네트워크 transport만 local fixture로
치환하며 Podman/Trivy는 모의 처리한다. 실제 Git으로 URL별 헤더 선택/다른 host·scheme·path 거부를
확인하고, 실제 자식 프로세스가 인증 헤더를 stdout/stderr로 출력해도 증적에 남지 않는지 확인한다.
누락/변형 Token, 비허용 저장소, 실패/timeout, 앱 환경 제외, 새/기존 origin과 증적의 repository
불일치도 검증한다. 실제 GitHub 인증 응답·실서버 환경·앱 pytest·Build/Scan 성공은 미검증이다.

이 변경은 PR #19 위에 쌓은 후속 PR이다. PR #19 branch에 직접 병합하지 않는다.
#19의 잔여 독립 리뷰와 main 병합 후 이 PR을 main으로 전환하여 변경/검증 결과를 확인한다.
Source validation만 해당 base branch의 PR까지 확장한다(호스팅 Runner, Token 없는 시험).
앱 runtime Workflow는 여전히 main/manual 전용이다.

공식 근거: [Git config-env](https://git-scm.com/docs/git#Documentation/git.txt---config-envltnamegtltenvvargt),
[Git URL별 HTTP 설정](https://git-scm.com/docs/git-config#Documentation/git-config.txt-httplturlgt),
[GitHub 인증 선택](https://docs.github.com/en/actions/tutorials/authenticate-with-github_token),
[fine-grained PAT 최소 권한/만료](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens).
