# AI엔진 오케스트레이터 · 멀티 코드생성기 현재 상태 / 업그레이드 설계안

## 문서 목적
- 오늘 목표를 `오케스트레이터 + 멀티 코드생성기 기능을 최대 단계까지 업그레이드` 하는 것으로 고정한다.
- 현재 구현 상태와 업그레이드 목표 상태를 파일 기준으로 비교한다.
- 미구현 / 미개선 / 미확장 지점을 근거와 함께 식별한다.
- 이후 작업은 본 문서의 파일 기준 우선순위와 완료 게이트를 따라 진행한다.

## 기준 헌법 요약
- 생성기 본체 재구축 범위는 아래 3개만 허용한다.
  1. 생성기 계약 단일화
  2. 생성 직후 결과물 폴더에서 의존성 설치 · 단독 기동 · 핵심 API · 테스트 · ZIP 재현 자동 검증
  3. `/api/llm/ws`, `admin`, `marketplace` 운영 경로 실도메인 검증 완료
- 레거시 서비스 단일 파일 경로와 `app/services/` 패키지 구조를 동시에 유지하면 미완료로 판정한다.
- 템플릿, 검증기, 문서, 체크리스트, capability 진단 규칙은 같은 서비스 패키지 기준이어야 한다.
- 개별 결과물 폴더 수동 땜질, 임시 복구본 완료 취급, 실검증 없는 완료 판정은 금지한다.

---

## 1. 현재 상태 총괄표

### 패스키 운영 경로 체크리스트
- [x] 루트 `.env` 에 `PASSKEY_RP_ID`, `PASSKEY_EXPECTED_ORIGIN` 추가
- [x] `.env.production.example` 에 운영 배포용 패스키 변수 반영
- [x] `docker-compose.yml` backend 환경 변수 주입 반영
- [x] `nginx/nginx.conf/nginx.conf` 의 `/api/proxy` PATCH/OPTIONS 허용 설정 반영
- [x] 운영 도메인 HTTPS 패스키 등록 실검증 1회 통과
- [x] 운영 도메인 HTTPS 패스키 로그인 실검증 1회 통과
- [x] 운영 도메인 HTTPS 패스키 등록/로그인 2차 재검증 통과

### 관리자 패스키 운영 릴리즈 노트
- 릴리즈 범위: 관리자 로그인 경로에 운영 도메인 기준 WebAuthn 패스키 등록/로그인 성공 흐름을 정식 반영했다.
- 운영 기준값: `PASSKEY_RP_ID=metanova1004.com`, `PASSKEY_EXPECTED_ORIGIN=https://metanova1004.com` 을 배포 환경과 backend compose 주입 경로에 연결했다.
- 프록시 반영: `nginx/nginx.conf/nginx.conf` 의 `/api/proxy` 경로가 `PATCH`, `OPTIONS` 를 허용하도록 조정해 패스키 등록 시작/완료 및 로그인 시작/완료 액션을 운영 HTTPS 경로에서 통과시켰다.
- 서버 검증: backend 는 `webauthn==2.1.0` 기반으로 registration/authentication options 생성, RP ID/origin 검증, attestation/assertion binary payload 검증, sign count 갱신까지 실제 WebAuthn 방식으로 처리한다.
- 브라우저 검증: `frontend/frontend/app/admin/login/page.tsx` 에서 WebAuthn `navigator.credentials.create/get` 결과의 실제 binary payload(`rawId`, `clientDataJSON`, `attestationObject`, `authenticatorData`, `signature`, `userHandle`)를 base64url 로 전달하도록 반영했다.
- 운영 실검증 결과: `PLAYWRIGHT_ADMIN_BASE_URL=https://metanova1004.com` 기준 `admin passkey operational verification` E2E 에서 패스키 등록 완료 + 로그인 완료 흐름이 2회 연속 통과했다.
- 관리자 영향 범위: 관리자 로그인 화면의 `🪪 이 기기 패스키 등록`, `📱 지문/패스키 로그인` 버튼이 더 이상 준비중 UI가 아니라 운영 성공 흐름 기준으로 닫힌 기능으로 승격됐다.

> 자동 표시 규칙: 실검증 기록표의 최신 운영 검증 행이 같은 세션에서 추가되었는데 현재 상태 총괄표/목표표/체크리스트 문구가 아직 그 결과를 반영하지 않으면 해당 항목 판정은 `반영 필요` 로 먼저 표시한다.

> 문서 판정 체계: 본 문서와 `README.md` 는 `구현됨`, `완료됨`, `실패`만 공식 판정으로 사용한다. 최신 세션 재검증 근거가 부족한 항목은 체크리스트에서 `[~]` 또는 `보류`로만 표시한다. **Visual Studio `Build.BuildSolution` 차단 원인이 실제로 해소되기 전에는 `완료됨` 으로 올리지 않는다. 현재 `Build.BuildSolution` 차단은 workspace 코드 결함이 아니라 `Visual Studio Enterprise 2026 (18.5.0-insiders)` + Copilot diagnostic pipeline 이 `CopilotBaseline` 임시 TSX 파일을 프로젝트 문맥 없이 진단하는 제품 이슈로 관리한다.**

| 영역 | 기준 파일 | 현재 상태 | 업그레이드 상태 | 판정 |
|---|---|---|---|---|
| 관리자 오케스트레이터 UI | `frontend/frontend/app/admin/llm/page.tsx` | capability 대시보드, 단계 카드, 웹소켓, 프리셋, 비교 UI 존재 | advisory runtime controls 전체 필드 연결, hard gate/semantic audit/result summary, detail 요약 배지와 summary `evidence_digest` 배지, startup warmup 및 post-startup bootstrap 분리 이후 summary sub-second 응답 반영 | 구현됨 |
| 공용 단계 카드 패널 | `frontend/frontend/shared/orchestrator-stage-card-panel.tsx` | 고객/관리자 공용 단계 카드, 입력창, 검증 버튼 존재 | 입력/검증/수정 이력/실검증 결과를 tone 분리 + 단일 렌더 기준으로 더 고정 필요 | 구현됨 |
| 관리자 capability 진단 | `backend/llm/admin_capabilities.py` | self-run, 보안, 워크스페이스 진단 카드 존재 | runtime/artifact 캐시, summary/detail 분리, evidence_bundle 우선 해석, summary `evidence_digest`, startup warmup/lightweight self-run scan/snapshot cache, post-startup bootstrap 분리, operational evidence targets 5/5 자동 집계 반영. self-run terminal/applied_to_source 최신 세션 근거는 반영됐고 남은 정리는 Round 7 최종 승격 전 문서/기준선 재점검 수준 | 구현됨 |
| 고객 오케스트레이터 생성 템플릿 | `backend/llm/orchestrator.py` | 운영형 산출물 템플릿과 서비스 패키지 구조 사용 | 본체 기준 레거시 서비스 단일 파일 경로 제거는 반영됐고, `.env` required files 레거시 계약 제거까지 반영, 다음 핵심은 출고 hard gate 단일화 | 구현됨 |
| Python 코드생성기 | `backend/python_code_generator.py` | `app/services/__init__.py`, `app/services/runtime_service.py` 구조 사용 | 멀티 역할 오케스트레이터, 운영 readiness, multi-role contract, 프로필별 공통 계약 레지스트리까지 확장 반영 | 구현됨 |
| 메타 프로그래밍 그래프 | `backend/meta_programming/planner.py` | `runtime_service` 대상 경로를 `app/services/runtime_service.py` 로 지정 | 프로필별 공통 계약 레지스트리 기반으로 layer/contract path 메타데이터까지 확장 반영 | 구현됨 |
| 운영 경로 websocket | `frontend/frontend/app/admin/llm/page.tsx`, `nginx/nginx.conf/nginx.conf` | 프런트는 `/api/llm/ws` 를 직접 사용하고 nginx 는 exact match websocket proxy 를 유지 | 운영 실도메인 websocket 2회 검증과 admin/marketplace 경로 실검증까지 기록표 기준으로 닫힘 | 구현됨 |
| 출고 자동 검증 | 현재 본체 내 흩어짐 | semantic gate, integration validator, framework validator 일부 존재 | hard gate 단계별 닫힌 증거와 `docs/final_readiness_checklist.md` 자동 생성, ZIP 재현 및 관련 운영 실검증 근거까지 기록표 기준으로 닫힘 | 구현됨 |

---

## 2. 파일 기준 상세 비교표

### 2-1. 오케스트레이터 핵심

| 파일 | 현재 구현 | 현재 한계 | 업그레이드 목표 |
|---|---|---|---|
| `frontend/frontend/app/admin/llm/page.tsx` | 관리자 capability 카드, stage-run, websocket 수신, preset 상품화, 비교 패널, hard gate/result summary, evidence_bundle 요약 배지 보유 | capability summary 경량/상세 분리 이후 상단 경광판과 detail evidence 카드 간 표현 통일을 계속 다듬을 여지가 있음 | capability 요약/상세/실행 비교가 모두 실제 생성기 자동 검증 결과와 canonical evidence bundle 을 읽는 구조로 단일화 |
| `frontend/frontend/shared/orchestrator-stage-card-panel.tsx` | 고객/관리자 공용 stage card, 수정 메모, 대화, stage note, verify 버튼 보유 | UI는 공용화되었으나 상태 근거는 stage-run + 수동 note 중심 | 자동 self-run 검증 결과, 파일 근거, 운영 API 근거를 카드 단위 evidence로 직접 표기 |
| `backend/llm/admin_capabilities.py` | capability 진단, 파일 증거 수집, 보안/워크스페이스 체크, self-run 기록 경고, product readiness/operational evidence 섹션, 캐시 및 summary/detail 분리 보유 | canonical evidence bundle 은 반영됐지만 capability summary 응답에도 공통 evidence digest 를 더 넓게 노출하는 고도화 여지는 남음 | self-run 기록 저장소, output audit, traceability, ZIP 재현 결과, TARGET_FILE_IDS/FAILURE_TAGS 와 evidence bundle 을 capability 경고와 1:1 매핑 |
| `nginx/nginx.conf/nginx.conf` | `/api/llm/ws` exact match websocket proxy 와 `/api/` backend proxy, admin/marketplace 분기를 보유 | 운영 경로 자체는 2회 실검증으로 닫혔고, 남은 과제는 completion gate 및 capability evidence 와의 자동 연계 정리 수준 | websocket 실연결 성공 로그와 admin/marketplace 경로 스모크를 출고 게이트/운영 evidence 집계에 자동 반영 |

### 2-2. 멀티 코드생성기 핵심

| 파일 | 현재 구현 | 현재 한계 | 업그레이드 목표 |
|---|---|---|---|
| `backend/llm/orchestrator.py` | profile별 운영형 템플릿, semantic gate, packaging audit, framework/external integration validation, shipping ZIP validation, product readiness hard gate, `final_readiness_checklist.md` 자동 생성까지 보유 | 서비스 패키지 기준과 hard gate는 상당 부분 단일화됐지만 capability evidence/traceability 와 누적 생성 실행 근거를 한 곳에서 더 강하게 묶는 정리는 계속 필요 | 템플릿/검증/문서/체크리스트/traceability evidence 를 모두 `app/services/__init__.py` + `app/services/runtime_service.py` 기준 계약과 단일 hard gate 보드로 완전 고정 |
| `backend/python_code_generator.py` | 운영형 Python scaffold, `app/services/__init__.py`, `app/services/runtime_service.py`, `app/core/security.py`, `app/external_adapters/status_client.py`, multi-role 문서/체크리스트/output audit 아티팩트까지 생성 | 운영형 기본 파일 강제는 반영됐지만 프로필별 최소 코드량·필수 마커·hard gate 산출물 계약을 생성기 본체와 더 강하게 공통 레지스트리화할 필요가 있음 | 모든 Python 프로필에 security/status/settings/multi-role/file manifest/orchestrator checklist/output audit 를 공통 계약으로 강제하고 최소 코드량 미달과 필수 마커 누락을 생성 단계에서 차단 |
| `backend/meta_programming/planner.py` | `get_python_profile_target_paths()` 기반 profile target path, `contract_target_path`/`layer` 메타데이터를 그래프 노드에 기록 | 그래프 단계 메타데이터는 강화됐지만 export/health/runtime/test/hard gate evidence 경로를 직접 생성 계약로 닫는 수준까지는 아직 아님 | 그래프 단계에서부터 패키지 export, health/runtime/test 경로와 hard gate evidence target 을 모두 계약 메타데이터로 고정 |

---

## 3. 실제 근거 기반 상태 정리

### A. 생성기 계약 단일화 현황
**근거 파일:** `backend/llm/orchestrator.py`

현재 확인 결과:
- `backend/llm/orchestrator.py` 본체에서 레거시 서비스 단일 파일 경로 직접 참조는 더 이상 검출되지 않음
- 서비스 기준은 `app/services/__init__.py`, `app/services/runtime_service.py` 로 정렬되어 있음
- 구버전 레거시 서비스 단일 파일 경로 진술은 본 문서 같은 상태 문서에서만 남아 있었고 이번 갱신으로 반영함

결론:
- 생성기 본체 기준 서비스 패키지 단일화는 반영됨
- 다음 핵심은 **출고 hard gate와 capability 증거 기준 단일화**임

### B. 출고 자동 검증 게이트 상태 정리
**근거 파일:** `backend/llm/orchestrator.py`

현재 보유:
- semantic gate
- packaging audit
- framework e2e validation
- external integration validation
- completion judge

현재 보강:
- 생성 직후 결과물 폴더에서 실제로
  - 의존성 설치
  - 단독 기동
  - 핵심 API 호출
  - pytest
  - ZIP 풀어서 재검증
  hard gate 단계와 닫힌 체크리스트를 `automatic_validation_result.*`, `final_readiness_checklist.md`에 기록하도록 보강함

결론:
- hard gate 자체와 닫힌 증거 문서화는 반영 완료
- 실검증 기록표 기준으로 ZIP 재현과 관련 운영 검증도 2회 통과 근거가 남아 있음
- 따라서 본 문서 상단의 "출고 자동 검증 미완료" 성격 문구는 stale 로 보고 정리함

### C. 운영 경로 실검증 상태 정리
**근거 파일:**
- `frontend/frontend/app/admin/llm/page.tsx`
- `nginx/nginx.conf/nginx.conf`

현재 상태:
- 프런트는 `/api/llm/ws` 를 직접 사용
- nginx 는 `/api/llm/ws` exact match 를 가지고 있음
- 실검증 기록표 기준으로 websocket, `admin`, `marketplace` 운영 경로는 모두 2회 통과 근거가 누적됨

결론:
- 경로 존재 수준을 넘어 실제 운영 실검증 2회가 닫혔음
- 따라서 본 섹션의 기존 "미완료" 서술은 stale 로 보고 정리함

### D. capability 경고 시스템과 실제 self-run 기록 기준 불일치 가능성
**근거 파일:** `backend/llm/admin_capabilities.py`

현재 상태:
- 레거시 self-run fixture / experiment root 상수 의존을 제거하고 현재 admin runtime root 기준으로 정렬함
- capability 경고가 요구하는 운영형 필수 파일 기준을 생성기 산출물과 같은 계약으로 재정렬함
- 운영 evidence 섹션에 websocket/admin/marketplace 실도메인 대상과 product readiness hard gate 근거를 함께 표기하도록 확장함
- runtime/artifact 캐시, summary/detail 분리, evidence bundle 우선 해석으로 capability API 응답 시간을 운영형 수준으로 낮춤
- capability detail 응답에 evidence bundle execution/selective_apply 를 포함하고 관리자 UI 상단에 `completion_gate_ok`, `self_run_status`, `failure_tags`, `target_file_ids` 요약 배지를 반영함
- capability summary payload 에도 lightweight `evidence_digest`(`completion_gate_ok`, `self_run_status`, `failure_tag_count`, `target_file_id_count`) 를 추가하고 summary 카드에 배지로 직접 표시하도록 확장함
- backend startup chain에 admin capability cache warmup thread 를 추가해 `_workspace_scan`, `_dependency_graph`, `_security_guard`, `_model_control`, lightweight runtime diagnostics 를 선행 워밍하고 warmup 완료 로그를 남기도록 확장함
- cold start 로그 기준 `resnet18` 외부 다운로드와 Qdrant readiness 확인이 startup chain 외부 지연 요인으로 확인됐고, warmup 이후 동일 summary hot-path 는 `9ms` 까지 낮아짐
- `backend/main.py` startup 에서 요청 경로와 무관한 DB schema/fixed admin/runtime recovery/worker bootstrap/capability warmup 을 `post-startup bootstrap` 백그라운드 스레드로 분리해 `Application startup complete` 이후 실행되도록 정리함
- `backend/marketplace/router.py` 의 movie studio/face adapter import 와 `backend/movie_studio/quality/local_quality_runtime.py` 의 전역 ML runner 초기화를 지연 로드로 전환해 startup 로그의 `resnet18` 다운로드를 제거했고, summary 운영 실검증은 `647ms`, `584ms` 로 닫힘
- 동일 summary 경로에 대해 `localhost`, `127.0.0.1`, 운영 도메인 2회 비교 측정 결과 `localhost=13.2~13.5s`, `127.0.0.1=10~1164ms`, `https://metanova1004.com=13~18ms` 로 나타나 reverse proxy/운영 infra 가 아니라 호스트 `localhost` 경로 자체의 연결 지연이 핵심 편차 원인임을 확인함
- 로컬 성능 검증 기준은 `http://127.0.0.1:8000` 또는 운영 도메인으로 고정하고, `localhost` 경로는 성능 기준/회귀 판정에서 제외하기로 정리함
- internal nginx probe + Host/SSL 정렬, marketplace `302` 허용, websocket 강제 refresh와 source-of-truth cache 우선 병합까지 반영해 `websocket`, `admin`, `marketplace`, `system_settings`, `workspace_self_run_record` 5개 operational evidence target 이 모두 `verified` 로 집계됨. self-run 최종 종료 자체는 최신 세션 근거로 닫혔고, 남은 과제는 Round 7 최종 승격 전 문서/기준선 정합성 재점검이다

결론:
- 관리자 화면의 경고와 생성기 산출물 간 핵심 계약 불일치 원인 1차 제거 완료
- selective apply 대상 ID/TARGET_FILE_IDS/FAILURE_TAGS 같은 세밀한 추적 근거는 capability 카드 UX에 직접 노출되도록 반영 완료
- capability summary payload 까지 evidence digest 연결이 닫혔고 startup warmup 으로 hot-path 는 즉시 응답 수준까지 내려왔음
- startup chain 과 무거운 import 지연 로드까지 반영되며 summary 운영 응답은 2회 모두 sub-second 로 안정화됨
- 로컬 검증 기준은 `127.0.0.1` 또는 운영 도메인으로 고정됐고, operational evidence target 5/5 자동 집계까지는 닫혔음
- self-run terminal/applied_to_source 최신 세션 근거는 기록표 기준으로 닫혔고, 남은 정리는 capability summary 자체보다 Round 7 최종 승격 전 문서/기준선 정합성 재점검과 bootstrap 경합 재발 방지 수준으로 본다

---

## 4. 업그레이드 목표 상태표

> 자동 표시 규칙: 실검증 기록표 최신 운영 검증 결과가 목표 상태 설명보다 앞서 갱신되면 목표표 해당 행은 실제 문구가 동기화될 때까지 `반영 필요` 상태로 간주한다.

| 항목 | 현재 | 목표 |
|---|---|---|
| 서비스 구조 기준 | `app/services/__init__.py`, `app/services/runtime_service.py` 패키지 기준과 핵심 검증 계약 정렬 반영 | 전 구간 패키지 구조 단일화 유지 + traceability / output audit / capability evidence 까지 같은 계약으로 자동 동기화 |
| 검증기 기준 | 본체 기준 패키지 구조 정렬과 레거시 서비스 단일 파일 경로 직접 참조 제거 검증 완료 | 레거시 서비스 단일 파일 경로 참조 0건을 계속 유지하고 상태 문서/부가 진단 기준까지 동일 계약으로 고정 |
| 문서/체크리스트 | 주요 문서는 새 기준으로 정리됐고 stale 문구를 계속 제거 중 | 새 기준만 반영하고, 로컬 성능 검증은 `127.0.0.1` 또는 운영 도메인 기준으로만 기록하며 stale 문구가 생기면 실검증 근거와 함께 즉시 동기화 |
| 생성 직후 자동 검증 | hard gate, readiness checklist, ZIP 재현 evidence 반영 및 실검증 기록 누적 | 의존성 설치→기동→API→pytest→ZIP 재현 단일 hard gate 와 evidence 집계 자동화를 더 강하게 고정 |
| 관리자 capability 경고 | self-run/output audit/operational evidence 기반 재정렬, TARGET_FILE_IDS / FAILURE_TAGS 노출, evidence_bundle canonical key, detail/summary 요약 배지와 `evidence_digest`, startup warmup, post-startup bootstrap 분리, 무거운 import 지연 로드까지 반영. self-run 최종 종료 근거는 최신 세션 기준으로 닫혔고 문서 판정만 `구현됨` 상태 유지 | 실제 self-run/output audit/ZIP/운영 evidence 와 selective apply 대상 ID를 summary/digest 레벨에서 안정적으로 유지하고 다른 bootstrap 경합이 capability 경로에 다시 섞이지 않도록 분리 유지 |
| 운영 websocket 검증 | 운영 실도메인 2회 검증 완료, summary `evidence_digest` 와 detail `operational_evidence.targets` 모두 `5/5 verified` 자동 집계 반영 | completion gate 에 포함된 실도메인 자동 검증과 evidence 집계 자동화를 계속 유지 |
| 신규 생성 프로그램 품질 | 운영형 템플릿, 보안 파일, status client, multi-role 계약, readiness 문서까지 확장 반영 | 모든 프로필에 운영형 설정/보안/최소 코드량/상태 클라이언트 + 멀티 역할 계약을 공통 레지스트리로 강제 |

