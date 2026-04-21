'use client';

import * as React from 'react';
import type { FeatureCatalogItem } from '@/hooks/use-feature-orchestrator';

interface FeatureLauncherGridProps {
    catalog: FeatureCatalogItem[];
    catalogLoading: boolean;
    catalogError: string;
    activeFeatureId: string;
    onLaunch: (featureId: string) => void;
}

export default function FeatureLauncherGrid({ catalog, catalogLoading, catalogError, activeFeatureId, onLaunch }: FeatureLauncherGridProps) {
    if (catalogLoading) {
        return <div className="rounded-[22px] border border-[#25304a] bg-[#10182b] px-5 py-8 text-center text-base text-[#8b949e]">feature catalog 를 불러오는 중...</div>;
    }

    if (catalogError) {
        return <div className="rounded-[22px] border border-red-300 bg-red-50 px-4 py-3 text-sm text-red-800">{catalogError}</div>;
    }

    return (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-5" data-testid="marketplace-feature-launcher-grid">
            {catalog.map((feature) => {
                const enabled = feature.status === 'enabled';
                const isSpreadsheetFeature = feature.feature_id === 'ai-sheet';
                const featureSummary = isSpreadsheetFeature
                    ? '시트 schema preview 를 먼저 확인하고, final phase 에서 xlsx/csv workbook 패키지를 즉시 다운로드합니다.'
                    : feature.summary;
                return (
                    <article key={feature.feature_id} data-testid={`marketplace-feature-card-${feature.feature_id}`} className={`rounded-[20px] border p-4 shadow-[0_0_0_1px_rgba(255,255,255,0.03)] ${enabled ? 'border-[#30363d] bg-[#0d1117]' : 'border-[#2b3240] bg-[#121826]'}`}>
                        <div className="flex items-start justify-between gap-3">
                            <div>
                                <p className="text-base font-bold text-[#58c9ff]">{feature.title}</p>
                                <p className="mt-2 text-xs uppercase tracking-[0.18em] text-[#8b949e]">{feature.popup_mode}</p>
                            </div>
                            <span className={`rounded-full border px-3 py-1.5 text-xs font-bold ${enabled ? 'border-[#31c45d] text-[#31c45d]' : 'border-[#7d8590] text-[#7d8590]'}`}>
                                {enabled ? (isSpreadsheetFeature ? '엑셀 즉시 생성' : 'Popup 실행') : '준비 중'}
                            </span>
                        </div>
                        <p className="mt-4 min-h-[96px] text-sm leading-7 text-[#98a3b3]">{featureSummary}</p>
                        <div className="mt-4 flex flex-wrap gap-2 text-[11px] text-[#d2d9e3]">
                            {isSpreadsheetFeature && <span className="rounded-full border border-[#2a7cff] bg-[#10264a] px-3 py-1.5 text-[#9ecbff]">schema preview</span>}
                            {isSpreadsheetFeature && <span className="rounded-full border border-[#2a7cff] bg-[#10264a] px-3 py-1.5 text-[#9ecbff]">xlsx/csv 다운로드</span>}
                            {feature.supports_photo_upload && <span className="rounded-full border border-[#30363d] bg-[#151b23] px-3 py-1.5">사진 업로드</span>}
                            {feature.supports_final_phase && <span className="rounded-full border border-[#30363d] bg-[#151b23] px-3 py-1.5">final phase</span>}
                            {feature.feature_id === activeFeatureId && <span className="rounded-full border border-[#2a7cff] bg-[#10264a] px-3 py-1.5 text-[#9ecbff]">선택됨</span>}
                        </div>
                        <div className="mt-6">
                            <button type="button" data-testid={`marketplace-feature-launch-${feature.feature_id}`} onClick={() => enabled && onLaunch(feature.feature_id)} disabled={!enabled} className={`w-full rounded-2xl px-4 py-2.5 text-sm font-bold ${enabled ? 'bg-[#2a7cff] text-white' : 'cursor-not-allowed bg-[#202938] text-[#7d8590]'}`}>
                                {enabled ? (isSpreadsheetFeature ? '엑셀 시트 생성 시작' : '팝업 오케스트레이터 열기') : '후속 구현 예정'}
                            </button>
                        </div>
                    </article>
                );
            })}
        </div>
    );
}