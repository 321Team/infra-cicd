# Harbor 초기화·정식 발급 Runbook v1.3

채택 방식: **임시 환경은 보존·격리하고 새 데이터·키·자격증명으로 정식 구성한다.** 초기화 요청을 이 방식으로 구현한다. 서비스 전환은 이하영, CA 운영·서명과 Runner Trust는 이권욱이 맡는다.

2026-09-15 현재: **신규 Root·Harbor Leaf 발급, 암호화 백업/파일 복구, Harbor 공개 발급물 수신·기존 키 일치 검증을 완료했다.** [실제 구성·증거 범위](implementation-state-20260915.md)를 기준으로 읽는다. 현재 요청 `harbor01-sn0wepv7`의 키·CSR·인증서를 보존하며 §1·§3·§4의 최초 생성/발급 절차를 반복하지 않는다. Harbor 서비스 적용은 미실행이다. §5.1의 실제 입력 경로를 수신 디렉터리에 맞췄으며 적용은 이하영 주관 HBR 범위다.

2026-09-10 CA 결정 변경: **기존 승인 CA는 폐기하고 새 개인키로 신규 Root를 발급한다.** 재사용·부재 확인 조건을 대체한다. 이 결정 이후 신규 발급은 완료했으며, 구 CA 지문·소비자 전환·폐기는 별도 미검증 항목으로 마지막 전환 절차를 따른다.

## 1. Harbor 키·CSR 최초 생성 절차 — 현재 요청은 재실행하지 않음

개인키는 `/etc/harbor/pki/requests/harbor01-<임의값>/harbor01.key`에 root 전용으로 생성한다. 기존 키·인증서·서비스·데이터를 변경하지 않는다. 재실행하면 새로운 요청이 생기므로 성공한 요청의 키·CSR 쌍을 계속 사용한다.

ZIP과 sidecar를 `server08`의 홈에 복사한 뒤 실행한다.

```bash
(
  set -euo pipefail
  umask 077
  test "$(hostname -s)" = harbor01
  test "$(id -un)" = server08
  cd "$HOME"
  sha256sum --check P1-CICD-01_Baseline_Application_Kit_v1.0.zip.sha256
  csr_work="$(mktemp -d "$HOME/P1_CICD_Harbor_CSR.XXXXXXXX")"
  python3 -m zipfile -e P1-CICD-01_Baseline_Application_Kit_v1.0.zip "$csr_work"
  cd "$csr_work/P1-CICD-01_Baseline_Application_Kit_v1.0"
  sha256sum --check SHA256SUMS
  mkdir -m 700 "$csr_work/results"
  sudo python3 tools/pki/create_harbor_csr.py --apply \
    --output-dir "$csr_work/results"
)
```

성공 시 `CSR=CREATED`, `PRIVATE_KEY_EXPORTED=false`, `HARBOR_SERVICE_CHANGED=false` 및 `ARCHIVE`·`SIDECAR`가 출력된다. 종료 0은 CSR 생성 성공, 3은 오류이며 Delivery 성공이 아니다. `--apply` 없이 실행하면 계획만 출력한다. 출력 Archive와 sidecar만 공유한다. Archive 구성은 `harbor01.csr.pem`, `receipt.json`, `SHA256SUMS`다. 영수증의 개인키 경로는 서버 내 위치 참조이며 키 내용은 없다.

### 실패·재시도와 요청 디렉터리 정리

개인키 생성 이후 실패했다고 같은 `--apply`를 즉시 반복하지 않는다. 새 소스는 실패 단계와 `PRIVATE_REQUEST_DIR`를 출력하고 기존 파일을 보존한다. 이미 전달한 v1.0 ZIP은 변경하지 않았다. 해당 ZIP 실행에는 새 진단 필드가 없을 수 있다.

