# #46 기존 대상 운영 SITE 조립 후보

2026-10-23 운영 구성 진입점은 [운영 채택표](operating-site-adoption.md)를 따른다. 확인된 Runner 기대값을 `delivery_fds_operating.assemble_job`에 고정했으며 아래 일반 조립 API를 재사용한다. 전체 SITE의 미확정 연결과 Runtime 미실행 상태는 유지한다.

`delivery_fds_job_site.assemble_existing_job_site`는 명시한 운영 입력을 기존 FDS profile → 5역할 source wiring → helper UID 수신 → rendezvous 소유권 wrapper → native `JobSite`로 연결한다. 아직 미채택 입력을 생성하거나 전역 `delivery_job_site.SITE`를 채우지 않는다. **SITE=None·실제 Runtime 미실행**이며, 이 코드는 운영 채택 후보다.

## 명시 입력과 보존되는 계약

| 입력 | 조립·확인 | 실제 공급 책임 |
|---|---|---|
| `runner` | 정확한 `RunnerIdentity`, 일반 UID. status/gate는 실제 build·listener를 호출하지 않음 | 현재 job의 실제 호스트/계정/UID를 채택하는 권욱, 기존 Runner 자료 대조 재환 |
| `config`·`plan` 또는 `materialize_plan` | FDS 기존 대상 profile. full plan 또는 수신 후 materializer 중 하나만 허용 | 권욱: 실제 승인·issuer/platform·producer·baseline 원본/control·Binding 연결. 기존 source10/10·발급 인가 재사용 |
| `wire` | 정확한 `SourceRoleWiring`, approval/platform/producer/finish/**baseline** 5역할, 부모와 같은 monotonic 함수 | 권욱: 실제 repository-scoped 원본 reader·finish scope/decoder. 하영/재환의 기존 영역 원본을 소비하며 새 의미 합성 금지 |
| `routes`·`finish_slot` | 기존 profile/parent의 같은 Binding·SourceRoute 계약 재사용 | 실제 채널·저장소 pin·종료 관측 연결 담당 권욱 |
| `expected_peer`·`expected_helper_uid` | 명시 peer 및 nonroot helper 송신 UID를 기존 커널 인증에 전달 | SSH 접속 계정→Runner 측 helper UID 매핑을 권욱이 채택. job listener UID와 구분 |
| `deadline`·`monotonic` | 부모/owner/source wiring의 동일 job-host 시계와 원래 절대 마감. 생성 중 시계 호출0 | 기존 자격·승인 수명과 job 범위를 채택하는 권욱. SSH 발급측 시계와 직접 비교하지 않음 |
| `publish_ready` | listen 후 인증된 공개 request 알림. bridge 완료를 동기 대기하지 않음 | 실제 알림/발급측 scheduling 연결 담당 권욱 |

SSH 발급측은 [bridge 후보](ssh-memory-bridge.md)의 고정 명시 설정을 별도로 사용한다. 그 설정의 `listener_uid`는 실제 job의 `runner.uid`, 이 조립 함수의 `expected_helper_uid`는 SSH 원격 helper의 실제 UID다. 서로를 임의로 같은 UID로 복사하지 않는다. helper·job은 기존 같은 netns에 있어야 하며 이 함수가 namespace 이동·root·새 계정/key·권한을 만들지 않는다. 같은 UID/root·FD alias·주소 선점/첫 연결 DoS 한계는 기존 계약대로 남는다.

## 호출 시점과 소유권

조립은 콜백·시계·파일·소켓·nonce·자격 공급을 실행하지 않는다. 기존 대상 선택은 명시적이며 first-empty 모드로 폴백하지 않는다. 이후 native driver가 실제 job/Runner/revision을 확인하고 build를 호출할 때만 부모가 공개 request/nonce와 listener를 만든다.

생성 순서는 inert owner → 기존 `connect_existing_parent` → `OwnedRendezvousBuilder` → `JobSite.check`다. 성공 반환 때 owner와 내부 prepared 계획이 기존 build 경로로 이전된다. 이후 부모 실패·수신 실패·완료 뒤의 정리는 기존 owner/driver 계약을 따른다.

조립 중 실패·interrupt에는 자기 inert owner를 닫는다. materializer 경로로 **이번 조립이 내부 생성한** prepared 계획만 닫고, caller가 제공한 full plan·wire·finish slot은 닫지 않는다. 타입 추측으로 borrowed plan을 소유하지 않는다. constructor는 FD를 만들지 않으므로 FD 소유권을 새로 넘기거나 close할 대상도 없다. 원문 예외·callback text를 chain/note로 공개하지 않는다. 정리가 불명확하면 고정 cleanup 오류로 중단한다.

## 10/23 실제 수신 자료의 연결 한계

고정 비공개 공유의 창 C 두 묶음은 수신 사본을 재해시했다. 재환 묶음 `8eb86b92…` 내부47/47, 하영 묶음 `5d462b33…` 29파일/D 시점 manifest10/10이 일치했다. 뒤에 추가한 restart/C-STOP 자료는 그 D manifest 대상이 아니며 전체 TGZ 해시로 수신 무결성을 확인했다. 이 결과는 Runtime/AC 인수와 별개다.

하영 묶음의 `03-after.txt`는 **6객체 UID/resourceVersion 표**이고, `06-pods.txt`는 Pod/Ready/restarts/imageID 표다. `14-cstop.txt`는 22:00:55 Pod0·두 Deployment replicas0 기록이다. 실제 source/render·승인 digest·UID 관측의 대조 근거로 재사용할 수 있으나 다음을 합성하지 않는다.

- UID 표와 manifest를 합쳐 full live spec/managedFields/허용 owner 원본을 만들어 baseline으로 채택하지 않는다. 기존에 확보한 다른 실제 원본이 있는지 먼저 연결하고, 이 묶음 자체가 full baseline JSON이라고 표시하지 않는다.
- 이 Pod 표에 없는 Pod IP/node를 추정해 db01 pcap의 출발지와 결속하지 않는다.
- 수동 창 C 결과를 #46 도구 Runtime·새 유지 DB_RUN·NP/A5 PASS로 바꾸지 않는다. 유지 RUN 이후 수동 변경은 실행 직전 baseline drift 검사에서 기존대로 확인한다.

실제 원본/control 의미·issuer/producer materializer·finish producer/decoder·현재 SSH/key/job/UID·기존6객체 owner/previous의 운영 채택은 여전히 필요하다. 입력 부재를 이유로 이미 수신한 모든 자료를 재제출하거나 같은 시험을 반복 요구하지 않는다. 실제 DB/Robot 유지 승인·최종 회수 기한도 이 조립 함수가 바꾸지 않는다.

## 오프라인 확인

시험은 SYNTHETIC 계획·원본·UID/자격만 쓴다. pure 조립·XOR/None·4/5역할·clock 불일치·borrowed/full과 내부 prepared 소유권·예외/interrupt·status/gate 무I/O와 실제 Linux local UID/rendezvous→기존 부모→owned invocation 이전·정리를 확인한다. API 연결 시험이 실제 source 권위 판정이나 현장 실행의 증거는 아니다. `SITE`/실행 workflow·현재 보안 통제·원본/claim/checkpoint/fence/finish 계약은 그대로다.
