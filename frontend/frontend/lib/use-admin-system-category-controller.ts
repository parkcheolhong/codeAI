import { useCallback, useEffect, useRef, useState } from 'react';
import {
    applyAdminGlobalAutomaticMode,
    buildSystemSettingsDraft,
    buildSystemSettingsOpenState,
    changeAdminAccountPassword,
    loadAdminIdentityProviderSettings,
    loadAdminSystemSettings,
    saveAdminSystemSettings,
    type AdminIdentityProviderSettings,
    updateAdminPostgresRuntimePassword,
    type AdminSystemSettingsResponse,
} from '@/lib/admin-system-settings-service';
import {
    createMarketplaceCategory,
    deleteMarketplaceCategory,
    loadAdminCategoryStats,
    loadMarketplaceCategories,
    updateMarketplaceCategory,
    type AdminProjectItem,
    type CategoryItem,
    type CategoryStat,
} from '@/lib/admin-category-service';
import type { LiveLogItem } from '@/lib/admin-runtime-types';
import { clearAdminToken, getAdminToken } from '@/lib/admin-session';
import { hardRedirectToAdminLogin } from '@/lib/admin-navigation';
import {
    clearAdminApiBackoff,
    isAdminApiBackoffActive,
    setAdminApiBackoff,
} from '@/lib/admin-api-guard';

export type UseAdminSystemCategoryControllerOptions = {
    apiBaseUrl: string;
    handleAdminUnauthorized: (message?: string) => void;
    normalizeSystemSettingsMessage: (message: string) => {
        userMessage: string;
        detailMessage: string;
        liveLogLevel: LiveLogItem['level'];
        liveLogMessage: string;
    };
    pushLiveLog: (level: LiveLogItem['level'], message: string) => void;
    setAutoRefreshEnabled: (value: boolean) => void;
    setRefreshSeconds: (value: number) => void;
    hideEmptyStorageKey: string;
    categorySortStorageKey: string;
};

