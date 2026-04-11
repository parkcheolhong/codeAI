# Admin Dashboard 식별 ID 나열표

## 문서 목적
- `/admin` 브라우저 화면의 버튼, 섹션, 모달, 보조 패널에 연결된 **식별 ID 체계**를 별도 문서로 고정한다.
- 테스트, 리팩터링, UI/UX 재배치 시 기준표로 사용한다.

## 구분 규칙
- **DOM test id**: 실제 `data-testid`
- **논리 ID**: 배열/섹션/버튼 데이터에서 사용하는 `id`
- **파생 토글 ID**: `toggleTestId` 또는 `admin-${section.id}-section` 규칙
- **상태 변수**: 모달 open/close 제어 state

---

## 1. 페이지 및 전역 식별자
| 이름 | 값 | 종류 | 근거 |
|---|---|---|---|
| Admin page root | `admin-workspace-page` | DOM test id | `frontend/frontend/app/admin/page.tsx:2105` |
| Brand rail item | `home` | 논리 ID | `page.tsx:2108` |
| Brand rail item | `market` | 논리 ID | `page.tsx:2109` |
| Brand rail item | `llm` | 논리 ID | `page.tsx:2110` |
| Brand rail item | `docs` | 논리 ID | `page.tsx:2111` |

---

## 2. 상단 액션 식별자
| 표시명 | 식별자 | 종류 | 근거 |
|---|---|---|---|
| 새로고침 | `admin-topnav-refresh` | DOM test id | `page.tsx:2118` |
| 마켓플레이스 | `admin-topnav-marketplace` | DOM test id | `page.tsx:2125` |
| PASS/KMC/KCB 계약 문서 | `admin-topnav-pass-kmc-kcb` | DOM test id | `page.tsx:2128` |
| 상용화 계약·약관 기준 | `admin-topnav-commercial-terms` | DOM test id | `page.tsx:2131` |
| API 문서 | `admin-topnav-api-docs` | DOM test id | `page.tsx:2134` |
| 로그아웃 | `admin-topnav-logout` | DOM test id | `page.tsx:2137` |

---

## 3. 중앙 런처 허브 논리 ID

### 3-1. 좌측 컬럼
| 논리 ID | 표시명 | 연결 상태 변수 |
|---|---|---|
| `admin-control-hub` | `🧩 ADMIN CONTROL HUB` | 인라인 스크롤 연결 |
| `system-settings` | `🧭 전역 .env 설정 패널` | `systemSettingsPanelOpen` |
| `auto-connect` | `🕸️ self auto-connect graph` | `autoConnectGraphPanelOpen` |
| `health-overview` | `🩺 관리자 자동 건강상태 / 자가진단 / 자가개선` | 인라인 스크롤 연결 |
| `ad-orders` | `🎬 광고 영상 주문 모니터링` | `adOrdersPanelOpen` |
| `category` | `🗂️ 마켓플레이스 카테고리 관리` | `categoryPanelOpen` |

근거: `frontend/frontend/app/admin/page.tsx:1854-1906`

### 3-2. 중앙 오케스트레이터
| 논리 ID | 표시명 | 연결 상태 변수 |
|---|---|---|
| `manual-orchestrator` | `관리자 수동 오케스트레이션` | `customerOrchestratorPanelOpen` |

근거: `frontend/frontend/app/admin/page.tsx:1922-1928`

### 3-3. 우측 컬럼
| 논리 ID | 표시명 | 연결 상태 변수 |
|---|---|---|
| `llm-control` | `🤖 LLM 통합 제어 패널` | `llmControlPanelOpen` |
| `live-logs` | `📡 운영 라이브 로그` | `liveLogsPanelOpen` + 인라인 스크롤 |
| `top-projects` | `🏆 상위 프로젝트` | `topProjectsOpen` + 인라인 스크롤 |
| `sample` | `🎯 원터치 샘플 생성` | `samplePanelOpen` |
| `cost` | `💸 비용 시뮬레이터` | `costSimulatorPanelOpen` |
| `quick-links` | `⚡ 빠른 이동` | `quickLinksPanelOpen` |

근거: `frontend/frontend/app/admin/page.tsx:1908-1921`

---

## 4. 섹션 모달 제어 식별자
| 섹션명 | 상태 변수 | 토글 ID | 윈도우 크기 |
|---|---|---|---|
| `🧭 전역 .env 설정 패널` | `systemSettingsPanelOpen` | `admin-system-settings-section` | `full` |
| `🕸️ self auto-connect graph` | `autoConnectGraphPanelOpen` | `admin-autoconnect-section` 파생 | `wide` |
| `관리자 수동 오케스트레이션` | `customerOrchestratorPanelOpen` | `admin-manual-section` 파생 | `full` |
| `🎬 광고 영상 주문 모니터링` | `adOrdersPanelOpen` | `admin-storyboard-section-toggle` | `full` |
| `🗂️ 마켓플레이스 카테고리 관리` | `categoryPanelOpen` | `admin-category-section` 파생 | `wide` |
| `💸 비용 시뮬레이터` | `costSimulatorPanelOpen` | `admin-cost-section` 파생 | `wide` |
| `⚡ 빠른 이동` | `quickLinksPanelOpen` | `admin-quick-section` 파생 | `default` |
| `🤖 LLM 통합 제어 패널` | `llmControlPanelOpen` | `admin-llm-section` 파생 | `full` |
| `🎯 원터치 샘플 생성` | `samplePanelOpen` | `admin-sample-section` 파생 | `wide` |