- **CSR 생성·자체 서명 검증 성공, 공개 결과 내보내기만 실패:** 기존 요청을 보존하고 같은 CSR을 다시 전달한다. 키를 다시 만들거나 요청 디렉터리를 통째로 압축하지 않는다.
- **생성 도중 실패:** 이하영이 root 보호 영역에서 파일 메타데이터와 CSR 유무·자체 서명을 확인한다. CA 요청/발급 대장, `current` 대상 및 서비스가 참조하는 키와 대조하여 활성·발급 대기 요청을 먼저 제외한다.
- **폐기 확정된 미발급 요청:** 요청 ID·실패 단계·폐기 이유를 기록한 뒤 해당 디렉터리 하나만 정리한다. 시간 기준 자동 삭제, 와일드카드/재귀 일괄 삭제를 하지 않는다. 발급된 요청은 갱신/폐기·백업 보존 정책과 함께 처리한다.
- **현재 `harbor01-sn0wepv7`:** 제출 CSR과 Leaf 발급·기존 개인키 일치 검증이 PASS다. 개인키·CSR 모두 보존 대상이다.

소유자가 정확한 폐기 대상임을 확인한 뒤 사용할 명령은 `sudo rm -- /etc/harbor/pki/requests/<폐기확정-ID>/harbor01.key /etc/harbor/pki/requests/<폐기확정-ID>/harbor01.csr.pem`, 이어 `sudo rmdir -- /etc/harbor/pki/requests/<폐기확정-ID>`다. 실제 존재하는 파일만 지정하며, 추가 파일이 있으면 `rmdir` 실패를 조사한다. 파일 삭제가 저장매체의 보안 소거를 보장하지는 않는다. 이 명령의 자리표시자는 현재 요청 ID로 바꾸어 실행하라는 뜻이 아니다.

CA 서명 도중 실패한 경우에도 CA `index.txt`·`serial`·`newcerts`를 초기화하지 않는다. 대장에 이미 발급이 기록되었는지 확인하고 동일 요청을 중복 발급하지 않는다. §5.1 실패로 남은 release 디렉터리도 출력 경로 하나만 확인하며 원래 요청 키를 유지한다.

## 2. CA 보관 환경 — server01 / ca01 배치 확정

2026-09-10 사용자 “server01배치안으로” 지시를 반영한다. server01은 물리 Windows/VMware 호스트이며 서명은 별도 ca01 VM에서 수행한다. 당초 계획은 2 vCPU·RAM 4GB·OS 20GB·CA 데이터 2GB였다. 09-15 실제 구성은 2 vCPU·RAM 4GiB·OS 40GiB·CA 데이터 2GiB이며 OS 용량 차이를 실제 구성 기록에 남겼다. LUKS 암호화·NIC 제거·별도 USB 암호화 백업/파일 복구를 확인했다. /boot·EFI 및 VMware 전체 VM 파일 암호화와 구분한다.

구현값: 이권욱이 관리하는 관리자 단말/전용 오프라인 환경의 **암호화 볼륨 `/mnt/fds-ca`**, 자산 디렉터리 `/mnt/fds-ca/fds-project-root-ca-2026-replacement`. 루트 키는 암호화하고 별도 암호화 백업 매체 1개를 분리 보관한다. 서명 중에만 볼륨을 열고 끝나면 분리한다. 개인키·암호를 Runner, Harbor, Git, Actions Secrets, 대화 또는 증적 Archive에 넣지 않는다.

이 경로는 새로 채택한 구축 위치다. 실제 장치 ID·암호화 방식·관리자 계정과 백업 매체는 현장에서 자산 기록에 연결한다. 암호화 볼륨이 준비되기 전에는 일반 디렉터리에 키를 생성하지 않는다. 사용자 결정에 따라 구 CA는 재사용하지 않으며 구 키·발급 대장·자산 경로는 신규 디렉터리에 복사하거나 덮어쓰지 않는다. 새 Root는 새 개인키로 만든다. 구 CA 지문·배포 대상·백업은 전환·폐기 이력으로 식별한다. `config/p1-cicd-01.json`은 계획과 실제 구성·발급 증거를 분리해 기록하며 구 CA 폐기와 실제 Delivery는 미검증으로 유지한다.

## 3. 보호 환경에서 신규 Root 최초 생성

