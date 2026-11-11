// Follow-ups Phase 1: the job's "Next step" card — what Tekly will do next, what it already did,
// Log call (ticks the linked task) and Skip this job (asks first, in the card).
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

vi.mock("../../components/Shell", () => ({ M: ({ n }: { n: string }) => <i data-icon={n} />, default: ({ children }: any) => <div>{children}</div> }));

import NextStepCard from "./NextStepCard";

const reply = (body: unknown, status = 200) => ({ ok: status < 400, status, statusText: "", headers: new Headers(), json: async () => body, text: async () => JSON.stringify(body) }) as Response;
const day = (n: number) => { const d = new Date(); d.setDate(d.getDate() + n); d.setHours(10, 0, 0, 0); return d.toISOString(); };

const ACTIVE = {
  active: true, preset_key: "ready_not_collected", state: "active", started_at: day(-6), stop_reason: null,
  steps: [
    { index: 0, label: "Ready text sent", action: "sms", due_at: day(-6), status: "sent", at: day(-6), task_id: null },
    { index: 1, label: "Reminder text (day 3)", action: "sms", due_at: day(-3), status: "sent", at: day(-3), task_id: null },
    { index: 2, label: "Phone call (day 6)", action: "task", due_at: day(0), status: "scheduled", at: null, task_id: "t9" },
    { index: 3, label: "Storage notice (day 30)", action: "task", due_at: day(24), status: "scheduled", at: null, task_id: null },
  ],
  next_step: { index: 2, label: "Call Jane about collection", due_at: day(0), task_id: "t9" },
};

let followup: any;
let status = 200;
let calls: { method: string; url: string; body: any }[] = [];
beforeEach(() => {
  calls = []; followup = ACTIVE; status = 200;
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    const method = init?.method || "GET";
    calls.push({ method, url, body: init?.body ? JSON.parse(String(init.body)) : null });
    if (url.startsWith("/api/tasks?")) return reply({ items: [{ id: "t9", title: "Call Jane about collection", assignee_name: "Priya Shah", due_at: day(0) }] });
    if (method === "PATCH") return reply({ ok: true });
    if (url.endsWith("/followup/skip")) return reply({ ...followup, active: false, state: "stopped", stop_reason: "skipped", next_step: null });
    if (url.endsWith("/followup")) return status === 200 ? reply(followup) : reply({ detail: "Not Found" }, status);
    return reply({});
  }));
});
afterEach(() => vi.unstubAllGlobals());

describe("NextStepCard", () => {
  it("renders nothing while loading", () => {
    const { container } = render(<NextStepCard orderId="o1" onFlash={vi.fn()} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("renders nothing when the API isn't deployed (404)", async () => {
    status = 404;
    const { container } = render(<NextStepCard orderId="o1" onFlash={vi.fn()} />);
    await vi.waitFor(() => expect(calls.some((c) => c.url === "/api/orders/o1/followup")).toBe(true));
    await new Promise((r) => setTimeout(r, 0));
    expect(container).toBeEmptyDOMElement();
  });

  it("renders nothing when the job has no follow-up", async () => {
    followup = { active: false, preset_key: null, state: "none", started_at: null, stop_reason: null, steps: [], next_step: null };
    const { container } = render(<NextStepCard orderId="o1" onFlash={vi.fn()} />);
    await vi.waitFor(() => expect(calls.length).toBeGreaterThan(0));
    await new Promise((r) => setTimeout(r, 0));
    expect(container).toBeEmptyDOMElement();
  });

  it("shows the next step, whose list it is on, and the timeline", async () => {
    render(<NextStepCard orderId="o1" onFlash={vi.fn()} />);
    expect(await screen.findByText("Call Jane about collection")).toBeInTheDocument();
    expect(screen.getByText("Next step · automatic")).toBeInTheDocument();
    expect(await screen.findByText(/On Priya’s list today\./)).toBeInTheDocument();
    expect(screen.getByText(/Closes by itself when the job is paid and collected\./)).toBeInTheDocument();
    const items = screen.getAllByRole("listitem");
    expect(items.map((li) => li.getAttribute("data-state"))).toEqual(["done", "done", "next", "scheduled"]);
    expect(items[2].textContent).toContain("Today");
  });

  it("Log call ticks the linked task off and reloads", async () => {
    const u = userEvent.setup();
    const onFlash = vi.fn();
    render(<NextStepCard orderId="o1" onFlash={onFlash} />);
    await u.click(await screen.findByRole("button", { name: "Log call" }));
    const p = calls.find((c) => c.method === "PATCH")!;
    expect(p.url).toBe("/api/tasks/t9");
    expect(p.body).toEqual({ status: "done" });
    expect(onFlash).toHaveBeenCalledWith("Call logged");
    expect(calls.filter((c) => c.url === "/api/orders/o1/followup").length).toBe(2);
  });

  it("Log call is greyed out when the next step is a text Tekly sends itself", async () => {
    followup = { ...ACTIVE, next_step: { index: 1, label: "Reminder text", due_at: day(1), task_id: null } };
    render(<NextStepCard orderId="o1" onFlash={vi.fn()} />);
    const btn = await screen.findByRole("button", { name: "Log call" });
    expect(btn).toBeDisabled();
    expect(screen.getByText(/Tekly sends it tomorrow\./)).toBeInTheDocument();
  });

  it("Skip this job asks in the card first, then stops the follow-up", async () => {
    const u = userEvent.setup();
    render(<NextStepCard orderId="o1" onFlash={vi.fn()} />);
    await u.click(await screen.findByRole("button", { name: "Skip this job" }));
    expect(calls.some((c) => c.url.endsWith("/skip"))).toBe(false);
    expect(screen.getByText(/No more texts or calls for this job/)).toBeInTheDocument();
    await u.click(screen.getByRole("button", { name: "Keep it" }));
    expect(screen.queryByText(/No more texts or calls for this job/)).not.toBeInTheDocument();
    await u.click(screen.getByRole("button", { name: "Skip this job" }));
    await u.click(screen.getByRole("button", { name: "Stop follow-ups" }));
    expect(calls.find((c) => c.method === "POST")!.url).toBe("/api/orders/o1/followup/skip");
    expect(await screen.findByText("Follow-up stopped")).toBeInTheDocument();
    expect(screen.getByText("Someone skipped this job.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Log call" })).not.toBeInTheDocument();
  });

  it("a stopped follow-up shows why, and its history", async () => {
    followup = { ...ACTIVE, active: false, state: "stopped", stop_reason: "paid_and_collected", next_step: null,
      steps: ACTIVE.steps.map((s) => (s.status === "scheduled" ? { ...s, status: "cancelled" } : s)) };
    render(<NextStepCard orderId="o1" onFlash={vi.fn()} />);
    expect(await screen.findByText("Follow-up stopped")).toBeInTheDocument();
    expect(screen.getByText("The job was paid and collected.")).toBeInTheDocument();
    expect(screen.getAllByRole("listitem").map((li) => li.getAttribute("data-state"))).toEqual(["done", "done", "off", "off"]);
  });
});
