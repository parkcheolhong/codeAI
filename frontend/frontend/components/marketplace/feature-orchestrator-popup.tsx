'use client';

import * as React from 'react';
import type { FeatureArtifact, FeatureLiveViewArtifact, FeaturePopupState, FeatureProgressSnapshot, FeatureStreamConnection, SpreadsheetDownloadLink, SpreadsheetRunSummary } from '@/hooks/use-feature-orchestrator';

type SpreadsheetFeatureArtifact = FeatureArtifact & {
    prompt_summary?: string;
    keywords?: string[];
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
        size_bytes?: number;
        exists?: boolean;
        generated_at?: string;
    }>;
    generated_at?: string;
};

const POPUP_STATE_FLOW: FeaturePopupState[] = ['accepted', 'preview_running', 'preview_ready', 'final_running', 'quality_review', 'completed'];

const STATE_LABELS: Record<FeaturePopupState, string> = {
    idle: '대기',
    accepted: '수락됨',
    preview_running: 'preview 실행 중',
    preview_ready: 'preview 준비 완료',
    final_running: 'final 실행 중',
    quality_review: '품질 검토',
    completed: '완료',
    completed_preview_only: 'preview 전용 완료',
    failed: '실패',
};

const CONNECTION_LABELS: Record<FeatureStreamConnection, string> = {
    idle: '대기',
    connecting: '스트림 연결 중',
    streaming: '실시간 수신 중',
    completed: '라이브뷰 완료',
    failed: '라이브뷰 실패',
};

function stateLabel(state: FeaturePopupState) {
    return STATE_LABELS[state];
}

function connectionLabel(state: FeatureStreamConnection) {
    return CONNECTION_LABELS[state];
}

function formatElapsed(seconds: number) {
    const safeSeconds = Math.max(0, Math.floor(seconds || 0));
    const mins = Math.floor(safeSeconds / 60);
    const secs = safeSeconds % 60;
    return `${String(mins).padStart(2, '0')}:${String(secs).padStart(2, '0')}`;
}

function progressWidthClass(percent: number) {
    const normalized = Math.max(0, Math.min(100, Math.round((percent || 0) / 5) * 5));
    return {
        0: 'w-0',
        5: 'w-[5%]',
        10: 'w-[10%]',
        15: 'w-[15%]',
        20: 'w-[20%]',
        25: 'w-[25%]',
        30: 'w-[30%]',
        35: 'w-[35%]',
        40: 'w-[40%]',
        45: 'w-[45%]',
        50: 'w-[50%]',
        55: 'w-[55%]',
        60: 'w-[60%]',
        65: 'w-[65%]',
        70: 'w-[70%]',
        75: 'w-[75%]',
        80: 'w-[80%]',
        85: 'w-[85%]',
        90: 'w-[90%]',
        95: 'w-[95%]',
        100: 'w-full',
    }[normalized as 0 | 5 | 10 | 15 | 20 | 25 | 30 | 35 | 40 | 45 | 50 | 55 | 60 | 65 | 70 | 75 | 80 | 85 | 90 | 95 | 100];
}

function formatBytes(size: number) {
    if (!Number.isFinite(size) || size <= 0) {
        return '0 B';
    }
    if (size >= 1024 * 1024) {
        return `${(size / (1024 * 1024)).toFixed(1)} MB`;
    }
    if (size >= 1024) {
        return `${(size / 1024).toFixed(1)} KB`;
    }
    return `${Math.round(size)} B`;
}

function normalizeSpreadsheetType(value?: string | null) {
    const normalized = String(value || '').trim().toLowerCase();
    if (!normalized) {
        return 'text';
    }
    if (['number', 'numeric', 'decimal', 'integer', 'int', 'float', 'double', 'currency', 'amount', 'price'].some((token) => normalized.includes(token))) {
        return 'number';
    }
    if (['date', 'datetime', 'timestamp', 'time'].some((token) => normalized.includes(token))) {
        return 'date';
    }
    return 'text';
}

function inferCellType(value: unknown, declaredType?: string | null) {
    const normalizedDeclaredType = normalizeSpreadsheetType(declaredType);
    if (normalizedDeclaredType !== 'text') {
        return normalizedDeclaredType;
    }

    if (typeof value === 'number' && Number.isFinite(value)) {
        return 'number';
    }

    if (value instanceof Date && !Number.isNaN(value.getTime())) {
        return 'date';
    }

    if (typeof value === 'string') {
        const trimmed = value.trim();
        if (!trimmed) {
            return 'text';
        }
        const numericValue = Number(trimmed.replace(/,/g, ''));
        if (!Number.isNaN(numericValue) && /^[-+]?\d[\d,]*(\.\d+)?$/.test(trimmed)) {
            return 'number';
        }
        const parsedDate = Date.parse(trimmed);
        if (!Number.isNaN(parsedDate) && /[-/:.년월일T]/.test(trimmed)) {
            return 'date';
        }
    }

    return 'text';
}

