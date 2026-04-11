# 개발분석114.com - 완전한 프로덕션 시스템

## Level 3 완전 자율 AI 시스템 + 마켓플레이스 + 양자화 LLM 통합

[![Python](https://img.shields.io/badge/Python-3.10+-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.109-green.svg)](https://fastapi.tiangolo.com/)
[![Docker](https://img.shields.io/badge/Docker-GPU-2496ED.svg)](https://www.docker.com/)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

---

## 🚦 배포 승인 관점 요약

- 현재 판정: `완료됨`
- 현재 상태: 핵심 웹 진입·게이트웨이·관리자/마켓 분리, 운영 경로 실검증, hard gate/readiness artifact 구조와 self-run terminal/applied_to_source 최신 세션 재검증 근거가 모두 최신 세션 기준으로 닫힘
- 보류 항목: 없음
- 기준 문서:
  - `docs/system-cleanup-checklist.md`
  - `docs/deployment-blockers-checklist.md`

### 현재 운영 핵심 메모

1. 프런트 앱은 공개 메인 `frontend`, 관리자 전용 `frontend/frontend`로 분리 운영됨
2. 운영 게이트웨이는 compose 내부 프런트 런타임과 nginx 기준으로 검증됨
3. 결제는 실PG가 아니라 시뮬레이션 완료 반영 방식으로 유지됨
4. 리뷰 API와 공개 상세 페이지 리뷰 UI 연결 검증이 완료됨
5. 고급 검색 공개 라우트와 Stripe 실결제 경로는 현재 실서비스 범위에서 제거됨
6. OpenAPI는 현재 `3.1.0` 기준으로 노출됨
7. self-run/오케스트레이션 단계는 현재 9단 고정 순서(구조 설계 → 폴더 및 기초 구현 → 설계반영된 골조구현 → 핵심엔진 구성 → 로직(id식별) → 데이터 → 서비스 → API → 프론트) 기준으로 확장됨
8. 관리자 self-run runtime artifact(`job_request.json`, `worker.log`, `worker_status.json`)는 강제 생성 및 존재 검증이 반영됐고, worker PID 기록까지는 실확인됨
9. 관리자 self-run 최종 종료 상태(`pending_approval`, `no_changes`, `applied_to_source`)의 최신 세션 재검증 근거는 기록표에 반영됐고, Round 7 최종 승격까지 완료됐다

### 현재 운영 해석

- 백엔드, DB, nginx, 기본 인증/마켓/관리자 API는 동작 가능
- 공개 메인 앱은 `frontend`, 관리자 전용 앱은 `frontend/frontend` 기준으로 운영한다
- RTX 5090 기준 영상/LLM 운영 설정은 runtime config, 관리자 시스템 설정, LLM 상태 API에 실제 반영돼 있음
- 핵심 사용자 플로우(로그인, 마켓 목록/상세, 리뷰, 구매 완료 반영, 다운로드, 관리자 진입) 재검증이 완료됐다
- 마켓플레이스 cross-site `OPTIONS /marketplace` 는 nginx preflight 처리로 `204` 응답 기준까지 확인됐다
- 관리자 self-run 은 runtime artifact 생성, terminal state, `applied_to_source evidence` 최신 세션 재검증까지 기록표 기준으로 닫혔다
- 따라서 현재 저장소는 문서 판정 체계상 `완료됨` 상태이며, Round 7 최종 승격까지 동기화가 완료됐다. 2026-04-10 최신 세션에서 `https://metanova1004.com/api/admin/orchestrator/capabilities/summary`, `.../code-generator`, `/admin/llm?capability=code-generator`, `/api/admin/system-settings`, `/api/admin/workspace-self-run-record?latest=true`, `wss://metanova1004.com/api/llm/ws` 운영 실검증 2회, `https://xn--114-2p7l635dz3bh5j.com/api/auth/login` 및 `/api/marketplace/projects?skip=0&limit=24&sort_by=downloads&sort_order=desc` 2회 실검증, `PLAYWRIGHT_ADMIN_BASE_URL=https://metanova1004.com npm --prefix frontend/frontend run e2e -- admin-passkey-operational.playwright.spec.ts` 기준 관리자 패스키 등록·로그인 2회 실검증도 모두 통과했다.

---

## 🎯 프로젝트 개요

**개발분석114.com**은 AI 기반 프로젝트 분석 및 관리 플랫폼입니다:

- 🧠 **듀얼 브레인 시스템** (A뇌 + B뇌)
- 📊 **20+ 태스크 자동 분해**
- 📈 **5가지 플로우차트**
- 🤖 **Level 3 완전 자율 시스템**
- 🛒 **AI 프로젝트 마켓플레이스**
- 🔥 **양자화 LLM (16-22B)**
- 🐳 **Docker 프로덕션 환경**

---

## ✨ 주요 기능

### 1. **프로젝트 분해 시스템**

- 최소 20개 이상 원자적 태스크 생성
- 8가지 카테고리 자동 분류
- 의존성 분석 및 크리티컬 패스
- A뇌/B뇌 최적 할당

### 2. **Level 3 자율 시스템**

- 관리자 운영 진단 패널
- 오케스트레이터 런타임 설정/실행 제어
- 작업 이력 및 자기 실행 승인 흐름
- 시스템 상태/리소스 헬스 점검
- self-run worker runtime artifact 강제 생성 및 상태 추적
- worker 로그 진행 마커(`worker_boot`, `worker_dispatch`, `context_prepare_*`, `orchestrator_*`, `python_fallback_*`, `approval_finalized`, `worker_finalize`) 기록

> 주의: 과거 문서에 있던 독립 `autonomous` 모듈과 `/api/autonomous/*` API는 현재 저장소 기준으로 별도 노출돼 있지 않습니다. 현재 운영 기능은 관리자/오케스트레이터 경로 중심입니다.
>
> 현재 보류 항목: self-run 최종 종료 자체가 아니라 Round 7 최종 승격 전 같은 세션 기준 문서/기준선 재점검입니다.

### 3. **AI 프로젝트 마켓플레이스** 🆕

- 프로젝트 업로드/다운로드
- 기본 구매/다운로드 플로우
- 리뷰/평점 데이터 및 백엔드 API
- MinIO 파일 스토리지

> 현재 구현 기준:
>
> - README 과거 버전에 있던 `토스페이먼츠/이니시스`, `라이센스 관리 (MIT/Commercial/Personal)`는 현재 저장소 기준으로 실구현 상태가 아닙니다.
> - 현재 구매/결제 흐름은 실결제 연동이 아니라 시뮬레이션 결제 완료 반영 방식입니다.
> - 리뷰 API와 공개 상세 페이지의 리뷰 작성/조회 UI가 연결되어 있으며, 공개 리뷰 플로우 검증이 완료되었습니다.

### 4. **양자화 LLM 통합** 🆕

- GGUF Q5_K_M 포맷 후보 모델 지원
- RTX 5090 기준 Ollama 32B 런타임 우선
- GGUF 직접 보관 + Ollama 태그 등록 방식
- 관리자 런타임 설정 연동

> 현재 저장소에는 `scripts/setup_llm.sh` 자동 다운로드 스크립트가 포함돼 있지 않습니다. 현재 문서는 Ollama 태그 기반 운영을 기준으로 봐야 합니다.

### 5. **인증 & 보안**

- JWT (Access + Refresh Token)
- bcrypt 비밀번호 해싱
- 외부 엔진 연동용 API 키 설정
- 관리자/일반 사용자 권한 분리
- Rate Limiting
- Audit Logging

### 6. **소비자/관리자 대시보드 고도화** 🆕

- 소비자 대시보드: KPI 카드, 최근 활동 피드, 빠른 액션 패널
- 관리자 대시보드: 운영 알림 패널, 상위 프로젝트 검색, 관리자 액션 버튼
- 기존 API 기반 실시간 상태 반영 (`/api/health`, `/api/llm/status`, 마켓 통계 API)

---

## 🚀 빠른 시작 (현재 저장소 기준)

### 1. 프로젝트 클론

```bash
git clone https://github.com/your-repo/devanalysis114.git
cd devanalysis114
```

### 2. 현재 운영 스택 시작

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\start_all_in_one.ps1
```

> 필수: 시작 전에 `FIXED_ADMIN_PASSWORD`를 `.env` 또는 PowerShell 환경변수에 설정해야 합니다.

```powershell
$env:FIXED_ADMIN_PASSWORD = '원하는-강한-관리자-비밀번호'
powershell -ExecutionPolicy Bypass -File .\scripts\start_all_in_one.ps1
```

**현재 확인 가능한 운영 절차**:

1. 환경 변수 설정
2. backend/postgres/redis/qdrant 및 compose 프론트 스택 시작
3. 고정 관리자 계정 보장
4. 핵심 URL 헬스체크

### 2-1. 현재 docker compose 기준 서비스

현재 `docker-compose.yml` 기준으로 직접 올라오는 서비스는 아래와 같습니다.

| 서비스 | 구분 | 기본 포트/접근 | 비고 |
|---|---|---|---|
| `postgres` | 필수 | 내부 서비스 | 메인 DB |
| `redis` | 필수 | 내부 서비스 | 작업 큐/렌더 큐 |
| `qdrant` | 선택적 연결 | `6333` | 벡터 저장소. 현재 고급 검색 공개 라우트는 제거됨 |
| `backend` | 필수 | `8000` | FastAPI |
| `video-worker` | 필수 | 내부 서비스 | 광고/영상 렌더 worker |
| `frontend-marketplace` | 필수 | 내부 서비스 | 공개 메인 Next.js 런타임 |
| `frontend-admin` | 필수 | 내부 서비스 | 관리자 Next.js 런타임 |
| `nginx` | 게이트웨이 | `8080` / `8443` (`.env` override 가능) | compose 내부 nginx |

> 현재 compose에는 `Grafana`, `MinIO` 컨테이너가 포함되어 있지 않습니다.
> `MinIO`는 `host.docker.internal:9000` 외부 의존성으로 연결합니다.

### 3. 운영 상태 점검

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\ops_health_check.ps1
```

### 4. 접속

- **API 문서**: <http://127.0.0.1:8000/docs>
- **Qdrant**: <http://127.0.0.1:6333>
- **공개 메인 진입 URL**: <https://metanova1004.com/marketplace>
- **관리자 기본 진입 URL**: <https://metanova1004.com/admin>
- **Marketplace (Frontend Dev, 선택)**: `http://127.0.0.1:3000/marketplace`
- **Admin Dashboard (Frontend Dev, 선택)**: `http://127.0.0.1:3005/admin`
- **프로덕션**: <https://개발분석114.com>

외부 의존성:

- **MinIO API**: `host.docker.internal:9000`
- **MinIO Console**: 환경에 따라 별도 운영 (현재 compose 미포함)

> 운영 상태(실측 기준): 현재 직접 검증된 기본 진입 URL은 `https://metanova1004.com/marketplace`, `https://metanova1004.com/admin`입니다.
>
> 성능 검증 기준: 로컬 백엔드 성능 측정은 `http://127.0.0.1:8000` 또는 운영 도메인 기준으로만 기록합니다. `localhost` 는 호스트 연결 편차가 커서 성능 회귀 기준에서 제외합니다.
>
> 포트 기준: 운영 경로는 compose 프런트 컨테이너를 nginx가 라우팅합니다. 로컬 Next 개발 서버는 `3000`(Marketplace) / `3005`(Admin Dashboard) 직접 개발용으로만 사용하고, 백엔드는 `8000`, Qdrant는 `6333`입니다. `3001`은 사용하지 않습니다.
>
> compose 내부 nginx 포트는 기본값 기준 `8080`/`8443`이며, `.env`로 override될 수 있습니다. 별도 호스트 nginx/포트 포워딩이 활성화된 환경에서는 실측 기준 기본 진입 URL(`443`)이 우선입니다.
>
> 기준 진입 URL: 공개 메인은 `https://metanova1004.com/marketplace`, 관리자 전용은 `https://metanova1004.com/admin`입니다. 개발 서버 직접 진입은 각각 `http://127.0.0.1:3000/marketplace`, `http://127.0.0.1:3005/admin`을 사용합니다.

### 운영자 실행 명령 요약

운영자는 아래 표 기준만 사용합니다.

| 목적 | 표준 명령 | 비고 |
|---|---|---|
| 전체 시작 | `npm run start:platform` | backend + worker + frontend + nginx |
| 전체 중지 | `npm run stop:platform` | 전체 플랫폼 종료 |
| 상태 점검 | `npm run health:platform` | 운영 헬스체크 |
| 관리자 계정 보장 | `npm run ensure:admin` | `FIXED_ADMIN_PASSWORD` 필요 |
| 백엔드만 시작 | `npm run start:backend` | DB/redis/qdrant/minio/backend/worker |
| 백엔드만 중지 | `npm run stop:backend` | 백엔드 스택만 종료 |
| 관리자 프런트 빌드 | `npm run build:admin` | `frontend/frontend` 빌드 |
| 프런트 빌드 | `npm run build:frontend` | 현재는 `frontend/frontend` 기준 |

### 운영 기준 메모

- 평소 운영 시작: `npm run start:platform`
- 평소 운영 종료: `npm run stop:platform`
- 장애 확인: `npm run health:platform`
- 부분 작업이 아니면 `start:backend` 단독 사용보다 `start:platform`을 우선합니다.

### 하위 호환 별칭

- `npm run start:all` → `npm run start:platform`
- `npm run stop:all` → `npm run stop:platform`
- `npm run health:ops` → `npm run health:platform`

### 제거된 잘못된 명령

- `npm run start:frontend-dual`
- `npm run stop:frontend-dual`
- `npm run build:marketplace`

### 운영용 / 개발용 설정 차이

| 항목 | 운영/게이트웨이 기준 | 개발 직접 진입 기준 |
|---|---|---|
| 공개 메인 | `https://metanova1004.com/marketplace` | `http://127.0.0.1:3000/marketplace` |
| 관리자 | `https://metanova1004.com/admin` | `http://127.0.0.1:3005/admin` |
| 백엔드 API | nginx 또는 도메인 경유 | `http://127.0.0.1:8000` |
| nginx 포트 | compose 기본 `8080`/`8443`, 환경에 따라 host gateway `443` 가능 | 미사용 |
| 프런트 실행 | compose 내부 `frontend-marketplace` / `frontend-admin` 사용 | 필요 시 host dev server 직접 사용 |
| 결제 | 시뮬레이션 완료 반영 흐름 | 동일 |

### 운영 비밀값 분리 / 외부 엔진 API 키 관리

- 운영 비밀값은 더 이상 `.env`에 실제 값 자체를 두는 방식만 전제하지 않는다.
- backend는 아래 항목에 대해 `환경변수명` 또는 `환경변수명_FILE` 방식을 지원한다.
  - `MINIO_ACCESS_KEY` / `MINIO_ACCESS_KEY_FILE`
  - `MINIO_SECRET_KEY` / `MINIO_SECRET_KEY_FILE`
  - `PAYMENT_API_KEY` / `PAYMENT_API_KEY_FILE`
  - `VIDEO_EXTERNAL_API_KEY` / `VIDEO_EXTERNAL_API_KEY_FILE`
  - `VIDEO_DEDICATED_ENGINE_API_KEY` / `VIDEO_DEDICATED_ENGINE_API_KEY_FILE`
- JWT 비밀키는 `SECRET_KEY` 또는 `SECRET_KEY_FILE` 방식으로 운영한다.
- 관리자 계정 고정 비밀번호는 `.env`가 아니라 OS 환경변수 `FIXED_ADMIN_PASSWORD`로 주입한다.

권장 방식:

1. 실제 비밀값은 운영 호스트의 외부 파일 또는 시스템 환경변수에 저장
2. `.env`에는 필요 시 파일 경로(`*_FILE`)만 기록
3. 외부 엔진 API 키는 backend 컨테이너만 읽도록 제한
4. 프런트 컨테이너에는 외부 엔진 API 키를 주입하지 않음

예시:

```dotenv
SECRET_KEY_FILE=D:/secrets/codeai/jwt_secret.txt
MINIO_SECRET_KEY_FILE=D:/secrets/codeai/minio_secret.txt
VIDEO_DEDICATED_ENGINE_API_KEY_FILE=D:/secrets/codeai/dedicated_engine_api_key.txt
VIDEO_EXTERNAL_API_KEY_FILE=D:/secrets/codeai/video_external_api_key.txt
```

운영 원칙:

- 외부 엔진 API 키는 `backend`/`video-worker`에서만 사용
- 관리자 UI는 키 값을 직접 보관하지 않고, backend의 시스템 설정/프록시 경로로만 접근
- 키 교체 시 `.env`의 실제 비밀 문자열을 수정하지 말고 외부 파일 내용을 교체하거나 시스템 환경변수를 갱신

### 롤백 절차

장애 시 우선순위는 `프런트 프로세스 → nginx → backend/video-worker → 전체 스택` 순서로 최소 범위 복구입니다.

1. **프런트 dev server만 재기동**

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\stop_frontend_dual.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\start_frontend_dual.ps1
```

1. **nginx 게이트웨이만 재기동**

```powershell
docker compose stop nginx
docker compose up -d nginx
```

1. **backend / video-worker 재기동**

```powershell
docker compose up -d --build backend video-worker
```

1. **전체 운영 진입 복구**

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\stop_all_in_one.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\start_all_in_one.ps1
```

1. **복구 확인**

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\ops_health_check.ps1
```

### 백업 / 로그 / 오류 추적 경로

| 구분 | 실제 경로/위치 | 설명 |
|---|---|---|
| Postgres 데이터 | Docker volume `codeai_postgres-data` | 운영 DB 영속 볼륨 |
| Qdrant 데이터 | Docker volume `codeai_qdrant-data` | 벡터 저장소 영속 볼륨 |
| 업로드 루트 | 호스트 `./uploads` → 컨테이너 `/app/uploads` | `MARKETPLACE_HOST_ROOT` 미지정 시 기본 경로 |
| Marketplace 로컬 파일 | `uploads/marketplace_local` | MinIO fallback 및 샘플 ZIP 보관 |
| 고객 self-run 결과 | `uploads/projects/customer_<user_id>/runs/<project>` | publish/재실행 대상 산출물 루트 |
| self-run 임시/최종 비디오 | `uploads/tmp/final_video_outputs` 또는 job `output_dir` | ffmpeg 최종 출력 및 로그 |
| self-run ffmpeg 로그 | `<output_dir>/<basename>.log` | API `log_path`와 동일한 실제 호스트 파일 |
| 프런트 PID/런타임 정보 | `.runtime/*.pid` | `start_frontend_dual.ps1` / `stop_frontend_dual.ps1` 사용 |
| TLS 인증서 | `certbot/local-certs` | nginx 로컬 인증서 마운트 |
| backend 코드/컨테이너 마운트 | `./backend` → `/app/backend` | 재빌드/재기동 시 반영 |
| feature execution 추적 | DB 테이블 `feature_execution_logs` | `trace_id`, `flow_id`, `step_id`, `action` 기록 |
| feature retry 추적 | DB 테이블 `feature_retry_queue` | 재시도 큐/오류 기록 |
| customer completion 추적 | DB 테이블 `customer_orchestrator_completions` | `output_dir`, `gate_passed`, `override_used` 기록 |

백업 예시:

```powershell
# Postgres dump
docker exec -e PGPASSWORD=changeme devanalysis114-postgres pg_dump -U admin -d devanalysis114 > .\reports\devanalysis114-backup.sql

# uploads 스냅샷
Compress-Archive -Path .\uploads -DestinationPath .\reports\uploads-backup.zip -Force
```

### 구현 상태 표

| 항목 | 상태 | 설명 |
|---|---|---|
| 인증/로그인 | 구현됨 | 실제 로그인/리다이렉트 검증 완료 |
| 마켓 목록/상세 | 구현됨 | 실제 페이지 렌더 및 상세 진입 검증 완료 |
| 업로드 | 구현됨 | 실제 업로드 후 상세 생성 검증 완료 |
| 구매/다운로드 | 구현됨 | 구매 생성, 완료 반영, 다운로드 토큰 발급 검증 완료 |
| 결제 | 보류(시뮬레이션) | 실결제 아님. 완료 반영 페이지 기반 |
| 리뷰 공개 UI | 구현됨 | 공개 상세 페이지에서 리뷰 조회/통계/작성 폼 연결 및 검증 완료 |
| 고급 검색 | 제거 완료 | 미등록 경로/파일 제거 |
| Stripe 실결제 | 제거 완료 | 미등록 경로/서비스 제거 |

### 단일 기동 명령 (전체 동시 실행)

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\start_all_in_one.ps1
```

위 명령은 아래를 순차 실행합니다.

1. 고정 관리자 계정 보장 + backend/postgres 기동
2. 프론트 재기동 (marketplace=`3000`, admin=`3005`)
3. 핵심 URL 헬스체크 출력

### 로컬 HTTPS 인증서 경고 없이 접속하기 (Windows)

`localhost`는 로컬 인증서 SAN에 포함되어 있지 않아 경고가 날 수 있습니다. 관리자 PowerShell에서 아래를 1회 실행하세요.

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup_local_domain_hosts.ps1
```

실행 후 접속:

- <https://metanova1004.com/admin>
- <https://api.xn--114-2p7l635dz3bh5j.com/docs>

### 공인 SSL 발급/갱신

현재 nginx는 `certbot/local-certs/fullchain.pem`, `privkey.pem`을 읽습니다. 공인 인증서 발급 스크립트는 Certbot으로 실제 인증서를 발급/갱신한 뒤 이 경로로 자동 동기화하고 nginx를 reload합니다.

사전 조건:

1. `metanova1004.com`, `개발분석114.com`, `api.개발분석114.com` DNS가 현재 서버 공인 IP를 가리켜야 합니다.
2. 외부에서 `80`, `443` 포트가 현재 서버까지 도달해야 합니다.
3. `.env`의 `SSL_EMAIL`이 실제 수신 가능한 메일이어야 합니다.

실행 예시:

```powershell
# 실제 발급
powershell -ExecutionPolicy Bypass -File .\scripts\manage_public_tls_cert.ps1 -CertAction issue

# Let's Encrypt staging으로 사전 검증
powershell -ExecutionPolicy Bypass -File .\scripts\manage_public_tls_cert.ps1 -CertAction issue -UseStaging

# 명령만 확인
powershell -ExecutionPolicy Bypass -File .\scripts\manage_public_tls_cert.ps1 -CertAction issue -DryRun

# 기존 인증서 갱신
powershell -ExecutionPolicy Bypass -File .\scripts\manage_public_tls_cert.ps1 -CertAction renew
```

운영 관찰 메모:

1. 2026-03-13 시점 공용 DNS(8.8.8.8)에서는 `metanova1004.com`, `xn--114-2p7l635dz3bh5j.com`, `api.xn--114-2p7l635dz3bh5j.com` 모두 `211.218.172.124`로 해석됐습니다.
2. 같은 시점 현재 PC의 hosts에는 `metanova1004.com`만 `127.0.0.1`로 고정돼 있어, 이 PC에서 보는 응답은 공용 경로와 다를 수 있습니다.

---

## 📦 시스템 구성

### Docker 서비스 (현재 compose 기준 6개 + 외부 의존)

| 서비스 | 포트 | 설명 |
| --- | --- | --- |
| PostgreSQL | 5432 | 메인 데이터베이스 |
| Redis | 6379 | 캐시 & 세션 |
| Qdrant | 6333 | 벡터 DB |
| Backend | 8000 | FastAPI (GPU 지원) |
| Video Worker | 내부 | 광고 비디오 큐 소비용 워커 |
| Nginx | 8080, 8443 | 리버스 프록시 + SSL |

외부/별도 의존:

- MinIO
- Certbot 인증서 경로
- Grafana / Prometheus (현재 루트 compose 기준 기본 포함 아님)

### 프로젝트 구조

```text
개발분석114.com/
├── backend/
│   ├── main.py                    # FastAPI 앱 진입점
│   ├── auth_router.py             # 인증 API
│   ├── admin_router.py            # 관리자 API
│   ├── video_api_router.py        # 비디오 호환 API
│   ├── llm/                       # 오케스트레이터/LLM 제어
│   ├── marketplace/               # 마켓플레이스 도메인
│   └── image/                     # 이미지 생성 API
├── frontend/                      # Marketplace Next.js 앱
│   └── frontend/                  # Admin Next.js 앱
├── scripts/
│   ├── start_all_in_one.ps1       # 전체 시작
│   ├── stop_all_in_one.ps1        # 전체 중지
│   ├── ops_health_check.ps1       # 운영 점검
│   └── ensure_fixed_admin_account.ps1
├── docker-compose.yml
├── Dockerfile.backend
└── README.md
```

---

## 🤖 LLM 모델 (자동 다운로드)

### GGUF 다운로드 후보 모델

| 모델 | 크기 | 특징 | 추천도 |
| --- | --- | --- | --- |
| **Qwen2.5-Coder-14B** | 10.2GB | 코딩 특화 | ⭐⭐⭐⭐⭐ |
| Mistral-Nemo-12B | 8.5GB | 범용 추론 | ⭐⭐⭐⭐ |
| Yi-1.5-16B | 11.3GB | 다국어 | ⭐⭐⭐⭐ |
| CodeLlama-20B | 14.1GB | 코드 생성 | ⭐⭐⭐⭐ |

현재 구현 기준:

- 위 표의 모델들은 GGUF 다운로드 후보입니다.
- 현재 백엔드 런타임은 `LLM_MODEL_PATH`를 직접 읽는 llama.cpp 경로가 아니라 Ollama 모델 태그를 사용합니다.
- 따라서 GGUF 파일만 내려받아 `models/`에 두면 바로 적용되지 않고, Ollama에 별도 등록해야 런타임에서 사용됩니다.
- RTX 5090 현행 기본값은 `qwen2.5-coder:32b`이며, 초기 14B/20B 구성으로 운영하려면 해당 GGUF를 Ollama 태그로 등록하거나 llama.cpp 로더를 새로 구현해야 합니다.

### LLM 준비

현재 저장소에는 자동 설치 스크립트가 포함돼 있지 않습니다.

현재 기준 준비 방식:

1. Ollama에 사용할 모델 태그를 준비합니다.
2. 런타임 설정 또는 `.env`에서 해당 태그를 참조하도록 맞춥니다.
3. GGUF 파일을 직접 보관하는 경우에도, 바로 사용되는 것이 아니라 Ollama 등록 또는 별도 로더 구현이 필요합니다.
4. GPU 검증

---

## 🛒 마켓플레이스 사용 가이드

### 판매자 (프로젝트 업로드)

```bash
# 1. 로그인
POST /api/auth/login

# 2. 프로젝트 업로드
POST /api/marketplace/projects
- title: "AI 챗봇 시스템"
- price: 50000 (KRW)
- category_id: 1
- file: project.zip

# 3. 파일 업로드
POST /api/marketplace/projects/{id}/upload
```

### 구매자 (프로젝트 구매)

```bash
# 1. 프로젝트 검색
GET /api/marketplace/projects?search=AI&category=backend

# 2. 상세 정보
GET /api/marketplace/projects/{id}

# 3. 구매
POST /api/marketplace/purchase
- project_id: 123
- payment_method: "card"

# 4. 다운로드
GET /api/marketplace/download/{token}
```

### 리뷰 작성

```bash
 POST /api/marketplace/projects/{project_id}/reviews
- project_id: 123
- rating: 5
- comment: "훌륭한 프로젝트입니다!"
```

---

## 🔧 설정

### 환경 변수 (.env)

```bash
# Domain
DOMAIN_NAME=xn--114-2p7l635dz3bh5j.com
DOMAIN_ORIGINAL=개발분석114.com

# Database
POSTGRES_PASSWORD=your-secure-password

# JWT
JWT_SECRET_KEY=your-jwt-secret

# MinIO
MINIO_ROOT_PASSWORD=your-minio-password

# LLM GGUF 원본 파일(보관/등록용)
LLM_MODEL_PATH=./models/qwen2.5-coder-32b-instruct-q5_k_m.gguf
LLM_GPU_LAYERS=50
LLM_CONTEXT_SIZE=8192

# LLM 라우팅 기본값 (RTX 5090: 32B 우선)
LLM_MODEL_DEFAULT=qwen2.5-coder:32b
LLM_MODEL_REASONING=qwen2.5-coder:32b
LLM_MODEL_CODING=qwen2.5-coder:32b
LLM_MODEL_CHAT=qwen2.5-coder:32b
LLM_MODEL_VOICE_CHAT=qwen2.5-coder:32b
LLM_MODEL_PLANNER=qwen2.5-coder:32b
LLM_MODEL_CODER=qwen2.5-coder:32b
LLM_MODEL_REVIEWER=qwen2.5-coder:32b
LLM_MODEL_DESIGNER=qwen2.5-coder:32b
LLM_MODEL_SMART_PLANNER=qwen2.5-coder:32b
LLM_MODEL_SMART_EXECUTOR=qwen2.5-coder:32b
LLM_MODEL_SMART_DESIGNER=qwen2.5-coder:32b

# 역할별 권장 분리
# - 추론: LLM_MODEL_REASONING
# - 코딩: LLM_MODEL_CODING
# - 일반 챗봇: LLM_MODEL_CHAT
# - 음성 챗봇 응답: LLM_MODEL_VOICE_CHAT
# - 세부 오케스트레이터/스마트 라우터는 위 값들을 기본 fallback으로 사용

# 오케스트레이터 강제 완성(부분 산출물 금지)
ORCH_FORCE_COMPLETE=true
ORCH_MIN_FILES=10
ORCH_MIN_DIRS=2
ORCH_MAX_FORCE_RETRIES=99
ORCH_REQUIRED_FILES=README.md,requirements.txt,app/main.py,app/routes.py,tests/test_health.py,.gitignore
```

### 한글 도메인 설정

**DNS 설정** (가비아/호스팅케이알):

```text
A 레코드:
  호스트: @
  값: [서버 IP]

CNAME 레코드:
  호스트: www
  값: xn--114-2p7l635dz3bh5j.com
```

### 전용 영상 엔진 Production 설정 (고정 JSON 계약)

- 계약 파일: `backend/marketplace/contracts/dedicated_engine_contract.v1.json`
- 목적: 하드웨어 전용 엔진 구축 후 백엔드에 즉시 연동 가능한 고정 규격
- 고정 정책: `duration_seconds=60`, `cut_seconds=10`, `cut_count=6`

권장 환경 변수:

```bash
VIDEO_DEDICATED_ENGINE_URL=https://video-engine.your-domain.com/dedicated
VIDEO_DEDICATED_ENGINE_API_KEY=replace-with-token
VIDEO_DEDICATED_SUBMIT_PATH=/jobs
VIDEO_DEDICATED_TIMEOUT_SEC=900
VIDEO_DEDICATED_POLL_SEC=2
VIDEO_ENGINE_FALLBACK_TO_INTERNAL=false
VIDEO_REQUIRE_GENERATIVE_ENGINE=true
VIDEO_ALLOW_LOCAL_DEDICATED_ENGINE=false
```

주의:

- `VIDEO_REQUIRE_GENERATIVE_ENGINE=true`이면 `localhost`, `127.0.0.1`, `host.docker.internal:18081`, `mock` 엔진 URL은 거부됩니다.
- 즉, 실사용 장면 재현형 광고를 위해 실제 text-to-video 전용 엔진 URL을 반드시 설정해야 합니다.

자체 엔진(로컬/사내 서버) 모드:

- 자체 서버를 dedicated 엔진으로 사용할 때는 아래 값을 사용합니다.

```bash
VIDEO_DEDICATED_ENGINE_URL=http://host.docker.internal:18082
VIDEO_DEDICATED_SUBMIT_PATH=/jobs
VIDEO_ALLOW_LOCAL_DEDICATED_ENGINE=true
VIDEO_REQUIRE_GENERATIVE_ENGINE=true
```

- 자체 엔진 서버 실행(파워셸 컷 분할 + concat 파이프라인):

```powershell
erpowshell -ExecutionPolicy Bypass -File .\scripts\start_self_dedicated_engine.ps1 -Port 18082
```

- 얼굴 기준 고정 + 스타일 자동 생성(권장):

```bash
SELF_ENGINE_REQUIRE_FACE_IMAGE=true
SELF_ENGINE_MAX_RETRY=2
SELF_ENGINE_MIN_VIDEO_BYTES=200000
SELF_ENGINE_MIN_VIDEO_BYTES_PER_SEC=8000
SELF_ENGINE_MIN_DURATION_RATIO=0.75
SELF_ENGINE_MIN_DURATION_SECONDS=2.0
```

- 위 설정은 얼굴 기준 이미지가 없는 주문을 즉시 실패시키고, 렌더 결과가 너무 짧거나 파일이 비정상적으로 작은 경우 자동 재시도를 수행합니다. 파일 크기 검증은 `기본 최소 바이트`와 `초당 최소 바이트`를 함께 사용하므로 5초 샘플과 60초 본편에 같은 고정 임계값을 강제하지 않습니다.

- 오케스트레이터 생성물(폴더/파일) D드라이브 고정 저장:

```bash
MARKETPLACE_HOST_ROOT=D:/marketplace
MARKETPLACE_UPLOAD_ROOT=/app/uploads
MARKETPLACE_RETENTION_DAYS=30
MARKETPLACE_TEMP_RETENTION_DAYS=7
AD_DOWNLOAD_MIN_NOTICE_MINUTES=60
AD_DOWNLOAD_WINDOW_DAYS=30
AD_DOWNLOAD_MAX_COUNT=2
```

- `docker-compose.yml`에서 `${MARKETPLACE_HOST_ROOT}:/app/uploads`로 바인딩되며,
  고객 오케스트레이터 결과물은 `D:/marketplace/projects/customer_{user_id}/...` 경로에 자동 생성됩니다.
- 사용자 다운로드 자산(프로젝트/산출물)은 `MARKETPLACE_RETENTION_DAYS` 기준(기본 30일)으로 보관됩니다.
- 임시 파일은 컨테이너 `/tmp` 대신 `/app/uploads/tmp`를 사용하고,
  `MARKETPLACE_TEMP_RETENTION_DAYS` 기준(기본 7일)으로 정리되어 컨테이너 writable layer 잔여를 최소화합니다.
- 광고 영상 다운로드는 품질/운영 정책으로 주문 후 `AD_DOWNLOAD_MIN_NOTICE_MINUTES`(기본 60분) 이후부터 가능하며,
  `AD_DOWNLOAD_WINDOW_DAYS`(기본 30일) 이내, `AD_DOWNLOAD_MAX_COUNT`(기본 2회)로 제한됩니다.

- 생성형 엔진 모드 추가(옵션):

```bash
SELF_ENGINE_GENERATIVE_PROVIDER=local-diffusers
SELF_ENGINE_GENERATIVE_ENABLED=true
SELF_ENGINE_GENERATIVE_FALLBACK_COMPOSITOR=true
SELF_ENGINE_GENERATIVE_SUBMIT_URL=https://your-generative-engine/api/jobs
SELF_ENGINE_GENERATIVE_STATUS_URL_TEMPLATE=https://your-generative-engine/api/jobs/{job_id}
SELF_ENGINE_GENERATIVE_API_KEY=replace-with-token
SELF_ENGINE_GENERATIVE_TIMEOUT_SEC=1200
SELF_ENGINE_GENERATIVE_POLL_SEC=3
SELF_ENGINE_GENERATIVE_VIDEO_URL_FIELD=video_url
SELF_ENGINE_LOCAL_VIDEO_PIPELINE=i2vgen-xl
SELF_ENGINE_LOCAL_VIDEO_MODEL_ID=ali-vilab/i2vgen-xl
SELF_ENGINE_LOCAL_VIDEO_WIDTH=704
SELF_ENGINE_LOCAL_VIDEO_HEIGHT=512
SELF_ENGINE_LOCAL_VIDEO_NUM_FRAMES=24
SELF_ENGINE_LOCAL_VIDEO_STEPS=20
SELF_ENGINE_LOCAL_VIDEO_GUIDANCE=9.0
SELF_ENGINE_LOCAL_VIDEO_MAX_UNIQUE_CLIPS=3
SELF_ENGINE_LOCAL_VIDEO_PAD_TO_CUT=true
SELF_ENGINE_LOCAL_VIDEO_OUTPUT_FPS=30
```

- 동작 방식:
  - `SELF_ENGINE_GENERATIVE_PROVIDER=local-diffusers`이면 같은 서버 GPU에서 Hugging Face Diffusers 기반 image-to-video 모델을 직접 실행
  - 기본 공개 모델은 `ali-vilab/i2vgen-xl`이며, gated 저장소 인증 없이 바로 내려받아 실행 가능
  - 공개 비디오 API의 `delivery_profile=general`은 `30fps` 기준, `delivery_profile=youtube_web`은 `60fps` 기준으로 출력 프리셋을 전달
  - 내부적으로는 실제 모델 소스 프레임보다 출력 FPS와 프레임 보간을 우선 제어해 더 부드러운 결과를 만들고, 합성 경로는 전체 프레임 기준의 일관된 배경 구도를 우선 사용
  - 1분 모드 기본값은 `5초 컷 x 12개` 타임라인을 만들되, 서버 부하를 낮추기 위해 기본적으로 최대 3개의 고유 컷만 생성하고 반복 배치하여 60초를 구성
  - 생성형 엔진 ON 시 먼저 생성형 경로를 실행
  - 실패 시 `SELF_ENGINE_GENERATIVE_FALLBACK_COMPOSITOR=true`이면 기존 합성 파이프라인으로 자동 폴백

- 서버 구현 파일:
  - `scripts/self_dedicated_engine_server.py`
  - `scripts/self_dedicated_chunk_pipeline.ps1`

템플릿 파일:

- `.env.production.example`

연동 확인:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\test_ad_video_order_e2e.ps1 -EngineType dedicated_engine -MaxWaitSec 600
```

---

## 📊 API 엔드포인트

### 인증 (12개)

- `POST /api/auth/register` - 회원가입
- `POST /api/auth/login` - 로그인
- `POST /api/auth/refresh` - 토큰 갱신
- `GET /api/auth/me` - 내 정보
- 외 8개...

### 마켓플레이스 (8개)

- `POST /api/marketplace/projects` - 프로젝트 업로드
- `GET /api/marketplace/projects` - 프로젝트 목록
- `POST /api/marketplace/purchase` - 구매
- `GET /api/marketplace/download/{token}` - 다운로드
- 외 4개...

### 관리자/오케스트레이터 관련 API

- `GET /api/admin/system-settings` - 운영 설정 조회
- `POST /api/admin/workspace-self-run` - 관리자 자기 실행 요청
- `POST /api/llm/orchestrate` - 오케스트레이터 실행
- `GET /api/llm/runtime-config` - 런타임 설정 조회

**전체 API 문서**: <http://127.0.0.1:8000/docs>

---

## 🎓 사용 예시

### Python SDK

```python
import requests

# 로그인
response = requests.post("http://127.0.0.1:8000/api/auth/login", json={
    "username": "user@example.com",
    "password": "password"
})
token = response.json()["access_token"]

# 프로젝트 업로드
files = {"file": open("project.zip", "rb")}
data = {
    "title": "AI 챗봇",
  "price": 50000,
  "category_id": 1
}
headers = {"Authorization": f"Bearer {token}"}

response = requests.post(
    "http://127.0.0.1:8000/api/marketplace/projects",
    files=files,
    data=data,
    headers=headers
)

print(response.json())
```

---

## 🔐 보안

### 구현된 보안 기능

- ✅ JWT 인증 (Access + Refresh)
- ✅ bcrypt 비밀번호 해싱
- ✅ SQL Injection 방지
- ✅ XSS 방지
- ✅ CSRF 방지
- ✅ Rate Limiting
- ✅ Input Validation
- ✅ Security Headers
- ✅ Audit Logging
- ✅ 외부 엔진 연동용 API 키 설정
- ✅ HTTPS (Let's Encrypt)

---

## 📈 모니터링

### Grafana 대시보드

- **시스템 메트릭**: CPU, 메모리, GPU, 디스크
- **API 성능**: 응답 시간, 처리량, 에러율
- **사용자 활동**: 로그인, 프로젝트 업로드, 구매
- **자율 시스템**: 진단, 개선, 배포 이벤트

**접속**: <http://127.0.0.1:3000>  
**기본 계정**: admin / admin

---

## 🐛 트러블슈팅

### Q: GPU가 인식되지 않습니다

```bash
# NVIDIA Docker 설치 확인
nvidia-smi
docker run --rm --gpus all nvidia/cuda:12.1-base nvidia-smi
```

### Q: 포트가 이미 사용 중입니다

```bash
# 사용 중인 포트 확인
sudo lsof -i :8000
# 프로세스 종료
sudo kill -9 <PID>
```

### Q: 데이터베이스 연결 실패

```bash
# PostgreSQL 상태 확인
docker compose logs postgres
docker compose restart postgres
```

---

## 📝 라이센스

MIT License - 자유롭게 사용, 수정, 배포 가능합니다.

---

## 🤝 기여

이슈 및 PR은 언제나 환영합니다!

---

## 📞 지원

- **문서**: [docs/](docs/)
- **API 문서**: <http://127.0.0.1:8000/docs>
- **이슈**: GitHub Issues
- **이메일**: support@개발분석114.com

---

## 🌟 로드맵

- [ ] 음성 입출력 (Whisper + TTS)
- [ ] 멀티모달 (이미지 이해/생성)
- [ ] React Native 모바일 앱
- [ ] RAG 시스템 (벡터 검색)
- [ ] 플러그인 시스템 확장
- [ ] 팀 협업 기능

---

### 💜 개발분석114.com - AI가 스스로 진화하는 미래

Made with ❤️ by Level 3 Autonomous AI

---

## 🧰 운영/테스트 명령어 모음 (최신)

지금까지 전체 작업에서 사용한 명령어를 카테고리별로 정리해 드립니다.

### 가장 먼저 보는 단일 명령 4개

```powershell
# 1) 전체 시작
powershell -ExecutionPolicy Bypass -File ".\scripts\start_all_in_one.ps1"

# 2) 전체 중지
powershell -ExecutionPolicy Bypass -File ".\scripts\stop_all_in_one.ps1"

# 3) 운영 점검
powershell -ExecutionPolicy Bypass -File ".\scripts\ops_health_check.ps1"

# 4) 고정 관리자 계정 보장(필요 시)
powershell -ExecutionPolicy Bypass -File ".\scripts\ensure_fixed_admin_account.ps1"
```

권장 사용 순서:

1. 처음 띄울 때 `start_all_in_one.ps1`
2. 동작 이상 시 `ops_health_check.ps1`
3. 종료 시 `stop_all_in_one.ps1`

---

## 📦 1. Docker 관련 명령어

### 컨테이너 관리

```powershell
# 작업 디렉토리 이동 (항상 먼저 실행)
Set-Location C:\Users\WORK\source\repos\parkcheolhong\codeAI

# 전체 중지
docker compose down

# 백엔드만 재시작 (빠름, 코드 변경 반영)
docker compose restart backend

# 전체 시작 (백그라운드)
docker compose up -d

# 백엔드 이미지 완전 재빌드 (Dockerfile 변경 시)
docker compose build --no-cache backend

# 재빌드 후 재시작
docker compose build --no-cache backend
docker compose up -d

# 컨테이너 상태 확인
docker compose ps
```

### 로그 확인

```powershell
# 최근 25줄 로그
docker compose logs backend --tail=25

# 라우터 로딩 상태만 필터링
docker compose logs backend 2>&1 | Select-String "WARN|ERROR|error|warn|skip|OK"

# 실시간 로그 스트리밍
docker compose logs backend -f
```

### 컨테이너 내부 실행

```powershell
# PostgreSQL DB 직접 조회
docker exec devanalysis114-postgres psql -U admin -d devanalysis114 -c "SELECT id, email, username, is_admin FROM users LIMIT 5;"

# 백엔드 Python 직접 실행 (import 테스트)
docker exec devanalysis114-backend python -c "import sys; sys.path.insert(0,'/app'); from backend.auth_router import router; print('auth OK')"

# User 모델 컬럼 확인
docker exec devanalysis114-backend python -c "
import sys; sys.path.insert(0, '/app')
from backend.models import User
print([c.name for c in User.__table__.columns])
"

# 비밀번호 재해시 (DB 반영)
docker exec devanalysis114-backend python -c "
import sys, bcrypt
sys.path.insert(0, '/app')
from backend.database import SessionLocal
from backend.models import User

db = SessionLocal()
user = db.query(User).filter(User.email == '119cash@naver.com').first()
if user:
    new_hash = bcrypt.hashpw('space0215@'.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
    user.password = new_hash
    db.commit()
    print('재해시 완료:', new_hash[:20])
else:
    print('유저 없음')
db.close()
"
```

---

## 🌐 2. API 테스트 명령어 (PowerShell)

### 헬스 체크

```powershell
# 기본 헬스 체크
Invoke-WebRequest -Uri "http://127.0.0.1:8000/api/health" -UseBasicParsing

# 등록된 전체 라우트 조회
Invoke-WebRequest -Uri "http://127.0.0.1:8000/openapi.json" -UseBasicParsing |
  ConvertFrom-Json | Select-Object -ExpandProperty paths | Get-Member -MemberType NoteProperty | Select-Object Name
```

### 인증 API

```powershell
# 로그인 (토큰 발급)
$response = Invoke-WebRequest `
  -Uri "http://127.0.0.1:8000/api/auth/login" `
  -Method POST `
  -ContentType "application/x-www-form-urlencoded" `
  -Body "username=119cash%40naver.com&password=space0215%40"

# 토큰 추출
$token = ($response.Content | ConvertFrom-Json).access_token
echo $token

# /me 엔드포인트 (내 정보 조회)
Invoke-WebRequest `
  -Uri "http://127.0.0.1:8000/api/auth/me" `
  -Headers @{"Authorization" = "Bearer $token"} `
  -UseBasicParsing
```

### LLM API

```powershell
# LLM 상태 확인
Invoke-WebRequest -Uri "http://127.0.0.1:8000/api/llm/status" -UseBasicParsing

# LLM 오케스트레이터 호출
$body = '{"task":"코드 리뷰해줘", "context":"Python FastAPI"}'
Invoke-WebRequest `
  -Uri "http://127.0.0.1:8000/api/llm/orchestrate" `
  -Method POST `
  -ContentType "application/json" `
  -Headers @{"Authorization" = "Bearer $token"} `
  -Body $body
```

### 관리자 API

```powershell
# 전체 유저 목록
Invoke-WebRequest `
  -Uri "http://127.0.0.1:8000/api/admin/users" `
  -Headers @{"Authorization" = "Bearer $token"} `
  -UseBasicParsing
```

---

## 📁 3. 파일 확인 명령어

### 파일 존재 여부 확인

```powershell
# 주요 백엔드 파일
Test-Path "backend\auth.py"
Test-Path "backend\auth_router.py"
Test-Path "backend\admin_router.py"
Test-Path "backend\main.py"
Test-Path "backend\llm\orchestrator.py"

# 주요 프론트엔드 파일
Test-Path "frontend\frontend\app\admin\page.tsx"
Test-Path "frontend\frontend\app\admin\users\page.tsx"
Test-Path "frontend\frontend\app\admin\llm\page.tsx"
Test-Path "frontend\frontend\.env.local"

# 한 번에 전체 확인
@(
  "backend\auth.py",
  "backend\auth_router.py",
  "backend\admin_router.py",
  "backend\llm\orchestrator.py",
  "frontend\frontend\app\admin\users\page.tsx",
  "frontend\frontend\app\admin\llm\page.tsx"
) | ForEach-Object { "$_ : $(Test-Path $_)" }
```

### 파일 내용 확인

```powershell
# .env.local 내용
Get-Content "frontend\frontend\.env.local"

# main.py 전체
Get-Content "backend\main.py"

# auth_router.py 전체
Get-Content "backend\auth_router.py"

# 파일 첫 20줄만
Get-Content "backend\auth.py" | Select-Object -First 20
```

### 백엔드 파일 구조

```powershell
# 트리 구조 출력
Get-ChildItem -Path "backend" -Recurse -Filter "*.py" |
  Select-Object FullName, @{N="Size(KB)";E={[math]::Round($_.Length/1KB,1)}}
```

---

## ⚙️ 4. 프론트엔드 명령어

### 개발 서버 관리

```powershell
# 포트 3005 사용 중인 PID 확인
netstat -ano | findstr ":3005"

# 해당 PID 종료 (예: 48396)
Stop-Process -Id 48396 -Force

# 루트에서 바로 시작
npm run dev:admin

# 또는 프론트엔드 디렉토리로 이동 후 직접 시작
Set-Location "frontend\frontend"
npm run dev -- --port 3005

# .next 캐시 초기화 후 재시작 (페이지 인식 안될 때)
Push-Location "frontend\frontend"
Remove-Item -Recurse -Force ".next"
npm run dev -- --port 3005
Pop-Location
```

### 페이지 HTTP 상태 확인

```powershell
# 각 admin 페이지 상태 확인
Invoke-WebRequest -Uri "http://127.0.0.1:3005/admin" -UseBasicParsing | Select-Object StatusCode
Invoke-WebRequest -Uri "http://127.0.0.1:3005/admin/users" -UseBasicParsing | Select-Object StatusCode
Invoke-WebRequest -Uri "http://127.0.0.1:3005/admin/llm" -UseBasicParsing | Select-Object StatusCode
```

### 브라우저로 열기

```powershell
Start-Process "http://127.0.0.1:3005/admin/login"
Start-Process "http://127.0.0.1:3005/admin"
Start-Process "http://127.0.0.1:3005/admin/users"
Start-Process "http://127.0.0.1:3005/admin/llm"
```

---

## 🛠️ 5. 문제 해결용 수동 명령어

### bcrypt 버전 확인

```powershell
docker exec devanalysis114-backend python -c "import bcrypt; print(bcrypt.__version__)"
docker exec devanalysis114-backend python -c "import passlib; print(passlib.__version__)"
```

### DB 연결 테스트

```powershell
docker exec devanalysis114-postgres psql -U admin -d devanalysis114 -c "\dt"
```

### 라우터 import 직접 테스트

```powershell
docker exec devanalysis114-backend python -c "
import sys; sys.path.insert(0,'/app')
try:
    from backend.auth_router import router; print('[OK] auth_router')
except Exception as e: print('[FAIL] auth_router:', e)
try:
    from backend.admin_router import router; print('[OK] admin_router')
except Exception as e: print('[FAIL] admin_router:', e)
try:
    from backend.llm.orchestrator import router; print('[OK] orchestrator')
except Exception as e: print('[FAIL] orchestrator:', e)
"
```

### 프로젝트 경량화 자동 보관 (중복/백업 정리)

```powershell
# 1) 후보만 점검 (실제 이동 없음)
powershell -ExecutionPolicy Bypass -File ".\scripts\archive_workspace_redundant.ps1" -DryRun

# 2) D드라이브로 실제 보관 이동
powershell -ExecutionPolicy Bypass -File ".\scripts\archive_workspace_redundant.ps1"
```

### 로컬 표준 상태 자동 정렬 (균일성 유지)

```powershell
# 빠른 정렬(이미지 재빌드 없이)
powershell -ExecutionPolicy Bypass -File ".\scripts\ensure_local_uniform_state.ps1" -SkipBuild

# 완전 정렬(이미지 포함 재정렬)
powershell -ExecutionPolicy Bypass -File ".\scripts\ensure_local_uniform_state.ps1"
```

### 운영 점검

```powershell
# 현재 운영 상태 점검
powershell -ExecutionPolicy Bypass -File ".\scripts\ops_health_check.ps1"
```

### VS Code Task 실행 이름

- `Run Ops Health Check`
- `Ensure Fixed Admin Account`
- `Start Frontend Dual`
- `Stop Frontend Dual`
- `Start Platform Stack`
- `Stop Platform Stack`

---

## 📋 빠른 참조 치트시트

| 상황 | 명령어 |
| --- | --- |
| 코드 수정 후 반영 확인 | `docker compose logs backend --tail=10` |
| 라우터 로딩 실패 | `docker exec ... python -c "from backend.xxx import router"` |
| 로그인 테스트 | `Invoke-WebRequest ... /api/auth/login` |
| 토큰으로 인증 테스트 | `Invoke-WebRequest ... -Headers @{"Authorization"="Bearer $token"}` |
| 운영 상태 점검 | `powershell -ExecutionPolicy Bypass -File ".\scripts\ops_health_check.ps1"` |
| 프론트 새 페이지 안보임 | `Push-Location frontend/frontend; Remove-Item -Recurse -Force .next; npm run dev -- --port 3005; Pop-Location` |
| 전체 재빌드 | `docker compose down` 후 `docker compose build --no-cache backend`, `docker compose up -d` |

---

## ✅ 문서 릴리즈 체크리스트

- [ ] 운영 점검 실행: `powershell -ExecutionPolicy Bypass -File ".\scripts\ops_health_check.ps1"`
- [ ] `README.md`의 운영 명령어 섹션과 실제 스크립트/태스크 이름 일치 여부 확인
- [ ] VS Code Problems 기준 Markdown 오류 0건 확인 (`README.md`)

현재 문서는 남아 있는 운영 스크립트 기준으로만 정리되어 있습니다.

---

## 🏥 전체 시스템 헬스체크 스크립트 완성

### 📥 설치 방법

**1단계 — 파일 다운로드:**

| 파일 | 설명 |
| --- | --- |
| [health_check.py](computer:///mnt/user-data/outputs/healthcheck/health_check.py) | 메인 Python 헬스체크 스크립트 |
| [health_check.ps1](computer:///mnt/user-data/outputs/healthcheck/health_check.ps1) | PowerShell 실행기 (가상환경 자동 활성화) |

**2단계 — 프로젝트 루트에 복사:**

```powershell
# 다운로드한 파일을 프로젝트 루트에 복사
Copy-Item "health_check.py" "C:\Users\WORK\source\repos\parkcheolhong\codeAI\"
Copy-Item "health_check.ps1" "C:\Users\WORK\source\repos\parkcheolhong\codeAI\"
```

**3단계 — 실행:**

```powershell
# 방법 1: PowerShell 스크립트로 실행 (권장)
Set-Location C:\Users\WORK\source\repos\parkcheolhong\codeAI
.\health_check.ps1

# 방법 2: Python 직접 실행
python health_check.py
```

---

### 📊 헬스체크 항목 (총 33개 엔드포인트)

| 섹션 | 테스트 항목 | 내용 |
| --- | --- | --- |
| 🖥️ **SYSTEM** | 4개 | `/health`, `/api/health`, `/docs`, `/openapi.json` |
| 🔐 **AUTH** | 3개 | 로그인·JWT발급, `/me`, 회원가입 엔드포인트 |
| 👑 **ADMIN** | 2개 | 유저 목록, 프로젝트 목록 (관리자 전용) |
| 🤖 **LLM** | 8개 | status, health, models, model-map, agents, orchestrate, smart, designer-drafts |
| 🛒 **MARKETPLACE** | 9개 | 프로젝트 목록, 구매내역, 어시스턴트, stats 3종, advanced 3종 |
| 📁 **FILES** | 1개 | MinIO 파일 엔드포인트 |
| 🌐 **FRONTEND** | 4개 | `/admin/login`, `/admin`, `/admin/users`, `/admin/llm` |
| ⚡ **OLLAMA** | 2개 | `/api/tags` (모델 목록), `/api/version` |

---

### 🎯 실행 결과 예시 (최신)

```text
╔══════════════════════════════════════════════════════════════╗
║      🏥  METANOVA codeAI — 전체 시스템 헬스체크              ║
║      시각: 2026-03-02 12:30:00                              ║
╚══════════════════════════════════════════════════════════════╝

──── 1️⃣  SYSTEM HEALTH ────
  ✅  GET  /health                    [200]   12ms  → status: ok
  ✅  GET  /api/health                [200]    8ms  → status: ok
  ✅  GET  /docs  (Swagger UI)        [200]   45ms
  ✅  GET  /openapi.json              [200]   23ms  → 33 endpoints 등록됨

──── 2️⃣  AUTH ENDPOINTS ────
  ✅  POST /api/auth/login            [200]   89ms  → JWT 발급 성공
  ✅  GET  /api/auth/me               [200]   34ms  → id=5 is_admin=True
  ...

📊  헬스체크 최종 결과 요약
  ████████████████████████████████████░░░░  30/33  (90%)

  🖥️  SYSTEM         4개   0개  ✅ ALL OK
  🔐  AUTH           3개   0개  ✅ ALL OK
  🤖  LLM            8개   0개  ✅ ALL OK
  🛒  MARKETPLACE    6개   3개  ⚠️  3개 실패 (advanced_router)
