# Harbor 초기화·정식 발급 Runbook v1.0

채택 방식: **임시 환경은 보존·격리하고 새 데이터·키·자격증명으로 정식 구성한다.** 초기화 요청을 이 방식으로 구현한다. 서비스 전환은 이하영, CA 운영·서명과 Runner Trust는 이권욱이 맡는다.

## 1. Harbor에서 바로 실행할 작업: 새 키·CSR

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

## 2. CA 보관 환경

구현값: 이권욱이 관리하는 관리자 단말/전용 오프라인 환경의 **암호화 볼륨 `/mnt/fds-ca`**, 자산 디렉터리 `/mnt/fds-ca/fds-project-root-ca-2026`. 루트 키는 암호화하고 별도 암호화 백업 매체 1개를 분리 보관한다. 서명 중에만 볼륨을 열고 끝나면 분리한다. 개인키·암호를 Runner, Harbor, Git, Actions Secrets, 대화 또는 증적 Archive에 넣지 않는다.

이 경로는 새로 채택한 구축 위치다. 실제 장치 ID·암호화 방식·관리자 계정과 백업 매체는 현장에서 자산 기록에 연결한다. 암호화 볼륨이 준비되기 전에는 일반 디렉터리에 키를 생성하지 않는다. 승인 프로젝트 CA가 있으면 그 관리 체계와 지문을 재사용하고, 아래 최초 생성 절차는 실행하지 않는다. Harbor 임시 인증서는 승인 CA로 인정하지 않는다. CA 자산의 미확인 상태는 `config/p1-cicd-01.json`에 그대로 남겼다.

## 3. 승인 CA가 없음을 확인한 경우에만 최초 생성

다음 명령은 **위 오프라인 CA 환경 전용**이다. 이 대화 환경이나 Runner·Harbor에서 실행하는 명령이 아니다. 암호화 볼륨 장치와 백업을 현장에서 확인하고, 키트의 `pki/root-ca.cnf`를 복사한다. 암호는 OpenSSL의 보호된 대화형 프롬프트로 입력한다. 이 단계에서 만든 CA 공개 지문을 자산 기록에 확정한다.

```bash
(
  set -euo pipefail
  umask 077
  case "$(hostname -s)" in cicd-runner01|harbor01|edge01) exit 3 ;; esac
  mountpoint -q /mnt/fds-ca
  test ! -e /mnt/fds-ca/fds-project-root-ca-2026
  test ! -L /mnt/fds-ca/fds-project-root-ca-2026
  mkdir -m 700 /mnt/fds-ca/fds-project-root-ca-2026
)
```

검사한 키트에서 `pki/root-ca.cnf`와 `pki/harbor-leaf.ext`를 새 CA 자산 디렉터리로 복사한 뒤 다음을 실행한다. `mountpoint` 성공 자체는 암호화 확인을 대신하지 않는다.

