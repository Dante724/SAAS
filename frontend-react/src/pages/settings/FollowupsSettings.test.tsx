// Follow-ups Phase 1: Settings › Follow-ups — two presets, their steps (change the delay, switch a
// step off), and Turn on only after a preview of what it would affect today.
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

vi.mock("../../components/Shell", () => ({ M: ({ n }: { n: string }) => <i data-icon={n} />, default: ({ children }: any) => <div>{children}</div> }));
const meState = { canSettings: true, plan: true };
vi.mock("../../context/Me", () => ({
  useMe: () => ({ me: { id: "u1" }, can: (c?: string) => (c === "can_manage_settings" ? meState.canSettings : true), fmt: String, loading: false, refresh: async () => {} }),
  hasFeature: () => meState.plan,
}));

import FollowupsSettings from "./FollowupsSettings";

const reply = (body: unknown, status = 200) => ({ ok: status < 400, status, statusText: "", headers: new Headers(), json: async () => body, text: async () => JSON.stringify(body) }) as Response;
const PRESETS = () => ({ items: [
  { key: "ready_not_collected", name: "Ready but not collected", enabled: false, stop_conditions: ["paid_and_collected"], start_status_ids: ["s-ready"],
    steps: [
      { index: 0, after_hours: 72, action: "sms", label: "Reminder text", enabled: true },
      { index: 1, after_hours: 144, action: "task", label: "Call the customer", enabled: true },
      { index: 2, after_hours: 720, action: "task", label: "Printable storage notice (optional)", enabled: false },
    ] },
  { key: "quote_not_answered", name: "Quote not answered", enabled: true, stop_conditions: ["approved", "declined"], start_status_ids: ["s-wait"],
    steps: [
      { index: 0, after_hours: 24, action: "sms", label: "Nudge text", enabled: true },
      { index: 1, after_hours: 48, action: "task", label: "Call about the quote", enabled: true },
    ] },
] });
const PREVIEW = { count: 4, total_amount: 1240, currency: "GBP", messages_today: 3, jobs: [
  { order_id: "o1", order_number: "1019", client_name: "Jane Kim", amount: 180, since: "2026-11-05", days_waiting: 6 },
  { order_id: "o2", order_number: "1022", client_name: "Tom Reed", amount: 420, since: "2026-11-02", days_waiting: 9 },
] };

let presetsStatus = 200;
let calls: { method: string; url: string; body: any }[] = [];
beforeEach(() => {
  calls = []; presetsStatus = 200; meState.canSettings = true; meState.plan = true;
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    const method = init?.method || "GET";
    const body = init?.body ? JSON.parse(String(init.body)) : null;
    calls.push({ method, url, body });
    if (url.startsWith("/api/statuses")) return reply({ items: [{ id: "s-ready", name: "Ready for pickup" }, { id: "s-wait", name: "Waiting for approval" }] });
    if (url.endsWith("/preview")) return reply(PREVIEW);
    if (method === "PUT") {
      const key = url.split("/").pop();
      const p = PRESETS().items.find((x) => x.key === key)!;
      return reply({ ...p, ...(body.enabled !== undefined ? { enabled: body.enabled } : {}),
        steps: body.steps ? p.steps.map((s) => ({ ...s, ...body.steps.find((b: any) => b.index === s.index) })) : p.steps });
    }
    if (url === "/api/followups/presets") return presetsStatus === 200 ? reply(PRESETS()) : reply({ detail: "no" }, presetsStatus);
    return reply({});
  }));
});
afterEach(() => vi.unstubAllGlobals());

const card = async (name: string) => (await screen.findByText(name)).closest("section") as HTMLElement;

