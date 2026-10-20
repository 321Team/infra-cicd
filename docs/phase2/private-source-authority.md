# 비공개 원본 읽기·승인 authority 연결

PR95의 공개 원본 reader와 같은 CA/IP/SNI·제한시간·GET allowlist를 비공개 저장소에도 사용한다. 기본 CLI를 실행 가능하게 바꾸지 않으며 ambient 로그인·환경변수·파일·stdin·인자로 token을 찾지 않는다. 이 문서는 실제값을 채운 실행 카드나 실행 GO가 아니다.

## 구현한 범위

- `GitHubReadCredential`은 ROSA `BearerCredential`과 다른 타입이다. 저장소 pin1개와 **monotonic 로컬 사용 상한**을 코드 설정으로 받는다. token header에 CR/LF 등은 허용하지 않고 객체 repr·오류에 값을 넣지 않는다. token/reference를 놓는 `close()`는 메모리 완전 소거를 보장하지 않는다.
- `GitHubPipeCredentialReceiver`는 **별도 Linux 익명 pipe의 소유 read FD**를 1회 소비한다. 부모는 source/ROSA 각각 별도의 fd를 인계해야 한다. 기존 채널의 동일 UID·pipefs·읽기전용·EOF·16KiB/10초·deadline·scratch cleanup을 재사용한다. GitHub handle은 추가4096자 상한과 저장소/로컬 사용 상한을 적용한다. 기존 ROSA site builder는 여전히 exact `PipeCredentialReceiver`만 받으므로 GitHub receiver를 넣으면 거부한다. 동일 문자열을 타입만 바꿔 쓰는 것이 issuer 분리의 증거는 아니다.
- `PrivateGitHubTLS`는 허용 저장소 경로 경계 밖에는 token을 보내지 않는다. CA/numeric endpoint를 명시하고 `api.github.com` 호스트명을 검증한다. 요청 전체(handshake/send/read)는 `min(request.deadline, local_use_until)`에 제한된다. redirect·401/403·폐기/불명·TLS/JSON 실패에는 공개/익명 fallback·재시도·캐시를 사용하지 않는다.
- `PrivateGitHubOriginalAdapter`는 repository/owner numeric ID·full_name·`private=true`를 확인한다. 기존 댓글 ID/creator/revision/bodyhash와 Git ref 이력/tree/blob 해시 검증을 그대로 재사용한다. `SourceOriginal.execution_authorized=false`, `body_authorship_verified=false`는 유지한다. creator ID는 현재 본문의 작성자·승인권자 증명이 아니다.
- `ReviewedApprovalAdapter`는 기존 `AuthorityAdapters.approval` 위치에 명시적으로 연결하는 callable이다. 같은 Binding·ROSA credential identity·progress·deadline을 검사하고 **fresh original→resolver→fresh original→resolver** 결과를 대조한다. 원본 변경/삭제/403·철회·authority revision/window 교체·다른 RUN/action·deadline/clock/progress 변경·캐시된 원본·임의 JSON은 중단한다. 현재 window를 확인하며 monotonic 경과가 wall 정지로 사라지지 않는다. 각 원본/authority를 읽는 호출은 원자적 transaction이 아니며 직후의 미래 변경까지 보장하지 않는다.

## 의도적으로 자동화하지 않은 신뢰 경계

**이 연결 코드는 실제 운영 authority resolver 자체가 아니다.** 검토된 코드 설정에서 resolver를 반드시 주입해야 한다. resolver는 해당 원본을 실제로 채택한 주체의 권한·철회/교체·정확 RUN/action/작업창·전체 Binding을 자신의 권위 있는 원본으로 검증하고, 그 호출에서 받은 `SourceOriginal` 객체와 정확히 결속한 `AuthorityDecision(original, ApprovalEvidence(...))`를 반환해야 한다. Python 타입·문자열 `ACTIVE`·해시 일치만으로 그 진위가 생기지 않는다. 미설정·불명은 STOP이며 JSON/JWT/댓글 문구/새 서명키의 기본 로더가 없다.

resolver는 `ReadRequest.deadline` 안에 자체 I/O 제한/취소를 구현해야 한다. 연결층의 호출 전후 시간 검사는 무한 대기 callback을 강제 종료하지 못한다. 서로 다른 댓글·authority 저장소의 원자적 snapshot을 보장하지 않는다. 플랫폼의 실제 issuer/audience/서버 만료·최소 RBAC·UID/CA, producer의 build/scan/push·생성 계보, finish의 독점 종료 검증자는 별도 잔여다. PR96의 빈 기준선은 이번 branch에 포함하지 않았다.

**GET-only는 token이 실제 읽기 전용 권한만 가진다는 증거가 아니다.** 공급 주체가 기존 승인 범위의 저장소 최소 읽기 권한·실제 유효기간을 확인한 GitHub 전용 자격을 준비해야 한다. `local_use_until`을 서버의 token expiry나 승인 만료로 사용하지 않는다. 이번 변경에서 token을 발급/갱신하거나 정책·GitHub App·PAT를 생성하지 않았다. 필요 권한은 사용하는 REST endpoint를 [GitHub 공식 권한표](https://docs.github.com/en/rest/authentication/permissions-required-for-fine-grained-personal-access-tokens)와 대조한다. 표의 요구 권한·응답 헤더도 공급 token이 가진 모든 권한의 증명으로 바꾸지 않는다.

## 검토된 구성에서의 연결 순서

1. code-configured repository/comment 또는 blob pin, 별도 source credential pipe, 신뢰 CA/numeric endpoint·로컬 사용 상한을 준비한다. 같은 ROSA 자격 객체를 source 요청으로 보내지 않는다.
2. source용 receiver로 `GitHubReadCredential`을 1회 수신하고 private TLS/reader를 구성한다. 실제 자격 발급/수신은 이 PR 검증에서 수행하지 않았다.
3. 실제 권한·철회 검증 resolver와 같은 Binding/ROSA credential/progress clock을 `ReviewedApprovalAdapter`에 전달한다. 기존 application의 `AuthorityAdapters`에서 approval만 명시적으로 교체하고 platform/producer/baseline/finish의 미구현 경계를 유지한다. 동적 plugin·CLI 승인 flag는 추가하지 않는다.
4. 각 재검증에서 source/authority 두 번의 fresh 확인과 동일 request 결속을 수행한다. 실패 시 해당 요청을 거부하고 token·응답 원문을 공개하지 않는다. 기존 claim/journal/fence는 이 adapter가 삭제/해제하지 않는다.
5. source transport/credential·소유 receiver를 닫는다. 이것이 실제 token 폐기/운영 최소 권한 회수·배포 완료·전체 AC 인수는 아니다.

## 오프라인 검증

모든 token/reply/authority는 `SYNTHETIC` fixture이며 네트워크 socket은 기본 차단하고 fake TLS/socket만 주입한다. 저장소 경계/ROSA 혼용/403/redirect/clock/상한·수신 후 FD 소유/원본 변경·철회·cached decision·미설정 resolver/JSON·전체 Binding·실제 OperationalProviders approval 접점과 공개 reader 회귀를 검사한다. Linux 실제 익명 pipe 시험은 Linux CI에서 수행하며 Windows SKIP을 PASS로 합치지 않는다. source scope·원본 재검증·authority 재검증 guard의 in-memory 제거를 별도로 검출한다. STATIC/MOCK/CI는 실제 GitHub 비공개 읽기·권한 검증·ROSA/배포 Runtime 성공이 아니다.
