# Trivy 갱신 운영과 DB 선행 검사

정책: **2026-09-16 사용자 채택 / 2026-09-22 채택 기록 PR #28 병합 완료**.
현황 정정: **2026-09-24 KST**. DB 선행 검사 PR #9는 독립 리뷰·병합 완료이며,
[Run 35061666680/1](https://github.com/321Team/infra-cicd/actions/runs/35061666680)은 success다.
9/16 수신 기록의 DB 준비 Runtime PASS와 실제 앱 Delivery 인수는 구분한다.
상위 작업: [P1-CICD-01 #16](https://github.com/321Team/fds-project-baseline/issues/16).
운영 추적: [#8](https://github.com/321Team/infra-cicd/issues/8).
당시 정본 후보 등록 이력: [v3.1.19 PR #73](https://github.com/321Team/fds-project-baseline/pull/73),
[채택 상세](https://github.com/321Team/fds-project-baseline/blob/14876f4d59caeef096f6caf2a76d9957546eea4d/audit/v3.1.19/intake/p1-cicd-trivy-update-policy-20260916.md).

## 기준과 책임

이권욱이 구현·최종 판정하고 이하영이 독립 기술 리뷰한다. 바이너리의 버전과 SHA-256을
고정하고, 새 릴리스 또는 보안 공지의 영향·출처·체크섬·옵션/종료코드 호환성·복구본을
검토한 뒤 관리자가 교체한다. 배포 Job에서 latest 설치나 sudo 자기 갱신은 하지 않는다.
현재 검증한 0.74.0을 무기한 유지하거나 최신 보안 적합성 검토가 끝났다는 뜻은 아니다.
변경 시 기존 정상 바이너리와 고정값을 복구 가능한 상태로 보관하고, 실행 중인 Job이
없는 점검 시점에 교체한다. 변경된 실제 도구 동작을 검증한 뒤 다음 Delivery에 사용한다.

승인된 바이너리 고정값은 `verify_runner.py`의 상수와 동일하다. 새 DB 도구는 이 상수를
가져오며 파일 해시도 같은 `sha256()` 함수를 재사용한다. 두 구현의 바이너리 SHA-256 고정값과
파일 해시 함수를 공유한다. 보고서의 버전 문자열 검사는 각 검사 코드에 있으므로
버전 변경 시 함께 대조한다. 상수 변경은 기존
toolcheck의 버전 판정 및 탐지/오류 계약도 함께 검토해야 한다.

기존 [9/14 결정](https://github.com/321Team/fds-project-baseline/issues/16#issuecomment-5659051242)의
미해결 HIGH/CRITICAL·스캐너/DB 오류 Push/Deploy 차단, CVE·Digest·사유·만료·승인자 단위 예외,
Source/Workflow SHA와 Run/Attempt 및 Digest 추적을 유지한다. Harbor Scanner는 P1-HBR,
CI Scan/Digest는 P1-CICD 책임이다. 기존 AC·역할·권한·네트워크 경로·일정을 확대하지 않는다.

## DB 선행 검사 구현 범위

`.github/workflows/trivy-db-preflight.yml`은 main의 수동 실행만 허용하며,
내부 Runner에 준비된 Trivy로 **공식 GHCR DB를 새 Run 전용 캐시에 준비**한다.
각 Run에서 새 캐시를 사용하므로 다운로드가 필요하다. 일괄 자동 바이너리 갱신은 없다.

실행기는 `/usr/bin/python3`이며 Python 3.11 이상이 필요하다. 기존 파일 해시 함수의
`hashlib.file_digest()` 의존성을 명시하고, DB의 RFC3339Nano 시각 파싱도 같은 지원 범위에서
검증한다. 실제 버전과 실행 경로를 로그 및 성공적으로 생성된 context 증적에 기록한다.
3.11 미만이면 `PYTHON_3_11_REQUIRED`로 증적 디렉터리 생성·해시·통신 전에 종료한다.
자동 Python 설치/업그레이드는 하지 않는다. 9/16 Run 35061666680/1의 수신 기록에는
`/usr/bin/python3` 3.12.14가 확인돼 있다([이슈 #8](https://github.com/321Team/infra-cicd/issues/8)).
이는 해당 Run 시점의 값이며 현재 호스트 버전을 다시 측정한 결과는 아니다.

1. 호스트·계정/UID·repo·manual event·main ref·실제 checkout SHA를 검사한다.
2. `prepare_db.py::verify_binary()`가 `/usr/local/bin/trivy`의 `lstat()` 일반 파일 여부·
   root:root/0755·기존 SHA-256을 대조한다. `verify_runner.py::execute()`의 toolcheck는
   일반 파일·symlink 배제·해시 검사이며 소유권/모드 검사와 구분한다.
   실제 이미지 경로 `scan_images.py::scan_pair()`는 `prepare_db.verify_binary()`를 호출한다.
3. Runner HOME 아래 새 0700 디렉터리의 빈 캐시에
   `ghcr.io/aquasecurity/trivy-db:2`를 `--download-db-only`로 준비한다.
4. 실패/timeout이면 nonzero로 끝낸다. 기존 `$HOME/.cache/trivy/db`를 변경하거나
   다운로드 실패 후 이전 캐시로 우회하지 않는다.
5. `trivy.db`와 metadata.json의 일반 파일/비어 있지 않은 DB, schema 2,
   timezone 있는 시각과 순서, `검사 시각 < NextUpdate`를 확인하고 해시를 기록한다.
   중복 JSON 키·누락·미지원 스키마·미래 시각·갱신 시각 경과는 실패한다.
6. 해시 계산 뒤 시각과 바이너리를 다시 검사한다. 통과하면 `DB_PREFLIGHT=PASS`지만
   `DELIVERY_GATE=NOT_VERIFIED`를 유지한다.

NextUpdate는 갱신 필요 시점을 판단하기 위한 값이다. 공급자의 보안 만료 보증이나
실제 새 CVE가 없다는 보증으로 해석하지 않는다. 이 단계는 DB 다운로드와 메타데이터·해시
준비까지 확인한다. DB 엔진이 모든 파일을 읽을 수 있는지와 실제 이미지 취약점 판정은
후속 스캔이 성공해야 확인된다. non-empty 파일이나 다운로드 rc=0만으로 Scan PASS를 만들지 않는다.

## 실행과 증적

DB preflight의 최초 실행은 위 Run에서 완료됐다. 이번 문서 정정 때문에 같은 검사를 다시 실행하지 않는다.
향후 별도 DB 준비 점검이 필요할 때는 승인된 main에서 **P1 Trivy DB preflight → Run workflow → main**을
실행한다. 실제 앱 Workflow는 같은 Run의 DB 선행 검사를 자체 호출하므로 과거 PASS를 재사용하지 않는다.
PR 브랜치의 코드를 내부 Runner에서 자동 실행하지 않는다.
이 Workflow는 이미지 Build/Scan·Harbor 인증/Push·Kubernetes Deploy를 수행하지 않는다.
새 권한이나 네트워크 우회를 추가하지 않으며 기존 승인 경로에서 GHCR 공급 실패 시 실패한다.

Summary와 로그의 EVIDENCE_DIR에는 result.json, SHA256SUMS, cache/db/trivy.db,
cache/db/metadata.json이 남는다. 실패하면 result.json과 부분 다운로드가 남을 수 있다.
성공 manifest는 결과와 실제 DB/metadata 3개 파일을 포함한다. 실패 manifest는 result.json만 포함하며,
부분 DB를 승인된 캐시로 표시하지 않는다. 공유 HOME 증적은 기존 인계·확인 후 정리 절차를 따른다.

Trivy 원시 stdout/stderr는 기록하지 않는다. 프록시 자격증명이 오류문에 포함될 수 있기 때문이다.
오류는 단계·정해진 코드·다운로드 종료코드로 기록한다. 추가 원인 확인이 필요하면 별도 진단한다.
자식 환경에는 시스템 PATH·HOME·locale과 기존 프록시 경로만 전달하고 Actions 토큰이나
임의 TRIVY 설정은 전달하지 않는다. 원시 환경변수와 프록시 URL은 증적에 쓰지 않는다.
소스에는 Binary/DB 파일·개인키·Token을 넣지 않으며 GitHub Artifact 업로드도 추가하지 않는다.

## 실제 이미지 파이프라인 통합 시 지켜야 할 조건

- 같은 Run에서 새 DB 준비 결과가 성공해야 이미지 스캔을 실행한다. 오래된 다른 Run의
  PASS 또는 임의 result.json만으로 승인하지 않는다. 실제 사용 직전 metadata/hash를 확인한다.
- 해당 Run의 준비한 캐시를 명시해 스캔하고 결과 JSON·종료코드·대상 이미지 일치를 판정한다.
  바이너리와 DB 확인 실패, 취약점 실패, 검사 오류는 모두 Push/Deploy를 차단한다.
- 샘플 toolcheck의 예상 rc=10/1 성공은 음성시험 성공이며 앱 이미지의 배포 허용값이 아니다.
- Image ID·OCI archive 해시를 Harbor manifest Digest라고 주장하지 않는다. 실제 Scan/Harbor/
  Manifest/Pod의 동일 대상 연결과 기존 승인 Digest 복구는 별도 통합·검증 대상이다.
- 이후 캐시 재사용 최적화가 필요하면 최신성·해시·동시 실행 통제를 리뷰한다. 캐시 유예 기간,
  바이너리 cron/timer/Ansible 자동 교체, OS 갱신 서비스 중지는 이 변경에 없다.

## 검증 이력과 잔여

기존 테스트는 메타데이터 시간/스키마, 다운로드 실패·timeout, 기존 캐시 거부, 심볼릭 링크,
바이너리 불일치 시 통신 차단과 토큰/원문 미출력을 검사한다. Mock DB는 실제 DB가 아니다.
기존 source-validation의 `tools/trivy/test_*.py` 탐색에 포함된다.
DB preflight는 9/16 Run 범위에서 PASS다. 앱 Scan의 최신 수신 상태는 별도
[후속 기록](runtime-block-followup-20260923.md)을 따른다. 이번 정정으로 두 이미지 Scan,
Push/Deploy, Runtime AC 또는 Delivery Gate를 완료 처리하지 않는다.
외부 자동화 존재 여부는 UNKNOWN을 유지한다. 592개 로컬 파일의 참조 0건을 전체 환경의
자동 갱신 부재로 확대하지 않는다.

PR #9 최초 리뷰에 따라 소수점 초 1~9자리·시간대 변환, 실제 수신 metadata의 9자리 시각,
NextUpdate 경계, 지원하지 않는 Python의 조기 실패 및 버전 증적을 시험한다.
Python datetime은 마이크로초 정밀도이며, 입력 원문은 metadata/result에 보존한다.
NextUpdate 비교에서는 절삭으로 최대 1마이크로초 미만 일찍 실패할 수 있으나 유예하지 않는다.
정확한 나노초 순서 검증을 수행한다고 주장하지 않는다. 위 항목은 PR #9 당시 보완 이력이며,
리뷰·병합·DB Runtime 상태는 문서 상단의 확정 기록을 따른다.

## 2026-09-24 외부 자동화 후속 조사 범위

- CI/CD 코드 대조: `4258d0879b186296fc4a4f5a7f06a4818d11bef0`의 Workflow 5개와
  도구 코드 내 Trivy 직접 호출·DB 준비·검사 경로를 확인했다. 이후
  [PR #35](https://github.com/321Team/infra-cicd/pull/35)의 문서 1개 정정만 추가됐고 실행 코드는 같다.
- Ansible 저장소 대조: `321Team/infra-ansible@fa12a35578ba33e6b7fb4e0e037c0a11631f41d1`의
  `roles/playbooks/scripts/inventory/library/module_utils/callback_plugins/tools`에서
  validation 하위·test 접두 파일을 제외한 98개 파일(관련 설명 12개 포함)을 현재 Git blob과
  대조한 뒤 `trivy|aquasecurity`를 대소문자 구분 없이 검색했다. 직접 참조는 0건이다.
- 위 결과는 지정 Source의 정적 검색이다. ansible01의 실제 checkout·미추적 파일, 다른 저장소,
  개인/중앙 cron·timer, 동적 URL/간접 호출 및 다른 계정 권한 범위는 확인하지 않았다.
  기존 9/16 Runner 수집을 다시 실행한 결과도 아니다.
- 남은 사실 확인은 실제 서버의 예약 작업→호출 파일→바이너리 교체 대상 연결이다.
  추가 자료는 호스트·계정·KST 시각, 조사한 경로/작업명, 파일 해시, Trivy 관련 줄번호,
  제외/읽기 실패 범위를 기록한다. 명령행·환경변수·토큰·개인키 원문은 제출하지 않는다.
  확인되지 않은 경로나 읽기 실패는 “없음”으로 처리하지 않는다.
- `AUTO_UPDATE_ABSENCE=NOT_PROVEN` 및 이슈 OPEN을 유지한다. 이 비차단 후속을 앱 Scan이나
  PR 병합의 신규 선행조건으로 추가하지 않는다. 이번 작업은 서버 설정 변경·바이너리 교체·
  DB 재다운로드·서비스 중지를 수행하지 않았다.
- 로컬 검증: 위 CI/CD Source의 기존 Trivy 모의시험 48개 PASS, skip 0.
  이는 실제 서버의 현재 바이너리/소유권이나 외부 자동화 부재 검증이 아니다.

기능 근거: [Trivy DB 관리](https://trivy.dev/docs/latest/configuration/db/).
Python 근거: [file_digest (3.11 추가)](https://docs.python.org/3.11/library/hashlib.html#hashlib.file_digest),
[datetime.fromisoformat](https://docs.python.org/3.11/library/datetime.html#datetime.datetime.fromisoformat).
