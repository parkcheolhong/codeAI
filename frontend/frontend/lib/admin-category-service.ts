export interface CategoryItem {
    id: number;
    name: string;
    description?: string;
}

export interface AdminProjectItem {
    id: number;
    title?: string | null;
    category_id?: number | null;
    price?: number | null;
    downloads?: number | null;
    rating?: number | null;
    is_active?: boolean | null;
    created_at?: string | null;
}

export interface CategoryStat {
    total: number;
    today: number;
    yesterday: number;
    downloads: number;
    revenue: number;
    ratingSum: number;
    ratingCount: number;
    averageRating: number;
    activeCount: number;
    inactiveCount: number;
}

export type CategoryListResult = {
    categories: CategoryItem[];
    nextSelectedCategoryId?: number;
};

export type CategoryStatsResult = {
    stats: Record<number, CategoryStat>;
    recentProjects: Record<number, AdminProjectItem[]>;
};

export type CategoryMutationResult = {
    id?: number;
    name: string;
};

export function createEmptyCategoryStat(): CategoryStat {
    return {
        total: 0,
        today: 0,
        yesterday: 0,
        downloads: 0,
        revenue: 0,
        ratingSum: 0,
        ratingCount: 0,
        averageRating: 0,
        activeCount: 0,
        inactiveCount: 0,
    };
}

export function isUnauthorizedCategoryResponse(status: number) {
    return status === 401 || status === 403;
}

export async function loadMarketplaceCategories(options: {
    apiBaseUrl: string;
    selectedCategoryId: number;
    fetchImpl?: typeof fetch;
}): Promise<CategoryListResult> {
    const fetcher = options.fetchImpl || fetch;
    const response = await fetcher(`${options.apiBaseUrl}/api/marketplace/categories`);
    if (!response.ok) {
        throw new Error(`카테고리 조회 실패(${response.status})`);
    }
    const data = await response.json().catch(() => []);
    const categories = Array.isArray(data) ? data as CategoryItem[] : [];
    return {
        categories,
        nextSelectedCategoryId: categories.length > 0 && options.selectedCategoryId === 0 ? categories[0].id : undefined,
    };
}