function workbookCellClassName(value: unknown, declaredType?: string | null) {
    const cellType = inferCellType(value, declaredType);
    if (cellType === 'number') {
        return 'border-b border-[#1e2a3c] px-3 py-2 text-right font-medium tabular-nums text-[#ffd37a]';
    }
    if (cellType === 'date') {
        return 'border-b border-[#1e2a3c] px-3 py-2 text-center font-medium text-[#8ec5ff]';
    }
    return 'border-b border-[#1e2a3c] px-3 py-2 text-left text-[#e6edf3]';
}

function renderSchemaTable(columns: Array<{ name: string; type: string }>, rowGoal: number) {
    return (
        <div data-testid="marketplace-schema-preview-table" className="mt-3 overflow-hidden rounded-2xl border border-[#1e2a3c] bg-[#10182b]">
            <table className="min-w-full border-collapse text-left text-xs text-[#d2d9e3]">
                <thead className="bg-[#111a2a] text-[#8ec5ff]">
                    <tr>
                        <th className="border-b border-[#1e2a3c] px-3 py-2 font-semibold">#</th>
                        <th className="border-b border-[#1e2a3c] px-3 py-2 font-semibold">컬럼명</th>
                        <th className="border-b border-[#1e2a3c] px-3 py-2 font-semibold">타입</th>
                        <th className="border-b border-[#1e2a3c] px-3 py-2 font-semibold">목표 행</th>
                    </tr>
                </thead>
                <tbody>
                    {columns.map((column, index) => (
                        <tr key={`${column.name}-${column.type}`} className="odd:bg-[#10182b] even:bg-[#0d1420]">
                            <td className="border-b border-[#1e2a3c] px-3 py-2 text-[#8ea4bf]">{index + 1}</td>
                            <td className="border-b border-[#1e2a3c] px-3 py-2 font-semibold text-white">{column.name}</td>
                            <td className="border-b border-[#1e2a3c] px-3 py-2 text-[#8ec5ff]">{column.type}</td>
                            <td className="border-b border-[#1e2a3c] px-3 py-2 text-[#8ea4bf]">{rowGoal}</td>
                        </tr>
                    ))}
                </tbody>
            </table>
        </div>
    );
}

function renderWorkbookRowsTable(rows: Array<Record<string, unknown>>, columns?: Array<{ name: string; type: string }>) {
    if (!rows.length) {
        return null;
    }
    const headers = Object.keys(rows[0] || {});
    const declaredTypes = new Map((columns || []).map((column) => [column.name, column.type]));
    return (
        <div data-testid="marketplace-workbook-sample-rows" className="mt-3 overflow-hidden rounded-2xl border border-[#1e2a3c] bg-[#10182b]">
            <table className="min-w-full border-collapse text-left text-xs text-[#d2d9e3]">
                <thead className="bg-[#111a2a] text-[#8ec5ff]">
                    <tr>
                        <th data-testid="marketplace-workbook-row-number-header" className="border-b border-[#1e2a3c] px-3 py-2 text-center font-semibold">행</th>
                        {headers.map((header) => (
                            <th key={header} className="border-b border-[#1e2a3c] px-3 py-2 font-semibold">{header}</th>
                        ))}
                    </tr>
                </thead>
                <tbody>
                    {rows.map((row, rowIndex) => (
                        <tr key={`sample-row-${rowIndex}`} className="odd:bg-[#10182b] even:bg-[#0d1420]">
                            <td data-testid={`marketplace-workbook-row-number-${rowIndex + 1}`} className="border-b border-[#1e2a3c] px-3 py-2 text-center font-semibold text-[#8ea4bf] tabular-nums">{rowIndex + 1}</td>
                            {headers.map((header) => (
                                <td key={`${rowIndex}-${header}`} className={workbookCellClassName(row[header], declaredTypes.get(header))}>{String(row[header] ?? '')}</td>
                            ))}
                        </tr>
                    ))}
                </tbody>
            </table>
        </div>
    );
}

