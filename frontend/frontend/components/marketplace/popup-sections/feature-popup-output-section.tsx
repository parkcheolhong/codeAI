'use client';

import * as React from 'react';
import type { FeatureArtifact, FeatureExperienceMeta, SpreadsheetDownloadLink } from '@/hooks/use-feature-orchestrator';
import type { PopupQualityReview } from '@/components/marketplace/popup-sections/feature-popup-types';

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

type RichFeatureArtifact = FeatureArtifact & {
    prompt_summary?: string;
    keywords?: string[];
    notes?: string[];
    sections?: Array<{
        title?: string;
        summary?: string;
    }>;
    outline?: string[];
    scene_cards?: Array<{
        title?: string;
        summary?: string;
        duration?: string;
        cta?: string;
    }>;
    track_structure?: Array<{
        title?: string;
        summary?: string;
        duration?: string;
        mood?: string;
    }>;
    package_assets?: Array<{
        label?: string;
        value?: string;
    }>;
};

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

function renderSummaryBlock(label: string, value?: string | null) {
    if (!value) {
        return null;
    }
    return (
        <div className="rounded-xl border border-[#2d3d56] bg-[#0b1019] px-4 py-3">
            <p className="text-xs uppercase tracking-[0.18em] text-[#7fa7d8]">{label}</p>
            <p className="mt-2 text-sm leading-6 text-white">{value}</p>
        </div>
    );
}

function renderTagList(tags?: string[]) {
    if (!tags?.length) {
        return null;
    }
    return (
        <div className="flex flex-wrap gap-2">
            {tags.map((tag) => (
                <span key={tag} className="rounded-full border border-[#30363d] bg-[#151b23] px-3 py-1 text-xs text-[#d2d9e3]">#{tag}</span>
            ))}
        </div>
    );
}

function renderImageArtifact(artifact: RichFeatureArtifact) {
    return (
        <div className="mt-3 space-y-3 text-sm text-[#d2d9e3]">
            {artifact.image_data_url && <img src={artifact.image_data_url} alt={artifact.title || 'image artifact'} className="h-48 w-full rounded-xl object-cover" />}
            <div className="grid gap-3 sm:grid-cols-3">
                <div className="rounded-xl border border-[#1e2a3c] bg-[#10182b] px-3 py-2">
                    <p className="text-xs text-[#8ea4bf]">Template</p>
                    <p className="mt-1 text-sm font-semibold text-white">{artifact.composition?.template_id || 'auto'}</p>
                </div>
                <div className="rounded-xl border border-[#1e2a3c] bg-[#10182b] px-3 py-2">
                    <p className="text-xs text-[#8ea4bf]">Photo Ref</p>
                    <p className="mt-1 text-sm font-semibold text-white">{artifact.composition?.photo_reference || 'none'}</p>
                </div>
                <div className="rounded-xl border border-[#1e2a3c] bg-[#10182b] px-3 py-2">
                    <p className="text-xs text-[#8ea4bf]">State</p>
                    <p className="mt-1 text-sm font-semibold text-white">{artifact.state || artifact.phase || 'artifact'}</p>
                </div>
            </div>
            {renderSummaryBlock('Prompt Summary', artifact.prompt_summary)}
            {renderTagList(artifact.keywords)}
        </div>
    );
}

