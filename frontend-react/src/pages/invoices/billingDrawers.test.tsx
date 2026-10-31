// Billing drawers: record payment (partial / overpay), credit notes (totals, draft → issue), invoice view.
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

vi.mock("../../components/Shell", () => ({ M: ({ n }: { n: string }) => <i data-icon={n} />, default: ({ children }: any) => <div>{children}</div> }));
vi.mock("../../components/BulkPrintDialog", () => ({ default: () => null }));
const perms = { deny: new Set<string>() };
vi.mock("../../context/Me", () => ({ useMe: () => ({ can: (c?: string) => !c || !perms.deny.has(c), me: null, loading: false }) }));
vi.mock("../../lib/dialog", () => ({ confirmDialog: vi.fn(async () => true), alertDialog: vi.fn(async () => {}) }));

import RecordPaymentDrawer from "./RecordPaymentDrawer";
import CreditNoteDrawer, { linesFromInvoice, lineAmounts } from "./CreditNoteDrawer";
import InvoiceDrawer from "./InvoiceDrawer";
import type { InvoiceDetail } from "./billing";

type Call = { url: string; method: string; body: any };
let calls: Call[] = [];
let routes: Record<string, (body: any) => unknown> = {};
const reply = (body: unknown, status = 200) => ({ ok: status < 400, status, headers: new Headers(), json: async () => body, text: async () => JSON.stringify(body) }) as Response;

beforeEach(() => {
  calls = []; perms.deny = new Set();
  routes = { "GET /api/cash/accounts": () => ({ items: [{ id: "a1", name: "Front till", payment_option: "cash" }, { id: "a2", name: "Card reader", payment_option: "card" }] }) };
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    const method = init?.method || "GET";
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    calls.push({ url, method, body });
    const key = Object.keys(routes).find((k) => { const [m, p] = k.split(" "); return m === method && url.split("?")[0] === p; });
    return reply(key ? routes[key](body) : {});
  }));
});
afterEach(() => vi.unstubAllGlobals());

const inv = (o: Partial<InvoiceDetail> = {}): InvoiceDetail => ({
  id: "i1", invoice_number: "INV-00007", status: "partial", subtotal: 100, total: 100, amount_paid: 40, credited: 0, balance_due: 60,
  client_id: "c1", customer: { id: "c1", name: "Ravi" }, display_mode: "detailed", discount: 0, tax: 0,
  lines: [{ description: "Screen", qty: 1, unit_price: 100, total: 100 }], payments: [], credit_notes: [], ...o,
});
const posted = (path: string) => calls.filter((c) => c.method !== "GET" && c.url === `/api${path}`);

describe("RecordPaymentDrawer", () => {
  it("records a partial payment into the chosen account", async () => {
    const u = userEvent.setup(); const onPaid = vi.fn();
    routes["POST /api/finance/invoices/i1/pay"] = () => ({ ok: true });
    render(<RecordPaymentDrawer invoice={inv()} onClose={() => {}} onPaid={onPaid} />);
    const amount = screen.getByLabelText("Amount");
    expect((amount as HTMLInputElement).value).toBe("60");          // defaults to the balance due
    await u.clear(amount); await u.type(amount, "25");
    await screen.findByRole("option", { name: "Front till" });
    await u.click(screen.getByRole("button", { name: /Record .*25\.00/ }));
    await vi.waitFor(() => expect(onPaid).toHaveBeenCalled());
    expect(posted("/finance/invoices/i1/pay")[0].body).toMatchObject({ amount: 25, method: "cash", account_id: "a1", overpay: null, paid_at: null });
  });

  it("asks what to do with an overpayment and sends the choice", async () => {
    const u = userEvent.setup(); const onPaid = vi.fn();
    routes["POST /api/finance/invoices/i1/pay"] = () => ({ ok: true });
    render(<RecordPaymentDrawer invoice={inv()} onClose={() => {}} onPaid={onPaid} />);
    const amount = screen.getByLabelText("Amount");
    await u.clear(amount); await u.type(amount, "75.5");
    expect(screen.getByText(/15\.50 more than the balance due/)).toBeTruthy();
    expect((screen.getByRole("radio", { name: /Keep as client balance/ }) as HTMLInputElement).checked).toBe(true);
    await u.click(screen.getByRole("radio", { name: /Refund the difference/ }));
    await u.click(screen.getByRole("button", { name: /Record/ }));
    await vi.waitFor(() => expect(onPaid).toHaveBeenCalled());
    expect(posted("/finance/invoices/i1/pay")[0].body).toMatchObject({ amount: 75.5, overpay: "refund" });
  });

  it("can't keep credit on a walk-in invoice or refund without the permission", async () => {
    perms.deny.add("can_create_returns");
    const u = userEvent.setup();
    render(<RecordPaymentDrawer invoice={inv({ client_id: null })} onClose={() => {}} onPaid={() => {}} />);
    const amount = screen.getByLabelText("Amount");
    await u.clear(amount); await u.type(amount, "80");
    expect((screen.getByRole("radio", { name: /Keep as client balance/ }) as HTMLInputElement).disabled).toBe(true);
    expect((screen.getByRole("radio", { name: /Refund the difference/ }) as HTMLInputElement).disabled).toBe(true);
    expect((screen.getByRole("button", { name: /Record/ }) as HTMLButtonElement).disabled).toBe(true);
  });
});

