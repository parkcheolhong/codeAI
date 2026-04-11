# Frontend 구번들/캐시 완화 체크리스트

## 검증 기준
- 실제 코드/빌드로 확인된 항목만 `[x]` 처리
- 운영 배포 전 항목은 로컬 구현 근거까지만 기록
- 브라우저 캐시/구번들 영향 완화를 위한 최소 안전 조치만 반영

## 1. 구번들 영향 완화 코드 반영
- [x] `/admin` 및 `/marketplace` HTML 응답에 no-store 계열 헤더를 추가했다.
  - 근거: `frontend/frontend/next.config.ts` `headers()`에 `/admin/:path*`, `/marketplace/:path*`, `/privacy`, `/terms` 대상 `Cache-Control: no-store` 계열 헤더 반영
- [x] 기존 `proxy.ts`에서 관리자/마켓 주요 화면 응답에 no-store 헤더를 강제했다.
  - 근거: `frontend/frontend/proxy.ts`에서 `Cache-Control`, `Pragma`, `Expires`, `Surrogate-Control`, `x-frontend-shell-cache-policy` 설정
- [x] 구번들 영향 진단용 응답 헤더를 추가했다.
  - 근거: `frontend/frontend/proxy.ts`의 `x-frontend-shell-cache-policy: no-store`
- [x] 관리자 프로젝트 목록 API 응답에 짧은 캐시 헤더와 진단 헤더를 추가했다.
  - 근거: `backend/admin_router.py`의 `Cache-Control: private, max-age=<ttl>, stale-while-revalidate=15`, `x-admin-projects-applied-limit`, `x-stale-client-mitigation`
- [x] 마켓플레이스 카테고리 API 응답에 짧은 캐시 헤더와 진단 헤더를 추가했다.
  - 근거: `backend/marketplace/router.py`의 `Cache-Control: public, max-age=<ttl>, stale-while-revalidate=30`, `x-stale-client-mitigation`
- [x] `/admin`과 `/marketplace`를 App Router 레벨에서 강제 dynamic/no-store로 전환했다.
  - 근거: `frontend/frontend/app/admin/layout.tsx`, `frontend/frontend/app/marketplace/layout.tsx`의 `dynamic = 'force-dynamic'`, `revalidate = 0`, `fetchCache = 'force-no-store'`
- [x] App Router 런타임을 `nodejs`로 고정해 프리렌더 재고정을 더 막았다.
  - 근거: `frontend/frontend/app/admin/layout.tsx`, `frontend/frontend/app/marketplace/layout.tsx`의 `runtime = 'nodejs'`
- [x] 백엔드 전역 HTTP 미들웨어에서 stale frontend burst guard를 추가했다.
  - 근거: `backend/main.py`의 `@app.middleware("http") stale_frontend_burst_guard`
- [x] 운영 nginx 에서 `/admin`, `/marketplace` HTML 캐시 헤더를 강제로 no-store 로 덮었다.
  - 근거: `nginx/nginx.conf/nginx.conf`의 `proxy_hide_header Cache-Control...`, `add_header Cache-Control "no-store..."`, `x-frontend-shell-cache-policy`

## 2. 반복 호출 완화 상태
- [x] 관리자 카테고리 통계 조회의 프로젝트 상한을 500으로 줄였다.
  - 근거: `frontend/frontend/lib/admin-category-service.ts`의 `projectLimit` 상한 500
- [x] 카테고리 목록/통계 로드에 중복 호출 방지 장치를 추가했다.
  - 근거: `frontend/frontend/lib/use-admin-system-category-controller.ts`의 in-flight ref 및 시간 간격 가드
- [x] 관리자 페이지 초기 카테고리/통계 부트스트랩을 1회만 실행하도록 고정했다.
  - 근거: `frontend/frontend/app/admin/page.tsx`의 `adminCategoriesBootstrappedRef`, `adminCategoryStatsBootstrappedRef`
