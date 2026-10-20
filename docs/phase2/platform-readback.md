# #46 동일 자격의 플랫폼 관측·발급 인증 연결

`delivery_platform_readback.py`는 운영 인증 연결의 관측 부분과 기존 `OperationalProviders.platform` 접점을 구현한다. 실제 token·subject·서버값을 설정하지 않았고 기본 CLI STOP은 유지한다. 생성자는 네트워크 I/O를 하지 않는다.

## 관측 reader

`PlatformObservationReader`는 code-configured `ExpectedSubject(username, uid, groups)`와 같은 `BearerCredential` handle을 사용한다. CA 원본/해시·숫자 IP·API origin/SNI·TLS 정책은 기존 Sender에 결속하고, 다음 정확한 API만 허용한다.

| 동작 | 대조할 것 |
|---|---|
| SelfSubjectReview POST 2회 | API 서버가 인식한 username·UID·groups. 최초/최종 결과를 사전 채택 주체와 대조 |
| ClusterVersion GET 2회 | `config.openshift.io/v1`, 이름 `version`, `spec.clusterID`와 Binding 일치 |
| Namespace GET 2회 | `fds-app`의 UID·Active·삭제 미착수. 처음/마지막에 확인 |
| SelfSubjectAccessReview POST 12회 | 고정 6객체의 get/patch 각각. 정확한 group/version/resource/name/namespace/verb, `allowed=true`, denied 없음/false, evaluationError 없음 |

한 번의 `inspect`는 최대 18요청이며 모두 공유 `ReadRequest.deadline`과 각 요청 10초 상한을 소비한다. redirect·retry·DNS·proxy·credential 갱신·impersonation 헤더는 없다. Secret/TokenReview/TokenRequest·RBAC 쓰기·workload PATCH/DELETE 경로는 허용하지 않는다. SSR/SSAR POST는 권한을 부여하는 API가 아니다. 같은 credential·Binding·progress가 달라지거나 조회가 실패하면 중단한다.

`close()`는 이 reader/adapter의 재사용을 막고 로컬 token/handle/callback 참조를 해제한다. 외부 소유 handle을 폐기하거나 서버 token을 회수하는 동작·메모리 완전 소거의 보장은 아니다.

반환 `PlatformReadback`은 비공개 메모리 원본과 확인한 **필요 능력**의 관측이며 다음 값은 false로 고정된다.

- `execution_authorized`
- `full_platform_authenticated`
- `least_privilege_verified`

SSAR allow는 추가 권한이 없다는 증명이 아니다. 서버가 현재 주체를 인식했다는 결과도 token issuer/audience·서버 만료를 증명하지 않는다. 이 타입은 `PlatformEvidence`로 직접 사용할 수 없다. 조회는 순차적이며 원자 snapshot이 아니고, 최종 관측 이후의 RBAC/대상 변경을 막지 않는다. 실제 요청의 인가는 서버가 계속 수행한다.

SSAR의 core API `group`은 Kubernetes의 `omitempty` 직렬화로 응답에서 생략될 수 있다. 이 값만 누락/빈 문자열을 동일하게 정규화한다. non-core group이나 namespace/name/version/verb의 누락·범위 확대, 다른 spec 필드는 그대로 거부한다. 모의 응답도 이 실제 직렬화 형태를 반영했다.

## 발급 인증자와 기존 platform 포트 연결

`ReadbackCheckedPlatformAdapter`는 **별도로 실제 원본을 인증하는 `issuer_resolver`가 있을 때만** 연결할 수 있다. 그 구현은 아직 제공하지 않는다. JSON/해시/타입/직접 decode한 JWT claim을 인증 결과로 바꾸는 loader나 기본값은 없다.

순서는 발급 인증 결과 → 동일 자격의 API 관측 → 발급 인증 결과 재조회이다. 같은 request·credential·Binding·CA·endpoint·principal·원본 receipt hash, issuer/audience·900초 요청·실제 발급/만료 구간을 대조하고 결과가 바뀌면 거부한다. 실제 credential 잔여는 관측 I/O deadline을 더 좁힌다. 조건을 통과해도 새 인증 결과를 합성하지 않고 **원래 인증자의 `PlatformEvidence`를 반환**한다.

따라서 실제 issuer/audience·발급 원본·서버 expiry·최소 RBAC를 인증할 운영 구현과 site 채택이 없으면 `OperationalProviders.platform`에 이 연결을 운영 활성화하지 않는다. 토큰 발급·권한 추가 없이 오프라인 접점 시험만 수행했다. 무한 대기 callback의 취소/독립 I/O 상한은 실제 인증자의 책임이며 전후 deadline 검사만으로 취소했다고 주장하지 않는다.

추가 조회 권한이 실제 credential에 있는지와 API 지원은 현장 미확인이다. 403/미지원이면 기본 STOP으로 해당 담당의 기존 준비 계약에 연결한다. 이 코드가 새 RBAC·전체 관리자 권한·기존 D-11d 재시험을 요구하거나 승인하지 않는다. producer 생성 계보·finish 인증·실제 배포 AC는 별도 잔여다.

## 오프라인 검증

`python -B -m unittest discover -s tools/phase2 -p test_delivery_platform_readback.py -v`

fake TLS socket/clock·합성 API 응답만 쓰고 실제 socket/DNS를 차단한다. 주체·UID·기간·필요 권한·리뷰 응답의 변조/오류·다른 handle·namespace 교체·endpoint/CA/receipt 차이·불명확 결과를 검증한다. 기존 platform 포트에 연결해도 다른 provider나 workload sender를 호출하지 않는 시험을 포함한다.

공식 API 근거: [SelfSubjectReview](https://kubernetes.io/docs/reference/kubernetes-api/definitions/self-subject-review-v1-authentication/), [SelfSubjectAccessReview](https://kubernetes.io/docs/reference/kubernetes-api/definitions/self-subject-access-review-v1-authorization/), [OpenShift 4.22 ClusterVersion](https://docs.redhat.com/en/documentation/openshift_container_platform/4.22/html/config_apis/clusterversion-config-openshift-io-v1).
