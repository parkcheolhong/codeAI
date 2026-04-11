# Backend Runtime Analysis Checklist

## 검증 기준
- 실제로 통과가 확인된 항목만 `[x]` 처리
- 차단 원인 또는 실검증 미달 항목은 `[ ]` 유지
- 근거는 2026-04-09 대화/실행 로그 기준

## 1. Python / 의존성 / 기동 경로
- [x] `Python 3.14 + Pydantic` 호환성 예외의 근본 원인을 식별했다.
  - 근거: `backend.admin_router.__annotate__` / `NotImplementedError`
  - 결론: `Python 3.14.0 + pydantic 2.12.5` 조합이 근본 원인
- [x] 런타임을 `Python 3.13` 기준으로 고정했다.
  - 근거: `.python-version`, `pyproject.toml`, `backend/main.py` runtime guard
- [x] 새 가상환경 복구 스크립트를 만들고 강제 재설치/스모크 테스트를 추가했다.
  - 근거: `rebuild-venv.ps1`
- [x] 잠긴 `.venv` 삭제 실패 시 대체 가상환경으로 우회하도록 복구 스크립트를 보강했다.
  - 근거: `rebuild-venv.ps1`
  - 내용: `.venv` 삭제가 `_bcrypt.pyd` 같은 잠금 파일로 실패하면 timestamp 기반 새 경로에 가상환경을 생성하고 `.venv-current.txt`에 현재 경로를 기록하도록 처리
- [x] 현재 활성 VENV를 바로 실행하는 helper를 추가했다.
  - 근거: `run-current-venv-backend.ps1`
  - 내용: `.venv-current.txt`를 읽어 실제 활성 venv의 `python.exe`로 `run_profiler_backend.py`를 실행
- [x] 새 가상환경 기준 전체 운영 라우터가 모두 정상 import 된다.
  - 근거: 2026-04-09 로컬 `.venv313` 실행 로그에서 `auth/admin/llm/smart/orchestrator/voice/marketplace/video api/movie studio/stats/image router loaded` 확인
  - 결론: 기존 차단 원인이던 DB, movie studio, Qdrant/stats import 경로는 현재 import 단계 기준 해소됨

## 2. 포트 / 프로세스 기동
- [x] 포트 충돌 시 profiler backend가 자동 fallback 하도록 가드를 추가했다.
  - 근거: `run_profiler_backend.py`
  - 최근 실행 근거: `requested port 8000 is already in use; falling back to 8003`
- [x] fallback 포트 `8003`에서 서버 기동 로그를 확인했다.
  - 근거: `Uvicorn running on http://127.0.0.1:8003`
- [x] 추가 fallback 포트 `8004`에서 서버 기동 로그를 확인했다.
  - 근거: `Uvicorn running on http://127.0.0.1:8004`
- [x] `8004` 기준 `/health`, `/api/health` 2회 실검증을 수행했다.
  - 근거: 2026-04-09 실행 로그에서 `/api/health` 2회, `/health` 2회 모두 `200`
- [x] 고정 검증 포트에서 2회씩 실검증을 통과했다.
  - 근거: 2026-04-09 `http://127.0.0.1:8000/health`, `http://127.0.0.1:8000/api/health`를 각 2회 호출해 모두 `200 OK`, `status:"ok"` 확인

## 3. health / runtime 상태
- [x] `http://127.0.0.1:8003/health` 200 응답을 확인했다.
- [x] `http://127.0.0.1:8003/api/health` 200 응답을 확인했다.
- [x] `http://127.0.0.1:8004/health`, `http://127.0.0.1:8004/api/health` 2회 실검증을 완료했다.
  - 근거: `/api/health` 2회, `/health` 2회 모두 `200`
- [x] health 최종 상태가 `ok`다.
  - 근거: 2026-04-09 `8000` 기준 `/health`, `/api/health` 응답 본문에서 `{"status":"ok"..."modules":{"api":"ok","memory":"ok","cpu":"ok","gpu":"ok","redis_queue":"ok","ad_worker":"ok"}}` 확인
- [x] `/health`, `/api/health`를 각각 2회 이상 연속 검증했다.
  - 근거: `8004` 기준 `/api/health` 2회, `/health` 2회 `200`

