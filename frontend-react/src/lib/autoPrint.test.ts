import { describe, it, expect, vi, afterEach } from "vitest";
import { autoPrintForJob, autoPrintForSale, autoPrintIfClosed } from "./autoPrint";
import { detectBrowser } from "../components/PrintSetupCard";
import { autoPrintChoices, toggleEvent } from "../pages/settings/TemplateEditorDrawer";

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

describe("autoPrintForSale", () => {
  it("prints what's switched on for a sale", async () => {
    const fetchMock = vi.fn(async () => reply({ docs: [{ name: "Sales receipt", html: "<p>R</p>" }] }));
    vi.stubGlobal("fetch", fetchMock);
    const printed: string[] = [];
    expect(await autoPrintForSale("s1", (h) => printed.push(h))).toBe(1);
    expect(printed).toEqual(["<p>R</p>"]);
    expect((fetchMock.mock.calls[0] as unknown as [string])[0]).toBe("/api/documents/auto-print-sale/s1");
  });
});

describe("autoPrintIfClosed", () => {
  it("prints the closing documents only when the status change closed the job", async () => {
    const fetchMock = vi.fn(async () => reply({ docs: [{ name: "Invoice", html: "<p>I</p>" }] }));
    vi.stubGlobal("fetch", fetchMock);
    const print = vi.fn();
    expect(await autoPrintIfClosed("o1", { is_closing: false }, print)).toBe(0);
    expect(fetchMock).not.toHaveBeenCalled();
    expect(await autoPrintIfClosed("o1", { is_closing: true }, print)).toBe(1);
    expect((fetchMock.mock.calls[0] as unknown as [string])[0]).toBe("/api/documents/auto-print/o1?event=close");
  });
});

describe("template editor auto-print choices", () => {
  it("offers sale only to receipts and other documents, and nothing to credit notes", () => {
    expect(autoPrintChoices("receipt").map(([e]) => e)).toEqual(["create", "close", "sale"]);
    expect(autoPrintChoices("label").map(([e]) => e)).toEqual(["create", "close"]);
    expect(autoPrintChoices("credit_note")).toEqual([]);
  });
  it("toggles one moment, keeping a fixed order", () => {
    expect(toggleEvent("", "close")).toBe("close");
    expect(toggleEvent("close", "create")).toBe("create,close");
    expect(toggleEvent("create,close", "create")).toBe("close");
  });
});

describe("detectBrowser", () => {
  it("tells the browsers apart for the one-click printing steps", () => {
    expect(detectBrowser("Mozilla/5.0 (Windows NT 10.0) Gecko/20100101 Firefox/131.0")).toBe("firefox");
    expect(detectBrowser("Mozilla/5.0 AppleWebKit/537.36 Chrome/130.0 Safari/537.36 Edg/130.0")).toBe("edge");
    expect(detectBrowser("Mozilla/5.0 AppleWebKit/537.36 Chrome/130.0 Safari/537.36")).toBe("chrome");
    expect(detectBrowser("Mozilla/5.0 (Macintosh) AppleWebKit/605.1.15 Version/18.0 Safari/605.1.15")).toBe("safari");
  });
});
