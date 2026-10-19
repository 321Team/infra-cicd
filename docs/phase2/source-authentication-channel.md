# #46 원본 인증 adapter와 자격 전달 채널

이 구현은 **공개 GitHub 원본의 진위·버전 확인**과 **Linux 익명 pipe 자격 전달**을 제공한다. 검토된 현장 코드에서 호출하는 라이브러리이며 기본 CLI를 배포 가능하게 설정하지 않는다. 실제값·GO·자격을 만들거나 Runtime을 실행하지 않는다.

## 원본 확인 범위

`delivery_github_source.GitHubOriginalAdapter`는 `PublicGitHubTLS`만 사용한다. 검토된 구성에서 명시한 GitHub 신뢰 CA·숫자 endpoint를 사용하고 `api.github.com` 호스트명을 인증한다. 시스템 CA·프록시·DNS·환경 자격·리다이렉트·자동 재시도·캐시 성공 사본으로 대체하지 않는다. GitHub에는 익명 GET만 보내므로 ROSA 자격이 전송되지 않는다. 비공개 저장소·첨부 raw·개인 자격을 수집하는 도구가 아니다.

| 입력 | 확인하는 것 | 확인하지 않는 것 |
|---|---|---|
| `CommentPin` | repository/owner numeric ID, 정확한 issue/comment ID·최초 게시 계정 numeric ID·URL·수정 시각·본문 SHA256 | 현재 본문 수정자의 신원·가시성/최소화 상태·배포 승인 권한, 다른 댓글의 철회·대체, exact RUN/action/작업창 허용 여부 |
| `BlobPin` | repository/owner numeric ID, 고정 heads/ref tip과 대상 commit의 조상 관계, full commit→tree→경로→일반 blob, Git blob SHA1·원본 바이트 SHA256 | Git 작성자 문자열의 신원, clean render 생성·Build/Scan/Push 계보·P2 채택 승인 |

Git 객체가 fork network에서 조회되는 것만으로 저장소 이력을 인정하지 않는다. 명시한 `anchor_ref`의 정확한 `anchor_commit`을 전후 재조회하고, compare의 base/merge-base와 상태로 대상 commit의 동일/조상 관계를 확인한다. ref 버전 고정은 새 실행 GO가 아니다. tree/commit 매핑은 GitHub TLS 원본을 신뢰하며 blob 바이트만 Git 해시로 다시 계산한다.

삭제·이동·계정/저장소 교체·수정·축약 tree·symlink/submodule·비정상 JSON·304·403/404/429·TLS 오류·기한 초과는 거부한다. 모든 GET이 같은 `ReadRequest.deadline`을 소비하고 각 네트워크 교환은 최대10초다. 응답은 최대1MiB, blob은256KiB, 경로는16요소까지다. endpoint는 사전에 검토·선택한 숫자 주소가 필요하며 이 라이브러리가 DNS 조회나 승인값 선택을 하지 않는다.

`SourceOriginal`에는 같은 request, 원본 reference/revision·numeric ID·원문과 응답이 남는다. `comment_creator_id`는 댓글 최초 생성 계정일 뿐 현재 본문 수정자가 아니며 `body_authorship_verified=false`를 유지한다. REST에서 제공하지 않는 편집자/가시성 상태를 검증했다고 표시하지 않는다. 원문·응답은 repr에서 숨기고 자동 저장·출력하지 않는다. `execution_authorized`는 항상 false다. 이 결과를 `ApprovalEvidence`/`ProducerEvidence`로 간주하거나 해시·타입을 근거로 GO를 만들어서는 안 된다. GET 시점의 원본 일치이며, 조회 직후 변경이나 별도 철회 댓글을 지속 감시하는 보장은 없다. 공개 원본 조회의 API 제한에 걸리면 실패로 닫히며 자격 추가·자동 재시도로 우회하지 않는다.

## 자격 전달과 연결

