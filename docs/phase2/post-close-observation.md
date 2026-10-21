# #46 종료 관측 생성부·원본 해석기·현장 연결

이권욱 담당 #46의 코드 작업이다. 다른 팀원의 미제출 파일을 기다리는 작업으로 돌리지 않는다. 원본을 만드는 P2 코드가 없었던 부분에 `delivery_finish_observation.py`를 추가했다. 기존 GitHub 원본 경로는 유지하고 직접 관측 경로를 명시적으로 선택할 수 있다.

## 생성과 판정의 차이

`P2-CICD-post-close-observation-1`은 **이번 코드가 생산하는 내부 비공개 관측 형식**이다. 과거 운영 형식이라고 주장하지 않는다. 새 팀원 제출 양식·중복 GO·서명 키·GitHub 게시 절차를 요구하지 않는다.

1. 실제 `sender.close()` 성공 뒤에만 session이 `SenderClosed`를 만든다. 같은 credential 객체·Binding·terminal progress·시각을 결속한다. close 실패·불완전 적용·finalize 미요청에는 원본을 만들지 않는다.
2. 같은 credential/CA/endpoint의 기존 `ObjectProvider/ObjectReconciler`로 close 이후 고정 6객체/2이미지 또는 명시한 9객체/3이미지를 새 GET한다. UID/spec/소유권과 durable ACK를 대조하고 Deployment의 현재 generation·replica·ready/available·실패 조건도 확인한다.
3. 실제 RUN/action/Binding/progress·close/관측 시각과 **읽은 응답 바이트 그대로**를 직렬화한다. 없는 UID/spec/status·실제값·승인값을 채우지 않는다. 실행 문맥 식별자 기록과 누락된 서버 관측 사실 생성은 구분한다.
4. strict decoder는 정확한 키·타입·중복 키·비유한 수·크기·base64·동일 문맥/자격/close·미래 시각·scope를 확인하고 같은 reconciler로 native 본문을 해석한다. 해시·파싱 성공만으로 종료를 판정하지 않는다.

로컬 close는 **전송 객체의 종료 사건**이며 토큰 폐기·타인 세션 종료·운영 권한 회수의 증거가 아니다. 기존 검증 권한 유지 결정을 바꾸지 않는다. `EXCLUSIVE_ACCESS_ENDED`는 이 RUN에 필요한 독점 업무가 끝나 claim 해제를 시도할 수 있다는 기존 lifecycle 판정이며 모든 사용자 접근 종료를 뜻하지 않는다.

관측 원본의 `execution_authorized`는 항상 false다. 생성·해석만으로 CompletionEvidence를 반환하지 않는다. 기존 종료 범위의 실제 원본 의미를 검토한 **명시적 코드 채택 정책**과 fresh source/control 인증이 필요하다.

## 명시적 사이트 구성

검토한 사이트 Python 코드에서 다음 구성을 기존 `runtime.main(..., finish_observation=...)` 또는 `RuntimeAuthorityFactory(..., finish_observation=...)`에 전달한다. 미래 종료 원본 pin은 필요하지 않다.

```python
from delivery_finish_observation import FinishObservationConfig, FOLLOWUPS

# 변수는 이미 확보·검토한 실제 자료다. 이 예제는 승인/운영값을 제공하지 않는다.
finish_observation = FinishObservationConfig(
    scope_grant=reviewed_existing_completion_scope,
    readers=existing_finish_source_readers,
    required_followups=FOLLOWUPS,
)
```

scope_grant는 기존 승인·업무 종료 범위 원본의 exact pin·현재 control·주체·채택 근거·기간이다. 배포 승인·PR 승인을 자동 복사하지 않는다. JSON/CLI/env 권위 loader와 기본 설정은 없다.

**남은 독점 업무가 post-close 객체·rollout readback인 범위만 지원한다.** 실제 원본이 이 의미를 허용하는지 확인하는 책임은 사이트 코드 채택·독립 리뷰에 있다. Health/Rule 등 추가 독점 업무가 남았거나 원본에서 이 종료 범위를 확인하지 못하면 정책을 선택하지 않고 미구성 STOP을 유지한다. 이 PR이 실제 의미 채택·Runtime GO를 대신하거나 AC를 낮추지 않는다.

