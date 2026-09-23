## 목적과 변경

- 변경 분류: 코드/실행 설정 포함(혼합 포함) / 사전 검증(Pre-check) 코드 / 문서·증적만
- Pre-check인 경우: 책임 티켓 / 담당 Lead / `REVIEW=NOT PERFORMED (Pre-check)` — 운영 서버 적용 전 해당 SHA 독립 리뷰 필요(순수 검증기 실행은 제외, 판단 근거 기록)
- 해당 Lead:
- 관련 티켓:
- 변경 이유 및 동작:
- 영향 대상(Runner / Test·Build·Scan / Harbor / Kubernetes / 복구):

## 검증

- 검토 대상 Commit:
- 수행한 검증과 결과:
- 미수행 항목과 사유:
- 증적 Commit·경로·해시:

## 검토 항목

- [ ] Token·개인키·원본 kubeconfig·Secret이 포함되지 않음
- [ ] 변경 영향과 필요한 시험 범위를 확인함
- [ ] 실행 코드 변경 시 실패 차단·권한·자격증명·복구 영향을 검토함
- [ ] 코드 포함 PR: 작성자 외 독립 기술 리뷰 결과와 최신 Commit을 기록함 (문서 전용은 N/A)
- [ ] 문서 전용 PR: 해당 Lead의 검토·병합 판단을 기록함 (별도 독립 승인 불필요)

## 적용 및 잔여 사항

- 배포 승인 대상 Source/Workflow SHA·Digest:
- 적용·복구 방법:
- 차단 사항 및 후속 작업:

PR 병합, 실제 실행, Delivery Gate 판정은 각각 구분한다. 코드 병합 외 단계는 해당 Lead가 결정하고, 여러 단계의 판단은 한 기록에 함께 남길 수 있다. 동일 코드의 Runtime·인수·Done마다 독립 승인을 반복하지 않는다.
