# 착수 문서 원문과 출처

이 디렉터리는 README에서 인용하는 착수 문서의 배포 당시 원문을 보존한다. 현재 운영 상태나 최신 Notion 티켓의 원격 사본이 아니다.

## 보존한 파일

- [P1-CICD-01 Kickoff User Story v1.0](P1-CICD-01_Notion_UserStory_v1.0.md)
- 원래 파일명: `P1-CICD-01_Notion_UserStory_v1.0.md`
- 문서 자체 작성일: 2026-09-08. 저장소 편입일: 2026-09-09.
- 출처: 기존 배포 `P1-CICD-01_Kickoff_Kit_v1.0.zip` 내부의 동명 파일.
- 원문 SHA-256: `5a8faa5bec823e0cbde73b9029777f4a6ea03513b78400f0035d68a4a8d617b2`
- 배포 ZIP SHA-256: `5e701cdfb92237bef61e1b999e004292856c5ab2dd3f9f8f458981790f3e9726`
- 대조 결과: ZIP 외부 sidecar, 내부 SHA256SUMS의 해당 항목, 작업 공간 원문 및 ZIP 내부 원문 바이트 일치.

문서 내용과 LF 바이트를 수정하지 않고 편입했다. 해시는 해당 파일의 동일성을 확인하며 작성자 서명이나 Notion 원격 갱신을 입증하지 않는다.

## 현재 문서와의 관계

- 원문의 일정·체크박스·진행 상태는 작성 시점 기록이다. 현재 Runtime 완료 여부는 후속 실행 증적으로 판정한다.
- 원문의 `infra-ansible #5` CI 연계는 별도 추적 항목이며 Runner의 저장소 등록 범위 확대를 뜻하지 않는다. 후속 채택값은 `321Team/infra-cicd` 전용 Runner·중앙 실행이다.
- 초기 베이스라인과 이후 구현 선택을 구분하며, 역할의 출처와 현재 책임 경계는 [역할 결정 기록](../decisions/p1-cicd-01-roles.md)에 연결한다.
- 이번 편입은 기존 Notion 티켓, WBS 또는 베이스라인 정본을 갱신하지 않는다.
