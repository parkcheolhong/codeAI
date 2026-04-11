'use client';

import * as React from 'react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { resolveApiBaseUrl } from '@/lib/api';
import AdminAdPreviewModal from '@/components/admin/admin-ad-preview-modal';
import AdminAdOrdersSection from '@/components/admin/admin-ad-orders-section';
import AdminAutoConnectGraphPanel from '@/components/admin/admin-auto-connect-graph-panel';
import AdminCategoryManagementSection from '@/components/admin/admin-category-management-section';
import AdminDashboardOverview from '@/components/admin/admin-dashboard-overview';
import AdminCostSimulatorSection from '@/components/admin/admin-cost-simulator-section';
import AdminLlmControlSummary from '@/components/admin/admin-llm-control-summary';
import AdminManualOrchestratorSection from '@/components/admin/admin-manual-orchestrator-section';
import AdminManagementSection from '@/components/admin/admin-management-section';
import AdminQuickLinksSection from '@/components/admin/admin-quick-links-section';
import AdminSampleProductsSection from '@/components/admin/admin-sample-products-section';
import AdminStoryboardModal from '@/components/admin/admin-storyboard-modal';
import AdminSystemSettingsPanel from '@/components/admin/admin-system-settings-panel';
import WorkspaceChrome from '@/components/ui/workspace-chrome';
import { type SharedOrchestratorStageRun } from '@shared/orchestrator-stage-card-panel';
import {
    buildAdminDashboardOverviewAssembly,
    buildAdminPageHealthAnalysis,
} from '@/app/admin/admin-page-health-analysis';
import { buildAdminAutoConnectGraphAssembly } from '@/app/admin/admin-page-auto-connect-graph-assembly';
import type { AdminStageRunResponse } from '@/app/admin/admin-page-types';
import {
    buildAdminPageAdOrdersAssembly,
    buildAdminPageManualOrchestratorAssembly,
    buildAdminPageSampleProductsAssembly,
    buildAdminPageSystemSettingsAssembly,
} from '@/app/admin/admin-page-orchestrator-assemblies';
import {
    attachActiveAdminConnectionMeta,
    buildAdminAutoConnectMeta,
    readAdminAutoConnectGraphSnapshot,
    registerAdminAutoConnectGraphEvent,
} from '@/lib/admin-auto-connect';
import {
    buildCapabilityConnectionId,
    buildSettlementOrderConnectionId,
    createDashboardAutoConnectTracker,
    type AdminAutoConnectGraphSnapshot,
} from '@/lib/admin-dashboard-auto-connect';
import {
    assertAdminAdOrderFallbackContract,
    buildFallbackAdOrderMonitorSummary,
    buildFallbackAdSettlementDashboard,
} from '@/lib/admin-ad-order-fallback';
import {
    assertAdminAlertSpeechContract,
    buildAdminAlertSpeech,
    speakAdminAlert,
} from '@/lib/admin-alert-speech';
import {
    assertAdminAdProductionAnalysisContract,
    buildAdminAdProductionStages,
    getAdminAdProductionCurrentStage,
    getAdminMotionTempoLabel,
    getAdminSceneFrameHint,
} from '@/lib/admin-ad-production-analysis';
import {
    assertAdminAutoConnectServiceContract,
} from '@/lib/admin-auto-connect-service';
import { assertAdminOrchestratorBridgeContract } from '@/lib/admin-orchestrator-bridge';
import {
    assertAdminAdOrderActionsContract,
} from '@/lib/admin-ad-order-actions';
import {
    assertAdminAdReviewStateContract,
} from '@/lib/admin-ad-review-state';
import {
    assertAdminCategoryServiceContract,
} from '@/lib/admin-category-service';
import {
    assertAdminRuntimeTypesContract,
    type AdminCostSimulatorResponse,
    type LiveLogItem,
    type LlmStatus,
} from '@/lib/admin-runtime-types';
import {
    assertAdminSampleProductServiceContract,
} from '@/lib/admin-sample-product-service';
import {
    assertAdminHealthAnalysisContract,
    formatHealthMetricLabel,
    formatHealthMetricValue,
    getHealthAlertMetrics,
    getHealthAlertRootCause,
    type HealthStatus,
} from '@/lib/admin-health-analysis';
import {
    assertAdminSystemSettingsServiceContract,
} from '@/lib/admin-system-settings-service';
import {
    assertAdminManualOrchestratorContract,
} from '@/lib/admin-manual-orchestrator';
import {
    assertAdminManualOrchestratorControllerContract,
    useAdminManualOrchestratorController,
} from '@/lib/use-admin-manual-orchestrator-controller';
import {
    assertAdminAdOperationsControllerContract,
    useAdminAdOperationsController,
} from '@/lib/use-admin-ad-operations-controller';
import {
    assertAdminSystemCategoryControllerContract,
    useAdminSystemCategoryController,
} from '@/lib/use-admin-system-category-controller';
import {
    assertAdminSampleProductsControllerContract,
    useAdminSampleProductsController,
} from '@/lib/use-admin-sample-products-controller';
import {
    assertAdminAutoConnectControllerContract,
    useAdminAutoConnectController,
} from '@/lib/use-admin-auto-connect-controller';
import { assertAdminManualWorklogContract } from '@/lib/admin-manual-worklog';
import {
    assertAdminDashboardTypesContract,
    type AdminAdOrderMonitorSummary,
    type AdminAdOrderSettlementDashboard,
    type AdminAdVideoOrderItem,
    type AdminDashboardSelfRunStatus,
    type AutoRecoveryHistoryItem,
    type FocusedSelfHealingApplyResult,
    type FocusedSelfHealingPlan,
    type OrchestratorCapabilityDetailResponse,
    type OrchestratorCapabilitySummaryResponse,
    type OverviewStats,
    type RevenueStats,
    type TopProject,
} from '@/lib/admin-dashboard-types';
import {
    assertAdminDashboardUiTypesContract,
} from '@/lib/admin-dashboard-ui-types';
import {
    downloadCsvFromRows,
    formatCurrency,
    getOrchestratorActionGuide,
    normalizeStoredLiveLog,
    normalizeSystemSettingsMessage,
    pickPreferredModel,
    toFileHref,
} from '@/lib/admin-dashboard-page-helpers';
import {
    ADMIN_ACTION_TEMPLATE_LABELS,
    ADMIN_ALERT_VOICE_ENABLED_STORAGE_KEY,
    ADMIN_AUTO_RECOVERY_HISTORY_STORAGE_KEY,
    ADMIN_CATEGORY_SORT_STORAGE_KEY,
    ADMIN_DASHBOARD_PREFERENCES_STORAGE_KEY,
    ADMIN_HIDE_EMPTY_CATEGORIES_STORAGE_KEY,
    ADMIN_HUMAN_OBJECT_INTERACTION_RULES,
    ADMIN_LIVE_LOGS_STORAGE_KEY,
    ADMIN_MANUAL_ORCHESTRATOR_META_STORAGE_KEY,
    ADMIN_MANUAL_ORCHESTRATOR_STAGE_RUN_ID_STORAGE_KEY,
    ADMIN_MANUAL_ORCHESTRATOR_STATE_STORAGE_KEY,
    ADMIN_SAMPLE_SETTINGS_STORAGE_KEY,
    ADMIN_SYSTEM_SETTINGS_STATUS_SECTIONS,
    GENERATOR_ENV_KEY_MAP,
    OPTIMIZED_GENERATOR_DEFAULTS,
    OPTIMIZED_RUNTIME_ROUTE_ENV_MAP,
    OPTIMIZED_RUNTIME_ROUTE_PRESETS,
} from '@/lib/admin-dashboard-page-constants';
import {
    assertAdminApiGuardContract,
    buildApiErrorMessage,
    clearAdminApiBackoff,
    isAdminApiBackoffActive,
    setAdminApiBackoff,
} from '@/lib/admin-api-guard';
import {
    assertAdminDashboardBootstrapContract,
} from '@/lib/admin-dashboard-bootstrap';
import {
    assertAdminBootstrapFetchContract,
    fetchWithAdminBootstrapRetry,
} from '@/lib/admin-bootstrap-fetch';
import {
    assertAdminDashboardControllerContract,
    loadAdminDashboardController,
} from '@/lib/admin-dashboard-controller';
import {
    bindAutoConnectGraphSnapshot,
    refreshAdminStageRunAction,
    runCostSimulationAction,
    updateAdminStageStatusAction,
} from '@/lib/admin-dashboard-actions';
import {
    assertAdminDashboardStateAssemblerContract,
} from '@/lib/admin-dashboard-state-assembler';
import {
    assertAdminDashboardSnapshotContract,
    type AdminDashboardSnapshot,
} from '@/lib/admin-dashboard-snapshot';
import {
    assertAdminAutoRecoveryContract,
    executeAdminAutomaticRecovery,
    shouldRunSelfRunAutoNormalization,
} from '@/lib/admin-auto-recovery';
import { assertAdminSelfRunAnalysisContract } from '@/lib/admin-self-run-analysis';
import {
    assertAdminSelfRunControlContract,
    approveWorkspaceSelfRunRequest,
    normalizeWorkspaceSelfRunRequest,
    retryWorkspaceSelfRunRequest,
} from '@/lib/admin-self-run-control';
import {
    ADMIN_SESSION_CHECK_INTERVAL_MS,
    ADMIN_SESSION_WARNING_WINDOW_MS,
    clearAdminToken,
    extendAdminSessionToken,
    getAdminToken,
    getAdminTokenExpiryMs,
    getRemainingSessionMinutes,
} from '@/lib/admin-session';
import { hardRedirectToAdminLogin, redirectToAdminLogin } from '@/lib/admin-navigation';

assertAdminAdOrderFallbackContract();
assertAdminAlertSpeechContract();
assertAdminAdProductionAnalysisContract();
assertAdminAutoConnectServiceContract();
assertAdminOrchestratorBridgeContract();
assertAdminAdOrderActionsContract();
assertAdminAdReviewStateContract();
assertAdminCategoryServiceContract();
assertAdminHealthAnalysisContract();
assertAdminRuntimeTypesContract();
assertAdminSampleProductServiceContract();
assertAdminSystemSettingsServiceContract();
assertAdminManualOrchestratorContract();
assertAdminManualOrchestratorControllerContract();
assertAdminAdOperationsControllerContract();
assertAdminSystemCategoryControllerContract();
assertAdminSampleProductsControllerContract();
assertAdminAutoConnectControllerContract();
assertAdminManualWorklogContract();
assertAdminDashboardTypesContract();
assertAdminDashboardUiTypesContract();
assertAdminApiGuardContract();
assertAdminBootstrapFetchContract();
assertAdminDashboardBootstrapContract();
assertAdminDashboardControllerContract();
assertAdminDashboardStateAssemblerContract();
assertAdminDashboardSnapshotContract();
assertAdminAutoRecoveryContract();
assertAdminSelfRunAnalysisContract();
assertAdminSelfRunControlContract();


const initialOverview: OverviewStats = { projects: 0, users: 0, purchases: 0, reviews: 0 };
const initialRevenue: RevenueStats = { total_revenue: 0, total_purchases: 0, average_purchase_amount: 0 };

