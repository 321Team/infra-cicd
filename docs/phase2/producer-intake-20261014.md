# P1 공급 증적의 P2 소비 연결 — 10/14 준비

CODEX_ASSISTED · 이권욱. GitHub 조회 2026-10-14 00:24~00:25 KST. **P1은 읽기 공급 자료, 이번 변경은 tools/phase2·docs/phase2에 한정**한다. `tools/delivery/*`, P1 workflow·Runner·Harbor/CA·Robot·공용 OCP·DB·DNS·route·권한·AC는 수정·실행하지 않는다.

| 기존 생산물 | 확인한 현재 단계 | P2에 재사용하는 범위 |
|---|---|---|
| [TBS 증적217](https://github.com/321Team/fds-project-evidence/pull/217) HEAD47ac79e99ed85e09e0a32fd4db0fd74f97ced920 | MERGED, CI1 SUCCESS | CICD147f760/APP affbc69의 Test/Build/Scan 원본 연결. P2용 새 TBS를 무조건 반복하지 않음 |
| [Push 증적218](https://github.com/321Team/fds-project-evidence/pull/218) HEAD9647a83724dea442c9e8238e60d68bfe2602dc8d | MERGED, CI1 SUCCESS | 원격 digest/receipt/전송 결과를 동일 APP source와 결속할 자료. P2 Registry 선택·pull 인증·cold-pull을 증명하지 않음 |
| [Deploy 증적219](https://github.com/321Team/fds-project-evidence/pull/219) HEADe7859e3256cfc1cae311a0ae1c897ae7ce8683c3 | MERGED, CI1 SUCCESS, 병합00:10:47 | P1 실제 배포 보고의 참조. **P1 API/namespace UID/승인/token/claim을 P2 Binding/Lease에 복사하지 않음** |

[CICD #11 최신 보고](https://github.com/321Team/infra-cicd/issues/11#issuecomment-6147808781)는 두 앱 배포/Pod 형상과 정리 결과를 보고한다. 이번 작성자는 해당 서버·비공개 raw를 새로 독립 검증하지 않았다. 병합·CI와 Runtime 보고·최종 인수를 구분한다. 최종 HA/Probe·업무 회귀·DB 판단은 P1 담당 흐름에서 수행하며 P2 준비 작업으로 착수하지 않는다.

## 실제 입력 도착 전/후의 작업 분리

1. 지금: 위 기존 링크·HEAD/RUN과 생산 source를 연결하고 P2 코어·오프라인 실패/복구·기록 검증을 준비한다. 공개 문서에 raw Plan/state/credential을 넣지 않는다.
2. 재환 #43: P2가 소비할 current/previous 두 이미지의 승인 digest·clean source/렌더·원격 receipt를 기존 원본으로 인계한다. P1에서 사용한 digest가 P2 후보일 수 있으나 같은 이미지라는 이유만으로 P2 승인값으로 확정하지 않는다.
3. 하영 #38: actual ROSA cluster/API·namespace `fds-app` UID·CA·승인 host·지원 인증/최소 RBAC receipt를 인계한다. `rosa-app`은 overlay다. 같은 namespace 문자열만으로 P1/공용 OCP와 같은 대상으로 판단하지 않는다.
4. 권욱 #46: 원본 수신/재해시/내용 검토, D22·D23·플랫폼 입력과 정확한 source/render·당회 실행 승인을 결속한다. 외부 boolean/문자열 진단을 Lease로 변환하지 않는다.
5. 그 뒤: 운영 authorizer·수명주기·실제 root/CLI 연결과 한정 수동 조정 구현을 독립 검토하고 별도 Runtime 창에서 검증한다. 현재 PENDING 값을 합성한 임시 운영값·권한 확대·재Apply·배포는 수행하지 않는다.

읽기 전용 복구 CLI 추가로 **기존 journal 내용의 재구성**은 준비됐다. 실제 상태 대조 자동화·권한/승인 검증·fence 조정·배포 CLI는 아직 완료하지 않았다. 실재 입력과 운영계약이 없는 부분을 완료로 표시하거나 운영 Runner의 환경을 탐색해 채우지 않는다.
