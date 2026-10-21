# #46 P2 수동 job / driver 배선

## 현재 범위와 상태

P2 전용 `p2-rosa-delivery.yml`의 수동 요청을 `delivery_job.py`에서 검사한 뒤 기존 `delivery_runtime_site.main`으로 연결한다. 현재 `delivery_job_site.SITE = None`이므로 운영 실행은 **BLOCKED**이다. 실제값을 채운 예제 구성, 승인 입력 JSON, 환경변수 토큰 로더, 자동 발급기는 추가하지 않았다. 이 PR은 코드·오프라인 시험 범위이며 Runtime GO나 실제 배포 완료를 뜻하지 않는다.

기존 P1 `app-deploy.yml`은 변경하지 않았다. Runner 등록에서 확인한 기존 label만 후보로 사용한다. 등록 정보는 현장 준비 또는 P2 사용 승인이 아니므로 hostname·일반 계정·UID도 별도로 검토된 `JobSite.runner`와 일치해야 한다.

## 연결 순서

1. GitHub의 `workflow_dispatch`만 허용한다. 입력 기본값은 `status`, `execute=false`이다. push·PR·재실행(attempt 2 이상)·main 외 브랜치는 실행 경로에 진입하지 않는다.
2. GitHub hosted `prepare`에서 native 저장소·ref·event·Run/Attempt와 checkout 코드 HEAD를 확인하고 고정된 SITE 선언 유무만 검사한다. builder·자격 수신·서버 조회·claim은 하지 않는다. 현재 미구성은 실패(rc2), downstream job은 skipped이다. `dispatch_configured=true`도 승인이나 실행 가능 판정은 아니다.
3. `delivery` **job 자체의 if**에서 prepare 성공·구성 표시·명시 요청을 확인한 뒤에만 기존 self-hosted label을 배정 후보로 쓴다. 단계 내부 검사에만 의존하지 않는다. [GitHub 공식 job 조건 규칙](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/control-jobs-with-conditions)에 따라 조건 불충족 job은 skipped이다. skipped를 Runtime PASS로 해석하지 않는다.
4. 새 native Run/Attempt 전용 checkout 경로가 없음을 확인하고 정확한 workflow SHA를 고정 checkout한다. 영구 credential 저장·기존 경로 삭제·submodule·LFS는 사용하지 않는다. 드라이버는 일반 계정 identity, native context, 코드 HEAD를 확인한 뒤에만 검토된 builder를 1회 호출한다. workflow 코드 SHA와 앱 manifest source SHA는 서로 다른 값이다.
5. builder가 정확한 `RuntimeInvocation`으로 소유권을 넘긴 후 driver가 context·action·typed 구성 정합성을 확인한다. 기존 runtime이 승인 원본/control·issuer·producer·선택 Actions Run/Jobs·baseline·단기 credential·RBAC·독점 접근·claim/checkpoint·finish를 기존 규칙으로 재검증한다. `--execute`나 native 메타데이터로 이를 대체하지 않는다.

## 운영 구성 채택 전 잔여

