# 병행 준비 도구 검증 기록 v1.0

검증일: 2026-09-09. 로컬 검증이며 대상 VM 실행 결과는 아직 수신하지 않았다.

## 수행 결과

- `python3 -m unittest discover -s tools/parallel-prep -p 'test_*.py' -v`: 15건 PASS.
- Runbook의 Bash 실행 블록: `bash -n` PASS.
- 인계 양식 JSON 구문, UTF-8 파일: PASS.
- 실제 로컬 환경에서 CLI 실행: 대상 호스트/IP/계정 불일치 코드 3, 수집·결과 디렉터리 생성 차단 확인.

## 검증 범위

| 위험 | 검증 |
|---|---|
| 대상 외 서버 실행 | 호스트·IP·계정 검사 및 수집/저장 호출 전 차단 |
| 비밀정보 포함 | YAML 허용 필드 추출, NGINX 인증서 경로 추출, 개인키 경로 참조 후 파일 미열기, Runner 인증 파일 미열기 |
| 명령 출력 노출 | stderr/타임아웃 부분 출력 제외, 환경 Token 미상속, 알려진 인증 패턴 제거 |
| 임의 로컬 대상 연결 | GitHub 이름이 사설 주소로 해석될 때 연결 차단 |
| TLS 오류 오탐 | 인증서 검증 실패를 성공으로 기록하지 않음 |
| 수집기 정지 | 설정 경로가 FIFO 등 일반 파일이 아닐 때 비차단 열기 후 거부 |
| 과도한 컨테이너 정보 | 로컬 Docker 소켓과 선택 필드 inspect만 호출 |
| 증적 무결성 | 결과 Archive 구성원 제한, 내부/외부 SHA-256 일치, 파일 0600·디렉터리 0700 |

## 검증 한계와 판정

단위시험은 해당 경계 동작을 로컬 Mock/임시 파일로 검증했다. 실제 Docker 버전의 템플릿 실행, 서버 서비스 상태, GitHub 통신·등록·Job 수신, CA 존재·승인·발급, Harbor 적용·TLS·Push/Pull·Kubernetes 배포를 검증한 것은 아니다. 수집 결과의 개별 실패와 설정 파서 한계는 소유자 확인 대상이다.

`PREPARATION_STATUS=REVIEW_REQUIRED`, `DELIVERY_GATE=NOT_VERIFIED`를 유지한다. 결과 코드 2를 TLS 또는 배포 실패로 일괄 해석하지 않는다.

배포 ZIP은 등록한 소스 Commit을 SOURCE.json에 고정하고, 별도 SHA256SUMS 및 ZIP sidecar로 검증한다. 이 파일에는 실행 토큰·개인키·원본 서버 설정이 없다.
