// Vitest: WO-03 header edits carry If-Match, and the job's version follows our own saves
// (re-review N6), while someone else's save is refused with a clear message.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../../api/client";
import {
  STALE_ORDER_MESSAGE, forgetOrderVersion, isStaleOrder, orderVersion, patchOrderHeader, rememberOrderVersion,
} from "./orderVersion";

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

describe("patchOrderHeader", () => {
  let fetchMock: ReturnType<typeof vi.fn>;
  beforeEach(() => { fetchMock = vi.fn(); vi.stubGlobal("fetch", fetchMock); forgetOrderVersion("o1"); });
  afterEach(() => { vi.unstubAllGlobals(); });

  const sentHeaders = (n: number) => (fetchMock.mock.calls[n][1] as RequestInit).headers as Record<string, string>;

  it("sends the version it last saw and keeps the one the save returns", async () => {
    rememberOrderVersion("o1", "2026-11-13T10:00:00.000001");
    fetchMock.mockResolvedValueOnce(json({ ok: true, updated_at: "2026-11-13T10:00:05.000002" }));
    await patchOrderHeader("o1", "/orders/o1/notes", { tech_notes: "a" });
    expect(sentHeaders(0)["If-Match"]).toBe("2026-11-13T10:00:00.000001");
    expect(orderVersion("o1")).toBe("2026-11-13T10:00:05.000002");

    // the next edit sends the new version, not the stale one
    fetchMock.mockResolvedValueOnce(json({ ok: true, updated_at: "2026-11-13T10:00:09.000003" }));
    await patchOrderHeader("o1", "/orders/o1/priority", { priority: 2 });
    expect(sentHeaders(1)["If-Match"]).toBe("2026-11-13T10:00:05.000002");
  });

  it("a line edit or status reply moves the version on, so our own next header save isn't refused", async () => {
    rememberOrderVersion("o1", "v1");
    rememberOrderVersion("o1", "v2");                 // e.g. the reply to adding a line
    rememberOrderVersion("o1", undefined);            // a reply without a version changes nothing
    fetchMock.mockResolvedValueOnce(json({ ok: true, updated_at: "v3" }));
    await patchOrderHeader("o1", "/orders/o1/schedule", { scheduled_for: null });
    expect(sentHeaders(0)["If-Match"]).toBe("v2");
  });

  it("without a known version it sends no If-Match (the server then doesn't check)", async () => {
    fetchMock.mockResolvedValueOnce(json({ ok: true, updated_at: "v9" }));
    await patchOrderHeader("o1", "/orders/o1/notes", {});
    expect(sentHeaders(0)["If-Match"]).toBeUndefined();
    expect(orderVersion("o1")).toBe("v9");
  });

  it("turns 409 stale_order into a clear message and forgets the old version", async () => {
    rememberOrderVersion("o1", "old");
    fetchMock.mockResolvedValueOnce(json({ detail: { code: "stale_order", message: "Someone else saved this job",
                                                     updated_at: "newer" } }, 409));
    const err = await patchOrderHeader("o1", "/orders/o1/notes", { tech_notes: "x" }).catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(isStaleOrder(err)).toBe(true);
    expect(err.message).toBe(STALE_ORDER_MESSAGE);
    expect(orderVersion("o1")).toBeUndefined();
  });

  it("other errors pass through unchanged", async () => {
    fetchMock.mockResolvedValueOnce(json({ detail: "This job is closed" }, 409));
    const err = await patchOrderHeader("o1", "/orders/o1/notes", {}).catch((e) => e);
    expect(isStaleOrder(err)).toBe(false);
    expect(err.message).toBe("This job is closed");
  });
});
