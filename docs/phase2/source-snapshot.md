# #43 원본 소스 고정·#46 렌더 재현 준비

2026-10-17 KST · 이권욱 / CODEX_ASSISTED. PR89 독립 리뷰 대기와 무관하게 main `7fc43f1139b7ed8313e387e9652ef7ba1f212ef8`에서 준비했다. PR89 파일·HEAD를 변경하지 않는다.

`producer_snapshot.capture(repo, full_commit)`는 기존 [producer bridge](producer-intake-20261014.md)의 Git 읽기 경계를 재사용해 고정 커밋의 렌더 관련 소스9개를 재현 가능한 source pack으로 반환한다. 작업 폴더를 신뢰하거나 HEAD 문자열만 기록하는 것을 피하고, 이후 같은 원본으로 렌더했는지 비교할 준비다. **렌더 실행기·승인 provider·새 제출 양식은 아니다.** 기존 #43 인계를 소비하는 권욱 측 선택 도구이며 생산자에게 이 pack을 새로 제출할 의무를 만들지 않는다.

## 포함 범위와 실패 처리

고정9개: `deploy/base`의 kustomization/앱2/NetworkPolicy, `deploy/components/openshift`의 kustomization/Route, `deploy/overlays/rosa-app/kustomization.yaml`, `deploy/delivery/rosa.yaml`, `tools/check_delivery_render.py`다. P1 overlay, 작업 폴더 변경·미추적 파일, `.git`, credential, 다른 소스 파일은 수집하지 않는다. **허용된 파일 안의 내용까지 비밀 제거하는 도구는 아니다.** pack은 검토 자료로 취급하고 공개 전 내용 검토가 필요하다.

Git 전체40자리 commit만 받는다. 기존 no-replace/no-lazy-fetch/프로토콜 금지·Git당15초 제한을 재사용하고 commit/tree/blob 원본과 Git 객체 ID를 재계산한다. 파일별 SHA256·원래 Git mode·source tree/commit을 내부 manifest에 연결한다. 원본이 없는 경우 자동 fetch나 worktree fallback은 없다. 경로의 tree/regular blob을 확인하고 symlink·submodule·누락·크기초과·객체 손상·미검토 checker는 실패한다. 파일당1MiB, 읽은 고유 객체 합계16MiB 상한이다.

함수는 메모리 bytes만 반환한다. 파일 생성·원본 수정·압축 해제·Python checker 실행·kubectl/Kustomize·실제 네트워크 호출은 하지 않는다. 오류는 고정 코드이며 Git stderr·소스 내용·비공개 경로를 결과에 넣지 않는다. 실패하면 부분 pack을 반환하지 않으며 원본 정리/복구 작업도 없다.

archive는 고정 순서의 regular 파일만 포함하고 uid/gid=0, mode0644, mtime0·빈 gzip filename을 사용한다. 같은 Python/zlib 환경의 반복 실행은 byte-identical이다. 서로 다른 압축 라이브러리 버전의 compressed bytes까지 같다고 주장하지 않으며 이식 시 manifest와 원본 파일 bytes를 우선 대조한다. 원래 실행 mode는 manifest에 보존하고 archive의 실행 비트는 제거한다.

## 이 결과가 입증하지 않는 것

- `source_integrity=MATCH`는 선택된 원본 바이트의 무결성이다. GitHub 원본 신뢰·독립 리뷰·실행 승인·시험 성공은 아니다.
- `source_approval=NOT_VERIFIED`, `render_generation=NOT_RUN`, `dependency_closure=NOT_VERIFIED`, `execution_authorized=false`를 고정한다. ProviderEvidence·Lease로 변환하지 않는다.
- 이9개는 현재 소스 구조의 준비 범위다. 미래 Kustomization이 remote base/plugin/다른 파일을 참조하는지 검증하거나 안전한 renderer를 실행하는 구현이 아니다. source pack을 받았다는 이유로 포함 코드를 실행하지 않는다.
- 실제 endpoint·승인 Registry/host·배포용 clean render·rendered SHA는 여전히 기존 #38/#42/#43 입력에 의존한다. 현재 PENDING을 치환하거나 임의 값으로 승인하지 않는다. 기존 `CommittedProducerBridge.render_generation=NOT_VERIFIED`도 그대로다.

## 재현

저장소 루트, 일반 사용자 Python3.10+ 표준 라이브러리·로컬 Git가 필요하다. Git의 `--no-lazy-fetch` 지원이 없으면 실패하며 fallback하지 않는다. PyYAML·kubectl 설치·클러스터/토큰은 필요 없다.

```text
python -m unittest discover -s tools/phase2 -p test_producer_snapshot.py -v
python -m unittest discover -s tools/phase2 -p "test_*.py" -v
```

새 시험은 합성 로컬 Git 저장소로 byte 재현·원본 보존·dirty/untracked/P1 제외·replace ref 우회 방지·누락/링크/용량/객체손상/checker 변경 거부를 확인한다. 실제 렌더·Runtime 시험은 아니다. 초기 시험2건은 checker fixture에도 있던 같은 marker를 비공개 파일 누출 표지로 사용한 충돌이어서, 고유 marker로 정정했다. 제품 검사를 완화하지 않았다.

권욱 측 최신 fds-msa source `93800f09d2f5de61f6e2336771e62881f036c9ff`를 로컬에서 이 방식으로 수집했다. 현재 digest2개는 #43 채택 값이고 Registry/host는 아직 PENDING이다. 실제 입력 도착 뒤 같은 source·고정 생성 도구/입력과 clean render를 비교하는 연결이 다음 작업이다. 재빌드/Push·기존 충분한 시험·새 수동 승인 양식을 요구하지 않는다. #46 운영 CLI는 계속 BLOCKED이며 Phase1·IAM·실제 자원 변경·Runtime GO·정본 발행·종결은 없다.
