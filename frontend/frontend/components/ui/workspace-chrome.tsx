'use client';

import Link from 'next/link';
import type { ReactNode } from 'react';

export type WorkspaceRailItem = {
    id: string;
    label: string;
    shortLabel?: string;
    href?: string;
    active?: boolean;
    accent?: 'blue' | 'violet' | 'emerald' | 'amber' | 'neutral' | 'slate' | 'cyan';
    onClick?: () => void;
    icon?: ReactNode;
    testId?: string;
};

type WorkspaceFeaturePill =
    | string
    | {
        id: string;
        label: string;
        href?: string;
        onClick?: () => void;
    };

export interface WorkspaceChromeProps {
    brand: string;
    title?: string;
    description?: string;
    statusLabel?: string;
    railItems: WorkspaceRailItem[];
    rightRailItems?: WorkspaceRailItem[];
    topActions?: ReactNode;
    heroBadge?: string;
    heroEyebrow?: string;
    heroTitle?: string;
    heroDescription?: string;
    heroActions?: ReactNode;
    heroFeaturePills?: WorkspaceFeaturePill[];
    sidebar?: ReactNode;
    hideHero?: boolean;
    children: ReactNode;
    pageTestId?: string;
    compactHeader?: boolean;
}

const accentClassMap: Record<NonNullable<WorkspaceRailItem['accent']>, string> = {
    blue: 'workspace-rail-item-accent-blue',
    violet: 'workspace-rail-item-accent-violet',
    emerald: 'workspace-rail-item-accent-emerald',
    amber: 'workspace-rail-item-accent-amber',
    neutral: 'workspace-rail-item-accent-neutral',
    slate: 'workspace-rail-item-accent-neutral',
    cyan: 'workspace-rail-item-accent-blue',
};

export default function WorkspaceChrome({
    brand,
    title,
    description,
    statusLabel,
    railItems,
    rightRailItems,
    topActions,
    heroBadge,
    heroEyebrow,
    heroTitle,
    heroDescription,
    heroActions,
    heroFeaturePills,
    sidebar,
    children,
    pageTestId,
    compactHeader,
    hideHero,
}: WorkspaceChromeProps) {
    return (
        <div className="workspace-shell" data-testid={pageTestId}>
            <aside className="workspace-rail" aria-label={`${brand} 탐색`}>
                <div className="workspace-rail-brand">{brand.slice(0, 2).toUpperCase()}</div>
                <nav className="workspace-rail-nav">
                    {railItems.map((item) => {
                        const accentClass = accentClassMap[item.accent ?? 'neutral'];
                        const className = `workspace-rail-item ${accentClass} ${item.active ? 'workspace-rail-item-active' : ''}`.trim();
                        const content = (
                            <>
                                {item.icon ? item.icon : <span className="workspace-rail-item-dot" aria-hidden="true" />}
                                <span className="workspace-rail-item-text">{item.shortLabel ?? item.label}</span>
                            </>
                        );
                        if (item.href) {
                            return (
                                <Link key={item.id} href={item.href} className={className}>
                                    {content}
                                </Link>
                            );
                        }
                        if (item.onClick) {
                            return (
                                <button key={item.id} type="button" onClick={item.onClick} className={className} data-testid={item.testId}>
                                    {content}
                                </button>
                            );
                        }
                        return (
                            <div key={item.id} className={className}>
                                {content}
                            </div>
                        );
                    })}
                </nav>
                <div className="workspace-rail-footer">LIVE</div>
            </aside>

            <div className={`workspace-stage ${compactHeader ? 'workspace-stage-compact' : ''}`}>
                <header className={`workspace-topbar ${compactHeader ? 'workspace-topbar-compact' : ''}`}>
                    <div>
                        {title && <p className="workspace-overline">{title}</p>}
                        <h1 className="workspace-page-title" style={!title && !description ? { margin: 0, fontSize: '18px' } : undefined}>{brand}</h1>
                        {description && <p className="workspace-page-description">{description}</p>}
                    </div>
                    <div className="workspace-topbar-actions">{topActions}</div>
                </header>

                {!hideHero && (
                    <section className={`workspace-hero-panel ${compactHeader ? 'workspace-hero-panel-compact' : ''}`}>
                        <div className="workspace-hero-copy">
                            <div className="workspace-hero-copy-topline">
                                {heroBadge ? <span className="workspace-badge">{heroBadge}</span> : null}
                                {statusLabel ? <span className="workspace-subtle-badge">{statusLabel}</span> : null}
                            </div>
                            {heroEyebrow ? <p className="workspace-hero-eyebrow">{heroEyebrow}</p> : null}
                            <h2 className="workspace-hero-title">{heroTitle}</h2>
                            <p className="workspace-hero-description">{heroDescription}</p>
                            {heroActions ? <div className="workspace-hero-actions">{heroActions}</div> : null}
                        </div>
                        {heroFeaturePills?.length ? (
                            <div className={`workspace-feature-grid ${compactHeader ? 'workspace-feature-grid-compact' : ''}`} aria-label="핵심 기능 바로가기">
                                {heroFeaturePills.map((pill) => {
                                    if (typeof pill === 'string') {
                                        return <div key={pill} className="workspace-feature-pill">{pill}</div>;
                                    }

                                    if (pill.href) {
                                        return (
                                            <Link key={pill.id} href={pill.href} className="workspace-feature-pill">
                                                {pill.label}
                                            </Link>
                                        );
                                    }

                                    if (pill.onClick) {
                                        return (
                                            <button key={pill.id} type="button" onClick={pill.onClick} className="workspace-feature-pill text-left">
                                                {pill.label}
                                            </button>
                                        );
                                    }

                                    return <div key={pill.id} className="workspace-feature-pill">{pill.label}</div>;
                                })}
                            </div>
                        ) : null}
                    </section>
                )}

                <div className={`workspace-content-grid ${sidebar ? 'workspace-content-grid-with-sidebar' : ''}`}>
                    <main className="workspace-main-content">{children}</main>
                    {sidebar ? <aside className="workspace-sidebar">{sidebar}</aside> : null}
                </div>
            </div>

            {rightRailItems && rightRailItems.length > 0 && (
                <aside className="workspace-rail workspace-rail-right" aria-label="추가 기능 탐색">
                    <nav className="workspace-rail-nav">
                        {rightRailItems.map((item) => {
                            const accentClass = accentClassMap[item.accent ?? 'neutral'];
                            const className = `workspace-rail-item ${accentClass} ${item.active ? 'workspace-rail-item-active' : ''}`.trim();
                            const content = (
                                <>
                                    {item.icon ? item.icon : <span className="workspace-rail-item-dot" aria-hidden="true" />}
                                    <span className="workspace-rail-item-text">{item.shortLabel ?? item.label}</span>
                                </>
                            );
                            if (item.href) {
                                return (
                                    <Link key={item.id} href={item.href} className={className}>
                                        {content}
                                    </Link>
                                );
                            }
                            if (item.onClick) {
                                return (
                                    <button key={item.id} type="button" onClick={item.onClick} className={className} data-testid={item.testId}>
                                        {content}
                                    </button>
                                );
                            }
                            return (
                                <div key={item.id} className={className}>
                                    {content}
                                </div>
                            );
                        })}
                    </nav>
                </aside>
            )}
        </div>
    );
}
