import { describe, expect, it, vi } from "vitest";
import { webcrypto } from "node:crypto";
import { classify, createQueue, isNetworkBlocked, type QueueStore, type Row } from "./offlineQueue";

function memStore(): QueueStore & { rows: Row[]; meta: Map<string, unknown> } {
  let next = 1;
  const rows: Row[] = [];
  const meta = new Map<string, unknown>();
  return {
    rows, meta,
    async add(payload, at) { rows.push({ id: next++, payload, enqueued_at: at }); },
    async all() { return [...rows]; },
    async remove(id) { const i = rows.findIndex((r) => r.id === id); if (i >= 0) rows.splice(i, 1); },
    async getMeta<T>(k: string) { return meta.get(k) as T | undefined; },
    async putMeta(k, v) { meta.set(k, v); },
  };
}

const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

function setup(opts: { online?: boolean } = {}) {
  const store = memStore();
  const state = { online: opts.online ?? false };
  const fetch = vi.fn<(url: string, init?: RequestInit) => Promise<Response>>();
  const q = createQueue({
    store, fetch: fetch as unknown as typeof globalThis.fetch, token: () => "tok-1",
    online: () => state.online, subtle: webcrypto.subtle as unknown as SubtleCrypto,
  });
  return { store, state, fetch, q };
}

describe("classify", () => {
  it("maps replay answers to what happens to the item", () => {
    expect(classify(200, {})).toBe("done");
    expect(classify(204, null)).toBe("done");
    expect(classify(409, { detail: "x" })).toBe("conflict");
    expect(classify(401, null)).toBe("auth");
    expect(classify(403, { detail: { code: "network_not_allowed", ip: "1.2.3.4" } })).toBe("held");
    expect(classify(403, { code: "network_not_allowed" })).toBe("held");
    expect(classify(403, { detail: "You can't do that" })).toBe("drop");
    expect(classify(422, null)).toBe("drop");
    expect(classify(500, null)).toBe("retry");
    expect(classify(503, null)).toBe("retry");
  });
  it("recognises the shop-network refusal only by its code", () => {
    expect(isNetworkBlocked({ detail: { code: "network_not_allowed" } })).toBe(true);
    expect(isNetworkBlocked({ detail: "network_not_allowed" })).toBe(false);
    expect(isNetworkBlocked(null)).toBe(false);
  });
});

