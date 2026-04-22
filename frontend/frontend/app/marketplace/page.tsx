"use client";

import * as React from 'react';
import Link from 'next/link';
import FeatureLauncherGrid from '@/components/marketplace/feature-launcher-grid';
import FeatureOrchestratorPopup from '@/components/marketplace/feature-orchestrator-popup';
import { useFeatureOrchestrator } from '@/hooks/use-feature-orchestrator';
import WorkspaceChrome from '@/components/ui/workspace-chrome';
import { resolveAdminSiteHref, resolveMarketplaceSiteHref, resolveStaffSiteHref } from '@/lib/canonical-site';

function resolveApiBaseUrl(): string {
    if (typeof window !== 'undefined') {
        const { hostname, origin, port, protocol } = window.location;
        const isLocalHost = hostname === 'localhost' || hostname === '127.0.0.1';
        const isDirectFrontendDevPort = port === '3000' || port === '3005';
        const isGatewayPort = port === '8080' || port === '8443';

        if (isDirectFrontendDevPort && protocol !== 'https:') {
            return process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000';
        }

        if (!isLocalHost || isGatewayPort || protocol === 'https:') {
            return origin;
        }
    }

    return process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000';
}

const generatorMarketplaceProducts = [
    {
        id: 'project-scanner-starter',
        title: '프로젝트 스캐너 스타터 팩',
        category: '구조 진단형',
        price: '189,000원',
        summary: '프로젝트 구조 검색, 문제 포인트, 상품화 가능한 엔진을 복구합니다.',
        highlights: ['구조화하여', '엔진 추출', '해적점 지도'],
    },
    {
        id: 'security-guard-bundle',
        title: '보안 강화 패키지',
        category: '보안 강화형',
        price: '249,000원',
        summary: '인증·권한·민감정보 바깥쪽을 상품으로 독점 보안 가드를 구성합니다.',
        highlights: ['인증', '권한 부여', '보안 가드'],
    },
    {
        id: 'self-healing-ops-suite',
        title: '자가 치유 운영 제품군',
        category: '운영 복구형',
        price: '279,000원',
        summary: '실패 루프 복구, 자체 실행 자동 구성, 운영 상황 흐름을 한 번에 제공합니다.',
        highlights: ['자동 복구', '자체 실행 묶음', '운영 메모장'],
    },
    {
        id: 'code-generator-deployment-kit',
        title: '코드 생성기 배포 키트',
        category: '실배포 구현형',
        price: '329,000원',
        summary: '실배포를 포함하는 코드 생성, 런타임 정책, 패키징과 배포 스모크 컨텍스트까지 함께 구성합니다.',
        highlights: ['완성형 코드 생성', '런타임 정책', '배포 패키징'],
    },
];

type CategoryItem = {
    id: number;
    name: string;
    description?: string | null;
};

type ProjectTag = {
    id: number;
    name: string;
};

type ProjectItem = {
    id: number;
    title: string;
    description?: string | null;
    price: number;
    category_id: number;
    downloads: number;
    rating: number;
    demo_url?: string | null;
    github_url?: string | null;
    image_url?: string | null;
    is_active: boolean;
    category?: CategoryItem | null;
    tags?: ProjectTag[];
};

type ProjectListResponse = {
    projects: ProjectItem[];
    total: number;
    skip: number;
    limit: number;
};

type OverviewStats = {
    projects: number;
    users: number;
    purchases: number;
    reviews: number;
};

type RevenueStats = {
    total_revenue: number;
    total_purchases: number;
    average_purchase_amount: number;
};

type TopProject = {
    id: number;
    title: string;
    downloads: number;
    rating: number;
    price: number;
};

type CustomerMe = {
    email: string;
    username: string;
    full_name?: string | null;
    member_type?: string;
    business_name?: string | null;
    business_registration_number?: string | null;
    representative_name?: string | null;
};

type CustomerMemberType = 'individual' | 'sole_proprietor' | 'corporation';

const CUSTOMER_TOKEN_KEY = 'customer_token';
const MEMBER_TYPE_LABELS: Record<CustomerMemberType, string> = {
    individual: '개인',
    sole_proprietor: '개인사업자',
    corporation: '법인사업자',
};

