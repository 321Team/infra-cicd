# server01의 ca01 VM 준비 v1.0

2026-09-10 사용자 지시 **“server01배치안으로”**에 따라 server01 물리 PC의 VMware Workstation에 별도 ca01 VM을 두기로 결정했다. 배치 선택은 확정됐으며 VM 생성·암호화·CA 발급을 완료한 기록은 아니다.

## 구축값

| 항목 | 값 |
|---|---|
| 물리 호스트 | server01 — Windows/VMware Workstation 호스트 |
| VM 이름·Linux hostname | ca01 |
| OS | CentOS Stream 10, x86_64, Minimal Install |
| CPU·RAM | 2 vCPU·4GB RAM |
| 디스크 | 신규 OS 가상 디스크 20GB + 신규 CA 데이터 가상 디스크 2GB |
| CA 저장 | 2GB 데이터 디스크를 LUKS2로 암호화하여 /mnt/fds-ca 사용 |
| 새 Root 자산 | /mnt/fds-ca/fds-project-root-ca-2026-replacement |
| 운영 계정 | 설치 시 별도 관리자 계정 생성, 비root 로그인 + 필요한 작업에 sudo |
| 네트워크 | 초기 OS·패키지 준비 후 가상 NIC 제거. 발급 운영에는 IP·DNS 레코드 미할당 |
| 전원·보관 | 발급/갱신 때만 실행. 작업 종료 후 볼륨 잠금·정상 종료 |
| 백업 | 별도 물리 매체에 암호화 백업, 복구 확인. 같은 호스트의 스냅샷은 별도 백업을 대체하지 않음 |

서버 이름 server01은 물리 배치를 뜻한다. cicd-runner01·ansible01 VM에 CA를 설치하는 명령이 아니다. 실제 여유 메모리/디스크, 호스트 관리자 및 VM 파일 접근통제, 백업 매체는 현장에서 확인한다. 동일 물리 호스트의 관리자 권한까지 VM NIC 제거로 격리되는 것은 아니다.

## 1. VMware에서 준비

1. server01의 기존 VM을 가동한 상태에서 작업 관리자·디스크 여유 공간을 확인한다. 최대 할당값 22GB 외에도 OS 호스트와 기존 VM에 필요한 여유를 확보한다. 여유가 부족하면 기존 VM 파일이나 디스크를 삭제하지 말고 자원 배치를 조정한다.
2. 새 VM ca01을 생성한다. 서비스 VM 복제본을 사용하거나 기존 Harbor/Runner 디스크를 연결하지 않는다. 2 vCPU, RAM 4GB, 새 OS 디스크 20GB를 지정한다. 별도 CA 데이터 디스크 2GB는 OS 설치 후 VM을 종료한 상태에서 추가하면 설치 대상 혼동을 줄일 수 있다.
3. 검증한 CentOS Stream 10 설치 ISO로 Minimal Install을 진행하고 hostname을 ca01로 설정한다. 관리자 계정과 강한 암호를 설정한다. 새 OS 디스크 안의 시스템/스왑 암호화도 설치 시 적용하여 임시 데이터 보호를 보완한다. 기존 물리 디스크의 파티션을 변경하는 작업은 아니다.
4. CA 개인키 생성 전에 필요한 OS 업데이트와 openssl·python3·cryptsetup 패키지를 준비한다. 초기 네트워크가 필요하면 기존 VMnet8 NAT에서 준비하고, 패키지 공급·시각을 확인한 뒤 정상 종료하여 NIC를 제거한다. CA 키를 보관한 상태로 상시 네트워크에 연결하지 않는다.
5. 새 CA 데이터 디스크 2GB를 추가한다. 공유 폴더·클립보드/드래그앤드롭 통합은 사용하지 않도록 설정한다. 이후 운영은 VMware 콘솔에서 수행한다.

발급용 공개 CSR·검토된 설정은 읽기 전용 ISO 등 통제된 방법으로 반입한다. 공개 인증서·지문·발급 기록의 반출 매체와 CA 개인키 백업 매체는 구분한다. CA 개인키·암호는 Git·대화·일반 증적 파일에 넣지 않는다. 키를 연 상태의 VM 일시중지·메모리 스냅샷을 백업 방식으로 사용하지 않는다.

## 2. ca01 콘솔에서 장치·환경 확인

아래는 조회와 일반 환경 기록만 수행한다. 데이터 디스크의 실제 이름을 확인하기 전 /dev/sdb 등을 임의로 포맷하지 않는다. NIC가 없는 상태에서는 loopback 외 인터페이스가 나타나는지 확인한다.

```bash
(
  set -euo pipefail
  umask 077
  test "$(hostname -s)" = ca01
  test "$(id -u)" -ne 0
  ca_work="$(mktemp -d "$HOME/P1_CA01_Preparation.XXXXXXXX")"
  {
    date --iso-8601=seconds
    hostname -s
    id -un
    cat /etc/os-release
    getenforce
    lsblk -o NAME,SIZE,TYPE,FSTYPE,MOUNTPOINTS
    ip -brief address
    for ca_package in openssl python3 cryptsetup; do
      if ! rpm -q "$ca_package"; then
        printf 'PACKAGE_NOT_VERIFIED=%s\n' "$ca_package"
      fi
    done
    printf 'COLLECTION_ONLY=true\nCA_VOLUME=NOT_VERIFIED\nCA_ISSUANCE=NOT_EXECUTED\n'
  } > "$ca_work/ca01-preparation.txt"
  cd "$ca_work"
  sha256sum ca01-preparation.txt > ca01-preparation.txt.sha256
  printf 'REPORT=%s/ca01-preparation.txt\nSIDECAR=%s/ca01-preparation.txt.sha256\n' "$ca_work" "$ca_work"
)
```

이 조회값은 CA 디스크 암호화·별도 백업·발급 성공을 입증하지 않는다. 패키지 미확인 표시는 원인을 확인하고 필요한 패키지를 준비해야 한다는 뜻이다.

## 3. 다음 단계

실제 OS/CA 디스크 매핑과 별도 백업 매체를 확인한 후 **신규 CA 데이터 디스크 하나**에 대한 LUKS2 초기화·마운트·복구 절차를 확정한다. 현재 장치 ID·암호화 UUID는 알려지지 않았으므로 이 문서에 포맷 명령을 넣지 않았다. 보호 환경 준비 이후 신규 Root 생성·기존 Harbor CSR 서명·Consumer 전환·구 CA 신뢰 제거/키 폐기를 이어간다.

[CA 준비 양식과 소스 ZIP 압축 해제 안내](ca-and-gate-preparation.md), [신규 CA·기존 CA 폐기 결정](decisions/ca-replacement-20260910.md).
