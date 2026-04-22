'use client';

import * as React from 'react';
import type { FeaturePopupState, FeatureProgressSnapshot } from '@/hooks/use-feature-orchestrator';
import type { PopupEventLogItem } from '@/components/marketplace/popup-sections/feature-popup-types';

interface FeaturePopupStateSectionProps {
    popupState: FeaturePopupState;
    popupStateFlow: FeaturePopupState[];
    stateLabel: (state: FeaturePopupState) => string;
    eventLog: PopupEventLogItem[];
    progressHistory: FeatureProgressSnapshot[];
}

export default function FeaturePopupStateSection(props: FeaturePopupStateSectionProps) {
    return (
        <div className="rounded-[24px] border border-[#25304a] bg-[linear-gradient(180deg,#10182b,#0e1524)] p-4 shadow-[0_18px_40px_rgba(0,0,0,0.14)]">
            <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
                <div>
                    <p className="text-sm font-semibold text-white">상태 전이</p>
                    <p className="mt-1 text-xs leading-6 text-[#8ea4bf]">preview → final → quality review 흐름을 milestone/event log 기준으로 추적합니다.</p>
                </div>
                <span className="inline-flex w-fit rounded-full border border-[#2d3d56] bg-[#0f1726] px-3 py-1 text-[11px] text-[#c7d2df]">현재 · {props.stateLabel(props.popupState)}</span>
            </div>
            <div data-testid="marketplace-state-flow" className="mt-4 flex flex-wrap gap-2">
                {props.popupStateFlow.map((state) => {
                    const active = props.popupState === state;
                    const completed = props.eventLog.some((item) => item.state === state) && !active;
                    return (
                        <span key={state} className={`rounded-full border px-3 py-1.5 text-xs ${active ? 'border-[#19c3f3] bg-[#0d2230] text-[#67d6ff]' : completed ? 'border-[#31c45d] bg-[#102416] text-[#7af0a0]' : 'border-[#30363d] bg-[#0d1117] text-[#8b949e]'}`}>
                            {props.stateLabel(state)}
                        </span>
                    );
                })}
                {props.popupState === 'completed_preview_only' && <span className="rounded-full border border-[#f0b43f] bg-[#2d220b] px-3 py-1.5 text-xs text-[#f0d28a]">preview 전용 완료</span>}
                {props.popupState === 'failed' && <span className="rounded-full border border-red-500/40 bg-red-950/20 px-3 py-1.5 text-xs text-red-200">실패</span>}
            </div>
            <div className="mt-4 rounded-2xl border border-[#2d3d56] bg-[#0d1420] p-4">
                <p className="text-xs uppercase tracking-[0.18em] text-[#7fa7d8]">Event Timeline</p>
                <div className="mt-3 space-y-2 text-xs text-[#8b949e]">
                {props.eventLog.map((item, index) => (
                    <p key={`${item.state}-${item.at}-${index}`} className="rounded-xl border border-[#1e2a3c] bg-[#0b1019] px-3 py-2">{new Date(item.at).toLocaleTimeString('ko-KR')} · {props.stateLabel(item.state)}</p>
                ))}
                </div>
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
    );
}
