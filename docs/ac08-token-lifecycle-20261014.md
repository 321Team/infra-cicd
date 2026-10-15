# CICD AC08 단기 토큰 만료·재발급 진단

2026-10-14 KST / CODEX_ASSISTED / Lead 이권욱 / 독립 코드 검토 이하영.
책임: [CICD #11](https://github.com/321Team/infra-cicd/issues/11),
[P1-CICD-01 #16](https://github.com/321Team/fds-project-baseline/issues/16).

## 문제와 범위

기존 `Credential`은 만료 전에 요청을 차단한다. 이 클라이언트 오류는 API 서버가
만료 Token을 거부했다는 증거가 아니다. 새 `deploy_token_lifecycle.py`는 별도의
진단 경로에서 **유효 Token GET 200 → 만료 Token GET 401 → 새 Token GET 200**을
관측한다. 정상 Deploy/AC07의 만료 차단과 기본 실행 경로는 유지한다.

세 GET은 모두 `/apis/apps/v1/namespaces/fds-app/deployments/transaction-api`다.
Deployment 쓰기, exec, RBAC 변경, Harbor Push, Pipeline dispatch는 없다.
CP1의 기존 TokenRequest/TokenReview 경로와 기존 정상 계정만 사용한다.
TokenRequest 명령은 회차 전체에서 최대 2회이며, 각 CP1 세션은 기존 한 번 제한을
유지한다. 첫 401의 시각·본문·UID/Token 관측 연결을 확인하기 전에는 두 번째
CP1 세션을 시작하지 않는다. 발급 응답을 잃어도 해당 시도는 소진한다.

이 코드는 **Runtime 및 보안 통제 코드**다. 순수 GET Pre-check 예외로 운영 서버에
적용하지 않는다. 정확 HEAD의 독립 리뷰·필수 CI·병합과 Lead 실행 판단이 선행한다.
과거 AC07의 한 번 발급 허용이나 이 PR 병합을 새 두 번 발급 허용으로 재사용하지 않는다.

## 고정 대상과 사전 확인

| 항목 | 기준 / 실행 직전 확인 |
|---|---|
| 중계 | ansible01 `server01_ansible`, 기존 SSH trust 및 명시 key |
| 발급·검증 | CP1 `server02`, 기존 관리 config로 SA GET 및 TokenRequest/TokenReview |
| 소비 | cicd-runner01 `server01_cicd-runner`, root 전환 없음 |
| API | `10.1.93.109:6443`, IPv4 고정, IP TLS 검증, 환경 proxy/redirect 미사용 |
| CA SHA256 | `5e4462b48b67aa3c452119f02578feb5b2ace358782c9b050ba0ad2ea951a49c` |
| SA | `ci-cd/cicd-runner-sa`, 관측 UID `8f1ddfef-7bec-4b15-8cb7-c4b1a068a176`, automount=false |
| Token | 기존 audience, TokenRequest 900초, 실제 JWT/status와 fresh TokenReview 일치 |
| 비중첩 | Runner/Harbor/앱·RBAC·인증·시계 변경 시험이 진행 중인지 새로 확인 |

위 UID는 AC07 관측 기준이며 현행성 보장은 아니다. UID·CA·권한·신뢰 경로가 달라지면
자동 갱신하지 않고 중단한다. 실행 전후 두 Deployment와 6개 Ready Pod의 UID,
spec/image/imageID/restart 및 관련 Role/Binding을 기존 수집 절차로 비교한다.
서버 사용 가능 통보와 실제 실행 직전 비중첩 확인을 구분한다.

## 시간·비밀정보 계약

- 최소 시작 여유는 `900 + 75 + 90 = 1065초` 초과다. 준비·회수를 포함해 **25분**을 확보하고, 명시한 UTC 종료 시각을 broker/양 endpoint에 동일하게 전달한다. `Window`의 최대 1시간 제한은 유지한다.
- JWT 만료 뒤 75초까지 wall clock과 monotonic clock을 모두 확인한다. 두 시계 경과 차이가 5초를 넘거나 역행하면 중단한다. 시계를 변경해서 시험하지 않는다.
- Kubernetes v1.36.4 ServiceAccount 검증은 JWT 라이브러리의 기본 1분 leeway를 사용한다. 75초 대기는 이 경계를 넘기 위한 여유이며 성공 판정 자체가 아니다. 실제 TLS 응답 Date가 JWT 만료+60초 이후이고, 401 및 `v1/Status`, `Failure`, `Unauthorized`, code=401이 모두 일치해야 한다. 403/429/5xx/timeout/redirect는 인증 만료 성공이 아니다.
- 각 요청은 전체 10초 이내, 응답 1MiB 이하, 재시도 없음이다. 복구 GET은 첫 GET과 같은 Deployment UID를 요구한다. 두 번째 Token은 같은 SA UID, 첫 JWT 만료 이후 발급 시각, fresh TokenReview를 요구한다.
- Token은 private SSH stdin/stdout pipe와 프로세스 메모리에만 전달한다. argv/env/공개 JSON/파일/Artifact에 쓰지 않는다. 진단 Token을 가진 프로세스는 core dump와 dumpable을 끈다. 이 프로세스 설정은 종료와 함께 사라지고 호스트 설정을 바꾸지 않는다.
- 만료 대기는 15초 heartbeat와 broker ACK로 연결 생존을 확인한다. 연결 단절·ACK 부재·전체 기한 부족 시 참조를 해제하고 종료한다. 첫 Token은 만료 GET 직후, 새 Token은 복구 GET 직후 논리 참조를 해제한다. Python 메모리의 물리적 zeroization이나 swap/전체 파일시스템 무잔존을 주장하지 않는다.

## 검토·실행 순서

1. 최신 독립 승인 SHA와 CI, 병합 결과를 확인한다. 같은 병합 commit에서 broker `transfer_native_probe.py`와 아래 8개 source를 가져와 별도 공개 bundle/manifest로 고정한다. 기존 bundle에 일부 파일을 덮어쓰지 않는다.
2. bundle은 기존 [source 계약](deploy-native-auth-probe.md#source-bundle-계약)을 따르되 schema를 `P1-CICD-token-lifecycle-source-1`로 바꾼다. files는 `deploy_token_lifecycle.py`, `deploy_native_probe.py`, `deploy_cp1_access.py`, `deploy_binding.py`, `deploy_credential.py`, `deploy_transport.py`, `deploy_adapter.py`, `delivery_gate.py` 정확히 8개다. UTF-8 single-line JSON + LF, commit SHA, 각 Git blob, 전체 SHA256을 검증한다. broker는 bundle 밖에서 같은 commit 바이트/SHA256을 별도 확인한다.
3. 정상 계정의 새 전용 0700 폴더/0600 파일로 공급한다. 기존 SSH known_hosts/keys/CA를 변경하거나 인증서 검증을 끄지 않는다. source 승인 자체를 도구가 조회하는 것은 아니므로 실행자가 정확 SHA와 manifest 일치를 확인한다.
4. ansible01에서 아래 기본 preflight를 실행한다. CP1 SA/권한·Runner source/CA·정상 계정 확인 후 종료하며 발급 명령은 0회다. `$BROKER`, `$BUNDLE`, `$BUNDLE_SHA256`, `$SOURCE_SHA`, `$CP1_KEY`, `$RUNNER_KEY`는 위에서 확인한 **공개 경로/해시**다. Token 값을 변수에 넣지 않는다.

   ```sh
   /usr/bin/python3 -I -B "$BROKER" --token-lifecycle \
     --bundle "$BUNDLE" --bundle-sha256 "$BUNDLE_SHA256" --source-sha "$SOURCE_SHA" \
     --cp1-key "$CP1_KEY" --runner-key "$RUNNER_KEY"
   ```

5. Lead가 동일 SHA/bundle·두 번 발급 상한·실행 종료 시각·비중첩을 기록한 뒤 같은 명령에 다음을 추가한다. `$AUTHORIZATION_EXPIRES`는 실제 결정한 UTC ISO8601 시각이다. 실행 명령을 재입력하여 자동 반복하지 않는다.

   ```sh
   --execute --max-token-requests 2 --authorization-expires "$AUTHORIZATION_EXPIRES"
   ```

6. 공개 출력·실행 명령·KST/UTC 시작종료·rc·실행 SHA/bundle hash를 원본으로 보존한다. 원문 TokenRequest/TokenReview 응답, SSH private pipe, Authorization 헤더를 tee/디버그/셸 추적으로 수집하지 않는다.
7. 전후 앱·RBAC 상태와 종료 프로세스·아래 잔존 검증을 회수한다. 증적 저장소 독립 리뷰와 Lead AC08 인수는 Runtime 판정 뒤 별도로 남긴다.

## 판정과 잔존 검증

`TOKEN_LIFECYCLE=PASS`는 고정 GET의 200/401/200, 같은 SA/Deployment UID,
두 Token 메타데이터, 양 CP1 cleanup ACK, Runner cleanup ACK/정상 종료의
관측 성공이다. `TOKEN_REQUEST_COMMANDS=2`는 명령 횟수이며 서버 발급 audit 전수
확인과 다르다. `DELIVERY_GATE=NOT_VERIFIED`, `DEPLOY=NOT_EXECUTED`를 유지한다.

**AC08 전체 완료에는 별도 잔존 수집이 필요하다.** 공개 결과는
`GLOBAL_LOG_ARTIFACT_RESIDUE=REQUIRES_POSTCOLLECTION`으로 남긴다.

- 결과의 `local_source_workdir` 및 세 `sources[].workdir`를 허용 경로로 삼아, 정상 계정·0700/0600·정확 8개 공개 source·manifest 해시를 확인한다. 새 증적/결과 파일도 파일 목록·바이트·해시와 공개 필드 허용 목록으로 대조한다. Runner만의 source 변경 없음 결과를 세 호스트 전체 검사로 확대하지 않는다.
- broker와 세 endpoint의 종료·잔존 진단 프로세스 0개를 확인한다. 이번 시험은 Actions 실행/Artifact 업로드가 없으므로 기존 다른 회차 Artifact를 이번 무잔존 증거로 재사용하지 않는다. 이번 수집 경로/공개 로그에 credential/JWT/Authorization 값이 없는지 값 출력 없이 검사하고 범위를 기록한다.
- 경로 접근 실패, 로그/Artifact 목록 누락, 공개 파일 예상 외 추가, cleanup 미확인은 `PARTIAL/NOT_CONFIRMED`다. Token 값 자체를 저장하거나 광범위 grep 결과를 출력해 잔존 증거를 만들지 않는다. 전체 디스크·메모리의 무잔존 주장이 아니다.

## 중단·복구

예상 외 HTTP, TokenReview/UID/CA/clock/source drift, 기한 부족, cleanup ACK/SSH 종료
실패에서 후속 발급·GET을 중단한다. 이미 보낸 발급 명령은 소진 처리하고 자동 재실행하지
않는다. 자기 SSH subprocess/메모리 참조만 정리하고 Token은 자연 만료시킨다.
발급·정리 여부가 불명하면 그 상태와 완료한 HTTP까지만 기록한다. SA/Role/Binding
삭제, 보안 설정 해제, 운영 Token 교체, Harbor 재시작, VM·물리 호스트 종료는 복구에
포함하지 않는다. 상태 보존·원인 확인 후 새 회차는 별도 Lead 판단으로 결정한다.

## 근거

- [Kubernetes ServiceAccount Token](https://kubernetes.io/docs/concepts/security/service-accounts/): 수명·audience와 TokenRequest 계약.
- [v1.36.4 ServiceAccount claims 검증](https://github.com/kubernetes/kubernetes/blob/v1.36.4/pkg/serviceaccount/claims.go) 및 [동일 버전 JWT validation](https://github.com/kubernetes/kubernetes/blob/v1.36.4/vendor/gopkg.in/go-jose/go-jose.v2/jwt/validation.go): 실제 만료 검증과 기본 1분 leeway. 실행 전 실제 API 버전도 재확인한다.
- [인증 문서](https://kubernetes.io/docs/reference/access-authn-authz/authentication/): 잘못된 Bearer Token과 HTTP 401. 본 코드는 실제 응답을 요구한다.
- [AC07 설계 기록](ac07-rbac-followup-20261014.md#ac08-후속-설계-입력)은 당시 미구현 계획이다. 본 문서는 그 후속 구현 후보이며 Runtime 성공 기록이 아니다.