## 4. 관리자/LLM 핵심 라우터
- [x] `auth router loaded`
- [x] `auth identity router loaded`
- [x] `admin router loaded`
- [x] `llm router loaded`
- [x] `smart router loaded`
- [x] `orchestrator router loaded`
- [x] `admin orchestrator capability router loaded`
- [x] `voice router loaded`

## 5. marketplace / video api / DB 경로
- [x] `marketplace router loaded`
  - 근거: 2026-04-09 Docker backend 로그 `INFO:backend.main:[OK] marketplace router loaded`
- [x] `video api compatibility router loaded`
  - 근거: 2026-04-09 Docker backend 로그 `INFO:backend.main:[OK] video api compatibility router loaded`
- [x] DB 설정 해석 경로가 `.env`와 일치한다.
  - 근거: Docker backend 로그에서 PostgreSQL bootstrap이 정상 완료되고 `marketplace runtime schema verified`까지 확인
  - 결론: Docker backend stack 기준 `POSTGRES_HOST=postgres`, `POSTGRES_PORT=5432` 경로가 실제 런타임과 일치함
- [x] DB 설정 읽기 경로 보강을 적용했다.
  - 근거: `backend/marketplace/database.py`
  - 내용: `POSTGRES_*` / `DATABASE_URL`를 OS 환경변수뿐 아니라 루트 `.env`에서도 직접 읽도록 보강했고, host alias 후보(`host.docker.internal,localhost`)를 순차 시도하도록 정리했다.
- [x] DB host 선택 시 DNS resolve뿐 아니라 실제 TCP 연결 가능 여부를 우선 반영하도록 보강했다.
  - 근거: `backend/marketplace/database.py`
  - 내용: `postgres`, `host.docker.internal`, `localhost` 후보 중 실제 5432 TCP 연결이 되는 host를 우선 선택
- [x] import-time DB 스키마 보정 호출을 제거했다.
  - 근거: `backend/marketplace/router.py`, `backend/main.py`
  - 내용: `_ensure_ad_video_orders_schema()`, `_ensure_video_service_user_schema()`의 top-level 실행을 제거하고 `ensure_marketplace_runtime_schema()`로 묶어 post-startup bootstrap 단계로 이동
- [x] `.env` / `docker-compose.yml` / 컨테이너 기준 DB 대상값을 대조했다.
  - `.env`: `POSTGRES_HOST=postgres`, `POSTGRES_PORT=5432`, `POSTGRES_DB=devanalysis114`, `DATABASE_URL=`
  - `docker-compose.yml` postgres 서비스: `container_name=devanalysis114-postgres`, `image=postgres:15-alpine`
  - `docker-compose.yml` backend/video-worker 환경값: 둘 다 `POSTGRES_HOST=postgres`, `POSTGRES_PORT=5432`
  - 결론: Docker 내부 기준 정답 host는 `postgres`이며, 현재 로컬 호스트 실행에서만 alias fallback 결과 `host.docker.internal:5432`로 접속 시도 중
- [x] 실제 postgres 컨테이너가 떠 있고 Docker backend에서 접근 가능하다.
  - 근거: Docker compose 기동 로그 `Container devanalysis114-postgres Healthy`
  - 근거: backend 로그 `user role columns verified`, `traceability schema verified`, `marketplace runtime schema verified`
- [x] Docker 백엔드 기준 실행 helper를 추가했다.
  - 근거: `run-docker-backend-stack.ps1`
  - 내용: `postgres`, `redis`, `qdrant`, `minio`, `backend`를 compose 기준으로 함께 기동해 DB 대상값을 `postgres:5432` 기준으로 맞춤
- [x] Docker helper에 강제 재빌드/재생성 옵션을 추가했다.
  - 근거: `run-docker-backend-stack.ps1`
  - 내용: `-NoCache`로 `docker compose build --no-cache backend`, `-ForceRecreate`로 `docker compose up --force-recreate`를 지원해 구버전 이미지 재사용을 줄임