describe("CreditNoteDrawer", () => {
  it("computes line tax half-up like the server", () => {
    expect(lineAmounts({ description: "x", qty: 3, unit_price: 33.33, tax_rate: 7.5 })).toEqual({ net: 99.99, tax: 7.5, total: 107.49 });
  });

  it("prefills from the invoice at its discount and tax, or the remainder once part is credited", () => {
    // discount and tax are amounts: 10 off 100, then 4.50 tax on the 90 left is 5%
    expect(linesFromInvoice(inv({ subtotal: 100, discount: 10, tax: 4.5, total: 94.5 }))).toEqual([{ description: "Screen", qty: 1, unit_price: 90, tax_rate: 5 }]);
    expect(linesFromInvoice(inv({ subtotal: 300, tax: 9, total: 309 }))[0].tax_rate).toBe(3);
    expect(linesFromInvoice(inv({ credited: 40, tax: 0 }))[0]).toMatchObject({ unit_price: 60, qty: 1 });
  });

  it("saves the draft and issues it", async () => {
    const u = userEvent.setup(); const onChanged = vi.fn();
    const note = { id: "cn1", status: "draft", invoice_id: "i1", invoice_number: "INV-00007", client_id: "c1", client_name: "Ravi",
      subtotal: 100, tax_total: 0, total: 100, lines: [{ description: "Screen", qty: 1, unit_price: 100, tax_rate: 0 }] };
    routes["POST /api/finance/credit-notes"] = (b) => ({ ...note, lines: b.lines });
    routes["POST /api/finance/credit-notes/cn1/issue"] = () => ({ ...note, status: "issued", number: "CN-00001", issued_at: "2026-10-21T10:00:00" });
    render(<CreditNoteDrawer invoice={inv()} onClose={() => {}} onChanged={onChanged} />);
    const price = screen.getByLabelText("Line 1 unit price");
    await u.clear(price); await u.type(price, "20");
    await u.type(screen.getByLabelText("Reason"), "Cracked bezel");
    await u.click(screen.getByRole("button", { name: /Issue .*20\.00/ }));
    await screen.findByText("Credit note CN-00001");
    const body = posted("/finance/credit-notes")[0].body;
    expect(body).toMatchObject({ invoice_id: "i1", client_id: null, reason: "Cracked bezel" });
    expect(body.lines).toEqual([{ description: "Screen", qty: 1, unit_price: 20, tax_rate: 0 }]);
    expect(posted("/finance/credit-notes/cn1/issue")).toHaveLength(1);
    expect(onChanged).toHaveBeenCalled();
    // issued is locked: no inputs, no save/issue, but it can be canceled
    expect(screen.queryByLabelText("Line 1 unit price")).toBeNull();
    expect(screen.queryByRole("button", { name: "Save draft" })).toBeNull();
    // the extra actions (PDF, print, cancel) live in the footer's "⋮" menu
    await u.click(screen.getByRole("button", { name: "More actions" }));
    expect(screen.getByRole("menuitem", { name: "Cancel credit note" })).toBeTruthy();
    expect(screen.getByRole("menuitem", { name: "Download PDF" })).toBeTruthy();
    expect(screen.queryByRole("menuitem", { name: "Delete draft" })).toBeNull();
  });

  it("a standalone note needs a client", async () => {
    const u = userEvent.setup();
    routes["GET /api/clients"] = () => ({ items: [{ id: "c9", name: "Mia" }] });
    render(<CreditNoteDrawer onClose={() => {}} />);
    await u.type(screen.getByLabelText("Line 1 description"), "Goodwill");
    await u.click(screen.getByRole("button", { name: "Save draft" }));
    expect(screen.getByRole("alert").textContent).toMatch(/Pick the client/);
    expect(posted("/finance/credit-notes")).toHaveLength(0);
  });
});

