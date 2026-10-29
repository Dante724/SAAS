import { describe, it, expect, vi, afterEach } from "vitest";
import { autoPrintForJob } from "./autoPrint";

const reply = (body: unknown, ok = true) => ({ ok, status: ok ? 200 : 500, headers: new Headers(), json: async () => body, text: async () => JSON.stringify(body) }) as Response;
afterEach(() => vi.unstubAllGlobals());

describe("autoPrintForJob", () => {
  it("prints every document the server returns for the new job", async () => {
    const fetchMock = vi.fn(async () => reply({ docs: [{ name: "Asset label", html: "<p>A</p>" }, { name: "Receipt", html: "<p>B</p>" }] }));
    vi.stubGlobal("fetch", fetchMock);
    const printed: string[] = [];
    expect(await autoPrintForJob("o1", "create", (h) => printed.push(h))).toBe(2);
    expect(printed).toEqual(["<p>A</p>", "<p>B</p>"]);
    expect((fetchMock.mock.calls[0] as unknown as [string])[0]).toBe("/api/documents/auto-print/o1?event=create");
  });

  it("prints nothing when no template is set to auto-print", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => reply({ docs: [] })));
    const print = vi.fn();
    expect(await autoPrintForJob("o1", "create", print)).toBe(0);
    expect(print).not.toHaveBeenCalled();
  });

  it("never throws when the server fails", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => reply({ detail: "boom" }, false)));
    expect(await autoPrintForJob("o1", "create", vi.fn())).toBe(0);
  });
});
