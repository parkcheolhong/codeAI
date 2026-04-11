# Final Readiness Checklist

## 검증 기준
- 실제 산출물 문서에서 확인된 항목만 반영
- 과거 산출물의 실패 기록은 임의로 성공 처리하지 않음
- 2026-04-09 기준 `docs/` 및 `uploads/projects/` 하위 체크리스트성 문서를 대조한 결과만 기록

## 1. 루트 런타임 정합성
- [x] `docs/backend-runtime-analysis-checklist.md` 기준 로컬 runtime / DB / Redis / health 검증은 완료됐다.
  - 근거: `docs/backend-runtime-analysis-checklist.md`
- [x] 운영 서버/nginx/포트포워딩 경로를 복구했고 `metanova1004.com` 실도메인 경로가 다시 응답한다.
  - 근거: `scripts/ops_health_check.ps1` 재실행 결과 `metanova1004.com /health = 200`, `metanova1004.com HTTPS /health = 200`, `관리자 HTTPS 라우팅 = 200`
- [x] 운영 실도메인 핵심 경로(`summary`, `code-generator detail`, `admin/llm`, `system-settings`, `workspace-self-run-record`, `websocket`)는 2회 연속 실검증을 통과했다.
  - 근거: 2026-04-10 현재 세션 1차/2차 실측에서 `https://metanova1004.com/api/admin/orchestrator/capabilities/summary = 200`, `https://metanova1004.com/api/admin/orchestrator/capabilities/code-generator = 200`, `https://metanova1004.com/admin/llm?capability=code-generator = 200`, `https://metanova1004.com/api/admin/system-settings = 200`, `https://metanova1004.com/api/admin/workspace-self-run-record?latest=true = 200`, `wss://metanova1004.com/api/llm/ws = connected + pong`
- [x] 운영 로그인 경로와 marketplace 프로젝트 목록 경로는 실도메인 2회 재검증에서 모두 정상 응답한다.
  - 근거: 2026-04-10 현재 세션 1차/2차 실측에서 `https://xn--114-2p7l635dz3bh5j.com/api/auth/login`은 2회 모두 `access_token` 발급 성공, `https://xn--114-2p7l635dz3bh5j.com/api/marketplace/projects?skip=0&limit=24&sort_by=downloads&sort_order=desc`는 2회 모두 `projectCount = 24`, `total = 37`
- [x] 관리자 패스키 등록/로그인 브라우저 흐름은 운영 도메인 기준 2회 실검증을 통과했다.
  - 근거: `PLAYWRIGHT_ADMIN_BASE_URL=https://metanova1004.com npm --prefix frontend/frontend run e2e -- admin-passkey-operational.playwright.spec.ts` 실행 결과 `passkey register + login closes operational flow attempt 1`, `attempt 2` 모두 통과
- [x] 멀티 생성기 루트 `python_fastapi` 산출물의 semantic gate 필수 artifact 누락이 해소됐다.
  - 근거: `tools/inspect_python_fastapi_artifacts.py` 실행 결과 `missing_expected = []`
- [x] 멀티 생성기 산출물은 semantic gate 2회 재검증에서 모두 통과했다.
  - 근거: `docs/multi_generator_semantic_gate_verification.md`
- [x] 멀티 생성기 관련 빌드는 성공했다.
  - 근거: workspace build success

## 2. phaseb-direct-run-* 실패 체크리스트 정리
- [x] `phaseb-direct-run-*` 산출물 실제 위치를 확인하고 관련 실패 문서를 대조했다.
  - 근거: `uploads/projects/phaseb-direct-run-01_20260405_225523`, `uploads/projects/phaseb-direct-run-02_20260405_232213`
- [x] `phaseb-direct-run-01_20260405_225523/docs/final_readiness_checklist.md`의 `completion gate`는 현재 재실행 2회에서 닫혔다.
  - 근거: `tools/rerun_phaseb_only.py` 실행 결과 2회 모두 `completion_gate_checked = true`, `response_completion_gate_ok = true`
- [x] `phaseb-direct-run-01_20260405_225523/docs/final_readiness_checklist.md`의 `semantic gate`는 현재 재실행 2회에서 닫혔다.
  - 근거: `tools/rerun_phaseb_only.py` 실행 결과 2회 모두 `semantic_gate_checked = true`, `semantic_gate_score = 100`