export async function createMarketplaceCategory(options: {
    apiBaseUrl: string;
    token: string;
    name: string;
    description?: string;
    existingCategories: CategoryItem[];
    fetchImpl?: typeof fetch;
}): Promise<CategoryMutationResult> {
    const duplicated = options.existingCategories.some((category) => category.name === options.name);
    if (duplicated) {
        throw new Error(`이미 존재하는 카테고리입니다: ${options.name}`);
    }

    const fetcher = options.fetchImpl || fetch;
    const response = await fetcher(`${options.apiBaseUrl}/api/marketplace/categories`, {
        method: 'POST',
        headers: {
            Authorization: `Bearer ${options.token}`,
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({
            name: options.name,
            description: options.description || undefined,
        }),
    });

    if (isUnauthorizedCategoryResponse(response.status)) {
        throw new Error('__ADMIN_CATEGORY_UNAUTHORIZED__');
    }

    const data = await response.json().catch(() => null);
    if (!response.ok) {
        throw new Error(data?.detail || `카테고리 생성 실패(${response.status})`);
    }

    return {
        id: typeof data?.id === 'number' ? data.id : undefined,
        name: data?.name || options.name,
    };
}

export async function updateMarketplaceCategory(options: {
    apiBaseUrl: string;
    token: string;
    categoryId: number;
    name: string;
    description?: string;
    fetchImpl?: typeof fetch;
}): Promise<CategoryMutationResult> {
    const fetcher = options.fetchImpl || fetch;
    const response = await fetcher(`${options.apiBaseUrl}/api/marketplace/categories/${options.categoryId}`, {
        method: 'PUT',
        headers: {
            Authorization: `Bearer ${options.token}`,
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({
            name: options.name,
            description: options.description || undefined,
        }),
    });

    if (isUnauthorizedCategoryResponse(response.status)) {
        throw new Error('__ADMIN_CATEGORY_UNAUTHORIZED__');
    }

    const data = await response.json().catch(() => null);
    if (!response.ok) {
        throw new Error(data?.detail || `카테고리 수정 실패(${response.status})`);
    }

    return {
        id: options.categoryId,
        name: data?.name || options.name,
    };
}

export async function deleteMarketplaceCategory(options: {
    apiBaseUrl: string;
    token: string;
    category: CategoryItem;
    fetchImpl?: typeof fetch;
}): Promise<CategoryMutationResult> {
    const fetcher = options.fetchImpl || fetch;
    const response = await fetcher(`${options.apiBaseUrl}/api/marketplace/categories/${options.category.id}`, {
        method: 'DELETE',
        headers: {
            Authorization: `Bearer ${options.token}`,
        },
    });

    if (isUnauthorizedCategoryResponse(response.status)) {
        throw new Error('__ADMIN_CATEGORY_UNAUTHORIZED__');
    }

    if (!response.ok) {
        const data = await response.json().catch(() => null);
        throw new Error(data?.detail || `카테고리 삭제 실패(${response.status})`);
    }

    return {
        id: options.category.id,
        name: options.category.name,
    };
}

export async function loadAdminCategoryStats(options: {
    apiBaseUrl: string;
    token: string;
    limit?: number;
    fetchImpl?: typeof fetch;
}): Promise<CategoryStatsResult> {
    const fetcher = options.fetchImpl || fetch;
    const projectLimit = Number.isFinite(options.limit) && (options.limit as number) > 0
        ? Math.min(Math.trunc(options.limit as number), 500)
        : 500;
    const response = await fetcher(`${options.apiBaseUrl}/api/admin/projects?skip=0&limit=${projectLimit}`, {
        headers: { Authorization: `Bearer ${options.token}` },
    });

    if (isUnauthorizedCategoryResponse(response.status)) {
        throw new Error('__ADMIN_CATEGORY_UNAUTHORIZED__');
    }
    if (!response.ok) {
        throw new Error(`카테고리 통계 조회 실패(${response.status})`);
    }

    const data = await response.json().catch(() => null);
    const projects = (Array.isArray(data?.projects) ? data.projects : []) as AdminProjectItem[];
    const stats: Record<number, CategoryStat> = {};
    const recentProjects: Record<number, AdminProjectItem[]> = {};

    const now = new Date();
    const todayKey = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`;
    const yesterday = new Date(now);
    yesterday.setDate(now.getDate() - 1);
    const yesterdayKey = `${yesterday.getFullYear()}-${String(yesterday.getMonth() + 1).padStart(2, '0')}-${String(yesterday.getDate()).padStart(2, '0')}`;

    for (const project of projects) {
        const categoryId = Number(project.category_id || 0);
        if (categoryId <= 0) continue;

        if (!stats[categoryId]) {
            stats[categoryId] = createEmptyCategoryStat();
        }
        stats[categoryId].total += 1;
        stats[categoryId].downloads += Number(project.downloads || 0);
        stats[categoryId].revenue += Number(project.price || 0) * Number(project.downloads || 0);
        if (typeof project.rating === 'number' && project.rating > 0) {
            stats[categoryId].ratingSum += Number(project.rating || 0);
            stats[categoryId].ratingCount += 1;
        }
        if (project.is_active === false) {
            stats[categoryId].inactiveCount += 1;
        } else {
            stats[categoryId].activeCount += 1;
        }

        if (!recentProjects[categoryId]) {
            recentProjects[categoryId] = [];
        }
        recentProjects[categoryId].push(project);

        if (project.created_at) {
            const createdAt = new Date(project.created_at);
            if (!Number.isNaN(createdAt.getTime())) {
                const createdKey = `${createdAt.getFullYear()}-${String(createdAt.getMonth() + 1).padStart(2, '0')}-${String(createdAt.getDate()).padStart(2, '0')}`;
                if (createdKey === todayKey) {
                    stats[categoryId].today += 1;
                } else if (createdKey === yesterdayKey) {
                    stats[categoryId].yesterday += 1;
                }
            }
        }
    }

    Object.values(stats).forEach((stat) => {
        stat.averageRating = stat.ratingCount > 0
            ? Number((stat.ratingSum / stat.ratingCount).toFixed(2))
            : 0;
    });

    return {
        stats,
        recentProjects: Object.fromEntries(
            Object.entries(recentProjects).map(([categoryId, items]) => [
                Number(categoryId),
                [...items]
                    .sort((left, right) => {
                        const leftTime = left.created_at ? new Date(left.created_at).getTime() : 0;
                        const rightTime = right.created_at ? new Date(right.created_at).getTime() : 0;
                        return rightTime - leftTime;
                    })
                    .slice(0, 3),
            ]),
        ) as Record<number, AdminProjectItem[]>,
    };
}

export function assertAdminCategoryServiceContract() {
    const stat = createEmptyCategoryStat();
    if (stat.total !== 0 || stat.activeCount !== 0) {
        throw new Error('admin category service contract 누락: empty stat 기본값 필요');
    }
}
