# FDS #46 수신 원본 기반 SITE 입력 연결

기준 소스: infra-cicd `7726a290a323dabe96d0325aad811e951b87c027`.
관측·원본 대조 기준: 2026-10-22 KST. 이번 구현은 **확인된 고정 입력의 연결**이며 운영 SITE 채택, 인증, 실행 GO가 아니다. `delivery_job_site.SITE=None`과 실행 workflow는 그대로 둔다.

## 연결한 값과 근거

`tools/phase2/delivery_fds_site_inputs.py`는 기존 `Binding` → `SiteConfiguration` → `ParentJobBuilder`를 사용한다. 별도 정책 파일/JSON 플래그/자격 로더를 만들지 않는다.

| 입력 | 확인된 내용 | 근거와 한계 |
|---|---|---|
| API·namespace | `api.fds-rosa-hcp.khht.p3.openshiftapps.com:443`, `fds-app` UID `222bf961-73d9-4743-972f-2d44da216af2` | 전달 원본·기존 첫 설정 증적; 실행 시 기존 readback이 현재성을 재검증 |
| cluster_id | ClusterVersion `spec.clusterID` = `37eecf03-b7bc-45e1-8089-5a14a47fccd9` | OCM ROSA ID `2th4q1q4qf7ra35862s1u9q7f6n0vnmc`와 구분 |
| 배포 subject | `system:serviceaccount:ci-cd:fds-deployer`, UID `6a4c1f9f-1291-4f1d-b999-b4dbc5d179c2` | 관측 identity만 고정; groups·현재 자격·최소 권한은 합성하지 않음 |
| issuer/audience | `https://oidc.op1.openshiftapps.com/2te9vlkhgbi43b770ni4f7nhiasie0fh` | discovery·기존 claims와 대조; 새 JWT 서명/발급 사실의 증거는 아님 |
| 명시 CA | 공개 ISRG Root X1 PEM SHA256 `22b557a27055b33606b6559f37703928d3e4ad79f110b407d04986e1843543d1` | 수신 서버 chain과 오프라인 서명/hostname 대조; 실제 TLS 연결 미실행 |
| source | MSA42 merge `69e28c494a120c388afe18d901043d05333d7c7c` | 검토 HEAD와 merge tree 동일; 이미지 build source와 혼동하지 않음 |
| render·contract | app `3c8b6fe17d9a4705fd57158ec426294fe2a24413c83091aa3a568912b7263516`, contract `65059dcbf6cfb6e6a6a2e65d92cfb95ea5efe3637265eeb79918c631faa6089c` | 6객체·Harbor 이미지2개·승인 Route host만. prerequisites/NP/Secret/RBAC는 적용하지 않음 |

비공개 전달 tgz `p46-inputs-20261022T190046.tgz`는 6522바이트, 외부 SHA256 `c166fbb3f19c144645b932a5a6e53911907ac6edd8c9cb59d7f1fa6627155a38`, 내부 manifest `c646983d0a16fddf33d694155c0bf67d4701b8813c253db035ce4f84c1bbf086`, 10/10 일치다. 이는 바이트 무결성 확인이다. 비공개 원본·자격은 저장소에 포함하지 않는다.

