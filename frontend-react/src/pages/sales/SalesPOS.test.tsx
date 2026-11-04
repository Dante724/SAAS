// F1 scan-to-cart: a keyboard-wedge burst or Enter in the search box adds the exact match,
// several matches open a picker, and no match says so.
import { describe, it, expect, vi, beforeAll, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, createEvent, act, within, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

vi.mock("../../components/Shell", () => ({ M: ({ n }: { n: string }) => <i data-icon={n} />, default: ({ children }: any) => <div>{children}</div> }));
vi.mock("../../lib/scanner", async (orig) => ({ ...(await orig<typeof import("../../lib/scanner")>()), scanBeep: vi.fn() }));
// The card reader dialog is its own component: here it only approves or declines.
vi.mock("../../components/ReaderCharge", () => ({ default: ({ onApproved, onClose }: any) => (
  <div role="dialog" aria-label="Card reader">
    <button onClick={() => { onApproved({ status: "succeeded" }); onClose({ status: "succeeded" }); }}>Approve card</button>
    <button onClick={() => onClose({ status: "declined" })}>Decline card</button>
  </div>) }));

import SalesPOS from "./SalesPOS";
import { scanBeep } from "../../lib/scanner";

const reply = (body: unknown, status = 200) => ({ ok: status < 400, status, headers: new Headers(), json: async () => body, text: async () => JSON.stringify(body) }) as Response;
const CABLE = { id: "p1", name: "USB-C cable", sku: "USB-C-1M", barcode: "0123456789012", sell_price: 9.5, qty_available: 4 };
let lookups: string[] = [];
let lookupItems: Record<string, unknown[]> = {};
let products: unknown[] = [CABLE];
type Call = { method: string; path: string; body: any };
let calls: Call[] = [];
let accounts: unknown[] = [];
// B5: the server prices the cart; by default a plain sum with no tax or rewards.
const plainQuote = (b: any): Record<string, unknown> => {
  const sub = b.lines.reduce((s: number, l: any) => s + l.unit_price * l.qty, 0);
  const total = Math.max(sub - (b.discount || 0), 0);
  return { subtotal: sub, discount: b.discount || 0, coupon: 0, loyalty: 0, discount_total: b.discount || 0, taxes: [], tax_total: 0,
    tax_included: 0, total, gift_card: 0, due: total, cash_tendered: b.cash_tendered ?? null,
    change_due: b.cash_tendered == null ? null : Math.max(b.cash_tendered - total, 0), problems: {} };
};
let quote = plainQuote;

// jsdom parses its whole default (UA) stylesheet the first time anything calls getComputedStyle
// (~200ms of CPU when idle, several times that on a loaded machine). ByRole queries and every
// user-event pointer action call it, so that one-off cost used to land inside whichever test's
// first findBy/waitFor window (1000ms) came first. Pay it here, outside any assertion timeout.
beforeAll(() => { getComputedStyle(document.body); });

beforeEach(() => {
  lookups = [];
  calls = [];
  accounts = [];
  quote = plainQuote;
  products = [CABLE];
  lookupItems = { "0123456789012": [CABLE], "555": [{ ...CABLE, id: "p2", name: "Case red" }, { ...CABLE, id: "p3", name: "Case blue" }] };
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    const u = new URL(url, "http://x");
    const method = init?.method || "GET";
    const body = init?.body ? JSON.parse(String(init.body)) : null;
    calls.push({ method, path: u.pathname, body });
    if (u.pathname === "/api/sales/quote") return reply(quote(body));
    if (u.pathname === "/api/cash/accounts") return reply({ items: accounts });
    if (u.pathname === "/api/sales" && method === "POST") {
      const q = quote(body);
      return reply({ id: "s1", sale_number: "S-1", total: q.total, due: q.due, paid: !!body.account_id, redeemed: {}, change_due: q.change_due }, 201);
    }
    if (u.pathname === "/api/sales/s1/pay") return reply({ id: "s1", paid: true, due: 20, change_due: null });
    if (u.pathname === "/api/sales/s1/receipt") return reply({ sale_number: "S-1", shop_name: "Shop", total: 20, paid: true, lines: [], text: "" });
    if (u.pathname === "/api/sales/lookup") {
      const code = u.searchParams.get("code") || "";
      lookups.push(code);
      return reply({ code, items: lookupItems[code] || [] });
    }
    if (u.pathname === "/api/catalog/products") return reply({ items: products });
    return reply({ items: [] });
  }));
});
afterEach(() => { vi.unstubAllGlobals(); vi.mocked(scanBeep).mockClear(); });

