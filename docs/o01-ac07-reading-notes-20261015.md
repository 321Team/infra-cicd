# O01 AC07 고정 증적 판독 보완

2026-10-15 KST · CODEX_ASSISTED · 이재환의 O01 1차 판독에서 제기한 문서 결함 후보 D1~D5의 대조와 정정 안내.

## 적용 대상과 원본 보존

- O01 입력: [evidence `43e26384ae422f2017f1e96e6c1bb5fe46d087d4`](https://github.com/321Team/fds-project-evidence/tree/43e26384ae422f2017f1e96e6c1bb5fe46d087d4/evidence/P1-CICD-01/ac07-native-runtime-20261014).
- 대상: `evidence/P1-CICD-01/ac07-native-runtime-20261014`. 아래 경로는 이 폴더 기준이다.
- Manifest SHA256: `8ef7fa2bbadae05a526cd9890dd69d16915f227c7d549d883513996a0a22bfb2`.
- 봉인 Runbook `references/deploy-native-auth-probe.md` SHA256: `f7bd2e0936ec983cd5f56dccea9606ba5f1c84380324a79c7fde277b4bfc3085`.
- 수정 검토 기준은 infra-cicd `c373f1d7a8e6e84548180839098c34e9a1262eef`의 문서다. 이는 AC07 실측 소스 SHA와 다르다.

봉인 README·Runbook·리뷰 사본·verifier·Manifest는 수정하지 않는다. 이 문서를 별도 판독 안내로 사용한다. 해시 검증을 통과시키려고 원본 또는 Manifest를 재작성하지 않는다.

## 결함 후보별 대조와 처분

| 항목 | 확인한 사실 | 처분과 재검증 |
|---|---|---|
| D1 SSAR 건수 | 봉인 Runbook L62의 실행 순서 5번은 13건, 같은 문서의 요청 행렬과 실제 결과는 25건(allow 6 / deny 19)이다. 현행 원 문서에도 남은 불일치다. | 원 저장소 Runbook의 실행 순서를 25건(6/19)으로 정정한다. 봉인 사본을 읽을 때에도 25건을 적용하고 `raw/native-execute.stdout`의 permissions와 대조한다. |
| D2 소스 기준 | 머리말 PR61은 초기 구현 이력이다. AC07 실측은 PR82 merge `9fb2e2dcac8682a89c4b7c267c833ae8d9aaeb18`이며, 검토 HEAD는 `db8230d7580820c55ab388680a1e87fc3e45db0a`다. | 원 문서 머리말에서 초기 이력과 AC07 실측 소스를 구분한다. `raw/SOURCE_BINDING.json` 및 PR82 리뷰의 commit_id와 대조한다. |
| D3 상대 링크 | `[governance.md](governance.md)`는 infra-cicd의 `docs/`에서는 유효하다. evidence의 `references/`에는 대상 파일이 없어 복사된 Runbook에서 깨진다. | 아래의 실측 소스 고정 governance 링크를 사용한다. 원 저장소 전체의 링크 결함으로 확대하지 않는다. |
| D4 README 상태 | README의 증적 검토·AC07 인수 대기와 verifier의 REVIEW_PENDING은 봉인 당시 상태다. | 봉인 원본 변경 대상이 아니다. 아래 승인·병합·인수 기록을 함께 읽는다. 기존 O01 세션 카드가 이미 이 해석을 제공한다. |
| D5 리뷰 경로 | README의 `raw/reviews82.json`과 세션 카드·verifier의 `references/pr82-reviews.json`은 둘 다 존재하며 바이트가 같다. | 내용 충돌은 없다. 판독 시 아래 두 경로의 동일성을 참고한다. 삭제·이동·재봉인은 하지 않는다. |

D1~D3는 문서 판독을 보완할 항목이며, 실제 AC07 출력의 실패를 의미하지 않는다. D4는 기존 안내로 보완된 이력 해석, D5는 동일 자료의 경로 설명으로 처리한다. 이 대조만으로 O01 전체의 Critical 문서 결함 0 또는 DOC Freeze를 판정하지 않는다.

## 복사본에서 사용할 고정 링크

- [AC07 실행 소스의 governance.md](https://github.com/321Team/infra-cicd/blob/9fb2e2dcac8682a89c4b7c267c833ae8d9aaeb18/docs/governance.md): 봉인 Runbook의 깨진 상대 링크 대신 사용한다.
- [AC07 실행 소스의 원 Runbook](https://github.com/321Team/infra-cicd/blob/9fb2e2dcac8682a89c4b7c267c833ae8d9aaeb18/docs/deploy-native-auth-probe.md): 당시 원본이며 위 D1·D2가 남아 있다.
- [실행 소스 결속](https://github.com/321Team/fds-project-evidence/blob/43e26384ae422f2017f1e96e6c1bb5fe46d087d4/evidence/P1-CICD-01/ac07-native-runtime-20261014/raw/SOURCE_BINDING.json).
- [raw/reviews82.json](https://github.com/321Team/fds-project-evidence/blob/43e26384ae422f2017f1e96e6c1bb5fe46d087d4/evidence/P1-CICD-01/ac07-native-runtime-20261014/raw/reviews82.json) / [references/pr82-reviews.json](https://github.com/321Team/fds-project-evidence/blob/43e26384ae422f2017f1e96e6c1bb5fe46d087d4/evidence/P1-CICD-01/ac07-native-runtime-20261014/references/pr82-reviews.json): 공통 SHA256 `4b4358b7c9d5c952d8ae5ca831d1742e2816911a54f5e8a12eba7d2e4f0fecc1`.

## 봉인 후 승인·인수 이력과 판독 범위

[증적 PR231의 이하영 승인](https://github.com/321Team/fds-project-evidence/pull/231#pullrequestreview-5518576437) 이후 merge `72375108ec743455ae49c68b1398d211dcc1b669`로 증적이 편입됐고, [P1-CICD-01 AC07 인수 결정](https://github.com/321Team/fds-project-baseline/issues/16#issuecomment-6171164664)이 별도로 기록됐다. [O01 1차 세션 카드](https://github.com/321Team/fds-project-baseline/blob/e1eb5b99389bccd78b66a9951516993efe9badf7/governance/handoffs/grafana-o01-ac07-start-20261015/O01_AC07_SESSION.md)는 이 후속 기록과 원본 보존 방법을 함께 안내한다.

이재환의 1차는 기존 원본 탐색·오프라인 판정기 실행·Runbook 판독 범위다. Claude의 명령 안내·필드 출력·결함 정리 보조와 사람의 실행·판단을 구분한 수신 결과는 [DOC #24](https://github.com/321Team/fds-project-baseline/issues/24)에 연결한다. 오프라인 PASS는 새 서버 실행·토큰 발급·AC08 또는 AC09 시험·O01 전체 인수의 증거가 아니다. `AC08=NOT_EXECUTED` 등 고정 출력은 해당 AC07 회차 당시 값으로 해석한다.

## 재검증과 되돌리기

수정된 Runbook의 25건 설명·실측 소스·고정 링크를 위 원본과 대조한다. 문서 정정으로 운영 명령·코드·권한·발급 횟수·승인 조건은 변경하지 않는다. 불일치가 나오면 판독 결과와 정확한 경로를 DOC #24에 남기고 새 Runtime으로 보충하지 않는다. 문서 정정을 되돌려야 하면 이 문서와 Runbook 변경만 별도 PR로 revert하며, 봉인 증적은 그대로 보존한다.
