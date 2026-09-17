# 앱 담당 검토용: 공급 완료에 따른 pin·lock 반영안

원본 requirements Source: `supreme33-kr/fds-msa@254b82231f8af79a06419b581174647bbccbe2bc`.
기존 승인 `01758583...`를 이 변경의 승인으로 승계하지 않는다. 이 폴더는 **앱 저장소에 아직 반영되지 않은 인계 후보**다.

| 파일 | 인계 내용 |
|---|---|
| 서비스별 Dockerfile | `docker.io/library/python@sha256:2fe5997d249a808b8eeea52c58a1dbffbba28754dc11699ef5c029f2d818ce79`, 추가 local context `python-wheels`, 오프라인 전체 hash 설치·pip check. 기존 app/run.py, UID10001, 두 포트 유지 |
| transaction-api/requirements-runtime.lock | 수집된25개 항목 원본, SHA256 `3a71d0d227415f3f818642b5370920f02a23a9bcb28f4dd31aac1d59d96c756e` |
| transaction-api/requirements-test.lock | 수집된29개 항목 원본, SHA256 `72137816616e16e2b88f86630c507800414c3b37c14c7f624b9042fd66a8ef85` |
| fds-engine/requirements-runtime.lock | 수집된18개 항목 원본, SHA256 `c9d6138da341b431f0b7fdaf4aee78d59f9207e9d3f2bf4636b0505681c1f80b` |
| fds-engine/requirements-test.lock | 수집된26개 항목 원본, SHA256 `3a51dbbb6dd56d09658f0d2343e4b4847394f006f1f2aa082886bc468e797b65` |

lock 원본은 [정본 후보 수신 기록](https://github.com/321Team/fds-project-baseline/tree/7636a609989a91d6f107353b199ebe0857396132/audit/v3.1.19/intake/p1-cicd-supply-20260917T010757Z)과 동일하다. 무작위로 새 버전을 resolve한 파일이 아니다. wheel 원본은 Runner의 기존 공급 폴더에 남아 있다.

앱 담당이 6개 파일과 Unit/Contract 범위를 검토해 반영한 최종40자리 SHA·승인 근거를 인계한다. API Unit은 새 테스트가 필요한지, 기존 인계대로 해당 없음인지 명시해야 한다. 기존 requirements4개가 바뀌면 현재 공급 lock을 그대로 승인하지 않는다.

runtime lock은 Python3.12.14/pip25.0.1/glibc2.41 base image 대상, test lock은 Python3.12.14/pip23.3.2/glibc2.39 Runner 대상에서 생산·설치 검증됐다. 실제 앱 Build/실행/스캔 성공은 별도이다. 새 Dockerfile의 실제 소비 연결은 [실행 안내](../../docs/offline-build-scan.md)를 따른다.
