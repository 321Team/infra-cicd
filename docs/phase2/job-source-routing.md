# #46 native job의 원본·control·finish reader 연결

`delivery_job_sources.py`는 PR113의 native job/부모·인증된 자격 수신·준비 계획 경로에 저장소별 실제 reader를 연결한다. **전송 인증·원본 의미·GO를 만드는 모듈이 아니다.** import/생성 시 파일·네트워크·자격 수신·프로세스·claim은0이며 `SITE=None`과 실행 workflow를 유지한다.

## 역할과 순서

검토된 site Python 코드에서 `SourceRoleWiring(binding=..., routes=..., finish=..., producer_repository=..., wall=..., monotonic=...)`을 만든다. **wall·monotonic은 부모/source/factory와 같은 기준의 clock을 명시**하며, custom clock 사용 중 finish locator만 다른 실제 시간 기본값을 쓰지 않도록 암묵 기본값을 두지 않았다. routes는 아래 순서의 `RoleRoute(role, original_repository, control_repository)` tuple이다. 두 저장소 값은 실제로 확인한 `RepositoryPin`이어야 하며 이름·ID·owner ID를 추측하지 않는다.

| 순서 | role | 기존 소비자 |
|---|---|---|
| 0 | approval | 승인 원문과 채택된 철회 control |
| 1 | platform | 실제 issuer 원본과 그 control |
| 2 | producer | 실제 공급/채택 원본과 그 control |
| 3 | finish | 실제 종료 원본/기존 완료 scope와 그 control |
| 4(선택) | baseline | 실제 기존 객체 기준선 원본/control. 이 행의 유무는 완성 계획의 baseline과 일치해야 함 |

같은 저장소의 reader를 여러 역할이 빌려 쓸 수 있다. 원본·control이 같은 저장소라는 이유만으로 다른 token을 재발급하게 하거나 기존 source credential의 허용된 여러 저장소 사용을 막지 않는다. 각 역할은 별도의 실제 pin·원본 의미·현재 control로 인증되며 reader 공유가 권한 공유/승인 증거는 아니다. 선택적 `producer_repository`는 기존 Actions Run 대조 reader에 연결한다. `producer_pins`는 기존 부모 설정에서 함께 전달해야 한다.

1. 기존 `JobSite`/driver는 native repository/event/ref/workflow SHA/attempt·실제 Runner identity를 확인한다. `SourceRoleWiring`은 이 검사를 대체하지 않는다.
2. 부모는 **ready 게시 전** 같은 Binding과 필요한 모든 원본/control 저장소의 명시적 `SourceRoute`가 있는지 비교한다. 누락·다른 RepositoryPin이면 ready/전송을 시작하지 않는다. 채택된 추가 경로를 새 승인 없이 발견·생성하지 않는다.
3. 기존 ingress가 실제 전송 peer·job target/nonce·프레임/EOF를 대조한 뒤 메모리 자격과 실제 Linux pipe를 driver로 넘긴다. 실제 인증된 `open_channel`의 운영 채택은 여전히 별도다. 이 모듈은 SSH key·호스트·원격 계정을 기본 설정하지 않는다.
4. `PrivateSourceSession`이 자격을 수신한 뒤 이 wiring을 1회 호출한다. `session.reader(RepositoryPin)`만 사용하고, 기존 source handoff 절대 마감의 전후를 확인한다. 저장소 누락/실패도 소비되며 wiring을 자동 재시도하지 않는다.
5. 준비 계획이면 trusted materializer가 실제 받은 자격/원본으로 완성한다. 이어 실제 plan의 approval/issuer/producer(+baseline) 원본·control 저장소를 routing과 비교한다. 그 뒤 **기존 factory가 실제 원본/control·유효기간·issuer·현재 RBAC를 인증**한다. pin/형식/저장소 대조만으로 원문 의미를 승인하지 않는다.

`connect_parent`의 `wire`에 이 객체를 전달한다. 기존 callable 방식은 호환 유지한다. FDS 연결부도 다른 Binding의 wiring을 거부한다. 실제 Runner/peer/관리 접근 채널, 실제 승인·issuer/producer·control 의미/위치와 실발급 결과, 수동 C 이후 기존6객체 기준선·previous 채택은 여전히 확인된 원본을 요구한다. 이 코드는 그 값을 채우지 않는다.

## finish 모드

`finish`는 다음 중 하나이며 동시에 두 모드를 선택하지 않는다. `finish=None`은 기존 외부 finish slot/capture 경로를 보존하며 없는 finish를 성공으로 만들지 않는다.

- `ObservationRoute(scope_grant, required_followups)`: **별도로 의미를 채택한 실제 기존 완료 scope**와 기존 `FOLLOWUPS`를 명시한다. 배포 approval을 자동 복사하지 않는다. 기존 `FinishObservationConfig`를 만들고 sender close 뒤 새 객체/rollout readback→원본 create-once 보존→scope/control 재인증 뒤에만 완료를 허용한다. DB/앱 중지·C-STOP·토큰 회수까지 남아 있는 scope를 단순 readback 종료로 간주하면 안 된다.
- `CollectionRoute(policy, decode, comment_id, max_seconds)`: 기존 `FinishCollectionPolicy`와 **실제 native 원본 의미를 해석하는 필수 decoder**를 받아 `NativeFinishLocator`→`FinishCollectorConfig`로 연결한다. blob은 comment_id=None, comment는 실제 확인한 ID가 필요하다. 생성/배선 중에는 조회·decoder·worker를 실행하지 않는다. 기존 terminal 이후 collector의 hard timeout과 원본/control 재인증을 그대로 사용한다. decoder·원본 형식·GO를 새로 만들지 않는다.

원본과 control의 저장소는 finish scope/policy와 일치해야 한다. 원본/정책이 아직 없으면 이 모드를 설정해 운영 채택하지 않는다. 생성되는 관측 바이트·파일 해시·callback 반환형은 독립적인 종료 승인이나 서버 권한 회수의 증거가 아니다.

## 실패·소유권·검증

수신 전 topology 불일치는 ready와 credential handoff 이전에 차단한다. 수신 후 plan/route 불일치·마감 소진은 claim/write 이전에 중단하고 source session이 자기 자격/채널을 닫는다. 종료 원본·scope/control 철회/실패는 기존 fence를 보존하며 자동 unlock·재발급·배포 재시도를 하지 않는다. 공통 reader/transport의 소유자는 session 하나이고 각 adapter는 빌린 reader를 닫지 않는다. 로컬 close는 실제 서버 token revoke나 보안상 메모리 zeroization과 다르다.

오프라인 시험은 SYNTHETIC 저장소·원본·자격·시계와 가짜 API만 사용한다. 현재 source/control 인증·준비 계획·session/claim을 실제 함수로 연결하고, 종료 후 control 철회 시 claim 보존, 올바른 scope의 fresh 관측 후 완료, 같은 token의 명시적 다중 저장소 사용, 순서/Binding/저장소 혼입·재사용·status 무수신을 검증한다. Linux 전용 시험은 실제 익명 pipe→native driver→wiring→authority 경로를 검사한다. 이는 원격 인증 채널이나 현장 Runtime 성공이 아니다.

STATIC/MOCK/CI·독립 REVIEW·MERGE·운영 SITE 채택·RUNTIME·AC 인수는 분리한다. 실제 환경 접속·자격 발급·권한 변경·Runtime은 수행하지 않는다.