- [x] `phaseb-direct-run-01_20260405_225523/docs/orchestrator_checklist.md`의 `semantic_audit_ok`는 현재 재실행 2회에서 `True`로 갱신됐다.
  - 근거: `tools/rerun_phaseb_only.py` 실행 결과 2회 모두 `semantic_audit_ok_true = true`, `response_semantic_audit_ok = true`
- [x] `phaseb-direct-run-01_20260405_225523/docs/semantic_completion_audit.md`는 현재 재실행 2회에서 `score: 100`, `status: pass`로 갱신됐다.
  - 근거: `tools/rerun_phaseb_only.py` 실행 결과 2회 모두 `semantic_completion_pass = true`, `response_semantic_audit_score = 100`
- [x] `phaseb-direct-run-01_20260405_225523/docs/generator_checklist.md`의 self-artifact 누락 문구는 현재 재실행 2회에서 재현되지 않았다.
  - 근거: `tools/rerun_past_failure_outputs.py` 실행 결과 2회 모두 `generator_missing_self_artifact = false`
- [x] `phaseb-direct-run-02_20260405_232213/docs/final_readiness_checklist.md`의 `completion gate`는 현재 재실행 2회에서 닫혔다.
  - 근거: `tools/rerun_phaseb_only.py` 실행 결과 2회 모두 `completion_gate_checked = true`, `response_completion_gate_ok = true`
- [x] `phaseb-direct-run-02_20260405_232213/docs/final_readiness_checklist.md`의 `semantic gate`는 현재 재실행 2회에서 닫혔다.
  - 근거: `tools/rerun_phaseb_only.py` 실행 결과 2회 모두 `semantic_gate_checked = true`, `semantic_gate_score = 100`
- [x] `phaseb-direct-run-02_20260405_232213/docs/orchestrator_checklist.md`의 `semantic_audit_ok`는 현재 재실행 2회에서 `True`로 갱신됐다.
  - 근거: `tools/rerun_phaseb_only.py` 실행 결과 2회 모두 `semantic_audit_ok_true = true`, `response_semantic_audit_ok = true`
- [x] `phaseb-direct-run-02_20260405_232213/docs/semantic_completion_audit.md`는 현재 재실행 2회에서 `score: 100`, `status: pass`로 갱신됐다.
  - 근거: `tools/rerun_phaseb_only.py` 실행 결과 2회 모두 `semantic_completion_pass = true`, `response_semantic_audit_score = 100`
- [x] `phaseb-direct-run-02_20260405_232213/docs/generator_checklist.md`의 self-artifact 누락 문구는 현재 재실행 2회에서 재현되지 않았다.
  - 근거: `tools/rerun_past_failure_outputs.py` 실행 결과 2회 모두 `generator_missing_self_artifact = false`

## 3. phase-c-and-d-smoke* 실패 체크리스트 정리
- [x] `phase-c-and-d-smoke*` 산출물 실제 위치를 확인하고 관련 실패 문서를 대조했다.
  - 근거: `uploads/projects/phase-c-and-d-smoke_20260405_183024`, `uploads/projects/phase-c-and-d-smoke-rerun_20260405_183237`
- [x] `phase-c-and-d-smoke_20260405_183024/docs/final_readiness_checklist.md`의 `completion gate`는 현재 재실행 2회에서 닫혔다.
  - 근거: `tools/rerun_past_failure_outputs.py` 최신 실행 결과 2회 모두 `response_completion_gate_ok = true`, `completion_gate_checked = true`
- [x] `phase-c-and-d-smoke_20260405_183024/docs/final_readiness_checklist.md`의 `semantic gate`는 현재 재실행 2회에서 닫혔다.
  - 근거: `tools/rerun_past_failure_outputs.py` 실행 결과 2회 모두 `semantic_gate_checked = true`
- [x] `phase-c-and-d-smoke_20260405_183024/docs/orchestrator_checklist.md`의 `semantic_audit_ok`는 현재 재실행 2회에서 `True`로 갱신됐다.
  - 근거: `tools/rerun_past_failure_outputs.py` 실행 결과 2회 모두 `semantic_audit_ok_true = true`, `semantic_audit_score_100 = true`
- [x] `phase-c-and-d-smoke_20260405_183024/docs/semantic_completion_audit.md`는 현재 재실행 2회에서 `score: 100`, `status: pass`로 갱신됐다.
  - 근거: `tools/rerun_past_failure_outputs.py` 실행 결과 2회 모두 `semantic_completion_pass = true`