기존 한 승인 원본이 배포와 종료 범위를 모두 명시하면 같은 원본을 코드에서 별도로 채택할 수 있다. 같은 pin 사용을 금지해 새 GO·중복 제출을 강제하지 않는다. 반대로 원본에 없는 의미를 상수·타입·해시로 만들어 넣을 수는 없다. 기존 `CodeAdoptedGrantResolver`와 같은 reviewed-code trust root이며 자동 자연어·서명 검증기가 아니다.

`ReadbackFinishAuthority`는 기존 `CodeAdoptedGrantResolver/ReviewedApprovalAdapter`로 원본→control→원본→control을 관측 전후 fresh 인증한다. 계정·해시가 의미 권위를 대신하지 않는다. 변경·철회·만료·다른 RUN은 STOP이다. 기존 finish_capture/finish_collection·이미 설치한 slot과 함께 선택할 수 없다.

실제 순서는 적용·durable terminal → sender close 성공·내부 사건 → 기존 verifier 재확인 → 종료 scope 인증 → fresh readback → 원본 생성·해석 → 단회 보존 → 종료 scope/control/progress/기한 재확인 → 기존 verifier 전체 재확인 → claim.complete다. 완료 marker를 선행 원본으로 쓰는 순환은 없다. 원본 생성·보존만으로 잠금을 풀지 않는다. status/preflight는 종료 관측·저장·완료를 하지 않는다. status는 자격 수신/source I/O도 하지 않는다. 기존 경로와 기본 STOP은 유지한다.

## 비공개 원본·실패·복구

기존 고정 POSIX store root의 `<binding.key>.finish-observation.json`에 보존한다. 새 root로 active 잠금을 우회하지 않는다. 기존 store의 0700 root·0600 file·O_EXCL/no-follow·fsync·inode/원문 대조를 재사용한다. 개별 원문 최대 1MiB, 전체 최대 13MiB이며 Windows에 약한 대체 저장 경로를 만들지 않는다.

**원본에는 애플리케이션 설정 등 민감정보가 있을 수 있다.** redacted diagnostic이 아니므로 stdout·예외·공개 GitHub·PR에 올리지 않는다. 토큰은 원본에 넣지 않는다. 비공개 전달 시 고정 검토 공유 경로를 사용한다. 이번 작업에서 운영 원본 생성·반출은 하지 않았다.

GET 거부·TLS/시간 오류·객체 변경·rollout 악화·원본 오류·저장 실패·철회·기한 초과는 STOP이며 claim/journal을 보존한다. 부분 파일이나 저장 후 판정 실패 원본을 삭제·덮어쓰기·재사용하지 않는다. 생산부는 단회이며 실패 후 재시도하지 않는다. 새 RUN 자동 생성·자격 refresh·자동 rollback/unlock도 없다. 기존 읽기 전용 복구에서 원본·journal·실제 상태를 대조한다.

GET/source는 같은 request deadline을 공유하고 단계 전후 progress·wall/monotonic을 확인한다. close 사건의 session effective clock을 anchor로 사용한다. 여러 GET은 원자 snapshot이 아니다. 파일시스템 fsync의 커널 무응답까지 hard timeout으로 취소하거나 trusted Python 코드를 sandbox로 격리한다고 주장하지 않는다.

scope adapter/resolver는 factory bundle이, provider는 기존 OperationalApplication.finally가 정리한다. session에 이중 소유를 추가하지 않는다. source reader는 부모 소유로 유지하며 실제 권한을 회수하지 않는다.

## 검증·잔여

```text
python -m unittest discover -s tools/phase2 -p test_delivery_finish_observation.py -v
python -m unittest discover -s tools/phase2 -p test_*.py -v
```

시험은 명시적 SYNTHETIC fixture/API/source/control/자격/clock과 임시 로컬 파일만 사용한다. Windows는 생성·해석·정책·main/provider/session 연결, Linux hosted CI는 같은 시험과 실제 POSIX 단회 보존·symlink/덮어쓰기 거부를 확인한다. CI 성공은 Runtime·실제 ROSA 원본·인수 증적이 아니다.

**생성 코드·자체 형식 해석기·main→factory→session 연결은 이번 구현 범위**다. 실제 종료 scope 의미 채택·issuer/producer/source/credential·사이트 입력·최신 HEAD 사람 리뷰·별도 승인 Runtime 정상/실패·복구·Health/Rule 인수는 남아 있다. #46 Status/AC·작업창·운영 권한·정본 발행·종결은 변경하지 않는다.