현재 Root는 이미 생성됐다. 아래는 최초 구축 이력·별도 신규 작업을 위한 참고이며 현재 자산에 재실행하지 않는다. 다음 명령은 **위 오프라인 CA 환경 전용**이다. 이 대화 환경이나 Runner·Harbor에서 실행하는 명령이 아니다. 암호화 볼륨 장치와 백업을 현장에서 확인하고, 키트의 `pki/root-ca.cnf`를 복사한다. 암호는 OpenSSL의 보호된 대화형 프롬프트로 입력한다. 이 단계에서 만든 CA 공개 지문을 자산 기록에 확정한다.

```bash
(
  set -euo pipefail
  umask 077
  test "$(hostname -s)" = ca01
  mountpoint -q /mnt/fds-ca
  test ! -e /mnt/fds-ca/fds-project-root-ca-2026-replacement
  test ! -L /mnt/fds-ca/fds-project-root-ca-2026-replacement
  mkdir -m 700 /mnt/fds-ca/fds-project-root-ca-2026-replacement
)
```

검사한 키트에서 `pki/root-ca.cnf`와 `pki/harbor-leaf.ext`를 새 CA 자산 디렉터리로 복사한 뒤 다음을 실행한다. `mountpoint` 성공 자체는 암호화 확인을 대신하지 않는다.

```bash
(
  set -euo pipefail
  umask 077
  test "$(hostname -s)" = ca01
  cd /mnt/fds-ca/fds-project-root-ca-2026-replacement
  test -f root-ca.cnf
  test -f harbor-leaf.ext
  test ! -e private
  test ! -e index.txt
  mkdir -m 700 private certs newcerts issued requests
  : > index.txt
  printf 'unique_subject = no\n' > index.txt.attr
  printf '1000\n' > serial
  openssl genpkey -algorithm RSA -aes-256-cbc \
    -pkeyopt rsa_keygen_bits:4096 -out private/fds-root-ca.key
  openssl req -new -x509 -sha256 -days 1825 \
    -key private/fds-root-ca.key -config root-ca.cnf \
    -out certs/fds-root-ca.crt
  openssl x509 -in certs/fds-root-ca.crt -noout \
    -subject -issuer -dates -fingerprint -sha256
)
```

