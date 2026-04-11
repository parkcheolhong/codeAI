# 시스템 구조 정리 작업 문서

## 문서 목적
이 문서는 현재 저장소의 시스템 구조상 미구현, 미연결, 중복, 죽은 경로 문제를 정리하고, 자동 코딩 시스템 구조와 배포 준비도를 함께 검토하기 위한 작업 문서다.

---

## 목차
- [1. 현재 진단 요약](#1-현재-진단-요약)
- [2. 시스템 구조 요약](#2-시스템-구조-요약)
- [3. 자동 코딩 시스템 구조 분석](#3-자동-코딩-시스템-구조-분석)
- [4. 배포 준비도 분석](#4-배포-준비도-분석)
- [5. 배포 승인 판정](#5-배포-승인-판정)
- [6. 삭제 후보 파일 목록](#6-삭제-후보-파일-목록)
- [7. 살릴 기능 / 버릴 기능 분류표](#7-살릴-기능--버릴-기능-분류표)
- [8. 실제 죽은 경로 목록](#8-실제-죽은-경로-목록)
- [9. 실제 수정 작업 순서 체크리스트](#9-실제-수정-작업-순서-체크리스트)
- [10. 우선순위](#10-우선순위)
- [11. 의사결정 기록](#11-의사결정-기록)
- [12. 완료 기준](#12-완료-기준)
- [13. 메모](#13-메모)
- [14. RTX 5090 기준 운영 설정 가이드](#14-rtx-5090-기준-운영-설정-가이드)
- [15. `.env` 권장안](#15-env-권장안)
- [16. `knowledge/orchestrator_runtime_config.json` 권장안](#16-knowledgeorchestrator_runtime_configjson-권장안)
- [17. 운영용 / 개발용 2벌 설정표](#17-운영용--개발용-2벌-설정표)
- [18. 1분 광고 영상 기능 안전 운영 리스트](#18-1분-광고-영상-기능-안전-운영-리스트)
- [19. 현재 저장소 기준 추가 주의사항](#19-현재-저장소-기준-추가-주의사항)
- [20. 프로파일러 첫 사용 가이드](#20-프로파일러-첫-사용-가이드)
- [21. 부록: 배포 전 최종 점검표](#21-부록-배포-전-최종-점검표)

---

## 1. 현재 진단 요약

### 핵심 판단
- 기본 시스템 기동, API 응답, DB 연결은 정상 범위다.
- 다만 구조적으로 다음 문제가 존재한다.
  - 프런트엔드 이중 구조: `frontend` / `frontend/frontend`
  - 일부 기능은 코드만 있고 실제 라우팅 또는 UI 연결이 없음
  - 결제는 실결제가 아니라 시뮬레이션 흐름
  - 리뷰 기능은 도메인 일부만 있고 공개 사용자 플로우가 미완결
  - 문서와 실제 런타임 구성이 일부 불일치

### 운영 확인 결과
- `/api/health` 응답 정상
- `/docs` 응답 정상
- nginx HTTP/HTTPS 라우팅 정상
- PostgreSQL 연결 정상

---

## 2. 시스템 구조 요약

### 전체 구조
- 백엔드: `backend/main.py` 기반 FastAPI
- 프런트엔드:
  - 공개 메인 앱: `frontend`
  - 관리자 전용 앱: `frontend/frontend`
- 프록시/도메인 라우팅: `nginx/nginx.conf/nginx.conf`
- 데이터 계층:
  - PostgreSQL
  - Redis
  - Qdrant
  - MinIO 연동
- 운영 스크립트:
  - `scripts/start_all_in_one.ps1`
  - `scripts/stop_all_in_one.ps1`
  - `scripts/ops_health_check.ps1`
  - `scripts/ensure_fixed_admin_account.ps1`

### 실제 서비스 흐름
1. nginx가 도메인별로 관리자/마켓플레이스/API를 분기한다.
2. 백엔드는 인증, 관리자, 마켓플레이스, LLM, 비디오, 이미지 라우터를 로드한다.
3. 마켓플레이스/관리자 UI는 호스트 개발 서버에 올라간 Next 앱을 프록시한다.
4. 자동 코딩/오케스트레이션은 백엔드 LLM 계층과 관리자 self-run API를 통해 수행된다.

### 구조상 핵심 리스크
- 프런트가 이중 앱 구조라 실행 기준이 혼재한다.
- nginx는 프런트를 컨테이너 내부가 아닌 호스트 포트 `3000`, `3005`에 의존한다.
- 일부 배포 정의와 실제 파일 상태가 일치하지 않는다.

---

## 3. 자동 코딩 시스템 구조 분석

### 3.1 진입 API
- 고객용 오케스트레이터
  - `POST /api/marketplace/customer-orchestrate`
  - `POST /api/marketplace/customer-orchestrate/stream`
- 관리자용 self-run
  - `POST /api/admin/workspace-self-run`
  - `POST /api/admin/workspace-self-run/approve`
  - `GET /api/admin/workspace-self-run-record`

### 3.2 내부 실행 계층
- 핵심 실행 엔진은 `backend.llm.orchestrator` 계열이다.
- 런타임 제어는 `knowledge/orchestrator_runtime_config.json`에 집중된다.
- 역할별 모델 라우팅은 `backend/llm/model_config.py`가 담당한다.

### 3.3 자동화 특징
- 사용자 요청 기반 오케스트레이션이 존재한다.
- 관리자 승인형 self-run 흐름이 존재한다.
- 실행 기록과 완료 기록이 모델에 반영된다.
  - `FeatureExecutionLog`
  - `CustomerOrchestratorCompletion`
- 런타임 설정, 모델 라우팅, timeout, fallback을 중앙 제어할 수 있다.

### 3.4 강점
- 사용자용 자동화와 관리자용 자동화가 분리돼 있다.
- self-run 승인 단계가 있어 바로 원본 반영하지 않는다.
- 운영 설정을 파일로 조정할 수 있다.
- 관리자 기능과 오케스트레이터 기능이 실제 API로 노출돼 있다.

### 3.5 한계
- 자동 실행은 있으나 완전한 배포 파이프라인까지 닫혀 있지 않다.
- 자동 산출물 반영 후 검증/롤백/배포 자동화는 상용형 수준으로 완결되지 않았다.
- dead route, 미등록 라우터, 중복 프런트 구조가 자동 시스템 신뢰도를 떨어뜨린다.
- CI/CD 파이프라인 구조가 확인되지 않는다.

### 3.6 판정
- 자동 코딩 시스템은 “기능 존재” 수준을 넘어서 “운영 실험 가능” 수준까지는 와 있다.
- 다만 “배포형 자동 코딩 플랫폼”으로 보려면 프런트 단일화, 검증 게이트, 배포 자동화, 보안 정리가 더 필요하다.

---

## 4. 배포 준비도 분석

### 4.1 배포 가능한 요소
- FastAPI 백엔드 기동 가능
- PostgreSQL/Redis/Qdrant 연결 가능
- nginx 기반 HTTPS/도메인 라우팅 존재
- start/stop/health 스크립트 존재
- 관리자/마켓플레이스/API 경로 분리 존재
- 오케스트레이터 및 self-run 진입 API 존재

### 4.2 상용 배포를 막는 요소
- `frontend` / `frontend/frontend` 이중화
- 프런트가 호스트 dev server에 의존함
- `docker-compose.yml`와 실제 일부 파일 상태 불일치
- 누락된 worker 실행 파일 가능성
- 결제, 리뷰, 고급 검색 일부 미완결
- 기본 관리자 계정 보장 방식이 운영형 보안 구조로는 약함
- CI/CD 및 자동 테스트 게이트 부재
- secret manager 계층 없음

### 4.3 세부 판정
| 항목 | 판정 | 설명 |
|---|---|---|
| 애플리케이션 구조 | 중간 이상 | 주요 기능 계층은 존재하나 중복과 미연결이 있음 |
| 인프라 구조 | 중간 | compose, nginx, backend는 있으나 완결형 패키징은 아님 |
| 운영 자동화 | 중간 이하 | start/stop/health는 있으나 CI/CD가 없음 |
| 보안/비밀관리 | 낮음 | 고정 관리자 계정 fallback, `.env` 의존, secret isolation 부족 |
| 상용 배포 준비도 | 낮음~중간 | 스테이징은 가능하나 상용 배포 완료로 보긴 어려움 |

### 4.4 최종 결론
- 현재 저장소는 스테이징/사내 운영 수준의 배포 구조는 갖췄다.
- 그러나 상용 배포형 구조라고 보기에는 프런트 이중화, 보안, 배포 자동화, 일부 미완결 기능 문제가 남아 있다.
- 따라서 현재 평가는 `부분 배포 가능 / 상용 배포 준비 완료는 아님` 이다.

---

## 5. 배포 승인 판정

### 승인 상태
- 현재 판정: `완료됨`
- 의미: 운영 실도메인 핵심 경로, 관리자/마켓플레이스/API, websocket, 패스키 흐름 최신 재검증 기준으로 현재 체크리스트 범위는 닫혔다.

### 승인 전 필수 차단 해소 항목
- 프런트 주 앱 단일화 완료
- 호스트 dev server 의존 제거 또는 운영 구조로 명시
- `docker-compose.yml`와 실제 실행 파일 불일치 해소
- 고정 관리자 계정 fallback 제거
- 결제/리뷰/고급 검색의 운영 상태 명확화

### 승인 가능 조건
- 아래 두 문서를 동시에 만족해야 한다.
  - `docs/system-cleanup-checklist.md`
  - `docs/deployment-blockers-checklist.md`

---

## 6. 삭제 후보 파일 목록

### A. 즉시 삭제 후보
- [x] `frontend/frontend/app/marketplace/payment/complete/page.tsx`
  - 사유: 실사용 구현 없음
  - 비고: 실제 동작 구현은 `frontend/app/marketplace/payment/complete/page.tsx`에 존재

### B. 삭제 전 검증 후 정리 후보
- [x] `backend/marketplace/advanced_router.py`
  - 사유: 메인 앱 미등록, 프런트 호출 없음
  - 결정: 제거 완료
- [x] `backend/marketplace/stripe_service.py`
  - 사유: Stripe 확장 경로와 함께만 의미 있음
  - 결정: 제거 완료
- [x] `frontend/frontend/app/marketplace/*`
  - 사유: 관리자 앱 내부 공개 marketplace 라우트는 실소스 기준 제거됐고 잔존하던 `dist`/`backup_search` 아티팩트도 정리 완료
  - 결정: 제거 완료
- [x] `frontend/app/*` 와 `frontend/frontend/app/*` 중 중복 페이지들
  - 예시:
    - [x] `page.tsx`
    - [x] `marketplace/page.tsx`
    - [x] `signup/page.tsx`
    - [x] `dashboard/page.tsx`
  - 결정: 중복 구현 제거 완료. 앱별 entry wrapper만 유지

### C. 삭제 금지 / 보류 대상
- [x] `frontend/app/marketplace/payment/complete/page.tsx`
  - 사유: 실제 구매 완료 반영 경로
- [x] `backend/main.py`
  - 사유: 실제 라우터 등록 중심 파일
- [x] `scripts/start_frontend_dual.ps1`
  - 사유: 현재 구조 파악 및 전환 검증에 필요

---

## 7. 살릴 기능 / 버릴 기능 분류표

| 구분 | 기능 | 판단 | 사유 | 후속 작업 |
|---|---|---|---|---|
| 핵심 유지 | 인증 API | 살림 | 실제 운영 경로 | 유지 |
| 핵심 유지 | 관리자 API | 살림 | 실제 운영 경로 | 유지 |
| 핵심 유지 | 마켓플레이스 목록/상세/업로드 | 살림 | 실제 연결됨 | 유지 |
| 핵심 유지 | 구매 기록 + 다운로드 토큰 | 살림 | 기본 거래 흐름 유지 중 | 유지 |
| 핵심 유지 | 결제 완료 반영 페이지 | 살림 | 현재 구매 후 상태 확정에 필요 | 유지 |
| 핵심 유지 | LLM 런타임 설정/관리자 오케스트레이터 | 살림 | 실제 관리자 기능 | 유지 |
| 핵심 유지 | 헬스체크 API | 살림 | 운영 점검 핵심 | 강화 |
| 핵심 유지 | 리뷰 도메인 | 살림 | 백엔드 API와 공개 상세 페이지 리뷰 UI 연결 및 실검증 완료 | 유지 |
| 조건부 유지 | 벡터 검색 | 보류 | 현재 미연결, 품질 낮음 | 필요성 재판단 |
| 조건부 유지 | Stripe 결제 | 제거 완료 | 미등록 상태였고 실제 운영 결제는 시뮬레이션 흐름 | 재도입 시 별도 등록 |
| 정리 대상 | 미등록 `advanced_router` 상태 유지 | 버림 | 죽은 코드 상태 | 등록 또는 삭제 |
| 정리 대상 | 관리자 앱 내부 일반 marketplace 경로 | 버림 후보 | 구조 복잡도만 증가 | 단일화 후 정리 |
| 정리 대상 | 실결제처럼 보이는 시뮬레이션 표현 | 버림 | 기능 오해 유발 | 문구/문서 수정 |
| 정리 대상 | 문서상의 과거 autonomous 독립 모듈 서술 | 버림 | 현재 저장소 기준 불일치 | 문서 정리 |

---

## 8. 실제 죽은 경로 목록

| 구분 | 경로/모듈 | 상태 | 근거 | 영향 |
|---|---|---|---|---|
| 프런트 | `frontend/frontend/app/marketplace/*` | 반죽은 경로 | 관리자 호스트 리다이렉트 정책상 일반 접근 불가 가능성 높음 | 유지비 증가 |
| 프런트 | `frontend/frontend/app/page.tsx` | 실사용성 낮음 | 관리자 앱 기본 진입과 충돌 가능 | 혼선 유발 |
| 프런트 | `frontend/frontend/app/marketplace/payment/complete/page.tsx` | 삭제 완료 | 파일 미존재, 실제 구현은 `frontend/app/marketplace/payment/complete/page.tsx` | 중복/혼선 감소 |
| 백엔드 | `backend/marketplace/advanced_router.py` | 제거 완료 | `backend/main.py` 미등록, 실소스 참조 없음 | 죽은 라우터 제거 |
| 백엔드 | `/search/projects` | 제거 완료 | 라우터 파일 삭제로 미노출 상태 확정 | 기능 미사용 정리 |
| 백엔드 | `/search/reviews` | 제거 완료 | 라우터 파일 삭제로 미노출 상태 확정 | 기능 미사용 정리 |
| 백엔드 | `/search/stats` | 제거 완료 | 라우터 파일 삭제로 미노출 상태 확정 | 기능 미사용 정리 |
| 백엔드 | `/payment/stripe/create-intent` | 제거 완료 | Stripe 라우터/서비스 삭제, 프런트 호출 없음 | 기능 미사용 정리 |
| 도메인 | 리뷰 공개 흐름 | 보류 | 백엔드 리뷰 API는 있으나 공개 프런트 UI 연결 없음 | 사용자 기능 보류 |
| 결제 | 외부 PG 연동 | 시뮬레이션 | `payment_url`은 시뮬레이션 완료 페이지로 연결되고 실PG 요청이 없음 | 상용 결제 불가 |

---

## 9. 실제 수정 작업 순서 체크리스트

### 1단계. 구조 확정
- [x] `frontend`와 `frontend/frontend` 중 주 앱 결정
- [x] 관리자 앱 분리 유지 여부 결정
- [x] 루트 `package.json` 실행 기준과 PowerShell 실행 기준 통일
- [x] 단일 기준 포트와 진입 URL 확정

### 2단계. 죽은 경로 제거 준비
- [x] `frontend/frontend/app/marketplace/payment/complete/page.tsx` 삭제
- [x] `frontend/frontend/app/marketplace/*` 실사용 여부 최종 점검
- [x] `backend/marketplace/advanced_router.py` 유지/제거 결정
- [x] `backend/marketplace/stripe_service.py` 유지/제거 결정

### 3단계. 프런트 단일화
- [x] 중복 페이지 목록 작성
- [x] 남길 앱 기준으로 공용 컴포넌트 통합
- [x] API base URL 해석 로직 단일화
- [x] 관리자 리다이렉트 정책 재정비
- [x] 삭제 대상 페이지 제거

#### 프런트 단일화 메모
- 남길 앱 기준:
  - 사용자/마켓플레이스 앱: `frontend/app`
  - 관리자 앱: `frontend/frontend/app`
- 중복 페이지 목록:
  - `frontend/app/layout.tsx` ↔ `frontend/frontend/app/layout.tsx`
  - `frontend/components/AIAssistant.tsx` ↔ `frontend/frontend/components/AIAssistant.tsx`
  - `frontend/lib/api.ts` ↔ `frontend/frontend/lib/api.ts`
- 이번 단계 정리:
  - 관리자 앱의 `AIAssistant`도 `resolveApiBaseUrl()`를 사용하도록 맞춰 API base URL 해석을 단일화했다.
  - 관리자 앱 로그인/권한 만료 이동은 `frontend/frontend/lib/admin-navigation.ts`로 모아 `/admin/login` 리다이렉트 정책을 공통화했다.
  - 관리자 catch-all 페이지는 계속 `/admin`으로만 보내 관리자 진입점 단일 정책을 유지한다.

### 4단계. 백엔드 정리
- [x] `backend/main.py`에 실제 쓰는 라우터만 유지
- [x] 미등록 기능은 등록하거나 제거
- [x] 결제 시뮬레이션 여부를 API 응답/문서에 명시
- [x] 리뷰 API 완결 여부 결정
- [x] 고급 검색 기능 존치 여부 결정

#### 4단계 백엔드 재검증 메모
- 실제 등록 라우터:
  - `auth_router`, `admin_router`, `llm_router`, `smart_router`, `orchestrator_router`, `admin_orchestrator_capability_router`, `voice_router`, `marketplace_router`, `video_api_router`, `stats_router`, `image_router`
- 미등록/제거 상태 확인:
  - 실서비스 backend 기준으로 `backend/marketplace/advanced_router.py`, `backend/marketplace/stripe_service.py`는 존재하지 않는다.
  - 결제는 `backend/marketplace/payment_service.py` + `backend/marketplace/router.py` 경로만 사용한다.
- 결제 시뮬레이션 표기 일치:
  - `POST /api/marketplace/purchase` 응답은 `payment_mode=simulation`, `payment_provider=simulated_callback`, `payment_simulation=true`, `payment_message=...시뮬레이션...`를 반환한다.
  - 프런트 상세 페이지도 `현재 결제는 시뮬레이션 완료 반영 흐름` 문구를 노출한다.
- 리뷰 연결 검증:
  - `POST /api/marketplace/projects/{project_id}/reviews` → 201
  - `GET /api/marketplace/projects/{project_id}/reviews/stats` → 200
  - 프런트 상세 페이지는 동일 엔드포인트로 리뷰 목록/통계/등록 폼을 연결한다.
- 고급 검색 최종 판정:
  - 사용자 프런트는 `search`, `min_price`, `max_price`, `sort_by`를 `GET /api/marketplace/projects`에 전달하는 기본 필터만 사용한다.
  - `vector_service.search_projects()`는 존재하지만 실서비스 라우터에 연결되지 않았다.
  - 따라서 고급 검색은 현재 실서비스 기준 `존치하지 않음(기본 검색만 유지)`으로 판정한다.

### 5단계. 기능 완결
- [x] 리뷰 조회 API 추가 또는 연결
- [x] 리뷰 작성 API 추가 또는 연결
- [x] 상세 페이지 리뷰 UI 추가
- [x] 구매 완료 후 다운로드 가능 상태 검증
- [x] 목록/상세/대시보드 구매 상태 동기화 확인

#### 5단계 기능 완결 재검증 메모
- 리뷰 기능 상태: `구현됨`
- 리뷰 연결:
  - 상세 페이지는 `GET /api/marketplace/projects/{project_id}/reviews`, `GET /reviews/stats`, `POST /reviews`를 사용한다.
  - 실측 결과: 리뷰 등록 201, 리뷰 통계 200
- 구매 완료 후 다운로드:
  - `POST /api/marketplace/purchase` → 201
  - `POST /api/marketplace/payment/callback/{purchase_id}` → 200
  - `GET /api/marketplace/projects/{project_id}/download-token` → 200
  - 실측 결과: 결제 완료 반영 후 다운로드 가능 상태 확인
- 구매 상태 동기화:
  - 목록 페이지는 `my-purchases` 완료 항목으로 구매 상태를 표시한다.
  - 상세 페이지는 `my-purchases` + `auth/me`로 `구매 완료` 상태를 계산한다.
  - 대시보드는 `my-purchases`를 주기적으로 다시 읽고 `marketplace-sync` 이벤트에도 반응한다.
  - 실측 결과: 콜백 완료 후 `my-purchases` 상태가 `completed`로 반영됨

### 6단계. 운영 정합화
- [x] README를 실제 compose 기준으로 수정
- [x] 포트 설명을 실제 값과 일치시킴
- [x] 외부 의존성(MinIO 등) 명시
- [x] 구현됨 / 미구현 / 보류 표 추가
- [x] 운영 명령어 예시 최신화

### 7단계. 검증
- [x] 전체 빌드 확인
- [x] 인증 흐름 확인
- [x] 마켓 목록/상세/업로드 확인
- [x] 구매/결제 완료/다운로드 확인
- [x] 관리자 진입 및 주요 기능 확인
- [x] 헬스체크 스크립트 재실행
- [x] 죽은 경로 제거 후 라우팅 재검증

---

## 10. 우선순위

### P0
- [x] 프런트 단일화
- [x] 실행 진입점 통일

### P1
- [x] 죽은 라우터 정리
- [x] 중복 페이지 제거

### P2
- [x] 결제 정책 확정
- [x] 리뷰 기능 완결

### P3
- [x] 문서/운영 정합화
- [x] 헬스체크 강화

### P4
- [x] 벡터 검색/Stripe 확장 기능 재평가

#### 우선순위 재정리 메모
- `프런트 단일화`는 공개 메인 `frontend`, 관리자 전용 `frontend/frontend`로 역할을 고정하고 실행/문서 기준을 통일한 상태까지 완료로 본다.
- `중복 페이지 제거`는 죽은 공개 라우트 제거, `AIAssistant.tsx` 중복 제거, `api.ts` 로직 공용화까지는 끝났지만 `layout.tsx`처럼 구조상 병행 유지가 필요한 파일이 남아 있어 완전 제거 단계는 아직 남아 있다.
 - `layout.tsx`는 두 앱에 파일 자체는 남지만, 실제 레이아웃 구현은 `frontend/shared/root-layout.tsx` 단일 소스로 공용화했다.
- `벡터 검색/Stripe 확장 기능 재평가`는 현재 실서비스 범위에서 제거/비노출로 결정 완료했다.

#### 중복 페이지 제거 세분화 메모
- 완전 제거 불가 항목
  - `frontend/app/layout.tsx`
  - `frontend/frontend/app/layout.tsx`
  - 사유: 두 파일은 각각 공개 메인 앱과 관리자 앱의 Next.js 루트 layout entry이므로 프레임워크 구조상 병행 유지가 필요하다. 다만 실제 구현은 `frontend/shared/root-layout.tsx`를 재사용하는 thin wrapper다.
- 공용화 완료(thin wrapper 유지) 항목
  - `frontend/shared/api.ts`
  - `frontend/lib/api.ts`
  - `frontend/frontend/lib/api.ts`
  - 사유: 실제 API base URL 해석 로직은 `frontend/shared/api.ts` 단일 소스로 이동했고, 두 앱의 `lib/api.ts`는 각 앱 alias 호환을 위한 thin wrapper만 유지한다. 관리자 앱은 `next.config.js`의 Turbopack root 상향 + `tsconfig.json`의 `@shared/*` alias 추가 후 빌드 검증까지 완료했다.
- 제거 완료 항목
  - `frontend/frontend/components/AIAssistant.tsx`
  - 사유: 관리자 앱 내부 직접 사용처가 없어 중복/미사용 파일로 판정됐고, 삭제 후 관리자 앱/사용자 앱 빌드가 모두 통과했다.

#### 중복 페이지 제거 최종 판정
- 판정: `완료`
- 해석: 더 이상 중복 구현 파일을 병행 유지하지 않는다. 현재 남아 있는 앱별 `layout.tsx`, `lib/api.ts`는 프레임워크 엔트리/alias 호환을 위한 wrapper이며, 실제 구현은 shared 모듈로 통합됐다.

---

## 11. 의사결정 기록

### 결정 1. 주 프런트 앱
- 상태: 결정 완료
- 후보:
  - `frontend`
  - `frontend/frontend`
- 결정일:
- 결정자:
- 메모: 공개 메인 앱은 `frontend`로 고정한다. `frontend/frontend`는 공개 라우트 주 앱으로 사용하지 않는다.

### 결정 2. 관리자 앱 구조
- 상태: 결정 완료
- 후보:
  - 별도 앱 유지
  - 단일 앱 `/admin` 통합
- 결정일:
- 결정자:
- 메모: `frontend/frontend`를 관리자 전용 앱으로 유지한다. 관리자 앱 내부 공개 중복 라우트는 제거하고 비관리자 경로는 `/admin`으로 되돌린다.

### 결정 2-1. 기준 포트와 진입 URL
- 상태: 결정 완료
- 후보:
  - 공개 메인: `https://metanova1004.com/marketplace`, `http://127.0.0.1:3000/marketplace`
  - 관리자 전용: `https://metanova1004.com/admin`, `http://127.0.0.1:3005/admin`
- 결정일:
- 결정자:
- 메모: 공개 메인 기본 진입은 `localhost` 호스트의 `/marketplace`, 관리자 기본 진입은 `metanova1004.com` 호스트의 `/admin`으로 고정한다. 개발 서버 직접 진입은 `3000/marketplace`, `3005/admin`을 기준으로 유지한다.

### 결정 3. 결제 방식
- 상태: 결정 완료
- 후보:
  - 시뮬레이션 유지
  - 실결제 연동
- 결정일:
- 결정자:
- 메모: 현재 운영 결제는 시뮬레이션 완료 반영 흐름으로 고정한다. 실결제 연동으로 판단하지 않으며, 실PG 도입 시 별도 구현/문서 갱신이 필요하다.

### 결정 4. 리뷰 기능
- 상태: 결정 완료
- 후보:
  - 완결 후 유지
  - 제거
- 확정 상태:
  - 구현됨
- 결정일:
- 결정자:
- 메모: 리뷰 기능은 구현됨으로 고정한다. 백엔드 리뷰 API, 평점 계산, 공개 상세 페이지의 리뷰 작성/조회 UI 연결과 실제 작성/조회 검증이 완료되었다.

### 결정 5. 고급 검색 / Stripe 기능
- 상태: 결정 완료
- 후보:
  - 실제 연결
  - 삭제
- 결정일:
- 결정자:
- 메모: `backend/marketplace/advanced_router.py`와 `backend/marketplace/stripe_service.py`는 현재 `backend/main.py`에 라우터 등록이 없고, 실소스 기준 호출도 확인되지 않아 현 상태는 제거 대상으로 본다. Stripe 결제는 현재 운영 플로우의 시뮬레이션 결제/완료 반영 경로와 별도이며, 재도입 시 명시적으로 재등록한다.

---

## 12. 완료 기준

다음 조건을 모두 만족하면 구조 정리 완료로 판단한다.

- [x] 단일 앱 미통합이지만 역할 분리 구조로 확정됨
- [x] 죽은 파일/죽은 라우터가 제거됨
- [x] 결제 상태가 문서와 코드에서 일치함
- [x] 리뷰 기능 상태가 명확해짐
- [x] README와 실제 런타임이 일치함
- [x] 핵심 사용자 플로우가 재검증됨
- [x] 헬스체크가 실제 상태를 반영함

---

## 13. 메모

- 현재 기본 시스템과 핵심 사용자 플로우는 실측 기준으로 정상이다.
- 가장 큰 잔여 구조 과제는 두 프런트 앱에 남아 있는 `lib/api.ts` 계열 중복을 안전하게 공용화할 수 있는 구조 재설계다.
- 결제는 시뮬레이션 정책으로 확정됐고, 리뷰 기능은 구현됨으로 검증 완료됐다.
- 고급 검색/Stripe 확장 경로는 현재 실서비스 범위에서 제거 또는 비노출 상태다.

---

## 14. RTX 5090 기준 운영 설정 가이드

### 목적
이 섹션은 RTX 5090 단일 GPU 기준으로 1분 광고 영상 기능을 가장 안전하게 운영하기 위한 권장 설정을 정리한다.

### 운영 원칙
- 영상 생성은 전용 엔진 기준으로 고정한다.
- 운영 환경에서는 fallback 성공처럼 보이는 우회 경로를 막는다.
- 1분 광고는 `60초 / 6컷 / 컷당 10초 / 1작업 직렬 처리`를 기본 기준으로 둔다.
- LLM 모델은 Q5 중심으로 보수적으로 사용한다.
- self-run worker는 운영에서는 비활성화하고 필요 시에만 수동 검토 후 사용한다.

---

## 15. `.env` 권장안

### 운영용
```
APP_ENV=production

VIDEO_DEDICATED_ENGINE_URL=https://video-engine.your-domain.com/dedicated
VIDEO_DEDICATED_SUBMIT_PATH=/jobs
VIDEO_DEDICATED_TIMEOUT_SEC=900
VIDEO_DEDICATED_POLL_SEC=2
VIDEO_ALLOW_LOCAL_DEDICATED_ENGINE=false
VIDEO_REQUIRE_GENERATIVE_ENGINE=true
VIDEO_ENGINE_FALLBACK_TO_INTERNAL=false
VIDEO_RENDER_QUEUE_NAME=video_render_queue

ENABLE_AD_ORDER_WORKER_BOOTSTRAP=true
ENABLE_SELF_RUN_VIDEO_WORKER_BOOTSTRAP=false

SELF_ENGINE_REQUIRE_FACE_IMAGE=true
SELF_ENGINE_MAX_RETRY=2
SELF_ENGINE_MIN_VIDEO_BYTES=200000
SELF_ENGINE_MIN_VIDEO_BYTES_PER_SEC=8000
SELF_ENGINE_MIN_DURATION_RATIO=0.75
SELF_ENGINE_MIN_DURATION_SECONDS=2.0

ORCH_FORCE_COMPLETE=false
ORCH_MAX_FORCE_RETRIES=2

LLM_MODEL_DEFAULT=qwen2.5-coder:32b-q5km
LLM_MODEL_REASONING=qwen2.5-coder:32b-q5km
LLM_MODEL_CODING=qwen2.5-coder:32b-q5km
LLM_MODEL_CHAT=qwen2.5-coder:32b-q4km
LLM_MODEL_VOICE_CHAT=qwen2.5-coder:32b-q4km
LLM_MODEL_PLANNER=qwen2.5-coder:32b-q5km
LLM_MODEL_CODER=qwen2.5-coder:32b-q5km
LLM_MODEL_REVIEWER=qwen2.5-coder:32b-q5km
LLM_MODEL_DESIGNER=qwen2.5-coder:32b-q4km
LLM_MODEL_SMART_PLANNER=qwen2.5-coder:32b-q5km
LLM_MODEL_SMART_EXECUTOR=qwen2.5-coder:32b-q5km
LLM_MODEL_SMART_DESIGNER=qwen2.5-coder:32b-q4km
```

### 개발용
```
APP_ENV=development

VIDEO_DEDICATED_ENGINE_URL=http://host.docker.internal:18082
VIDEO_DEDICATED_SUBMIT_PATH=/jobs
VIDEO_DEDICATED_TIMEOUT_SEC=1800
VIDEO_DEDICATED_POLL_SEC=3
VIDEO_ALLOW_LOCAL_DEDICATED_ENGINE=true
VIDEO_REQUIRE_GENERATIVE_ENGINE=false
VIDEO_ENGINE_FALLBACK_TO_INTERNAL=true
VIDEO_RENDER_QUEUE_NAME=video_render_queue

ENABLE_AD_ORDER_WORKER_BOOTSTRAP=true
ENABLE_SELF_RUN_VIDEO_WORKER_BOOTSTRAP=true

SELF_ENGINE_REQUIRE_FACE_IMAGE=true
SELF_ENGINE_MAX_RETRY=2
SELF_ENGINE_MIN_VIDEO_BYTES=200000
SELF_ENGINE_MIN_VIDEO_BYTES_PER_SEC=8000
SELF_ENGINE_MIN_DURATION_RATIO=0.75
SELF_ENGINE_MIN_DURATION_SECONDS=2.0

ORCH_FORCE_COMPLETE=false
ORCH_MAX_FORCE_RETRIES=4

LLM_MODEL_DEFAULT=qwen2.5-coder:32b-q4km
LLM_MODEL_REASONING=qwen2.5-coder:32b-q5km
LLM_MODEL_CODING=qwen2.5-coder:32b-q4km
LLM_MODEL_CHAT=qwen2.5-coder:32b-q4km
LLM_MODEL_VOICE_CHAT=qwen2.5-coder:32b-q4km
LLM_MODEL_PLANNER=qwen2.5-coder:32b-q5km
LLM_MODEL_CODER=qwen2.5-coder:32b-q4km
LLM_MODEL_REVIEWER=qwen2.5-coder:32b-q5km
LLM_MODEL_DESIGNER=qwen2.5-coder:32b-q4km
LLM_MODEL_SMART_PLANNER=qwen2.5-coder:32b-q5km
LLM_MODEL_SMART_EXECUTOR=qwen2.5-coder:32b-q4km
LLM_MODEL_SMART_DESIGNER=qwen2.5-coder:32b-q4km
```

### 설정 해설
- 운영은 `VIDEO_REQUIRE_GENERATIVE_ENGINE=true`로 실제 생성형 엔진만 허용한다.
- 운영은 `VIDEO_ENGINE_FALLBACK_TO_INTERNAL=false`로 실패를 숨기지 않는다.
- 운영은 `ENABLE_SELF_RUN_VIDEO_WORKER_BOOTSTRAP=false`로 경로를 단순화한다.
- 운영은 Q5 중심, 개발은 Q4/Q5 혼합으로 VRAM 여유를 확보한다.

---

## 16. `knowledge/orchestrator_runtime_config.json` 권장안

### 운영용
```
{
  "max_tokens_per_step": 1024,
  "default_request_max_tokens": 1024,
  "chat_request_max_tokens": 128,
  "default_agent_max_tokens": 1024,
  "planner_max_tokens": 1024,
  "coder_max_tokens": 1024,
  "reviewer_max_tokens": 1024,
  "step_timeout_sec": 60,
  "job_timeout_sec": 900,
  "agent_http_timeout_sec": 180,
  "planner_agent_timeout_sec": 60,
  "coder_agent_timeout_sec": 60,
  "reviewer_agent_timeout_sec": 60,
  "index_context_timeout_sec": 0,
  "planner_prompt_char_limit": 1200,
  "coder_prompt_char_limit": 1200,
  "reviewer_prompt_char_limit": 1200,
  "planner_context_char_limit": 0,
  "coder_context_char_limit": 0,
  "reviewer_context_char_limit": 0,
  "experience_memory_char_limit": 0,
  "forensic_max_inventory": 100,
  "max_force_retries": 2,
  "force_complete": false,
  "allow_synthetic_fallback": false,
  "code_generation_strategy": "auto_generator",
  "min_files": 27,
  "min_dirs": 3,
  "selected_profile": "rtx5090_32gb",
  "model_tuning_level": 0,
  "token_tuning_level": 0,
  "timeout_tuning_level": 1,
  "model_routes": {
    "default": "qwen2.5-coder:32b-q5km",
    "reasoning": "qwen2.5-coder:32b-q5km",
    "coding": "qwen2.5-coder:32b-q5km",
    "chat": "qwen2.5-coder:32b-q4km",
    "voice_chat": "qwen2.5-coder:32b-q4km",
    "planner": "qwen2.5-coder:32b-q5km",
    "coder": "qwen2.5-coder:32b-q5km",
    "reviewer": "qwen2.5-coder:32b-q5km",
    "designer": "qwen2.5-coder:32b-q4km",
    "smart_planner": "qwen2.5-coder:32b-q5km",
    "smart_executor": "qwen2.5-coder:32b-q5km",
    "smart_designer": "qwen2.5-coder:32b-q4km"
  },
  "execution_controls": {
    "default": { "acceleration_mode": "gpu_only", "num_gpu": -1 },
    "reasoning": { "acceleration_mode": "gpu_only", "num_gpu": -1 },
    "coding": { "acceleration_mode": "gpu_only", "num_gpu": -1 },
    "chat": { "acceleration_mode": "gpu_only", "num_gpu": -1 },
    "voice_chat": { "acceleration_mode": "gpu_only", "num_gpu": -1 },
    "planner": { "acceleration_mode": "gpu_only", "num_gpu": -1 },
    "coder": { "acceleration_mode": "gpu_only", "num_gpu": -1 },
    "reviewer": { "acceleration_mode": "gpu_only", "num_gpu": -1 },
    "designer": { "acceleration_mode": "gpu_only", "num_gpu": -1 },
    "smart_planner": { "acceleration_mode": "gpu_only", "num_gpu": -1 },
    "smart_executor": { "acceleration_mode": "gpu_only", "num_gpu": -1 },
    "smart_designer": { "acceleration_mode": "gpu_only", "num_gpu": -1 }
  },
  "advisory_controls": {
    "clarification_questions_enabled": true,
    "max_clarification_questions": 3,
    "evidence_panel_enabled": true,
    "max_evidence_items": 5,
    "next_action_suggestions_enabled": true,
    "max_next_actions": 3
  },
  "gpu_only_preferred": true,
  "config_path": "knowledge/orchestrator_runtime_config.json"
}
```

### 개발용
```
{
  "max_tokens_per_step": 1024,
  "default_request_max_tokens": 1024,
  "chat_request_max_tokens": 128,
  "default_agent_max_tokens": 1024,
  "planner_max_tokens": 1024,
  "coder_max_tokens": 1024,
  "reviewer_max_tokens": 1024,
  "step_timeout_sec": 60,
  "job_timeout_sec": 600,
  "agent_http_timeout_sec": 180,
  "planner_agent_timeout_sec": 60,
  "coder_agent_timeout_sec": 60,
  "reviewer_agent_timeout_sec": 60,
  "index_context_TIMEOUT_SEC": 0,
  "planner_prompt_char_limit": 1200,
  "coder_prompt_char_limit": 1200,
  "reviewer_prompt_char_limit": 1200,
  "planner_context_char_limit": 0,
  "coder_context_char_limit": 0,
  "reviewer_context_char_limit": 0,
  "experience_memory_char_limit": 0,
  "forensic_max_inventory": 100,
  "max_force_retries": 4,
  "force_complete": false,
  "allow_synthetic_fallback": true,
  "code_generation_strategy": "auto_generator",
  "min_files": 27,
  "min_dirs": 3,
  "selected_profile": "rtx5090_32gb",
  "model_tuning_level": 0,
  "token_tuning_level": 0,
  "timeout_tuning_level": 1,
  "model_routes": {
    "default": "qwen2.5-coder:32b-q4km",
    "reasoning": "qwen2.5-coder:32b-q5km",
    "coding": "qwen2.5-coder:32b-q4km",
    "chat": "qwen2.5-coder:32b-q4km",
    "voice_chat": "qwen2.5-coder:32b-q4km",
    "planner": "qwen2.5-coder:32b-q5km",
    "coder": "qwen2.5-coder:32b-q4km",
    "reviewer": "qwen2.5-coder:32b-q5km",
    "designer": "qwen2.5-coder:32b-q4km",
    "smart_planner": "qwen2.5-coder:32b-q5km",
    "smart_executor": "qwen2.5-coder:32b-q4km",
    "smart_designer": "qwen2.5-coder:32b-q4km"
  },
  "execution_controls": {
    "default": { "acceleration_mode": "gpu_only", "num_gpu": -1 },
    "reasoning": { "acceleration_mode": "gpu_only", "num_gpu": -1 },
    "coding": { "acceleration_mode": "gpu_only", "num_gpu": -1 },
    "chat": { "acceleration_mode": "gpu_only", "num_gpu": -1 },
    "voice_chat": { "acceleration_mode": "gpu_only", "num_gpu": -1 },
    "planner": { "acceleration_mode": "gpu_only", "num_gpu": -1 },
    "coder": { "acceleration_mode": "gpu_only", "num_gpu": -1 },
    "reviewer": { "acceleration_mode": "gpu_only", "num_gpu": -1 },
    "designer": { "acceleration_mode": "gpu_only", "num_gpu": -1 },
    "smart_planner": { "acceleration_mode": "gpu_only", "num_gpu": -1 },
    "smart_executor": { "acceleration_mode": "gpu_only", "num_gpu": -1 },
    "smart_designer": { "acceleration_mode": "gpu_only", "num_gpu": -1 }
  },
  "advisory_controls": {
    "clarification_questions_enabled": true,
    "max_clarification_questions": 3,
    "evidence_panel_enabled": true,
    "max_evidence_items": 5,
    "next_action_suggestions_enabled": true,
    "max_next_actions": 3
  },
  "gpu_only_preferred": true,
  "config_path": "knowledge/orchestrator_runtime_config.json"
}
```

### 설정 해설
- 운영은 `force_complete=false`, `allow_synthetic_fallback=false`로 강제 완료와 합성 우회를 막는다.
- 개발은 `allow_synthetic_fallback=true`로 디버깅 편리성을 확보한다.
- 운영은 `job_timeout_sec=900`으로 1분 광고 기준 타임아웃을 넉넉하되 과도하지 않게 유지한다.

---

## 17. 운영용 / 개발용 2벌 설정표

| 항목 | 운영용 | 개발용 | 기준 |
|---|---|---|---|
| 전용 엔진 URL | 외부 HTTPS 실엔진 | `host.docker.internal:18082` | 운영은 실엔진 고정 |
| `VIDEO_ALLOW_LOCAL_DEDICATED_ENGINE` | `false` | `true` | 운영은 로컬 의존 차단 |
| `VIDEO_REQUIRE_GENERATIVE_ENGINE` | `true` | `false` | 운영은 mock/우회 금지 |
| `VIDEO_ENGINE_FALLBACK_TO_INTERNAL` | `false` | `true` | 운영은 실패를 숨기지 않음 |
| `VIDEO_DEDICATED_TIMEOUT_SEC` | `900` | `1800` | 운영은 장애 감지 우선 |
| `VIDEO_DEDICATED_POLL_SEC` | `2` | `3` | 운영은 빠른 상태 확인 |
| `ENABLE_AD_ORDER_WORKER_BOOTSTRAP` | `true` | `true` | 기본 큐 소비 유지 |
| `ENABLE_SELF_RUN_VIDEO_WORKER_BOOTSTRAP` | `false` | `true` | 운영은 경로 단순화 |
| `SELF_ENGINE_REQUIRE_FACE_IMAGE` | `true` | `true` | 품질 안정성 유지 |
| `ORCH_FORCE_COMPLETE` | `false` | `false` | 억지 완료 금지 |
| `allow_synthetic_fallback` | `false` | `true` | 운영은 가짜 성공 금지 |
| 기본 LLM | `Q5KM` 중심 | `Q4KM/Q5KM` 혼합 | 운영은 보수적 VRAM 사용 |
| 채팅/디자이너 LLM | `Q4KM` | `Q4KM` | 경량 유지 |
| 작업 동시성 | `1` | `1~2` | 운영은 직렬 처리 |
| 1분 광고 컷 기준 | `6컷 x 10초` | `6컷 x 10초` | 가장 안전한 고정 규격 |
| 1차 렌더 품질 | `general/high` | `general/high` | `ultra`는 검증 후 |

---

## 18. 1분 광고 영상 기능 안전 운영 리스트

### 입력 기준
- [x] 길이는 `60초`로 고정한다.
- [ ] 컷 수는 `6컷`으로 제한한다.
- [ ] 컷 길이는 `10초`로 맞춘다.
- [ ] 입력 이미지는 `1~3장`으로 제한한다.
- [ ] 얼굴 기준 이미지는 필수로 받는다.
- [ ] 화면비는 작업당 하나만 선택한다.
  - [ ] `16:9`
  - [ ] `9:16`
  - [ ] `1:1`

### 실행 기준
- [ ] 활성 영상 작업은 항상 `1개`만 유지한다.
- [ ] 첫 렌더는 `general/high`로 진행한다.
- [ ] 완료 검증 후 필요한 경우에만 `youtube_web/ultra`를 사용한다.
- [ ] 큐 적체는 `2건 이하`로 유지한다.
- [ ] 영상 생성 중에는 대형 LLM 작업을 동시에 실행하지 않는다.
- [ ] 운영 환경에서는 self-run worker를 기본 비활성화한다.

### 실패 기준
- [ ] fallback으로 성공처럼 처리하지 않는다.
- [x] 자동 재시도는 최대 `2회`까지만 허용한다.
- [ ] 얼굴 기준 이미지가 없으면 즉시 실패 처리한다.
- [ ] 결과 길이가 과도하게 짧으면 실패 처리한다.
- [ ] 파일 크기 기준 미달 시 실패 처리한다.
- [ ] 전용 엔진 타임아웃 시 실패 처리한다.
- [x] 다운로드 파일 미생성 시 실패 처리한다.

### 완료 판정
- [x] 상태가 `completed`인지 확인한다.
- [x] mp4 파일이 실제 존재하는지 확인한다.
- [x] 길이가 60초 근접인지 확인한다.
- [x] 최소 바이트 기준을 통과하는지 확인한다.
- [ ] 컷 전환 continuity 이상이 없는지 확인한다.
- [ ] 마지막 10초에 CTA가 포함되는지 확인한다.

#### 18단계 실검증 메모
- 입력 기준 실측:
  - 실제 산출물 `uploads/tmp/video_connector_runs/video-connector-20260323012626-b18185eb/coffee_ad_60s.mp4`는 `ffprobe` 기준 `59.92초`로 확인됐다.
  - 같은 실행의 `sections.json` 총 길이는 `60초`다.
  - 다만 `sections.json` 기준 컷 수는 `8컷`이며 각 컷 길이도 `10초` 고정이 아니다. 현재 문서 목표(`6컷 x 10초`)와 실산출물이 불일치한다.
  - 입력 이미지 `1~3장`, 얼굴 기준 이미지 필수, 화면비 단일 선택(`16:9/9:16/1:1`)은 현재 실측 가능한 요청 샘플/검증 로그를 확보하지 못해 미판정으로 둔다.
- 실행 기준 실측:
  - `backend` 컨테이너 실환경값은 `VIDEO_ALLOW_LOCAL_DEDICATED_ENGINE=true`, `VIDEO_ENGINE_FALLBACK_TO_INTERNAL=true`, `VIDEO_REQUIRE_GENERATIVE_ENGINE=false`, `VIDEO_DEDICATED_TIMEOUT_SEC=2400`이다.
  - 이는 문서의 운영 기준(`local dedicated false`, `fallback false`, `generative true`, `timeout 900`)과 불일치한다.
  - `GET /api/marketplace/video-engine/self-run-worker/status` 실측 결과 `running=true`, `queue_depth=0`로 self-run worker가 활성 상태다. 문서의 운영 기준(`ENABLE_SELF_RUN_VIDEO_WORKER_BOOTSTRAP=false`)과 불일치한다.
  - 활성 작업 1개 유지, 첫 렌더 `general/high`, `youtube_web/ultra` 조건부 사용, 큐 적체 2건 이하, 영상 생성 중 대형 LLM 동시 실행 금지는 현재 저장소와 런타임만으로 확정 검증하지 못했다.
- 실패 기준 실측:
  - `knowledge/orchestrator_runtime_config.json`의 `max_force_retries=1`과 `backend/marketplace/models.py`의 `FeatureRetryQueue.max_attempts=3`가 혼재해 있다. 광고 주문 재시도의 최종 최대값을 문서 기준 `2회`로 확정했다고 보긴 어렵다.
  - `backend/marketplace/router.py` 다운로드 경로는 `output_video_key`/`output_file_key`가 없거나 실제 파일 바이트를 찾지 못하면 404로 실패 처리한다.
  - 얼굴 기준 이미지 누락, 과도하게 짧은 결과 길이, 파일 크기 최소 기준, 전용 엔진 타임아웃 실패 조건은 현재 코드/런타임에서 문서 수준으로 닫혀 있다고 확정하지 못했다.
- 완료 판정 실측:
  - 완료된 영상만 다운로드/미리보기 허용: `backend/marketplace/router.py`에서 `order.status == completed`를 강제한다.
  - 실제 mp4 존재: `coffee_ad_60s.mp4` 파일 존재, 크기 `258,488 bytes` 확인.
  - 실제 길이: `59.92초` 확인.
  - continuity/CTA는 `sections.json`에 `match_cut_continuity`, `브랜드 마감` 등 흔적은 있으나 자동 품질 검증 결과로 확정할 근거는 부족하다.

---

## 19. 현재 저장소 기준 추가 주의사항

- 현재 `docker-compose.yml`의 `video-worker` 서비스는 워크스페이스의 `scripts/start_video_render_worker.py`와 실행 경로가 일치한다.
- 다만 현재 구조에서 가장 안전한 운영 기준은 별도 worker 서비스 의존보다 `backend` 내부 ad worker bootstrap 중심으로 보는 것이 타당하다.
- 이 상태가 유지되면 운영 기준은 아래를 우선 적용한다.
  - `ENABLE_AD_ORDER_WORKER_BOOTSTRAP=true`
  - `ENABLE_SELF_RUN_VIDEO_WORKER_BOOTSTRAP=false`
- `scripts/ensure_fixed_admin_account.ps1`는 기본 관리자 비밀번호 fallback을 포함하므로 상용 배포 전 별도 보안 정리가 필요하다.
- nginx는 프런트 컨테이너가 아니라 호스트 개발 서버 포트에 의존하므로, 상용 배포 전 프런트 패키징 전략을 다시 정해야 한다.

---

## 20. 프로파일러 첫 사용 가이드

### 목적
이 섹션은 Visual Studio 환경에서 처음 성능 분석을 시작할 때 무엇을 먼저 봐야 하는지 정리한다.

### 언제 프로파일러를 쓰는가
- 앱이 느릴 때: `CPU` 기준으로 본다.
- 메모리 사용량이 비정상적으로 높을 때: `MEMORY` 기준으로 본다.
- 특정 함수가 느린지 확신이 없을 때: 먼저 프로파일러로 병목 위치를 찾는다.

### 처음 쓰는 순서
1. 느림인지 메모리 문제인지 먼저 분류한다.
2. 전체 병목이면 프로파일러로 trace를 먼저 캡처한다.
3. 특정 함수가 이미 의심되면 benchmark 또는 테스트 기반 측정을 먼저 검토한다.
4. 측정 결과 없이 바로 최적화하지 않는다.
5. 변경 후에는 같은 측정 경로로 다시 비교한다.

### Visual Studio 기준 권장 시작점
- CPU 문제
  - API 응답 지연
  - 오케스트레이터 실행 지연
  - 영상 처리 준비/큐 소비 지연
- MEMORY 문제
  - 장시간 실행 후 메모리 증가
  - 대형 모델 로딩 이후 회수 안 됨
  - 이미지/비디오 파이프라인 후 메모리 잔류

### 현재 저장소에서 먼저 볼 후보
- `backend.llm.orchestrator` 실행 경로
- `backend.marketplace.router`의 주문 처리 경로
- self-run 관련 승인/기록 생성 경로
- 영상 렌더 전후의 파일/큐 처리 경로

### 첫 분석 체크리스트
- [ ] 문제 유형이 CPU인지 MEMORY인지 구분했다.
- [ ] 전체 병목인지 특정 함수 병목인지 정했다.
- [ ] 기존 benchmark 존재 여부를 먼저 확인했다.
- [ ] 기존 테스트로 재현 가능한지 확인했다.
- [ ] baseline 측정을 먼저 확보했다.
- [ ] 변경 후 같은 조건으로 재측정했다.

### 문서화 규칙
- 측정 전 체감만으로 결론 내리지 않는다.
- before/after 수치를 남긴다.
- 측정 대상 함수, 입력, 데이터 크기, 환경을 함께 기록한다.

---

## 21. 비디오 엔진 생존 판정용 4종 샘플 체크폼

### 공통 정보
- 샘플 유형
  - [ ] 사람형
  - [ ] 동물형
  - [ ] 건물/실내/도시형
  - [ ] 자연/바다/배경형
- run_id:
- 생성 일시:
- 결과 mp4 경로:
- 프레임 폴더 경로:

### 1) 규격 판정
- [ ] 60초 정확히 충족
- [ ] 8fps 충족
- [ ] 총 480프레임 충족
- [ ] 프레임 480장 실제 보관
- [ ] mp4 실제 재생 가능

실측값
- duration:
- fps:
- total_frames:
- preserved_frames:

판정
- [ ] 통과
- [ ] 실패

### 2) 연속동작 판정
- [ ] 초반 모션이 자연스럽게 이어짐
- [ ] 중반 정지처럼 보이는 구간 없음
- [ ] 후반 정지처럼 보이는 구간 없음
- [ ] 마지막 10초 CTA 구간도 연속동작 유지
- [ ] `delta < 0.01` 연속 구간 없음

실측값
- early avg delta:
- mid avg delta:
- late avg delta:
- cta last 10s avg delta:
- stagnant run count:

판정
- [ ] 통과
- [ ] 실패

### 3) 도메인 형상 품질 판정

#### 사람형
- [ ] 얼굴 동일성 유지
- [ ] 손/손가락 붕괴 없음
- [ ] 팔/다리 비율 이상 없음
- [ ] 표정 변화 자연스러움

#### 동물형
- [ ] 같은 개체 유지
- [ ] 눈/귀/입 형상 안정
- [ ] 다리/꼬리 붕괴 없음
- [ ] 털/피부 질감 자연스러움

#### 건물/실내/도시형
- [ ] 수직선/수평선 안정
- [ ] 원근 왜곡 없음
- [ ] 창문/벽 패턴 붕괴 없음
- [ ] 카메라 이동 중 구조 유지

#### 자연/바다/배경형
- [ ] 파도/수면 continuity 유지
- [ ] 하늘/구름 flicker 없음
- [ ] 광원 방향 일관
- [ ] 배경 랜덤 점프 없음

판정
- [ ] 통과
- [ ] 실패

### 4) CTA 판정
- [ ] 마지막 10초 CTA 존재
- [ ] CTA 포즈 변화가 살아 있음
- [ ] 제품 또는 핵심 대상이 명확히 보임
- [ ] 행동 유도 메시지가 읽힘
- [ ] CTA 구간이 정지처럼 보이지 않음

실측값
- CTA start frame:
- CTA end frame:
- CTA min delta:
- CTA avg delta:

판정
- [ ] 통과
- [ ] 실패

### 5) 최종 판정
- [ ] 실사용 가능
- [ ] 조건부 보완 필요
- [ ] 실사용 불가

실패 사유 요약
- 형상:
- 배경:
- 모션:
- CTA:
- 기타:

### 최종 생존 기준
- 비디오 엔진 유지
  - [ ] 4종 모두 규격 통과
  - [ ] 4종 모두 연속동작 통과
  - [ ] 4종 모두 도메인 품질 통과
  - [ ] 4종 모두 CTA 통과
- 영화 스튜디오 엔진 전환 검토
  - [ ] 4종 중 2종 이상 핵심 실패
  - [ ] 사람형 또는 동물형 치명 실패
  - [ ] 배경/건물 continuity 반복 실패
  - [ ] CTA/중후반 정지 현상 재발

---

## 22. 부록: 배포 전 최종 점검표

### 배포 구조
- [x] 단일 앱 미통합이지만 공개 메인(`frontend`) / 관리자 전용(`frontend/frontend`) 역할 분리 구조가 문서와 런타임 기준으로 확정되었는지 확인
- [x] 호스트 dev server 의존 없이 배포 가능한지 확인
- [x] `docker-compose.yml`와 실제 실행 파일이 일치하는지 확인
- [x] nginx 라우팅과 실제 서비스 경로가 일치하는지 확인

### 보안
- [x] 고정 관리자 계정 fallback 제거
- [x] 운영 비밀번호/토큰을 `.env` 외부 안전 저장소로 분리
- [x] 외부 엔진 API 키 관리 정책 수립
- [x] 운영 HTTPS 인증서 경로와 갱신 절차 재검증

### 기능
- [x] 결제 방식이 시뮬레이션인지 실연동인지 명확히 문서화
- [x] 리뷰 기능 상태를 명확히 문서화
- [x] dead route와 미등록 라우터 정리 완료
- [x] 자동 코딩/self-run 결과 검증 루틴 정리 완료

### 운영
- [x] 헬스체크가 HTTP 200 외 실질 상태를 판정하는지 확인
- [x] 백업/로그/오류 추적 경로가 문서화됐는지 확인
- [x] 장애 시 롤백 절차가 있는지 확인
- [x] 운영용/개발용 설정 분리가 실제 반영되는지 확인
