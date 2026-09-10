# PR #2 추가 확인·CA 결정 변경 — 2026-09-10

조회한 HEAD: `baeda8850cf10b916ab2fe0b64ae45864d75e8c2`. 리뷰 조회 시 PR은 open/unmerged이며 이하영(hayeounglee)의 [기존 댓글 1건](https://github.com/321Team/infra-cicd/pull/2#issuecomment-5614536834), 제출 리뷰 0건, inline 댓글 0건이다. 새 승인·재검토 결과는 확인되지 않았다.

기존 [항목별 반영 기록](pr-2-review-response-20260910.md)과 소스를 대조했다. 인증서 설치·권한·키 일치 검사, 실패 요청 보존/폐기 안내, PR 범위 요약, hosted 단위시험 CI, smoke 실행 조건 표시, 템플릿 `cmp`가 반영돼 있다. raw stderr 반출 대신 고정 오류 요약을 제공하고 Runner 출발 IP 변수를 재사용한다. `.gitattributes`는 PR #1 선행 병합으로 main에 존재한다. 기존 수정 요구는 반영 확인이며 독립 승인·GitHub CI 실행 성공과 구분한다.

## 새 사용자 결정의 영향

사용자: **“기존 승인 CA는 폐기 신규 발급 필요”**. 이에 따라 이번 후속 Commit은 다음을 갱신한다.

- `config/p1-cicd-01.json`: CA 재사용/부재 분기를 신규 키·Root 선택으로 변경. 실제 구 CA 폐기는 RETIREMENT_REQUIRED/NOT_VERIFIED, 새 발급은 NOT_EXECUTED로 유지.
- `harbor-formalization.md`: 새로운 자산 디렉터리에서 새 Root 생성, 동일 Harbor CSR 서명, Consumer 전환·구 Root 신뢰 제거·구 키/백업 폐기 기록. 폐기한 구 CA 신뢰를 다시 추가하는 복구 방식은 제외.
- `baseline-implementation.md`, `parallel-preparation.md`: 신규 CA 결정과 기존 CSR 보존을 동기화.

이 변경은 정책·문서/설정 변경이며 CSR/수집기 소스·인증서 프로파일·Workflow는 동일하다. 기존 baeda 시점의 28건 PASS 이력은 그 Commit에 귀속된다. 이번 변경은 JSON 파싱·CA 변경 필드 확인·문서 Bash 구문·CSR/Leaf/Root 프로파일 및 실행 소스 동일성을 검증한다. 실제 키 생성·서명·Trust 변경·서비스 실행·폐기는 수행하지 않는다. 호스트 장치와 현장 입력 없이 삭제/포맷 명령을 만들지 않는다.

v3.1.17 본문 D19의 신뢰 구조와 후속 구현 통제인 오프라인 CA 보관을 구분한다. Harbor와 동일 VM에 Root 키를 두는 것은 현재 구현안과 충돌한다. 동일 물리 PC의 별도 CA VM은 자원 및 관리자 접근통제를 확인해 검토할 수 있으며 CA 전용 물리 서버가 해당 본문에 필수로 지정된 것은 아니다.

판정: 기존 댓글의 수정 사항은 반영 확인. 신규 CA 정책 Commit은 이하영의 독립 재검토 대상. PR 병합·운영 인수는 미완료이며 `DELIVERY_GATE=NOT_VERIFIED`.