function formatCurrency(value: number) {
    return new Intl.NumberFormat('ko-KR', { style: 'currency', currency: 'KRW', maximumFractionDigits: 0 }).format(Number(value || 0));
}

export default function MarketplacePage() {
    const featureOrchestrator = useFeatureOrchestrator();
    const apiBaseUrl = React.useMemo(() => resolveApiBaseUrl(), []);
    const marketplaceHomeHref = React.useMemo(() => resolveMarketplaceSiteHref('/'), []);
    const adminHomeHref = React.useMemo(() => resolveAdminSiteHref('/'), []);
    const adminLlmHref = React.useMemo(() => resolveStaffSiteHref('/staff/login'), []);
    const [categories, setCategories] = React.useState<CategoryItem[]>([]);
    const [projects, setProjects] = React.useState<ProjectItem[]>([]);
    const [topProjects, setTopProjects] = React.useState<TopProject[]>([]);
    const [overview, setOverview] = React.useState<OverviewStats | null>(null);
    const [revenue, setRevenue] = React.useState<RevenueStats | null>(null);
    const [selectedCategoryId, setSelectedCategoryId] = React.useState(0);
    const [search, setSearch] = React.useState('');
    const [sortBy, setSortBy] = React.useState<'created_at' | 'downloads' | 'rating' | 'price'>('downloads');
    const [loading, setLoading] = React.useState(true);
    const [error, setError] = React.useState('');
    const [token, setToken] = React.useState('');
    const [me, setMe] = React.useState<CustomerMe | null>(null);
    const [authMode, setAuthMode] = React.useState<'login' | 'signup'>('login');
    const [email, setEmail] = React.useState('');
    const [username, setUsername] = React.useState('');
    const [fullName, setFullName] = React.useState('');
    const [memberType, setMemberType] = React.useState<CustomerMemberType>('individual');
    const [businessName, setBusinessName] = React.useState('');
    const [businessRegistrationNumber, setBusinessRegistrationNumber] = React.useState('');
    const [representativeName, setRepresentativeName] = React.useState('');
    const [password, setPassword] = React.useState('');
    const [authLoading, setAuthLoading] = React.useState(false);
    const [authMessage, setAuthMessage] = React.useState('');
    const marketplaceLoadedRef = React.useRef(false);

    const loadMyInfo = React.useCallback(async (targetToken: string) => {
        const response = await fetch(`${apiBaseUrl}/api/auth/me`, {
            headers: { Authorization: `Bearer ${targetToken}` },
            cache: 'no-store',
        });
        if (!response.ok) {
            throw new Error('내 정보를 불러오지 못했습니다.');
        }
        const payload = await response.json();
        setMe(payload);
    }, [apiBaseUrl]);

    const loadMarketplace = React.useCallback(async () => {
        setLoading(true);
        setError('');
        try {
            const projectParams = new URLSearchParams({
                skip: '0',
                limit: '24',
                sort_by: sortBy,
                sort_order: 'desc',
            });
            if (selectedCategoryId > 0) {
                projectParams.set('category_id', String(selectedCategoryId));
            }
            if (search.trim()) {
                projectParams.set('search', search.trim());
            }

            const [categoriesResponse, projectsResponse, overviewResponse, revenueResponse, topProjectsResponse] = await Promise.all([
                fetch(`${apiBaseUrl}/api/marketplace/categories`, { cache: 'no-store' }),
                fetch(`${apiBaseUrl}/api/marketplace/projects?${projectParams.toString()}`, { cache: 'no-store' }),
                fetch(`${apiBaseUrl}/api/marketplace/stats/overview`, { cache: 'no-store' }),
                fetch(`${apiBaseUrl}/api/marketplace/stats/revenue`, { cache: 'no-store' }),
                fetch(`${apiBaseUrl}/api/marketplace/stats/top-projects?limit=6`, { cache: 'no-store' }),
            ]);

            if (!categoriesResponse.ok || !projectsResponse.ok || !overviewResponse.ok || !revenueResponse.ok || !topProjectsResponse.ok) {
                throw new Error('마켓플레이스 데이터를 불러오지 못했습니다.');
            }

            const categoriesPayload = await categoriesResponse.json().catch(() => []);
            const projectsPayload = await projectsResponse.json().catch(() => ({ projects: [], total: 0, skip: 0, limit: 24 }));
            const overviewPayload = await overviewResponse.json().catch(() => null);
            const revenuePayload = await revenueResponse.json().catch(() => null);
            const topProjectsPayload = await topProjectsResponse.json().catch(() => []);

            setCategories(Array.isArray(categoriesPayload) ? categoriesPayload : []);
            setProjects(Array.isArray((projectsPayload as ProjectListResponse)?.projects) ? (projectsPayload as ProjectListResponse).projects : []);
            setOverview(overviewPayload);
            setRevenue(revenuePayload);
            setTopProjects(Array.isArray(topProjectsPayload) ? topProjectsPayload : []);
        } catch (loadError: any) {
            setError(loadError?.message || '마켓플레이스 데이터를 불러오지 못했습니다.');
        } finally {
            setLoading(false);
        }
    }, [apiBaseUrl, search, selectedCategoryId, sortBy]);

    React.useEffect(() => {
        if (marketplaceLoadedRef.current) {
            return;
        }
        marketplaceLoadedRef.current = true;
        void loadMarketplace();
    }, [loadMarketplace]);

    React.useEffect(() => {
        if (typeof window === 'undefined') {
            return;
        }
        const savedToken = localStorage.getItem(CUSTOMER_TOKEN_KEY) || '';
        if (!savedToken) {
            return;
        }
        setToken(savedToken);
        loadMyInfo(savedToken).catch(() => {
            localStorage.removeItem(CUSTOMER_TOKEN_KEY);
            setToken('');
            setMe(null);
        });
    }, [loadMyInfo]);

    const handleAuth = React.useCallback(async () => {
        setAuthLoading(true);
        setAuthMessage('');
        try {
            if (authMode === 'signup') {
                const signupResponse = await fetch(`${apiBaseUrl}/api/auth/signup`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        username: username.trim(),
                        email: email.trim(),
                        password,
                        full_name: fullName.trim(),
                        member_type: memberType,
                        business_name: memberType === 'individual' ? null : businessName.trim(),
                        business_registration_number: memberType === 'individual' ? null : businessRegistrationNumber.trim(),
                        representative_name: memberType === 'corporation' ? representativeName.trim() : null,
                    }),
                });
                const signupPayload = await signupResponse.json().catch(() => null);
                if (!signupResponse.ok) {
                    throw new Error(signupPayload?.detail || '회원가입에 실패했습니다.');
                }
            }

            const formData = new URLSearchParams();
            formData.set('username', email.trim());
            formData.set('password', password);

            const loginResponse = await fetch(`${apiBaseUrl}/api/auth/login`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
                body: formData.toString(),
            });
            const loginPayload = await loginResponse.json().catch(() => null);
            if (!loginResponse.ok || !loginPayload?.access_token) {
                throw new Error(loginPayload?.detail || '로그인에 실패했습니다.');
            }
            if (typeof window !== 'undefined') {
                localStorage.setItem(CUSTOMER_TOKEN_KEY, loginPayload.access_token);
            }
            setToken(loginPayload.access_token);
            await loadMyInfo(loginPayload.access_token);
            setAuthMessage(authMode === 'signup' ? '회원가입과 로그인이 완료되었습니다.' : '로그인되었습니다.');
        } catch (authError: any) {
            setAuthMessage(authError?.message || '인증 처리 중 오류가 발생했습니다.');
        } finally {
            setAuthLoading(false);
        }
    }, [apiBaseUrl, authMode, businessName, businessRegistrationNumber, email, fullName, loadMyInfo, memberType, password, representativeName, username]);

    const handleLogout = React.useCallback(() => {
        if (typeof window !== 'undefined') {
            localStorage.removeItem(CUSTOMER_TOKEN_KEY);
        }
        setToken('');
        setMe(null);
        setAuthMessage('로그아웃되었습니다.');
    }, []);

    const marketplaceSummaryCards = [
        {
            id: 'projects',
            label: '진열 가능 상품',
            value: String(overview?.projects ?? 0),
            note: '실제 등록 프로젝트 기준',
        },
        {
            id: 'purchases',
            label: '완료 구매',
            value: String(overview?.purchases ?? 0),
            note: '결제 완료/반영 건수',
        },
        {
            id: 'revenue',
            label: '평균 구매 금액',
            value: formatCurrency(revenue?.average_purchase_amount ?? 0),
            note: `총 매출 ${formatCurrency(revenue?.total_revenue ?? 0)}`,
        },
        {
            id: 'reviews',
            label: '리뷰 수',
            value: String(overview?.reviews ?? 0),
            note: '공개 상세 리뷰 기준',
        },
    ];

    const marketplaceSidebar = (
        <div className="workspace-section-stack">
            <div className="workspace-sidebar-card" data-testid="marketplace-auth-panel">
                <p className="workspace-card-kicker">Account</p>
                <h3 className="workspace-card-title">회원가입 / 내정보</h3>
                {!me ? (
                    <form
                        className="workspace-form-stack mt-4"
                        onSubmit={(event) => {
                            event.preventDefault();
                            void handleAuth();
                        }}
                    >
                        <div className="workspace-auth-switch text-sm">
                            <button type="button" onClick={() => setAuthMode('login')} className={authMode === 'login' ? 'active' : ''}>로그인</button>
                            <button type="button" onClick={() => setAuthMode('signup')} className={authMode === 'signup' ? 'active' : ''}>회원가입</button>
                        </div>
                        <input value={email} onChange={(event) => setEmail(event.target.value)} placeholder="이메일" className="workspace-input" />
                        {authMode === 'signup' && (
                            <>
                                <input value={username} onChange={(event) => setUsername(event.target.value)} placeholder="사용자명" className="workspace-input" />
                                <input value={fullName} onChange={(event) => setFullName(event.target.value)} placeholder="이름 / 담당자명" className="workspace-input" />
                                <select value={memberType} onChange={(event) => setMemberType(event.target.value as CustomerMemberType)} className="workspace-select">
                                    <option value="individual">개인</option>
                                    <option value="sole_proprietor">개인사업자</option>
                                    <option value="corporation">법인사업자</option>
                                </select>
                                {memberType !== 'individual' && (
                                    <>
                                        <input value={businessName} onChange={(event) => setBusinessName(event.target.value)} placeholder={memberType === 'corporation' ? '법인명' : '상호명'} className="workspace-input" />
                                        <input value={businessRegistrationNumber} onChange={(event) => setBusinessRegistrationNumber(event.target.value)} placeholder="사업자등록번호" className="workspace-input" />
                                    </>
                                )}
                                {memberType === 'corporation' && (
                                    <input value={representativeName} onChange={(event) => setRepresentativeName(event.target.value)} placeholder="대표자명" className="workspace-input" />
                                )}
                            </>
                        )}
                        <input type="password" value={password} onChange={(event) => setPassword(event.target.value)} placeholder="비밀번호" className="workspace-input" />
                        <button type="submit" disabled={authLoading} className="workspace-primary-button w-full justify-center text-center">
                            {authLoading ? '처리 중...' : authMode === 'signup' ? '회원가입 후 시작' : '로그인 후 시작'}
                        </button>
                        {authMessage ? <p className="workspace-card-copy">{authMessage}</p> : null}
                    </form>
                ) : (
                    <div className="workspace-list mt-4 text-sm">
                        <div className="workspace-list-item"><strong>이메일</strong><span>{me.email}</span></div>
                        <div className="workspace-list-item"><strong>사용자명</strong><span>{me.username}</span></div>
                        <div className="workspace-list-item"><strong>가입 유형</strong><span>{MEMBER_TYPE_LABELS[(me.member_type as CustomerMemberType) || 'individual']}</span></div>
                        {me.business_name ? <div className="workspace-list-item"><strong>사업자명/법인명</strong><span>{me.business_name}</span></div> : null}
                    </div>
                )}
            </div>

            <div className="workspace-sidebar-card" data-testid="marketplace-top-projects">
                <p className="workspace-card-kicker">Top Projects</p>
                <h3 className="workspace-card-title">다운로드 상위 프로젝트</h3>
                <div className="workspace-list mt-4">
                    {topProjects.slice(0, 6).map((project) => (
                        <div key={`top-${project.id}`} className="workspace-list-item">
                            <div>
                                <strong>{project.title}</strong>
                                <span>다운로드 {project.downloads} · 평점 {Number(project.rating || 0).toFixed(1)}</span>
                            </div>
                            <strong>{formatCurrency(project.price)}</strong>
                        </div>
                    ))}
                </div>
            </div>
        </div>
    );

    return (
        <>
            <WorkspaceChrome
                brand="Marketplace Workspace"
                title="고객 거래 워크스페이스"
                description="관리자 대시보드와 동일한 디자인 시스템으로 마켓 탐색/주문/운영 통계를 통합합니다."
                statusLabel={loading ? '데이터 동기화 중' : '운영 API 연결 완료'}
                pageTestId="marketplace-main-page"
                compactHeader
                railItems={[
                    { id: 'market-home', label: '마켓 홈', shortLabel: '홈', href: '/marketplace', active: true, accent: 'blue' },
                    { id: 'order', label: '오케스트레이터', shortLabel: '주문', href: '/marketplace/orchestrator', accent: 'violet' },
                    { id: 'admin', label: '관리', shortLabel: '관리', href: '/admin', accent: 'emerald' },
                    { id: 'docs', label: '문서', shortLabel: '문서', href: '/privacy', accent: 'amber' },
                ]}
                topActions={(
                    <>
                        <Link href="/marketplace/orchestrator" className="workspace-topbar-chip">고객용 오케스트레이터</Link>
                        <Link href="/marketplace" className="workspace-topbar-chip">내 Marketplace</Link>
                        <button type="button" onClick={handleLogout} className="workspace-ghost-button">로그아웃</button>
                    </>
                )}
                heroBadge="실데이터 프로젝트 거래소"
                heroEyebrow="프로젝트 탐색 · 주문 · 리뷰 · 통계 API 연결형 메인 화면"
                heroTitle="프로젝트 탐색 · 주문 · 계정을 한 화면에서 처리하는 메인 마켓"
                heroDescription="헤더를 얇게 줄이고, 좌측 레일 + 중앙 상품 그리드 + 우측 계정/Top 패널 고정 구조로 재설계했습니다. 히어로 문구는 축소하고 상품 카드와 주문 버튼의 존재감을 높였습니다."
                heroActions={(
                    <>
                        <Link href="/marketplace/orchestrator" className="workspace-primary-button">맞춤 제작 바로 시작</Link>
                        <Link href="/admin" className="workspace-secondary-button">관리자 상품 관리</Link>
                        <Link href="/marketplace/orchestrator" className="workspace-ghost-button">운영형 주문 안내 열기</Link>
                    </>
                )}
                heroFeaturePills={[
                    '실제 등록 프로젝트 탐색',
                    '카테고리/정렬/검색 필터',
                    '오케스트레이터 주문 진입',
                    'Top 프로젝트/매출 통계',
                    '회원가입 / 내정보 패널',
                    '프로젝트 상세 / 데모 / GitHub 진입',
                ]}
                sidebar={marketplaceSidebar}
            >
                <div className="workspace-section-stack">
                    <div className="workspace-metric-grid" data-testid="marketplace-stats-cards">
                        {marketplaceSummaryCards.map((card) => (
                            <div key={card.id} className="workspace-metric-card">
                                <p className="workspace-metric-label">{card.label}</p>
                                <p className="workspace-metric-value">{card.value}</p>
                                <p className="workspace-metric-note">{card.note}</p>
                            </div>
                        ))}
                    </div>

                    <section className="workspace-card" data-testid="marketplace-filters">
                        <div className="mb-4 flex flex-wrap items-center justify-between gap-4">
                            <div>
                                <p className="workspace-market-hero-minor">상품 필터</p>
                                <h2 className="workspace-card-heading">검색과 카테고리 필터를 먼저 고정</h2>
                            </div>
                            <div className="workspace-chip-row !mt-0">
                                <span className="workspace-chip workspace-chip-active">실시간 API 연동</span>
                                <span className="workspace-chip">상품 {projects.length}개 노출</span>
                            </div>
                        </div>
                        <div className="grid gap-4 lg:grid-cols-[1fr_220px_220px]">
                            <input id="marketplace-search-input" name="marketplace-search" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="프로젝트 제목/설명 검색" className="workspace-input" />
                            <select id="marketplace-category-filter" name="marketplace-category-filter" aria-label="카테고리 필터" title="카테고리 필터" value={String(selectedCategoryId)} onChange={(event) => setSelectedCategoryId(Number(event.target.value))} className="workspace-select">
                                <option value="0">전체 카테고리</option>
                                {categories.map((category) => (
                                    <option key={category.id} value={category.id}>{category.name}</option>
                                ))}
                            </select>
                            <select id="marketplace-sort-by" name="marketplace-sort-by" aria-label="정렬 기준" title="정렬 기준" value={sortBy} onChange={(event) => setSortBy(event.target.value as typeof sortBy)} className="workspace-select">
                                <option value="downloads">다운로드순</option>
                                <option value="rating">평점순</option>
                                <option value="price">가격순</option>
                                <option value="created_at">최신순</option>
                            </select>
                        </div>
                        <div className="workspace-chip-row mt-4">
                            {categories.map((category) => (
                                <button key={category.id} type="button" onClick={() => setSelectedCategoryId(category.id)} className={`workspace-chip ${selectedCategoryId === category.id ? 'workspace-chip-active' : ''}`}>
                                    {category.name}
                                </button>
                            ))}
                            {selectedCategoryId !== 0 && (
                                <button type="button" onClick={() => setSelectedCategoryId(0)} className="workspace-chip">필터 초기화</button>
                            )}
                        </div>
                    </section>

                    <section className="workspace-card" data-testid="marketplace-popup-feature-launcher">
                        <div className="mb-5 flex flex-wrap items-end justify-between gap-4">
                            <div>
                                <p className="workspace-card-kicker">팝업형 백엔드 오케스트레이터</p>
                                <h2 className="workspace-card-heading">대화는 팝업에서, 실행은 백엔드에서</h2>
                                <p className="workspace-card-copy">공개 marketplace 본문은 기존 거래형 구조를 유지하고, 생성형 기능은 popup orchestrator 로 분리했습니다. ai-sheet 기능은 preview schema 와 final workbook 패키지까지 백엔드에서 실행됩니다.</p>
                            </div>
                        </div>
                        <FeatureLauncherGrid
                            catalog={featureOrchestrator.catalog}
                            catalogLoading={featureOrchestrator.catalogLoading}
                            catalogError={featureOrchestrator.catalogError}
                            activeFeatureId={featureOrchestrator.activeFeatureId}
                            onLaunch={featureOrchestrator.openFeature}
                        />
                    </section>

                    <section className="workspace-card" data-testid="marketplace-generator-products-legacy">
                        <div className="mb-5 flex flex-wrap items-end justify-between gap-4">
                            <div>
                                <p className="workspace-card-kicker">기존 코드생성기 상품 진열</p>
                                <h2 className="workspace-card-heading">4가지 코드생성기 상품</h2>
                                <p className="workspace-card-copy">이전 marketplace에 있던 4가지 코드생성기 상품을 별도 진열 섹션으로 복원했습니다. 실데이터 프로젝트 목록과 함께 병행 비교할 수 있습니다.</p>
                            </div>
                        </div>
                        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
                            {generatorMarketplaceProducts.map((product) => (
                                <article key={product.id} className="rounded-[20px] border border-[#30363d] bg-[#0d1117] p-4 shadow-[0_0_0_1px_rgba(255,255,255,0.03)]">
                                    <div className="flex items-start justify-between gap-3">
                                        <div>
                                            <p className="text-base font-bold text-[#58c9ff]">{product.category}</p>
                                            <h3 className="mt-3 text-[20px] font-bold leading-tight text-white">{product.title}</h3>
                                        </div>
                                        <span className="rounded-full border border-[#31c45d] px-3 py-1.5 text-sm font-bold text-[#31c45d]">운영중</span>
                                    </div>
                                    <p className="mt-4 min-h-[104px] text-sm leading-7 text-[#98a3b3]">{product.summary}</p>
                                    <div className="mt-4 flex flex-wrap gap-2">
                                        {product.highlights.map((item) => (
                                            <span key={`${product.id}-${item}`} className="rounded-full border border-[#30363d] bg-[#151b23] px-3 py-1.5 text-xs text-[#d2d9e3]">{item}</span>
                                        ))}
                                    </div>
                                    <div className="mt-6 flex items-end justify-between gap-3">
                                        <span className="text-2xl font-bold text-[#f0b43f]">{product.price}</span>
                                        <a href={adminLlmHref} className="rounded-2xl bg-[#2a7cff] px-4 py-2.5 text-sm font-bold text-white no-underline">직원 검토</a>
                                    </div>
                                </article>
                            ))}
                        </div>
                    </section>

                    <section className="workspace-card">
                        <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
                            <div>
                                <p className="workspace-card-kicker">운영형 프로젝트 목록</p>
                                <h2 className="workspace-card-heading">실제 등록 프로젝트</h2>
                                <p className="workspace-card-copy">DB 프로젝트 기준으로 정렬/검색/카테고리 필터 결과를 바로 보여줍니다. 각 프로젝트는 상세 진입과 오케스트레이터 주문 진입점을 함께 제공합니다.</p>
                            </div>
                            <div className="flex flex-wrap gap-3">
                                <a href={adminHomeHref} className="workspace-secondary-button">관리자 제어 화면</a>
                                <a href={adminLlmHref} className="workspace-secondary-button">직원 오케스트레이터 열기</a>
                            </div>
                        </div>

                        {error && <div className="mb-4 rounded-2xl border border-red-300 bg-red-50 px-4 py-3 text-sm text-red-800">{error}</div>}
                        {loading ? (
                            <div className="workspace-card-copy px-5 py-8 text-center text-base">마켓플레이스 데이터를 불러오는 중...</div>
                        ) : projects.length === 0 ? (
                            <div className="workspace-card-copy px-5 py-8 text-center text-base">조건에 맞는 프로젝트가 없습니다.</div>
                        ) : (
                            <div className="workspace-market-grid" data-testid="marketplace-project-grid">
                                {projects.map((project) => (
                                    <article key={project.id} className="workspace-project-card">
                                        <div className="flex items-start justify-between gap-3">
                                            <div>
                                                <p className="workspace-card-kicker">{project.category?.name || categories.find((item) => item.id === project.category_id)?.name || `카테고리 ${project.category_id}`}</p>
                                                <h3 className="workspace-card-heading">{project.title}</h3>
                                            </div>
                                            <span className={`workspace-chip ${project.is_active ? 'workspace-chip-active' : ''}`}>{project.is_active ? '운영중' : '비활성'}</span>
                                        </div>
                                        <p className="workspace-card-body">{project.description || '설명이 아직 등록되지 않았습니다.'}</p>
                                        {!!project.tags?.length && (
                                            <div className="workspace-chip-row">
                                                {project.tags.slice(0, 5).map((tag) => (
                                                    <span key={`${project.id}-${tag.id}-${tag.name}`} className="workspace-chip">#{tag.name}</span>
                                                ))}
                                            </div>
                                        )}
                                        <div className="workspace-project-stats">
                                            <div className="workspace-project-stat">
                                                <p className="workspace-project-stat-label">다운로드</p>
                                                <p className="workspace-project-stat-value">{project.downloads || 0}</p>
                                            </div>
                                            <div className="workspace-project-stat">
                                                <p className="workspace-project-stat-label">평점</p>
                                                <p className="workspace-project-stat-value">{Number(project.rating || 0).toFixed(1)}</p>
                                            </div>
                                            <div className="workspace-project-stat">
                                                <p className="workspace-project-stat-label">가격</p>
                                                <p className="workspace-project-stat-value text-[#f0b43f]">{formatCurrency(project.price)}</p>
                                            </div>
                                        </div>
                                        <div className="mt-5 flex flex-wrap items-center justify-between gap-2">
                                            <div className="flex flex-wrap gap-2">
                                                <Link href={`/marketplace/${project.id}`} className="workspace-secondary-button">상세 보기</Link>
                                                <Link href={`/marketplace/orchestrator?product=code-generator-deployment-kit&projectId=${project.id}&projectTitle=${encodeURIComponent(project.title)}&projectSummary=${encodeURIComponent(project.description || '')}`} className="workspace-primary-button">오케스트레이터 주문</Link>
                                                <a href={adminLlmHref} className="workspace-secondary-button">직원 오케스트레이터</a>
                                            </div>
                                            <div className="flex flex-wrap gap-2">
                                                {project.demo_url && <a href={project.demo_url} target="_blank" rel="noreferrer" className="workspace-topbar-chip">데모</a>}
                                                {project.github_url && <a href={project.github_url} target="_blank" rel="noreferrer" className="workspace-topbar-chip">GitHub</a>}
                                            </div>
                                        </div>
                                    </article>
                                ))}
                            </div>
                        )}
                    </section>
                </div>
            </WorkspaceChrome>
            <FeatureOrchestratorPopup
                isOpen={featureOrchestrator.isPopupOpen}
                activeFeatureId={featureOrchestrator.activeFeatureId}
                featureMeta={featureOrchestrator.activeFeatureMeta}
                popupMode={featureOrchestrator.activeFeature?.popup_mode}
                title={featureOrchestrator.activeFeature?.title || 'AI 엑셀 시트'}
                featureSummary={featureOrchestrator.activeFeature?.summary || '시트 schema preview 와 최종 workbook 패키지를 생성하는 실행형 backend'}
                popupState={featureOrchestrator.popupState}
                projectName={featureOrchestrator.projectName}
                setProjectName={featureOrchestrator.setProjectName}
                prompt={featureOrchestrator.prompt}
                setPrompt={featureOrchestrator.setPrompt}
                templateId={featureOrchestrator.templateId}
                setTemplateId={featureOrchestrator.setTemplateId}
                finalEnabled={featureOrchestrator.finalEnabled}
                setFinalEnabled={featureOrchestrator.setFinalEnabled}
                supportsPhotoUpload={Boolean(featureOrchestrator.activeFeature?.supports_photo_upload)}
                photoFileName={featureOrchestrator.photoFileName}
                photoPreviewUrl={featureOrchestrator.photoPreviewUrl}
                applyPhotoFile={featureOrchestrator.applyPhotoFile}
                previewArtifact={featureOrchestrator.previewArtifact}
                finalArtifact={featureOrchestrator.finalArtifact}
                qualityReview={featureOrchestrator.qualityReview}
                submitLoading={featureOrchestrator.submitLoading}
                submitFeature={featureOrchestrator.submitFeature}
                closePopup={featureOrchestrator.closePopup}
                errorText={featureOrchestrator.errorText}
                runId={featureOrchestrator.runId}
                eventLog={featureOrchestrator.eventLog}
                streamConnection={featureOrchestrator.streamConnection}
                stageRunStatus={featureOrchestrator.stageRun?.status}
                latestEventAt={featureOrchestrator.latestEventAt}
                elapsedSeconds={featureOrchestrator.elapsedSeconds}
                liveViewArtifact={featureOrchestrator.liveViewArtifact}
                spreadsheetRunSummary={featureOrchestrator.spreadsheetRunSummary}
                spreadsheetDownloadLinks={featureOrchestrator.spreadsheetDownloadLinks}
                progressSnapshot={featureOrchestrator.progressSnapshot}
                progressHistory={featureOrchestrator.progressHistory}
            />
        </>
    );
}
