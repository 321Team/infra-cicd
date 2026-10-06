# CP1 TokenReview·실패 dry-run 및 Runner TLS 사전 확인

2026-10-06 KST / AI usage: CODEX_ASSISTED.
후속 기준 main e9b0d40d9c8c7f1cc2d68f25193cd5a63d8711e1(PR59 승인 병합).
PR59 리뷰5426333521의 discovery 잘림과 CP1 인증 경로 미확인을 후속 처리한다.
이 변경의 코드 승인과 실제 Deploy/Runtime 승인은 별개다.

## 이번 구현과 실행 경계

tools/delivery/deploy_cp1_access.py는 기존 CP1의 kubectl 인증 경로를 사용하는 backend다.
capture_deploy_access.py는 세 가지 모드만 실행한다.

| 모드 | 실행 호스트 | 허용 동작 | 관측의 한계 |
|---|---|---|---|
| runner-tls | cicd-runner01 / server01_cicd-runner / UID1000 | numeric IPv4 10.1.93.109:6443 TCP·IP hostname TLS handshake | HTTP/SA 인증·RBAC·Deploy는 검증하지 않음 |
| cp1-review | k8s-control01 / server02 | 최신 named SA GET + 주입 token의 v1 TokenReview POST | token 발급/Runner 주입·Runner 실제 인증·Runtime wiring 미실행 |
| cp1-negative-test | k8s-control01 / server02 | 두 named Deployment GET, 고정 UID 실패 test-only server dry-run, 이후 GET | 실 API의 409/422 형태 관찰용. 기존 transport 판정은 변경하지 않음 |

아직 실제 호스트에서 실행한 결과는 없다. Mock subprocess/socket 시험을 실제 TLS·token·배포
증거로 쓰지 않는다. token 발급·갱신·취소, Secret/RBAC 변경, kubectl 설치, 이미지 PATCH,
관리자 kubeconfig/token의 Runner 전달, Deploy Workflow 연결은 구현하지 않았다.

## CP1 기존 인증 경로

backend는 POSIX 호스트/계정을 먼저 확인한다. 설치된 절대 경로 kubectl, 단일 KUBECONFIG
(없으면 ~/.kube/config), 공개 CA의 파일/상위 경로를 확인한다. 상위 경로는 root/현재 UID 소유,
group/other write 금지, symlink 금지다. 일반 파일은 hardlink도 거부한다.
CA는 별도로 확인한 SHA256 및 기존 transport.context_checked의 PEM/TLS 설정으로 확인한다.

기존 선택 config는 고정 API https://10.1.93.109:6443, TLS 검증 해제 없음, 다른 TLS name/
proxy 없음이어야 한다. 현재 구현은 client certificate/key 인증만 지원한다. embedded 인증
자료는 config 자체의 해시로, 외부 cert/key 파일은 보호 경로와 해시로 변경을 감시한다.
exec/auth-provider/token/기본 인증/impersonation 설정, 여러 kubeconfig 병합은 거부한다.
현장 metadata 관측과 실제 인증 결과는 PR의 실행 기록에 구분한다. unsupported 결과가 나면 기존 설정을 바꿔 우회하지 않는다.

매 호출 전에 선택 config를 다시 대조하고 API/CA/IP TLS name/context/request timeout를 명시한다.
proxy 환경을 제거하고 KUBECTL_KUBERC=false, TokenReview는 --v=0으로 호출한다.
config·CA·외부 인증 파일·kubectl 속성을 호출 전후 대조한다. 변경/오류가 나면 객체를 중단하고
자동으로 다시 호출하지 않는다. 신뢰 UID가 파일을 악의적으로 바꾸는 상황을 격리하는 sandbox는 아니다.

