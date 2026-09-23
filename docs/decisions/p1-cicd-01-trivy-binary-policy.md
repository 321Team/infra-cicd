# P1-CICD-01 Trivy 바이너리 관리 정책 결정 기록

기록일: 2026-09-22. 상태: **ADOPTED_BY_LEAD**.

[이슈 #8](https://github.com/321Team/infra-cicd/issues/8)에서 제안된 "권장안 — 미채택·미적용" 표를 Lead 이권욱이 정식 채택한 기록이다. 이 채택은 앞으로의 운영 정책 결정이며, cicd-runner01에 외부 자동화(중앙 관리 서버·개인 cron/timer)가 실제로 존재하는지에 대한 사실 확인이 아니다. 그 질문은 이슈 #8에 별도로 열려 있다.

## 근거

| 근거 | 확인 내용 |
|---|---|
| [이슈 #8 — 2026-09-16 권장안](https://github.com/321Team/infra-cicd/issues/8) | 바이너리 고정·독립 리뷰, DB 선행 검사, HIGH/CRITICAL 차단, 자동 교체 미포함의 6개 권장 항목 |
| `tools/trivy/verify_runner.py` | `BINARY_SHA256` 상수 고정. `execute()`가 `sha256(TRIVY)`를 고정값과 대조하며, 이 모듈에는 `verify_binary()`나 소유권/모드 강제 검사가 없음 |
| `tools/trivy/prepare_db.py` | `verify_binary()`가 일반 파일·root:root·0755·SHA-256을 검사. `prepare()`는 DB 준비 전후 이를 호출하고 Run 전용 DB 다운로드·해시·시각 및 `validate_metadata()`의 `NextUpdate` 검사를 수행 |
| `tools/trivy/scan_images.py` | `prepare_db.verify_binary`를 가져와 각 이미지 Scan 전후 호출. `scan_pair()`의 `require(scanned['scan'] == 'PASS', 'HIGH_CRITICAL_FINDINGS')`로 첫 BLOCK에서 중단 |

## 채택 정책

| 항목 | 정책 | 구현 근거 |
|---|---|---|
| 바이너리 관리 | 검토한 버전·SHA-256 고정. latest 자동 수신·root 자기 갱신 금지. 교체는 이권욱 기록·적용, 이하영 독립 리뷰 | 고정값은 `verify_runner.py`에 있으며 일반 파일·소유권·모드·해시 강제 검사는 `prepare_db.verify_binary()`에 있음. DB 준비와 이미지 Scan 경로가 이를 호출 |
| 변경 시 검증 | 새 버전 영향·출처·체크섬·옵션 호환성을 PR에 기록, 독립 리뷰 후 교체, 변경 후 실제 탐지·오류 차단 재검증 | 저장소 기본 운영 원칙(README "코드/실행 설정 포함 PR은 독립 리뷰 후 Lead가 병합")에 이미 포함, 별도 신설 불필요 |
| 취약점 DB 선행 검사 | 앱 이미지 Scan 전마다 공식 DB 공급원(`ghcr.io/aquasecurity/trivy-db:2`) 갱신 필요 여부 확인, 메타데이터·해시·시각 기록, Run 전용 사본으로 검사 | `prepare_db.py` `prepare()` |
| DB 유효성 상세 | `NextUpdate` 기준 갱신 필요 판정. 갱신 필요 시 실패 처리. 임의 캐시 유예 없음 | `prepare_db.py` `validate_metadata()` — `now < next_update` 미충족 시 `DB_REFRESH_REQUIRED` |
| 실제 배포 차단 | 미승인 예외 포함 HIGH/CRITICAL, 검사 실패, DB 갱신 실패 시 Push/Deploy 실행 금지. 이전 캐시로 우회한 PASS 생성 금지 | `scan_images.py` `scan_pair()` |
| 자동화 운영 범위 | P1에서는 검토된 수동 바이너리 교체 + Workflow의 DB 선행 검사만 채택. 신규 바이너리 자동 교체 cron/timer/Ansible 작업은 이 채택에 포함하지 않음 | 대조한 `verify_runner.py`, `prepare_db.py`, `scan_images.py`에 바이너리 교체 기능 없음. 외부 예약 작업·중앙 관리 서버의 실제 상태는 별도 미확인 |

## 이 결정이 답하지 않는 것

- cicd-runner01에 이미 외부 자동화(중앙 관리 서버, 개인 cron/systemd timer)가 존재하는지 여부 — [이슈 #8](https://github.com/321Team/infra-cicd/issues/8)의 운영자 확인 질문 1·2로 별도 확인한다. 이 채택은 "새로 그런 자동화를 두지 않는다"는 정책이지, "현재 존재하지 않는다"는 사실 확인이 아니다.
- Runtime AC·Delivery Gate 판정 — 이 채택으로 상향하지 않는다.
- GitHub 요금제(Free/Team/Enterprise)는 이 정책의 채택 여부나 cron/systemd/Ansible 자동화의 기술적 가능 여부와 무관하다. Free 플랜이 실제로 제한하는 것은 Private 저장소의 브랜치 보호 규칙 강제이며(`README.md` 기존 기록), 호스트 레벨 자동화나 Self-hosted Runner 동작을 막지 않는다.

이후 바이너리 버전 교체나 정책 변경이 필요하면 이슈·PR에 구체적인 변경을 남기고 이 문서와 `config/p1-cicd-01.json`을 함께 갱신한다.

## 2026-09-24 코드 인용 정정

대조 Source: [infra-cicd `4258d0879b186296fc4a4f5a7f06a4818d11bef0`](https://github.com/321Team/infra-cicd/commit/4258d0879b186296fc4a4f5a7f06a4818d11bef0).

- 최초 문서가 `verify_binary()`를 `verify_runner.py` 소속으로 인용한 부분은 잘못됐다. 실제 정의는 [prepare_db.py](https://github.com/321Team/infra-cicd/blob/4258d0879b186296fc4a4f5a7f06a4818d11bef0/tools/trivy/prepare_db.py#L39-L44)이며, 일반 파일·UID/GID 0·0755·고정 SHA-256을 검사한다.
- [verify_runner.py의 execute()](https://github.com/321Team/infra-cicd/blob/4258d0879b186296fc4a4f5a7f06a4818d11bef0/tools/trivy/verify_runner.py#L80-L84)는 해시 검사 경로다. 이 모듈만 보고 전체 저장소에 소유권/모드 검사가 없다고 판단할 수 없다.
- [scan_images.py의 scan_pair()](https://github.com/321Team/infra-cicd/blob/4258d0879b186296fc4a4f5a7f06a4818d11bef0/tools/trivy/scan_images.py)는 DB 준비 이후 각 이미지 검사 전후 `prepare_db.verify_binary()`를 호출한다. 정책상 Push/Deploy 차단 요구와 현재 Scan의 BLOCK 반환·Push/Deploy 미실행은 구분한다.
- 따라서 [09-22 리뷰](https://github.com/321Team/infra-cicd/issues/8#issuecomment-5777359763)의 **함수 소속 오류 지적은 반영**하지만, “저장소 전체에 소유권/모드 검증 로직도 없다”는 설명은 위 Source와 일치하지 않는다. 이번 정정은 기존 통제를 정확하게 설명하며 보안 통제 코드를 새로 추가하거나 제거하지 않는다.
- [PR28](https://github.com/321Team/infra-cicd/pull/28)은 이미 병합됐고 정책 채택도 완료됐다. 외부 자동교체 여부는 여전히 `AUTO_UPDATE_ABSENCE=NOT_PROVEN`이다. 정책 채택 응답을 과거 운영 현황 질문의 답으로 재해석하지 않는다.

검증은 위 세 모듈의 정의·호출 경로와 문서 정합성에 대한 STATIC 대조다. 서버 실행·현재 바이너리 속성 재확인·실제 변조/오류 차단 시험을 이번에 수행하지 않았으며, Runtime AC1/10·앱 BLOCK·Delivery NOT_VERIFIED·이슈 OPEN을 유지한다.