// The wedge detector compares each key's event.timeStamp with the previous one (max 50ms gap).
// jsdom stamps events with the wall clock, so a GC pause or a descheduled worker on a loaded
// machine could split one "scan" into two slow bursts and the scan was silently dropped. The
// scanner's keys are stamped explicitly instead, 5ms apart: a burst is a burst however busy the CPU.
// The clock starts at "now" (jsdom stamps real events with Date.now()) and only moves forward, so a
// scan never looks like it came before, or glued onto, a real keypress.
let scanClock = 0;
function scanKey(key: string) {
  const ev = createEvent.keyDown(window, { key });
  Object.defineProperty(ev, "timeStamp", { value: (scanClock += 5) });
  fireEvent(window, ev);
}
function wedge(code: string) {
  // A scanner types every key within a few milliseconds, then Enter.
  scanClock = Math.max(scanClock, Date.now());
  for (const ch of code) scanKey(ch);
  scanKey("Enter");
}

const cartLines = () => screen.queryAllByTestId("cart-line");
// Polling uses Testing Library's waitFor, not vi.waitFor: every failed getByRole() poll inside
// vi.waitFor built the full "Here are the accessible roles" + DOM dump error (60-100ms of CPU per
// poll, every 50ms, while waiting out the 150ms quote debounce), starving the very render it was
// waiting for under load. RTL's waitFor skips those diagnostics while polling and runs in act().