- [x] `phase-c-and-d-smoke_20260405_183024/docs/generator_checklist.md`의 self-artifact 누락 문구는 현재 재실행 2회에서 재현되지 않았다.
  - 근거: `tools/rerun_past_failure_outputs.py` 실행 결과 2회 모두 `generator_missing_self_artifact = false`
- [x] `phase-c-and-d-smoke-rerun_20260405_183237/docs/final_readiness_checklist.md`의 `completion gate`는 현재 재실행 2회에서 닫혔다.
  - 근거: `tools/rerun_past_failure_outputs.py` 최신 실행 결과 2회 모두 `response_completion_gate_ok = true`, `completion_gate_checked = true`
- [x] `phase-c-and-d-smoke-rerun_20260405_183237/docs/final_readiness_checklist.md`의 `semantic gate`는 현재 재실행 2회에서 닫혔다.
  - 근거: `tools/rerun_past_failure_outputs.py` 실행 결과 2회 모두 `semantic_gate_checked = true`

## 4. ui-hard-gate-smoke 실패 체크리스트 정리
- [x] `ui-hard-gate-smoke*` 산출물 실제 위치를 확인하고 관련 실패 문서를 대조했다.
  - 근거: `uploads/projects/ui-hard-gate-smoke_20260405_172507`
- [x] `ui-hard-gate-smoke_20260405_172507/docs/final_readiness_checklist.md`의 `completion gate`는 현재 재실행 2회에서 닫혔다.
  - 근거: `tools/rerun_past_failure_outputs.py` 최신 실행 결과 2회 모두 `response_completion_gate_ok = true`, `completion_gate_checked = true`
- [x] `ui-hard-gate-smoke_20260405_172507/docs/final_readiness_checklist.md`의 hard gate 하위 항목(`integration test engine`, `shipping zip validation`, `product readiness hard gate`, `dependency install`, `standalone boot`, `core api smoke`, `zip reproduction`)은 현재 재실행 2회에서 모두 닫혔다.
  - 근거: `tools/rerun_past_failure_outputs.py` 최신 실행 결과 2회 모두 `response_completion_gate_ok = true`이며 실산출물 체크리스트가 `[x]`로 갱신됨
- [x] `ui-hard-gate-smoke_20260405_172507/docs/orchestrator_checklist.md`의 `semantic_audit_ok`는 현재 재실행 2회에서 `True`로 갱신됐다.
  - 근거: `tools/rerun_past_failure_outputs.py` 실행 결과 2회 모두 `semantic_audit_ok_true = true`, `semantic_audit_score_100 = true`
- [x] `ui-hard-gate-smoke_20260405_172507/docs/semantic_completion_audit.md`는 현재 재실행 2회에서 `score: 100`, `status: pass`로 갱신됐다.
  - 근거: `tools/rerun_past_failure_outputs.py` 실행 결과 2회 모두 `semantic_completion_pass = true`
- [x] `ui-hard-gate-smoke_20260405_172507/docs/generator_checklist.md`의 self-artifact 누락 문구는 현재 재실행 2회에서 재현되지 않았다.
  - 근거: `tools/rerun_past_failure_outputs.py` 실행 결과 2회 모두 `generator_missing_self_artifact = false`

## 5. 공통 패턴 요약
- [x] 공통 실패 패턴은 `phaseb-direct-run-*`, `phase-c-and-d-smoke*`, `ui-hard-gate-smoke*` 실산출물 문서 대조로 재확인했다.
  - 근거: `uploads/projects/phaseb-direct-run-01_20260405_225523`, `uploads/projects/phaseb-direct-run-02_20260405_232213`, `uploads/projects/phase-c-and-d-smoke_20260405_183024`, `uploads/projects/phase-c-and-d-smoke-rerun_20260405_183237`, `uploads/projects/ui-hard-gate-smoke_20260405_172507`
- [x] 공통 실패 1: `completion gate` 미완료는 현재 Python FastAPI 생성 경로 보강 후 동일 검증 2회에서 재현되지 않았다.
  - 대상: 과거 산출물 `phaseb-direct-run-01`, `phaseb-direct-run-02`, `phase-c-and-d-smoke`, `phase-c-and-d-smoke-rerun`
  - 정리: 과거 산출물에는 남아 있었지만, 현재 생성기 기준 재검증에서는 `completion_gate_ok = true`로 해소됐다.
