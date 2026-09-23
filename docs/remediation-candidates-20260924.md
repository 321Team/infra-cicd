# P1-CICD-01 — 증적 인수 delta와 앱·공급 개선 후보

검토일: 2026-09-24 KST / P1-CICD-01 Lead 이권욱 작업의 Codex 보조 검토.
분류: 일반 저장소 문서 전용. 후보 선정·정적 대조이며 운영 입력 승인이나 취약점 예외가 아니다.
책임: [CICD #11](https://github.com/321Team/infra-cicd/issues/11), [baseline #16](https://github.com/321Team/fds-project-baseline/issues/16).

## 1. 현재 판정과 이미 완료한 작업

**원 앱 BLOCK / fds-engine NOT_SCANNED / Runtime AC1/10 / Delivery NOT_VERIFIED 유지.**
기존 Run35683010080/Attempt1의 취약점 요약과 app-evidence 수신 검증은 완료됐다.
[00:24 KST 수신 기록](https://github.com/321Team/infra-cicd/issues/11#issuecomment-5797608952)을 재사용하며 이번에 과거 검증기·수집·앱 실행을 반복하지 않았다.

| 대상 | 현재 원본·판정 |
|---|---|
| CICD 기준 main | `d513defd761954d89d0b29ef0a015299bca27964`; [PR32](https://github.com/321Team/infra-cicd/pull/32) 병합, 기존 main Source validation Run35863662730 success |
| 앱 현재 main | `5b35fdbd8293e258daaad56d3e535721ca3c90ff` |
| 실제 승인·실행 Source | `ec69b67067decf35abbfed0a6a99f3073132787b`; 현재 CICD config도 이 SHA 유지 |
| 입력 변화 점검 | 두 서비스의 Python·Dockerfile·requirements·lock·.trivyignore 총31파일은 승인 Source와 현재 main의 Git blob/mode 동일. 별도 도구/문서 PR을 취약점 수정으로 보지 않음 |
| 수신 결과 | EVIDENCE_CHECK=PASS, BINDING=COMPLETE, SOURCE_BINDING=MATCH. archive SHA256 `11578ae33211f569df91830a2b26314c01ffcaea70a67dc7165f9e3dad4b1c79` |
| 증적 편입 | [Draft PR22](https://github.com/321Team/fds-project-evidence/pull/22), HEAD `a6aa1d86723051fcb30196d7f26223b8d1cf7683`; 조회 시 독립 리뷰0건, 미병합 |

수신 PASS는 원 서버의 raw 로그·OCI·Source archive·Trivy report·DB 바이트를 수신 측에서 다시 검사한 결과가 아니다.
`RAW_PAYLOAD_CHECK=NOT_INCLUDED_SERVER_RECORD_ONLY`와 기존 AC03/05/06 부분 증거 범위를 유지한다.

## 2. 기존 증적에서 보완할 최소 항목

새 collect나 앱 재실행 대신 원 수집 작업 폴더 `CICD_Evidence35683010080_1.*`의 아래 기존 자료만 확인한다.
여러 폴더가 있다면 export archive hash가 위 수신 hash와 일치하는 회차를 식별한다.
반환을 쉽게 하기 위한 [기존 collect 기록 보완 수집 도구·실행 안내](collect-record-followup-20260924.md)를 준비했다. 원 앱/collect 재실행 없이 기존3파일만 묶으며 실제 Runner 실행은 아직 NOT RUN이다.

| 항목 | 확인 목적·정상 기준 |
|---|---|
| `collect.log` | 서버 측 EVIDENCE_CHECK=PASS, RAW_PAYLOAD_CHECK=LOCAL_BYTES, BINDING=COMPLETE, SOURCE_BINDING=MATCH와 원 BLOCK 확인 |
| `started-at.txt`, `finished-at.txt` | 정확한 collect 시각. 원 앱 Run 시각 또는 수신 verify 시각으로 대체하지 않음 |
| 보존된 종료 화면 | 당시 명령 성공/실패·archive 출력 hash 확인. 새 명령 뒤의 `$?`로 과거 종료코드를 만들지 않음 |
| 원 archive의 검토자 접근 | 기존 승인 보관 위치의 실제 접근 확인. 개인 파일 ID만으로 팀 접근 완료를 주장하지 않음 |

파일·화면이 없으면 미수신으로 남기고 원본을 사후 작성하지 않는다. 이 누락은 과거 수집 provenance와 증적 편입의 제한이며, 앱·base 후보 분석까지 중단시킬 이유는 아니다.
PR22의 독립 검토·편입 판단은 [중앙 정책 §3.1](https://github.com/321Team/fds-project-baseline/blob/49278c189dacb620e81d282c6401faa2a9c677a6/governance/REVIEW_AUTHORITY_POLICY.md)과 증적 저장소 정책을 적용한다.

## 3. 앱 의존성: 격리 시험 우선 후보

**권장 후보: FastAPI 0.141.1 + Starlette 1.7.0.** 아직 앱 Lead의 승인 버전·운영 Source가 아니다.
기존 FastAPI0.115.5에 Starlette만 강제 상향하지 않는다.

| 공식 선언·현재 입력 | 대조 결과 |
|---|---|
| FastAPI0.141.1 | Python>=3.10, Starlette>=0.46.0, Pydantic>=2.9.0. Starlette1.7.0·현재 Pydantic2.9.2가 선언 범위 안에 있음 |
| Starlette1.7.0 | Python>=3.10, AnyIO>=4,<5, Python<3.13에서 typing-extensions>=4.10.0 |
| 현재 runtime lock | AnyIO4.15.1, typing-extensions4.16.0. 위 직접 제약과 일치 |
| 새 의존성 | FastAPI의 typing-inspection>=0.4.2, annotated-doc>=0.0.2를 새 공급에 포함해야 함. 현재 lock에 없는 의존성을 기존 wheel만으로 충족했다고 보지 않음 |
| 버전 선택 이유 | 1.7.0은 기존 보고의 수정 경계0.49.1/1.1.0/1.3.1 이후이며, 공식 릴리스에 AnyIO4.15 호환 수정이 명시됨. 현재 AnyIO4.15.1 입력을 고려한 격리 시험 후보 |

공식 근거: [FastAPI pyproject](https://github.com/fastapi/fastapi/blob/0.141.1/pyproject.toml), [Starlette pyproject](https://github.com/Kludex/starlette/blob/1.7.0/pyproject.toml), [Starlette1.7.0 릴리스](https://github.com/Kludex/starlette/releases/tag/1.7.0).
1.7.0은 2026-09-23에 발표됐다. 선언상 호환을 실제 앱 회귀 PASS로 확대하지 않는다. 1.6.0 등 대안을 선택한다면 AnyIO4.15 및 TestClient 영향부터 비교한다.
현재 조합이 세 기존 탐지의 수정 버전 범위를 만족한다는 판단과 새 이미지의 전체 취약점 PASS는 서로 다르다.

앱 Lead 인계 순서:

1. 두 서비스 requirements에 최종 선택한 FastAPI/Starlette를 명시하고 나머지 직접 의존성의 변경 필요 여부를 판단한다.
2. 해당 플랫폼에서 runtime/test 4개 lock과 wheel inventory를 새로 생성한다. runtime은 base 이미지용, test는 Runner host용이다. 모든 전이 의존성과 wheel hash를 포함하고 `pip check`로 설치 정합성을 확인한다.
3. 기존 엔진 Rule Unit, 양쪽 health/metrics Contract를 실행한다. 추가로 라우트 import/기동, OpenAPI 생성, 정상·잘못된 JSON의 상태코드와 응답 스키마, 실제 DB 저장·엔진 timeout/fail-open 경로의 영향과 검증 범위를 기록한다. 현재 Contract 성공만으로 DB/E2E 통과를 주장하지 않는다.
4. API Unit N/A 판단을 유지한다면 변경 후 근거를 재확인한다. 새 앱/lock/Dockerfile PR은 독립 코드 리뷰 후 최종40자리 SHA·시험 범위·승인 근거를 CICD에 인계한다.

이번 환경에는 새 wheel 묶음과 Docker/Podman 실행기가 없다. 직접 제약 비교·소스 정적 점검만 수행했고 resolver·설치·pytest·이미지 Build/Scan은 NOT RUN이다. 새 lock이나 공급물 hash를 추측해 만들지 않았다.

## 4. 취약 기능 사용 경로의 정적 확인

현재 main의 두 서비스 Python19파일을 읽고 AST 파싱 및 문자열 사용 경로를 확인했다. `FileResponse`, `StaticFiles`, `.form(`, `Form`, `UploadFile`, `Mount`를 직접 사용하는 코드는 발견하지 못했다.

| 탐지 | 공식 영향 조건 | 현재 정적 관찰·판정 |
|---|---|---|
| CVE-2025-62727 | FileResponse/StaticFiles의 Range 처리 | 직접 사용 미발견. 동적/의존성 경로와 실행 환경 전체의 비해당 증명은 아님 |
| CVE-2026-48818 | Windows의 StaticFiles UNC 경로 | 제출 이미지는 Debian/Linux이며 직접 StaticFiles 사용 미발견. 개별 비해당 검토 근거 후보 |
| CVE-2026-54283 | urlencoded `request.form()` 처리 | 두 POST 경로는 Pydantic JSON 모델, form 직접 사용 미발견. 자동 면제 또는 이미지 Scan PASS 아님 |

근거: [62727](https://github.com/Kludex/starlette/security/advisories/GHSA-7f5h-v6xp-fcq8), [48818](https://github.com/Kludex/starlette/security/advisories/GHSA-wqp7-x3pw-xc5r), [54283](https://github.com/Kludex/starlette/security/advisories/GHSA-82w8-qh3p-5jfq).
이 관찰로 예외를 적용하지 않았다. 의존성 업데이트와 재스캔을 먼저 진행하고 남은 개별 항목만 해당 Lead가 판단한다.

### 기존 .trivyignore와 중앙 스캐너의 차이

두 앱의 `.trivyignore`에는 perl-base CRITICAL3개의 과거 제외값과 08-27 미패치 설명이 남아 있다. 09-23 수신 보고에는 해당 수정 버전이 기록되어 있어 설명도 현재 근거와 다르다.
현재 CICD `tools/trivy/scan_images.py`는 실행별 빈 ignore 파일을 `--ignorefile`로 명시하고 `--ignore-unfixed=false`, HIGH/CRITICAL 게이트를 유지한다. 이번 실제 BLOCK을 보면 이 앱 파일을 면제 입력으로 사용하지 않았다.
앱 후속 PR에서는 낡은 제외값의 제거 또는 명확한 비운영 참고 보관을 검토한다. 다른 사람이 앱 디렉터리에서 수동 Trivy를 실행할 때 동일 기준이라고 오해할 가능성이 있다. 이번 문서에서 ignore 파일이나 중앙 보안 통제를 수정하지 않았다.

## 5. Base와 공급물: 같은 계열 후보부터 평가

| 우선순위 | 후보·선택 조건 | 남은 확인 |
|---|---|---|
| 1 | 공식 `python:3.12.14-slim-trixie`, linux/amd64. Python3.12·Debian13 계열을 유지하며 패치 반영 차이를 먼저 평가 | registry의 amd64 manifest/config digest, OCI archive/layer hash·패키지 목록·새 Scan |
| 2 | 공식 `python:3.12.14-slim-bookworm`. 1안의 미해결 위험 또는 호환 문제로 대체가 필요할 때 비교 | Debian 변경에 따른 glibc/native wheel·사용 도구·정책 영향·기능/보안 회귀. 더 안전하다고 미리 판정하지 않음 |

공식 이미지 목록의 두 후보는 amd64를 포함하며 Dockerfile 소스는 `688a0b86bb44289df16a363e9f41d90514c1a5f9`의 `3.12/slim-trixie`, `3.12/slim-bookworm`이다.
근거: [공식 목록](https://github.com/docker-library/official-images/blob/master/library/python), [trixie Dockerfile](https://github.com/docker-library/python/blob/688a0b86bb44289df16a363e9f41d90514c1a5f9/3.12/slim-trixie/Dockerfile), [bookworm Dockerfile](https://github.com/docker-library/python/blob/688a0b86bb44289df16a363e9f41d90514c1a5f9/3.12/slim-bookworm/Dockerfile).
태그와 Dockerfile commit은 게시된 OCI digest가 아니다. 두 후보의 새 image digest와 다운로드된 바이트는 이번에 확보하지 못했으므로 운영 config에는 반영하지 않는다.

09-23 원 보고 분류상 수정 버전이 없는 OS 탐지는44건/고유8CVE다. util-linux36, ncurses4, systemd2, acl1, perl1 기록이다. 당시 분류를 현재 공급 후보의 미패치 여부로 재사용하지 않는다.
패치 base를 받아도 전체0건을 보장하지 않으며, 실제 새 패키지 목록·Trivy DB·두 앱 이미지 결과로 다시 판단한다. 패키지DB 삭제·강제 패키지 제거·HIGH 제외·ignore-unfixed 전환은 개선 방식으로 사용하지 않는다.

새 공급물은 운영 CI와 분리된 승인 공급 위치에서 준비한다. [PR18 종결](https://github.com/321Team/infra-cicd/pull/18#issuecomment-5772479406)로 닫힌 Runner 직접 다운로드 경로와 과거60분 허용을 재사용하지 않는다.
새 공급 위치·허용 경로가 미확정이면 담당 Lead가 해당 입력을 먼저 정한다. 준비물은 새 디렉터리에 보관하고 기존 공급 폴더를 덮어쓰지 않는다.

인계 필드: base repository@amd64-manifest-digest, config digest, OCI archive SHA256, 네 프로필 roots/lock/inventory/wheel hash, requirements4개 hash, 플랫폼/Python/ABI, 실행 계정·시각·로그, 최종 앱 승인 SHA.
이 값이 모두 실제 바이트와 일치하면 기존 offline 공급 검사·digest inspect/최소 Build를 새 공급 대상으로 검증하고 config PR을 작성한다. 코드/실행 설정 PR의 독립 리뷰 후 새 Run을 실행한다.

## 6. 진행 순서·중단·복구

1. 기존 collect 로그/시각·검토자 접근 보완과 PR22 독립 증적 검토를 진행한다. 새 앱 준비와 병행 가능하다.
2. 앱 Lead는 위 호환 후보의 격리 시험과 최종 SHA 인계를, CICD/공급 담당은 base·새 wheel/OCI 인수를 진행한다.
3. 승인 입력이 일치하는 config PR을 독립 리뷰한다. 누락·hash/platform 불일치·설치 또는 시험 실패면 중단하고 현재 config를 유지한다.
4. 새 승인 SHA에서 두 이미지 Test→Build→Scan을 실행한다. 첫 이미지가 BLOCK이면 fds-engine은 계속 NOT_SCANNED로 기록하며 실패 차단을 우회하지 않는다.
5. 새 Run/Attempt/Source/config로 expected를 생성하고 증적을 수신한다. Push/Deploy와 Harbor→Manifest→Pod digest·실패 시 이미지 유지·Rollback/Health는 이후 별도 구현·검증 범위다.

이번 문서 변경의 복구는 문서 PR revert다. 운영 source/config·공급물·Runner·게이트는 변경하지 않았으므로 이번 작업에 서버 복구 단계는 없다. 향후 후보 시험은 새 경로와 격리 환경에서 수행하고 실패 결과를 보존한다.

## 7. 이번 검증 범위

- GitHub 최신 상태와 승인 Source를 대조했다. 앱31파일의 현재 원격 Git blob과 로컬 바이트가 일치했고, 승인 Source와 현재 main의 대상31파일도 동일하다.
- Python19파일 AST 파싱·직접 사용 패턴 확인. 전체 실행 도달성/보안 비해당 검증은 아니다.
- 공식 pyproject를 파싱하여 FastAPI/Starlette·현재 Pydantic/AnyIO/typing-extensions 및 Python3.11/3.12의 선택된 직접 제약을 비교했다. 전체 resolver·새 wheel 검증을 대신하지 않는다.
- STATIC 분석 완료. 새 후보 MOCK/UNIT·CI·RUNTIME·독립 REVIEW·MERGE는 미수행. 과거 Source validation success는 새 후보의 시험 결과가 아니다.
