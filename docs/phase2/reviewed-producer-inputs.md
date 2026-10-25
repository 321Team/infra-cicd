# #46 기승인 이미지 공급과 실제 native CI 연결

2026-10-25 조회·구현. 코드 선택과 오프라인 준비이며 새 배포 승인이나 현재 환경 인수가 아니다.

## 기존 원본에서 확인한 값

| 구분 | 확인한 원본 / 의미 |
|---|---|
| 이미지 제작 source | fds-msa `affbc69beef9967f7977d09a6fec14f7fb6c0e94`, 기존 PR27 승인·이미지 공급 원본 재사용 |
| 공급 원본 | [ev218 병합본](https://github.com/321Team/fds-project-evidence/blob/7e6bb13dafad1e5f60887880576364fe02a461de/evidence/P1-CICD-01/push-run-38404449622-20261013/NEXT_DEPLOY_HANDOFF.json), SHA256 `79f4f056290b953912dcb229604ea0fc20d3a2412ed8a66ff13e764ef33996f1` |
| 원본의 실제 한계 | `OBSERVED_PUSH_OUTPUTS_ONLY_NOT_DEPLOY_AUTHORIZATION`, `deploy_authorization=NOT_RECEIVED`, `deployment_current_baseline=NOT_REFRESHED_IN_THIS_RUN` 명시. producer 사실만 재사용하며 실행 grant·previous·현재 baseline으로 사용하지 않음 |
| TBS native | infra-cicd run38395350748/attempt1, workflow363199301, workflow SHA `147f760298dd4a683ea58f2a887483769352b97d`, job115289040431 `app`, step4 `Validate handoff and run the approved application`, 완료/success |
| Push native | run38404449622/attempt1, workflow375319885, 같은 workflow SHA, job115320270242 `push`, step5 `Execute approved Push`, 완료/success. step4의 비실행 점검(skipped)을 Push 성공으로 사용하지 않음 |
| 현재 render source | MSA42 merge `69e28c494a120c388afe18d901043d05333d7c7c`. [계약9절](https://github.com/321Team/fds-msa/blob/69e28c494a120c388afe18d901043d05333d7c7c/deploy/DELIVERY-CONTRACT-rosa.md)은 기존 두 digest 재사용과 최초 P2 previous 부재를 명시 |
| 변하지 않은 앱 코드 | 두 source의 transaction-api tree `7351c35ece23f5807787af9f156b1ce4fa5e7cc8`, fds-engine `89f203edf408b8358f644c49362b5a7bc94d073e`, ci `f27523fe7f81cb4e6fbfc637d385ff146460d183`, ci/apko `d95202cce53b93fc12c85b692b8e810b222f9f82` 일치 |

native API 원문과 기존 계약/공급 원본을 대조했다. workflow SHA, 이미지 제작 source,
render source는 서로 다른 값이다. 현재 render source의 Source validation 성공 하나를
Test/Build/Scan/Push로 대체하지 않으며, 트리 일치를 전체 외부 빌드 입력이나 재현 빌드의
증거로 확대하지 않는다. 고정 이미지의 기존 공급 계보를 재사용하므로 같은 이미지 재빌드·Push를 요구하지 않는다.
원본의 10/13 scan은 해당 시점 결과다. 현재 취약점 상태·scan 최신성 수용을 자동 생성하지 않는다.

## 기존 SITE 조립에 넣는 방법

1. 실제 RUN의 `build_configuration`에 위 **실제 공급 원본 SHA**를 supply receipt hash로 넣는다.
   platform receipt·RUN·grant·기간을 만들거나 추정하지 않는다.
2. `connect_reviewed_producer(config, grant=실제 producer grant,
   previous_images=명시 채택한 이전 profile)`을 사용한다. `delivery_fds_operating`에서도 같은 함수를 제공한다.
   grant의 Binding·원본 pin·기존 control pin 및 유한 기간 형태를 대조하고
   기존 `EvidenceAdoption(ProducerFacts)`와 정확한 `StagePin`4개를 반환한다.
   함수 생성은 파일·네트워크·clock·자격을 사용하지 않는다.
3. 기존 `NativePlanSelection`의 producer에 adoption을, 기존 operating 조립의
   `producer_pins`에 pins를, `producer_repository`에 `PRODUCER_REPOSITORY`를 연결한다.
   producer **원본 route는 evidence 저장소**, native run reader는 **infra-cicd 저장소**다.
   기존 `SourceRoleWiring`은 둘을 분리하고 기존 control reader와 같은 infra-cicd 채널을 재사용할 수 있다.
   새 토큰이나 별도 승인 파일을 만들지 않는다. 실제 메모리 자격 공급은 후속 실행 범위다.
4. 반환된 선택은 인증 결과가 아니다. 기존 Runtime factory가 원본·control의 전후 인증과
   `ActionsRunSource`의 실제 run/attempt/job/step 성공 검사를 결합한다. TBS의 한 집계 step은
   내부 세 단계 성공의 단독 증거가 아니며 검토된 공급 원문 의미와 함께 사용한다.
5. previous는 필수 명시 입력이다. 과거 P1 관측값·현재 desired·지금 GET한 객체에서 자동 추론하지 않는다.
   인계 뒤 새 full baseline과 실제 owner/spec/previous를 기존 경로에서 대조한다.

공급 원본 pin은 독립 편입된 PR218의 보존 branch
`heads/evidence/p1-push-readiness-20261013-2150` / 실제 head
`9647a83724dea442c9e8238e60d68bfe2602dc8d`를 선택했다.
원본 commit도 같은 값이며 merge7e6bb13d의 Git blob `601da8416b12b08b2c722fb5ff36a50f4ce180ca`와 동일하다.
이 ref가 바뀌거나 삭제되면 기존 reader가 중단한다. 자동 main 전환·대체 pin은 없다.

## 남는 연결과 단계

확인된 producer 값을 더 이상 일괄 미수신으로 두지 않는다. 실제 실행 grant·인계 후 baseline·
본인 메모리 인증 공급·actual job/helper/READY·finish scope의 최종 연결은 아직 남는다.
finish 관측 원본·hash는 실제 sender close 뒤 생산되므로 미래 파일 제출 대상이 아니다.
`SITE=None`·기본 dispatch 차단·Runtime NOT_RUN을 유지하며 자격 발급·앱 배포는 수행하지 않는다.
상시 시간대, 10/26 08:30 소비 종료 계획과 12:00 최종 정리 완료 기준도 유지한다.