export function useAdminSystemCategoryController(options: UseAdminSystemCategoryControllerOptions) {
    const {
        apiBaseUrl,
        handleAdminUnauthorized,
        normalizeSystemSettingsMessage,
        pushLiveLog,
        setAutoRefreshEnabled,
        setRefreshSeconds,
        hideEmptyStorageKey,
        categorySortStorageKey,
    } = options;
    const [systemSettings, setSystemSettings] = useState<AdminSystemSettingsResponse | null>(null);
    const [systemSettingsDraft, setSystemSettingsDraft] = useState<Record<string, string>>({});
    const [systemSettingsOpen, setSystemSettingsOpen] = useState<Record<string, boolean>>({});
    const [systemSettingsLoading, setSystemSettingsLoading] = useState(false);
    const [systemSettingsSaving, setSystemSettingsSaving] = useState(false);
    const [systemAutomaticApplying, setSystemAutomaticApplying] = useState(false);
    const [systemSettingsMessage, setSystemSettingsMessage] = useState('');
    const [identityProviderSettings, setIdentityProviderSettings] = useState<AdminIdentityProviderSettings | null>(null);
    const [adminPasswordCurrent, setAdminPasswordCurrent] = useState('');
    const [adminPasswordNext, setAdminPasswordNext] = useState('');
    const [adminPasswordConfirm, setAdminPasswordConfirm] = useState('');
    const [adminPasswordChanging, setAdminPasswordChanging] = useState(false);
    const [adminPasswordMessage, setAdminPasswordMessage] = useState('');
    const [postgresPasswordNext, setPostgresPasswordNext] = useState('');
    const [postgresPasswordConfirm, setPostgresPasswordConfirm] = useState('');
    const [postgresPasswordSaving, setPostgresPasswordSaving] = useState(false);
    const [postgresPasswordMessage, setPostgresPasswordMessage] = useState('');
    const [categories, setCategories] = useState<CategoryItem[]>([]);
    const [selectedCategoryId, setSelectedCategoryId] = useState<number>(0);
    const [categoryStats, setCategoryStats] = useState<Record<number, CategoryStat>>({});
    const [categoryRecentProjects, setCategoryRecentProjects] = useState<Record<number, AdminProjectItem[]>>({});
    const [categoryName, setCategoryName] = useState('프로그램 개발코너');
    const [categoryDescription, setCategoryDescription] = useState('간단한 프로그램 개발 상품용 카테고리');
    const [categoryCreating, setCategoryCreating] = useState(false);
    const [categoryUpdatingId, setCategoryUpdatingId] = useState<number | null>(null);
    const [categoryDeletingId, setCategoryDeletingId] = useState<number | null>(null);
    const [editingCategoryId, setEditingCategoryId] = useState<number | null>(null);
    const [editingCategoryName, setEditingCategoryName] = useState('');
    const [editingCategoryDescription, setEditingCategoryDescription] = useState('');
    const [hideEmptyCategories, setHideEmptyCategories] = useState(false);
    const [categorySortBy, setCategorySortBy] = useState<'name' | 'projects' | 'today' | 'downloads' | 'revenue' | 'rating' | 'active'>('projects');
    const [categoryMessage, setCategoryMessage] = useState('');
    const [lastSystemSettingsLoadedAt, setLastSystemSettingsLoadedAt] = useState(0);
    const selectedCategoryIdRef = useRef(0);
    const lastCategoriesLoadedAtRef = useRef(0);
    const categoriesRequestInFlightRef = useRef(false);
    const categoriesRetryAfterRef = useRef(0);
    const lastCategoryStatsLoadedAtRef = useRef(0);
    const categoryStatsRequestInFlightRef = useRef(false);
    const categoryStatsRetryAfterRef = useRef(0);
    const systemSettingsAbortRef = useRef<AbortController | null>(null);
    const SYSTEM_SETTINGS_BACKOFF_KEY = 'admin-system-settings';

    const syncOptimizedRuntimeConfig = useCallback(async (values: Record<string, string>) => {
        const token = getAdminToken();
        if (!token) {
            throw new Error('__ADMIN_SYSTEM_UNAUTHORIZED__');
        }
        const response = await fetch(`${apiBaseUrl}/api/llm/runtime-config`, {
            method: 'POST',
            headers: {
                Authorization: `Bearer ${token}`,
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({
                code_generation_strategy: 'auto_generator',
                selected_profile: 'rtx5090_32gb',
                min_files: 27,
                min_dirs: 3,
                model_routes: {
                    default: values.LLM_MODEL_DEFAULT || '',
                    reasoning: values.LLM_MODEL_REASONING || values.LLM_MODEL_DEFAULT || '',
                    coding: values.LLM_MODEL_CODING || values.LLM_MODEL_DEFAULT || '',
                    chat: values.LLM_MODEL_CHAT || values.LLM_MODEL_DEFAULT || '',
                    voice_chat: values.LLM_MODEL_VOICE_CHAT || values.LLM_MODEL_CHAT || values.LLM_MODEL_DEFAULT || '',
                    planner: values.LLM_MODEL_PLANNER || values.LLM_MODEL_REASONING || values.LLM_MODEL_DEFAULT || '',
                    coder: values.LLM_MODEL_CODER || values.LLM_MODEL_CODING || values.LLM_MODEL_DEFAULT || '',
                    reviewer: values.LLM_MODEL_REVIEWER || values.LLM_MODEL_REASONING || values.LLM_MODEL_DEFAULT || '',
                    designer: values.LLM_MODEL_DESIGNER || values.LLM_MODEL_CHAT || values.LLM_MODEL_DEFAULT || '',
                    smart_planner: values.LLM_MODEL_SMART_PLANNER || values.LLM_MODEL_PLANNER || values.LLM_MODEL_REASONING || values.LLM_MODEL_DEFAULT || '',
                    smart_executor: values.LLM_MODEL_SMART_EXECUTOR || values.LLM_MODEL_CODER || values.LLM_MODEL_CODING || values.LLM_MODEL_DEFAULT || '',
                    smart_designer: values.LLM_MODEL_SMART_DESIGNER || values.LLM_MODEL_DESIGNER || values.LLM_MODEL_CHAT || values.LLM_MODEL_DEFAULT || '',
                },
            }),
        });
        if (response.status === 401 || response.status === 403) {
            throw new Error('__ADMIN_SYSTEM_UNAUTHORIZED__');
        }
        const data = await response.json().catch(() => null);
        if (!response.ok || !data) {
            throw new Error((data as any)?.detail || `런타임 설정 저장 실패(${response.status})`);
        }
        return data;
    }, [apiBaseUrl]);

    useEffect(() => {
        try {
            const hideEmptyRaw = localStorage.getItem(hideEmptyStorageKey);
            setHideEmptyCategories(hideEmptyRaw === 'true');
        } catch {
            setHideEmptyCategories(false);
        }

        try {
            const categorySortRaw = localStorage.getItem(categorySortStorageKey);
            if (categorySortRaw === 'name' || categorySortRaw === 'projects' || categorySortRaw === 'today' || categorySortRaw === 'downloads' || categorySortRaw === 'revenue' || categorySortRaw === 'rating' || categorySortRaw === 'active') {
                setCategorySortBy(categorySortRaw);
            }
        } catch {
            setCategorySortBy('projects');
        }
    }, [categorySortStorageKey, hideEmptyStorageKey]);

    useEffect(() => {
        try {
            localStorage.setItem(hideEmptyStorageKey, hideEmptyCategories ? 'true' : 'false');
        } catch {
        }
    }, [hideEmptyCategories, hideEmptyStorageKey]);

    useEffect(() => {
        try {
            localStorage.setItem(categorySortStorageKey, categorySortBy);
        } catch {
        }
    }, [categorySortBy, categorySortStorageKey]);

    useEffect(() => {
        selectedCategoryIdRef.current = selectedCategoryId;
    }, [selectedCategoryId]);

    const loadSystemSettings = useCallback(async () => {
        const token = getAdminToken();
        if (!token) {
            return;
        }

        if (isAdminApiBackoffActive(SYSTEM_SETTINGS_BACKOFF_KEY)) {
            return;
        }

        if (systemSettingsLoading) {
            return;
        }

        const now = Date.now();
        if (now - lastSystemSettingsLoadedAt < 1500) {
            return;
        }

        setSystemSettingsLoading(true);
        setSystemSettingsMessage('');
        try {
            const nextSettings = await loadAdminSystemSettings({
                apiBaseUrl,
                token,
            });
            const nextIdentityProviderSettings = await loadAdminIdentityProviderSettings({
                apiBaseUrl,
                token,
            });
            setSystemSettings(nextSettings);
            setSystemSettingsDraft(buildSystemSettingsDraft(nextSettings));
            setSystemSettingsOpen((prev) => buildSystemSettingsOpenState(nextSettings, prev));
            setIdentityProviderSettings(nextIdentityProviderSettings);
            setLastSystemSettingsLoadedAt(Date.now());
            clearAdminApiBackoff(SYSTEM_SETTINGS_BACKOFF_KEY);
        } catch (error: any) {
            if (error?.message === '__ADMIN_SYSTEM_UNAUTHORIZED__') {
                handleAdminUnauthorized();
                return;
            }
            const errorText = String(error?.message || '');
            if (/502|503|504|Gateway Timeout|connection refused/i.test(errorText)) {
                setAdminApiBackoff(SYSTEM_SETTINGS_BACKOFF_KEY);
            }
            const normalized = normalizeSystemSettingsMessage(
                error?.message || '전역 설정 조회 중 오류가 발생했습니다.',
            );
            setSystemSettingsMessage(`${normalized.userMessage}\n${normalized.detailMessage}`);
            pushLiveLog(normalized.liveLogLevel, normalized.liveLogMessage);
        } finally {
            setSystemSettingsLoading(false);
        }
    }, [apiBaseUrl, handleAdminUnauthorized, normalizeSystemSettingsMessage, pushLiveLog]);

    const changeAdminPassword = useCallback(async () => {
        const token = getAdminToken();
        if (!token) {
            handleAdminUnauthorized();
            return;
        }

        setAdminPasswordChanging(true);
        setAdminPasswordMessage('');
        try {
            const data = await changeAdminAccountPassword({
                apiBaseUrl,
                token,
                currentPassword: adminPasswordCurrent,
                newPassword: adminPasswordNext,
                confirmPassword: adminPasswordConfirm,
            });
            if (!data) {
                throw new Error('관리자 비밀번호 변경 응답이 비어 있습니다.');
            }
            setAdminPasswordCurrent('');
            setAdminPasswordNext('');
            setAdminPasswordConfirm('');
            setAdminPasswordMessage(data?.message || '관리자 비밀번호가 변경되었습니다.');
            clearAdminToken();
            window.setTimeout(() => {
                hardRedirectToAdminLogin();
            }, 1200);
        } catch (error: any) {
            if (error?.message === '__ADMIN_SYSTEM_UNAUTHORIZED__') {
                handleAdminUnauthorized();
                return;
            }
            const normalizedMessage = String(error?.message || '관리자 비밀번호 변경 중 오류가 발생했습니다.').trim();
            setAdminPasswordMessage(normalizedMessage);
            pushLiveLog('warning', `관리자 비밀번호 변경 실패: ${normalizedMessage}`);
        } finally {
            setAdminPasswordChanging(false);
        }
    }, [adminPasswordConfirm, adminPasswordCurrent, adminPasswordNext, apiBaseUrl, handleAdminUnauthorized]);

    const updatePostgresRuntimePassword = useCallback(async () => {
        const token = getAdminToken();
        if (!token) {
            handleAdminUnauthorized();
            return;
        }

        setPostgresPasswordSaving(true);
        setPostgresPasswordMessage('');
        try {
            const data = await updateAdminPostgresRuntimePassword({
                apiBaseUrl,
                token,
                newPassword: postgresPasswordNext,
                confirmPassword: postgresPasswordConfirm,
            });
            if (!data) {
                throw new Error('PostgreSQL 런타임 비밀번호 응답이 비어 있습니다.');
            }
            setPostgresPasswordNext('');
            setPostgresPasswordConfirm('');
            setPostgresPasswordMessage([
                data?.message || 'PostgreSQL 런타임 비밀번호를 저장했습니다.',
                data?.secret_host_path ? `시크릿 파일: ${data.secret_host_path}` : '',
                data?.env_path ? `ENV 경로: ${data.env_path}` : '',
            ].filter(Boolean).join('\n'));
            void loadSystemSettings();
        } catch (error: any) {
            if (error?.message === '__ADMIN_SYSTEM_UNAUTHORIZED__') {
                handleAdminUnauthorized();
                return;
            }
            setPostgresPasswordMessage(error?.message || 'PostgreSQL 런타임 비밀번호 저장 중 오류가 발생했습니다.');
        } finally {
            setPostgresPasswordSaving(false);
        }
    }, [apiBaseUrl, handleAdminUnauthorized, loadSystemSettings, postgresPasswordConfirm, postgresPasswordNext]);

    const updateSystemSettingValue = useCallback((key: string, value: string) => {
        setSystemSettingsDraft((prev) => ({
            ...prev,
            [key]: value,
        }));
    }, []);

    const toggleSystemSettingsSection = useCallback((sectionId: string) => {
        setSystemSettingsOpen((prev) => ({
            ...prev,
            [sectionId]: !prev[sectionId],
        }));
    }, []);

    const saveSystemSettings = useCallback(async () => {
        const token = getAdminToken();
        if (!token) {
            handleAdminUnauthorized('관리자 로그인이 필요합니다. 다시 로그인하세요.');
            return;
        }

        setSystemSettingsSaving(true);
        setSystemSettingsMessage('');
        try {
            const nextSettings = await saveAdminSystemSettings({
                apiBaseUrl,
                token,
                values: systemSettingsDraft,
            });
            await syncOptimizedRuntimeConfig(systemSettingsDraft);
            setSystemSettings(nextSettings);
            setSystemSettingsDraft(buildSystemSettingsDraft(nextSettings));
            setSystemSettingsMessage('.env 전역 설정과 runtime-config 최적화 기본값이 함께 저장되었습니다.');
            pushLiveLog('success', '관리자 전역 .env 설정 및 runtime-config 저장 완료');
        } catch (error: any) {
            if (error?.message === '__ADMIN_SYSTEM_UNAUTHORIZED__') {
                handleAdminUnauthorized();
                return;
            }
            const message = error?.message || '전역 설정 저장 중 오류가 발생했습니다.';
            setSystemSettingsMessage(message);
            pushLiveLog('warning', `전역 설정 저장 실패: ${message}`);
        } finally {
            setSystemSettingsSaving(false);
        }
    }, [apiBaseUrl, handleAdminUnauthorized, pushLiveLog, systemSettingsDraft]);

    const applyGlobalAutomaticMode = useCallback(async () => {
        const token = getAdminToken();
        if (!token) {
            handleAdminUnauthorized('관리자 로그인이 필요합니다. 다시 로그인하세요.');
            return;
        }

        setSystemAutomaticApplying(true);
        setSystemSettingsMessage('');
        try {
            const applied = await applyAdminGlobalAutomaticMode({
                apiBaseUrl,
                token,
            });
            setAutoRefreshEnabled(true);
            setRefreshSeconds(20);
            await loadSystemSettings();
            setSystemSettingsMessage(
                `${applied.message}\n적용 시각: ${new Date(applied.applied_at).toLocaleString('ko-KR')}\n선택 프로필: ${applied.runtime_summary.selected_profile || '-'} · code generation: ${applied.runtime_summary.code_generation_strategy || '-'}${applied.restart_required ? '\n주의: .env 기반 부팅 설정은 서비스 재기동 후 완전 적용됩니다.' : ''}`,
            );
            pushLiveLog('success', '관리자 대시보드 전역 자동 프리셋 적용 완료');
        } catch (error: any) {
            if (error?.message === '__ADMIN_SYSTEM_UNAUTHORIZED__') {
                handleAdminUnauthorized();
                return;
            }
            const message = error?.message || '전역 자동 전환 중 오류가 발생했습니다.';
            setSystemSettingsMessage(message);
            pushLiveLog('warning', `전역 자동 전환 실패: ${message}`);
        } finally {
            setSystemAutomaticApplying(false);
        }
    }, [apiBaseUrl, handleAdminUnauthorized, loadSystemSettings, pushLiveLog, setAutoRefreshEnabled, setRefreshSeconds]);

    const loadCategories = useCallback(async () => {
        const now = Date.now();
        if (now < categoriesRetryAfterRef.current) {
            return;
        }
        if (categoriesRequestInFlightRef.current || now - lastCategoriesLoadedAtRef.current < 1500) {
            return;
        }
        categoriesRequestInFlightRef.current = true;
        try {
            const result = await loadMarketplaceCategories({
                apiBaseUrl,
                selectedCategoryId: selectedCategoryIdRef.current,
            });
            setCategories(result.categories);
            lastCategoriesLoadedAtRef.current = Date.now();
            categoriesRetryAfterRef.current = 0;
            if (typeof result.nextSelectedCategoryId === 'number') {
                setSelectedCategoryId(result.nextSelectedCategoryId);
            }
        } catch {
            categoriesRetryAfterRef.current = Date.now() + 10000;
        } finally {
            categoriesRequestInFlightRef.current = false;
        }
    }, [apiBaseUrl]);

    const createCategory = useCallback(async () => {
        const name = categoryName.trim();
        if (!name) {
            setCategoryMessage('카테고리명을 입력해주세요.');
            return;
        }

        const token = getAdminToken();
        if (!token) {
            handleAdminUnauthorized('관리자 로그인이 필요합니다. 다시 로그인하세요.');
            return;
        }

        const duplicated = categories.some((category) => category.name === name);
        if (duplicated) {
            setCategoryMessage(`이미 존재하는 카테고리입니다: ${name}`);
            return;
        }

        setCategoryCreating(true);
        setCategoryMessage('');
        try {
            const result = await createMarketplaceCategory({
                apiBaseUrl: options.apiBaseUrl,
                token,
                name,
                description: categoryDescription.trim() || undefined,
                existingCategories: categories,
            });
            setCategoryMessage(`생성 완료: ${name} (ID: ${result.id ?? '-'})`);
            options.pushLiveLog('success', `카테고리 생성 완료: ${name}`);
            await loadCategories();
            if (typeof result.id === 'number') {
                setSelectedCategoryId(result.id);
            }
        } catch (error: any) {
            if (error?.message === '__ADMIN_CATEGORY_UNAUTHORIZED__') {
                options.handleAdminUnauthorized();
                return;
            }
            const message = error?.message || '카테고리 생성 중 오류가 발생했습니다.';
            setCategoryMessage(message);
            options.pushLiveLog('warning', message);
        } finally {
            setCategoryCreating(false);
        }
    }, [categories, categoryDescription, categoryName, loadCategories, options]);

    const beginEditCategory = useCallback((category: CategoryItem) => {
        setEditingCategoryId(category.id);
        setEditingCategoryName(category.name || '');
        setEditingCategoryDescription(category.description || '');
        setCategoryMessage('');
    }, []);

    const cancelEditCategory = useCallback(() => {
        setEditingCategoryId(null);
        setEditingCategoryName('');
        setEditingCategoryDescription('');
    }, []);

    const updateCategory = useCallback(async (categoryId: number) => {
        const token = getAdminToken();
        if (!token) {
            options.handleAdminUnauthorized('관리자 로그인이 필요합니다. 다시 로그인하세요.');
            return;
        }

        const name = editingCategoryName.trim();
        if (!name) {
            setCategoryMessage('카테고리명을 입력해주세요.');
            return;
        }

        setCategoryUpdatingId(categoryId);
        setCategoryMessage('');
        try {
            const result = await updateMarketplaceCategory({
                apiBaseUrl: options.apiBaseUrl,
                token,
                categoryId,
                name,
                description: editingCategoryDescription.trim() || undefined,
            });
            setCategoryMessage(`수정 완료: ${result.name || name}`);
            options.pushLiveLog('success', `카테고리 수정 완료: ${result.name || name}`);
            cancelEditCategory();
            await loadCategories();
        } catch (error: any) {
            if (error?.message === '__ADMIN_CATEGORY_UNAUTHORIZED__') {
                options.handleAdminUnauthorized();
                return;
            }
            const message = error?.message || '카테고리 수정 중 오류가 발생했습니다.';
            setCategoryMessage(message);
            options.pushLiveLog('warning', message);
        } finally {
            setCategoryUpdatingId(null);
        }
    }, [cancelEditCategory, editingCategoryDescription, editingCategoryName, loadCategories, options]);

    const loadCategoryStats = useCallback(async () => {
        const token = getAdminToken();
        if (!token) return;

        const now = Date.now();
        if (now < categoryStatsRetryAfterRef.current) {
            return;
        }
        if (categoryStatsRequestInFlightRef.current || now - lastCategoryStatsLoadedAtRef.current < 3000) {
            return;
        }
        categoryStatsRequestInFlightRef.current = true;
        try {
            const result = await loadAdminCategoryStats({
                apiBaseUrl: options.apiBaseUrl,
                token,
                limit: 200,
            });
            setCategoryStats(result.stats);
            setCategoryRecentProjects(result.recentProjects);
            lastCategoryStatsLoadedAtRef.current = Date.now();
            categoryStatsRetryAfterRef.current = 0;
        } catch {
            categoryStatsRetryAfterRef.current = Date.now() + 15000;
        } finally {
            categoryStatsRequestInFlightRef.current = false;
        }
    }, [options.apiBaseUrl]);

    const deleteCategory = useCallback(async (category: CategoryItem) => {
        const token = localStorage.getItem('admin_token');
        if (!token) {
            options.handleAdminUnauthorized('관리자 로그인이 필요합니다. 다시 로그인하세요.');
            return;
        }

        if (typeof window !== 'undefined') {
            const confirmed = window.confirm(`카테고리 '${category.name}'을(를) 삭제하시겠습니까?`);
            if (!confirmed) {
                return;
            }
        }

        setCategoryDeletingId(category.id);
        setCategoryMessage('');
        try {
            const result = await deleteMarketplaceCategory({
                apiBaseUrl: options.apiBaseUrl,
                token,
                category,
            });
            setCategoryMessage(`삭제 완료: ${result.name}`);
            options.pushLiveLog('success', `카테고리 삭제 완료: ${result.name}`);
            if (selectedCategoryId === category.id) {
                setSelectedCategoryId(0);
            }
            if (editingCategoryId === category.id) {
                cancelEditCategory();
            }
            await loadCategories();
            await loadCategoryStats();
        } catch (error: any) {
            if (error?.message === '__ADMIN_CATEGORY_UNAUTHORIZED__') {
                options.handleAdminUnauthorized();
                return;
            }
            const message = error?.message || '카테고리 삭제 중 오류가 발생했습니다.';
            setCategoryMessage(message);
            options.pushLiveLog('warning', message);
        } finally {
            setCategoryDeletingId(null);
        }
    }, [cancelEditCategory, editingCategoryId, loadCategories, loadCategoryStats, options, selectedCategoryId]);

    return {
        systemSettings,
        systemSettingsDraft,
        systemSettingsOpen,
        systemSettingsLoading,
        systemSettingsSaving,
        systemAutomaticApplying,
        systemSettingsMessage,
        identityProviderSettings,
        adminPasswordCurrent,
        setAdminPasswordCurrent,
        adminPasswordNext,
        setAdminPasswordNext,
        adminPasswordConfirm,
        setAdminPasswordConfirm,
        adminPasswordChanging,
        adminPasswordMessage,
        postgresPasswordNext,
        setPostgresPasswordNext,
        postgresPasswordConfirm,
        setPostgresPasswordConfirm,
        postgresPasswordSaving,
        postgresPasswordMessage,
        categories,
        selectedCategoryId,
        setSelectedCategoryId,
        categoryStats,
        categoryRecentProjects,
        categoryName,
        setCategoryName,
        categoryDescription,
        setCategoryDescription,
        categoryCreating,
        categoryUpdatingId,
        categoryDeletingId,
        editingCategoryId,
        editingCategoryName,
        setEditingCategoryName,
        editingCategoryDescription,
        setEditingCategoryDescription,
        hideEmptyCategories,
        setHideEmptyCategories,
        categorySortBy,
        setCategorySortBy,
        categoryMessage,
        loadSystemSettings,
        changeAdminPassword,
        updatePostgresRuntimePassword,
        updateSystemSettingValue,
        toggleSystemSettingsSection,
        saveSystemSettings,
        applyGlobalAutomaticMode,
        loadCategories,
        createCategory,
        beginEditCategory,
        cancelEditCategory,
        updateCategory,
        deleteCategory,
        loadCategoryStats,
    };
}

export function assertAdminSystemCategoryControllerContract() {
    const draft = buildSystemSettingsDraft({
        env_path: '',
        runtime_config_path: '',
        sections: [],
        summary: {
            admin_domain: '',
            api_domain: '',
            local_api_base_url: '',
            marketplace_host_root: '',
            marketplace_upload_root: '',
            nginx_http_port: '',
            nginx_https_port: '',
            selected_profile: '',
            code_generation_strategy: '',
            default_model: '',
            chat_model: '',
            voice_chat_model: '',
            reasoning_model: '',
            coding_model: '',
            available_model_count: 0,
            available_models: [],
            generator_profiles: [],
        },
    });
    if (typeof draft !== 'object' || Array.isArray(draft)) {
        throw new Error('admin system category controller contract 누락: system settings draft object 필요');
    }
}
