# #46 현장 입력 연결 — 검토용 구현

이 변경은 검토된 Python 현장 코드가 **명시적 파일·Git source·영구 store와 기존 provider/CLI를 연결**하도록 한다. 기본 CLI를 운영용으로 설정하거나 실제 대상·자격·승인 원본을 만들지 않는다. 후속 [공개 원본 확인·익명 pipe 채널](source-authentication-channel.md)을 연결할 수 있지만 다섯 authority adapter 전체의 구현·검토는 별도 잔여다.

## 구성과 실행 위치

- 코드: `tools/phase2/delivery_site.py`. 호출 위치는 검토된 Linux/POSIX Runner의 저장소 루트다. 이번 작업은 해당 Runner에 접속하거나 설치하지 않는다.
- `SiteConfiguration`에 exact Binding, 입력 root와 render/contract의 서로 다른 파일명, 해당 commit을 보유한 Git root, 공통 영구 store root, 시간 예산과 finalize 요청을 **검토된 Python 코드가 직접** 제공한다. 세 root는 `..` 없는 명시적 절대 경로여야 하며 현재 작업 디렉터리에서 추정하지 않는다.
- 같은 실행의 메모리 `BearerCredential`과 기존 `AuthorityAdapters`를 그대로 넘긴다. 문자열 token 옵션, 환경변수 탐색, kubeconfig 자동 발견, JSON/JWT·plugin·approved 플래그 로더는 없다.
- 원본 승인·플랫폼·생산·baseline 채택·완료를 인증하는 adapter는 이미 갖춰져 있어야 한다. Python 타입·provenance 문자열·파일 해시 자체는 인증이 아니다. 이 helper는 raw 입력을 ApprovalEvidence/PlatformEvidence 등으로 변환하지 않는다.
- 실제값이 아직 없으면 기본 차단을 유지한다. 과거 관측이나 MOCK fixture를 현장 설정으로 채우지 않는다. 기존 source/contract/host·current digest를 불필요하게 다시 제출받거나 재승인하지 않는다.

## 입력을 소비하는 순서

1. 검토된 코드가 구성·자격·adapter를 메모리로 제공한다. `status`는 파일·Git·API 조회 없이 설정이 **미검증**임을 보고한다.
2. 실행 명령에서는 Binding·동작·구성 타입과 POSIX 지원을 확인한다. 입력 root와 store root는 존재해야 하고 자동 생성·chmod·복사하지 않는다.
3. 입력 root는 현재 UID/0700, 부모는 root 또는 현재 UID 소유이고 group/other 쓰기 불가여야 한다. render/contract는 서로 다른 단일 basename만 허용한다.
4. dir-fd 기반 no-follow/nonblock 열기 후 일반 파일·현재 UID/0600·단일 hardlink·1MiB 상한을 확인하고 읽기 전후 inode/metadata 및 정확한 Binding 해시를 대조한다. 링크·FIFO·장치·경로 이탈·교체·크기 초과는 중단한다.
5. `GitSnapshot`으로 Binding의 exact manifest commit에서 고정 checker/contract 두 blob을 읽는다. checker pin과 contract 일치를 확인하며 CRLF/LF 차이만 허용한다. worktree 수정이나 자동 fetch를 소비하지 않는다. 앱 이미지 build source와 manifest source를 혼용하지 않는다.
6. 실제 바이트와 같은 credential/adapters를 기존 `RunInputs`·`OperationalApplication`으로 전달한다. 이후의 fresh 인증·6객체 조회·소유/허용범위 비교·claim/journal/TLS/session은 기존 코어를 따른다.

Git blob/해시 일치는 무결성·고정 source 연결이다. clean render 생성·공급 계보·검토/승인의 진위를 증명하지 않는다. 각 인증 adapter의 I/O 기한·취소 구현 의무도 이 파일 로더로 해결되지 않는다.

이 확인은 준비 시점 기준이며, 사용 시점은 기존 경계가 다시 검증한다. 입력 root와 journal/store root는 서로 다르고 서로를 포함하지 않아야 한다. 부모 경로의 group/other 쓰기 허용과 symlink는 거부한다.

## 상태·실패·정리

- 단독 CLI 또는 현장 구성이 없는 호출은 rc2/BLOCKED다. `CONFIGURED_NOT_VERIFIED`와 preflight의 `PREFLIGHT_OBSERVED`는 배포 GO나 업무 인수가 아니다.
- 준비 오류는 고정 코드만 출력하며 경로·render·credential·원래 예외 메시지를 공개하지 않는다. 입력 준비 실패 시 application/session·claim·쓰기 sender를 만들지 않는다.
- 기존 application 한 개는 한 번만 실행한다. 실제 실행 전후의 권한 철회·만료·대상 변경·객체 drift 검사는 그대로다. 새 root로 재시도하거나 token을 자동 갱신하지 않는다.
- 실패 원본·partial 파일·journal/active/fence는 삭제·원복하지 않는다. 기존 recovery 절차와 실제 권한으로 판단하며 `force-unlock`/임의 재실행을 추가하지 않는다.
- Python에서 credential 참조를 버리는 것은 메모리의 안전한 소거를 보장하지 않는다. 이 helper가 IAM/RBAC 회수나 token 폐기를 수행한다고 표시하지 않는다.

## 오프라인 검증과 이식성

저장소 루트에서 추가 범위만 실행한다. fixture는 시험용 합성 자료이고 실제값·승인 증적이 아니다. 시험은 socket/DNS를 차단하고 API·sender·authority를 대역으로 사용한다.

```text
python -m unittest discover -s tools/phase2 -p test_delivery_site.py -v
```

- Windows: 순수 구성/status/차단 경로를 확인하고 POSIX 실제 파일 시험은 명시적으로 SKIP한다. SKIP은 PASS가 아니다.
- Linux hosted CI: 기존 P2 offline workflow가 새 시험을 포함해 실제 POSIX 파일 읽기·mode/owner/link/FIFO/크기·hash/source 오류를 확인한다. 운영 Runner/ROSA 검증이 아니다.
- 기존 provider/CLI 회귀는 영향 범위로 확인한다. 같은 기존 시험·원본·권한의 재제출을 담당자에게 요구하지 않는다.

## 실제 연결 전에 남는 것

실제 D22 image refs·clean render/계보와 인증 공급 receipt, 배포 자격·API CA/endpoint·issuer/audience/실제 수명·최소 RBAC의 인증 원본, 같은 자격의 6객체 baseline 채택, 현장 Git/store 경로와 시간 예산, 정확한 RUN/action/창의 유효 승인이 남는다. finish는 실행 후 별도 확인이다. 이미 연결한 정적 값과 이 실제 실행 조건을 구분한다.

이 PR의 코드/STATIC/MOCK/CI/리뷰는 Runtime·Deploy·복구/Health·전체 AC 인수 또는 별도 GO가 아니다. 독립 사람 리뷰를 받기 위한 Draft이며 자기 승인·병합하지 않는다.
