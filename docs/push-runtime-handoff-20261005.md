# P1-CICD-01 Push 운영 인계 보완 — 2026-10-05

## 기준과 현재 판정

책임 티켓: https://github.com/321Team/fds-project-baseline/issues/16
정본: [v3.1.21 P1-CICD-01](https://github.com/321Team/fds-project-baseline/blob/979facb98de8ca1f1e2ce22f38b27ac3366f3fc3/tickets/v3.1.21/descriptions/P1-CICD-01.md).
이 문서는 운영 준비 기록이며 정본 AC·담당·일정을 변경하거나 Runtime GO를 승인하지 않는다.

PR #48은 exact HEAD b55cbf5f468c7cd308003d0f5dbc9f2690d79c6e에 대한
hayeounglee 독립 APPROVED(5413195011) 후 병합됐다.
merge/main 조회 SHA: a7f117d2f15e00b62965940a4df16cc39e970a58.
[병합 결정](https://github.com/321Team/infra-cicd/pull/48#issuecomment-5993011920).
기존 push-adapter 문서의 미병합 표기는 작성 당시 상태이며 현재 상태는 위 기록을 따른다.
같은 코드에 독립 리뷰를 반복 요구하지 않는다. 이후 실행 코드 변경은 해당 diff의 리뷰가 필요하다.

## Skopeo 추가 영향과 버전 표

| 항목 | 판정 |
|---|---|
| Actions·외부 Runner·기존 Build/Trivy·Harbor | 기존 구성 유지 |
| TLS 검증·최소권한·승인 Digest·AC | 기존 요구 유지, 완화 없음 |
| Runner 소프트웨어 | Skopeo 신규 의존성 추가 |
| 추가 이유 | 기존 검증 OCI archive 직접 전송, preserve-digests 및 원격 raw manifest 대조 |
| 설치 후보 | skopeo 3:1.24.0-2.el10.x86_64 / appstream |
| 사용자 dnf 미리보기 | 신규 1개, 다운로드 8.2 M, 설치 크기 26 M; 실제 설치 아님 |
| 실제 설치 버전·RPM 검증·옵션 지원 | 미확인 |
| 재구축 영향 | Runner 설치/복구 목록에 버전·repository·설치 결과 포함 필요 |
| 대안 | Podman 사용은 가능성 검토 대상이나 현재 adapter 교체와 Digest 보존 재검증 필요 |

Skopeo는 정본에 지정된 필수 도구가 아니라 PR #48의 구현 선택이다.
기존 #16과 이 PR로 변경 이유·범위·증적을 연결하고 다음 정본 후보 인계에 포함한다.
패키지 설치만으로 Push·전체 Delivery 완료가 되지 않는다.
새 DNS/egress·권한·Workload가 필요해지면 기존 변경관리 기록에 실제 차이를 추가한다.

## 권장 순서와 담당

1. 이권욱: 이 문서의 변경 범위·버전 후보 확인. 설치 거래가 기존 신규1개와 같은지 재확인.
2. 이권욱: Runner 패키지 설치 및 UID/HOME·옵션 지원 증적 수집. 설치와 Secret 보호 검토는 독립적으로 진행 가능.
3. 이권욱 + 이하영: Secret 등록 전에 아래 보호 방식과 잔여위험 확정.
4. 이권욱: 실행 시점 main SHA·Run #5 원본 hash·승인 reference/유효기간으로 record 구성.
   독립 검토된 record hash를 인계하고 execute=false로 검증. 승인값이나 과거 증적을 임의 PASS로 채우지 않는다.
5. 이권욱: Robot 권한·만료·tag 정책·fresh scan 준비와 실행 판단을 기록한 뒤 실제 Push.
6. 결과: 원격 tag/digest manifest, 서비스별 결과, credential cleanup, dispatch-result 및 SHA256SUMS 확인.
   이하영 독립 증적 검토와 Lead 판정으로 연결. Deploy/Health/E2E/rollback은 별도 잔여.

## 설치·검증·복구 절차

실행 위치는 cicd-runner01, 계정 server01_cicd-runner, UID1000이다.
설치 후보는 위 RPM으로 한정한다. 기존 사용자 미리보기는 설치 승인의 증거로 확대하지 않는다.

- 실행 직전 hostname/id, HOME 소유자·mode(현재 wrapper는 정확히0700 요구), rpm 설치 여부를 읽기 전용 확인.
  HOME 권한이 다르면 자동 chmod하지 않고 기존 사용 영향과 변경 범위를 확인한다.
- sudo dnf의 동일 버전 설치 거래를 확인한다. 추가 upgrade/remove/다른 의존성이 나타나면 중단하고 차이를 재검토한다.
- 설치 시 후보 NEVRA를 지정하고 대화형 거래 확인을 유지한다. 자동 -y는 사용하지 않는다.
- 설치 후 rpm -q, rpm -V skopeo, /usr/bin/skopeo --version을 보존한다.
  copy 도움말의 --preserve-digests / --dest-authfile / --dest-tls-verify,
  inspect 도움말의 --raw / --authfile / --tls-verify를 각각 확인한다.
- 실패 시 Push로 진행하지 않는다. dnf 거래 ID/출력과 실제 설치 상태를 보존한다.
  제거가 필요하면 skopeo만 대상으로 제거 미리보기를 확인하고 다른 패키지 제거가 있으면 중단한다.
  dnf history undo, autoremove, 전체 패키지 갱신을 자동 실행하지 않는다.
- 설치는 이미지 push, Podman load, 서비스 재시작, CA 재설치를 포함하지 않는다.
  정상 판정은 패키지·옵션 준비 완료까지이며 Skopeo의 실제 Harbor 동작은 후속 Runtime에서 검증한다.

## Secret 보호 방식: 등록 전 미해결

독립 리뷰 권고는 Environment secret + main 제한이다.
하지만 현행 docs/governance.md는 GitHub Free 조직 / Private 저장소를 전제로 한다.
[GitHub 공식 문서](https://docs.github.com/en/actions/how-tos/deploy/configure-and-manage-deployments/manage-environments)에 따르면
Free의 Private 저장소에서는 Environment/Environment secrets를 사용할 수 없으며,
Team Private에서도 required reviewers 지원을 당연시할 수 없다.
현재 실제 조직 플랜·기능 설정은 이번 커넥터로 확인하지 못했다.

권장: 기존 Free/Private 전제를 유지해 수동 승인·제한된 write/dispatch 권한과
기존 P1-SEC/P1-HBR 자격증명 수명/회수 통제의 적용 가능성을 먼저 확정한다.
Repository secret은 다른 branch workflow 작성자에 대한 main 전용 보안 경계가 아니다.
수동 통제를 Environment 보호와 동등하다고 표현하지 않는다.
이 잔여위험의 수용 여부와 실제 통제가 확정되기 전 Secret 등록/execute=true를 진행하지 않는다.
조직이 이미 지원 플랜이면 Environment 경로를 검토하되 workflow 변경은 별도 독립 코드 리뷰 대상이다.
플랜 구매·Public 전환·새 Robot 발급은 이 문서의 실행 범위가 아니다.

## 검증 수준

사용자 콘솔: OS CA 설치 및 curl Harbor TLS PASS, Run #5 원본 chain/hash PASS,
Skopeo 부재 및 dnf 설치 미리보기 확인.
GitHub: PR #48 병합 및 독립 리뷰 원문 확인.
실제 설치·Secret 등록·Skopeo Push·Deploy는 이번 작업에서 실행하지 않았다.
PUSH=NOT_EXECUTED / DELIVERY_GATE=NOT_VERIFIED 유지.
AI usage: CODEX_ASSISTED.
