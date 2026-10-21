// F1 scan-to-cart: a keyboard-wedge burst or Enter in the search box adds the exact match,
// several matches open a picker, and no match says so.
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, act, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

vi.mock("../../components/Shell", () => ({ M: ({ n }: { n: string }) => <i data-icon={n} />, default: ({ children }: any) => <div>{children}</div> }));
vi.mock("../../lib/scanner", async (orig) => ({ ...(await orig<typeof import("../../lib/scanner")>()), scanBeep: vi.fn() }));

import SalesPOS from "./SalesPOS";
import { scanBeep } from "../../lib/scanner";

const reply = (body: unknown, status = 200) => ({ ok: status < 400, status, headers: new Headers(), json: async () => body, text: async () => JSON.stringify(body) }) as Response;
const CABLE = { id: "p1", name: "USB-C cable", sku: "USB-C-1M", barcode: "0123456789012", sell_price: 9.5, qty_available: 4 };
let lookups: string[] = [];
let lookupItems: Record<string, unknown[]> = {};

beforeEach(() => {
  lookups = [];
  lookupItems = { "0123456789012": [CABLE], "555": [{ ...CABLE, id: "p2", name: "Case red" }, { ...CABLE, id: "p3", name: "Case blue" }] };
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    const u = new URL(url, "http://x");
    if (u.pathname === "/api/sales/lookup") {
      const code = u.searchParams.get("code") || "";
      lookups.push(code);
      return reply({ code, items: lookupItems[code] || [] });
    }
    if (u.pathname === "/api/catalog/products") return reply({ items: [CABLE] });
    return reply({ items: [] });
  }));
});
afterEach(() => { vi.unstubAllGlobals(); vi.mocked(scanBeep).mockClear(); });

function wedge(code: string) {
  // A scanner types every key within a few milliseconds, then Enter.
  for (const ch of code) fireEvent.keyDown(window, { key: ch });
  fireEvent.keyDown(window, { key: "Enter" });
}

const cartLines = () => screen.queryAllByTestId("cart-line");

describe("SalesPOS scan-to-cart", () => {
  it("a wedge scan of an exact barcode adds the product, and a second scan adds one more", async () => {
    render(<SalesPOS />);
    await act(async () => wedge("0123456789012"));
    await vi.waitFor(() => expect(cartLines()).toHaveLength(1));
    expect(within(cartLines()[0]).getByText("USB-C cable")).toBeInTheDocument();
    expect(scanBeep).toHaveBeenCalledWith(true);
    await act(async () => wedge("0123456789012"));
    await vi.waitFor(() => expect(within(cartLines()[0]).getByText("2")).toBeInTheDocument());
    expect(cartLines()).toHaveLength(1);
    expect(lookups).toEqual(["0123456789012", "0123456789012"]);
  });

  it("several matches open a picker; picking one adds it", async () => {
    const u = userEvent.setup();
    render(<SalesPOS />);
    await act(async () => wedge("555"));
    expect(lookups).toEqual([]);                    // too short for a scanner burst (min 4)
    const search = screen.getByLabelText("Search or scan");
    await u.type(search, "555{Enter}");
    const dlg = await screen.findByRole("dialog", { name: "Choose a product" });
    await u.click(within(dlg).getByText("Case blue"));
    await vi.waitFor(() => expect(cartLines()).toHaveLength(1));
    expect(within(cartLines()[0]).getByText("Case blue")).toBeInTheDocument();
    expect(screen.queryByRole("dialog", { name: "Choose a product" })).not.toBeInTheDocument();
  });

  it("no match beeps low, adds nothing and says so", async () => {
    render(<SalesPOS />);
    await act(async () => wedge("NOPE-404"));
    expect(await screen.findByText('No product has the code "NOPE-404"')).toBeInTheDocument();
    expect(scanBeep).toHaveBeenCalledWith(false);
    expect(cartLines()).toHaveLength(0);
  });

  it("typing in a field never triggers a scan", async () => {
    const u = userEvent.setup();
    render(<SalesPOS />);
    await u.type(screen.getByPlaceholderText("Attach client (optional)…"), "0123456789012{Enter}");
    expect(lookups).toEqual([]);
  });
});
