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
    await u.click(await screen.findByRole("button", { name: /Take payment/ }));
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

describe("InvoicesTab payments carry an Idempotency-Key", () => {
  const keyOf = (init?: RequestInit) => (init?.headers as Record<string, string> | undefined)?.["Idempotency-Key"];
  for (const kind of ["pay", "prepay"] as const) {
    it(`${kind}: a retry after a failure re-sends the same key; the next payment gets a new one`, async () => {
      const sent: { url: string; key?: string }[] = [];
      let fail = true;
      vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
        if (init?.method === "POST") {
          sent.push({ url, key: keyOf(init) });
          if (fail) { fail = false; return { ok: false, status: 502, headers: new Headers(), json: async () => ({}), text: async () => "bad gateway" } as Response; }
          return reply({ ok: true });
        }
        if (url.includes("/payment-context")) return reply({ order_total: 100, already_paid: 0, prepaid: 0, remaining: 100, accounts_by_method: { cash: [{ id: "acc1", name: "Till" }] } });
        if (url.includes("/invoices")) return reply({ invoices: [], payments: [] });
        return reply({});
      }));
      const u = userEvent.setup();
      const onFlash = vi.fn();
      render(<InvoicesTab orderId="o1" total={100} onFlash={onFlash} />);
      const open = async () => {
        await u.click(await screen.findByRole("button", { name: kind === "pay" ? /Take payment/ : /Take prepayment/ }));
        const submit = await screen.findByRole("button", { name: kind === "pay" ? "Record payment" : "Record prepayment" });
        if (kind === "prepay") await u.type(screen.getAllByRole("spinbutton").at(-1)!, "30");
        return submit;
      };
      await u.click(await open());
      await vi.waitFor(() => expect(onFlash).toHaveBeenCalledWith(expect.stringMatching(/^Failed/)));
      await u.click(screen.getByRole("button", { name: kind === "pay" ? "Record payment" : "Record prepayment" }));
      await vi.waitFor(() => expect(onFlash).toHaveBeenCalledWith("Payment recorded"));
      expect(sent).toHaveLength(2);
      expect(sent[0].url).toBe(`/api/cash/orders/o1/${kind}`);
      expect(sent[0].key).toMatch(/^web-/);
      expect(sent[1].key).toBe(sent[0].key);                 // the retry can't post twice
      await u.click(await open());
      await vi.waitFor(() => expect(sent).toHaveLength(3));
      expect(sent[2].key).not.toBe(sent[0].key);             // a new payment is a new action
    });
  }
});
