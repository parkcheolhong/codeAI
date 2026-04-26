'use client';

import * as React from 'react';
import type { FeatureArtifact, FeatureExperienceMeta, FeatureLiveViewArtifact, FeaturePopupState, FeatureProgressSnapshot, FeatureStreamConnection, SpreadsheetDownloadLink, SpreadsheetRunSummary } from '@/hooks/use-feature-orchestrator';
import FeaturePopupInputSection from '@/components/marketplace/popup-sections/feature-popup-input-section';
import { connectionLabel, formatElapsed, POPUP_STATE_FLOW, progressWidthClass, stateLabel } from '@/components/marketplace/popup-sections/feature-popup-helpers';
import FeaturePopupLiveViewSection from '@/components/marketplace/popup-sections/feature-popup-live-view-section';
import FeaturePopupOutputSection from '@/components/marketplace/popup-sections/feature-popup-output-section';
import FeaturePopupStateSection from '@/components/marketplace/popup-sections/feature-popup-state-section';
import type { PopupEventLogItem, PopupQualityReview } from '@/components/marketplace/popup-sections/feature-popup-types';

interface FeatureOrchestratorPopupProps {
    isOpen: boolean;
    activeFeatureId: string;
    featureMeta: FeatureExperienceMeta;
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
    qualityReview: PopupQualityReview;
    submitLoading: boolean;
    submitFeature: () => void;
    closePopup: () => void;
    errorText: string;
    runId: string;
    eventLog: PopupEventLogItem[];
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

    const dialogId = React.useId();
    const descriptionId = React.useId();
    const dialogRef = React.useRef<HTMLDivElement | null>(null);
    const closeButtonRef = React.useRef<HTMLButtonElement | null>(null);
    const contentStartRef = React.useRef<HTMLDivElement | null>(null);
    const meta = props.featureMeta;
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
    const liveViewTitle = props.liveViewArtifact?.title || meta.liveViewTitle;
    const liveViewDescription = props.liveViewArtifact?.caption || meta.liveViewDescription;

    React.useEffect(() => {
        const previousActiveElement = document.activeElement instanceof HTMLElement ? document.activeElement : null;
        const timer = window.setTimeout(() => {
            closeButtonRef.current?.focus();
        }, 0);

        return () => {
            window.clearTimeout(timer);
            previousActiveElement?.focus();
        };
    }, []);

    React.useEffect(() => {
        const previousOverflow = document.body.style.overflow;
        document.body.style.overflow = 'hidden';

        return () => {
            document.body.style.overflow = previousOverflow;
        };
    }, []);

    const handleDialogKeyDown = React.useCallback((event: React.KeyboardEvent<HTMLDivElement>) => {
        if (event.key === 'Escape') {
            event.preventDefault();
            props.closePopup();
            return;
        }

        if (event.key !== 'Tab' || !dialogRef.current) {
            return;
        }

        const focusableElements = Array.from(
            dialogRef.current.querySelectorAll<HTMLElement>(
                'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
            ),
        ).filter((element) => !element.hasAttribute('disabled') && element.tabIndex !== -1 && element.offsetParent !== null);

        if (!focusableElements.length) {
            event.preventDefault();
            dialogRef.current.focus();
            return;
        }

        const firstElement = focusableElements[0];
        const lastElement = focusableElements[focusableElements.length - 1];
        const activeElement = document.activeElement as HTMLElement | null;

        if (event.shiftKey) {
            if (!activeElement || activeElement === firstElement || !dialogRef.current.contains(activeElement)) {
                event.preventDefault();
                lastElement.focus();
            }
            return;
        }

        if (!activeElement || activeElement === lastElement || !dialogRef.current.contains(activeElement)) {
            event.preventDefault();
            firstElement.focus();
        }
    }, [props]);