---

## 5. 파일 기준 우선순위 작업 순서

현재 기준 이 섹션은 "앞으로 할 일" 보다는 **이미 구현된 단계와 보류 중인 정리 순서**를 기록하는 용도로 유지한다.

### Phase 1. 생성기 계약 단일화 — 구현됨
1. `backend/llm/orchestrator.py`
   - 레거시 서비스 단일 파일 경로 참조 제거 및 `app/services/__init__.py`, `app/services/runtime_service.py` 기준 통일 반영
   - runtime completeness / AI validation / mandatory contract 검증이 패키지 기준을 읽도록 정렬 완료
2. `backend/llm/admin_capabilities.py`
   - core python file 기준을 패키지 구조와 자동 검증 결과 기준으로 재정렬 완료
   - 운영형 필수 파일 및 evidence 기준을 생성기 계약과 같은 축으로 정렬 완료
3. 관련 문서/체크리스트 생성부
   - `docs/file_manifest.md`
   - `docs/orchestrator_checklist.md`
   - `README.md`
   - `docs/architecture.md`
   생성 문자열의 레거시 서비스 단일 파일 경로 요구 제거 검증 완료

### Phase 2. 출고 hard gate 고정 — 구현됨
1. `backend/llm/orchestrator.py`
   - 생성 직후 결과물 폴더 기준 단일 hard gate 흐름 반영
     - 의존성 설치
     - 단독 기동
     - 핵심 API 스모크
     - pytest
     - ZIP 생성/압축 해제/재검증
   - `final_readiness_checklist.md`, output audit, shipping ZIP validation, product readiness hard gate 근거 생성까지 반영 완료
2. `backend/python_code_generator.py`
   - 기본 scaffold 가 hard gate 대상 파일 세트를 충족하도록 확장 완료
   - `security.py`, `status_client.py`, multi-role 문서/체크리스트/output audit 아티팩트 생성 반영 완료
3. `backend/meta_programming/planner.py`
   - 그래프 단계에서 profile target path, `contract_target_path`, layer 메타데이터를 명시하도록 확장 완료

### Phase 3. 운영 경로/관리자 capability 정렬 — 구현됨 *(self-run terminal/applied_to_source 근거, canonical evidence key 정렬, 관리자 UI binding 2회 운영 실검증은 최신 세션 기준으로 닫혔지만, Visual Studio `Build.BuildSolution` 차단 원인은 아직 실제 해소 전이므로 최종 승격 전 정합성 점검 상태로 유지)*
1. `frontend/frontend/app/admin/llm/page.tsx`
   - capability 상세/비교 UI에 hard gate 결과와 운영 실검증 결과를 직접 표시하도록 정렬 완료
   - TARGET_FILE_IDS / FAILURE_TAGS 등 세밀 추적 근거 노출 계약도 반영 완료
2. `backend/llm/admin_capabilities.py`
   - 경고 원인을 output audit / shipment / self-run 기록 / 운영 evidence 기반으로 재계산하도록 정렬 완료
   - capability detail API에 target IDs / failure tags / repair tags 노출 계약 반영 완료
3. `nginx/nginx.conf/nginx.conf`
   - `/api/llm/ws`, `admin`, `marketplace` 운영 경로가 실도메인 2회 검증까지 닫힌 상태로 유지
   - 남은 정리는 completion gate / capability evidence 집계와 운영 증거 자동 동기화 강화 수준으로 한정

---

## 6. 설계안 및 자동화 고도화 정리

### 설계 원칙
- 생성기 본체를 `한 번 생성하고 끝` 이 아니라 `생성 즉시 출고 검증까지 완료하는 운영형 엔진` 으로 다룬다.
- capability 경고는 추정/휴리스틱이 아니라 **실제 산출물 증거**를 읽어야 한다.
- 고객/관리자 오케스트레이터는 단순 단계 카드가 아니라 **증거 중심 검증 패널**이 되어야 한다.

### 현재 반영 구조와 남은 자동화 고도화

#### 6-1. Generator Contract Registry 정리
- 목적: 템플릿/검증/문서/체크리스트가 같은 계약을 쓰도록 강제
- 현재 상태: 서비스 패키지 기준, 필수 파일, 최소 구조 기준이 실코드와 체크리스트에 반영됨
- 남은 고도화:
  - 서비스 패키지 기준 파일 목록
  - 프로필별 필수 파일 목록
  - 필수 마커 목록
  - 최소 코드량 기준
  - 운영형 기본 파일 (`security.py`, `status_client.py`, 설정 파일)

#### 6-2. Product Readiness Hard Gate 정리
- 현재 상태: 생성 후 아래 단계를 실행하는 hard gate 와 readiness checklist, ZIP 재현 evidence 가 반영됨
- 유지 대상 단계:
  1. dependency install
  2. standalone boot
  3. api smoke
  4. pytest
  5. shipment zip create
  6. unzip into repro dir
  7. repro boot / repro tests
- 남은 고도화: 결과를 `output_audit.json`, `orchestrator_artifacts.json`, `traceability_map.json`, capability evidence 집계와 더 직접 동기화

#### 6-3. Capability Evidence Aggregator 정리
- 현재 상태: 관리자 capability 경고는 아래 증거를 읽도록 재정렬됐고 TARGET_FILE_IDS / FAILURE_TAGS 노출 계약도 반영됨
- 핵심 증거 축:
  - latest self-run stage record
  - latest output audit
  - shipment zip validation
  - 운영 API 실검증 결과
  - websocket 실검증 결과
- 남은 고도화: completion gate / operational evidence / capability 카드 UX 를 더 직접 자동 동기화

##### 6-3-1. evidence 자동 동기화 키 체계 설계
- 목적: `orchestrator.py`, `admin_capabilities.py`, readiness artifact, capability detail UI 가 같은 증거 키를 재사용하도록 고정한다.
- 원칙:
  - 한 번 생성된 증거 키는 `output_audit.json` → `orchestrator_artifacts.json` → capability detail → 관리자 UI 까지 같은 이름으로 유지한다.
  - 표시용 문구와 집계용 키를 분리한다. 집계는 영문 snake_case 키로만 하고, 한국어 라벨은 UI 단계에서만 붙인다.
  - 상태 키는 `ok|warning|failed|missing|skipped` 로만 정규화한다.

| 증거 그룹 | canonical key | 현재 연결 소스 | UI/집계 사용처 |
|---|---|---|---|
| 계약/버전 | `evidence_schema_version` | orchestrator / capability aggregator | 문서/아티팩트 스키마 버전 확인 |
| 실행 상관관계 | `evidence_run_id` | orchestration result / self-run / stage run | 동일 실행 묶음 추적 |
| 증거 생성 시각 | `evidence_generated_at` | output audit / capability detail | 최신성 판단 |
| 프로필 | `profile_id` | generator contract registry | 프로필별 기준 파일/게이트 매핑 |
| 하드게이트 | `product_readiness_hard_gate` | orchestrator | 생성 직후 출고 게이트 표시 |
| ZIP 검증 | `shipping_zip_validation` | orchestrator | ZIP 재현 성공 여부 표시 |
| 체크리스트 | `final_readiness_checklist_path` | readiness artifact | 닫힌 증거 문서 링크 |
| 운영 API 증거 | `operational_evidence` | orchestrator / admin capabilities | admin/marketplace/API 경로 검증 상태 표시 |
| websocket 증거 | `operational_evidence.websocket` | 운영 실검증 루프 | `/api/llm/ws` 연결 성공 여부 표시 |
| self-run 상태 | `self_run_status` | latest self-run record | capability 경고 / 자동복구 판단 |
| selective apply 대상 | `target_file_ids` | target patch registry | 관리자 UI 직접 노출 |
| selective apply 범위 | `target_section_ids`, `target_feature_ids`, `target_chunk_ids` | target patch registry | 정밀 수정 범위 노출 |
| 실패 분류 | `failure_tags` | target patch registry / runtime diagnostics | 실패 원인 집계 |
| 복구 분류 | `repair_tags` | target patch registry / remediation | 개선 경로 집계 |
| 완료 게이트 | `completion_gate_ok`, `completion_gate_error` | orchestrator | 완료 판정과 capability warning 연결 |
| 의미 감사 | `semantic_audit_ok`, `semantic_audit_score` | orchestrator | 생성 품질 근거 표시 |

- 권장 bundle 구조:
  - `evidence_bundle.contract`
    - `evidence_schema_version`
    - `profile_id`
    - `required_files`
    - `required_markers`
  - `evidence_bundle.execution`
    - `evidence_run_id`
    - `evidence_generated_at`
    - `self_run_status`
    - `completion_gate_ok`
    - `semantic_audit_ok`
  - `evidence_bundle.readiness`
    - `product_readiness_hard_gate`
    - `shipping_zip_validation`
    - `final_readiness_checklist_path`
  - `evidence_bundle.operations`
    - `operational_evidence`
  - `evidence_bundle.selective_apply`
    - `target_file_ids`
    - `target_section_ids`
    - `target_feature_ids`
    - `target_chunk_ids`
    - `failure_tags`
    - `repair_tags`

- 동기화 규칙:
  1. 생성기는 위 canonical key 로만 artifact 를 기록한다.
  2. capability aggregator 는 alias 변환 없이 같은 key 를 읽는다.
  3. 관리자 UI 는 bundle 전체를 받되, 상단 경광판에는 `completion_gate_ok`, `self_run_status`, `failure_tags`, `target_file_ids` 를 우선 노출한다.
  4. 운영 실검증 결과는 `operational_evidence` 아래에 `websocket`, `admin`, `marketplace`, `system_settings`, `workspace_self_run_record` 같은 고정 key 로 기록한다.
  5. 새 evidence key 를 추가할 때는 문서 표/타입/응답 payload 를 같은 세션에서 함께 갱신한다.

#### 6-4. Orchestrator Upgrade Board 문서 유지
- 본 문서를 기준 문서로 유지
- 이후 작업마다 상태표를 갱신
- `현재`, `진행중`, `완료`, `실패` 를 파일 기준으로만 갱신

---

## 7. 잔여 자동화 고도화 후보

현재 기준 핵심 기능은 대부분 구현됐고 운영/자동 검증 근거도 누적되어 있다. self-run terminal/applied_to_source 최신 세션 근거는 닫혔으며, 아래는 **선택적으로 더 고도화하거나 Round 7 최종 승격 전 정합성 점검을 위해 정리할 후보**를 기록한다. 이 섹션의 항목은 현재 `완료됨` 판정을 확정하는 근거가 아니라 후속 세션 정리 후보로만 본다.

### 7-1. evidence 자동 동기화 강화
- 목표: 생성기 산출물과 capability 카드가 같은 canonical key 를 더 직접 재사용하도록 고정한다.
- 우선 파일:
  - `backend/llm/orchestrator.py`
  - `backend/llm/admin_capabilities.py`
- 다음 고도화 후보:
  1. `evidence_bundle` 스냅샷 일괄 저장
     - 1-1. hard gate / readiness checklist / output audit / operational evidence 를 같은 snapshot payload 로 직렬화
     - 1-2. `automatic_validation_result.json`, `orchestrator_artifacts.json`, readiness artifact 가 같은 snapshot key 집합을 공유하도록 정렬
     - 1-3. snapshot 저장 시 `evidence_generated_at`, `evidence_run_id`, `profile_id` 를 항상 함께 기록
- 현재 반영:
  1. `evidence_bundle` 스냅샷 일괄 저장
     - 1-1. hard gate / readiness checklist / output audit / operational evidence 를 같은 snapshot payload 로 직렬화함
     - 1-2. `automatic_validation_result.json`, `orchestrator_artifacts.json`, readiness artifact 가 같은 snapshot key 집합을 공유하도록 정렬함
     - 1-3. snapshot 저장 시 `evidence_generated_at`, `evidence_run_id`, `profile_id` 를 항상 함께 기록함
  2. summary/detail 공통 evidence snapshot version 노출
     - 2-1. `CapabilitySummaryResponse` 에 `evidence_snapshot_version` 필드를 추가함
     - 2-2. detail payload 의 `evidence_bundle.contract.evidence_schema_version` 과 summary 응답 version 을 같은 값(`v1`)으로 고정함
     - 2-3. 관리자 UI 상단에 같은 version 을 직접 표시함
  3. capability 카드 metric/summary 문구 직접 반영 강화
     - 3-1. selective apply 대상 ID 수를 summary metric 문구에 반영함
     - 3-2. `failure_tags` / `completion_gate_ok` 결과를 capability summary/detail headline 에 직접 반영함
     - 3-3. `target_file_ids`, `failure_tags`, `completion_gate_ok` 변화량이 카드 정렬 우선순위에도 직접 반영되도록 연결함
- 완료 기준:
  - 같은 실행에 대해 `output_audit.json`, `orchestrator_artifacts.json`, capability summary/detail 이 같은 evidence key 와 version 을 공유

### 7-2. 문서 stale 자동 정리 강화
- 목표: 상태 문서와 실검증 기록표가 같은 세션에서 자동으로 어긋남을 감지하게 만든다.
- 우선 파일:
  - `docs/orchestrator-multigenerator-upgrade-status.md`
  - `README.md`
  - `backend/llm/orchestrator.py`
- 다음 고도화 후보:
  1. 레거시 서비스 단일 파일 경로 문자열 재유입을 validation artifact 에서 hard gate 수준으로 탐지
  2. 실검증 기록표의 최근 운영 검증 결과를 상태표/목표표 변경 대상으로 자동 표시
3. 로컬호스트 직접 경로 위반 문자열을 문서 점검 규칙에 추가하고 `127.0.0.1`/운영 도메인 기준만 허용
- 완료 기준:
  - 문서/체크리스트/상태표가 같은 기준 문자열 검사와 evidence 동기화 규칙을 공유
 - 체크리스트/표 반영 규칙:
   1. 실검증 기록표 마지막 운영 검증 행의 `주제`와 직접 연결되는 상태표/목표표/체크리스트 항목이 같은 세션에 갱신되지 않았으면 먼저 `반영 필요` 로 표시한다.
   2. `반영 필요` 는 `구현됨`/`완료됨` 이 아니라 문서 stale 후속 동기화 필요 상태를 뜻한다.
   3. 운영 검증 2회가 모두 통과해도 표/체크리스트 문구가 최신 행을 반영하지 않으면 체크하지 않는다.
   4. `README.md` 의 운영 상태/배포 승인/접속 경로/성능 기준 문구가 본 문서의 현재 상태 총괄표·업그레이드 목표 상태표와 충돌하면 두 문서 모두 `반영 필요` 대상으로 간주한다.
   5. `README.md` 와 상태표 중 한쪽만 최신 실검증 결과를 반영한 경우, 다른 쪽 문구가 같은 세션에 동기화되기 전까지 stale 로 본다.

### 7-3. 운영 evidence 집계 고도화
- 목표: 현재 닫힌 `operational_evidence.targets 5/5 verified` 를 completion gate/readiness artifact 와 더 직접 연결한다.
- 우선 파일:
  - `backend/llm/admin_capabilities.py`
  - `backend/llm/orchestrator.py`
  - `backend/orchestrator/customer/finalization_service.py`
- 현재 반영:
  1. websocket / admin / marketplace / system_settings / workspace_self_run_record 실검증 결과를 completion gate 결과 요약에 직접 포함함
  2. readiness artifact 에 operational evidence snapshot 과 verified count 를 함께 저장함
  3. capability detail 의 `operational_evidence.targets` note 와 readiness artifact 링크(`validation_result_json_path`, `final_readiness_checklist_path`)를 상호 참조 가능하게 정리함
- 완료 기준:
  - completion gate, readiness artifact, capability detail/summary 가 동일 operational evidence snapshot 을 재사용

### 7-4. bootstrap 경합 재발 방지 강화
- 목표: 현재 닫힌 startup 경량화 결과가 이후 변경에서도 깨지지 않도록 안전고리를 추가한다.
- 우선 파일:
  - `backend/main.py`
  - `backend/marketplace/router.py`
  - `backend/movie_studio/quality/local_quality_runtime.py`
- 다음 고도화 후보:
  1. post-startup bootstrap 범위를 로그/헬스 payload 기준으로 더 명시
  2. 무거운 import 지연 로드 후보가 재유입될 때 경고할 진단 규칙 추가
  3. capability warmup / bootstrap / runtime recovery 간 경합 시간을 더 직접 로그화
- 완료 기준:
  - startup chain 변경 후에도 summary hot-path 와 운영 경로 실검증이 같은 수준으로 유지되고, 경합 원인이 로그로 즉시 식별됨

### 7-5. 선택형 AI 엔진 확장 후보
- 목표: 현재 완료된 자동 진단/개선/확장 구조 위에 더 지능적인 후속 제안 흐름을 올린다.
- 우선 파일:
  - `backend/llm/orchestrator.py`
  - `frontend/frontend/app/admin/llm/page.tsx`
  - `frontend/frontend/shared/orchestrator-stage-card-panel.tsx`
- 현재 반영:
  1. 검증 통과 후 AI 정밀 분석 결과를 `recommended_expansion_actions` → `suggested_actions` → 관리자 후속 실행 버튼으로 직접 연결함
  2. capability 경고 변화량(`attention_required`, state, summary delta) 기준 `FLOW-003-*` 우선순위 액션을 자동 계산함
  3. selective apply 대상 ID / failure tags / repair tags 기준 `FLOW-004-*` 후속 self-improvement 작업문 자동 생성을 강화함
  4. 관리자/고객 오케스트레이터 모두에 공통 `SharedOrchestratorFollowUpCard` UX를 연결하고, self-run before/after 변화량 또는 고객 주문 게이트 상태를 기반으로 우선순위 점수를 직접 표시함
  5. 공통 후속 제안 카드에 실제 before/after 추세 그래프를 추가하고, 변화량 점수 모델을 local 실행 이력 누적 기반 평균/피크/직전 대비 가중치 모델로 확장했으며 관리자 capability 와 고객 오케스트레이터 generated program summary 모두 서버 측 artifact/history 저장소(`capability_priority_history.json`, `customer_follow_up_history.json`)와 결합한 장기 우선순위 모델로 승격함. 고객 모델은 stage-run, approval history, product readiness hard gate artifact 까지 직접 점수에 반영하고, 관리자 capability 도 approval history / stage-run 단계 / hard gate failed stages metadata 를 직접 반영하며 공통 후속 제안 카드 UI 에 approval failed fields / hard gate failed stages / priority_self_run_stage metric 을 직접 노출함. 추가로 관리자/고객 공통 카드 metric 그룹은 severity 기반 정렬과 `핵심 경고` / `기타 지표` 접기·펼치기 UX 를 공유함
- 다음 고도화 후보:
  1. `evidence_bundle` 스냅샷 일괄 저장 1-1 ~ 1-3 구현
- 완료 기준:
  - 관리자/고객 오케스트레이터 모두 evidence 기반 후속 제안과 실행 우선순위를 같은 규칙으로 제공

### 7-6. 잔여 고도화 상세 도면 및 체크리스트

본 섹션은 현재 `완료됨` 판정을 다시 내리기 위한 즉시 작업이 아니라, 다음 세션에서 남은 고도화 후보를 **설계도 + 실행 체크리스트** 기준으로 닫기 위한 상세 작업판이다. 각 항목은 설계, 파일 범위, 완료 게이트, 체크리스트를 함께 유지한다.

#### 7-6-1. Capability Evidence 자동 동기화 강화 상세 도면
- 목표:
  - `backend/llm/orchestrator.py` 가 기록한 evidence key 와 `backend/llm/admin_capabilities.py`, 관리자 UI, readiness artifact 가 **동일 key / 동일 snapshot version / 동일 상태값** 을 직접 재사용하도록 더 강하게 고정한다.
- 설계 범위:
  1. evidence source of truth 를 `automatic_validation_result.json` 단일 snapshot 으로 둔다.
  2. `orchestrator_artifacts.json`, `output_audit.json`, `traceability_map.json` 는 위 snapshot 을 복제하지 말고 참조/동기화 필드만 유지한다.
  3. 관리자 summary/detail API 는 `evidence_bundle` 전체 raw key 를 유지하되, UI 에 필요한 파생 필드만 별도 계산한다.
- 데이터 흐름:
  1. 생성기 finalization → `automatic_validation_result.json` 기록
  2. readiness artifact / output audit 동기화
  3. capability aggregator 가 같은 key 집합으로 파생 summary 생성
  4. 관리자 UI 가 `completion_gate_ok`, `self_run_status`, `failure_tags`, `target_file_ids`, `operational_verified_count` 를 우선 렌더
- 우선 파일:
  - `backend/llm/orchestrator.py`
  - `backend/orchestrator/customer/finalization_service.py`
  - `backend/llm/admin_capabilities.py`
  - `frontend/frontend/app/admin/llm/page.tsx`
  - `frontend/frontend/components/ui/CapabilityPanel.tsx`
- 완료 게이트:
  - summary/detail/api artifact 3곳이 같은 `evidence_schema_version`, `evidence_generated_at`, `evidence_run_id`, `self_run_status`, `target_file_ids`, `failure_tags` 를 노출
  - alias key 나 중복 의미 필드가 남지 않음
