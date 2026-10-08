# P1-CICD-01 최신 단계와 잔여 입력 — 2026-10-08/09 KST

CODEX_ASSISTED · 이권욱. 조회한 main `de59c91a5c45966b50e173d370f13828e0a13564`와 기존 인계 자료를 기준으로 상태를 정합화한다. 새 실행 record를 발행하거나 기존 승인·credential을 재사용하는 문서가 아니다.

## 완료된 것

| 산출물 | 실제 단계 | 범위 |
|---|---|---|
| [evidence #147](https://github.com/321Team/fds-project-evidence/pull/147) | 증적 REVIEW/MERGE 완료(10/8 11:39 KST) | Run37642123031/attempt1, fds-msa `adc90410f99cb7af5b40bc8ff92c45272e9f8bee`, workflow `4e9324cd8acf0ba67b35f950b4c952db8064540e`의 Test/Build/Scan |
| [PR65](https://github.com/321Team/infra-cicd/pull/65) | 코드·인계 REVIEW/MERGE 완료 | 기존 [Push 초안](../handoff/push-review-37642123031/README.md)과 receipt 연결. 최종 실행 입력 아님 |
| [PR66](https://github.com/321Team/infra-cicd/pull/66) | 코드 REVIEW/MERGE 완료 | Deploy Session |
| [PR67](https://github.com/321Team/infra-cicd/pull/67) | 코드 REVIEW/MERGE 완료 | Deploy Workflow/broker 및 CI·MOCK. 실제 TokenRequest·Deploy 아님 |

기존 Test/Build/Scan 성공을 새 Source의 성공으로 옮기지 않는다. `fds-msa` [PR23](https://github.com/321Team/fds-msa/pull/23) 등 후속 변경이 있으므로, 최종 납품 대상이 기존 `adc90410`인지 새 수정본인지 APP Lead와 고정해야 한다. 새 Source를 선택하면 그 SHA의 인계·Test/Build/Scan을 새로 연결하고 옛 digest를 붙이지 않는다. 기존 Source를 유지하는 경우 같은 입력의 성공 회차를 이유 없이 반복하지 않는다.

## 남은 입력과 책임

10/9 후속 후보: [fds-msa#27](https://github.com/321Team/fds-msa/pull/27) `aeb9995d626ffdc24454477038eb82d5d9e4744e`의 GitHub hosted Build/Scan과 [증적#176](https://github.com/321Team/fds-project-evidence/pull/176)을 [기존 metrics 시험 인계 문서](app-metrics-handoff.md#현재-후보와-승인-경계--2026-10-09)에 연결했다. 새 API logging1건을 포함한 전체51건 매핑은 준비안이며 APP Source/Unit 예외 재확인·새 공급물·consumer config 리뷰가 필요하다. 현재 활성 config는 adc90410을 유지하고 후보 CI 결과를 현장 Test/Build/Scan으로 대체하지 않는다.

| 항목 | 생산/결정 | 소비/실행 | 현재 상태·근거 |
|---|---|---|---|
| 사용할 CI Robot의 ID/세대·권한·만료·disabled·사용 가능성 | HBR 이하영 | CICD 이권욱 | [기존 인계 요청](https://github.com/321Team/fds-project-baseline/issues/13#issuecomment-6041140607). #165 목록/만료 화면만으로 현재 credential 사용과 권한 확인을 대신하지 않음 |
| 안전한 credential 전달·회수·실패 시 인수 | HBR/SEC | CICD | 비밀값·token 해시를 GitHub에 넣지 않음. 재발급 HOLD는 기존 계정 불가 판정과 다름 |
| TokenRequest 발급 주체·audience·최대 만료·갱신 정책 | SEC 이하영 | CICD 이권욱 | [소유/소비 경계](https://github.com/321Team/fds-project-baseline/issues/19#issuecomment-6035815364). 코드 기본1h 또는 broker TTL900을 일반 정책 승인값으로 확정하지 않음 |
| 최종 Source/Workflow·Run/Attempt·두 이미지·현장 CA/상태 | 각 원본 생산자 / CICD | CICD | 기존 인계 index와 보존 bytes 재사용. 새 실행 시 현재 상태와 결속 |
| 새 Push 및 Deploy 입력/승인·유효기간·복구 범위 | 기존 Lead/검토 책임 | CICD | 과거 승인5997038348은 다른 회차이며 만료됨. 초안의 null/PENDING을 임의 채우지 않음 |
| 비중첩 작업창 | 사용자·관련 운영자 | CICD | 사용자가 10/9 00:00~09:00 단독 창을 확인함([기록](https://github.com/321Team/fds-project-baseline/issues/19#issuecomment-6062332401)). Harbor 타 세션과 공유 자원 사용은 실제 실행 직전 별도 대조. 개별 GO를 대신하지 않음 |

## 다음 실행 순서

1. 기존 [Push handoff](../handoff/push-review-37642123031/README.md), [Deploy Session](deploy-session.md), [Workflow handoff](deploy-workflow-handoff.md)를 재사용한다. 새 adapter/중복 worksheet를 만들지 않는다.
2. 위 필수 입력과 승인된 Source를 고정하고, 새 최종 입력 바이트·해시·승인 참조·만료를 검증한다. 초안을 활성 입력 경로에 배치하지 않는다.
3. 해당 최종 입력으로 `execute=false`를 수행한다. 이것도 실제 수행한 결과를 받기 전에는 NOT_RUN이다.
4. 허용된 회차/창에서만 실제 Push를 실행하고 fresh Scan→원격 tag/digest→credential 정리 receipt를 확인한다. 두 서비스의 부분 성공을 합쳐 성공으로 표시하지 않는다.
5. 새 Push 결과를 Deploy의 현재 SA UID·대상 Deployment UID·이전 image·롤백/스키마 호환성과 연결한다. 실제 TokenRequest/TokenReview·rollout·E2E·정리는 각각 결과를 남긴다.
6. expiry·drift·승인 철회·예상 밖 동시 작업·정리 실패 시 기존 도구의 STOP/복구 계약을 따른다. claim 삭제·권한 확대·임의 재시도는 하지 않는다.

현재: **Test/Build/Scan 증적 편입 완료 / Push·Deploy NOT_EXECUTED / 새 최종 입력 execute=false NOT_RUN / DELIVERY_GATE NOT_VERIFIED**. 2026-10-06 이전 회차의 Push 성공과 별개다. 이 정합화는 Runtime AC 체크·전체 #16/#11 종결 판정이 아니다.

Harbor 자동기동 수정·시험은 타 세션 범위다. 이 문서의 다음 작업에 포함하지 않는다.
