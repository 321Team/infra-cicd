# Base manifest 원본 오프라인 대조

상태: **PREPARATION / 실제 공급 자료 미수신**. 관련 [#11](https://github.com/321Team/infra-cicd/issues/11).
이 도구는 경로 승인 후 받은 manifest 자료를 대조하기 위한 선행 준비다.
실제 Docker Hub/PyPI 접속·승인·이미지 Pull·설치·Build/Scan을 수행하지 않는다.

## 해결하는 문제

기존 [PR #13 공급 인계 절차](https://github.com/321Team/infra-cicd/pull/13)는 원본 index,
linux/amd64 child manifest, config를 인계하도록 안내하지만 이를 자동 대조하는 도구는 없었다.
이미지의 index digest·단일 플랫폼 manifest digest·config ID는 서로 다른 값이다.
`tools/delivery/verify_base_manifest.py`는 원본 바이트의 SHA-256·descriptor size와
플랫폼 연결을 확인해 `docker.io/library/python@sha256:<manifest digest>` 후보를 출력한다.
보기 좋게 다시 저장한 JSON을 원본과 같은 digest라고 간주하지 않는다.

기존 `oci_archive.py`는 **Build 후 OCI archive와 layer blob**을 검사한다.
이번 도구는 **Build 전 공급 manifest 자료**를 검사하므로 해당 코드의 동작을 바꾸지 않는다.
공통 `PreflightError`·중복 JSON key 검사만 재사용한다. 외부 패키지가 필요 없다.

## 입력과 판정 범위

| 입력 | 실제로 확보해야 할 자료 |
|---|---|
| `--index` | 선택사항. registry가 반환한 index/manifest list 원본 바이트 |
| `--manifest` | 선택한 linux/amd64 image manifest 원본 바이트 |
| `--config` | manifest의 config descriptor가 가리키는 JSON 원본 바이트 |
| `--expected-digest` | index가 있으면 그 응답 digest, 직접 manifest 응답이면 해당 digest. 별도 수집 기록과 대조할 값 |

최상위 raw 바이트를 호출자가 제공한 expected digest와 먼저 대조한다.
index가 있으면 linux/amd64 descriptor가 **정확히 하나**인지 확인하고 child manifest의
size·digest·mediaType을 비교한다. manifest의 config size·digest를 비교한 뒤 config의
OS/architecture도 linux/amd64인지 확인한다. index 없이 받은 직접 manifest도 지원한다.

**검증기의 expected digest와 repository 이름은 신뢰 근거가 아니다.** 원본과 expected 값을
함께 조작해도 내부 일관성 검사는 통과할 수 있다. 코드의 고정 repository label은 현재 공급
후보를 나타낼 뿐 실제 Docker Hub에서 받은 파일임을 증명하지 않는다. 수집 주체·승인 경로·
조회 UTC 시각·repository/tag·도구 버전·TLS/응답 기록과 별도로 연결해야 한다.
다른 registry/mirror 공급을 이 label로 바꾸어 보고하지 않는다.

지원 프로필은 OCI image index/manifest/config와 Docker schema2 list/manifest/config다.
다음은 이 프로젝트 도구의 제한이며 범용 OCI 적합성 요구나 새로운 앱 AC가 아니다.

- SHA-256만 지원. UTF-8 JSON object, 중복 key·NaN/Infinity 거부, 파일별 1 byte~4 MiB.
- index 1~256개 descriptor 중 linux/amd64가 하나여야 한다. 여러 개면 첫 항목을 임의 선택하지 않는다.
  다른 플랫폼·unknown/unknown attestation 항목은 선택하지 않는다. nested index는 따라가지 않는다.
- 선택한 index/manifest/config에는 추가 CPU variant·OS version/feature 요구를 허용하지 않는다.
  선택한 image의 artifact/subject와 descriptor의 `urls`/`data`/artifactType은 지원하지 않는다.
- OCI manifest는 OCI config와 OCI tar/gzip/zstd layer descriptor, Docker manifest는 Docker config와
  gzip layer descriptor를 요구한다. 혼합 config 형식·foreign/nondistributable layer는 지원하지 않는다.
- layer 1~512개 descriptor의 타입·SHA-256 형식·양의 정수 size 및 config rootfs/diff_ids 개수·형식을
  확인한다. **layer 파일을 받지 않으므로 실제 layer hash·압축 해제·DiffID 일치는 검증하지 않는다.**
- image mediaType 필드를 명시적으로 요구한다. 범용 규격에서 선택 가능한 형태도 이 프로필에서
  지원하지 않으면 실패로 남긴다. 실패한 자료를 자동 변환하거나 해시를 재작성하지 않는다.

## 실행과 파일 보호

Linux/Python 3.11 이상, 이 코드가 있는 전체 저장소 checkout에서 실행한다.
Skopeo·Docker·Podman 설치와 sudo가 필요 없다. 입력은 미리 받은 로컬 파일이어야 한다.
아래는 **인자 형식 예시**이며 아직 없는 실제 파일이나 digest를 제공한 명령이 아니다.

```bash
python3 tools/delivery/verify_base_manifest.py \
  --index /path/to/index.raw.json \
  --manifest /path/to/linux-amd64.manifest.raw.json \
  --config /path/to/config.raw.json \
  --expected-digest 'sha256:<수집한-index-응답의-64자리-값>'
```

직접 image manifest 응답이면 `--index`를 생략하고 해당 manifest의 응답 digest를 사용한다.
결과 JSON은 stdout으로만 출력한다. 파일 출력 경로 인자를 받지 않으며 입력/홈에 쓰거나
chmod/chown/delete/extract하지 않는다. 원본 JSON·환경변수·config Env·예외의 경로 원문은 출력하지 않는다.

입력의 마지막 경로가 symlink이면 열지 않는다. 동일 FD에서 일반 파일·크기를 확인한 뒤 제한된
바이트만 읽고, 읽은 전후 FD와 현재 경로의 inode/size/mtime/ctime을 대조한다. 디렉터리·FIFO·장치도
거부한다. 상위 디렉터리의 symlink까지 금지하는 sandbox는 아니며 같은 UID의 악성 프로세스에 대한
완전한 격리를 보장하지 않는다. 이 검사는 입력 파일이나 계정 홈 권한을 변경하지 않는다.

| 결과 | 의미 |
|---|---|
| rc0 / `BASE_MANIFEST_CHECK=PASS` | 제출된 raw 파일 간 해시·크기·지원 프로필·플랫폼 연결 일치 |
| rc1 / `BASE_MANIFEST_CHECK=FAIL` | 자료 불일치·미지원·읽기/JSON 오류. 고정 오류 코드만 출력; base 후보 없음 |
| rc2 | 필수 인자 누락 등 CLI 사용 오류; argparse usage 출력 |

성공 결과에는 index/manifest/config digest를 각각 보존하고 **child manifest digest**로만
`base_reference_candidate`를 만든다. `NETWORK=NOT_EXECUTED`, `REGISTRY_PROVENANCE=NOT_VERIFIED`,
`SUPPLY_APPROVAL=NOT_VERIFIED`, `LAYER_BLOBS=NOT_VERIFIED`, `IMAGE_SCAN=NOT_EXECUTED`,
`DELIVERY_GATE=NOT_VERIFIED`를 유지한다. rc0만으로 실제 base 채택·이미지 안전성을 승인하지 않는다.

## 실행 전후 인계 순서

1. 기존 OID-03 경로·목적지·Owner·Expiry 또는 사전 반입 근거를 확정한다. IF-08 GitHub 성공을
   registry/PyPI 허용으로 간주하지 않는다. 실제 네트워크 수집 명령은 이 도구에 포함하지 않는다.
2. 승인된 수집에서 응답 digest와 원본 바이트를 함께 보존한다. `jq`/재직렬화본으로 대체하지 않는다.
3. 이 도구로 오프라인 대조 후 공개 결과와 원본 무결성 기록을 #11에 연결한다.
   FAIL이면 해당 자료만 재확인하며 DNS/TLS/방화벽을 바꾸어 우회하지 않는다.
4. 공급 출처와 linux/amd64 manifest를 별도로 검토한 뒤 앱 담당에게 전체 base reference를 인계한다.
   두 Dockerfile pin·시험 범위·최종40자리 Source SHA 승인 후 CICD 입력을 완성한다.
5. 변경 HEAD의 독립 리뷰·운영 Workflow 검토를 거쳐 승인된 내부 Runner Test→Build→Scan을 수행한다.
   이번 준비 코드가 자동으로 이 단계를 시작하거나 config를 승인 상태로 바꾸지는 않는다.

현재 실제 index/manifest/config·expected digest를 확보한 것이 아니므로 구체 digest는 비워 둔다.
기존 로컬 수집 자료와 앱32 PASS 보고를 다시 제출할 필요는 없다.

## 검증과 규격 근거

신규 시험은 합성 raw 파일로 OCI/Docker 및 직접 manifest 정상 연결, 세 digest 구분, 바이트 변형,
size/hash 불일치, 중복/누락 플랫폼, config 플랫폼 불일치, 미지원 형식·외부 URL·잘못된 JSON,
디렉터리·symlink·FIFO·경로 교체, 기존 파일/홈 mode 보존과 오류 출력 비밀값 제외를 확인한다.
실제 registry/image Runtime 성공을 주장하지 않는다. CI 실행 결과는 해당 PR의 Run에 기록한다.

작성 전 기존 공급 문서·Build/Scan·OCI archive 검증기를 대조했고 다음 1차 자료를 확인했다.

- [OCI v1.1.0 Index](https://github.com/opencontainers/image-spec/blob/v1.1.0/image-index.md): 플랫폼 descriptor와 index 연결.
- [OCI v1.1.0 Descriptor](https://github.com/opencontainers/image-spec/blob/v1.1.0/descriptor.md): 원본 바이트의 digest/size 비교.
- [OCI v1.1.0 Manifest](https://github.com/opencontainers/image-spec/blob/v1.1.0/manifest.md),
  [Config](https://github.com/opencontainers/image-spec/blob/v1.1.0/config.md): config·layer 연결, OS/architecture와 ImageID 의미.
- [CNCF Distribution schema2](https://distribution.github.io/distribution/spec/manifest-v2-2/): Docker list/manifest/config media type과 descriptor 연결.

이 도구는 정본의 공급·승인 조건을 보조하며 정본·AC·담당·기존 배포 게이트를 변경하지 않는다.