function renderMusicArtifact(artifact: RichFeatureArtifact) {
    const trackStructure = artifact.track_structure || [];
    return (
        <div className="mt-3 space-y-3 text-sm text-[#d2d9e3]">
            {renderSummaryBlock('Track Brief', artifact.prompt_summary || artifact.title)}
            <div className="grid gap-3 sm:grid-cols-3">
                <div className="rounded-xl border border-[#1e2a3c] bg-[#10182b] px-3 py-2">
                    <p className="text-xs text-[#8ea4bf]">Mood</p>
                    <p className="mt-1 text-sm font-semibold text-white">{artifact.keywords?.[0] || 'auto'}</p>
                </div>
                <div className="rounded-xl border border-[#1e2a3c] bg-[#10182b] px-3 py-2">
                    <p className="text-xs text-[#8ea4bf]">Structure</p>
                    <p className="mt-1 text-sm font-semibold text-white">{trackStructure.length || 0} section</p>
                </div>
                <div className="rounded-xl border border-[#1e2a3c] bg-[#10182b] px-3 py-2">
                    <p className="text-xs text-[#8ea4bf]">Package</p>
                    <p className="mt-1 text-sm font-semibold text-white">{artifact.package_assets?.length || 0} asset</p>
                </div>
            </div>
            {!!trackStructure.length && (
                <div className="space-y-2 rounded-xl border border-[#2d3d56] bg-[#0b1019] p-4">
                    <p className="font-semibold text-white">Track Structure</p>
                    {trackStructure.map((section, index) => (
                        <div key={`${section.title}-${index}`} className="rounded-xl border border-[#1e2a3c] bg-[#10182b] px-3 py-3">
                            <div className="flex items-center justify-between gap-3">
                                <p className="font-semibold text-white">{section.title || `Section ${index + 1}`}</p>
                                <span className="text-xs text-[#8ec5ff]">{section.duration || section.mood || 'planned'}</span>
                            </div>
                            <p className="mt-2 text-xs leading-6 text-[#c7d2df]">{section.summary || '트랙 구성 설명이 아직 없습니다.'}</p>
                        </div>
                    ))}
                </div>
            )}
            {renderTagList(artifact.keywords)}
        </div>
    );
}

function renderDocumentArtifact(artifact: RichFeatureArtifact) {
    const outline = artifact.outline || artifact.sections?.map((item) => item.title || '').filter(Boolean) || [];
    return (
        <div className="mt-3 space-y-3 text-sm text-[#d2d9e3]">
            {renderSummaryBlock('Document Brief', artifact.prompt_summary || artifact.title)}
            {!!outline.length && (
                <div className="rounded-xl border border-[#2d3d56] bg-[#0b1019] p-4">
                    <p className="font-semibold text-white">Outline</p>
                    <div className="mt-3 space-y-2">
                        {outline.map((item, index) => (
                            <div key={`${item}-${index}`} className="rounded-xl border border-[#1e2a3c] bg-[#10182b] px-3 py-2 text-xs text-[#d2d9e3]">
                                {index + 1}. {item}
                            </div>
                        ))}
                    </div>
                </div>
            )}
            {!!artifact.sections?.length && (
                <div className="rounded-xl border border-[#2d3d56] bg-[#0b1019] p-4">
                    <p className="font-semibold text-white">Key Sections</p>
                    <div className="mt-3 space-y-2">
                        {artifact.sections.map((section, index) => (
                            <div key={`${section.title}-${index}`} className="rounded-xl border border-[#1e2a3c] bg-[#10182b] px-3 py-3">
                                <p className="font-semibold text-white">{section.title || `Section ${index + 1}`}</p>
                                <p className="mt-2 text-xs leading-6 text-[#c7d2df]">{section.summary || '섹션 요약이 아직 없습니다.'}</p>
                            </div>
                        ))}
                    </div>
                </div>
            )}
            {renderTagList(artifact.keywords)}
        </div>
    );
}