export default function AdminDashboardPage() {
    const router = useRouter();
    const apiBaseUrl = resolveApiBaseUrl();
    const adminCategoriesBootstrappedRef = useRef(false);
    const adminCategoryStatsBootstrappedRef = useRef(false);
    const apiDocsHref = (() => {
        try {
            return new URL('/docs', apiBaseUrl).toString();
        } catch {
            return '/docs';
        }
    })();

    const [authChecked, setAuthChecked] = useState(false);
    const [authStatusMessage, setAuthStatusMessage] = useState('인증 확인 중...');
    const [adminUser, setAdminUser] = useState<{ username: string; email: string } | null>(null);
    const [loading, setLoading] = useState(true);
    const [refreshing, setRefreshing] = useState(false);
    const [lastUpdated, setLastUpdated] = useState<string>('');
    const [error, setError] = useState<string | null>(null);
    const [overview, setOverview] = useState<OverviewStats>(initialOverview);
    const [revenue, setRevenue] = useState<RevenueStats>(initialRevenue);
    const [topProjects, setTopProjects] = useState<TopProject[]>([]);
    const [health, setHealth] = useState<HealthStatus | null>(null);
    const [llmStatus, setLlmStatus] = useState<LlmStatus | null>(null);
    const [projectQuery, setProjectQuery] = useState('');
    const [topProjectsOpen, setTopProjectsOpen] = useState(true);
    const [adOrdersOpen, setAdOrdersOpen] = useState(false);
    const [autoRefreshEnabled, setAutoRefreshEnabled] = useState(true);
    const [refreshSeconds, setRefreshSeconds] = useState(20);
    const [liveLogs, setLiveLogs] = useState<LiveLogItem[]>([]);
    const [autoConnectGraph, setAutoConnectGraph] = useState<AdminAutoConnectGraphSnapshot>({ active_connection_id: '', events: [] });
    const [adVideoOrders, setAdVideoOrders] = useState<AdminAdVideoOrderItem[]>([]);
    const [adVideoTotal, setAdVideoTotal] = useState(0);
    const [adOrderMonitorSummary, setAdOrderMonitorSummary] = useState<AdminAdOrderMonitorSummary | null>(null);
    const [adSettlementDashboard, setAdSettlementDashboard] = useState<AdminAdOrderSettlementDashboard | null>(null);
    const [costSimulatorPanelOpen, setCostSimulatorPanelOpen] = useState(false);
    const [costSimulatorLoading, setCostSimulatorLoading] = useState(false);
    const [costSimulatorError, setCostSimulatorError] = useState('');
    const [costSimulatorResult, setCostSimulatorResult] = useState<AdminCostSimulatorResponse | null>(null);
    const [costSimulatorForm, setCostSimulatorForm] = useState({
        monthly_orders: 100,
        cuts_per_order: 8,
        preview_runs_per_order: 3,
        approved_external_cuts_per_order: 8,
        candidates_per_cut: 1.0,
        retry_rate: 0.25,
        external_image_unit_cost: 0.12,
        external_video_unit_cost: 0.0,
        external_video_ratio: 0.0,
        local_preview_unit_cost: 0.01,
        local_stitch_unit_cost: 0.02,
        storage_unit_cost: 0.005,
        premium_ratio: 0.2,
        currency: 'USD',
    });

    const [orchestratorCapabilitySummary, setOrchestratorCapabilitySummary] = useState<OrchestratorCapabilitySummaryResponse | null>(null);
    const [securityGuardDetail, setSecurityGuardDetail] = useState<OrchestratorCapabilityDetailResponse | null>(null);
    const [dashboardSelfRunStatus, setDashboardSelfRunStatus] = useState<AdminDashboardSelfRunStatus | null>(null);
    const [voiceAlertEnabled, setVoiceAlertEnabled] = useState(true);
    const [llmPanelHeight, setLlmPanelHeight] = useState(1800);
    const [systemSettingsPanelOpen, setSystemSettingsPanelOpen] = useState(false);
    const [liveLogsPanelOpen, setLiveLogsPanelOpen] = useState(false);
    const [autoConnectGraphPanelOpen, setAutoConnectGraphPanelOpen] = useState(false);
    const [customerOrchestratorPanelOpen, setCustomerOrchestratorPanelOpen] = useState(false);
    const [topProjectsPanelOpen, setTopProjectsPanelOpen] = useState(false);
    const [adminControlHubOpen, setAdminControlHubOpen] = useState(false);
    const [healthOverviewOpen, setHealthOverviewOpen] = useState(false);
    const [adOrdersPanelOpen, setAdOrdersPanelOpen] = useState(false);
    const [categoryPanelOpen, setCategoryPanelOpen] = useState(false);
    const [quickLinksPanelOpen, setQuickLinksPanelOpen] = useState(false);
    const [llmControlPanelOpen, setLlmControlPanelOpen] = useState(false);
    const [samplePanelOpen, setSamplePanelOpen] = useState(false);
    const [generatorModelOverrides, setGeneratorModelOverrides] = useState<Record<string, string>>({});
    const [adminStageRun, setAdminStageRun] = useState<AdminStageRunResponse | null>(null);
    const [adminStageNoteDraft, setAdminStageNoteDraft] = useState('');
    const [adminStageSubstepChecks, setAdminStageSubstepChecks] = useState<Record<string, boolean>>({});
    const [adminStageRevisionNote, setAdminStageRevisionNote] = useState('');
    const [adminStageUpdateLoading, setAdminStageUpdateLoading] = useState(false);
    const latestDedicatedOrder = useMemo(
        () => adVideoOrders.find((order) => order.engine_type === 'dedicated_engine') || null,
        [adVideoOrders],
    );
    const latestDedicatedProductionStages = useMemo(
        () => buildAdminAdProductionStages(latestDedicatedOrder),
        [latestDedicatedOrder],
    );
    const latestDedicatedReadyCount = useMemo(
        () => latestDedicatedProductionStages.filter((stage) => stage.ready).length,
        [latestDedicatedProductionStages],
    );
    const latestDedicatedCurrentStage = useMemo(
        () => getAdminAdProductionCurrentStage(latestDedicatedProductionStages),
        [latestDedicatedProductionStages],
    );
    const latestDedicatedWorkReady = latestDedicatedProductionStages.length > 0 && latestDedicatedProductionStages.every((stage) => stage.ready);
    const updateCostSimulatorField = (key: keyof typeof costSimulatorForm, value: string) => {
        setCostSimulatorForm((prev) => ({
            ...prev,
            [key]: key === 'currency' ? value : Number(value),
        }));
    };
    const [autoOpsEnabled, setAutoOpsEnabled] = useState(true);
    const [autoOpsLastExecutedAt, setAutoOpsLastExecutedAt] = useState<string>('');
    const [autoRecoveryHistory, setAutoRecoveryHistory] = useState<AutoRecoveryHistoryItem[]>([]);
    const [autoRecoveryRunning, setAutoRecoveryRunning] = useState(false);
    const [selfRunApproving, setSelfRunApproving] = useState(false);
    const [selfRunRetrying, setSelfRunRetrying] = useState(false);
    const [selfRunNormalizing, setSelfRunNormalizing] = useState(false);
    const [focusedSelfHealingBusy, setFocusedSelfHealingBusy] = useState(false);
    const [focusedSelfHealingModalOpen, setFocusedSelfHealingModalOpen] = useState(false);
    const [focusedSelfHealingRequestedPath, setFocusedSelfHealingRequestedPath] = useState('frontend/frontend/app/admin/page.tsx');
    const [focusedSelfHealingReason, setFocusedSelfHealingReason] = useState('health score contract mismatch');
    const [focusedSelfHealingPlan, setFocusedSelfHealingPlan] = useState<FocusedSelfHealingPlan | null>(null);
    const [focusedSelfHealingApplyResult, setFocusedSelfHealingApplyResult] = useState<FocusedSelfHealingApplyResult | null>(null);
    const [focusedSelfHealingApprovalConfirmed, setFocusedSelfHealingApprovalConfirmed] = useState(false);
    const [focusedSelfHealingSelectedOptionId, setFocusedSelfHealingSelectedOptionId] = useState('');
    const [focusedSelfHealingMessage, setFocusedSelfHealingMessage] = useState('');
    const [capabilityBootstrapReady, setCapabilityBootstrapReady] = useState(false);
    const snapshotRef = useRef<AdminDashboardSnapshot | null>(null);
    const sessionWarningExpRef = useRef<number | null>(null);
    const selfRunNormalizationRef = useRef<string>('');
    const selfRunApiUnavailableRef = useRef(false);
    const autoConnectGraphApiUnavailableRef = useRef(false);
    const adMonitorApiUnavailableRef = useRef(false);
    const adSettlementApiUnavailableRef = useRef(false);
    const lastSpokenAlertSignatureRef = useRef('');
    const autoOpsSignatureRef = useRef('');
    const pushLiveLog = useCallback((level: LiveLogItem['level'], message: string, meta?: Partial<LiveLogItem> & { capabilityId?: string }) => {
        const connectionMeta = meta?.connection_id
            ? {
                connection_id: meta.connection_id,
                flow_id: meta.flow_id || '',
                step_id: meta.step_id || '',
                action: meta.action || '',
                panel_id: meta.panel_id || 'PANEL-ADMIN-DASHBOARD',
            }
            : attachActiveAdminConnectionMeta({
                fallbackCapabilityId: meta?.capabilityId || 'dashboard',
                panelId: meta?.panel_id || 'PANEL-ADMIN-DASHBOARD',
                execution: 'observe',
            });
        setLiveLogs((prev) => [
            {
                id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
                level,
                message,
                createdAt: new Date().toLocaleTimeString('ko-KR'),
                connection_id: connectionMeta.connection_id,
                flow_id: connectionMeta.flow_id,
                step_id: connectionMeta.step_id,
                action: connectionMeta.action,
                panel_id: connectionMeta.panel_id,
            },
            ...prev,
        ].slice(0, 30));
        registerAdminAutoConnectGraphEvent({
            meta: {
                ...connectionMeta,
                route_id: meta?.route_id || 'ROUTE-DASHBOARD',
                capability_id: meta?.capabilityId || 'dashboard',
                command_id: connectionMeta.connection_id,
            },
            source: meta?.capabilityId === 'settlement' ? 'settlement' : meta?.capabilityId ? 'orchestrator' : 'admin-dashboard',
            title: '관리자 라이브로그',
            detail: message,
            status: level === 'warning' ? 'warning' : level === 'success' ? 'success' : 'linked',
            activate: false,
        });
    }, []);
    const {
        adminManualOrchestratorStepId,
        setAdminManualOrchestratorStepId,
        adminManualMeta,
        setAdminManualMeta,
        selectedAdminManualStep,
        selectedAdminManualStepState,
        previousAdminManualStep,
        nextAdminManualStep,
        completedManualStepCount,
        toggleAdminManualAction,
        toggleAdminManualStepCompleted,
        updateAdminManualStepNote,
        updateAdminManualStepField,
        addAdminManualAttachmentLink,
        removeAdminManualAttachmentLink,
        updateAdminManualStepRouteStage,
        updateAdminManualStepDuration,
        updateAdminManualExternalStageMirror,
        moveAdminManualStep,
        downloadAdminManualWorklog,
        openMarketplaceOrchestratorBridge,
        openAdminLlmOrchestratorBridge,
    } = useAdminManualOrchestratorController({
        storageKey: ADMIN_MANUAL_ORCHESTRATOR_STATE_STORAGE_KEY,
        metaStorageKey: ADMIN_MANUAL_ORCHESTRATOR_META_STORAGE_KEY,
        initialStepId: 'ARCH-001',
        latestDedicatedOrder,
        onOpenAdminLlm: () => router.push('/admin/llm'),
        onOpenMarketplaceOrchestrator: () => router.push('/marketplace/orchestrator'),
        pushLiveLog: (level, message) => pushLiveLog(level, message),
    });

    const {
        expandedAdReviewOrderId,
        adReviewDrafts,
        adReviewDiffOnly,
        adReviewStatusDiffOnly,
        adReviewNoteDiffOnly,
        adReviewSavingId,
        adStoryboardModal,
        setAdStoryboardModal,
        adPreviewLoadingId,
        adRetryingId,
        adPreviewOrder,
        adPreviewUrl,
        adPreviewError,
        adSettlementExporting,
        closeAdPreview,
        handlePreviewAdOrder,
        handleDownloadAdOrder,
        handleRetryAdOrder,
        openAdReviewPanel,
        updateAdReviewDraft,
        toggleAdReviewDiffOnly,
        toggleAdReviewStatusDiffOnly,
        toggleAdReviewNoteDiffOnly,
        resetAdReviewFilters,
        matchesAdReviewSceneFilter,
        moveAdStoryboardModalCut,
        currentAdStoryboardModalDiff,
        currentAdStoryboardModalIndex,
        handleSaveAdReview,
        exportAdSettlementCsv,
        buildSettlementConnectionId,
    } = useAdminAdOperationsController({
        apiBaseUrl,
        adVideoOrders,
        adSettlementDashboard,
        buildApiErrorMessage,
        handleAdminUnauthorized: (...args) => handleAdminUnauthorized(...args),
        loadDashboard: (...args) => loadDashboard(...args),
        pushLiveLog: (...args) => pushLiveLog(...args),
        downloadCsvFromRows,
        adSettlementApiUnavailableRef,
    });
    const {
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
    } = useAdminSystemCategoryController({
        apiBaseUrl,
        handleAdminUnauthorized: (...args) => handleAdminUnauthorized(...args),
        normalizeSystemSettingsMessage,
        pushLiveLog: (level, message) => pushLiveLog(level, message),
        setAutoRefreshEnabled,
        setRefreshSeconds,
        hideEmptyStorageKey: ADMIN_HIDE_EMPTY_CATEGORIES_STORAGE_KEY,
        categorySortStorageKey: ADMIN_CATEGORY_SORT_STORAGE_KEY,
    });
    const {
        sampleTemplates,
        sampleCreating,
        sampleResult,
        sampleBatchCount,
        setSampleBatchCount,
        sampleCleanupPattern,
        setSampleCleanupPattern,
        selectedCategoryStat,
        selectedCategoryDelta,
        createSampleProduct,
        createBatchSamples,
        runSampleCleanup,
    } = useAdminSampleProductsController({
        apiBaseUrl,
        categories,
        selectedCategoryId,
        setSelectedCategoryId,
        categoryStats,
        handleAdminUnauthorized: (...args) => handleAdminUnauthorized(...args),
        loadDashboard: (...args) => loadDashboard(...args),
        loadCategoryStats,
        pushLiveLog: (level, message) => pushLiveLog(level, message),
        settingsStorageKey: ADMIN_SAMPLE_SETTINGS_STORAGE_KEY,
    });
    const {
        adminCompletionHistory,
        adminTraceHistory,
        adminRetryQueueItems,
        adminConnectionLookupId,
        setAdminConnectionLookupId,
        adminConnectionLookupLoading,
        adminConnectionLookupResult,
        adminTraceFilter,
        setAdminTraceFilter,
        adminReplayQueueId,
        loadAdminCompletionHistory,
        loadAdminTraceHistory,
        loadAdminRetryQueue,
        loadAdminConnectionLookup,
        handleReplayRetryQueue,
        filteredAdminCompletionHistory,
        filteredAdminTraceHistory,
        filteredAdminRetryQueueItems,
    } = useAdminAutoConnectController({
        apiBaseUrl,
        authChecked,
        activeConnectionId: autoConnectGraph.active_connection_id || '',
        apiUnavailableRef: autoConnectGraphApiUnavailableRef,
        handleAdminUnauthorized: (...args) => handleAdminUnauthorized(...args),
        setAdminApiBackoff,
        pushLiveLog: (level, message) => pushLiveLog(level, message),
        setError,
    });
    useEffect(() => {
        try {
            localStorage.setItem(ADMIN_AUTO_RECOVERY_HISTORY_STORAGE_KEY, JSON.stringify(autoRecoveryHistory));
        } catch {
        }
    }, [autoRecoveryHistory]);

    useEffect(() => {
        const handleMessage = (event: MessageEvent) => {
            if (event.origin !== window.location.origin) {
                return;
            }
            if (!event.data || event.data.type !== 'admin-llm-frame-height') {
                return;
            }
            const nextHeight = Number(event.data.height);
            if (!Number.isFinite(nextHeight)) {
                return;
            }
            setLlmPanelHeight(Math.max(1400, Math.min(Math.trunc(nextHeight) + 24, 8000)));
        };

        window.addEventListener('message', handleMessage);
        return () => {
            window.removeEventListener('message', handleMessage);
        };
    }, []);

    useEffect(() => {
        try {
            const storedLogsRaw = localStorage.getItem(ADMIN_LIVE_LOGS_STORAGE_KEY);
            if (storedLogsRaw) {
                const parsedLogs = JSON.parse(storedLogsRaw) as LiveLogItem[];
                if (Array.isArray(parsedLogs)) {
                    setLiveLogs(
                        parsedLogs
                            .map(normalizeStoredLiveLog)
                            .filter((item): item is LiveLogItem => item !== null)
                            .slice(0, 30)
                    );
                }
            }

            const storedVoiceAlertEnabled = localStorage.getItem(ADMIN_ALERT_VOICE_ENABLED_STORAGE_KEY);
            if (storedVoiceAlertEnabled === 'false') {
                setVoiceAlertEnabled(false);
            }

            const storedDashboardPreferencesRaw = localStorage.getItem(ADMIN_DASHBOARD_PREFERENCES_STORAGE_KEY);
            if (storedDashboardPreferencesRaw) {
                const preferences = JSON.parse(storedDashboardPreferencesRaw) as {
                    refreshSeconds?: number;
                    autoRefreshEnabled?: boolean;
                };
                if (typeof preferences.refreshSeconds === 'number') {
                    setRefreshSeconds(Math.max(5, Math.min(300, preferences.refreshSeconds)));
                }
                if (typeof preferences.autoRefreshEnabled === 'boolean') {
                    setAutoRefreshEnabled(preferences.autoRefreshEnabled);
                }
            }

            const storedAutoRecoveryHistoryRaw = localStorage.getItem(ADMIN_AUTO_RECOVERY_HISTORY_STORAGE_KEY);
            if (storedAutoRecoveryHistoryRaw) {
                const parsedHistory = JSON.parse(storedAutoRecoveryHistoryRaw) as AutoRecoveryHistoryItem[];
                if (Array.isArray(parsedHistory)) {
                    setAutoRecoveryHistory(parsedHistory.slice(0, 20));
                }
            }
        } catch {
        }
    }, []);

    useEffect(() => {
        try {
            localStorage.setItem(ADMIN_LIVE_LOGS_STORAGE_KEY, JSON.stringify(liveLogs.slice(0, 30)));
        } catch {
        }
    }, [liveLogs]);

    useEffect(() => {
        try {
            localStorage.setItem(
                ADMIN_ALERT_VOICE_ENABLED_STORAGE_KEY,
                voiceAlertEnabled ? 'true' : 'false'
            );
        } catch {
        }
    }, [voiceAlertEnabled]);

    useEffect(() => {
        try {
            localStorage.setItem(
                ADMIN_DASHBOARD_PREFERENCES_STORAGE_KEY,
                JSON.stringify({
                    refreshSeconds,
                    autoRefreshEnabled,
                }),
            );
        } catch {
        }
    }, [autoRefreshEnabled, refreshSeconds]);

    useEffect(() => {
        const controller = new AbortController();
        const token = getAdminToken();
        const authUrl = '/api/proxy';
        if (!token) {
            setAuthStatusMessage('로그인 페이지로 이동 중...');
            redirectToAdminLogin(router);
            return () => {
                controller.abort();
            };
        }
        setAuthChecked(false);
        setAuthStatusMessage('관리자 인증 확인 중...');
        fetchWithAdminBootstrapRetry(authUrl, {
            headers: { Authorization: `Bearer ${token}` },
            signal: controller.signal,
            cache: 'no-store',
        }, {
            retries: 1,
            retryDelayMs: 500,
            timeoutMs: 8000,
        })
            .then(async (response) => {
                if (!response.ok) {
                    return null;
                }
                return response.json();
            })
            .then((me) => {
                if (!me || (!me.is_admin && !me.is_superuser)) {
                    setAuthStatusMessage('로그인 페이지로 이동 중...');
                    clearAdminToken();
                    setAdminUser(null);
                    setAuthChecked(false);
                    redirectToAdminLogin(router);
                    return;
                }
                setAdminUser({ username: me.username, email: me.email });
                setAuthStatusMessage('관리자 인증 확인 완료');
                setAuthChecked(true);
            })
            .catch(() => {
                setAuthStatusMessage('인증 확인 실패, 로그인 페이지로 이동 중...');
                clearAdminToken();
                setAdminUser(null);
                setAuthChecked(false);
                redirectToAdminLogin(router);
            });

        return () => {
            controller.abort();
        };
    }, [router, apiBaseUrl]);

    const handleLogout = () => {
        clearAdminToken();
        hardRedirectToAdminLogin();
    };

    const handleAdminUnauthorized = useCallback((message = '관리자 세션이 만료되었습니다. 다시 로그인하세요.') => {
        clearAdminToken();
        setAdminUser(null);
        setAuthChecked(false);
        setAuthStatusMessage(message);
        setError(null);
        setAdVideoOrders([]);
        setAdVideoTotal(0);
        hardRedirectToAdminLogin();
    }, []);

    const refreshAdminStageRun = useCallback((runId: string) => refreshAdminStageRunAction({
        apiBaseUrl,
        runId,
        onUnauthorized: () => handleAdminUnauthorized(),
        setAdminStageRun,
        setAdminStageSubstepChecks,
    }), [apiBaseUrl, handleAdminUnauthorized]);

    const updateAdminStageStatus = useCallback((status: 'passed' | 'failed' | 'manual_correction') => updateAdminStageStatusAction({
        apiBaseUrl,
        adminStageRun,
        adminStageNoteDraft,
        adminStageRevisionNote,
        adminStageSubstepChecks,
        status,
        onUnauthorized: () => handleAdminUnauthorized(),
        pushLiveLog,
        setAdminStageRun,
        setAdminStageSubstepChecks,
        setAdminStageRevisionNote,
        setAdminStageNoteDraft,
        setAdminStageUpdateLoading,
    }), [adminStageNoteDraft, adminStageRevisionNote, adminStageRun, adminStageSubstepChecks, apiBaseUrl, handleAdminUnauthorized, pushLiveLog]);

    useEffect(() => {
        if (!adminStageRun?.current_stage_id) {
            return;
        }
        setAdminManualOrchestratorStepId(adminStageRun.current_stage_id);
    }, [adminStageRun?.current_stage_id, setAdminManualOrchestratorStepId]);

    useEffect(() => {
        const syncRefinerFixerStage = async () => {
            const token = getAdminToken();
            if (!token) {
                return;
            }
            try {
                const storedRunId = typeof window !== 'undefined'
                    ? localStorage.getItem(ADMIN_MANUAL_ORCHESTRATOR_STAGE_RUN_ID_STORAGE_KEY) || ''
                    : '';
                const existingStageRun = storedRunId
                    ? await refreshAdminStageRun(storedRunId)
                    : null;
                let stageRun = existingStageRun;
                if (!stageRun) {
                    const response = await fetchWithAdminBootstrapRetry(`${apiBaseUrl}/api/marketplace/customer-orchestrate/stage-runs`, {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json',
                            Authorization: `Bearer ${token}`,
                        },
                        body: JSON.stringify({
                            task: 'admin refiner/fixer stage mirror probe',
                            mode: 'manual_9step',
                            project_name: 'admin-refiner-fixer-stage-probe',
                        }),
                    });
                    if (response.status === 401 || response.status === 403) {
                        handleAdminUnauthorized();
                        return;
                    }
                    const payload = await response.json().catch(() => null);
                    if (!response.ok || !payload) {
                        return;
                    }
                    const createdStageRun = payload as AdminStageRunResponse;
                    stageRun = createdStageRun;
                    setAdminStageRun(createdStageRun);
                    const activeStagePayload = (createdStageRun.stages || []).find((stage) => stage.id === createdStageRun.current_stage_id);
                    const checks = Object.fromEntries(((activeStagePayload?.substeps || []).map((item) => [item.id, Boolean(item.checked)])));
                    setAdminStageSubstepChecks(checks);
                    if (typeof window !== 'undefined' && createdStageRun.run_id) {
                        localStorage.setItem(ADMIN_MANUAL_ORCHESTRATOR_STAGE_RUN_ID_STORAGE_KEY, createdStageRun.run_id);
                    }
                }
                if (!stageRun) {
                    return;
                }
                const refinerFixerStage = Array.isArray(stageRun?.stages)
                    ? stageRun.stages.find((stage: { id?: string }) => stage?.id === 'ARCH-0045')
                    : null;
                if (!stageRun?.run_id || !refinerFixerStage) {
                    return;
                }
                updateAdminManualExternalStageMirror({
                    stageRunId: String(stageRun.run_id),
                    stageId: 'ARCH-0045',
                    status: String(refinerFixerStage.status || ''),
                    label: String(refinerFixerStage.label || ''),
                    title: String(refinerFixerStage.title || ''),
                    summary: String(refinerFixerStage.summary || ''),
                    updatedAt: String(refinerFixerStage.updated_at || stageRun.updated_at || ''),
                });
            } catch {
            }
        };
        void syncRefinerFixerStage();
    }, [apiBaseUrl, handleAdminUnauthorized, refreshAdminStageRun, updateAdminManualExternalStageMirror]);

    const runCostSimulation = useCallback(() => runCostSimulationAction({
        apiBaseUrl,
        costSimulatorForm,
        onUnauthorized: () => handleAdminUnauthorized(),
        setCostSimulatorLoading,
        setCostSimulatorError,
        setCostSimulatorResult,
    }), [apiBaseUrl, costSimulatorForm, handleAdminUnauthorized]);

    const trackDashboardAutoConnect = useMemo(() => createDashboardAutoConnectTracker({
        registerEvent: registerAdminAutoConnectGraphEvent,
        attachMeta: attachActiveAdminConnectionMeta,
        buildMeta: buildAdminAutoConnectMeta,
    }), []);
    const retryWorkspaceSelfRun = useCallback(async (targetStage: 'diagnosis' | 'remediation' = 'remediation', sourcePath?: string | null) => {
        const token = localStorage.getItem('admin_token');
        if (!token) {
            handleAdminUnauthorized('관리자 로그인이 필요합니다. 다시 로그인하세요.');
            return null;
        }

        setSelfRunRetrying(true);
        try {
            return await retryWorkspaceSelfRunRequest({
                apiBaseUrl,
                token,
                approvalId: dashboardSelfRunStatus?.approval_id || null,
                sourcePath: sourcePath || null,
                targetStage,
                buildApiErrorMessage,
                onUnauthorized: () => handleAdminUnauthorized(),
                onUnsupported: (message) => {
                    if (!selfRunApiUnavailableRef.current) {
                        pushLiveLog('warning', message);
                    }
                },
                onSuccess: (message) => pushLiveLog('success', message),
                onWarning: (message) => pushLiveLog('warning', message),
                setUnavailable: (nextValue) => {
                    selfRunApiUnavailableRef.current = nextValue;
                },
            });
        } catch (error: any) {
            pushLiveLog('warning', error?.message || 'self-run 재시도에 실패했습니다.');
            return null;
        } finally {
            setSelfRunRetrying(false);
        }
    }, [apiBaseUrl, dashboardSelfRunStatus?.approval_id, handleAdminUnauthorized]);

    const normalizeWorkspaceSelfRun = useCallback(async (cleanupOnly = false) => {
        const token = localStorage.getItem('admin_token');
        if (!token) {
            handleAdminUnauthorized('관리자 로그인이 필요합니다. 다시 로그인하세요.');
            return null;
        }

        setSelfRunNormalizing(true);
        try {
            return await normalizeWorkspaceSelfRunRequest({
                apiBaseUrl,
                token,
                approvalId: dashboardSelfRunStatus?.approval_id || null,
                cleanupOnly,
                buildApiErrorMessage,
                onUnauthorized: () => handleAdminUnauthorized(),
                onUnsupported: (message) => {
                    if (!selfRunApiUnavailableRef.current) {
                        pushLiveLog('warning', message);
                    }
                },
                onSuccess: (message) => pushLiveLog('success', message),
                onWarning: (message) => pushLiveLog('warning', message),
                setUnavailable: (nextValue) => {
                    selfRunApiUnavailableRef.current = nextValue;
                },
            });
        } catch (error: any) {
            pushLiveLog('warning', error?.message || 'self-run 정상화에 실패했습니다.');
            return null;
        } finally {
            setSelfRunNormalizing(false);
        }
    }, [apiBaseUrl, dashboardSelfRunStatus?.approval_id, handleAdminUnauthorized]);

    const loadDashboard = useCallback(async (isRefresh = false) => {
        if (isRefresh) setRefreshing(true);
        else setLoading(true);
        setError(null);
        if (isRefresh && !capabilityBootstrapReady) {
            setCapabilityBootstrapReady(true);
        }
        autoConnectGraphApiUnavailableRef.current = isAdminApiBackoffActive('auto-connect-graph');
        adMonitorApiUnavailableRef.current = isAdminApiBackoffActive('ad-video-orders-monitor-summary');
        adSettlementApiUnavailableRef.current = isAdminApiBackoffActive('ad-video-orders-settlement-dashboard');
        trackDashboardAutoConnect({
            capabilityId: 'dashboard-sync',
            title: isRefresh ? '관리자 대시보드 새로고침' : '관리자 대시보드 초기 동기화',
            detail: isRefresh ? '관리자 상태 재수집 요청' : '관리자 초기 상태 동기화 요청',
            panelId: 'PANEL-ADMIN-DASHBOARD',
            status: 'queued',
            execution: 'sync',
        });

        const token = localStorage.getItem('admin_token');
        if (!token) {
            handleAdminUnauthorized('관리자 로그인이 필요합니다. 다시 로그인하세요.');
            if (isRefresh) setRefreshing(false);
            else setLoading(false);
            return;
        }
        const controllerResult = await loadAdminDashboardController({
            apiBaseUrl,
            token,
            previousSnapshot: snapshotRef.current,
            currentOverview: overview,
            currentRevenue: revenue,
            currentHealth: health,
            currentLlmStatus: llmStatus,
            adMonitorUnavailable: adMonitorApiUnavailableRef.current,
            adSettlementUnavailable: adSettlementApiUnavailableRef.current,
            includeCapabilityBootstrap: isRefresh || capabilityBootstrapReady,
            formatCurrency,
            buildFallbackAdOrderMonitorSummary,
            buildFallbackAdSettlementDashboard,
        });
        if (controllerResult.unauthorized) {
            handleAdminUnauthorized();
            return;
        }
        if (controllerResult.adMonitorUnavailable) {
            setAdminApiBackoff('ad-video-orders-monitor-summary');
            adMonitorApiUnavailableRef.current = true;
        } else {
            clearAdminApiBackoff('ad-video-orders-monitor-summary');
            adMonitorApiUnavailableRef.current = false;
        }
        if (controllerResult.adSettlementUnavailable) {
            setAdminApiBackoff('ad-video-orders-settlement-dashboard');
            adSettlementApiUnavailableRef.current = true;
        } else {
            clearAdminApiBackoff('ad-video-orders-settlement-dashboard');
            adSettlementApiUnavailableRef.current = false;
        }
        controllerResult.liveLogEvents.forEach((entry) => pushLiveLog(entry.level, entry.message));
        if (controllerResult.overviewData) setOverview(controllerResult.overviewData);
        if (controllerResult.revenueData) setRevenue(controllerResult.revenueData);
        if (controllerResult.topData) setTopProjects(controllerResult.topData);
        if (controllerResult.healthData) setHealth(controllerResult.healthData);
        if (controllerResult.llmData) setLlmStatus(controllerResult.llmData);
        if (controllerResult.assembledState.adVideoOrders) {
            setAdVideoTotal(Number(controllerResult.assembledState.adVideoTotal || 0));
            setAdVideoOrders(controllerResult.assembledState.adVideoOrders);
        }
        if (controllerResult.assembledState.adOrderMonitorSummary) {
            setAdOrderMonitorSummary(controllerResult.assembledState.adOrderMonitorSummary);
        }
        if (controllerResult.assembledState.adSettlementDashboard) {
            setAdSettlementDashboard(controllerResult.assembledState.adSettlementDashboard);
        }
        if (controllerResult.assembledState.orchestratorCapabilitySummary) {
            setOrchestratorCapabilitySummary(controllerResult.assembledState.orchestratorCapabilitySummary);
        }
        setSecurityGuardDetail(controllerResult.assembledState.securityGuardDetail);
        setDashboardSelfRunStatus(controllerResult.assembledState.dashboardSelfRunStatus);
        if (controllerResult.failedMessages.length > 0) setError(controllerResult.failedMessages.join(' · '));
        snapshotRef.current = controllerResult.nextSnapshot;
        setLastUpdated(controllerResult.lastUpdated);
        if (isRefresh) setRefreshing(false);
        else setLoading(false);
    }, [apiBaseUrl, capabilityBootstrapReady, handleAdminUnauthorized]);

    const approveWorkspaceSelfRun = useCallback(async () => {
        const token = localStorage.getItem('admin_token');
        if (!token) {
            handleAdminUnauthorized('관리자 로그인이 필요합니다. 다시 로그인하세요.');
            return null;
        }
        if (!dashboardSelfRunStatus?.approval_id) {
            pushLiveLog('warning', '승인할 self-run approval_id가 없습니다.');
            return null;
        }

        setSelfRunApproving(true);
        try {
            const result = await approveWorkspaceSelfRunRequest({
                apiBaseUrl,
                token,
                approvalId: dashboardSelfRunStatus.approval_id,
                buildApiErrorMessage,
                onUnauthorized: () => handleAdminUnauthorized(),
                onUnsupported: (message) => {
                    if (!selfRunApiUnavailableRef.current) {
                        pushLiveLog('warning', message);
                    }
                },
                onSuccess: (message) => pushLiveLog('success', message),
                setUnavailable: (nextValue) => {
                    selfRunApiUnavailableRef.current = nextValue;
                },
            });
            await loadDashboard(true);
            return result;
        } catch (error: any) {
            pushLiveLog('warning', error?.message || 'self-run 승인 반영에 실패했습니다.');
            return null;
        } finally {
            setSelfRunApproving(false);
        }
    }, [apiBaseUrl, dashboardSelfRunStatus?.approval_id, handleAdminUnauthorized, loadDashboard, pushLiveLog]);

    const runFocusedSelfHealingPlan = useCallback(async () => {
        const token = localStorage.getItem('admin_token');
        if (!token) {
            handleAdminUnauthorized('관리자 로그인이 필요합니다. 다시 로그인하세요.');
            return null;
        }
        setFocusedSelfHealingBusy(true);
        try {
            const response = await fetch(`${apiBaseUrl}/api/admin/focused-self-healing/plan`, {
                method: 'POST',
                headers: {
                    Authorization: `Bearer ${token}`,
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    issue_id: `heal-${new Date().toISOString().slice(0, 10).replace(/-/g, '')}-ui`,
                    requested_path: focusedSelfHealingRequestedPath,
                    reason: focusedSelfHealingReason,
                    proposal_title: '관리자 메인 화면 Focused Self-Healing',
                    proposal_summary: '메인 화면에서 tower crane 옵션을 바로 선택하기 위한 운영 흐름',
                }),
            });
            const data = await response.json().catch(() => null);
            if (!response.ok || !data) {
                throw new Error(data?.detail || 'focused self-healing plan 호출에 실패했습니다.');
            }
            setFocusedSelfHealingPlan(data as FocusedSelfHealingPlan);
            setFocusedSelfHealingApplyResult(null);
            setFocusedSelfHealingSelectedOptionId(data.options?.[0]?.option_id || '');
            setFocusedSelfHealingApprovalConfirmed(false);
            setFocusedSelfHealingMessage(`plan 완료 · proposal_id=${data.proposal_id}`);
            pushLiveLog('success', `focused self-healing plan 완료 · ${data.proposal_id}`);
            return data as FocusedSelfHealingPlan;
        } catch (error: any) {
            setFocusedSelfHealingMessage(error?.message || 'focused self-healing plan 호출에 실패했습니다.');
            pushLiveLog('warning', error?.message || 'focused self-healing plan 호출에 실패했습니다.');
            return null;
        } finally {
            setFocusedSelfHealingBusy(false);
        }
    }, [apiBaseUrl, focusedSelfHealingReason, focusedSelfHealingRequestedPath, handleAdminUnauthorized, pushLiveLog]);

    const applyFocusedSelfHealing = useCallback(async () => {
        const token = localStorage.getItem('admin_token');
        if (!token) {
            handleAdminUnauthorized('관리자 로그인이 필요합니다. 다시 로그인하세요.');
            return null;
        }
        if (!focusedSelfHealingPlan || !focusedSelfHealingSelectedOptionId) {
            setFocusedSelfHealingMessage('먼저 focused self-healing plan 과 옵션 선택을 완료해야 합니다.');
            return null;
        }
        if (focusedSelfHealingPlan.approval_required && !focusedSelfHealingApprovalConfirmed) {
            setFocusedSelfHealingMessage('승인 필요 범위이므로 승인 스위치를 먼저 켜야 합니다.');
            return null;
        }
        setFocusedSelfHealingBusy(true);
        try {
            const response = await fetch(`${apiBaseUrl}/api/admin/focused-self-healing/apply`, {
                method: 'POST',
                headers: {
                    Authorization: `Bearer ${token}`,
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    issue_id: focusedSelfHealingPlan.issue_id,
                    requested_path: focusedSelfHealingRequestedPath,
                    reason: focusedSelfHealingReason,
                    approved: focusedSelfHealingApprovalConfirmed,
                    selected_option_id: focusedSelfHealingSelectedOptionId,
                }),
            });
            const data = await response.json().catch(() => null);
            if (!response.ok || !data) {
                throw new Error(data?.detail || 'focused self-healing apply 호출에 실패했습니다.');
            }
            setFocusedSelfHealingApplyResult(data as FocusedSelfHealingApplyResult);
            setFocusedSelfHealingMessage(data.message || 'focused self-healing 실행을 큐에 등록했습니다.');
            pushLiveLog('success', data.message || 'focused self-healing 실행을 큐에 등록했습니다.');
            return data as FocusedSelfHealingApplyResult;
        } catch (error: any) {
            setFocusedSelfHealingMessage(error?.message || 'focused self-healing apply 호출에 실패했습니다.');
            pushLiveLog('warning', error?.message || 'focused self-healing apply 호출에 실패했습니다.');
            return null;
        } finally {
            setFocusedSelfHealingBusy(false);
        }
    }, [apiBaseUrl, focusedSelfHealingApprovalConfirmed, focusedSelfHealingPlan, focusedSelfHealingReason, focusedSelfHealingRequestedPath, focusedSelfHealingSelectedOptionId, handleAdminUnauthorized, pushLiveLog]);

    useEffect(() => {
        if (authChecked) loadDashboard();
    }, [authChecked, loadDashboard]);

    useEffect(() => {
        if (!authChecked || capabilityBootstrapReady) {
            return;
        }
        const timerId = window.setTimeout(() => {
            setCapabilityBootstrapReady(true);
        }, 15000);
        return () => {
            window.clearTimeout(timerId);
        };
    }, [authChecked, capabilityBootstrapReady]);

    useEffect(() => {
        if (!authChecked) {
            return;
        }
        void loadSystemSettings();
    }, [authChecked, loadSystemSettings]);

    useEffect(() => {
        if (!authChecked) {
            return;
        }
        if (adminCategoriesBootstrappedRef.current) {
            return;
        }
        adminCategoriesBootstrappedRef.current = true;
        void loadCategories();
    }, [authChecked, loadCategories]);

    useEffect(() => {
        if (!authChecked) {
            return;
        }
        if (adminCategoryStatsBootstrappedRef.current) {
            return;
        }
        adminCategoryStatsBootstrappedRef.current = true;
        void loadCategoryStats();
    }, [authChecked, loadCategoryStats]);

    useEffect(() => {
        return bindAutoConnectGraphSnapshot({
            setAutoConnectGraph,
            readSnapshot: readAdminAutoConnectGraphSnapshot,
        });
    }, []);

    useEffect(() => {
        adMonitorApiUnavailableRef.current = isAdminApiBackoffActive('ad-video-orders-monitor-summary');
        adSettlementApiUnavailableRef.current = isAdminApiBackoffActive('ad-video-orders-settlement-dashboard');
    }, []);

    useEffect(() => {
        if (!authChecked) {
            sessionWarningExpRef.current = null;
            return;
        }

        const checkSessionExpiry = async () => {
            const currentToken = getAdminToken();
            const expiryMs = getAdminTokenExpiryMs(currentToken);

            if (!currentToken || !expiryMs) {
                return;
            }

            const remainingMs = expiryMs - Date.now();
            if (remainingMs <= 0) {
                handleAdminUnauthorized('관리자 세션 시간이 만료되었습니다. 다시 로그인하세요.');
                return;
            }

            if (remainingMs > ADMIN_SESSION_WARNING_WINDOW_MS) {
                sessionWarningExpRef.current = null;
                return;
            }

            if (sessionWarningExpRef.current === expiryMs) {
                return;
            }

            sessionWarningExpRef.current = expiryMs;
            const shouldExtend = window.confirm(
                `관리자 세션이 약 ${getRemainingSessionMinutes(expiryMs)}분 후 만료됩니다. 로그인 시간을 연장할까요?`,
            );

            if (!shouldExtend) {
                pushLiveLog('warning', '관리자 세션 연장 안내를 보류했습니다.');
                return;
            }

            try {
                await extendAdminSessionToken(currentToken);
                sessionWarningExpRef.current = null;
                pushLiveLog('success', '관리자 세션 시간을 연장했습니다.');
            } catch (error: any) {
                handleAdminUnauthorized(error?.message || '관리자 세션 연장에 실패했습니다. 다시 로그인하세요.');
            }
        };

        void checkSessionExpiry();
        const intervalId = window.setInterval(() => {
            void checkSessionExpiry();
        }, ADMIN_SESSION_CHECK_INTERVAL_MS);

        return () => {
            window.clearInterval(intervalId);
        };
    }, [authChecked, handleAdminUnauthorized]);

    useEffect(() => {
        if (!authChecked || !autoRefreshEnabled) return;

        const interval = setInterval(() => {
            loadDashboard(true);
        }, refreshSeconds * 1000);

        return () => clearInterval(interval);
    }, [authChecked, autoRefreshEnabled, refreshSeconds, loadDashboard]);

    const dashboardAnalysis = useMemo(() => buildAdminPageHealthAnalysis({
        overview,
        revenue,
        health,
        llmStatus,
        orchestratorCapabilitySummary,
        securityGuardDetail,
        dashboardSelfRunStatus,
        systemSettingsDisconnected: !systemSettings && !systemSettingsLoading && !!systemSettingsMessage,
        capabilityBootstrapEnabled: capabilityBootstrapReady,
        projectQuery,
        topProjects,
        formatCurrency,
    }), [dashboardSelfRunStatus, formatCurrency, health, llmStatus, orchestratorCapabilitySummary, overview, projectQuery, revenue, securityGuardDetail, systemSettings, systemSettingsLoading, systemSettingsMessage, topProjects]);
    const filteredTopProjects = useMemo(() => {
        const query = projectQuery.trim().toLowerCase();
        if (!query) return topProjects;
        return topProjects.filter((project) => project.title.toLowerCase().includes(query));
    }, [projectQuery, topProjects]);
    const generatorRoleOptions = useMemo(() => {
        const installedModels = systemSettings?.summary.available_models || [];
        const fallbackModels = installedModels.length > 0
            ? installedModels
            : [
                systemSettings?.summary.default_model,
                systemSettings?.summary.chat_model,
                systemSettings?.summary.voice_chat_model,
                systemSettings?.summary.reasoning_model,
                systemSettings?.summary.coding_model,
            ].filter((value): value is string => Boolean(value));
        const optimizedDefaults = {
            python_fastapi: pickPreferredModel(installedModels, OPTIMIZED_GENERATOR_DEFAULTS.reasoning, systemSettings?.summary.reasoning_model || systemSettings?.summary.default_model || ''),
            python_worker: pickPreferredModel(installedModels, OPTIMIZED_GENERATOR_DEFAULTS.coding, systemSettings?.summary.coding_model || systemSettings?.summary.default_model || ''),
            nextjs_react: pickPreferredModel(installedModels, OPTIMIZED_GENERATOR_DEFAULTS.uiux, systemSettings?.summary.chat_model || systemSettings?.summary.default_model || ''),
            node_service: pickPreferredModel(installedModels, OPTIMIZED_GENERATOR_DEFAULTS.template, systemSettings?.summary.coding_model || systemSettings?.summary.default_model || ''),
            go_service: pickPreferredModel(installedModels, OPTIMIZED_GENERATOR_DEFAULTS.reasoning, systemSettings?.summary.reasoning_model || systemSettings?.summary.default_model || ''),
            rust_service: pickPreferredModel(installedModels, OPTIMIZED_GENERATOR_DEFAULTS.ad_video, systemSettings?.summary.voice_chat_model || systemSettings?.summary.default_model || ''),
        };
        return (systemSettings?.summary.generator_profiles || []).map((profile) => {
            const defaultModel = optimizedDefaults[profile.id as keyof typeof optimizedDefaults]
                || (profile.runtime_role === 'frontend web'
                    ? (systemSettings?.summary.chat_model || systemSettings?.summary.default_model || '')
                    : (systemSettings?.summary.coding_model || systemSettings?.summary.default_model || ''));
            const selectedModel = generatorModelOverrides[profile.id] || defaultModel;
            return {
                ...profile,
                options: Array.from(new Set([defaultModel, ...fallbackModels].filter(Boolean))),
                defaultModel: selectedModel,
            };
        });
    }, [generatorModelOverrides, systemSettings]);
    const optimizedRuntimeRouteDraft = useMemo(() => {
        const availableModels = systemSettings?.summary.available_models || [];
        return Object.fromEntries(
            Object.entries(OPTIMIZED_RUNTIME_ROUTE_PRESETS).map(([routeKey, candidates]) => {
                const envKey = OPTIMIZED_RUNTIME_ROUTE_ENV_MAP[routeKey];
                const fallback = systemSettingsDraft[envKey] || systemSettings?.summary.default_model || '';
                return [routeKey, pickPreferredModel(availableModels, candidates, fallback)];
            }),
        ) as Record<string, string>;
    }, [systemSettings?.summary.available_models, systemSettings?.summary.default_model, systemSettingsDraft]);
    const applyGeneratorModelOverride = useCallback((profileId: string, modelName: string) => {
        setGeneratorModelOverrides((prev) => ({
            ...prev,
            [profileId]: modelName,
        }));
        const envKeys = GENERATOR_ENV_KEY_MAP[profileId] || [];
        envKeys.forEach((envKey) => updateSystemSettingValue(envKey, modelName));
    }, [updateSystemSettingValue]);

    const adminAlertSpeech = useMemo(
        () => buildAdminAlertSpeech(dashboardAnalysis.opsAlerts, dashboardAnalysis.orchestratorProblemCards),
        [dashboardAnalysis.opsAlerts, dashboardAnalysis.orchestratorProblemCards]
    );

    const visibleCategories = useMemo(
        () => hideEmptyCategories
            ? categories.filter((category) => (categoryStats[category.id]?.total || 0) > 0)
            : categories,
        [categories, categoryStats, hideEmptyCategories],
    );
    const sortedVisibleCategories = useMemo(() => {
        const items = [...visibleCategories];
        items.sort((left, right) => {
            const leftStat = categoryStats[left.id] ?? { total: 0, today: 0, yesterday: 0, downloads: 0, revenue: 0, ratingSum: 0, ratingCount: 0, averageRating: 0, activeCount: 0, inactiveCount: 0 };
            const rightStat = categoryStats[right.id] ?? { total: 0, today: 0, yesterday: 0, downloads: 0, revenue: 0, ratingSum: 0, ratingCount: 0, averageRating: 0, activeCount: 0, inactiveCount: 0 };
            if (categorySortBy === 'name') {
                return left.name.localeCompare(right.name, 'ko');
            }
            if (categorySortBy === 'today') {
                return rightStat.today - leftStat.today || rightStat.total - leftStat.total;
            }
            if (categorySortBy === 'downloads') {
                return rightStat.downloads - leftStat.downloads || rightStat.total - leftStat.total;
            }
            if (categorySortBy === 'revenue') {
                return rightStat.revenue - leftStat.revenue || rightStat.total - leftStat.total;
            }
            if (categorySortBy === 'rating') {
                return rightStat.averageRating - leftStat.averageRating || rightStat.total - leftStat.total;
            }
            if (categorySortBy === 'active') {
                return rightStat.activeCount - leftStat.activeCount || rightStat.total - leftStat.total;
            }
            return rightStat.total - leftStat.total || left.name.localeCompare(right.name, 'ko');
        });
        return items;
    }, [categorySortBy, categoryStats, visibleCategories]);
    const systemSettingsDisconnected = !systemSettings
        && !systemSettingsLoading
        && /설정 조회 실패\((5\d\d)\)|upstream timeout/i.test(systemSettingsMessage);

    const executeAutomaticRecovery = useCallback(async (mode: 'auto' | 'manual') => {
        setAutoRecoveryRunning(true);
        try {
            const recoveryResult = await executeAdminAutomaticRecovery({
                mode,
                selfRunFailureInsight: dashboardAnalysis.selfRunFailureInsight,
                dashboardSelfRunStatus,
                systemSettingsDisconnected,
                hasOrchestratorCapabilityError: dashboardAnalysis.hasOrchestratorCapabilityError,
                hasOrchestratorCapabilityWarning: dashboardAnalysis.hasOrchestratorCapabilityWarning,
                selfRunApiUnavailable: selfRunApiUnavailableRef.current,
                retryWorkspaceSelfRun,
                normalizeWorkspaceSelfRun,
            });
            if (recoveryResult.shouldOpenPanels) {
                setLiveLogsPanelOpen(true);
                setAutoConnectGraphPanelOpen(true);
                setLlmControlPanelOpen(true);
                setCustomerOrchestratorPanelOpen(true);
            }
            if (recoveryResult.shouldOpenSystemSettingsPanel) {
                setSystemSettingsPanelOpen(true);
                await loadSystemSettings();
            }
            if (recoveryResult.shouldReloadDashboard) {
                await loadDashboard(true);
            }
            setAutoOpsLastExecutedAt(recoveryResult.executedAt);
            setAutoRecoveryHistory((prev) => [recoveryResult.historyItem as AutoRecoveryHistoryItem, ...prev].slice(0, 20));
        } finally {
            setAutoRecoveryRunning(false);
        }
    }, [
        dashboardSelfRunStatus?.approval_id,
        dashboardAnalysis.hasOrchestratorCapabilityError,
        dashboardAnalysis.hasOrchestratorCapabilityWarning,
        loadDashboard,
        loadSystemSettings,
        normalizeWorkspaceSelfRun,
        retryWorkspaceSelfRun,
        dashboardAnalysis.selfRunFailureInsight,
        systemSettingsDisconnected,
    ]);

    useEffect(() => {
        if (!dashboardSelfRunStatus) {
            return;
        }
        const hasSecurityGuardProblem = dashboardAnalysis.orchestratorProblemCards.some((card) => card.id === 'security-guard');
        const signature = `${dashboardSelfRunStatus.approval_id || 'latest'}:${dashboardSelfRunStatus.status}`;
        const shouldNormalize = shouldRunSelfRunAutoNormalization({
            autoOpsEnabled,
            dashboardSelfRunStatus,
            selfRunApiUnavailable: selfRunApiUnavailableRef.current,
            selfRunRetrying,
            selfRunNormalizing,
            hasSecurityGuardProblem,
            normalizationSignature: selfRunNormalizationRef.current,
            currentSignature: signature,
        });
        if (!shouldNormalize) {
            return;
        }
        selfRunNormalizationRef.current = signature;
        void normalizeWorkspaceSelfRun(false).then((result) => {
            if (result?.normalized) {
                void loadDashboard(true);
            }
        });
    }, [
        autoOpsEnabled,
        dashboardSelfRunStatus,
        loadDashboard,
        normalizeWorkspaceSelfRun,
        dashboardAnalysis.orchestratorProblemCards,
        selfRunNormalizing,
        selfRunRetrying,
    ]);

    // 헬스 상태: "ok" 또는 "healthy" 모두 초록색
    const isHealthOk = health?.status === 'ok' || health?.status === 'healthy';
    useEffect(() => {
        if (!authChecked || !voiceAlertEnabled) {
            return;
        }
        const signature = `${dashboardAnalysis.opsAlerts.map((alert) => `${alert.level}:${alert.id}:${alert.message}:${alert.action}`).join('|')}__${dashboardAnalysis.orchestratorProblemCards.map((card) => `${card.id}:${card.state}:${card.detail || card.metric}`).join('|')}`;
        if (!adminAlertSpeech || !signature) {
            return;
        }
        if (lastSpokenAlertSignatureRef.current === signature) {
            return;
        }
        lastSpokenAlertSignatureRef.current = signature;
        // 동일 경고를 매 refresh마다 반복 낭독하지 않도록 signature 변화가 있을 때만 읽는다.
        speakAdminAlert(adminAlertSpeech);
    }, [
        adminAlertSpeech,
        authChecked,
        dashboardAnalysis.opsAlerts,
        dashboardAnalysis.orchestratorProblemCards,
        voiceAlertEnabled,
    ]);

    useEffect(() => {
        if (!autoOpsEnabled || !authChecked) return;
        const signature = [
            dashboardSelfRunStatus?.approval_id || '-',
            dashboardSelfRunStatus?.status || '-',
                dashboardAnalysis.selfRunFailureInsight?.category || '-',
            String(systemSettingsDisconnected),
                String(dashboardAnalysis.hasOrchestratorCapabilityError),
                String(dashboardAnalysis.hasOrchestratorCapabilityWarning),
        ].join('|');
        if (autoOpsSignatureRef.current === signature) return;
        autoOpsSignatureRef.current = signature;
        if (dashboardAnalysis.selfRunFailureInsight) {
            pushLiveLog(dashboardAnalysis.selfRunFailureInsight.severity === 'critical' ? 'warning' : 'info', `자동 진단: ${dashboardAnalysis.selfRunFailureInsight.title}`);
        }
        void executeAutomaticRecovery('auto');
    }, [
        authChecked,
        autoOpsEnabled,
        dashboardSelfRunStatus?.approval_id,
        dashboardSelfRunStatus?.status,
        executeAutomaticRecovery,
        dashboardAnalysis.selfRunFailureInsight,
    ]);

    if (!authChecked) {
        return (
            <div className="min-h-screen flex items-center justify-center bg-[#0d1117] text-[#c9d1d9]">
                <div className="text-center">
                    <div className="text-3xl mb-4">🔐</div>
                    <p className="text-gray-500">{authStatusMessage}</p>
                </div>
            </div>
        );
    }



    const adminManualOrchestratorAssembly = buildAdminPageManualOrchestratorAssembly({
        adminStageRun: adminStageRun as SharedOrchestratorStageRun | null,
        adminStageNoteDraft,
        setAdminStageNoteDraft,
        adminStageSubstepChecks,
        setAdminStageSubstepChecks,
        adminStageRevisionNote,
        setAdminStageRevisionNote,
        adminStageUpdateLoading,
        updateAdminStageStatus,
        refreshAdminStageRun,
        latestDedicatedOrder: latestDedicatedOrder || null,
        selectedAdminManualStep,
        selectedAdminManualStepState,
        adminManualOrchestratorStepId,
        completedManualStepCount,
        previousAdminManualStep,
        nextAdminManualStep,
        adminManualMeta,
        setAdminManualOrchestratorStepId,
        moveAdminManualStep,
        updateAdminManualStepRouteStage,
        updateAdminManualStepDuration,
        setAdminManualMeta,
        downloadAdminManualWorklog,
        openAdminLlmOrchestratorBridge,
        openMarketplaceOrchestratorBridge,
        toggleAdminManualAction,
        toggleAdminManualStepCompleted,
        updateAdminManualStepNote,
        updateAdminManualStepField,
        addAdminManualAttachmentLink,
        removeAdminManualAttachmentLink,
        latestDedicatedProductionStages,
        latestDedicatedCurrentStage,
        latestDedicatedWorkReady,
        latestDedicatedReadyCount,
        actionTemplateLabel: ADMIN_ACTION_TEMPLATE_LABELS[latestDedicatedOrder?.action_template_key || ''] || '미지정',
        motionTempoLabel: getAdminMotionTempoLabel(latestDedicatedOrder?.motion_tempo),
        humanInteractionRules: ADMIN_HUMAN_OBJECT_INTERACTION_RULES,
        filteredAdminCompletionHistory,
        filteredAdminTraceHistory,
        filteredAdminRetryQueueItems,
        adminReplayQueueId,
        adminTraceFilter,
        loadAdminCompletionHistory,
        loadAdminTraceHistory,
        loadAdminRetryQueue,
        handleReplayRetryQueue,
        setAdminTraceFilter,
        getAdminSceneFrameHint,
    });

    const adminAdOrdersAssembly = buildAdminPageAdOrdersAssembly({
        adOrdersOpen,
        setAdOrdersOpen,
        adVideoTotal,
        adVideoOrders,
        adOrderMonitorSummary,
        adSettlementDashboard,
        adSettlementExporting,
        adMonitorApiUnavailable: adMonitorApiUnavailableRef.current,
        adSettlementApiUnavailable: adSettlementApiUnavailableRef.current,
        actionTemplateLabels: ADMIN_ACTION_TEMPLATE_LABELS,
        onRefresh: () => {
            trackDashboardAutoConnect({
                capabilityId: 'settlement-dashboard',
                title: '광고 주문 새로고침',
                detail: '광고 주문/정산 모니터링 새로고침',
                panelId: 'PANEL-ADMIN-SETTLEMENT',
                status: 'linked',
                execution: 'sync',
            });
            clearAdminApiBackoff('ad-video-orders-monitor-summary');
            clearAdminApiBackoff('ad-video-orders-settlement-dashboard');
            adMonitorApiUnavailableRef.current = false;
            adSettlementApiUnavailableRef.current = false;
            void loadDashboard(true);
        },
        onExportSettlementCsv: () => {
            trackDashboardAutoConnect({
                capabilityId: 'settlement-export',
                title: '정산 CSV 다운로드',
                detail: '광고 주문 정산 CSV 다운로드 실행',
                panelId: 'PANEL-ADMIN-SETTLEMENT',
                status: 'linked',
            });
            void exportAdSettlementCsv();
        },
        buildSettlementConnectionId: buildSettlementOrderConnectionId,
        review: {
            expandedOrderId: expandedAdReviewOrderId,
            drafts: adReviewDrafts,
            diffOnly: adReviewDiffOnly,
            statusDiffOnly: adReviewStatusDiffOnly,
            noteDiffOnly: adReviewNoteDiffOnly,
            savingId: adReviewSavingId,
            onOpenPanel: openAdReviewPanel,
            onToggleDiffOnly: toggleAdReviewDiffOnly,
            onToggleStatusDiffOnly: toggleAdReviewStatusDiffOnly,
            onToggleNoteDiffOnly: toggleAdReviewNoteDiffOnly,
            onResetFilters: resetAdReviewFilters,
            onStoryboardModalOpen: setAdStoryboardModal,
            matchesSceneFilter: matchesAdReviewSceneFilter,
            onUpdateDraft: updateAdReviewDraft,
            onSave: (order) => void handleSaveAdReview(order),
        },
        actions: {
            previewLoadingId: adPreviewLoadingId,
            retryingId: adRetryingId,
            onPreview: (order) => void handlePreviewAdOrder(order),
            onDownload: (order) => void handleDownloadAdOrder(order),
            onRetry: (order) => void handleRetryAdOrder(order),
        },
    });

    const adminDashboardOverviewAssembly = buildAdminDashboardOverviewAssembly({
        error,
        dashboardAnalysis,
        selfRunApproving,
        onApproveWorkspaceSelfRun: () => void approveWorkspaceSelfRun(),
        autoOpsEnabled,
        onAutoOpsEnabledChange: setAutoOpsEnabled,
        autoOpsLastExecutedAt,
        autoRecoveryRunning,
        onExecuteAutomaticRecovery: () => void executeAutomaticRecovery('manual'),
        onReloadDashboard: () => void loadDashboard(true),
        autoRecoveryHistory,
        buildCapabilityConnectionId,
        onOpenOrchestratorDetail: (capabilityId, detail, status) => trackDashboardAutoConnect({
            capabilityId: `orchestrator-${capabilityId}`,
            title: status === 'warning' ? '오케스트레이터 경고 상세 이동' : '오케스트레이터 상세 제어 열기',
            detail,
            panelId: 'PANEL-ADMIN-ORCHESTRATOR',
            status,
        }),
        getOrchestratorActionGuide,
        toFileHref,
        dashboardSelfRunStatus: dashboardAnalysis.normalizedDashboardSelfRunStatus,
        getHealthAlertMetrics: (alert) => {
            const metrics = getHealthAlertMetrics(alert);
            return Object.fromEntries(
                Object.entries(metrics).filter(([, value]) => typeof value === 'string' || typeof value === 'number'),
            ) as Record<string, string | number>;
        },
        getHealthAlertRootCause,
        formatHealthMetricLabel,
        formatHealthMetricValue,
        apiBaseUrl,
        onImmediateRefresh: () => {
            trackDashboardAutoConnect({
                capabilityId: 'dashboard-sync',
                title: '관리자 액션 즉시 상태 재수집',
                detail: '관리자 액션 패널에서 상태 재수집 실행',
                panelId: 'PANEL-ADMIN-DASHBOARD',
                status: 'linked',
                execution: 'sync',
            });
            clearAdminApiBackoff('ad-video-orders-monitor-summary');
            clearAdminApiBackoff('ad-video-orders-settlement-dashboard');
            adMonitorApiUnavailableRef.current = false;
            adSettlementApiUnavailableRef.current = false;
            void loadDashboard(true);
        },
        voiceAlertEnabled,
        onToggleVoiceAlertEnabled: () => setVoiceAlertEnabled((prev) => !prev),
        onSpeakAdminAlert: () => speakAdminAlert(adminAlertSpeech || '현재 발성할 관리자 경고가 없습니다.'),
        autoRefreshEnabled,
        onToggleAutoRefreshEnabled: () => setAutoRefreshEnabled((prev) => !prev),
        refreshSeconds,
        onRefreshSecondsChange: setRefreshSeconds,
        refreshing,
        lastUpdated,
        focusedSelfHealingBusy,
        focusedSelfHealingModalOpen,
        onOpenFocusedSelfHealing: () => setFocusedSelfHealingModalOpen(true),
        onCloseFocusedSelfHealing: () => setFocusedSelfHealingModalOpen(false),
        focusedSelfHealingRequestedPath,
        onFocusedSelfHealingRequestedPathChange: setFocusedSelfHealingRequestedPath,
        focusedSelfHealingReason,
        onFocusedSelfHealingReasonChange: setFocusedSelfHealingReason,
        focusedSelfHealingPlan,
        focusedSelfHealingApplyResult,
        focusedSelfHealingApprovalConfirmed,
        onFocusedSelfHealingApprovalConfirmedChange: setFocusedSelfHealingApprovalConfirmed,
        focusedSelfHealingSelectedOptionId,
        onFocusedSelfHealingSelectedOptionIdChange: setFocusedSelfHealingSelectedOptionId,
        onRunFocusedSelfHealingPlan: () => void runFocusedSelfHealingPlan(),
        onApplyFocusedSelfHealing: () => void applyFocusedSelfHealing(),
        focusedSelfHealingMessage,
    });

    const adminAutoConnectGraphAssembly = buildAdminAutoConnectGraphAssembly({
        autoConnectGraph,
        adminConnectionLookupId,
        onAdminConnectionLookupIdChange: setAdminConnectionLookupId,
        onLoadLookup: () => loadAdminConnectionLookup(),
        adminConnectionLookupLoading,
        adminConnectionLookupResult,
        adminReplayQueueId,
        onReplayRetryQueue: handleReplayRetryQueue,
        setAdminConnectionLookupId,
    });

    const adminSystemSettingsAssembly = buildAdminPageSystemSettingsAssembly({
        systemSettings,
        systemSettingsDisconnected,
        systemSettingsLoading,
        systemSettingsSaving,
        systemAutomaticApplying,
        systemSettingsMessage,
        identityProviderSettings,
        generatorRoleOptions,
        optimizedRuntimeRouteDraft,
        statusSections: ADMIN_SYSTEM_SETTINGS_STATUS_SECTIONS,
        generatorEnvKeyMap: GENERATOR_ENV_KEY_MAP,
        runtimeRouteEnvMap: OPTIMIZED_RUNTIME_ROUTE_ENV_MAP,
        systemSettingsOpen,
        systemSettingsDraft,
        postgresPasswordNext,
        postgresPasswordConfirm,
        postgresPasswordSaving,
        postgresPasswordMessage,
        onApplyGlobalAutomaticMode: applyGlobalAutomaticMode,
        onLoadSystemSettings: loadSystemSettings,
        onSaveSystemSettings: saveSystemSettings,
        onApplyGeneratorModelOverride: applyGeneratorModelOverride,
        onToggleSystemSettingsSection: toggleSystemSettingsSection,
        onUpdateSystemSettingValue: updateSystemSettingValue,
        onPostgresPasswordNextChange: setPostgresPasswordNext,
        onPostgresPasswordConfirmChange: setPostgresPasswordConfirm,
        onUpdatePostgresRuntimePassword: updatePostgresRuntimePassword,
    });

    const adminSampleProductsAssembly = buildAdminPageSampleProductsAssembly({
        categories,
        selectedCategoryId,
        onSelectedCategoryIdChange: setSelectedCategoryId,
        selectedCategoryStat,
        selectedCategoryDelta,
        sampleBatchCount,
        onSampleBatchCountChange: setSampleBatchCount,
        sampleCleanupPattern,
        onSampleCleanupPatternChange: setSampleCleanupPattern,
        sampleTemplates,
        sampleCreating,
        sampleResult,
        onCreateBatchSamples: createBatchSamples,
        onRunSampleCleanup: runSampleCleanup,
        onCreateSampleProduct: createSampleProduct,
    });

    const dashboardSummaryCards = [
        {
            id: 'health-score',
            label: '자동 건강상태 점수',
            value: String(adminDashboardOverviewAssembly.automaticHealthScore ?? '-'),
            note: adminDashboardOverviewAssembly.automaticHealthLabel || '운영 상태 스냅샷',
        },
        {
            id: 'self-run-state',
            label: '최근 self-run 상태',
            value: dashboardAnalysis.normalizedDashboardSelfRunStatus?.status || '-',
            note: dashboardAnalysis.normalizedDashboardSelfRunStatus?.approval_id || '최신 approval 추적',
        },
        {
            id: 'auto-recovery',
            label: '자동 복구',
            value: autoRecoveryRunning ? '실행 중' : '대기',
            note: autoRecoveryRunning ? '실시간 복구 루프 가동' : autoRecoveryHistory?.[0]?.triggeredAt || '최근 이력 없음',
        },
        {
            id: 'llm-runtime',
            label: 'LLM 런타임',
            value: llmStatus?.loaded ? 'loaded' : 'not_loaded',
            note: llmStatus?.model_path || llmStatus?.gpu_runtime_label || '런타임 정보 대기',
        },
    ];

    const openAdminSurface = (selector: string, beforeOpen?: () => void) => {
        beforeOpen?.();

        if (typeof window === 'undefined') {
            return;
        }

        window.requestAnimationFrame(() => {
            window.setTimeout(() => {
                document.querySelector(selector)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
            }, 60);
        });
    };

    const launcherLeftColumn = [
        {
            id: 'admin-control-hub',
            label: '🧩 ADMIN CONTROL HUB',
            summary: '운영자 명령 허브 · 설정 새로고침 · 전역 자동 전환',
            accent: 'slate',
            onClick: () => setAdminControlHubOpen(true),
        },
        {
            id: 'system-settings',
            label: '🧭 전역 .env 설정 패널',
            summary: '도메인 · 저장 경로 · runtime · .env 운영값',
            accent: 'cyan',
            onClick: () => setSystemSettingsPanelOpen(true),
        },
        {
            id: 'auto-connect',
            label: '🕸️ self auto-connect graph',
            summary: 'connection_id 흐름 · active graph · DB 조회',
            accent: 'blue',
            onClick: () => setAutoConnectGraphPanelOpen(true),
        },
        {
            id: 'health-overview',
            label: '🩺 관리자 자동 건강상태 / 자가진단 / 자가개선',
            summary: 'health score · self-run · 자동 복구 · focused self-healing',
            accent: 'emerald',
            onClick: () => setHealthOverviewOpen(true),
        },
        {
            id: 'ad-orders',
            label: '🎬 광고 영상 주문 모니터링',
            summary: '재시도 · 다운로드 · 정산 · 리뷰 상태',
            accent: 'amber',
            onClick: () => setAdOrdersPanelOpen(true),
        },
        {
            id: 'category',
            label: '🗂️ 마켓플레이스 카테고리 관리',
            summary: '카테고리 등록 · 통계 · 최근 프로젝트',
            accent: 'blue',
            onClick: () => setCategoryPanelOpen(true),
        },
    ] as const;

    const launcherRightColumn = [
        {
            id: 'llm-control',
            label: '🤖 LLM 통합 제어 패널',
            summary: '모델 · 정책 · 역할별 런타임 · 실행 정책',
            accent: 'violet',
            onClick: () => setLlmControlPanelOpen(true),
        },
        {
            id: 'live-logs',
            label: '📡 운영 라이브 로그',
            summary: '최근 30건 이벤트 · connection_id · panel_id · action',
            accent: 'blue',
            onClick: () => setLiveLogsPanelOpen(true),
        },
        {
            id: 'top-projects',
            label: '🏆 상위 프로젝트',
            summary: '다운로드 기준 순위 · 가격 · 평점 반응 지표',
            accent: 'amber',
            onClick: () => setTopProjectsPanelOpen(true),
        },
        {
            id: 'sample',
            label: '🎯 원터치 샘플 생성',
            summary: '일괄 생성 · 중복 정리 · 단건 생성',
            accent: 'violet',
            onClick: () => setSamplePanelOpen(true),
        },
        {
            id: 'cost',
            label: '💸 비용 시뮬레이터',
            summary: '월 주문 · 컷 단가 · 권장 아키텍처 계산',
            accent: 'emerald',
            onClick: () => setCostSimulatorPanelOpen(true),
        },
        {
            id: 'quick-links',
            label: '⚡ 빠른 이동',
            summary: '문서 · Swagger · 마켓 · 고객 오케스트레이터',
            accent: 'slate',
            onClick: () => setQuickLinksPanelOpen(true),
        },
    ] as const;

    const orchestratorLauncher = {
        id: 'manual-orchestrator',
        label: '관리자 수동 오케스트레이션',
        summary: '장기 분석 · 구조 설계 · 관리자 공용 오케스트레이터 · 단계별 작업 흐름',
        accent: 'emerald',
        onClick: () => setCustomerOrchestratorPanelOpen(true),
    } as const;

    const adminSidebar = (
        <div className="workspace-section-stack">
            <div className="workspace-sidebar-card">
                <p className="workspace-card-kicker">Operator</p>
                <h3 className="workspace-card-title">운영자 세션</h3>
                <p className="workspace-card-copy">
                    {adminUser ? `${adminUser.username} (${adminUser.email})` : '운영자 인증 상태를 확인 중입니다.'}
                </p>
                <div className="workspace-chip-row">
                    <span className="workspace-chip workspace-chip-active">관리자</span>
                    <span className="workspace-chip">Round 7 synced</span>
                </div>
            </div>
            <div className="workspace-sidebar-card">
                <p className="workspace-card-kicker">핵심 지표</p>
                <div className="workspace-list">
                    {dashboardSummaryCards.map((card) => (
                        <div key={card.id} className="workspace-list-item">
                            <div>
                                <strong>{card.label}</strong>
                                <span>{card.note}</span>
                            </div>
                            <strong>{card.value}</strong>
                        </div>
                    ))}
                </div>
            </div>
            <div className="workspace-sidebar-card">
                <p className="workspace-card-kicker">바로 이동</p>
                <div className="workspace-list">
                    <div className="workspace-list-item"><strong>🧩 ADMIN CONTROL HUB</strong><span>운영자 명령 허브 / 설정 새로고침 / 전역 자동 전환</span></div>
                    <div className="workspace-list-item"><strong>🧭 전역 .env 설정 패널</strong><span>.env / runtime / LLM 제어</span></div>
                    <div className="workspace-list-item"><strong>관리자 수동 오케스트레이션</strong><span>장기 분석 / 구조 설계 / 공용 오케스트레이터</span></div>
                    <div className="workspace-list-item"><strong>🕸️ self auto-connect graph</strong><span>connection_id 흐름 추적</span></div>
                </div>
            </div>
        </div>
    );

    const boardSections = [
        {
            id: 'admin-control',
            title: '🧩 ADMIN CONTROL HUB',
            usage: '전역 운영 지표 요약 및 퀵 액션',
            description: '앱 건강상태와 최근 설정 정보를 한눈에 모니터링합니다.',
            open: adminControlHubOpen,
            onToggle: () => setAdminControlHubOpen((prev) => !prev),
            body: (
                <div className="workspace-admin-command" style={{ padding: '0 20px', maxWidth: '800px', margin: '0 auto' }}>
                    <textarea
                        className="workspace-admin-command-textarea"
                        readOnly
                        style={{ height: '180px' }}
                        value={[
                            '운영자 명령 허브',
                            `- 관리자 도메인: ${systemSettings?.summary.admin_domain || '-'}`,
                            `- API 기준 주소: ${systemSettings?.summary.local_api_base_url || '-'}`,
                            `- 저장 루트: ${systemSettings?.summary.marketplace_host_root || '-'}`,
                            `- 현재 LLM 프로필: ${systemSettings?.summary.selected_profile || '-'}`,
                            `- 최근 self-run: ${dashboardAnalysis.normalizedDashboardSelfRunStatus?.status || '-'}`,
                        ].join('\n')}
                    />
                    <div className="workspace-admin-command-actions" style={{ marginTop: '16px' }}>
                        <button type="button" onClick={() => setSystemSettingsPanelOpen(true)} className="workspace-primary-button">전역 설정 열기</button>
                        <button type="button" onClick={loadSystemSettings} className="workspace-secondary-button">설정 새로고침</button>
                        <button type="button" onClick={applyGlobalAutomaticMode} className="workspace-ghost-button">전역 자동 전환</button>
                    </div>
                </div>
            ),
            toggleTestId: 'admin-control-hub',
            windowSize: 'wide' as const,
        },
        {
            id: 'health',
            title: '🩺 관리자 자동 건강상태 / 자가진단',
            usage: '전체 시스템 건강 상태 모니터링 및 자가 치유',
            description: '각 컴포넌트의 상태를 점검하고 복구 동작을 진행합니다.',
            open: healthOverviewOpen,
            onToggle: () => setHealthOverviewOpen((prev) => !prev),
            body: <AdminDashboardOverview {...adminDashboardOverviewAssembly} />,
            toggleTestId: 'admin-health-overview-section',
            windowSize: 'full' as const,
        },
        {
            id: 'autoconnect',
            title: '🕸️ self auto-connect graph',
            usage: '버튼/패널/대화/실행 공통 추적키 확인',
            description: '관리자 대시보드와 관리자 LLM의 active connection 흐름을 추적합니다.',
            open: autoConnectGraphPanelOpen,
            onToggle: () => setAutoConnectGraphPanelOpen((prev) => !prev),
            body: <AdminAutoConnectGraphPanel {...adminAutoConnectGraphAssembly} />,
            windowSize: 'wide' as const,
        },
        {
            id: 'manual',
            title: '관리자 수동 오케스트레이션',
            usage: '관리자 전용 수동 분석·구조 설계',
            description: '장기 분석, 기술 정보 유입, 구조 수립을 단계별로 수동 관리합니다.',
            open: customerOrchestratorPanelOpen,
            onToggle: () => setCustomerOrchestratorPanelOpen((prev) => !prev),
            body: <AdminManualOrchestratorSection {...adminManualOrchestratorAssembly} />,
            windowSize: 'full' as const,
        },
        {
            id: 'ad-orders',
            title: '🎬 광고 영상 주문 모니터링',
            usage: '광고 주문 상태와 재시도/다운로드 운영 관리',
            description: '주문 모니터링과 액션을 별도 보드 카드로 바로 수행합니다.',
            open: adOrdersPanelOpen,
            onToggle: () => setAdOrdersPanelOpen((prev) => !prev),
            body: (
                <AdminAdOrdersSection
                    summary={adminAdOrdersAssembly.summary}
                    review={adminAdOrdersAssembly.review}
                    actions={adminAdOrdersAssembly.actions}
                />
            ),
            toggleTestId: 'admin-storyboard-section-toggle',
            wide: true,
            windowSize: 'full' as const,
        },
        {
            id: 'category',
            title: '🗂️ 마켓플레이스 카테고리 관리',
            usage: '카테고리 등록과 운영 분류 체계 정리',
            description: '상품 분류 체계를 정리하는 관리자용 카드입니다.',
            open: categoryPanelOpen,
            onToggle: () => setCategoryPanelOpen((prev) => !prev),
            body: (
                <AdminCategoryManagementSection
                    list={{
                        visibleCategories,
                        sortedVisibleCategories,
                        categoryStats,
                        categoryRecentProjects,
                        categoryMessage,
                        categoryUpdatingId,
                        categoryDeletingId,
                        onLoadCategories: loadCategories,
                        onUpdateCategory: (categoryId) => void updateCategory(categoryId),
                        onCancelEditCategory: cancelEditCategory,
                        onBeginEditCategory: beginEditCategory,
                        onDeleteCategory: (category) => void deleteCategory(category),
                    }}
                    filter={{
                        categoryName,
                        categoryDescription,
                        categoryCreating,
                        hideEmptyCategories,
                        categorySortBy,
                        onCategoryNameChange: setCategoryName,
                        onCategoryDescriptionChange: setCategoryDescription,
                        onCreateCategory: () => void createCategory(),
                        onHideEmptyCategoriesChange: setHideEmptyCategories,
                        onCategorySortByChange: setCategorySortBy,
                    }}
                    editing={{
                        editingCategoryId,
                        editingCategoryName,
                        editingCategoryDescription,
                        onEditingCategoryNameChange: setEditingCategoryName,
                        onEditingCategoryDescriptionChange: setEditingCategoryDescription,
                    }}
                />
            ),
            windowSize: 'wide' as const,
        },
        {
            id: 'cost',
            title: '💸 비용 시뮬레이터',
            usage: '로컬+외부 하이브리드 비용 모델 계산',
            description: '컷당 비용과 권장 아키텍처를 즉시 계산합니다.',
            open: costSimulatorPanelOpen,
            onToggle: () => setCostSimulatorPanelOpen((prev) => !prev),
            body: (
                <AdminCostSimulatorSection
                    form={costSimulatorForm}
                    loading={costSimulatorLoading}
                    error={costSimulatorError}
                    result={costSimulatorResult}
                    onFieldChange={updateCostSimulatorField}
                    onRun={runCostSimulation}
                />
            ),
            windowSize: 'wide' as const,
        },
        {
            id: 'quick',
            title: '⚡ 빠른 이동',
            usage: '운영자가 자주 여는 핵심 화면과 API 바로가기',
            description: '핵심 운영 경로를 작은 카드로 정리합니다.',
            open: quickLinksPanelOpen,
            onToggle: () => setQuickLinksPanelOpen((prev) => !prev),
            body: <AdminQuickLinksSection apiBaseUrl={apiBaseUrl} />,
            windowSize: 'default' as const,
        },
        {
            id: 'llm',
            title: '🤖 LLM 통합 제어 패널',
            usage: 'LLM runtime, 역할별 모델, 실행 정책 관리',
            description: '핵심 LLM 운영값을 집결 관리합니다.',
            open: llmControlPanelOpen,
            onToggle: () => setLlmControlPanelOpen((prev) => !prev),
            body: <AdminLlmControlSummary llmPanelHeight={llmPanelHeight} />,
            windowSize: 'full' as const,
        },
        {
            id: 'sample',
            title: '🎯 원터치 샘플 생성',
            usage: '테스트/디자인 검증용 샘플 상품 생성과 정리',
            description: '샘플 생성과 정리를 별도 카드로 분리합니다.',
            open: samplePanelOpen,
            onToggle: () => setSamplePanelOpen((prev) => !prev),
            body: <AdminSampleProductsSection {...adminSampleProductsAssembly} />,
            windowSize: 'wide' as const,
        },
        {
            id: 'live-logs',
            title: '📡 운영 라이브 로그',
            usage: '실시간 이벤트 스트림 확인',
            description: '운영 이벤트를 스트림 형태로 제공합니다.',
            open: liveLogsPanelOpen,
            onToggle: () => setLiveLogsPanelOpen((prev) => !prev),
            body: (
                <div style={{ padding: '0 20px', paddingBottom: '20px' }}>
                    {liveLogs.length === 0 ? (
                        <p className="workspace-card-copy">아직 기록된 실시간 이벤트가 없습니다.</p>
                    ) : (
                        <ul className="workspace-list">
                            {liveLogs.map((log) => (
                                <li key={log.id} className="workspace-list-item">
                                    <div>
                                        <strong>{log.message}</strong>
                                        <span>{log.connection_id || '-'} · {log.panel_id || '-'} · {log.action || '-'}</span>
                                    </div>
                                    <span>{log.createdAt}</span>
                                </li>
                            ))}
                        </ul>
                    )}
                </div>
            ),
            toggleTestId: 'admin-live-logs-section',
            windowSize: 'wide' as const,
        },
        {
            id: 'top-projects',
            title: '🏆 상위 프로젝트',
            usage: '다운로드 기준 상위 항목 확인',
            description: '인기 프로젝트 지표를 요약해 제공합니다.',
            open: topProjectsPanelOpen,
            onToggle: () => setTopProjectsPanelOpen((prev) => !prev),
            body: (
                <div style={{ padding: '0 20px', paddingBottom: '20px' }}>
                    {filteredTopProjects.length === 0 ? (
                        <p className="workspace-card-copy">아직 집계된 상위 프로젝트가 없습니다.</p>
                    ) : (
                        <div className="workspace-list mt-4">
                            {filteredTopProjects.map((project) => (
                                <div key={project.id} className="workspace-list-item">
                                    <div>
                                        <strong>{project.title}</strong>
                                        <span>다운로드 {project.downloads} · 평점 {project.rating?.toFixed?.(1) ?? project.rating}</span>
                                    </div>
                                    <strong>{formatCurrency(project.price)}</strong>
                                </div>
                            ))}
                        </div>
                    )}
                </div>
            ),
            toggleTestId: 'admin-top-projects-section',
            windowSize: 'wide' as const,
        },
    ];

    return (
        <div className="admin-dark">
            <WorkspaceChrome
                brand="Workspace 4.0"
                statusLabel={refreshing ? '실시간 갱신 중' : '운영 연결 유지'}
                pageTestId="admin-workspace-page"
                compactHeader
                hideHero
                railItems={[
                    { 
                        id: 'home', label: '대시보드', shortLabel: '대시', href: '/admin', active: true, accent: 'blue',
                        icon: <div className="flex items-center justify-center w-10 h-10 rounded-full bg-white/5 border border-white/10 mb-1 shadow-[0_0_12px_rgba(255,255,255,0.05)] text-lg">🏠</div> 
                    },
                    { 
                        id: 'market', label: '마켓', shortLabel: '마켓', href: '/marketplace', accent: 'emerald',
                        icon: <div className="flex items-center justify-center w-10 h-10 rounded-full bg-white/5 border border-white/10 mb-1 text-lg">🛒</div> 
                    },
                    { 
                        id: 'llm', label: 'LLM', shortLabel: 'LLM', href: '/admin/llm', accent: 'violet',
                        icon: <div className="flex items-center justify-center w-10 h-10 rounded-full bg-white/5 border border-white/10 mb-1 text-lg">🤖</div> 
                    },
                    { 
                        id: 'docs', label: '문서', shortLabel: '문서', href: '/admin/docs-viewer?path=docs%2Fidentity-provider-integration-contract.md', accent: 'amber',
                        icon: <div className="flex items-center justify-center w-10 h-10 rounded-full bg-white/5 border border-white/10 mb-1 text-lg">📘</div> 
                    },
                    ...launcherLeftColumn.map(item => {
                        const match = item.label.match(/[\u2700-\u27BF]|[\uE000-\uF8FF]|\uD83C[\uDC00-\uDFFF]|\uD83D[\uDC00-\uDFFF]|[\u2011-\u26FF]|\uD83E[\uDD10-\uDDFF]/);
                        const emoji = match ? match[0] : '●';
                        let cleanLabel = item.label.replace(emoji, '').trim();
                        let short = cleanLabel.split(' ')[0]?.slice(0, 2) || cleanLabel.slice(0, 2);
                        if (short.toUpperCase() === 'AD') short = '제어';
                        if (item.id === 'system-settings') short = '설정';
                        if (item.id === 'auto-connect') short = '연결';

                        return {
                            id: item.id,
                            label: item.label,
                            shortLabel: short,
                            accent: item.accent as 'blue' | 'violet' | 'emerald' | 'amber' | 'neutral' | 'slate' | 'cyan',
                            onClick: item.onClick,
                            testId: `admin-launcher-${item.id}`,
                            icon: <div className="flex items-center justify-center w-10 h-10 rounded-full bg-white/5 border border-white/10 mb-1 text-lg group-hover:bg-white/10 transition-colors">{emoji}</div> 
                        };
                    }),
                ]}
                rightRailItems={launcherRightColumn.map(item => {
                    const match = item.label.match(/[\u2700-\u27BF]|[\uE000-\uF8FF]|\uD83C[\uDC00-\uDFFF]|\uD83D[\uDC00-\uDFFF]|[\u2011-\u26FF]|\uD83E[\uDD10-\uDDFF]/);
                    const emoji = match ? match[0] : '●';
                    let cleanLabel = item.label.replace(emoji, '').trim();
                    let short = cleanLabel.split(' ')[0]?.slice(0, 2) || cleanLabel.slice(0, 2);
                    if (item.id === 'live-logs') short = '로그';
                    if (item.id === 'top-projects') short = '인기';
                    if (item.id === 'cost') short = '비용';
                    if (item.id === 'quick-links') short = '빠른';

                    return {
                        id: item.id,
                        label: item.label,
                        shortLabel: short,
                        accent: item.accent as 'blue' | 'violet' | 'emerald' | 'amber' | 'neutral' | 'slate' | 'cyan',
                        onClick: item.onClick,
                        testId: `admin-launcher-${item.id}`,
                        icon: <div className="flex items-center justify-center w-10 h-10 rounded-full bg-white/5 border border-white/10 mb-1 text-lg group-hover:bg-white/10 transition-colors">{emoji}</div> 
                    };
                })}
                topActions={(
                    <>
                        <button
                            type="button"
                            onClick={() => loadDashboard(true)}
                            data-testid="admin-topnav-refresh"
                            aria-label="관리자 대시보드 새로고침"
                            className="workspace-topbar-chip"
                            disabled={refreshing}
                        >
                            {refreshing ? '⏳' : '🔄'}
                        </button>
                        <button type="button" onClick={handleLogout} data-testid="admin-topnav-logout" aria-label="로그아웃" className="workspace-ghost-button">
                            🚪
                        </button>
                    </>
                )}
            >
                <div style={{ maxWidth: '800px', margin: '0 auto', minHeight: 'calc(100vh - 120px)', display: 'flex', flexDirection: 'column', justifyContent: 'center' }}>
                    <div style={{ textAlign: 'center', marginBottom: '40px' }}>
                        <h2 style={{ fontSize: '28px', fontWeight: 600, color: 'white', marginBottom: '8px' }}>GenSpark 스타일 AI 워크스페이스 4.0</h2>
                        <p style={{ color: 'rgba(255,255,255,0.6)', fontSize: '15px' }}>무엇이든 물어보고 만들어보세요 (관리자 전용)</p>
                    </div>
                    <div className="w-full">
                        <AdminManualOrchestratorSection {...adminManualOrchestratorAssembly} />
                    </div>
                </div>

                <AdminManagementSection
                    title="🧭 전역 .env 설정 패널"
                    usage="프로그램 전반 운영값과 연결 설정을 중앙 관리"
                    description="도메인, 저장 경로, LLM 기본 환경값, 셀프 엔진 연동 설정을 첫 화면 핵심 카드 아래 바로 붙입니다."
                    open={systemSettingsPanelOpen}
                    onToggle={() => setSystemSettingsPanelOpen((prev) => !prev)}
                    toggleTestId="admin-system-settings-section"
                    windowSize="full"
                    launcherHidden
                >
                    <AdminSystemSettingsPanel {...adminSystemSettingsAssembly} />
                </AdminManagementSection>

                {boardSections.filter(section => section.id !== 'manual').map((section) => (
                    <AdminManagementSection
                        key={section.id}
                        title={section.title}
                        usage={section.usage}
                        description={section.description}
                        open={section.open}
                        onToggle={section.onToggle}
                        toggleTestId={section.toggleTestId || `admin-${section.id}-section`}
                        windowSize={section.windowSize}
                        launcherHidden
                    >
                        {section.body}
                    </AdminManagementSection>
                ))}
            </WorkspaceChrome>

            <AdminAdPreviewModal
                order={adPreviewOrder}
                previewUrl={adPreviewUrl}
                previewError={adPreviewError}
                onClose={closeAdPreview}
            />

            <AdminStoryboardModal
                modal={adStoryboardModal}
                currentDiff={currentAdStoryboardModalDiff}
                currentIndex={currentAdStoryboardModalIndex}
                onClose={() => setAdStoryboardModal(null)}
                onMoveCut={moveAdStoryboardModalCut}
            />
        </div>
    );
}
