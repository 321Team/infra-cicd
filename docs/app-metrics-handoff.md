# Metrics 시험 매핑 인계 — 2026-10-09 갱신

## 현재 후보와 승인 경계 — 2026-10-09

CODEX_ASSISTED · 이권욱. **PREPARATION / PENDING_APP_HANDOFF**. 이 절은 [fds-msa#27](https://github.com/321Team/fds-msa/pull/27) 후보 `affbc69beef9967f7977d09a6fec14f7fb6c0e94`의 검토 자료다. 이전 aeb9995에서 앱 코드/시험은 같고 후보 CI의 실제 TCP listener 검사·도구 회귀가 추가됐다. 활성 `config/app-build-scan.json`은 CICD main `04e49811e46ffcd3f35183c2cfb9493901f84dc5` 기준으로 과거 승인 Source `adc90410f99cb7af5b40bc8ff92c45272e9f8bee`를 유지한다. 아래 후보를 그 파일에 적용하거나 새 Source/Unit 예외를 승인한 상태가 아니다.

### 새 Source를 선택할 때의 시험 매핑

| 서비스 | 단계 | 서비스 디렉터리 기준 pytest 파일 | 후보 시험 건수 |
|---|---|---|---:|
| fds-engine | Unit | `tests/test_rules.py` | 16 |
| fds-engine | Contract | `tests/test_dependency_upgrade_contract.py tests/test_health.py tests/test_metrics.py` | 15 |
| transaction-api | Contract | `tests/test_dependency_upgrade_contract.py tests/test_health.py tests/test_metrics.py tests/test_listener_logging.py` | 20 |
| transaction-api | Unit | 기존 NOT_APPLICABLE은 adc90410의 승인에 한정. 새 Source에도 적용할지는 APP Lead 재확인 필요 | 별도 판정 |

각 명령은 기존 `.venv/bin/python -m pytest -q`에 위 파일 목록을 붙이며 cwd/timeout300초는 기존 consumer 매핑을 재사용하는 제안이다. 중복 실행 없이 engine31+API20=51건이며, [Source validation Run37808461629](https://github.com/321Team/fds-msa/actions/runs/37808461629)의 전체 앱 suite 결과와 대조했다. parametrization이 포함된 실행 건수이며 단순 AST 함수 개수와 다르다. 해당 CI의 도구115건은 앱51건에 합산하지 않는다.

**기존 API contract 명령은 logging 시험1건을 빠뜨린다.** 후보 Source의 전체 test 파일 목록과 대조하면 위 마지막 `test_listener_logging.py`가 유일한 추가 파일이다. 이 시험은 실제 Uvicorn 설정을 읽되 Server를 대체해 소켓을 열지 않는 로그 회귀 시험이다. 실제 두 listener TCP 검증으로 표시하지 않는다. 기존 명령만 후보에 사용하면 앱50건이며, 기존 adc90410 회차의 수행 오류로 소급 판정하지 않는다.

### 이미 확인한 후보 결과와 공급물 경계

[Build/Scan Run37808457843 / attempt1](https://github.com/321Team/fds-msa/actions/runs/37808457843), [증적#178](https://github.com/321Team/fds-project-evidence/pull/178)에서 같은 affbc69 Source의 두 이미지 빌드·컨테이너 loopback TCP16건(livez200·전용metrics200/앱 counter·업무metrics12건404)·Trivy HIGH/CRITICAL 각각0/0을 확인했다. artifact11563449559는 원본 manifest53/53 검증된 결과 파일이며 **이미지 tar와 offline 공급용 SUPPLY_MANIFEST가 없는 후보 검사 artifact**다. 기존 consumer의 공급 입력으로 대체하지 않는다. image config digest를 remote manifest digest나 Pod imageID로 사용하지 않는다. 이전 aeb9995/livez-only Run37803786426과 [증적#176](https://github.com/321Team/fds-project-evidence/pull/176)은 이력으로 보존한다.

Wolfi index drift는 이번 실행에서34시간/기존 한도3일 이내였다. 새 실행은 freshness를 다시 판단하며, lock 재생성이나 날짜 갱신만으로 검사를 우회하지 않는다. CI loopback TCP는 실제 운영 Service/NodePort/NetworkPolicy·replica·DB/Rule E2E를 입증하지 않는다. 현장 Runner Test/Build/Scan·Push/Deploy는 별도다.

### 08:32 별도 오프라인 공급 준비 — Source 승인 대기 유지

[공급 Run37859961937 / attempt1](https://github.com/321Team/fds-msa/actions/runs/37859961937) SUCCESS, [증적183](https://github.com/321Team/fds-project-evidence/pull/183) HEAD `8ad3321a68879b7a92c85ba60967eb913f53e344`로 실제 공급 bytes를 추가 확보했다. Source는 위 `affbc69` 그대로이며 공급 tool/workflow는 main `8eac4c982076e3c219840a13686b15c46879981f`다. 기존 후보 Scan artifact178을 공급물로 바꿔 부른 것이 아니다.

- [공급 artifact11585741989](https://github.com/321Team/fds-msa/actions/runs/37859961937/artifacts/11585741989): 원 ZIP109,786,915bytes, SHA256 `2f13771909415439bc5a4c83036284e665b47d023b6447b37e3e18c24ea2e3fd`.
- SUPPLY_MANIFEST SHA256 `6ab02000437a7b5f7d3c99e5202d40b663056a998018a1894360c58de68b73d4`. 내부125파일, Source입력12개,4프로필 wheel106개(프로필 간 중복 포함), 베이스2종 manifest/config/계층 해시를 로컬에서 대조했다.
- workflow는 베이스 각2회 byte재현·runtime UID/GID smoke·잠긴 wheels 생성만 수행했다. 새 앱 Test/Build/Scan 결과가 아니다. 이번 실제 drift age41시간/기존3일 한도 이내이며 로컬 원본 진단을 보존했다. 새 현장 회차에서는 freshness를 다시 판단한다.
- API 표기 artifact 만료는 **2026-10-16 08:32:03 KST**. 실행자 비공개 PC에 재압축하지 않은 원 ZIP을 보존했고, 현장 Runner 전송·다른 담당자 수신·장기 보관 인수는 아직 하지 않았다.

**APP Source/Unit 승인, 공급 tool 승인 연결 및 consumer config 검토는 계속 필요하다.** step 명칭의 approved를 실제 Source 승인으로 쓰지 않는다. `delivery_authorization=false` 유지, materialize 도구 미실행, APPROVED config/승인 참조 미생성. 위 산출물이 승인 입력을 대신하거나 활성 config를 변경하지 않는다. 후보 SHA·입력·tool이 바뀌면 영향 범위를 대조한 뒤 이번 공급의 재사용 가능성을 판단한다.

08:44 consumer 보완: [증적183의 consumer-check](https://github.com/321Team/fds-project-evidence/tree/115cc4387e1c0ed722c6abc2e3d3fe1bf507e591/evidence/P1-CICD-01/app-offline-supply-37859961937/consumer-check)에서 CICD main `04e49811e46ffcd3f35183c2cfb9493901f84dc5`의 실제 `validate_record/stage/verify_staged/validate_source`를 기존 ansible01 정상 계정·새 임시 폴더에서 호출했다. 실제 공급 bytes/Source Dockerfile·입력에 대해 PASS, 잘못된 예상 hash3건 모두 거부, 원 입력 재검증 PASS다.08:45 임시270파일을 hash/정확한 집합 확인 후 정리했다. 이는 별도 consumer 함수의 파일 호환성 검증이며 전체APP config/정상 Runner 경로·권한·SELinux·이미지 load·앱 테스트를 검증한 것이 아니다. 사전 준비용 임시 전송과 현장 Runner 공급 인수를 구분한다.

### 승인 후 연결 순서·중단 기준

1. APP Lead가 최종40자리 Source, 승인자·참조, 위 Unit/Contract 매핑과 API Unit 처분을 확정한다. 후보 HEAD가 바뀌면 source·시험·공급 입력 차이를 대조하고 영향받은 검증을 다시 연결한다. 이번 문서의 병합이 해당 APP 승인을 대신하지 않는다.
2. 기존 `tools/delivery/materialize_app_build_scan_v3.py`와 [이전 config 인계 절차](../handoff/app-config-adc90410-20261007/README.md)를 재사용한다. 새 Source에 결속된 공급 생산자·승인 tool SHA·Run/Attempt·artifact ID/만료·ZIP/SUPPLY_MANIFEST hash·이미지 archives·wheels/profiles/requirements/source_inputs를 대조한다. 옛 공급 디렉터리·digest·승인 참조를 새 회차 값으로 복사하지 않는다.
3. 확정된 APP 인계와 공급 bytes로 만든 config diff를 별도 리뷰한다. Source 승인, 설정 형식 검증, consumer 독립 리뷰/병합은 구분한다. 준비 문서를 서버 활성 입력에 배치하지 않는다.
4. 정상 Runner 계정, reviewed main/workflow/config SHA, 최신 scan DB/CA/freshness, 비중첩 및 복구 인수를 확인한 뒤 승인된 회차에서 Test/Build/Scan을 실행한다. 현장 결과가 나온 뒤 별도 Robot/TokenRequest 정책·Push/Deploy GO와 연결한다.
5. Source·시험 범위·hash·만료·승인 불일치면 STOP하고 입력/실패 증적을 보존한다. 현재 문서 변경은 Runtime 원복이 없다. 향후 config 전환 실패 시 검토된 변경만 되돌리며, 과거 설정 복귀를 오래된 공급물 재실행 승인으로 보지 않는다.

새 adapter·worksheet·실행 record는 생성하지 않았다. 활성 config, Workflow, 승인 상태와 Runtime은 그대로다.

---

## 아래는 2026-09-16 당시 기록

이하의32건·Python3.12·PENDING config 설명은 당시 상태다. 현재 config나 후보 Source의 사실로 재사용하지 않는다. 원래 매핑·담당 회신·한계를 이력으로 보존한다.

### Metrics 시험 추가에 따른 CICD 매핑 후보 — 2026-09-16

상태: **PREPARATION / PENDING_APP_HANDOFF**.
이 문서는 기존 [앱 시험 매핑](app-handoff-mapping.md)의 26개 시험 관측 이후 변경을 기록한다.
앱 실행 승인이나 내부 Runner Runtime 결과를 생성하지 않는다.

## 작성 전 대조한 변경

- 관측 앱 main: `supreme33-kr/fds-msa@254b82231f8af79a06419b581174647bbccbe2bc`.
  이전 관측 `5b0049c2441b98196db6cd8cd4be9a85607568ea` 이후
  [앱 PR #5](https://github.com/supreme33-kr/fds-msa/pull/5)가 병합됐다.
  관측 main은 CICD 실행을 승인한 최종 Source SHA가 아니다.
- 두 서비스에 `app/metrics.py`, `run.py`, `tests/test_metrics.py`가 추가됐다.
  Dockerfile은 `run.py`를 복사하고 `CMD ["python", "run.py"]`로 변경됐다.
  main/metrics 포트는 transaction-api 8000/9091, fds-engine 8001/9090이다.
- 두 Dockerfile의 FROM은 여전히 `python:3.12-slim`이다. 공급 경로·digest 고정은 미완료다.
- 위 고정 Commit의 시험 파일 5개 원격 Git blob과 로컬 바이트를 대조하고 AST를 확인했다.
  CICD main `90f2f37d9e1ab77252b50e80bca339cfef48bd51`의 입력·검증 순서를 읽고,
  기존 Contract argv가 `test_health.py`만 지정해 신규 시험 6개를 제외하는 것을 확인했다.

## 중복 없는 매핑 후보

| 서비스 | 단계 | 파일 | 정적 시험 함수 수 |
|---|---|---|---:|
| fds-engine | Unit | `tests/test_rules.py` | 16 |
| fds-engine | Contract | `tests/test_health.py`, `tests/test_metrics.py` | 5 + 3 |
| transaction-api | Unit | 인계 없음, 기존 `null` 유지 | 0 |
| transaction-api | Contract | `tests/test_health.py`, `tests/test_metrics.py` | 5 + 3 |

총 32개는 AST 함수 정의 수다. 이번 작업에서 앱 pytest를 실행한 수치가 아니다.
각 시험 파일은 한 단계에만 배정하며 같은 전체 suite를 Unit/Contract에서 중복 실행하지 않는다.
setup의 서비스별 venv·requirements-test.txt 설치, timeout은 기존 후보를 유지한다.

두 Contract 명령은 각 서비스 디렉터리에서 다음 argv에 대응한다.

```text
.venv/bin/python -m pytest -q tests/test_health.py tests/test_metrics.py
```

API Unit 공백은 기존 입력 모델과 인계 시험 범위의 매핑 확인사항이다.
신규 필수 suite/AC를 추가하거나 성공만 반환하는 명령·중복 시험으로 채우지 않는다.

## 앱 담당 보고와 검증 한계

[이재환의 2026-09-16 21:41:24 KST 회신](https://github.com/supreme33-kr/fds-msa/pull/5#issuecomment-5697587143)은
edge01에서 API 8/8, engine 24/24 PASS와 별도 listener의 응답을 보고했다.
API는 8000/livez·9091/metrics, engine은 기존 cockpit-tls의 9090 포트 충돌로
`METRICS_PORT=19090`을 사용한 8001/livez·metrics 확인이다.
원래 engine metrics 포트 9090의 성공으로 옮겨 적지 않는다.

이 회신은 이전 26 PASS 이후의 앱 담당 보고이며 원본 로그를 이번에 재검증한 결과나
내부 CICD Runner의 Test/Build/Scan Runtime PASS가 아니다. 동일 보고를 다시 요구하지 않는다.
PR 본문의 이전 NOT RUN과 후속 실행 회신은 시점을 구분한다.

새 metrics 시험은 TestClient를 통한 ASGI 응답·경로 제한과 Counter 관련 검사를 포함한다.
`run.py`를 실행해 실제 두 TCP listener를 검증하는 시험은 아니다.
API 시험의 DB ping mock 및 Counter 직접 증가는 실제 DB 저장·조회 계약 검증으로 확대하지 않는다.

## 실행 차단과 후속

`config/app-build-scan.json`의 변경은 두 Contract argv에 파일 하나씩 추가하는 것뿐이다.
`PENDING_APP_HANDOFF`, Source Commit·승인자·근거 `null`, 두 base_image `null`, API unit `null`을 유지한다.
실행기는 status를 먼저 검사하므로 이 설정은 `APP_HANDOFF_PENDING`/rc1로 종료한다.
새 앱 main을 자동 승인값으로 채택하지 않는다.

1. OID-03에 맞는 실제 공급 경로·대상·Owner·Expiry와 base manifest digest를 확정한다.
2. 앱 담당이 두 Dockerfile을 동일 검증 base reference로 pin하고, metrics 변경·시험을 포함한
   최종 40자리 Source SHA·승인자·근거를 인계한다.
3. CICD 단계 매핑·실제 job/Build 공급 환경·최종 입력과 Workflow를 검토한 뒤 내부 Runner에서 실행한다.

PR #13의 수집기 재검토는 별도 추적한다. 이 변경은 그 PR의 소스나 리뷰 대상을 바꾸지 않는다.
관리 50%(5/10)·Runtime AC 1/10·DELIVERY_GATE=NOT_VERIFIED 유지.