`delivery_credential_channel.PipeCredentialReceiver`는 **Linux 전용**이다. 검토된 부모 프로세스가 기존 익명 pipe의 읽기 FD 소유권을 명시적으로 넘긴다. 부모는 자격을 승인된 기존 발급 절차에서 받아 pipe에 기록하고 쓰기 쪽을 닫는다. 표준 FD(0/1/2), 파일 경로·CLI 인자·환경변수·stdin·named FIFO·socket·TTY를 전달 경로로 쓰지 않는다. 이 문서는 새 token 발급·권한 부여를 승인하지 않는다.

수신자는 pipefs 장치·소유 UID·읽기 모드를 확인하고 nonblocking·상속 금지를 설정한다. 호출자가 지정한 절대 monotonic 기한과 최대10초 중 빠른 기한을 적용한다. 1~16384바이트 ASCII bearer 문법과 EOF를 모두 요구하며 공백/개행을 제거해 보정하지 않는다. 기한 후 도착한 데이터/EOF도 성공으로 인정하지 않는다. Linux 익명 pipe는 링크 수가0이라고 가정하지 않는다.

성공·실패 때 FD를 닫고 재사용을 거부한다. 사용하지 않은 수신자도 `with` 또는 `close()`로 닫는다. `build_from_channel(config, receiver=..., deadline=..., adapters=...)`은 기존 site 구성·adapter를 확인한 뒤 자격을1회 수신하여 같은 `BearerCredential` 객체를 기존 `build_application`에 넘긴다. 구성·빌드 실패도 수신자를 닫는다. `status`나 단독 CLI에 수신 경로를 추가하지 않는다.

반환 자격은 issuer/audience/서명·실제 만료·대상·RBAC를 아직 인증하지 않은 메모리 handle이다. 같은 자격을 실제 플랫폼에서 검증하는 adapter는 여전히 필요하다. 임시 버퍼 정리는 수행하지만 Python 문자열의 보안 메모리 소거를 보장하지 않는다. 이 모듈의 예외·traceback 로컬 변수·원문 응답을 crash reporter에 수집하거나 공개하지 않는다. 신뢰된 부모·단일 소비자·FD 소유권 인계를 전제로 하며, 침해된 같은 UID 프로세스나 root에 대한 격리 장치가 아니다.

## 정상·실패 판정과 후속

- 오프라인 정상: 합성 원본 응답/pipe에서 exact bytes·같은 request/credential 객체가 연결되고, `execution_authorized=false`가 유지된다. 실제 승인·Runtime PASS가 아니다.
- 실패: 고정 오류 코드만 반환하고 FD/socket을 닫는다. 캐시/부분 token으로 계속하지 않고 자동 재발급·재시도도 하지 않는다. 원본 및 기존 journal/fence·별도 검증 권한을 삭제/회수하는 작업이 아니다.
- 원본 게시·작성자 권한과 철회/대체 해석, 플랫폼 token/대상/최소 RBAC 인증, 공급 생성 계보, baseline 채택과 finish 판정의 **상위 authority adapter 결속은 후속 구현·검토**다. 이번 두 경계를 다섯 authority adapter 전체 완료라고 표시하지 않는다.
- 실제 구성 CA/endpoint·원본 pin·자격·생산 render/계보·작업창은 입력하지 않았다. 실제값 합성, token 발급, ROSA/AWS/Runner 접속, Deploy/Runtime GO, #46 AC 완료는 없다.
- Windows 로컬 시험은 원본 파서/주입 socket·채널 입력/정리·연결 검사를 수행한다. pipefs/readv/select·자식 FD 인계와 POSIX 경로 권한은 Linux hosted CI의 합성 시험으로 검증한다. 지원하지 않는 운영체제를 통과한 것으로 표시하지 않는다.

설계 근거: [GitHub issue comment API](https://docs.github.com/en/rest/issues/comments#get-an-issue-comment), [Git ref API](https://docs.github.com/en/rest/git/refs#get-a-reference), [compare API](https://docs.github.com/en/rest/commits/commits#compare-two-commits), [Git blob API](https://docs.github.com/en/rest/git/blobs#get-a-blob), [Python selectors](https://docs.python.org/3/library/selectors.html), [Linux pipe 구현](https://github.com/torvalds/linux/blob/master/fs/pipe.c). 실제 단계는 최신 CI·리뷰와 별도로 기록한다.