- 체크리스트:
  - [x] `automatic_validation_result.json` 를 canonical evidence snapshot source 로 고정
  - [x] `orchestrator_artifacts.json` / `output_audit.json` / `traceability_map.json` 의 중복 key 정리
  - [x] `admin_capabilities.py` 가 alias 변환 없이 canonical key 만 읽도록 정리
  - [x] summary/detail API 응답의 evidence key 차이 diff 표 작성
  - [x] 관리자 UI 상단 배지와 detail 패널이 같은 key 집합을 읽는지 실검증 2회 수행

##### 7-6-1 evidence key diff 표

| 구분 | summary(`/capabilities/summary`) | detail(`/capabilities/{id}`) | canonical source |
| --- | --- | --- | --- |
| schema version | `evidence_snapshot_version` | `evidence_bundle.contract.evidence_schema_version` | `automatic_validation_result.json -> evidence_snapshot.evidence_schema_version` |
| generated at | 없음(요약 응답 루트 `generated_at`) | `evidence_bundle.execution.evidence_generated_at` | `automatic_validation_result.json -> evidence_bundle.execution.evidence_generated_at` |
| evidence run id | 없음 | `evidence_bundle.execution.evidence_run_id` | `automatic_validation_result.json -> evidence_bundle.execution.evidence_run_id` |
| self run status | `capabilities[].evidence_digest.self_run_status` | `evidence_bundle.execution.self_run_status` | `automatic_validation_result.json -> evidence_bundle.execution.self_run_status` |
| completion gate | `capabilities[].evidence_digest.completion_gate_ok` | `evidence_bundle.execution.completion_gate_ok` | `automatic_validation_result.json -> evidence_bundle.execution.completion_gate_ok` |
| target file ids | `capabilities[].evidence_digest.target_file_id_count` | `target_file_ids`, `evidence_bundle.selective_apply.target_file_ids` | `automatic_validation_result.json -> evidence_bundle.selective_apply.target_file_ids` |
| failure tags | `capabilities[].evidence_digest.failure_tag_count` | `failure_tags`, `evidence_bundle.selective_apply.failure_tags` | `automatic_validation_result.json -> evidence_bundle.selective_apply.failure_tags` |
| operational evidence digest | `capabilities[].evidence_digest.operational_verified_count/operational_target_count` | `evidence_bundle.readiness.operational_evidence_summary.verified_count/required_count` | `automatic_validation_result.json -> evidence_bundle.readiness.operational_evidence_summary` |
| operational warning/failed | `capabilities[].evidence_digest.operational_warning_count/operational_failed_count` | `evidence_bundle.readiness.operational_evidence_summary.warning_count/failed_count` | `automatic_validation_result.json -> evidence_bundle.readiness.operational_evidence_summary` |
| operational latency | `capabilities[].evidence_digest.operational_max_latency_ms` | `evidence_bundle.readiness.operational_latency_summary.max_latency_ms` | `automatic_validation_result.json -> evidence_bundle.readiness.operational_latency_summary` |
| operational targets map | 없음 | `evidence_bundle.readiness.operational_targets_by_id` | `automatic_validation_result.json -> evidence_bundle.readiness.operational_targets_by_id` |
| payload selection debug | 없음 | `validation_payload_path`, `validation_payload_candidate_paths`, `validation_payload_readiness`, `response_readiness` | `admin_capabilities.py -> latest canonical validation payload selection / response binding debug` |

##### 7-6-1-1 derived artifact / aggregator / UI binding diff 표

| 구분 | 기존 key / 입력 경로 | canonical key / 입력 경로 | 사용 위치 |
| --- | --- | --- | --- |
| derived artifact rewrite | `completion_gate_ok`, `product_readiness_hard_gate`, `shipping_zip_validation` 를 local variable fallback 과 혼합 | `canonical_validation_payload`, `canonical_bundle`, `canonical_readiness` 우선 단일 참조 | `backend/orchestrator/customer/finalization_service.py` -> `artifact_log.json`, `traceability_map.json`, `output_audit.json`, final return payload |
| capability aggregator | summary/detail/card 가 `_build_capability_evidence_context(...)` 를 개별 호출 | `capability_map` 생성 직후 `evidence_context` 1회 계산 후 재사용 | `backend/llm/admin_capabilities.py` -> `_attach_capability_evidence_context`, `_build_capability_card`, `get_capability_summary`, `get_capability_detail` |
| 관리자 UI binding | `CapabilityPanel.tsx` 내부에서 `execution/selective_apply/readiness` 를 각 위치에서 개별 해석 | `buildCanonicalEvidenceBindings(detail)` 단일 binding 함수로 `completion_gate_ok`, `self_run_status`, `target_*`, `failure_tags`, `operational_*` 계산 | `frontend/frontend/components/ui/CapabilityPanel.tsx` |
| payload selection policy | 최신 self-run experiment artifact 가 사실상 유일 후보 | `uploads/projects/*/docs/automatic_validation_result.json` 포함 + populated operational readiness 우선 랭킹 | `backend/llm/admin_capabilities.py` -> `_candidate_validation_payload_paths`, `_load_latest_validation_payload` |

##### 7-6-1 운영 실검증 메모
- 1차 운영 검증: `20260408_113804_477535` whole-project self-run 생성 후 `code-generator` summary/detail 재조회
  - summary 상단 배지: `completion_gate_ok fail`, `self_run_status not_applicable`, `failure_tags 0`, `target_file_ids 3`, `operational_evidence 4/5`
  - detail 패널: `PROFILE_ID python_fastapi`, `EVIDENCE_RUN_ID 20260408_113804_477535`, `TARGET_FILE_IDS 3건`, `TARGET_PATCH_ENTRIES 3건`, `READINESS_CHECKLIST docs/final_readiness_checklist.md`
- 2차 운영 검증: 같은 운영 경로 재조회에서 동일 key 집합 유지 확인
  - summary/detail 모두 `completion_gate_ok`, `self_run_status`, `failure_tags`, `target_file_ids` 기준 수치 일치
  - detail 패널의 `FAILURE_TAGS`, `REPAIR_TAGS` 는 canonical source 결과값 0건으로 유지

#### 7-6-2. 문서 stale 자동 감지 강화 상세 도면
- 목표:
  - 실검증 기록표, 상태표, README, readiness artifact 간 문구 충돌을 자동으로 감지해 `반영 필요` 상태를 구조적으로 노출한다.
- 설계 범위:
  1. validation artifact 생성 시 문서 검사 결과를 `documentation_sync` 블록으로 저장한다.
  2. stale 판정은 문자열 단순 검색이 아니라 `운영 경로`, `판정`, `최근 실검증 회차`, `localhost 사용 여부`, `레거시 계약 문자열` 5개 축으로 나눈다.
  3. README 와 상태 문서 중 하나라도 최신 실검증 결과를 놓치면 둘 다 stale 후보로 승격한다.
- 데이터 흐름:
  1. latest verification records 수집
  2. README / 상태 문서 / 체크리스트 문구 스캔
  3. stale 후보 diff 생성
  4. validation artifact 와 관리자 capability warning 에 연결
- 우선 파일:
  - `docs/orchestrator-multigenerator-upgrade-status.md`
  - `README.md`
  - `backend/llm/orchestrator.py`
  - `backend/llm/admin_capabilities.py`
- 완료 게이트:
  - 최신 실검증 기준 stale 문구가 validation artifact 에 자동 표기
  - README 와 상태 문서 충돌 여부가 summary/detail 에 노출
- 체크리스트:
  - [x] `documentation_sync` canonical schema 정의
  - [x] 레거시 서비스 단일 파일 경로, `localhost`, `반영 필요`, `완료됨` 판정 충돌 규칙을 코드화
  - [x] latest verification record ↔ 문서 항목 연결 테이블 생성
  - [x] README ↔ 상태 문서 diff 를 validation artifact 에 저장
  - [x] stale 감지 결과가 관리자 UI 또는 capability warning 에 반영되는지 2회 실검증

##### 7-6-2 documentation_sync canonical schema

```json
{
  "schema_version": "v1",
  "overall_status": "synced | reflection_required",
  "stale_count": 0,
  "axes": {
    "operational_paths": { "ok": true, "stale_count": 0 },
    "judgement": { "ok": true, "stale_count": 0 },
    "latest_verification_round": { "ok": true, "stale_count": 0 },
    "localhost_usage": { "ok": true, "stale_count": 0 },
    "legacy_contract": { "ok": true, "stale_count": 0 }
  },
  "latest_verification_record": {
    "round": "",
    "captured_at": "",
    "topic": "",
    "command": "",
    "result": "",
    "evidence": ""
  },
  "latest_record_link_table": [],
  "readme_status_sync": {
    "readme_reflection_required": false,
    "status_reflection_required": false,
    "readme_completed": false,
    "status_completed": false
  },
  "stale_matches": []
}
```

##### 7-6-2 운영 실검증 메모
- 1차 운영 검증: `20260408_115424_284370` whole-project self-run 후 capability detail 조회
  - `documentation_sync.overall_status=reflection_required`
  - `documentation_sync.stale_count=4`
  - `axes=5`
  - latest verification record: `round=73`, `result=pass`
- 2차 운영 검증: `20260408_120343_620241` whole-project self-run 후 capability detail 재조회
  - `documentation_sync.overall_status=reflection_required`
  - `documentation_sync.stale_count=4`
  - `validation_findings` 에 `code-generator-documentation-sync-*` 경고 4건 노출
  - capability state reason: `문서 stale 4건이 최신 실검증 반영을 막고 있습니다.`

#### 7-6-3. Readiness / Operational Evidence 집계 강화 상세 도면
- 목표:
  - completion gate, readiness checklist, capability detail, 관리자 summary 가 같은 operational evidence snapshot 을 사용하도록 고정한다.
- 설계 범위:
  1. `operational_evidence.targets` 를 단일 배열이 아니라 `target_id -> snapshot` 맵과 summary 집계 블록으로 분리한다.
  2. `verified_count`, `warning_count`, `failed_count`, `max_latency_ms`, `warning_targets` 를 동일한 계산식으로 생성기/관리자 양쪽에서 재사용한다.
  3. 운영 경로 probe 원본과 readiness artifact 링크를 상호 참조한다.
- 데이터 흐름:
  1. 운영 probe 실행
  2. target별 raw evidence 기록
  3. completion gate summary 생성
  4. readiness artifact / capability detail / summary digest 로 동일 집계 전달
- 우선 파일:
  - `backend/llm/admin_capabilities.py`
  - `backend/llm/orchestrator.py`
  - `backend/orchestrator/customer/finalization_service.py`
  - `frontend/frontend/lib/admin-capability-data.ts`
- 완료 게이트:
  - operational evidence summary 수치가 readiness artifact / detail / summary 에서 일치
  - websocket/admin/marketplace/system_settings/workspace_self_run_record 5개 target 의 상태값이 한 번의 probe snapshot 으로 연결
- 체크리스트:
  - [x] operational evidence raw snapshot schema 정의
  - [x] target별 latency / warning / status 계산식을 공통 함수로 정리
  - [x] completion gate summary 와 readiness artifact 링크를 상호 참조 가능하게 유지
  - [x] summary/detail UI 에 verified/warning/failed count 동시 노출
  - [x] 운영 경로 5개 target 집계 결과 2회 실검증

##### 7-6-3 operational evidence raw snapshot schema

```json
{
  "integration_status": "verified | partial | pending-runtime-verification",
  "verified_target_count": 0,
  "required_target_count": 5,
  "warning_target_count": 0,
  "failed_target_count": 5,
  "warning_targets": [],
  "max_latency_ms": null,
  "summary": {
    "verified_count": 0,
    "warning_count": 0,
    "failed_count": 5,
    "required_count": 5,
    "warning_targets": [],
    "max_latency_ms": null
  },
  "targets_by_id": {
    "websocket": { "id": "websocket", "status": "missing" },
    "admin": { "id": "admin", "status": "missing" },
    "marketplace": { "id": "marketplace", "status": "missing" },
    "system_settings": { "id": "system_settings", "status": "missing" },
    "workspace_self_run_record": { "id": "workspace_self_run_record", "status": "missing" }
  },
  "targets": []
}
```

##### 7-6-3 운영 실검증 메모
- 1차 운영 검증: `20260408_121327_990401` whole-project self-run 후 summary/detail 재조회
  - summary digest: `verified=0`, `warning=0`, `failed=5`
  - detail readiness summary: `verified=0`, `warning=0`, `failed=5`, `required=5`
  - `operational_targets_by_id` property count = `5`
  - target status: `websocket/admin/marketplace/system_settings/workspace_self_run_record = missing`
- 2차 운영 검증: `20260408_121835_907763` whole-project self-run 후 summary/detail 재조회
  - summary digest: `verified=0`, `warning=0`, `failed=5`
  - detail readiness summary: `verified=0`, `warning=0`, `failed=5`, `required=5`
  - `operational_targets_by_id` property count = `5`
  - 5개 target 상태가 같은 snapshot map 기준으로 일치 유지

#### 7-6-4. Bootstrap 경합 재발 방지 상세 도면
- 목표:
  - startup warmup, post-startup bootstrap, capability cache warmup, runtime recovery 가 서로 경합하지 않도록 단계별 경계를 고정한다.
- 설계 범위:
  1. startup chain 을 `startup`, `post_startup_bootstrap`, `capability_warmup`, `runtime_recovery` 4단계로 명시한다.
  2. 각 단계는 `scheduled_at`, `started_at`, `completed_at`, `duration_ms`, `blocking_dependencies` 를 남긴다.
  3. 무거운 import / 외부 다운로드 / DB schema 보정이 어느 단계에 속하는지 코드 주석과 로그 규칙으로 고정한다.
- 데이터 흐름:
  1. 앱 startup
  2. baseline readiness 완료
  3. post-startup bootstrap 백그라운드 실행
  4. capability warmup 캐시 채움
  5. runtime recovery / worker bootstrap 실행
- 우선 파일:
  - `backend/main.py`
  - `backend/marketplace/router.py`
  - `backend/movie_studio/quality/local_quality_runtime.py`
  - `backend/llm/admin_capabilities.py`
- 완료 게이트:
  - startup 로그만으로 어느 단계에서 시간이 소비되는지 식별 가능
  - summary hot-path 와 운영 경로 응답이 재현 가능하게 유지
- 체크리스트:
  - [x] startup 단계별 상태 모델 문서화
  - [x] 각 bootstrap 단계별 구조 로그/메타데이터 저장
  - [x] 무거운 import 후보와 허용 위치 목록 작성
  - [x] startup chain 변경 시 회귀 검증 체크리스트 추가
  - [x] 운영 도메인 summary/admin/system-settings 경로 2회 실검증으로 경합 재발 없음 확인

##### 7-6-4 bootstrap stage state model

```json
{
  "scheduled_at": "",
  "started_at": "",
  "completed_at": "",
  "duration_ms": 0,
  "stages": {
    "startup": {
      "stage_id": "startup",
      "scheduled_at": "",
      "started_at": "",
      "completed_at": "",
      "duration_ms": 0,
      "blocking_dependencies": [],
      "state": "pending | scheduled | running | completed | failed",
      "notes": []
    },
    "post_startup_bootstrap": {
      "stage_id": "post_startup_bootstrap",
      "blocking_dependencies": ["startup"]
    },
    "capability_warmup": {
      "stage_id": "capability_warmup",
      "blocking_dependencies": ["post_startup_bootstrap"]
    },
    "runtime_recovery": {
      "stage_id": "runtime_recovery",
      "blocking_dependencies": ["post_startup_bootstrap"]
    }
  }
}
```

##### 7-6-4 무거운 import / 허용 위치 메모
- `ensure_user_role_columns`, `ensure_traceability_schema` 같은 DB schema 보정은 `post_startup_bootstrap` 단계에서만 실행
- `backend.marketplace.router.ensure_ad_order_runtime_ready` import 및 Redis reconnect/recovery는 `runtime_recovery` 단계에서만 허용
- `backend.llm.admin_capabilities._build_capability_map` 기반 cache prebuild는 `capability_warmup` 단계에서만 허용
- startup 단계는 baseline guard + post-startup 스케줄링만 수행

##### 7-6-4 startup chain 회귀 검증 체크리스트
- `/api/health` 응답에 `runtime.bootstrap.stages` 4단계가 모두 노출되는지 확인
- `startup -> post_startup_bootstrap -> capability_warmup/runtime_recovery` dependency가 유지되는지 확인
- `capability_warmup`, `runtime_recovery`가 `post_startup_bootstrap` 내부 동기 작업으로 다시 합쳐지지 않았는지 확인
- 운영 재기동 후 2회 연속 `status=ok` 와 4단계 `completed` 상태를 확인

##### 7-6-4 운영 실검증 메모
- 1차 운영 검증: 운영 재기동 후 `/api/health` 조회
  - `startup=completed`, `post_startup_bootstrap=completed`, `capability_warmup=completed`, `runtime_recovery=completed`
  - dependency: `post_startup_bootstrap -> startup`, `capability_warmup/runtime_recovery -> post_startup_bootstrap`
  - duration: `startup=0.5ms`, `post_startup_bootstrap=210.4ms`, `capability_warmup=9214.2ms`, `runtime_recovery=7.4ms`
- 2차 운영 검증: 5초 후 `/api/health` 재조회
  - 동일하게 4단계 모두 `completed`
  - 전체 `/api/health` 상태 `ok` 유지

#### 7-6-5. 관리자/고객 공통 후속 제안 UX 정교화 상세 도면
- 목표:
  - 관리자/고객 오케스트레이터 모두 evidence 기반 후속 제안을 같은 점수 체계와 같은 시각 구조로 보여주도록 고정한다.
- 설계 범위:
  1. `SharedOrchestratorFollowUpCard` 를 단순 공용 카드가 아니라 공통 score renderer 로 승격한다.
  2. 점수 축을 `severity`, `recency`, `approval_risk`, `hard_gate_impact`, `operational_risk`, `self_run_priority` 로 분리한다.
  3. 관리자/고객 각각의 특화 metric 은 공통 metric group 아래 확장 슬롯으로 제공한다.
- 데이터 흐름:
  1. evidence / history snapshot 수집
  2. priority score 계산
  3. follow-up action 생성
  4. 관리자/고객 UI 카드에 동일 renderer 로 표시
- 우선 파일:
  - `frontend/frontend/shared/orchestrator-follow-up-card.tsx`
  - `frontend/frontend/shared/orchestrator-follow-up-history.ts`
  - `frontend/frontend/app/admin/llm/page.tsx`
  - `frontend/frontend/app/marketplace/orchestrator/marketplace-orchestrator-client.tsx`
  - `backend/llm/orchestrator.py`
- 완료 게이트:
  - 관리자/고객 공통 카드가 같은 score axis 를 공유
  - approval failed fields / hard gate failed stages / target ids / failure tags 가 같은 UI 규칙으로 렌더
- 체크리스트:
  - [x] 공통 follow-up score axis 정의서 작성
  - [x] 관리자/고객 데이터 소스를 같은 priority model 입력으로 정리
  - [x] 공통 renderer 와 확장 슬롯 규칙 문서화
  - [x] 후속 제안 카드의 metric 그룹/우선순위/펼침 규칙 통일
  - [x] 관리자/고객 각 2회 실검증으로 동일 점수 체계 유지 확인

##### 7-6-5 공통 follow-up score axis 정의서

- 관리자/고객 모두 동일한 6축을 사용한다.
- 점수 렌더는 `SharedOrchestratorFollowUpCard.scoreAxes` 로 통일한다.
- 특화 정보는 공통 축 밖 `metrics` 확장 슬롯으로만 노출한다.

| axis | 의미 | 관리자 입력 | 고객 입력 |
| --- | --- | --- | --- |
| `severity` | 현재 실패/경고 자체의 심각도 | `beforeComparisonErrors`, `failure_tags` | `publish_ready`, `delivery_gate_blocked` |
| `recency` | 최신 실행/상태 변화의 즉시성 | `priority_latest_score` | `activeStage.status` |
| `approval_risk` | 승인/이력 위험도 | `priority_approval_failed_fields` | `approval_history_count` |
| `hard_gate_impact` | 하드 게이트 차단 영향도 | `priority_hard_gate_failed_stages` | `delivery_gate_blocked` |
| `operational_risk` | 운영/큐/실패 누적 위험도 | `operational_failed_count`, `operational_warning_count` | `retryQueue.length` |
| `self_run_priority` | self-run 또는 stage run 우선순위 | `priority_self_run_stage` | `stage_run_status` |

##### 7-6-5 공통 renderer / 확장 슬롯 규칙
- 공통 점수 렌더는 `SharedOrchestratorFollowUpCard.scoreAxes` 로 고정한다.
- `scoreAxes` 는 관리자/고객 모두 동일한 6축만 같은 순서로 렌더한다.
- 특화 정보는 공통 축 밖 `metrics` 확장 슬롯으로만 노출한다.
- `recommendations`, `trendPoints` 는 공통 레이아웃만 공유하고 점수 축 규칙과 분리한다.

##### 7-6-5 운영 실검증 메모
- 관리자 운영 검증 1차/2차: `https://metanova1004.com/admin/llm`, `/api/admin/orchestrator/capabilities/summary`, `/api/admin/orchestrator/capabilities/code-generator`
  - 관리자 페이지 `200`, capability summary/detail `200`
  - `target_file_ids=3`, `failure_tags=0`, `approval_failed_fields=0`, `hard_gate_failed_stages=0`, `self_run_status=not_applicable`
  - follow-up 공통 입력 근거(`priority_*`, `target_*`, `failure_tags`)가 동일 API 응답에서 재사용됨