function renderVideoArtifact(artifact: RichFeatureArtifact) {
    const sceneCards = artifact.scene_cards || [];
    return (
        <div className="mt-3 space-y-3 text-sm text-[#d2d9e3]">
            {renderSummaryBlock('Storyboard Brief', artifact.prompt_summary || artifact.title)}
            <div className="grid gap-3 sm:grid-cols-3">
                <div className="rounded-xl border border-[#1e2a3c] bg-[#10182b] px-3 py-2">
                    <p className="text-xs text-[#8ea4bf]">Scenes</p>
                    <p className="mt-1 text-sm font-semibold text-white">{sceneCards.length || 0}</p>
                </div>
                <div className="rounded-xl border border-[#1e2a3c] bg-[#10182b] px-3 py-2">
                    <p className="text-xs text-[#8ea4bf]">CTA</p>
                    <p className="mt-1 text-sm font-semibold text-white">{sceneCards.find((scene) => scene.cta)?.cta || 'planned'}</p>
                </div>
                <div className="rounded-xl border border-[#1e2a3c] bg-[#10182b] px-3 py-2">
                    <p className="text-xs text-[#8ea4bf]">Package</p>
                    <p className="mt-1 text-sm font-semibold text-white">{artifact.package_assets?.length || 0} asset</p>
                </div>
            </div>
            {!!sceneCards.length && (
                <div className="space-y-2 rounded-xl border border-[#2d3d56] bg-[#0b1019] p-4">
                    <p className="font-semibold text-white">Scene Cards</p>
                    {sceneCards.map((scene, index) => (
                        <div key={`${scene.title}-${index}`} className="rounded-xl border border-[#1e2a3c] bg-[#10182b] px-3 py-3">
                            <div className="flex items-center justify-between gap-3">
                                <p className="font-semibold text-white">{scene.title || `Scene ${index + 1}`}</p>
                                <span className="text-xs text-[#8ec5ff]">{scene.duration || 'planned'}</span>
                            </div>
                            <p className="mt-2 text-xs leading-6 text-[#c7d2df]">{scene.summary || '장면 설명이 아직 없습니다.'}</p>
                            {scene.cta && <p className="mt-2 text-xs font-semibold text-[#ffd37a]">CTA · {scene.cta}</p>}
                        </div>
                    ))}
                </div>
            )}
            {renderTagList(artifact.keywords)}
        </div>
    );
}

function renderGenericArtifact(artifact: RichFeatureArtifact) {
    return (
        <div className="mt-3 space-y-3 text-sm text-[#d2d9e3]">
            {renderSummaryBlock('Artifact Summary', artifact.prompt_summary || artifact.title)}
            {renderTagList(artifact.keywords)}
            {!!artifact.notes?.length && (
                <div className="rounded-xl border border-[#2d3d56] bg-[#0b1019] p-4">
                    <p className="font-semibold text-white">Notes</p>
                    <div className="mt-3 space-y-1 text-xs text-[#c7d2df]">
                        {artifact.notes.map((note) => (
                            <p key={note}>- {note}</p>
                        ))}
                    </div>
                </div>
            )}
        </div>
    );
}

