# P1 CICD Unit 실패 차단 시험 준비

책임: P1-CICD-01 / 이권욱. 독립 코드 리뷰: 이하영. 관련 티켓은
[baseline#16](https://github.com/321Team/fds-project-baseline/issues/16)와
[infra-cicd#11](https://github.com/321Team/infra-cicd/issues/11).

## 현재 근거와 이번 변경

main `147f760298dd4a683ea58f2a887483769352b97d`의 TBS38395350748,
Push38404449622, Deploy38412128227은 성공했다. 그 성공을 고의 Unit 실패의
차단 증거로 사용할 수는 없다. 이번 PR은 **CICD-AC-04 시험 도구 준비**다.

`app-unit-negative.yml`은 main/manual/Attempt1에서만 실행한다. 기존 config의
원본 바이트·Source·공급 해시 검사 및 앱 Unit 시험을 그대로 거친 다음,
고정된 stdlib unittest 한 건을 별도 builder 컨테이너에서 실패시킨다.
앱의 기존 Unit 자체를 변조하는 시험은 아니다. 추가 시험의 실패가 기존
`build_scan.command()`의 nonzero 처리로 Contract/Build/Scan 진입을 막는지 본다.
이 범위를 AC04 증적으로 수용하는 판단은 실행 결과와 함께 별도로 기록한다.

- 정상 TBS Workflow/config/Source/공급물은 변경하지 않는다. 새 mode 기본값은 false.
- 새 mode는 별도 Workflow에 결속하며 config/source CLI 덮어쓰기를 금지한다.
- 고정 실패는 실제 unittest assertion/exit1이다. 임의 명령/테스트 이름 입력은 없다.
- 고의 실패 Run은 **GitHub FAILURE/프로그램 rc1**을 그대로 남긴다. 성공으로 감싸지 않는다.
- 정확한 step rc1·marker·`Ran 1 test`·`FAILED (failures=1)`·원본 로그 해시와
  own-CID 제거를 모두 확인해야 `EXPECTED_FAILURE_OBSERVED`다.
- 다른 Unit 실패, 준비 오류, timeout, 취소, 예상 밖 exit0, cleanup 미확인은
  `NOT_CONFIRMED`이며 모두 후속 단계를 차단한다.
- probe 컨테이너는 network none/read-only/비루트/host mount 없음, capability 제거,
  no-new-privileges, 메모리/PID 제한. cleanup은 private store의 정확한 CID·name·label·image
  대조 후 수행한다. 식별 불일치 시 다른 컨테이너를 지우지 않는다.
- 기존 TBS concurrency와 호스트 잠금을 공유한다. Push/Deploy와의 비중첩은 실행 전
  active Run·Runner 상태·작업창 확인으로 확보한다. 서로 다른 Workflow concurrency가
  자동으로 전체 Delivery를 직렬화한다고 주장하지 않는다.
- 전용 `FDS_CICD_UnitNegative.*` 원본과 FAIL 영수증은 정상 Push 입력으로 쓸 수 없다.
  이 Workflow는 source-read만 소비하며 Harbor Robot·Kubernetes Token을 받지 않는다.

## 실행 절차 — 독립 리뷰/병합 후

1. 최신 병합 SHA와 독립 리뷰 범위를 대조한다. 사용자 실행 요청의 범위 내에서
   정확한 새 1회와 시작/수집창을 실행 기록에 적는다. 과거 소진 회차는 재사용하지 않는다.
2. CICD active Run0, Runner online/idle/Worker0, 다른 앱·Harbor 시험 비중첩을 확인한다.
   source-read Secret은 값 조회 없이 존재만 확인하고, 공급·여유 공간을 재검사한다.
3. control01의 기존 승인 경로로 두 Deployment의 UID/generation/spec hash/image 및
   실제 Pod imageID/Ready/restart를 읽어 시각·수집기 hash·rc를 보존한다.
   10/14 09:07~09:08 준비 조회는 이미 시간이 지난 참고자료이며 실행 직전 자료를 대체하지 않는다.
4. `gh workflow run app-unit-negative.yml --repo 321Team/infra-cicd --ref main`을 한 번만
   dispatch한다. 자동 재시도/같은 Run rerun은 하지 않는다. 대상은 cicd-runner01 정상 UID1000,
   새 checkout과 전용 private evidence 경로다. sudo 인증/권한 확대는 이 시험 자체에 필요 없다.
5. Run/Attempt·Job·SHA·원본 result/로그/해시, fixture exit1, Build/Scan 미실행,
   own-CID 제거·Worker0을 확인한다. **Run FAILURE 자체만으로 시험 PASS가 아니다.**
6. 직후 동일 GET으로 두 Deployment UID/generation/spec/image·Pod imageID를 대조한다.
   Run 구간의 다른 Push/Deploy가 없었는지도 GitHub 기록으로 확인한다.
   원본 수신/해시 대조 후 증적 PR을 발행하고 독립 편입 리뷰·AC 판정을 구분한다.

이 구현에는 downstream Push/Deploy Job이 없다. 따라서 GitHub의 실제 `skipped`
Job이 있었다고 쓰지 않는다. 입증 가능한 범위는 기존 TBS 실행기의 실패 차단,
새 이미지 미생성, Push/Deploy 미호출, 별도 관측한 배포 이미지 유지다.
전체 Delivery 실패 경로/AC04 최종 인수는 이 자료와 운영 구조를 함께 검토한다.

## 중단·정리

예상 Unit 실패 외 오류·timeout·취소·이미지 drift·다른 active Run·cleanup 미확인 시 HOLD.
원본/claim/private storage를 보존하고 범위를 좁혀 잔존 상태를 확인한다. 운영 Deployment나
Harbor 데이터/서비스는 이 시험의 변경 대상이 아니므로 자동 rollback하지 않는다.
`prune`, 넓은 경로 삭제, 공급물 교체, 보안 설정 해제, 자동 재실행을 하지 않는다.
새 fixture의 finally는 정확한 own-CID 정리를 시도하나 프로세스 강제 종료는 finally를
보장하지 않는다. 이 경우 전용 store의 CID/label을 확인한 후 별도 제한 정리를 판단한다.

## 다음 순서와 한계

| 순서 | 범위 | 현재 상태 / 완료 조건 |
|---|---|---|
| 1 | AC04 고의 Unit 실패 | 이 PR의 코드 검증·독립 리뷰 → 1회 Run → 전후 image 대조·증적 인수 |
| 2 | AC05 취약점/스캐너 오류 | 기존 HIGH/CRITICAL 차단 원본을 재사용하되, 각 실제 오류와 당시 이미지 유지 증적의 누락을 별도 보완. 이번 Unit 시험으로 대체하지 않음 |
| 3 | AC07/08 실제 권한·Token 만료/갱신 | 새 Token의 제한된 발급/소비/만료·부정 요청 계획과 원본 필요. 이번 도구는 Token 발급/권한 변경 없음 |
| 4 | AC09 이전 승인 Digest 복귀 | 이전 두 앱 호환성/DB 영향·현재 이미지 복귀까지 정확히 결정한 별도 실행. 정상 Deploy 반복으로 대체하지 않음 |
| 5 | AC03/06/10 전체 인수 | HTTP/업무 E2E·CI→Harbor→Manifest→Pod 추적, 독립 검토 및 정본 인계 |

STATIC/MOCK/CI와 on-prem Runtime을 구분한다. 실제 fixture 컨테이너 실행,
전후 이미지 유지 및 AC 인수는 이 문서 작성 시 **NOT_RUN**이다.

기술 참조: [Podman 6.1 create](https://docs.podman.io/en/v6.1.0/markdown/podman-create.1.html),
[start/attach](https://docs.podman.io/en/v6.1.0/markdown/podman-start.1.html),
[GitHub run context](https://docs.github.com/en/actions/reference/workflows-and-actions/contexts).