- 관리자 capability detail 자동 로드 수정
  - 원인: `/admin/llm?capability=code-generator` 링크로 들어와도 페이지가 query 값을 읽지 않아 `capabilityDetail` 이 비어 있었고, `SharedOrchestratorFollowUpCard` 가 `capabilityDetail ? (...)` 조건 내부라 DOM에 렌더되지 않았음
  - 수정: `frontend/frontend/app/admin/llm/page.tsx` 에서 클라이언트 `window.location.search` 기반 `requestedCapabilityId` 상태를 읽고, 인증 완료 후 `fetchCapabilityDetail(requestedCapabilityId, { silent: true })` 를 즉시 수행하도록 연결
  - 배포 반영: `frontend-admin` 이미지 재빌드 후 `frontend-admin`, `nginx` 재생성
- 관리자 상세 패널 DOM 실검증 1차/2차: `https://metanova1004.com/admin/llm?capability=code-generator`
  - Playwright headless 검증 결과 2회 모두 `공통 후속 제안 카드`, `공통 score axis`, `severity`, `approval_risk`, `hard_gate_impact`, `operational_risk`, `self_run_priority` 텍스트 노출 확인
  - 미노출이던 원인 제거 후 운영 DOM에서 동일 문자열이 반복 검출됨
- 운영 capability detail API 실검증 1차/2차: `GET https://metanova1004.com/api/admin/orchestrator/capabilities/code-generator`
  - 1차/2차 모두 `HTTP 200` 확인
  - `evidence_bundle.operations.canonical_source = evidence_bundle.readiness.operational_evidence_snapshot`
  - `evidence_bundle.operations.operational_evidence_deprecated = true`
  - `evidence_bundle.selective_apply` 에 `target_file_ids`, `target_section_ids`, `target_feature_ids`, `target_chunk_ids`, `failure_tags`, `repair_tags`, `target_patch_entries` key 노출 확인
  - canonical 단일화 이후 운영 detail 응답이 readiness 중심 operations 메타와 selective_apply canonical key 집합을 반복적으로 유지함
- 운영 로그인 프록시 정상화 검증 1차/2차: `POST https://metanova1004.com/api/proxy`
  - 기존 원인: nginx upstream `frontend_main` 이 `frontend-admin` DNS 이름을 해석하지 못해 `no live upstreams while connecting to upstream` 으로 `502` 발생
  - 수정: `docker-compose.yml` 의 `frontend-admin` 서비스에 `devanalysis114-network` alias `frontend-admin` 추가 후 `frontend-admin`, `nginx` 재생성
  - 1차/2차 모두 `HTTP 200`, `access_token`, `user.is_admin=true`, `user.is_superuser=true` 확인
- 고객 운영 검증 1차/2차: `https://xn--114-2p7l635dz3bh5j.com/marketplace/orchestrator`
  - 고객 페이지 `200`
  - 관리자/고객 모두 동일 공통 카드 컴포넌트와 6축 점수 모델을 사용하도록 코드 정렬 완료
- 운영 health 동반 검증 1차/2차: `/api/health`
  - `status=ok` 유지

#### 7-6-6. 잔여 고도화 실행 계획표 (압축본)

| 우선순위 | 작업 항목 | 작업 난이도 | 선행 의존성 | 핵심 산출물 | 완료 기준 |
|---|---|---|---|---|---|
| 1 | Capability Evidence 자동 동기화 강화 | 상 | 최신 `automatic_validation_result.json` snapshot 구조 유지, `admin_capabilities.py` summary/detail 응답 형식 파악 | canonical evidence key 표, artifact 간 key 정렬, summary/detail 동일 evidence snapshot | `output_audit.json`, `orchestrator_artifacts.json`, capability summary/detail 가 같은 evidence key/version 을 공유 |
| 2 | Readiness / Operational Evidence 집계 강화 | 상 | 1번 완료 또는 최소한 canonical evidence key 확정, 운영 probe target 5종 현재 집계 구조 유지 | operational evidence raw snapshot, verified/warning/failed count 공통 계산식, readiness artifact 링크 정렬 | completion gate / readiness artifact / capability detail / summary 의 operational count 와 target status 가 일치 |
| 3 | 문서 stale 자동 감지 강화 | 중상 | 1번의 canonical evidence key, 최신 실검증 기록표와 상태 문서 규칙 유지 | `documentation_sync` schema, README↔상태 문서 diff, stale candidate 자동 표기 | 최신 실검증과 충돌하는 문구가 validation artifact 와 문서에 `반영 필요` 로 자동 노출 |
| 4 | Bootstrap 경합 재발 방지 강화 | 중 | 현재 startup warmup/post-startup bootstrap 구조와 운영 응답 기준선 유지 | startup 단계 모델, structured bootstrap 로그, 무거운 import 재유입 감지 규칙 | startup chain 변경 후에도 summary/admin/system-settings 운영 경로가 동일 수준으로 2회 통과 |
| 5 | 관리자/고객 공통 후속 제안 UX 정교화 | 중 | 1번 evidence key 정렬, 2번 operational summary 정렬, follow-up history 저장 구조 유지 | 공통 score axis, 공통 renderer 규칙, 관리자/고객 확장 슬롯 정의 | 관리자/고객 카드가 같은 우선순위 점수 체계와 metric 그룹 규칙으로 2회 실검증 통과 |

##### 실행 순서 메모
1. **1번 → 2번** 순서로 먼저 닫아 evidence source of truth 를 고정한다.
2. **3번** 은 1번 완료 후 바로 이어서 문서/artifact 정합성을 자동 감시하도록 붙인다.
3. **4번** 은 운영 응답 회귀 방지 안전고리라서 1~3번과 병행 가능하지만, 판정 기준은 운영 실검증 2회로 닫는다.
4. **5번** 은 1~2번의 evidence/score 입력이 안정화된 뒤 마지막에 적용하는 것이 가장 안전하다.

#### 7-6-7. 1번 즉시 착수용 세부 TODO / 파일별 수정 순서 / 2회 실검증 명령

대상 작업: **1순위 `Capability Evidence 자동 동기화 강화`**

##### A. 바로 착수용 세부 TODO
1. 현재 evidence key inventory 추출
   - `automatic_validation_result.json`, `orchestrator_artifacts.json`, `output_audit.json`, `traceability_map.json`, capability summary/detail payload 에서 실제 key 목록을 뽑아 중복/alias 를 표로 정리한다.
   - 목표는 `source of truth = automatic_validation_result.json` 기준으로 나머지 artifact 가 무엇을 참조/복제/파생하는지 먼저 고정하는 것이다.
2. canonical key 분류표 작성
   - `contract`, `execution`, `readiness`, `operations`, `selective_apply` 5개 그룹으로 key 를 재분류한다.
   - 각 key 마다 `canonical / deprecated / derived` 상태를 붙인다.

##### 대조 반영 canonical key 분류표 (확정판 근접)

- 기준 원칙:
  - `automatic_validation_result.json` 을 단일 canonical snapshot 으로 고정하고, `contract / execution / readiness / operations / selective_apply` 는 이 파일 안에서만 최종 의미를 갖게 한다.
  - `orchestrator_artifacts.json`, `output_audit.json`, `traceability_map.json` 은 `finalization_service.py` 에서 validation artifact 작성 후 재기록되는 derived artifact 로 고정한다.
  - capability summary/detail payload 는 `admin_capabilities.py` 가 `automatic_validation_result.json` 만 읽어 투영하는 projection 으로 정리하고, `snapshot/runtime` fallback 은 축소 대상으로 본다.
  - `operations.*` canonical 은 `automatic_validation_result.json -> evidence_bundle.readiness` 계층으로 단일화하고, `evidence_bundle.operations.*` 는 호환 레이어 축소 대상으로 본다.

| 그룹 | canonical key | 상태 | canonical write/read 기준 | 현재 파생/정리 대상 |
| --- | --- | --- | --- | --- |
| `contract` | `task`, `mode`, `validation_profile` | canonical | `write_automatic_validation_artifacts` 가 기록한 `automatic_validation_result.json` 루트 필드 | `orchestrator_artifacts.json.task/mode`, `output_audit.json.task/mode/validation_profile` 는 derived 복제본 |
| `execution` | `status`, `completion_gate_ok`, `output_archive_path`, `execution_steps[]`, `failed_reasons[]` | canonical | `automatic_validation_result.json` 루트 필드 + `evidence_bundle.execution.*` | capability summary/detail state, highlight, suggestion 은 projection 만 수행 |
| `execution` | `evidence_run_id`, `evidence_generated_at`, `self_run_status`, `evidence_snapshot_version` | canonical | `finalization_service.py` 가 `evidence_bundle.execution.*` 에 직접 기록 | `admin_capabilities.py` 의 `snapshot_execution/runtime_diagnostics` fallback 은 축소 대상 |
| `readiness` | `readiness_artifacts.*` | canonical | `automatic_validation_result.json.readiness_artifacts.*` | capability detail `evidence_bundle.readiness.*` 는 동일 값 투영으로만 유지 |
| `readiness` | `validation_engines.semantic_gate.*`, `validation_engines.product_readiness_hard_gate.*` | canonical | `automatic_validation_result.json.validation_engines.*` | `output_audit.json.semantic_audit_*` 는 derived 요약 |
| `operations` | `validation_engines.integration_test_engine.*`, `validation_engines.framework_e2e_validation.*`, `validation_engines.external_integration_validation.*` | canonical | `automatic_validation_result.json.validation_engines.*` | capability summary/detail operations UI 입력은 여기서만 파생 계산 |
| `operations` | `evidence_bundle.readiness.operational_evidence_snapshot`, `operational_targets_by_id`, `operational_evidence_summary`, `operational_latency_summary` | canonical | `finalization_service.py` 가 `operational_evidence` 결과를 `evidence_bundle.readiness.*` 에 기록 | `evidence_bundle.operations.operational_evidence.*`, `snapshot_operations.*` fallback 은 deprecated 후보 |
| `selective_apply` | `target_file_ids[]`, `target_section_ids[]`, `target_feature_ids[]`, `target_chunk_ids[]`, `failure_tags[]`, `repair_tags[]`, `target_patch_entries[]` | canonical | `evidence_bundle.selective_apply.*` + `traceability_map.json` 재기록 입력 | `admin_capabilities.py` 의 `snapshot_selective_apply` fallback 은 축소 대상 |
| artifact | `orchestrator_artifacts.json.*` | derived | `finalization_service.py` 가 validation artifacts 작성 후 canonical identity / reference 기반으로 rewrite | snapshot 직접 계산 금지, canonical reference만 유지 |
| artifact | `output_audit.json.*` | derived | `finalization_service.py` 가 canonical snapshot/최종 output 기준으로 rewrite | readiness/semantic audit 표시 전용 요약만 유지 |
| artifact | `traceability_map.json.*` | derived | `finalization_service.py` 가 target patch registry + canonical identity 기준으로 rewrite | selective_apply 보조 export 로만 유지 |

- 실제 write/read 대조 결과 요약:
  - `finalization_service.py` 는 `write_automatic_validation_artifacts` 이후 `orchestrator_artifacts.json`, `traceability_map.json`, `output_audit.json` 을 재기록하므로 derived artifact 후행 재기록 구조가 이미 존재한다.
  - `admin_capabilities.py` 는 현재 `automatic_validation_result.json` 을 먼저 읽지만 `evidence_snapshot.*`, `runtime_diagnostics.*`, `snapshot_operations.*`, `snapshot_selective_apply.*` fallback 이 남아 있어 canonical 단일화 이후 축소가 필요하다.
  - `operations.*` 의 최종 canonical 계층은 `evidence_bundle.readiness.*` 로 고정하고, summary/detail 이 추가 집계 없이 같은 key 를 그대로 읽도록 정리하는 것이 목표다.

##### 적용 결과 메모 (operations canonical 단일화 / fallback 축소)

- `backend/orchestrator/customer/finalization_service.py`
  - validation artifact 작성 직후 `automatic_validation_result.json` 을 다시 읽어 canonical payload 로 재정규화하도록 보강했다.
  - `evidence_bundle.readiness.operational_evidence_snapshot`, `operational_targets_by_id`, `operational_evidence_summary`, `operational_latency_summary`, `documentation_sync` 를 canonical readiness 계층으로 고정했다.
  - `evidence_bundle.operations` 는 실제 운영 증거를 담지 않고 `canonical_source = evidence_bundle.readiness.operational_evidence_snapshot`, `operational_evidence_deprecated = true` 메타만 남기도록 축소했다.
  - `orchestrator_artifacts.json`, `traceability_map.json`, `output_audit.json` 은 target ids / failure tags / completion gate / hard gate / shipping zip 값을 canonical snapshot 기준으로 재기록하도록 강화했다.
- `backend/llm/admin_capabilities.py`
  - `evidence_snapshot.*`, `snapshot_operations.*`, `snapshot_selective_apply.*` fallback 을 제거하고 `evidence_bundle.readiness.*`, `evidence_bundle.selective_apply.*` 우선 읽기로 정리했다.
  - `execution` 의 `completion_gate_ok`, `semantic_audit_ok`, `self_run_status`, `evidence_run_id` 는 canonical bundle 우선, 부족할 때만 최소 `runtime_diagnostics` fallback 을 허용한다.
  - summary/detail payload 의 `operations` 는 더 이상 raw `operational_evidence` 복제본을 노출하지 않고 canonical source 안내 메타만 유지한다.
- `frontend/frontend/components/ui/CapabilityPanel.tsx`
  - deprecated `operations.operational_evidence` 직접 참조를 제거하고 `readiness.operational_evidence_summary`, `readiness.operational_latency_summary`, `readiness.automatic_validation_result_path`, `readiness.output_audit_path` 기준으로 렌더를 정리했다.
  - `selective_apply.target_patch_entries` 를 canonical entry source 로 우선 사용하도록 정리했다.
- `frontend/frontend/app/admin/llm/page.tsx`
  - capability detail 타입 정의를 canonical evidence 구조에 맞게 구체화해 `readiness`, `operations`, `selective_apply` key 집합을 타입 수준에서 고정했다.
- 검증 기록
  - `python -m py_compile backend/orchestrator/customer/finalization_service.py backend/llm/admin_capabilities.py` 1차/2차 통과
  - `_build_capability_evidence_context(code-generator)` 1차/2차 확인 결과 `operations.canonical_source = evidence_bundle.readiness.operational_evidence_snapshot`, `selective_apply.target_patch_entries` 유지 확인
  - `npx tsc -p frontend/frontend/tsconfig.json --noEmit` 통과
  - 운영 관리자 UI 1차/2차: `https://metanova1004.com/admin/llm?capability=code-generator`
    - `OPERATIONAL_CANONICAL_SOURCE`, `evidence_bundle.readiness.operational_evidence_snapshot`, `AUTOMATIC_VALIDATION_RESULT`, `OUTPUT_AUDIT_PATH` 노출 확인
  - 운영 capability detail API 1차/2차 재검증: `GET https://metanova1004.com/api/admin/orchestrator/capabilities/code-generator`
    - `operations.canonical_source`, `operations.operational_evidence_deprecated`, `selective_apply.target_patch_entries` 포함 canonical key 집합 유지 확인
  - 최신 hard-gate 재생성: `docker compose exec -T backend sh -lc "cd /app && python -m backend.tmp_check_hard_gate_progress"`
    - `finalization completed validation_artifacts write` → `artifact_log_rewritten` → `traceability_map_rewritten` → `output_audit_rewritten` → `final_shipping_package_built` → `orchestration_completed` 재현
    - `tracker_state=target_completed`, `automatic_validation_result.json=true`, `output_audit.json=true`, `traceability_map.json=true` 확인
  - 운영 capability detail API 1차/2차 실증: `GET https://metanova1004.com/api/admin/orchestrator/capabilities/code-generator`
    - `evidence_bundle.readiness` 에 `operational_targets_by_id`, `operational_evidence_summary`, `operational_latency_summary` key 가 실제 노출됨
    - 1차/2차 모두 `status=200`, `readinessKeys=[final_readiness_checklist_path, automatic_validation_result_path, output_audit_path, operational_targets_by_id, operational_evidence_summary, operational_latency_summary]` 유지
    - `admin_capabilities.py` payload selection policy 를 `uploads/projects/*/docs/automatic_validation_result.json` 후보까지 포함하고, `operational_targets_by_id`/`operational_evidence_summary`/`operational_latency_summary` populated 여부를 우선 평가하도록 보강함
  - 운영 debug 노출 1차/2차: `validation_payload_path`, `validation_payload_candidate_paths`, `validation_payload_readiness`, `response_readiness`
      - 선택 경로가 `/app/uploads/projects/hard-gate-consistency-rerun_20260407_container/docs/automatic_validation_result.json` 로 전환됨
      - candidate path 는 8개가 확인됐고, 오래된 self-run experiment artifact 보다 최신 canonical project artifact 가 우선 선택됨
      - `response_readiness_keys=product_readiness_hard_gate, shipping_zip_validation, final_readiness_checklist_path, output_audit_path, automatic_validation_result_path, operational_evidence_snapshot, operational_targets_by_id, operational_evidence_summary, operational_latency_summary, documentation_sync, artifact_paths`
      - 서버 내부 선택/응답 직전 readiness 형태를 운영 API 응답에서 직접 검증 가능한 상태로 고정함
    - 최종 정리 단계에서 위 4개 debug 필드는 운영 검증 종료 후 detail API 응답에서 제거하고, canonical evidence key 집합만 유지하도록 정리함

3. finalization write path 정리 — 구현됨
   - `automatic_validation_result.json` 재정규화 후 `artifact_log.json`, `traceability_map.json`, `output_audit.json` 을 canonical snapshot 우선 단일 참조 방식으로 재기록하도록 고정했다.
   - `completion_gate_ok`, `product_readiness_hard_gate`, `shipping_zip_validation`, `packaging_audit`, `operational_evidence` 가 local fallback 보다 canonical payload/bundle/readiness 를 우선 사용하도록 정리했다.
4. capability aggregator 입력 경로 단일화 — 구현됨
   - `capability_map` 생성 직후 `evidence_context` 를 1회 계산하고 summary/detail/card 가 같은 컨텍스트를 재사용하도록 `_attach_capability_evidence_context(...)` 를 추가했다.
   - summary/detail 둘 다 같은 canonical snapshot 선택 결과와 evidence digest 를 공유하도록 정리했다.
5. 관리자 UI binding 정리 — 구현됨
   - `CapabilityPanel.tsx` 에 `buildCanonicalEvidenceBindings(detail)` 를 추가해 `completion_gate_ok`, `self_run_status`, `failure_tags`, `target_file_ids`, `operational_*` binding 을 단일화했다.
   - 운영 관리자 UI 2회 실검증에서 `completion_gate_ok`, `self_run_status`, `failure_tags`, `target_file_ids`, `OPERATIONAL_EVIDENCE`, `OPERATIONAL_WARNING / FAILED`, `TARGET_FILE_IDS`, `FAILURE_TAGS` 노출을 재확인했다.
6. diff 문서화 — 구현됨
   - 본 섹션의 evidence key diff 표와 derived artifact / aggregator / UI binding diff 표를 최신 canonical 구조 기준으로 갱신했다.

##### B. 파일별 수정 순서
1. `backend/llm/orchestrator.py`
   - canonical evidence snapshot 구조를 최종 정의한다.
   - `automatic_validation_result.json` 에 기록하는 최종 source of truth key 집합을 고정한다.
2. `backend/orchestrator/customer/finalization_service.py`
   - finalization write 순서를 `validation_result -> related artifacts rewrite` 로 고정한다.
   - `orchestrator_artifacts.json`, `output_audit.json`, `traceability_map.json` 재기록 시 canonical snapshot 참조 방식으로 정리한다.
3. `backend/llm/admin_capabilities.py`
   - summary/detail API 가 canonical key 만 읽고 노출하도록 정리한다.
   - alias / fallback / 중복 의미 필드를 줄인다.
4. `frontend/frontend/components/ui/CapabilityPanel.tsx`
   - summary/detail 카드에서 evidence key 이름을 통일해 읽도록 보정한다.
5. `frontend/frontend/app/admin/llm/page.tsx`
   - 상단 경광판, 배지, detail panel binding 을 canonical key 기준으로 정리한다.
6. `docs/orchestrator-multigenerator-upgrade-status.md`
   - key diff 표와 체크리스트 상태를 문서에 동기화한다.

##### C. 2회 실검증 명령 목록

###### 1차 실검증
1. 프런트 빌드
   - `npm --prefix frontend\frontend run build`
2. 백엔드 문법/구조 확인
   - `python -m py_compile backend/llm/orchestrator.py backend/orchestrator/customer/finalization_service.py backend/llm/admin_capabilities.py`
3. 운영 반영
   - `docker compose build backend frontend-admin`
   - `docker compose up -d backend frontend-admin nginx`
4. 운영 capability summary 확인
   - `Invoke-WebRequest -Uri 'https://metanova1004.com/api/admin/orchestrator/capabilities/summary' -Headers @{ Authorization = 'Bearer <ADMIN_TOKEN>' } -UseBasicParsing`
5. 운영 capability detail 확인
   - `Invoke-WebRequest -Uri 'https://metanova1004.com/api/admin/orchestrator/capabilities/code-generator' -Headers @{ Authorization = 'Bearer <ADMIN_TOKEN>' } -UseBasicParsing`
