# 첫 배포의 빈 기준선 채택

`tools/phase2/delivery_baseline.py`의 `FirstDeploymentBaseline`은 기존 `ObjectReader`가 **정확한 대상 6개 모두에 인증된 404**를 반환할 때만 `(None,) * 6` 기준선을 채택한다. 기존 객체를 자동 인수하거나, 403·TLS 실패·일반 프록시 404를 부재로 처리하지 않는다. 이 adapter만으로 배포·권한·현장 준비가 승인되지는 않는다.

실제 현장 연결은 아직 구성하지 않았다. 이후 운영 연결 시 검토된 구성 코드가 기존 플랫폼 인증 뒤 생성한 고정 CA/endpoint·Binding·같은 메모리 자격의 `ObjectReader`, 실제 durable progress 조회, 기존 원본의 provenance를 명시적으로 전달해야 한다. 이 adapter 자체는 POSIX syscall을 추가하지 않지만 전체 영구 store/session의 기존 Linux/POSIX 전제는 유지한다. 실제 플랫폼의 issuer/audience/자격 수명·최소 RBAC 인증과 approval/producer/finish 검증은 여전히 별도다. provenance 문자열이나 Python 타입을 승인 원본으로 변환하지 않는다. 기본 CLI는 여전히 미설정/차단이며 실제값·권한·승인 스키마를 추가하지 않는다.

실행 위치는 검토된 현장 저장소의 Python 구성 코드다. `AuthorityAdapters.baseline` callable로만 선택적으로 연결한다. `baseline(request)`를 호출하면 같은 요청·Binding·자격과 `deploy` 동작, journal/응답이 없는 시작 progress를 확인한 뒤 6개의 정확 GET을 공유 absolute deadline 안에서 순서대로 조회한다. 각 조회 전후 progress와 기한을 다시 확인하며, 정상 반환은 같은 request·Binding과 기존 provenance를 가진 `AdmittedBaseline`이다. 기존 `ObjectProvider`는 이를 받은 뒤에도 객체를 새로 조회·대조한다.

이 조회는 원자적 snapshot이 아니다. 기존 독점 claim·작업창·직전 권한/대상 확인을 유지해야 한다. 이미 객체가 하나라도 있으면 기존 소유권·허용 spec을 인수하는 별도 정책이 필요하며, 이 도구가 삭제하거나 부재로 바꾸지 않는다. restore·부분 쓰기 후 재개/새 baseline 채택은 거부한다.

실패하면 고정 오류코드로 닫히며 추가 GET·재시도·쓰기·journal 삭제·강제 잠금 해제를 하지 않는다. 실패한 관측을 캐시하지 않는다. 다음 시도는 기존 세션의 재실행 안전성과 실제 권한 판단을 따르며, 이 adapter가 재GO·자격 갱신을 제공하지 않는다. progress callback은 기존 trusted 구성의 bounded 읽기 함수여야 한다.

추가 범위 오프라인 시험:

```text
python -m unittest discover -s tools/phase2 -p test_delivery_baseline.py -v
```

모든 CA·자격·응답은 합성 fixture이며 가짜 TLS socket만 쓴다. 기존/오류 응답·같은 자격/대상·진행 상태 변경·공유 기한·실패 후 새 조회·고정 오류를 검증한다. Windows 결과와 hosted Linux CI를 구분하고 실제 ROSA·Runner·배포 또는 첫 배포 객체 부재의 Runtime 증적으로 쓰지 않는다.
