# #46 issuer·producer·finish 원본 연결 검토본

이 변경은 기존 source reader를 `AuthorityAdapters.platform/producer/finish`에 연결하는 선택적 코드다. **실제 발급자·생산자·종료 관측의 의미와 권위는 별도 현장 resolver가 인증해야 하며, 그 계약과 운영 채택은 아직 미연결이다.** 실제값, 자격 발급, Runtime, 실행 GO를 추가하지 않는다. 단독 CLI 기본 STOP은 유지한다.

## 구현한 부분

`delivery_evidence_source.py`의 `ReviewedEvidenceSource`는 명시적 role·원본 pin·reader·resolver·Binding·동일 자격 handle·실제 progress closure를 받는다. 생성자는 I/O가 없고, JSON/환경변수/플러그인/approved 플래그로 정책을 로드하지 않는다.

1. 기존 공개 또는 별도 자격을 쓰는 비공개 GitHub reader로 원본을 읽는다. repository ID, exact 버전·바이트·댓글 생성 계정 또는 commit/blob/명시적 branch anchor 검증을 재사용한다. 현재 본문의 실제 편집자나 승인권을 이 값에서 추정하지 않는다.
2. source → 별도 trusted resolver → source → resolver 순서로 같은 ReadRequest·credential·progress·절대 monotonic deadline을 유지한다. 두 원본은 각각 새 객체여야 하며 두 판정은 동일해야 한다. 원본의 `execution_authorized`·`body_authorship_verified`는 계속 false다.
3. issuer/platform은 원본 receipt **바이트 자체**와 Binding 해시, 동일 자격 handle/fingerprint, CA·숫자 IP, issuer/audience 필드, 실제 발급·만료 시각과 900초 이하 수명을 대조한다. 플랫폼 관측 PR98의 별도 issuer 입력으로도 사용할 수 있는 포트이나 PR98을 이 변경에서 자동 채택하지 않는다.
4. producer는 같은 원본 바이트·공급 receipt 해시·source/contract/render/checker pin을 대조한다. 해시나 기존 `CommittedProducerBridge`만으로 clean 생성·Build/Scan/Push·current/previous 채택을 입증했다고 표시하지 않는다.
5. finish는 로컬 session의 실제 progress가 최종 `ROLLOUT_OBSERVED`·6 ACK·2 rollout·pending 없음·checkpoint 오류 없음인지 먼저 확인한다. 6개 실제 관측 응답은 고정 객체 순서·scope·UID/resourceVersion을 검사한다. 그 뒤 별도 원본의 fresh resolver 판정이 같은 progress key·`EXCLUSIVE_ACCESS_ENDED`여야 한다. 로컬 journal이 독점 종료 원본을 대신하지 않고, `sender.close`만으로 권한 회수나 다른 세션 종료를 주장하지 않는다.

모든 포트는 기존 `OriginalDecision(original, evidence)`의 exact 원본·request를 확인하고 반환한다. Platform/producer pin은 원본 receipt blob이어야 한다. 공개 성공 요약 댓글을 receipt로 변환하지 않는다. finish 원본은 검토된 blob 또는 exact 댓글을 쓸 수 있지만 실제 완료 관측·권위·기존 AC와의 대응은 resolver의 책임이다. 타입/문자열/해시만으로 이 책임이 충족되지 않는다.

## 현장 연결의 잔여와 실패 처리

- 하영의 기존 issuer·플랫폼 입력에서 실제 발급 주체/수신자/실제 수명·최소 RBAC·대상/Route 승인과 원본 의미를 인증하는 resolver가 필요하다. 이 모듈은 토큰을 발급·decode하거나 TokenRequest/TokenReview·RBAC/API를 호출하지 않는다.
- 재환의 기존 공급 원본과 #43 계약에서 clean 생성·Build/Scan/Push·P2 current/previous 채택을 인증하는 resolver가 필요하다. 새 양식·동일 원본 재시험을 요구하지 않는다.
- 권욱의 기존 #46 종료/복구 인계에 실제 후속 관측·독점 접근 종료 원본을 연결해야 한다. 임의 source·미확정 실제값을 채워 실행 가능 상태로 만들지 않는다.
- finish resolver는 원본의 실제 RUN/action·관측 시점·progress key까지 인증해야 한다. 같은 예전 종료 원본을 읽고 현재 key를 복사하는 구현은 허용되지 않는다. 최종 원본이 아직 없다면 그 미래 해시를 미리 합성하지 않는다. 신뢰된 현장 코드가 실제 종료 원본이 확보된 시점에 finish 연결을 구성하거나 완료를 STOP하고 기존 fence를 보존해야 한다. 오프라인 시험의 미리 고정된 합성 원본은 현장 구성 예시가 아니다.
- 각 resolver는 자신의 I/O를 request.deadline 안에 취소·완료해야 한다. 연결부의 전후 시계 검사만으로 무한 대기하는 callback이 취소되지는 않는다. 벽시계 정지에도 monotonic 경과로 남은 수명을 줄이며 시계 역행은 거부한다.
- 원본 변경·삭제·권한 거부·callback 오류·기한/수명 만료·진행 변경·불완전 완료·출처 불일치는 고정 오류로 STOP한다. 자동 재시도·토큰 갱신·stale fallback이 없다. 기존 session에서 실패 시 fence/journal을 보존한다. 완료는 기존 store/verifier/session의 재검증·완료 기록 절차를 통과해야 한다.
- `close()`는 이 연결부의 로컬 참조만 정리한다. 공유 reader·자격의 수명은 소유자가 관리하며 토큰/운영 권한/서버 세션을 회수하지 않는다. Python 메모리 완전 소거도 보장하지 않는다.

## 오프라인 검증

저장소 루트에서 일반 작업 PC Python으로 실행한다.

```text
python -m unittest discover -s tools/phase2 -p test_delivery_evidence_source.py -v
```

시험은 명시적인 합성 원본·authority·자격·CA·clock·API·MemoryStore를 사용하고 socket/DNS를 금지한다. 실제 Verifier/session/provider 연결, 정상 6 PATCH·2 rollout, finish 거부 시 fence 보존, 원본/authority 변경·캐시·다른 자격·receipt/계보 pin·기한·부분 journal·잘못된 응답을 확인한다. MemoryStore 결과를 디스크 내구성이나 ROSA 인수로 표시하지 않는다. POSIX 저장/소스 reader의 기존 검증은 재사용한다.

STATIC/MOCK/CI/REVIEW/MERGE와 RUNTIME·Delivery AC·정본 발행은 별도다. 이 변경이 병합돼도 실제 운영 구성·인증 계약이 생기기 전에는 기본 STOP이다.
