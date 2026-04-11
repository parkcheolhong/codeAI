# Admin Dashboard UI/UX 브라우저 설계도 및 식별 ID 분석표

## 문서 목적
- 현재 `/admin` 화면을 **브라우저 UI/UX 설계도 관점**으로 해부한다.
- 화면의 **버튼, 박스(영역), 기능 로직 연결**, 그리고 **식별 ID / test id / 논리 ID**를 별도 표로 고정한다.
- 실제 코드에서 확인한 항목만 기록한다.

## 분석 기준
- 실제 렌더 코드로 확인된 구조만 적는다.
- 추정 UI는 `제안`으로만 적고 현재 구현으로 단정하지 않는다.
- 식별자는 다음 3종으로 구분한다.
  - **DOM test id**: `data-testid`
  - **논리 ID**: 배열/상태/섹션 식별용 `id`
  - **내부 토글 ID**: `toggleTestId` 또는 파생 문자열

---

## 1. 브라우저 UI/UX 상위 설계도

### 1-1. 개편 예정 `/admin` 브라우저 레이아웃 (다크 테마 기반 중앙 집중형)
```text
┌─ 양측 세로 아이콘 레일(workspace-rails)
│  ├─ [좌측 레일] 홈, 마켓, 드라이브 등 미니멀 아이콘
│  └─ [우측 레일] 전역 설정, LLM 제어, 카테고리 관리 원형 기능 미니멀 아이콘
│
└─ 중앙 스테이지(workspace-stage, Dark Theme)
   └─ 오케스트레이터 중심(workspace-orchestrator-hub)
      ├─ 좌측 레일 프롬프트(명령 입력창) 정중앙 배치 미니멀 버튼으로 이동
      └─ 우측 레일 정보/런처 박스는 양측 미니멀 이동 미니멀 아이콘으로 분리
```

### 1-2. 개편 후 중앙 작업 흐름
1. 전체 디자인은 **어두운 톤(Dark Theme)** 을 기반으로 구성하여 중앙 프롬프트에 시선을 집중한다.
2. 화면 중앙은 복잡한 정보 나열 없이 오직 큰 **'관리자 수동 오케스트레이션(프롬프트)'** 영역만 단독으로 차지한다.
3. 관리자 제어 등 주요 기능들은 양쪽 화면 끝에 고정으로 붙은 **미니멀 원형 아이콘(좌/우 고정 레일)** 을 클릭하여 개별 브라우저 팝업(모달형 오버레이) 창으로 연다. (가운데 버튼 런처 없음)
4. 기존 우측 사이드바와 3열 런처 구조는 삭제하거나 토글(숨김) 형태로 통합하여 메인 공간을 최대로 확보한다.

---

## 2. 박스(영역) 설계도

