# #46 운영 인증 판정·현장 진입점 구현

후속: [native Run·종료 관측 연결](native-run-finish-connection.md)은 선택적 Test/Build/Scan/Push Run 관측과 sender.close 뒤 finish 인수 hook을 제공한다. 아래의 실제 원본·운영 채택 및 Runtime 구분은 유지한다.

이 변경은 운영용 **코드 구현과 오프라인 검증**이다. 기존 callback 경계에 구체적인 원본 의미 채택 판정기와 현장 연결 factory를 제공한다. 실제 원본·자격·운영값 채택, Runtime 실행, 정상/실패 배포·Health/Rule·복구 인수는 별도이며 #46은 In Progress다.

## 구현 범위와 신뢰 전제

`delivery_semantic_authority.py`의 `CodeAdoptedEvidenceResolver`는 **검토된 Python 현장 구성 코드가 실제 원본의 의미·권한을 채택하는 선택적 방식**이다. 기존 `CodeAdoptedGrantResolver`의 원본·주체·채택 근거·유효 창·fresh control 조회를 재사용한다. `IssuerFacts`, `ProducerFacts`, `FinishFacts`의 타입이나 문자열 자체가 사실을 인증하는 것은 아니다.

구성 작성자는 기존 원본의 해당 내용과 판정권을 확인한 뒤 그 원본의 exact pin과 사실을 연결해야 한다. 예컨대 issuer 원본의 실제 발급·수신자·최소 권한·Route 승인, producer 원본의 clean 생성·Test/Build/Scan/Push·P2 current/previous 채택, finish 원본의 후속 관측·독점 접근 종료를 검토해야 한다. 실제 원본에 없는 사실을 채우면 이 신뢰 전제가 성립하지 않는다.

이 모듈은 JWT 서명 검증기, TokenRequest 발급기, CI attestation 자동 검증기가 아니다. 해시·GitHub 생성 계정·성공 요약·SSAR의 필요한 권한 관측만으로 그 사실 전체를 증명했다고 표시하지 않는다. 기존 원본 양식을 다시 만들거나 새 제출 서식·매 회 새 GO를 강제하지 않는다. 실제 배포 허가는 **별도의 approval adapter**가 같은 RUN/action/Binding/창에서 확인한다. 의미 채택의 유효 창을 배포 GO로 사용하지 않는다.

선택한 방식의 철회 확인은 **이미 의미와 갱신 규칙을 채택한 control의 명시적 ref/버전**에 한정한다. 다른 댓글이나 외부 채널의 모든 철회를 자동 탐지하지 않는다. 새 control ledger 작성이 팀의 추가 의무가 되는 것은 아니다. 다른 신뢰 방식을 쓰는 기존 명시적 adapter 경로도 유지한다.

## 판정 내용

| 대상 | 구현된 대조 | 운영 연결 시 필요한 실제 근거 |
|---|---|---|
| issuer | 원본 바이트/receipt, 동일 자격 지문, issuer/audience, 실제 발급·만료, 요청 900초·실제 수명 900초 이하, CA·숫자 IP, RBAC/Route 원본 참조 | 실제 발급·권한 원본의 의미와 신뢰를 채택한 구성 |
| producer | Binding의 source/contract/render/checker·current 이미지 전체, previous 전체 profile 또는 명시 채택한 최초 부재, Test/Build/Scan/Push·clean render·P2 채택 참조 | 그 참조가 같은 공급 계보라는 실제 원본 검토. [최초 부재](first-deployment-inputs.md)는 deploy+빈 대상의 exact 404 baseline 경로에서만 허용 |
| finish | 같은 RUN/action, 실제 durable terminal progress의 key, 모든 ACK/응답·UID·rollout, 관측 시각과 후속/독점 종료 원본 | 실제 실행 후 생성된 원본과 그 의미·판정권 |
| 기존 객체 기준선 | 별도 채택 원본·control 재조회 사이에 동일 자격으로 GET하고 승인된 UID/spec/owner와 비교 | 복구/기존 객체의 허용 소유·사양 검토. fresh GET을 자동 승인하지 않음 |

두 번의 source/control 판정이 달라지거나, 조회 거부·삭제·만료·다른 자격·진행 변경이면 중단한다. 자동 갱신·재시도·기준선 재채택은 없다. 현재 객체는 기존 보수적 normalization으로 비교하며 status/resourceVersion 등의 기존 허용 변동 외 UID·사양·소유 변경은 거부한다.

## 현장 연결

`delivery_runtime_site.py`의 `RuntimeAuthorityFactory`는 실제 session이 제공한 progress closure와 동일한 credential로 다음을 구성한다.

1. 기존 원본과 `CodeAdoptedGrantResolver`를 연결한 approval.
2. issuer 의미 판정과 `ReadbackCheckedPlatformAdapter`를 연결한 플랫폼 관측. 기존 CA/hostname 검증, cluster/namespace UID, SelfSubjectReview, 지정 자원의 get/patch SSAR를 사용한다. 필요한 권한 관측은 최소 권한 전체의 증명이 아니다.
3. producer 의미 판정.
4. 최초 배포라면 fresh exact-object 404 기준선. `BaselineAdoption`이 명시되면 그 원본의 기존 객체/허용 소유를 fresh GET과 대조한다. restore는 별도 승인된 새 RUN/Binding과 **모든 기존 객체가 있는 명시적 기준선**이 필수다.
5. 실제 종료 원본을 기다리는 finish adapter.

