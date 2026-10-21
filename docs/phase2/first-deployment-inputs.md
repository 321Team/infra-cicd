# #46 최초 배포 입력 연결 — previous 없음과 실제 현장 준비

2026-10-21 18:20 KST 이후 GitHub 원본을 재조회했다. 이는 클러스터·Registry·Runner의 현재 Runtime 조회가 아니다. 실제값·자격·종료 권위·새 GO를 생성하지 않는다.

## 원본 계약과 수정 이유

[#43 current/previous 채택](https://github.com/321Team/fds-project-baseline/issues/43#issuecomment-6185877075)과 [권욱 수용](https://github.com/321Team/fds-project-baseline/issues/43#issuecomment-6186106346)은 최초 P2 배포의 current 두 앱을 채택하고 **previous는 명시적으로 없음**으로 남겼다. 현재 fds-msa main `20ae1611102e0eed6138145039bcc863b64b6646`의 [`DELIVERY-CONTRACT-rosa.md` §9](https://github.com/321Team/fds-msa/blob/20ae1611102e0eed6138145039bcc863b64b6646/deploy/DELIVERY-CONTRACT-rosa.md)도 같은 의미다. current source/Push 원본은 재사용하며 P1 rollback 관측값이나 current를 previous로 복사하지 않는다.

기존 `CodeAdoptedEvidenceResolver.producer_checked`는 previous 전체 tuple을 항상 요구해 이 수용된 입력을 소비하지 못했다. 이번 수정은 이권욱 담당 소비 코드의 불일치를 해소한다. 생산자에게 새 previous·재빌드·재Push·기존 원본 재제출을 요구하지 않는다.

- `ProducerFacts.previous_images`는 기본값 없는 필수 필드다. 원본에서 부재를 명시적으로 채택한 `None`만 지원하고, 빈 tuple/list·문자열·일부 앱만 있는 profile은 계속 거부한다.
- standalone resolver는 `first_deployment=False`가 기본이다. 코드에서 명시한 `first_deployment=True`이고 action이 deploy일 때만 None을 허용한다. 이 선택은 승인·404 관측을 대신하지 않는다.
- 공식 `RuntimeAuthorityFactory`는 None을 **deploy + plan.baseline is None**일 때만 받는다. 이 경로에서만 first_deployment를 전달하며 기존 `FirstDeploymentBaseline`을 사용한다.
- `FirstDeploymentBaseline`은 인증된 같은 자격/CA/endpoint로 정확한 6/9개 객체를 실제 GET하여 **모두 분류된 NotFound**인지 확인한다. namespace 존재·권한·API 오류를 404로 바꾸지 않는다. 하나라도 존재/거부/불명확이면 첫 claim·sender 전에 STOP한다. 이후 claim·single writer·ObjectProvider 재대조도 유지한다.
- 기존 대상의 명시적 baseline을 쓰는 재배포·restore는 전체 previous가 계속 필수다. 정상적인 기존 full previous profile은 그대로 지원한다. 최초 배포에서 명시적으로 승인된 previous와 current가 우연히 같은 digest라는 이유만으로 새 거부 규칙을 만들지 않는다.
- source/current digest·checker·clean render·Test/Build/Scan/Push·P2 채택·원본/control·시간/자격 검사에는 None 예외를 적용하지 않는다. source facts를 채택하는 코드의 신뢰·독립 리뷰 책임은 그대로다.

부분 적용·timeout·응답 불명확·rollout 실패는 기존 pending/ACK/fence를 보존하고 사람이 실제 상태를 대조한다. None을 current로 대체하거나 자동 restore·재적용·삭제·unlock하지 않는다. 최초 배포의 검증·인수 뒤에만 승인된 원본 계보를 다음 previous 후보로 연결한다.

## 최신 입력 상태와 책임

| 입력 | 확인된 최신 근거 | 현재 소비 판단 |
|---|---|---|
| 최초 current/previous | #43 채택·수용, 위 고정 main 계약. current 두 앱, previous 없음 | 명시적 부재 계약의 소비 코드를 이번에 연결. 실제 Registry·render·계보 인증은 계속 필요 |
| Route host | [Handoff2 수용](https://github.com/321Team/infra-rosa/issues/4#issuecomment-6285752690)과 고정 `deploy/delivery/rosa.yaml`의 approved host | 이미 수용한 host를 다시 PENDING으로 돌리거나 재확정하지 않음. 실제 DNS/TLS/Admitted 관측과 구분 |
| 플랫폼 | [#38 9칸 부분 인계](https://github.com/321Team/fds-project-baseline/issues/38#issuecomment-6288502558) | BASIC_READY=NOT_READY, 6·7·8·9 잔여. 개인 D-11d가 배포 SA의 issuer/expiry·전체 플랫폼 Ready를 대신하지 않음 |
| PG03 | [admission 원본 수용](https://github.com/321Team/infra-rosa/issues/6#issuecomment-6288618598). 수정 PR51은 실제 main `70e5dc8cc52e44919c2390feb71d1631111a6e1b`로 병합됨 | 과거 댓글의 PR51 미병합 표기는 당시 이력. 6-2 실제 Pod 관측은 미실행, PG03 HOLD 유지 |
| Registry | [D22 소비 기록](https://github.com/321Team/fds-project-baseline/issues/42#issuecomment-6293161531), [C1~C3 v1 보완 회신](https://github.com/321Team/infra-rosa/issues/12#issuecomment-6293556321) | Harbor Primary/ECR 수동 A와 D7 수용은 재사용. v1 회신은 있음. 실제 격리 캡처/적용·TLS/인증·cold-pull 수용·D22 FINAL은 미완료 |
| end-page/clean render | [MSA PR41](https://github.com/321Team/fds-msa/pull/41), HEAD `06b10919c44740eac9093f6db304c71f53cbebff` Draft | end-page zero digest와 실제 clean render 잔여. 9객체 입력을 현재 승인값으로 합성하지 않음 |
| DB Secret | [#47 v2 검토](https://github.com/321Team/fds-project-baseline/issues/47#issuecomment-6287016696) | 기존 역할/키/endpoint·위험 수용은 재사용. 생성 절차 B1~B3 보완 전 Runtime HOLD. 비밀번호는 당사자 1:1 전달, 코드 검토자가 수집하지 않음 |
| 실행/종료 범위·issuer·현장 경로 | #46 최신 댓글 6294821045 이후 새 회신 없음, PR105/106 및 scope 리뷰 | 실제 실행 GO·readback-only 종료 의미·새 SA issuer/수명·CA/endpoint·원본/control·고정 input/Git/store 경로는 아직 운영 채택되지 않음. 코드 병합이나 이 문서를 GO로 읽지 않음 |

위 표는 원본 조회 결과의 구분이지 새 팀 공용 정책·작업창·사양 변경이 아니다. 댓글에 남은 옛 $450·매창 권한 회수·Phase1 일괄 금지는 최신 사용자 결정으로 자동 재부활시키지 않는다. 비용 재승인·원본 재제출·전량 재시험을 추가하지 않는다.

## 권욱이 지금 연결하는 것과 실제 실행 전에 남는 것

권욱 담당: 수용된 previous 없음 계약의 소비 코드·실패/복구 음성 시험, 종료 scope 의미 채택 준비, 현장 원본과 source/credential/input 경로의 코드 결속·독립 리뷰·실제 실행 인수. 하영 담당: 기존 플랫폼/issuer·Registry/Secret·보안 준비의 필요한 delta와 원본. 재환 담당: 실제 앱 이미지·계보·Registry/host를 반영한 clean render. 준비 자료가 있다는 이유로 실제 발급·기동·최종 Ready를 만들지 않는다.

종료 scope는 [post-close 정책](post-close-observation.md)대로 **남은 독점 업무가 객체·rollout readback뿐인 실제 범위**를 먼저 확인해야 한다. Health/Rule을 별도로 수행할지 같은 RUN의 독점 업무로 남길지도 실제 실행안에서 명시한다. 필요한 경우 기존 실행 GO에 범위를 한 줄로 연결할 수 있으며 새 양식·별도 중복 GO를 요구하지 않는다. 이번에는 그 실제 범위를 확정·발행하지 않았다.

Runtime 때는 실제 자격의 남은 수명과 승인창 안에 preflight/쓰기/rollout/close/후속 GET·scope 인증·보존·정리/보고 예산을 확보해야 한다. first-write 660/690초 조건은 900초 자격 내 최종 완료 보장이 아니다. 이 문서는 미래 시작 시각·만료 시각·자격 지문을 채우지 않는다.

## 필요한 검증

```text
python -m unittest discover -s tools/phase2 -p test_delivery_first_deploy_inputs.py -v
python -m unittest discover -s tools/phase2 -p test_delivery_finish_observation.py -v
python -m unittest discover -s tools/phase2 -p test_*.py -v
```

모든 fixture는 SYNTHETIC이며 실제 연결·서버 변경은 없다. 최초 None/기존 객체·explicit baseline/restore 거부, 기존 source/current/clean 계보 거부, partial write fence 보존, 6/9 객체 경로를 검사한다. PR106 비차단 권고의 rollout 3종은 고정 오류를 직접 검사하여 다른 reconciler 방어가 시험을 가리지 않게 했다. close 중복은 실제 live provider에서 확인한다. STATIC/MOCK/CI를 Runtime/AC로 승격하지 않는다.