TokenReview token은 stdin에만 넣는다. argv/env/임시 파일/영수증에 넣지 않는다.
반환 응답은 Credential의 exact token/audience/SA UID/group 검증에 사용한 후 출력하지 않는다.
TTY 확인과 getpass.GetPassWarning 오류 전환으로 echo fallback 전에 중단한다.
raw TokenReview나 subprocess stderr를 PR/증적에 넣지 않는다. 관측은 token 없는 메타데이터만 출력한다.
TokenReview 요청 전 config 확인을 포함한 전체 예산은 caller가 준 min(10초, expiry 잔여) 이내다.
subprocess timeout 및 사후 monotonic 검사로 늦은 완료를 거부한다. 실행 중 메모리에 잡힌 출력의
수용 크기를 사후 제한하며 OS scheduling/종료 지연에 대한 실시간 보장은 아니다.
kubectl/client-go 내부 HTTP retry는 이 wrapper가 끄지 못하므로 request timeout도 명시한다.
wrapper 자체의 자동 재시도·token 재발급은 없다. 실제 kubectl 버전/로그 형태도 현장 확인 대상이다.

## 실패 test-only dry-run

두 서비스 각각의 실제 UID가 all-zero UUID가 아닌 것을 GET으로 확인하고, 아래 단일 연산만
--dry-run=server로 보낸다. add/replace/remove 연산과 이미지 값은 허용하지 않는다.

~~~json
[{"op":"test","path":"/metadata/uid","value":"00000000-0000-0000-0000-000000000000"}]
~~~

kubectl --v=8의 client-go v0.36.4 Request(verb/url)→Response(status)→Response Body
순서를 메모리에서 대조한다. 고정 API의 해당 named Deployment PATCH 및 dryRun=All만
대상이며 허용 query(dryRun/fieldManager/fieldValidation/timeout)는 단일 값이어야 한다.
대상 PATCH 전에 모든 기존 요청·응답 본문이 완료되어야 한다. 완료된 discovery의 본문은
잘림·비JSON 여부에 관계없이 PATCH 증거로 사용하지 않는다. Request/Response 행이 없는 구형·다른
로그 형식, 미완료 discovery, 대상 구간의 interleave·중복 PATCH·미결속 body는 STOP이다.
이름이 같은 Status만 검색해 다른 요청의 실패를 성공 관측으로 오인하는 방식은 사용하지 않는다.
대상 본문의 기존 "Response Body: {...}", quoted body="{...}", klog의 body=< 여러 줄 형식을 지원한다.
여러 줄 본문은 각 행의 선행 TAB 하나와 정확한 닫힘 행 " >"를 대조해 전체 블록을 소비한다.
discovery 본문 안의 Request/Response 문자열을 새 로그 이벤트로 처리하지 않는다.
닫힘/들여쓰기 누락과 대상 Status 잘림·비JSON은 STOP이다. apimachinery v0.36.4의 JSON
Encode는 마지막 LF를 추가하며, client-go가 사용하는 klog v2.140.0은 LF가 있는 본문을
여러 줄 블록으로 기록한다. 단일 행 fixture만으로 현장 형식 수용을 증명하지 않는다.
raw stderr/HTTP headers/request body/Status message/causes는 저장·출력하지 않는다.
하나의 v1 Status Failure, HTTP/body code가 동일한409/422, reason Conflict/Invalid만 수용한다.
대상 Status의 잘림/중복 JSON key/누락/다른 응답/예상과 다른 성공이면 static STOP이다.
관측 response_binding=PATCH_REQUEST_HTTP_STATUS_BODY와 고정 request_path를 남긴다.
이 결속은 신뢰된 kubectl stderr의 순서를 대조한 것이며 독립 HTTP capture나 서버 audit 증거는 아니다.
관측은 고정 Status 필드와 allowlisted details, 로그에서 복원한 본문 SHA256 및
status_body_hash_basis만 포함한다. LOG_BODY_DECODED는 단일 행 로그에서 복원한 값이고,
KLOG_NORMALIZED_TRAILING_LF는 여러 줄 행을 결합하고 마지막 LF 하나를 붙인 값이다.
klog는 원 본문의 마지막 LF 유무를 구별해 기록하지 않으므로 이 해시는 원 HTTP payload의
바이트 해시라고 주장할 수 없다. sanitized projection은 raw 원문 증적이나 전체 원문 해시
재검증을 대신하지 않는다.
실제 shape 수용을 검토하려면 필요한 비밀 없는 필드를 별도 검토해 확장해야 한다.

