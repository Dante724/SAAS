// Card readers are switched off (server CARD_READERS_ENABLED, /auth/me card_readers): the shop has
// no reader and can't connect one. No reader UI renders anywhere and nothing asks the reader API;
// cash and card recorded by hand still go through. With the switch on, the reader parts come back.
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

vi.mock("./components/Shell", () => ({ M: ({ n }: { n: string }) => <i data-icon={n} />, default: ({ children }: any) => <div>{children}</div> }));
const readers = vi.hoisted(() => ({ on: false }));
vi.mock("./lib/cardReaders", () => ({ useCardReaders: () => readers.on }));

import CardPaymentsList from "./components/CardPaymentsList";
import CardReadersCard from "./pages/settings/CardReadersCard";
import { InvoicesTab } from "./pages/orders/WODrawer";

const reply = (body: unknown) => ({ ok: true, status: 200, headers: new Headers(), json: async () => body, text: async () => JSON.stringify(body) }) as Response;
let calls: { method: string; url: string }[] = [];
const prov = (configured: boolean) => ({ configured, test_mode: true, live_refused: false });
const CFG = {
  providers: { stripe: prov(false), square: prov(false), simulated: prov(true) }, test_mode: true,
  square_sandbox_devices: [], stripe_test_registration_code: "simulated-wpe", webhook_paths: { stripe: "/s", square: "/q" },
};

beforeEach(() => {
  calls = []; readers.on = false;
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ method: init?.method || "GET", url });
    if (init?.method === "POST") return reply({ ok: true });
    if (url.includes("/payment-context")) return reply({ order_total: 100, already_paid: 0, prepaid: 0, remaining: 100,
      accounts_by_method: { cash: [{ id: "acc1", name: "Till" }], card: [{ id: "acc2", name: "Card" }] } });
    if (url.includes("/invoices")) return reply({ invoices: [], payments: [] });
    if (url.includes("/payments/terminal/config")) return reply(CFG);
    if (url.includes("/payments/terminal/readers")) return reply({ items: [] });
    if (url.includes("/payments/terminal/charges")) return reply({ items: [{ id: "c1", amount: 40, refunded_amount: 0, status: "succeeded", terminal_label: "Front desk" }] });
    return reply({ items: [] });
  }));
});
afterEach(() => vi.unstubAllGlobals());

const readerCalls = () => calls.filter((c) => c.url.includes("/payments/terminal"));
const settle = () => new Promise((r) => setTimeout(r, 30));

describe("card readers off", () => {
  it("Settings › Integrations: the card readers card isn't there", async () => {
    const { container } = render(<CardReadersCard />);
    await settle();
    expect(container).toBeEmptyDOMElement();
    expect(readerCalls()).toHaveLength(0);
  });

  it("card-reader payments list renders nothing and asks nothing", async () => {
    const { container } = render(<CardPaymentsList sales />);
    await settle();
    expect(container).toBeEmptyDOMElement();
    expect(readerCalls()).toHaveLength(0);
  });

  it("work order: card is recorded by hand, with no Charge on reader", async () => {
    const u = userEvent.setup();
    const onFlash = vi.fn();
    render(<InvoicesTab orderId="o1" total={100} onFlash={onFlash} />);
    await u.click(await screen.findByRole("button", { name: /Take payment/ }));
    const method = await screen.findByDisplayValue("Cash");
    await u.selectOptions(method, "card");
    expect(screen.queryByRole("button", { name: /Charge on reader/ })).toBeNull();
    await u.click(screen.getByRole("button", { name: "Record payment" }));
    await vi.waitFor(() => expect(onFlash).toHaveBeenCalledWith("Payment recorded"));
    expect(calls.filter((c) => c.method === "POST").map((c) => c.url)).toEqual(["/api/cash/orders/o1/pay"]);
    expect(readerCalls()).toHaveLength(0);
  });
});

describe("card readers on", () => {
  it("the work order offers Charge on reader again", async () => {
    readers.on = true;
    const u = userEvent.setup();
    render(<InvoicesTab orderId="o1" total={100} onFlash={vi.fn()} />);
    await u.click(await screen.findByRole("button", { name: /Take payment/ }));
    await u.selectOptions(await screen.findByDisplayValue("Cash"), "card");
    expect(screen.getByRole("button", { name: /Charge on reader/ })).toBeInTheDocument();
  });

  it("the settings card and the payments list come back", async () => {
    readers.on = true;
    render(<><CardReadersCard /><CardPaymentsList sales /></>);
    expect(await screen.findByText(/No card readers yet/)).toBeInTheDocument();
    expect(await screen.findByText("Card reader payments")).toBeInTheDocument();
  });
});
