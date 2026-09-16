# Metrics 시험 추가에 따른 CICD 매핑 후보 — 2026-09-16

상태: **PREPARATION / PENDING_APP_HANDOFF**.
이 문서는 기존 [앱 시험 매핑](app-handoff-mapping.md)의 26개 시험 관측 이후 변경을 기록한다.
앱 실행 승인이나 내부 Runner Runtime 결과를 생성하지 않는다.

## 작성 전 대조한 변경

- 관측 앱 main: `supreme33-kr/fds-msa@254b82231f8af79a06419b581174647bbccbe2bc`.
  이전 관측 `5b0049c2441b98196db6cd8cd4be9a85607568ea` 이후
  [앱 PR #5](https://github.com/supreme33-kr/fds-msa/pull/5)가 병합됐다.
  관측 main은 CICD 실행을 승인한 최종 Source SHA가 아니다.
- 두 서비스에 `app/metrics.py`, `run.py`, `tests/test_metrics.py`가 추가됐다.
  Dockerfile은 `run.py`를 복사하고 `CMD ["python", "run.py"]`로 변경됐다.
  main/metrics 포트는 transaction-api 8000/9091, fds-engine 8001/9090이다.
- 두 Dockerfile의 FROM은 여전히 `python:3.12-slim`이다. 공급 경로·digest 고정은 미완료다.
- 위 고정 Commit의 시험 파일 5개 원격 Git blob과 로컬 바이트를 대조하고 AST를 확인했다.
  CICD main `90f2f37d9e1ab77252b50e80bca339cfef48bd51`의 입력·검증 순서를 읽고,
  기존 Contract argv가 `test_health.py`만 지정해 신규 시험 6개를 제외하는 것을 확인했다.

## 중복 없는 매핑 후보

| 서비스 | 단계 | 파일 | 정적 시험 함수 수 |
|---|---|---|---:|
| fds-engine | Unit | `tests/test_rules.py` | 16 |
| fds-engine | Contract | `tests/test_health.py`, `tests/test_metrics.py` | 5 + 3 |
| transaction-api | Unit | 인계 없음, 기존 `null` 유지 | 0 |
| transaction-api | Contract | `tests/test_health.py`, `tests/test_metrics.py` | 5 + 3 |

총 32개는 AST 함수 정의 수다. 이번 작업에서 앱 pytest를 실행한 수치가 아니다.
각 시험 파일은 한 단계에만 배정하며 같은 전체 suite를 Unit/Contract에서 중복 실행하지 않는다.
setup의 서비스별 venv·requirements-test.txt 설치, timeout은 기존 후보를 유지한다.

두 Contract 명령은 각 서비스 디렉터리에서 다음 argv에 대응한다.

```text
.venv/bin/python -m pytest -q tests/test_health.py tests/test_metrics.py
```

API Unit 공백은 기존 입력 모델과 인계 시험 범위의 매핑 확인사항이다.
신규 필수 suite/AC를 추가하거나 성공만 반환하는 명령·중복 시험으로 채우지 않는다.

## 앱 담당 보고와 검증 한계

[이재환의 2026-09-16 21:41:24 KST 회신](https://github.com/supreme33-kr/fds-msa/pull/5#issuecomment-5697587143)은
edge01에서 API 8/8, engine 24/24 PASS와 별도 listener의 응답을 보고했다.
API는 8000/livez·9091/metrics, engine은 기존 cockpit-tls의 9090 포트 충돌로
`METRICS_PORT=19090`을 사용한 8001/livez·metrics 확인이다.
원래 engine metrics 포트 9090의 성공으로 옮겨 적지 않는다.

이 회신은 이전 26 PASS 이후의 앱 담당 보고이며 원본 로그를 이번에 재검증한 결과나
내부 CICD Runner의 Test/Build/Scan Runtime PASS가 아니다. 동일 보고를 다시 요구하지 않는다.
PR 본문의 이전 NOT RUN과 후속 실행 회신은 시점을 구분한다.

새 metrics 시험은 TestClient를 통한 ASGI 응답·경로 제한과 Counter 관련 검사를 포함한다.
`run.py`를 실행해 실제 두 TCP listener를 검증하는 시험은 아니다.
API 시험의 DB ping mock 및 Counter 직접 증가는 실제 DB 저장·조회 계약 검증으로 확대하지 않는다.

## 실행 차단과 후속

`config/app-build-scan.json`의 변경은 두 Contract argv에 파일 하나씩 추가하는 것뿐이다.
`PENDING_APP_HANDOFF`, Source Commit·승인자·근거 `null`, 두 base_image `null`, API unit `null`을 유지한다.
실행기는 status를 먼저 검사하므로 이 설정은 `APP_HANDOFF_PENDING`/rc1로 종료한다.
새 앱 main을 자동 승인값으로 채택하지 않는다.

1. OID-03에 맞는 실제 공급 경로·대상·Owner·Expiry와 base manifest digest를 확정한다.
2. 앱 담당이 두 Dockerfile을 동일 검증 base reference로 pin하고, metrics 변경·시험을 포함한
   최종 40자리 Source SHA·승인자·근거를 인계한다.
3. CICD 단계 매핑·실제 job/Build 공급 환경·최종 입력과 Workflow를 검토한 뒤 내부 Runner에서 실행한다.

PR #13의 수집기 재검토는 별도 추적한다. 이 변경은 그 PR의 소스나 리뷰 대상을 바꾸지 않는다.
관리 50%(5/10)·Runtime AC 1/10·DELIVERY_GATE=NOT_VERIFIED 유지.