describe("offline queue", () => {
  it("queues encrypted while offline and replays in order once online", async () => {
    const { store, state, fetch, q } = setup();
    expect(await q.mutate("PATCH", "/api/orders/1/status", { status: "ready" })).toEqual({ ok: true, queued: true });
    await q.mutate("PATCH", "/api/orders/2/status", { status: "done" });
    expect(fetch).not.toHaveBeenCalled();
    expect(store.rows).toHaveLength(2);
    // stored encrypted: the plaintext never sits in IndexedDB
    expect(JSON.stringify(store.rows)).not.toContain("ready");

    state.online = true;
    fetch.mockResolvedValue(json(200, { ok: true }));
    const r = await q.flush();
    expect(r.done).toBe(2);
    expect(store.rows).toHaveLength(0);
    expect(fetch.mock.calls.map((c) => c[0])).toEqual(["/api/orders/1/status", "/api/orders/2/status"]);
    const init = fetch.mock.calls[0][1]!;
    expect(init.method).toBe("PATCH");
    expect(JSON.parse(init.body as string)).toEqual({ status: "ready" });
    expect((init.headers as Record<string, string>).Authorization).toBe("Bearer tok-1");
  });

  it("keeps a change the shop network refused (403 network_not_allowed) and stops", async () => {
    const { store, state, fetch, q } = setup();
    await q.mutate("PATCH", "/api/orders/1/status", { status: "ready" });
    await q.mutate("PATCH", "/api/orders/2/status", { status: "done" });
    state.online = true;
    fetch.mockResolvedValue(json(403, { detail: { code: "network_not_allowed", ip: "10.0.0.9" } }));
    const r = await q.flush();
    expect(r.held).toBe(true);
    expect(r.dropped).toBe(0);
    expect(store.rows).toHaveLength(2);            // nothing lost
    expect(fetch).toHaveBeenCalledTimes(1);        // stopped at the first refusal
    expect((await q.status()).held).toBe(true);

    // back on shop Wi-Fi: the same items go through
    fetch.mockResolvedValue(json(200, {}));
    expect((await q.flush()).done).toBe(2);
    expect(store.rows).toHaveLength(0);
    expect((await q.status()).held).toBe(false);
  });

  it("queues an online write the shop network refuses instead of failing it", async () => {
    const { store, fetch, q } = setup({ online: true });
    fetch.mockResolvedValue(json(403, { detail: { code: "network_not_allowed" } }));
    expect(await q.mutate("PATCH", "/api/orders/1/status", { status: "ready" })).toEqual({ ok: true, queued: true, held: true });
    expect(store.rows).toHaveLength(1);
  });

  it("drops other 4xx, records 409 as a conflict, keeps 5xx", async () => {
    const { store, state, fetch, q } = setup();
    await q.mutate("PATCH", "/api/orders/1/status", { status: "a" });   // 422 → dropped
    await q.mutate("PATCH", "/api/orders/2/status", { status: "b" });   // 409 → conflict
    await q.mutate("PATCH", "/api/orders/3/status", { status: "c" });   // 500 → kept
    state.online = true;
    fetch
      .mockResolvedValueOnce(json(422, { detail: "bad" }))
      .mockResolvedValueOnce(json(409, { detail: "'Ready' requires a comment on the order first." }))
      .mockResolvedValueOnce(json(500, {}));
    const r = await q.flush();
    expect(r).toMatchObject({ dropped: 1, conflicts: 1, failed: 1, done: 0 });
    expect(store.rows).toHaveLength(1);
    const conflicts = await q.conflicts();
    expect(conflicts).toHaveLength(1);
    expect(conflicts[0]).toMatchObject({ url: "/api/orders/2/status", detail: "'Ready' requires a comment on the order first." });

    // re-apply the conflict: it is queued again and replayed
    fetch.mockResolvedValue(json(200, {}));
    await q.resolveConflict(conflicts[0].id, "retry");
    expect(await q.conflicts()).toHaveLength(0);
    expect(store.rows).toHaveLength(0);
  });

  it("stops on 401 without dropping anything, and on a network error mid-flush", async () => {
    const { store, state, fetch, q } = setup();
    await q.mutate("POST", "/api/a", { n: 1 });
    await q.mutate("POST", "/api/b", { n: 2 });
    state.online = true;
    fetch.mockResolvedValueOnce(json(401, {}));
    await q.flush();
    expect(store.rows).toHaveLength(2);
    expect(fetch).toHaveBeenCalledTimes(1);

    fetch.mockRejectedValueOnce(new TypeError("Failed to fetch"));
    await q.flush();
    expect(store.rows).toHaveLength(2);
  });

  it("goes straight to the server when online, and queues on a transient 5xx", async () => {
    const { store, fetch, q } = setup({ online: true });
    fetch.mockResolvedValueOnce(json(200, { status: "ready" }));
    expect(await q.mutate("PATCH", "/api/orders/1/status", { status: "ready" })).toEqual({ ok: true, queued: false, data: { status: "ready" } });
    fetch.mockResolvedValueOnce(json(409, { detail: "gate" }));
    expect(await q.mutate("PATCH", "/api/orders/1/status", { status: "done" })).toMatchObject({ ok: false, status: 409 });
    fetch.mockResolvedValueOnce(json(502, {}));
    expect(await q.mutate("PATCH", "/api/orders/1/status", { status: "done" })).toEqual({ ok: true, queued: true });
    expect(store.rows).toHaveLength(1);
  });

  it("tells subscribers about changes", async () => {
    const { q } = setup();
    const seen: number[] = [];
    const off = q.subscribe((s) => seen.push(s.pending));
    await q.mutate("POST", "/api/a", {});
    off();
    await q.mutate("POST", "/api/b", {});
    expect(seen).toEqual([1]);
  });
});
