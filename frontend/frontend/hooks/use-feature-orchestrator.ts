'use client';

import * as React from 'react';
import { resolveApiBaseUrl } from '@shared/api';
import { MARKETPLACE_ORCHESTRATOR_BRIDGE_KEY, type MarketplaceOrchestratorBridgePayload } from '@/lib/admin-orchestrator-bridge';

export type FeaturePopupState =
    | 'idle'
    | 'accepted'
    | 'preview_running'
    | 'preview_ready'
    | 'final_running'
    | 'quality_review'
    | 'completed'
    | 'completed_preview_only'
    | 'failed';

export type FeatureCatalogItem = {
    feature_id: string;
    title: string;
    summary: string;
    popup_mode: string;
    status: string;
    supports_photo_upload: boolean;
    supports_final_phase: boolean;
};

export type FeatureArtifact = {
    artifact_id?: string | null;
    artifact_type?: string;
    phase?: string;
    state?: string;
    title?: string;
    image_data_url?: string;
    prompt_summary?: string;
    keywords?: string[];
    composition?: {
        template_id?: string;
        photo_reference?: string;
        warnings?: string[];
    };
    sheet_schema?: {
        sheet_name?: string;
        row_goal?: number;
        columns?: Array<{
            name: string;
            type: string;
        }>;
    };
    workbook?: {
        sheet_name?: string;
        column_count?: number;
        row_count?: number;
        sample_rows?: Array<Record<string, unknown>>;
    };
    delivery_assets?: Array<{
        format?: string;
        path?: string;
        path_hint?: string;
        mime_type?: string;
        size_bytes?: number;
        exists?: boolean;
        generated_at?: string;
    }>;
    generated_at?: string;
    notes?: string[];
};

export type FeatureStreamConnection = 'idle' | 'connecting' | 'streaming' | 'completed' | 'failed';

export type FeatureLiveViewArtifact = {
    title: string;
    caption: string;
    image_data_url: string;
    source: 'upload' | 'preview' | 'final';
};

export type SpreadsheetDownloadLink = {
    format: string;
    href: string;
    fileName: string;
    sizeLabel: string;
    ready: boolean;
    completedAt: string;
    completedAtLabel: string;
};

export type SpreadsheetRunSummary = {
    stageLabel: string;
    stageDescription: string;
    sheetName: string;
    columnCount: number;
    rowCount: number;
    promptSummary: string;
};

export type FeatureProgressSnapshot = {
    percent: number;
    step: string;
    state?: FeaturePopupState;
    message: string;
    updated_at: string;
};

type FeatureQualityReview = {
    passed?: boolean;
    score?: number;
    issues?: string[];
    checks?: Record<string, boolean>;
};

type FeatureStageRun = {
    run_id: string;
    current_stage_id?: string;
    status?: string;
    final_completed?: boolean;
};

type StreamEnvelope = {
    event: string;
    payload: {
        run_id?: string;
        state?: FeaturePopupState;
        artifact?: FeatureArtifact;
        artifact_manifest?: {
            preview_artifact?: FeatureArtifact;
            final_artifact?: FeatureArtifact;
        };
        quality_review?: FeatureQualityReview;
        progress?: FeatureProgressSnapshot;
        message?: string;
    };
};

type FeaturePreset = {
    projectName: string;
    prompt: string;
    templateId: string;
    finalEnabled: boolean;
    contextTags: string[];
};

