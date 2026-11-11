// Council M13: adding a line to a job takes stock, so a retried add must not add it twice. The
// add sends an Idempotency-Key; a retry after a lost connection re-sends the same key (the
// server answers with the first add if it got there); a deliberate second add of the same item
// once the first went through gets a new key.
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

vi.mock("../../components/Shell", () => ({ M: ({ n }: { n: string }) => <i data-icon={n} />, default: ({ children }: any) => <div>{children}</div> }));
vi.mock("../memberships/WOPlan", () => ({ PlanBanner: () => null, CheckoutOffer: () => null }));
vi.mock("../../components/sense/Sense", () => ({ DeferredWork: () => null, MarginNote: () => null, ReadyWhy: () => null, ServiceDue: () => null, TogetherChips: () => null }));
vi.mock("./RecommendationsPanel", () => ({ default: () => null }));
vi.mock("../../components/CatalogPicker", () => ({
  default: ({ onPick }: any) => <button onClick={() => onPick({ kind: "service", ref_id: "s1", name: "Screen fit", price: 40 })}>pick Screen fit</button>,
}));

import { ServicesTab } from "./WODrawer";
import { NO_CONNECTION } from "../../api/client";

const reply = (body: unknown) => ({ ok: true, status: 200, headers: new Headers(), json: async () => body, text: async () => JSON.stringify(body) }) as Response;
const keyOf = (init?: RequestInit) => (init?.headers as Record<string, string> | undefined)?.["Idempotency-Key"];
let adds: (string | undefined)[] = [];
let failNext = 0;

beforeEach(() => {
  adds = []; failNext = 0;
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    if (init?.method === "POST" && url.endsWith("/orders/o1/lines")) {
      adds.push(keyOf(init));
      if (failNext > 0) { failNext--; throw new TypeError("Failed to fetch"); }
      return reply({ ok: true, line_id: `l${adds.length}` });
    }
    return reply({});
  }));
});
afterEach(() => vi.unstubAllGlobals());

describe("ServicesTab add line", () => {
  it("a retry after a lost connection re-sends the same key; the next add of the item gets a new one", async () => {
    const u = userEvent.setup();
    const flash = vi.fn();
    render(<ServicesTab orderId="o1" lines={[]} total={0} onFlash={flash} />);
    failNext = 1;
    await u.click(screen.getByRole("button", { name: "pick Screen fit" }));
    await waitFor(() => expect(flash).toHaveBeenCalledWith(NO_CONNECTION));

    await u.click(screen.getByRole("button", { name: "pick Screen fit" }));   // the retry
    await waitFor(() => expect(flash).toHaveBeenCalledWith("Screen fit added"));
    expect(adds).toHaveLength(2);
    expect(adds[0]).toBeTruthy();
    expect(adds[1]).toBe(adds[0]);

    await u.click(screen.getByRole("button", { name: "pick Screen fit" }));   // a second one on purpose
    await waitFor(() => expect(adds).toHaveLength(3));
    expect(adds[2]).toBeTruthy();
    expect(adds[2]).not.toBe(adds[0]);
  });
});
