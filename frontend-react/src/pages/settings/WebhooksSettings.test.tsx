// whh1 B15: webhooks card — multi-select events, secret shown once, delivery log, redeliver, test.
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

vi.mock("../../lib/dialog", () => ({ confirmDialog: async () => true, alertDialog: async () => {} }));
import WebhooksSettings, { deliveryState } from "./WebhooksSettings";

const reply = (body: unknown, status = 200) => ({ ok: status < 400, status, statusText: "", headers: new Headers({ "content-type": "application/json" }), json: async () => body, text: async () => JSON.stringify(body) }) as Response;
const CATALOGUE = [
  { name: "order.created", description: "A work order was created, in the app or through the API." },
  { name: "invoice.paid", description: "An invoice was paid in full." },
];
let endpoints: any[] = [];
let calls: { method: string; url: string; body: any }[] = [];
beforeEach(() => {
  calls = [];
  endpoints = [{ id: "e1", url: "https://hooks.example.com/in", description: "Accounting", events: "*", active: true, secret_hint: "whsec_…abcd" }];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    const method = init?.method || "GET";
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    calls.push({ method, url, body });
    if (method === "POST" && url.endsWith("/api/webhooks")) return reply({ id: "e2", ...body, active: true, secret: "whsec_brandnewsecret" }, 201);
    if (method === "POST" && url.includes("/redeliver")) return reply({ id: "d1", status: "sent", result: "sent" });
    if (method === "POST" && url.includes("/test")) return reply({ id: "t1", status: "sent", response_code: 200 });
    if (url.includes("/deliveries/d1")) return reply({ id: "d1", event: "order.created", status: "failed", attempts: 2, payload: { id: "evt_1", event: "order.created" } });
    if (url.includes("/deliveries")) return reply({ items: [{ id: "d1", endpoint_id: "e1", event: "order.created", status: "failed", attempts: 2, response_code: 500, error: "HTTP 500", next_attempt_at: null, created_at: "2026-10-25T10:00:00" }] });
    if (url.includes("/api/webhooks")) return reply({ items: endpoints, available_events: CATALOGUE.map((c) => c.name), event_catalogue: CATALOGUE, max_endpoints: 10 });
    return reply({});
  }));
});
afterEach(() => vi.unstubAllGlobals());

describe("WebhooksSettings", () => {
  it("adds an endpoint for chosen events and shows the secret once", async () => {
    const u = userEvent.setup();
    render(<WebhooksSettings />);
    expect(await screen.findByText("Accounting")).toBeInTheDocument();
    await u.click(screen.getByRole("button", { name: /Add endpoint/ }));
    const dlg = await screen.findByRole("dialog", { name: "Add webhook" });
    await u.type(within(dlg).getByLabelText("Endpoint URL"), "https://erp.example.com/tekly");
    await u.click(within(dlg).getByLabelText("Choose events"));
    await u.click(within(dlg).getByLabelText("order.created"));
    await u.click(within(dlg).getByLabelText("invoice.paid"));
    await u.click(within(dlg).getByRole("button", { name: "Add webhook" }));
    const post = calls.find((c) => c.method === "POST" && c.url.endsWith("/api/webhooks"))!;
    expect(post.body).toEqual({ url: "https://erp.example.com/tekly", description: null, events: ["order.created", "invoice.paid"] });
    const shown = await screen.findByRole("dialog", { name: "Signing secret" });
    expect(within(shown).getByTestId("fresh-secret")).toHaveTextContent("whsec_brandnewsecret");
    await u.click(within(shown).getByRole("button", { name: "I've saved it" }));
    expect(screen.queryByTestId("fresh-secret")).toBeNull();
  });

  it("shows the delivery log with redeliver and the test button", async () => {
    const u = userEvent.setup();
    render(<WebhooksSettings />);
    await u.click(await screen.findByRole("button", { name: /Open webhook/ }));
    const dlg = await screen.findByRole("dialog", { name: "Edit webhook" });
    expect(within(dlg).getByText("whsec_…abcd")).toBeInTheDocument();
    expect(await within(dlg).findByText("Failed")).toBeInTheDocument();
    await u.click(within(dlg).getByRole("button", { name: "Redeliver" }));
    expect(calls.some((c) => c.method === "POST" && c.url.includes("/deliveries/d1/redeliver"))).toBe(true);
    await u.click(within(dlg).getByRole("button", { name: /Send test event/ }));
    expect(calls.some((c) => c.method === "POST" && c.url.includes("/webhooks/e1/test"))).toBe(true);
  });

  it("names delivery states in plain words", () => {
    const base = { id: "x", endpoint_id: "e", event: "order.created", attempts: 1 };
    expect(deliveryState({ ...base, status: "sent" }).label).toBe("Delivered");
    expect(deliveryState({ ...base, status: "failed", next_attempt_at: "2026-10-25T10:00:00" }).label).toBe("Will retry");
    expect(deliveryState({ ...base, status: "failed", next_attempt_at: null }).label).toBe("Failed");
  });
});
