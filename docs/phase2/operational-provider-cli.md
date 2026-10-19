# #46 provider 조합·CLI 검토본

2026-10-19 KST · 이권욱 / CODEX_ASSISTED. PR89 `dd06c005`의 TLS sender/진단을 재사용한 후속 구현이다. PR90 `adc9cdc3`의 원문 수집은 독립 변경이며 이 구현의 코드 의존성이 아니다. [설계 Freeze](https://github.com/321Team/fds-project-baseline/issues/34#issuecomment-6259117130)를 소비하되 실제 실행 GO로 쓰지 않는다.

## 실제 추가한 연결

- `delivery_provider.py`: 신뢰된 통합 코드의 AuthorityAdapters → 실제 ObjectReader/ObjectReconciler/ObjectProvider → 기존 Verifier/session → 동일 credential의 TLS sender를 연결한다. baseline은 별도의 인증·소유/허용범위 확인을 마친 AdmittedBaseline으로 딱 한 번 받고 자기 쓰기 뒤 다시 채택하지 않는다.
- `delivery_session.py`: 선택적 provider_factory에 **실제 journal/관측 응답 progress closure**를 넘긴다. 기존 provider 직접 주입 API는 유지한다. factory 실패는 claim·sender 전 STOP이다.
- `delivery_cli.py`: status/preflight/deploy/restore를 code-configured OperationalApplication에 전달한다. 단독 CLI의 기본값은 여전히 BLOCKED이며 **JSON·token·파일경로·환경변수·plugin·approved 플래그로 구성을 로드하는 경로는 없다.** 신뢰된 검토 대상 Python 통합 코드만 메모리 application을 전달할 수 있다.
- `delivery_diagnostics.py`: 이미 분류된 200/404 object 조회 원문만 binding/RUN/action/progress/관측시각/고정 오류코드에 결속해 private diagnostic으로 남긴다. 403 body·깨진 JSON·TLS 오류 원문은 넣지 않는다. 승인·ObjectEvidence·ACK·완료 marker로 변환하지 않는다.

## 아직 구현·실측하지 않은 신뢰 경계

이 코드는 **승인·플랫폼·공급·baseline admission·완료의 원본 인증 adapter 자체가 아니다.** 이 다섯 adapter는 exact request/Binding·원본/권한/철회·credential issuer/audience/실제 수명·namespace/cluster UID/CA/RBAC·clean source/render 생성·객체 소유 허용·독점 종료를 실제로 검증해야 한다. Python 타입/provenance 문자열/해시만으로 진위를 인정하지 않는다. `delivery_intake`의 선언 JSON이나 PR 승인·설계 Freeze를 이 객체로 변환하지 않는다.

각 authority adapter는 request의 절대 monotonic deadline 안에 반환하도록 자신의 I/O 취소·제한시간을 구현해야 한다. 조합층은 호출 전후에 검사하나 무한 대기하는 callback을 강제 종료하지 못한다. ObjectReader의 실제6객체 GET은 기존 bounded TLS 구현을 사용한다. 동기 callback의 post-call 시간 검사를 무응답 차단 성공으로 보고하지 않는다.

실제 플랫폼/credential 전달·지원 인증·영구 POSIX root·실행 입력이 미확정이므로 **site entry point는 제공하지 않는다.** 기본 CLI를 배포 가능하게 바꾸거나 자격증명을 발급하지 않았다. 실제 입력은 하영 #38의 API443/CA/identity/SA 권한/UID/유효기간, 재환 #43의 승인 Registry/current/previous·clean render·공급 원본, 해당 Lead의 exact RUN/action/창에 연결한다. 기존 CA/D7/검증 자료를 다시 만들게 하지 않는다.

## 생명주기와 실패·복구

1. 실제 승인된 Binding/render/contract/source를 신뢰된 통합 코드가 RunInputs로 제공한다. validator는 고정 source의 CommittedProducerBridge다. 입력 검증을 통과해야 provider가 만들어진다.
2. preflight는 fresh 승인/플랫폼/공급/admitted baseline/객체만 읽는다. claim·쓰기 sender는 만들지 않는다. PREFLIGHT_OBSERVED는 검사 완료 상태이며 실행 승인이나 runtime 인수가 아니다. API 읽기 자체도 실제 운영에서는 승인 범위를 따라야 한다.
3. deploy/restore는 기존 session의 fresh 검증→영속 claim/journal→동일 credential TLS sender→최대6 PATCH/rollout 순서를 따른다. API origin은 입력 그대로(현재 승인443), `fds-app`만 사용하고 ambient kubeconfig/P1 API fallback이 없다.
4. platform의 credential fingerprint·CA·endpoint·issuer/audience·수명·receipt/provenance가 바뀌면 reader를 자동 교체하지 않고 STOP한다. 매 호출 실제 progress와 request deadline을 비교한다.
5. 실패 진단은 **같은** store_root의 `<binding-key>.diagnostic.json` 한 개에만 create-once로 기록한다. root0700/파일0600·owner/no-follow/O_EXCL/fsync·readback 검산은 기존 store 경계를 재사용한다. partial file/기존 파일/저장 실패는 공개 결과 `diagnostics=FAILED`; 원문/예외/경로를 출력하지 않고 claim·fence를 해제하지 않는다.
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
