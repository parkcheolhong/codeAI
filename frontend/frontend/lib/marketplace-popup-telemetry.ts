'use client';

export type MarketplacePopupTelemetryEventName =
    | 'popup_open'
    | 'popup_close'
    | 'popup_submit'
    | 'popup_dwell_time';

export type MarketplacePopupTelemetryEvent = {
    eventName: MarketplacePopupTelemetryEventName;
    featureId: string;
    popupMode?: string;
    runId?: string;
    elapsedSeconds?: number;
    trigger?: string;
    timestamp: string;
    metadata?: Record<string, string | number | boolean | null | undefined>;
};

const MARKETPLACE_POPUP_TELEMETRY_STORAGE_KEY = 'marketplace-popup-telemetry-log';
const MARKETPLACE_POPUP_TELEMETRY_EVENT_NAME = 'marketplace:popup-telemetry';
const MAX_TELEMETRY_EVENTS = 50;

type WindowWithTelemetry = Window & {
    __MARKETPLACE_POPUP_TELEMETRY__?: MarketplacePopupTelemetryEvent[];
};

function isBrowserEnvironment() {
    return typeof window !== 'undefined';
}

function sanitizeEvent(event: MarketplacePopupTelemetryEvent): MarketplacePopupTelemetryEvent {
    return {
        ...event,
        featureId: String(event.featureId || '').trim(),
        popupMode: event.popupMode ? String(event.popupMode).trim() : undefined,
        runId: event.runId ? String(event.runId).trim() : undefined,
        trigger: event.trigger ? String(event.trigger).trim() : undefined,
        elapsedSeconds: Number.isFinite(Number(event.elapsedSeconds)) ? Number(event.elapsedSeconds) : undefined,
        timestamp: event.timestamp || new Date().toISOString(),
    };
}

function readStoredEvents(): MarketplacePopupTelemetryEvent[] {
    if (!isBrowserEnvironment()) {
        return [];
    }
    try {
        const raw = window.sessionStorage.getItem(MARKETPLACE_POPUP_TELEMETRY_STORAGE_KEY);
        const parsed = raw ? JSON.parse(raw) : [];
        return Array.isArray(parsed) ? parsed : [];
    } catch {
        return [];
    }
}

function writeStoredEvents(events: MarketplacePopupTelemetryEvent[]) {
    if (!isBrowserEnvironment()) {
        return;
    }
    try {
        const trimmed = events.slice(-MAX_TELEMETRY_EVENTS);
        window.sessionStorage.setItem(MARKETPLACE_POPUP_TELEMETRY_STORAGE_KEY, JSON.stringify(trimmed));
        (window as WindowWithTelemetry).__MARKETPLACE_POPUP_TELEMETRY__ = trimmed;
    } catch {
    }
}

export function recordMarketplacePopupTelemetry(event: MarketplacePopupTelemetryEvent) {
    if (!isBrowserEnvironment()) {
        return;
    }

    const safeEvent = sanitizeEvent(event);
    if (!safeEvent.featureId) {
        return;
    }

    const nextEvents = [...readStoredEvents(), safeEvent];
    writeStoredEvents(nextEvents);

    try {
        window.dispatchEvent(new CustomEvent(MARKETPLACE_POPUP_TELEMETRY_EVENT_NAME, { detail: safeEvent }));
    } catch {
    }

    try {
        console.debug('[marketplace-popup-telemetry]', safeEvent);
    } catch {
    }
}

export function buildMarketplacePopupTelemetryEvent(
    eventName: MarketplacePopupTelemetryEventName,
    input: Omit<MarketplacePopupTelemetryEvent, 'eventName' | 'timestamp'>,
): MarketplacePopupTelemetryEvent {
    return sanitizeEvent({
        eventName,
        timestamp: new Date().toISOString(),
        ...input,
    });
}

export function getMarketplacePopupTelemetryEvents() {
    return readStoredEvents();
}

export function clearMarketplacePopupTelemetryEvents() {
    if (!isBrowserEnvironment()) {
        return;
    }
    try {
        window.sessionStorage.removeItem(MARKETPLACE_POPUP_TELEMETRY_STORAGE_KEY);
        (window as WindowWithTelemetry).__MARKETPLACE_POPUP_TELEMETRY__ = [];
    } catch {
    }
}

export { MARKETPLACE_POPUP_TELEMETRY_EVENT_NAME };