- [x] 공통 실패 2: `semantic gate` 미완료는 현재 Python FastAPI 생성 경로 보강 후 동일 검증 2회에서 재현되지 않았다.
  - 대상: 과거 산출물 `phaseb-direct-run-01`, `phaseb-direct-run-02`, `phase-c-and-d-smoke`, `phase-c-and-d-smoke-rerun`
  - 정리: 과거 산출물에는 남아 있었지만, 현재 생성기 기준 재검증에서는 `semantic_gate_ok = true`, `semantic_gate_score = 100`으로 해소됐다.
- [x] 공통 실패 3: `semantic_audit_ok: False` 및 `Semantic Completion Audit score: 0`은 현재 Python FastAPI 생성 경로 보강 후 동일 검증 2회에서 재현되지 않았다.
  - 대상: 과거 산출물 `phaseb-direct-run-01`, `phaseb-direct-run-02`, `phase-c-and-d-smoke`, `ui-hard-gate-smoke`
  - 정리: 과거 산출물에는 남아 있었지만, 현재 생성기 기준 재검증에서는 semantic gate가 2회 모두 통과해 더 이상 재현되지 않았다.
- [x] 공통 실패 4: `generator_checklist.md` 내부 self-artifact 누락 판정은 과거 산출물에는 남아 있었지만, 현재 로직 기준 재검증으로는 해소됐다.
  - 대상: 과거 산출물 `phaseb-direct-run-01`, `phaseb-direct-run-02`, `phase-c-and-d-smoke`, `ui-hard-gate-smoke`
  - 정리: 과거 산출물 문서에는 `missing required artifact: docs/generator_checklist.md`가 남아 있었으나, 현재 생성기 로직 재검증에서는 same-path self-reference 누락 문구가 재현되지 않았다.
- [x] `generator_checklist.md` self-reference 판정 로직의 원인을 확인했고, 최종 artifact 집합 기준으로 다시 기록하도록 수정했다.
  - 근거: `backend/generators/facade.py`
- [x] 수정 후 새 산출물에서 `missing required artifact: docs/generator_checklist.md` self-reference 문구가 사라졌고 2회 재검증을 통과했다.
  - 근거: `tools/verify_generator_checklist_self_reference.py` 실행 결과 2회 모두 `has_missing_self_artifact = false`, `generation_ok_true = true`
- [x] 공통 실패 1~3은 한때 현재 생성기 기준에서도 재현됐지만, Python FastAPI 생성 경로의 AI/ops/security/runtime completeness 보강 후 동일 검증 2회에서 모두 해소됐다.
  - 근거: `tools/verify_current_common_failures.py` 최신 실행 결과 2회 모두 `completion_gate_ok = true`, `semantic_gate_ok = true`, `semantic_gate_score = 100`
- [x] 과거 실패 산출물 재실행 2회 결과, `phase-c-and-d-smoke*`와 `ui-hard-gate-smoke`의 semantic/audit/self-artifact 실패 항목은 실제 문서 기준으로 닫혔다.
  - 근거: `tools/rerun_past_failure_outputs.py` 실행 결과 2회 모두 `phase-c-and-d-smoke*`는 `semantic_gate_checked = true`, `semantic_audit_ok_true = true`, `semantic_completion_pass = true`, `generator_missing_self_artifact = false`, `ui-hard-gate-smoke`는 `semantic_audit_ok_true = true`, `semantic_completion_pass = true`, `generator_missing_self_artifact = false`
- [x] 남은 공통 잔존 항목이었던 `phase-c-and-d-smoke*`, `ui-hard-gate-smoke`의 `completion gate` 및 hard gate 하위 항목은 현재 재실행 2회에서 모두 닫혔다.
  - 근거: `tools/rerun_past_failure_outputs.py` 최신 실행 결과 2회 모두 해당 산출물들의 `response_completion_gate_ok = true`, `completion_gate_checked = true`

## 현재 판정
- 상태: **완료됨**
- 이유: `phaseb-direct-run-*`, `phase-c-and-d-smoke*`, `ui-hard-gate-smoke`의 잔존 문서 항목을 생성기 본체 수정 후 실제 재실행 2회로 다시 검증했고, 각 산출물 문서에서 `completion gate`, hard gate 하위 항목, `semantic gate`, `semantic_audit_ok`, `semantic completion audit`가 모두 갱신된 것을 확인했다. 추가로 운영 서버/nginx/포트 경로를 복구한 뒤 `metanova1004.com` 실도메인 핵심 경로와 `/api/llm/ws`, 고객 로그인/API marketplace 목록, 관리자 패스키 등록·로그인 브라우저 흐름을 모두 2회 실검증해 통과했으므로, 헌법 규칙 기준 최종 판정을 `완료됨`으로 유지한다.
