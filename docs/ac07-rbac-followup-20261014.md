# CICD AC07 RBAC·exec 거부 관측 보완

2026-10-14 KST / CODEX_ASSISTED / Lead 이권욱 / 독립 코드 검토 이하영.
대상: [CICD #11](https://github.com/321Team/infra-cicd/issues/11),
[P1-CICD-01 #16](https://github.com/321Team/fds-project-baseline/issues/16).

## 문제와 변경

AC07은 실제 Runner 자격증명으로 두 Deployment 권한을 허용하고 임의
Deployment·create/delete·Secret·RBAC·exec 권한을 거부해야 한다.
기존 `deploy_native_probe`의 13개 SSAR에는 RBAC와 exec 거부가 빠져 있었다.
정상 Deploy 성공이나 관리자 계정의 Role GET으로 이 빈칸을 채우지 않는다.

기존 allow 6 / deny 7에 RBAC·exec deny 12개를 추가한다. 정확한 요청은
[native probe 행렬](deploy-native-auth-probe.md#runner-요청과-판정)을 따른다.
25개의 SSAR가 모두 기대와 일치한 뒤 기존 두 named Deployment GET을 수행한다.
권한 변경·exec 연결·실제 금지된 변경 요청을 구현하는 것이 아니다.
고정 namespace/name/verb의 검사이며 가능한 모든 권한을 전수 증명하지 않는다.

`attributes`의 subresource 표현과 고정 SSAR allowlist만 확장한다. 실제 HTTP
허용 경로는 두 Deployment GET과 SelfSubjectAccessReview POST뿐이다.
예상 deny의 allow, 평가 오류, 응답의 namespace/name/group/subresource 불일치는
기존과 같이 STOP이며 후속 GET 및 PASS 생성을 차단한다. TokenRequest 900초·
fresh TokenReview·CA·정상 계정·메모리/pipe 전달·자연 만료 계약은 그대로 적용한다.

## 확인된 범위

- AC05 두 실제 스캐너 실패·전후 이미지 유지 증적은 [evidence PR229](https://github.com/321Team/fds-project-evidence/pull/229), merge `d0cd94fa60bc0fbf163cf34bc89af40186903269`에 편입됐다. 그 두 회차는 소진했으며 본 진단의 Token 발급 허용으로 사용하지 않는다.
- 2026-10-14 21:26 KST 기존 관리 경로의 GET에서 `ci-cd/cicd-runner-sa` UID `8f1ddfef-7bec-4b15-8cb7-c4b1a068a176`, automount=false, `fds-app/cicd-deploy-updater`의 두 named Deployment get/update/patch 규칙과 연결 Binding을 확인했다. 다른 namespace의 Binding 전체 또는 Runner Token의 실효 권한을 전수 확인한 결과는 아니다.
- 같은 시각 CP1 `server02` 기존 config의 SA GET 성공과 Runner `server01_cicd-runner`에서 API `10.1.93.109:6443`의 기존 CA·IP TLS 검증을 확인했다. SA Token으로 Runner 인증한 새 결과는 아니다. 서버 설정/파일 쓰기·토큰 발급은 없었다.
- 이 읽기 전용 점검 원본은 로컬 `deliverables/p1-cicd-ac05-acceptance-ac07-ac08-20261014/`에 보존한다. 증적 저장소 편입·독립 검토는 아직 수행하지 않았다. 이후 실행 직전에 다시 확인한다.

## 리뷰 후 실행 순서

1. 본 PR의 정확 HEAD 독립 코드 리뷰·필수 CI·병합을 확인한다. 병합 SHA에서 broker와 native source 7개를 묶고 전체 SHA256/Git blob을 고정한다. 과거 bundle에 이 파일만 덮어쓰지 않는다.
2. Lead가 원래 계정·SA UID·공개 CA·접근 경로·비중첩·실행 기한과 TokenRequest 최대 한 번을 기록한다. 다른 세션의 Runner·Harbor·앱 변경과 진행 중 작업을 확인한다. 과거 window release는 현재 비중첩 증거가 아니다.
3. ansible01의 기존 `server01_ansible` 계정에서 [기존 broker 기본 preflight 명령](deploy-native-auth-probe.md#source-bundle-계약)을 실행한다. 기본 모드에서는 토큰을 발급하지 않는다. CP1 `server02`와 Runner `server01_cicd-runner`의 기존 SSH trust/CA를 사용한다.
4. preflight 통과와 Lead 실행 결정 후 같은 bundle로 기존 `--execute --authorization-expires`를 사용한다. 한 Token의 25 SSAR + 두 named GET만 수행한다. 별도 root 전환·RBAC 확대·proxy/TLS 우회·배포는 없다.
5. 성공은 `NATIVE_AUTH_PROBE=PASS`, `RUNNER_TOKEN_AUTH=PASS`, 기대 25개 권한 관측, 두 UID 확인, 양쪽 cleanup ACK다. Token 값/원문 TokenReview/Authorization 헤더는 로그·argv·env·Artifact에 남기지 않는다. 공개 결과·실행 SHA·명령·시각·rc·원본 hash를 보존한다.
6. 기대 불일치·CA/UID/권한 변화·기한 만료·연결 단절 시 즉시 중단한다. 발급 불명 상태에서 다시 발급하지 않는다. 기존 broker의 자기 subprocess 종료와 논리 참조 정리, 자연 만료를 따른다. SA/Role 삭제나 권한 변경으로 복구하지 않는다. cleanup ACK 부재는 `NOT_CONFIRMED`로 기록한다.

코드/CI·preflight·실제 Runtime·증적 편입·AC07 인수는 각각 구분한다. Runtime 결과가
없는 현재는 AC07 PARTIAL, AC08 미완료다. 이 PR의 병합만으로 새 발급/Runtime을
실행하거나 AC를 체크하지 않는다.

## AC08 후속 설계 입력

AC08은 만료 Token의 **서버 인증 실패**, 새 단기 Token으로 복구, 로그/Artifact/
작업 디렉터리의 자격증명 잔존 부재를 요구한다. 기존 정상 Deploy의 900초 Token
소비·로컬 참조 해제는 부분 증거다. 현재 Credential/RunnerProbe는 만료 전에
클라이언트에서 막으므로 그 오류를 서버 401의 증거로 사용하지 않는다.

별도 독립 리뷰할 진단 후보는 기존 정상 인증 1회 → 실제 JWT/status 만료 뒤
기존 토큰으로 고정 named Deployment GET 1회(401만 기대) → 새 900초 이하
Token을 명시적으로 1회 발급하여 같은 GET 200 확인 순서다. 클럭 변경·기존
credential gate 해제·SA 삭제·운영 Token 교체로 재현하지 않는다. 만료 관측용
클라이언트는 test-only 경로로 분리하고 토큰을 메모리에만 유지해야 한다.

이 후보는 **계획이며 미구현/미실행**이다. 최대 2회 발급, 만료 대기/전체 기한,
API 서버의 실제 401 결속, 새 Token 동일 SA UID/audience·TTL 검증, 네트워크
실패/403/클라이언트 만료와 인증 실패 구분, 비밀값 출력 차단, 각 endpoint 정리,
허용된 로그/작업 경로의 잔존 검증을 먼저 구현·시험·독립 리뷰해야 한다.
금번 AC07 보완을 AC08 성공 또는 새 발급 두 번의 승인으로 취급하지 않는다.

## API 근거

- [Kubernetes v1.36.4 ResourceAttributes](https://github.com/kubernetes/api/blob/kubernetes-1.36.4/authorization/v1/types.go): resource와 subresource를 별도 필드로 결속한다.
- [공식 RBAC 권한 상승 방지](https://kubernetes.io/docs/reference/access-authn-authz/rbac/#privilege-escalation-prevention-and-bootstrapping): 일반 Role/Binding 쓰기와 bind/escalate 권한을 구분한다.
