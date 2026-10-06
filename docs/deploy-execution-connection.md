# Deploy v2 승인 조회와 claim·Adapter 연결

2026-10-06 KST / AI usage: CODEX_ASSISTED.
기준: 병합된 PR55(2d8356eff60b9e4e04b12e40da061f49e7fb9e10)과 독립 리뷰5425003879.
기존 승인된 v1/v2/Adapter/claim 코드는 변경하지 않고 연결 모듈을 추가한다.

## 실제 승인 원문과 코드 리뷰 구분

deploy_approval.Verifier는 v2 승인 bytes의 외부 확정 해시를 검사한 뒤 GitHub
원문 댓글을 새로 조회한다. 검토 승인(APPROVED), 일반적인 "승인" 댓글, 선언된
approved_by만으로 Deploy 실행을 허용하지 않는다.

현재 구현의 책임 티켓은 infra-cicd #11 또는 fds-project-baseline #16의
#issuecomment-<정수> 링크다. 승인 JSON 전체가 해당 댓글의 raw body와 **UTF-8 bytes로 정확히**
같고, API 응답의 comment ID/html_url과 작성자 login=GWONUK/type=User가 일치해야 한다.
이는 기존 P1-CICD Lead 이권욱/GWONUK 역할을 연결한 구현 제약이며 다른 업무의
승인 권한이나 팀 정책을 변경하지 않는다. 대체 Lead를 사용하려면 별도 근거·코드 검토가 필요하다.

분리 승인 JSON은 schema=P1-CICD-deploy-approval-2, template_only=false이고
계획 SHA, Workflow, 실행 기간·복구 범위를 포함한다. 게시할 댓글 ID가 필요하므로
댓글 위치를 먼저 확보한 뒤 최종 승인 원문을 게시/확정하고 해시를 고정하는 방식이 가능하다.
이번 PR은 댓글 생성/수정이나 실제 승인 원문 발행을 수행하지 않는다.
Markdown fence·설명문·줄바꿈 정규화는 하지 않으며 그런 형식은 원문 불일치로 거부한다.

GitHubComments는 공식 GET /repos/{owner}/{repo}/issues/comments/{comment_id}만 읽는다.
기본 CA·hostname TLS 검증, 명시적인 proxy 비사용, redirect 거부, raw+json 응답,
no-cache 요청, 응답1MiB 상한과 JSON 중복 키 거부를 사용한다. 재시도·write 요청은 없다.
메모리 token_provider는 이미 승인된 읽기 자격증명을 제공해야 하며 token을 반환 자료에 넣지 않는다.
새 GitHub 권한/Secret 생성이나 Workflow 권한 확대는 이번 변경에 없다.