function renderArtifactCard(title: string, artifact: FeatureArtifact | null, mode: 'image' | 'music' | 'document' | 'spreadsheet' | 'video') {
    if (!artifact) {
        return <div className="rounded-[24px] border border-dashed border-[#30363d] bg-[#0d1117] p-5 text-sm leading-7 text-[#8b949e]">{title} 결과가 아직 없습니다.</div>;
    }

    const richArtifact = artifact as RichFeatureArtifact;
    const spreadsheetArtifact = artifact as SpreadsheetFeatureArtifact;
    const isSpreadsheetArtifact = !!spreadsheetArtifact.sheet_schema || !!spreadsheetArtifact.workbook || !!spreadsheetArtifact.delivery_assets?.length;

    return (
        <div className="rounded-[24px] border border-[#30363d] bg-[linear-gradient(180deg,#0d1117,#0b1016)] p-4 shadow-[0_22px_50px_rgba(0,0,0,0.18)] sm:p-5">
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                <div>
                    <p className="text-xs uppercase tracking-[0.18em] text-[#8ea4bf]">Artifact Panel</p>
                    <p className="mt-1 text-sm font-semibold text-white">{title}</p>
                </div>
                <span className="rounded-full border border-[#2a7cff] bg-[#10264a] px-3 py-1 text-[11px] text-[#9ecbff]">{artifact.state || artifact.phase || 'artifact'}</span>
            </div>
            {mode === 'spreadsheet' || isSpreadsheetArtifact ? (
                renderSpreadsheetArtifact(artifact)
            ) : mode === 'image' ? (
                renderImageArtifact(richArtifact)
            ) : mode === 'music' ? (
                renderMusicArtifact(richArtifact)
            ) : mode === 'document' ? (
                renderDocumentArtifact(richArtifact)
            ) : mode === 'video' ? (
                renderVideoArtifact(richArtifact)
            ) : (
                renderGenericArtifact(richArtifact)
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

interface FeaturePopupOutputSectionProps {
    outputKind?: FeatureExperienceMeta['outputKind'];
    isSpreadsheetBuilder: boolean;
    spreadsheetDownloadLinks?: SpreadsheetDownloadLink[];
    latestSpreadsheetDownloadFormat: string;
    previewTitle: string;
    finalTitle: string;
    previewArtifact: FeatureArtifact | null;
    finalArtifact: FeatureArtifact | null;
    qualityReview: PopupQualityReview;
    qualityGateScoreLabel: string;
}

export default function FeaturePopupOutputSection(props: FeaturePopupOutputSectionProps) {
    const outputKind = props.outputKind || (props.isSpreadsheetBuilder ? 'spreadsheet' : 'image');
    return (
        <section className="space-y-4" aria-label="생성 결과와 다운로드 패널">
            {props.isSpreadsheetBuilder && !!props.spreadsheetDownloadLinks?.length && (
                <div data-testid="marketplace-spreadsheet-downloads" className="rounded-[24px] border border-[#25304a] bg-[linear-gradient(180deg,#10182b,#0e1524)] p-4 shadow-[0_18px_40px_rgba(0,0,0,0.16)] sm:p-5">
                    <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                        <div className="min-w-0 flex-1">
                            <p className="text-sm font-semibold text-white">Spreadsheet Downloads</p>
                            <p className="mt-1 text-xs text-[#8b949e]">final phase 가 완료되면 xlsx/csv 결과물을 바로 내려받을 수 있습니다.</p>
                        </div>
                        <span className="inline-flex w-fit rounded-full border border-[#30363d] px-3 py-1 text-xs text-[#c9d1d9]">{props.spreadsheetDownloadLinks.filter((item) => item.ready).length} ready</span>
                    </div>
                    <div className="mt-4 grid gap-3 sm:grid-cols-2">
                        {props.spreadsheetDownloadLinks.map((item) => {
                            const isLatest = item.ready && item.format === props.latestSpreadsheetDownloadFormat;
                            return (
                                <a
                                    key={item.format}
                                    data-testid={`marketplace-spreadsheet-download-${item.format}`}
                                    href={item.ready ? item.href : undefined}
                                    download={item.fileName}
                                    className={`rounded-[22px] border px-4 py-3 no-underline transition focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#58c9ff] ${isLatest ? 'shadow-[0_0_0_1px_rgba(255,211,122,0.35),0_12px_30px_rgba(42,124,255,0.18)]' : ''} ${item.ready ? isLatest ? 'border-[#ffd37a] bg-[linear-gradient(180deg,rgba(42,124,255,0.3),rgba(16,38,74,0.92))] text-white' : 'border-[#2a7cff] bg-[#10264a] text-white' : 'pointer-events-none border-[#30363d] bg-[#0d1117] text-[#7d8590]'}`}
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
                <div data-testid="marketplace-preview-artifact-card">{renderArtifactCard(props.previewTitle, props.previewArtifact, outputKind)}</div>
                <div data-testid="marketplace-final-artifact-card">{renderArtifactCard(props.finalTitle, props.finalArtifact, outputKind)}</div>
            </div>
            <div data-testid="marketplace-quality-gate" className="rounded-[24px] border border-[#25304a] bg-[linear-gradient(180deg,#10182b,#0e1524)] p-4 shadow-[0_18px_40px_rgba(0,0,0,0.16)] sm:p-5">
                <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                    <p className="text-sm font-semibold text-white">Quality Gate</p>
                    <span className={`inline-flex w-fit rounded-full border px-3 py-1 text-xs ${props.qualityReview?.passed ? 'border-[#31c45d] text-[#7af0a0]' : 'border-[#7d8590] text-[#c9d1d9]'}`}>{props.qualityGateScoreLabel}</span>
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
    );
}
