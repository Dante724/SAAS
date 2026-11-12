// Tasks page: one page (no tabs), the flat list a server without My day falls back to, search
// across the shop, links, Auto badge, edit/delete/undo in the panel, subtasks — against the
// current API (full list) and the paged one (`total`).
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

describe("TasksHub", () => {
  it("is one page: no My day | List tabs, no second New task button", async () => {
    render(<MemoryRouter><TasksHub /></MemoryRouter>);
    await screen.findByText("Order the screen");
    expect(screen.queryByRole("tab")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "New task" })).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Filter by priority")).not.toBeInTheDocument();
    expect(screen.getAllByLabelText(/Search/)).toHaveLength(1);                   // one search box
    expect(screen.getAllByLabelText("Whose tasks")).toHaveLength(1);              // one person menu
  });

  it("without My day on the server: the flat list of my tasks, asked of the server and narrowed here", async () => {
    render(<MemoryRouter><TasksHub /></MemoryRouter>);
    expect(await screen.findByText("Order the screen")).toBeInTheDocument();
    expect(screen.getByText("Call the supplier")).toBeInTheDocument();          // I made it
    expect(screen.queryByText("Clean the bench")).not.toBeInTheDocument();      // someone else's (older API sends all)
    const list = calls.find((c) => c.url.startsWith("/api/tasks?"))!;
    expect(list.url).toContain("assignee=me");
    expect(list.url).toContain("mine=true");
  });

  it("the flat list shows links, Auto, priority and who it's for", async () => {
    render(<MemoryRouter><TasksHub /></MemoryRouter>);
    const r1 = (await screen.findByText("Order the screen")).closest("li")!;
    expect(within(r1).getByText("WO-12")).toBeInTheDocument();
    expect(within(r1).getByText(/Ann Lee/)).toBeInTheDocument();
    expect(within(r1).getByText(/Made by Tekly/)).toBeInTheDocument();
    expect(within(r1).getByText("Urgent")).toBeInTheDocument();
    expect(within(r1).getByRole("img", { name: "Assigned to Me" })).toBeInTheDocument();
    const r2 = screen.getByText("Call the supplier").closest("li")!;
    expect(within(r2).getByText("High")).toBeInTheDocument();                 // legacy string priority
    expect(within(r2).queryByText(/Made by Tekly/)).not.toBeInTheDocument();
    expect(within(r2).getByText("Up for grabs")).toBeInTheDocument();         // nobody on it
  });

  it("search reaches the whole shop and Clear search goes back", async () => {
    const u = userEvent.setup();
    render(<MemoryRouter><TasksHub /></MemoryRouter>);
    await screen.findByText("Order the screen");
    await u.type(screen.getByLabelText("Search all tasks"), "bench");
    expect(await screen.findByText("Clean the bench")).toBeInTheDocument();     // someone else's: search covers everyone
    expect(screen.queryByText("Order the screen")).not.toBeInTheDocument();
    const url = calls.filter((c) => c.url.startsWith("/api/tasks?")).pop()!.url;
    expect(url).toContain("q=bench");
    expect(url).not.toContain("assignee=");
    await u.click(screen.getByRole("button", { name: "Clear search" }));
    expect(await screen.findByText("Order the screen")).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByText("Clean the bench")).not.toBeInTheDocument());
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
});