`OperationalApplication`은 기존 고정 `AuthorityAdapters`와 새 `authority_factory` 중 정확히 하나를 받는다. factory는 실제 progress를 얻는 시점에 한 번만 호출한다. `SiteConfiguration`의 기존 POSIX 입력 파일·고정 Git 원본·영구 store 점검은 그대로 적용한다. namespace는 `fds-app`이며 `rosa-app`은 overlay다. 명시 Binding의 기존 6객체/2이미지 또는 9객체/3이미지 범위를 넓히지 않는다.

운영 구성은 검토된 Python 코드가 `delivery_runtime_site.main(argv, config=…, plan=…, readers=…, finish_slot=…, receiver=…, handoff_deadline=…)`에 메모리로 전달한다. JSON/환경변수/플러그인/`approved=true`/명령행 token·대상 로더는 없다. 실제 구성을 저장소 예제로 합성하지 않았다.

`status`는 파일·네트워크·자격 수신·claim 없이 `CONFIGURED_NOT_VERIFIED`만 반환한다. 이미 인계받은 receiver가 있으면 읽지 않고 닫는다. 나머지 명령은 Linux 익명 pipe에서 기존 자격을 한 번 받아 로컬 입력 확인 → application → 기존 CLI 순으로 연결한다. preflight도 실제 사용 시에는 원본/API 읽기가 발생하므로 이번 작업에서는 실행하지 않았다. 기본 단독 CLI는 계속 미설정/STOP이다.

## 종료 원본과 복구

finish 원본은 실행 후 생긴다. `FinishAdoptionSlot`은 빈 상태로 만들고, 신뢰된 부모가 **실제 terminal progress와 종료 원본을 확보·검토한 뒤** `EvidenceAdoption`을 한 번만 설치한다. 두 번째 설치·진행 키 변경·불완전 journal·미래 관측 시각은 거부한다. 원본 수집·실제 의미 채택을 자동으로 만들어 주지 않는다.

최종 원본이 없으면 finish source 조회 전에 중단하고 fence를 유지한다. `finalize=False`도 rollout 이후 `REVIEW_REQUIRED`/fence 유지이며 완료나 AC PASS가 아니다. 이미 기록한 ACK·pending·journal은 보존한다. 완료 후 로컬 cleanup 실패가 나더라도 이미 해제된 fence를 RETAINED라고 바꾸지 않고 고정 오류와 실제 상태를 함께 보고한다. 자동 unlock·resume·rollback은 없다. 기존 읽기 전용 recovery 절차로 대조한다.

`AuthorityBundle`은 소유한 adapter/reader를 정리하고, factory 부분 생성 실패·KeyboardInterrupt에서도 미이전 핸들을 정리한다. 공유 원본 reader와 그 GitHub 자격은 부모 소유이며 자동으로 닫거나 권한을 회수하지 않는다. ROSA 자격과 GitHub 자격은 분리한다. close는 로컬 참조 해제이며 실제 토큰 회수·서버 세션 종료·Python 메모리 완전 소거를 보장하지 않는다. traceback locals·HTTP wire·환경 덤프 수집을 활성화하지 않는다.

## 오프라인 시험

저장소 루트에서 일반 작업 PC Python으로 실행한다.

```text
python -m unittest discover -s tools/phase2 -p test_delivery_semantic_authority.py -v
python -m unittest discover -s tools/phase2 -p test_delivery_runtime_site.py -v
python -m unittest discover -s tools/phase2 -p test_*.py -v
```

시험 fixture만 명시적 합성 값이며 실제 승인·자격·대상값으로 저장하거나 채택하지 않는다. socket/DNS는 금지한다. 실제 코드의 정상 6/9객체 세션, 별도 복구 RUN, 실패 차단·원본 철회·finish 미설치 시 fence 유지, 같은 credential/progress, 부분 초기화/중단/완료 후 정리 오류와 기본 STOP을 시험한다. POSIX 파일/pipe/store는 기존 Linux 시험과 신규 factory 전달 시험으로 확인한다. MemoryStore 결과를 디스크 내구성으로 확대하지 않는다.

PR100 위에 쌓은 변경을 독립 검토할 수 있도록 P2 오프라인 CI의 PR 대상에 그 정확한 base branch만 추가했다. permissions는 contents:read, 기존 고정 checkout과 hosted Ubuntu이며 실제 Runner·배포 workflow·자격 설정은 변경하지 않는다.

## 구현 완료와 잔여의 구분

- 이번 구현: 선택적 의미 채택 판정기, 승인/issuer/producer/기준선/finish의 구체 factory, 기존 CLI·pipe·실제 progress 연결과 실패/정리 처리.
- 실제 연결 잔여: 해당 방식의 운영 채택, 실제 발급/RBAC·공급/이미지/clean render·종료 원본과 본인 자격, 승인 대상/창·현장 경로를 구성하는 일. 미래 종료 원본이나 end-page digest를 추정해 채우지 않는다.
- 인수 잔여: 정상 Commit→실제 ROSA 배포, 실제 Test/Scan 실패 배포 차단, Pod imageID·Commit 역추적, Health/R01/R02/R04/R07, 이전 승인 digest 복구 및 원본 증적 편입. STATIC/MOCK/CI/REVIEW/MERGE는 이 Runtime AC를 대신하지 않는다.
