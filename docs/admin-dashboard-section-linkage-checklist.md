# Admin Dashboard 섹션 연동 체감 복구 체크리스트

## 검증 기준
- 실제 코드/화면 동작으로 확인된 항목만 `[x]` 처리
- 단순 스타일 수정이 아니라 버튼/섹션/스크롤 연결이 체감 가능한 경우만 통과 처리
- 최소 2회 이상 화면 검증 또는 빌드 검증 근거가 있어야 닫는다

## 1. 상단 액션과 섹션 연결
- [x] `전역 설정 열기` 버튼이 실제 전역 설정 섹션을 펼치고 이동하도록 연결했다.
  - 근거: `frontend/frontend/app/admin/page.tsx`의 `setSystemSettingsPanelOpen(true)` + `scrollIntoView()` + `data-testid="admin-system-settings-section"`
- [x] `LLM 제어 이동` 버튼이 별도 라우트 이동 대신 현재 화면의 LLM 통합 제어 섹션을 열고 이동하도록 바꿨다.
  - 근거: `frontend/frontend/app/admin/page.tsx`의 `setLlmControlPanelOpen(true)` + `scrollIntoView()` + `data-testid="admin-llm-control-section"`
- [x] `연결 그래프 보기` 버튼이 실제 auto-connect 그래프 섹션을 열고 이동하도록 연결했다.
  - 근거: `frontend/frontend/app/admin/page.tsx`의 `setAutoConnectGraphPanelOpen(true)` + `scrollIntoView()` + `data-testid="admin-autoconnect-section"`

## 2. 히어로 기능 pill 연결
- [x] 히어로 기능 pill 을 단순 텍스트가 아니라 실제 클릭 가능한 액션으로 확장했다.
  - 근거: `frontend/frontend/components/ui/workspace-chrome.tsx`의 `WorkspaceFeaturePill` 추가 및 `href`/`onClick` 지원
- [x] 관리자 대시보드 히어로 기능 pill 이 각 운영 섹션 열기 액션에 연결됐다.
  - 근거: `frontend/frontend/app/admin/page.tsx`의 `heroFeaturePills` 객체형 구성

## 3. 기본 펼침 상태 복구
- [ ] 전역 설정, 광고 주문, 카테고리, 비용, 빠른 이동, LLM, 샘플 섹션을 기본 펼침 상태로 전환했다.
  - 차단 근거: 사용자 요구가 인라인 펼침이 아니라 버튼형 창 열기였으므로 이 접근은 요구에 맞지 않아 보류로 낮춤
- [ ] 상위 프로젝트 패널도 기본 펼침 상태로 전환했다.
  - 차단 근거: 사용자가 요구한 방향은 기본 펼침이 아니라 각 섹션별 버튼형 창 열기

## 4. 버튼형 창 열기 UI 전환
- [x] 각 관리 섹션을 인라인 접기 카드가 아니라 버튼형 창 열기 패널로 전환했다.
  - 근거: `frontend/frontend/components/admin/admin-management-section.tsx`의 `workspace-open-window-button`, `workspace-window-backdrop`, `role="dialog"`
- [x] 전역 설정 패널과 보드 섹션마다 창 크기 타입을 지정할 수 있도록 확장했다.
  - 근거: `frontend/frontend/components/admin/admin-management-section.tsx`의 `windowSize`, `frontend/frontend/app/admin/page.tsx`의 `windowSize="full"` 및 각 `boardSections[].windowSize`
- [x] 각 섹션 카드가 제목만 있는 빈 카드가 아니라 `창 열기` 액션을 가진 런처 카드로 보이도록 바꿨다.
  - 근거: `frontend/frontend/components/admin/admin-management-section.tsx`의 `workspace-section-launcher`, `workspace-open-window-button`
- [x] 오버레이 창형 UI 스타일을 추가해 실제 팝업처럼 보이도록 보강했다.
  - 근거: `frontend/frontend/styles/globals.css`의 `workspace-window-*`, `workspace-window-backdrop`, `workspace-window-body`

## 5. 깔끔한 중앙 버튼 허브 재배치
- [x] 기존 보드 섹션 카드 나열 대신 중앙에 섹션 실행용 버튼 허브를 추가했다.
  - 근거: `frontend/frontend/app/admin/page.tsx`의 `workspace-launcher-hub`, `launcherLeftColumn`, `launcherRightColumn`, `orchestratorLauncher`
- [x] 각 섹션을 Genspark 스타일에 가까운 타일형 버튼으로 배치했다.
  - 근거: `frontend/frontend/app/admin/page.tsx`의 `workspace-launcher-tile-*`, `창 열기` 액션 타일 구성
- [x] 실제 섹션 모달은 유지하되 별도 런처 카드는 숨겨 중앙 허브만 보이게 정리했다.
  - 근거: `frontend/frontend/components/admin/admin-management-section.tsx`의 `launcherHidden`, `frontend/frontend/app/admin/page.tsx`의 `launcherHidden` 전달
- [x] 관리자 수동 오케스트레이션 타일을 중앙에 배치하고 양옆 버튼 크기를 맞춰 재정렬했다.
  - 근거: `frontend/frontend/app/admin/page.tsx`의 `orchestratorLauncher`, `launcherLeftColumn`, `launcherRightColumn`, `frontend/frontend/styles/globals.css`의 `workspace-launcher-stage`, `workspace-launcher-column`, `workspace-launcher-tile-center`
- [x] 버튼 이름을 실제 열리는 기능 섹션 제목과 일치하도록 정리했다.
  - 근거: `frontend/frontend/app/admin/page.tsx`의 `launcherLeftColumn`, `launcherRightColumn`, `orchestratorLauncher`, `heroActions`, `adminSidebar`
- [x] 중앙 런처에서 인라인 섹션과 모달 섹션을 빠짐없이 모두 연결하도록 확장했다.
  - 근거: `frontend/frontend/app/admin/page.tsx`의 `openAdminSurface`, `admin-launcher-*`, `admin-control-hub`, `admin-health-overview-section`, `admin-live-logs-section`, `admin-top-projects-section`

## 6. 검증
- [x] 프런트 빌드가 현재 수정 상태에서 성공했다.
  - 근거: `cmd /c npm run build` 2회 성공
- [x] 관리자 UI/UX 브라우저 설계도 문서를 별도 생성했다.
  - 근거: `docs/admin-dashboard-ui-ux-browser-blueprint.md`
- [x] 버튼/박스/기능 로직별 식별 ID 나열표를 별도 생성했다.
  - 근거: `docs/admin-dashboard-ui-identification-id-table.md`
- [x] 겹침을 줄이도록 레이어형 반응형 브라우저 UX 브레이크포인트를 적용했다.
  - 근거: `frontend/frontend/styles/globals.css`의 `@media (max-width: 1600px)`, `@media (max-width: 1450px)`, `@media (max-width: 900px)` 및 sidebar/hub/launcher/window 폭 조정
- [ ] 관리자 실제 화면에서 모든 섹션이 체감 가능하게 열리고 연결되는지 2회 실화면 확인
  - 차단 근거: 현재 대화 세션에서는 이미지 1회만 제공되었고, 수정 후 실화면 캡처 2회 확인은 아직 미수행

## 현재 판정
- 상태: **구현됨**
- 이유: 사용자 요구에 맞춰 관리자 대시보드 섹션을 인라인 접기형이 아니라 각 섹션별 `창 열기` 버튼으로 여는 오버레이 창형 패널로 전환했다. 다만 수정 후 실화면 체감 검증 2회는 아직 남아 있어 완료로 닫지는 않는다.