const FEATURE_PRESETS: Record<string, FeaturePreset> = {
    'ai-sheet': {
        projectName: 'marketplace-sheet-run',
        prompt: '영업 리드 관리용 엑셀 시트를 만들어주세요. 컬럼은 고객사명, 담당자, 예상 매출, 미팅일을 포함하고 24행 샘플 데이터를 채워주세요.',
        templateId: 'sheet-schema-template',
        finalEnabled: true,
        contextTags: ['marketplace-popup', 'spreadsheet-builder'],
    },
    'ai-image': {
        projectName: 'marketplace-image-run',
        prompt: '',
        templateId: 'ad-photo-template',
        finalEnabled: true,
        contextTags: ['marketplace-popup', 'hybrid-image'],
    },
    'ai-music': {
        projectName: 'marketplace-music-run',
        prompt: '브랜드 런칭 티저용 30초 음악 트랙을 생성해주세요. 도입부는 차분하게, 후반부는 고조되는 구조로 구성해주세요.',
        templateId: 'music-track-template',
        finalEnabled: true,
        contextTags: ['marketplace-popup', 'music-generator'],
    },
    'ai-document': {
        projectName: 'marketplace-document-run',
        prompt: '신규 서비스 제안서를 위한 목차 preview 와 최종 문서 패키지를 만들어주세요.',
        templateId: 'document-outline-template',
        finalEnabled: true,
        contextTags: ['marketplace-popup', 'document-builder'],
    },
    'ai-video': {
        projectName: 'marketplace-video-run',
        prompt: '제품 소개용 15초 영상 스토리보드와 최종 렌더 패키지를 생성해주세요.',
        templateId: 'video-storyboard-template',
        finalEnabled: true,
        contextTags: ['marketplace-popup', 'video-generator'],
    },
};

function getFeaturePreset(featureId: string): FeaturePreset {
    return FEATURE_PRESETS[featureId] || FEATURE_PRESETS['ai-sheet'];
}

function formatFileSizeLabel(size?: number): string {
    const safe = Number(size || 0);
    if (!Number.isFinite(safe) || safe <= 0) {
        return '0 B';
    }
    if (safe >= 1024 * 1024) {
        return `${(safe / (1024 * 1024)).toFixed(1)} MB`;
    }
    if (safe >= 1024) {
        return `${(safe / 1024).toFixed(1)} KB`;
    }
    return `${Math.round(safe)} B`;
}

function buildBridgePrompt(bridge: MarketplaceOrchestratorBridgePayload): {
    featureId: string;
    projectName: string;
    prompt: string;
} {
    if (bridge.source === 'admin-dashboard') {
        return {
            featureId: 'ai-image',
            projectName: bridge.title || 'admin-dashboard-image-request',
            prompt: [bridge.title, bridge.imagePrompt, bridge.backgroundPrompt, bridge.captionText, bridge.scenarioScript].filter(Boolean).join('\n'),
        };
    }
    return {
        featureId: 'ai-image',
        projectName: bridge.projectName || 'admin-llm-image-request',
        prompt: bridge.task,
    };
}

