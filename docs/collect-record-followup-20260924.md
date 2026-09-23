# P1-CICD-01 — 기존 collect 기록 보완

대상: Run35683010080 / Attempt1. app-evidence 수신 검증 완료, [증적 Draft PR22](https://github.com/321Team/fds-project-evidence/pull/22)의 collect 로그/시각 보완용이다.
책임: [CICD #11](https://github.com/321Team/infra-cicd/issues/11) / Lead 이권욱.

## 범위와 분류

`tools/precheck/collect_existing_evidence_record.py`는 기존 수집 폴더의 `collect.log`, `started-at.txt`, `finished-at.txt`만 새 묶음으로 복사한다. 기존 archive hash는 이미 수신된 회차를 선택하기 위한 식별값이다. 원 app_evidence 검증기·Scan/Delivery 게이트를 대체하거나 운영 입력으로 소비하지 않는다.

[중앙 정책 §3.2](https://github.com/321Team/fds-project-baseline/blob/49278c189dacb620e81d282c6401faa2a9c677a6/governance/REVIEW_AUTHORITY_POLICY.md)의 정보 수집 Pre-check, **REVIEW=NOT PERFORMED (Pre-check)**로 분류한다. Lead 자체 코드 확인: 네트워크/subprocess/패키지 설치/서비스/계정/권한/운영 파일 변경 없음, 1회 종료, 승인된 기존 계정으로 읽고 새 수집 경로에만 결과를 쓴다. 서버 Runtime 적용이나 AC 인수 판정이 아니다.

- 대상 홈 바로 아래 최대4096개 항목을 열거하고 이름이 맞는 수집 폴더 최대32개만 확인한다. 재귀 검색 없음.
- 회차 식별용 archive는 각 최대1MiB, collect.log 최대8KiB, 각 시각 파일 최대128bytes다. 원 Run의 OCI/Trivy report/DB/Source/앱 로그를 읽지 않는다.
- 경로 구성요소의 symlink·특수파일·읽는 중 변경을 거부한다. 원본을 삭제하거나 수정하지 않는다.
- collect.log의 기존3행 형식·허용된7개 결과 필드·대상 archive 경로/hash를 확인한 경우만 원문을 묶는다. 예상 밖 로그·JSON 값/중복키·잘못된 시각은 원문 노출 없이 STOP이다.
- 새 0700 폴더와 0600 archive/sidecar만 만든다. 기존 출력 덮어쓰기·자동 삭제 없음.

## 실행

환경: `cicd-runner01`, `server01_cicd-runner`, UID1000, Python3.11 이상. sudo 불필요.
아래 SHA256과 일치하는 스크립트를 `/home/server01_cicd-runner/collect_existing_evidence_record.py`로 전송한 뒤 해당 계정의 Bash에서 실행한다.

```bash
(
  set -euo pipefail
  cd /home/server01_cicd-runner
  printf '%s\n' '87868f5baab72288ccfc4de3f1a42341e3d29f219b230d8b29f754897813a265  collect_existing_evidence_record.py' | sha256sum --strict -c -
  /usr/bin/python3 -I -B ./collect_existing_evidence_record.py
)
```

고정 원 archive SHA256: `11578ae33211f569df91830a2b26314c01ffcaea70a67dc7165f9e3dad4b1c79`.
자동 선택은 `CICD_Evidence35683010080_1.XXXXXX/export/app-evidence.tar.gz` 중 일치 회차가 정확히1개인 경우만 진행한다. 다른 Run의 archive를 선택하거나 새 collect로 대체하지 않는다.

| 결과 | 판정과 조치 |
|---|---|
| `COLLECT_RECORD=COLLECTED`, exit0 | 기존3파일을 묶음. 출력 ARCHIVE/SIDECAR와 현재 종료 화면을 회신 |
| `COLLECTED_WITH_MISSING`, exit0 | 없는 파일을 생성하지 않고 누락 목록을 남긴 묶음. 동일하게 회신하되 누락 해소로 보지 않음 |
| `STOP`, exit1 | 출력 REASON과 화면을 회신. 원본 수정·삭제·앱/collect 재실행 금지. 서비스 변경이 없어 서비스 복구 불필요 |

`MATCH_NOT_UNIQUE`는 일치 회차0개 또는 복수다. 기존 폴더가 여러 개라면 원본 위치를 확인하고 같은 계정 홈의 정확한 회차를 `--case-dir /home/server01_cicd-runner/CICD_Evidence35683010080_1.실제6자리`로 지정할 수 있다. 예시 경로를 그대로 실행하지 않는다. hash 불일치를 기대값 변경으로 우회하지 않는다.
쓰기 도중 오류가 나면 새 `CICD_CollectRecord35683010080_1.*` 폴더에 부분 출력이 남을 수 있다. 보존 후 조사하고 자동 청소하지 않는다.

## 반환물·한계

출력 `collect-record.tar.gz`와 `.sha256`에는 원문3파일(있는 것만), 새 `collection-record.json`, 내부 SHA256SUMS가 들어 있다. 새 기록에는 실행 도구 hash·계정·호스트·시각과 원 archive 식별값을 남긴다.

- 이 도구의 exit0은 새 보완 묶음 생성 성공이다. **과거 collect exit code는 NOT_RECORDED**, 과거 종료 화면은 NOT_INCLUDED로 유지한다.
- 원래 남아 있는 종료 화면이 있으면 별도로 회신한다. 현재 화면을 과거 실행 화면으로 등록하지 않는다.
- 이번 보완 시각(`recorded_at`)과 원본 started/finished-at, 09-22 앱 Run 시각, 09-24 수신 verify 시각을 구분한다.
- 정보 수집 성공·hash 일치는 원 실행/승인 진위의 독립 증명이나 앱 취약점 해소가 아니다. 같은 UID의 악성 변경까지 방지하는 attestation 기능은 없다.
- 수신 후 원문/해시·기존 receipt·기존 수신 회차와 대조하고 PR22에 새 보완 기록으로 편입한다. 독립 증적 리뷰·원 archive 검토자 접근은 별도로 남는다.
- 앱 BLOCK, fds-engine NOT_SCANNED, Runtime AC1/10, Delivery NOT_VERIFIED 유지.

## 로컬 검증

`python3 -m unittest discover -s tools/delivery -p 'test_collect_existing_evidence_record.py' -v`: 합성 자료 기반13 PASS, skip0.
정상/누락/불일치/복수/기존 출력 보존, symlink 파일·부모 디렉터리, FIFO, 크기 상한, 시각 역전, JSON 중복키, 예상 밖 로그 비노출, 계정 차단을 확인했다. 실제 Runner 실행은 NOT RUN이다. GitHub Source validation 결과는 PR의 해당 HEAD를 확인한다.
