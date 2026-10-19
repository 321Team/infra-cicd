# #46 TLS sender와 session 연결 — 오프라인 검토본

2026-10-16 KST · 이권욱 / CODEX_ASSISTED. 기준 PR88 병합 `7fc43f1139b7ed8313e387e9652ef7ba1f212ef8`.
`delivery_sender_binding.bind_tls_sender`가 기존 `run_session`의 sender factory 인터페이스에 실제 `delivery_transport.Sender`를 연결한다. 운영 CLI는 계속 BLOCKED이며 자동 선택·실제 접속·token 발급은 없다.

## 연결 범위

신뢰된 연결 코드에서 `sender_factory=bind_tls_sender`를 명시적으로 전달한다. session이 이미 검증한 PlatformEvidence의 Binding·요청·credential 객체 동일성·token fingerprint를 확인하고, 같은 CA 원문·numeric endpoint로 Sender를 구성한다. Sender 자체의 CA 해시/TLS hostname/최소 TLS1.2/허용 요청 범위 검사를 재사용한다.

이 factory는 진위 검증기가 아니다. 사용자가 만든 PlatformEvidence나 문자열 `approved=true`를 인증하지 않으며, credential issuer/audience/수명/RBAC·승인 철회·namespace UID는 기존 trusted provider와 session 검증 책임이다. 공급 receipt·baseline admission·완료 관측도 기존 책임을 유지한다. credential 입력 방식·새 승인 절차·추가 IAM 권한을 정하지 않는다.

생성자는 CA를 로드하지만 socket은 열지 않는다. PATCH6개와 Deployment rollout GET2개는 기존 Sender와 Adapter의 exact scope/절대 실행 예산/요청별 timeout을 따른다. 새 List/watch/Delete/Secret 경로는 없다. 소비 namespace는 `fds-app`, `rosa-app`은 overlay다.

## 종료·실패

session의 `close()`는 이 factory가 반환한 송신 함수를 비활성화하고 closure의 Sender 참조를 해제한다. 여러 번 닫아도 안전하며 닫힌 뒤 송신은 socket 생성 전에 거부한다. socket은 기존 Sender가 각 요청의 finally에서 닫는다. 순차 호출 전용이며 동시 실행 취소나 token 메모리 소거를 보장하지 않는다. credential handle의 수명·폐기는 연결 코드의 책임이다.

PATCH의 4xx·TLS·파싱 실패는 계속 결과 불명으로 처리하고 pending/fence를 보존한다. 성공 응답 뒤 ACK 기록 실패·승인 철회 시 다음 쓰기는 없다. sender 생성 실패도 이미 획득한 claim/journal을 닫되 기록을 삭제하지 않는다. 자동 재시도·token 갱신·fence 제거·rollback Apply는 없다. 종료가 Runtime/Delivery 인수 완료를 뜻하지 않는다.

## 재현과 한계

저장소 루트, Python3.10+ 표준 라이브러리, 일반 사용자로 실행한다.

```text
python -m unittest discover -s tools/phase2 -p test_delivery_sender_binding.py -v
python -m unittest discover -s tools/phase2 -p "test_*.py" -v
```

실제 factory/session/transport/parser를 연결하되 authority·TLS socket/인증서·저장소는 합성 모형이다. DNS·실제 socket 생성을 차단한 환경에서 정상6 PATCH→2 GET, credential/CA/endpoint 결속 거부, close 이후 송신0건, PATCH403 결과 불명, ACK 실패, 승인 철회, 생성 실패 정리를 확인한다. 저장소 영속성은 별도 기존 POSIX 시험에서 다루며 이 MemoryStore 시험을 fsync 증거로 보고하지 않는다. 최초 시험의 ACK 상태 예상값을 기존 저장소 명칭 `WRITE_PENDING`에 맞춰 정정했고 제품 상태기는 변경하지 않았다.

성공 조건은 RC0·실패/오류0이며 SKIP을 별도 집계한다. 네트워크 암호화 실통신·실제 credential/RBAC·운영 RUN 시험은 미실행이다. 독립 코드 리뷰·변경 CI 이후에도 승인 원본 provider, fresh 플랫폼 provider, 생산 원본/허용 baseline, 영구 POSIX root 및 완료 관측을 연결해야 한다. CLI를 개방하거나 실제 배포를 승인한 산출물이 아니다. Phase1 DB/Harbor CA/Runner/공용 OCP/DNS/LB/AC는 변경하지 않는다.
