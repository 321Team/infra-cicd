# #46 native RUN을 실행 시점에 받는 SITE 선언

`declare_observed_native_job(select_inputs=검토된_Python_함수)`는 기존
`assemble_observed_native_job`을 실제 `build(context, command)` 시점에 호출하는
운영 조립 지점이다. 미래 native RUN/attempt를 사전 제출받거나 만들어 넣지 않는다.
기존 `JobSite`·driver·원문/control/claim/fence·finish 경로를 재사용한다.

## 선언과 실행

1. 검토된 Python callback이 같은 context/command에 맞는 기존 조립 함수의 명시 입력을
   반환한다. 반환은 Python dict이며 파일·JSON·환경변수 승인 loader가 아니다.
   callback은 구성 조립만 하고 I/O·발급·자원 변경을 하지 않는다. partial 자원을 만들었다면
   반환 전까지 그 callback 소유다. 실제 grant·원본 의미를 생성하거나 재해석하지 않는다.
2. `declare_observed_native_job`는 기대 Runner와 단회 builder로 `JobSite`를 만든다.
   선언·hosted gate·status는 callback, clock, 로그인, SSH, 원본 reader를 호출하지 않는다.
   gate의 구성 판정은 routing이며 Runtime 승인·현장 readiness가 아니다.
3. 기존 driver가 실제 context·code SHA·현재 Runner를 검증한 뒤 builder를 호출한다.
   그때만 실제 context/command를 callback과 기존 native 조립에 전달한다.
   kwargs에서 context를 바꾸거나 입력을 누락하면 실패한다. preflight/deploy만 가능하며
   기존 deploy profile을 restore에 재사용하지 않는다.
4. 조립이 끝나면 기존 owned parent를 1회 호출한다. 실제 grant/원문/control·기간·baseline,
   peer/READY·5역할·producer·claim/fence·finish 검증과 성공/실패의 소유권 정리는 그대로다.
   callback 실패·잘못된 context/command·실패한 진입도 소비되며 자동 재시도는 없다.
   외부 예외 원문을 보존하지 않고 KI/SystemExit는 비밀 없이 기존 중단 의미로 전달한다.

이 함수는 global `delivery_job_site.SITE`를 설정하지 않는다. 실제 입력이 연결된
검토된 callback과 현장 준비가 있어야 운영 구성을 채울 수 있다. `SITE=None`과
실제 자격 발급·권한 변경·배포 금지를 이번 변경에서는 유지한다.

## 이미 구현한 것과 실제로 남은 것

메모리 로그인/TokenRequest, SSH·READY, source 5역할과 native materializer,
baseline archive·producer native 검사·finish 수집은 기존 구현을 재사용한다.
추가 일반 wrapper·control ledger·새 승인 파일이나 단계별 GO를 만들지 않는다.

운영 시작 전 준비는 다음 묶음으로 마무리한다.

| 관문 | 남은 완료조건 |
|---|---|
| 코드 리뷰 마감 | 최신 HEAD의 변경 영역 독립 리뷰·필수 검사. 이미 검토한 내용 전체 재검토나 비차단 보완의 별도 승인 없음 |
| 실제 SITE·현장 준비 연결 | 실제 유한 실행 grant/원문 의미·인계 후 baseline·producer RunPin/단계 대응·finish scope를 callback에 연결. P2 전용 입력/source/state 경로와 실제 job checkout/helper 준비, 본인 인증·현재권한·READY 확인 |
| Runtime 전환 | 준비 결과·비중첩·복구 인수 확인 후 전체 실행 범위를 확정. 실행 때 native RUN/attempt·nonce·발급 응답·finish 원본을 실제 생산하고 후속 인수 |

실제 RUN/attempt·nonce·TokenRequest 응답·finish hash는 실행 중 산출물이며 사전 제출
조건이 아니다. 실제 producer **과거 RUN/attempt**와 운영 grant·현재 baseline은 서로 다르므로
새 실행의 native RUN과 혼용하지 않는다. 상시 시간대 결정은 유지하며 실제 소비/회수
비중첩과 기존 최종 정리 10/26 12:00을 따로 확인한다.

10/25 02:02 KST 읽기 점검에서 ansible01→Runner SSH, 기존 host key, Python3.12.15,
API/OAuth TLS와 메타데이터를 확인했다. P2 전용 base/input/source/state와 job checkout은
없었고 기본 checkout에 helper2도 없었다. 이는 별도 helper를 base checkout에 설치하라는
뜻이 아니다. 승인된 실제 job은 기존 workflow의 exact SHA `p2-job-{run_id}-{attempt}`
checkout으로 helper를 받으며, 이 경로를 실행 전에 만들어 달라고 요구하지 않는다.
현재 metadata와 코드 검증은 본인 로그인·TokenRequest·배포·최종 인수 성공의 증거가 아니다.