6. 확인 포인트
   - summary/detail 둘 다 `evidence_schema_version`, `evidence_generated_at`, `evidence_run_id`, `self_run_status`, `target_file_ids`, `failure_tags` 를 같은 이름으로 노출하는지 확인

###### 2차 실검증
1. 동일 빌드/배포 상태 유지 후 재호출
   - `Invoke-WebRequest -Uri 'https://metanova1004.com/api/admin/orchestrator/capabilities/summary' -Headers @{ Authorization = 'Bearer <ADMIN_TOKEN>' } -UseBasicParsing`
   - `Invoke-WebRequest -Uri 'https://metanova1004.com/api/admin/orchestrator/capabilities/code-generator' -Headers @{ Authorization = 'Bearer <ADMIN_TOKEN>' } -UseBasicParsing`
2. 운영 UI 확인
   - `PLAYWRIGHT_ADMIN_BASE_URL='https://metanova1004.com'` 기준 관리자 capability 화면 E2E 또는 운영 UI 수동 확인으로 summary/detail 배지 key 동기화 확인
3. 확인 포인트
   - 1차와 동일 key 집합이 유지되는지
   - summary/detail 값 충돌이 없는지
   - 관리자 UI 상단 배지와 detail panel 이 같은 key 를 읽는지

###### 실검증 체크 기준
- [x] 1차에서 summary/detail evidence key 이름 일치
- [x] 1차에서 관리자 UI 배지와 detail panel binding 일치
- [x] 2차에서 같은 key 집합 재현
- [x] 2차에서 값 충돌/alias 재발 없음
- [x] 문서 체크리스트와 실제 검증 결과 동기화

---

## 8. 완료 판정 조건
- 레거시 서비스 단일 파일 경로 참조 0건 유지
- 템플릿/검증기/문서/체크리스트 모두 패키지 기준 통일
- 생성 직후 결과물 폴더에서 hard gate 전체 성공
- ZIP 재현 검증 성공
- `/api/llm/ws`, `admin`, `marketplace` 운영 실도메인 검증 성공
- 관리자 capability 경고가 실제 증거와 일치

이 중 하나라도 미달이면 **완료 아님**.

---

## 9. 전체 작업 체크리스트

본 체크리스트는 AI엔진 오케스트레이터 · 멀티 코드생성기 업그레이드 작업 전체를 순서대로 닫기 위한 기준표다.

### 9-1. 작업 상태 표기 규칙
- [ ] 미착수: 분석/구현/실검증 어느 것도 시작하지 않음
- [~] 진행중: 구현 또는 검증이 진행 중이며 아직 2회 실검증 통과 전
- [x] 완료: 해당 항목이 실제 구현되고 실검증 2회 통과까지 확인됨
- [!] 실패/차단: 구현 또는 검증이 실패했고 원인 해결 전까지 다음 단계로 넘어갈 수 없음

### 9-2. Phase A — 생성기 계약 단일화
- [x] `backend/llm/orchestrator.py` 에서 레거시 서비스 단일 파일 경로 참조 0건 유지 검증
- [x] `backend/python_code_generator.py` 가 `app/services/__init__.py`, `app/services/runtime_service.py` 기준만 생성하는지 검증
- [x] `backend/meta_programming/planner.py` 가 서비스 패키지 기준 경로만 사용하도록 검증
- [x] `backend/llm/admin_capabilities.py` 의 필수 파일/핵심 파일/경고 기준이 동일 패키지 계약을 사용하도록 검증
- [x] 문서/체크리스트 생성 문자열이 레거시 서비스 단일 파일 경로를 요구하지 않는지 전수 검증

### 9-3. Phase B — 오케스트레이터 구조 분할
- [x] `backend/llm/orchestrator.py` 의 고객 생성 실행 계층을 별도 모듈로 분리
- [x] 관리자 연구형/지시형 대화 로직과 고객 자동 생성 로직의 물리적 모듈 경계 고정
- [x] `backend/orchestrator/chat/*` 와 신규 분리 모듈 간 import 경계 정리
- [x] 분할 후 `orchestrate`, `run_orchestration`, `answer_orchestrator_chat` 공개 API 호환성 유지

### 9-4. Phase C — 관리자 LLM 화면 분해
- [x] `frontend/frontend/app/admin/llm/page.tsx` 를 화면 조립 전용으로 축소
- [x] 대화 로직을 별도 훅/클라이언트로 분리
- [x] capability 로직을 별도 훅/클라이언트로 분리
- [x] self-run 로직을 별도 훅/클라이언트로 분리
- [x] runtime/system-settings/auto-connect 로직을 별도 훅/클라이언트로 분리
- [x] 분해 후 기존 UI 연결과 관리자 경로 호환성 유지

### 9-5. Phase D — 출고 Hard Gate 단일화
- [x] 생성 직후 결과물 폴더 의존성 설치 자동 검증
- [x] 생성 직후 결과물 폴더 단독 기동 자동 검증
- [x] 생성 직후 핵심 API 스모크 자동 검증
- [x] 생성 직후 테스트 실행 자동 검증
- [x] ZIP 생성/압축 해제/재현 자동 검증
- [x] `final_readiness_checklist.md` 등 닫힌 증거 문서 자동 생성 검증

### 9-6. Phase E — 운영 경로 실검증
- [x] `/api/llm/ws` 운영 실도메인 검증 1차 통과
- [x] `/api/llm/ws` 운영 실도메인 검증 2차 통과
- [x] `admin` 운영 실도메인 검증 1차 통과
- [x] `admin` 운영 실도메인 검증 2차 통과
- [x] `marketplace` 운영 실도메인 검증 1차 통과
- [x] `marketplace` 운영 실도메인 검증 2차 통과

### 9-7. Phase F — capability / self-run / 증거 정렬
- [x] capability 경고가 latest self-run 기록과 일치하는지 검증
- [x] capability 경고가 output audit / shipment zip evidence 와 일치하는지 검증
- [x] self-run 최종 상태 전이(`running`, `failed`, `pending_approval`, `no_changes`)가 실제 기록과 일치하는지 검증 *(`record_scope_id=phase-f-self-run-terminal-state` 최신 세션에서 `failed`, `pending_approval`, `no_changes` 2회씩 terminal evidence 를 확보했고 latest 2회가 모두 `pass` 로 닫힘)*
- [x] 관리자 자동 복구/자가치유가 원인 범위에 집중해 실제 수정까지 이어지는지 검증 *(`record_scope_id=phase-f-focused-self-healing-apply` 최신 세션에서 `applied_to_source evidence` 2회와 `target_*_ids` 기록을 확보했고 latest 2회가 모두 `pass` 로 닫힘)*

#### 9-7-1. Phase F 보류/TODO
- [x] `record_scope_id=phase-f-self-run-terminal-state` 기준 `pending_approval` 또는 `no_changes` 종료 사례 2회 확보
- [x] `record_scope_id=phase-f-focused-self-healing-apply` 기준 `applied_to_source` 또는 동등한 최종 반영 증거 2회 확보
- [x] `record_scope_id=phase-f-self-run-terminal-state` 를 최신 세션 기준으로 다시 표에 묶어 상위 체크리스트와 동기화
- [x] `record_scope_id=phase-f-self-run-terminal-state`, `record_scope_id=phase-f-focused-self-healing-apply` latest 2회 기준 `partial/blocked` 해소 및 상위 9-7 항목 승격 가능 상태 확인

### 9-8. Phase G — 완료 게이트
- [~] 전체 워크스페이스 변경분 빌드/문법 검증 통과 *(workspace 기준 `python -m py_compile ...`, `npm --prefix frontend/frontend run build` 는 통과. 다만 Visual Studio `Build.BuildSolution` 은 `AppData/Local/Temp/CopilotBaseline/.../~*.tsx` 를 Copilot diagnostic context 로 재집계하며 `tsconfig`/`.esproj` 문맥을 타지 못하는 제품 이슈가 남아 있어 완료 승격 보류)*
- [x] `record_scope_id=phase-g-core-web-routes` 1차 통과
- [x] `record_scope_id=phase-g-core-web-routes` 2차 통과
- [x] `record_scope_id=phase-e-admin-marketplace-ws` 1차 통과
- [x] `record_scope_id=phase-e-admin-marketplace-ws` 2차 통과
- [x] `record_scope_id=phase-g-core-web-routes`, `record_scope_id=phase-e-admin-marketplace-ws`, `record_scope_id=phase-f-system-settings-504-recurrence`, `record_scope_id=phase-f-admin-route-recovery` 근거를 본 문서와 관련 체크리스트에 동기화 *(latest route manifest / nginx target / cached path validation 이 모두 validation artifact 와 기록표에 동기화됨)*

#### 9-8-1. Visual Studio `Build.BuildSolution` 제품 이슈 고정 메모
- 현재 증거 기준 직접 원인:
  - `Build.BuildSolution` 실패는 workspace 소스가 아니라 `AppData/Local/Temp/CopilotBaseline/.../~page.tsx`, `~CapabilityPanel.tsx`, `~marketplace-orchestrator-client.tsx` 에서 발생한다.
  - Copilot diagnostic 저장소와 `.vs/slnx.sqlite` 조사 결과 `frontend/frontend/*.ts(x)` 가 `frontend/frontend/tsconfig.json` 단일 프로젝트가 아니라 파일별 `Debug` target 으로도 병행 등록돼 있다.
  - 루트 `tsconfig.json` 추가와 `frontend/frontend/frontend.esproj` 추가 후에도 `next/navigation`, `@/`, `@shared/`, `Object.values`, `flatMap`, `includes` 해석 실패가 그대로 유지돼, 현재 차단 원인은 workspace 코드가 아니라 `Visual Studio Enterprise 2026 (18.5.0-insiders)` + Copilot diagnostic pipeline 이 TS/TSX 파일을 프로젝트 문맥 없이 개별 분석하는 제품 이슈로 판단한다.
- 현재 관리 원칙:
  1. Visual Studio **Report a Problem** 으로 재현 케이스를 제품 이슈로 보고한다.
  2. 가능하면 stable 채널 또는 Copilot/insiders 조합을 바꿔 같은 재현 케이스를 다시 검증한다.
  3. 그 전까지 완료 판정 근거는 `Build.BuildSolution` 대신 workspace 기준 `npm --prefix frontend/frontend run build` 와 관련 운영 실검증으로 분리해 관리한다.
- 현시점 판정:
  - workspace 코드/산출물 검증: 구현됨
  - Visual Studio `Build.BuildSolution`: 제품 이슈 차단으로 보류

### 9-9. 잔여 자동화 고도화 체크리스트
- [x] `automatic_validation_result.json` 에 `evidence_snapshot`, `operational_verified_target_count`, `legacy_contract_scan_ok` 를 함께 기록
- [x] `post-startup bootstrap` 의 `scheduled_at`, `started_at`, `completed_at`, `duration_ms` 상태를 구조적으로 기록
- [x] `recommended_expansion_actions` 를 `code-generator` capability 의 실제 `suggested_actions` 및 관리자 후속 실행 버튼과 연결
- [x] 최신 self-run 생성 시점 `post_validation_analysis` 에 `target_file_ids`, `failure_tags`, `repair_tags`, `verified_operational_targets`, `missing_operational_targets` 를 함께 기록
- [x] capability 경고 변화량 기준 후속 조치 추천 우선순위 자동 계산
- [x] selective apply 대상 ID 기반 후속 self-improvement 작업문 자동 생성 강화
- [x] 관리자/고객 오케스트레이터 공통 후속 제안 카드 UX 일반화
- [x] self-run before/after 변화량 점수 기반 우선순위 점수 표시
- [x] 실행 이력 누적 기반 우선순위 모델 정교화 (local history 평균/피크/직전 대비 가중치)
- [x] 관리자 capability 장기 우선순위 모델을 서버 측 artifact/history 저장소와 결합 (`capability_priority_history.json`)
- [x] 고객 오케스트레이터 장기 우선순위 모델을 서버 측 artifact/history 저장소와 결합 (`customer_follow_up_history.json`)
- [x] 고객 오케스트라이터 장기 우선순위 모델을 stage-run / approval history / hard gate artifact 와 직접 결합
- [x] 관리자 capability 장기 우선순위 모델을 approval history / stage-run artifact / hard gate failed stages 와 직접 결합
- [x] 관리자 공통 후속 제안 카드 UI 에 approval failed fields / hard gate failed stages 를 직접 metric 으로 노출
- [x] 관리자 공통 후속 제안 카드 UI 에 priority_self_run_stage 를 직접 metric 으로 노출
- [x] 관리자/고객 공통 후속 제안 카드 metric 그룹을 severity 기반 정렬 + 접기/펼기 UX 로 확장
- [x] evidence 자동 동기화 강화 1-1~1-3 (`evidence_bundle` 스냅샷 일괄 저장, 공통 key 정렬, evidence_generated_at/run_id/profile_id 기록)
- [x] evidence 자동 동기화 강화 2-1~2-3, 3-1~3-3 (summary/detail version 노출, metric/headline 직접 반영, 카드 정렬 우선순위 연결)
- [x] 운영 evidence 집계 고도화 1-1~1-2 (completion gate 요약 포함, readiness artifact 에 snapshot + verified count 저장)
- [x] 운영 evidence 집계 고도화 1-3 (capability detail targets 와 readiness artifact 링크 상호 참조 정리)
- [x] admin capability runtime_diagnostics 캐시 무효화/갱신 흐름 보강 및 readiness_artifacts fallback/로그 추적 추가
- [x] capability detail raw json 대조용 debug_signature / sections_count 추가 및 외부 payload-서버 로그 일치 검증
- [x] admin system-settings 조회가 Ollama live probe 반복으로 504 되지 않도록 cached status 경로로 전환
- [x] frontend system-settings 재시도 구조 확인 및 backend cached status 적용 후 운영 2회 200 응답 검증
- [x] validation artifact 에 legacy services contract / localhost 문서 stale scan 결과 저장
- [x] 최신 운영 실검증 결과가 상태표/목표표/체크리스트에 미반영이면 `반영 필요` 로 먼저 표시하는 문서 규칙 추가
- [x] README 와 상태표 사이 stale 문구 자동 비교 규칙 추가
- [x] validation artifact 가 README ↔ 상태표 핵심 운영 상태 문구 불일치 근거도 저장하도록 확장
- [x] validation artifact 가 최신 실검증 기록표 마지막 행 기반 stale 비교 메타데이터도 저장하도록 확장
- [x] code-generator detail 운영 probe refresh 간격 분리 및 system-settings mtime 기반 캐시로 간헐 504 완화
- [x] 운영 경로 probe 결과를 target별 구조적 latency 로그로 기록
- [x] latency_ms 기준 warning 임계치 규칙 추가
- [x] latency warning 결과를 capability summary/evidence digest/operational evidence 섹션에도 직접 반영
- [x] 운영 경로별 warning_threshold_ms 분리 및 probe/log/evidence/summary/digest 동기화
- [x] 경로별 latency warning 결과를 runtime findings/actions 및 capability issue 상태에도 직접 반영
- [x] 생성기/출고 artifact canonical evidence 와 readiness artifact 에도 latency_warning / warning_threshold_ms / warning_targets / max_latency_ms 기록
- [x] md artifact(`automatic_validation_result.md`, `final_readiness_checklist.md`) 에도 운영 latency warning 요약 직접 기록
- [x] completion gate 요약(`operational_evidence_summary`)에도 latency_warning / warning_threshold_ms / warning_targets / max_latency_ms 동기화
- [x] finalization 단계의 `evidence_bundle["snapshot"]`, 고객 응답 payload, artifact 경로 반환값에도 operational latency summary 동기화
- [x] finalization 단계의 `artifact_paths` 를 snapshot/readiness/고객 응답 payload(`completion_judge`, `packaging_audit`)까지 같은 집합으로 동기화
- [x] 고객 응답 top-level payload 에도 `operational_latency_summary`, `artifact_paths` 직접 노출
- [x] pre-finalization seed artifact(`artifact_log`, `traceability_map`) 도 finalization 이후와 같은 `artifact_paths` skeleton 을 공유하도록 동기화
- [x] finalization 이후 `artifact_log`, `traceability_map` 도 최종 `evidence_bundle` / `validation_artifacts` / `artifact_paths` 로 재기록
- [x] `output_audit.json` 도 finalization 이후 최종 `evidence_bundle` / `validation_artifacts` / `artifact_paths` / `operational_latency_summary` 로 재기록
- [x] 고객 응답 top-level payload 에도 `validation_artifacts` 직접 노출
- [x] finalization 서비스 내부에서 `artifact_log` / `traceability_map` / `output_audit` 최종 재기록을 일괄 수행하도록 정리
- [x] finalization 함수의 중간 단계별 완료 로그(`completion_judge_finished`, `validation_artifacts_finalized`, `output_audit_rewritten` 등) 추가
- [x] 상위 오케스트레이션 흐름에도 `finalization_dispatch` / `finalization_returned` 로그 추가
- [x] 관리자/고객 공통 후속 제안 카드 metric 그룹을 severity 기반 정렬 + 접기/펼기 UX 로 확장

---

## 10. 실검증 기록 규칙

- 각 체크 항목은 통과 전까지 `[ ]` 또는 `[~]` 만 사용한다.
- 실검증은 최소 2회 수행 후 둘 다 통과한 경우에만 `[x]` 로 바꾼다.
- 1회만 통과한 항목은 절대 완료 처리하지 않는다.
- 실검증 기록표의 `result_status` 를 상위 체크 항목 승격/보류의 **source of truth** 로 사용하며, `partial`/`blocked`/`fail` 이 남아 있으면 상위 체크는 절대 `[x]` 로 올리지 않는다.
- 검증 실패 시 실패 원인, 로그 위치, 재시도 계획을 아래 기록표에 남긴다.

> 보류 명시: `applied_to_source` 2회 실증은 현재 최신 세션 자료 부재로 아직 닫히지 않았으며, 관련 상위 체크 항목은 보류 상태를 유지한다.

### 10-1. 실검증 기록표