| 영역명 | UI 클래스/컴포넌트 | 역할 | 코드 근거 |
|---|---|---|---|
| 좌측 세로 레일 | `WorkspaceChrome` / `workspace-rail` | 전역 1차 탐색 | `frontend/frontend/components/ui/workspace-chrome.tsx:71-95` |
| 상단 바 | `workspace-topbar` | 새로고침, 마켓, 문서, API 문서, 로그아웃 | `frontend/frontend/app/admin/page.tsx:2113-2140` |
| 히어로 패널 | `workspace-hero-panel` | 현재 페이지 컨셉, 핵심 진입 버튼 | `frontend/frontend/app/admin/page.tsx:2141-2153`, `workspace-chrome.tsx:107-145` |
| Admin Control Hub | `workspace-admin-hub` | 전역 설정 핵심 정보, 운영자 명령 허브, 요약 지표 | `frontend/frontend/app/admin/page.tsx:2155-2197` |
| 버튼 런처 허브 | `workspace-launcher-hub` | 섹션별 버튼형 창 열기 메인 허브 | `frontend/frontend/app/admin/page.tsx:2199-2249` |
| 좌측 런처 열 | `launcherLeftColumn` | Admin Control Hub / 전역 설정 / auto-connect / 건강상태 / 광고 주문 / 카테고리 | `frontend/frontend/app/admin/page.tsx` 런처 컬럼 구간 |
| 중앙 오케스트레이터 | `orchestratorLauncher` | 관리자 수동 오케스트레이션 전용 중심축 | `frontend/frontend/app/admin/page.tsx` 중앙 런처 구간 |
| 우측 런처 열 | `launcherRightColumn` | LLM / 라이브 로그 / 상위 프로젝트 / 샘플 / 비용 / 빠른 이동 | `frontend/frontend/app/admin/page.tsx` 런처 컬럼 구간 |
| 건강상태 개요 블록 | `AdminDashboardOverview` | 건강상태, 자동 복구, self-run, focused self-healing | `frontend/frontend/components/admin/admin-dashboard-overview.tsx:147-340` |
| 운영 로그 박스 | `workspace-card` | 최근 30건 이벤트 표시 | `frontend/frontend/app/admin/page.tsx:2288-2305` |
| 상위 프로젝트 박스 | `workspace-card` | 다운로드 기준 순위 표시 | `frontend/frontend/app/admin/page.tsx` 하단 Top projects 섹션 |
| 우측 운영자 세션 박스 | `workspace-sidebar-card` | 관리자 계정 정보와 뱃지 | `frontend/frontend/app/admin/page.tsx:1923-1935` |
| 우측 핵심 지표 박스 | `workspace-sidebar-card` | 자동 건강상태 / self-run / 자동 복구 / LLM 런타임 | `frontend/frontend/app/admin/page.tsx:1936-1949` |
| 우측 바로 이동 박스 | `workspace-sidebar-card` | 텍스트형 안내형 바로 이동 요약 | `frontend/frontend/app/admin/page.tsx:1950-1958` |

---

## 3. 버튼 설계도

### 3-1. 좌측 세로 레일 버튼
| 논리 ID | 표시명 | 유형 | 연결 대상 |
|---|---|---|---|
| `home` | 대시 | 링크 | `/admin` |
| `market` | 마켓 | 링크 | `/marketplace` |
| `llm` | LLM | 링크 | `/admin/llm` |
| `docs` | 문서 | 링크 | docs viewer |

근거: `frontend/frontend/app/admin/page.tsx:2107-2111`, `workspace-chrome.tsx:73-92`

### 3-2. 상단 바 버튼
| DOM test id | 표시명 | 동작 | 코드 근거 |
|---|---|---|---|
| `admin-topnav-refresh` | 새로고침 | `loadDashboard(true)` | `page.tsx:2115-2124` |
| `admin-topnav-marketplace` | 마켓플레이스 | 링크 이동 | `page.tsx:2125-2127` |
| `admin-topnav-pass-kmc-kcb` | PASS/KMC/KCB 계약 문서 | 링크 이동 | `page.tsx:2128-2130` |
| `admin-topnav-commercial-terms` | 상용화 계약·약관 기준 | 링크 이동 | `page.tsx:2131-2133` |
| `admin-topnav-api-docs` | API 문서 | 외부 링크 | `page.tsx:2134-2136` |
| `admin-topnav-logout` | 로그아웃 | 로그아웃 처리 | `page.tsx:2137-2139` |

### 3-3. 히어로 핵심 액션 버튼
| 표시명 | 동작 | 실연결 섹션 |
|---|---|---|
| `🧭 전역 .env 설정 패널` | `setSystemSettingsPanelOpen(true)` | 전역 설정 모달 |
| `관리자 수동 오케스트레이션` | `setCustomerOrchestratorPanelOpen(true)` | 관리자 수동 오케스트레이션 모달 |
| `🕸️ self auto-connect graph` | `setAutoConnectGraphPanelOpen(true)` | auto-connect graph 모달 |

근거: `frontend/frontend/app/admin/page.tsx:2146-2151`

### 3-4. 중앙 런처 허브 버튼
#### 좌측 열
| 논리 ID | 표시명 | 기능 섹션 |
|---|---|---|
| `admin-control-hub` | `🧩 ADMIN CONTROL HUB` | Admin Control Hub 인라인 섹션으로 스크롤 |
| `system-settings` | `🧭 전역 .env 설정 패널` | 전역 설정 모달 |
| `auto-connect` | `🕸️ self auto-connect graph` | auto-connect graph 모달 |
| `health-overview` | `🩺 관리자 자동 건강상태 / 자가진단 / 자가개선` | 건강상태 인라인 섹션으로 스크롤 |
| `ad-orders` | `🎬 광고 영상 주문 모니터링` | 광고 주문 모달 |
| `category` | `🗂️ 마켓플레이스 카테고리 관리` | 카테고리 관리 모달 |

