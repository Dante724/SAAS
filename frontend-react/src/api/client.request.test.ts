// Vitest: the request layer's network behaviour (council M13). A dead wifi used to surface as
// the browser's raw "Failed to fetch" and a request could hang forever; now every call carries
// a 25 s timeout and a network or timeout failure reads as one plain sentence.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api, ApiError, NO_CONNECTION, REQUEST_TIMEOUT_MS } from "./client";

const okJson = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

// A fetch that never answers, and rejects the way a browser does once its signal aborts.
const hangUntilAborted = (_url: string, init?: RequestInit) => new Promise<Response>((_res, rej) => {
  const s = init?.signal;
  s?.addEventListener("abort", () => rej(s.reason instanceof Error ? s.reason : new DOMException("aborted", "AbortError")));
});

describe("request()", () => {
  let fetchMock: ReturnType<typeof vi.fn>;
  beforeEach(() => { fetchMock = vi.fn(); vi.stubGlobal("fetch", fetchMock); });
  afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

  it("sends every request with an abort signal and a 25 s budget", async () => {
    fetchMock.mockResolvedValue(okJson({ ok: true }));
    await api.get("/orders");
    const init = fetchMock.mock.calls[0][1] as RequestInit;
    expect(init.signal).toBeInstanceOf(AbortSignal);
    expect(REQUEST_TIMEOUT_MS).toBe(25_000);
  });

  it("gives up after 25 s with the no-connection sentence instead of hanging", async () => {
    vi.useFakeTimers();
    fetchMock.mockImplementation(hangUntilAborted);
    const p = api.post("/orders", { client_id: "c1" }).catch((e) => e);
    await vi.advanceTimersByTimeAsync(REQUEST_TIMEOUT_MS - 1);
    expect((fetchMock.mock.calls[0][1] as RequestInit).signal!.aborted).toBe(false);
    await vi.advanceTimersByTimeAsync(1);
    const err = await p;
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(0);
    expect(err.message).toBe(NO_CONNECTION);
  });

  it("maps a network failure (TypeError) to a plain no-connection ApiError", async () => {
    fetchMock.mockRejectedValue(new TypeError("Failed to fetch"));
    const err = await api.post("/orders/1/lines", { qty: 1 }).catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(0);
    expect(err.message).toBe(NO_CONNECTION);
    expect(err.message).toMatch(/^No connection\..*check your internet.*try again/i);
    expect(err.message).not.toMatch(/Failed to fetch/);
  });

  it("maps a timeout (TimeoutError / AbortError from the timer) to the same sentence", async () => {
    for (const name of ["TimeoutError", "AbortError"]) {
      fetchMock.mockRejectedValueOnce(new DOMException("signal timed out", name));
      const err = await api.get("/orders").catch((e) => e);
      expect(err).toBeInstanceOf(ApiError);
      expect(err.message).toBe(NO_CONNECTION);
    }
  });

  it("a caller's own abort still cancels the request and is not reported as a lost connection", async () => {
    fetchMock.mockImplementation(hangUntilAborted);
    const ctl = new AbortController();
    const p = api.get("/orders", { signal: ctl.signal }).catch((e) => e);
    ctl.abort();
    const err = await p;
    expect(err).not.toBeInstanceOf(ApiError);
    expect(err.name).toBe("AbortError");
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
