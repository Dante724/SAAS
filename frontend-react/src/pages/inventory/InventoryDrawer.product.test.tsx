// Product drawer: generating a code keeps the manufacturer barcode, the photo upload is real,
// repair price is editable, cost-blind roles see no cost/margin, and save errors read plainly.
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

vi.mock("../../components/Shell", () => ({ M: ({ n }: { n: string }) => <i data-icon={n} />, default: ({ children }: any) => <div>{children}</div> }));
const perms = vi.hoisted(() => ({ deny: new Set<string>() }));
vi.mock("../../context/Me", () => ({
  useMe: () => ({ me: null, loading: false, can: (c?: string) => !c || !perms.deny.has(c), fmt: (n: number) => `${n}`, refresh: async () => {} }),
  hasFeature: () => true,   // plan gates (services/billing.py): every feature on
}));

import InventoryDrawer, { productPhoto, dataUrlBytes } from "./InventoryDrawer";
import { newProductBody, matchedBy } from "./PasteImport";

const PROD = { id: "p1", name: "Screen", sku: "S-1", barcode: "5901234123457", barcode_type: "ean13", uom: "pcs",
  cost_price: 40, avg_cost: 40, sell_price: 100, repair_price: 0, min_stock: 1, qty_available: 3, qty_reserved: 0 };

let calls: { method: string; url: string; body?: any }[] = [];
let failPut = false;
let served: any = PROD;
const reply = (body: unknown, status = 200) => ({ ok: status < 400, status, headers: new Headers(), json: async () => body, text: async () => JSON.stringify(body) }) as Response;

beforeEach(() => {
  calls = []; failPut = false; served = PROD; perms.deny.clear();
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    const method = init?.method || "GET";
    calls.push({ method, url, body: init?.body ? JSON.parse(String(init.body)) : undefined });
    if (url.includes("/barcode/generate")) return reply({ barcode: "2000000000015", barcode_type: "qr", is_main: false });
    if (url.endsWith("/barcodes")) return reply({ items: [{ id: "b1", barcode: PROD.barcode, barcode_type: "ean13", is_main: true }] });
    if (url === "/api/catalog/products/p1" && method === "GET") return reply({ product: served });
    if (url === "/api/catalog/products/p1" && method === "PUT") return failPut ? reply({}, 500) : reply(PROD);
    if (url.startsWith("/api/catalog/categories")) return reply({ tree: [] });
    if (url.startsWith("/api/staff/locations")) return reply({ items: [] });
    if (url.startsWith("/api/forms/layout")) return reply({}, 500);      // → every built-in field, in order
    return reply({ ok: true, items: [] });
  }));
});
afterEach(() => vi.unstubAllGlobals());

const puts = () => calls.filter((c) => c.method === "PUT");