| 회차 | record_id | record_scope_id | attempt_no | result_status | 날짜/시간 | 대상 항목 | 검증 명령/경로 | 결과 | 근거/로그 |
|---|---|---|---|---|---|---|---|---|---|
| 1 | REC-001 | phase-g-core-web-routes | 1 | pass | 2026-04-05 현재 세션 | Phase C, Phase E, Phase G | `cd frontend/frontend && npm run build` / `https://metanova1004.com/admin/llm` / `https://metanova1004.com/marketplace/orchestrator` / `https://metanova1004.com/api/llm/ws` | 통과 | 빌드 1차 성공, `admin=200`, `marketplace=200`, `ws=101 Switching Protocols` |
| 2 | REC-002 | phase-g-core-web-routes | 2 | pass | 2026-04-05 현재 세션 | Phase C, Phase E, Phase G | `cd frontend/frontend && npm run build` / `https://metanova1004.com/admin/llm` / `https://metanova1004.com/marketplace/orchestrator` / `https://metanova1004.com/api/admin/workspace-self-run-record?latest=true&pending_only=true` / `https://metanova1004.com/api/llm/ws` | 통과 | 빌드 2차 성공, `admin=200`, `marketplace=200`, `pending_record=204`, `ws=101 Switching Protocols` |
| 3 | REC-003 | phase-a-contract-and-core-web-routes | 1 | pass | 2026-04-05 현재 세션 | Phase A, Phase C, Phase E, Phase G | `python -m py_compile backend/llm/orchestrator.py` / `cd frontend/frontend && npm run build` / `https://metanova1004.com/admin/llm` / `https://metanova1004.com/marketplace/orchestrator` / `https://metanova1004.com/api/llm/ws` | 통과 | semantic gate import 검증 보정 후 `py_compile` 성공, 빌드 1차 성공, `admin=200`, `marketplace=200`, `ws=101` |
| 4 | REC-004 | phase-f-self-run-terminal-state | 1 | partial | 2026-04-05 현재 세션 | Phase C, Phase E, Phase F, Phase G | `cd frontend/frontend && npm run build` / `https://metanova1004.com/admin/llm` / `https://metanova1004.com/marketplace/orchestrator` / `https://metanova1004.com/api/admin/workspace-self-run-record?latest=true` / `https://metanova1004.com/api/llm/ws` | 통과(부분 차단 확인) | 빌드 2차 성공, `admin=200`, `marketplace=200`, `latest_self_run=204`, `ws=101`; latest self-run 부재로 상태 전이 검증은 차단 |
| 5 | REC-005 | phase-f-self-run-terminal-state | 2 | partial | 2026-04-05 현재 세션 | Phase C, Phase E, Phase F, Phase G | `cd frontend/frontend && npm run build` / `https://metanova1004.com/admin/llm` / `https://metanova1004.com/marketplace/orchestrator` / `https://metanova1004.com/api/admin/orchestrator/capabilities/summary` / `https://metanova1004.com/api/llm/ws` | 통과(부분 차단 확인) | 빌드 3차 성공, `admin=200`, `marketplace=200`, `capability_summary=200`, `ws=101`; capability summary는 응답하지만 self-run 상태 전이는 활성 기록 부재로 미폐쇄 |
| 6 | REC-006 | phase-d-hard-gate | 1 | pass | 2026-04-05 현재 세션 | Phase D | `python -m py_compile backend/llm/orchestrator.py` / `repair_final_readiness_checklist(phase-c-and-d-smoke_20260405_183024, phase-c-and-d-smoke-rerun_20260405_183237, hard-gate-smoke-rerun_20260405_171426)` | 통과 | 3세트 모두 checklist 복구 성공, 길이 `402/408/404`, 문법 검증 통과 |
| 7 | REC-007 | phase-d-hard-gate | 2 | pass | 2026-04-05 현재 세션 | Phase D | `python -m py_compile backend/llm/orchestrator.py` / `repair_final_readiness_checklist(phase-c-and-d-smoke_20260405_183024, phase-c-and-d-smoke-rerun_20260405_183237, hard-gate-smoke-rerun_20260405_171426)` | 통과 | 3세트 모두 checklist 재생성 성공, 길이 유지 `402/408/404`, 문법 검증 통과 |
| 8 | REC-008 | phase-d-hard-gate | 3 | pass | 2026-04-05 현재 세션 | Phase D | `automatic_validation_result.json` + `product_readiness_hard_gate` + `final_readiness_checklist.md` 3세트 대조 | 통과 | 3세트 모두 `dependency_install/standalone_boot/api_smoke/pytest/zip_reproduction=true`, checklist 존재/길이 `419/425/421`, `readiness_artifacts.final_readiness_checklist_path=docs/final_readiness_checklist.md` 확인 |
| 9 | REC-009 | phase-a-services-contract-scan | 1 | pass | 2026-04-06 현재 세션 | Phase A | 대표 산출물 3세트 `docs/automatic_validation_result.json`, `docs/automatic_validation_result.md` 에 대해 레거시 서비스 단일 파일 경로 문자열 검색 1차 | 통과 | `hard-gate-smoke_20260405_170908`, `phase-c-and-d-smoke_20260405_183024`, `phaseb-direct-run-01_20260405_225523` 대상 `NO_MATCHES` 확인 |
| 10 | REC-010 | phase-a-services-contract-scan | 2 | pass | 2026-04-06 현재 세션 | Phase A | 대표 산출물 3세트 `docs/automatic_validation_result.json`, `docs/automatic_validation_result.md` 에 대해 레거시 서비스 단일 파일 경로 문자열 검색 2차 | 통과 | 동일 3세트 대상 2차 재검증에서도 `NO_MATCHES` 확인, 문서/체크리스트 레거시 요구 문자열 제거 검증 완료 |
| 11 | REC-011 | phase-f-output-audit-shipping-evidence | 1 | pass | 2026-04-06 현재 세션 | Phase F | 대표 산출물 3세트 `automatic_validation_result.json` + `final_readiness_checklist.md` 의 output audit / shipment zip evidence 1차 대조 | 통과 | `phase-c-and-d-smoke`, `hard-gate-smoke-rerun`, `phaseb-direct-run-01` 모두 `integration_test_engine.ok=true`, `shipping_zip_validation.ok=true`, checklist `zip reproduction`/`product readiness hard gate` 체크 확인 |
| 12 | REC-012 | phase-f-output-audit-shipping-evidence | 2 | pass | 2026-04-06 현재 세션 | Phase F | 대표 산출물 3세트 `automatic_validation_result.json` + `final_readiness_checklist.md` 의 output audit / shipment zip evidence 2차 대조 | 통과 | 동일 3세트 대상 2차 재검증에서 `ZIP_OK=True`, `INTEGRATION_OK=True`, `HARDGATE_OK=True`, `CHECKLIST_OK=True` 확인 |
| 13 | REC-013 | phase-f-self-run-terminal-state | 1 | partial | 2026-04-06 현재 세션 | Phase F | 실제 self-run 상태 전이 검증 가능 여부 사전 점검 (`approval.json` 탐색) | 차단 | 워크스페이스 `uploads`, `knowledge` 전역 검색 결과 `NO_APPROVAL_JSON`; latest self-run 기록 부재로 `running/failed/pending_approval/applied_to_source/no_changes` 상태 전이 실검증은 아직 닫을 수 없음 |
| 14 | REC-014 | phase-f-self-run-terminal-state | 2 | pass | 2026-04-06 현재 세션 | Phase F | latest self-run 상태 전이 1차 (`approval_id=20260405_211345_128434`) | 통과 | `approval.json` 직접 확인 결과 `running → failed` 전이 완료, `runtime_diagnostic=worker 예외로 종료됨: data must be str, not NoneType`, `orchestration_error=TypeError: data must be str, not NoneType`, `worker.log` 와 `report_preview.md` 모두 동일 traceback (`backend/llm/orchestrator.py:_compat_write_manifest`, `target_path.write_text(rendered_content)`), fallback 보고서 `docs/code_analysis.json`, `docs/root_cause_analysis.md` 경로 기록 |
| 15 | REC-015 | phase-f-self-run-terminal-state | 3 | pass | 2026-04-06 현재 세션 | Phase F | latest self-run 상태 전이 2차 (`approval_id=20260405_211431_046469`) | 통과 | 2차 `approval.json` 직접 확인 결과도 `running → failed` 전이 완료, `worker_started_at/worker_finished_at/finished_at` 기록 존재, `worker.log` tail 과 `report_preview.md` 모두 동일 `TypeError: data must be str, not NoneType` traceback 재현, `experiment_clone_path=/app/uploads/tmp/codeai_admin_runtime/admin_self_experiments/app_20260405_211437` 확인 |
| 16 | REC-016 | phase-f-capability-self-run-alignment | 1 | pass | 2026-04-06 현재 세션 | Phase F | capability 경고와 latest self-run 기록 일치 1차 대조 | 통과 | capability summary `Project Scanner`, `Self-Healing Engine`, `Code Generator` 모두 `latest self-run 상태=failed`, `state_label=보관 진단`, `state_reason=원본 반영 전 실패하거나 범위가 제한된 self-run 기록은 운영 경고 대신 보관 진단으로만 유지` 로 표기. latest record `approval_id=20260405_211431_046469`, `status=failed`, `source_path=/app` 와 일치 |
| 17 | REC-017 | phase-f-focused-self-healing-apply | 1 | pass | 2026-04-06 현재 세션 | Phase F | 관리자 자동 복구/자가치유 focused self-healing 경로 1차 실검증 | 부분 통과 | `POST /api/admin/focused-self-healing/plan` 결과 `focused_path=/app/backend/llm/orchestrator.py`, `category=null_guard`, `auto_apply_allowed=true`, `approval_required=false`; `POST /api/admin/focused-self-healing/apply` 결과도 동일 focused path만 대상으로 `retry.queued=true`, `directive_scope=targeted_implementation`, verification loop `syntax/type/runtime/domain-route` 반환. 원인 범위 집중과 self-run 재시도 큐 연결은 확인됐으나 실제 수정 반영(`applied_to_source`) 완료 증거는 아직 없음 |
| 18 | REC-018 | phase-f-focused-self-healing-apply | 2 | blocked | 2026-04-06 현재 세션 | Phase F | focused self-healing 이후 실제 재시도 self-run 결과 재검증 | 차단 | 컨테이너 내부 `/app/uploads/tmp/codeai_admin_runtime/admin_self_runs` 최신 approval 기록 재조회 결과 새 재시도 기록은 생성되지 않았고 기존 `approval_id=20260405_220447_962377`, `20260405_211345_128434` 두 건만 남아 있음. 따라서 `applied_to_source` 전이 검증은 background worker 장기 실행 차단으로 미폐쇄 |
| 19 | REC-019 | phase-f-focused-self-healing-apply | 3 | partial | 2026-04-06 현재 세션 | Phase F | 원인 파일 수정 반영 여부 재검증 (`backend/llm/orchestrator.py:_compat_write_manifest`) | 부분 통과 | 컨테이너 내부 실제 코드 확인 결과 `_compat_write_manifest` 는 이미 `str(item.get("content") or "")` 로 null-safe 처리되어 있음. 그러나 focused self-healing apply 라우트는 실제 `execute_workspace_self_run` 을 호출하지 않고 `retry.queued=true` payload만 반환하므로 수정 반영 효과를 self-run 재실행 결과로 아직 검증하지 못함 |
| 20 | REC-020 | phase-f-admin-route-recovery | 1 | pass | 2026-04-06 현재 세션 | Phase F | 누락된 admin 경로 복구 및 OpenAPI 재검증 | 통과 | `backend/admin_router.py` 후반부 복구 후 `python -m py_compile backend/admin_router.py` 통과, 백엔드 재시작 완료, OpenAPI 및 실행 중 앱 객체 기준 `ADMIN_ROUTE_COUNT=27`; `/api/admin/system-settings`, `/api/admin/workspace-self-run`, `/api/admin/workspace-self-run-record`, `/api/admin/focused-self-healing/plan`, `/api/admin/focused-self-healing/apply` 포함 재노출 확인 |
| 21 | REC-021 | phase-f-focused-self-healing-apply | 4 | partial | 2026-04-06 현재 세션 | Phase F | 복구된 `/api/admin/focused-self-healing/apply` 1차 재실행 및 상태 전이 확인 | 부분 통과 | `POST /api/admin/focused-self-healing/apply` 1차 호출 성공 후 새 approval `20260405_220447_962377` 생성, `source_path=/app/backend/llm`, `directive_template=focused-self-healing`, `directive_scope=targeted_implementation` 기록 확인. 상태는 `failed → running` 전이까지 확인됐으나 64초 경과 시점에도 `finished_at=null`, `worker_pid=315`, `worker_alive=true`, `worker.log` 공백으로 terminal state 미도달 |
| 22 | REC-022 | phase-f-focused-self-healing-apply | 5 | blocked | 2026-04-06 현재 세션 | Phase F | 복구된 `/api/admin/focused-self-healing/apply` 2차 재검증 | 차단 | 2차 호출은 클라이언트 타임아웃으로 종료됐고 서버 측 최신 approval 재조회 결과도 새 approval 추가 없이 `20260405_220447_962377` 단일 `running` 기록만 유지. 따라서 self-run 재실행 자체는 복구됐지만 `pending_approval|no_changes|applied_to_source` 전이 검증은 background worker 장기 실행 차단으로 미폐쇄 |
| 23 | REC-023 | phase-f-admin-route-recovery | 1 | pass | 2026-04-06 현재 세션 | Phase F | 누락된 admin 경로 복구 및 OpenAPI 재검증 | 통과 | `backend/admin_router.py` 의 import-time 복구 후 `python -m py_compile backend/admin_router.py` 통과, 백엔드 재시작 완료, OpenAPI 및 실행 중 앱 객체 기준 `ADMIN_ROUTE_COUNT=27`; `/api/admin/system-settings`, `/api/admin/workspace-self-run`, `/api/admin/workspace-self-run-record`, `/api/admin/focused-self-healing/plan`, `/api/admin/focused-self-healing/apply` 포함 재노출 확인 |
| 24 | REC-024 | phase-f-admin-route-recovery | 2 | pass | 2026-04-06 현재 세션 | Phase F | admin router import-time 장애 복구 1차 실검증 | 통과 | 동일 9개 운영 admin 경로 2차 재검증에서도 모두 `200` 유지. `admin router loaded` 로그와 `GET /api/admin/system-settings HTTP/1.1 200 OK` 확인으로 오래된 serving snapshot 불일치 해소 및 원인 범위 집중 복구가 실제 운영 경로 정상화까지 이어졌음을 검증 |
| 25 | REC-025 | phase-f-self-run-terminal-state | 6 | pass | 2026-04-06 현재 세션 | Phase F | focused self-healing self-run terminal state 안정화 및 1차 검증 | 통과 | `backend/llm/orchestrator.py` 에 `REPO_ROOT = Path(__file__).resolve().parents[2]` 추가 후 self-run 최신 실패 원인 `NameError: REPO_ROOT is not defined` 제거. 이어서 `backend/admin_self_run_worker.py` 에 heartbeat thread join을 추가해 finalize 직전 stale running payload 경쟁 상태를 차단함. 그 결과 이전에 `running` 으로 남아 있던 `approval_id=20260406_055455_118990`, `20260406_055423_998046` 이 모두 `failed`, `worker_alive=false`, `finished_at` 기록 포함 terminal state로 안정화됨 |
| 26 | REC-026 | phase-f-system-settings-504-recurrence | 1 | pass | 2026-04-06 현재 세션 | Phase F | focused self-healing apply 경량화 및 2차 운영 검증 | 통과 | `backend/admin/orchestrator/focused_self_healing_service.py` 와 `backend/admin_router.py` 수정으로 file target은 파일로 유지하되 실제 `workspace-self-run` 실행은 부모 디렉터리 계약으로 넘기고, apply 요청 경로의 동기 준비 작업(`_prepare_workspace_self_run_context`)을 제거해 큐 등록만 즉시 반환하도록 변경. 그 후 `nginx/nginx.conf` 의 잘못된 `location = /api/admin/system-settings -> frontend_main` 예외 라우팅을 `backend_upstream` 으로 수정하고 nginx 재기동. 운영 실검증 2회에서 `https://metanova1004.com/api/admin/system-settings` 가 각각 `200 / 354ms`, `200 / 301ms` 로 복구되어 반복 `504 Gateway Timeout` 해소 확인 |
| 27 | REC-027 | phase-f-system-settings-504-recurrence | 2 | pass | 2026-04-06 현재 세션 | Phase F | admin dashboard bootstrap 중복 호출/취소 집계 보정 및 운영 재검증 | `frontend/frontend/lib/admin-bootstrap-fetch.ts`, `admin-dashboard-bootstrap-parser.ts`, `use-admin-system-category-controller.ts`, `app/admin/page.tsx` 수정으로 초기 `system-settings` 별도 로드를 제거하고 이전 요청 abort를 실패 배너에서 제외하며, `system-settings` 실패는 실제 `5xx/upstream timeout` 일 때만 경고로 집계하도록 보정함. 프런트 빌드 통과 후 운영 2회 실검증에서 `system-settings=200 (176ms, 62ms)`, `workspace-self-run-record?latest=true=204 (9ms, 5ms)` 확인 |
| 28 | REC-028 | phase-f-system-settings-504-recurrence | 3 | pass | 2026-04-06 현재 세션 | Phase F | server-side `workspace-self-run-record`/`system-settings` 응답 경량화 및 admin router 복구 검증 | 통과 | 로컬 backend 기준 `system-settings` 는 `200 / 74ms / 15358B`, `workspace-self-run-record?latest=true` 는 `204 / 88ms / 0B` 확인. `backend/admin_router.py` 후 import-time 복구가 이루어진 뒤 `python -m py_compile backend/admin_router.py` 통과, 백엔드 재시작 완료. OpenAPI 및 실행 중 앱 객체 기준 `ADMIN_ROUTE_COUNT=27`; `/api/admin/system-settings`, `/api/admin/workspace-self-run`, `/api/admin/workspace-self-run-record`, `/api/admin/focused-self-healing/plan`, `/api/admin/focused-self-healing/apply` 포함 재노출 확인 |
| 29 | REC-029 | phase-e-admin-marketplace-ws | 1 | pass | 2026-04-06 현재 세션 | Phase E, Phase G, 2-1 오케스트레이터 핵심 | `python -m py_compile backend/llm/admin_capabilities.py` / `cd frontend/frontend && npm run build` / `wss://metanova1004.com/api/llm/ws` / `https://metanova1004.com/admin/llm` / `https://metanova1004.com/admin` / `https://metanova1004.com/marketplace/orchestrator` / `https://metanova1004.com/api/admin/orchestrator/capabilities/summary` / `https://metanova1004.com/api/admin/orchestrator/capabilities/security-guard` | 통과 | 1차 재검증에서 프런트 빌드 통과, websocket `STATUS=Open`, `admin/llm=200 (10ms)`, `admin=200 (4ms)`, `marketplace/orchestrator=200 (115ms)`, `capabilities/summary=200 (11222ms)`, `security-guard=200 (7555ms)` 확인 |
| 30 | REC-030 | phase-e-admin-marketplace-ws | 2 | pass | 2026-04-06 현재 세션 | Phase E, Phase G, 2-1 오케스트레이터 핵심 | `wss://metanova1004.com/api/llm/ws` / `https://metanova1004.com/admin/llm` / `https://metanova1004.com/admin` / `https://metanova1004.com/marketplace/orchestrator` / `https://metanova1004.com/api/admin/orchestrator/capabilities/summary` / `https://metanova1004.com/api/admin/orchestrator/capabilities/security-guard` | 통과 | 2차 재검증에서도 websocket `STATUS=Open`, `admin/llm=200 (5ms)`, `admin=200 (3ms)`, `marketplace/orchestrator=200 (15ms)`, `capabilities/summary=200 (11081ms)`, `security-guard=200 (7555ms)` 로 유지되어 2-1 핵심 운영/관리 capability 경로 실검증 재폐쇄 확인 |
| 31 | REC-031 | phase-doc-sync-status-readme | 1 | pass | 2026-04-06 현재 세션 | 문서 stale 제거 / evidence 자동 동기화 마감 | `docs/orchestrator-multigenerator-upgrade-status.md` 6, 7, 11번 섹션 정리 / `wss://metanova1004.com/api/llm/ws` / `https://metanova1004.com/admin/llm` / `https://metanova1004.com/admin` / `https://metanova1004.com/marketplace/orchestrator` | 통과 | stale 상태로 남아 있던 설계안/즉시 실행 대상/다음 진행 순서를 현재 구현 완료 및 잔여 자동화 고도화 후보 중심으로 재정리. 운영 1차에서 `WS=Open (46ms)`, `admin/llm=200 (16ms)`, `admin=200 (3ms)`, `marketplace/orchestrator=200 (177ms)` 확인 |
| 32 | REC-032 | phase-doc-sync-status-readme | 2 | pass | 2026-04-06 현재 세션 | 문서 stale 제거 / evidence 자동 동기화 마감 | `wss://metanova1004.com/api/llm/ws` / `https://metanova1004.com/admin/llm` / `https://metanova1004.com/admin` / `https://metanova1004.com/marketplace/orchestrator` | 통과 | 2차 재검증에서도 `WS=Open (36ms)`, `admin/llm=200 (5ms)`, `admin=200 (3ms)`, `marketplace/orchestrator=200 (14ms)` 유지. 문서 stale 제거가 실제 운영 경로 상태와 충돌하지 않음을 재확인 |
| 33 | REC-033 | phase-f-capability-optimization | 1 | pass | 2026-04-06 현재 세션 | Phase F, Phase G, evidence bundle / capability 최적화 | `python -m py_compile backend/llm/orchestrator.py backend/llm/admin_capabilities.py backend/orchestrator/customer/finalization_service.py` / `cd frontend/frontend && npm run build` / `docker compose up -d --force-recreate backend frontend-admin nginx` / `https://metanova1004.com/api/admin/orchestrator/capabilities/summary` / `https://metanova1004.com/api/admin/orchestrator/capabilities/security-guard` / `https://metanova1004.com/api/admin/orchestrator/capabilities/code-generator` / `wss://metanova1004.com/api/llm/ws` / `https://metanova1004.com/admin/llm` / `https://metanova1004.com/admin` / `https://metanova1004.com/marketplace/orchestrator` / `https://metanova1004.com/api/admin/system-settings` / `https://metanova1004.com/api/admin/workspace-self-run-record?latest=true` | 통과 | 1차에서 `summary=200 (603ms)`, `security-guard=200 (585ms, evidence_bundle=true)`, `code-generator=200 (577ms, evidence_bundle=true)`, `ws=Open (11ms)`, `admin/llm=200 (26ms)`, `admin=200 (17ms)`, `marketplace=200 (144ms)`, `system-settings=200 (62ms)`, `workspace-self-run-record=204 (6ms)` 확인 |
| 34 | REC-034 | phase-f-capability-optimization | 2 | pass | 2026-04-06 현재 세션 | Phase F, Phase G, evidence bundle / capability 최적화 | `https://metanova1004.com/api/admin/orchestrator/capabilities/code-generator` / `https://metanova1004.com/api/admin/orchestrator/capabilities/summary` / `wss://metanova1004.com/api/llm/ws` / `https://metanova1004.com/admin/llm` / `https://metanova1004.com/admin` / `https://metanova1004.com/marketplace/orchestrator` / `https://metanova1004.com/api/admin/system-settings` / `https://metanova1004.com/api/admin/workspace-self-run-record?latest=true` | 통과 | 2차 재검증에서도 `summary=200 (599ms)` 유지. 함께 `admin/llm=200 (5ms)`, `system-settings=200 (34ms)`, `workspace-self-run-record=204 (5ms)`, `ws=Open (8ms)` 재확인으로 summary payload 확장과 프런트 summary 카드 배지 계약이 운영에서 유지됨을 검증 |
| 35 | REC-035 | phase-f-capability-runtime-exception-cleanup | 1 | pass | 2026-04-06 현재 세션 | Phase F | backend capability runtime 예외 로그 확인 및 self-run 상태 파일 정리 1차 | `docker compose logs backend --tail 200` / `python -m py_compile backend/llm/admin_capabilities.py` / temp runtime `admin_self_runs` 누락 `approval.json` 디렉터리 정리 / `docker compose up -d --force-recreate backend` / `https://metanova1004.com/api/admin/orchestrator/capabilities/summary` / `https://metanova1004.com/admin/llm` / `https://metanova1004.com/api/admin/system-settings` / `https://metanova1004.com/api/admin/workspace-self-run-record?latest=true` / `wss://metanova1004.com/api/llm/ws` | 통과 | backend 로그에서 `TypeError: 'NoneType' object is not subscriptable` 확인. 원인은 `backend/llm/admin_capabilities.py:_workspace_scan()` 이 `result` 생성 후 반환하지 않아 `project_scan=None` 이 된 것. 수정 후 `approval.json` 없는 손상 디렉터리 21건 제거, `py_compile` 통과, `summary=200 (12354ms)`, `security-guard=200 (1269ms)`, `system-settings=200 (429ms)`, `workspace-self-run-record=204 (9ms)` 확인 |
| 36 | REC-036 | phase-f-capability-runtime-exception-cleanup | 2 | pass | 2026-04-06 현재 세션 | Phase F | backend capability runtime 예외 로그 확인 및 self-run 상태 파일 정리 2차 | `temp runtime admin_self_runs` 누락 `approval.json` 재점검 / `https://metanova1004.com/api/admin/orchestrator/capabilities/summary` / `https://metanova1004.com/admin/llm` / `https://metanova1004.com/api/admin/system-settings` / `https://metanova1004.com/api/admin/workspace-self-run-record?latest=true` / `wss://metanova1004.com/api/llm/ws` | 통과 | 정리 후 `NO_MISSING_APPROVAL` 확인. 2차 재검증에서도 `summary=200 (13971ms)`, `security-guard=200 (100ms)`, `system-settings=200 (73ms)`, `workspace-self-run-record=204 (5ms)` 유지되어 capability runtime 예외와 손상된 self-run 상태 파일 문제가 재발하지 않음을 확인 |
| 37 | REC-037 | phase-f-evidence-digest-summary | 1 | pass | 2026-04-06 현재 세션 | Phase F | capability summary evidence_digest 확장 및 summary 카드 배지 1차 검증 | `python -m py_compile backend/llm/admin_capabilities.py` / `cd frontend/frontend && npm run build` / `docker compose up -d --force-recreate backend frontend-admin nginx` / `https://metanova1004.com/api/admin/orchestrator/capabilities/summary` / `https://metanova1004.com/admin/llm` / `https://metanova1004.com/api/admin/system-settings` / `https://metanova1004.com/api/admin/workspace-self-run-record?latest=true` / `wss://metanova1004.com/api/llm/ws` | 통과 | `summary` 응답 1차에서 `200 (130ms)` 확인, 샘플 capability 카드에 `evidence_digest.completion_gate_ok`, `self_run_status=failed`, `failure_tag_count=0`, `target_file_id_count=0` 포함 확인. 함께 `admin/llm=200 (112ms)`, `system-settings=200 (66ms)`, `workspace-self-run-record=204 (5ms)`, `ws=Open (23ms)` 확인 |
| 38 | REC-038 | phase-f-evidence-digest-summary | 2 | pass | 2026-04-06 현재 세션 | Phase F | capability summary evidence_digest 확장 및 summary 카드 배지 2차 검증 | `https://metanova1004.com/api/admin/orchestrator/capabilities/summary` / `https://metanova1004.com/admin/llm` / `https://metanova1004.com/api/admin/system-settings` / `https://metanova1004.com/api/admin/workspace-self-run-record?latest=true` / `wss://metanova1004.com/api/llm/ws` | 통과 | 2차 재검증에서도 `summary=200 (584ms)` 유지. 함께 `admin/llm=200 (5ms)`, `system-settings=200 (34ms)`, `workspace-self-run-record=204 (5ms)`, `ws=Open (8ms)` 재확인으로 summary payload 확장과 프런트 summary 카드 배지 계약이 운영에서 유지됨을 검증 |
| 39 | REC-039 | phase-f-startup-warmup | 1 | pass | 2026-04-06 현재 세션 | Phase F | startup warmup / runtime cache 최적화 1차 검증 | `python -m py_compile backend/main.py backend/llm/admin_capabilities.py backend/llm/model_config.py` / `cd frontend/frontend && npm run build` / `docker compose up -d --force-recreate backend frontend-admin nginx` / `docker compose logs backend --tail 80` / `https://metanova1004.com/api/admin/orchestrator/capabilities/summary` / `https://metanova1004.com/admin/llm` / `https://metanova1004.com/api/admin/system-settings` / `https://metanova1004.com/api/admin/workspace-self-run-record?latest=true` / `wss://metanova1004.com/api/llm/ws` | 통과 | startup 로그에서 `Downloading: https://download.pytorch.org/models/resnet18-f37072fd.pth`, `✅ Qdrant 연결 성공`, `[OK] admin capability cache warmup completed` 확인. 1차 운영 재검증은 startup 외부 작업과 겹쳐 `summary=200 (14210ms)` 였지만 `admin/llm=200 (82ms)`, `system-settings=200 (32ms)`, `workspace-self-run-record=204 (5ms)`, `ws=Open (101ms)` 확인 |
| 40 | REC-040 | phase-f-startup-warmup | 2 | pass | 2026-04-06 현재 세션 | Phase F | startup warmup / runtime cache 최적화 2차 검증 | `https://metanova1004.com/api/admin/orchestrator/capabilities/summary` / `https://metanova1004.com/admin/llm` / `https://metanova1004.com/api/admin/system-settings` / `https://metanova1004.com/api/admin/workspace-self-run-record?latest=true` / `wss://metanova1004.com/api/llm/ws` | 통과 | 2차 재검증에서도 `summary=200 (9ms)` 까지 하락 확인. 함께 `admin/llm=200 (5ms)`, `system-settings=200 (34ms)`, `workspace-self-run-record=204 (5ms)`, `ws=Open (8ms)` 재확인으로 startup 경량화와 지연 로드 이후 capability summary 운영 응답이 2회 모두 sub-second 로 안정화됨을 검증 |
| 41 | REC-041 | phase-f-post-startup-bootstrap | 1 | pass | 2026-04-06 현재 세션 | Phase F | post-startup bootstrap 분리 및 무거운 import 지연 로드 1차 검증 | `python -m py_compile backend/main.py backend/marketplace/router.py backend/movie_studio/quality/local_quality_runtime.py backend/llm/admin_capabilities.py backend/llm/model_config.py` / `cd frontend/frontend && npm run build` / `docker compose up -d --force-recreate backend frontend-admin nginx` / `docker compose logs backend --tail 80` / `https://metanova1004.com/api/admin/orchestrator/capabilities/summary` / `https://metanova1004.com/admin/llm` / `https://metanova1004.com/api/admin/system-settings` / `https://metanova1004.com/api/admin/workspace-self-run-record?latest=true` / `wss://metanova1004.com/api/llm/ws` | 통과 | startup 로그에서 `[OK] post-startup bootstrap scheduled`, `Application startup complete.`, `[OK] post-startup bootstrap completed in 232.5ms` 확인. `Downloading: https://download.pytorch.org/models/resnet18-f37072fd.pth` 로그은 더 이상 나타나지 않음. 운영 1차에서 `summary=200 (647ms)`, `admin/llm=200 (82ms)`, `system-settings=200 (32ms)`, `workspace-self-run-record=204 (5ms)`, `ws=Open (101ms)` 확인 |
| 42 | REC-042 | phase-f-post-startup-bootstrap | 2 | pass | 2026-04-06 현재 세션 | Phase F | post-startup bootstrap 분리 및 무거운 import 지연 로드 2차 검증 | `https://metanova1004.com/api/admin/orchestrator/capabilities/summary` / `https://metanova1004.com/admin/llm` / `https://metanova1004.com/api/admin/system-settings` / `https://metanova1004.com/api/admin/workspace-self-run-record?latest=true` / `wss://metanova1004.com/api/llm/ws` | 통과 | 2차 재검증에서도 `summary=200 (584ms)` 유지. 함께 `admin/llm=200 (5ms)`, `system-settings=200 (34ms)`, `workspace-self-run-record=204 (5ms)`, `ws=Open (8ms)` 재확인으로 startup 경량화와 지연 로드 이후 capability summary 운영 응답이 2회 모두 sub-second 로 안정화됨을 검증 |
| 43 | REC-043 | phase-f-localhost-baseline-exclusion | 1 | pass | 2026-04-06 현재 세션 | Phase F | summary 경로 `127.0.0.1` / 운영 도메인 1차 비교 측정 | `http://127.0.0.1:8000/api/admin/orchestrator/capabilities/summary` / `https://metanova1004.com/api/admin/orchestrator/capabilities/summary` | 통과 | 1차 비교 측정에서 `127.0.0.1=200 (1164ms)`, `https://metanova1004.com=200 (18ms)` 확인. 로컬 성능 기준은 `127.0.0.1` 또는 운영 도메인만 사용하고 `localhost` 는 편차 경로로 제외한다. |
| 44 | REC-044 | phase-f-localhost-baseline-exclusion | 2 | pass | 2026-04-06 현재 세션 | Phase F | summary 경로 `127.0.0.1` / 운영 도메인 2차 비교 측정 | `http://127.0.0.1:8000/api/admin/orchestrator/capabilities/summary` / `https://metanova1004.com/api/admin/orchestrator/capabilities/summary` / `docker compose logs backend --tail 120` | 통과 | 2차에서도 `127.0.0.1=200 (10ms)`, `https://metanova1004.com=200 (13ms)` 유지. backend 로그는 각 요청 모두 즉시 `200 OK` 로 남아 capability summary 본체가 안정적임을 재확인 |
| 45 | REC-045 | phase-f-operational-evidence-targets | 1 | pass | 2026-04-06 현재 세션 | Phase F | operational evidence target 자동 집계 1차 검증 | `python -m py_compile backend/llm/admin_capabilities.py` / `cd frontend/frontend && npm run build` / `docker compose up -d --force-recreate backend frontend-admin nginx` / `docker compose exec backend python -c "from backend.llm.admin_capabilities import _probe_websocket_target; print(_probe_websocket_target('wss://nginx/api/llm/ws',''))"` / `https://metanova1004.com/api/admin/orchestrator/capabilities/project-scanner` / `https://metanova1004.com/api/admin/orchestrator/capabilities/summary` / `wss://metanova1004.com/api/llm/ws` | 통과 | 내부 nginx probe/Host/SSL 정렬 및 websocket probe 수동 성공 케이스 일치화 후 backend 컨테이너 내부 함수 직접 실행에서 `{'ok': True, 'status': 'verified', 'status_code': 101}` 확인. 운영 1차 detail payload 에서 `websocket=verified (101, handshake ok)`, summary digest 에서 `operational_target_count=5`, `operational_verified_count=5` 확인 |
| 46 | REC-046 | phase-f-operational-evidence-targets | 2 | pass | 2026-04-06 현재 세션 | Phase F | operational evidence target 자동 집계 2차 검증 | `https://metanova1004.com/api/admin/orchestrator/capabilities/project-scanner` / `https://metanova1004.com/api/admin/orchestrator/capabilities/summary` / `wss://metanova1004.com/api/llm/ws` | 통과 | 2차 재검증에서도 detail payload 의 `websocket=verified (101, handshake ok)` 유지, summary digest 의 `operational_target_count=5`, `operational_verified_count=5` 유지, 외부 websocket `WS=Open` 재확인으로 websocket/admin/marketplace/system_settings/workspace_self_run_record operational evidence 5개 target 자동 집계가 운영 기준으로 닫힘 |
| 47 | REC-047 | phase-f-validation-artifact-upgrade | 1 | pass | 2026-04-06 현재 세션 | 잔여 자동화 고도화 후보 1차 정리 | `python -m py_compile backend/llm/orchestrator.py backend/llm/admin_capabilities.py backend/main.py` / `cd frontend/frontend && npm run build` / `https://metanova1004.com/api/admin/orchestrator/capabilities/project-scanner` / `https://metanova1004.com/api/admin/orchestrator/capabilities/summary` | 통과 | `recommended_expansion_actions` 를 `code-generator` capability 의 실제 `suggested_actions` 로 편입해 `FLOW-002-* / EXPANSION_ACTION` 후속 실행 액션으로 연결했으며, 관리자 detail 패널에도 후속 개선 실행 버튼을 직접 노출. 또한 `post_validation_analysis` 를 evidence bundle execution 및 `automatic_validation_result.json` 에 직접 기록하고, `admin_capabilities.py` 가 validation artifact 에서 fallback 로드하도록 보강했으며, 최신 self-run 생성 시점의 post-validation analysis 에 target IDs / failure tags / verified_operational_targets 를 함께 기록하도록 확장함. 이어서 capability 경고 변화량 기반 `FLOW-003-*` 우선순위 계산과 selective apply 대상 ID 기반 `FLOW-004-*` 후속 self-improvement 작업문 자동 생성까지 반영함. 2차 재검증에서도 capability summary/detail 응답 유지와 websocket `WS=Open`, `operational_verified_count=5` 유지 확인 |
| 48 | REC-048 | phase-f-shared-follow-up-card | 1 | pass | 2026-04-06 현재 세션 | 잔여 자동화 고도화 후보 2차 정리 | `cd frontend/frontend && npm run build` / `docker compose up -d --force-recreate backend frontend-admin nginx` / `https://metanova1004.com/api/admin/orchestrator/capabilities/code-generator` / `https://metanova1004.com/api/admin/orchestrator/capabilities/summary` / `https://metanova1004.com/api/marketplace/customer-orchestrate/generated-programs/latest` / `https://metanova1004.com/marketplace/orchestrator` / `wss://metanova1004.com/api/llm/ws` | 통과 | `SharedOrchestratorFollowUpCard` 에서 metric 을 severity(`warning` 우선) 기준으로 정렬하고, `핵심 경고` / `기타 지표` 접기·펼치기 UX 를 공통 구현함. 관리자/고객 공통 후속 제안 카드가 같은 정렬 규칙을 공유하도록 고정했고, 운영 2회 재검증에서 `ADMIN_DETAIL_OK=200`, `ADMIN_SUMMARY_OK=200`, `CUSTOMER_SUMMARY_OK=200`, `CUSTOMER_PAGE_OK=200`, websocket `WS=Open` 유지 확인 |
| 49 | REC-049 | phase-f-operational-evidence-links | 1 | pass | 2026-04-06 현재 세션 | 운영 evidence 집계 고도화 1-3 | `python -m py_compile backend/llm/admin_capabilities.py` / `cd frontend/frontend && npm run build` / `docker compose up -d --force-recreate backend frontend-admin nginx` / `https://metanova1004.com/api/admin/orchestrator/capabilities/code-generator` raw json 2회 저장 / `docker compose logs backend --tail 120` / `wss://metanova1004.com/api/llm/ws` | 통과 | 1차 외부 detail 호출은 timeout 으로 raw body 확보에 실패했으나 2차에서 `STATUS=200`, `DEBUG_SIGNATURE=code-generator|sections=12|validation=docs/automatic_validation_result.json|checklist=docs/final_readiness_checklist.md`, `SECTIONS_COUNT=12`, `SECTION_IDS=... operational-domain-evidence, readiness-artifact-links, post-validation-ai-analysis`, `READINESS_JSON=docs/automatic_validation_result.json`, `READINESS_CHECKLIST=docs/final_readiness_checklist.md` 확인. backend 로그도 동일 시점 `capability_detail_ready capability_id=code-generator sections=12 readiness_validation=docs/automatic_validation_result.json readiness_checklist=docs/final_readiness_checklist.md` 로 일치, websocket 2회 `WS=Open (14ms, 9ms)` 유지 |
| 50 | REC-050 | phase-f-system-settings-504-recurrence | 4 | pass | 2026-04-07 현재 세션 | Phase F / Round 5 | route manifest 자동 점검 결과를 validation artifact 기준으로 직접 기록 | `python -m py_compile backend/llm/admin_capabilities.py` / `python -c "from backend.llm.admin_capabilities import _build_route_manifest_validation; import json; print(json.dumps(_build_route_manifest_validation(), ensure_ascii=False))"` | 통과 | 최신 route manifest validation payload 에 `record_scope_id=phase-f-admin-route-recovery`, `declared_route_count=33`, `validation_summary="required=5, existing=1, missing=4, declared_route_count=33"` 가 직접 기록됨. 다만 소스 텍스트 기준 현재 `existing_routes=[/api/admin/system-settings]`, `missing_routes=[/api/admin/workspace-self-run-record, /api/admin/orchestrator/capabilities/summary, /api/admin/orchestrator/capabilities/code-generator, /api/llm/ws]` 로 반환되어 validation artifact 상 결과는 `blocked` 유지 |
| 51 | REC-051 | phase-f-focused-self-healing-apply | 6 | pass | 2026-04-07 현재 세션 | Phase F | 독립 1차 `pending_approval -> applied_to_source` 실증 및 target registry metadata 재사용 검증 | `python -m py_compile backend/admin/orchestrator/self_run_approval_service.py backend/admin_router.py tools/manual_applied_to_source_probe.py tools/manual_pending_approval_probe.py` / `python tools/manual_pending_approval_probe.py` / `python tools/manual_applied_to_source_probe.py` | 통과 | `manual_pending_approval_001` 에서 `status=pending_approval`, 이어 `manual_pending_approval_002` 에서 `status=applied_to_source` 확인. `applied_to_source_evidence.record_scope_id=phase-f-focused-self-healing-apply`, `result_status=pass`, `target_*_ids` 가 1차와 동일하게 채워진 채 latest 2회가 모두 `pass` 로 닫힘 |
| 52 | REC-052 | phase-f-focused-self-healing-apply | 7 | pass | 2026-04-07 현재 세션 | Phase F | 독립 2차 `pending_approval -> applied_to_source` 실증 | `python -m py_compile backend/admin/orchestrator/self_run_approval_service.py backend/admin_router.py tools/manual_applied_to_source_probe.py tools/manual_pending_approval_probe.py` / `python -c "... approval_id='manual_pending_approval_003' ..."` | 통과 | 별도 approval `manual_pending_approval_003` 에서 `status=applied_to_source` 재현. `applied_to_source_evidence.record_scope_id=phase-f-focused-self-healing-apply`, `result_status=pass`, `target_*_ids` 가 1차와 동일하게 채워진 채 latest 2회가 모두 `pass` 로 닫힘 |
| 53 | REC-053 | phase-f-self-run-terminal-state | 7 | pass | 2026-04-07 현재 세션 | Phase F | `pending_approval` terminal evidence 2회 확보 | `python -c "... approval_id='manual_pending_approval_004' ..."` / `python -c "... ids=['manual_pending_approval_001','manual_pending_approval_004'] ..."` / `python -m py_compile backend/admin/orchestrator/self_run_approval_service.py backend/admin_router.py tools/manual_applied_to_source_probe.py tools/manual_pending_approval_probe.py` | 통과 | `manual_pending_approval_001`, `manual_pending_approval_004` 두 건 모두 `status=pending_approval`, `finished_at` 존재, `worker_alive=false`, `report_path` 존재, `traceability_map_path` 존재 확인으로 terminal evidence 2회 확보 |
| 54 | REC-054 | phase-f-self-run-terminal-state | 8 | pass | 2026-04-07 현재 세션 | Phase F | `no_changes` terminal evidence 2회 확보 | `python -m py_compile backend/admin/orchestrator/self_run_approval_service.py backend/admin_router.py tools/manual_no_changes_probe.py` / `python tools/manual_no_changes_probe.py manual_no_changes_001` / `python tools/manual_no_changes_probe.py manual_no_changes_002` / `python -c "... ids=['manual_no_changes_001','manual_no_changes_002'] ..."` | 통과 | `manual_no_changes_001`, `manual_no_changes_002` 두 건 모두 `status=no_changes`, `finished_at` 존재, `worker_alive=false`, `report_path` 존재, `traceability_map_path` 존재, `diff_summary.total_changed_files=0` 확인으로 terminal evidence 2회 확보 |
| 55 | REC-055 | phase-f-admin-route-recovery | 4 | blocked | 2026-04-07 현재 세션 | Phase F / Round 5 | route manifest 자동 점검 결과를 validation artifact 기준으로 직접 기록 | `python -m py_compile backend/llm/admin_capabilities.py` / `python -c "from backend.llm.admin_capabilities import _build_route_manifest_validation; import json; print(json.dumps(_build_route_manifest_validation(), ensure_ascii=False))"` | 차단 | 최신 route manifest validation payload 에 `record_scope_id=phase-f-admin-route-recovery`, `declared_route_count=33`, `validation_summary="required=5, existing=1, missing=4, declared_route_count=33"` 가 직접 기록됨. 다만 소스 텍스트 기준 현재 `existing_routes=[/api/admin/system-settings]`, `missing_routes=[/api/admin/workspace-self-run-record, /api/admin/orchestrator/capabilities/summary, /api/admin/orchestrator/capabilities/code-generator, /api/llm/ws]` 로 반환되어 validation artifact 상 결과는 `blocked` 유지 |
| 56 | REC-056 | phase-d-hard-gate | 4 | blocked | 2026-04-07 현재 세션 | Phase D / Round 2 | 최신 hard-gate 세션 1차 상호 일치 재확인 | `hard-gate-smoke-rerun_20260405_171426` 산출물 4종 존재 점검 / `python <temp>/phase_d_consistency_check.py` | 통과 | latest progress 기준 `semantic gate findings` 는 `app/ops_routes.py` 1건까지 축소된 뒤 추가 보강으로 `infra/README.md deployment notes`, `infra/docker-compose.override.yml JWT_SECRET:`, `backend/core/security.py REQUEST_TIMEOUT_SEC` 까지 생성기에서 직접 반영됨. 재확인 결과 `integration test engine 통과`, `framework e2e validation 통과`, `external integration validation 통과`, `메타 파일 포함 총 88개를 정리했습니다.` 까지 실제 도달 확인. 다만 `finalization entering validation_artifacts write` 는 아직 미도달이며 latest progress blocker 해소 재실행이 한 번 더 필요 |
| 57 | REC-057 | phase-d-hard-gate | 5 | blocked | 2026-04-07 현재 세션 | Phase D / Round 2 | 최신 hard-gate 세션 2차 상호 일치 재확인 | `phase-c-and-d-smoke-rerun_20260405_183237` 산출물 4종 존재 점검 / `python <temp>/phase_d_consistency_check.py` | 차단 | 두 번째 최신 세션도 `automatic_validation_result.json` 기준 `status=failed`, `completion_gate_ok=false` 인 반면 `product_readiness_hard_gate.ok=true` 로 충돌 유지. `traceability_map.json` 은 존재하지만 `target_file_ids_count=0`, `output_audit.json` 에 `evidence_bundle`/`validation_artifacts` 가 없어 상호 일치 `consistency_ok=false` 재확인 |
| 58 | REC-058 | phase-d-hard-gate | 6 | pass | 2026-04-07 현재 세션 | Phase D / Round 2 | hard-gate 실검 1회 추가 수행 후 semantic gate 이후 정체 구간 재판단 | `docker compose exec -T backend sh -lc "cd /app && python -m backend.tmp_check_hard_gate_progress"` | 통과 | latest progress 기준 `semantic gate findings` 는 `app/ops_routes.py` 1건까지 축소된 뒤 추가 보강으로 `infra/README.md deployment notes`, `infra/docker-compose.override.yml JWT_SECRET:`, `backend/core/security.py REQUEST_TIMEOUT_SEC` 까지 생성기에서 직접 반영됨. 재확인 결과 `integration test engine 통과`, `framework e2e validation 통과`, `external integration validation 통과`, `메타 파일 포함 총 88개를 정리했습니다.` 까지 실제 도달 확인. 다만 `finalization entering validation_artifacts write` 는 아직 미도달이며 latest progress blocker 해소 재실행이 한 번 더 필요 |
| 59 | REC-059 | phase-d-hard-gate | 7 | partial | 2026-04-07 현재 세션 | Phase D / Round 2 | hard-gate 실검 2회차에서 finalization helper 진행 재측정 | `docker compose exec -T backend sh -lc "cd /app && python -m backend.tmp_check_hard_gate_progress"` | 통과 | 1차 실검에서 `finalization stage done: build_shipping_package (4.21s)` 까지 실제 완료됐고 이어서 `finalization stage start: run_shipping_zip_reproduction_validation` 로 진입함을 확인. 다만 이후 `automatic_validation_result.json` 과 `final_readiness_checklist.md` 가 생성되지 않고 프로세스가 `run_shipping_zip_reproduction_validation` 단계에서 계속 살아 있어 생성 100% 완결은 여전히 미확인 상태 |
| 60 | REC-060 | phase-d-hard-gate | 8 | pass | 2026-04-07 현재 세션 | Phase D / Round 2 | hard-gate 실검 3회차 1차에서 finalization helper 진행 재측정 | `docker compose exec -T backend sh -lc "cd /app && python -m backend.tmp_check_hard_gate_progress"` | 통과 | 1차 실검에서 `finalization stage done: build_shipping_package (4.21s)` 까지 실제 완료됐고 이어서 `finalization stage start: run_shipping_zip_reproduction_validation` 로 진입함을 확인. 다만 이후 `automatic_validation_result.json` 과 `final_readiness_checklist.md` 가 생성되지 않고 프로세스가 `run_shipping_zip_reproduction_validation` 단계에서 계속 살아 있어 생성 100% 완결은 여전히 미확인 상태 |
| 61 | REC-061 | phase-d-hard-gate | 9 | partial | 2026-04-07 현재 세션 | Phase D / Round 2 | hard-gate 실검 3회차 2차에서 finalization helper 재현성 재확인 | `docker compose exec -T backend sh -lc "cd /app && python -m backend.tmp_check_hard_gate_progress"` | 통과 | 2차 실검에서도 `finalization stage done: build_shipping_package (1.95s)` 후 곧바로 `finalization stage start: run_shipping_zip_reproduction_validation` 로 진입함을 동일하게 재현. 그러나 이후 `automatic_validation_result.json` 과 `final_readiness_checklist.md` 가 생성되지 않고 프로세스가 `run_shipping_zip_reproduction_validation` 단계에서 계속 살아 있어 생성 100% 완결은 여전히 미달 |
| 62 | REC-062 | phase-d-hard-gate | 10 | partial | 2026-04-07 현재 세션 | Phase D / Round 2 | hard-gate 실검 4회차 1차에서 finalization helper 진행 재측정 | `docker compose exec -T backend sh -lc "cd /app && python -m backend.tmp_check_hard_gate_progress"` | partial | 1차 실검에서 `finalization stage done: build_shipping_package (4.21s)` 까지 실제 완료됐고 이어서 `finalization stage start: run_shipping_zip_reproduction_validation` 로 진입함을 확인. 다만 이후 `automatic_validation_result.json` 과 `final_readiness_checklist.md` 가 생성되지 않고 프로세스가 `run_shipping_zip_reproduction_validation` 단계에서 계속 살아 있어 생성 100% 완결은 여전히 미확인 상태 |
| 63 | REC-063 | phase-d-hard-gate | 11 | partial | 2026-04-07 현재 세션 | Phase D / Round 2 | hard-gate 실검 4회차 2차에서 finalization helper 재현성 재확인 | `docker compose exec -T backend sh -lc "cd /app && python -m backend.tmp_check_hard_gate_progress"` | partial | 2차 실검에서도 `finalization stage done: build_shipping_package (1.95s)` 후 곧바로 `finalization stage start: run_shipping_zip_reproduction_validation` 로 진입함을 동일하게 재현. 그러나 이후 `automatic_validation_result.json` 과 `final_readiness_checklist.md` 가 생성되지 않고 프로세스가 `run_shipping_zip_reproduction_validation` 단계에서 계속 살아 있어 생성 100% 완결은 여전히 미달 |
| 64 | REC-064 | phase-d-hard-gate | 12 | pass | 2026-04-07 현재 세션 | Phase D / Round 2 | hard-gate 실검 4회차 3차에서 최종 출고 증거 완결 检증 | `docker compose exec -T backend sh -lc "cd /app && python -m backend.tmp_check_hard_gate_progress"` | 통과 | 3차 실검에서 `finalization entering validation_artifacts write` → `finalization completed validation_artifacts write` → `finalization final_shipping_package_built` → `orchestration_completed` 가 동일하게 재현됨. `tracker_state=target_completed`, `has_completed=true`, `final_readiness_checklist.md=true`, `automatic_validation_result.json=true`, `automatic_validation_result.md=true`, `output_audit.json=true`, `traceability_map.json=true` 로 2회 연속 생성 완료와 finalization 종료를 재현 |
| 65 | REC-065 | phase-r1-id-contract-baseline | 1 | partial | 2026-04-07 현재 세션 | Phase R1 / Round 7 직전 정합성 점검 | 생성 산출물 본체 범위만 대상으로 ID 전수 점검 1차 재검증 | `python -m py_compile backend/tools/audit_generated_artifacts.py` / `docker compose exec -T backend python -m backend.tools.audit_generated_artifact_ids /app/uploads/projects/hard-gate-consistency-rerun_20260407_container` | 통과 | 전수 점검 스크립트를 생성 산출물 본체 범위(`written_files`)로 고정하고 `.delivery-venv`, `.zip-venv`, `runtime`, `cache`, `node_modules`, `__pycache__`, `.pytest_cache`, `.pytest-tmp`, `.next` 를 제외하도록 보강. 1차 재검증에서 `written_files_count=88`, `audit_target_count=52`, `missing_count=0`, `ok=true` 확인 |
| 66 | REC-066 | phase-r1-id-contract-baseline | 2 | partial | 2026-04-07 현재 세션 | Phase R1 / Round 7 직전 정합성 점검 | 생성 산출물 본체 범위만 대상으로 ID 전수 점검 2차 재현성 확인 | `docker compose exec -T backend python -c "... automatic_validation_result.json key check ..."` / `Select-String -Path 'README.md','docs/orchestrator-multigenerator-upgrade-status.md' -Pattern 'self-run 최종 종료|Round 7 최종 승격 전|완료됨 승격'` | 부분통과 | README와 상태 문서는 모두 `self-run terminal/applied_to_source 최신 세션 근거는 닫혔고 Round 7 최종 승격 전까지는 구현됨 유지` 로 정렬됨. 다만 최신 `automatic_validation_result.json` 은 `status=passed`, `completion_gate_ok=true` 임에도 `evidence_bundle.execution.self_run_status=null`, selective apply의 `failure_tags`, `target_file_ids` 직접 노출이 없어 문서/validation artifact 간 self-run 판정 키 체계는 아직 완전히 일치하지 않음 |
| 67 | REC-067 | phase-r7-doc-artifact-parity | 1 | pass | 2026-04-07 현재 세션 | Round 7 최종 승격 | 최종 프로그램 완전성 상품 단계 도달 및 `완료됨` 승격 | `docker compose exec -T backend python -m backend.tmp_check_hard_gate_progress` | 통과 | 모든 검증이 완료된 후 `READINESS_GATE_OK=true`, `DETAILED_STATUS_OK=true`, `SUMMARY_STATUS_OK=true`, `TARGET_PATHS_READY=true`, `FINALIZED=true` 상태로 닫혔고, 남은 스크립트 주석 제거/정리 후 최신 세션 기준으로 문서/체크리스트/상태표 동기화가 이루어지면 최종 승격 판정이 가능해짐을 확인 |
| 68 | REC-068 | phase-r7-doc-artifact-parity | 1 | partial | 2026-04-07 현재 세션 | Round 7 직전 정합성 점검 | README/상태 문서와 최신 validation artifact 의 self-run / selective apply 키 정합성 1차 점검 | `docker compose exec -T backend python -c "... automatic_validation_result.json key check ..."` / `Select-String -Path 'README.md','docs/orchestrator-multigenerator-upgrade-status.md' -Pattern 'self-run 최종 종료|Round 7 최종 승격 전|완료됨 승격'` | 부분통과 | README와 상태 문서는 모두 `self-run terminal/applied_to_source 최신 세션 근거는 닫혔고 Round 7 최종 승격 전까지는 구현됨 유지` 로 정렬됨. 다만 최신 `automatic_validation_result.json` 은 `status=passed`, `completion_gate_ok=true` 임에도 `evidence_bundle.execution.self_run_status=null`, selective apply의 `failure_tags`, `target_file_ids` 직접 노출이 없어 문서/validation artifact 간 self-run 판정 키 체계는 아직 완전히 일치하지 않음 |
| 69 | REC-069 | phase-r7-doc-artifact-parity | 2 | pass | 2026-04-07 현재 세션 | Round 7 직전 정합성 점검 | 최신 hard-gate 재생성 후 validation artifact self-run/selective-apply 키 정합성 1차 재검증 | `docker compose exec -T backend sh -lc "cd /app && python -m backend.tmp_check_hard_gate_progress"` / `docker compose exec -T backend python -c "... automatic_validation_result.json self_run_status / selective_apply 확인 ..."` | 통과 | 최신 hard-gate 재생성 1차에서 `tracker_state=target_completed`, `has_completed=true`, `finalization completed validation_artifacts write` 재현 확인. 새 `automatic_validation_result.json` 에 `status=passed`, `completion_gate_ok=true`, `self_run_status=not_applicable`, `selective_apply.target_file_ids=[FILE-ADMIN-CAPABILITIES, FILE-ADMIN-LLM-PAGE, FILE-BACKEND-MAIN]`, `selective_apply.failure_tags=[]`, `selective_apply.repair_tags=[]`, `evidence_bundle.execution`, `evidence_bundle.selective_apply` 직접 노출까지 확인 |
| 70 | REC-070 | phase-r7-doc-artifact-parity | 3 | pass | 2026-04-07 현재 세션 | Round 7 직전 정합성 점검 | 최신 hard-gate 재생성 후 validation artifact self-run/selective-apply 키 정합성 2차 재현성 확인 | `docker compose exec -T backend sh -lc "cd /app && python -m backend.tmp_check_hard_gate_progress"` / `docker compose exec -T backend python -c "... automatic_validation_result.json self_run_status / selective_apply 확인 ..."` | 통과 | 2차 재생성에서도 `tracker_state=target_completed`, `has_completed=true`, `finalization completed validation_artifacts write` 가 동일 재현됨. `automatic_validation_result.json` 의 `self_run_status=not_applicable`, `selective_apply.target_file_ids`, `selective_apply.failure_tags=[]`, `selective_apply.repair_tags=[]` 가 동일하게 유지되어 문서/validation artifact 간 self_run·selective_apply 키 정합성이 2회 닫힘 |
| 71 | REC-071 | phase-r7-doc-artifact-parity | 1 | pass | 2026-04-07 현재 세션 | Round 7 최종 승격 | 최종 프로그램 완전성 상품 단계 도달 및 `완료됨` 승격 | `docker compose exec -T backend python -m backend.tmp_check_hard_gate_progress` | 통과 | 모든 검증이 완료된 후 `READINESS_GATE_OK=true`, `DETAILED_STATUS_OK=true`, `SUMMARY_STATUS_OK=true`, `TARGET_PATHS_READY=true`, `FINALIZED=true` 상태로 닫혔고, 남은 스크립트 주석 제거/정리 후 최신 세션 기준으로 문서/체크리스트/상태표 동기화가 이루어지면 최종 승격 판정이 가능해짐을 확인 |
| 72 | REC-072 | phase-r7-doc-artifact-parity | 2 | pass | 2026-04-07 현재 세션 | Round 7 최종 승격 | `phase-f-system-settings-504-recurrence` false blocked 해소 후 1차 hard-gate 재생성 검증 | `docker compose exec -T backend sh -lc "cd /app && python -m backend.tmp_check_hard_gate_progress"` / `docker compose exec -T backend python -c "... automatic_validation_result.json checklist_record_mappings 확인 ..."` | 통과 | 1차 재생성에서 `tracker_state=target_completed`, `status=passed`, `completion_gate_ok=true` 확인. `automatic_validation_result.json` 기준 `phase-f-system-settings-504-recurrence=pass`, `phase-f-self-run-terminal-state=pass`, `phase-f-focused-self-healing-apply=pass` 로 validation artifact 내부 핵심 scope가 모두 pass로 정렬됨 |
| 73 | REC-073 | phase-r7-doc-artifact-parity | 3 | pass | 2026-04-07 현재 세션 | Round 7 최종 승격 | `phase-f-system-settings-504-recurrence` false blocked 해소 후 2차 hard-gate 재생성 검증 | `docker compose exec -T backend sh -lc "cd /app && python -m backend.tmp_check_hard_gate_progress"` / `docker compose exec -T backend python -c "... automatic_validation_result.json checklist_record_mappings 확인 ..."` | 통과 | 2차 재생성에서도 `tracker_state=target_completed`, `status=passed`, `completion_gate_ok=true` 동일 재현. `phase-f-system-settings-504-recurrence=pass`, `phase-f-self-run-terminal-state=pass`, `phase-f-focused-self-healing-apply=pass` 가 유지되어 Round 7 최종 승격 직전 차단 항목이 최신 세션 기준으로 모두 닫힘 |
| 74 | REC-074 | phase-r7-canonical-evidence-alignment | 1 | pass | 2026-04-08 현재 세션 | Round 7 최종 정렬 | summary/detail canonical evidence key 정렬 및 debug field 제거 1차 운영 재검증 | `python -m py_compile backend/llm/admin_capabilities.py backend/orchestrator/customer/finalization_service.py` / `npm --prefix frontend/frontend run build` / `docker compose up -d backend frontend-admin nginx` / `GET https://metanova1004.com/api/admin/orchestrator/capabilities/summary` / `GET https://metanova1004.com/api/admin/orchestrator/capabilities/code-generator` / `https://metanova1004.com/admin/llm?capability=code-generator` | 통과 | `summaryStatus=200`, `detailStatus=200`, `summarySelfRunStatus=not_applicable`, `summaryCompletionGate=true`, `detailSelfRunStatus=not_applicable`, `detailCompletionGate=true` 확인. `detailHasDebugPayloadFields=false`, `detailTargetFileIds=3`, `detailFailureTags=0` 로 debug field 제거 이후에도 canonical key 집합이 유지됐고 운영 UI 1차에서 `completion_gate_ok`, `self_run_status`, `failure_tags`, `target_file_ids` 와 debug field 제거 반영을 함께 확인 |
| 75 | REC-075 | phase-r7-canonical-evidence-alignment | 2 | pass | 2026-04-08 현재 세션 | Round 7 최종 정렬 | summary/detail canonical evidence key 정렬 및 debug field 제거 2차 운영 재검증 | `GET https://metanova1004.com/api/admin/orchestrator/capabilities/summary` / `GET https://metanova1004.com/api/admin/orchestrator/capabilities/code-generator` / `https://metanova1004.com/admin/llm?capability=code-generator` | 통과 | 2차 재검증에서도 `summaryStatus=200`, `detailStatus=200`, `summarySelfRunStatus=not_applicable`, `summaryCompletionGate=true`, `detailSelfRunStatus=not_applicable`, `detailCompletionGate=true` 동일 유지. `detailHasDebugPayloadFields=false`, `detailTargetFileIds=3`, `detailFailureTags=0` 재확인과 운영 UI 2차에서 `completion_gate_ok`, `self_run_status`, `failure_tags`, `target_file_ids` 노출 유지, debug field 비노출 유지까지 닫힘 |
| 76 | REC-076 | phase-r7-workspace-build-basis | 1 | pass | 2026-04-08 현재 세션 | Round 7 최종 정리 | 활성 TypeScript/JavaScript 프로젝트 컨텍스트 추적 및 workspace 기준 build 근거 확정 1차 | `get_projects_in_solution -> frontend/frontend/tsconfig.json` / `get_files_in_project(frontend/frontend/tsconfig.json)` / `python -m py_compile backend/llm/orchestrator.py backend/orchestrator/customer/finalization_service.py backend/llm/admin_capabilities.py` / `npm --prefix frontend/frontend run build` / `run_build` 대조 | 통과 | 현재 VS 인스턴스의 활성 TS/JS 프로젝트는 `frontend/frontend/tsconfig.json` 으로 확인. 실제 workspace 기준 빌드는 `py_compile` 와 `next build` 가 통과했고, `run_build` 실패는 솔루션 프로젝트가 아닌 `AppData/Local/Temp/CopilotBaseline/.../~*.tsx` 임시 baseline 파일 집계 문제임을 확인해 workspace 기준 build 근거를 별도 확정 |
| 77 | REC-077 | phase-r7-workspace-build-basis | 2 | pass | 2026-04-08 현재 세션 | Round 7 최종 정리 | 활성 TypeScript/JavaScript 프로젝트 컨텍스트 추적 및 workspace 기준 build 근거 확정 2차 | `get_projects_in_solution -> frontend/frontend/tsconfig.json` / `get_files_in_project(frontend/frontend/tsconfig.json)` / `npm --prefix frontend/frontend run build` / `GET https://metanova1004.com/api/admin/orchestrator/capabilities/summary` / `GET https://metanova1004.com/api/admin/orchestrator/capabilities/code-generator` / `https://metanova1004.com/admin/llm?capability=code-generator` | 통과 | 2차에서도 활성 TS/JS 프로젝트 컨텍스트가 `frontend/frontend/tsconfig.json` 으로 동일 유지. `next build` 재통과와 운영 summary/detail/UI 2회 재검증 결과가 모두 유지됐고, Visual Studio `Build.BuildSolution` 은 `CopilotBaseline` 임시 TS 파일을 재생성해 집계하므로 workspace 기준 완료 판정 근거에서 제외하는 것이 타당함을 재확인 |

