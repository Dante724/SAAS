// Cross-device user prefs (144ca adoption). Backs the per-user UI choices
// (saved views, row density) with the server /api/prefs KV store so they follow
// a user across devices, while keeping a localStorage cache for instant paint
// and offline. Keys must be scoped "listview:*" to pass the server allowlist.
import { api } from "../api/client";

// Synchronous cached read — use for the initial useState so there's no flash.
export function cachedPref<T>(key: string, fallback: T): T {
  try { const ls = localStorage.getItem(key); if (ls) return JSON.parse(ls) as T; } catch { /* noop */ }
  return fallback;
}

// Reconcile with the server (call in a mount effect). Resolves to the server
// value when present, else the cached/fallback value; refreshes the cache.
export async function loadPref<T>(key: string, fallback: T): Promise<T> {
  try {
    const r = await api.get<{ value: any }>(`/prefs/${encodeURIComponent(key)}`);
    if (r && r.value && Object.keys(r.value).length) {
      try { localStorage.setItem(key, JSON.stringify(r.value)); } catch { /* noop */ }
      return r.value as T;
    }
  } catch { /* offline / not logged in — fall back to cache */ }
  return cachedPref(key, fallback);
}

// Write-through: update the cache immediately, push to the server best-effort.
export function savePref(key: string, value: unknown): void {
  try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* noop */ }
  api.put(`/prefs/${encodeURIComponent(key)}`, { value }).catch(() => { /* best effort */ });
}
