# P1-CICD 이미지·Python 의존성 사전 반입 준비

상태: **PREPARATION_ONLY / 실제 공급·실행 승인 미완료**.
사용자 `2번 착수`에 따라 이권욱의 공급안 준비를 수행한다. 정책 리드는 이하영,
독립 코드 리뷰는 기존 부재 대체 범위의 이재환이다. 담당·권한을 새로 변경하지 않는다.
[OID-03·§15.2](https://github.com/321Team/fds-project-baseline/blob/docs-v3.1.18/baseline/FDS_Project_Implementation_Baseline_v3.1.18.md)의
사전 반입 방식을 우선 권장한다. Runner 외부 허용 확대가 없는 준비안이며 실제 생산
호스트·담당·승인된 다운로드 경로와 반입 근거는 아직 미확정이다.
metadata 수집의 기존 60분/900초 승인을 layer/PyPI 공급에 사용하지 않는다.

## 확보된 입력

- 앱 관측 main: `254b82231f8af79a06419b581174647bbccbe2bc`. 최종 승인 Source는 아니다.
- 두 Dockerfile은 아직 `FROM python:3.12-slim` 및 일반 `RUN pip install`을 사용한다.
- [기존 metadata 증적](https://github.com/321Team/infra-cicd/issues/11)의 index/manifest/config
  연결은 PASS. base는 `docker.io/library/python@sha256:2fe5997d249a808b8eeea52c58a1dbffbba28754dc11699ef5c029f2d818ce79`.
- config ID는 `sha256:ec7d6c95cd3692a2e2d228a8b1ca74e4025b54121fcc4c5da6f09cfa473315ad`.
  linux/amd64, config의 PYTHON_VERSION 선언은 3.12.14. 이미지 실행으로 확인한 값은 아니다.
- 원본 manifest의 압축 layer는 4개, 합계 **46,175,163 bytes**. 이는 압축 blob 크기 합이며
  OCI tar 전체 크기·설치 후 디스크 사용량·충분한 여유 공간을 뜻하지 않는다. layer 원본은 미수신.
- 원본 requirements 4개와 Git blob/SHA-256, layer별 digest/size는
  [offline-supply-plan.json](offline-supply-plan.json)에 보존했다. 이 파일은 운영 config가 아니다.

| 패키지 묶음 | 직접 입력 | 대상 |
|---|---|---|
| transaction-api-runtime | fastapi 0.115.5, uvicorn[standard] 0.30.6, pydantic 2.9.2, sqlalchemy[asyncio] 2.0.35, asyncpg 0.29.0, httpx 0.27.2, prometheus-client 0.20.0 | 고정 base 이미지 |
| fds-engine-runtime | fastapi 0.115.5, uvicorn[standard] 0.30.6, pydantic 2.9.2, prometheus-client 0.20.0 | 고정 base 이미지 |
| transaction-api-test | 위 API runtime + pytest 8.3.3 | Runner 호스트의 새 venv |
| fds-engine-test | 위 engine runtime + pytest 8.3.3, httpx 0.27.2 | Runner 호스트의 새 venv |

이는 **직접 의존성**이며 전체 잠금 목록이 아니다. extras와 전이 의존성의 실제 파일·버전·해시는
아직 해석하지 않았다. Runner Python 관측값도 3.12.14지만 OS/libc/지원 wheel tag가 같다고
볼 수 없으므로 호스트 시험용과 이미지 실행용을 구분한다. 동일 wheel은 해시가 같을 때만
저장 중복을 줄이고, 각 대상의 검증 기록·lock은 별도로 유지한다.

## 반입물과 수용 조건

| 반입물 | 생산·인수 조건 |
|---|---|
| 단일 linux/amd64 OCI archive | 위 manifest/config와 참조 layer 4개를 포함한 uncompressed OCI tar. 생산 도구/버전·시각·승인 공급 경로, archive SHA-256 별도 기록 |
| 4개 환경별 wheelhouse + lock | 정확한 최종 Source·입력 requirements hash, 대상 Python/OS/libc/지원 tags, resolver/pip 버전, 직접·전이 패키지 전체 `==` pin 및 선택 wheel SHA-256 |
| 공급 inventory | 서비스/대상, 각 상대 파일명·크기·SHA-256, 생산자/호스트/승인 근거, 대상 Source, offline 해석·설치 결과. Token·인증 URL 제외 |
| 전송 영수증 | 기존 승인된 파일 전송 경로, 송신/수신 hash 일치, 수신 계정/위치. 새 SSH 목적지를 임의 허용하지 않음 |

승인된 외부 생산 환경에서 먼저 `--preserve-digests`를 지원하는 도구로 pinned manifest를
단일 OCI archive로 준비한다. 형식 변환·재압축으로 manifest digest가 달라지면 인수를 중단하고
새 값의 채택 근거를 검토한다. 단순 tag 변경·expected hash 재작성으로 통과시키지 않는다.
생산자의 archive hash를 수신 후 새로 계산한 값으로 대체하지 않는다.

wheel 준비는 대상 환경에서 해석하는 방식을 우선한다. Windows 호스트의 기본 `pip download`
결과를 Linux용으로 간주하지 않는다. 교차 다운로드가 필요하면 platform/python-version/
implementation/abi를 명시하되 dependency marker와 실제 대상 설치 검증도 필요하다.
`--only-binary=:all:`로 sdist를 거부하고, wheel이 없는 패키지는 명시적으로 중단한다.
자동 컴파일·임시 네트워크 개방·의존성 버전 완화로 우회하지 않는다.

전체 lock은 직접·전이 의존성의 버전과 선택 wheel hash를 포함한다. 외부 URL/VCS/editable,
원격 find-links/extra-index, 승인되지 않은 local path는 허용하지 않는 좁은 인수 프로필을 쓴다.
일반 `pip freeze` 출력만으로 배포파일 hash·공급 출처가 확보됐다고 보지 않는다.
생산 도구/버전과 실제 lock 형식을 정한 뒤 lock 생성 절차를 고정한다. 아직 결과를 만들지 않았다.

## 준비된 base archive 검증기

`tools/delivery/verify_base_archive.py`는 기존 `tools/trivy/oci_archive.py`를 재사용한다.
호출자가 독립적으로 보존한 archive SHA-256 및 승인 검토 대상 manifest/config digest를
모두 요구한다. file hash만 일치하고 layer가 누락·변조되거나 다른 정상 이미지이면 실패한다.

- 일반 파일·최대1GiB·uncompressed OCI tar·단일 OCI linux/amd64 이미지 프로필.
- manifest/config와 참조 layer의 실제 blob SHA-256·크기, rootfs diff_ids 형식/개수 검사.
- archive의 전후 inode/size/mtime/ctime/hash 변화 거부. symlink/FIFO/디렉터리 입력과
  archive의 경로 이탈·링크/특수 파일 거부. 압축 해제·load·실행·네트워크·chmod·삭제 없음.
- 완전한 OCI 범용 적합성 검사는 아니다. layer 압축 해제 후 DiffID 계산, 서명/출처 인증,
  취약점, wheel 호환성, Podman import/실행은 별도이다. 기존 검사기는 허용된 blob 경로의
  비참조 blob을 반드시 거부하지 않으므로 이를 엄밀한 최솟값 파일 목록 검증으로 표현하지 않는다.
- 같은 UID의 적대적 동시 경로 교체를 완전히 격리하는 sandbox는 아니다. 전송 완료 후
  쓰기 주체가 없는 검증 디렉터리를 사용하고 import 직전에도 같은 archive hash를 재확인한다.

아래는 **실제 반입 후 사용할 인자 형식**이다. 현재 archive/생산자 hash는 없으며 명령을
실행하지 않았다. 저장소 전체 checkout이 필요하고 sudo/Podman 설치는 필요 없다.

```bash
python3 -B tools/delivery/verify_base_archive.py \
  --archive /path/to/base.oci.tar \
  --expected-archive-sha256 '<생산자에게 별도로 인수한 64자리 SHA-256>' \
  --expected-manifest sha256:2fe5997d249a808b8eeea52c58a1dbffbba28754dc11699ef5c029f2d818ce79 \
  --expected-config sha256:ec7d6c95cd3692a2e2d228a8b1ca74e4025b54121fcc4c5da6f09cfa473315ad
```

rc0/BASE_ARCHIVE_CHECK=PASS는 참조 blob 인수 검사만 통과한 것이다. SUPPLY_APPROVAL,
REGISTRY_PROVENANCE, PYTHON_DEPENDENCIES, DELIVERY_GATE는 NOT_VERIFIED,
IMAGE_IMPORT/IMAGE_SCAN/NETWORK는 NOT_EXECUTED로 유지한다. 실패는 고정 오류 코드만
stdout JSON에 출력하고 rc1이다. 합성 OCI 시험의 성공을 실제 layer 확보로 보고하지 않는다.

## 실행기·앱에 필요한 후속 변경

현재 main `90f2f37`의 `build_scan.execute`는 새 `evidence/storage`를 만들고 서비스마다
원격 base-pull을 강제한다. 셸에서 기본 Podman 저장소에 미리 load해도 이 저장소와 다르다.
앱 Build 소스도 Git archive로 새로 만들므로 미추적 wheelhouse를 앱 폴더에 복사해 두는 것으로는
빌드에 전달되지 않는다. 이번 PR은 아래 통합을 **아직 구현·승인하지 않는다**.

1. 운영 입력에 최종 Source·승인된 supply inventory/hash·정확한 로컬 반입물 경로를 연결한다.
   기본 PENDING_APP_HANDOFF 차단과 Source/Workflow 승인 조건은 유지한다.
2. 생성된 같은 rootless Podman storage/runroot에 검증한 OCI archive를 load하고, 실제
   repository@manifest digest가 로컬에서 해석되는지 확인한다. 단순 tag 추가만으로 원 digest
   참조가 해석된다고 가정하지 않는다. 실패 시 registry pull fallback 없이 중단한다.
3. 직접 원격 pull을 대체하고 build에 `--pull=never --network=none`을 적용한다.
   검증한 wheelhouse를 별도 추가 build context로 제공하여 원본 앱 snapshot을 조용히 수정하지 않는다.
4. 앱 담당이 Dockerfile에 정확한 FROM 및 명시적 offline 설치를 반영한다. 권장 형태는
   `COPY --from=python-wheels / /opt/wheels/`와 hash lock의 COPY, 그리고 다음 설치다.
   `python -m pip install --no-index --find-links=/opt/wheels --only-binary=:all: --require-hashes -r requirements-runtime.lock`.
   실제 lock 이름/경로와 build-context 이름은 양쪽 구현에서 함께 고정한다. wheel 파일을 이미지에
   포함하는 이 단순안의 용량도 검토한다. 운영 이미지에는 시험용 wheel/pytest를 넣지 않는다.
5. Runner 시험 setup은 별도 venv에서 각 test lock/wheelhouse로 같은 offline 설치 제약을 사용한다.
   pip 설정·환경의 원격 index/find-links 및 사용자 site 영향을 통제한다. 이 옵션만으로 악성
   패키지/앱 코드의 일반 네트워크 행위가 격리되는 것은 아니다.
6. 네 대상에서 새 환경의 offline resolve/install 및 `pip check`를 확인하고 Unit/Contract 실행.
   missing wheel·잘못된 tag/hash·미승인 Source·digest 미해석·network fallback 경로를 실패 시험한다.
7. 실제 Build→유효 Trivy DB→HIGH/CRITICAL 및 스캐너 오류 차단. Scan PASS 전 Push/Deploy 없음.

## 현재 완료 / 다음 입력

이번에 완료한 것은 실제 metadata 기반 공급 inventory, requirements 원본 hash·4개 대상 분리,
실행기 통합 누락 분석, base archive 검증기·합성 실패 시험이다. 실제 layer/wheel 다운로드,
Skopeo/패키지 설치, archive import, 앱 변경, Build/Scan, 방화벽/DNS/NAT 수정은 수행하지 않았다.

다음 실제 생산에 필요한 입력은 **생산 호스트·담당·기존 승인 공급 경로**와
**생산 요청 대상으로 고정할 requirements/Source**다. 먼저 그 입력으로 lock·wheel을 생산하고,
앱 pin/lock 변경을 반영한 최종 Source SHA 승인은 실제 앱 실행 전에 완료한다.
현재 metadata는 재수집할 필요가 없다. 준비 코드 독립 리뷰와
공급 정책·생산 승인, 실제 인수, 앱 Runtime 판정을 분리하며 Runtime AC1/10·Delivery 미검증 유지.

공식 동작 근거(2026-09-17 확인):

- [pip download: target options와 wheel 수집](https://pip.pypa.io/en/stable/cli/pip_download/)
- [pip secure installs: 전체 의존성 hash 요구](https://pip.pypa.io/en/stable/topics/secure-installs/)
- [Podman load](https://docs.podman.io/en/latest/markdown/podman-load.1.html)
- [Podman 6.1 build: 추가 context·network·pull](https://docs.podman.io/en/v6.1.0/markdown/podman-build.1.html)
- [Skopeo copy: preserve-digests](https://github.com/containers/skopeo/blob/main/docs/skopeo-copy.1.md)
