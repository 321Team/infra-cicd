# #46 고정 9객체 소비 프로파일 — 오프라인 구현

기준 소스: infra-cicd PR99 병합 `4827ff3c1d0ec2dca5fa894b96c7c2cbb0f4c2fd`.
작성: 이권욱 / CODEX_ASSISTED, 2026-10-20 KST. 이 변경은 실제 배포·자격 발급·승인·인수가 아니다.

## 변경과 호환성

기존 `(transaction-api, fds-engine)` 이미지 두 개의 Binding은 기존 6객체를 그대로 사용한다.
세 번째에 `end-page`를 명시한 Binding만 고정 9객체를 사용한다. 다른 이름·순서·중복·네 번째 이미지는 거부한다.
Binding 필드·직렬화·key 계산을 바꾸지 않는다. 문서 수·ACK 수·저장된 PASS로 프로파일을 추론하지 않는다.

9객체 순서는 기존 Service2·Deployment2·Route2 뒤에 end-page Service·Deployment·Route를 붙인다.
namespace는 `fds-app`이며 `rosa-app`은 overlay 이름이다. SA·NetworkPolicy·Secret·RBAC 쓰기는 추가하지 않는다.
`path_for(key)`와 Binding 없이 호출하는 receipt/replay API는 기존 6객체만 허용한다.
실제 session·journal·recovery·object reader·baseline·sender·finish 경로에는 동일 Binding을 전달한다.

end-page는 container 1개, `end-page-sa`, automount=false, onprem-egress 없음, DB 환경변수·envFrom 없음을 강제한다.
volume은 emptyDir/Downward API만 허용한다. Route는 승인된 host의 `/end`, end-page backend·http port·edge/Redirect로 고정한다.
기존 producer 검사기 SHA256 `b889d6123fe0d566424a17ab88a6e41e49696bb1bb572b16ed88f22a654df2e1`은 변경하지 않는다.
fds-msa PR41 HEAD `06b10919c44740eac9093f6db304c71f53cbebff`의 검사기 원본과 해시 일치를 확인했다.
이는 producer 전체 승인·clean render·실제 이미지 digest 인수를 뜻하지 않는다.

## 마감과 실패·복구

TokenRequest 요구값 900초와 실제 자격/실행 승인 만료 검증은 유지한다. 첫 쓰기에는 최소 660초가 남아야 한다.
Deployment별 최대 300초, 세 Deployment 전체 rollout은 공유 600초 안에 제한한다. 갱신·자동 재시도는 없다.
9객체별 durable intent→ACK 후에만 다음으로 진행한다. 불명확한 쓰기 결과는 정확한 pending과 fence를 보존한다.
복구 조회는 읽기 전용이다. 자동 resume/unlock/rollback은 수행하지 않으며 별도 새 RUN·승인된 restore 입력이 필요하다.
완료 receipt/원본 인증은 9개 응답의 순서·identity·UID·namespace·삭제 상태를 대조한다. rollout 관측을 배포 AC PASS로 바꾸지 않는다.

권한 관측은 기존 동일 주체·cluster·namespace 전후 재조회와 9객체의 named get/patch 18개를 사용한다(총 24요청).
SSAR HTTP 201·대상 echo·allowed 의미를 검사하며 HTTP 200·다른 namespace·권한 확대·삭제·토큰 발급은 거부한다.
이는 현재 주체의 허용 관측이며 issuer/최소 권한 전체 검증이나 실행 GO의 대체물이 아니다.

## 검증과 실제 사용 전 잔여

`python -m unittest discover -s tools/phase2 -p 'test_*.py' -v`로 합성 오프라인 시험을 수행한다.
새 시험은 9 PATCH/3 GET, legacy 호환, 세 번째 이미지 누락/오배치, 중간 거부/불명확 결과,
receipt·완료·재시작 조회, 600초 상한, named SSAR, TLS sender, fresh 404 baseline을 포함한다.
Windows에서는 POSIX 권한·fsync·no-follow 시험이 제외되며 Linux CI 결과와 구분한다.

실제 issuer·producer·finish의 원본 의미 인증 및 site 채택 계약, 실제 end-page 승인 digest/clean render는 아직 연결되지 않았다.
기존 앱 두 digest를 세 번째 실제 값으로 복사하거나 placeholder를 승인값으로 사용하지 않는다.
CLI 기본 미설정 STOP은 유지한다. 이 문서·MOCK·CI 성공으로 Runtime GO, G4/G5/G6 인수 또는 티켓 종결을 부여하지 않는다.

기존 상세 문서는 [delivery-adapter.md](delivery-adapter.md)와 운영 인증 관련 문서를 함께 읽되,
그 안의 6객체·2앱 설명은 legacy 프로파일의 계약으로 해석한다.