근거:
- `frontend/frontend/app/admin/page.tsx:1961-2094, 2251-2280`
- `frontend/frontend/components/admin/admin-management-section.tsx:6-15, 77-100`

---

## 4-1. 인라인 섹션 식별자
| 섹션명 | 식별자 | 종류 |
|---|---|---|
| Admin Control Hub | `admin-control-hub` | DOM test id |
| 건강상태 overview | `admin-health-overview-section` | DOM test id |
| 운영 라이브 로그 | `admin-live-logs-section` | DOM test id |
| 상위 프로젝트 | `admin-top-projects-section` | DOM test id |

근거: `frontend/frontend/app/admin/page.tsx` 인라인 섹션 wrapper

---

## 4-2. 중앙 런처 버튼 test id 규칙
| 규칙 | 예시 |
|---|---|
| `admin-launcher-${id}` | `admin-launcher-system-settings`, `admin-launcher-manual-orchestrator`, `admin-launcher-live-logs` |

적용 대상:
- `admin-control-hub`
- `system-settings`
- `auto-connect`
- `health-overview`
- `ad-orders`
- `category`
- `manual-orchestrator`
- `llm-control`
- `live-logs`
- `top-projects`
- `sample`
- `cost`
- `quick-links`

---

## 5. 관리자 대시보드 Overview 관련 test id
| test id | 설명 | 근거 |
|---|---|---|
| `admin-dashboard-capability-bootstrap-notice` | capability bootstrap notice | `frontend/frontend/components/admin/admin-dashboard-overview.tsx:153` |
| `admin-dashboard-error-banner` | 일부 데이터 로드 실패 배너 | `frontend/frontend/components/admin/admin-dashboard-overview.tsx:158` |

---

## 6. Storyboard modal test id 목록
| test id | 설명 | 근거 |
|---|---|---|
| `admin-storyboard-modal` | 스토리보드 모달 루트 | `frontend/frontend/components/admin/admin-storyboard-modal.tsx:37` |
| `admin-storyboard-modal-title` | 제목 | `:41` |
| `admin-storyboard-modal-status` | 검수 상태 | `:42` |
| `admin-storyboard-modal-index` | 컷 index | `:44` |
| `admin-storyboard-modal-close` | 닫기 버튼 | `:52` |
| `admin-storyboard-modal-prev` | 이전 컷 버튼 | `:62` |
| `admin-storyboard-modal-next` | 다음 컷 버튼 | `:70` |
| `admin-storyboard-modal-image` | 이미지 표시 | `:76` |
| `admin-storyboard-diff-panel` | diff 패널 | `:78` |
| `admin-storyboard-diff-status` | 상태 diff | `:82` |
| `admin-storyboard-diff-note-before` | 이전 메모 | `:86` |
| `admin-storyboard-diff-note-after` | 현재 메모 | `:90` |
| `admin-storyboard-diff-empty` | diff 없음 상태 | `:96` |

---

## 7. 빠른 이동 섹션 test id 목록
| test id | 설명 | 근거 |
|---|---|---|
| `admin-quicklink-admin` | `/admin` 링크 | `frontend/frontend/components/admin/admin-quick-links-section.tsx:22` |
| `admin-quicklink-marketplace` | `/marketplace` 링크 | `:25` |
| `admin-quicklink-marketplace-orchestrator` | `/marketplace/orchestrator` 링크 | `:28` |
| `admin-quicklink-pass-kmc-kcb` | PASS/KMC/KCB 계약 문서 | `:31` |
| `admin-quicklink-operations-package` | 운영 전환 패키지 | `:34` |
| `admin-quicklink-business-type-guide` | 사업자 유형 가이드 | `:37` |
| `admin-quicklink-commercial-terms` | 상용화 기준 | `:40` |
| `admin-quicklink-llm-status-api` | LLM 상태 API | `:43` |
| `admin-quicklink-api-docs` | Swagger UI | `:46` |

---

## 8. 클래스 기반 주요 UI 식별자
| 클래스명 | 역할 |
|---|---|
| `workspace-shell` | admin 전체 shell |
| `workspace-rail` | 좌측 세로 레일 |
| `workspace-topbar` | 상단 바 |
| `workspace-hero-panel` | 히어로 영역 |
| `workspace-admin-hub` | Admin Control Hub |
| `workspace-launcher-hub` | 버튼 허브 전체 |
| `workspace-launcher-stage` | 좌/중/우 런처 배치 루트 |
| `workspace-launcher-column` | 좌측/우측 버튼 열 |
| `workspace-launcher-tile` | 개별 기능 버튼 |
| `workspace-launcher-tile-center` | 중앙 오케스트레이터 버튼 |
| `workspace-window-backdrop` | 모달 배경 오버레이 |
| `workspace-window` | 공통 모달 창 |
| `workspace-window-body` | 모달 내부 본문 |

근거: `frontend/frontend/styles/globals.css`, `workspace-chrome.tsx`, `admin-management-section.tsx`

---

## 9. 식별자 운용 관찰
- 중앙 런처 버튼은 현재 **`admin-launcher-${id}` 규칙의 `data-testid`를 가짐**.
- 기능 모달은 공통 `AdminManagementSection`으로 렌더되어 **모달별 DOM test id 분리 수준은 낮음**.
- top nav / storyboard / quick links는 test id 가 비교적 잘 깔려 있다.
- 인라인 섹션도 `admin-control-hub`, `admin-health-overview-section`, `admin-live-logs-section`, `admin-top-projects-section` 기준으로 직접 추적 가능하다.
