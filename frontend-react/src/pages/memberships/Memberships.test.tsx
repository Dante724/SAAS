// Memberships: the member card, adding a plan, the Contacts list filters, the plan editor and the
// checkout offer.
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

let perms: Record<string, boolean> = {};
vi.mock("../../context/Me", async (orig) => {
  const real: any = await orig();
  return { ...real, useMe: () => ({ can: (c: string) => perms[c] !== false }) };
});
import { MemberCard, MembershipsCard } from "./shared";
import MembershipsView from "./MembershipsView";
import PlansTab from "./PlansTab";
import { CheckoutOffer } from "./WOPlan";

const reply = (body: unknown, status = 200) => ({ ok: status < 400, status, statusText: "", headers: new Headers({ "content-type": "application/json" }), json: async () => body, text: async () => JSON.stringify(body) }) as Response;
const member = (over: Record<string, unknown> = {}) => ({
  id: "m1", plan_id: "p1", plan: "Boat Care", color: "#0059ba", client_id: "c1", client: "Sam Rivera", device_id: "d1",
  asset: "Bayliner VR5", billing: "yearly", status: "active", renews: true, renews_on: "2027-10-29", starts_on: "2026-10-29",
  period_end: "2027-10-29", unpaid: false, price: 449, owed: 0,
  items: [{ id: "i1", kind: "service", text: "Haul-out × 2", left: 1, of: 2 }, { id: "i2", kind: "discount", text: "10% off labour" }],
  ...over,
});
const plan = { id: "p1", name: "Boat Care", description: "", asset_group_ids: [], price_monthly: 45, price_yearly: 449, term: "rolling",
  presale_days: 60, late_join: "prorated", color: "#0059ba", active: true, members: 3,
  items: [{ id: "i1", kind: "service", service_id: "s1", label: "Haul-out", qty: 2, pct: 0, applies_to: "labour", rollover: false, text: "Haul-out × 2" }] };

let calls: { method: string; url: string; body?: any }[] = [];
let routes: Record<string, unknown> = {};
beforeEach(() => {
  perms = {}; calls = [];
  routes = {};
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    const method = init?.method || "GET";
    calls.push({ method, url, body: init?.body ? JSON.parse(String(init.body)) : undefined });
    const hit = Object.keys(routes).sort((a, b) => b.length - a.length).find((k) => url.includes(k));
    if (method !== "GET" && url.endsWith("/memberships")) return reply({ ...member(), invoice_id: "inv1" }, 201);
    return reply(hit ? routes[hit] : { items: [] });
  }));
});
afterEach(() => vi.unstubAllGlobals());

describe("MemberCard", () => {
  it("shows what's left at a glance, not a table", () => {
    render(<MemberCard m={member() as any} />);
    expect(screen.getByText("Boat Care")).toBeInTheDocument();
    expect(screen.getByText("Active")).toBeInTheDocument();
    expect(screen.getByText("1 of 2 left")).toBeInTheDocument();
    expect(screen.getByText("10% off labour")).toBeInTheDocument();
    expect(screen.getByText(/Bayliner VR5 · Yearly · renews/)).toBeInTheDocument();
  });
  it("says when a plan is paused for an unpaid bill", () => {
    render(<MemberCard m={member({ status: "paused", owed: 45 }) as any} />);
    expect(screen.getByText("Paused · unpaid")).toBeInTheDocument();
    expect(screen.getByText("$45 due")).toBeInTheDocument();
  });
});

describe("MembershipsCard", () => {
  it("offers one clear 'Add a plan' when there's none, and adds it in one step", async () => {
    routes = { "/memberships/for": { live: [], former: [] }, "/memberships/plans": { items: [plan] } };
    render(<MembershipsCard clientId="c1" deviceId="d1" />);
    await userEvent.click(await screen.findByRole("button", { name: /Add a membership plan/ }));
    await userEvent.click(await screen.findByRole("button", { name: /\$45 a month/ }));
    await userEvent.click(screen.getByRole("button", { name: /Add Boat Care · \$45\/mo/ }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.url.endsWith("/memberships"))).toBe(true));
    expect(calls.find((c) => c.method === "POST")!.body).toMatchObject({ plan_id: "p1", client_id: "c1", device_id: "d1", billing: "monthly" });
  });
  it("shows a former member quietly, with Re-join", async () => {
    routes = { "/memberships/for": { live: [], former: [member({ status: "cancelled", ended_on: "2026-03-12" })] } };
    render(<MembershipsCard clientId="c1" />);
    expect(await screen.findByText(/Former Boat Care member · ended/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Re-join" })).toBeInTheDocument();
  });
  it("hides selling from staff who can't sell plans", async () => {
    perms = { can_sell_memberships: false };
    routes = { "/memberships/for": { live: [], former: [] } };
    const { container } = render(<MembershipsCard clientId="c1" />);
    await waitFor(() => expect(calls.length).toBeGreaterThan(0));
    expect(container).toBeEmptyDOMElement();
  });
});

describe("MembershipsView", () => {
  it("filters by the chips and shows counts", async () => {
    routes = { "/memberships?": { items: [member()], counts: { all: 3, unpaid: 1 } } };
    render(<MembershipsView />);
    expect(await screen.findByText("Sam Rivera")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /^Unpaid/ }));
    await waitFor(() => expect(calls.some((c) => c.url.includes("view=unpaid"))).toBe(true));
  });
});

describe("PlansTab", () => {
  it("lists plans as cards and saves a new one with its included items", async () => {
    routes = { "/memberships/plans": { items: [plan] }, "/catalog/services": { items: [{ id: "s1", name: "Haul-out" }] } };
    render(<PlansTab />);
    expect(await screen.findByText("Haul-out × 2")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /New plan/ }));
    await userEvent.type(screen.getByLabelText("Plan name"), "Auto Care");
    await userEvent.type(screen.getByLabelText("Yearly price"), "199");
    await userEvent.click(screen.getByRole("button", { name: "+ Member discount" }));
    await userEvent.click(screen.getByRole("button", { name: "Save plan" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.url.endsWith("/memberships/plans"))).toBe(true));
    const sent = calls.find((c) => c.method === "POST")!.body;
    expect(sent).toMatchObject({ name: "Auto Care", price_yearly: 199, items: [{ kind: "discount", pct: 10 }] });
  });
});

describe("CheckoutOffer", () => {
  it("shows the saving, and Not now snoozes it", async () => {
    routes = { "/memberships/order/o1": { plans: [], coverable: 0, can_apply: true, client_id: "c1", device_id: "d1",
      offer: { plan_id: "p1", name: "Boat Care", saving: 300, price_yearly: 449, includes: ["Haul-out × 1"] } } };
    render(<CheckoutOffer orderId="o1" onJoined={() => {}} />);
    expect(await screen.findByText("$300 today")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Join · $449/yr" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Not now" }));
    expect(screen.queryByText("$300 today")).toBeNull();
    expect(calls.some((c) => c.method === "POST" && c.url.endsWith("/memberships/order/o1/snooze"))).toBe(true);
  });
});