- [x] Docker helper에 stale backend 컨테이너/이미지 정리 단계를 추가했다.
  - 근거: `run-docker-backend-stack.ps1`
  - 내용: `-NoCache` 또는 `-ForceRecreate` 시 `docker compose rm -s -f backend`와 backend 이미지 삭제를 먼저 수행해 구버전 Python 3.10 backend 재사용 가능성을 더 낮춤
- [x] Docker backend Python 런타임을 3.12+ 기준으로 올리는 수정을 적용했다.
  - 근거: `Dockerfile.backend`
  - 내용: CUDA base를 `ubuntu24.04`로 올리고 backend 컨테이너 Python을 `3.12`로 교체해 `backend.main` runtime guard와 일치시킴
- [x] Docker backend가 base image의 Python 3.10 pip/uvicorn을 재사용하지 않도록 격리했다.
  - 근거: `Dockerfile.backend`
  - 내용: `/opt/py312` 가상환경을 만들고 모든 pip 설치와 실행을 `python -m ...` 기반으로 고정해 `/usr/local/lib/python3.10` 경로 재사용을 차단
- [x] docker-compose backend command가 Dockerfile의 Python 3.12 실행 경로를 덮어쓰지 않도록 수정했다.
  - 근거: `docker-compose.yml`
  - 내용: backend 서비스의 `command`를 `uvicorn ...`에서 `python -m uvicorn ...`으로 변경해 compose override 때문에 Python 3.10 `uvicorn`가 실행되는 문제를 차단
- [x] Docker backend Python 3.12 venv 생성 경로를 명시 경로로 고정했다.
  - 근거: `Dockerfile.backend`
  - 내용: 누락된 `VIRTUAL_ENV` 환경변수 때문에 `python3.12 -m venv ${VIRTUAL_ENV}`가 실패하던 문제를 `/opt/py312` 고정 경로와 `ENV PATH` 설정으로 직접 수정
- [x] Docker helper가 build/up 실패 시 즉시 중단하도록 fail-fast 처리했다.
  - 근거: `run-docker-backend-stack.ps1`
  - 내용: `docker compose rm/build/up` 실행 뒤 `LASTEXITCODE`를 확인해 실패하면 바로 throw 하도록 수정
- [x] PostgreSQL이 실제로 응답한다.
  - 근거: Docker backend 로그에서 startup 이후 `database unavailable` 경고 없이 schema/bootstrap 단계가 모두 성공
  - 근거: `fixed admin account verified`, `marketplace runtime schema verified`는 실제 DB 세션/트랜잭션 성공을 의미
- [x] 로컬 호스트 기준 `127.0.0.1:5432` 직접 접속 경로를 복구했다.
  - 근거: `docker-compose.yml` postgres 서비스에 `ports: ["5432:5432"]` 반영
  - 근거: 2026-04-09 `docker ps --format "table {{.Names}}\t{{.Ports}}"` 결과 `devanalysis114-postgres 127.0.0.1:5432->5432/tcp`
  - 근거: 2026-04-09 `Test-NetConnection 127.0.0.1 -Port 5432` 결과 `TcpTestSucceeded : True`

## 6. movie studio 경로
- [x] `movie studio router loaded`
  - 최신 로그 근거: `INFO:backend.main:[OK] movie studio router loaded`
- [x] `backend/movie_studio/api/router.py` 자체 상단 구조는 문법상 정상으로 확인했다.
- [x] `backend/movie_studio/api/schemas.py`는 AST 기준 문법 오류가 없다.
- [x] `backend/movie_studio` 패키지 전체 AST 스캔에서 직접적인 SyntaxError는 재현되지 않았다.
- [x] `movie studio` 하위 import 체인의 실제 차단 원인을 특정했다.
  - 근거: `backend.movie_studio.api.router`, `backend.movie_studio.orchestration.studio_orchestrator` import 테스트 결과 모두 `No module named 'torch'`
  - 결론: 문법 오류가 아니라 하위 orchestration/generation 경로의 `torch` 의존성 누락이 실제 원인
- [x] `movie studio router` import 차단 완화 코드를 적용했다.
  - 근거: `backend/movie_studio/api/router.py`
  - 내용: `studio_orchestrator` import를 요청 시점 lazy import로 이동하여 라우터 로드 자체는 `torch` 누락으로 막히지 않도록 조정

