# P1-CICD-01 실제 구성과 후속 실행 — 2026-09-15

이 문서는 09-15 제출 증적과 GitHub 조회 결과를 연결한다. 실시간 상태 조회나 전체 인수 승인 기록은 아니다. 실행 증거는 [baseline #16](https://github.com/321Team/fds-project-baseline/issues/16)에 기록했다.

## 2026-09-23 재점검 — 취약점 조치와 원본 증적 회수

- [PR #19](https://github.com/321Team/infra-cicd/pull/19)·[#21](https://github.com/321Team/infra-cicd/pull/21)·[#24](https://github.com/321Team/infra-cicd/pull/24)·[#23](https://github.com/321Team/infra-cicd/pull/23)·[#26](https://github.com/321Team/infra-cicd/pull/26)은 병합됐다. 앱 Source/Unit 인계와 repository 정정은 config에 반영됐다. 이전 리뷰·인계 대기를 현재 차단으로 사용하지 않는다.
- [Run35683010080 / Job106603847854](https://github.com/321Team/infra-cicd/actions/runs/35683010080/job/106603847854) 원본 로그: 2026-09-22 12:24:04~12:25:34 KST, cicd-runner01, `APP_WORKFLOW=BLOCK`, `ERROR_CODE=NONE`, exit10, Push/Deploy 미실행. 로그와 해당 실행 코드 경로 대조로 승인 Private Source 수신·인계 검사 후 Build/Scan 경로 도달을 확인했다. Token의 현재 만료·권한 전체를 입증하는 기록은 아니다.
- [운영자 상세 보고](https://github.com/321Team/infra-cicd/issues/11#issuecomment-5771627965)는 transaction-api의 HIGH/CRITICAL 차단과 fds-engine **NOT_SCANNED**를 명시한다. 따라서 이전 '끝까지 완주'는 첫 이미지 보안 차단 경로 도달로 한정한다. 두 이미지 Scan PASS나 전체 Delivery 완료가 아니다. CVE별 원본 report는 이번 점검에서 미회수다.
- 두 서비스의 FastAPI0.115.5는 [상류 의존성](https://github.com/fastapi/fastapi/blob/0.115.5/pyproject.toml)상 Starlette>=0.40.0,<0.42.0을 요구한다. Starlette lock 단독 상향 대신 앱 담당이 호환되는 FastAPI/Starlette 조합·requirements·4개 환경별 lock을 검토한다. 새 base manifest/config/layer와 OCI·wheel 공급도 함께 인계해야 한다.
- PR #18은 09-22 Lead 결정으로 미병합 종결(Superseded)됐다. 과거 공급 생산 증적은 보존하며 기존 수집기를 재활성화하지 않는다. 새 공급물의 생산 위치·승인 경로는 개선 입력에 맞춰 별도 인계한다. PR #3은 Push/Deploy 참고 설계 Draft로 유지한다.
- [취약점 후속·증적 회수 절차](runtime-block-followup-20260923.md)에 실제 Run 기준과 담당별 입력을 고정했다. 새 수집기는 기존 report의 허용 필드만 새 파일로 모으는 일회성 Pre-check이며 보안 게이트·무결성 판정기·Delivery 실행 경로가 아니다.
- **Runtime AC1/10 / APP_TEST_BUILD_SCAN=EXECUTED_BLOCK / Delivery NOT_VERIFIED**. AC05의 검사 오류·취약점 차단 근거는 늘었지만 실제 Deployment 이미지 불변 증적은 확보하지 못해 AC 완료로 올리지 않는다. 증적 저장소 `evidence/P1-CICD-01/`은 조회 시점 main에 없고 편입 독립 리뷰는 별도다.

## 2026-09-22 후속 상태

첫 실제 Runtime 종단 실행과 그 과정에서 발견·수정한 결함을 기록한다.

| 범위 | 확인 결과 |
|---|---|
| 1차 시도 | [Run 35588577241](https://github.com/321Team/infra-cicd/actions/runs/35588577241): Source fetch·Handoff·Build까지 성공, Trivy Scan 단계에서 `SCANNER_EXECUTION_ERROR`로 실패 |
| 근본 원인 | cicd-runner01에서 수동 재현(3건: 원본 tar/엔트리 재정렬 tar/압축 해제 디렉터리)으로 확정. Trivy의 OCI-image 오프너가 `--input` 경로에서 `index.json`을 stat하므로 압축된 oci-archive tar 자체는 "not a directory"로 거부됨. 이 저장소의 `inspect_archive()`(Python)는 tar를 직접 파싱해 문제없이 통과했던 것이라 그동안 코드 리뷰·로컬 테스트로는 드러나지 않았음 |
| 수정 | [PR #26](https://github.com/321Team/infra-cicd/pull/26): `scan_one()`에서 Trivy 실행 전 이미 검증된 archive를 압축 해제해 그 디렉터리를 `--input`으로 전달. 이재환 APPROVED, main `7e939d7` 병합 |
| 2차 시도 (수정 후) | [Run 35683010080](https://github.com/321Team/infra-cicd/actions/runs/35683010080): Source fetch → Handoff → Build(2개 이미지) → Scan까지 **`ERROR_CODE=NONE`으로 도구 오류 없이 완주**. 결과 `APP_WORKFLOW=BLOCK` — 실제 HIGH/CRITICAL 취약점 발견에 따른 정상 차단 판정(도구 결함 아님) |
| 잔여 | 어떤 서비스·CVE가 걸렸는지 상세는 `<EVIDENCE_DIR>/build-scan/scan/result.json`에서 확인 필요(이번 갱신에서 미회수). 취약점 자체 해소는 fds-msa(앱 저장소) 과제이며 infra-cicd 범위 밖 |
| 인수 | Runtime AC **1/10**(불변), APP_TEST_BUILD_SCAN **EXECUTED_BLOCK**(최초로 NOT_EXECUTED에서 전환), DELIVERY_GATE **NOT_VERIFIED**(불변 — BLOCK은 통과가 아님) |

**AC 개수를 이번 실행으로 재해석하지 않는다.** "파이프라인이 도구 오류 없이 끝까지 실행됨"과 "10개 AC 중 몇 개를 충족하는가"는 별개 판단이며, 후자는 Lead가 각 AC 정의와 대조해 별도로 판정한다.

## 2026-09-21 후속 상태

09-17 이후 남아 있던 코드 리뷰·앱 인계·Runtime 실행 선행조건을 아래와 같이 갱신한다. Runtime AC·Delivery Gate는 실제 앱 Runtime 결과로만 상향하며, 이번 갱신(코드 병합·config 승인)만으로 바꾸지 않는다.

| 범위 | 확인 결과 |
|---|---|
| 코드 병합 | [PR #19](https://github.com/321Team/infra-cicd/pull/19)(오프라인 Test·Build·Scan·중앙 Workflow) → [PR #21](https://github.com/321Team/infra-cicd/pull/21)(Private Source 인증) → [PR #24](https://github.com/321Team/infra-cicd/pull/24)(테스트 fixture·CI 트리거 수정) → [PR #23](https://github.com/321Team/infra-cicd/pull/23)(config 승인값 반영) 순서로 main 병합 완료. 최종 main `be8fd912e455dae97055c6ab7615331e858500bf` |
| 독립 리뷰 | 4건 모두 이재환(`supreme33-kr`) APPROVED, 각 병합 직전 HEAD와 승인 commit_id 일치 확인. PR #21은 "차단 지적 없음"으로 조건 없는 승인 |
| 실제 CI | main push 트리거 기준 최종 HEAD(`be8fd91`)의 [Run35580238808](https://github.com/321Team/infra-cicd/actions/runs/35580238808) success. 중간 병합 커밋들의 CI는 concurrency 설정으로 취소됨(정상, 다음 push가 대체 검증) |
| 앱 인계 | 이재환 최종 회신([2026-09-18](https://github.com/321Team/infra-cicd/issues/11#issuecomment-5725640696), [저장소 정정 2026-09-21](https://github.com/321Team/infra-cicd/issues/11#issuecomment-5757298518))의 Source `321Team/fds-msa@ec69b67067decf35abbfed0a6a99f3073132787b`, transaction-api Unit NOT_APPLICABLE 사유를 `config/app-build-scan.json`에 반영. `status: APPROVED_FOR_TEST_BUILD_SCAN` |
| CI 인프라 | `source-validation.yml` 트리거 브랜치 목록에 `ci/private-app-source-20260918` 누락으로 PR #23·#24가 CI 0건이었던 문제를 [PR #24](https://github.com/321Team/infra-cicd/pull/24)에서 수정. PR #21이 이미 쓴 것과 동일 패턴 |
| 인수 | Runtime AC **1/10**(불변), APP_TEST_BUILD_SCAN **NOT_EXECUTED**(불변), DELIVERY_GATE **NOT_VERIFIED**(불변). 코드/config 준비 완료와 실제 Runtime 실행 완료는 구분한다 |

다음 실행 순서는 중앙 수동 Workflow(`app-test-build-scan.yml`) `workflow_dispatch` 실행 → cicd-runner01의 실제 Test→Build→Scan 결과 회수 → Lead(이권욱)의 AC 판정이다.

## 2026-09-17 후속 상태

아래 별도 관측을 09-15 기록 위에 덧붙인다. 이후 본문의 첫 Job 미실행·Runtime AC 0/10은 **09-15 당시 상태**다. CA·Runner 신규 생성/등록 및 성공한 공급·probe를 다시 수행하라는 지시가 아니다.

| 범위 | 이후 확인 결과와 한계 |
|---|---|
| 정본 | docs-v3.1.18 유지. v3.1.19는 수집 후보이며 발행 미완료 |
| 실제 Runner Job | Trivy DB 사전검증 [Run35061666680](https://github.com/321Team/infra-cicd/actions/runs/35061666680), Job104683095209 PASS. 앱 Runtime의 성공으로 확대하지 않음 |
| 공급 수집 | 09-17 10:07:57–10:09:09 KST, 내부 hash59/59·명령22/22·offline pip4프로필 PASS. archive SHA256 `6ae2fe28d2ab8eef0d4cf2e92a48b469b7a60fed3b59b8070461e61db2367403` |
| base 소비 probe | 09-17 11:41:25–30 KST, 내부 hash7/7·명령6/6 및 고정 digest의 최소 Build PASS. archive SHA256 `db6c4a18857aa5ecb1f9a923888268193d7653932b9b0eeb0174116fe07f7831`. 실제 앱 시험/Build/Scan 미포함 |
| 코드 | main의 Scan·Build 어댑터와 [PR #19](https://github.com/321Team/infra-cicd/pull/19)의 공급 소비·중앙 Workflow 후보를 구분. 기존 PR #3의 CRITICAL 단독 모의 Gate는 현행 HIGH/CRITICAL 기준을 충족하지 않음 |
| 앱 인계 | 최종 승인 SHA, pin/lock 반영 및 API Unit 범위가 미확정. [Issue #11](https://github.com/321Team/infra-cicd/issues/11)의 최종 인계 후 config를 대조 |
| 리뷰 | 사용자 부재 대체 지시에 따라 PR #5·#19 리뷰 요청은 이재환(supreme33-kr). PR #2의 과거 승인을 새 변경에 이전하지 않음 |
| 인수 | Runtime AC **1/10**, APP_TEST_BUILD_SCAN **NOT_EXECUTED**, DELIVERY_GATE **NOT_VERIFIED**. 공급·probe PASS와 구분 |

Harbor 서버 상태는 이번 문서 충돌 해소에서 새로 조회하지 않았다. 이하영 Lead의 [HBR #13](https://github.com/321Team/fds-project-baseline/issues/13) 인계와 실제 서비스·엄격 TLS·인증 Push/Pull 증적을 별도로 대조한다. 이후 실행 순서는 최종 앱 인계 → 최신 config/코드 독립 리뷰·main 반영 → 중앙 수동 실행이다.

## 09-15 관측 이력

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
