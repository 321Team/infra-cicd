# #46 운영 구성 채택 — 2026-10-23

**확인된 기대 설정을 `delivery_fds_operating`에 채택했다. 전체 운영 SITE와 실행 승인은 아직 완료되지 않았다.** `delivery_job_site.SITE=None`을 유지하고 self-hosted job 예약을 차단한다. 새 입력 양식·서명·중복 승인 절차는 추가하지 않는다.

## 이번에 연결한 설정

| 설정 | 실제 채택·연결 | 원본과 한계 |
|---|---|---|
| Runner | `cicd-runner01 / server01_cicd-runner / UID 1000`을 `assemble_job`의 고정 기대값으로 사용 | [기존 workflow](https://github.com/321Team/infra-cicd/blob/aa64731c0265d6f0cdc1e14d012f43dafa28616b/.github/workflows/app-test-build-scan.yml)와 PR109 재환 담당 리뷰. 현재 프로세스 실측은 아니다. native driver의 실제 host/account/UID 대조는 유지한다 |
| 배포 대상·소스 | 기존 `delivery_fds_site_inputs`의 API·클러스터/namespace UID·CA·SA/UID·source `69e28c49`·rendered hash·2개 digest를 직접 재사용 | [제출 원본 정정](https://github.com/321Team/fds-project-baseline/issues/46#issuecomment-6339313808), 기존 MSA PR42 배포 묶음. 값 복사나 새 현재값 추정 없음. 대상은 기존 6객체이며 first-empty로 폴백하지 않는다 |
| 발급 담당 | `hayeoung`과 정정 댓글을 담당 지정 근거로 기록. 기존 TokenRequest issuer 진입점을 그대로 사용 | 10/22 `create serviceaccounts/token` 및 특정 SA token 권한 `yes`, `hayeoung-admin` RoleBinding 대조. **이름·과거 can-i는 현재 credential 인증이 아니다.** API 호출·토큰 발급·권한 추가 없음 |
| 조립 진입점 | `build_configuration`·`build_token_issuer`는 기존 구현을 직접 참조하고, `assemble_job`만 고정 Runner를 기존 5역할/6객체 조립에 연결 | caller의 Runner 덮어쓰기를 받지 않는다. helper UID는 별도 명시 입력이며 1000으로 복사하지 않는다. 생성 중 callback·시계·소켓·원본 읽기 없음 |
| 상태 확인 | `delivery_job.py status`가 채택된 설정·미확정 연결·실행 중 생성값을 분리 출력 | `BLOCKED / dispatch_configured=false / execution_authorized=false / runtime=NOT_RUN` 유지. 보고서 수정으로 gate가 열리지 않는다 |

운영 설정의 선택 근거와 실제 실행 증적을 구분한다. source/rendered 값은 검토한 배포 입력의 선택이며 현재 live spec의 증명이나 최근 수동 변경 수용은 아니다. 실행 때 native revision·원본/control·baseline drift 검증을 그대로 거친다.

## 미확정 입력과 남은 중앙 연결

아래 세 묶음은 새 제출 양식이 아니다. 이미 제출한 자료를 먼저 재사용하고, 없는 사실 또는 아직 미구현인 연결만 보완한다.

| 묶음·담당 | 아직 필요한 것 | 완료조건 |
|---|---|---|
| **1. 전송·배치 — 권욱**, 재환/하영은 기존 배치 사실 확인 | SSH 실제 방향·host/key/known_hosts 경로·원격 helper 경로와 UID/netns, 인증된 READY 전달, input/source/persistent store 위치, 현재 endpoint | 실제 기존 배치 근거를 `SshBridgeConfig`, source route 및 job 조립에 명시한다. 새로운 계정·키·권한·파일 위치를 이미 존재하는 값으로 기입하지 않는다. 양 호스트 monotonic 값을 직접 공유하지 않는다 |
| **2. 원본/control/finish — 권욱 구현·채택**, 담당자 기존 원본 재사용 | approval/platform/producer/finish/baseline 5역할의 원본·control pin, 비공개 보관 인증·materializer, 실행 및 종료 범위 | 기수신 10/10 플랫폼 원본·생산자 원본과 실제 승인 의미를 연결한다. 역사적 창 C 수동 GO를 #46 자동 배포 GO로 전환하지 않는다. 원본의 해시 일치만으로 의미·권위를 채택하지 않는다 |
| **3. 기존 6객체 기준선 — 권욱 채택**, 하영 원본 위치·재환 previous 근거 | 변경 후 full raw, UID/spec/managedFields/owner와 이전 이미지 | 기존 저장 원본이 있으면 위치와 버전만 연결한다. 없을 때만 후속 승인된 현장 수집으로 확보한다. `03-after.txt` UID/RV 표·`06-pods.txt`·#38 SCC/UID JSON은 full baseline 대체물이 아니다 |

누락값을 책임자에게 모두 새로 생산시키는 요청이 아니다. 2번의 코드/의미 연결은 권욱 업무이고, 1·3번은 기존 자료에 해당 사실이 있는지 담당자 확인이 필요한 부분이다. 새 API/SSH/수집을 이 PR에서 실행하지 않는다.

다음은 **사전 누락 입력이 아니다**: 미래 native run/attempt, READY nonce, 발급 응답·fingerprint·iat/exp, 실제 finish 관측 원본. 기존 코드가 해당 실행 때 생성·검증한다. 반면 생성 위치·범위·원본 해석 방식과 control 정책은 실행 전에 연결해야 한다. 사후 발급 응답 해시를 사전 `Binding.platform_receipt_sha256`에 역대입하지 않는다.

## 확인 방법과 인수 범위

저장소 루트에서 오프라인 상태만 확인한다.

```sh
python3 -B tools/phase2/delivery_job.py status
```

현재 정상 결과는 종료 코드 **2**, `P2_JOB_UNCONFIGURED`와 `operating_configuration.selection=PARTIAL`이다. 미확정 세 묶음이 보고되며 자격·실제 원본·환경변수·호스트를 조회하지 않는다. `--execute`를 붙여도 현재 기본 SITE는 활성화되지 않는다. 이 문서는 Runtime 실행 명령이나 GO가 아니다.

시험은 기존 SYNTHETIC 계획으로 실제 조립 타입, 기대 Runner 고정 및 native mismatch 차단, helper UID 분리, foreign target 거부, deferred materializer 무호출, 상태보고 무I/O 및 gate 불변을 확인한다. 기존 workflow/보안 경계·발급 권한·6객체 검증·claim/fence/finish 계약은 유지한다. 원복은 이 코드 변경을 되돌리는 것으로 충분하며 서버 자원 원복은 발생하지 않는다.

Claude Code·Grok·Antigravity에는 일반 구성 연결/상태 계약만 역할별 전달했다. 프로젝트 운영 메타데이터 전송은 자동 검토에서 거부돼 제공하지 않았다. 세 도구의 응답은 일반 설계 자문이며 이 소스·실행 원본의 외부 심층 검토나 독립 사람 승인이 아니다. Codex가 원본·코드·시험을 대조했다.

PR 병합 뒤에도 세 묶음 연결과 실제 실행 승인이 별도로 남는다. DB·Robot 유지 조건 및 **10/26 12:00 KST 최종 정리** 결정은 변경하지 않는다.