#### 중앙
| 논리 ID | 표시명 | 기능 섹션 |
|---|---|---|
| `manual-orchestrator` | `관리자 수동 오케스트레이션` | 관리자 수동 오케스트레이션 모달 |

#### 우측 열
| 논리 ID | 표시명 | 기능 섹션 |
|---|---|---|
| `llm-control` | `🤖 LLM 통합 제어 패널` | LLM 통합 제어 모달 |
| `live-logs` | `📡 운영 라이브 로그` | 라이브 로그 인라인 섹션으로 스크롤 + 펼치기 |
| `top-projects` | `🏆 상위 프로젝트` | 상위 프로젝트 인라인 섹션으로 스크롤 + 펼치기 |
| `sample` | `🎯 원터치 샘플 생성` | 샘플 생성 모달 |
| `cost` | `💸 비용 시뮬레이터` | 비용 시뮬레이터 모달 |
| `quick-links` | `⚡ 빠른 이동` | 빠른 이동 모달 |

근거: `frontend/frontend/app/admin/page.tsx:1854-1928, 2207-2247`

### 3-5. Admin Control Hub 내부 버튼
| 표시명 | 동작 |
|---|---|
| `전역 설정 열기` | `setSystemSettingsPanelOpen(true)` |
| `설정 새로고침` | `loadSystemSettings` |
| `전역 자동 전환` | `applyGlobalAutomaticMode` |

근거: `frontend/frontend/app/admin/page.tsx:2176-2180`

### 3-6. 자동 건강상태/자가개선 버튼
| 표시명 | 동작 | 코드 근거 |
|---|---|---|
| `자동 복구 즉시 실행` | `onExecuteAutomaticRecovery` | `admin-dashboard-overview.tsx:230-233` |
| `focused self-healing 실행` | `onOpenFocusedSelfHealing` | `admin-dashboard-overview.tsx:234-236` |
| `재진단 새로고침` | `onReloadDashboard` | `admin-dashboard-overview.tsx:237-239` |
| `self-run 상세 제어 열기` | `/admin/llm` 링크 | `admin-dashboard-overview.tsx:187-189` |
| `승인 후 원본 반영` | `onApproveWorkspaceSelfRun` | `admin-dashboard-overview.tsx:190-199` |

---

## 4. 기능 로직 연결도

### 4-1. 모달 열기 로직
현재 모달형 섹션 열기 구조는 다음 흐름이다.

```text
버튼 클릭
→ page.tsx 의 setXxxPanelOpen(true)
→ AdminManagementSection(open=true)
→ workspace-window-backdrop 렌더
→ workspace-window dialog 표시
→ 내부 children 로 실제 기능 섹션 렌더
```

근거:
- 상태 선언: `frontend/frontend/app/admin/page.tsx:282-327`
- 모달 렌더: `frontend/frontend/components/admin/admin-management-section.tsx:50-102`

### 4-2. 인라인 섹션 열기 로직
현재 인라인 섹션 연결 구조는 다음 흐름이다.

```text
런처 버튼 클릭
→ openAdminSurface(selector, beforeOpen?) 호출
→ beforeOpen 이 있으면 먼저 펼침 상태 true
→ requestAnimationFrame + setTimeout
→ selector 대상 섹션 scrollIntoView
```

적용 대상:
- `🧩 ADMIN CONTROL HUB`
- `🩺 관리자 자동 건강상태 / 자가진단 / 자가개선`
- `📡 운영 라이브 로그`
- `🏆 상위 프로젝트`

근거: `frontend/frontend/app/admin/page.tsx`의 `openAdminSurface` 및 각 launcher item `onClick`

### 4-3. 모달 닫기 로직
```text
닫기 버튼 클릭
또는 backdrop 클릭
또는 ESC 입력
→ onToggle 호출
→ setXxxPanelOpen(false)
→ dialog 제거
```