```bash
(
  set -euo pipefail
  umask 077
  cd /mnt/fds-ca/fds-project-root-ca-2026
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

Root의 1825일과 Leaf의 365일은 이번 운영값이다. 기존 CA가 있다면 기존 유효기간을 임의로 늘리지 않는다. 새 Root는 RSA4096, CA:TRUE/pathlen:0, keyCertSign/cRLSign이다. 서명 DB·serial·인증서·암호화 키를 함께 보호 백업한다. 이 단순 오프라인 CA 절차는 전체 PKI 자동화 시스템을 구현했다는 의미가 아니다. [OpenSSL CA 운영](https://docs.openssl.org/3.5/man1/openssl-ca/)

## 4. CSR 검토·Leaf 서명

CSR Archive의 외부 해시를 확인한 후 새 디렉터리에 압축을 풀고 내부 SHA256SUMS를 확인한다. 위 Harbor 도구에서 받은 공개 CSR만 CA 자산의 `requests/harbor01.csr.pem`으로 복사한다. 기존 파일이 있으면 덮어쓰지 않고 요청 ID가 다른 디렉터리에 보관한다.

```bash
(
  set -euo pipefail
  umask 077
  cd /mnt/fds-ca/fds-project-root-ca-2026
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

서명 프롬프트에서 요청 주체·RSA3072·정해진 SAN을 검토한다. CA는 요청의 임의 확장을 복사하지 않고 `harbor-leaf.ext`로 CA:FALSE와 serverAuth 및 SAN을 강제한다. 승인된 CA가 기존에 있으면 해당 CA의 서명 도구·대장을 사용하여 같은 Leaf 프로파일로 발급한다. CA 잔여 유효기간이 충분하지 않으면 먼저 갱신/Consumer 전환 계획을 세운다. [OpenSSL CSR](https://docs.openssl.org/3.5/man1/openssl-req/)

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

`current`는 검증된 새 키·Leaf 쌍의 버전 디렉터리를 가리키게 전환한다. CSR 영수증의 기존 개인키를 서버 안에서 사용하고 새 키를 다시 만들지 않는다. CA/Leaf 검증과 공개키 일치, root 소유·최소권한을 확인한다. 생성된 Compose/NGINX의 실제 파일·마운트도 확인한다. Harbor의 `prepare`는 입력을 실제 서비스 설정으로 전개하므로 원본 경로만 바꾸고 서비스 반영을 완료했다고 하지 않는다. [Harbor HTTPS](https://goharbor.io/docs/2.13.0/install-config/configure-https/)

전환 순서:

1. 현재 9개 컨테이너 ID·데이터 마운트와 새로 쓸 모든 bind mount를 현장에서 확인한다. 기존 라벨 `/home/fdsadmin/harbor`만으로 Compose를 실행하지 않는다.
2. 필요한 정지 시간과 백업 용량을 확인하고 **확인된 임시 Harbor 컨테이너만** 중지한다. 동일 시점의 DB·Redis·Registry·공유 Secret·구성·구 인증서/키를 보호 백업하고 읽기·해시를 검사한다. 백업에 비밀값이 포함되므로 서버 보호 영역 또는 암호화 백업 매체에만 둔다.
3. 기존 컨테이너 제거가 필요하면 검증된 ID만 대상으로 하고 볼륨 삭제 옵션을 사용하지 않는다. `/data` 전체 삭제, `docker system prune`, `compose down -v`를 초기화 수단으로 사용하지 않는다.
4. 신규 `/data/harbor-formal`이 비어 있고 기존 `/data/database`, `/data/redis`, `/data/registry`, `/data/secret`을 신규 Compose가 참조하지 않는지 확인한다. 동일 포트·이름 충돌을 해소한 다음 새 구성을 기동한다. 설정·데이터를 새로 만드는 선택이므로 임시 계정·프로젝트·이미지는 자동 이관되지 않는다.
5. 실제 제공 Leaf·SAN·chain·TLS 1.2 이상·서비스 health를 확인하고 새 프로젝트/Robot을 발급한다. 새 Registry로 운영 전환하기 전 복구 절차를 점검한다.

`data_volume`은 Harbor의 영속 데이터 위치이며 컨테이너 재생성만으로 초기화되지 않는다. [Harbor 설정](https://goharbor.io/docs/2.13.0/install-config/configure-yml-file/)

복구는 신규 구성을 정지하고 보존한 이전 구성·데이터·키 쌍으로 되돌리는 순서다. 신규 환경에서 쓰기가 시작된 후에는 구 환경 복귀가 신규 데이터를 잃게 할 수 있으므로 전환 이후 데이터 처리와 함께 판단한다. 임시 인증서로 돌아가면 이전 TLS 미검증 상태도 되돌아온다. 기존/신규 데이터 간 부분 복사는 복구 수단으로 사용하지 않는다.

## 6. Trust와 최종 인수

Runner·Worker에 승인 지문과 일치하는 CA 공개 인증서만 설치한다. Runner의 OS trust와 실제 Podman trust, Worker의 실제 containerd trust가 적용됐는지 각각 확인한다. 이전 임시 cert의 blanket trust나 `-k`·`--tls-verify=false`는 사용하지 않는다. 승인 CA로 strict TLS를 통과한 뒤 CI Push+Pull 및 Worker Pull/Pod Start·Push DENY를 시험한다. CA 키를 Consumer에 복사하지 않는다.

IP endpoint를 유지하므로 DNS 미구현이 초기 발급을 막지 않는다. SAN에 이름을 넣는 것은 DNS 레코드/방화벽 적용 완료를 뜻하지 않는다. IF-09/10/11, 내부 source 보존과 API 인증·최소권한은 별도 증적으로 확인한다. **현재 DELIVERY_GATE=NOT_VERIFIED**이며 이 키트만으로 초기화·정식 발급·배포 완료를 선언하지 않는다.