공개 CA DER SHA256은 `96bcec06264976f37460779acf28c5a7cfe8a3c0aae11a8ffcee05c0bddf08c6`이다. [ISRG 공개 인증서](https://letsencrypt.org/certificates/)와 [CP/CPS](https://github.com/letsencrypt/cp-cps/blob/main/CP-CPS.md)를 대조했다. 19시 관측 LB IP4개를 현재 endpoint로 기본 지정하지 않는다. Sender의 명시 CA·정확한 hostname·TLS 검증·numeric endpoint 계약은 유지한다.

운영 기록: [#46 수신 원본 검토](https://github.com/321Team/fds-project-baseline/issues/46#issuecomment-6339413996), [D22 Harbor 소스 Final](https://github.com/321Team/fds-project-baseline/issues/42#issuecomment-6337537087), [MSA42](https://github.com/321Team/fds-msa/pull/42).

## 사용 위치와 순서

1. 검토된 Runner/job 부모 코드에서 `build_configuration`을 호출한다. 실제 `run_id`, platform/supply 원본 SHA, 절대 input/source/store 경로, 파일명, 실행 예산과 finalize 여부를 **모두 명시 인자**로 전달한다. 함수는 파일을 읽거나 디렉터리를 만들지 않는다. 안전한 POSIX 파일·Git source·실제 바이트 검증은 기존 loader가 담당한다.
2. 기존 형식의 실제 `RuntimeAuthorityPlan`을 별도로 검토·채택한다. `validate_plan`은 고정 profile과의 일치만 검사하며 인증 판정이 아니다. 현재 token fingerprint/iat/exp/groups/IP·원본/control은 호출자가 실제 자료로 제공해야 한다.
3. 실제 `FinishAdoptionSlot`, 인증된 peer/routes, 절대 deadline, wire/publish/open/monotonic callback을 명시해 `connect_parent`로 기존 부모에 연결한다. 생성 시 callback을 호출하지 않는다. 실제 실행 시 기존 부모의 native job·단일 사용·deadline·채널·정리 검사가 그대로 적용된다.
4. 이번 first-empty-target profile은 `previous=None`만 대조하고 기존 `FirstDeploymentBaseline`의 fresh GET/404 조건을 유지한다. **수동 C 배포 후 기존 객체가 있으면 차단**한다. 실제 기존 객체·UID·owner/spec 및 previous에 대한 검토 없이 baseline을 생성하거나 current를 previous로 복사하지 않는다. restore/end-page를 포함하지 않는다.

실패 시 고정 코드로 중단한다. 생성 실패는 외부 자원을 만들지 않으며, 실행 중 실패한 경우 기존 parent/session의 자기 소유 핸들 정리·claim 보존 규칙을 사용한다. 자동 재발급/재시도/삭제/원복 실행을 추가하지 않는다.

## 여전히 필요한 운영 연결

| 항목 | 담당·완료조건 |
|---|---|
| 현장 issuer 원본/실발급 | 권욱이 실제 발급 결과·fingerprint·수명·issuer 검토를 코드에 채택, 하영이 기존 권한 경로의 실제 관측 인계. `can-i yes`를 발급 성공으로 바꾸지 않음 |
| 실제 job/Runner·peer·전달 경로 | 권욱 구현, 재환 Runner 정보 인계. 현재 계정/UID·job 정체성·host key/peer 인증·bounded channel·rendezvous를 실제 자료로 검토 |
| post-auth 발급/plan 완성 순서 | 권욱 구현. 기존 parent는 ready 전에 최종 Binding/IssuerFacts를 요구한다. 뒤늦은 발급 결과를 연결하려면 검토된 두 단계 준비/완성 경로가 필요하며 이 고정 입력 helper가 해결한 것으로 표시하지 않음 |
| 원본/control·종료 증적 | 권욱이 승인·producer·issuer 실제 원본/철회 control 의미, 현장 경로와 종료 관측 원본을 연결. hash/type만으로 원본 의미/승인 판정 금지 |
| 수동 C 후 객체/이전 상태 | 하영 readback·권욱 배포 baseline 검토. 실제 원본 없으면 비어 있다고 가정하지 않음. current source 선택은 운영 인수 완료가 아님 |

새로운 플랫폼 양식·중복 서명·동일 시험을 담당자에게 추가 요구하지 않는다. 이미 수신한 원본의 해당 부분만 재사용하고 새로 생기는 발급/종료 관측만 연결한다.

## 검증과 단계

신규 시험은 관측된 공개 pin과 명시적인 **SYNTHETIC** per-run fixture를 분리한다. fixture는 실제값·승인값·실행 증적이 아니다. 네트워크/DNS를 금지하고 UUID 혼동, source/render/CA/subject drift, 인자 누락, callback 미실행, 기존 객체 자동 채택 금지와 기존 parent 연계를 검사한다.

STATIC/MOCK만 이 변경의 로컬 완료 범위다. CI는 실제 해당 HEAD 결과를 별도 확인한다. RUNTIME·운영 SITE 채택·독립 REVIEW·MERGE·PUBLICATION·#46 AC 인수는 자동 완료되지 않는다.
