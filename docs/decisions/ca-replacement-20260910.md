# 기존 CA 폐기·신규 Root 발급 결정 — 2026-09-10

결정자: 이권욱. 사용자 지시: **“기존 승인 CA는 폐기 신규 발급 필요”**.

기존 CA 재사용 및 부재 확인 때만 최초 생성하던 선택을 **기존 CA 사용 종료·새 개인키로 신규 Root CA 발급**으로 변경한다. 이는 폐기·발급의 실행 완료 기록이 아니다. 기존 CA의 지문·키 위치·백업·신뢰 배포 대상은 아직 미확인이다. D19의 프로젝트 Private Root → Harbor/edge01 별도 Leaf 구조와 담당 경계는 유지한다. 정본 변경·PR 병합·서버 적용을 수행한 것으로 표시하지 않는다.

| 항목 | 결정·상태 |
|---|---|
| CA 선택 | REPLACE_WITH_NEW_ROOT. 기존 CA로 새 Leaf를 발급하지 않음 |
| 기존 CA | RETIREMENT_REQUIRED / 실제 신뢰 제거·키 폐기 NOT_VERIFIED |
| 신규 Root | 새 RSA4096 개인키, 1825일, CA:TRUE/pathlen:0, keyCertSign/cRLSign. 아직 생성·발급 미수행 |
| Harbor Leaf | 기존 RSA3072 CSR에 신규 Root로 서명, 365일·만료 30일 전 갱신. CA:FALSE/serverAuth·기존 SAN 유지 |
| 기존 Harbor 요청 | harbor01-sn0wepv7 유지. CA 교체만으로 서버 개인키·CSR을 다시 만들지 않음 |
| 보관 환경 | 이권욱 관리 오프라인 암호화 환경과 별도 암호화 백업. 실제 호스트·볼륨·매체 미확인 |
| ca01 제안 | server01의 전용 VM 제안은 자원·호스트 접근통제 확인 전이며 설치 완료·배치 확정으로 기록하지 않음 |

## 실행 순서와 인수