소스:
- [GitHub Get an issue comment](https://docs.github.com/en/rest/issues/comments?apiVersion=2022-11-28#get-an-issue-comment)
- [Python urllib.request](https://docs.python.org/3/library/urllib.request.html)

매 GET/PATCH/rollback의 authorize가 댓글을 다시 읽는다. 원문 수정·삭제, 다른 작성자,
403/404·타임아웃·형식 오류는 승인을 재사용하지 않고 중단한다. 원문을 REVOKED로 바꾸거나
삭제하면 해당 원문으로는 더 이상 실행할 수 없다. **별도 새 댓글에 적은 철회 문구나
외부 승인 시스템의 철회는 이 구현이 자동 탐색하지 않는다.** 승인 확정/철회 운영 절차와
실제 권한 인계는 후속 Runtime 연결에서 확정해야 한다.

조회는 각 작업 직전의 관측이다. GitHub 승인 변경과 Kubernetes PATCH를 하나의 원자적
거래로 묶는 것은 아니다. 소켓 timeout은 최대10초 및 현재 잔여시간 이하로 제한하며
조회 종료 후 시간 예산을 재검사한다. urllib의 소켓 timeout 자체가 전체 호출의
실시간 강제 중단을 보장하는 것은 아니므로 Workflow 전체 timeout/운영 감시도 필요하다.

## 연결 API와 claim 소진 경계

deploy_execution.Execution(client, approval_verifier, checkpoint, state_root,
credential_remaining_seconds=..., now=..., clock=..., timeout=..., interval=...)
.run(plan_raw, approval_raw, push_raw, expected_plan_sha256,
expected_approval_sha256, execution)

Kubernetes client와 내구성 checkpoint는 검토된 Runtime 호출자가 제공한다.
실제 TLS Kubernetes client, 토큰 발급·metadata 검증·회수, file checkpoint 구현,
Workflow/운영 CLI는 이 모듈에 없다. 라이브러리 호출을 운영 준비 완료로 보고하지 않는다.

| 단계 | 동작 |
|---|---|
| claim 전 | v2 원본·실제 context 검증 → 승인/실제 token 각각 2×timeout+margin 확인 → 실제 승인 조회 → 조회에 소비한 시간까지 다시 검사 |
| claim | 고정 상태 경로의 전역 mutex + 계획별 지속 claim 확보 |
| 매 작업 authorize | Claim.check() → rollback이면 별도 범위 확인 → 승인 댓글 fresh lookup → Claim.check() 재검사 → 승인/실제 token 현재 수명 확인 |
| checkpoint | core 영수증에 실제 Run/Attempt·계획/승인/Push/claim 해시를 더해 호출자의 durable 저장 콜백에 전달 |
| 종료 | mutex를 해제하되 claim 파일 유지. 자동 claim 삭제/재사용 없음 |

토큰 잔여시간은 **이미 검증된 실제 발급/만료 metadata**에서 새로 계산하는 입력이어야 한다.
TTL3600 요청값이나 JWT payload decode만으로 대체하지 않는다.
이번 연결은 metadata가 이미 제공된 자격증명 주입 경로를 위한 것이다.
PR55 문서의 claim 후 신규 토큰 발급 경로는 아직 연결하지 않았으며, 새 토큰 발급과
claim 직전 실제 수명 검사를 함께 지원하려면 그 순서·잠금·실패 시 회수를 후속으로 검토한다.
metadata 불명/짧은 수명은 claim 전에 멈춰 불필요한 소진을 줄인다.

실제 Deployment 사전조회에서 BASELINE_NOT_READY 등으로 멈춘 경우에는 claim 소진이 맞다.
write/fsync 실패·프로세스 종료·실패 결과도 기존 claim을 자동 해제하지 않는다.
수동 대조 후 새 현재 상태·request_id의 계획과 새 승인으로 처리하며 기존 파일은 보존한다.

승인 예산은 검증 당시의 만료시각과 monotonic deadline 중 작은 잔여값을 사용한다.
벽시계가 뒤로 가도 원래 승인 수명을 늘리지 않는다. credential callback의 실제 만료
검증/시간원은 호출자의 책임이며 CREDENTIAL_METADATA=CALLER_SUPPLIED_NOT_VERIFIED_HERE로 기록한다.

## 결과 및 실패 처리

입력/사전 승인·window 실패는 ROLLOUT=NOT_EXECUTED이고 Kubernetes API 읽기/쓰기·claim을 시작하지 않는다.
claim 확보 실패 뒤 부분 파일 존재 가능성은 deploy_claim의 기존 계약대로 수동 대조한다.
claim 뒤 실행 실패는 소비 파일을 보존한다. 권한·승인·claim·token 유효성 상실은
AUTHORIZATION_DENIED로 추가 API 작업을 막고 자동 rollback도 하지 않는다.
authority_error_code는 토큰 만료·댓글 변경·claim drift 등 **고정 코드만** 보완 기록한다.
주입된 helper 예외의 임의 문자열/비밀값은 코드 필드에도 넣지 않는다.

rollback은 승인 원문의 allow_rollback=true가 필요하고, 매 작업마다 같은 fresh 승인·claim·token
검사를 거친다. PATCH 응답 불명·checkpoint 실패는 기존 core의 수동 대조 경계를 유지한다.
checkpoint는 내구성 저장을 끝내거나 예외를 내야 한다. 예외 시 추가 PATCH를 하지 않으며,
실패 요약 반환 자체는 디스크에 최종 영수증이 저장됐다는 뜻이 아니다.

실행 영수증의 DEPLOY_APPROVAL_REFERENCE=MATCHED_AT_LAST_SUCCESSFUL_LOOKUP는 과거 성공 관측이다.
현재 승인이나 실행 전체 성공을 뜻하지 않는다. SOURCE_APPROVAL=NOT_INDEPENDENTLY_VERIFIED,
DELIVERY_GATE/HEALTH_E2E/POD_IMAGE_ID=NOT_VERIFIED를 유지한다.
Source/Build/Scan·Harbor 원본 인수까지 이번 승인 조회로 상향하지 않는다.

## 시험과 다음 작업

승인 조회 MOCK16건, POSIX 연결 MOCK21건을 기존 source-validation CI에 추가한다.
명시적 TLS/redirect/proxy·원문/작성자/hash/삭제·응답 상한, short window의 claim 비소진,
사전조회 실패의 claim 소진, 변경/삭제된 claim과 승인에 따른 쓰기 중단,
PENDING 뒤 token 만료, 별도 rollback·철회, checkpoint 실패, 벽시계 역행,
예외·invalid hash 비노출을 확인한다. 실제 GitHub 승인/토큰/네트워크/서버를 시험하지 않는다.

단일 Runner·POSIX·하나의 고정된 보호 상태 경로 전제는 유지한다.
Runner를 늘리거나 상태 경로를 Job마다 바꾸면 이 mutex/claim의 중복 방지 전제가 깨진다.
분산 lock을 지원한다고 보고하지 않는다.

다음: 실제 Kubernetes TLS transport·요청 deadline → durable file checkpoint·cleanup →
Workflow와 검증된 credential metadata/읽기 권한 연결 → 운영 실행 조건 확인.
기존 K8S 시험·보호 실패, 최신 Deployment/HPA, 이전 image 재Pull/호환성,
Worker Pull, APP DB TLS/Health/E2E는 별도 실제 검증 대상이다.
이번 변경은 Runner 상태 디렉터리 생성, 토큰 발급, Deploy·Runtime 실행 승인 또는 Phase1 인수가 아니다.
