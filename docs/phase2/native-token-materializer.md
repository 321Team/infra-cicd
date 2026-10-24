# TokenRequest 원본 대조용 materializer 구현과 운영 잔여

`delivery_token_materializer.py`는 기존 `PreparedAuthorityPlan` callback 위치에
연결하는 선택적 구현이다. 실제 배포 SITE는 계속 미설정이며 발급·권한 변경·배포를 실행하지 않는다.

후속 [비공개 원본 전달 reader](native-original-reader.md)는 선택적 v2 frame을 추가한다.
기존 v1과 실제 운영 적용·출처/authority 검증은 구분한다.

## 원본에서 확인한 의미와 채택 범위

2026-10-24 KST 기준 baseline #46 기존 댓글, 저장소 tree와 비공개 공유 원본의
경로·해시를 문서 작성 시점에 읽기 전용으로 대조했다. Runtime 자동 검증 결과는 아니다.
원본 파일과 자격은 이 문서에 게시하지 않는다.

| 역할 | 재사용 근거 | 증명 범위 / 남은 입력 |
|---|---|---|
| approval | #46 기존 Lead 구성 결정과 담당자 회신 | 구성 방향·담당을 확인한다. 코드 승인·과거 수동 창 C GO를 현재 자동 배포의 Binding·기간·철회 권한으로 전환하지 않는다. 실제 실행 grant/control 선택은 잔여다. |
| platform | 기존 SA UID·issuer/audience·CA·RBAC/Route 기록, TokenRequest native parser | 발급 전 metadata와 발급 뒤 응답은 별개다. 현재 수신 token과 같은 실제 응답의 지문·iat·exp만 연결한다. metadata pin을 발급 뒤 응답 hash로 교체하지 않는다. 실제 원본 전달 채널은 잔여다. |
| producer | `fds-msa` source `69e28c49…`, `msa42-deploy-69e28c49.tgz`, 기존 digest·contract/render | source/manifest와 현재 승인 digest 선택에 재사용한다. 이 manifest source SHA를 이미지 build source SHA로 취급하지 않는다. Test/Build/Scan/Push/clean-render 의미와 control pin은 별도 채택해야 한다. |
| baseline | 이미 선택된 `baseline6-20261023T133926`, 기존 replicas 인계 후보 | 실제 UID/owner/spec 기준선을 재사용한다. 검토 후보를 실행 완료 기준선으로 올리지 않는다. 실제 grant 및 필요한 replicas 인계는 잔여다. |
| finish | 기존 종료 관측 생성부·수동 창 C 결과 원본 | 과거 수동 소비/정리 결과와 미래 #46 native job의 종료 원본을 분리한다. 새 job 후속 관측·독점 접근 종료는 실제 실행 뒤 생성하며 현재 만들지 않는다. 실제 finish scope/control 선택은 잔여다. |

`CommentPin`은 repository/작성자 ID·updated_at·body hash, `BlobPin`은 repository/
commit/path/hash와 anchor ref/commit을 고정한다. 이는 내용·위치의 결속이며 실행 권한의 증거가
아니다. control ref의 변경을 철회·교체로 해석하는 규칙도 실제 채택해야 한다.
별도 팀 제출 양식이나 새 control ledger를 필수로 추가하지 않는다.

## 구현한 callback과 대조 범위

1. `NativePlanSelection`에 검토한 approval/issuer/producer/baseline·subject·TokenTarget·CA/IP·
   RBAC/Route 참조를 명시한다. constructor는 shape/Binding/pin hash만 검사하며 원본을 인증하거나 I/O하지 않는다.
2. `NativeTokenMaterializer(binding=..., context=..., selection=..., read_original=..., wall=...)`를
   기존 FDS `assemble_observed_job(..., materialize_plan=...)`에 전달할 수 있다.
   기존 FDS profile·baseline·source/control·현재 API 검사와 finish 절차를 그대로 거친다.
3. 수신 뒤 materializer가 생성 시 고정한 native context와 같은지 확인하고,
   `read_original`에 같은 context/credential/source/roles/deadline을 전달해 비공개 원본을 읽는다.
   기존 TokenRequest parser로 namespace/name/SA UID·issuer/audience·수명·중복 JSON 등을 대조하고,
   원본의 token이 수신 자격과 정확히 같은 경우에만 지문·발급/만료·요청 900초를 facts에 연결한다.
   `wall`은 native 발급·만료의 시각 일관성을, source의 monotonic 시계는 기존 수신 deadline을 검사한다.
   grant·생산자·baseline은 선택한 객체 그대로이며 receipt에서 새로 만들지 않는다.
4. 누락·다른 자격·잘못된 응답·deadline 소진·close·예외는 이번 시도를 소진하고 중단한다.
   읽기 재시도·새 자격 발급·기간 연장·추정값 보완은 없다. 오류는 고정 코드만 공개하며
   원본/reader 예외 연결을 남기지 않는다. Python 참조 해제는 완전 소거·서버 회수가 아니다.

`finish`는 이 selection의 입력이 아니다. 기존 실제 실행의 sender 종료 뒤 관측·인수 경로에서
설치한다. materializer에서 실패하면 application 실행 전 중단하므로 완료 finish를 생성하지 않는다.
이미 이전 단계에서 생긴 발급·공급측 정리는 그 소유자의 기존 정리 절차를 따른다.

## 운영 미연결 부분과 검증 한계

`read_original`은 같은 job에 결속되고 실제 출처가 인증된 원본 bytes를 기존 마감 안에 반환하는
신뢰 코드여야 한다. **기존 credential-only v1 SSH frame은 TokenRequest 원본을 전달하지 않는다.**
테스트의 같은 process receipt hook을 운영 원본 전달 구현으로 보고하지 않는다.
callback을 제공하지 않은 상태의 자동 발견·file/env/global cache fallback은 없다.

원본 parser는 일관성 검사이며 이 위치에서 TLS 201 출처·JWT 서명을 검증하지 않는다.
`issuer_grant.original_pin`은 Binding의 기존 platform metadata 원본을 가리킨다. 발급 뒤 native
TokenRequest 응답과 다른 산출물이므로 두 bytes의 hash가 같아야 한다고 요구하지 않는다.
native 원본의 실제 출처·same-job 결속은 별도 reader 계약이다. 후속 reader 구현과
실제 운영 조립·선택/출처 검증 완료는 구분한다.
원본 bytes가 있다는 이유로 발급 권한·RBAC·Route·배포 GO를 인증한 것으로 판단하지 않는다.
선택한 실제 원본/control 채택과 인증 경로가 준비된 뒤에만 운영 구성을 완성할 수 있다.

시험은 SYNTHETIC bytes와 차단한 socket/DNS로 수행한다. 오프라인 시험·CI·코드 병합은
실제 발급·운영 SSH·배포·Health/Rule·인수 PASS와 별개다. 원복은 코드 revert이며
이 변경으로 생성·변경한 운영 자원은 없다.