근거: `frontend/frontend/components/admin/admin-management-section.tsx:29-47, 77-101`

### 4-4. 현재 기능 섹션별 연결 맵
| 상태 변수 | 연결 섹션 제목 | 렌더 바디 |
|---|---|---|
| `systemSettingsPanelOpen` | `🧭 전역 .env 설정 패널` | `AdminSystemSettingsPanel` |
| `autoConnectGraphPanelOpen` | `🕸️ self auto-connect graph` | `AdminAutoConnectGraphPanel` |
| `customerOrchestratorPanelOpen` | `관리자 수동 오케스트레이션` | `AdminManualOrchestratorSection` |
| `adOrdersPanelOpen` | `🎬 광고 영상 주문 모니터링` | `AdminAdOrdersSection` |
| `categoryPanelOpen` | `🗂️ 마켓플레이스 카테고리 관리` | `AdminCategoryManagementSection` |
| `llmControlPanelOpen` | `🤖 LLM 통합 제어 패널` | `AdminLlmControlSummary` |
| `samplePanelOpen` | `🎯 원터치 샘플 생성` | `AdminSampleProductsSection` |
| `costSimulatorPanelOpen` | `💸 비용 시뮬레이터` | `AdminCostSimulatorSection` |
| `quickLinksPanelOpen` | `⚡ 빠른 이동` | `AdminQuickLinksSection` |

근거: `frontend/frontend/app/admin/page.tsx:1961-2094, 2251-2284`

### 4-5. 현재 인라인 섹션별 연결 맵
| 연결 방식 | 섹션명 | 대상 식별자 |
|---|---|---|
| 스크롤 | `🧩 ADMIN CONTROL HUB` | `data-testid="admin-control-hub"` |
| 스크롤 | `🩺 관리자 자동 건강상태 / 자가진단 / 자가개선` | `data-testid="admin-health-overview-section"` |
| 펼친 뒤 스크롤 | `📡 운영 라이브 로그` | `data-testid="admin-live-logs-section"` |
| 펼친 뒤 스크롤 | `🏆 상위 프로젝트` | `data-testid="admin-top-projects-section"` |

근거: `frontend/frontend/app/admin/page.tsx` 런처 onClick 및 인라인 섹션 test id 구간

---

## 5. 식별 ID 나열표

## 5-1. 페이지 / 레일 / 상단 전역 식별자
| 분류 | 식별자 | 종류 | 위치 |
|---|---|---|---|
| 페이지 | `admin-workspace-page` | DOM test id | `page.tsx:2105` |
| 레일 | `home` | 논리 ID | `page.tsx:2107-2111` |
| 레일 | `market` | 논리 ID | `page.tsx:2107-2111` |
| 레일 | `llm` | 논리 ID | `page.tsx:2107-2111` |
| 레일 | `docs` | 논리 ID | `page.tsx:2107-2111` |
| 상단 액션 | `admin-topnav-refresh` | DOM test id | `page.tsx:2118` |
| 상단 액션 | `admin-topnav-marketplace` | DOM test id | `page.tsx:2125` |
| 상단 액션 | `admin-topnav-pass-kmc-kcb` | DOM test id | `page.tsx:2128` |
| 상단 액션 | `admin-topnav-commercial-terms` | DOM test id | `page.tsx:2131` |
| 상단 액션 | `admin-topnav-api-docs` | DOM test id | `page.tsx:2134` |
| 상단 액션 | `admin-topnav-logout` | DOM test id | `page.tsx:2137` |

## 5-2. 중앙 런처 허브 논리 ID
| 식별자 | 표시명 | 코드 위치 |
|---|---|---|
| `system-settings` | `🧭 전역 .env 설정 패널` | `page.tsx:1854-1861` |
| `auto-connect` | `🕸️ self auto-connect graph` | `page.tsx:1862-1868` |
| `ad-orders` | `🎬 광고 영상 주문 모니터링` | `page.tsx:1869-1875` |
| `category` | `🗂️ 마켓플레이스 카테고리 관리` | `page.tsx:1900-1906` |
| `manual-orchestrator` | `관리자 수동 오케스트레이션` | `page.tsx:1922-1928` |
| `llm-control` | `🤖 LLM 통합 제어 패널` | `page.tsx:1908-1914` |
| `sample` | `🎯 원터치 샘플 생성` | `page.tsx:1915-1921` |
| `cost` | `💸 비용 시뮬레이터` | `page.tsx:1915-1921` |
| `quick-links` | `⚡ 빠른 이동` | `page.tsx:1915-1921` |

