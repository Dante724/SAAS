// Money-safety: cancelling the "Delete this transaction?" prompt must not void anything.
// promptDialog resolves null on Cancel / Escape / backdrop, "" on OK with no reason.
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

vi.mock("../../components/Shell", () => ({ M: ({ n }: { n: string }) => <i data-icon={n} />, default: ({ children }: any) => <div>{children}</div> }));

import CashPanel from "./CashPanel";

let calls: { method: string; url: string }[] = [];
const reply = (body: unknown) => ({ ok: true, status: 200, headers: new Headers(), json: async () => body, text: async () => JSON.stringify(body) }) as Response;

beforeEach(() => {
  calls = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ method: init?.method || "GET", url });
    if (url.startsWith("/api/cash/accounts")) return reply({ items: [{ id: "acc1", name: "Till", payment_option: "cash", balance: 100 }] });
    if (url.startsWith("/api/cash/transactions?")) return reply({ items: [{ id: "t1", txn_type: "income", amount: 50, comment: "Screen repair", status: "posted" }], total: 240,
      summary: { count: 237, income: 9000, expense: 10, today_in: 50, today_out: 0, today_net: 50 } });
    return reply({ items: [] });
  }));
});
afterEach(() => vi.unstubAllGlobals());

const deletes = () => calls.filter((c) => c.method === "DELETE");

describe("CashPanel delete transaction", () => {
  it("Cancel on the reason prompt sends no DELETE", async () => {
    const u = userEvent.setup();
    render(<CashPanel />);
    await u.click(await screen.findByRole("button", { name: "Delete" }, { timeout: 5000 }));
    await u.click(await screen.findByRole("button", { name: "Cancel" }));
    await new Promise((r) => setTimeout(r, 0));
    expect(deletes()).toHaveLength(0);
  });

  it("Escape on the reason prompt sends no DELETE", async () => {
    const u = userEvent.setup();
    render(<CashPanel />);
    await u.click(await screen.findByRole("button", { name: "Delete" }, { timeout: 5000 }));
    await screen.findByRole("button", { name: "Cancel" });
    await u.keyboard("{Escape}");
    await new Promise((r) => setTimeout(r, 0));
    expect(deletes()).toHaveLength(0);
  });

  it("OK with no reason still voids the transaction", async () => {
    const u = userEvent.setup();
    render(<CashPanel />);
    await u.click(await screen.findByRole("button", { name: "Delete" }, { timeout: 5000 }));
    await u.click(await screen.findByRole("button", { name: "OK" }));
    await vi.waitFor(() => expect(deletes()).toHaveLength(1));
    expect(deletes()[0].url).toBe("/api/cash/transactions/t1");
  });

  it("OK with a reason sends it along", async () => {
    const u = userEvent.setup();
    render(<CashPanel />);
    await u.click(await screen.findByRole("button", { name: "Delete" }, { timeout: 5000 }));
    await u.type(await screen.findByRole("textbox"), "duplicate");
    await u.click(screen.getByRole("button", { name: "OK" }));
    await vi.waitFor(() => expect(deletes()).toHaveLength(1));
    expect(deletes()[0].url).toBe("/api/cash/transactions/t1?reason=duplicate");
  });
});

describe("CashPanel ledger paging", () => {
  it("asks the server for one page plus full-set totals", async () => {
    render(<CashPanel />);
    expect(await screen.findByText(/237 transactions/)).toBeTruthy();
    const u = new URL(calls.find((c) => c.url.startsWith("/api/cash/transactions?"))!.url, "http://x");
    expect(u.searchParams.get("account_id")).toBe("acc1");
    expect(u.searchParams.get("limit")).toBe("50");
    expect(u.searchParams.get("summary")).toBe("true");
    expect(calls.every((c) => !c.url.includes("limit=1000"))).toBe(true);
  });
});
