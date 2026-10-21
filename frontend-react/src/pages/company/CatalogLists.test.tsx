// A2 — the services list quietly marks services hidden from online booking.
// A3 — the products list shows MPN and finds products by it; the product drawer edits it.
import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

vi.mock("../../components/Shell", () => ({ M: ({ n }: { n: string }) => <i data-icon={n} />, default: ({ children }: any) => <div>{children}</div> }));

import { Services, Products } from "./CompanyHub";
import InventoryDrawer from "../inventory/InventoryDrawer";

const reply = (body: unknown, status = 200) => ({ ok: status < 400, status, headers: new Headers(), json: async () => body, text: async () => JSON.stringify(body) }) as Response;
let calls: { method: string; url: string; body?: any }[] = [];
function stub(routes: Record<string, unknown | number>) {
  calls = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ method: init?.method || "GET", url, body: init?.body ? JSON.parse(String(init.body)) : undefined });
    const hit = Object.keys(routes).find((k) => url.startsWith(k));
    const v = hit ? routes[hit] : { items: [], tree: [] };
    return typeof v === "number" ? reply({}, v) : reply(v);
  }));
}
afterEach(() => vi.unstubAllGlobals());

describe("Services list", () => {
  it("marks only the services hidden from online booking, quietly", async () => {
    stub({ "/api/catalog/services": { items: [
      { id: "a", name: "Diagnostics", price: 20, bookable: true },
      { id: "b", name: "Internal rework", price: 0, bookable: false },
      { id: "c", name: "Legacy clean", price: 5 },                       // unset means shown
    ] } });
    render(<Services onOpen={() => {}} />);
    const shown = (await screen.findByText("Diagnostics")).closest("tr")!;
    expect(within(shown).queryByText("Not bookable online")).toBeNull();
    expect(within(screen.getByText("Legacy clean").closest("tr")!).queryByText("Not bookable online")).toBeNull();
    const hidden = screen.getByText("Internal rework").closest("tr")!;
    expect(within(hidden).getByText("Not bookable online")).toBeInTheDocument();
    expect(screen.queryByText("Online booking")).toBeNull();                // no pill on every row
    expect(screen.getByRole("columnheader", { name: "Price" })).toBeInTheDocument();
  });

  it("the whole row opens the service, and the row menu holds Edit and Duplicate", async () => {
    const u = userEvent.setup();
    const onOpen = vi.fn();
    stub({ "/api/catalog/services": { items: [{ id: "a", name: "Diagnostics", price: 20, bookable: true }] } });
    render(<Services onOpen={onOpen} />);
    await u.click(await screen.findByText("Diagnostics"));
    expect(onOpen).toHaveBeenLastCalledWith(expect.objectContaining({ id: "a" }));
    onOpen.mockClear();
    await u.click(screen.getByRole("button", { name: "More actions for Diagnostics" }));
    expect(onOpen).not.toHaveBeenCalled();                                   // the menu doesn't open the row
    await u.click(screen.getByRole("menuitem", { name: "Duplicate" }));
    expect(onOpen).toHaveBeenCalledWith(expect.objectContaining({ id: undefined, name: "Diagnostics (copy)" }));
    expect(screen.queryByRole("menu")).toBeNull();
  });

  it("shows an error with a retry instead of an empty list", async () => {
    stub({ "/api/catalog/services": 500 });
    render(<Services onOpen={() => {}} />);
    expect(await screen.findByText(/Couldn't load services/)).toBeInTheDocument();
    expect(screen.queryByText("No services yet.")).toBeNull();
  });
});

describe("Products list", () => {
  it("has an MPN column and finds products by MPN", async () => {
    const u = userEvent.setup();
    stub({ "/api/catalog/products": { items: [
      { id: "p1", name: "iPhone 13 screen", sku: "SCR-13", mpn: "661-12345", sell_price: 90 },
      { id: "p2", name: "Battery", sku: "BAT-1", mpn: null, sell_price: 30 },
    ] } });
    render(<Products onOpen={() => {}} />);
    expect(await screen.findByRole("columnheader", { name: "MPN" })).toBeInTheDocument();
    expect(screen.getByText("661-12345")).toBeInTheDocument();
    await u.type(screen.getByPlaceholderText("Search products, SKU, MPN…"), "661-123");
    expect(screen.getByText("iPhone 13 screen")).toBeInTheDocument();
    expect(screen.queryByText("Battery")).toBeNull();
  });

  it("shows an error with a retry instead of an empty list", async () => {
    stub({ "/api/catalog/products": 500 });
    render(<Products onOpen={() => {}} />);
    expect(await screen.findByText(/Couldn't load products/)).toBeInTheDocument();
  });
});

describe("Product drawer MPN", () => {
  it("edits and saves the MPN with the barcode type kept", async () => {
    const u = userEvent.setup();
    stub({ "/api/forms/layout": 500, "/api/catalog/products/p1": { product: { id: "p1", name: "Screen", sku: "S", mpn: "OLD-1", barcode: "X1", barcode_type: "qr", sell_price: 9 } } });
    render(<InventoryDrawer kind="product" id="p1" onClose={() => {}} />);
    const mpn = await screen.findByLabelText(/^MPN/);
    await vi.waitFor(() => expect(mpn).toHaveValue("OLD-1"));
    await u.clear(mpn); await u.type(mpn, " NEW-2 ");
    await u.click(screen.getByRole("button", { name: "Save changes" }));
    await vi.waitFor(() => expect(calls.some((c) => c.method === "PUT")).toBe(true));
    const put = calls.find((c) => c.method === "PUT")!;
    expect(put.url).toBe("/api/catalog/products/p1");
    expect(put.body).toMatchObject({ mpn: "NEW-2", barcode_type: "qr", barcode: "X1" });
  });
});