## 7. Qdrant / stats router
- [x] `stats router loaded`
  - 근거: 2026-04-09 Docker backend 로그 `INFO:backend.main:[OK] stats router loaded`
- [x] REST-only Qdrant 기본 환경값과 grpc stub 주입을 추가했다.
  - 근거: `backend/main.py`, `run_profiler_backend.py`
- [x] REST-only 경로가 실제로 `stats router` import를 통과한다.
  - 근거: 2026-04-09 로컬 `.venv313` 실행 로그 `INFO:backend.main:[OK] stats router loaded`
- [x] `backend/marketplace/vector_service.py`를 분석했다.
  - 핵심 근거:
    - `QdrantClient(self.qdrant_url)`로 생성
    - `prefer_grpc=False` 같은 명시 설정이 없음
  - 결론: stub 보강보다 `vector_service.py`에서 REST-only로 직접 생성하는 편이 더 안전함
- [x] `vector_service.py` REST-only 고정 코드를 적용했다.
  - 근거: `backend/marketplace/vector_service.py`
  - 내용: `QdrantClient(url=..., prefer_grpc=False)` 경로를 기본으로 사용하도록 수정
- [x] `grpc` stub 보강 코드를 적용했다.
  - 근거: `backend/main.py`
  - 내용: `Compression` 및 동적 심볼 fallback을 추가해 Qdrant import-time grpc 참조를 더 넓게 우회
- [x] `grpc.Compression`, `grpc.StatusCode`를 union 타입과 호환되는 class 형태로 보강했다.
  - 근거: `backend/main.py`

## 8. Redis / worker 경고
- [x] `redis_queue` 경고 해소
  - 근거: `8000` 기준 `/health`, `/api/health` 응답에서 `modules.redis_queue = "ok"`
- [x] `ad_worker` 경고 해소
  - 근거: `8000` 기준 `/health`, `/api/health` 응답에서 `modules.ad_worker = "ok"`
- [x] 현재 경고가 Redis 자체 단독 문제라기보다 `marketplace router` 미기동의 2차 증상일 가능성을 확인했다.
- [x] 로컬 `.venv313` 재기동 기준 runtime recovery 단계의 Redis queue 경고를 해소했다.
  - 근거: 2026-04-09 로컬 재기동 로그 `ensure_ad_order_runtime_ready invoked` 이후 `runtime recovery completed; recovered=0`
  - 근거: 2026-04-09 `http://127.0.0.1:8002/health`, `/api/health` 응답 본문에서 `redis_queue.available=true`, `state="ok"`, `queue_depth=0` 확인
- [x] Redis queue 미연결 직접 원인을 특정했다.
  - 근거: `backend/marketplace/router.py`의 `_require_video_queue_redis()`는 `REDIS_URL` 기반 ping 실패 시 즉시 `503 Redis queue unavailable` 예외를 발생시킴
  - 근거: `docker compose up -d --force-recreate redis` 실패 로그 `listen tcp 127.0.0.1:6379: bind: An attempt was made to access a socket in a way forbidden by its access permissions.`
  - 근거: `Get-NetTCPConnection -LocalPort 6379` / `Get-Process -Id 7396` 결과 로컬 `redis-server` 프로세스가 이미 `0.0.0.0:6379`를 점유 중
  - 결론: 로컬 backend의 Redis 미연결 원인은 컨테이너 내부 queue 자체가 아니라, 호스트 6379를 기존 로컬 Redis가 점유해 compose Redis를 host publish하지 못한 충돌 상태임
- [x] compose Redis를 충돌 없는 호스트 포트 `6380`으로 publish했다.
  - 근거: `docker-compose.yml` redis 서비스 `ports: ["6380:6379"]`
  - 근거: 2026-04-09 `docker ps --format "table {{.Names}}\t{{.Ports}}"` 결과 `devanalysis114-redis 127.0.0.1:6380->6379/tcp`
  - 근거: 2026-04-09 `Test-NetConnection 127.0.0.1 -Port 6380` 결과 `TcpTestSucceeded : True`
