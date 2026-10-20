# #46 코드 채택 정책 resolver — 검토용 오프라인 구현

PR95의 원본/authority 연결과 PR96의 빈 기준선 adapter 병합 이후의 후속이다. 실제 운영 설정·GO·자격·대상값은 추가하지 않았다.

`CodeAdoptedGrantResolver`는 검토된 site Python 정책이 명시적으로 채택한 단일 grant의 Binding·원본 pin·권한 주체·채택 근거·기간을 대조한다. 기존 `ReviewedApprovalAdapter`의 resolver 자리에 연결하며 매 호출마다 기존 GitHub blob reader로 채택된 control을 새로 읽는다.

## 구현한 범위

- 원본 reference/revision/repository ID/최초 게시 계정 ID/본문 해시와 모든 Binding 필드의 정확한 일치. prefix·glob·현재값 자동 채택 없음.
- control BlobPin의 고정 branch head·도달성·해시를 기존 bounded TLS reader로 확인하고 반환 원본의 request identity·본문·revision·저장소를 대조한다.
- 같은 credential handle·progress·공유 deadline, 유한 starts/expiry, 시계 역행과 wall 정지 시 monotonic 경과시간을 확인한다. 시작은 포함, 만료는 제외한다.
- 원본/control 변경·삭제·거부·시간초과·다른 회차는 실패로 종료한다. 고정 오류코드만 반환하고 원문·token·임의 예외 문자열을 노출하지 않는다.

## 운영 채택과의 구분

권한의 신뢰 루트는 **검토되어 배포된 site 정책 코드**다. JSON/body의 `ACTIVE`, 작성 계정, PR 승인 badge, 해시 또는 Python 객체 타입이 스스로 실행 권한이 되지 않는다. 이 모듈은 권한을 새로 만들거나 실제 채택 사실을 증명하지 않는다.

현재 프로젝트의 실제 권위 control 원본과 site 채택 계약은 연결하지 않았다. 기존 GO 댓글을 새 양식으로 바꾸거나 새 ledger·서명키·별도 GO를 만드는 절차를 요구하지 않는다. 기존 운영 방식에서 신뢰 계약이 성립하지 않으면 이 **선택 가능한 후보를 활성화하지 않고 기본 STOP**을 유지한다.

철회/대체를 관측하려면 실제 채택된 control의 head가 변경되어야 한다. 다른 댓글에만 철회가 남으면 이 resolver는 발견하지 못한다. 운영 계약의 이 전제를 확인하지 않고 “운영 철회 인증 완료”라고 보고하면 안 된다. 두 번의 조회도 원자적 snapshot이나 마지막 조회 이후 변경 방지를 보장하지 않는다.

`adoption_reference`는 검토 근거의 추적용 문자열이고 권한 증명·원문 파서가 아니다. 외부 데이터에서 정책을 만드는 loader·기본값·샘플 운영 설정은 없다. source의 `execution_authorized=false`, `body_authorship_verified=false`는 유지하며 최초 게시 계정과 현재 본문의 실제 작성자를 구분한다.

플랫폼 issuer/audience·실제 credential 만료/최소 RBAC, producer 생성 계보, finish의 독점 종료 인증과 실제 GitHub/ROSA 현장 검증은 별도 잔여다. 이 코드·MOCK·CI를 #46 AC나 Runtime GO로 확대하지 않는다.

## 오프라인 검증

`python -B -m unittest discover -s tools/phase2 -p test_delivery_adopted_authority.py -v`

합성 fixture·fake clock·mock reader를 사용하고 socket/DNS 호출을 차단한다. 정확한 grant/control 정상 연결, 전체 Binding 차이, 무설정/JSON, 원본 변경, 두 번째 control 변경, 삭제/거부/비밀 오류, 기간 경계·deadline·progress·credential·시계 역행을 확인한다. 합성 시험값은 실제 승인값이 아니다.

참고: [GitHub issue comments API](https://docs.github.com/en/rest/issues/comments), [Git references API](https://docs.github.com/en/rest/git/refs). API 원본·ref 조회와 프로젝트의 grant 권한 판정은 다른 계약이다.
