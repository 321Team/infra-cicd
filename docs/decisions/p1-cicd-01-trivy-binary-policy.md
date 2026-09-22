# P1-CICD-01 Trivy 바이너리 관리 정책 결정 기록

기록일: 2026-09-22. 상태: **ADOPTED_BY_LEAD**.

[이슈 #8](https://github.com/321Team/infra-cicd/issues/8)에서 제안된 "권장안 — 미채택·미적용" 표를 Lead 이권욱이 정식 채택한 기록이다. 이 채택은 앞으로의 운영 정책 결정이며, cicd-runner01에 외부 자동화(중앙 관리 서버·개인 cron/timer)가 실제로 존재하는지에 대한 사실 확인이 아니다. 그 질문은 이슈 #8에 별도로 열려 있다.

## 근거

| 근거 | 확인 내용 |
|---|---|
| [이슈 #8 — 2026-09-16 권장안](https://github.com/321Team/infra-cicd/issues/8) | 바이너리 고정·독립 리뷰, DB 선행 검사, HIGH/CRITICAL 차단, 자동 교체 미포함의 6개 권장 항목 |
| `tools/trivy/verify_runner.py` | `BINARY_SHA256` 상수 고정, `verify_binary()`의 소유권/모드/해시 검증 |
| `tools/trivy/prepare_db.py` | `prepare()`의 매 Run 전용 DB 다운로드·해시·시각 기록, `validate_metadata()`의 `NextUpdate` 강제 |
| `tools/trivy/scan_images.py` | `scan_pair()`의 `require(scanned['scan'] == 'PASS', 'HIGH_CRITICAL_FINDINGS')` |

## 채택 정책

| 항목 | 정책 | 구현 근거 |
|---|---|---|
| 바이너리 관리 | 검토한 버전·SHA-256 고정. latest 자동 수신·root 자기 갱신 금지. 교체는 이권욱 기록·적용, 이하영 독립 리뷰 | `verify_runner.py`의 `BINARY_SHA256`; 바이너리를 다운로드·교체하는 코드/Workflow가 저장소에 없음(직접 확인) |
| 변경 시 검증 | 새 버전 영향·출처·체크섬·옵션 호환성을 PR에 기록, 독립 리뷰 후 교체, 변경 후 실제 탐지·오류 차단 재검증 | 저장소 기본 운영 원칙(README "코드/실행 설정 포함 PR은 독립 리뷰 후 Lead가 병합")에 이미 포함, 별도 신설 불필요 |
| 취약점 DB 선행 검사 | 앱 이미지 Scan 전마다 공식 DB 공급원(`ghcr.io/aquasecurity/trivy-db:2`) 갱신 필요 여부 확인, 메타데이터·해시·시각 기록, Run 전용 사본으로 검사 | `prepare_db.py` `prepare()` |
| DB 유효성 상세 | `NextUpdate` 기준 갱신 필요 판정. 갱신 필요 시 실패 처리. 임의 캐시 유예 없음 | `prepare_db.py` `validate_metadata()` — `now < next_update` 미충족 시 `DB_REFRESH_REQUIRED` |
| 실제 배포 차단 | 미승인 예외 포함 HIGH/CRITICAL, 검사 실패, DB 갱신 실패 시 Push/Deploy 실행 금지. 이전 캐시로 우회한 PASS 생성 금지 | `scan_images.py` `scan_pair()` |
| 자동화 운영 범위 | P1에서는 검토된 수동 바이너리 교체 + Workflow의 DB 선행 검사만 채택. 신규 바이너리 자동 교체 cron/timer/Ansible 작업은 이 채택에 포함하지 않음 | 위 코드 전체에 바이너리 자기 교체·외부 자동 설치 로직 없음(정책적 배제, 코드 변경 불필요) |

## 이 결정이 답하지 않는 것

- cicd-runner01에 이미 외부 자동화(중앙 관리 서버, 개인 cron/systemd timer)가 존재하는지 여부 — [이슈 #8](https://github.com/321Team/infra-cicd/issues/8)의 운영자 확인 질문 1·2로 별도 확인한다. 이 채택은 "새로 그런 자동화를 두지 않는다"는 정책이지, "현재 존재하지 않는다"는 사실 확인이 아니다.
- Runtime AC·Delivery Gate 판정 — 이 채택으로 상향하지 않는다.
- GitHub 요금제(Free/Team/Enterprise)는 이 정책의 채택 여부나 cron/systemd/Ansible 자동화의 기술적 가능 여부와 무관하다. Free 플랜이 실제로 제한하는 것은 Private 저장소의 브랜치 보호 규칙 강제이며(`README.md` 기존 기록), 호스트 레벨 자동화나 Self-hosted Runner 동작을 막지 않는다.

이후 바이너리 버전 교체나 정책 변경이 필요하면 이슈·PR에 구체적인 변경을 남기고 이 문서와 `config/p1-cicd-01.json`을 함께 갱신한다.