> 주의: `sample`, `cost`, `quick-links` 는 같은 배열 블록 안에 선언되어 있으므로 세부 줄 위치는 인접 구간으로 확인해야 한다.

## 5-3. 섹션 모달/토글 관련 식별자
| 식별자 | 종류 | 설명 |
|---|---|---|
| `admin-system-settings-section` | 내부 토글 ID | 전역 설정 섹션 식별자 |
| `admin-storyboard-section-toggle` | 내부 토글 ID | 광고 주문/스토리보드 관련 토글 식별자 |
| `admin-${section.id}-section` | 내부 토글 ID 규칙 | 기본 섹션 토글 ID 파생 규칙 |
| `workspace-window-backdrop` | 클래스 식별 | 모든 섹션 모달 오버레이 |
| `workspace-window` | 클래스 식별 | 모든 섹션 모달 창 본체 |

근거:
- `frontend/frontend/app/admin/page.tsx:2251-2279`
- `frontend/frontend/components/admin/admin-management-section.tsx:78-100`

## 5-4. 관리자 대시보드 개요/경고/헬스 관련 test id
| test id | 위치 | 설명 |
|---|---|---|
| `admin-dashboard-capability-bootstrap-notice` | `admin-dashboard-overview.tsx:153` | capability bootstrap 안내 배너 |
| `admin-dashboard-error-banner` | `admin-dashboard-overview.tsx:158` | 일부 데이터 로드 실패 배너 |

## 5-5. Storyboard modal test id
| test id | 설명 | 위치 |
|---|---|---|
| `admin-storyboard-modal` | 모달 루트 | `admin-storyboard-modal.tsx:37` |
| `admin-storyboard-modal-title` | 제목 | `:41` |
| `admin-storyboard-modal-status` | 상태 텍스트 | `:42` |
| `admin-storyboard-modal-index` | 컷 index | `:44` |
| `admin-storyboard-modal-close` | 닫기 버튼 | `:52` |
| `admin-storyboard-modal-prev` | 이전 컷 버튼 | `:62` |
| `admin-storyboard-modal-next` | 다음 컷 버튼 | `:70` |
| `admin-storyboard-modal-image` | 이미지 | `:76` |
| `admin-storyboard-diff-panel` | diff 패널 | `:78` |
| `admin-storyboard-diff-status` | 상태 diff | `:82` |
| `admin-storyboard-diff-note-before` | 이전 메모 | `:86` |
| `admin-storyboard-diff-note-after` | 현재 메모 | `:90` |
| `admin-storyboard-diff-empty` | diff 없음 | `:96` |

## 5-6. 빠른 이동 섹션 test id
| test id | 설명 | 위치 |
|---|---|---|
| `admin-quicklink-admin` | `/admin` 링크 | `admin-quick-links-section.tsx:22` |
| `admin-quicklink-marketplace` | `/marketplace` 링크 | `:25` |
| `admin-quicklink-marketplace-orchestrator` | `/marketplace/orchestrator` 링크 | `:28` |
| `admin-quicklink-pass-kmc-kcb` | 계약 문서 | `:31` |
| `admin-quicklink-operations-package` | 운영 전환 패키지 | `:34` |
| `admin-quicklink-business-type-guide` | 사업자 유형 가이드 | `:37` |
| `admin-quicklink-commercial-terms` | 상용화 기준 | `:40` |
| `admin-quicklink-llm-status-api` | LLM 상태 API | `:43` |
| `admin-quicklink-api-docs` | Swagger UI | `:46` |

---

## 6. 현재 구조에서 드러난 UX 관찰 포인트

