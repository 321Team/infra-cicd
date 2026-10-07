# adc90410 공급물의 별도 파일 배치

CODEX_ASSISTED · 2026-10-07. 기존 APP 공급물·서비스·이미지·활성 config를 변경하지 않는다. 이 도구는 새 공급 디렉터리에 파일만 배치한다.

대상: cicd-runner01 / server01_cicd-runner / UID1000 정상 로그인. sudo 불필요·사용 금지. 기존 ansible01 SSH 키 경유 접속은 확인됐으며 키·권한 변경은 없다.

입력: artifact-11466476253.zip, 109786891 bytes, SHA256 `2eaed6486b68f9260f735e362b45a167d7f01aa0fa819accf751ea8d74951ad6`. exact APP/supply tool `adc90410f99cb7af5b40bc8ff92c45272e9f8bee`. SUPPLY_MANIFEST SHA256 `f4d55db6ecbefe2057cdb7ca6539b6818549744eb8bfc127ba12dcfa4eb6143b`.

Python3.11+ 표준 라이브러리만 필요하다. 받은 새 private 작업 폴더에서 다음 순서로 실행한다.

```bash
python3 stage_supply.py artifact-11466476253.zip
python3 stage_supply.py artifact-11466476253.zip --stage
```

첫 명령은 파일 검사만 한다. 두 번째는 계정/UID/host/home, 링크·소유자·권한·공간을 확인한 뒤 아래 고정 새 디렉터리를 만든다.

`/home/server01_cicd-runner/FDS_CICD_Runtime/FDS_CICD_ApkoSupply.adc90410f99cb.f4d55db6ecbe`

정상 판정은 rc0, `STAGED_FILES_VERIFIED_NOT_RUNTIME`, 파일126개 재해시·uid1000·파일0600/디렉터리0700, `STAGED_FILES_RECEIPT.json` 생성이다. 기존 destination이 있으면 덮어쓰지 않고 중단한다. 부모 경로 권한이 다르면 chmod로 우회하지 않는다. 실패 시 입력과 이번 부분 디렉터리를 보존하고 조사한다. 성공 receipt가 없으면 소비 금지다. 기존 서비스·파일 원복 대상은 없다. 재실행을 위해 부분 디렉터리를 자동 삭제하지 않는다.

이 결과는 Runner 파일 배치까지만 의미한다. APP exact source/test 인계 확인·consumer config 승인·lock age와 Trivy 신선도·실행 창 조건은 별도다. 이미지 load, Test/Build/Scan, Push, Deploy, 계정/방화벽/서비스 변경은 실행하지 않는다. 기존 Ansible 일회성 Runner 공급은 별도의 sudo 실행 경로이며 이 배치 도구에 포함하지 않는다.

## 실제 수행 기록

2026-10-07 18:09:21 KST, 위 새 공급 디렉터리 생성·파일126개 배치와 권한/해시 검증을 완료했다. 원본 서버 receipt는 STAGED_FILES_RECEIPT.json에 보존한다. SHA256 `072eb533486fc46d3e66233611f14c886145c01e0000c45b83c2ac6b24c8a64e`는 서버→ansible01→작업 PC에서 일치했다.

ansible01에서 Linux MOCK6건 PASS/rc0(정상 배치·기존 경로 STOP, 부모/대상 symlink STOP, 부분 실패 receipt 미생성, 손상 입력·잘못된 이름 STOP), 실제 ZIP 읽기 검사 rc0를 확인한 뒤 정상 SSH 로그인 계정으로 실행했다. 새 키나 sudo 권한 변경은 없다. 실제 실행한 stage_supply.py의 hash는 VALIDATION.json에 결속했다.

Ansible ephemeral Runner는 정상 계정 접속까지 해소됐지만 sudo가 암호 입력을 요구해 token 발급/serve는 아직 미실행이다. 이 기록은 구축·파일 배치 기록이며 PR 리뷰를 수행하지 않는다. APP 실행 config와 승인·상태/AC는 유지한다.