describe("FollowupsSettings", () => {
  it("shows a loading line, then both presets with their steps and start status", async () => {
    render(<FollowupsSettings />);
    expect(screen.getByText("Loading follow-ups…")).toBeInTheDocument();
    const ready = await card("Ready but not collected");
    expect(within(ready).getByText(/Day 3 · Reminder text/)).toBeInTheDocument();
    expect(within(ready).getByText(/Day 6 · Call the customer/)).toBeInTheDocument();
    expect(await within(ready).findByText(/Starts when a job is “Ready for pickup”/)).toBeInTheDocument();
    const quote = await card("Quote not answered");
    expect(within(quote).getByText(/After 24 h · Nudge text/)).toBeInTheDocument();
    expect(within(quote).getByRole("switch", { name: "Quote not answered" })).toHaveAttribute("aria-checked", "true");
  });

  it("turning on shows the preview first; Turn on saves only from the preview", async () => {
    const u = userEvent.setup();
    render(<FollowupsSettings />);
    const ready = await card("Ready but not collected");
    await u.click(within(ready).getByRole("switch", { name: "Ready but not collected" }));
    expect(calls.some((c) => c.method === "PUT")).toBe(false);
    expect(calls.find((c) => c.url.endsWith("/preview"))!.url).toBe("/api/followups/presets/ready_not_collected/preview");
    const prev = await within(ready).findByRole("region", { name: "Preview before you switch on" });
    expect(prev.textContent).toContain("Right now this would affect 4 jobs (£1,240 waiting).");
    expect(prev.textContent).toContain("No customer gets a message until you press Turn on.");
    // the job list opens on request
    expect(within(prev).queryByText("Jane Kim")).not.toBeInTheDocument();
    await u.click(within(prev).getByRole("button", { name: /See the 4 jobs/ }));
    expect(within(prev).getByText("Jane Kim")).toBeInTheDocument();
    await u.click(within(prev).getByRole("button", { name: "Turn on" }));
    const put = calls.find((c) => c.method === "PUT")!;
    expect(put.url).toBe("/api/followups/presets/ready_not_collected");
    expect(put.body).toEqual({ enabled: true });
    expect(await within(ready).findByRole("switch", { name: "Ready but not collected" })).toHaveAttribute("aria-checked", "true");
    expect(within(ready).queryByRole("region", { name: "Preview before you switch on" })).not.toBeInTheDocument();
  });

  it("Not now closes the preview without saving", async () => {
    const u = userEvent.setup();
    render(<FollowupsSettings />);
    const ready = await card("Ready but not collected");
    await u.click(within(ready).getByRole("switch", { name: "Ready but not collected" }));
    await within(ready).findByRole("region", { name: "Preview before you switch on" });
    await u.click(within(ready).getByRole("button", { name: "Not now" }));
    expect(within(ready).queryByRole("region", { name: "Preview before you switch on" })).not.toBeInTheDocument();
    expect(calls.some((c) => c.method === "PUT")).toBe(false);
  });

  it("turning off saves straight away", async () => {
    const u = userEvent.setup();
    render(<FollowupsSettings />);
    const quote = await card("Quote not answered");
    await u.click(within(quote).getByRole("switch", { name: "Quote not answered" }));
    expect(calls.find((c) => c.method === "PUT")!.body).toEqual({ enabled: false });
    expect(await within(quote).findByRole("switch", { name: "Quote not answered" })).toHaveAttribute("aria-checked", "false");
  });

  it("edits a step's delay and switches a step on, then saves the steps", async () => {
    const u = userEvent.setup();
    render(<FollowupsSettings />);
    const ready = await card("Ready but not collected");
    await u.click(within(ready).getByRole("button", { name: "Change steps" }));
    const delay = within(ready).getByLabelText("Delay for Reminder text");
    await u.clear(delay); await u.type(delay, "2");
    await u.click(within(ready).getByRole("switch", { name: "Printable storage notice (optional)" }));
    await u.click(within(ready).getByRole("button", { name: "Save steps" }));
    const put = calls.find((c) => c.method === "PUT")!;
    expect(put.body).toEqual({ steps: [
      { index: 0, after_hours: 48, enabled: true },
      { index: 1, after_hours: 144, enabled: true },
      { index: 2, after_hours: 720, enabled: true },
    ] });
  });

  it("a step delay can be set in hours", async () => {
    const u = userEvent.setup();
    render(<FollowupsSettings />);
    const quote = await card("Quote not answered");
    await u.click(within(quote).getByRole("button", { name: "Change steps" }));
    expect(within(quote).getByLabelText("Unit for Nudge text")).toHaveValue("hours");
    const delay = within(quote).getByLabelText("Delay for Nudge text");
    await u.clear(delay); await u.type(delay, "12");
    await u.click(within(quote).getByRole("button", { name: "Save steps" }));
    expect(calls.find((c) => c.method === "PUT")!.body.steps[0]).toEqual({ index: 0, after_hours: 12, enabled: true });
  });

  it("without permission everything is greyed out with the reason", async () => {
    meState.canSettings = false;
    render(<FollowupsSettings />);
    expect(await screen.findByText(/Only people who can change settings/)).toBeInTheDocument();
    const ready = await card("Ready but not collected");
    expect(within(ready).getByRole("switch", { name: "Ready but not collected" })).toBeDisabled();
    expect(within(ready).getByRole("button", { name: "Change steps" })).toBeDisabled();
  });

  it("on a plan without automations everything is greyed out and says so", async () => {
    meState.plan = false;
    render(<FollowupsSettings onOpenBilling={vi.fn()} />);
    expect(await screen.findByText(/Follow-ups come with the Shop and Pro plans/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "See plans" })).toBeInTheDocument();
    const ready = await card("Ready but not collected");
    expect(within(ready).getByRole("switch", { name: "Ready but not collected" })).toBeDisabled();
  });

  it("a server that refuses (403) shows the reason instead of presets", async () => {
    presetsStatus = 403;
    render(<FollowupsSettings />);
    expect(await screen.findByText(/You can’t change follow-ups/)).toBeInTheDocument();
  });

  it("a server without follow-ups (404) says it isn't available yet", async () => {
    presetsStatus = 404;
    render(<FollowupsSettings />);
    expect(await screen.findByText("Follow-ups aren’t available yet")).toBeInTheDocument();
  });
});
