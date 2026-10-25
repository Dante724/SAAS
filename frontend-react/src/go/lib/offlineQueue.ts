// Encrypted offline mutation queue for Tekly Go — a TypeScript port of frontend/static/js/pwa.js.
//
// Same behaviour as the /tech app: a write is tried online first; when the phone is offline (or the
// server has a transient 5xx) it is encrypted with a non-extractable AES-GCM key and kept in
// IndexedDB, then replayed in order when the connection comes back. On replay:
//   2xx                → done, removed
//   409                → removed and kept as a "conflict" the user can re-apply or discard
//   401                → stop; the user has to sign in again (nothing is dropped)
//   403 network_not_allowed → KEEP and stop. The shop has "shop-only access" on and this phone is
//                        off the shop network; the change must survive until it is back on shop
//                        Wi-Fi, not be thrown away like other 4xx answers (design §4.6).
//   other 4xx          → dropped (the server will never accept it) and reported
//   5xx / network error → kept for the next try
//
// The storage, clock and network are injected so the replay rules can be unit-tested without a
// browser; `goQueue` at the bottom is the real instance backed by IndexedDB.
import { GO_CLIENT } from "./client";

export type QueuedItem = { method: string; url: string; body?: unknown };
export type Row = { id: number; payload: Encrypted; enqueued_at: number };
export type Encrypted = { iv: number[]; ct: number[] };
export type Conflict = QueuedItem & { id: string; detail: string; at: number };
export type QueueStatus = { pending: number; online: boolean; syncing: boolean; conflicts: number; held: boolean };
export type MutateResult =
  | { ok: true; queued: false; data: any }
  | { ok: true; queued: true; held?: boolean }
  | { ok: false; queued: false; status: number; data: any };

export interface QueueStore {
  add(payload: Encrypted, at: number): Promise<void>;
  all(): Promise<Row[]>;
  remove(id: number): Promise<void>;
  getMeta<T>(k: string): Promise<T | undefined>;
  putMeta(k: string, v: unknown): Promise<void>;
}

export type QueueDeps = {
  store: QueueStore;
  fetch: typeof fetch;
  token: () => string;
  online: () => boolean;
  now?: () => number;
  subtle?: SubtleCrypto;
  onRegister?: () => void;   // ask for a Background Sync after an enqueue
};

export type ReplayOutcome = "done" | "conflict" | "auth" | "held" | "drop" | "retry";

/** What a replay answer means for the queued item. Pure, so the rules are easy to test. */
export function classify(status: number, body: any): ReplayOutcome {
  if (status >= 200 && status < 300) return "done";
  if (status === 409) return "conflict";
  if (status === 401) return "auth";
  if (status === 403 && isNetworkBlocked(body)) return "held";
  if (status >= 400 && status < 500) return "drop";
  return "retry";
}

/** The shop-only-access refusal: `{code:"network_not_allowed"}`, bare or inside FastAPI's `detail`. */
export function isNetworkBlocked(body: any): boolean {
  const code = body?.code ?? body?.detail?.code;
  return code === "network_not_allowed";
}

const readJson = async (res: Response) => { try { return await res.clone().json(); } catch { return null; } };