function renderSpreadsheetArtifact(artifact: FeatureArtifact) {
    const spreadsheetArtifact = artifact as SpreadsheetFeatureArtifact;
    const schema = spreadsheetArtifact.sheet_schema;
    const workbook = spreadsheetArtifact.workbook;
    const deliveryAssets = spreadsheetArtifact.delivery_assets || [];

    return (
        <div className="mt-3 space-y-3 text-sm text-[#d2d9e3]">
            {spreadsheetArtifact.prompt_summary && (
                <div className="rounded-xl border border-[#2d3d56] bg-[#0b1019] px-4 py-3">
                    <p className="text-xs uppercase tracking-[0.18em] text-[#7fa7d8]">Prompt Summary</p>
                    <p className="mt-2 text-sm text-white">{spreadsheetArtifact.prompt_summary}</p>
                </div>
            )}
            {!!spreadsheetArtifact.keywords?.length && (
                <div className="flex flex-wrap gap-2">
                    {spreadsheetArtifact.keywords.map((keyword: string) => (
                        <span key={keyword} className="rounded-full border border-[#30363d] bg-[#151b23] px-3 py-1 text-xs text-[#d2d9e3]">#{keyword}</span>
                    ))}
                </div>
            )}
            {schema && (
                <div className="rounded-xl border border-[#2d3d56] bg-[#0b1019] p-4">
                    <div className="flex items-center justify-between gap-3">
                        <p className="font-semibold text-white">Sheet Schema Preview</p>
                        <span className="text-xs text-[#8ea4bf]">{schema.sheet_name || 'GeneratedSheet'}</span>
                    </div>
                    {renderSchemaTable(schema.columns || [], Number(schema.row_goal || 0))}
                </div>
            )}
            {workbook && (
                <div className="rounded-xl border border-[#2d3d56] bg-[#0b1019] p-4">
                    <p className="font-semibold text-white">Workbook Package</p>
                    <div className="mt-3 grid gap-3 sm:grid-cols-3">
                        <div className="rounded-xl border border-[#1e2a3c] bg-[#10182b] px-3 py-2">
                            <p className="text-xs text-[#8ea4bf]">Sheet</p>
                            <p className="mt-1 text-sm font-semibold text-white">{workbook.sheet_name || 'GeneratedSheet'}</p>
                        </div>
                        <div className="rounded-xl border border-[#1e2a3c] bg-[#10182b] px-3 py-2">
                            <p className="text-xs text-[#8ea4bf]">Columns</p>
                            <p className="mt-1 text-sm font-semibold text-white">{workbook.column_count || 0}</p>
                        </div>
                        <div className="rounded-xl border border-[#1e2a3c] bg-[#10182b] px-3 py-2">
                            <p className="text-xs text-[#8ea4bf]">Rows</p>
                            <p className="mt-1 text-sm font-semibold text-white">{workbook.row_count || 0}</p>
                        </div>
                    </div>
                    {renderWorkbookRowsTable(workbook.sample_rows || [], schema?.columns || [])}
                </div>
            )}
            {!!deliveryAssets.length && (
                <div className="rounded-xl border border-[#2d3d56] bg-[#0b1019] p-4">
                    <p className="font-semibold text-white">Delivery Assets</p>
                    <div className="mt-3 space-y-2">
                        {deliveryAssets.map((asset: NonNullable<SpreadsheetFeatureArtifact['delivery_assets']>[number]) => (
                            <div key={`${asset.format}-${asset.path}`} className="flex items-center justify-between gap-3 rounded-xl border border-[#1e2a3c] bg-[#10182b] px-3 py-2 text-xs">
                                <div>
                                    <p className="font-semibold text-white">{String(asset.format || '').toUpperCase() || 'FILE'}</p>
                                    <p className="mt-1 text-[#8ea4bf]">{asset.path_hint || asset.path || 'path unavailable'}</p>
                                </div>
                                <div className="text-right">
                                    <p className={`${asset.exists ? 'text-[#7af0a0]' : 'text-[#f0b43f]'}`}>{asset.exists ? 'ready' : 'missing'}</p>
                                    <p className="mt-1 text-[#8ea4bf]">{formatBytes(Number(asset.size_bytes || 0))}</p>
                                </div>
                            </div>
                        ))}
                    </div>
                </div>
            )}
        </div>
    );
}

function renderArtifactCard(title: string, artifact: FeatureArtifact | null) {
    if (!artifact) {
        return <div className="rounded-2xl border border-[#30363d] bg-[#0d1117] p-4 text-sm text-[#8b949e]">{title} 결과가 아직 없습니다.</div>;
    }

    const spreadsheetArtifact = artifact as SpreadsheetFeatureArtifact;
    const isSpreadsheetArtifact = !!spreadsheetArtifact.sheet_schema || !!spreadsheetArtifact.workbook || !!spreadsheetArtifact.delivery_assets?.length;

    return (
        <div className="rounded-2xl border border-[#30363d] bg-[#0d1117] p-4">
            <div className="flex items-center justify-between gap-3">
                <p className="text-sm font-semibold text-white">{title}</p>
                <span className="rounded-full border border-[#2a7cff] bg-[#10264a] px-3 py-1 text-[11px] text-[#9ecbff]">{artifact.state || artifact.phase || 'artifact'}</span>
            </div>
            {artifact.image_data_url ? (
                <img src={artifact.image_data_url} alt={title} className="mt-3 h-48 w-full rounded-xl object-cover" />
            ) : isSpreadsheetArtifact ? (
                renderSpreadsheetArtifact(artifact)
            ) : (
                <div className="mt-3 rounded-xl border border-dashed border-[#30363d] px-4 py-8 text-center text-sm text-[#8b949e]">이미지 미리보기가 아직 없습니다.</div>
            )}
            {!!artifact.composition?.warnings?.length && (
                <div className="mt-3 space-y-1 text-xs text-[#f0b43f]">
                    {artifact.composition.warnings.map((warning: string) => (
                        <p key={warning}>{warning}</p>
                    ))}
                </div>
            )}
            {!!artifact.notes?.length && (
                <div className="mt-3 space-y-1 text-xs text-[#8b949e]">
                    {artifact.notes.map((note: string) => (
                        <p key={note}>{note}</p>
                    ))}
                </div>
            )}
        </div>
    );
}

