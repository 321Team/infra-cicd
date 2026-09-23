# 수집한 공급물을 실제 Test·Build·Scan에 연결

현재 현황(2026-09-24 KST): 공급 probe와 실제 앱 Test/Build·첫 이미지 Scan 실행은 완료됐고 transaction-api는 취약점 BLOCK, fds-engine은 NOT_SCANNED다. app-evidence 수신 검증 완료·[증적 PR22](https://github.com/321Team/fds-project-evidence/pull/22) 독립 리뷰 대기이며 Runtime AC1/10·Delivery NOT_VERIFIED를 유지한다. [최신 후속·후보·인계 조건](remediation-candidates-20260924.md)을 따른다.

이하 내용은 09-17 최초 구현 당시의 기록이다. 당시 `PENDING_APP_HANDOFF`, Source/Unit null, 실제 앱 미실행·리뷰 대기 설명을 현재 상태로 사용하지 않는다. 현재 config는 승인 Source `ec69b67067decf35abbfed0a6a99f3073132787b`다. PR18 다운로드 경로는 09-22 종결됐고 기존 공급물 소비 코드는 유지된다.

2026-09-17 사용자 “권장안으로 착수”를 반영한다. 공급 Runtime은 [Issue11](https://github.com/321Team/infra-cicd/issues/11)에 제출된 증적 기준 PASS다. 이번 변경은 소비 실행기와 독립된 base 조회·빌드 시험을 추가한다. 실제 앱 Test→Build→Scan은 미실행이며 최종 앱 SHA/승인과 독립 코드 리뷰가 남아 있다.

## 입력과 코드 계승

- PR15 `5bc38b3aa41b2b32eab535ef317e033c42e8f1a1`의 명시적 Unit 해당 없음 처리와 시험을 그대로 계승한다. 둘 다 Unit을 생략할 수 없고 API `unit=null`은 계속 미확정이다. PR15의 독립 리뷰를 대신하거나 병합 완료로 취급하지 않는다.
- PR18 `73db58f21711bc4712c2bd3b8a16599fe498995f`에서 생산한 공급물을 소비한다. 수집기 코드 자체를 바꾸거나 재수집하지 않는다.
- `config/app-build-scan.json`의 schema2에 서버 경로, OCI transport/manifest/config hash, profile4개 roots/lock/inventory hash, 원본 requirements4개 hash를 고정했다. 이는 공급 인수 식별자이며 앱 Source 실행 승인은 아니다.
- 과거 schema1 호출은 호환성을 유지한다. 현재 기본 설정은 schema2이며 `PENDING_APP_HANDOFF`, Source 승인값 null, API Unit null을 유지한다. `build_scan.py` 기본 호출은 명령 실행 없이 exit1이다.

공급 경로는 `/home/server01_cicd-runner/FDS_CICD_Supply.20260917T010757Z.974fb972`다. 이 경로의 기존 자료·venv·Podman 저장소는 보존한다. 원본 OCI/wheel은 아직 Codex에 제출되지 않았으므로 **새 소비 실행에서 실물 hash를 직접 재검사**한다.

## schema2 순서

1. 기존 main/수동 실행/전용 Runner/UID1000/Workflow SHA 및 입력 바이트 결합을 유지한다. 앱의 승인 SHA와 origin을 확인하고 git archive로 test/build 사본을 분리한다.
2. 새 실행 디렉터리에 OCI, profile별 roots/lock/inventory/wheel을 복사한다. 모든 경로 구성요소의 symlink, 특수파일, 기존 출력 디렉터리, 파일 변경·누락·추가·중복·hash 불일치를 거부한다. OCI 참조 blob/hash/OS/architecture를 검사하고 inventory↔lock↔root version/extras를 대조한다. wheel metadata의 외부 URL 의존성과 위험한 ZIP 경로도 거부한다. wheel은 개당64MiB, OCI+복사 wheel은 총1GiB 한도다.
3. 최종 앱 Source의 기존 requirements4개 hash, 수집 roots, 새 runtime/test lock을 검증한다. Dockerfile은 인계 폴더의 한 단계 오프라인 형식만 지원한다. 임의 ADD·외부 COPY·frontend·추가 FROM·빌드 코드 변경은 허용하지 않는다. Source 파일을 실행 중 수정하지 않는다.
4. 실행별 rootless VFS storage/runroot에서 OCI를 load하고 config ID에 repository의 로컬 보조 tag를 부여한다. 이어 **원래 `docker.io/library/python@sha256:...` 자체**를 image inspect한다. Id, Digest, RepoDigests, OS/architecture, RootFS가 모두 일치해야 한다. config ID만 보이거나 digest 조회가 안 되면 중단한다. 원격 pull 또는 FROM 치환으로 우회하지 않는다. 이 연결은 실제 서버 probe 결과 전까지 Runtime 미검증이다.
5. host/test2개는 Source 사본에 새 `.venv`를 만들고 `--isolated --no-index --only-binary=:all: --require-hashes`와 해당 test wheelhouse/lock으로 설치·pip check한다. 기존 `.venv`가 Source에 있으면 거부한다. 일반 setup 배열은 비어 있어야 한다. 환경의 proxy/상위 token/임의 pip 설정을 전달하지 않는다.
6. 승인된 Unit/두 Contract를 실행한다. 시험 후와 각 Build 직전에 공급 사본의 hash를 재확인한다. Build는 새 committed Source 사본과 **해당 runtime wheelhouse만** `--build-context python-wheels=...`로 받는다. `--pull=never --network=none --http-proxy=false --no-cache`와 빈 authfile을 사용한다.
7. 두 OCI 산출물의 config/manifest/archive identity를 기존 Scan 어댑터에 전달한다. 취약점 BLOCK·DB/검사 오류·실제 검사 범위·Source/Run/Attempt 결합은 유지한다. Scan 어댑터의 승인된 최신 Trivy DB 취득 절차는 그대로이므로 **파이프라인 전체가 네트워크 없이 동작한다고 주장하지 않는다**.

Podman의 추가 local build context와 pull/network 옵션은 [6.1.0 공식 Build 문서](https://docs.podman.io/en/v6.1.0/markdown/podman-build.1.html)에 따른다. local 보조 tag는 새 store 안의 이름 매핑이며 Dockerfile은 계속 원본 manifest digest를 사용한다. 실제 조회/빌드 성공은 아래 probe 또는 실제 Run으로 판정한다.

## 독립된 실서버 공급 소비 시험

`probe_offline_supply.py`는 최종 앱 승인 없이 수행할 수 있는 **공급 인수/정확한 base FROM의 최소 Build 시험**이다. 앱 checkout·pytest·앱 image build·Trivy·Push/Deploy를 호출하지 않는다. source SHA/Unit 승인을 임의 생성하지 않는다.

검토한 소스에서 `build_offline_probe_bundle.py`로 만든 zipapp에 config의 공급 hash를 내장한다. 사용자 계정 `server01_cicd-runner`, 호스트 `cicd-runner01`, UID1000, 기존 home0700, 여유4GiB 및 Runner.Worker 부재를 확인한다. 전용 flock과 새 `FDS_CICD_OfflineProbe.*` 폴더만 사용하고 전체900초 제한을 둔다. Worker 부재는 GitHub 스케줄러 예약을 뜻하지 않는다.

실행 파일과 SHA는 PR/인계 답변의 같은 버전을 사용한다. 서버에서 `sha256sum -c` 성공 후 `/usr/bin/python3 -I -B <검증한 번들.pyz> --prepare`를 실행한다. 완료 또는 오류 후 출력된 **새 폴더의** `offline-probe-evidence.tar.gz` 및 `.sha256`를 반환한다. 기존 `runner_supply_73db58f.pyz --collect`는 다시 실행하지 않는다.

probe는 공급 사본 검증 → rootless load/tag/digest inspect → literal digest FROM과 Python print만 있는 최소 Build → 공급 사본 재검사 순서다. `OFFLINE_PROBE=PASS`도 실제 앱 실행 완료가 아니다. digest 조회 실패는 `REPOSITORY_DIGEST_RESOLUTION=FAIL`, 이후 Build는 `NOT_EXECUTED`로 남는다.

원본 공급 폴더·홈 소유권/권한·Runner 서비스·DNS/방화벽/NAT/CA·전역 저장소를 정리하거나 바꾸지 않는다. Podman은 기존 `/run/user/1000` 사용자 runtime을 사용하며 storage/runroot/tmpdir은 새 폴더 아래다. `label=disable`은 **새 Build 컨테이너에만** 적용하며 호스트 SELinux 정책 변경 또는 container_t MAC 격리 증명으로 표현하지 않는다. 기존 권한을 보존하므로 경로 접근이나 label 문제는 실패로 반환한다.

## 앱 인계와 남은 실행 조건

[앱 인계 폴더](../handoff/offline-app-20260917/)의 Dockerfile2개, 실제 생산 lock4개를 앱 담당이 검토해 최종 Source에 반영한다. 기존 requirements4개를 변경하면 새 공급 판단이 필요하다. runtime lock은 pinned image용, test lock은 Runner host용이며 서로 바꾸지 않는다. Python3.12만 같다는 이유로 다른 플랫폼에 적용하지 않는다.

API Unit은 필요한 명령 또는 해당 Source에 대한 명시적 해당 없음 사유/승인 근거가 필요하다. 엔진 Unit `test_rules.py`, 양쪽 Contract `test_health.py`+`test_metrics.py`는 인계 후보이며 이번에 실행한 결과가 아니다. 이후 최종40자리 SHA·앱 승인·CICD 전체 변경 독립 리뷰·승인 main에서 중앙 Workflow/읽기 전용 checkout 연결을 완료한 뒤 실제 Test→Build→Scan을 수행한다. 이번 변경은 운영 Workflow를 새로 활성화하지 않는다.

## 검증 한계

시험은 실제 임시 Git 사본, OCI/wheel 바이트 변조, symlink·직접 URL·lock/요구사항 불일치, 명령 실패, digest 조회 실패, 시험 후 변조 차단을 포함한다. 작은 합성 wheel은 실제 새 venv의 pip로 `--no-index --require-hashes` 설치·pip check한다. Podman/Trivy는 명령 대역이며 내부 서버의 digest Build 또는 실제 앱 Runtime PASS가 아니다. 같은 UID의 악성 코드에 대한 sandbox/원격 attestation/방화벽 집행도 아니다.
