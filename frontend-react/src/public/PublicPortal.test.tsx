import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import PublicPortal from "./PublicPortal";

const HOME = {
  shop_name: "Fixit", client_name: "Ana Lima", logo_data: null, brand_color: null,
  estimates: [{ id: "e1", order_number: "E-7", is_estimate: true, stage_label: "Sent", total: 120,
    total_converted: 120, currency_symbol: "$", device: "Pixel 8", awaiting_approval: true, open_recommendations: 0 }],
  work_orders: [{ id: "w1", order_number: "WO-3", is_estimate: false, stage_label: "In repair", total: 80,
    total_converted: 80, currency_symbol: "$", device: "iPhone 14", awaiting_approval: false, open_recommendations: 0,
    amount_paid: 80, balance_due: 0, paid_in_full: true,
    warranty_until: "2027-01-05T00:00:00" }],
  counts: { work_orders: 1, estimates: 1, under_warranty: 1 },
};
const JOB = {
  id: "w1", order_number: "WO-3", is_estimate: false, stage_label: "In repair", stage_index: 3,
  stages: [{ label: "Received" }, { label: "Diagnosing" }, { label: "Waiting" }, { label: "In repair" }, { label: "Ready" }, { label: "Done" }],
  shop_name: "Fixit", device: "iPhone 14", total: 80, currency_symbol: "$", public_token: "trk1",
  lines: [{ label: "Screen", qty: 1, amount: 80 }], recommendations: [], updates: [], timeline: [],
  allow_messages: true, amount_paid: 80, balance_due: 0, paid_in_full: true,
};

let calls: { url: string; init?: RequestInit }[] = [];
beforeEach(() => {
  calls = [];
  history.replaceState(null, "", "/portal/ptok");
  window.scrollTo = vi.fn() as unknown as typeof window.scrollTo;
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, init });
    const body = url === "/api/public/portal/ptok" ? HOME
      : url === "/api/public/portal/ptok/order/w1" ? JOB
      : url.endsWith("/message") ? { ok: true } : null;
    return { ok: body !== null, json: async () => body } as Response;
  }));
});
afterEach(() => vi.unstubAllGlobals());

describe("PublicPortal", () => {
  it("lists every job, with what needs the customer first", async () => {
    render(<PublicPortal token="ptok" />);
    expect(await screen.findByText("Hi Ana")).toBeInTheDocument();
    expect(screen.getByText("Needs your attention")).toBeInTheDocument();
    expect(screen.getByText("Ready for you to review")).toBeInTheDocument();
    expect(screen.getByText("iPhone 14")).toBeInTheDocument();
    expect(screen.getByText(/Under warranty until/)).toBeInTheDocument();
    expect(screen.getByText("Paid in full")).toBeInTheDocument();     // F2: a settled job says so
  });

  it("a paid job says Paid in full; a part-paid one names the balance", async () => {
    const user = userEvent.setup();
    render(<PublicPortal token="ptok" />);
    await user.click(await screen.findByText("iPhone 14"));
    expect(await screen.findByText("What we're doing")).toBeInTheDocument();
    expect(screen.getAllByText("Paid in full").length).toBeGreaterThan(0);
    Object.assign(JOB, { amount_paid: 30, balance_due: 50, paid_in_full: false });
    try {
      await user.click(screen.getByText("All your repairs"));
      await user.click(await screen.findByText("iPhone 14"));
      expect((await screen.findAllByText("Balance due $50")).length).toBeGreaterThan(0);
      expect(screen.queryByText("Paid in full")).toBeNull();
    } finally {
      Object.assign(JOB, { amount_paid: 80, balance_due: 0, paid_in_full: true });
    }
  });

  it("opens a job in the track view and sends actions to the portal API", async () => {
    const user = userEvent.setup();
    render(<PublicPortal token="ptok" />);
    await user.click(await screen.findByText("iPhone 14"));
    expect(location.search).toBe("?job=w1");
    expect(await screen.findByText("What we're doing")).toBeInTheDocument();
    await user.type(screen.getByPlaceholderText("Ask a question or add a note…"), "When ready?");
    await user.click(screen.getByText("Send"));
    const sent = calls.find((c) => c.url.endsWith("/message"));
    expect(sent?.url).toBe("/api/public/portal/ptok/order/w1/message");
    await user.click(screen.getByText("All your repairs"));
    expect(await screen.findByText("Hi Ana")).toBeInTheDocument();
  });

  it("says the link is invalid when the portal token is unknown", async () => {
    render(<PublicPortal token="nope" />);
    expect(await screen.findByText("Link not found")).toBeInTheDocument();
  });
});
