# 발급 원본의 비공개 전달 reader와 source/control 의미 채택

2026-10-24 KST, #46 준비 구현. 기존 SSH/UID/job 경로와 TokenRequest parser를 재사용한다.
실제 로그인·자격 발급·권한 변경·배포를 실행하거나 운영 SITE를 활성화하는 문서가 아니다.

## 기존 원본의 해석 채택

이권욱의 #46 원본 의미 채택 요청 범위에서 아래 **해석**을 준비 구성에 채택한다.
실제 배포 grant를 만들거나 미래 RUN·기간·원본을 합성하지 않는다.

| 구분 | 기존 근거와 채택한 의미 | 적용 한계 |
|---|---|---|
| 구성·담당 | baseline #46 댓글 6339313808·6364045357·6379184704·6380606054·6383290202 | 기존 SA/CA/대상·운영 구성·담당을 선택하는 근거다. 작성자 ID·갱신 시각·코드 승인을 현재 실행 principal/기간/허가로 승격하지 않는다. |
| source·producer | MSA source `69e28c49…`, `deploy/delivery/rosa.yaml` hash `65059dcb…`, 창 C rendered hash `3c8b6fe1…` | 기존 입력 bytes 선택은 재사용한다. manifest source SHA는 이미지 build source SHA가 아니다. Test/Build/Scan/Push/clean-render의 전체 의미 채택을 해시 일치로 대신하지 않는다. |
| original pin | 기존 CommentPin의 repo/author ID·updated_at·body hash, BlobPin의 commit/path/hash | 해당 원본과 bytes를 고정한다. hash/작성자/CI 자체가 현재 grant의 권위는 아니다. |
| control 의미 | 기존 CodeAdoptedGrantResolver의 지정 control 원본·anchor ref/commit을 fresh 대조하는 계약 | 지정 snapshot 불일치·삭제·403·기한 소진은 현재 근거를 쓸 수 없어 STOP이다. ref 이동만으로 모든 외부 철회를 알아냈다고 표시하지 않는다. 일반 main을 임의로 control로 지정하거나 fast-forward를 자동 허용하지 않는다. |
| 실제 grant·finish | 기존 수동 창 C GO/결과와 #46 코드 검토의 범위 구분 | 과거 수동 GO는 현재 자동 job의 RUN/action/Binding/기간·철회 의미를 제공하지 않는다. 새 job의 후속 관측/독점 종료는 실행 후 생산한다. 현재 실행 grant/control pin·finish scope 선택은 남아 있다. |

별도 control ledger·팀 공용 정책·매 단계 새 승인 절차는 추가하지 않는다. 실제 grant를
채택할 때는 기존 원문이 이미 허용한 대상·행위·기간과 그 원문에 적용되는 control을 연결한다.
현재 조회 원본에 없는 항목은 비워 두며 실제 실행 요청과 분리한다.

## 전달과 reader 선택

1. 공급측의 기존 `MemoryOperatingSupplier.send(include_original=True)`를 명시적으로 선택한다.
   공개 READY 확인 뒤 기존 본인 로그인/인증→TokenRequest 1회→전달→자기 OAuth 회수 순서를 유지한다.
   공급자가 같은 호출에서 받은 `IssuanceReceipt.raw`만 같은 private frame에 넣는다.
   `include_original=False` 기본값은 기존 token-only 동작이다. constructor는 실행하지 않는다.
2. 기존 SSH stdin/helper/같은 job의 Unix socket으로 v2 frame을 전달한다. native bytes는
   strict Base64로 운반하고 raw 48 KiB·전체 frame 96 KiB 상한을 지킨다. 이중 상한 때문에
   합계가 넘으면 중단하며 v1으로 fallback하지 않는다. Base64는 암호화가 아니다.
   원본 포함 모드는 발급 전에 이미 선택한 source token의 실제 길이와 SA token/raw 최대 길이로
   wire 예산을 검사한다. 실제 응답이 더 작으면 담겼을 선택도 보수적으로 거부할 수 있다.
   발급 후 예상 밖 응답이 raw 상한을 넘는 경우는 여전히 실패할 수 있으며, 이때 receipt를
   보존하고 자동 재발급하지 않는다. 이미 발급한 SA token의 즉시 회수를 보장하지 않는다.
3. job ingress의 기존 peer·context/Binding·nonce·repository·EOF 검증 뒤 원본을 소유한 bundle을
   기존 job handoff로 넘긴다. source session이 context/Binding/수신 자격 지문을 고정한 원본을 소유한다.
4. `NativeTokenMaterializer(..., read_original=native_original_reader(config.binding), ...)`를
   명시적으로 연결한다. source 인수 완료 뒤 같은 context/Binding/자격/기존 deadline에 한해
   1회 반환한다. v1 원본 누락·다른 context/Binding/token·기한 소진·close는 중단하며 재시도하지 않는다.
   반환 bytes는 기존 native parser로 다시 대조한다. 출처/서명·grant 인증을 parser에 맡기지 않는다.
5. source close와 실패 정리는 소유한 원본 참조를 놓는다. reader 실패는 application/claim/write 전에
   멈추며 finish를 만들지 않는다. 전달 실패 뒤 공급측의 기존 실제 private receipt는 복구 확인용
   `take_receipt()`에 보존한다. 다른 소유자의 alias·Python 사본을 완전히 소거하는 보장은 아니다.

원본 bytes에는 SA token이 있다. **파일·환경변수·argv·로그·CI artifact·PR/댓글로 내보내지 않는다.**
이 구현은 경로를 추가하거나 원본을 저장하지 않는다. 증적이 필요하면 응답 hash·iat/exp·요청 900초·
대상 SA UID 등 비밀값을 뺀 metadata만 기존 양식에 남긴다. 공개 reader/credential은 ROSA 자격과 분리한다.

PR139 재환 리뷰의 비차단 권고도 함께 반영했다. source 상태·deadline 동일성·읽기 전 만료의
각 위반에서 reader 호출 0회를 단언한다. 요청 900초와 서버가 단축한 실제 만료(예: 시험의 600초)는
다르므로 짧아진 응답은 허용하고 900초 상한 밖 원본은 거부한다. 이는 합성 시험값의 검증이며 운영값이 아니다.

## 시험·운영의 구분

SYNTHETIC byte·모의 API로 v1/v2·상한·strict Base64·다른 context/Binding/자격·단회 소비·
동시 take/close·재진입 close·실패 receipt 보존을 시험한다. Linux에서는 OpenSSH 대신 로컬 Python
helper를 실행해 실제 AF_UNIX/익명 pipe→source→reader→materializer 연결을 검사한다.
이는 운영 SSH·실제 원본 발급 출처·JWT 서명·GitHub authority·실제 배포/인수의 증거가 아니다.

권욱 잔여는 실제 grant/control pin·기간·producer 의미·replicas 인계 baseline/finish scope와
운영 조립/선택이다. helper 설치·운영 채널 검증은 실제 실행 범위가 열렸을 때 확인한다.
기존 SITE=None·workflow 실행 차단, 실제 인수조건과 10/26 12:00 KST 정리 기한은 유지한다.
원복은 이 변경 revert와 기존 v1 선택 복귀이며 이 구현으로 만든 운영 자원은 없다.
