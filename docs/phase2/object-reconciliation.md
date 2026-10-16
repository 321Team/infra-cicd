# #46 객체 대조 — 오프라인 provider 보조 구현

2026-10-16 · 이권욱 · CODEX_ASSISTED. `delivery_objects.ObjectReconciler`는 기존 verifier/session의 미구현 `objects()`를 위한 순수 비교 함수다. **실제 API reader·운영 provider·배포 CLI가 아니다.** CLI는 계속 차단한다. 합성 fixture를 실제 baseline/승인값으로 사용할 수 없다.

## 입력과 신뢰 경계

- 신뢰된 통합 코드가 고정 Binding, 동일 render/contract와 pinned producer validator, 6개 객체의 인증된 최초 조회 원본을 전달한다. `None`은 같은 credential로 확인한 실제 404에만 매핑한다. 조회 거부/timeout/파싱 실패를 부재로 변환하면 안 된다.
- 이후 같은 credential로 읽은 fresh 6개 원본과 기존 durable journal/관측 PATCH 응답을 받는다. 객체당 1MiB, JSON duplicate/non-finite/타입·범위 오류는 거부한다. namespace는 `fds-app`, `rosa-app`은 overlay다.
- 이 함수는 최초 원본의 진위·허용 소유자·기존 객체 안전성, 조회 freshness/deadline, API admission/defaulting 정책을 증명하지 않는다. 이 조건을 갖춘 실제 provider가 비교 성공 뒤 `ObjectEvidence`를 반환해야 한다. 본 함수는 실행 승인이나 `ObjectEvidence`를 직접 만들지 않는다.

## 판정

| 상태 | 기대값 / 실패 시 처리 |
|---|---|
| 쓰기 전 | 고정 baseline과 동일해야 한다. 기존 이미지가 새 current와 다른 것은 정상일 수 있으며 자동 덮어쓰기 권한은 아니다 |
| intent 저장 후 송신 전 | 다음 pending은 허용하지만 객체는 여전히 baseline이어야 한다. ACK 수와 응답 수가 같아야 함 |
| 정상 PATCH+durable ACK | ACK 순서와 일치하는 실제 응답만 예상 상태로 채택. 기존 UID 유지, 승인 render의 spec/labels/annotations 포함, `fds-deployer` Apply 소유 기록 필요 |
| 응답 있음·ACK 없음 | `OBJECT_REPLY_ACK_MISMATCH`, 다음 송신 없음. 원본·pending·fence를 보존하고 수동 복구 검토 |
| 타인 변경/삭제·재생성 | UID/spec/labels/annotations/finalizer/비status field ownership 등이 달라지면 STOP |
| 정상 관측 변화 | top-level status/resourceVersion 및 managedFields의 time·순수 status 소유 기록만 비교에서 제외. Service 외부 LB ingress는 별도로 거부 |

managedFields는 [Kubernetes SSA 공식 문서](https://kubernetes.io/docs/reference/using-api/server-side-apply/)가 설명하는 필드 관리 기록이다. 이것을 사람의 실행 권한이나 필드 변경 주체의 인증서로 취급하지 않는다. 비status 항목은 시간 외 모든 내용을 비교하며 알 수 없는 subresource는 STOP한다. list 순서/길이를 완화하거나 모든 controller 변경을 허용하지 않는다. 정상 controller가 비status annotation/spec을 바꾸면 보수적으로 STOP할 수 있으며 실제 원본을 보고 한정 허용을 별도 검토한다.

서버가 추가하는 dict 기본값은 ACK의 승인 필드 대조에서 허용한다. 해당 추가값의 안전성을 일반적으로 검증하는 admission validator는 여기 구현하지 않았다. 실제 승인된 플랫폼/정책 및 API readback 검증은 운영 provider의 필수 조건이다. 현재 CLI의 차단을 해제하는 근거로 이 helper만 사용할 수 없다.

## 재현·실패·복구

개발 PC의 저장소 루트, Python3.10 이상 표준 라이브러리:

```text
python -m unittest discover -s tools/phase2 -p test_delivery_objects.py -v
```

socket/DNS는 시험 harness에서 금지한다. 정상 기준 RC0·실패/오류0, PASS/SKIP 별도 기록. 비교·session 연결22시험은 모두 합성 자료다. 실제 TLS/서버·Runner·권한·Deploy 시험이 아니다. 최초 통합 fixture에서 PATCH/GET의 UID가 서로 달라 정상 경로가 `DEPLOYMENT_DRIFT`로 멈춘 1건은 fake GET 저장소를 같은 응답으로 맞춰 정정했다. 제품 판정 우회는 하지 않았다.

실패하면 새 배포를 하지 않고 원본 보존·기존 recovery 읽기 진단으로 인계한다. baseline 자동 갱신·ACK 추정·재전송·force/prune/delete·fence 제거·권한 재발급은 없다. Phase1 설정 변경과 실제 클라우드 호출도 없다.