interface FeatureOrchestratorPopupProps {
    isOpen: boolean;
    activeFeatureId: string;
    popupMode?: string;
    title: string;
    featureSummary: string;
    popupState: FeaturePopupState;
    projectName: string;
    setProjectName: (value: string) => void;
    prompt: string;
    setPrompt: (value: string) => void;
    templateId: string;
    setTemplateId: (value: string) => void;
    finalEnabled: boolean;
    setFinalEnabled: (value: boolean) => void;
    supportsPhotoUpload: boolean;
    photoFileName: string;
    photoPreviewUrl: string;
    applyPhotoFile: (file: File | null) => void;
    previewArtifact: FeatureArtifact | null;
    finalArtifact: FeatureArtifact | null;
    qualityReview: {
        passed?: boolean;
        score?: number;
        issues?: string[];
    } | null;
    submitLoading: boolean;
    submitFeature: () => void;
    closePopup: () => void;
    errorText: string;
    runId: string;
    eventLog: Array<{ state: FeaturePopupState; at: string }>;
    streamConnection: FeatureStreamConnection;
    stageRunStatus?: string;
    latestEventAt: string;
    elapsedSeconds: number;
    liveViewArtifact: FeatureLiveViewArtifact | null;
    spreadsheetRunSummary?: SpreadsheetRunSummary | null;
    spreadsheetDownloadLinks?: SpreadsheetDownloadLink[];
    progressSnapshot: FeatureProgressSnapshot | null;
    progressHistory: FeatureProgressSnapshot[];
}

