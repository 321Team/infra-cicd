# #46 replicas 소유권 충돌 처리안

2026-10-24. **오프라인 검토안이며 소유권 변경·배포 Runtime GO가 아니다.**

## 확인한 상태와 이번 변경

[원본 수집](https://github.com/321Team/fds-project-baseline/issues/46#issuecomment-6366234985)의
10/23 22:39:26 KST snapshot에서 Deployment `transaction-api`, `fds-engine`의
replicas는 모두 0이고 `kubectl-patch / Update`가 해당 필드를 소유한다.
검토 렌더는 3, 기존 sender는 `fds-deployer`의 무강제 SSA다. 다른 소유자의 값을
변경하면 충돌한다는 [공식 규칙](https://kubernetes.io/docs/reference/using-api/server-side-apply/#conflicts)에
따라 이 상태의 직접 적용은 409가 예상된다. snapshot이 현재 live라는 주장은 아니다.

`ObjectReconciler`가 기존 baseline 인증·fresh 대조 후, claim·sender 생성 전에
replicas 변경의 알려진 충돌을 검사한다. 그러므로 Deployment 충돌 때문에 앞순서의
Service 두 개만 먼저 바뀌는 상황도 방지한다. 새 승인 gate나 서버 조회는 추가하지 않는다.

- 현재 값과 목표가 같으면 공동 소유 자체를 차단하지 않는다.
- 값이 다르면 `f:spec/f:replicas`를 주장하는 모든 entry가 같은 main-resource
  `fds-deployer / Apply / apps/v1`이어야 한다. 다른 Update/Apply·API 버전은 차단한다.
- 빈 spec 소유 집합은 보수적으로 잠재 충돌로 처리하고 잘못된 spec 소유 형태는 거부한다.
  반면 granular map의 `.` 표시나 template만의 소유를 replicas 소유로 확대하지 않는다.
- replicas는 실제 정수 범위여야 한다. bool·누락·문자열·음수·범위 초과는 거부한다.

이 검사는 **replicas에 한정된 보수적 사전검사**다. 전체 SSA 충돌 시뮬레이션이나 API
dry-run, admission·권한·가용성 검증이 아니다. 통과해도 다른 필드의 충돌과 경합은 남으며
기존 fresh drift·claim/fence·sender의 무강제 409 중단 처리를 유지한다.

## 2026-10-25 실제 인계 완료·새 baseline 채택

아래 과거 임시 manager/force 제안은 실행 절차로 사용하지 않는다. 실제 준비에서 기존
완전한 intent+replicas0의 무강제 공유를 확인하고, 사용자가 별도 승인한 좁은 정정안으로
old `kubectl-patch/Update/apps/v1/main`의 **replicas만 소유한 항목1개/앱**을 정리했다.
현재 UID·RV·generation·replicas0·전체 managedFields 배열 test를 건 JSONPatch만 사용했다.
전체 managedFields 초기화·다른 소유 필드 삭제·force는 수행하지 않았다.

서버 dry-run4·준비 쓰기4·단계별 readback을 마쳤다. 최종 replicas 소유자는 두 앱 모두
`fds-deployer/Apply/apps/v1/main`, 값0이며 기존 UID·generation·전체 spec·이미지/template와
Service2/Route2의 전체 객체·RV를 보존했다. exact GET6는 **18:15:03~18:15:04 KST**다.
[실제 인계·metadata 원문](https://github.com/321Team/fds-project-baseline/issues/46#issuecomment-6395884121)과
[새 baseline 연결](native-run-inputs.md#인계-후-실제-full6-선택--2026-10-25)을 사용한다.
원본6객체의 native 해석·기존 owned intent68/62노드·replicas 사전검사2건을 오프라인 대조했다.
이는 그 시각의 준비 결과이며, 다음 job의 fresh drift·현재 권한·무강제 SSA 검사는 유지한다.
실제 앱 기동·TokenRequest·배포·finish는 아직 수행하지 않았다.

## 과거 제안 기록 — 실제 실행에는 위 정정 결과 적용

자동 job의 0→3 변경을 검증하려면 먼저 **앱을 0으로 유지하면서** replicas 소유권만
정리한다는 인계 전 과거 초안이었다. 아래 절차는 실제 운영에서 실행하지 않는다.

| 순서·담당 | 필요한 조치와 확인 |
|---|---|
| 1. 권욱·하영 | 현재 UID·resourceVersion·replicas0·필드 소유자와 외부 scale writer 비중첩을 확인한다. 원본과 다르면 기존 계획을 그대로 실행하지 않는다. 현재 fds-deployer가 소유하는 전체 apply intent를 복구 자료로 확보한다 |
| 2. 하영 실행·권욱 검토 | **기존 fds-deployer와 별개의 임시 manager**로 두 Deployment 각각의 replicas0만 담은 SSA 인계 payload를 검토한다. 필요 시 그 필드만 force로 이전하는 별도 유지보수 범위다. 정확한 대상·현재 버전 조건을 묶고, spec/이미지/Pod template/replicas0가 유지되는지 readback한다 |
| 3. 하영 실행·권욱 검토 | fds-deployer가 기존에 소유하던 필드를 모두 보존한 **완전한 기존 apply intent + replicas0**를 무강제로 적용해 공동 소유로 만든다. 전체 live JSON을 그대로 apply하지 않는다. 이는 검토 렌더3의 hash/binding을 재사용할 수 없는 별도 유지보수 payload이며 변경 범위 검토가 먼저다 |
| 4. 하영 실행·권욱 대조 | fds-deployer가 replicas0의 Apply 소유자라는 readback이 있을 때만 임시 manager의 최소 intent에서 replicas를 생략해 그 소유권을 놓는다. 최종 값0·fds-deployer 소유·다른 spec 불변을 확인한다 |
| 5. 권욱 | 인계 후 6객체 원본을 받으면 기존 decoder로 대조하고 새 hash/owner baseline을 명시 채택한다. 그 뒤에만 별도 승인된 job의 0→3 실행을 준비한다. 기존 snapshot 상수나 control을 fresh GET으로 자동 교체하지 않는다 |

이전은 [공식 협조적 이전 절차](https://kubernetes.io/docs/reference/using-api/server-side-apply/#transferring-ownership-between-managers)에
근거한 운영 제안이다. 실제 서버의 dry-run/readback은 미실행이며, payload·현재 소유 필드
대조와 작업창 승인이 갖춰져야 한다. 새로운 helper나 자동 force 실행 기능은 만들지 않았다.

**피해야 할 축약:** 기존 fds-deployer로 replicas-only SSA를 보내면, 그 manager가 이전에
소유했지만 이번 intent에서 생략한 필드가 제거·기본화될 수 있다. 전체 배포 manifest에
`force=true`를 붙이거나 managedFields를 삭제하는 방식도 채택하지 않는다.

**부분 실패:** 어느 단계든 0/UID/필드 불변이나 RC·readback이 불명확하면 후속 배포를
중단한다. 공동 소유자가 확인되지 않았는데 임시 manager의 replicas를 생략하면 기본값으로
바뀔 수 있으므로 자동 정리하지 않는다. 앱0 유지·현재 원본 보존 후 해당 단계의 복구를
판단한다. 임의 재시도·소유권 역변경을 복구 성공으로 가정하지 않는다.

대안은 기존 수동 경로에서 먼저 3으로 올린 뒤 같은 값 baseline을 다시 검토하는 방식이다.
별도 앱 시작 GO가 필요하고, 이 경우 #46 job이 0→3을 수행했다는 증거가 되지 않는다.
현재의 무인 앱0 유지 조건을 바꾸는 방식이므로 기본 권고안으로 선택하지 않았다.

## previous·SSH·control/finish와 남은 범위

PR129 previous는 승인된 두 이미지로 명시 선택했으며 다음 target과 동일하다. 따라서 다음
검증은 **같은 digest의 재배포/replicas 변경**이고 다른 이미지로 전환·복귀하는 rollback
시연으로 기록할 수 없다. 실제 rollback 검증·승인은 별개다. PR129 리뷰의 비차단 권고에
따라 previous 상수를 독립 공급 프로필 `IMAGES`와 대조하는 시험도 추가했다.

SSH/helper/READY의 기존 경로·key·known_hosts·실행 UID/netns 및 배치 위치는 새 회신이
없었다. 이후 사용자 요청으로 [저장소 교차 대조](operating-site-adoption.md#저장소의-기존-ssh-값-교차-대조--2026-10-24)를
진행해 기존 관리 경로의 key/known_hosts까지 확인했다. 남은 issuer/helper 배치와
원본 5역할 grant/control·보관 인증·materializer·finish 범위는 기존 운영 결정과
연결이 필요하다. 이미 존재하는 API를 감싸는 wrapper나 임의 경로·grant를 추가하지 않았다.
`SITE=None`, 기본 실행 차단·원본/control 앞뒤 인증·finish/fence 계약을 유지한다.

검증은 SYNTHETIC 원본으로 충돌/공동소유/동일값/타 필드·map marker/잘못된 값과
전체 provider에서 claim·sender·Service PATCH 0건을 검사한다. 실제 원본의 오프라인
대조와 합성 fixture 시험은 구분한다. 실제 API·SSH·발급·필드 인계·배포는 미실행이다.
코드 원복은 이번 preflight·시험·문서 변경을 revert하면 되고 서버 원복은 발생하지 않는다.
DB·Robot 유지 및 10/26 12:00 KST 최종 정리 기한은 변경하지 않는다.

## 오프라인 0 유지용 intent 후보 준비

기존 렌더 `3c8b6fe1…73516`과 원본 snapshot `e1022708…66c2a6f`를 오프라인 대조하여
**기존 렌더의 Deployment replicas만 3→0으로 바꾼 별도 검토 후보 2개**를 준비했다.
기존 fds-deployer Apply의 FieldsV1 소유 항목을 후보와 대조해 transaction-api 67노드,
fds-engine 61노드에서 필드 누락·관측 소유 값 변경이 없음을 확인했다. f:/k:/dot과 atomic
하위트리만 보수적으로 해석했고, container ports의 list key 비교에만 공식 TCP 기본값을
사용했다([Kubernetes API 정의](https://github.com/kubernetes/api/blob/master/core/v1/generated.proto)).
이는 candidate에 protocol 값을 새로 넣는 동작이 아니다. 모르는 selector/형태는 거부한다.

| 검토용 후보 | SHA-256 |
|---|---|
| transaction-api-zero-intent.review.json | `af061731ffd14f8d3b0aea2c1bbca2dc69fc6a807e3d114a03b9ae4491eb0916` |
| fds-engine-zero-intent.review.json | `49d4a615092c1f567782f962a1a383951b47d307db139ab619201437b218b71b` |

후보와 검사 자료는 중앙 로컬 비공개 검토 폴더에 두었다. Git에 현재 raw·candidate를
포함하지 않았고 source/binding/rendered hash를 교체하지 않았다. 별도 로컬 SYNTHETIC
시험8건으로 소유 필드 누락·값 변경·중복/타입 불일치 selector·잘못된 TCP 기본값 적용·
atomic 차이·다른 신원/소유자·미지원 tree/깊이 초과 거부를 확인했다.

이 결과는 현재 live·다른 소유자와의 충돌·서버 기본화/admission·SSA 성공이나 인계 완료의
증거가 아니다. 위 3단계의 **완전한 기존 intent 후보를 준비한 범위**이고, 실제 별도 작업창에서
현재 UID/버전·RC/readback을 확인해야 한다. 임시 manager용 실행 코드/force 명령은 만들지 않았다.
