import { describe, it, expect, vi, afterEach } from "vitest";
import { autoPrintAfterCreate } from "./autoPrint";

function serve(body: unknown, ok = true) {
  vi.stubGlobal("fetch", vi.fn(async () => ({ ok, status: ok ? 200 : 500, statusText: "", headers: new Headers(),
    json: async () => body, text: async () => JSON.stringify(body) }) as Response));
}
afterEach(() => vi.unstubAllGlobals());

describe("autoPrintAfterCreate", () => {
  it("prints each label template set to print on create", async () => {
    serve({ items: [{ template_id: "t1", name: "Device label", count: 1, html: "<b>SN1</b>" }, { template_id: "t2", name: "Empty", count: 0, html: "" }] });
    const print = vi.fn();
    expect(await autoPrintAfterCreate("o1", print)).toBe(1);
    expect(print).toHaveBeenCalledWith("<b>SN1</b>");
    expect((fetch as any).mock.calls[0][0]).toBe("/api/auto-print/order-created/o1");
  });
  it("does nothing, quietly, when nothing is set up or the server fails", async () => {
    const print = vi.fn();
    serve({ items: [] });
    expect(await autoPrintAfterCreate("o1", print)).toBe(0);
    serve({ detail: "boom" }, false);
    expect(await autoPrintAfterCreate("o1", print)).toBe(0);
    expect(print).not.toHaveBeenCalled();
  });
});
