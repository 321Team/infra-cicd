# #46 객체 대조 — 오프라인 provider 보조 구현

2026-10-16 · 이권욱 · CODEX_ASSISTED. `delivery_objects.ObjectReconciler`는 기존 verifier/session의 미구현 `objects()`를 위한 오프라인 비교 helper다. **실제 API reader·운영 provider·배포 CLI가 아니다.** CLI는 계속 차단한다. 합성 fixture를 실제 baseline/승인값으로 사용할 수 없다.

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
| 정상 관측 변화 | top-level status/resourceVersion 및 managedFields의 time·순수 status 소유 기록, 아래 조건의 ACK된 Deployment revision만 비교에서 제외. Service 외부 LB ingress는 별도로 거부 |

managedFields는 [Kubernetes SSA 공식 문서](https://kubernetes.io/docs/reference/using-api/server-side-apply/)가 설명하는 필드 관리 기록이다. 이것을 사람의 실행 권한이나 필드 변경 주체의 인증서로 취급하지 않는다. 아래 한정 revision 외 비status 필드/소유는 비교하며 알 수 없는 subresource는 STOP한다. list 순서/길이나 모든 controller 변경을 허용하지 않는다.

### Deployment revision 한정 예외 — 리뷰5544846402 대응

기존 helper에서 생성·template 변경 후 정상 revision 변화가 `OBJECT_DRIFT`가 되는 결함을 수용했다. **같은 RUN의 durable ACK 이후, 고정된 두 apps/v1 Deployment에만** `metadata.annotations[deployment.kubernetes.io/revision]`과 그 필드 소유 항목 하나를 제외한다. 최초 baseline 및 해당 객체의 ACK 전에는 revision/소유도 그대로 비교한다.

- manager는 `kube-controller-manager`, operation은 `Update`, apiVersion은 `apps/v1`, fieldsType은 `FieldsV1`이어야 한다. fieldsV1의 비status 부분은 정확히 `f:metadata → f:annotations → f:deployment.kubernetes.io/revision → {}` 하나다. `f:annotations` 아래에 빈 `".": {}`가 함께 있는 변형 한 가지만 추가 허용한다. 이는 [Kubernetes FieldsV1 형식](https://github.com/kubernetes/apimachinery/blob/v0.34.1/pkg/apis/meta/v1/types.go#L1270)의 해당 필드 자체 소유 표시이며 모든 자식 annotation을 허용한다는 뜻이 아니다. 다른 annotation/spec 소유, 비어 있지 않은 점 표식·다른 위치의 점 표식, 중복 owner는 이 예외로 숨기지 않는다.
- main-resource 항목뿐 아니라 `subresource: status`에서 위 metadata와 `f:status`만 있는 항목을 지원한다. 이는 Kubernetes [v1.34.1 Deployment controller](https://github.com/kubernetes/kubernetes/blob/v1.34.1/pkg/controller/deployment/sync.go#L153)의 revision 설정 후 UpdateStatus 경로를 반영한다. 이 upstream 버전을 실제 ROSA 버전이라고 가정하지 않는다.
- 값은 양의10진 int64 문자열이다. baseline·ACK 응답·앞서 성공한 관측보다 낮아지거나 사라지면 STOP한다. 전체6개 대조 성공 후에만 메모리상의 최댓값을 갱신한다. 이것은 durable checkpoint·ACK 추정·프로세스 재시작 후 재개 허가가 아니다.
- **실제 ROSA/OpenShift manager·fieldsV1 모양은 미검증 가정이다.** 첫 정상 실행의 기존 원본 readback으로 확인한다. 별도 시험/재생성/담당자 새 자료 양식을 추가하지 않는다. 모양이 다르면 exact 원본으로 예외를 재검토하며 manager 전체/metadata 전체를 허용하지 않는다.

revision이 바뀌더라도 spec/image/labels/다른 annotation/UID/finalizer·소유 변경은 계속 STOP한다. 예외 밖 정상 플랫폼 변경은 현재도 보수적 STOP이며 실제 원본 없이는 무차별 완화하지 않는다.

10/16 재환 리뷰5545354950: 앞선 예외는 annotations 맵을 처음 만든 controller의 `".": {}` 형태를 거부했다. 오프라인 재현으로 ACK 뒤 STOP을 확인하고 위 한정 변형만 수용했다. 실제 ROSA에서 그 형태가 관측됐다고 주장하지 않으며, 이 검증기를 통과시키려고 앱 render에 불필요한 annotation을 추가하지 않았다.

서버가 추가하는 dict 기본값은 ACK의 승인 필드 대조에서 허용한다. 해당 추가값의 안전성을 일반적으로 검증하는 admission validator는 여기 구현하지 않았다. 실제 승인된 플랫폼/정책 및 API readback 검증은 운영 provider의 필수 조건이다. 현재 CLI의 차단을 해제하는 근거로 이 helper만 사용할 수 없다.

## 재현·실패·복구

개발 PC의 저장소 루트, Python3.10 이상 표준 라이브러리:

```text
python -m unittest discover -s tools/phase2 -p test_delivery_objects.py -v
```

socket/DNS는 시험 harness에서 금지한다. 정상 기준 RC0·실패/오류0, PASS/SKIP 별도 기록. 비교·session 연결36시험은 모두 합성 자료다. 기존22에 revision 회귀11과 점 표식 정상/비정상·baseline/ACK/단조성3을 추가했다. 전체 session/finish는 점 표식 유무와 두 subresource 경로를 함께 검증한다. 실제 TLS/서버·Runner·권한·Deploy 시험이 아니다. 최초 통합 fixture의 PATCH/GET UID 불일치와 앞선 추가 시험의 tuple 수정 오류는 fixture 결함으로 정정했으며 제품 판정을 우회하지 않았다.

실패하면 새 배포를 하지 않고 원본 보존·기존 recovery 읽기 진단으로 인계한다. baseline 자동 갱신·ACK 추정·재전송·force/prune/delete·fence 제거·권한 재발급은 없다. Phase1 설정 변경과 실제 클라우드 호출도 없다.

pending 객체의 `OBJECT_DRIFT`는 타인 변경과 응답을 잃은 자기 쓰기를 코드만으로 구분하지 않는다. 기존 원본·실제 readback에서 `fds-deployer` Apply 소유/UID·승인 spec부터 대조하되, 그 소유 이름만으로 자기 ACK를 확정하거나 재전송하지 않는다.
