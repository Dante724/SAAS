// Vitest: the request layer's network behaviour (council M13). A dead wifi used to surface as
// the browser's raw "Failed to fetch" and a request could hang forever; now every call carries
// a 25 s timeout and a network or timeout failure reads as one plain sentence.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api, ApiError, NO_CONNECTION, REQUEST_TIMEOUT_MS } from "./client";

const okJson = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

describe("request()", () => {
  let fetchMock: ReturnType<typeof vi.fn>;
  beforeEach(() => { fetchMock = vi.fn(); vi.stubGlobal("fetch", fetchMock); });
  afterEach(() => { vi.unstubAllGlobals(); });

  it("sends every request with a 25 s timeout signal", async () => {
    fetchMock.mockResolvedValue(okJson({ ok: true }));
    await api.get("/orders");
    const init = fetchMock.mock.calls[0][1] as RequestInit;
    expect(init.signal).toBeInstanceOf(AbortSignal);
    expect(REQUEST_TIMEOUT_MS).toBe(25_000);
  });

  it("maps a network failure (TypeError) to a plain no-connection ApiError", async () => {
    fetchMock.mockRejectedValue(new TypeError("Failed to fetch"));
    const err = await api.post("/orders/1/lines", { qty: 1 }).catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(0);
    expect(err.message).toBe(NO_CONNECTION);
    expect(err.message).toContain("check the wifi");
  });

  it("maps a timeout (TimeoutError / AbortError) to the same sentence", async () => {
    for (const name of ["TimeoutError", "AbortError"]) {
      fetchMock.mockRejectedValueOnce(new DOMException("signal timed out", name));
      const err = await api.get("/orders").catch((e) => e);
      expect(err).toBeInstanceOf(ApiError);
      expect(err.message).toBe(NO_CONNECTION);
    }
  });

  it("leaves a server error alone: status and the server's sentence", async () => {
    fetchMock.mockResolvedValue(okJson({ detail: "Order not found" }, 404));
    const err = await api.get("/orders/x").catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(404);
    expect(err.message).toBe("Order not found");
  });

  it("postOnce sends the Idempotency-Key header", async () => {
    fetchMock.mockResolvedValue(okJson({ ok: true, line_id: "l1" }));
    await api.postOnce("/orders/1/lines", { qty: 1 }, "web-abc");
    const init = fetchMock.mock.calls[0][1] as RequestInit;
    expect((init.headers as Record<string, string>)["Idempotency-Key"]).toBe("web-abc");
  });
});