| 담당 | 필요한 실제 입력 / 완료조건 |
|---|---|
| 권욱 (#46 구현·통합) | 실행 GO의 정확한 binding·명령·대상·기간, 승인된 범위에 맞는 실제 source/control adoption, typed builder·finish 연결, 실제 input/Git/store 경로를 검토 코드로 채택 |
| 하영 (ROSA·현장 권한) | 실제 issuer/subject UID·CA·endpoint IP·기존 최소 RBAC·단기 credential의 별도 전달 경로와 만료 근거 확인. 원본 수신·검토와 권한 발급을 구분 |
| 재환 (producer·Runner) | producer native Run/Attempt/Jobs/step·소스/contract/render/digest 연결, Runner 일반 계정·UID·사용 가능 상태와 코드 채택 근거 확인 |
| 권욱 통합, 해당 담당 검토 | 공개 승인 JSON이나 임의 플러그인 대신 검토된 코드 builder로 위 사실을 연결하고 exact HEAD 검토·오프라인 시험 완료. 미확정 값은 계속 미구성 |

권욱의 구성 채택 작업과 타 담당자의 실제 관측 생산을 구분한다. 이미 수용한 증적·동일 시험을 새 양식으로 다시 요구하지 않는다. 기존 운영 준비 자료의 미확정 항목을 이 PR의 기본값으로 합성하지 않는다.

`JobSite.build(context, command)`는 `RuntimeInvocation` 반환 전 발생한 부분 생성 자원까지 스스로 정리한다. 반환 후에는 driver가 정확한 owned ROSA receiver / private source session만 닫는다. borrowed reader나 임의 객체의 `close`는 호출하지 않는다. 단기 토큰 발급 자체는 이 배선에 없으며, 기존 수명·최초 쓰기 예산을 연장하지 않는다. workflow 30분 timeout은 token TTL이나 GO 기간이 아니다.

이미 발급된 in-memory 자격을 받는 부모 연결 함수와 용량·마감·부분 실패·운영 채택 전 잔여는 [부모 전달 연결부](owned-job-handoff.md)에 정리한다. 이 선택 함수의 추가는 SITE 구성이나 실행 승인이 아니다.

## 실패·정리·재실행

- status는 native env·Git·Runner·builder·credential에 접근하지 않는다. gate는 native 메타데이터와 로컬 코드 HEAD만 읽는다. 본 문서와 시험을 확인하기 위해 운영 workflow를 dispatch하지 않는다.
- builder의 진단과 runtime 출력은 각각 128 KiB UTF-8 한도 안에서 내부 수신한다. 초과 출력은 버리고 플래그만 남기므로 관측 한도가 실행 중인 runtime에 예외를 주입하지 않는다. 반환 후 결과를 거부하고 출력 실패이면 고정 범위의 반환 코드만 보존하며 receipt를 재구성하지 않는다. 정상 구조화 결과의 receipt/fence/completion과 native trace는 분리하며 임의 예외 본문·경로·자격을 출력하지 않는다. JSON은 runtime 결과 수신용이며 승인 입력 로더가 아니다.
- 명령/action 또는 context가 다르면 전달받은 채널을 수신 전에 닫는다. 소유권 이전 전 builder 실패는 builder 책임이다. Python trusted code가 반환하지 않거나 프로세스가 강제 종료되는 경우까지 정리 완료를 주장하지 않는다.
- runtime 진입 후 결과가 유실되면 `NOT_VERIFIED / INSPECT_REQUIRED`로 중단한다. 쓰기 전이라고 단정하거나 자동 재Apply·restore·unlock·삭제하지 않는다. 실제 receipt와 영속 claim/checkpoint를 확인한 별도 복구 판단이 필요하다.
- 정리 실패 시 rc2 / STOPPED로 남기고 이미 기록된 nested receipt·`RELEASED`를 덮어쓰지 않는다. SESSION_COMPLETED도 Health/Rule·토큰 회수·전체 AC 완료를 뜻하지 않는다. 미실행 first-deploy의 이전 baseline은 임의 생성하지 않는다.
- native attempt 2는 차단한다. 새 Run을 만들더라도 기존 fence와 원본 승인·상태 검증을 통과해야 하며 새 Run 자체가 재실행 GO가 되지 않는다. 새 checkout은 자동 삭제하지 않고 사용한 경로를 관리 대상에 남긴다.

## 오프라인 검증

실행 위치는 이 저장소 checkout이다. 검증 환경은 CPython 3.12 + PyYAML 6.0.3이다. `requirements-test.txt`는 Linux x86_64 / Windows AMD64 wheel 해시를 고정하며 hosted CI의 임시 target에만 설치한다. 일반 Runner·운영 환경 설치가 아니다. 의존성 준비는 PyPI 다운로드이며, 그 뒤 시험 본체는 외부 통신을 하지 않는다.

`python -m unittest discover -s tools/phase2 -p 'test_delivery_job*.py' -v`로 새 시험만, `python -m unittest discover -s tools/phase2 -p 'test_*.py' -v`로 통합 회귀를 실행한다. 이 명령은 SYNTHETIC fixture·가짜 서버 경계·격리 MemoryStore만 사용하며 실제 GitHub/AWS/ROSA/Runner 요청을 하지 않는다.

정상 driver→source session→원본 factory→claim/checkpoint→finish 연결, native Actions 성공/실패, live 채널의 action/context 불일치, metadata·SHA·identity 불일치의 builder 전 차단, 자격 누설 방지, 정리 실패와 fence 보존, YAML의 job-level 차단을 확인한다. 실제 서버 검증은 별도 미실행이다.
