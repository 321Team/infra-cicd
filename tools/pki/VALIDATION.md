# CSR 도구 및 베이스라인 적용 키트 검증

## 2026-09-10 리뷰 보완 검증

현재 CSR 단위시험 **11건 PASS**. 기존 8건에 rc·stderr 고정 오류 유형/원문 제외, timeout 부분 출력 제외, 실패한 키·요청 디렉터리 보존 및 경로 보고 시험 3건을 추가했다. 개인키 자동 삭제·재생성 기능은 추가하지 않았다. 자동 실행은 GitHub 제공 Runner의 `source-validation.yml`로 준비했으며 실제 Run 결과는 PR에 별도 기록한다. 아래는 v1.0 작성 당시 기록이다.

## v1.0 당시 기록

검증일: 2026-09-09. 대상 VM에서 실행한 결과가 아닌 로컬 코드 검증이다.

- CSR 도구 안전성 시험 **8건 PASS**: 계획 실행 무변경, 호스트·계정·IP 불일치 차단, 출력 경로 이탈 차단, 개인키 디렉터리 symlink 거부, 공개 Archive allowlist·내부/외부 해시, 비밀키 미열기 및 사용자 권한으로 내린 뒤 결과 생성.
- Markdown Bash 9개 블록과 smoke 템플릿 Bash 1개: `bash -n` PASS.
- Python AST, 설정 JSON, smoke YAML 구문: PASS. smoke는 수동 trigger만 포함하는 템플릿이며 활성 Workflow가 아니다.
- 모의 키 문자열을 이용한 파일·권한 전환 경계 시험이다. 이 작업 환경에서 프로젝트 CA/Harbor 개인키·인증서를 생성하지 않았다.
- `create_harbor_csr.py --apply`는 harbor01/10.1.93.54/server08의 sudo 실행만 허용한다. 새 키는 root 전용 디렉터리에 생성한다. 공유 결과를 작성하기 전에 그룹·GID·UID를 server08로 내린다.

미검증: 대상 OS의 실제 OpenSSL CSR 생성, Runner 등록·서비스·rootless build, 오프라인 암호화 CA 환경과 서명, Harbor 정식 설치·전환·복구, TLS/Push/Pull/배포. 실행 결과로 별도 판정하며 DELIVERY_GATE=NOT_VERIFIED를 유지한다.

기존 parallel-prep 수집기 소스는 변경하지 않았다. 기존 15건 시험 결과와 수신한 Harbor/Runner 증적은 해당 수집 Commit에 연결한다. 이번 검증은 변경된 CSR 도구와 새 실행 절차를 대상으로 했다.
