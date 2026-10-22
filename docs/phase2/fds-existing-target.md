# #46 기존 FDS 6객체 기준선의 명시적 연결

`delivery_fds_existing_inputs.py`는 수동 C 이후 이미 존재하는6객체를 **실제 원본의 허용 UID/spec/owner·이전 이미지 의미를 검토한 뒤** 기존 `BaselineAdoption`/`CodeAdoptedBaseline` 경로에 연결하기 위한 코드다. 운영 기준선·GO를 새로 채택하거나 현재 객체를 조회/삭제하지 않는다. `SITE=None`과 Runtime workflow를 유지한다.

## 기존 경로와 분리

- `delivery_fds_site_inputs.validate_plan`/`connect_parent`: 기존 첫 빈 대상 전용, `baseline=None`·`previous_images=None`·실제 fresh404 조건을 유지한다.
- `validate_existing_plan`/`connect_existing_parent`: 별도 명시 API다. 실제로 검토한 `BaselineAdoption(grant, objects)`와 producer의 **명시적인 full previous tuple**을 요구한다. 기존 객체가 있다는 이유로 이 경로를 자동 선택하지 않는다. 이미 완성한 계획 또는 실제 자격 수신 후 완성할 trusted materializer 중 하나만 전달한다.

두 경로는 같은 FDS API/UUID·namespace·현재 source/render/contract·current digest2·subject/issuer/CA profile을 대조한다. 공통 비교 함수를 재사용했으며 첫 배포의 제한을 낮추거나 기존 객체를 first-empty-target으로 취급하지 않는다. restore·end-page/9객체·다른 source/대상은 이 연결에서 지원하지 않는다.

## 필요한 실제 입력과 확인 범위

| 입력 | 연결 시 확인 | 여전히 담당자가 실제 근거로 채택할 것 |
|---|---|---|
| BaselineAdoption | 같은 Binding의 grant, 정확한 대상 순서6개의 모두 존재하는 raw bytes, 서로 다른 UID | 원본·control의 실제 의미와 허용 owner/spec·기준선의 대상·시각/소유권. grant 객체/해시만으로 승인이 아님 |
| 객체 raw | 기존 `normalized(decode(raw), exact_key)`로 namespace/UID/spec/managedFields 형식을 검사. bytes를 변경하거나 다시 만든 관측으로 대체하지 않음 | 실제 API 원본, inline 비밀 제거가 필요한 경우 원본과 정제본의 별도 취급. 비공개 raw는 공개 GitHub에 올리지 않음 |
| previous_images | 정확한 서비스 순서2개·full immutable digest URI, 각 기존 Deployment의 유일한 컨테이너 이름/이미지와 일치. 미선언 sidecar/init/ephemeral은 이2-image profile로 인수하지 않음 | producer의 실제 previous 채택/계보. 현재 digest를 자동 복사하지 않으며, 같더라도 원본과 tuple에서 명시적으로 일치해야 함 |
| source readers | approval/platform/producer/finish/**baseline** 순서의5개 역할, 각 실제 original/control repository Pin 일치 | 검토된 repository 범위·현재 read credential. 4역할 첫 배포와5역할 기존 대상을 잘못 연결하면 준비 단계에서 거부 |

replicas0 등 실제 중지 상태를 인수할 수 있다. previous는 실제 선언된 baseline **spec 이미지의 추적값**이며 이전 Ready·실제 pull 성공·검증된 rollback 목적지를 증명하지 않는다. 원본에 없는 readiness나 복구 성공을 만들지 않는다.

## 실행 위치·순서와 정상/실패 판정

검토된 native job의 site Python 구성에서 `connect_existing_parent`를 명시적으로 선택한다. 이 함수의 인자는 기존 parent와 같고, 실제 baseline/previous는 완성 계획 또는 trusted materializer에서 공급한다. 파일/JSON에서 승인이나 baseline을 자동 로드하지 않는다. 생성 시 callback/파일/네트워크 I/O는0이다.

1. 기존 driver의 native context/Runner·부모 topology·peer/nonce·메모리 자격 수신 검사를 그대로 거친다. 실제 호스트/인증 채널·job rendezvous·원본 의미 채택은 여전히 별도 잔여다.
2. 정해진 existing validator가 반환 계획의6객체/previous/profile을 대조한다. 반대 모드 계획 반환·누락·Binding/역할 불일치는 STOP이고 현재 이미지로 보충하지 않는다.
3. 기존 `RuntimeAuthorityFactory`가5번째 원본/control을 인증하고 `CodeAdoptedBaseline`이 **원본 검증→같은 credential의 fresh GET6개·승인 UID/spec/owner 비교→원본/control 재검증**을 수행한다. fresh GET 자체를 baseline으로 채택하지 않는다.
4. 일치해야 기존 claim/checkpoint·ObjectReconciler·SSA·finish 흐름으로 넘어간다. 이후 실제 응답 UID·진행/ACK·사양/소유 변동은 기존 검증을 따른다. 본 코드가 새 배포/재시도 권한을 부여하지 않는다.

검토 뒤 UID/spec/owner 변경·원본/control 철회·인증/GET 오류면 claim/write 전에 차단한다. 쓰기 뒤 실패하면 실제 ACK/receipt와 fence를 보존하며 삭제·자동 rollback·unlock·fresh GET 재채택을 하지 않는다. 기준선 read/compare는 원자적 snapshot/쓰기 lock이 아니다. 기존 단일 writer·독점 작업 범위를 유지해야 한다.

기존 보수적 normalization의 status/resourceVersion 변동 허용은 유지한다. resourceVersion만 바뀌었다고 새 사양으로 인수하거나 무조건 실패시키지 않는다. 기존 core의 쓰기 계약/동시성 한계를 이 profile 연결에서 바꾸지 않았으며 실제 RV 기반 조건부 쓰기나 타 writer 대응을 새로 검증했다고 주장하지 않는다.

## 오프라인 시험과 운영 잔여

시험은 SYNTHETIC baseline·승인·자격·API만 사용한다. 정확 raw/previous 보존·두 모드 교차 거부·원본/control 인가·fresh UID/spec/owner 변경·control 철회·부분 거부 fence/ACK 보존·기존 허용 status/RV 변동을 실제 factory/provider로 검증한다. source/readback은 가짜 경계이며 MemoryStore는 영속 Runtime 증거가 아니다.

권욱은 실제 baseline 원본/control의 의미·객체 bytes·previous·허용 owner와 운영 SITE를 채택해야 한다. 하영의 실제 readback과 재환의 producer/이전 이미지 근거는 기존 자료 중 해당 부분만 연결한다. 실제 값이 없으면 비워 두며, 같은 전체 원본·서명·시험 재제출이나 기존6객체 삭제를 새 선행조건으로 요구하지 않는다. STATIC/MOCK/CI·독립 REVIEW·MERGE·실제 RUNTIME·AC 인수를 구분한다.
