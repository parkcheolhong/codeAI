'use client';

import * as React from 'react';
import type { FeatureExperienceMeta } from '@/hooks/use-feature-orchestrator';

interface FeaturePopupInputSectionProps {
    meta: FeatureExperienceMeta;
    projectName: string;
    setProjectName: (value: string) => void;
    templateId: string;
    setTemplateId: (value: string) => void;
    prompt: string;
    setPrompt: (value: string) => void;
    supportsPhotoUpload: boolean;
    photoFileName: string;
    photoPreviewUrl: string;
    applyPhotoFile: (file: File | null) => void;
    finalEnabled: boolean;
    setFinalEnabled: (value: boolean) => void;
    submitLoading: boolean;
    submitFeature: () => void;
    runId: string;
    errorText: string;
}

export default function FeaturePopupInputSection(props: FeaturePopupInputSectionProps) {
    return (
        <div className="rounded-[24px] border border-[#25304a] bg-[linear-gradient(180deg,#10182b,#0e1524)] p-4 shadow-[0_18px_50px_rgba(0,0,0,0.18)] sm:p-5">
            <div className="flex flex-col gap-3 sm:flex-row sm:flex-wrap sm:items-start sm:justify-between">
                <div className="min-w-0 flex-1">
                    <p className="text-sm font-semibold text-white">{props.meta.inputTitle}</p>
                    <p className="mt-1 text-xs leading-6 text-[#8ea4bf]">{props.meta.inputDescription}</p>
                </div>
                <div className="flex flex-wrap gap-2 text-[11px] text-[#d2d9e3]">
                    {props.meta.quickPromptChips.map((chip) => (
                        <button key={chip} type="button" onClick={() => props.setPrompt((props.prompt ? `${props.prompt.trim()}\n` : '') + chip)} className="rounded-full border border-[#2a7cff] bg-[#10264a] px-3 py-1.5 text-[#9ecbff] transition hover:border-[#58c9ff] hover:text-white focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#58c9ff]">{chip}</button>
                    ))}
                </div>
            </div>
            <div className="mt-4 space-y-3">
                <div className="space-y-2">
                    <label htmlFor="marketplace-popup-project-name" className="text-xs font-semibold uppercase tracking-[0.16em] text-[#8ea4bf]">프로젝트 이름</label>
                    <input id="marketplace-popup-project-name" data-testid="marketplace-popup-project-name" value={props.projectName} onChange={(event) => props.setProjectName(event.target.value)} placeholder={props.meta.projectPlaceholder} className="w-full rounded-2xl border border-[#30363d] bg-[#0d1117] px-4 py-3 text-sm text-white outline-none transition placeholder:text-[#5f6f83] focus:border-[#58c9ff] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#58c9ff]" />
                </div>
                <div>
                    <label htmlFor="marketplace-popup-template-id" className="mb-2 block text-xs uppercase tracking-[0.18em] text-[#8ea4bf]">{props.meta.templateLabel}</label>
                    <select id="marketplace-popup-template-id" data-testid="marketplace-popup-template-id" aria-label={props.meta.templateLabel} title={props.meta.templateLabel} value={props.templateId} onChange={(event) => props.setTemplateId(event.target.value)} className="w-full rounded-2xl border border-[#30363d] bg-[#0d1117] px-4 py-3 text-sm text-white outline-none transition focus:border-[#58c9ff] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#58c9ff]">
                        {props.meta.templateOptions.map((option) => (
                            <option key={option.value} value={option.value}>{option.label}</option>
                        ))}
                    </select>
                </div>
                <div className="space-y-2">
                    <label htmlFor="marketplace-popup-prompt" className="text-xs font-semibold uppercase tracking-[0.16em] text-[#8ea4bf]">생성 지시</label>
                    <textarea id="marketplace-popup-prompt" data-testid="marketplace-popup-prompt" value={props.prompt} onChange={(event) => props.setPrompt(event.target.value)} rows={8} placeholder={props.meta.promptPlaceholder} className="w-full rounded-2xl border border-[#30363d] bg-[#0d1117] px-4 py-3 text-sm leading-7 text-white outline-none transition placeholder:text-[#5f6f83] focus:border-[#58c9ff] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#58c9ff]" />
                    <div className="flex flex-wrap items-center justify-between gap-2 text-[11px] text-[#8ea4bf]">
                        <span>실행 목적과 결과 형식을 한 문장 이상으로 적으면 liveview 품질이 좋아집니다.</span>
                        <span>{props.prompt.trim().length} chars</span>
                    </div>
                </div>
                {props.supportsPhotoUpload && (
                    <>
                        <label data-testid="marketplace-popup-photo-upload" className="block rounded-2xl border border-dashed border-[#3a4558] bg-[#0d1117] px-4 py-4 text-sm text-[#c9d1d9] transition hover:border-[#58c9ff] focus-within:border-[#58c9ff] focus-within:outline focus-within:outline-2 focus-within:outline-offset-2 focus-within:outline-[#58c9ff]">
                            <span className="font-semibold text-white">{props.meta.uploadLabel || '참조 파일 업로드'}</span>
                            <input data-testid="marketplace-popup-photo-input" type="file" accept="image/*" className="mt-3 block w-full text-sm text-[#c9d1d9]" onChange={(event) => props.applyPhotoFile(event.target.files?.[0] || null)} />
                            {props.photoFileName && <p className="mt-2 text-xs text-[#8b949e]">선택 파일: {props.photoFileName}</p>}
                        </label>
                        {props.photoPreviewUrl && <img src={props.photoPreviewUrl} alt="업로드 preview" data-testid="marketplace-popup-photo-preview" className="h-32 w-full rounded-2xl object-cover sm:h-40" />}
                    </>
                )}
                <label className="flex items-start gap-3 rounded-2xl border border-[#30363d] bg-[#0d1117] px-4 py-3 text-sm text-[#d2d9e3] transition hover:border-[#58c9ff] focus-within:border-[#58c9ff]">
                    <input data-testid="marketplace-popup-final-enabled" type="checkbox" checked={props.finalEnabled} onChange={(event) => props.setFinalEnabled(event.target.checked)} />
                    <span>
                        <span className="font-semibold text-white">{props.meta.finalToggleLabel}</span>
                        <span className="mt-1 block text-xs text-[#8ea4bf]">preview만 볼지 final 산출물과 다운로드까지 생성할지 선택합니다.</span>
                    </span>
                </label>
            </div>
            <div className="mt-4 flex flex-col gap-3 sm:flex-row sm:items-center">
                <button type="button" data-testid="marketplace-popup-submit" onClick={props.submitFeature} disabled={props.submitLoading} className="w-full rounded-2xl bg-[linear-gradient(135deg,#2a7cff,#58c9ff)] px-5 py-3 text-sm font-bold text-white shadow-[0_14px_30px_rgba(42,124,255,0.28)] transition hover:brightness-110 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#58c9ff] disabled:cursor-not-allowed disabled:bg-[#29425f] disabled:shadow-none sm:w-auto">{props.submitLoading ? '실행 중...' : props.meta.submitLabel}</button>
                {props.runId && <span data-testid="marketplace-popup-run-id" className="w-full rounded-2xl border border-[#30363d] bg-[#0d1117] px-4 py-3 text-xs text-[#8b949e] sm:w-auto">run_id {props.runId}</span>}
            </div>
            {props.errorText && <div data-testid="marketplace-popup-error" className="mt-4 rounded-2xl border border-red-400/40 bg-red-950/20 px-4 py-3 text-sm text-red-200">{props.errorText}</div>}
        </div>
    );
}