describe("SalesPOS scan-to-cart", () => {
  it("a wedge scan of an exact barcode adds the product, and a second scan adds one more", async () => {
    render(<SalesPOS />);
    await act(async () => wedge("0123456789012"));
    await waitFor(() => expect(cartLines()).toHaveLength(1));
    expect(within(cartLines()[0]).getByText("USB-C cable")).toBeInTheDocument();
    expect(scanBeep).toHaveBeenCalledWith(true);
    await act(async () => wedge("0123456789012"));
    await waitFor(() => expect(within(cartLines()[0]).getByText("2")).toBeInTheDocument());
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
    await waitFor(() => expect(cartLines()).toHaveLength(1));
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

describe("SalesPOS look", () => {
  it("shows stock as a quiet count, amber only when low or out", async () => {
    products = [
      { ...CABLE, id: "a", name: "Plenty", qty_available: 12, low_stock: false },
      { ...CABLE, id: "b", name: "Running low", qty_available: 2, low_stock: true },
      { ...CABLE, id: "c", name: "Gone", qty_available: 0, low_stock: false },
    ];
    render(<SalesPOS />);
    const chipOf = async (name: string) => within((await screen.findByText(name)).closest("button")!).getByTestId("stock-chip");
    const plenty = await chipOf("Plenty");
    expect(plenty).toHaveTextContent("12 in stock");
    expect(plenty).toHaveAttribute("data-warn", "false");
    expect(plenty.className).not.toMatch(/amber/);
    const low = await chipOf("Running low");
    expect(low).toHaveTextContent("2 left");
    expect(low.className).toMatch(/amber/);
    const gone = await chipOf("Gone");
    expect(gone).toHaveTextContent("Out of stock");
    expect(gone).toHaveAttribute("data-warn", "true");
  });

  it("cart lines have touch-sized quantity and remove buttons", async () => {
    const u = userEvent.setup();
    render(<SalesPOS />);
    await u.click(await screen.findByText("USB-C cable"));
    const line = cartLines()[0];
    const remove = within(line).getByRole("button", { name: "Remove item" });
    const less = within(line).getByRole("button", { name: "Decrease quantity" });
    const more = within(line).getByRole("button", { name: "Increase quantity" });
    for (const b of [remove, less, more]) expect(b.className).toMatch(/\bw-9\b.*\bh-9\b|\bh-9\b.*\bw-9\b/);   // 36px
    expect(less).toBeDisabled();                                   // already at 1
    await u.click(more);
    expect(within(line).getByText("2")).toBeInTheDocument();
    expect(less).toBeEnabled();
    await u.click(remove);
    expect(cartLines()).toHaveLength(0);
  });

  it("money inputs use the shared field style, not mono", async () => {
    render(<SalesPOS />);
    const discount = await screen.findByLabelText(/Discount/);
    expect(discount.className).toMatch(/tabular-nums/);
    expect(discount.className).not.toMatch(/font-mono|glass-input/);
  });
});

// B5: Charge is enabled once the server has priced the cart (POST /sales/quote).
const chargeBtn = () => screen.getByRole("button", { name: /^Charge [^o]/ });

describe("SalesPOS charge", () => {
  it("sends an Idempotency-Key and re-sends it when a failed charge is retried", async () => {
    const keys: (string | undefined)[] = [];
    let fail = true;
    const base = vi.mocked(fetch).getMockImplementation()!;
    vi.mocked(fetch).mockImplementation(async (url: any, init?: RequestInit) => {
      if (init?.method === "POST" && new URL(url, "http://x").pathname === "/api/sales") {
        keys.push((init.headers as Record<string, string>)["Idempotency-Key"]);
        if (fail) { fail = false; return reply({ detail: "timeout" }, 504); }
        return reply({ id: "s1", sale_number: "S-1", total: 9.5, paid: true, redeemed: {} }, 201);
      }
      return base(url, init);
    });
    const u = userEvent.setup();
    render(<SalesPOS />);
    await act(async () => wedge("0123456789012"));
    await waitFor(() => expect(cartLines()).toHaveLength(1));
    await waitFor(() => expect(chargeBtn()).toBeEnabled());
    await u.click(chargeBtn());
    await waitFor(() => expect(keys).toHaveLength(1));
    await waitFor(() => expect(chargeBtn()).toBeEnabled());
    await u.click(chargeBtn());
    await waitFor(() => expect(keys).toHaveLength(2));
    expect(keys[0]).toMatch(/^web-/);
    expect(keys[1]).toBe(keys[0]);                       // the retry can't make a second sale
    await waitFor(() => expect(cartLines()).toHaveLength(0));   // the sale went through once
  });
});

// QA F2: a serialized phone needs its IMEI before the till charges, and the sale carries it.
describe("SalesPOS serialized items", () => {
  const PHONE = { id: "ph", name: "iPhone 13", sku: "IP13", barcode: "1900000000001", sell_price: 400, qty_available: 2, serialized: true };
  const IMEI = "352841012345067";
  function catchSales() {
    const bodies: any[] = [];
    const base = vi.mocked(fetch).getMockImplementation()!;
    vi.mocked(fetch).mockImplementation(async (url: any, init?: RequestInit) => {
      const path = new URL(url, "http://x").pathname;
      if (init?.method === "POST" && path === "/api/sales") {
        bodies.push(JSON.parse(String(init.body)));
        return reply({ id: "s1", sale_number: "S-1", total: 400, paid: true, redeemed: {} }, 201);
      }
      if (path === "/api/serials") return reply({ items: [{ serial_number: IMEI, status: "in_stock" }] });
      return base(url, init);
    });
    return bodies;
  }

  it("asks for the IMEI before charging, then sends it with the line", async () => {
    lookupItems["1900000000001"] = [PHONE];
    const bodies = catchSales();
    const u = userEvent.setup();
    render(<SalesPOS />);
    await act(async () => wedge("1900000000001"));
    await waitFor(() => expect(cartLines()).toHaveLength(1));
    await waitFor(() => expect(chargeBtn()).toBeEnabled());
    await u.click(chargeBtn());
    await waitFor(() => expect(screen.getByText(/Scan the IMEI \/ serial of each iPhone 13/)).toBeInTheDocument());
    expect(bodies).toHaveLength(0);                              // nothing sold without the unit
    const box = screen.getByLabelText("IMEI or serial for iPhone 13");
    await u.type(box, `${IMEI}{Enter}`);
    expect(within(cartLines()[0]).getByText(IMEI)).toBeInTheDocument();
    await waitFor(() => expect(chargeBtn()).toBeEnabled());
    await u.click(chargeBtn());
    await waitFor(() => expect(bodies).toHaveLength(1));
    expect(bodies[0].lines[0]).toMatchObject({ ref_id: "ph", qty: 1, serials: [IMEI] });
  });

  it("scanning the phone's IMEI puts that unit on the line", async () => {
    lookupItems[IMEI] = [{ ...PHONE, matched_on: "serial", serial: IMEI }];
    const bodies = catchSales();
    const u = userEvent.setup();
    render(<SalesPOS />);
    await act(async () => wedge(IMEI));
    await waitFor(() => expect(cartLines()).toHaveLength(1));
    expect(within(cartLines()[0]).getByText(IMEI)).toBeInTheDocument();
    expect(screen.queryByLabelText("IMEI or serial for iPhone 13")).toBeNull();
    await act(async () => wedge(IMEI));                       // the same phone scanned again: still one
    expect(within(cartLines()[0]).getByText("1")).toBeInTheDocument();
    await waitFor(() => expect(chargeBtn()).toBeEnabled());
    await u.click(chargeBtn());
    await waitFor(() => expect(bodies).toHaveLength(1));
    expect(bodies[0].lines[0].serials).toEqual([IMEI]);
  });

  it("tapping the phone, then scanning its IMEI, fills the line instead of adding a second phone", async () => {
    products = [PHONE];
    lookupItems[IMEI] = [{ ...PHONE, matched_on: "serial", serial: IMEI }];
    catchSales();
    const u = userEvent.setup();
    render(<SalesPOS />);
    await u.click(await screen.findByRole("button", { name: /iPhone 13/ }));
    await waitFor(() => expect(cartLines()).toHaveLength(1));
    await act(async () => wedge(IMEI));
    await waitFor(() => expect(within(cartLines()[0]).getByText(IMEI)).toBeInTheDocument());
    expect(within(cartLines()[0]).getByText("1")).toBeInTheDocument();
  });
});

describe("SalesPOS charges what the button says (B5)", () => {
  const CASE = { id: "p68", name: "Phone case", sku: "CASE", sell_price: 68, qty_available: 9 };
  const charge = () => screen.getByRole("button", { name: /^Charge (?!on reader)/ });
  const lastQuote = () => [...calls].reverse().find((c) => c.path === "/api/sales/quote")!.body;

  it("the reviewer's case: ₹68, ₹3 off and SAVE10 charges the server's 58.20, not 65", async () => {
    products = [CASE];
    quote = (b: any) => ({ subtotal: 68, discount: b.discount, coupon: b.coupon_code ? 6.8 : 0, loyalty: 0, discount_total: 0, taxes: [],
      tax_total: 0, tax_included: 0, total: 68 - b.discount - (b.coupon_code ? 6.8 : 0), gift_card: 0,
      due: 68 - b.discount - (b.coupon_code ? 6.8 : 0), cash_tendered: null, change_due: null, problems: {} });
    const u = userEvent.setup();
    render(<SalesPOS />);
    await u.click(await screen.findByText("Phone case"));
    const discount = screen.getByLabelText(/Discount/);
    await u.clear(discount); await u.type(discount, "3");
    await u.click(screen.getByRole("button", { name: /Rewards/ }));
    await u.type(screen.getByLabelText("Coupon code"), "save10");
    await waitFor(() => expect(charge()).toHaveTextContent("Charge $58.20"));
    expect(charge()).toBeEnabled();
    expect(lastQuote()).toMatchObject({ discount: 3, coupon_code: "SAVE10" });
    expect(screen.getByText("Coupon")).toBeInTheDocument();
    await u.click(charge());
    await waitFor(() => expect(calls.some((c) => c.path === "/api/sales" && c.method === "POST")).toBe(true));
    const sent = calls.find((c) => c.path === "/api/sales" && c.method === "POST")!.body;
    expect(sent.expected_due).toBeCloseTo(58.2);    // the server refuses to save any other amount
  });

  it("shows tax from the server and a gift card as a payment, charging only what is left", async () => {
    products = [CASE];
    quote = (b: any) => ({ subtotal: 68, discount: 0, coupon: 0, loyalty: 0, discount_total: 0,
      taxes: [{ name: "VAT", rate: 10, mode: "imposed", amount: 6.8 }], tax_total: 6.8, tax_included: 0, total: 74.8,
      gift_card: b.gift_card_code ? 30 : 0, due: b.gift_card_code ? 44.8 : 74.8, cash_tendered: null, change_due: null, problems: {} });
    const u = userEvent.setup();
    render(<SalesPOS />);
    await u.click(await screen.findByText("Phone case"));
    await waitFor(() => expect(charge()).toHaveTextContent("Charge $74.80"));
    expect(screen.getByText("VAT 10%")).toBeInTheDocument();
    await u.click(screen.getByRole("button", { name: /Rewards/ }));
    await u.type(screen.getByLabelText("Gift card"), "gc-1");
    await waitFor(() => expect(charge()).toHaveTextContent("Charge $44.80"));
    expect(screen.getByText("Paid by gift card")).toBeInTheDocument();
  });

  it("a bad code is shown under its field and priced without it", async () => {
    quote = (b: any) => ({ subtotal: 9.5, discount: 0, coupon: 0, loyalty: 0, discount_total: 0, taxes: [], tax_total: 0, tax_included: 0,
      total: 9.5, gift_card: 0, due: 9.5, cash_tendered: null, change_due: null,
      problems: b.coupon_code ? { coupon: "Coupon not found or inactive" } : {} });
    const u = userEvent.setup();
    render(<SalesPOS />);
    await u.click(await screen.findByText("USB-C cable"));
    await u.click(screen.getByRole("button", { name: /Rewards/ }));
    await u.type(screen.getByLabelText("Coupon code"), "NOPE");
    expect(await screen.findByText("Coupon not found or inactive")).toBeInTheDocument();
    expect(charge()).toBeDisabled();
  });

  it("cash: change due is shown, and too little cash can't be charged", async () => {
    accounts = [{ id: "till", name: "Till", payment_option: "cash" }];
    const u = userEvent.setup();
    render(<SalesPOS />);
    await u.click(await screen.findByText("USB-C cable"));
    const given = await screen.findByLabelText("Cash received");
    await u.type(given, "5");
    await waitFor(() => expect(charge()).toBeDisabled());
    expect(screen.getByText(/less than the total/)).toBeInTheDocument();
    await u.clear(given); await u.type(given, "20");
    await waitFor(() => expect(screen.getByTestId("change-due")).toHaveTextContent("$10.50"));
    expect(charge()).toBeEnabled();
  });

  it("a declined card keeps the cart and lets the cashier take payment another way", async () => {
    accounts = [{ id: "till", name: "Till", payment_option: "cash" }];
    const u = userEvent.setup();
    render(<SalesPOS />);
    await u.click(await screen.findByText("USB-C cable"));
    await waitFor(() => expect(charge()).toBeEnabled());
    await u.click(screen.getByRole("button", { name: /Charge on reader/ }));
    await u.click(await screen.findByText("Decline card"));
    expect(cartLines()).toHaveLength(1);                         // nothing lost
    const box = await screen.findByTestId("pending-sale");
    expect(within(box).getByText(/S-1 isn't paid yet/)).toBeInTheDocument();
    expect(within(box).getByRole("button", { name: "Try the card again" })).toBeInTheDocument();
    await u.click(within(box).getByRole("button", { name: /Take payment/ }));
    await waitFor(() => expect(calls.some((c) => c.path === "/api/sales/s1/pay")).toBe(true));
    expect(calls.find((c) => c.path === "/api/sales/s1/pay")!.body).toMatchObject({ account_id: "till" });
    await waitFor(() => expect(cartLines()).toHaveLength(0));
  });

  it("an approved card clears the cart only once it is paid", async () => {
    const u = userEvent.setup();
    render(<SalesPOS />);
    await u.click(await screen.findByText("USB-C cable"));
    await waitFor(() => expect(charge()).toBeEnabled());
    await u.click(screen.getByRole("button", { name: /Charge on reader/ }));
    await screen.findByRole("dialog", { name: "Card reader" });
    expect(cartLines()).toHaveLength(1);
    await u.click(screen.getByText("Approve card"));
    await waitFor(() => expect(cartLines()).toHaveLength(0));
  });
});