이후 Deployment UID와 canonical 전체 spec 해시를 재확인한다.
두 시점의 spec 일치는 중간 변경 부재를 증명하지 않는다. resourceVersion은 전후 값을 따로 기록하며
다른 controller의 status 변경으로 달라질 수 있다. spec 변화/조회 실패면 observation을 PASS로 만들지 않는다.
details 없는 422는 관측만 가능하며 기존 transport의 outcome unknown/수동 대조 판정은 유지한다.
각 API 단계는 config 확인을 포함하여 최대10초이며 두 서비스 probe는 여러 단계의 합이다.

## CP1 인증 경로를 먼저 확인

PR59 리뷰의 기존 root0600 admin.conf 경로는 server02가 sudo 없이 읽는 단일 client-cert
kubeconfig 전제를 충족한다고 볼 수 없다. 실제 사용자 경로가 있는지 아래 로컬 metadata 점검으로
확인한다. API/TokenReview/PATCH 호출·token 발급·파일 쓰기·sudo 호출이 없다.
관리 인증 값/전체 kubeconfig/stdout·stderr 원문을 출력하지 않고 field 존재 여부만 기록한다.
이 metadata 관측은 인증 성공이나 backend의 모든 CA/파일 drift 조건을 통과했다는 증명이 아니다.

**실행 위치: k8s-control01, server02.** 현장 실행 여부와 사용자 제공 metadata 관측은 PR 본문의 실행 기록에서 확인한다.
기존 승인된 계정의 읽기 전용 config 점검으로 실행할 수 있다. NO_READABLE_USER_KUBECONFIG/
UNSAFE_CONFIG_PATH/unsupported metadata면 그 결과에서 중단한다.
admin.conf chmod·복사·sudo wrapper 추가나 exec/token 인증 허용으로 우회하지 않는다.
기존 허용된 관리 경로와 backend 전제의 차이를 확인한 뒤 별도 구현/검토한다.

~~~bash
python3 - <<'PY'
import datetime as dt
import json
import os
import pwd
import shutil
import socket
import stat
import subprocess
from pathlib import Path

def require(ok, code):
    if not ok:
        raise SystemExit("STOP: " + code)

require(socket.gethostname().split(".")[0] == "k8s-control01", "HOST_MISMATCH")
require(pwd.getpwuid(os.getuid()).pw_name == "server02", "ACCOUNT_MISMATCH")
print("CHECKED_AT=" + dt.datetime.now(dt.timezone.utc).isoformat())
configured = os.environ.get("KUBECONFIG")
require(configured is None or (configured and ":" not in configured), "MULTIPLE_KUBECONFIG")
path = Path(configured) if configured else Path.home() / ".kube/config"
print("KUBECONFIG_PATH=" + str(path))
print("KUBECONFIG_READABLE=" + str(os.access(path, os.R_OK)).lower())
require(path.is_absolute() and path.exists() and os.access(path, os.R_OK),
        "NO_READABLE_USER_KUBECONFIG")
for node in (path, *path.parents):
    info = node.lstat()
    require((stat.S_ISREG(info.st_mode) if node == path else stat.S_ISDIR(info.st_mode))
            and info.st_uid in (0, os.getuid()) and not info.st_mode & 0o022
            and (node != path or info.st_nlink == 1), "UNSAFE_CONFIG_PATH")
binary = shutil.which("kubectl")
require(binary is not None, "KUBECTL_MISSING")
env = {k: v for k, v in os.environ.items()
       if k.lower() not in ("http_proxy", "https_proxy", "all_proxy")}
