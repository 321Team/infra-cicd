# 앱 공급 경로 현장 점검과 digest 인계

상태: **LOCAL COLLECTION READY / SUPPLY APPROVAL PENDING**.
PR #12는 2026-09-16 main `90f2f37d9e1ab77252b50e80bca339cfef48bd51`에 병합됐다.
실제 앱 Test/Build/Scan 실행 입력은 여전히 `PENDING_APP_HANDOFF`다.

## 작성 전 확인과 이번 범위

- 정본 [v3.1.18 §15.1.2 OID-03·§15.2](https://github.com/321Team/fds-project-baseline/blob/docs-v3.1.18/baseline/FDS_Project_Implementation_Baseline_v3.1.18.md)는
  공급의 사전 반입 또는 HTTPS 목적지·Owner·Expiry 확정과 미정의 인터페이스 기본 차단을 요구한다.
  IF-08 GitHub 성공을 registry/PyPI 공급 승인으로 확대하지 않는다.
- [#78](https://github.com/321Team/fds-project-baseline/issues/78)의 `.56:3128`은
  Alertmanager 전용이다. 현재 Runner의 일반 공급 경로나 Harbor proxy cache 인계 근거는 찾지 못했다.
- 앱 main은 `5b0049c2441b98196db6cd8cd4be9a85607568ea`로 관측됐다.
  두 Dockerfile 모두 `FROM python:3.12-slim`과 `RUN pip install ...`을 사용한다.
  호스트의 시험용 venv와 Build 내부 pip 공급은 각각 검증해야 한다.
- 기존 로컬 수집 명령, `collect_parallel_prep.py`의 identity/출력 제한/비밀정보 제외/묶음 저장,
  `build_scan.py`의 실행 환경과 입력 차단을 읽고 변경 영향을 검토했다.
  공통 수집·보존 함수를 재사용하며 Build/Scan 실행기와 기본 설정은 바꾸지 않는다.

## 1. 지금 실행할 로컬 점검

대상: **cicd-runner01 / server01_cicd-runner / UID 1000 / IPv4 10.250.10.20**.
sudo 불필요. 잘못된 대상이면 수집 및 증적 생성 전에 종료한다.

검증된 배포본 또는 해당 Commit의 전체 checkout에서:

```bash
/usr/bin/python3 tools/delivery/collect_supply_context.py
```

수집 범위:

| 항목 | 보존 내용 / 한계 |
|---|---|
| Identity | 호스트·계정·UID·IPv4. 실제 외부 NAT Source는 별도 확인 |
| 경로 | IPv4 route 전 테이블과 policy rule. 외부 destination 조회·접속 없음 |
| DNS | `/etc/resolv.conf`의 nameserver 주소만. 읽기에 성공한 빈 목록도 수집 완료이며, 읽기 실패(`null`)는 PARTIAL. 실제 질의·변경 없음 |
| Runner 서비스 | 고정 service의 LoadState/ActiveState/SubState/User/MainPID만 |
| 도구 | Python/Podman/Skopeo 버전 또는 UNAVAILABLE. 미설치 도구를 자동 설치하지 않음 |
| 공급 설정 | 정해진 pip/registry 파일·디렉터리의 존재·권한 메타데이터만 |
| 로그인 환경 | proxy/pip/CA 관련 변수의 **존재 여부**만. 값·endpoint·자격증명 제외 |
| 용량 | 계정 홈 파일시스템 여유 바이트 |

Runner process environ, service Environment/ExecStart, pip config 내용, registry auth, Token은
읽거나 저장하지 않는다. 실제 job 환경·effective index/proxy/mirror는 `NOT_INSPECTED`다.
이 자료만으로 로그인 셸과 Workflow 환경이 같다고 판정하지 않는다.

계정 DB의 홈 아래 새 private `P1_CICD_Parallel_*` 디렉터리에 아래를 만든다.
상위 `HOME` 환경변수로 저장 위치를 다른 계정으로 바꾸지 않는다.

- `preparation.json`: 수집 결과·수집기 두 파일의 SHA-256·시간·검증 한계
- `SHA256SUMS`: JSON 해시
- `P1-CICD-01_Parallel_runner-supply_Result.tar.gz`: JSON과 내부 체크섬만 포함
- 위 압축파일의 `.sha256`

출력의 **ARCHIVE와 SIDECAR 두 파일**을 전달한다. 디렉터리 0700, 파일 0600이다.
수집 외 시스템 설정 변경은 없으므로 원복할 서비스/네트워크 변경은 없다.

| 종료 코드 | 의미 / 조치 |
|---:|---|
| 0 | LOCAL_COLLECTION=COMPLETE: route/rule/resolver/service 로컬 자료 수집 완료. 도구 미설치 등 개별 결과와 실행 정책은 추가 검토 |
| 2 | PARTIAL: 일부 로컬 자료 미수집. 만들어진 두 파일을 그대로 전달; 설정 변경으로 우회하지 않음 |
| 3 | 대상 UID/호스트/IP/계정 불일치. 올바른 대상 확인 |
| 1 | Python 3.11 미만, 수집 또는 저장 오류. 고정 오류 코드 확인 |

모든 경우 `NETWORK_SUPPLY`·실제 registry/PyPI 연결과 `DELIVERY_GATE`를 PASS로 올리지 않는다.
현장 로컬 수집은 2026-09-16 21:44:56 KST에 실행됐고 제출 자료의 무결성·Source 일치를
[#11](https://github.com/321Team/infra-cicd/issues/11)에서 검증했다. 수집 Source는 `ab10e159`이며
nameserver `8.8.8.8`이 있어 빈 목록 판정 수정의 영향을 받지 않는다. 이 수정만을 위한 재수집은
요구하지 않는다. 코드 회귀 시험은 Mock/임시 파일 기반이며 실제 공급 연결은 아직 미검증이다.

## 2. 현장 자료 후 판단할 OID-03 공급 후보

다음은 **검토 후보**이며 허용 규칙·프록시 설정·실행 승인 입력이 아니다.
기존 경로 재사용 가능 여부부터 대조한다. 새로운 방화벽·DNS·NAT·proxy 설정은 자동 적용하지 않는다.

| 항목 | 후보 / 확정 조건 |
|---|---|
| 목적 | `docker.io/library/python:3.12-slim`의 linux/amd64 base manifest 확인과 승인 앱의 Python 의존성 공급 |
| Source | Runner 10.250.10.20; 실제 적용 interface·gateway·외부 SNAT Source는 현장 자료와 경로 소유자 확인 |
| 구현 Owner / 검토 | 이권욱 / 이하영. 실제 enforcement point의 변경 주체·승인 근거는 별도 기록 |
| 방식 | 확인된 기존 외부 경로의 제한 HTTPS 후보. 기존 정책이 허용하지 않으면 사전 반입 방식까지 검토 |
| 인증·manifest 후보 | `auth.docker.io:443`, `registry-1.docker.io:443`; repository 범위 `library/python`의 read/pull |
| 이미지 layer 공급 | 실제 응답의 blob host를 별도 확인. Docker 공식 목록에는 `production.cloudfront.docker.com`이 있으나 전체 Desktop 도메인 목록을 CICD allowlist로 복사하지 않음 |
| 패키지 후보 | `pypi.org:443`의 승인 requirements 관련 simple index, `files.pythonhosted.org:443`의 선택 배포파일 |
| 기간 후보 | 시작 시각을 명시한 최대 60분 검증 창, 첫 검증 종료 시 조기 회수. 실제 승인·시작·종료 시각은 아직 없음 |
| 중단·회수 | 미승인 redirect/목적지, TLS 오류, Source/경로 불일치, 만료 시 중단. 추가한 임시 규칙만 회수·재차단 확인; 기존 GitHub·내부 경로 보존 |
| 기록 | 변경 범위·Owner·승인자/근거·시작/Expiry·정확한 대상·회수 결과를 #11과 상위 #16에 연결 |

60분은 정본의 새 의무가 아닌 이번 한정 검증의 제안이다. 지속 공급 정책이나 전체 패키지
목적지 허용을 확정하지 않는다. Docker의 공개 목록은 공급 대상 후보의 근거이며
Podman 환경에서 실제 사용할 전체 host·redirect 목록을 증명하지 않는다.
신규 host가 관측되면 자동 추종/와일드카드 허용하지 않고 해당 범위를 추가 검토한다.

근거: [Docker 공식 도메인 목록](https://docs.docker.com/desktop/setup/allow-list/),
[pip index 기본값과 설치 공급원](https://pip.pypa.io/en/stable/cli/pip_install/),
[PyPI Index API와 배포파일 URL](https://docs.pypi.org/api/index-api/).
공식 문서는 2026-09-16 조회했다. 해당 문서가 프로젝트의 네트워크 승인을 대신하지 않는다.

## 3. 경로 승인 후 digest 인계 절차

1. 승인 기간·대상·TLS 검증 조건 아래 태그의 **원본 manifest/index 바이트**와 조회 UTC 시각,
   도구 버전·repository·응답 digest를 보존한다. 보기 좋게 재직렬화한 JSON의 해시를 원본 digest로 쓰지 않는다.
2. index이면 linux/amd64 descriptor를 선택하고 그 child manifest를 digest로 조회한다.
   해당 descriptor의 SHA-256·size와 child 원본 바이트를 대조하고, config에서도 OS/architecture를 확인한다.
   index digest, child manifest digest, config/image ID를 구분한다.
3. 실제 사용할 reference는 `docker.io/library/python@sha256:<검증한 linux/amd64 manifest digest>`다.
   이 문서에는 조회하지 않은 digest를 채우지 않는다. digest 고정 자체가 취약점 PASS는 아니다.
4. 앱 담당이 **동일 reference**를 두 Dockerfile의 단일 FROM에 반영하고,
   테스트·Dockerfile 변경을 포함한 최종 40자리 Source SHA·승인자·근거를 인계한다.
5. CICD에서 시험 단계 매핑·최종 입력·Workflow와 supply 검증 근거를 함께 검토한 뒤 실행한다.
   test venv와 Build 내부 pip의 index/CA/경로도 각각 확인한다. private mirror/인증이 필요한 공급은
   현재 빈 authfile/credential 미주입 실행기에 그대로 끼워 넣지 않는다.

근거: [OCI Image Index](https://github.com/opencontainers/image-spec/blob/main/image-index.md),
[OCI Manifest](https://specs.opencontainers.org/image-spec/manifest/?v=v1.1.0).

## 인계 응답에 필요한 공개 값

현장 로컬 압축본 외 공급 경로를 이미 승인한 기록이 있으면 **그 링크**를 함께 받는다.
실제 approval/manifest 조회 뒤에는 다음 값을 기록한다. Token·비밀번호·proxy 인증 URL은 제외한다.

```text
Supply approval reference / owner / enforcement owner:
Source / route / permitted destination FQDNs:
Approved start UTC / expiry UTC / removal evidence:
Repository / source tag / query UTC / tool version:
Index digest (if present):
linux/amd64 manifest digest / raw manifest SHA-256:
Base reference (same in both Dockerfiles):
Final app 40-character commit / approver / approval reference:
Test phase mapping / any approved N/A justification:
```

관리 50%(5/10)·Runtime AC 1/10·`DELIVERY_GATE=NOT_VERIFIED`를 유지한다.
