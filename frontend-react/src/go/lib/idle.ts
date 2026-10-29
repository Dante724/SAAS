// Idle rules for Tekly Go on a shared or unattended phone (pure, unit-tested; the provider in
// components/Lock.tsx feeds it taps, timer ticks and app-visible events).
//
// - Money needs a fresh check: after 5 minutes with no taps (or that long in the background),
//   taking a payment or making a pay link asks for the PIN first.
// - After 10 minutes the whole app locks behind the PIN, showing who is signed in.
// - The first tap after a long gap doesn't count as "still here": it's judged against the gap
//   that came before it, so you can't reset the clock just by touching the screen.
// - Time spent in the background counts as idle: nothing updates lastActive while hidden, and
//   the gap is measured on the wall clock when the app comes back.

export const RECHECK_MS = 5 * 60_000;
export const LOCK_MS = 10 * 60_000;

export type IdleState = {
  lastActive: number;   // wall-clock ms of the last tap / key while the app was visible
  stale: boolean;       // a 5-minute gap happened since the last PIN: money needs the PIN
  locked: boolean;      // a 10-minute gap happened: the lock screen is up
};

export function initIdle(now: number, stored?: number | null): IdleState {
  const last = stored && stored <= now ? stored : now;
  return tick({ lastActive: last, stale: now - last >= RECHECK_MS, locked: false }, now);
}

/** A tap or key press. */
export function activity(s: IdleState, now: number): IdleState {
  if (s.locked) return s;
  const gap = now - s.lastActive;
  if (gap >= LOCK_MS) return { ...s, locked: true, stale: true };
  return { lastActive: Math.max(now, s.lastActive), stale: s.stale || gap >= RECHECK_MS, locked: false };
}

/** A timer tick, or the app coming back to the foreground. */
export function tick(s: IdleState, now: number): IdleState {
  if (s.locked) return s;
  const gap = now - s.lastActive;
  if (gap >= LOCK_MS) return { ...s, locked: true, stale: true };
  if (gap >= RECHECK_MS && !s.stale) return { ...s, stale: true };
  return s;
}

/** Does a money action need the PIN right now? */
export function needsRecheck(s: IdleState, now: number): boolean {
  return s.locked || s.stale || now - s.lastActive >= RECHECK_MS;
}

/** The PIN (or Face ID) was just confirmed. */
export function verified(_s: IdleState, now: number): IdleState {
  return { lastActive: now, stale: false, locked: false };
}

// ── who the stored clock belongs to ──────────────────────────────────────────
// The last-active time survives a restart (so killing the app doesn't skip the lock), but it
// belongs to one sign-in: keyed by the token's user and original sign-in time, which a token
// refresh keeps. A new sign-in starts with a fresh clock.

export function sessionKey(token: string | null | undefined): string | null {
  if (!token) return null;
  try {
    const part = token.split(".")[1];
    if (!part) return null;
    const json = JSON.parse(atob(part.replace(/-/g, "+").replace(/_/g, "/").padEnd(Math.ceil(part.length / 4) * 4, "=")));
    if (!json?.sub) return null;
    return `${json.sub}:${json.auth_time ?? ""}`;
  } catch { return null; }
}

const KEY = "go_last_active";

export function readStored(session: string | null): number | null {
  if (!session) return null;
  try {
    const v = JSON.parse(localStorage.getItem(KEY) || "null");
    return v && v.s === session && typeof v.t === "number" ? v.t : null;
  } catch { return null; }
}

export function writeStored(session: string | null, t: number) {
  if (!session) return;
  try { localStorage.setItem(KEY, JSON.stringify({ s: session, t })); } catch { /* private mode */ }
}
