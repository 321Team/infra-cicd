# Deploy TLS transport와 POSIX checkpoint

2026-10-06 / AI usage: CODEX_ASSISTED.
기준 main756f19f4137aac2f9fc7799ad18e382eb2c8bda6(PR56 승인 병합).
운영 CLI/Workflow나 token 발급·회수는 없는 연결용 라이브러리 변경이다.

## Kubernetes client

deploy_transport.Client(targets, ca_raw, expected_ca_sha256, token_provider,
remaining_seconds=..., credential_remaining_seconds=..., request_timeout=10)

- endpoint는 https://10.1.93.109:6443의 fds-app 두 지정 Deployment만 허용한다.
  숫자 IPv4 TCP에 직접 연결하며 DNS, 환경 proxy, redirect, HTTP retry를 사용하지 않는다.
- 공개 CA bytes의 외부 확정 SHA-256을 먼저 대조한다. PROTOCOL_TLS_CLIENT,
  CERT_REQUIRED, hostname/IP 검증 및 TLS1.2 이상으로 **해당 CA bundle만** 신뢰한다.
  SSLKEYLOGFILE 환경으로 key log 파일을 만들지 않는다. CA 발급/전달·실제 IP SAN 검증은 잔여다.
- token은 검증된 호출자가 메모리 callback으로 제공한다. header injection은 차단하며
  예외/HTTP body/token을 error_code나 파일에 출력하지 않는다. Python bytes 메모리의
  물리적 zeroization은 보장하지 않는다. 실제 credential metadata 검증·주입·만료·회수는 미연결이다.
- 매 호출의 monotonic deadline은 min(10초, 현재 승인 잔여시간, 현재 실제 credential 잔여시간).
  TCP connect, TLS handshake, sendall, HTTP header/body 각 socket read의 timeout을 남은
  budget으로 줄인다. 수신/JSON decode 뒤에도 기한을 확인한다.
  응답 body 1MiB 상한, JSON 중복/NaN·중복 framing·압축/불완전 body를 거부한다.
- GET/PATCH만 지원한다. PATCH는 승인 targets에 맞는 UID/RV/container명/이전 image
  4개 test + 지정 image replace 1개 또는 정확한 역방향 복구만 보낼 수 있다.
  rollback 허용 여부는 Execution/Adapter authorize가 별도로 결정한다.
- 200 Deployment 이후 완전한 UID/spec/generation 검증은 기존 Adapter가 수행한다.
  409 Conflict/422 Invalid이며 Status Failure/code/지정 object details까지 일치한 경우만
  Rejected로 반환한다. 그 외 4xx, 인증 상실, redirect, 5xx, timeout, invalid response는
  해당 client를 영구 중단시킨다. 실제 서버가 details 없는 rejection을 보내면
  보수적으로 outcome unknown으로 처리하므로 자동 복구를 기대하지 않는다.
- PATCH 불명 상태에서 두 번째 쓰기·자동 rollback·retry를 하지 않는다.
  GET 실패도 client를 중단시키므로 이미 적용된 상태에서 재조회 실패 시 수동 대조가 필요하다.

시간 provider는 검증된 metadata에서 **로컬에서 즉시** 읽는 callback이어야 한다.
callback 자체가 block하거나 OS scheduler가 멈추면 이 라이브러리가 실시간 deadline을
강제 보장하지 못한다. fsync도 OS I/O라 강제 timeout이 없다. checkpoint 뒤의 authorize가
다시 만료를 검사한다. 전체 Job timeout/운영 감시와 수동 대조는 운영 연결에서 정한다.
GitHub 승인 client의 urllib 전체 호출 deadline 한계는 기존 문서를 따른다.

## 내구성 checkpoint

deploy_checkpoint.BoundCheckpoint(lambda: execution.claim)는 Execution에 전달할 callback이다.
첫 save에서 이미 소진·잠금 유지된 실제 Claim에 Journal을 연결한다. 호출자는 finally에서
BoundCheckpoint.close()로 journal descriptor를 닫고, Execution은 Claim을 닫는다.
원본 claim이나 checkpoint 파일은 자동 삭제하지 않는다. 실제 운영 wiring은 아직 없다.