export function createQueue(deps: QueueDeps) {
  const now = deps.now ?? (() => Date.now());
  const subtle = () => deps.subtle ?? globalThis.crypto.subtle;
  const listeners = new Set<(s: QueueStatus & { justFlushed?: boolean; dropped?: number }) => void>();
  let syncing = false;
  let held = false;

  async function key(): Promise<CryptoKey> {
    const existing = await deps.store.getMeta<CryptoKey>("cryptoKey");
    if (existing) return existing;
    const k = await subtle().generateKey({ name: "AES-GCM", length: 256 }, false, ["encrypt", "decrypt"]);
    await deps.store.putMeta("cryptoKey", k);
    return k;
  }
  async function enc(obj: QueuedItem): Promise<Encrypted> {
    const iv = globalThis.crypto.getRandomValues(new Uint8Array(12));
    const ct = await subtle().encrypt({ name: "AES-GCM", iv }, await key(), new TextEncoder().encode(JSON.stringify(obj)));
    return { iv: Array.from(iv), ct: Array.from(new Uint8Array(ct)) };
  }
  async function dec(blob: Encrypted): Promise<QueuedItem> {
    const pt = await subtle().decrypt({ name: "AES-GCM", iv: new Uint8Array(blob.iv) }, await key(), new Uint8Array(blob.ct));
    return JSON.parse(new TextDecoder().decode(pt));
  }

  const conflicts = async () => (await deps.store.getMeta<Conflict[]>("conflicts")) ?? [];

  async function status(): Promise<QueueStatus> {
    return { pending: (await deps.store.all()).length, online: deps.online(), syncing, conflicts: (await conflicts()).length, held };
  }
  async function emit(extra: Record<string, unknown> = {}) {
    const s = { ...(await status()), ...extra };
    listeners.forEach((fn) => { try { fn(s); } catch { /* a listener's bug never breaks the queue */ } });
  }

  function headers(): Record<string, string> {
    const t = deps.token();
    return { "Content-Type": "application/json", ...(t ? { Authorization: `Bearer ${t}` } : {}), "X-Tekly-Client": GO_CLIENT };
  }

  async function enqueue(item: QueuedItem) {
    await deps.store.add(await enc(item), now());
    deps.onRegister?.();
    await emit();
  }

  async function flush(): Promise<{ done: number; conflicts: number; dropped: number; failed: number; held: boolean }> {
    const out = { done: 0, conflicts: 0, dropped: 0, failed: 0, held: false };
    if (syncing || !deps.online()) return out;
    syncing = true;
    held = false;
    await emit();
    try {
      for (const row of await deps.store.all()) {
        let item: QueuedItem;
        try { item = await dec(row.payload); } catch { await deps.store.remove(row.id); continue; }   // unreadable (key lost) → can never replay
        let res: Response;
        try {
          res = await deps.fetch(item.url, { method: item.method, headers: headers(), body: item.body != null ? JSON.stringify(item.body) : undefined });
        } catch { out.failed++; break; }                                   // offline mid-flush → stop, keep the rest
        const body = await readJson(res);
        const outcome = classify(res.status, body);
        if (outcome === "done") { out.done++; await deps.store.remove(row.id); }
        else if (outcome === "conflict") {
          out.conflicts++;
          await deps.store.remove(row.id);
          const list = await conflicts();
          list.push({ ...item, id: `c${now()}${Math.random().toString(36).slice(2, 6)}`, at: now(),
            detail: (typeof body?.detail === "string" && body.detail) || body?.message || "Conflicts with a newer change" });
          await deps.store.putMeta("conflicts", list);
        }
        else if (outcome === "auth") { out.failed++; break; }
        else if (outcome === "held") { out.held = true; held = true; break; }   // off the shop network: keep everything, try again later
        else if (outcome === "drop") { out.dropped++; await deps.store.remove(row.id); }
        else { out.failed++; }                                             // 5xx → keep, retry later
      }
    } finally {
      syncing = false;
      await emit({ justFlushed: true, dropped: out.dropped });
    }
    return out;
  }

  /** Online first; queue when offline, on a transient failure, or when the shop network blocks it. */
  async function mutate(method: string, url: string, body?: unknown): Promise<MutateResult> {
    if (deps.online()) {
      try {
        const res = await deps.fetch(url, { method, headers: headers(), body: body != null ? JSON.stringify(body) : undefined });
        const data = await readJson(res);
        if (res.ok) return { ok: true, queued: false, data: data ?? {} };
        if (res.status === 403 && isNetworkBlocked(data)) {
          held = true;
          await enqueue({ method, url, body });
          return { ok: true, queued: true, held: true };
        }
        if (res.status < 500) return { ok: false, queued: false, status: res.status, data };
      } catch { /* network error → queue */ }
    }
    await enqueue({ method, url, body });
    return { ok: true, queued: true };
  }

  async function resolveConflict(id: string, action: "retry" | "discard") {
    const list = await conflicts();
    const c = list.find((x) => x.id === id);
    await deps.store.putMeta("conflicts", list.filter((x) => x.id !== id));
    if (c && action === "retry") {
      await enqueue({ method: c.method, url: c.url, body: c.body });
      if (deps.online()) await flush();
    }
    await emit();
  }

  /** Forget everything queued (on sign-out, so the next person can't replay someone else's changes). */
  async function clear() {
    for (const r of await deps.store.all()) await deps.store.remove(r.id);
    await deps.store.putMeta("conflicts", []);
    held = false;
    await emit();
  }

  return {
    mutate, flush, status, enqueue, resolveConflict, clear,
    conflicts,
    subscribe(fn: (s: QueueStatus & { justFlushed?: boolean; dropped?: number }) => void) {
      listeners.add(fn);
      return () => { listeners.delete(fn); };
    },
  };
}

