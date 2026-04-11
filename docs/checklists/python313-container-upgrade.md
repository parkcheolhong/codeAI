# Python 3.13 컨테이너 전환 체크리스트

- [x] Docker 이미지 재빌드
- [x] `devanalysis114-backend` 컨테이너 재생성
- [x] 컨테이너 내부 `python --version` 1차 검증 (`3.13.x`)
- [x] 컨테이너 내부 `python --version` 2차 검증 (`3.13.x`)
- [x] 컨테이너 내부 `python -c "import fastapi, annotated_doc, uvicorn; print('ok')"` 1차 검증
- [x] 컨테이너 내부 `python -c "import fastapi, annotated_doc, uvicorn; print('ok')"` 2차 검증
- [x] 컨테이너 내부 `run_profiler_backend.py` 1차 기동 검증
- [x] 컨테이너 내부 `run_profiler_backend.py` 2차 기동 검증

## 반영 내용
- `Dockerfile` 베이스 이미지를 `python:3.13-slim`으로 변경함.
- `rebuild_backend_python313_container.ps1`로 재빌드/재생성/검증을 자동화함.
- `verify_python313_container.ps1`는 Python 3.13/import/HTTP 헬스체크를 2회 검증하도록 강화함.
- `run_profiler_backend_container.ps1`는 shell 기반 경로 탐지 후 `run_profiler_backend.py`를 직접 실행하도록 수정함.
- `run_profiler_backend.py`는 컨테이너 내부 기본 바인드 호스트를 `0.0.0.0`으로 선택하도록 보강함.

## 현재 상태
- 코드 수정은 반영됨.
- 실증 근거 기준으로 Docker 이미지 재빌드, 컨테이너 재생성, Python 3.13 2회 검증, import 2회 검증, `run_profiler_backend.py` 2회 기동 검증, `/health` 자동 검증까지 통과함.
- `rebuild_backend_python313_container.ps1 -ContainerName devanalysis114-backend` 실행 결과 `[OK] backend container rebuilt and verified: devanalysis114-backend`가 확인됨.
- Python 3.13 컨테이너 전환 및 프로파일러 기동 검증은 완료됨.
- Redis runtime recovery 경고 수정 여부는 별도 체크리스트(`docs/checklists/redis-runtime-recovery-warning-fix.md`) 기준으로 관리함.
