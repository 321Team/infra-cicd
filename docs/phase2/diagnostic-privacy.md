# #46 S1 — 비공개 진단 사본의 값 제거

2026-10-19 KST · 이권욱 / CODEX_ASSISTED. PR91 merge `2610c66f8581656065ccfd4e5524c401a9fd657c` 이후의 별도 코드 변경이다. [하영 S1 리뷰](https://github.com/321Team/infra-cicd/pull/91#pullrequestreview-5566763852)의 두 선택지 중 **② 저장 전 값 제거**를 구현한다. 기존 렌더에 비밀이 없다는 정적 확인만으로 live 응답도 안전하다고 가정하지 않는다. 독립 코드 리뷰·실제 연결/Runtime은 별도다.

## 저장하는 것

`delivery_diagnostics.py`는 같은 root·같은 단계별 파일명과 0600/O_EXCL/no-follow/fsync를 유지한다. 원문은 ObjectReader/Reconciler가 메모리에서 그대로 검증하고, **추가 진단 사본을 만드는 시점에만** 파싱·값 제거한다. API 요청·대조·journal/ACK·baseline·fence의 판단 입력을 바꾸지 않는다.

schema `P2-CICD-private-diagnostic-3` / profile `P2-K8S-DIAGNOSTIC-1`:

- `source_sha256`: 실제 관측 원문 bytes의 해시. 원문 전체가 사본에 들어 있다는 뜻도, 시험/승인 성공의 증거도 아니다.
- `snapshot_base64`, `snapshot_sha256`: 아래 프로파일을 거친 UTF-8 canonical JSON과 그 해시. **원문도 API 적용 payload도 아니다.** `snapshot_is_original=false`, `execution_authorized=false`를 명시한다.
- key/status·Binding/RUN/action/phase·관측시각·progress·오류코드와 제거 범주별 개수를 남긴다. UID/resourceVersion·비밀 참조 이름·비해당 spec/label은 유지하여 수동 조사에 연결한다. 인증·freshness·원본의 증적 인수를 대신하지 않는다.

| 위치 | 처리 |
|---|---|
| Route `spec.tls.key/certificate/caCertificate/destinationCACertificate` | 값 제거, 고정 `<REDACTED>` |
| 일반/init/ephemeral container 등 `env[].value` | 값 제거. `valueFrom.secretKeyRef`는 참조 구조만 그대로 유지 |
| root/Pod template 등 `annotations` 값 | 전부 제거. last-applied JSON 같은 비밀의 그림자 사본도 저장하지 않음 |
| `command/args` 문자열, `httpHeaders[].value`, 자유서술 `message` | 값 제거. 명령/헤더·오류 메시지의 인라인 값 복사 방지 |
| 이미 검증된404 | 정확 group/plural/name의 Status/Failure/NotFound/404를 재확인하고 부재 판정 필드만 투영. message/추가 필드는 저장하지 않음 |

200은 고정6객체의 정확한 kind/name/apiVersion·fds-app namespace를 재확인한다. duplicate/non-finite/잘못된 JSON·리소스/namespace 불일치·지원하지 않는 제거 위치의 구조·64단계 초과 중첩·1MiB 초과 snapshot은 사본 생성 전에 실패한다. 원문 저장 fallback은 없다. 기존 caller는 `diagnostics=FAILED`로 표시하고 fence를 보존한다.

## 한계와 복구

이 고정 프로파일은 **일반 비밀 탐지기/DLP가 아니다.** label·이름·비해당 spec·managedFields의 허용 범위를 임의 확대하거나 임의 필드의 비밀 부재를 입증하지 않는다. 실제 site의 baseline admission/플랫폼 입력은 지원 객체 범위·참조·권한을 검증하고 비밀을 label/리소스 이름/그 밖의 필드에 넣지 않아야 한다. 진단은 계속 비공개다. 이 변경을 공개 GitHub 업로드 허가로 쓰지 않는다.

제거된 값은 이 사본에서 재구성할 수 없다. 원문 SHA는 원본과의 무결성 연결용이며 제거된 필드의 개별 hash를 만들어 비밀 추측의 입력으로 남기지 않는다. manual recovery는 기존 승인 source·journal·실제 authenticated readback과 대조한다. 진단만으로 자기 ACK·소유·복구 승인·재전송 가능 여부를 확정하지 않는다.

같은 RUN/단계는 최초 파일만 보존한다. preflight가 다시 실패하면 두 번째 사본은 O_EXCL로 거부되어 `diagnostics=FAILED`이며 첫 사본·fence는 그대로다. 이를 성공/자동 재실행 허가로 바꾸지 않는다. v1/v2 파일은 자동 변환·삭제·덮어쓰기하지 않으므로 기존 파일에 이 프로파일이 소급 적용됐다고 주장하지 않는다. 기존 실제 사본이 있다면 #46 담당자가 승인된 보존/정리 절차로 판단한다.

## 실행·검증·담당

일반 개발 PC/저장소 루트에서 `python -m unittest discover -s tools/phase2 -p test_delivery_diagnostics.py -v`를 실행한다. 모든 값·응답은 합성이고 네트워크·클러스터·token 발급이 없다. 단위 시험은 Route TLS 값, env/command/header·annotation의 그림자 사본 제거, 원문·UID/spec 보존,404 투영, JSON/identity/shape 실패, 결정적 bytes와 phase 결속을 확인한다. POSIX 시험은 실제 0600 파일·제거 후 저장·덮어쓰기 거부를 Linux에서 확인한다(Windows SKIP).

독립 검토: 하영=값 제거·형식/비밀/비공개 사본 보존, 재환=실패 진단의 추적성·재현·수동 복구 정보·기존 시나리오 회귀. 전체 기존 API 시험을 중복 요구하지 않고 변경5파일과 영향 범위를 본다.

운영사본 보존/삭제 담당은 이권욱이다. 기존 권고(반출·재해시·내용 검토·인수 완료 후7일 내 해당 운영사본 정리)의 실제 기산시각·예정일은 Runtime 카드에서 정하고, 미해결 사고/fence는 이유·다음 검토 시각과 함께 HOLD한다. 중앙 원본·승인 digest·state·journal/ACK/active를 삭제하지 않는다. 팀 공용 증적 보존정책을 바꾸지 않는다.

반영 실패·미지원 객체가 생기면 새 송신을 멈추고 고정 오류·기존 원본을 보존한다. 문제 해결을 위해 원문 사본 저장으로 우회하거나 파일/fence를 지우지 않는다. 코드 복귀도 원본 파일 정리·추가 실행 승인이 아니다. 실제 site/credential·권한·authority5종은 계속 잔여다. Phase1 DB/pg_hba/영구 방화벽/NM/route/Harbor CA/공용 OCP/Runner/DNS/LB/AC·실제 cloud/Deploy/Runtime GO 변경0이다.
