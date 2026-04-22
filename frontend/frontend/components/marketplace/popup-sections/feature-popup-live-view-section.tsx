'use client';

import * as React from 'react';
import type { FeatureExperienceMeta, FeatureLiveViewArtifact, FeaturePopupState, FeatureProgressSnapshot, FeatureStreamConnection, SpreadsheetDownloadLink, SpreadsheetRunSummary } from '@/hooks/use-feature-orchestrator';

interface FeaturePopupLiveViewSectionProps {
    meta: FeatureExperienceMeta;
    isSpreadsheetBuilder: boolean;
    liveViewTitle: string;
    liveViewDescription: string;
    liveViewArtifact: FeatureLiveViewArtifact | null;
    popupState: FeaturePopupState;
    popupStateLabel: string;
    streamConnection: FeatureStreamConnection;
    streamConnectionLabel: string;
    spreadsheetRunSummary?: SpreadsheetRunSummary | null;
    spreadsheetDownloadLinks?: SpreadsheetDownloadLink[];
    elapsedLabel: string;
    progressSnapshot: FeatureProgressSnapshot | null;
    progressWidthClassName: string;
    stageRunStatus?: string;
    latestEventTimeLabel: string;
}

export default function FeaturePopupLiveViewSection(props: FeaturePopupLiveViewSectionProps) {
    return (
        <div data-testid="marketplace-live-view-panel" className="rounded-[24px] border border-[#25304a] bg-[radial-gradient(circle_at_top,_rgba(54,118,255,0.22),_rgba(11,15,22,0.92)_55%)] p-4 shadow-[0_20px_50px_rgba(0,0,0,0.18)] sm:rounded-[28px] sm:p-5">
            <div className="grid gap-5 xl:grid-cols-[1.15fr_0.85fr]">
                <div>
                    <div className="flex flex-col gap-3 sm:flex-row sm:flex-wrap sm:items-start sm:justify-between">
                        <div>
                            <p className="text-xs font-semibold uppercase tracking-[0.2em] text-[#8ec5ff]">Real-time Live View</p>
                            <h3 className="mt-2 text-xl font-bold text-white sm:text-2xl">{props.liveViewTitle}</h3>
                            <p className="mt-2 max-w-[640px] text-sm leading-6 text-[#b8c6d9]">{props.liveViewDescription}</p>
                        </div>
                        <span data-testid="marketplace-live-view-connection" className={`inline-flex w-fit rounded-full border px-3 py-1.5 text-xs font-semibold ${props.streamConnection === 'streaming' ? 'border-[#2fe37d] bg-[#0f2b1a] text-[#8af4b0]' : props.streamConnection === 'completed' ? 'border-[#58c9ff] bg-[#10264a] text-[#a8e6ff]' : props.streamConnection === 'failed' ? 'border-red-500/40 bg-red-950/30 text-red-200' : 'border-[#3a4558] bg-[#121a28] text-[#c9d7ea]'}`}>
                            {props.streamConnectionLabel}
                        </span>
                    </div>

                    {props.liveViewArtifact?.image_data_url ? (
                        <div data-testid="marketplace-live-view-spotlight" className="relative mt-5 overflow-hidden rounded-[20px] border border-[#365d96] bg-[#0b1019] sm:rounded-[24px]">
                            <img src={props.liveViewArtifact.image_data_url} alt={props.liveViewArtifact.title} className="h-[220px] w-full object-cover sm:h-[320px]" data-testid="marketplace-live-view-image" />
                            <div className="absolute inset-x-0 bottom-0 flex flex-col gap-2 bg-gradient-to-t from-black/80 via-black/35 to-transparent px-4 py-4 text-xs text-[#dbe7f7] sm:flex-row sm:items-center sm:justify-between sm:gap-3">
                                <span data-testid="marketplace-live-view-source">source {props.liveViewArtifact.source}</span>
                                <span data-testid="marketplace-live-view-state">{props.popupStateLabel}</span>
                            </div>
                        </div>
                    ) : props.isSpreadsheetBuilder && props.spreadsheetRunSummary ? (
                        <div data-testid="marketplace-live-view-sheet-summary" className="mt-5 rounded-[20px] border border-[#365d96] bg-[#0b1019] p-4 shadow-[0_12px_30px_rgba(42,124,255,0.12)] sm:rounded-[24px] sm:p-5">
                            <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
                                <div>
                                    <p className="text-xs uppercase tracking-[0.18em] text-[#8ec5ff]">Excel Live Feed</p>
                                    <h4 className="mt-2 text-xl font-bold text-white sm:text-2xl">{props.spreadsheetRunSummary.stageLabel}</h4>
                                    <p className="mt-2 max-w-[560px] text-sm leading-6 text-[#b8c6d9]">{props.spreadsheetRunSummary.stageDescription}</p>
                                </div>
                                <span className="inline-flex w-fit rounded-full border border-[#2a7cff] bg-[#10264a] px-3 py-1.5 text-xs font-semibold text-[#9ecbff]">{props.spreadsheetRunSummary.sheetName}</span>
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
                            <div className="mt-5 grid gap-3 sm:grid-cols-2">
                                <div className="rounded-2xl border border-[#1e2a3c] bg-[#10182b] px-4 py-3">
                                    <p className="text-xs uppercase tracking-[0.18em] text-[#8ea4bf]">Stage Run</p>
                                    <p className="mt-2 text-sm font-semibold text-white">{props.stageRunStatus || '미수신'}</p>
                                </div>
                                <div className="rounded-2xl border border-[#1e2a3c] bg-[#10182b] px-4 py-3">
                                    <p className="text-xs uppercase tracking-[0.18em] text-[#8ea4bf]">최근 이벤트</p>
                                    <p className="mt-2 text-sm font-semibold text-white">{props.latestEventTimeLabel}</p>
                                </div>
                            </div>
                        </div>
                    ) : (
                        <div data-testid="marketplace-live-view-empty" className="mt-5 flex min-h-[220px] items-center justify-center rounded-[20px] border border-dashed border-[#365d96] bg-[#0b1019] px-5 text-center text-sm leading-7 text-[#8ea4bf] sm:min-h-[320px] sm:rounded-[24px] sm:px-6">
                            {props.meta.emptyArtifactText}
                        </div>
                    )}
                </div>

                <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-1">
                    <div className="rounded-2xl border border-[#2d3d56] bg-[#0d1420] p-4 sm:col-span-2 xl:col-span-1">
                        <p className="text-xs uppercase tracking-[0.18em] text-[#7fa7d8]">사용 목적 가이드</p>
                        <div className="mt-3 grid gap-3 sm:grid-cols-3 xl:grid-cols-1">
                            {props.meta.statCards.map((card) => (
                                <div key={card.id} className="rounded-2xl border border-[#1e2a3c] bg-[#10182b] px-4 py-3">
                                    <p className="text-xs text-[#8ea4bf]">{card.label}</p>
                                    <p className="mt-2 text-sm font-semibold text-white">{card.note}</p>
                                </div>
                            ))}
                        </div>
                    </div>
                    <div className="rounded-2xl border border-[#2d3d56] bg-[#0d1420] p-4">
                        <p className="text-xs uppercase tracking-[0.18em] text-[#7fa7d8]">현재 단계</p>
                        <p data-testid="marketplace-live-view-current-state" className="mt-3 text-xl font-bold text-white">{props.popupStateLabel}</p>
                        <p className="mt-2 text-xs text-[#8ea4bf]">event log 와 stage snapshot 에 맞춰 즉시 갱신됩니다.</p>
                    </div>
                    <div className="rounded-2xl border border-[#2d3d56] bg-[#0d1420] p-4">
                        <p className="text-xs uppercase tracking-[0.18em] text-[#7fa7d8]">경과 시간</p>
                        <p data-testid="marketplace-live-view-elapsed" className="mt-3 text-xl font-bold text-white">{props.elapsedLabel}</p>
                        <p className="mt-2 text-xs text-[#8ea4bf]">accepted 시점부터 스트림 종료까지 실시간으로 증가합니다.</p>
                    </div>
                    <div data-testid="marketplace-progress-panel" className="rounded-2xl border border-[#2d3d56] bg-[#0d1420] p-4 sm:col-span-2 xl:col-span-1">
                        <div className="flex items-center justify-between gap-3">
                            <p className="text-xs uppercase tracking-[0.18em] text-[#7fa7d8]">세부 진행률</p>
                            <span data-testid="marketplace-progress-percent" className="text-sm font-bold text-white">{Math.max(0, Math.min(100, props.progressSnapshot?.percent || 0))}%</span>
                        </div>
                        <div className="mt-3 h-2 overflow-hidden rounded-full bg-[#1b2432]">
                            <div data-testid="marketplace-progress-bar" className={`h-full rounded-full bg-[linear-gradient(90deg,#2a7cff,#58c9ff)] transition-all duration-300 ${props.progressWidthClassName}`} />
                        </div>
                        <p data-testid="marketplace-progress-message" className="mt-3 text-sm font-semibold text-white">{props.progressSnapshot?.message || 'progress 이벤트 대기 중'}</p>
                        <p data-testid="marketplace-progress-step" className="mt-1 text-xs text-[#8ea4bf]">{props.progressSnapshot?.step || 'accepted'}</p>
                    </div>
                    <div className="rounded-2xl border border-[#2d3d56] bg-[#0d1420] p-4">
                        <p className="text-xs uppercase tracking-[0.18em] text-[#7fa7d8]">Live Guidance</p>
                        <ul className="mt-3 space-y-2 text-xs leading-6 text-[#c7d2df]">
                            <li>• 진행률 바와 milestone을 함께 보면 stuck 상태를 빠르게 구분할 수 있습니다.</li>
                            <li>• final phase를 켜면 workbook 패키지와 다운로드 패널까지 이어집니다.</li>
                            <li>• 모바일에서는 상단 상태 배지와 이 카드만으로도 현재 실행 상황을 확인할 수 있습니다.</li>
                        </ul>
                    </div>
                </div>
            </div>
        </div>
    );
}
