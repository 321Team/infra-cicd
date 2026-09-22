# 현재 관측 결과와 선행조건

**2026-09-22 상태 대조:** 정본은 docs-v3.1.18, v3.1.19는 수집 후보다. 앱 인계 승인·코드 병합(PR #19·#21·#24·#23·#26)이 완료됐고, 실제 Runtime이 처음으로 Source fetch→Handoff→Build→Scan을 도구 오류 없이 완주했다(결과 BLOCK — 실제 취약점 발견에 따른 정상 차단). 그러나 Runtime AC는 여전히 1/10이며, BLOCK은 통과가 아니므로 전체 Delivery는 NOT_VERIFIED다. [후속 상태와 근거](implementation-state-20260915.md#2026-09-22-후속-상태)를 따른다. 09-21·09-17 시점 기록은 각각 [이력](implementation-state-20260915.md#2026-09-21-후속-상태)으로 보존한다.

**2026-09-15 관측은 [실제 구성·후속 실행 기록](implementation-state-20260915.md)을 기준으로 읽는다.** 신규 Root/Leaf 발급·백업/파일 복구·공개 인계와 Runner 로컬/Idle는 확인했다. PR #2는 승인 후 병합됐고 첫 실제 Job·Harbor 서비스 전환·Delivery는 미검증이다. 아래 표는 09-09 수신 결과로 보존하며 현재 상태로 해석하지 않는다.

이 문서는 제출 증적의 시점별 결과다. 실시간 모니터링 결과가 아니다.

| 항목 | 확인 범위 / 남은 작업 |
|---|---|
| Harbor 컨테이너 복구 | 2026-09-09 12:29 KST 무렵 제출 증적에서 9개 running/healthy 및 로컬 TCP 확인. 재부팅 후 자동 복구는 별도 검증 필요. |
| Runner → Harbor | 2026-09-09 12:45:18 KST 증적에서 TCP 연결 및 공개 인증서 1개 수신. |
| Harbor TLS | 같은 증적에서 오류 18 / self-signed certificate. 인증서는 CA:TRUE, IP SAN 10.1.93.54 일치·수집 시점 유효기간 내. 승인 CA가 서명한 별도 Harbor Leaf 및 Consumer Trust 적용 필요. |
| CA 발급 역할 | 이권욱 CA 관리·발급·Runner 검증, 이하영 Harbor 개인키·CSR·적용·복구 담당으로 사용자 확정. 실제 정식 CA 존재 여부·보관 환경·발급 현황은 미확인. |
| Runner 설치·등록 | 현 세션에서 등록 완료·실제 Job 수신 증적 미확인. 초기 inventory를 최신 설치 상태로 간주하지 않음. |
| API LB/CP | 이전에 미완료였던 인증 API/Backend 선행조건을 이번 Harbor 결과로 완료 처리하지 않음. |
| 앱 Source·Token·Robot·Trivy 세부 기준 | 관련 담당 인계 및 실제 실행 조건 확인 필요. |
| Delivery Gate | NOT_VERIFIED |

09-15 당시 다음 작업은 첫 Runner smoke와 앱 Source·테스트 계약 인계였다. 현재는 위 09-17 후속 상태에 따라 최종 앱 인계와 최신 코드 리뷰를 진행한다. Harbor 적용·신뢰 배포는 이하영 주관 HBR 작업과 연계한다. 실제 인증 Push/Pull과 배포는 각 선행조건 충족 후 수행한다.

근거 증적 Archive SHA-256:

- Harbor Start Result: `74cf64bdb709a4c19a454daba79154213ec48c89492d3082a70b75e4b4a87c68`
- Runner TLS Inspection Result: `fd9311fb131a03b2295087fa41e50f69143cfc93be8db79e3be84b19ffa39725`

원본 Archive의 저장소 수신 Commit·경로는 인수 후 연결한다. 현재 파일 업로드만으로 fds-project-evidence에 등록됐다고 표시하지 않는다.