- [x] 마켓플레이스 초기 데이터 부트스트랩을 1회만 실행하도록 고정했다.
  - 근거: `frontend/frontend/app/marketplace/page.tsx`의 `marketplaceLoadedRef`
- [x] 백엔드 관리자 프로젝트 목록 API도 요청 limit과 무관하게 최대 500건까지만 반환하도록 강제했다.
  - 근거: `backend/admin_router.py`의 `/api/admin/projects`에서 `safe_limit = min(limit, 500)` 적용 및 `applied_limit` 응답 포함
- [x] 백엔드 관리자 프로젝트 목록 API에 짧은 TTL 캐시를 추가했다.
  - 근거: `backend/admin_router.py`의 `_ADMIN_PROJECTS_RESPONSE_CACHE`, `ADMIN_PROJECTS_CACHE_TTL_SEC`
- [x] 백엔드 마켓플레이스 카테고리 API에 짧은 TTL 캐시를 추가했다.
  - 근거: `backend/marketplace/router.py`의 `_MARKETPLACE_CATEGORIES_CACHE`, `MARKETPLACE_CATEGORIES_CACHE_TTL_SEC`
- [x] 관리자 프로젝트 목록 API에 burst 동시 요청 락을 추가했다.
  - 근거: `backend/admin_router.py`의 `_ADMIN_PROJECTS_CACHE_LOCK`
- [x] 마켓플레이스 카테고리 API에 burst 동시 요청 락을 추가했다.
  - 근거: `backend/marketplace/router.py`의 `_MARKETPLACE_CATEGORIES_CACHE_LOCK`
- [x] 카테고리 생성/수정/삭제 후 카테고리 캐시를 즉시 무효화하도록 보강했다.
  - 근거: `backend/marketplace/router.py`의 `_invalidate_marketplace_categories_cache()` 호출
- [x] 관리자 프로젝트 목록 API에 짧은 윈도우 rate limit을 추가했다.
  - 근거: `backend/admin_router.py`의 `_ADMIN_PROJECTS_RATE_LIMIT_WINDOW_SEC`, `_ADMIN_PROJECTS_RATE_LIMIT_STATE`, `_should_throttle_admin_projects()`
- [x] 마켓플레이스 카테고리 API에 짧은 윈도우 rate limit을 추가했다.
  - 근거: `backend/marketplace/router.py`의 `_MARKETPLACE_CATEGORIES_RATE_LIMIT_WINDOW_SEC`, `_MARKETPLACE_CATEGORIES_RATE_LIMIT_STATE`, `_should_throttle_marketplace_categories()`
- [x] rate limit 시 429 대신 degraded 200 응답으로 즉시 연결을 닫도록 전환했다.
  - 근거: `backend/admin_router.py`의 `_build_admin_projects_degraded_payload()`, `x-admin-projects-degraded`; `backend/marketplace/router.py`의 `_build_marketplace_categories_degraded_payload()`, `x-marketplace-categories-degraded`
- [x] legacy `limit=5000` 관리자 프로젝트 요청은 첫 요청부터 degraded 응답으로 즉시 cutoff 하도록 보강했다.
  - 근거: `backend/admin_router.py`의 `_is_legacy_admin_projects_request()`, `admin-projects-legacy-limit-cutoff`
- [x] degraded 응답에 `Connection: close` 를 추가해 keep-alive 점유를 줄이도록 보강했다.
  - 근거: `backend/admin_router.py`의 `_apply_admin_projects_degraded_headers()`, `backend/marketplace/router.py`의 `_apply_marketplace_categories_degraded_headers()`
- [x] legacy `limit=5000` 요청과 categories burst를 라우터 이전 단계에서 즉시 끊도록 전역 가드를 추가했다.
  - 근거: `backend/main.py`의 `_stale_frontend_guard_response()`, `_legacy_admin_projects_payload()`, `_mark_and_check_stale_frontend_burst()`
- [x] 운영 nginx 에서 legacy `limit=5000` 관리자 프로젝트 요청을 엣지에서 즉시 차단했다.
  - 근거: `nginx/nginx.conf/nginx.conf`의 `location = /api/admin/projects`, `x-stale-client-mitigation: nginx-admin-projects-legacy-cutoff`