export default function FeatureOrchestratorPopup(props: FeatureOrchestratorPopupProps) {
    if (!props.isOpen) {
        return null;
    }

    const isSpreadsheetBuilder = props.activeFeatureId === 'ai-sheet' || props.popupMode === 'spreadsheet-builder';
    const qualityGateScoreLabel = props.qualityReview ? `${Math.round(Number(props.qualityReview.score || 0) * 100)}점` : '대기';
    const latestSpreadsheetDownloadFormat = React.useMemo(() => {
        const datedDownloads = (props.spreadsheetDownloadLinks || []).filter((item) => item.completedAt);
        if (!datedDownloads.length) {
            return '';
        }
        return datedDownloads
            .map((item) => ({
                format: item.format,
                completedAt: new Date(item.completedAt).getTime(),
            }))
            .filter((item) => !Number.isNaN(item.completedAt))
            .sort((left, right) => right.completedAt - left.completedAt)[0]?.format || '';
    }, [props.spreadsheetDownloadLinks]);
    const templateOptions = isSpreadsheetBuilder
        ? [
            { value: 'sheet-schema-template', label: '기본 시트 스키마 템플릿' },
            { value: 'sales-pipeline-template', label: '영업 파이프라인 템플릿' },
            { value: 'inventory-control-template', label: '재고 관리 템플릿' },
        ]
        : [
            { value: 'ad-photo-template', label: '광고 사진 템플릿' },
            { value: 'portrait-promo-template', label: '인물 프로모션 템플릿' },
            { value: 'product-banner-template', label: '제품 배너 템플릿' },
        ];

    return (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 px-4 py-6" data-testid="marketplace-feature-orchestrator-popup">
            <div className="max-h-[92vh] w-full max-w-6xl overflow-auto rounded-[28px] border border-[#25304a] bg-[#0b0f16] p-6 text-[#e6edf3] shadow-[0_30px_120px_rgba(0,0,0,0.45)]">
                <div className="flex items-start justify-between gap-4">
                    <div>
                        <p className="text-sm font-semibold uppercase tracking-[0.18em] text-[#58c9ff]">Popup Feature Orchestrator</p>
                        <h2 className="mt-2 text-3xl font-bold text-white">{props.title}</h2>
                        <p className="mt-3 max-w-[980px] text-sm leading-7 text-[#9eabba]">{props.featureSummary}</p>
                    </div>
                    <button type="button" onClick={props.closePopup} className="rounded-2xl border border-[#30363d] px-4 py-2 text-sm font-semibold text-[#d2d9e3]">닫기</button>
                </div>

                <div className="mt-6 grid gap-6 xl:grid-cols-[0.9fr_1.1fr]">
                    <section className="space-y-4">
                        <div data-testid="marketplace-live-view-panel" className="rounded-[28px] border border-[#25304a] bg-[radial-gradient(circle_at_top,_rgba(54,118,255,0.22),_rgba(11,15,22,0.92)_55%)] p-5">
                            <div className="grid gap-5 xl:grid-cols-[1.15fr_0.85fr]">
                                <div>
                                    <div className="flex flex-wrap items-start justify-between gap-3">
                                        <div>
                                            <p className="text-xs font-semibold uppercase tracking-[0.2em] text-[#8ec5ff]">Real-time Live View</p>
                                            <h3 className="mt-2 text-2xl font-bold text-white">{props.liveViewArtifact?.title || '라이브 피드 대기 중'}</h3>
                                            <p className="mt-2 max-w-[640px] text-sm leading-6 text-[#b8c6d9]">{props.liveViewArtifact?.caption || (isSpreadsheetBuilder ? 'spreadsheet-builder 는 preview 단계에서 시트 schema 를 만들고, final 단계에서 workbook 패키지와 delivery asset 상태를 확정합니다.' : '실행을 시작하면 preview, final, quality 단계가 들어오는 순서대로 최신 상태와 이미지를 이 영역에 고정합니다.')}</p>
                                        </div>
                                        <span data-testid="marketplace-live-view-connection" className={`rounded-full border px-3 py-1.5 text-xs font-semibold ${props.streamConnection === 'streaming' ? 'border-[#2fe37d] bg-[#0f2b1a] text-[#8af4b0]' : props.streamConnection === 'completed' ? 'border-[#58c9ff] bg-[#10264a] text-[#a8e6ff]' : props.streamConnection === 'failed' ? 'border-red-500/40 bg-red-950/30 text-red-200' : 'border-[#3a4558] bg-[#121a28] text-[#c9d7ea]'}`}>
                                            {connectionLabel(props.streamConnection)}
                                        </span>
                                    </div>

                                    {props.liveViewArtifact?.image_data_url ? (
                                        <div data-testid="marketplace-live-view-spotlight" className="relative mt-5 overflow-hidden rounded-[24px] border border-[#365d96] bg-[#0b1019]">
                                            <img src={props.liveViewArtifact.image_data_url} alt={props.liveViewArtifact.title} className="h-[320px] w-full object-cover" data-testid="marketplace-live-view-image" />
                                            <div className="absolute inset-x-0 bottom-0 flex items-center justify-between gap-3 bg-gradient-to-t from-black/80 via-black/35 to-transparent px-4 py-4 text-xs text-[#dbe7f7]">
                                                <span data-testid="marketplace-live-view-source">source {props.liveViewArtifact.source}</span>
                                                <span data-testid="marketplace-live-view-state">{stateLabel(props.popupState)}</span>
                                            </div>
                                        </div>
                                    ) : isSpreadsheetBuilder && props.spreadsheetRunSummary ? (
                                        <div data-testid="marketplace-live-view-sheet-summary" className="mt-5 rounded-[24px] border border-[#365d96] bg-[#0b1019] p-5">
                                            <div className="flex items-start justify-between gap-4">
                                                <div>
                                                    <p className="text-xs uppercase tracking-[0.18em] text-[#8ec5ff]">Excel Live Feed</p>
                                                    <h4 className="mt-2 text-2xl font-bold text-white">{props.spreadsheetRunSummary.stageLabel}</h4>
                                                    <p className="mt-2 max-w-[560px] text-sm leading-6 text-[#b8c6d9]">{props.spreadsheetRunSummary.stageDescription}</p>
                                                </div>
                                                <span className="rounded-full border border-[#2a7cff] bg-[#10264a] px-3 py-1.5 text-xs font-semibold text-[#9ecbff]">{props.spreadsheetRunSummary.sheetName}</span>
                                            </div>
                                            <div className="mt-5 grid gap-3 sm:grid-cols-3">
                                                <div className="rounded-2xl border border-[#1e2a3c] bg-[#10182b] px-4 py-3">
                                                    <p className="text-xs text-[#8ea4bf]">컬럼 수</p>
                                                    <p className="mt-2 text-2xl font-bold text-white">{props.spreadsheetRunSummary.columnCount}</p>
                                                </div>
                                                <div className="rounded-2xl border border-[#1e2a3c] bg-[#10182b] px-4 py-3">
                                                    <p className="text-xs text-[#8ea4bf]">행 수</p>
                                                    <p className="mt-2 text-2xl font-bold text-white">{props.spreadsheetRunSummary.rowCount}</p>
                                                </div>
                                                <div className="rounded-2xl border border-[#1e2a3c] bg-[#10182b] px-4 py-3">
                                                    <p className="text-xs text-[#8ea4bf]">다운로드 자산</p>
                                                    <p className="mt-2 text-2xl font-bold text-white">{props.spreadsheetDownloadLinks?.filter((item) => item.ready).length || 0}</p>
                                                </div>
                                            </div>
                                            <div className="mt-5 rounded-2xl border border-[#1e2a3c] bg-[#10182b] px-4 py-3">
                                                <p className="text-xs uppercase tracking-[0.18em] text-[#8ea4bf]">Prompt Summary</p>
                                                <p className="mt-2 text-sm leading-6 text-white">{props.spreadsheetRunSummary.promptSummary || '프롬프트 요약이 아직 없습니다.'}</p>
                                            </div>
                                        </div>
                                    ) : (
                                        <div data-testid="marketplace-live-view-empty" className="mt-5 flex h-[320px] items-center justify-center rounded-[24px] border border-dashed border-[#365d96] bg-[#0b1019] px-6 text-center text-sm leading-7 text-[#8ea4bf]">
                                            {isSpreadsheetBuilder ? '실행을 시작하면 preview 결과로 시트 schema 가 우측 카드에 표시되고, final 단계에서 workbook 패키지와 xlsx/csv delivery asset 이 확정됩니다.' : '실행을 시작하면 preview artifact 또는 final artifact 가 도착하는 즉시 이 영역이 자동으로 갱신됩니다.'}
                                        </div>
                                    )}
                                </div>

                                <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-1">
                                    <div className="rounded-2xl border border-[#2d3d56] bg-[#0d1420] p-4">
                                        <p className="text-xs uppercase tracking-[0.18em] text-[#7fa7d8]">현재 단계</p>
                                        <p data-testid="marketplace-live-view-current-state" className="mt-3 text-xl font-bold text-white">{stateLabel(props.popupState)}</p>
                                        <p className="mt-2 text-xs text-[#8ea4bf]">event log 와 stage snapshot 에 맞춰 즉시 갱신됩니다.</p>
                                    </div>
                                    <div className="rounded-2xl border border-[#2d3d56] bg-[#0d1420] p-4">
                                        <p className="text-xs uppercase tracking-[0.18em] text-[#7fa7d8]">경과 시간</p>
                                        <p data-testid="marketplace-live-view-elapsed" className="mt-3 text-xl font-bold text-white">{formatElapsed(props.elapsedSeconds)}</p>
                                        <p className="mt-2 text-xs text-[#8ea4bf]">accepted 시점부터 스트림 종료까지 실시간으로 증가합니다.</p>
                                    </div>
                                    <div data-testid="marketplace-progress-panel" className="rounded-2xl border border-[#2d3d56] bg-[#0d1420] p-4 sm:col-span-2 xl:col-span-1">
                                        <div className="flex items-center justify-between gap-3">
                                            <p className="text-xs uppercase tracking-[0.18em] text-[#7fa7d8]">세부 진행률</p>
                                            <span data-testid="marketplace-progress-percent" className="text-sm font-bold text-white">{Math.max(0, Math.min(100, props.progressSnapshot?.percent || 0))}%</span>
                                        </div>
                                        <div className="mt-3 h-2 overflow-hidden rounded-full bg-[#1b2432]">
                                            <div
                                                data-testid="marketplace-progress-bar"
                                                className={`h-full rounded-full bg-[linear-gradient(90deg,#2a7cff,#58c9ff)] transition-all duration-300 ${progressWidthClass(props.progressSnapshot?.percent || 0)}`}
                                            />
                                        </div>
                                        <p data-testid="marketplace-progress-message" className="mt-3 text-sm font-semibold text-white">{props.progressSnapshot?.message || 'progress 이벤트 대기 중'}</p>
                                        <p data-testid="marketplace-progress-step" className="mt-1 text-xs text-[#8ea4bf]">{props.progressSnapshot?.step || 'accepted'}</p>
                                    </div>
                                    <div className="rounded-2xl border border-[#2d3d56] bg-[#0d1420] p-4">
                                        <p className="text-xs uppercase tracking-[0.18em] text-[#7fa7d8]">Stage Run 상태</p>
                                        <p className="mt-3 text-xl font-bold text-white">{props.stageRunStatus || '미수신'}</p>
                                        <p className="mt-2 text-xs text-[#8ea4bf]">stream 과 별도로 stage-run snapshot 을 주기적으로 재조회합니다.</p>
                                    </div>
                                    <div className="rounded-2xl border border-[#2d3d56] bg-[#0d1420] p-4">
                                        <p className="text-xs uppercase tracking-[0.18em] text-[#7fa7d8]">마지막 갱신</p>
                                        <p className="mt-3 text-xl font-bold text-white">{props.latestEventAt ? new Date(props.latestEventAt).toLocaleTimeString('ko-KR') : '대기'}</p>
                                        <p className="mt-2 text-xs text-[#8ea4bf]">새 이벤트가 오면 즉시 시간과 화면이 함께 갱신됩니다.</p>
                                    </div>
                                </div>
                            </div>
                        </div>

                        <div className="rounded-2xl border border-[#25304a] bg-[#10182b] p-4">
                            <p className="text-sm font-semibold text-white">실행 입력</p>
                            <div className="mt-4 space-y-3">
                                <input data-testid="marketplace-popup-project-name" value={props.projectName} onChange={(event) => props.setProjectName(event.target.value)} placeholder="프로젝트명" className="w-full rounded-2xl border border-[#30363d] bg-[#0d1117] px-4 py-3 text-sm text-white outline-none" />
                                <select data-testid="marketplace-popup-template-id" aria-label={isSpreadsheetBuilder ? '시트 템플릿 선택' : '광고 템플릿 선택'} title={isSpreadsheetBuilder ? '시트 템플릿 선택' : '광고 템플릿 선택'} value={props.templateId} onChange={(event) => props.setTemplateId(event.target.value)} className="w-full rounded-2xl border border-[#30363d] bg-[#0d1117] px-4 py-3 text-sm text-white outline-none">
                                    {templateOptions.map((option) => (
                                        <option key={option.value} value={option.value}>{option.label}</option>
                                    ))}
                                </select>
                                <textarea data-testid="marketplace-popup-prompt" value={props.prompt} onChange={(event) => props.setPrompt(event.target.value)} rows={8} placeholder={isSpreadsheetBuilder ? '시트 목적, 필수 컬럼, 샘플 행 수, 날짜/숫자 형식을 입력하세요.' : '인물 사진, 배경, 광고 문구, 템플릿 방향을 입력하세요.'} className="w-full rounded-2xl border border-[#30363d] bg-[#0d1117] px-4 py-3 text-sm text-white outline-none" />
                                {props.supportsPhotoUpload && (
                                    <>
                                        <label data-testid="marketplace-popup-photo-upload" className="block rounded-2xl border border-dashed border-[#30363d] bg-[#0d1117] px-4 py-4 text-sm text-[#c9d1d9]">
                                            <span className="font-semibold text-white">사람 사진 업로드</span>
                                            <input data-testid="marketplace-popup-photo-input" type="file" accept="image/*" className="mt-3 block w-full text-sm text-[#c9d1d9]" onChange={(event) => props.applyPhotoFile(event.target.files?.[0] || null)} />
                                            {props.photoFileName && <p className="mt-2 text-xs text-[#8b949e]">선택 파일: {props.photoFileName}</p>}
                                        </label>
                                        {props.photoPreviewUrl && <img src={props.photoPreviewUrl} alt="업로드 preview" data-testid="marketplace-popup-photo-preview" className="h-40 w-full rounded-2xl object-cover" />}
                                    </>
                                )}
                                <label className="flex items-center gap-3 rounded-2xl border border-[#30363d] bg-[#0d1117] px-4 py-3 text-sm text-[#d2d9e3]">
                                    <input data-testid="marketplace-popup-final-enabled" type="checkbox" checked={props.finalEnabled} onChange={(event) => props.setFinalEnabled(event.target.checked)} />
                                    {isSpreadsheetBuilder ? 'schema preview 뒤 workbook 패키지 final phase 도 같이 실행' : 'preview 뒤 final phase 도 같이 실행'}
                                </label>
                            </div>
                            <div className="mt-4 flex gap-3">
                                <button type="button" data-testid="marketplace-popup-submit" onClick={props.submitFeature} disabled={props.submitLoading} className="rounded-2xl bg-[#2a7cff] px-5 py-3 text-sm font-bold text-white disabled:cursor-not-allowed disabled:bg-[#29425f]">{props.submitLoading ? '실행 중...' : '백엔드에서 실행'}</button>
                                {props.runId && <span data-testid="marketplace-popup-run-id" className="rounded-2xl border border-[#30363d] px-4 py-3 text-xs text-[#8b949e]">run_id {props.runId}</span>}
                            </div>
                            {props.errorText && <div data-testid="marketplace-popup-error" className="mt-4 rounded-2xl border border-red-400/40 bg-red-950/20 px-4 py-3 text-sm text-red-200">{props.errorText}</div>}
                        </div>

                        <div className="rounded-2xl border border-[#25304a] bg-[#10182b] p-4">
                            <p className="text-sm font-semibold text-white">상태 전이</p>
                            <div data-testid="marketplace-state-flow" className="mt-4 flex flex-wrap gap-2">
                                {POPUP_STATE_FLOW.map((state) => {
                                    const active = props.popupState === state;
                                    const completed = props.eventLog.some((item) => item.state === state) && !active;
                                    return (
                                        <span key={state} className={`rounded-full border px-3 py-1.5 text-xs ${active ? 'border-[#19c3f3] bg-[#0d2230] text-[#67d6ff]' : completed ? 'border-[#31c45d] bg-[#102416] text-[#7af0a0]' : 'border-[#30363d] bg-[#0d1117] text-[#8b949e]'}`}>
                                            {stateLabel(state)}
                                        </span>
                                    );
                                })}
                                {props.popupState === 'completed_preview_only' && <span className="rounded-full border border-[#f0b43f] bg-[#2d220b] px-3 py-1.5 text-xs text-[#f0d28a]">preview 전용 완료</span>}
                                {props.popupState === 'failed' && <span className="rounded-full border border-red-500/40 bg-red-950/20 px-3 py-1.5 text-xs text-red-200">실패</span>}
                            </div>
                            <div className="mt-4 space-y-1 text-xs text-[#8b949e]">
                                {props.eventLog.map((item, index) => (
                                    <p key={`${item.state}-${item.at}-${index}`}>{new Date(item.at).toLocaleTimeString('ko-KR')} · {stateLabel(item.state)}</p>
                                ))}
                            </div>
                            <div data-testid="marketplace-progress-milestones" className="mt-5 rounded-2xl border border-[#2d3d56] bg-[#0d1420] p-4">
                                <p className="text-xs uppercase tracking-[0.18em] text-[#7fa7d8]">Progress Milestones</p>
                                <div className="mt-3 space-y-2 text-xs text-[#c7d2df]">
                                    {props.progressHistory.map((item, index) => (
                                        <div data-testid={`marketplace-progress-milestone-${index}`} key={`${item.step}-${item.updated_at}-${index}`} className="flex items-start justify-between gap-3 rounded-xl border border-[#1e2a3c] bg-[#0b1019] px-3 py-2">
                                            <div>
                                                <p className="font-semibold text-white">{item.message}</p>
                                                <p className="mt-1 text-[#8ea4bf]">{item.step}</p>
                                            </div>
                                            <div className="text-right">
                                                <p className="font-semibold text-[#8ec5ff]">{item.percent}%</p>
                                                <p className="mt-1 text-[#6f8198]">{new Date(item.updated_at).toLocaleTimeString('ko-KR')}</p>
                                            </div>
                                        </div>
                                    ))}
                                </div>
                            </div>
                        </div>
                    </section>

                    <section className="space-y-4">
                        {isSpreadsheetBuilder && !!props.spreadsheetDownloadLinks?.length && (
                            <div data-testid="marketplace-spreadsheet-downloads" className="rounded-2xl border border-[#25304a] bg-[#10182b] p-4">
                                <div className="flex items-center justify-between gap-3">
                                    <div>
                                        <p className="text-sm font-semibold text-white">Spreadsheet Downloads</p>
                                        <p className="mt-1 text-xs text-[#8b949e]">final phase 가 완료되면 xlsx/csv 결과물을 바로 내려받을 수 있습니다.</p>
                                    </div>
                                    <span className="rounded-full border border-[#30363d] px-3 py-1 text-xs text-[#c9d1d9]">{props.spreadsheetDownloadLinks.filter((item) => item.ready).length} ready</span>
                                </div>
                                <div className="mt-4 grid gap-3 sm:grid-cols-2">
                                    {props.spreadsheetDownloadLinks.map((item) => {
                                        const isLatest = item.ready && item.format === latestSpreadsheetDownloadFormat;
                                        return (
                                            <a
                                                key={item.format}
                                                data-testid={`marketplace-spreadsheet-download-${item.format}`}
                                                href={item.ready ? item.href : undefined}
                                                download={item.fileName}
                                                className={`rounded-2xl border px-4 py-3 no-underline transition ${isLatest ? 'shadow-[0_0_0_1px_rgba(255,211,122,0.35),0_12px_30px_rgba(42,124,255,0.18)]' : ''} ${item.ready ? isLatest ? 'border-[#ffd37a] bg-[linear-gradient(180deg,rgba(42,124,255,0.3),rgba(16,38,74,0.92))] text-white' : 'border-[#2a7cff] bg-[#10264a] text-white' : 'pointer-events-none border-[#30363d] bg-[#0d1117] text-[#7d8590]'}`}
                                            >
                                                <div className="flex items-start justify-between gap-3">
                                                    <div>
                                                        <div className="flex flex-wrap items-center gap-2">
                                                            <p className="text-sm font-bold uppercase">{item.format}</p>
                                                            {isLatest && <span data-testid={`marketplace-spreadsheet-download-latest-badge-${item.format}`} className="rounded-full border border-[#ffd37a]/60 bg-[#3b2c09] px-2 py-0.5 text-[10px] font-semibold text-[#ffe29b]">최근 생성 파일</span>}
                                                        </div>
                                                        <p className="mt-1 text-xs text-[#c9d1d9]">{item.fileName}</p>
                                                    </div>
                                                    <span className={`text-xs font-semibold ${isLatest ? 'text-[#ffe29b]' : ''}`}>{item.sizeLabel}</span>
                                                </div>
                                                <p className={`mt-3 text-xs ${isLatest ? 'font-semibold text-[#ffe29b]' : ''}`}>{item.ready ? '결과물 다운로드' : '생성 대기 중'}</p>
                                                <p className="mt-1 text-[11px] text-[#8ea4bf]">파일 생성 완료 시각 {item.completedAtLabel}</p>
                                            </a>
                                        );
                                    })}
                                </div>
                            </div>
                        )}
                        <div className="grid gap-4 xl:grid-cols-2">
                            <div data-testid="marketplace-preview-artifact-card">{renderArtifactCard('Preview Artifact', props.previewArtifact)}</div>
                            <div data-testid="marketplace-final-artifact-card">{renderArtifactCard('Final Artifact', props.finalArtifact)}</div>
                        </div>
                        <div data-testid="marketplace-quality-gate" className="rounded-2xl border border-[#25304a] bg-[#10182b] p-4">
                            <div className="flex items-center justify-between gap-3">
                                <p className="text-sm font-semibold text-white">Quality Gate</p>
                                <span className={`rounded-full border px-3 py-1 text-xs ${props.qualityReview?.passed ? 'border-[#31c45d] text-[#7af0a0]' : 'border-[#7d8590] text-[#c9d1d9]'}`}>{qualityGateScoreLabel}</span>
                            </div>
                            {props.qualityReview ? (
                                <div className="mt-3 space-y-2 text-sm text-[#d2d9e3]">
                                    <p>판정: {props.qualityReview.passed ? '통과' : 'preview fallback 유지'}</p>
                                    {props.qualityReview.issues?.map((issue) => (
                                        <p key={issue} className="text-[#f0b43f]">- {issue}</p>
                                    ))}
                                </div>
                            ) : (
                                <p className="mt-3 text-sm text-[#8b949e]">preview/final 단계가 끝나면 품질 검토 결과가 표시됩니다.</p>
                            )}
                        </div>
                    </section>
                </div>
            </div>
        </div>
    );
}
