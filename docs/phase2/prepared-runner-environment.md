# #46 P2 전용 소스·입력·parser 준비 — 2026-10-25

사용자의 P2 전용 경로·현장 준비 지시에 따라 Runner `cicd-runner01`의 기존
`server01_cicd-runner`/UID1000 HOME 아래 준비했다. 실제 발급·앱 배포는 하지 않았다.

## 실제 준비한 경로

루트는 `/home/server01_cicd-runner/fds-p2-delivery`다. 기존 경로를 chmod하거나
덮어쓰지 않고 신규 경로만 생성했으며 루트·아래 구분 경로는 UID1000/mode700으로 확인했다.

| 경로 | 용도·실제 확인 |
|---|---|
| `state` | 빈 영속 claim/checkpoint 루트. 실제 claim 생성·해제·복구를 수행하지 않음 |
| `sources/69e28c494a120c388afe18d901043d05333d7c7c` | 고정 source commit의 bare shallow Git cache. 원래 shallow 경계와 실제 commit/tree/blob을 보존. checker/contract hash·ref→commit·Git connectivity 대조 |
| `inputs/prepared-69e28c494a120c388afe18d901043d05333d7c7c` | 실제 수신한 render/contract의 정확한 바이트. **실제 RUN input_root가 아닌 준비 template**. 파일은 mode600. Runtime grant·receipt·새 baseline 없음 |
| `prepared-assets` | hash 검증한 Git bundle·wheel·원본 parser/checker·잠금 의존성. 승인값 loader나 credential 저장소가 아님 |
| `runtime-py312-yaml603` | private CPython3.12.15/PyYAML6.0.3 venv. 시스템 Python/PyYAML은 변경하지 않음 |

기존 렌더 SHA256 `3c8b6fe17d9a4705fd57158ec426294fe2a24413c83091aa3a568912b7263516`,
contract `65059dcbf6cfb6e6a6a2e65d92cfb95ea5efe3637265eeb79918c631faa6089c`,
checker `b889d6123fe0d566424a17ab88a6e41e49696bb1bb572b16ed88f22a654df2e1`를 대조했다.
준비한 private 환경에서 기존 고정 checker로 6객체 render 정적 검사 PASS를 확인했다.
해시·정적 PASS는 현재 클러스터 상태, render 생산 계보, 실행 허가나 인수 증거를 대신하지 않는다.

## 실제 발견한 환경 차이와 연결

Runner Git2.52.0은 `--no-lazy-fetch`를 지원한다. 시스템 PyYAML은6.0.1로 기존
`producer_bridge.PYYAML_VERSION`의6.0.3과 달랐다. 기존 검사 버전을 낮추지 않고
P2 private 환경에 CPython3.12 Linux wheel을 hash-pinned/offline 설치했다.
wheel SHA256은 `ba1cc08a7ccde2d2ec775841541641e4548226580ab850948cbfda66a1befcdc`다.
시스템 패키지·P1 workflow·계정/인증서/SSH trust·IAM/RBAC는 변경하지 않았다.

P2 self-hosted job의 실행 인터프리터만 위 private Python으로 선택하고,
`check_interpreter()`가 prefix·Python ABI·기존 exact PyYAML 버전과 module 위치를
확인한 뒤 기존 `delivery_job.main`을 호출한다. 잘못된 환경에서 system Python이나
6.0.1로 fallback하지 않는다. hosted declaration gate는 기존 Python을 그대로 쓴다.
수동 dispatch·SITE gate·exact SHA checkout·Runner identity·execute 요청·원본/control·
current RBAC·claim/fence/finish 검증은 유지한다. workflow dispatch는 수행하지 않았다.

`configuration_paths()`는 기존 `build_configuration`에 전달할 확인된 source/store와
파일 이름만 반환한다. 실제 RUN input_root·RUN/receipt·기간/예산은 호출자가 실제 값으로
제공한다. 준비 template를 실제 RUN으로 자동 복사하거나 native RUN을 만들지 않는다.
기존 `SOURCE_COMMIT`·render/contract 값을 재사용하며 새 approval/JSON/env loader를 추가하지 않는다.

## 실패·복구와 남은 범위

전달 시 플랫 payload의 파일 개수·크기·hash를 모두 검사했다. 기존 대상이 있으면 중단하고
덮어쓰지 않는다. 준비 중 실패한 새 경로는 검사 대상으로 보존하며 자동 재귀 삭제하지 않는다.
완료 상태는 모든 검사와 fsync 뒤에만 기록한다. 이 코드 변경을 되돌리면 P2 실행 선택만
원래대로 돌아가고 private 준비 파일은 남는다. 회수는 최종 소비·claim 상태를 확인한 뒤
이 P2 영역의 자기 파일만 처리하며 기존 자원 최종 정리 10/26 12:00을 유지한다.

실제 grant/원문 의미·인계 후 새 baseline·producer native 대응·finish scope의 운영 callback
연결과 본인 인증/현재 RBAC/actual READY는 남는다. 미래 native job의 RUN/nonce·발급 응답·
finish 원본은 실행 때 생성한다. 현재 `SITE=None`·self-hosted dispatch 차단·발급/앱 배포 금지를
유지한다. 준비 디렉터리·버전 보고나 이 문서를 실행 grant로 쓰지 않는다.
