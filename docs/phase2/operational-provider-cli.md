# #46 provider 조합·CLI 검토본

PR91은 HEAD `2c48dc1` 독립 재리뷰 뒤 merge `2610c66`으로 병합됐다. 후속 [S1 진단 값 제거](diagnostic-privacy.md)는 별도 변경/리뷰 범위다. 현재 설명은 이 후속의 schema3 snapshot을 반영하며, 실제 site/Runtime 조건을 자동 충족시키지 않는다.

2026-10-19 KST · 이권욱 / CODEX_ASSISTED. PR89 `dd06c005`의 TLS sender/진단을 재사용한 후속 구현이다. PR90 `adc9cdc3`의 원문 수집은 독립 변경이며 이 구현의 코드 의존성이 아니다. [설계 Freeze](https://github.com/321Team/fds-project-baseline/issues/34#issuecomment-6259117130)를 소비하되 실제 실행 GO로 쓰지 않는다.

## 실제 추가한 연결

- `delivery_provider.py`: 신뢰된 통합 코드의 AuthorityAdapters → 실제 ObjectReader/ObjectReconciler/ObjectProvider → 기존 Verifier/session → 동일 credential의 TLS sender를 연결한다. baseline은 별도의 인증·소유/허용범위 확인을 마친 AdmittedBaseline으로 딱 한 번 받고 자기 쓰기 뒤 다시 채택하지 않는다.
- `delivery_session.py`: 선택적 provider_factory에 **실제 journal/관측 응답 progress closure**를 넘긴다. 기존 provider 직접 주입 API는 유지한다. factory 실패는 claim·sender 전 STOP이다.
- `delivery_cli.py`: status/preflight/deploy/restore를 code-configured OperationalApplication에 전달한다. 단독 CLI의 기본값은 여전히 BLOCKED이며 **JSON·token·파일경로·환경변수·plugin·approved 플래그로 구성을 로드하는 경로는 없다.** 신뢰된 검토 대상 Python 통합 코드만 메모리 application을 전달할 수 있다.
- `delivery_diagnostics.py`: 이미 분류된 200/404 object에서 [고정 프로파일](diagnostic-privacy.md)로 값을 제거한 snapshot만 binding/RUN/action/progress/관측시각/고정 오류코드·원문 SHA256에 결속해 private diagnostic으로 남긴다. 403 body·깨진 JSON·TLS 오류 원문은 넣지 않는다. 승인·ObjectEvidence·ACK·완료 marker로 변환하지 않는다.

## 아직 구현·실측하지 않은 신뢰 경계

이 코드는 **승인·플랫폼·공급·baseline admission·완료의 원본 인증 adapter 자체가 아니다.** 이 다섯 adapter는 exact request/Binding·원본/권한/철회·credential issuer/audience/실제 수명·namespace/cluster UID/CA/RBAC·clean source/render 생성·객체 소유 허용·독점 종료를 실제로 검증해야 한다. Python 타입/provenance 문자열/해시만으로 진위를 인정하지 않는다. `delivery_intake`의 선언 JSON이나 PR 승인·설계 Freeze를 이 객체로 변환하지 않는다.

후속 [원본 확인·자격 채널](source-authentication-channel.md)은 공개 GitHub 원본의 TLS·저장소·작성자·버전 확인과 Linux 익명 pipe 수신을 구현한다. 상위 authority의 승인/철회·플랫폼/공급/객체 채택/완료 판정이나 현장 구성 완료로 확대하지 않는다.

각 authority adapter는 request의 절대 monotonic deadline 안에 반환하도록 자신의 I/O 취소·제한시간을 구현해야 한다. 조합층은 호출 전후에 검사하나 무한 대기하는 callback을 강제 종료하지 못한다. ObjectReader의 실제6객체 GET은 기존 bounded TLS 구현을 사용한다. 동기 callback의 post-call 시간 검사를 무응답 차단 성공으로 보고하지 않는다.

후속 [현장 입력 연결](site-connection.md)은 검토된 Python 구성에서 명시적 로컬 파일/Git/store를 기존 application에 연결하는 선택적 site entry를 제공한다. **실제 플랫폼/credential 전달·지원 인증·영구 POSIX root·실행 입력과 원본 인증 adapter는 여전히 별도 조건**이다. 기본 CLI를 배포 가능하게 바꾸거나 자격증명을 발급하지 않았다. 실제 입력은 하영 #38의 API443/CA/identity/SA 권한/UID/유효기간, 재환 #43의 승인 Registry/current/previous·clean render·공급 원본, 해당 Lead의 exact RUN/action/창에 연결한다. 기존 CA/D7/검증 자료를 다시 만들게 하지 않는다.

## 생명주기와 실패·복구

후속 [issuer·producer·finish 원본 연결](evidence-source-ports.md)은 같은 reader의 새 원본과 별도 현장 resolver 판정을 세 포트에 결속한다. 원본 receipt 바이트/계보 pin, 실제 issuer 수명, 최종 progress·독점 종료 판정을 대조하지만 실제 resolver의 의미·권위 인증 계약은 미연결이다. 기본 CLI STOP·실제 Runtime 미실행은 유지한다.

1. 실제 승인된 Binding/render/contract/source를 신뢰된 통합 코드가 RunInputs로 제공한다. validator는 고정 source의 CommittedProducerBridge다. 입력 검증을 통과해야 provider가 만들어진다.
2. preflight는 fresh 승인/플랫폼/공급/admitted baseline/객체만 읽는다. claim·쓰기 sender는 만들지 않는다. PREFLIGHT_OBSERVED는 검사 완료 상태이며 실행 승인이나 runtime 인수가 아니다. API 읽기 자체도 실제 운영에서는 승인 범위를 따라야 한다.
3. deploy/restore는 기존 session의 fresh 검증→영속 claim/journal→동일 credential TLS sender→최대6 PATCH/rollout 순서를 따른다. API origin은 입력 그대로(현재 승인443), `fds-app`만 사용하고 ambient kubeconfig/P1 API fallback이 없다.
4. platform의 credential fingerprint·CA·endpoint·issuer/audience·수명·receipt/provenance가 바뀌면 reader를 자동 교체하지 않고 STOP한다. 매 호출 실제 progress와 request deadline을 비교한다.
5. 실패 진단은 **같은** store_root의 `<binding-key>.<phase>.diagnostic.json`에 기록한다. phase는 코드가 정하는 `preflight`/`session` 두 값뿐이며, RUN·단계마다 create-once다. 사전 조회 실패와 이후 배포/restore 실패는 각각 보존하고, 같은 단계의 기존 파일은 덮어쓰지 않는다. schema `P2-CICD-private-diagnostic-4`에 phase·progress key·source/snapshot hash와 원문이 아님을 기록한다. 지원 밖 본문은 `REJECTED`와 key/status/source SHA만 남기며, 하나라도 있으면 `redaction_complete=false`다. `RECORDED`는 최소 기록 저장이며 본문 검증/완전 확보·실행 성공이 아니다. 양의 int64 root Deployment revision만 annotation 예외로 남기고 모든 condition message는 제거한다([프로파일](diagnostic-privacy.md)). 종전 v1/v2/v3 파일은 이동·삭제하지 않는다. root0700/파일0600·owner/no-follow/O_EXCL/fsync·readback 검산은 기존 store 경계를 재사용한다. 잘못된 envelope·partial file/기존 파일/저장 실패는 공개 결과 `diagnostics=FAILED`; 원문/예외/경로를 출력하지 않고 claim·fence를 해제하지 않는다. preflight의 객체 오류는 Verifier의 예외 마스킹 뒤에도 정확한 SAFE_CODES 값만 결과에 남기고, 알 수 없는 문자열은 고정 fallback으로 처리한다.
6. 한 application은 한 번만 preflight 또는 deploy/restore에 사용한다. 상태 조회는 I/O가 없고, 두 번째 실행·action 불일치는 쓰기 전에 거부한다. 프로세스가 바뀌어도 같은 root의 consumed RUN/target active가 재실행을 막는다. 다른 root·자동 refresh/retry/replay·force apply·fence 삭제는 없다.
7. finalize는 완료 요청일 뿐이다. sender.close와 별도 fresh finish 증거/재검증을 거친 기존 session만 완료 marker를 기록한다. ROLLOUT_OBSERVED/SESSION_COMPLETED도 업무 Health/Smoke/Pod imageID/Delivery AC PASS가 아니다.

CLI JSON의 execution_authorized=false는 출력 보고서를 승인 문서로 쓸 수 없다는 뜻이다. 신뢰된 application의 동작은 실제 authority adapter가 검증해야만 가능하다. `preflight`도 application을 소비하므로 실행 전 검사만 원하면 별도 검토된 호출을 하고, deploy는 내부 fresh preflight를 직접 사용한다. token 자동 재발급을 요구하는 의미가 아니다. credential 참조를 종료 때 버리지만 메모리 소거를 보장하지 않으며 별도 운영 최소권한의 회수 작업을 수행하지 않는다.

## 검증과 실행 위치

저장소 루트, 일반 작업 PC Python3.10+에서 다음은 **실제 네트워크 없이** 실행한다.

```text
python -m unittest discover -s tools/phase2 -p test_delivery_provider.py -v
python -m unittest discover -s tools/phase2 -p "test_*.py" -v
python tools/phase2/delivery_cli.py status
python tools/phase2/delivery_cli.py preflight
python tools/phase2/delivery_cli.py deploy
```

단독 CLI는 예상 rc2/BLOCKED/sender0/claim없음이다. 시험은 합성 authority·TLS context·API read·sender·MemoryStore를 쓰되 실제 Verifier/ObjectProvider/Reconciler/session/CLI 연결을 실행한다. 정상6 PATCH·두 rollout, 철회/개정·기한·다른 주체/endpoint·비인증 입력·baseline 재채택 방지·partial GET/ACK·진단 실패·fence 보존/재실행 차단·별도 restore·완료 거부를 검증한다. POSIX diagnostic3건은 Windows에서 SKIP이며 Linux CI가 실제 private file 생성·중복/링크/unsafe root 거부를 실행한다. 합성 store 성공을 디스크 내구성/실제 ROSA 성공으로 표시하지 않는다.

PR89 비차단5557787590의 쓰기401/403/429 → TRANSPORT_FAILED/요청1회 시험도 추가했다. GET의 세분 진단을 PATCH의 확정 비변경 판정으로 쓰지 못하게 한다. 현재 테스트 수정은 새 후속 검토 범위이고 PR89 승인 자체를 변경하거나 전체 기존 시험을 팀원에게 다시 요구하지 않는다.

새 코드/CI/독립 검토 뒤에도 실제 adapter·대상/작업창·Runtime 검증은 별도다. 이번 구현 중 AWS/ROSA/DB/Harbor/Runner/OCP/IAM/DNS/LB/token 발급·Phase1 변경·Deploy·Runtime GO는 0이다. 보호할 기존 P1 경로·운영 권한·CA/보안 통제·AC는 그대로다.

## 10/19 독립 리뷰 후속

재환 리뷰5566845066의 preflight→같은 RUN session 진단 충돌을 단계별 immutable 파일로 수정했다. 실제 Progress가 이미 pending/reply를 담은 첫 객체 조회는 baseline adapter 호출 전에 거부되고, CA/만료시각 pin만 바뀌어도 provider 자체가 거부하는 직접 시험을 추가했다. baseline/플랫폼 pin guard를 각각 제거하면 새 시험이 실패하는지 확인한다. 기존 전체 시험은 이 변경의 session/diagnostic 회귀 범위만 확인하며 실제 authority 구현으로 표시하지 않는다.

하영 리뷰5566763852 **S1은 실제 site/Runtime 전 조건으로 남아 있다.** PR91의 base64 원문 사본 위험을 후속 [고정 프로파일 값 제거](diagnostic-privacy.md)로 보완한다. base64 자체는 마스킹이 아니며 값 제거 뒤의 snapshot과 원문 SHA를 구분한다. 기존 fe9e8277 렌더의 비밀 부재(STATIC)를 live baseline/이후 객체의 비밀 부재로 확대하지 않는다. 이 후속은 독립 리뷰를 거쳐야 하며, 프로파일 밖 임의 비밀을 탐지한다는 보장이나 공개 GitHub 반출 허가가 아니다. 실제 연결 전 입력/객체·권한/원본 인증과 적용 코드의 일치를 확인한다.

진단 운영사본 보존·삭제의 담당은 #46 이권욱이다. 운영안 권고는 반출·재해시·내용 검토·인수 완료 후7일 내 해당 사본 삭제이며, 실제 기산시각·예정일은 Runtime 카드에서 정한다. 미해결 사고/fence 복구 필요 시 삭제를 HOLD하고 이유·다음 검토 시각을 기록한다. 이는 중앙 원본·승인 digest·journal/ACK/active/state의 삭제나 팀 공용 증적 보존정책 변경이 아니다. S1 충족·실제 삭제 완료로 표시하지 않는다.
## 동일 자격의 플랫폼 관측 후속

[플랫폼 관측·발급 인증 연결](platform-readback.md)을 오프라인 adapter로 추가했다. 실제 issuer 인증자는 미구현이며 관측 결과만으로 `PlatformEvidence`를 만들지 않는다. 기본 CLI STOP과 실제값 미설정은 유지한다.