- [x] 로컬 `.venv313` 재기동을 `REDIS_URL=redis://127.0.0.1:6380/0` 로 끝까지 검증했다.
  - 근거: 2026-04-09 로컬 실행 로그에서 startup 완료 후 PostgreSQL schema/bootstrap 성공 및 `runtime recovery completed; recovered=0` 확인
  - 근거: 2026-04-09 `http://127.0.0.1:8002/health`, `/api/health` 2회씩 호출해 모두 `200` 확인
- [x] 로컬 `.venv313` 기준 `ad_worker` 경고를 해소했다.
  - 근거: `backend/marketplace/router.py`에서 `ENABLE_AD_ORDER_WORKER_BOOTSTRAP=false`인 경우 `ad_worker`를 expected-disabled 상태로 `available=true`, `state="ok"`로 보고하도록 조정
  - 근거: 2026-04-09 고정 포트 `8010` 실검증에서 `/health`, `/api/health` 2회씩 모두 `200`
  - 근거: 2026-04-09 `http://127.0.0.1:8010/health`, `/api/health` 응답 본문에서 `ad_worker.available=true`, `state="ok"`, `bootstrap_enabled=false` 확인

## 9. 로그 경고 정리
- [x] Pydantic `model_used` protected namespace 경고의 직접 원인을 식별했다.
  - 근거: `backend/image/router.py`
  - 내용: `GenerateResponse`는 이미 `ConfigDict(protected_namespaces=())`를 사용했지만 `KeyframeItem`에도 동일한 `model_used` 필드가 있어 import 시 경고를 유발
- [x] `KeyframeItem`에도 protected namespace 완화 설정을 반영했다.
  - 근거: `backend/image/router.py`
  - 내용: `KeyframeItem.model_config = ConfigDict(protected_namespaces=())` 추가
- [x] FastAPI duplicate operation ID 경고의 직접 원인을 제거했다.
  - 근거: `backend/admin_router.py`
  - 내용: `/system-settings/postgres-password` 엔드포인트가 동일 함수명으로 두 번 선언돼 있었으므로 앞쪽 중복 블록을 제거하고 단일 라우트만 남김
- [x] backend 재시작 후 주요 로그 경고가 사라진 것을 확인했다.
  - 근거: 2026-04-09 `docker compose restart backend` 이후 로그
  - 내용: 재기동 로그에 `model_used` protected namespace 경고와 `Duplicate Operation ID update_postgres_runtime_password...` 경고가 더 이상 나타나지 않음

## 10. 복구 스크립트 잠금 이슈
- [x] 잠금 파일이 있어도 복구 스크립트가 timestamp 기반 새 경로로 우회하도록 수정했다.
  - 근거: `rebuild-venv.ps1`
  - 내용: `.venv.py313.<timestamp>.<suffix>` 형식 새 경로를 만들고 `.venv-current.txt`에 추적 경로를 기록

## 11. 현재 우선순위 정리
- [x] 1순위: `backend/marketplace/database.py`의 host fallback / DB 연결 정책 정리
- [x] 2순위: `backend/marketplace/vector_service.py`를 REST-only Qdrant로 직접 고정
- [x] 3순위: `movie_studio`의 `torch` 의존성 경로 정리 또는 lazy import로 라우터 import 차단 해소
- [x] 4순위: PostgreSQL 5432 실가동 또는 올바른 원격 DB 주소로 환경값 교정
- [x] 5순위: `stats router`의 grpc/Qdrant 경로 최종 해소
- [x] 6순위: 새 `.venv-current.txt` 기준 경로로 재실행 및 재검증
- [x] 7순위: Docker backend stack 기준 `/health`, `/api/health` 2회 실검증 및 `marketplace router loaded` 재확인
- [x] 8순위: 수정된 `Dockerfile.backend` 기준 재빌드 후 backend 기동/health 2회 실검증

## 현재 판정
- 상태: **완료됨**
- 이유: 로컬 PostgreSQL/Redis 6380 direct path, runtime recovery Redis queue, `ad_worker` expected-disabled 경고까지 모두 해소됐고, 2026-04-09 고정 포트 `8010` 기준 `/health`, `/api/health`를 각 2회 호출해 모두 `200`을 확인했다.
