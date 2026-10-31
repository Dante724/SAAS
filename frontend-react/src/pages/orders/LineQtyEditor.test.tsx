// One-click add: picking from search adds the line with quantity 1 (no extra "Add" step), and the
// line's own "1 × €12" opens a small editor for quantity and discount that saves only what changed.
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

vi.mock("../../components/Shell", () => ({ M: ({ n }: { n: string }) => <i data-icon={n} />, default: ({ children }: any) => <div>{children}</div> }));
vi.mock("../memberships/WOPlan", () => ({ PlanBanner: () => null, CheckoutOffer: () => null }));
vi.mock("../../components/sense/Sense", () => ({ DeferredWork: () => null, MarginNote: () => null, ReadyWhy: () => null, ServiceDue: () => null, TogetherChips: () => null }));
vi.mock("./RecommendationsPanel", () => ({ default: () => null }));
vi.mock("../../components/CatalogPicker", () => ({
  default: ({ onPick }: any) => <button onClick={() => onPick({ kind: "product", ref_id: "p1", name: "Thermal Paste", price: 2, available: 0 })}>pick Thermal Paste</button>,
}));

import { ServicesTab } from "./WODrawer";

const reply = (body: unknown) => ({ ok: true, status: 200, headers: new Headers(), json: async () => body, text: async () => JSON.stringify(body) }) as Response;
let calls: { m: string; url: string; body?: any }[] = [];
beforeEach(() => {
  calls = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ m: init?.method || "GET", url, body: init?.body ? JSON.parse(String(init.body)) : undefined });
    return reply({});
  }));
});
afterEach(() => vi.unstubAllGlobals());

const line = { id: "l9", description: "Thermal Paste", kind: "product", line_type: "part", qty: 1, unit_price: 2, total: 2 };

describe("adding and adjusting lines", () => {
  it("adds the picked item at once with quantity 1, and says when it's out of stock", async () => {
    const flash = vi.fn();
    render(<ServicesTab orderId="o1" lines={[]} total={0} onFlash={flash} />);
    await userEvent.click(screen.getByRole("button", { name: "pick Thermal Paste" }));
    await waitFor(() => expect(calls.some((c) => c.m === "POST" && c.url.endsWith("/orders/o1/lines"))).toBe(true));
    const body = calls.find((c) => c.m === "POST")!.body;
    expect(body).toMatchObject({ ref_id: "p1", qty: 1, kind: "product" });
    expect(body.discount).toBeUndefined();
    expect(flash).toHaveBeenCalledWith(expect.stringMatching(/Thermal Paste added\. It's out of stock/));
    expect(screen.queryByRole("button", { name: "Add" })).toBeNull();
  });

  it("changes quantity and discount from the line and sends only what changed", async () => {
    render(<ServicesTab orderId="o1" lines={[line] as any} total={2} onFlash={() => {}} />);
    await userEvent.click(screen.getByRole("button", { name: "Change quantity or discount for Thermal Paste" }));
    await userEvent.click(await screen.findByRole("button", { name: "Increase quantity" }));
    await userEvent.click(screen.getByRole("button", { name: "Done" }));
    await waitFor(() => expect(calls.some((c) => c.m === "PATCH")).toBe(true));
    expect(calls.find((c) => c.m === "PATCH")).toMatchObject({ url: expect.stringContaining("/orders/o1/lines/l9"), body: { qty: 2 } });

    calls = [];
    await userEvent.click(screen.getByRole("button", { name: "Change quantity or discount for Thermal Paste" }));
    await userEvent.type(await screen.findByRole("textbox", { name: "Discount percent" }), "10");
    await userEvent.click(screen.getByRole("button", { name: "Done" }));
    await waitFor(() => expect(calls.find((c) => c.m === "PATCH")?.body).toEqual({ discount: 10, discount_type: "pct" }));
  });
});
