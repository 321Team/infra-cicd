# P1-CICD-01 실제 구성과 후속 실행 — 2026-09-15

이 문서는 09-15 제출 증적과 GitHub 조회 결과를 연결한다. 실시간 상태 조회나 전체 인수 승인 기록은 아니다. 실행 증거는 [baseline #16](https://github.com/321Team/fds-project-baseline/issues/16)에 기록했다.

## 코드 승인·병합

- PR #2 HEAD: `5f575fc8bfbd68d29fc3995fe68bceb87f1b194b`.
- [이하영 APPROVED](https://github.com/321Team/infra-cicd/pull/2#pullrequestreview-5208370669): 2026-09-15 18:54:29 KST / 09:54:29 UTC, 위 HEAD와 일치.
- 이권욱의 명시적 병합 지시 후 [PR #2](https://github.com/321Team/infra-cicd/pull/2)를 19:05:42 KST / 10:05:42 UTC에 병합했다.
- 병합 Commit: `ffaa764623c5941b14fe12384e9ba5629bd60a6f`. 부모는 기존 main `be92fd00b269ff7d7abc124bb17f6d3e4c007851`와 위 승인 HEAD다.
- 이 후속 변경은 별도 검토 대상이다. PR #2의 승인을 새 문서·설정 변경의 승인으로 확대하지 않는다.

## CA 자산과 공개 인계

| 항목 | 확인한 사실 |
|---|---|
| CA VM | server01의 별도 ca01, CentOS Stream 10 Minimal, 2 vCPU / RAM 4GiB |
| 용량 차이 | 초기 계획 OS 20GB / 실제 OS 40GiB, CA 데이터 2GiB. 설정은 계획과 실제 관측을 별도 필드로 보존 |
| 암호화·격리 | OS root/swap 및 CA 데이터 LUKS, CA 볼륨 LUKS2, SELinux Enforcing, 준비 후 가상 NIC 제거. /boot·EFI와 VMware 전체 VM 파일 암호화는 포함하지 않음 |
| 발급 | 신규 Root RSA4096/1825일, Harbor 기존 요청 `harbor01-sn0wepv7` Leaf RSA3072/365일. Leaf serial `1000`, 다음 발급 serial `1001` |
| 검증 | 발급 화면과 실제 수신 공개 바이트에서 서명·프로파일·SAN·CSR 공개키 일치 확인 |
| 백업·복구 | 별도 물리 USB LUKS2/XFS, CA 자산 archive와 원본 CA 볼륨 LUKS 헤더 백업. 닫기/읽기 전용 재개방 후 해시 확인, 새 디렉터리에 실제 파일 복원, 키 유효성·Root 키/인증서·대장·권한 검증 PASS |
| 복구 범위 | 파일·키·인증서·대장 복구. 전체 VM/디스크 재구축과 실제 LUKS 헤더 복원은 미실행 |
| 마무리 | 복원 시험 임시본 제거·볼륨 마운트 해제·암호 매핑 닫기·USB 게스트 분리 화면 확인. 전원 종료·USB 분리보관은 사용자 완료 보고 |

CA 자산은 `/mnt/fds-ca/fds-project-root-ca-2026-replacement`에 있다. 현재 Root·Leaf·키·CSR을 다시 생성하거나 serial/index를 초기화하지 않는다. 보호 경로의 존재를 공개 파일 또는 비밀키의 저장소 첨부로 해석하지 않는다.

공개 인계값:

- `ca01-public-handoff-v1.0.tar.gz`, 4,725 bytes, SHA-256 `bf03bd09fde0c8a19e9da7b782ec782e5a5eda99facd84964b3213eb5826a4a0`.
- 정규 파일 7개: Root/Leaf/CSR, root-info.txt, harbor-info.txt, request-id.txt, SHA256SUMS. 내부 체크섬 6/6 PASS, 개인키 미포함.
- Root SHA-256 지문: `6A:75:BA:F4:DB:E0:3E:F2:4C:CE:94:4E:61:63:84:D3:18:F7:CA:91:9C:D7:BD:91:46:24:B4:CE:BA:C0:6B:10`.
- Root serial: `3F4570F7333F7601C592B1FF385204AB831559A5`. 이전 화면 전사의 `…C592A1…`를 수신 PEM 기준 `…C592B1…`로 정정했으며 인증서 교체가 아니다.
- Harbor 수신 디렉터리: `/home/server08/ca01-public-handoff-v1.0/verified`.
- 기존 서버 키: `/etc/harbor/pki/requests/harbor01-sn0wepv7/harbor01.key`, root:root 0600. 키 내용은 수신하거나 Git에 넣지 않았다.
- 기존 키·CSR·Leaf의 SPKI SHA-256: `636f85ce3ae0d5e3ea49e50fca2b8ec1e5c132f990d7867e2ad59d0a2e333638`.

## Harbor 적용 경계

Harbor에서 수신 archive·내부 체크섬·기존 키/Leaf 일치, `harbor.fds.internal` 서버 용도 검증 OK 및 `Key is valid`를 확인했다. 09-15 화면 기준 v2.13.1 컨테이너 9개 중 harbor-log만 실행 중이고 8개는 Exited(128)이다. 종료 원인은 미확인이다. `/opt/harbor`, `/etc/harbor/pki/current`, `/data/harbor-formal`은 존재하지 않았다. 인증서 수신 검증은 서비스 TLS 전환과 구분한다.

서비스 설치판·기존 데이터/구성 보호·TLS/Scanner/Robot 적용은 **P1-HBR-01 이하영 Lead / 이권욱 Support**다. 이권욱은 CA 공개 인계·Runner Trust·CI Trivy/Push/Digest/배포 연계를 담당한다. Worker Trust/Pull은 HBR/K8S 연계다. 구 CA 신뢰 전환·키/복제본 폐기는 아직 검증되지 않았다.

## Runner 증거와 첫 Job

09-15 18:43:37+09:00 제출 화면: 호스트 `cicd-runner01`, 사용자 `server01_cicd-runner`, UID 1000, SELinux Enforcing, 지정 서비스 User 일치와 active/running, Git 2.52.0, Podman 6.1.0, 로그인 rootless=true. GitHub Runners 추가 화면에서 Idle 및 self-hosted/Linux/X64/p1-cicd/onprem/podman 라벨 6개를 확인했다. 후자 화면에는 저장소 URL·촬영 시각이 없으므로 이번 infra-cicd 확인 흐름의 증거로만 연결한다.

| 증거 | 수신 이미지 SHA-256 |
|---|---|
| 로컬 사전점검, 0fa9c1bc-bf21-4cfb-8ed0-817744609571.png | `8818ca32d3c38c8e999f506bda1ddd47683d2ef2c42fb0250c36dbf8df1e6b75` |
| GitHub Idle/라벨, 0a27d275-c0f8-4f37-84a0-746f2a45c5c1.png | `b1ba889799c6a9cf02850f40760111ded98f9f2cf00665713163e9ca66b9bad0` |

서비스 이름: `actions.runner.321Team-infra-cicd.cicd-runner01.service`.

첫 실행 순서:

1. [P1 Runner smoke](https://github.com/321Team/infra-cicd/actions/workflows/runner-smoke.yml)에서 **Run workflow → main**으로 한 번 실행한다. 승인 main의 기존 수동 Workflow를 사용한다.
2. Run URL·Workflow Commit·Run Attempt를 저장하고 Job conclusion=success, `JOB_RECEPTION=VERIFIED`, `BUILDER_ROOTLESS=VERIFIED`를 함께 확인한다.
3. 실행 중인 Job이 없는 유휴 상태에서 서비스 재시작 후 다시 Job을 수신하는지 후속 검증한다. 재시작 명령 실행이나 성공을 이번 기록으로 선언하지 않는다.
4. 앱 Source·테스트 계약·Trivy DB·Robot·K8s 단기 Token을 인계해 실제 Build/Scan/Push/Deploy를 연결한다.

병합 직후 조회까지 Actions Run은 0건이며 원인은 미확인이다. Workflow 파일 존재나 Idle 상태만으로 실제 Job 성공 또는 필요한 모든 GitHub 다운로드/Artifact egress 허용을 판정하지 않는다. 첫 실행은 위 GitHub 화면의 수동 경로를 사용한다. 이 상태 기록을 갱신하기 위해 PR 트리거나 자동 내부망 Job을 추가하지 않는다.

## 09-14 결정과 남은 Runtime

[HBR #13](https://github.com/321Team/fds-project-baseline/issues/13)과 [CICD #16](https://github.com/321Team/fds-project-baseline/issues/16)에 기록한 사용자 결정은 다음과 같다. 고정된 v3.1.17의 원문 요구사항으로 소급하지 않는다.

- 미해결 CRITICAL/HIGH 및 스캐너/DB 오류 시 Push와 Deploy 차단.
- 예외는 CVE·Digest·사유·만료·승인자를 연결.
- Harbor Scanner 유지, Robot 유한 30일; Never 미채택.

이번 설정/계약 동기화는 실행 어댑터·실제 정책 집행을 구현한 것이 아니다. Runner 실제 Job/재시작 후 수신, Harbor 서비스 TLS·Push/Pull·음성시험, 두 앱 Delivery, 최소권한 Token·실패 차단·Digest 복구가 남는다. `runtime_enforcement=NOT_INTEGRATED`, `DELIVERY_GATE=NOT_VERIFIED`, Runtime AC 0/10을 유지한다.