Root의 1825일과 Leaf의 365일은 이번 운영값이다. 새 키·인증서 지문·SPKI·serial과 발급 대장을 신규 자산 ID에 연결한다. 구 CA 개인키나 serial 대장을 새 CA에 재사용하지 않는다. 명령이 일부 진행된 뒤 실패하면 남은 파일·대장을 먼저 확인하며 디렉터리 초기화나 무조건 재생성을 하지 않는다. 새 Root는 RSA4096, CA:TRUE/pathlen:0, keyCertSign/cRLSign이다. 서명 DB·serial·인증서·암호화 키를 함께 보호 백업한다. 이 단순 오프라인 CA 절차는 전체 PKI 자동화 시스템을 구현했다는 의미가 아니다. [OpenSSL CA 운영](https://docs.openssl.org/3.5/man1/openssl-ca/)

## 4. CSR 검토·Leaf 서명

현재 요청은 serial `1000`으로 이미 발급됐다. 다음 serial은 `1001`이며 이 절차를 재실행하거나 대장을 초기화하지 않는다. 아래 명령은 최초 발급 절차 참조다.

CSR Archive의 외부 해시를 확인한 후 새 디렉터리에 압축을 풀고 내부 SHA256SUMS를 확인한다. 위 Harbor 도구에서 받은 공개 CSR만 CA 자산의 `requests/harbor01.csr.pem`으로 복사한다. 기존 파일이 있으면 덮어쓰지 않고 요청 ID가 다른 디렉터리에 보관한다.

```bash
(
  set -euo pipefail
  umask 077
  test "$(hostname -s)" = ca01
  cd /mnt/fds-ca/fds-project-root-ca-2026-replacement
  openssl req -in requests/harbor01.csr.pem -verify -noout
  openssl req -in requests/harbor01.csr.pem -noout -subject -text
  openssl x509 -in certs/fds-root-ca.crt -checkend 31622400 -noout
  test ! -e issued/harbor01.crt
  openssl ca -config root-ca.cnf -notext -days 365 \
    -extfile harbor-leaf.ext -extensions harbor_leaf \
    -in requests/harbor01.csr.pem -out issued/harbor01.crt
  openssl verify -CAfile certs/fds-root-ca.crt -purpose sslserver \
    -verify_ip 10.1.93.54 issued/harbor01.crt
  openssl verify -CAfile certs/fds-root-ca.crt -purpose sslserver \
    -verify_hostname harbor.fds.internal issued/harbor01.crt
  openssl x509 -in issued/harbor01.crt -noout -dates -fingerprint -sha256
)
```

서명 프롬프트에서 요청 주체·RSA3072·정해진 SAN을 검토한다. CA는 요청의 임의 확장을 복사하지 않고 `harbor-leaf.ext`로 CA:FALSE와 serverAuth 및 SAN을 강제한다. 이 단계에서는 새로 생성한 Root의 승인 지문·보관 위치·신규 발급 대장을 확인하고 그 CA로 서명한다. 구 CA를 서명자로 선택하지 않는다. CA 잔여 유효기간이 충분하지 않으면 발급을 중단하고 원인을 확인한다. [OpenSSL CSR](https://docs.openssl.org/3.5/man1/openssl-req/)

반환물: Leaf 공개 인증서, CA 공개 인증서, 두 인증서의 SHA-256 지문, CSR SHA-256, serial·유효기간·발급자·요청 ID. 실제 CA 키와 암호는 반환하지 않는다. 만료 30일 전 Leaf 갱신을 시작하고 같은 검증·교체·복구 절차를 반복한다.

## 5. 정식 Harbor 구성·전환

이하영이 아래 값을 적용한다. 현재 임시 v2.13.1 이미지를 관측한 사실만으로 해당 버전을 정식 승인하지 않는다. 정식 설치판의 버전·배포물 해시·보안 검토를 기록한 뒤 `/opt/harbor`에 새 원본을 둔다.

| 설정 | 채택값 |
|---|---|
| `hostname` | `10.1.93.54` (현재 접속 경로 유지) |
| `https.port` | `443` |
| `https.certificate` | `/etc/harbor/pki/current/harbor01.crt` |
| `https.private_key` | `/etc/harbor/pki/current/harbor01.key` |
| `data_volume` | `/data/harbor-formal` (새 빈 디렉터리) |
| 관리 원본 | `/opt/harbor` |
| 기존 DB·Redis·Registry·Secret | `/data`의 기존 개별 경로에 보존, 신규 구성으로 import하지 않음 |
| 자격증명 | 신규 관리자/내부 비밀값/CI Push+Pull Robot/Worker Pull Robot 분리 발급 |

### 5.1. 발급물 검증·서버 내 설치 — 최초 `current` 생성

CA는 **Leaf·Root 공개 인증서**를 반환한다. `.key`를 CA에서 받아오는 절차는 없다. 이하영이 Harbor에 이미 생성한 `/etc/harbor/pki/requests/harbor01-sn0wepv7/harbor01.key`를 서버 안에서 복사해 사용한다. 원래 요청 키는 이동·삭제하지 않는다.

이권욱이 서명 결과를 검토하여 CA 지문, Leaf의 SHA-256·serial·유효기간 및 CA:FALSE·digitalSignature/keyEncipherment·serverAuth·정확한 SAN을 발급 기록에 확정한다. Root 지문은 **받은 파일에서 계산한 값을 그대로 승인값으로 삼지 않고** 승인 CA 자산 기록과 대조한다. 아래는 채택한 Root→Harbor Leaf 직접 서명 구조다. 다른 체인 구조면 chain 파일·검증 절차를 먼저 맞춘다.

공개 반환물의 현재 검증된 수신 위치는 `server08` 홈의 `ca01-public-handoff-v1.0/verified`다. `SHA256SUMS`는 Root/Leaf/CSR·정보 파일·요청 ID의 6개 체크섬 목록이며 설치 대상은 공개 인증서 2개다. 압축본의 별도 SHA-256과 Root 지문은 [실제 구성 기록](implementation-state-20260915.md)에 고정했다. 설치 스크립트는 Root의 승인 지문, Leaf의 체인·IP/두 DNS 이름, 알려진 CSR 해시, CSR/Leaf/개인키 공개키 일치와 잔여 30일을 다시 검사한다. 체크섬만으로 CA 신뢰를 결정하지 않는다.

| 입력/결과 | 경로·권한 |
|---|---|
| 받은 공개 파일 | `~/ca01-public-handoff-v1.0/verified/harbor01.crt`, `fds-root-ca.crt`, `SHA256SUMS` |
| 원본 개인키 | `/etc/harbor/pki/requests/harbor01-sn0wepv7/harbor01.key`, root:root 0600 유지 |
| 새 설치 디렉터리 | `/etc/harbor/pki/releases/harbor01-<임의값>`, root:root 0700 |
| 설치 파일 | Leaf/Root 0644, 개인키 0600, 모두 root:root |
| Harbor 입력 링크 | `/etc/harbor/pki/current` → 검증 완료한 release 디렉터리 |

`current`가 이미 있으면 자동 교체하지 않고 중단한다. 최초 구성 준비를 위한 명령이며 서비스 중지·`prepare`·기동은 수행하지 않는다. 실패한 release는 root 0700에 남겨 조사하고 원본 요청을 보존한다.

```bash
(
  set -euo pipefail
  umask 077
  test "$(hostname -s)" = harbor01
  test "$(id -un)" = server08
  issued_dir="$HOME/ca01-public-handoff-v1.0/verified"
  cd "$issued_dir"
  sha256sum --check SHA256SUMS
  read -r -p '승인 CA SHA-256 지문(64자리, 콜론 허용): ' approved_ca_sha
  approved_ca_sha="${approved_ca_sha//:/}"
  approved_ca_sha="${approved_ca_sha,,}"
  [[ "$approved_ca_sha" =~ ^[0-9a-f]{64}$ ]]
  sudo bash -s -- "$issued_dir" "$approved_ca_sha" <<'HARBOR_TLS_INSTALL'
set -Eeuo pipefail
umask 077
export PATH=/usr/sbin:/usr/bin:/sbin:/bin LC_ALL=C
tls_phase=preconditions
tls_release=''
trap 'tls_rc=$?; printf "FAILED_PHASE=%s RC=%s RELEASE_PRESERVED=%s\n" "$tls_phase" "$tls_rc" "$tls_release" >&2; exit "$tls_rc"' ERR
test "$(hostname -s)" = harbor01
test "$(id -u)" = 0
test "${SUDO_USER:-}" = server08
tls_in="$1"
tls_ca_sha="$2"
[[ "$tls_ca_sha" =~ ^[0-9a-f]{64}$ ]]
tls_base=/etc/harbor/pki
tls_request="$tls_base/requests/harbor01-sn0wepv7"
test ! -e "$tls_base/current"
test ! -L "$tls_base/current"

# Validate every existing protected path without following symbolic links.
python3 - "$tls_request" "$tls_base/releases" <<'PY'
import os, pathlib, stat, sys
request, releases = map(pathlib.Path, sys.argv[1:])
for path in [*reversed(request.parents), request]:
    s = path.lstat()
    if not stat.S_ISDIR(s.st_mode) or s.st_uid != 0 or s.st_mode & 0o022:
        raise SystemExit('UNSAFE_PROTECTED_PATH')
for path in (request / 'harbor01.key', request / 'harbor01.csr.pem'):
    s = path.lstat()
    if (not stat.S_ISREG(s.st_mode) or s.st_uid != 0 or s.st_gid != 0
            or s.st_nlink != 1 or stat.S_IMODE(s.st_mode) != 0o600):
        raise SystemExit('UNSAFE_REQUEST_FILE')
if os.path.lexists(releases):
    s = releases.lstat()
    if (not stat.S_ISDIR(s.st_mode) or s.st_uid != 0 or s.st_gid != 0
            or stat.S_IMODE(s.st_mode) != 0o700):
        raise SystemExit('UNSAFE_RELEASE_DIRECTORY')
else:
    releases.mkdir(mode=0o700)
PY
tls_phase=stage_public_files
for tls_file in harbor01.crt fds-root-ca.crt; do
  test -f "$tls_in/$tls_file"
  test ! -L "$tls_in/$tls_file"
  test "$(stat -c %s "$tls_in/$tls_file")" -le 65536
done
tls_release="$(mktemp -d "$tls_base/releases/harbor01-XXXXXXXX")"
printf 'TLS_RELEASE_DIR=%s\n' "$tls_release"
install -o root -g root -m 0644 -- "$tls_in/harbor01.crt" "$tls_release/harbor01.crt"
install -o root -g root -m 0644 -- "$tls_in/fds-root-ca.crt" "$tls_release/fds-root-ca.crt"
cd "$tls_release"
test "$(grep -c '^-----BEGIN CERTIFICATE-----$' harbor01.crt)" = 1
test "$(grep -c '^-----BEGIN CERTIFICATE-----$' fds-root-ca.crt)" = 1
if grep -q 'PRIVATE KEY' harbor01.crt fds-root-ca.crt; then
  printf 'UNEXPECTED_PRIVATE_MATERIAL\n' >&2
  exit 3
fi

tls_phase=verify_ca_and_leaf
tls_actual_ca="$(openssl x509 -in fds-root-ca.crt -noout -fingerprint -sha256 | cut -d= -f2 | tr -d ':' | tr 'A-F' 'a-f')"
test "$tls_actual_ca" = "$tls_ca_sha"
openssl verify -CAfile fds-root-ca.crt -check_ss_sig fds-root-ca.crt
openssl verify -CAfile fds-root-ca.crt -purpose sslserver -verify_ip 10.1.93.54 harbor01.crt
for tls_name in harbor.fds.internal harbor01; do
  openssl verify -CAfile fds-root-ca.crt -purpose sslserver -verify_hostname "$tls_name" harbor01.crt
done
openssl x509 -in harbor01.crt -checkend 2592000 -noout
openssl x509 -in harbor01.crt -noout -ext basicConstraints | grep -Fq 'CA:FALSE'

tls_phase=verify_existing_key_and_csr
test "$(sha256sum "$tls_request/harbor01.csr.pem" | cut -d' ' -f1)" = 97473d44f37140dfe04c83a29f6fec17560bb6f7b29a543fd1fa06cce91ca890
openssl req -in "$tls_request/harbor01.csr.pem" -verify -noout
install -o root -g root -m 0600 -- "$tls_request/harbor01.key" harbor01.key
openssl pkey -in harbor01.key -passin file:/dev/null -check -noout
openssl x509 -in harbor01.crt -pubkey -noout | openssl pkey -pubin -outform DER > leaf-public.der
openssl req -in "$tls_request/harbor01.csr.pem" -pubkey -noout | openssl pkey -pubin -outform DER > csr-public.der
openssl pkey -in harbor01.key -passin file:/dev/null -pubout -outform DER > key-public.der
cmp -- leaf-public.der csr-public.der
cmp -- leaf-public.der key-public.der
test "$(sha256sum leaf-public.der | cut -d' ' -f1)" = 636f85ce3ae0d5e3ea49e50fca2b8ec1e5c132f990d7867e2ad59d0a2e333638
rm -- leaf-public.der csr-public.der key-public.der
openssl x509 -in harbor01.crt -noout -serial -dates -fingerprint -sha256
openssl x509 -in harbor01.crt -noout -ext basicConstraints,keyUsage,extendedKeyUsage,subjectAltName

tls_phase=create_current_link
ln -s -- "$tls_release" "$tls_base/current"
test "$(readlink -f "$tls_base/current")" = "$tls_release"
stat -c '%U:%G %a %n' "$tls_release" harbor01.crt harbor01.key fds-root-ca.crt
printf 'TLS_FILES_INSTALLED=true\nHARBOR_SERVICE_CHANGED=false\nDELIVERY_GATE=NOT_VERIFIED\n'
HARBOR_TLS_INSTALL
)
```

`current` 생성 직후 취소하고 아직 어떤 서비스도 참조하지 않았다면, 출력한 release 경로와 `readlink` 결과가 일치하는지 확인하고 `sudo unlink -- /etc/harbor/pki/current`로 링크만 제거한다. release와 원본 키는 보존한다. 서비스 반영 후에는 아래 전체 구성·데이터 복구 절차를 따른다. 갱신 시에는 기존 `current`의 대상·사용 중인 Leaf를 기록한 별도 교체 작업이 필요하며 위 최초 설치 블록을 반복하지 않는다.

### 5.2. Harbor 설정에 반영하고 검증

§5.1 설치 성공 후 `/opt/harbor/harbor.yml`의 `https.certificate`와 `https.private_key`를 위 표의 `current` 경로로 지정한다. 생성된 Compose/NGINX의 실제 파일·마운트도 확인한다. Harbor의 `prepare`는 입력을 실제 서비스 설정으로 전개하므로 원본 경로만 바꾸고 서비스 반영을 완료했다고 하지 않는다. [Harbor HTTPS](https://goharbor.io/docs/2.13.0/install-config/configure-https/)

선정 설치판·실제 bind mount·중지 시점·일관된 백업이 확인된 후에만 새 `/opt/harbor` 설치판의 `prepare`와 기동 절차를 수행한다. 설치판이 아직 확정되지 않았으므로 현재 시점에 서비스 전환 완료 또는 자동 전환 가능으로 기록하지 않는다. `prepare` 후 전개된 인증서 공개 지문과 입력 Leaf 지문을 대조하고, 개인키가 복사되는 실제 호스트 경로의 소유권·권한 및 SELinux 거부 유무를 확인한다. 전체 경로 `chmod 777`이나 SELinux 비활성화로 해결하지 않는다.

전환 순서:

1. 현재 9개 컨테이너 ID·데이터 마운트와 새로 쓸 모든 bind mount를 현장에서 확인한다. 기존 라벨 `/home/fdsadmin/harbor`만으로 Compose를 실행하지 않는다.
2. 필요한 정지 시간과 백업 용량을 확인하고 **확인된 임시 Harbor 컨테이너만** 중지한다. 동일 시점의 DB·Redis·Registry·공유 Secret·구성·구 인증서/키를 보호 백업하고 읽기·해시를 검사한다. 백업에 비밀값이 포함되므로 서버 보호 영역 또는 암호화 백업 매체에만 둔다.
3. 기존 컨테이너 제거가 필요하면 검증된 ID만 대상으로 하고 볼륨 삭제 옵션을 사용하지 않는다. `/data` 전체 삭제, `docker system prune`, `compose down -v`를 초기화 수단으로 사용하지 않는다.
4. 신규 `/data/harbor-formal`이 비어 있고 기존 `/data/database`, `/data/redis`, `/data/registry`, `/data/secret`을 신규 Compose가 참조하지 않는지 확인한다. 동일 포트·이름 충돌을 해소한 다음 새 구성을 기동한다. 설정·데이터를 새로 만드는 선택이므로 임시 계정·프로젝트·이미지는 자동 이관되지 않는다.
5. 실제 제공 Leaf·SAN·chain·TLS 1.2 이상·서비스 health를 확인하고 새 프로젝트/Robot을 발급한다. 새 Registry로 운영 전환하기 전 복구 절차를 점검한다.

`data_volume`은 Harbor의 영속 데이터 위치이며 컨테이너 재생성만으로 초기화되지 않는다. [Harbor 설정](https://goharbor.io/docs/2.13.0/install-config/configure-yml-file/)

복구는 신규 구성을 정지하고 보존한 이전 구성·데이터를 확인하는 순서다. 신규 환경에서 쓰기가 시작된 후에는 구 환경 복귀가 신규 데이터를 잃게 할 수 있으므로 전환 이후 데이터 처리와 함께 판단한다. CA 폐기 결정에 따라 구 Root 신뢰를 다시 추가하는 방식으로 복구하지 않는다. 이전 서비스 구성을 복원할 때도 새 Root가 서명한 유효 Leaf와 일치하는 서버 키를 적용할 수 있는지 사전에 검증하며, 불가능하면 서비스를 중지 상태로 두고 원인을 해결한다. 기존/신규 데이터 간 부분 복사는 복구 수단으로 사용하지 않는다.

## 6. Trust와 최종 인수

Runner·Worker에 승인 지문과 일치하는 CA 공개 인증서만 설치한다. Runner의 OS trust와 실제 Podman trust, Worker의 실제 containerd trust가 적용됐는지 각각 확인한다. 이전 임시 cert의 blanket trust나 `-k`·`--tls-verify=false`는 사용하지 않는다. 승인 CA로 strict TLS를 통과한 뒤 CI Push+Pull 및 Worker Pull/Pod Start·Push DENY를 시험한다. CA 키를 Consumer에 복사하지 않는다.

IP endpoint를 유지하므로 DNS 미구현이 초기 발급을 막지 않는다. SAN에 이름을 넣는 것은 DNS 레코드/방화벽 적용 완료를 뜻하지 않는다. IF-09/10/11, 내부 source 보존과 API 인증·최소권한은 별도 증적으로 확인한다. **현재 DELIVERY_GATE=NOT_VERIFIED**이며 이 키트만으로 초기화·정식 발급·배포 완료를 선언하지 않는다.


## 기존 CA 전환·폐기 인수 — 2026-09-10

- **담당:** 이권욱은 구 CA 신규 서명 중지·자산 식별·신규 Root 발급·Runner Trust와 최종 폐기 기록, 이하영은 Harbor Leaf 적용·복구·Worker Trust를 담당한다.
- **전환:** 기존 CA 공개 지문과 실제 소비자를 식별하고 신규 Root 공개 인증서 및 새 Leaf로 전환한다. Harbor·Runner·Worker 외 edge01·monitor01·클러스터 Prometheus·관리자 도구는 실제 구 CA 사용 여부에 따라 포함한다. D33의 monitor01 Leaf 및 Prometheus Trust도 조사한다. 사용 중인 대상이 없으면 근거를 기록하고 전환 시험을 N/A로 처리한다.
- **검증:** 새 Root 단독 신뢰로 strict TLS·필요한 Push/Pull을 확인한 뒤 실제 OS·Podman·런타임·앱 신뢰 저장소에서 구 Root를 제거한다. 다른 구 신뢰 경로도 확인하고 구 체인이 더는 신뢰되지 않는지 검사한다. Root 키 삭제나 CRL 생성만으로 기존 Consumer의 Root 신뢰가 자동 제거되지는 않는다. [OpenSSL Trust Anchors](https://docs.openssl.org/3.5/man1/openssl-verification-options/#trust-anchors)
- **폐기:** 전환·필요한 취소 기록 완료 후 구 CA 개인키·복제본·백업을 실제 매체에 맞게 폐기하고 처리자·시각·방법·대상·검증 근거를 남긴다. 공개 인증서·지문·발급 대장·폐기 기록은 보존한다. 구 키가 이미 폐기됐다면 완료 증적과 남은 Trust 제거를 확인한다. 폐기된 CA로의 복구는 사용하지 않는다.
- **현재 미확인:** 구 CA 지문·장치·복제본·실제 소비자 전환·폐기 현황. 새 CA 호스트·암호화 볼륨·백업/파일 복구는 09-15 증적을 확보했다. 대상 경로를 모르는 상태에서 일괄 삭제·포맷하지 않는다. 파일 삭제만으로 SSD·가상 디스크의 보안 소거를 입증하지 않는다.

`NEW_CA_ISSUANCE=ISSUED_SCREEN_AND_PUBLIC_BYTES_VERIFIED`, `LEGACY_CA_RETIREMENT=NOT_VERIFIED`, `DELIVERY_GATE=NOT_VERIFIED`.
