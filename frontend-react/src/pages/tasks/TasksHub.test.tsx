// Tasks screen (phase 0): Mine by default, search and filters, links, Auto badge, edit/delete/undo
// in the panel, subtasks — against the current API (full list) and the paged one (`total`).
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, within, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";

vi.mock("../../components/Shell", () => ({ M: ({ n }: { n: string }) => <i data-icon={n} />, default: ({ children }: any) => <div>{children}</div> }));
vi.mock("../../components/JobPicker", () => ({ default: () => <div data-testid="job-picker" /> }));
let manager = true;
vi.mock("../../context/Me", () => ({
  useMe: () => ({ me: { id: "me" }, can: (c?: string) => c !== "can_assign_staff" || manager }),
  hasFeature: () => false,
}));

import TasksHub from "./TasksHub";

const reply = (body: unknown) => ({ ok: true, status: 200, headers: new Headers(), json: async () => body, text: async () => JSON.stringify(body) }) as Response;
const ITEMS = [
  { id: "t1", title: "Order the screen", status: "weird", priority: 2, assignee_ids: ["me"], assignee_names: ["Me"], order_id: "o1", order_number: "WO-12", client_name: "Ann Lee", source: "automation" },
  { id: "t2", title: "Call the supplier", status: "open", priority: "high", created_by_id: "me" },
  { id: "t3", title: "Clean the bench", status: "in_progress", assigned_to_id: "e2", assignee_ids: ["e2"], assignee_names: ["Sam"] },
];
let calls: { method: string; url: string; body: any }[] = [];
let paged = false;

beforeEach(() => {
  calls = []; paged = false; manager = true;
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    const method = init?.method || "GET";
    calls.push({ method, url, body: init?.body ? JSON.parse(String(init.body)) : null });
    // This file covers the List view: a server without /tasks/my-day falls back to it.
    if (method === "GET" && url.startsWith("/api/tasks/my-day")) return { ok: false, status: 404, statusText: "Not Found", headers: new Headers(), json: async () => ({ detail: "Not Found" }), text: async () => '{"detail":"Not Found"}' } as Response;
    if (method === "GET" && url.startsWith("/api/tasks?")) return reply(paged ? { items: ITEMS, total: ITEMS.length } : { items: ITEMS, statuses: ["todo", "in_progress", "done"] });
    if (method === "GET" && url === "/api/tasks/t1") return reply({ ...ITEMS[0], description: "Old note", assignee_ids: ["me", "e2"], subtasks: [{ id: "s1", title: "Remove the old one", status: "done", completed_at: "2026-01-01T00:00:00Z" }] });
    if (method === "GET" && url.endsWith("/feed")) return reply({ items: [] });
    if (method === "GET" && url.startsWith("/api/staff/employees")) return reply({ items: [{ id: "me", name: "Me" }, { id: "e2", name: "Sam" }] });
    return reply({ ok: true });
  }));
});
afterEach(() => vi.unstubAllGlobals());

const rows = () => screen.getAllByRole("row").slice(1);