    return (
        <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/75 px-2 py-2 backdrop-blur-sm sm:px-4 sm:py-6" data-testid="marketplace-feature-orchestrator-popup" onMouseDown={(event) => {
            if (event.target === event.currentTarget) {
                props.closePopup();
            }
        }}>
            <div
                ref={dialogRef}
                role="dialog"
                aria-modal="true"
                aria-labelledby={dialogId}
                aria-describedby={descriptionId}
                tabIndex={-1}
                onKeyDown={handleDialogKeyDown}
                className="my-auto max-h-[calc(100vh-1rem)] w-full max-w-6xl overflow-x-hidden overflow-y-auto rounded-[24px] border border-[#25304a] bg-[#0b0f16] p-3 text-[#e6edf3] shadow-[0_30px_120px_rgba(0,0,0,0.45)] outline-none sm:max-h-[92vh] sm:rounded-[28px] sm:p-6"
            >
                <div className="sticky top-0 z-10 -mx-3 -mt-3 mb-5 border-b border-[#1f2937] bg-[linear-gradient(180deg,rgba(11,15,22,0.98),rgba(11,15,22,0.92))] px-3 py-3 backdrop-blur sm:-mx-6 sm:-mt-6 sm:mb-6 sm:px-6 sm:py-5">
                <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
                    <div className="min-w-0 flex-1">
                        <p className="text-sm font-semibold uppercase tracking-[0.18em] text-[#58c9ff]">{meta.popupKicker}</p>
                        <h2 id={dialogId} className="mt-2 text-2xl font-bold text-white sm:text-3xl">{props.title}</h2>
                        <p id={descriptionId} className="mt-3 max-w-[980px] text-sm leading-7 text-[#9eabba]">{props.featureSummary}</p>
                        <div className="mt-4 flex flex-wrap gap-2 text-[11px] text-[#d6e3f5]" aria-label="현재 popup 상태 요약">
                            <span className="rounded-full border border-[#2d3d56] bg-[#0f1726] px-3 py-1.5">상태 · {stateLabel(props.popupState)}</span>
                            <span className="rounded-full border border-[#2d3d56] bg-[#0f1726] px-3 py-1.5">run_id · {props.runId || '대기'}</span>
                            <span className="rounded-full border border-[#2d3d56] bg-[#0f1726] px-3 py-1.5">연결 · {connectionLabel(props.streamConnection)}</span>
                            <span className="rounded-full border border-[#2d3d56] bg-[#0f1726] px-3 py-1.5">경과 · {formatElapsed(props.elapsedSeconds)}</span>
                        </div>
                    </div>
                    <div className="flex w-full flex-col gap-2 sm:w-auto sm:items-end">
                        <button ref={closeButtonRef} type="button" aria-label={`${props.title} 팝업 닫기`} onClick={props.closePopup} className="w-full rounded-2xl border border-[#3a4558] bg-[#121826] px-4 py-2.5 text-sm font-semibold text-[#d2d9e3] transition hover:border-[#58c9ff] hover:text-white focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#58c9ff] sm:w-auto">닫기</button>
                        <button type="button" onClick={() => contentStartRef.current?.scrollIntoView({ block: 'start', behavior: 'smooth' })} className="w-full rounded-2xl border border-transparent bg-[#10264a] px-4 py-2 text-xs font-semibold text-[#9ecbff] transition hover:border-[#2a7cff] hover:text-white focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#58c9ff] sm:w-auto">입력/결과로 이동</button>
                    </div>
                </div>
                </div>

                <div ref={contentStartRef} className="grid gap-4 sm:gap-6 xl:grid-cols-[0.92fr_1.08fr]" data-testid="marketplace-popup-content-grid">
                    <section className="space-y-4" aria-label="실행 입력과 상태 패널">
                        <FeaturePopupLiveViewSection
                            meta={meta}
                            isSpreadsheetBuilder={isSpreadsheetBuilder}
                            liveViewTitle={liveViewTitle}
                            liveViewDescription={liveViewDescription}
                            liveViewArtifact={props.liveViewArtifact}
                            popupState={props.popupState}
                            popupStateLabel={stateLabel(props.popupState)}
                            streamConnection={props.streamConnection}
                            streamConnectionLabel={connectionLabel(props.streamConnection)}
                            spreadsheetRunSummary={props.spreadsheetRunSummary}
                            spreadsheetDownloadLinks={props.spreadsheetDownloadLinks}
                            elapsedLabel={formatElapsed(props.elapsedSeconds)}
                            progressSnapshot={props.progressSnapshot}
                            progressWidthClassName={progressWidthClass(props.progressSnapshot?.percent || 0)}
                            stageRunStatus={props.stageRunStatus}
                            latestEventTimeLabel={props.latestEventAt ? new Date(props.latestEventAt).toLocaleTimeString('ko-KR') : '대기'}
                        />

                        <FeaturePopupInputSection
                            meta={meta}
                            projectName={props.projectName}
                            setProjectName={props.setProjectName}
                            templateId={props.templateId}
                            setTemplateId={props.setTemplateId}
                            prompt={props.prompt}
                            setPrompt={props.setPrompt}
                            supportsPhotoUpload={props.supportsPhotoUpload}
                            photoFileName={props.photoFileName}
                            photoPreviewUrl={props.photoPreviewUrl}
                            applyPhotoFile={props.applyPhotoFile}
                            finalEnabled={props.finalEnabled}
                            setFinalEnabled={props.setFinalEnabled}
                            submitLoading={props.submitLoading}
                            submitFeature={props.submitFeature}
                            runId={props.runId}
                            errorText={props.errorText}
                        />

                        <FeaturePopupStateSection
                            popupState={props.popupState}
                            popupStateFlow={POPUP_STATE_FLOW}
                            stateLabel={stateLabel}
                            eventLog={props.eventLog}
                            progressHistory={props.progressHistory}
                        />
                    </section>

                    <FeaturePopupOutputSection
                        outputKind={meta.outputKind}
                        isSpreadsheetBuilder={isSpreadsheetBuilder}
                        spreadsheetDownloadLinks={props.spreadsheetDownloadLinks}
                        latestSpreadsheetDownloadFormat={latestSpreadsheetDownloadFormat}
                        previewTitle={meta.previewTitle}
                        finalTitle={meta.finalTitle}
                        previewArtifact={props.previewArtifact}
                        finalArtifact={props.finalArtifact}
                        qualityReview={props.qualityReview}
                        qualityGateScoreLabel={qualityGateScoreLabel}
                    />
                </div>
            </div>
        </div>
    );
}