export function useFeatureOrchestrator() {
    const apiBaseUrl = React.useMemo(() => resolveApiBaseUrl(), []);
    const initialPreset = React.useMemo(() => getFeaturePreset('ai-sheet'), []);
    const [catalog, setCatalog] = React.useState<FeatureCatalogItem[]>([]);
    const [catalogLoading, setCatalogLoading] = React.useState(true);
    const [catalogError, setCatalogError] = React.useState('');
    const [isPopupOpen, setIsPopupOpen] = React.useState(false);
    const [activeFeatureId, setActiveFeatureId] = React.useState<string>('ai-sheet');
    const [projectName, setProjectName] = React.useState(initialPreset.projectName);
    const [prompt, setPrompt] = React.useState(initialPreset.prompt);
    const [templateId, setTemplateId] = React.useState(initialPreset.templateId);
    const [finalEnabled, setFinalEnabled] = React.useState(initialPreset.finalEnabled);
    const [photoFileName, setPhotoFileName] = React.useState('');
    const [photoContentType, setPhotoContentType] = React.useState('');
    const [photoSize, setPhotoSize] = React.useState<number | undefined>(undefined);
    const [photoPreviewUrl, setPhotoPreviewUrl] = React.useState('');
    const [popupState, setPopupState] = React.useState<FeaturePopupState>('idle');
    const [runId, setRunId] = React.useState('');
    const [stageRun, setStageRun] = React.useState<FeatureStageRun | null>(null);
    const [previewArtifact, setPreviewArtifact] = React.useState<FeatureArtifact | null>(null);
    const [finalArtifact, setFinalArtifact] = React.useState<FeatureArtifact | null>(null);
    const [qualityReview, setQualityReview] = React.useState<FeatureQualityReview | null>(null);
    const [submitLoading, setSubmitLoading] = React.useState(false);
    const [errorText, setErrorText] = React.useState('');
    const [eventLog, setEventLog] = React.useState<Array<{ state: FeaturePopupState; at: string }>>([]);
    const [streamConnection, setStreamConnection] = React.useState<FeatureStreamConnection>('idle');
    const [streamStartedAt, setStreamStartedAt] = React.useState('');
    const [latestEventAt, setLatestEventAt] = React.useState('');
    const [elapsedSeconds, setElapsedSeconds] = React.useState(0);
    const [progressSnapshot, setProgressSnapshot] = React.useState<FeatureProgressSnapshot | null>(null);
    const [progressHistory, setProgressHistory] = React.useState<FeatureProgressSnapshot[]>([]);

    const activeFeature = React.useMemo(() => catalog.find((item) => item.feature_id === activeFeatureId) || null, [activeFeatureId, catalog]);

    React.useEffect(() => {
        let cancelled = false;
        const loadCatalog = async () => {
            setCatalogLoading(true);
            setCatalogError('');
            try {
                const response = await fetch(`${apiBaseUrl}/api/marketplace/feature-catalog`, { cache: 'no-store' });
                const payload = await response.json().catch(() => []);
                if (!response.ok) {
                    throw new Error('feature catalog 를 불러오지 못했습니다.');
                }
                if (!cancelled) {
                    setCatalog(Array.isArray(payload) ? payload : []);
                }
            } catch (error: any) {
                if (!cancelled) {
                    setCatalogError(error?.message || 'feature catalog 를 불러오지 못했습니다.');
                }
            } finally {
                if (!cancelled) {
                    setCatalogLoading(false);
                }
            }
        };
        void loadCatalog();
        return () => {
            cancelled = true;
        };
    }, [apiBaseUrl]);

    React.useEffect(() => () => {
        if (photoPreviewUrl) {
            window.URL.revokeObjectURL(photoPreviewUrl);
        }
    }, [photoPreviewUrl]);

    React.useEffect(() => {
        if (!streamStartedAt) {
            setElapsedSeconds(0);
            return;
        }
        const updateElapsed = () => {
            const startedAt = new Date(streamStartedAt).getTime();
            if (Number.isNaN(startedAt)) {
                setElapsedSeconds(0);
                return;
            }
            setElapsedSeconds(Math.max(0, Math.floor((Date.now() - startedAt) / 1000)));
        };
        updateElapsed();
        if (streamConnection !== 'connecting' && streamConnection !== 'streaming') {
            return;
        }
        const timer = window.setInterval(updateElapsed, 1000);
        return () => {
            window.clearInterval(timer);
        };
    }, [streamConnection, streamStartedAt]);

    React.useEffect(() => {
        if (typeof window === 'undefined') {
            return;
        }
        const raw = window.localStorage.getItem(MARKETPLACE_ORCHESTRATOR_BRIDGE_KEY);
        if (!raw) {
            return;
        }
        try {
            const parsed = JSON.parse(raw) as MarketplaceOrchestratorBridgePayload;
            const bridged = buildBridgePrompt(parsed);
            React.startTransition(() => {
                setActiveFeatureId(bridged.featureId);
                setProjectName(bridged.projectName);
                setPrompt(bridged.prompt);
                setIsPopupOpen(true);
            });
        } catch {
        } finally {
            window.localStorage.removeItem(MARKETPLACE_ORCHESTRATOR_BRIDGE_KEY);
        }
    }, []);

    const openFeature = React.useCallback((featureId: string) => {
        const preset = getFeaturePreset(featureId);
        setActiveFeatureId(featureId);
        setProjectName(preset.projectName);
        setPrompt(preset.prompt);
        setTemplateId(preset.templateId);
        setFinalEnabled(preset.finalEnabled);
        setErrorText('');
        setIsPopupOpen(true);
    }, []);

    const closePopup = React.useCallback(() => {
        setIsPopupOpen(false);
    }, []);

    const applyPhotoFile = React.useCallback((file: File | null) => {
        if (photoPreviewUrl) {
            window.URL.revokeObjectURL(photoPreviewUrl);
        }
        if (!file) {
            setPhotoFileName('');
            setPhotoContentType('');
            setPhotoSize(undefined);
            setPhotoPreviewUrl('');
            return;
        }
        setPhotoFileName(file.name);
        setPhotoContentType(file.type || 'application/octet-stream');
        setPhotoSize(file.size);
        setPhotoPreviewUrl(window.URL.createObjectURL(file));
    }, [photoPreviewUrl]);

    const refreshStageRun = React.useCallback(async (nextRunId: string) => {
        const response = await fetch(`${apiBaseUrl}/api/marketplace/feature-orchestrate/stage-runs/${encodeURIComponent(nextRunId)}`, { cache: 'no-store' });
        const payload = await response.json().catch(() => null);
        if (response.ok && payload) {
            setStageRun(payload as FeatureStageRun);
        }
    }, [apiBaseUrl]);

    React.useEffect(() => {
        if (!runId || (streamConnection !== 'connecting' && streamConnection !== 'streaming')) {
            return;
        }
        let cancelled = false;
        let timer: number | null = null;
        const pollStageRun = async () => {
            try {
                await refreshStageRun(runId);
            } catch {
            } finally {
                if (!cancelled) {
                    timer = window.setTimeout(() => {
                        void pollStageRun();
                    }, 1500);
                }
            }
        };
        void pollStageRun();
        return () => {
            cancelled = true;
            if (timer !== null) {
                window.clearTimeout(timer);
            }
        };
    }, [refreshStageRun, runId, streamConnection]);

    const applyEvent = React.useCallback(async (envelope: StreamEnvelope) => {
        const nextState = envelope.payload.state;
        const observedAt = new Date().toISOString();
        setLatestEventAt(observedAt);
        if (nextState) {
            setPopupState(nextState);
            setEventLog((prev) => [...prev, { state: nextState, at: observedAt }]);
            if (nextState === 'failed') {
                setStreamConnection('failed');
            } else if (nextState === 'completed' || nextState === 'completed_preview_only') {
                setStreamConnection('completed');
            } else {
                setStreamConnection('streaming');
            }
        }
        if (envelope.payload.progress) {
            setProgressSnapshot(envelope.payload.progress);
            setProgressHistory((prev) => [...prev, envelope.payload.progress as FeatureProgressSnapshot]);
        }
        if (envelope.payload.artifact) {
            setPreviewArtifact(envelope.payload.artifact);
        }
        if (envelope.event === 'quality_review' && envelope.payload.quality_review) {
            setQualityReview(envelope.payload.quality_review);
        }
        if (envelope.event === 'completed') {
            if (envelope.payload.artifact_manifest?.preview_artifact) {
                setPreviewArtifact(envelope.payload.artifact_manifest.preview_artifact);
            }
            if (envelope.payload.artifact_manifest?.final_artifact) {
                setFinalArtifact(envelope.payload.artifact_manifest.final_artifact);
            }
            if (envelope.payload.quality_review) {
                setQualityReview(envelope.payload.quality_review);
            }
        }
        if (envelope.event === 'failed') {
            setStreamConnection('failed');
            setErrorText(envelope.payload.message || 'feature orchestrator 실행 중 오류가 발생했습니다.');
        }
        if (envelope.payload.run_id) {
            await refreshStageRun(envelope.payload.run_id);
        }
    }, [refreshStageRun]);

    const submitFeature = React.useCallback(async () => {
        if (!prompt.trim()) {
            setErrorText('프롬프트를 입력하세요.');
            return;
        }
        setSubmitLoading(true);
        setErrorText('');
        setPopupState('accepted');
        setPreviewArtifact(null);
        setFinalArtifact(null);
        setQualityReview(null);
        const startedAt = new Date().toISOString();
        setEventLog([{ state: 'accepted', at: startedAt }]);
        setStreamConnection('connecting');
        setStreamStartedAt(startedAt);
        setLatestEventAt(startedAt);
        setProgressSnapshot({ percent: 0, step: 'accepted', state: 'accepted', message: '요청을 수락하고 스트림 연결을 준비합니다.', updated_at: startedAt });
        setProgressHistory([{ percent: 0, step: 'accepted', state: 'accepted', message: '요청을 수락하고 스트림 연결을 준비합니다.', updated_at: startedAt }]);
        try {
            const acceptedResponse = await fetch(`${apiBaseUrl}/api/marketplace/feature-orchestrate/accepted`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    feature_id: activeFeatureId,
                    project_name: projectName,
                    prompt,
                    template_id: templateId,
                    photo_reference: photoFileName || undefined,
                    photo_content_type: photoContentType || undefined,
                    photo_size: photoSize,
                    final_enabled: finalEnabled,
                    context_tags: getFeaturePreset(activeFeatureId).contextTags,
                }),
            });
            const acceptedPayload = await acceptedResponse.json().catch(() => null);
            if (!acceptedResponse.ok || !acceptedPayload) {
                throw new Error('feature orchestrator accepted 요청에 실패했습니다.');
            }
            setRunId(String(acceptedPayload.run_id || ''));
            setStageRun((acceptedPayload.stage_run || null) as FeatureStageRun | null);

            const streamResponse = await fetch(`${apiBaseUrl}/api/marketplace/feature-orchestrate/stream`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ run_id: acceptedPayload.run_id }),
            });
            if (!streamResponse.ok || !streamResponse.body) {
                throw new Error('feature orchestrator stream 연결에 실패했습니다.');
            }
            setStreamConnection('streaming');
            const reader = streamResponse.body.getReader();
            const decoder = new TextDecoder('utf-8');
            let buffer = '';
            while (true) {
                const { value, done } = await reader.read();
                if (done) {
                    break;
                }
                buffer += decoder.decode(value, { stream: true });
                const chunks = buffer.split('\n\n');
                buffer = chunks.pop() || '';
                for (const chunk of chunks) {
                    const line = chunk.split('\n').find((item) => item.startsWith('data: '));
                    if (!line) {
                        continue;
                    }
                    const envelope = JSON.parse(line.slice(6)) as StreamEnvelope;
                    await applyEvent(envelope);
                }
            }
            setStreamConnection((current) => (current === 'failed' ? current : 'completed'));
        } catch (error: any) {
            setPopupState('failed');
            setStreamConnection('failed');
            setLatestEventAt(new Date().toISOString());
            setErrorText(error?.message || 'feature orchestrator 실행에 실패했습니다.');
        } finally {
            setSubmitLoading(false);
        }
    }, [activeFeatureId, apiBaseUrl, applyEvent, finalEnabled, photoContentType, photoFileName, photoSize, projectName, prompt, templateId]);

    const liveViewArtifact = React.useMemo<FeatureLiveViewArtifact | null>(() => {
        if (finalArtifact?.image_data_url) {
            return {
                title: '최종 산출물 라이브뷰',
                caption: 'final artifact 가 도착하면 즉시 최신 결과를 전면에 보여줍니다.',
                image_data_url: finalArtifact.image_data_url,
                source: 'final',
            };
        }
        if (previewArtifact?.image_data_url) {
            return {
                title: '프리뷰 라이브뷰',
                caption: 'preview 단계에서 준비된 이미지를 실시간으로 노출합니다.',
                image_data_url: previewArtifact.image_data_url,
                source: 'preview',
            };
        }
        if (photoPreviewUrl) {
            return {
                title: '참조 이미지 대기 화면',
                caption: '생성 전에는 업로드한 참조 이미지를 기준 화면으로 유지합니다.',
                image_data_url: photoPreviewUrl,
                source: 'upload',
            };
        }
        return null;
    }, [finalArtifact, photoPreviewUrl, previewArtifact]);

    const spreadsheetDownloadLinks = React.useMemo<SpreadsheetDownloadLink[]>(() => {
        if (activeFeatureId !== 'ai-sheet' || !runId) {
            return [];
        }
        return (finalArtifact?.delivery_assets || [])
            .filter((asset) => Boolean(asset.format))
            .map((asset) => {
                const format = String(asset.format || '').toLowerCase();
                const generatedAt = String(asset.generated_at || finalArtifact?.generated_at || latestEventAt || '');
                return {
                    format,
                    href: `${apiBaseUrl}/api/marketplace/feature-orchestrate/stage-runs/${encodeURIComponent(runId)}/delivery-assets/${encodeURIComponent(format)}`,
                    fileName: `${projectName || 'spreadsheet-result'}.${format || 'bin'}`,
                    sizeLabel: formatFileSizeLabel(asset.size_bytes),
                    ready: Boolean(asset.exists),
                    completedAt: generatedAt,
                    completedAtLabel: generatedAt ? new Date(generatedAt).toLocaleString('ko-KR') : '기록 없음',
                };
            });
    }, [activeFeatureId, apiBaseUrl, finalArtifact?.delivery_assets, finalArtifact?.generated_at, latestEventAt, projectName, runId]);

    const spreadsheetRunSummary = React.useMemo<SpreadsheetRunSummary | null>(() => {
        if (activeFeatureId !== 'ai-sheet') {
            return null;
        }
        const sheetSchema = previewArtifact?.sheet_schema;
        const workbook = finalArtifact?.workbook;
        const stageMap: Record<FeaturePopupState, { label: string; description: string }> = {
            idle: { label: '대기', description: 'spreadsheet-builder 실행 전 상태입니다.' },
            accepted: { label: '요청 수락', description: '백엔드가 시트 생성 작업을 수락하고 실행 준비를 시작했습니다.' },
            preview_running: { label: '시트 schema 생성 중', description: '컬럼 구조와 목표 행 수를 계산하고 있습니다.' },
            preview_ready: { label: 'schema preview 준비', description: '시트 schema preview 결과를 확인할 수 있습니다.' },
            final_running: { label: 'workbook 패키징 중', description: 'xlsx/csv workbook 패키지를 생성하고 있습니다.' },
            quality_review: { label: 'quality 검토', description: '생성된 workbook 과 delivery asset 계약을 검증하고 있습니다.' },
            completed: { label: '완료', description: '최종 workbook 패키지와 다운로드 자산이 준비되었습니다.' },
            completed_preview_only: { label: 'preview 중심 완료', description: 'preview 기준 결과를 유지하며 final 품질 승격은 보류되었습니다.' },
            failed: { label: '실패', description: '실행 중 오류가 발생했습니다. 로그와 오류 메시지를 확인하세요.' },
        };
        const summary = stageMap[popupState];
        return {
            stageLabel: summary.label,
            stageDescription: summary.description,
            sheetName: String(workbook?.sheet_name || sheetSchema?.sheet_name || 'GeneratedSheet'),
            columnCount: Number(workbook?.column_count || sheetSchema?.columns?.length || 0),
            rowCount: Number(workbook?.row_count || sheetSchema?.row_goal || 0),
            promptSummary: String(finalArtifact?.prompt_summary || previewArtifact?.prompt_summary || prompt || ''),
        };
    }, [activeFeatureId, finalArtifact?.prompt_summary, finalArtifact?.workbook, popupState, previewArtifact?.prompt_summary, previewArtifact?.sheet_schema, prompt]);

    return {
        catalog,
        catalogLoading,
        catalogError,
        activeFeature,
        activeFeatureId,
        isPopupOpen,
        openFeature,
        closePopup,
        projectName,
        setProjectName,
        prompt,
        setPrompt,
        templateId,
        setTemplateId,
        finalEnabled,
        setFinalEnabled,
        photoFileName,
        photoPreviewUrl,
        applyPhotoFile,
        popupState,
        runId,
        stageRun,
        previewArtifact,
        finalArtifact,
        qualityReview,
        submitLoading,
        submitFeature,
        errorText,
        eventLog,
        streamConnection,
        streamStartedAt,
        latestEventAt,
        elapsedSeconds,
        liveViewArtifact,
        spreadsheetDownloadLinks,
        spreadsheetRunSummary,
        progressSnapshot,
        progressHistory,
    };
}