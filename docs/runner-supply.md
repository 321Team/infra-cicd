# Runner에서 공급 파일 생산 — 채택 범위 03

사용자 이권욱의 자원 추천 후 **“착수”**로 `P1-CICD-OID03-20260917-03`을 채택한다.
기존 정본 v3.1.18 OID-03 / §15.2의 Destination·Owner·Expiry를 구체화한 입력이며 정본의 새 Decision ID를 만들지 않는다.
기존 01(metadata 60분), 02(config 900초) 작업창을 수정·연장하지 않는다.

| 항목 | 이번 범위 |
|---|---|
| 생산자 | cicd-runner01 / 10.250.10.20 / server01_cicd-runner / UID1000, 이권욱 |
| 정책 리드·코드 대체 리뷰 | 이하영 / 이재환(supreme33-kr) |
| 외부 목적지 | auth.docker.io, registry-1.docker.io, production.cloudfront.docker.com, pypi.org, files.pythonhosted.org — HTTPS443 |
| 시작·종료 | 도구 사전 검사 후 첫 다운로드 직전 T0부터 최대3600초; 완료하면 종료, 재시도로 연장하지 않음 |
| 이미지 | 관측 manifest `sha256:2fe5997d249a808b8eeea52c58a1dbffbba28754dc11699ef5c029f2d818ce79`의 layer4개, 압축46,175,163bytes |
| Python | 관측 앱 SHA254b82231f8af79a06419b581174647bbccbe2bc의 requirements4개. API/engine runtime2개와 Runner test2개 |
| 후속 단계 | 실제 공급 증적 검토 → 앱 pin/lock 반영·최종 Source 승인 → 실행기 offline 통합·리뷰 → 앱 Test/Build/Scan |

생산 Source는 requirements 입력을 식별한다. 앱 실행 승인 SHA는 여전히 미확정이다.
독립 리뷰·병합과 사용자 공급 범위 채택도 구분한다. Runtime AC1/10, DELIVERY_GATE NOT_VERIFIED 유지.

## 실행 동작

`collect_runner_supply.py`와 보조 모듈을 `build_supply_bundle.py`로 결정적 zipapp 하나에 묶는다.
계정·호스트·홈 소유자/0700·현재 여유4GiB·proxy/CA override·Runner.Worker 부재를 먼저 확인한다.
이미 실행 중인 공급은 flock으로 중단한다. Runner.Worker 확인은 각 요청/명령 전의 관측이며 CI 스케줄러 예약은 아니다. Actions 작업이 없는 시간에 실행한다.
기존 홈 파일·권한·서비스·DNS/NAT/방화벽·전역 Podman 저장소는 변경하지 않는다.
홈 아래 새 `FDS_CICD_Supply.<UTC>.<random>`에만 venv·storage·runroot·파일을 생성한다.

1. host venv와 bundled pip, Podman 존재를 확인한다. venv/ensurepip가 없으면 추가 설치 없이 실패하며 다운로드 창을 시작하지 않는다.
2. 별도 `FDS_CICD_OID03_20260917_03.json`에 T0/expiry를 최초 생성한다. 만료되면 재실행도 중단한다. 기존 기록을 지우거나 수정하지 않는다.
3. 기존 검증 manifest/config 원본은 번들에 포함해 재사용한다. 고정 layer만 수신하고 compressed hash/size와 gzip 해제 후 DiffID까지 대조한다. 일반 OCI tar를 생성한다. registry307→정확한 CDN443로 한 번만 따라가며 Bearer를 전달하지 않는다. 추가 redirect는 중단한다.
4. 새 rootless VFS 저장소로 OCI를 load한다. config ID·OS·architecture를 확인한 뒤 이 ID로 공급용 컨테이너만 실행한다. repository@manifest digest가 load 후 해석되는지는 별도 후속 통합 과제이며 PASS로 만들지 않는다.
5. host와 실제 pinned image 각각 Python/pip/호환 tag를 기록한다. 같은 Python 버전이라는 이유로 호환성을 가정하지 않는다.
6. pip가 각 native target에서 wheel만 해석한다. 별도 수집기가 PyPI JSON simple API와 files.pythonhosted.org로만 접속한다. pip에 주는 index에는 검증된 wheel 링크만 있고 sdist/원격 metadata 링크는 없다. wheel SHA·METADATA Name을 검사하고 직접 URL/VCS/file 의존성은 pip에 전달하기 전에 중단한다.
7. 이미지 내부는 모든 단계 `--network=none --pull=never --read-only`, cap-drop, no-new-privileges, 1CPU/1GiB/128PID/timeout을 사용한다. IPC는 새 작업 폴더의 Unix socket 및 컨테이너 내부 loopback relay다. host networking을 사용하지 않는다. 새 컨테이너에만 SELinux label isolation을 비활성화하여 해당 socket/bind mount를 사용한다; 호스트 SELinux 설정은 바꾸지 않는다.
8. runtime/test별 선택된 전체 의존성에 `==version --hash=sha256:...` lock과 wheel inventory를 만들며 root extras를 보존한다. 각 대상의 새 venv에 `--no-index --find-links --only-binary=:all: --require-hashes` 설치와 `pip check`를 수행한다. host 설치는 pip의 offline 옵션을 사용하고 image 설치는 network namespace도 차단한다.

