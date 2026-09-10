# 소스 자동 시험과 내부 Runner 실행의 구분

2026-09-10 PR #2 검토 의견에 따라 `.github/workflows/source-validation.yml`을 추가했다.

| Workflow | 실행 위치 | 시작 조건 | 판정 범위 |
|---|---|---|---|
| P1 Source validation | GitHub 제공 `ubuntu-24.04` | main 대상 PR의 기본 이벤트(opened/synchronize/reopened), main push, 수동 실행 | 수집기·CSR 단위시험, smoke 템플릿/실행본 동일성 |
| P1 Runner smoke | 내부 `cicd-runner01`, 기존 지정 라벨 | `workflow_dispatch`, infra-cicd/main 조건 | 실제 서비스 계정·Git·Podman rootless 기본 실행 |

소스 자동 시험은 P1-CICD-01 배포 파이프라인을 실행하지 않는다. `contents: read`만 지정하고, checkout의 자격증명 저장을 끄며, `actions/checkout`을 공식 저장소에서 조회한 Commit `d23441a48e516b6c34aea4fa41551a30e30af803`에 고정했다. Harbor/CA/Robot/Kubernetes 자격증명, 내부망 연결, 환경 Secret, sudo·실제 CSR 생성·배포 단계는 사용하지 않는다. 시험은 Mock·로컬 임시 파일을 사용한다. 추가 Python 패키지 설치 없이 표준 라이브러리로 실행한다.

자동 시험이 PR 갱신 시 시작될 수 있다는 점을 PR 본문에 표시한다. main 병합 시에는 push 조건으로 소스 시험이 시작된다. 반면 smoke는 main에 등록되어 수동 실행 가능해지며, **병합만으로 내부 Runner Job이 시작되지 않는다.** `pull_request_target`은 사용하지 않는다. 실제 실행 가능 여부·사용량은 조직 Actions 정책과 이용 한도에 따르며 이 변경은 조직 설정·유료 예산을 변경하지 않는다. 실행이 정책이나 한도로 차단되면 그대로 기록하고 내부 Runner PR 실행으로 대체하지 않는다.

템플릿과 실행본을 변경할 때는 같은 PR에서 둘 다 수정한다. `cmp`가 차이를 발견하면 exit 1로 소스 시험이 실패하며 자동 덮어쓰기로 차이를 감추지 않는다. 로컬에서도 다음 동일한 검사를 실행할 수 있다.

```bash
(
  set -euo pipefail
  python3 -m unittest discover -s tools/parallel-prep -p 'test_*.py' -v
  python3 -m unittest discover -s tools/pki -p 'test_*.py' -v
  cmp -- templates/runner-smoke.yml .github/workflows/runner-smoke.yml
)
```

기존 베이스라인의 내부 영속 Runner 등록 범위(`infra-cicd` 전용), 승인 main·수동 실행, Podman rootless와 별개로 소스 검증을 자동화한다. 이 CI는 보조 시험이며 독립 기술 리뷰와 이권욱의 최종 병합 판단을 대체하지 않는다. GitHub Free Private의 필수 검사 강제가 설정됐다고 주장하지 않는다. 테스트 코드 자체를 바꾸어 검사를 우회하는 변경은 독립 리뷰에서 확인한다.

근거: [GitHub 이벤트](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows), [Action SHA 고정](https://docs.github.com/en/actions/reference/security/secure-use), [고정 checkout 소스](https://github.com/actions/checkout/blob/d23441a48e516b6c34aea4fa41551a30e30af803/README.md).
