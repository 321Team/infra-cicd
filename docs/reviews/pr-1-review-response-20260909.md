# PR #1 리뷰 요청 검토 및 수용 결과

검토일: 2026-09-09. 대상: `321Team/infra-cicd` [PR #1](https://github.com/321Team/infra-cicd/pull/1), 최초 검토 HEAD `7cdc9ca0a8af7eb4bc8696cc795e93ec451aea35`.

**판정: 4개 요청 모두 기존 요구사항에 부합하여 수용.** 사용자 지시는 기존 요구사항 범위 안의 보완 수용이며, 이 기록은 이하영님의 독립 기술 승인 또는 최종 병합 승인을 대신하지 않는다.

## 요청별 판단과 반영

[이하영님의 리뷰 의견](https://github.com/321Team/infra-cicd/pull/1#issuecomment-5598879029)은 일반 PR 댓글이며 정식 APPROVED 또는 REQUEST_CHANGES 제출과 구분한다.

| 요청 | 요구사항 대조 | 반영 |
|---|---|---|
| 필수: `*.pem` 제외 | 기존 개인키·자격증명 Git 반입 금지와 일치 | `.gitignore`에 한 줄 추가. 공개 CSR·인증서 발급·전달은 기존 절차 유지 |
| 권장: `.gitattributes` | Linux 셸·Workflow 소스의 줄바꿈 일관성 보완 | `*.sh`, `*.yml`, `*.yaml`에 `text eol=lf` 지정. 전체 파일 재정규화 없음 |
| 권장: Kickoff 근거 링크 | 기존 문서 인용의 추적성 보완 | 배포 원문을 바이트 그대로 보존하고 README에 전체 Commit SHA로 연결 |
| 권장: 역할 결정 출처 | 기존 Lead/Support·CA/Harbor 책임 경계의 기록 보완 | 결정 기록 작성, README 담당·근거 및 운영 기준 역할표에서 연결. 새로운 담당·권한 부여 없음 |

README의 초기 Workflow 선택지 설명도 [이미 채택된 구현 설정](https://github.com/321Team/infra-cicd/blob/790105e390c529ee2fd50b23e709efe160a059c6/config/p1-cicd-01.json)에 맞춰 정리했다. `infra-cicd` 전용 Runner·중앙 수동 실행을 유지하며 `infra-ansible`에 Runner 사용 범위를 추가하지 않는다. 앱 Source의 정본과 읽기 권한 인계는 각 앱 저장소에 유지한다.

## 원문과 결정 기록

원문·출처·역할 기록을 먼저 Commit `081b3dd947bbf04fbafe00f15d7cfdba25f3c0d1`에 보존해 링크 대상을 고정했다.

- [Kickoff User Story v1.0 원문](https://github.com/321Team/infra-cicd/blob/081b3dd947bbf04fbafe00f15d7cfdba25f3c0d1/docs/references/P1-CICD-01_Notion_UserStory_v1.0.md)
- [출처와 동일성 검증 범위](https://github.com/321Team/infra-cicd/blob/081b3dd947bbf04fbafe00f15d7cfdba25f3c0d1/docs/references/README.md)
- [역할 결정 기록](https://github.com/321Team/infra-cicd/blob/081b3dd947bbf04fbafe00f15d7cfdba25f3c0d1/docs/decisions/p1-cicd-01-roles.md)

Kickoff SHA-256: `5a8faa5bec823e0cbde73b9029777f4a6ea03513b78400f0035d68a4a8d617b2`. ZIP의 외부 sidecar·내부 파일 해시·원문 바이트 및 저장소의 원문 일치를 확인했다. 원문의 일정·미검증 체크박스는 당시 기록이며 현재 상태로 승격하지 않는다. Notion 원격 갱신이나 작성자 서명 검증을 주장하지 않는다.

## 검증 결과

- Git 2.51.1의 `check-ignore`: 루트·중첩 경로의 PEM 제외 및 설정·도구·문서·Workflow 경로의 비제외 확인 PASS.
- `core.autocrlf=true`에서 CRLF 셸·YML·YAML 입력의 Git index 저장 및 checkout 결과가 LF임을 확인 PASS. 임시 저장소에서 시험했으며 실제 Windows 호스트 시험은 아니다.
- UTF-8/LF, JSON 구문, 저장소 내부 상대 링크 확인 PASS.
- 원문 및 역할 기록의 원격 파일과 로컬 파일 바이트 일치 PASS.
- 변경 파일의 개인키 PEM 헤더 미포함 확인. 이는 전체 자격증명 탐지나 과거 Git 이력의 비밀값 전수 검사를 대체하지 않는다.
- 기존 파이프라인 계약·관측 기록·배포 기록 예제·PR 템플릿 4개 파일은 변경 없음.

`.gitignore`는 이미 추적 중인 파일이나 강제 추가를 막지 않는다. 변경 내용 검토 및 기존 자격증명 취급 원칙을 유지한다. [Git gitignore](https://git-scm.com/docs/gitignore)

LF 규칙은 Git의 정규화·체크아웃 동작에 적용한다. Runner 운영 설정, OS 줄바꿈 설정 또는 실행 권한을 변경하는 조치가 아니다. [Git gitattributes](https://git-scm.com/docs/gitattributes)

## 재검토 범위와 상태

새 파일 5개(줄바꿈 설정, 원문, 출처, 역할 기록, 이 검토 결과)와 기존 파일 3개(제외 규칙, README, 운영 기준)의 변경을 재검토한다. 원문의 보존 적정성, 현재 결정과 역사 기록의 구분, README·역할표 연결을 확인한다.

기술 리뷰 후 이권욱이 최종 병합을 판단하는 기존 절차를 유지한다. PR #2의 Runner/CSR/복구 코드 및 Workflow는 별도 검토 대상이다. 이 변경에서 main 병합, 서버 변경, 인증서 발급, Actions Job 및 배포는 실행하지 않았다.

`REVIEW_REQUESTS=ACCEPTED_4_OF_4`  
`IMPLEMENTATION=SOURCE_UPDATED`  
`INDEPENDENT_RE_REVIEW=PENDING`  
`DELIVERY_GATE=NOT_VERIFIED`