도구 상한: 전체 외부수신1GiB/2048요청, project256개, wheel당64MiB, wheel cache256MiB, index당32MiB, layer 해제512MiB. sdists·추가 host·기한/상한 초과는 자동 대체/확대하지 않는다.
수집기 wall/monotonic 기한·SIGALRM, HTTP timeout, child timeout 및 컨테이너 timeout을 적용한다.
실패해도 부분 결과·새 디렉터리는 증적으로 남긴다. 재실행은 같은 expiry 안에서 새 디렉터리를 사용한다.
정상 계정의 정지된 입력을 전제로 하며 동일 UID의 악의적 동시 파일 교체에 대한 sandbox를 주장하지 않는다.
클라이언트의 목적지 제한은 방화벽/패킷의 독립 관측 증적이 아니다.

## 배포·서버 실행

검토된 repo source에서 새 경로에 번들을 만든다. 출력 SHA256을 인수하고 다른 해시 파일을 실행하지 않는다.

```bash
python3 tools/delivery/build_supply_bundle.py /new/path/runner_supply.pyz
```

해당 번들을 Runner `/home/server01_cicd-runner/`로 복사한 뒤, 전달받은 파일명과 SHA256을 검증하고 실행한다.

```bash
/usr/bin/python3 -I -B /home/server01_cicd-runner/runner_supply.pyz --collect
```

실행 시 artifact의 버전별 파일명과 정확한 checksum은 인계 응답에 제공한다.
`--help`는 네트워크/출력 파일/작업창을 만들지 않는다. 실제 서버 실행은 Codex CI 시험과 별도다.

## 증적과 판정

- `base.oci.tar`, `.sha256`: layer 포함 공급 이미지. 원본 manifest/config digest와 archive transport hash를 구분한다.
- `<profile>/wheels`, `requirements.lock`, `inventory.json`, `roots.txt`: 네 가지 대상별 고정 공급물.
- host/image-target.json, command stdout/stderr, result.json: 도구/호환성/설치 결과 및 요청 영수증. CDN URL·auth token 비보존.
- **제출용 `supply-evidence.tar.gz` + `.sha256`**: 결과/명령 로그/대상 정보/완료된 profile의 lock·inventory와 내부 SHA256SUMS. 큰 OCI/wheel/venv/storage는 서버에 남긴다.

실패 시 result.error_code와 명령 stdout/stderr를 함께 검토한다. PASS는 본 공급 생산·검사 범위이며 앱 시험/이미지 Build·취약점 Scan/Push/Deploy/전체 Delivery를 의미하지 않는다.
현재 build_scan의 강제 원격 pull, 매 실행마다 새 storage, Dockerfile의 일반 pip install, committed snapshot의 별도 wheel context 문제는 이 도구만으로 해결되지 않는다. [통합 명세](offline-app-supply.md)를 후속 구현한다.

공식 동작 근거: [PyPI JSON index API](https://docs.pypi.org/api/index-api/), [pip download](https://pip.pypa.io/en/stable/cli/pip_download/), [pip hash-checking](https://pip.pypa.io/en/stable/topics/secure-installs/), [Podman6.1 run](https://docs.podman.io/en/v6.1.0/markdown/podman-run.1.html).
