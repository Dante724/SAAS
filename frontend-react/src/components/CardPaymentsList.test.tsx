// Card-reader payments list: approved charges for an order, with a Refund that calls the provider.
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

vi.mock("./Shell", () => ({ M: ({ n }: { n: string }) => <i data-icon={n} /> }));
vi.mock("../lib/dialog", () => ({ promptDialog: vi.fn(async () => "15") }));

const readers = vi.hoisted(() => ({ on: true }));   // card readers are off by default; these test the feature
vi.mock("../lib/cardReaders", () => ({ useCardReaders: () => readers.on }));

import CardPaymentsList from "./CardPaymentsList";

const reply = (body: unknown) => ({ ok: true, status: 200, headers: new Headers(), json: async () => body, text: async () => JSON.stringify(body) }) as Response;
let items: unknown[] = [];
let posts: { url: string; body: any; key?: string }[] = [];

beforeEach(() => {
  posts = [];
  items = [{ id: "c1", terminal_id: "t1", provider: "stripe", amount: 40, refunded_amount: 0, status: "succeeded", status_label: "Approved", terminal_label: "Front desk", created_at: "2026-10-20T10:00:00" }];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    if (init?.method === "POST") { posts.push({ url, body: JSON.parse(String(init.body)), key: (init.headers as Record<string, string>)?.["Idempotency-Key"] }); items = [{ ...(items[0] as object), refunded_amount: 15 }]; return reply({}); }
    return reply({ items });
  }));
});
afterEach(() => vi.unstubAllGlobals());

describe("CardPaymentsList", () => {
  it("lists an approved order charge and refunds part of it", async () => {
    const u = userEvent.setup();
    const onChanged = vi.fn();
    render(<CardPaymentsList orderId="o1" onChanged={onChanged} />);
    expect(await screen.findByText("Front desk")).toBeInTheDocument();
    await u.click(screen.getByRole("button", { name: "Refund" }));
    expect(posts).toEqual([{ url: "/api/payments/terminal/charges/c1/refund", body: { amount: 15 }, key: expect.stringMatching(/^web-/) }]);
    expect(await screen.findByText(/refunded/)).toBeInTheDocument();
    expect(onChanged).toHaveBeenCalled();
  });

  it("sends an Idempotency-Key per refund, so a genuine second refund isn't taken for a repeat", async () => {
    const u = userEvent.setup();
    render(<CardPaymentsList orderId="o1" />);
    expect(await screen.findByText("Front desk")).toBeInTheDocument();
    await u.click(screen.getByRole("button", { name: "Refund" }));
    await screen.findByText(/refunded/);
    await u.click(screen.getByRole("button", { name: "Refund" }));
    await vi.waitFor(() => expect(posts).toHaveLength(2));
    expect(posts[0].key).toBeTruthy();
    expect(posts[1].key).toBeTruthy();
    expect(posts[1].key).not.toBe(posts[0].key);
  });

  it("an order with no reader payments shows nothing; the sales list says so", async () => {
    items = [];
    const { container } = render(<CardPaymentsList orderId="o1" />);
    expect(container).toBeEmptyDOMElement();
    await vi.waitFor(() => expect(vi.mocked(fetch)).toHaveBeenCalled());
    await new Promise((r) => setTimeout(r, 20));
    expect(container).toBeEmptyDOMElement();
    render(<CardPaymentsList sales />);
    expect(await screen.findByText("No card reader payments yet.")).toBeInTheDocument();
  });
});
