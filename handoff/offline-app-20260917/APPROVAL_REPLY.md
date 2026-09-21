# 앱 담당 최종 인계 회신 양식 — 아직 승인된 기록이 아님

아래 양식을 [CICD Issue11](https://github.com/321Team/infra-cicd/issues/11)에 실제 앱 담당자가 검토 후 회신한다. 빈칸과 선택지는 미확정이며 자동 승인으로 사용하지 않는다. 이 파일 작성·패치 적용·Commit 생성만으로 CICD 실행 승인을 부여하지 않는다.

## 1. Source와 적용 파일

- 저장소: `supreme33-kr/fds-msa`
- 최종 **40자리 Commit SHA**: `<실제 반영 후 SHA>`
- 앱 PR/Commit URL: `<실제 링크>`
- 이번 SHA의 내부 CICD Test→Build→Scan 실행 판단: `<승인 / 보류>`
- 판단자 및 일시: `<본인·시각>`
- 반영 확인: Dockerfile2개, requirements-runtime.lock2개, requirements-test.lock2개
- 기존 requirements4개 유지 여부: `<유지 / 변경 사항과 새 공급 검토 필요>`

후보 적용 patch는 `offline-app-254b822.patch`이며 기준 Source는 `254b82231f8af79a06419b581174647bbccbe2bc`다. 이미 승인했던 `01758583...` 또는 패치 이전 SHA를 새 최종 승인 SHA로 기재하지 않는다.

## 2. Unit/Contract 범위

| 서비스·단계 | 검토 후보 | 최종 회신 |
|---|---|---|
| fds-engine Unit | `.venv/bin/python -m pytest -q tests/test_rules.py` | `<동의 / 수정 명령·사유>` |
| fds-engine Contract | `.venv/bin/python -m pytest -q tests/test_health.py tests/test_metrics.py` | `<동의 / 수정 명령·사유>` |
| transaction-api Contract | `.venv/bin/python -m pytest -q tests/test_health.py tests/test_metrics.py` | `<동의 / 수정 명령·사유>` |
| transaction-api Unit | 아래 둘 중 실제 Source에 맞는 하나 | `<Unit 명령 / 명시적 NOT_APPLICABLE>` |

cwd는 각 서비스 디렉터리, 후보 제한시간은 각300초다. 정상은 pytest exit0이며 nonzero/timeout은 다음 단계 중단이다. 운영 DB를 사용하지 않는 현재 health/metrics 응답 계약이며 DB 계약 전체 검증으로 확대하지 않는다. 전체 파이프라인 Unit과 두 Contract는 유지한다.

API Unit을 해당 없음으로 판단한 경우 아래 값을 **실제 사유로** 작성한다. 필요한 시험을 없애기 위한 형식적 면제가 아니다. Unit 명령이 있으면 cwd·argv 배열·제한시간을 대신 제공한다.

```json
{
  "status": "NOT_APPLICABLE",
  "reason": "<이 최종 SHA에서 API Unit을 별도 실행하지 않는 구체적 사유>",
  "approved_by": "<실제 판단자>",
  "approval_reference": "<판단을 남긴 실제 GitHub 댓글 URL>"
}
```

## 3. 실행과 검토 구분

- 이번 회신은 앱 Source·시험 범위 인계다. CICD PR19의 최신 전체 변경에 대한 독립 리뷰를 대신하지 않는다.
- 앱 담당 환경의 pytest 결과가 있으면 실행한 Source/명령/원본 증적을 따로 연결한다. 과거 edge01 결과를 새 Source나 내부 Runner 결과로 승계하지 않는다.
- CICD 담당은 위 값을 config에 반영·검사하고, 해당 변경까지 포함한 최신 독립 리뷰·main 병합 후 중앙 수동 Workflow를 실행한다.
