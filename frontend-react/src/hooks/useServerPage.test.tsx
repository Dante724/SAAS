// Server paging: one page per request (skip/limit), filters go to the server, new filters
// return to page 1, and nothing ever asks for the old limit=1000.
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { act, renderHook, waitFor } from "@testing-library/react";
import { useServerPage } from "./useServerPage";

let urls: string[] = [];
const reply = (body: unknown) => ({ ok: true, status: 200, headers: new Headers(), json: async () => body, text: async () => JSON.stringify(body) }) as Response;

beforeEach(() => {
  urls = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    urls.push(url);
    return reply({ items: [{ id: "a" }, { id: "b" }], total: 120 });
  }));
});
afterEach(() => vi.unstubAllGlobals());

describe("useServerPage", () => {
  it("fetches one page and the server total", async () => {
    const { result } = renderHook(() => useServerPage<{ id: string }>("/clients", { search: "zed" }, { pageSize: 50 }));
    await waitFor(() => expect(result.current.rows).toHaveLength(2));
    expect(result.current.total).toBe(120);
    const u = new URL(urls[0], "http://x");
    expect(u.pathname).toBe("/api/clients");
    expect(u.searchParams.get("search")).toBe("zed");
    expect(u.searchParams.get("skip")).toBe("0");
    expect(u.searchParams.get("limit")).toBe("50");
  });

  it("pages by skip and resets to page 1 when filters change", async () => {
    const { result, rerender } = renderHook(({ s }) => useServerPage<{ id: string }>("/assets", { search: s }, { pageSize: 50 }),
      { initialProps: { s: "" } });
    await waitFor(() => expect(result.current.rows).not.toBeNull());
    act(() => result.current.setPage(3));
    await waitFor(() => expect(urls.some((x) => x.includes("skip=100"))).toBe(true));
    rerender({ s: "apple" });
    await waitFor(() => expect(result.current.page).toBe(1));
    await waitFor(() => expect(urls.at(-1)).toContain("search=apple"));
    expect(urls.at(-1)).toContain("skip=0");
    expect(urls.every((x) => !x.includes("limit=1000"))).toBe(true);
  });

  it("waits while disabled", async () => {
    renderHook(() => useServerPage("/cash/transactions", {}, { enabled: false }));
    await new Promise((r) => setTimeout(r, 0));
    expect(urls).toHaveLength(0);
  });
});