env["KUBECTL_KUBERC"] = "false"
try:
    result = subprocess.run(
        [binary, "--kubeconfig=" + str(path), "--v=0", "config", "view", "--minify", "-o", "json"],
        capture_output=True, timeout=10, env=env)
    require(result.returncode == 0, "CONFIG_VIEW_FAILED")
    data = json.loads(result.stdout)
    require(len(data.get("clusters", [])) == len(data.get("users", [])) ==
            len(data.get("contexts", [])) == 1, "CONFIG_SELECTION_INVALID")
    cluster, user = data["clusters"][0]["cluster"], data["users"][0]["user"]
    flags = {k: k in user for k in (
        "client-certificate", "client-certificate-data", "client-key", "client-key-data",
        "exec", "auth-provider", "token", "tokenFile", "username", "password",
        "as", "as-uid", "as-groups", "as-user-extra")}
    print(json.dumps({
        "api_server_matches": cluster.get("server") == "https://10.1.93.109:6443",
        "tls_verification_disabled": bool(cluster.get("insecure-skip-tls-verify", False)),
        "tls_name_supported": cluster.get("tls-server-name", "10.1.93.109") in ("", "10.1.93.109"),
        "proxy_configured": bool(cluster.get("proxy-url")),
        "auth_fields_present": flags,
        "CP1_AUTH_METADATA": "CAPTURED_NOT_AUTHENTICATED",
        "TOKEN_ISSUED": False, "DEPLOY": "NOT_EXECUTED"
    }, ensure_ascii=False, indent=2))
except SystemExit:
    raise
except Exception:
    raise SystemExit("STOP: CONFIG_METADATA_CAPTURE_FAILED") from None
PY
~~~

정상 수집은 CHECKED_AT/KUBECONFIG_PATH/READABLE와 CP1_AUTH_METADATA=CAPTURED_NOT_AUTHENTICATED다.
고정 API·TLS·단일 client-cert 조건을 결과로 판단한 뒤 공개 CA/별도 지문 수집으로 넘어간다.
불일치/예외 시 STOP만 전달한다. 설정·권한·캐시를 바꾸거나 전체 인증 파일을 전달하지 않는다.

## 실행 순서와 명령

1. 이 코드와 credential 보완을 독립 리뷰한 HEAD를 고정한다. 병합 후 달라진 main을 임의로 사용하지 않는다.
2. CP1 사용자 kubeconfig metadata를 먼저 확인한다. 지원되는 경로가 확인되면 공개 CA와
   SHA256을 신뢰된 CP1 조회 경로에서 따로 전달한다.
3. 그 공개 CA만 Runner에 옮긴 뒤 runner-tls를 실행한다.
4. 현장 kubectl 버전과 실제 로그 형식을 확인하고, 최신 독립 리뷰한 parser SHA로 CP1
   cp1-negative-test를 실행해 sanitized 409/422 형태를 확인한다. 필요하면 먼저 고정 API의
   무해한 GET을 --v=8로 메모리에서 수집해 형식만 대조한다. 원 stderr/headers를 저장·전달하지 않는다.
5. 실제 단기 TokenRequest 발급/주입/원본 정리 경로를 별도 검토한 뒤에만 cp1-review를 실행한다.
   기존 유효한 candidate가 없으면 이 도구 때문에 token을 새로 발급하지 않는다.
6. Runner의 실제 SA API 인증·Runtime wiring·최신 Deployment/Worker/APP 조건을 확인한 뒤 별도 Deploy 범위를 결정한다.

이미 준비된 **리뷰 승인 SHA의 checkout** 및 보호 경로 공개 CA를 사용한다.
아래 CODE_SHA/checkout/CA_SHA는 실제 확인한 값으로 채워야 하며 예시는 성공 증거가 아니다.

~~~bash
CODE_SHA='<이 PR의 독립 리뷰 승인 HEAD>'
checkout='<검토한 infra-cicd checkout 절대 경로>'
CA_FILE='<보호 경로의 공개 ca.pem 절대 경로>'
CA_SHA='<CP1에서 별도로 확인한 공개 CA SHA256>'
[[ "$(git -C "$checkout" rev-parse HEAD)" == "$CODE_SHA" ]] || exit 64

# Runner에서: token·kubectl 없이 TLS만 확인
python3 "$checkout/tools/delivery/capture_deploy_access.py" runner-tls \
  --ca "$CA_FILE" --ca-sha256 "$CA_SHA"

# CP1에서: 이미지 변경 없이 실패 test-only 요청 관찰
python3 "$checkout/tools/delivery/capture_deploy_access.py" cp1-negative-test \
  --ca "$CA_FILE" --ca-sha256 "$CA_SHA"

