// J4 — tasks live on the job: listed, added with the job linked, ticked off.
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

vi.mock("../../components/Shell", () => ({ M: ({ n }: { n: string }) => <i data-icon={n} />, default: ({ children }: any) => <div>{children}</div> }));

import JobTasks from "./JobTasks";

const reply = (body: unknown) => ({ ok: true, status: 200, headers: new Headers(), json: async () => body, text: async () => JSON.stringify(body) }) as Response;
let calls: { method: string; url: string; body: any }[] = [];

beforeEach(() => {
  calls = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ method: init?.method || "GET", url, body: init?.body ? JSON.parse(String(init.body)) : null });
    if (!init?.method || init.method === "GET") return reply({ items: [{ id: "t1", title: "Order the screen", status: "todo", completed_at: null }] });
    return reply({ ok: true });
  }));
});
afterEach(() => vi.unstubAllGlobals());

describe("JobTasks", () => {
  it("lists the job's tasks and ticks one off", async () => {
    const u = userEvent.setup();
    render(<JobTasks orderId="o1" emps={[{ id: "e1", name: "Sam" }]} onFlash={vi.fn()} />);
    expect(await screen.findByText("Order the screen")).toBeInTheDocument();
    expect(calls[0].url).toBe("/api/tasks?order_id=o1");
    await u.click(screen.getByLabelText("Done: Order the screen"));
    const p = calls.find((c) => c.method === "PATCH")!;
    expect(p.url).toBe("/api/tasks/t1");
    expect(p.body).toEqual({ status: "done" });
  });

  it("adds a task linked to the job and assigned", async () => {
    const u = userEvent.setup();
    render(<JobTasks orderId="o1" emps={[{ id: "e1", name: "Sam" }]} onFlash={vi.fn()} />);
    await screen.findByText("Order the screen");
    await u.click(screen.getByRole("button", { name: /Add task/ }));
    await u.type(screen.getByLabelText("Task"), "Call the customer");
    await u.selectOptions(screen.getByLabelText("Assign to"), "e1");
    await u.click(screen.getByRole("button", { name: "Add" }));
    const p = calls.find((c) => c.method === "POST")!;
    expect(p.url).toBe("/api/tasks");
    expect(p.body).toMatchObject({ title: "Call the customer", order_id: "o1", assigned_to_id: "e1" });
  });
});