- [x] 운영 nginx 에서 `/api/marketplace/categories` 를 짧은 엣지 캐시로 흡수하도록 보강했다.
  - 근거: `nginx/nginx.conf/nginx.conf`의 `proxy_cache marketplace_api_cache`, `X-Cache-Status`

## 3. 로컬 검증
- [x] 프런트 build가 현재 수정 상태에서 성공했다.
  - 근거: `cmd /c npm run build` 결과 `/admin`, `/marketplace`가 동적(`ƒ`) 경로로 빌드됨
- [x] 백엔드 핵심 차단 파일의 Python 구문 검증이 통과했다.
  - 근거: `python -m py_compile .\backend\main.py .\backend\admin_router.py .\backend\marketplace\router.py` 성공
- [x] nginx 설정 문법 검증이 통과했다.
  - 근거: `docker exec devanalysis114-nginx nginx -t` 성공
- [x] 관리자 프런트/백엔드/nginx 재기동과 프런트 재빌드가 완료됐다.
  - 근거: `docker compose build frontend-admin`, `docker compose up -d frontend-admin nginx`, `docker compose restart nginx frontend-admin backend`
- [x] 워크스페이스 build는 프런트 임시/베이스라인 외부 오류와 별개로 이번 변경 파일 오류는 없음을 확인했다.
  - 근거: `get_errors`에서 수정한 백엔드/nginx/프런트 레이아웃 파일 직접 오류 없음

## 4. 운영 실측 2회
- [x] 운영 도메인 `/marketplace`, `/admin`, `/api/marketplace/categories`, `/api/admin/projects` 1차 실측을 수행했다.
  - 근거: `curl.exe -k -sS -D - -o NUL ...` 1차 결과 확인
- [x] 운영 도메인 `/marketplace`, `/admin`, `/api/marketplace/categories`, `/api/admin/projects` 2차 실측을 수행했다.
  - 근거: 동일 curl 2차 결과 확인
- [x] 운영 `/marketplace`, `/admin` 이 새 no-store 헤더로 전환되었다.
  - 근거: 실측 2회 모두 `Cache-Control: no-store, no-cache, must-revalidate, max-age=0`, `x-frontend-shell-cache-policy: no-store`, `surrogate-control: no-store`
- [x] 운영 응답에 진단 헤더가 노출된다.
  - 근거: 실측 2회 모두 `x-frontend-shell-cache-policy`, `x-frontend-build-marker`, `x-frontend-build-id`
- [x] 운영 `/api/admin/projects?skip=0&limit=5000` 에서 새 완충 헤더가 확인된다.
  - 근거: 실측 2회 모두 `x-stale-client-mitigation: nginx-admin-projects-legacy-cutoff`, `x-admin-projects-degraded: 1`, `x-admin-projects-applied-limit: 500`
- [x] 운영 `/api/marketplace/categories` 가 짧은 캐시 재사용 경로로 응답한다.
  - 근거: 실측에서 `X-Cache-Status: MISS/EXPIRED`, `cache-control: public, max-age=5, stale-while-revalidate=30`
- [x] 운영 구번들 폭주를 닫기 위한 직접 차단이 운영 경로에 반영됐다.
  - 근거: HTML no-store 전환 + legacy admin projects 엣지 즉시 cutoff + categories 엣지 캐시 반영 완료

## 현재 판정
- 상태: **완료됨**
- 이유: 프런트 초기 부트스트랩 1회 고정, 백엔드/전역 차단, nginx 엣지 차단과 no-store 덮기, admin projects legacy cutoff, marketplace categories 엣지 캐시, 관리자 프런트 재빌드/재기동까지 반영했고, 운영 도메인 실측 2회에서 `/admin`, `/marketplace`, `/api/admin/projects?skip=0&limit=5000`, `/api/marketplace/categories` 모두 새 헤더와 차단/완충 결과가 확인됐다.