### 6-1. 장점
- 섹션 모달 구조는 이미 고정되어 있어 기능별 창 열기 UX 자체는 명확하다.
- 중앙 런처 허브와 실제 기능 모달 사이 연결은 현재 코드상 직접적이다.
- 상단 바 / 히어로 / 런처 / 우측 사이드바로 운영자 시야 분산 지점이 명확히 분리돼 있다.

### 6-2. 현재 남아있는 UI/UX 리스크
- `Admin Control Hub` 자체가 여전히 매우 크고 시선을 많이 잡아먹는다.
- 중앙 런처 허브와 우측 사이드바의 요약 정보가 일부 중복된다.
- `toggleTestId`는 숨김 런처 구조에서는 실제 DOM에 노출되지 않는 경우가 있어, 테스트 기준과 런처 기준이 완전히 일치하지 않을 수 있다.
- `WorkspaceChrome`는 `heroFeaturePills` 기능을 지원하지만 admin page 현재 렌더에서는 사용하지 않는다.

### 6-3. 새로운 뷰 UX 설계 포인트 제안 (이미지 기반)
1. **다크 테마 적용**: 배경 진한 회색/검은색, 컴포넌트의 테두리와 내부 요소도 매칭되는 다크 모드 톤으로 설계한다.
2. **양쪽 화면 고정 런처 바**: 버튼을 양쪽(좌/우) 화면 테두리에 붙여서 고정으로 배치한다. 기존의 복잡한 런처 허브 메뉴들을 좌/우 고정 런처 패널의 미니멀 원형 아이콘으로 마이그레이션한다.
3. **가운데 집중형 오케스트레이터 허브**: 화면 가운데에는 아무 런처 버튼 없이 커다란 프롬프트(명령 입력창) 공간만 두어 AI 워크스페이스 형태로 구성한다.
4. **모달 기반 기능 확장**: 가운데가 아닌 양쪽 네비게이션에 위치한 다른 기능 아이콘들을 클릭하면, 그때 브라우저 창(dialog 오버레이) 형태가 열려 해당 기능을 조작하는 방식이다.
5. **선택과 집중**: 기존 우측 사이드바(지표 등)는 숨기거나 토글형 아이콘 안에 넣어버려 한눈에 하나의 행위(오케스트레이터 명령)만이 요구되도록 정리다.

## 6-4. 반응형 레이어 UX 규칙 (개편 적용)
- 데스크톱/와이드 화면: 좌/우 고정 레일 + 거대한 중앙 프롬프트 기반의 기본 구조 유지.
- 해상도 축소 시: 양옆의 아이콘 레일은 유지하되, 우측 레일 아이콘을 좌측 또는 하단 네비게이션으로 모바일화 적용 검토.
- 모바일 폭에서는 모달 폭을 `100vw - 24px` 기준으로 제한해 화면 밖으로 넘치지 않게 한다.

근거: `frontend/frontend/styles/globals.css`의 `@media (max-width: 1600px)`, `@media (max-width: 1450px)`, `@media (max-width: 900px)`

---

## 7. 향후 UI/UX 개편 구조 요약 (다크 테마 + 양방향 레일 + 중앙 프롬프트)
앞부분에서 제안된 변경안에 따라, 향후 변경될 `admin`의 뼈대는 다음과 같다.

- **가운데 집중형 오케스트레이터**: 중앙 런처 박스와 복잡한 섹션들을 제거하고 화면 가운데는 수동 오케스트레이션(AI 명령) 프롬프트를 대형으로 고정 배치한다.
- **양측 세로 아이콘 레일**: 좌/우측 테두리에 미니멀한 원형 아이콘을 배치하여 주요 기능 런처(전역 설정, 모니터링 등)로 사용한다. (클릭 시 브라우저 오버레이로 열림)
- **우측 사이즈바 숨김/통합**: 기본적으로 보이지 않게 감추거나 아이콘 레일 내부 단축 메뉴로 통폐합하여 시야를 단순화한다.
- **다크 테마 고정**: 배경과 컴포넌트의 테두리/톤을 짙은 회색·검은색으로 일체화하여 프롬프트 UI/UX의 몰입감을 극대화한다.

---
[참고] 이 문서는 현재의 코드 분석에 제안(이미지 기반)된 신규 UX 뼈대를 반영하여 통합·개편 예정이다.