export type GoQueue = ReturnType<typeof createQueue>;

// ── IndexedDB store (the real one) ──────────────────────────────────────────
const DB_NAME = "tekly-go";

function idb(): Promise<IDBDatabase> {
  return new Promise((res, rej) => {
    const r = indexedDB.open(DB_NAME, 1);
    r.onupgradeneeded = () => {
      const db = r.result;
      if (!db.objectStoreNames.contains("queue")) db.createObjectStore("queue", { keyPath: "id", autoIncrement: true });
      if (!db.objectStoreNames.contains("meta")) db.createObjectStore("meta", { keyPath: "k" });
    };
    r.onsuccess = () => res(r.result);
    r.onerror = () => rej(r.error);
  });
}
function req<T>(r: IDBRequest<T>): Promise<T> {
  return new Promise((res, rej) => { r.onsuccess = () => res(r.result); r.onerror = () => rej(r.error); });
}
const os = async (store: string, mode: IDBTransactionMode) => (await idb()).transaction(store, mode).objectStore(store);

export const idbStore: QueueStore = {
  async add(payload, at) { await req((await os("queue", "readwrite")).add({ payload, enqueued_at: at })); },
  async all() { return (await req((await os("queue", "readonly")).getAll())) as Row[]; },
  async remove(id) { await req((await os("queue", "readwrite")).delete(id)); },
  async getMeta<T>(k: string) { return ((await req((await os("meta", "readonly")).get(k))) as { v: T } | undefined)?.v; },
  async putMeta(k, v) { await req((await os("meta", "readwrite")).put({ k, v })); },
};

function storedToken(): string {
  try { return localStorage.getItem("ros_tok") || localStorage.getItem("ros_token") || localStorage.getItem("token") || ""; }
  catch { return ""; }
}

let _q: GoQueue | null = null;
/** The app's queue (created on first use so tests that import this module never touch IndexedDB). */
export function goQueue(): GoQueue {
  if (_q) return _q;
  const q = createQueue({
    store: idbStore,
    fetch: (...a) => fetch(...a),
    token: storedToken,
    online: () => navigator.onLine,
    onRegister: () => {
      if ("serviceWorker" in navigator && "SyncManager" in window) {
        navigator.serviceWorker.ready.then((reg: any) => reg.sync?.register("tekly-go-sync")).catch(() => {});
      }
    },
  });
  _q = q;
  // Retry whenever the connection might have changed: back online, back in the app, a different
  // network (e.g. phone moved from mobile data onto shop Wi-Fi), or the worker's Background Sync.
  const kick = () => { if (navigator.onLine) q.flush(); };
  window.addEventListener("online", kick);
  document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible") kick(); });
  (navigator as any).connection?.addEventListener?.("change", kick);
  navigator.serviceWorker?.addEventListener?.("message", (e: MessageEvent) => { if (e.data?.type === "FLUSH_QUEUE") kick(); });
  setTimeout(kick, 1500);
  return q;
}
