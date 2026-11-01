// Money-safety: a double-click on "Issue e-invoice" must post exactly once.
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

vi.mock("../../components/Shell", () => ({ M: ({ n }: { n: string }) => <i data-icon={n} />, default: ({ children }: any) => <div>{children}</div> }));
vi.mock("../../components/BulkPrintDialog", () => ({ default: () => null }));
vi.mock("../../context/Me", () => ({ useMe: () => ({ can: () => true, me: null, loading: false }) }));

import InvoicesHub from "./InvoicesHub";

let posts: string[] = [];
let list: unknown[] = [];
let releasePost: () => void = () => {};
const reply = (body: unknown) => ({ ok: true, status: 200, headers: new Headers(), json: async () => body, text: async () => JSON.stringify(body) }) as Response;

beforeEach(() => {
  posts = [];
  list = [{ id: "i1", invoice_number: "INV-1", status: "issued", total: 10, einvoice_status: "none" }];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    if (init?.method === "POST") { posts.push(url); await new Promise<void>((r) => { releasePost = r; }); return reply({}); }
    if (url.startsWith("/api/regions/pack")) return reply({ einvoice: { standard: "peppol" } });
    if (url.startsWith("/api/finance/invoices")) return reply({ items: list, total: list.length });
    return reply({});
  }));
});
afterEach(() => vi.unstubAllGlobals());

describe("InvoicesHub e-invoice", () => {
  it("double-clicking Issue e-invoice posts once and disables the button", async () => {
    const u = userEvent.setup();
    render(<InvoicesHub />);
    await u.dblClick(await screen.findByRole("button", { name: "Issue e-invoice" }));
    expect(posts).toEqual(["/api/finance/invoices/i1/einvoice"]);
    expect((screen.getByRole("button", { name: "Issuing…" }) as HTMLButtonElement).disabled).toBe(true);
    releasePost();
    await vi.waitFor(() => expect(screen.queryByRole("button", { name: "Issuing…" })).toBeNull());
    expect(posts).toHaveLength(1);
  });

  it("shows the balance in ink, and red with the days late only once it's past due", async () => {
    const past = new Date(Date.now() - 2 * 86400000).toISOString();
    const later = new Date(Date.now() + 9 * 86400000).toISOString();
    list = [
      { id: "a", invoice_number: "INV-A", status: "issued", total: 50, amount_paid: 0, balance_due: 50, due_at: later, client_name: "Ana" },
      { id: "b", invoice_number: "INV-B", status: "partial", total: 80, amount_paid: 20, balance_due: 60, due_at: past, client_name: "Ben" },
    ];
    render(<InvoicesHub />);
    const table = await screen.findByRole("table");
    const rowA = within(table).getByText("INV-A").closest("tr")!;
    const rowB = within(table).getByText("INV-B").closest("tr")!;
    expect(within(rowA).getByTestId("row-balance").className).not.toMatch(/rose/);
    expect(within(rowB).getByTestId("row-balance").className).toMatch(/text-rose-600/);
    expect(within(rowB).getByText("2 days late")).toBeTruthy();
    expect(within(rowA).queryByText(/late/)).toBeNull();
  });

  it("keeps Print and Delete in the row's menu", async () => {
    const u = userEvent.setup();
    list = [{ id: "a", invoice_number: "INV-A", status: "issued", total: 50, amount_paid: 0, balance_due: 50 }];
    render(<InvoicesHub />);
    const row = (await within(await screen.findByRole("table")).findByText("INV-A")).closest("tr")!;
    expect(within(row).queryByRole("button", { name: /Print/ })).toBeNull();
    await u.click(within(row).getByRole("button", { name: "More actions for INV-A" }));
    expect(screen.getByRole("menuitem", { name: "Print / PDF" })).toBeTruthy();
    expect(screen.getByRole("menuitem", { name: "Delete invoice" })).toBeTruthy();
  });

  it("explains an empty list and offers the first step", async () => {
    list = [];
    render(<InvoicesHub />);
    expect(await screen.findByText("No invoices yet")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Create your first invoice" })).toBeTruthy();
  });
});
