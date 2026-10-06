# Deploy credential window 후속 — 2026-10-06

기준 main 39e59842dc6b225d3a5d53278bddf0ae50b70cbb / PR53 독립 재리뷰의 토큰 만료 지적 대응.
AI usage: CODEX_ASSISTED. 실제 Runtime 연결·토큰 발급·배포 없음.

Adapter의 remaining_seconds는 검증된 Deploy 승인 만료에서 계산하고,
신규 필수 credential_remaining_seconds는 검증된 실제 자격증명 만료에서 계산한다.
요청한 TTL3600이나 발급 요청 시각만으로 실제 남은 수명을 가정하지 않는다.
JWT payload decode만으로 검증된 발급자/서명/SA/audience를 증명하지 않는다.
metadata 검증과 실제 API 인증은 향후 transport/Runtime 호출자의 책임이다.

두 대상 preflight 이후 및 첫 PATCH 직전에는 각각의 잔여시간이
2 × timeout + window_margin 이상이어야 한다. 긴 승인으로 짧은 토큰을 대체하거나
긴 토큰으로 짧은 승인을 대체하지 않는다. 양의 잔여시간은 매 GET/PATCH
권한 검사에서도 확인하며 0/음수/비유한수/bool/조회 실패는 중단한다.
작업 중 인증 유효성이 사라지면 자동 rollback을 하지 않는다.
일반 실패에 따른 별도 승인 rollback도 살아 있는 credential을 요구한다.

토큰 취소/서버 거부/연결 실패는 시계 계산만으로 감지할 수 없다.
실제 transport는 남은 승인/토큰 시간보다 긴 요청을 시작하지 않고,
API 인증 실패와 응답 유실을 구분해야 한다. 이번 core 변경은 transport를 구현하지 않는다.
토큰 값은 함수에 전달하지 않고 수명 숫자만 사용하며 예외 원문을 영수증에 기록하지 않는다.

추가 MOCK 8건: 짧은 token/긴 approval, 정확한 경계, polling 중 token 만료,
PATCH_PENDING 이후 만료, invalid/nonfinite 수명, provider 예외 비노출,
짧은 approval/긴 token, 복구 직전 token 만료.
기존 core35건과 합쳐43건. 전체 source-validation CI에서 실행한다.
로컬 shell은 앞선 실행환경 오류로 사용할 수 없으며 새 로컬 시험을 주장하지 않는다.

다음 남은 설계 결정:
- 승인 입력이 사전 dispatch Run ID를 요구하는 v1 binding 구조:
  PR53 재리뷰 대안(a) SHA+입력hash 승인 / Run·Attempt 영수증 결박을 권장하지만
  현재 v1을 묵시적으로 완화하지 않았다. 별도 스키마·재실행 claim·회귀·독립 리뷰로 연결한다.
- 실제 승인 참조 확인, TLS transport, lock/claim, durable checkpoint, cleanup,
  workflow, HPA·이전image Pull·APP DB TLS/E2E 검증.
라이브러리 병합은 Runtime 실행 승인이나 DELIVERY_GATE PASS가 아니다.
