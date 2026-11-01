import type { ErrorEvent } from "@sentry/react";

// Frontend error tracking. Only when VITE_SENTRY_DSN is set at build time do we download the
// Sentry SDK (its own lazy chunk) and start it; unset, nothing loads and reportError is a no-op.
// No PII leaves the browser: no request bodies, cookies, headers or query strings, only the user
// id, and every email address in the event is masked.

type SentryModule = typeof import("@sentry/react");
let sentry: SentryModule | null = null;
let starting: Promise<boolean> | null = null;

const EMAIL = /[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}/gi;

function mask<T>(v: T): T {
  if (typeof v === "string") return v.replace(EMAIL, "[email]") as T;
  if (Array.isArray(v)) return v.map(mask) as T;
  if (v && typeof v === "object") {
    const out: Record<string, unknown> = {};
    for (const [k, x] of Object.entries(v)) out[k] = mask(x);
    return out as T;
  }
  return v;
}

export function scrubEvent(event: ErrorEvent): ErrorEvent {
  const e = mask(event);
  if (e.request) {
    delete e.request.data;
    delete e.request.cookies;
    delete e.request.headers;
    delete e.request.query_string;
    if (e.request.url) e.request.url = e.request.url.split("?")[0];
  }
  if (e.user) e.user = e.user.id ? { id: e.user.id } : {};
  return e;
}

export function initSentry(dsn: string | undefined = import.meta.env.VITE_SENTRY_DSN): Promise<boolean> {
  if (!dsn) return Promise.resolve(false);
  if (!starting) {
    starting = import("@sentry/react").then((mod) => {
      mod.init({
        dsn,
        environment: import.meta.env.MODE,
        release: import.meta.env.VITE_RELEASE || undefined,
        sendDefaultPii: false,
        tracesSampleRate: 0,
        beforeSend: scrubEvent,
      });
      sentry = mod;
      return true;
    }).catch(() => false);   // a blocked/failed SDK download must never break the app
  }
  return starting;
}

/** Report an error React caught (error boundaries swallow them before window.onerror). */
export function reportError(error: unknown, componentStack?: string | null) {
  sentry?.captureException(error, componentStack ? { extra: { componentStack } } : undefined);
}

export function _resetForTests() { sentry = null; starting = null; }
