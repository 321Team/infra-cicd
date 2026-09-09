# 현재 관측 결과와 선행조건

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

우선 정식 CA 현황과 Harbor 적용 경로를 확인하고 TLS 신뢰를 정리한다. Runner 등록 준비와 앱 Source·테스트 계약 확인은 병행할 수 있다. 실제 인증 Push/Pull과 배포는 각 선행조건 충족 후 수행한다.

근거 증적 Archive SHA-256:

- Harbor Start Result: `74cf64bdb709a4c19a454daba79154213ec48c89492d3082a70b75e4b4a87c68`
- Runner TLS Inspection Result: `fd9311fb131a03b2295087fa41e50f69143cfc93be8db79e3be84b19ffa39725`

원본 Archive의 저장소 수신 Commit·경로는 인수 후 연결한다. 현재 파일 업로드만으로 fds-project-evidence에 등록됐다고 표시하지 않는다.