describe("TasksHub", () => {
  it("defaults to Mine and asks the server for my tasks", async () => {
    render(<MemoryRouter><TasksHub /></MemoryRouter>);
    expect(await screen.findByText("Order the screen")).toBeInTheDocument();
    expect(screen.getByText("Call the supplier")).toBeInTheDocument();          // I made it
    expect(screen.queryByText("Clean the bench")).not.toBeInTheDocument();      // someone else's
    expect(screen.getByRole("button", { name: "Mine" })).toHaveAttribute("aria-pressed", "true");
    const list = calls.find((c) => c.url.startsWith("/api/tasks?"))!;
    expect(list.url).toContain("assignee=me");
    expect(list.url).toContain("mine=true");
  });

  it("Everyone shows all tasks for a manager and is greyed out for others", async () => {
    const u = userEvent.setup();
    const { unmount } = render(<MemoryRouter><TasksHub /></MemoryRouter>);
    await screen.findByText("Order the screen");
    await u.click(screen.getByRole("button", { name: "Everyone" }));
    expect(await screen.findByText("Clean the bench")).toBeInTheDocument();
    unmount();
    manager = false;
    render(<MemoryRouter><TasksHub /></MemoryRouter>);
    await screen.findByText("Order the screen");
    expect(screen.getByRole("button", { name: "Everyone" })).toBeDisabled();
  });

  it("reads unknown statuses as To do, shows links, Auto and priority", async () => {
    render(<MemoryRouter><TasksHub /></MemoryRouter>);
    await screen.findByText("Order the screen");
    const r1 = rows().find((r) => within(r).queryByText("Order the screen"))!;
    expect(within(r1).getByText("To do")).toBeInTheDocument();
    expect(within(r1).getByText("WO-12")).toBeInTheDocument();
    expect(within(r1).getByText("Ann Lee")).toBeInTheDocument();
    expect(within(r1).getByText(/Made by Tekly/)).toBeInTheDocument();
    expect(within(r1).getByText("Urgent")).toBeInTheDocument();
    const r2 = rows().find((r) => within(r).queryByText("Call the supplier"))!;
    expect(within(r2).getByText("To do")).toBeInTheDocument();                // legacy "open"
    expect(within(r2).getByText("High")).toBeInTheDocument();                 // legacy string priority
    expect(within(r2).queryByText(/Made by Tekly/)).not.toBeInTheDocument();
  });

  it("searches by client and filters by priority and person", async () => {
    const u = userEvent.setup();
    render(<MemoryRouter><TasksHub /></MemoryRouter>);
    await screen.findByText("Order the screen");
    await u.click(screen.getByRole("button", { name: "Everyone" }));
    await screen.findByText("Clean the bench");
    await u.type(screen.getByLabelText("Search tasks"), "ann lee");
    await waitFor(() => expect(screen.queryByText("Call the supplier")).not.toBeInTheDocument());
    expect(screen.getByText("Order the screen")).toBeInTheDocument();
    expect(calls.filter((c) => c.url.startsWith("/api/tasks?")).pop()!.url).toContain("q=ann+lee");
    await u.clear(screen.getByLabelText("Search tasks"));
    await u.selectOptions(screen.getByLabelText("Filter by priority"), "1");
    await waitFor(() => expect(screen.queryByText("Order the screen")).not.toBeInTheDocument());
    expect(await screen.findByText("Call the supplier")).toBeInTheDocument();   // after the search debounce clears
    await u.selectOptions(screen.getByLabelText("Filter by priority"), "");
    await u.selectOptions(screen.getByLabelText("Filter by person"), "e2");
    await waitFor(() => expect(screen.queryByText("Order the screen")).not.toBeInTheDocument());
    expect(screen.getByText("Clean the bench")).toBeInTheDocument();
  });

  it("edits title, description and priority in the panel, shows subtasks, labels remove buttons", async () => {
    const u = userEvent.setup();
    render(<MemoryRouter><TasksHub /></MemoryRouter>);
    await u.click(await screen.findByText("Order the screen"));
    const title = await screen.findByLabelText("Task title");
    expect(screen.getByText("Remove the old one")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Remove Sam" })).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Close" })).toHaveLength(1);   // only the panel's own close
    await u.clear(title); await u.type(title, "Order the new screen"); await u.tab();
    expect(calls.find((c) => c.method === "PATCH")!.body).toEqual({ title: "Order the new screen" });
    const desc = screen.getByLabelText("Description");
    await u.clear(desc); await u.type(desc, "Ask for OEM"); await u.tab();
    expect(calls.filter((c) => c.method === "PATCH").pop()!.body).toEqual({ description: "Ask for OEM" });
    await u.selectOptions(screen.getByLabelText("Priority"), "1");
    expect(calls.filter((c) => c.method === "PATCH").pop()!.body).toEqual({ priority: 1 });
  });

  it("deletes after an in-page confirm and offers Undo on the soft-delete API", async () => {
    paged = true;
    const u = userEvent.setup();
    const confirm = vi.spyOn(window, "confirm");
    render(<MemoryRouter><TasksHub /></MemoryRouter>);
    await u.click(await screen.findByText("Order the screen"));
    await screen.findByLabelText("Task title");
    await u.click(screen.getByRole("button", { name: /Delete task/ }));
    expect(calls.some((c) => c.method === "DELETE")).toBe(false);
    await u.click(screen.getByRole("button", { name: "Delete" }));
    expect(calls.find((c) => c.method === "DELETE")!.url).toBe("/api/tasks/t1");
    expect(confirm).not.toHaveBeenCalled();
    await u.click(await screen.findByRole("button", { name: "Undo" }));
    expect(calls.find((c) => c.method === "POST")!.url).toBe("/api/tasks/t1/restore");
  });

  it("uses server status filtering and paging once the API reports a total", async () => {
    paged = true;
    const u = userEvent.setup();
    render(<MemoryRouter><TasksHub /></MemoryRouter>);
    await screen.findByText("Order the screen");
    await u.click(screen.getByRole("button", { name: "In progress" }));
    await waitFor(() => expect(calls.filter((c) => c.url.startsWith("/api/tasks?")).pop()!.url).toContain("status=in_progress"));
    expect(calls.filter((c) => c.url.startsWith("/api/tasks?")).pop()!.url).toContain("limit=50");
  });
});
