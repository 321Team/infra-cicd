# PR #2 검토 의견 반영 — 2026-09-10

검토 원문: [이하영(hayeounglee) 댓글](https://github.com/321Team/infra-cicd/pull/2#issuecomment-5614536834), 2026-09-10 07:04:24 UTC / 16:04:24 KST. 검토 시점 원본 HEAD는 `47d87b3b5d99db5205a2c1141f172d69d96b1c8c`다. 댓글은 수정 의견이며 GitHub APPROVED 제출 기록은 아니다.

판정: 필수 1건과 권고 5건을 기존 요구사항 범위에서 수용했다. 기타 3건 중 stderr는 비밀정보 보호를 유지하도록 수정 수용했고, IP 중복은 수정했으며, `.gitattributes`는 PR #1 선행 병합으로 충족했다. 아래는 작성 측 반영·시험 기록이며 독립 재검토 또는 최종 병합 승인 기록이 아니다.

| 의견 | 판정·반영 |
|---|---|
| 필수: 인증서 실제 설치 명령 누락 | 수용. [정식화 §5.1](../harbor-formalization.md)에 공개 발급물 수신, 승인 CA 지문/chain/IP·DNS/CSR/키 일치 검사, 기존 키 서버 내 복사, root 소유권·권한, release/current 경로, 실패·취소 절차 추가 |
| 권고 1: 실패한 임시 키 폴더 정리 | 수용. 실패 단계·요청 경로 출력, 원본 보존, 발급 대장/서비스 참조 확인 후 폐기 확정된 요청 하나만 정리. 이미 검토한 CSR을 자동 삭제하거나 재생성하지 않음 |
| 권고 2: PR 상단 범위 요약 | 수용. 제공 기능, 미완료 운영 검증, 실행 영향, 재검토 대상을 상단에 기록 |
| 권고 3: 자동 CI | 수용. [소스 자동 시험](../source-validation.md)은 GitHub 제공 ubuntu-24.04에서 PR/main 이벤트로 단위시험 수행. 내부 Runner는 기존 수동/main 조건 유지 |
| 권고 4: 실행 Workflow 포함 표시 | 수용. 자동 소스 CI의 PR 갱신/main push 실행 가능성과 내부 smoke의 수동 활성화 시점을 구분 |
| 권고 5: 템플릿/실행본 동기화 | 수용. CI의 `cmp`로 두 파일의 불일치를 실패 처리. 실제 smoke 코드·라벨·권한·트리거는 변경하지 않음 |
| 기타: stderr 상세 기록 | 수정 수용. 실패 단계·rc 및 고정된 stderr 오류 유형을 제공. 원문·타임아웃 부분 출력·임의 문자열은 내보내지 않으며 알 수 없는 상세는 DETAIL_WITHHELD로 기록 |
| 기타: .gitattributes 병합 순서 | 충족. PR #1은 main `be92fd00b269ff7d7abc124bb17f6d3e4c007851`에 먼저 병합됨. 기존 LF 규칙을 중복 추가하거나 전체 파일을 재정규화하지 않음 |
| 기타: Runner IP 변수 재사용 | 수용. GitHub probe source_address를 `HOSTS['runner'][1]`에서 가져오도록 수정 |

## 전제 정정과 실행 경계

CA가 Harbor `.key`를 반환한다는 전제는 채택하지 않는다. CA는 공개 Leaf/CA 인증서를 전달하며 Harbor는 이미 생성한 `harbor01-sn0wepv7` 요청의 개인키를 서버에서 사용한다. CSR 해시·SPKI는 기존 검토값에 고정했다. 승인 CA 실제 자산과 서명된 Leaf가 미수신인 상태에서 정식 설치를 실행할 수는 없다.

§5.1은 파일 설치·최초 `current` 생성 명령이다. 서비스 설정 전개·중지·기동은 선정 설치판, 실제 마운트와 일관된 백업 확인 후 §5.2에서 진행한다. 기존 `/data` 보존, 신규 `/data/harbor-formal`, CA/Leaf 역할 분담, Robot 분리 및 인수 조건을 유지한다.

## 로컬 검증

- 수집기 **17건**, CSR 도구 **11건**, 합계 **28건 PASS**. 기존 23건에 오류 진단·비밀값 제외·실패 키 보존·Runner source 변수 시험 5건을 추가했다.
- 문서 Bash 블록 및 중첩 설치 Bash, Workflow run 구문 PASS. Python AST·JSON·YAML·UTF-8/LF 검사 PASS.
- 실제 설치 블록은 이 비대상 호스트에서 전제조건 실패로 중단됨을 확인했다. Harbor 경로 변경이나 프로젝트 키 생성은 수행하지 않았다.
- hosted CI 트리거/main 제한·권한·실행 위치·checkout SHA/자격증명 저장 해제, smoke 템플릿/실행본 바이트 일치 확인.
- 단위시험의 오류 로그·임시 키 문자열은 Mock 시험 데이터다. 실제 프로젝트 CA/Harbor 키를 생성하거나 서버를 조작한 결과가 아니다.

GitHub CI 실행 결과는 실제 Run URL·대상 SHA·결론을 PR에 별도로 기록한다. 로컬 시험 PASS를 GitHub Actions Job 성공으로 바꾸어 기록하지 않는다. 내부 Runner smoke·Harbor strict TLS·Push/Pull·배포·복구 인수는 미완료이며 `DELIVERY_GATE=NOT_VERIFIED`를 유지한다.

기존 v1.0 배포 ZIP, 원본 제출 증적, 인증서 프로파일 및 채택 설정은 변경하지 않았다. 수정된 수집기·CSR 소스의 Commit/해시는 앞으로 실행할 결과에만 적용하며 과거 증적의 실행 소스에 소급하지 않는다. 새 Commit의 변경 범위는 이하영의 독립 재검토 대상이다.
