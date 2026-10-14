# P1 CICD AC05 — 두 스캐너 실패 유형 시험 준비

책임: P1-CICD-01 / 이권욱. 독립 코드·입력 검토: 이하영.
관련: [CICD #11](https://github.com/321Team/infra-cicd/issues/11),
[baseline #16](https://github.com/321Team/fds-project-baseline/issues/16),
[증적 PR227](https://github.com/321Team/fds-project-evidence/pull/227).

## 현재 판정과 시험 경계

PR227은 2026-10-14 14:44 KST에 병합됐다. 과거 scanner rc1 / 취약점 rc10은 확인됐지만
각 실패 당시 배포 이미지 관측은 없어서 **AC05 PARTIAL**이다. 이번 변경은 그 공백을
채울 **코드·입력 준비**이며, 실제 스캐너 시험·배포 전후 관측은 아직 NOT_RUN이다.
PR227 편입 승인이나 사용한 Unit-negative 회차를 새 두 회차의 승인으로 쓰지 않는다.

`app-scan-negative.yml`은 main/manual/Attempt1만 허용한다. 입력은 고정된 두 case 중
하나뿐이다. 기존 `scan_pair → scan_one`에서 실제 결과를 판정하고 상위 실행기는 항상
rc1/GitHub FAILURE로 종료한다. 일반 실패를 기대한 실패로 간주하지 않는다.

| case | 실제 실행 경로 | 기대한 실패로 인정하는 조건 |
|---|---|---|
| `vulnerability` | 기존 fresh DB 준비·검증 → 기존 두 이미지 스캔 어댑터 | transaction-api 실제 rc10, 유효한 이미지/OS/Python report, HIGH/CRITICAL findings, BLOCK; 두 번째 스캔 미진입 |
| `scanner-error` | 검증한 OCI layout → 새 빈 캐시 + 기존 `scan_one` Trivy 호출 → 기존 nonzero 오류 처리 | 실제 rc1·`SCANNER_EXECUTION_ERROR`·원본 stderr의 `--skip-db-update cannot be specified on the first run`; DB 생성·두 번째 스캔·report 생성 없음 |

오류 회차의 **DB 다운로드를 의도적으로 생략**하는 test-only 분기는 기본값 false다.
전용 context 목적·workflow ref·case·Attempt1이 없으면 복사/스캔 전에 거절한다.
정상 TBS config/Source, 일반 `scan_pair`의 fresh DB 경로와 차단 정책은 유지한다.
어떤 입력 오류·DB 다운로드 실패·timeout·signal·예상 외 rc0/rc10도 오류 회차 성공이 아니다.

이 도구는 **과거 실제 앱 이미지에 현재 스캐너 어댑터를 적용하는 시험**이다.
현재 앱 Source의 Unit/Contract/Build를 재실행하지 않는다. 이미지 load/run/build,
Podman store 생성, Harbor Push, Kubernetes 변경 경로가 없다. Push/Deploy Job이 없는
구조이므로 실제 `skipped` Job이 있었다고 표시하지 않는다. 입증 범위는 공유 스캐너의
실패 전파·상위 Run 실패·Push/Deploy 미호출과 별도 GET의 이미지 유지다.
이 경계를 AC05에 수용하는 판단은 코드·입력 검토와 실제 증적 인수에서 구분한다.

## 고정 입력과 보존

정상 Runner 계정의 HOME 아래 `FDS_CICD_AppWorkflow.log3xwta/build-scan/`에 남은
[Run35683010080](https://github.com/321Team/infra-cicd/actions/runs/35683010080) 원본을 사용한다.
출처 Source는 `ec69b67067decf35abbfed0a6a99f3073132787b`다. 공급 경로·해시·Source CLI
덮어쓰기는 없다. 경로/소유자/symlink/hardlink/쓰기 권한/크기/열기·읽기 전후 상태와
복사본 해시를 확인한다. 새 private 회차 폴더에 복사하며 기존 파일을 수정하지 않는다.

| 파일 | 크기(bytes) | SHA-256 |
|---|---:|---|
| transaction-api.oci.tar | 93404160 | `218c57b0613dc37e9628745d3ae580886543902714bbb9ab6249b5ac90d1266f` |
| fds-engine.oci.tar | 74496512 | `a8895e245ea2a2f30aa106524bf858020fccc65a12fc543d26f3c09a87f04d1c` |

위 값은 PR227의 읽기 전용 서버 재계산과 일치한다. Trivy는 기존 root 소유 0755
`/usr/local/bin/trivy` 0.74.0, SHA-256
`d89bcc6510a267f11b773398cbf1be5520ce39f9e8b6633178c4487f05b7d791`을 사용하며 실행 전후
검사한다. 취약점 회차는 새 DB 기준이므로 과거 57/3 수치의 재현을 강제하지 않는다.
현재 DB에서 기대한 취약점이 사라지면 `NOT_CONFIRMED`다.

오류 회차의 stderr/호출 receipt는 private 폴더에 남고 Actions 로그에 출력하지 않는다.
해당 자식 프로세스 파일 크기는 1 MiB로 제한한다. 상한 도달·다른 원인·기존 DB 영향은
기대 결과로 인정하지 않는다. proxy/token/Trivy 환경변수와 기존 HOME 설정을 상속하지
않는다. 정상 회차에는 기존 제한된 proxy 전달 및 private report 정책을 적용한다.

## 순차 실행 절차 — 코드·입력 검토 및 새 회차 범위 확정 후

1. 최신 병합 SHA와 독립 리뷰, 위 두 입력·Trivy 해시·Python 3.11 이상을 대조한다.
   정상 계정 `server01_cicd-runner` / UID1000 / `cicd-runner01`만 사용하며 HOME 0700,
   최소 4 GiB 여유가 필요하다. 임의 sudo·권한 변경·입력 파일 교체는 하지 않는다.
2. 두 case **각각 새 Run 1회**, 대상 SHA·시작/회수 시간·담당자를 실행 기록에 고정한다.
   source-read/Harbor/Kubernetes Secret을 새 Workflow에 공급하지 않는다. 기본 checkout은
   contents:read이며 persist-credentials=false다. 이전 승인 회차는 재사용하지 않는다.
3. CICD active Run0·Runner idle/Worker0, 관련 앱/Harbor 시험 비중첩을 fresh 확인한다.
   기존 TBS concurrency와 `FDS_CICD_AppWorkflow.lock`을 공유한다. Push/Deploy 등 다른
   group의 작업까지 이 잠금 하나로 직렬화된다고 가정하지 않는다.
4. 승인된 control01 관리 경로에서 `tools/trivy/capture_app_state.py`의 정확한 바이트를
   전달·검증하고 `sudo -n /usr/bin/python3 -I -B <검증한 수집기 경로>`로 수집한다.
   기존 관리 인증만 사용한다. 인증이 없으면 중단하며 Runner에 admin.conf를 전달하지 않는다.
   수집은 fds-app Deployment/Pod/Service/EndpointSlice **4개 GET**뿐이다. 이 파일은
   Unit-negative 회차의 기존 수집기와 바이트가 같으며 secret/env 값은 출력하지 않는다.
   stdout/stderr·수집기 해시·계정·시각·rc를 각 `before` 디렉터리에 보존한다.
5. 사전 관측을 확인한 뒤 5분 이내에 해당 Run이 시작하도록 한다. 큐 대기로 오래 걸리면
   새 사전 관측이 필요하다. 제어가 불가능하거나 실행이 이미 시작됐다면 회차 결과를
   보존하되 이미지 유지 조건을 미확인으로 남긴다. 자동 dispatch/rerun은 하지 않는다.

   ```text
   gh workflow run app-scan-negative.yml --repo 321Team/infra-cicd --ref main -f case=vulnerability
   ```

6. Run/Attempt/Job/SHA, scanner rc/report/로그/receipt/해시, 상위 rc1과 GitHub FAILURE를
   확인한다. 실패 Run이라는 사실만으로 기대 결과를 인정하지 않는다. 프로세스 잔존과
   Worker0을 확인하고 종료 후 5분 이내에 동일 수집기로 `after`를 수집한다.
7. 원본 수신·Manifest 검증 후 로컬에서 다음 대조를 수행한다. JSON의 source/transport
   해시, 실제 GitHub Run 결과와 비중첩 기록은 별도 검토한다. 이 도구 자체는 네트워크나
   쓰기 없이 사본을 비교하며 독립 승인 도구가 아니다.

   ```text
   python -B tools/trivy/compare_scan_observations.py --before before.json --after after.json --result result.json --run-id <실제RunID> --workflow-sha <실제40자리SHA> --case vulnerability
   ```

   정상은 두 Deployment의 UID/generation/spec hash/digest/replicas/ready와 6개 Pod의
   UID/owner/node/imageID/containerID/ready/restart가 동일하고 회차 전후 시간 조건이 맞아
   `IMAGE_PRESERVATION=OBSERVED_UNCHANGED`가 나오는 것이다. ResourceVersion 자체의
   변화는 비교에서 제외한다. 누락·중복·not-ready·drift·다른 Run/case는 실패한다.
8. 취약점 회차의 결과와 잔존 상태를 정리한 후 같은 절차를 새 전후 관측과 함께
   `-f case=scanner-error` / 비교기 `--case scanner-error`로 한 번 진행한다. 첫 회차의
   예상 외 실패를 확인하지 않은 채 두 번째로 넘어가지 않는다.

## 중단·회수·완료 조건

새 checkout·고유 private evidence·Run ID claim을 사용한다. 같은 Run은 case를 바꿔도
재사용할 수 없고 Attempt2도 거절한다. claim/output은 자동 삭제하지 않는다.
이 시험은 컨테이너·운영 DB·Deployment를 만들거나 수정하지 않으므로 자동 rollback을
수행하지 않는다. 원본·새 캐시·report는 보존하고 추후 보존 정책에 따라 회수한다.

불일치·동시 작업·인증 실패·timeout·취소 시 HOLD다. 운영 이미지 복구가 필요하면
기존 승인 복구 담당자에게 인계하며 이 도구로 변경하지 않는다. 광범위 rm/prune,
DB 재사용, 보안 통제 해제, 성공으로 바꾸는 오류 무시는 하지 않는다.
SIGINT/SIGTERM은 실패 receipt 보존을 시도하지만 SIGKILL/호스트 중단은 이를 보장하지
못하므로 private 경로와 해당 프로세스를 별도 확인한다.

두 실제 실패 회차와 각각의 배포 이미지 유지 관측·독립 증적 검토가 있어야 AC05
인수를 검토할 수 있다. STATIC/MOCK/CI·코드 병합만으로 Runtime/AC05/티켓 Done을 선언하지 않는다.
