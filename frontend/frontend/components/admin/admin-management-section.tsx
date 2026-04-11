'use client';

import { useEffect } from 'react';
import type { ReactNode } from 'react';

interface AdminManagementSectionProps {
    title: string;
    usage: string;
    description: string;
    open: boolean;
    onToggle: () => void;
    toggleTestId?: string;
    windowSize?: 'default' | 'wide' | 'full';
    launcherHidden?: boolean;
    children: ReactNode;
}

export default function AdminManagementSection({
    title,
    usage,
    description,
    open,
    onToggle,
    toggleTestId,
    windowSize = 'wide',
    launcherHidden = false,
    children,
}: AdminManagementSectionProps) {
    useEffect(() => {
        if (!open) {
            return;
        }

        const previousOverflow = document.body.style.overflow;
        document.body.style.overflow = 'hidden';

        const handleEscape = (event: KeyboardEvent) => {
            if (event.key === 'Escape') {
                onToggle();
            }
        };

        window.addEventListener('keydown', handleEscape);
        return () => {
            document.body.style.overflow = previousOverflow;
            window.removeEventListener('keydown', handleEscape);
        };
    }, [open, onToggle]);

    return (
        <>
            {!launcherHidden && (
                <section className="workspace-card mb-5 workspace-section-launcher" data-testid={toggleTestId}>
                    <div className="workspace-section-launcher-copy">
                        <div className="flex flex-wrap items-center gap-2">
                            <h2 className="workspace-card-title">{title}</h2>
                            <span className="workspace-chip">
                                관리 목적: {usage}
                            </span>
                            <span className="workspace-chip">
                                버튼형 창 열기
                            </span>
                        </div>
                        <p className="workspace-card-copy mt-1">{description}</p>
                    </div>
                    <div className="workspace-section-launcher-actions">
                        <button
                            type="button"
                            onClick={onToggle}
                            className="workspace-open-window-button"
                        >
                            {open ? '창 닫기' : '창 열기'}
                        </button>
                    </div>
                </section>
            )}
            {open && (
                <div className="workspace-window-backdrop" role="presentation" onClick={onToggle}>
                    <section
                        className={`workspace-window workspace-window-${windowSize}`}
                        role="dialog"
                        aria-modal="true"
                        aria-label={title}
                        onClick={(event) => event.stopPropagation()}
                    >
                        <div className="workspace-window-header">
                            <div>
                                <p className="workspace-card-kicker">WINDOW PANEL</p>
                                <h2 className="workspace-card-heading">{title}</h2>
                                <p className="workspace-card-copy">{description}</p>
                            </div>
                            <div className="workspace-window-actions">
                                <span className="workspace-chip">{usage}</span>
                                <button type="button" onClick={onToggle} className="workspace-ghost-button">
                                    닫기
                                </button>
                            </div>
                        </div>
                        <div className="workspace-window-body">{children}</div>
                    </section>
                </div>
            )}
        </>
    );
}