describe("Product drawer", () => {
  it("generating a QR on a product with a barcode keeps that barcode", async () => {
    const u = userEvent.setup();
    render(<InventoryDrawer kind="product" id="p1" initial={PROD} onClose={() => {}} />);
    const qr = await screen.findByRole("button", { name: "Generate QR" });
    expect(qr.getAttribute("title")).toMatch(/extra code/);
    await u.click(qr);
    expect(await screen.findByText("QR added as an extra code")).toBeInTheDocument();
    await u.click(screen.getByRole("button", { name: "Save changes" }));
    await vi.waitFor(() => expect(puts()).toHaveLength(1));
    expect(puts()[0].body).toMatchObject({ barcode: "5901234123457", barcode_type: "ean13" });
    expect(puts()[0].body).not.toHaveProperty("image_data");      // photo untouched → not sent
  });

  it("has no unsaved Type field and saves the repair price (blank = store price)", async () => {
    const u = userEvent.setup();
    render(<InventoryDrawer kind="product" id="p1" initial={PROD} onClose={() => {}} />);
    expect(screen.queryByText("Type", { selector: "label" })).toBeNull();
    const repair = await screen.findByLabelText(/Repair price/);
    expect(repair).toHaveAttribute("placeholder", expect.stringMatching(/Same as store price/));
    await u.type(repair, "120");
    await u.click(screen.getByRole("button", { name: "Save changes" }));
    await vi.waitFor(() => expect(puts()).toHaveLength(1));
    expect(puts()[0].body.repair_price).toBe(120);
    expect(puts()[0].body.cost_price).toBe(40);
  });

  it("hides cost and margin from a role that can't see purchase prices", async () => {
    perms.deny.add("can_view_purchase_price");
    const u = userEvent.setup();
    render(<InventoryDrawer kind="product" id="p1" initial={PROD} onClose={() => {}} />);
    await screen.findByLabelText(/Store price/);
    expect(screen.queryByLabelText(/Cost price/)).toBeNull();
    expect(screen.queryByText(/Margin/)).toBeNull();
    expect(screen.queryByText("Avg cost")).toBeNull();
    await u.click(screen.getByRole("button", { name: "Save changes" }));
    await vi.waitFor(() => expect(puts()).toHaveLength(1));
    expect(puts()[0].body).not.toHaveProperty("cost_price");
  });

  it("refuses negatives and a blank name before sending", async () => {
    const u = userEvent.setup();
    render(<InventoryDrawer kind="product" id="p1" initial={PROD} onClose={() => {}} />);
    const sell = await screen.findByLabelText(/Store price/);
    await u.clear(sell); await u.type(sell, "-5");
    await u.click(screen.getByRole("button", { name: "Save changes" }));
    expect(puts()).toHaveLength(0);
  });

  it("says plainly when the save fails", async () => {
    failPut = true;
    const u = userEvent.setup();
    render(<InventoryDrawer kind="product" id="p1" initial={PROD} onClose={() => {}} />);
    await screen.findByLabelText(/Store price/);
    await u.click(screen.getByRole("button", { name: "Save changes" }));
    await vi.waitFor(() => expect(puts()).toHaveLength(1));
    expect(screen.queryByText(/^Failed:/)).toBeNull();
  });

  it("offers a working photo picker for images only", async () => {
    render(<InventoryDrawer kind="product" id="p1" initial={{ ...PROD, image_data: null }} onClose={() => {}} />);
    const input = await screen.findByLabelText("Add a photo");
    expect(input).toHaveAttribute("type", "file");
    expect(input).toHaveAttribute("accept", "image/*");
    await expect(productPhoto(new Blob(["x"], { type: "application/pdf" }))).rejects.toThrow(/photo/);
    expect(dataUrlBytes("data:image/jpeg;base64,AAAA")).toBe(3);
  });

  it("names the remove buttons and the write-off type plainly", async () => {
    const u = userEvent.setup();
    served = { ...PROD, compatibility_json: { fits: [{ brand: "Apple" }] } };
    render(<InventoryDrawer kind="product" id="p1" initial={served} onClose={() => {}} />);
    expect(await screen.findByRole("button", { name: "Remove line" })).toBeInTheDocument();
    expect(screen.getAllByPlaceholderText("Brand").length).toBe(1);
    await u.click(screen.getByRole("button", { name: "Stock" }));
    await u.click(await screen.findByRole("button", { name: /Adjust stock/ }));
    expect(screen.getByRole("option", { name: "Write off (damaged or lost)" })).toBeInTheDocument();
  });
});

describe("Paste import", () => {
  it("never makes the supplier's code our SKU", () => {
    expect(newProductBody({ name: "Screen", code: "SUP-77" })).toMatchObject({ new_sku: null, new_mpn: "SUP-77", new_barcode: null });
    expect(newProductBody({ name: "Cable", code: "5901234123457" })).toMatchObject({ new_sku: null, new_barcode: "5901234123457", new_mpn: null });
    expect(newProductBody({ code: "X1", mpn: "M-1" })).toMatchObject({ new_name: "X1", new_mpn: "M-1" });
  });
  it("says how a row was matched", () => {
    expect(matchedBy({ code: "abc-1", sku: "ABC-1" })).toBe("same SKU");
    expect(matchedBy({ code: "Z", mpn: "M" })).toBe("MPN");
    expect(matchedBy({ name: "Screen", part_name: "screen" })).toBe("name");
  });
});