describe("InvoiceDrawer", () => {
  it("shows the balance due block and switches the display mode", async () => {
    const u = userEvent.setup();
    routes["GET /api/finance/invoices/i1"] = () => inv({ credited: 10, balance_due: 50, credit_notes: [{ id: "cn1", number: "CN-00001", status: "issued", total: 10 }] });
    routes["PATCH /api/finance/invoices/i1"] = (b) => inv({ display_mode: b.display_mode });
    render(<InvoiceDrawer id="i1" onClose={() => {}} onChanged={() => {}} />);
    const block = await screen.findByRole("region", { name: "Balance due" });
    expect(within(block).getByText(/50\.00/)).toBeTruthy();
    expect(within(block).getByText(/10\.00/)).toBeTruthy();
    expect(screen.getByText("CN-00001")).toBeTruthy();
    await u.click(screen.getByRole("radio", { name: "One line" }));
    expect(posted("/finance/invoices/i1")[0].body).toEqual({ display_mode: "one_line" });
    expect(screen.getByRole("button", { name: "Record payment" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Create credit note" })).toBeTruthy();
  });

  it("reads as a statement: total, then each payment and credit note, then Balance due", async () => {
    routes["GET /api/finance/invoices/i1"] = () => inv({ amount_paid: 40, credited: 10, balance_due: 50,
      payments: [{ id: "p1", kind: "payment", method: "cash", amount: 40, paid_at: "2026-10-02T10:00:00" }],
      credit_notes: [{ id: "cn1", number: "CN-00001", status: "issued", total: 10, issued_at: "2026-10-03T10:00:00" }] });
    render(<InvoiceDrawer id="i1" onClose={() => {}} onChanged={() => {}} />);
    const statement = await screen.findByRole("list", { name: "Statement" });
    const rows = [...statement.querySelectorAll("[data-row]")].map((r) => r.getAttribute("data-row"));
    expect(rows).toEqual(["total", "payment", "credit", "balance"]);
    expect(statement.querySelector('[data-row="payment"]')!.textContent).toMatch(/−\s*\$?40\.00/);
    expect(statement.querySelector('[data-row="credit"]')!.textContent).toMatch(/−\s*\$?10\.00/);
  });

  it("keeps Balance due in ink until the due date has passed, then shows it red with the days late", async () => {
    const soon = new Date(Date.now() + 5 * 86400000).toISOString();
    routes["GET /api/finance/invoices/i1"] = () => inv({ due_at: soon });
    const { unmount } = render(<InvoiceDrawer id="i1" onClose={() => {}} onChanged={() => {}} />);
    const ok = await screen.findByTestId("balance-due");
    expect(ok.className).not.toMatch(/rose/);
    expect(screen.queryByText(/days? late/)).toBeNull();
    unmount();

    const past = new Date(Date.now() - 3 * 86400000).toISOString();
    routes["GET /api/finance/invoices/i1"] = () => inv({ due_at: past });
    render(<InvoiceDrawer id="i1" onClose={() => {}} onChanged={() => {}} />);
    const late = await screen.findByTestId("balance-due");
    expect(late.className).toMatch(/text-rose-600/);
    expect(screen.getAllByText(/3 days late/).length).toBeGreaterThan(0);
  });

  it("hides money actions on a settled invoice", async () => {
    routes["GET /api/finance/invoices/i1"] = () => inv({ status: "paid", amount_paid: 100, balance_due: 0, credited: 0 });
    render(<InvoiceDrawer id="i1" onClose={() => {}} onChanged={() => {}} />);
    await screen.findByRole("region", { name: "Balance due" });
    expect(screen.queryByRole("button", { name: "Record payment" })).toBeNull();
  });
});
