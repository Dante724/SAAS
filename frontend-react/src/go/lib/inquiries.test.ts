import { describe, expect, it, vi } from "vitest";
import { webcrypto } from "node:crypto";
import { createQueue, type QueueStore, type Row } from "./offlineQueue";
import { goPath } from "./push";
import {
  callsText, convertInquiry, createInquiry, emptyDraft, listPath, loadPage, replyBy, setInquiryStatus, sortInquiries, telHref,
  type Inquiry,
} from "./inquiries";

function memStore(): QueueStore & { rows: Row[] } {
  let next = 1;
  const rows: Row[] = [];
  const meta = new Map<string, unknown>();
  return {
    rows,
    async add(payload, at) { rows.push({ id: next++, payload, enqueued_at: at }); },
    async all() { return [...rows]; },
    async remove(id) { const i = rows.findIndex((r) => r.id === id); if (i >= 0) rows.splice(i, 1); },
    async getMeta<T>(k: string) { return meta.get(k) as T | undefined; },
    async putMeta(k, v) { meta.set(k, v); },
  };
}
const json = (status: number, body: unknown) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
const lead = (over: Partial<Inquiry>): Inquiry => ({ id: "l1", name: "Maria", status: "new", ...over });

describe("inquiries list", () => {
  it("asks for open inquiries, paged, with the search", () => {
    expect(listPath("", 1)).toBe("/leads?open_only=true&page=1&page_size=25");
    expect(listPath("  0412 ", 2)).toBe("/leads?open_only=true&page=2&page_size=25&q=0412");
  });

  it("appends the next page without repeating a row and knows when there's more", async () => {
    const get = vi.fn()
      .mockResolvedValueOnce({ items: [lead({ id: "a" }), lead({ id: "b" })], total: 3 })
      .mockResolvedValueOnce({ items: [lead({ id: "b" }), lead({ id: "c" })], total: 3 });
    const p1 = await loadPage(get as any, "", 1);
    expect(p1.items.map((i) => i.id)).toEqual(["a", "b"]);
    expect(p1.more).toBe(true);
    const p2 = await loadPage(get as any, "", 2, p1.items);
    expect(p2.items.map((i) => i.id)).toEqual(["a", "b", "c"]);
    expect(p2.more).toBe(false);
  });

  it("puts late ones first, then the soonest reply-by", () => {
    const list = [
      lead({ id: "later", sla_due_at: "2026-10-25T15:00:00" }),
      lead({ id: "late", sla_due_at: "2026-10-25T09:00:00", sla_overdue: true }),
      lead({ id: "none" }),
      lead({ id: "soon", sla_due_at: "2026-10-25T11:00:00" }),
    ];
    expect(sortInquiries(list).map((i) => i.id)).toEqual(["late", "soon", "later", "none"]);
  });

  it("reads reply-by times as UTC and says it plainly", () => {
    const now = new Date("2026-10-25T10:00:00Z");
    expect(replyBy(lead({ sla_due_at: "2026-10-25T10:20:00" }), now)).toEqual({ text: "Reply in 20 min", late: false });
    expect(replyBy(lead({ sla_due_at: "2026-10-25T09:15:00", sla_overdue: true }), now)).toEqual({ text: "Late 45 min", late: true });
    expect(replyBy(lead({ sla_due_at: "2026-10-25T07:00:00" }), now)?.text).toBe("Late 3 h");
    expect(replyBy(lead({ status: "won", sla_due_at: "2026-10-25T07:00:00" }), now)).toBeNull();
    expect(callsText(3)).toBe("3 missed calls");
    expect(callsText(1)).toBe("1 missed call");
    expect(callsText(0)).toBe("");
    expect(telHref("+44 (0)7700 900-123")).toBe("tel:+4407700900123");
  });

  it("opens missed-call alerts on the inquiry in Tekly Go", () => {
    expect(goPath("/inquiries?lead=abc")).toBe("/go/inquiries?lead=abc");
    expect(goPath("https://shop.tekly.app/inquiries?x=1&lead=abc")).toBe("/go/inquiries?lead=abc");
    expect(goPath("/inquiries")).toBe("/go/inquiries");
    expect(goPath("/leadsboard")).toBe("/leadsboard");
  });
});

describe("new inquiry offline", () => {
  it("keeps one idempotency key across the first try and every replay", async () => {
    const store = memStore();
    const state = { online: true };
    const fetch = vi.fn<(url: string, init?: RequestInit) => Promise<Response>>()
      .mockRejectedValueOnce(new TypeError("Failed to fetch"))   // first try: the network drops
      .mockResolvedValueOnce(json(503, {}))                       // replay 1: server busy, kept
      .mockResolvedValueOnce(json(201, { id: "l9" }))             // replay 2: created
      .mockResolvedValueOnce(json(200, { id: "l9" }));            // a stray second replay: same inquiry
    const q = createQueue({ store, fetch: fetch as unknown as typeof globalThis.fetch, token: () => "t", online: () => state.online,
      subtle: webcrypto.subtle as unknown as SubtleCrypto });
    const d = { ...emptyDraft(), name: " Maria ", phone: "0412 345 678", brand: "Apple", model: "iPhone 14", inquiry: "Screen price" };

    expect(await createInquiry(q.mutate, d, "key-1")).toEqual({ ok: true, queued: true });
    expect(store.rows).toHaveLength(1);
    expect((await q.flush()).failed).toBe(1);
    expect(store.rows).toHaveLength(1);
    expect((await q.flush()).done).toBe(1);
    expect(store.rows).toHaveLength(0);

    const bodies = fetch.mock.calls.map(([, init]) => JSON.parse(String(init!.body)));
    expect(new Set(bodies.map((b) => b.idempotency_key))).toEqual(new Set(["key-1"]));
    expect(bodies[0]).toMatchObject({ name: "Maria", phone: "0412 345 678", device_brand: "Apple", device_model: "iPhone 14",
      serial: null, inquiry: "Screen price", lead_type_id: null });
    expect(fetch.mock.calls.every(([u, init]) => u === "/api/leads" && init!.method === "POST")).toBe(true);
  });

  it("start and mark lost go through the queue too", async () => {
    const mutate = vi.fn().mockResolvedValue({ ok: true, queued: true });
    await setInquiryStatus(mutate, "l 1", "lost");
    expect(mutate).toHaveBeenCalledWith("PATCH", "/api/leads/l%201", { status: "lost" });
  });
});

describe("convert", () => {
  it("makes a work order and goes to the new job", async () => {
    const post = vi.fn().mockResolvedValue({ order_id: "o-7", order_number: "WO-0042", device_id: "d1" });
    expect(await convertInquiry(post as any, "l1")).toEqual({ orderId: "o-7", number: "WO-0042", path: "/job/o-7" });
    expect(post).toHaveBeenCalledWith("/leads/l1/convert?is_estimate=false");
  });
  it("passes the server's refusal up so the screen can say why", async () => {
    const post = vi.fn().mockRejectedValue(new Error("You can't create jobs"));
    await expect(convertInquiry(post as any, "l1")).rejects.toThrow("You can't create jobs");
  });
});