# CP1에서: 이미 발급된 candidate를 TTY에서 숨김 입력. 발급 명령 아님.
python3 "$checkout/tools/delivery/capture_deploy_access.py" cp1-review \
  --ca "$CA_FILE" --ca-sha256 "$CA_SHA"
~~~

Runner와 CP1은 각자 해당 호스트의 명령만 실행한다. stdout의 sanitized JSON만 보존한다.
성공·실패 모두 호스트/계정, 리뷰 승인 source SHA와 실제 checkout 대조, KST 시각,
실행 명령/RC, sanitized observation을 함께 기록한다. 필드 값이나 해시 하나로 Runtime 인수로 확대하지 않는다.
실패 시 코드·시각·호스트·source SHA를 기록하고 반복 실행하기 전에 원인을 확인한다.
--v=8 로그 전체나 관리자 인증 파일을 전달하지 않는다.

## 시간·credential 보완

PR58 리뷰의 clock skew 기준은 그대로다. CREDENTIAL_NOT_YET_VALID가 나오면 CP1/Runner의 UTC와
기존 chronyc tracking/sources 관측으로 iat/nbf와 offset을 확인한다. 증거 없이 허용 오차를 늘리지 않는다.
exp-iat≤3600은 검토된 직접 TokenRequest 단기 주입 경로의 제한이다.
kubelet projected token의 연장된 exp나 요청 TTL만으로 계산한 expiry를 대신 쓰지 않는다.
backend는 native SA 발급 경로/actual exp/Runner 인증을 스스로 증명하지 않는다.

close 후 public closed 값을 다시 False로 바꾸더라도 token()과 remaining()/context 재진입은
삭제된 token 참조를 재사용할 수 없다. 로컬 참조 해제는 서버 취소/파일·Secret 삭제/물리적
zeroization을 의미하지 않는다.

기존27개+PATCH 결속10개+여러 줄 회귀8개=45개 precheck 시험은 private POSIX 파일·변경/링크/권한 거부, 명령 scope, token stdin·오류 비노출,
config TLS/인증/선택 거부, fresh UID, timeout/중단, 실패 dry-run projection·spec 변화,
Runner TLS Mock과 TTY/local cleanup을 확인한다. PR59의 credential 재사용 회귀 시험 1개는 유지한다.
이번10개는 discovery 잘림/비JSON/다른 실패를 제외하고 대상 PATCH의 URL·dryRun·HTTP/body code,
미결속/중복/interleave·미완료 discovery·target Status 잘림·두 서비스 이름 결속을 확인한다.
여러 줄 회귀8개는 실제 trailing-LF Status, 원 마지막 LF의 불확실성과 hash basis,
여러 줄 discovery/본문 안 로그 문자열, 닫힘·TAB 누락·빈 블록·잘림·중복,
현실적인 URL의409와 CP1 negative_test 통합 관측을 확인한다.
전체 CI 수/결과와 최신 HEAD 리뷰 상태는 이 변경 PR에 기록한다. 실제 kubectl 현장 실행은 별도 증거다.

참조:
- [kubectl create](https://kubernetes.io/docs/reference/kubectl/generated/kubectl_create/)의 --raw/-f -
- [TokenReview](https://kubernetes.io/docs/reference/kubernetes-api/definitions/token-review-v1-authentication/)
- [client-go v0.36.4 Request/Response logging](https://github.com/kubernetes/client-go/blob/v0.36.4/transport/round_trippers.go)
- [client-go v0.36.4 response body logging](https://github.com/kubernetes/client-go/blob/v0.36.4/rest/request.go)
- [apimachinery v0.36.4 JSON Encode](https://github.com/kubernetes/apimachinery/blob/v0.36.4/pkg/runtime/serializer/json/json.go)
- [client-go v0.36.4 klog dependency](https://github.com/kubernetes/client-go/blob/v0.36.4/go.mod)
- [klog v2.140.0 multiline string serialization](https://github.com/kubernetes/klog/blob/v2.140.0/internal/serialize/keyvalues.go)
- [Known Issue와 Worker 확인](../handoff/deploy-prep-37331796555/known-issues.md)
