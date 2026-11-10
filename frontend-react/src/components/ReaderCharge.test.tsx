// F2/F3 reader status UI: send → "Waiting for card…" (polled) → approved / declined / canceled.
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, act } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

vi.mock("./Shell", () => ({ M: ({ n }: { n: string }) => <i data-icon={n} /> }));

import ReaderCharge, { POLL_MS } from "./ReaderCharge";

const reply = (body: unknown, status = 200) => ({ ok: status < 400, status, headers: new Headers(), json: async () => body, text: async () => JSON.stringify(body) }) as Response;
const READER = { id: "t1", provider: "stripe", label: "Front desk", test_mode: true };
const charge = (status: string, extra = {}) => ({
  id: "c1", terminal_id: "t1", provider: "stripe", amount: 40, status, test_mode: true,
  status_label: { pending: "Waiting for card", succeeded: "Approved", declined: "Declined", canceled: "Canceled" }[status], ...extra });

let calls: { method: string; url: string; body?: any; key?: string }[] = [];
let readers: unknown[] = [READER];
let polled: string[] = [];
let chargeReply: () => Response = () => reply(charge("pending"));

beforeEach(() => {
  calls = []; readers = [READER]; polled = ["pending", "succeeded"];
  chargeReply = () => reply(charge("pending"));
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    const method = init?.method || "GET";
    calls.push({ method, url, body: init?.body ? JSON.parse(String(init.body)) : undefined,
                 key: (init?.headers as Record<string, string> | undefined)?.["Idempotency-Key"] });
    if (url.endsWith("/payments/terminal/readers")) return reply({ items: readers });
    if (url.endsWith("/payments/terminal/charge")) return chargeReply();
    if (url.endsWith("/charges/c1") && method === "GET") return reply(charge(polled.shift() || "succeeded"));
    if (url.endsWith("/charges/c1/cancel")) return reply(charge("canceled", { error: "Canceled by staff" }));
    if (url.endsWith("/charges/c1/simulate")) return reply(charge("declined", { error: "Your card was declined." }));
    if (url.endsWith("/readers/t1/clear")) return reply({ ok: true, canceled: 1 });
    return reply({});
  }));
});
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

