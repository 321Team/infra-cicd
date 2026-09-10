# CA 준비·배포 조건 검사 검증 v1.0

2026-09-10 로컬 검증. 대상: 이번 후속 브랜치의 신규 `tools/preparation` 코드. 기존 PR #2 수집기·CSR 도구와 분리했다.

`python3 -m unittest discover -s tools/preparation -p 'test_*.py' -v`: **30건 PASS**.

| 분류 | 시험 |
|---|---|
| CA 입력 8건 | 기재 완료가 발급 성공이 아님, UNKNOWN 차단, 기존 CA 재생성 선택 차단, 부재 기록 모순 차단, 암호화/백업/접근/오프라인 필수값, 서비스 호스트 거부, 기존 CSR 교체 차단, 미작성/오류 입력 |
| 배포 조건 19건 | 유효 모의 입력, 앱별 Test 실패, Critical 검출, HIGH 기준 유지, 스캐너 오류/불명확 건수, 보고서/DB 누락, Digest 불일치/Tag 거부, Run/Attempt/Workflow 승인 불일치, Source/저장소/브랜치/대상 범위, API/Token/TLS/RBAC/잠금 미확인, 단계 구분, callback 실패 전파, 미작성 입력 |
| JSON 입력 3건 | 중복 키·비유한 숫자, symlink/FIFO, 1MiB 초과 파일 거부 |

음성 시험은 Mock callback 호출 0회를 확인한다. 실제 Kubernetes·Harbor 상태 변화나 운영 배포 차단을 측정하지 않았다. CA는 합성 자산 참조와 공개 해시 문자열만 사용하며 개인키/CSR/인증서를 새로 만들지 않았다.

미작성 CA 양식은 INCOMPLETE/2, 미작성 Gate 양식은 BLOCK/2를 기대한다. 문서 Bash·Workflow run 구문, Python AST·JSON·UTF-8/LF 및 PREPARATION_SHA256SUMS 검사를 수행한다. GitHub CI 실행 성공은 실제 Run 확인 전까지 미검증이다.

조건 일치는 입력의 진위를 증명하지 않는다. 실제 승인·스캔 결과 수집, Token·RBAC·동시 배포 잠금 및 Build/Push/Patch 어댑터 통합은 후속 검토 대상이다. `DELIVERY_GATE=NOT_VERIFIED`.
