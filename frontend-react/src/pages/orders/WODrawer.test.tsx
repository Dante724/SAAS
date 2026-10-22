// Money-safety: a double-click on "Record payment" must post exactly one payment.
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

vi.mock("../../components/Shell", () => ({ M: ({ n }: { n: string }) => <i data-icon={n} />, default: ({ children }: any) => <div>{children}</div> }));

import { InvoicesTab } from "./WODrawer";

let posts: string[] = [];
let releasePost: () => void = () => {};
const reply = (body: unknown) => ({ ok: true, status: 200, headers: new Headers(), json: async () => body, text: async () => JSON.stringify(body) }) as Response;

beforeEach(() => {
  posts = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    if (init?.method === "POST") {
      posts.push(url);
      // Hold the request open so the second click lands while the first is in flight.
      await new Promise<void>((r) => { releasePost = r; });
      return reply({ ok: true });
    }
    if (url.includes("/payment-context")) return reply({ order_total: 100, already_paid: 0, prepaid: 0, remaining: 100, accounts_by_method: { cash: [{ id: "acc1", name: "Till" }] } });
    if (url.includes("/invoices")) return reply({ invoices: [], payments: [] });
    return reply({});
  }));
});
afterEach(() => vi.unstubAllGlobals());

describe("InvoicesTab record payment", () => {
  it("double-clicking Record payment posts exactly once and shows a busy label", async () => {
    const u = userEvent.setup();
    const onFlash = vi.fn();
    render(<InvoicesTab orderId="o1" total={100} onFlash={onFlash} />);
    await u.click(await screen.findByRole("button", { name: /Take Payment/ }));
    const btn = await screen.findByRole("button", { name: "Record payment" });
    await u.dblClick(btn);
    expect(posts).toEqual(["/api/cash/orders/o1/pay"]);
    const busy = screen.getByRole("button", { name: "Recording…" });
    expect((busy as HTMLButtonElement).disabled).toBe(true);
    releasePost();
    await vi.waitFor(() => expect(onFlash).toHaveBeenCalledWith("Payment recorded"));
    expect(posts).toHaveLength(1);
  });
});