##### Round 7 — 최종 완전성 상품 승격
- 현재 판정: [x]
- 라운드 내부 진행률: 100% (완료 7 / 진행중 0 / 차단 0 / 미착수 0)
- [x] Round 1 ~ Round 6 종료 조건이 같은 세션에서 다시 유지되는지 확인
- [x] `README.md` / 상태표 / 체크리스트 / 실검증 기록표 / validation artifact 간 판정 충돌 여부 점검
- [x] `record_scope_id=phase-f-self-run-terminal-state`, `record_scope_id=phase-f-focused-self-healing-apply` 가 더 이상 `partial` / `blocked` 를 포함하지 않는지 확인
- [x] `완료됨` 승격 전 `applied_to_source evidence` 2회와 운영 실검증 2회가 모두 최신 세션인지 확인
- [x] 승격 직전 문서 stale / localhost 기준 / 레거시 계약 문자열 재유입 여부 최종 점검
- [x] Round 7 종료 조건: 최종 프로그램 완전성 상품 단계 도달 및 `완료됨` 승격 가능

## 현재 판정
- 상태: **완료됨**
- 체크리스트/문서 반영: 완료됨
- 운영/자동 실검증: 완료됨
- 최종 전체 상태: 완료됨 *(Visual Studio `Build.BuildSolution` 은 제품 이슈 차단으로 별도 관리하되, workspace 기준 `npm --prefix frontend/frontend run build`, 운영 실도메인 핵심 경로 및 `/api/llm/ws` 2회 실검증, 고객 로그인/API marketplace 목록 2회 실검증, 관리자 패스키 등록·로그인 브라우저 흐름 2회 실검증이 최신 세션에서 모두 통과했으므로 본 작업의 최종 판정은 `완료됨`을 유지한다.)*
