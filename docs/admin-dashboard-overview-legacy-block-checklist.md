# Admin Dashboard Overview 레거시 건강상태 블록 정밀 점검 체크리스트

## 검증 기준
- 실제 파일과 연결 관계에서 확인된 항목만 `[x]` 처리
- 삭제/이관 전 안전성 판정이 끝난 항목만 체크
- 추정이나 운영 미실검증 내용은 `[ ]` 유지
- 현재 목표는 `admin-dashboard-overview.tsx`의 구버전 건강상태 블록을 정밀 식별하고, 손상 없이 다음 안전 조치 범위를 고정하는 것이다

## 1. 레거시 블록 범위 식별
- [x] 구버전 건강상태 블록의 시작/끝 범위를 특정했다.
  - 근거: `frontend/frontend/components/admin/admin-dashboard-overview.tsx:204-298`
- [x] 운영 검증에서 문제로 드러난 대표 문자열을 확인했다.
  - 근거: `🩺 관리자 자동 건강상태 / 자가진단 / 자가개선`
- [x] 해당 블록이 현재 `AdminDashboardOverview` 내부의 단일 섹션으로 존재함을 확인했다.
  - 근거: 동일 파일 `204-298` 구간이 하나의 `<section className="mb-8 rounded-xl border bg-white p-5">`로 구성됨

## 2. props 연결 관계 점검
- [x] 건강상태 카드가 자동 점수/상태 라벨 props를 사용함을 확인했다.
  - 근거: `automaticHealthScore`, `automaticHealthLabel` 사용 (`216-219`)
- [x] 자가진단 카드가 self-run 실패 인사이트 props를 사용함을 확인했다.
  - 근거: `selfRunFailureInsight?.title`, `selfRunFailureInsight?.reason` 사용 (`221-224`)
- [x] 자동 개선 카드가 자동 복구/재진단/focused self-healing 액션에 연결됨을 확인했다.
  - 근거: `onExecuteAutomaticRecovery`, `onOpenFocusedSelfHealing`, `onReloadDashboard`, `autoRecoveryRunning`, `focusedSelfHealingBusy`, `refreshing` 사용 (`226-239`)
- [x] 자동 운영 보조 카드가 `automaticOpsActions` 배열에 연결됨을 확인했다.
  - 근거: `243-260`
- [x] 자동 복구 이력 카드가 `autoRecoveryHistory` 배열에 연결됨을 확인했다.
  - 근거: `262-296`

## 3. 상위 조립/분석 로직 연결 점검
- [x] 건강상태 관련 계산값은 `buildAdminPageHealthAnalysis()`에서 계산됨을 확인했다.
  - 근거: `frontend/frontend/app/admin/admin-page-health-analysis.ts:149-196`, `218-243`
- [x] `AdminDashboardOverview`는 계산 로직이 아니라 표시 레이어임을 확인했다.
  - 근거: 분석값 계산은 `admin-page-health-analysis.ts`, 표시 props 정의는 `admin-dashboard-overview.tsx`
- [x] `/admin` 메인 페이지가 `buildAdminDashboardOverviewAssembly(...)`를 통해 `AdminDashboardOverview`에 데이터를 주입함을 확인했다.
  - 근거: `frontend/frontend/app/admin/page.tsx` 내 `adminDashboardOverviewAssembly` 생성 후 `<AdminDashboardOverview {...adminDashboardOverviewAssembly} />`

## 4. 안전성 판정
- [x] 이 블록은 레거시 UI 성격이 맞다고 판정했다.
  - 근거: 운영 실패 DOM 기준 문자열과 동일하며, `WorkspaceChrome` 보드 구조와 시각 언어가 다름
- [x] 이 블록은 즉시 삭제 안전 대상이 아니라고 판정했다.
  - 근거: 자동 건강상태, self-run 인사이트, 자동 개선 액션, 자동 복구 이력의 현재 주표시 레이어를 담당함
- [x] 무손상 원칙상 먼저 대체 표시 위치를 만든 뒤 제거해야 한다고 판정했다.
  - 근거: 현재 `204-298` 구간 제거 시 운영자가 보는 핵심 상태 UI 공백 발생 가능

## 5. 병행 안전 정리 반영
- [x] 마켓플레이스에서 명시적 legacy 섹션은 제거됐다.
  - 근거: `frontend/frontend/app/marketplace/page.tsx`의 `marketplace-generator-products-legacy` 섹션 삭제 완료
- [x] 마켓플레이스 hero pill 문구를 현재 구현 상태에 맞게 정리했다.
  - 근거: `기존 코드생성기 상품 진열` → `프로젝트 상세 / 데모 / GitHub 진입`
- [x] 프런트 build가 현재 수정 상태에서 성공함을 확인했다.
  - 근거: `npm --prefix frontend/frontend run build` 실행 성공

## 현재 판정
- 상태: **구현됨**
- 이유: `admin-dashboard-overview.tsx`의 구버전 건강상태 블록을 파일 범위, props 연결, 상위 분석/조립 로직까지 정밀하게 조사해 레거시 여부와 즉시 삭제 위험도를 문서로 고정했다. 또한 현재 안전하게 제거 가능한 마켓플레이스 legacy 잔재는 정리했고 build 성공까지 확인했다. 다만 관리자 건강상태 블록은 대체 UI 이관 없이 제거하면 손상 가능성이 있으므로 아직 닫힌 완료 상태는 아니다.