1. **기존 CA 사용 종료 기록:** 기존 CA의 신규 서명을 중지하는 운영 지시를 반영한다. 대상 공개 지문, 자산 위치·백업, 발급 대장과 실제 Consumer를 조사한다. 키 침해 여부는 제출된 정보가 없어 추정하지 않는다. 기존 CA 유무를 다시 선택할 필요는 없으며, 식별 조사는 폐기 대상과 영향 확인에 사용한다.
2. **새 CA 보호 환경 준비:** 신규 자산 디렉터리 `/mnt/fds-ca/fds-project-root-ca-2026-replacement`가 비어 있고 기존 자산과 분리되었는지 확인한다. 암호화·백업·접근통제를 확인한 후 새 키를 만든다. 기존 CA 키를 가져와 새 Root를 만드는 방식이나 기존 디렉터리·발급 대장을 초기화하는 방식은 사용하지 않는다. 이미 새 키를 생성한 경우에는 이어서 처리하며 실패 후 무조건 재생성하지 않는다.
3. **신규 발급·식별:** 새 Root의 인증서 지문·SPKI·serial·유효기간·보관 참조를 기록한다. 확보한 기존 CA 공개키와도 대조해 다른 키임을 확인한다. 기존 CA 식별이 남아 있으면 그 사실을 남기고 폐기 인수를 완료 처리하지 않는다. 알려진 Harbor CSR 자체 서명·SHA-256·SPKI 및 서버 키와 일치 여부를 확인한 후 새 Leaf를 발급한다.
4. **실제 대상 전환:** 이하영이 Harbor Leaf를 적용하고 Worker Trust를 연계하며, 이권욱이 Runner Trust를 검증한다. edge01·monitor01 및 클러스터 Prometheus·관리자 도구 등은 기존 CA를 실제로 사용하는지 확인한 대상만 포함한다. D33의 monitor01 서버 Leaf와 Prometheus의 CA Trust도 조사 범위이며 미구현 대상을 전환 완료로 간주하지 않는다. 변경 시점·이전 인증서·복구 조건을 기록하고 새 Root 단독 신뢰로 strict TLS 및 필요한 Push/Pull을 검증한다. 운영 중인 대상은 전환 순서를 조정하며 기존 신뢰를 먼저 일괄 삭제하지 않는다. 사용 중인 대상이 없으면 근거를 기록하고 해당 전환 단계는 N/A로 처리한다.
5. **기존 신뢰 제거:** 각 대상의 OS·Podman·컨테이너 런타임·개별 애플리케이션 신뢰 저장소에서 실제 등록된 구 Root를 지문으로 식별해 제거한다. 구 Root 및 필요한 다른 구 신뢰 경로까지 조사하고 구 체인이 더는 신뢰되지 않음을 확인한다. CA 키 삭제나 CRL 생성만으로 클라이언트의 Root 신뢰가 자동 사라지는 것은 아니다. [OpenSSL Trust Anchors](https://docs.openssl.org/3.5/man1/openssl-verification-options/#trust-anchors)
6. **기존 키 폐기·기록:** 영향 대상 전환과 필요한 취소/폐기 기록을 마친 후 구 개인키와 그 복제본·백업을 실제 저장매체에 맞는 방식으로 폐기하고 처리자·시각·대상·방법·검증 근거를 기록한다. 공개 인증서·지문·발급 대장·변경 및 폐기 기록은 보존한다. 파일 삭제를 SSD/가상 디스크의 검증된 보안 소거로 간주하지 않는다. 현재는 대상 장치와 경로를 모르므로 삭제·포맷 명령을 제공하거나 실행하지 않는다.

구 CA를 이미 물리적으로 폐기했다면 완료 증적과 남은 Trust 배포 대상을 확인한다. 삭제된 키 복구를 전환의 선행조건으로 요구하지 않는다. 폐기한 CA로의 복구는 사용하지 않으며 새 신뢰 기준의 복구 절차를 마련한다.

## 준비 검사와 실제 완료 구분

CA 준비 양식 v1.1은 신규 Root 선택과 전환 계획 참조를 요구하며 v1.0의 재사용 선택을 거부한다. 기존 지문이 null이어도 신규 준비 입력 확인은 가능하며 `LEGACY_CA_IDENTIFICATION_PENDING`을 남긴다. 실제 신뢰 제거·폐기는 별도 인수이므로 준비 검사 통과를 `RETIRED`로 기록하지 않는다.

현재: `NEW_CA_ISSUANCE=NOT_EXECUTED`, `LEGACY_CA_RETIREMENT=NOT_VERIFIED`, `DELIVERY_GATE=NOT_VERIFIED`.


## 물리 호스트와 CA 보관 기준의 구분

[v3.1.17 본문 §3.7 D19](https://github.com/321Team/fds-project-baseline/blob/2d93395a0e19ea67e5be199d7183a7edbaa6cf4c/baseline/FDS_Project_Implementation_Baseline_v3.1.17.md)은 프로젝트 Private Root, 서비스별 Leaf, Consumer 공개 CA Trust, TLS 1.2 이상과 검증 우회 금지를 규정한다. 해당 본문에 CA 전용 물리 서버·ca01의 CPU/RAM을 필수로 고정한 문구는 없다. 별도 오프라인 암호화 보관과 서비스 VM 내 CA 키 금지는 이후 infra-cicd에서 채택한 구현 통제이며 두 근거를 구분한다.

Harbor와 **동일 VM**에서 Root 개인키를 보관·서명하는 안은 현재 구현 통제와 충돌한다. **동일 물리 VMware 호스트의 별도 CA VM**은 자원·호스트 관리자·VM 파일 접근통제 확인을 전제로 검토할 수 있다. server01 배치 제안이 물리 호스트 강제 규칙은 아니다. NIC 제거는 VM 네트워크 분리이며 물리 호스트 관리자까지 격리하는 것은 아니다. 이번 질문을 Harbor VM에 CA를 통합하라는 변경 지시로 해석하지 않는다.