describe("ReaderCharge", () => {
  it("sends the order amount, shows waiting, polls, then reports approved once", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const u = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    const onApproved = vi.fn(); const onClose = vi.fn();
    render(<ReaderCharge orderId="o1" amount={40} accountId="acc" onApproved={onApproved} onClose={onClose} />);
    await u.click(await screen.findByRole("button", { name: /Send .*40 to reader/ }));
    expect(calls.find((c) => c.url.endsWith("/terminal/charge"))?.body).toEqual({ terminal_id: "t1", account_id: "acc", order_id: "o1", amount: 40 });
    expect(await screen.findByText("Waiting for card…")).toBeInTheDocument();
    expect(screen.getByText("Test mode")).toBeInTheDocument();
    expect(screen.getByTestId("reader-spinner")).toBeInTheDocument();             // a small calm spinner…
    expect(document.querySelector(".animate-pulse")).toBeNull();                  // …not a pulsing icon
    expect(screen.getByRole("button", { name: "Close" })).toBeDisabled();          // can't walk away mid-charge
    await act(async () => { await vi.advanceTimersByTimeAsync(POLL_MS); });
    expect(screen.getByText("Waiting for card…")).toBeInTheDocument();
    await act(async () => { await vi.advanceTimersByTimeAsync(POLL_MS); });
    expect(await screen.findByText("Approved")).toBeInTheDocument();
    expect(onApproved).toHaveBeenCalledTimes(1);
    await act(async () => { await vi.advanceTimersByTimeAsync(POLL_MS * 3); });
    expect(calls.filter((c) => c.url.endsWith("/charges/c1")).length).toBe(2);   // polling stopped
    await u.click(screen.getByRole("button", { name: "Done" }));
    expect(onClose).toHaveBeenCalledWith(expect.objectContaining({ status: "succeeded" }));
  });

  it("a sale is charged by id (the server takes its total), and staff can cancel", async () => {
    const u = userEvent.setup();
    render(<ReaderCharge saleId="s1" amount={25} onApproved={vi.fn()} onClose={vi.fn()} />);
    await u.click(await screen.findByRole("button", { name: /to reader/ }));
    expect(calls.find((c) => c.url.endsWith("/terminal/charge"))?.body).toEqual({ terminal_id: "t1", account_id: null, sale_id: "s1" });
    await u.click(await screen.findByRole("button", { name: "Cancel charge" }));
    expect(await screen.findByText("Canceled")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });

  it("test card decline shows the reason and offers a retry", async () => {
    const u = userEvent.setup();
    render(<ReaderCharge orderId="o1" amount={40} onApproved={vi.fn()} onClose={vi.fn()} />);
    await u.click(await screen.findByRole("button", { name: /to reader/ }));
    await u.click(await screen.findByRole("button", { name: "Decline" }));
    expect(await screen.findByText("Declined")).toBeInTheDocument();
    expect(screen.getByText("Your card was declined.")).toBeInTheDocument();
    expect(calls.find((c) => c.url.endsWith("/simulate"))?.body).toEqual({ outcome: "decline" });
    await u.click(screen.getByRole("button", { name: "Try again" }));
    expect(screen.getByRole("button", { name: /to reader/ })).toBeInTheDocument();
  });

  it("when status checks fail the cashier is told and can still close", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const u = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    const onClose = vi.fn();
    vi.mocked(fetch).mockImplementation(async (url: any, init?: RequestInit) => {
      if (String(url).endsWith("/payments/terminal/readers")) return reply({ items: readers });
      if (String(url).endsWith("/payments/terminal/charge")) return reply(charge("pending"));
      return reply({ detail: "Stripe: unreachable" }, 502);
    });
    render(<ReaderCharge orderId="o1" amount={40} onApproved={vi.fn()} onClose={onClose} />);
    await u.click(await screen.findByRole("button", { name: /to reader/ }));
    expect(screen.getByRole("button", { name: "Close" })).toBeDisabled();
    await act(async () => { await vi.advanceTimersByTimeAsync(POLL_MS); });
    expect(await screen.findByText(/Can't check the reader right now/)).toBeInTheDocument();
    await u.click(screen.getAllByRole("button", { name: "Close" })[0]);
    expect(onClose).toHaveBeenCalledWith(expect.objectContaining({ status: "pending" }));
  });

  it("no readers: says where to add one", async () => {
    readers = [];
    render(<ReaderCharge orderId="o1" amount={40} onApproved={vi.fn()} onClose={vi.fn()} />);
    expect(await screen.findByText(/No card reader yet/)).toBeInTheDocument();
  });

  it("a busy reader can be cleared", async () => {
    const u = userEvent.setup();
    chargeReply = () => reply({ detail: "A charge is already waiting on this reader or document. Cancel it first." }, 409);
    render(<ReaderCharge orderId="o1" amount={40} onApproved={vi.fn()} onClose={vi.fn()} />);
    await u.click(await screen.findByRole("button", { name: /to reader/ }));
    await u.click(await screen.findByRole("button", { name: "Cancel the waiting charge" }));
    expect(calls.some((c) => c.url.endsWith("/readers/t1/clear") && c.method === "POST")).toBe(true);
    expect(screen.queryByText(/already waiting/)).not.toBeInTheDocument();
  });

  it("the reader lost a same-moment race (409 in progress): it can be cleared too", async () => {
    const u = userEvent.setup();
    chargeReply = () => reply({ detail: "A charge is already in progress on this reader. Finish or cancel it first." }, 409);
    render(<ReaderCharge orderId="o1" amount={40} onApproved={vi.fn()} onClose={vi.fn()} />);
    await u.click(await screen.findByRole("button", { name: /to reader/ }));
    expect(await screen.findByRole("button", { name: "Cancel the waiting charge" })).toBeInTheDocument();
  });

  it("a lost answer (gateway 502) is retried with the same key, so the server answers with the charge it already started", async () => {
    // The request may have reached the server before the gateway gave up. The retry carries the
    // same Idempotency-Key, so the server replays the first charge (c1) instead of sending another.
    const u = userEvent.setup();
    chargeReply = () => reply({ detail: "Bad gateway" }, 502);
    render(<ReaderCharge orderId="o1" amount={40} onApproved={vi.fn()} onClose={vi.fn()} />);
    await u.click(await screen.findByRole("button", { name: /to reader/ }));
    await screen.findByText(/Bad gateway/);
    chargeReply = () => reply(charge("pending"));
    await u.click(await screen.findByRole("button", { name: /to reader/ }));
    expect(await screen.findByText("Waiting for card…")).toBeInTheDocument();
    const sent = calls.filter((c) => c.url.endsWith("/terminal/charge"));
    expect(sent).toHaveLength(2);
    expect(sent[0].key).toBeTruthy();
    expect(sent[1].key).toBe(sent[0].key);
  });

  it("a charge the reader didn't confirm stays waiting with the note, and nothing is sent again", async () => {
    // The server couldn't tell whether the reader got it: it answers with the charge, waiting, and
    // a note. The screen follows that charge (poll / cancel) instead of offering to send another.
    const u = userEvent.setup();
    const note = "The reader didn't confirm it received this charge. Watch the reader: it may still take the card.";
    chargeReply = () => reply(charge("pending", { error: note }));
    polled = ["pending", "pending", "pending"];
    render(<ReaderCharge orderId="o1" amount={40} onApproved={vi.fn()} onClose={vi.fn()} />);
    await u.click(await screen.findByRole("button", { name: /to reader/ }));
    expect(await screen.findByText("Waiting for card…")).toBeInTheDocument();
    expect(screen.getByText(/didn't confirm it received this charge/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /to reader/ })).toBeNull();
    expect(screen.getByRole("button", { name: /Cancel/ })).toBeInTheDocument();
    expect(calls.filter((c) => c.url.endsWith("/terminal/charge"))).toHaveLength(1);
  });
});