Journal은 claim.root_fd 상대의 <plan_sha256>.checkpoints 디렉터리를 O_EXCL에 해당하는
신규 mkdir로 만들고 디렉터리·부모 fsync를 완료한다. 기존 경로는 재개/덮어쓰기하지 않는다.
각 snapshot은 0001.json부터 O_CREAT|O_EXCL|O_NOFOLLOW, mode0600으로 생성하고:
write_all → file fsync → bytes/inode 검증 → 이전 chain/claim 대조 →
directory fsync → 신규 bytes/inode/claim 재검증 순서로 반환한다.
디렉터리는 mode0700, Runner euid 소유여야 한다. symlink·hardlink·권한/소유자 변화와
파일·디렉터리 교체를 검사한다. 매 save는 보존된 모든 이전 snapshot을 다시 확인한다.

checkpoint wrapper에는 sequence, previous_sha256, claim_sha256와 execution 영수증이 있다.
영수증 Run/Attempt·계획/승인 hash/claim은 현재 잠금의 실제 Claim과 대조한다.
기존 sanitized image summary만 허용하며 raw Deployment/env/kubeconfig/token 같은
추가 필드를 거부한다. Source approval/credential metadata/Health/E2E/Pod image ID/
DELIVERY_GATE 미검증 표기를 저장 단계가 상향하지 않는다.
전체 영수증 의미 검증은 기존 Adapter/Execution의 책임이고 파일 저장이 실제 배포 성공은 아니다.

부분 write, file/directory fsync 실패, 이전 snapshot 변경, claim 상실은 writer를 영구 실패로 만든다.
생성된 partial/full 파일과 claim은 보존한다. 같은 writer/sequence 재시도·새 writer 재개·
자동 파일 삭제는 없다. directory fsync 실패의 파일은 존재해도 durable 완료로 취급하지 않는다.
PATCH_PENDING이 마지막 저장이면 실제 PATCH 적용 여부는 수동 대조한다.
APPLIED 뒤 저장 실패도 추가 PATCH/자동 rollback을 하지 않는다.
최대64 snapshot/각1MiB 제한은 이 core 상태 수를 충분히 포함하는 구현 상한이다.

단일 POSIX Runner/고정 root/로컬 파일시스템/flock 전제는 유지한다.
fsync와 directory fsync를 지원하는 파일시스템이 필요하며 NFS/분산 storage·동일 UID의
악의적인 프로세스 방어·전원 장애 복구를 시험했다고 보고하지 않는다.
실제 Runner 상태 디렉터리 생성/설정은 수행하지 않았다.

## 검증 및 잔여

신규 transport 시험은 Mock socket/HTTP/TLS context만 사용하며 실제 TLS handshake가 아니다.
checkpoint 시험은 GitHub Linux CI 임시 작업 경로에서 실제 POSIX 파일 및 I/O fault를 검증한다.
Mock Kubernetes/승인 연결 시험은 PENDING 저장 전 PATCH 금지, APPLIED 저장 실패 뒤
추가 쓰기 금지와 Source/Delivery 미검증 유지를 확인한다.
LF/CRLF·끝 개행 exact-byte 승인 회귀 시험과 PR56 리뷰 대응은
[기존 연결 문서](deploy-execution-connection.md)에 정리했다.

다음은 검증된 실제 token metadata/cleanup 및 Runtime CLI/Workflow wiring의 독립 리뷰다.
CA 공개물 지문/권한, Runner 실제 token auth, 최신 Deployment/HPA/현재 digest,
이전 이미지 pull·호환성, Worker Pull, K8S 병행 시험, APP DB TLS/Health/E2E/복구는
운영 실행 전에 실제 증거로 판단한다. 새 token/권한/Secret 생성이나 Deploy 실행은 없다.

참조: [Python ssl](https://docs.python.org/3/library/ssl.html),
[http.client](https://docs.python.org/3/library/http.client.html),
[os fsync](https://docs.python.org/3/library/os.html#os.fsync).
