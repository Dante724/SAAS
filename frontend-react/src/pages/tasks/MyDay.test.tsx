// Tasks › My day (the default view): groups and counts, Catch up's "Move all to today", quick add
// (chips, Enter, optimistic insert and rollback), whose day, done + undo, empty / loading / error,
// the 404 fallback to the list, and /tasks?task=<id> deep links.
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, within, waitFor, act } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useLocation, useNavigate } from "react-router-dom";

vi.mock("../../components/Shell", () => ({ M: ({ n }: { n: string }) => <i data-icon={n} />, default: ({ children }: any) => <div>{children}</div> }));
vi.mock("../../components/JobPicker", () => ({ default: ({ value, onChange }: any) => (value
  ? <span data-testid="linked-job">{value.order_number}</span>
  : <button onClick={() => onChange({ id: "o9", order_number: "WO-1042" })}>pick job</button>) }));
let manager = true;
vi.mock("../../context/Me", () => ({
  useMe: () => ({ me: { id: "me" }, can: (c?: string) => c !== "can_assign_staff" || manager }),
  hasFeature: () => false,
}));

import TasksHub from "./TasksHub";
import { resetShopTz } from "../../lib/shopTime";

const reply = (body: unknown, status = 200) => ({ ok: status < 400, status, statusText: "", headers: new Headers(), json: async () => body, text: async () => JSON.stringify(body) }) as Response;
const DATE = "2026-11-11";
// Everything in "my day" is mine (assigned to me); "Up for grabs" tests add unassigned ones.
const mine = (ts: any[]) => ts.map((t) => ({ assigned_to_id: "me", assignee_ids: ["me"], ...t }));
const DAY = () => { const d: any = ({
  date: DATE,
  catch_up: [
    { id: "c1", title: "Chase supplier RMA #A88", due_at: "2026-11-06T17:00:00", overdue: true, order_id: "o1", order_number: "WO-0998", client_name: "MobileParts UK" },
    { id: "c2", title: "Call back missed call", due_at: "2026-11-10T10:00:00", overdue: true },
  ],
  today: [
    { id: "t1", title: "QC check MacBook Air keyboard", due_at: `${DATE}T15:00:00`, priority: 1, order_id: "o2", order_number: "WO-1031", client_name: "Tom Reed", assignee_names: ["Me"] },
    { id: "t2", title: "Call Jane about collection", due_at: `${DATE}T11:00:00`, order_id: "o3", order_number: "WO-1019", client_name: "Jane Kim", source: "automation" },
    { id: "t3", title: "Restock iPhone 13 screens", due_at: `${DATE}T23:59:59` },
  ],
  upcoming: [{ id: "u1", title: "Order iPad Air battery", due_at: "2026-11-12" }],
  later: [{ id: "l1", title: "Tidy the parts shelf", due_at: null }],
  done_today: [{ id: "d1", title: "Open the till", completed_at: `${DATE}T08:55:00Z`, status: "done" }],
  counts: { catch_up: 2, today: 3, upcoming: 1, later: 1, done_today: 1 },
  }); for (const k of ["catch_up", "today", "upcoming", "later", "done_today"]) d[k] = mine(d[k]); return d; };
let calls: { method: string; url: string; body: any }[] = [];
let dayBody: any;
let dayStatus = 200;
let failPost = false;
let failMove = false;
let shopTz: string | null = null;     // GET /staff/company timezone (none = UTC, like the server)
let shopCountry: string | null = null;
let tzFail = false;
let goneId: string | null = null;   // an employee who left the shop
let patchFail = false;
// Like the server: each list holds at most 100, counts are the true totals.
const capDay = (d: any) => ({ ...d, catch_up: d.catch_up.slice(0, 100), counts: { ...d.counts, catch_up: Math.max(d.counts.catch_up, d.catch_up.length) } });
let moveReply: any = { moved: 2 };
const LIST = () => [{ id: "x1", title: "A task in the list", assignee_ids: ["me"], due_at: "2026-11-11T04:00:00" }];
let listItems: any[] = LIST();
let listPaged = false;     // the newer list API (pages, says `total`)
let closedTotal = 0;       // its count of done + cancelled matches
// A server with assignee=pool (null = the #203 server: "pool" reads as an unknown person, 404 for
// managers and 403 for staff).
let poolServer: null | { pool: any } = null;
// GET /orders?q= (the work-order search) and GET /orders/{id}: what quick add's smart link sees.
let orderSearch: (q: string) => { items: any[]; delay?: number } = () => ({ items: [] });
let orderGet: (id: string) => number = () => 200;

beforeEach(() => {
  calls = []; manager = true; dayBody = DAY(); dayStatus = 200; failPost = false; failMove = false;
  try { localStorage.clear(); } catch { /* storage may be off */ }
  shopTz = null; shopCountry = null; tzFail = false; patchFail = false; goneId = null; moveReply = { moved: 2 }; listItems = LIST(); listPaged = false; closedTotal = 0; poolServer = null; orderSearch = () => ({ items: [] }); orderGet = () => 200; resetShopTz();
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    const method = init?.method || "GET";
    calls.push({ method, url, body: init?.body ? JSON.parse(String(init.body)) : null });
    if (method === "GET" && url.startsWith("/api/tasks/my-day") && goneId && url.includes(`assignee=${goneId}`)) return reply({ detail: "Employee not found" }, 404);
    if (method === "GET" && url.startsWith("/api/tasks/my-day") && url.includes("assignee=pool"))
      return poolServer ? reply(poolServer.pool) : manager ? reply({ detail: "Employee not found" }, 404) : reply({ detail: "Only a manager can see someone else's day." }, 403);
    if (method === "GET" && url.startsWith("/api/tasks/my-day")) return dayStatus === 200 ? reply(capDay(dayBody)) : reply({ detail: dayStatus === 404 && !url.includes("assignee=me") ? "Employee not found" : "Not Found" }, dayStatus);
    if (method === "GET" && url.startsWith("/api/staff/company")) return tzFail ? reply({ detail: "boom" }, 500) : reply({ name: "Fix Point", timezone: shopTz, country: shopCountry });
    if (method === "GET" && url.startsWith("/api/tasks?") && listPaged && url.includes("status=done%2Ccancelled")) return reply({ items: listItems.slice(0, 1), total: closedTotal });
    if (method === "GET" && url.startsWith("/api/tasks?")) return reply(listPaged ? { items: listItems, total: listItems.length } : { items: listItems });
    if (method === "POST" && url === "/api/tasks/move-to-today") {
      if (failMove) return reply({ detail: "no" }, 500);
      const ids: string[] = calls[calls.length - 1].body.ids; const skip = new Set(moveReply.skipped || []);
      for (const id of ids) { const t = dayBody.catch_up.find((x: any) => x.id === id); if (t && !skip.has(id)) { dayBody.catch_up = dayBody.catch_up.filter((x: any) => x.id !== id); dayBody.today.push({ ...t, overdue: false, due_at: `${DATE}T23:59:59` }); } }
      dayBody.counts = { ...dayBody.counts, catch_up: dayBody.catch_up.length + (dayBody.extraCatchUp || 0), today: dayBody.today.length };
      return reply(moveReply);
    }
    if (method === "POST" && url === "/api/tasks") { if (failPost) return reply({ detail: "no" }, 500); const b = calls[calls.length - 1].body; const t = { id: `new${calls.length}`, title: b.title, due_at: b.due_at }; if (b.assigned_to_id === "me") (b.due_at === DATE ? dayBody.today : dayBody.upcoming).push(t); return reply(t); }
    if (method === "PATCH" && /^\/api\/tasks\/[^/?]+$/.test(url)) {     // like the server: done moves to Done today and back
      const id = url.split("/").pop(); const b = calls[calls.length - 1].body;
      if (patchFail) return reply({ detail: "Someone else took it" }, 409);
      if (b?.assignee_ids) {   // a claim: the task is mine now
        for (const k of ["catch_up", "today", "upcoming", "later"]) dayBody[k] = dayBody[k].map((x: any) => (x.id === id ? { ...x, assigned_to_id: b.assignee_ids[0], assignee_ids: b.assignee_ids } : x));
        if (poolServer) for (const k of ["catch_up", "today", "upcoming", "later"]) poolServer.pool[k] = poolServer.pool[k].filter((x: any) => x.id !== id);
      }
      if (b?.status) {
        const all = ["catch_up", "today", "upcoming", "later", "done_today"].flatMap((k) => dayBody[k]);
        const t = all.find((x: any) => x.id === id);
        if (t) {
          for (const k of ["catch_up", "today", "upcoming", "later", "done_today"]) dayBody[k] = dayBody[k].filter((x: any) => x.id !== id);
          const n = { ...t, status: b.status, completed_at: b.status === "done" ? `${DATE}T12:00:00Z` : null };
          (b.status === "done" ? dayBody.done_today : t.overdue ? dayBody.catch_up : dayBody.today).push(n);
          dayBody.counts = Object.fromEntries(["catch_up", "today", "upcoming", "later", "done_today"].map((k) => [k, dayBody[k].length]));
        }
      }
      return reply({ ok: true });
    }
    if (method === "GET" && /^\/api\/tasks\/[^/?]+$/.test(url)) {
      const id = url.split("/").pop();
      if (id === "gone") return reply({ detail: "Not found" }, 404);
      if (id === "secret") return reply({ detail: "Forbidden" }, 403);
      if (id === "crit") return reply({ id, title: "Board-level repair for Ana", description: "", priority: 3, due_at: "2026-11-11T11:30:00" });
      return reply({ id, title: id === "t2" ? "Call Jane about collection" : "Chase supplier RMA #A88", description: "", due_at: "2026-11-06T11:30:00" });
    }
    if (method === "GET" && url.endsWith("/feed")) return reply({ items: [] });
    if (method === "GET" && url.startsWith("/api/orders?")) {
      const r = orderSearch(new URL(url, "http://x").searchParams.get("q") || "");
      if (r.delay) await new Promise((res) => setTimeout(res, r.delay));
      return reply({ items: r.items, total: r.items.length });
    }
    if (method === "GET" && /^\/api\/orders\/[^/?]+$/.test(url)) { const st = orderGet(url.split("/").pop()!); return st === 200 ? reply({ id: url.split("/").pop() }) : reply({ detail: "Not found" }, st); }
    if (method === "GET" && url.startsWith("/api/staff/employees")) return reply({ items: [{ id: "me", name: "Me" }, { id: "e2", name: "Jane Kim" }, { id: "e3", name: "Tom Reed" }] });
    return reply({ ok: true });
  }));
});
afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); });

let at = "";
let go: (to: string) => void = () => {};
function Spy() { const l = useLocation(); at = l.pathname + l.search; go = useNavigate(); return null; }
const renderAt = (path = "/tasks") => render(<MemoryRouter initialEntries={[path]}><TasksHub /><Spy /></MemoryRouter>);
const posts = (u: string) => calls.filter((c) => c.method === "POST" && c.url === u);

describe("My day", () => {
  it("is the default view: Catch up, Today grouped by time, counts on the tabs", async () => {
    renderAt();
    expect(await screen.findByText("QC check MacBook Air keyboard")).toBeInTheDocument();
    expect(calls.find((c) => c.url.startsWith("/api/tasks/my-day"))!.url).toBe("/api/tasks/my-day?assignee=me");
    const catchUp = screen.getByRole("region", { name: /^Catch up/ });
    expect(within(catchUp).getByText("Chase supplier RMA #A88")).toBeInTheDocument();
    expect(within(catchUp).getByText("2 tasks from earlier, oldest first", { exact: false })).toBeInTheDocument();
    // tabs: Today counts today + catch up
    expect(screen.getByRole("button", { name: /Today\s*5/ })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: /Upcoming\s*1/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Later\s*1/ })).toBeInTheDocument();
    // time groups, earliest first, then "any time"
    const heads = screen.getAllByRole("heading", { level: 3 }).map((h) => h.textContent);
    expect(heads).toEqual(["Morning1", "Afternoon1", "Any time today1"]);
    // job + client chip opens the job; Auto badge; priority badge
    expect(screen.getByRole("link", { name: /^Open work order\s*WO-1019.*Jane Kim$/ })).toHaveAttribute("href", "/orders?open=o3");
    const jane = screen.getByText("Call Jane about collection").closest("li")!;
    expect(within(jane).getByText(/Made by Tekly/)).toBeInTheDocument();
    expect(within(screen.getByText("QC check MacBook Air keyboard").closest("li")!).getByText("High")).toBeInTheDocument();
  });

  it("Upcoming and Later list by day", async () => {
    const u = userEvent.setup();
    renderAt();
    await screen.findByText("QC check MacBook Air keyboard");
    await u.click(screen.getByRole("button", { name: /Upcoming/ }));
    expect(screen.getByText("Order iPad Air battery")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /Tomorrow/ })).toBeInTheDocument();
    await u.click(screen.getByRole("button", { name: /Later/ }));
    expect(screen.getByRole("heading", { name: /No date/ })).toBeInTheDocument();
  });

  it("moves all catch-up tasks to today in one call, and puts them back if it fails", async () => {
    const u = userEvent.setup();
    renderAt();
    await screen.findByText("QC check MacBook Air keyboard");
    await u.click(screen.getByRole("button", { name: "Move all to today" }));
    expect(posts("/api/tasks/move-to-today")[0].body).toEqual({ ids: ["c1", "c2"] });
    expect(await screen.findByText("Moved 2 tasks to today")).toBeInTheDocument();

    failMove = true; dayBody = DAY();
    const r2 = renderAt();
    await waitFor(() => expect(screen.getAllByRole("button", { name: "Move all to today" }).length).toBeGreaterThan(0));
    const btn = within(r2.container).getByRole("button", { name: "Move all to today" });
    await u.click(btn);
    expect(await within(r2.container).findByText("Couldn't move them to today.")).toBeInTheDocument();
    expect(within(r2.container).getByRole("button", { name: "Try again" })).toBeInTheDocument();
    expect(within(r2.container).getByRole("region", { name: /^Catch up/ })).toBeInTheDocument();
  });

  it("marks a task done, moves it to Done today, and Undo reopens it", async () => {
    const u = userEvent.setup();
    renderAt();
    await screen.findByText("QC check MacBook Air keyboard");
    await u.click(screen.getByRole("button", { name: 'Mark "Call Jane about collection" done' }));
    expect(calls.filter((c) => c.method === "PATCH").pop()).toMatchObject({ url: "/api/tasks/t2", body: { status: "done" } });
    await waitFor(() => expect(screen.getByRole("button", { name: /^Done\s*2$/ })).toBeInTheDocument());
    await u.click(await screen.findByRole("button", { name: "Undo" }));
    expect(calls.filter((c) => c.method === "PATCH").pop()).toMatchObject({ url: "/api/tasks/t2", body: { status: "todo" } });
    await waitFor(() => expect(screen.getByRole("button", { name: /^Done\s*1$/ })).toBeInTheDocument());
  });

  it("Done is the fourth segment: today's done tasks, and older ones are a search away", async () => {
    const u = userEvent.setup();
    renderAt();
    await screen.findByText("QC check MacBook Air keyboard");
    expect(screen.queryByText("Open the till")).not.toBeInTheDocument();       // no folded "Done today" bar any more
    expect(screen.queryByRole("button", { name: /Done today/ })).not.toBeInTheDocument();
    const segs = within(screen.getByRole("group", { name: "Which days" })).getAllByRole("button").map((b) => b.textContent);
    expect(segs).toEqual(["Today5", "Upcoming1", "Later1", "Done1"]);
    await u.click(screen.getByRole("button", { name: /^Done\s*1$/ }));
    expect(screen.getByText("Open the till")).toBeInTheDocument();
    expect(screen.queryByText("QC check MacBook Air keyboard")).not.toBeInTheDocument();
    expect(screen.getByText(/Older tasks:/)).toBeInTheDocument();
    await u.click(screen.getByRole("button", { name: "use search" }));
    expect(screen.getByLabelText("Search all tasks")).toHaveFocus();
  });

  it("an empty Today with something coming up is one short line that leads to Upcoming", async () => {
    dayBody = { date: DATE, catch_up: [], today: [], upcoming: [{ id: "u1", title: "Order iPad Air battery", due_at: "2026-11-12", assigned_to_id: "me" }], later: [], done_today: [], counts: { catch_up: 0, today: 0, upcoming: 1, later: 0, done_today: 0 } };
    const u = userEvent.setup();
    renderAt();
    expect(await screen.findByText("Nothing due today")).toBeInTheDocument();
    expect(screen.getByText(/1 coming up/)).toBeInTheDocument();
    expect(screen.queryByText(/Your day is clear/)).not.toBeInTheDocument();      // not the big empty box
    await u.click(screen.getByRole("button", { name: "Show upcoming" }));
    expect(screen.getByRole("button", { name: /^Upcoming\s*1$/ })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByText("Order iPad Air battery")).toBeInTheDocument();
  });

  it("quick add: Enter saves for today with a date-only due, shows it at once", async () => {
    const u = userEvent.setup();
    renderAt();
    await screen.findByText("QC check MacBook Air keyboard");
    const box = screen.getByLabelText("Add a task");
    await u.type(box, "Ring Tom Reed about the quote{Enter}");
    expect(screen.getByText("Ring Tom Reed about the quote")).toBeInTheDocument();
    expect(posts("/api/tasks")[0].body).toMatchObject({ title: "Ring Tom Reed about the quote", due_at: DATE, assigned_to_id: "me", assignee_ids: ["me"], order_id: null });
    expect(box).toHaveValue("");
    expect(await screen.findByText("Task added for today")).toBeInTheDocument();
  });

  it("quick add: Tomorrow, another person and a job go in the request", async () => {
    const u = userEvent.setup();
    renderAt();
    await screen.findByText("QC check MacBook Air keyboard");
    const when = screen.getByRole("group", { name: "When" });
    await u.click(within(when).getByRole("button", { name: "Tomorrow" }));
    await u.click(within(screen.getByRole("group", { name: "Who" })).getByRole("button", { name: "Others" }));
    await u.selectOptions(screen.getByLabelText("Who it's for"), "e2");
    await u.click(screen.getByRole("button", { name: "Link a work order" }));
    await u.click(screen.getByRole("button", { name: "pick job" }));
    await u.type(screen.getByLabelText("Add a task"), "Order MacBook keyboard{Enter}");
    expect(posts("/api/tasks")[0].body).toMatchObject({ due_at: "2026-11-12", assigned_to_id: "e2", assignee_ids: ["e2"], order_id: "o9" });
    expect(await screen.findByText("Task added for Jane Kim, due tomorrow")).toBeInTheDocument();
  });

  it("quick add: a failed save takes the row away and gives the words back", async () => {
    failPost = true;
    const u = userEvent.setup();
    renderAt();
    await screen.findByText("QC check MacBook Air keyboard");
    await u.type(screen.getByLabelText("Add a task"), "Test the charge port{Enter}");
    expect(await screen.findByText("Couldn't add the task. Check your connection and try again.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Test the charge port" })).not.toBeInTheDocument();
    expect(screen.getByLabelText("Add a task")).toHaveValue("Test the charge port");
  });

  it("whose tasks: managers pick Everyone, Up for grabs or a person; staff see the same menu greyed", async () => {
    const u = userEvent.setup();
    const { unmount } = renderAt();
    await screen.findByText("QC check MacBook Air keyboard");
    const opts = () => [...(screen.getByLabelText("Whose tasks") as HTMLSelectElement).options].map((o) => [o.value, o.textContent, o.disabled]);
    expect(opts()).toEqual([["me", "Me", false], ["all", "Everyone", false], ["pool", "Up for grabs", false], ["e2", "Jane Kim", false], ["e3", "Tom Reed", false]]);
    await u.selectOptions(screen.getByLabelText("Whose tasks"), "all");
    await waitFor(() => expect(calls.filter((c) => c.url.startsWith("/api/tasks/my-day")).pop()!.url).toBe("/api/tasks/my-day?assignee=all"));
    await u.selectOptions(screen.getByLabelText("Whose tasks"), "e3");
    await waitFor(() => expect(calls.some((c) => c.url === "/api/tasks/my-day?assignee=e3")).toBe(true));
    unmount();
    manager = false;
    renderAt();
    await screen.findByText("QC check MacBook Air keyboard");
    expect(screen.getByLabelText("Whose tasks")).not.toBeDisabled();
    // greyed, never hidden: Everyone and the people are there but can't be picked; Up for grabs can
    expect(opts()).toEqual([["me", "Me", false], ["all", "Everyone (managers only)", true], ["pool", "Up for grabs", false], ["e2", "Jane Kim", true], ["e3", "Tom Reed", true]]);
    expect(screen.getByRole("group", { name: "People (managers only)" })).toBeInTheDocument();
    expect(within(screen.getByRole("group", { name: "Who" })).getByRole("button", { name: "Others" })).toBeDisabled();
  });

  it("search finds every task in the shop, history too; Clear search goes back to the day", async () => {
    listItems = [
      { id: "s1", title: "Call Jane about collection", status: "todo", assignee_ids: ["me"], assignee_names: ["Me"], due_at: "2026-11-11T11:00:00" },
      { id: "s2", title: "Order screen for Jane", status: "done", completed_at: "2026-11-03T10:00:00Z", assignee_ids: ["e2"], assignee_names: ["Jane Kim"] },
      { id: "s3", title: "Call Jane: second quote", status: "cancelled", assignee_ids: ["e2"], assignee_names: ["Jane Kim"] },
    ];
    const u = userEvent.setup();
    renderAt();
    await screen.findByText("QC check MacBook Air keyboard");
    await u.type(screen.getByLabelText("Search all tasks"), "jane");
    expect(await screen.findByText("Order screen for Jane")).toBeInTheDocument();
    const list = calls.filter((c) => c.url.startsWith("/api/tasks?") && !c.url.includes("limit=1&")).pop()!.url;
    expect(list).toContain("q=jane");
    expect(list).not.toContain("assignee=");                       // the whole shop, not only mine
    expect(list).not.toContain("status=");                         // done and cancelled too
    expect(screen.queryByText("QC check MacBook Air keyboard")).not.toBeInTheDocument();
    expect(screen.getByText(/across the shop, 2 from history/)).toBeInTheDocument();
    const done = screen.getByText("Order screen for Jane").closest("li")!;
    expect(within(done).getByText("Done Nov 3")).toBeInTheDocument();
    expect(within(done).getByRole("img", { name: "Assigned to Jane Kim" })).toBeInTheDocument();
    expect(within(screen.getByText("Call Jane: second quote").closest("li")!).getByText("Cancelled")).toBeInTheDocument();
    for (const b of within(screen.getByRole("group", { name: "Which days" })).getAllByRole("button")) expect(b).toBeDisabled();
    await u.click(screen.getByText("Order screen for Jane"));
    await waitFor(() => expect(at).toBe("/tasks?task=s2"));             // a result opens the same panel
    await u.click(screen.getByRole("button", { name: "Clear search" }));
    expect(await screen.findByText("QC check MacBook Air keyboard")).toBeInTheDocument();
    expect(screen.getByLabelText("Search all tasks")).toHaveValue("");
  });

  it("an empty day invites a task instead of apologising", async () => {
    dayBody = { date: DATE, catch_up: [], today: [], upcoming: [], later: [], done_today: [], counts: { catch_up: 0, today: 0, upcoming: 0, later: 0, done_today: 0 } };
    renderAt();
    expect(await screen.findByText("Nothing due today")).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: /^Catch up/ })).not.toBeInTheDocument();
  });

  it("shows a skeleton while loading and a retry when it fails", async () => {
    dayStatus = 500;
    const u = userEvent.setup();
    renderAt();
    expect(screen.getByRole("status", { name: "Loading your day" })).toBeInTheDocument();
    expect(await screen.findByText("Couldn't load your day")).toBeInTheDocument();
    dayStatus = 200;
    await u.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText("QC check MacBook Air keyboard")).toBeInTheDocument();
  });

  it("falls back to the flat list when the server has no My day yet, explained, segments greyed", async () => {
    dayStatus = 404;
    renderAt();
    expect(await screen.findByText("A task in the list")).toBeInTheDocument();
    expect(screen.queryByRole("tab")).not.toBeInTheDocument();                 // no My day | List tabs
    expect(screen.getByText("My day isn't ready yet. Here's the full list for now.")).toBeInTheDocument();
    for (const b of within(screen.getByRole("group", { name: "Which days" })).getAllByRole("button")) expect(b).toBeDisabled();
    expect(calls.filter((c) => c.url.startsWith("/api/tasks?")).pop()!.url).toContain("assignee=me");
    expect(screen.getByLabelText("Add a task")).toBeInTheDocument();             // quick add still works
  });
});

describe("Task deep links", () => {
  it("/tasks?task=<id> opens that task's panel on load; closing clears the parameter", async () => {
    const u = userEvent.setup();
    renderAt("/tasks?task=c1");
    expect(await screen.findByDisplayValue("Chase supplier RMA #A88")).toBeInTheDocument();
    expect(calls.some((c) => c.url === "/api/tasks/c1")).toBe(true);
    await u.click(screen.getByRole("button", { name: "Close" }));
    expect(at).toBe("/tasks");
    expect(screen.queryByLabelText("Task title")).not.toBeInTheDocument();
  });

  it("opens when the URL changes (a bell click while already on Tasks), and rows set the URL", async () => {
    const u = userEvent.setup();
    renderAt();
    await screen.findByText("QC check MacBook Air keyboard");
    act(() => go("/tasks?task=t2"));
    expect(await screen.findByDisplayValue("Call Jane about collection")).toBeInTheDocument();
    await u.keyboard("{Escape}");
    expect(at).toBe("/tasks");
    await u.click(screen.getByRole("button", { name: "Chase supplier RMA #A88" }));
    expect(at).toBe("/tasks?task=c1");
    expect(await screen.findByDisplayValue("Chase supplier RMA #A88")).toBeInTheDocument();
  });

  it("a task that is gone or not yours: same neutral message, no panel, parameter cleared", async () => {
    for (const id of ["gone", "secret"]) {
      const { unmount } = renderAt(`/tasks?task=${id}`);
      await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
      await waitFor(() => expect(at).toBe("/tasks"));
      expect(await screen.findByText("This task isn't available")).toBeInTheDocument();
      expect(await screen.findByText("QC check MacBook Air keyboard")).toBeInTheDocument();   // My day still there
      unmount();
    }
  });

  it("the panel takes focus, keeps Tab inside, and Back after closing doesn't reopen it", async () => {
    const u = userEvent.setup();
    renderAt();
    const rowBtn = await screen.findByRole("button", { name: "Call Jane about collection" });
    await u.click(rowBtn);
    const dialog = await screen.findByRole("dialog");
    await screen.findByDisplayValue("Call Jane about collection");
    expect(dialog.contains(document.activeElement)).toBe(true);
    for (let i = 0; i < 25; i++) await u.tab();
    expect(dialog.contains(document.activeElement)).toBe(true);
    await u.click(within(dialog).getAllByRole("button", { name: "Close" })[0]);
    expect(at).toBe("/tasks");
    expect(document.activeElement).toBe(rowBtn);
    act(() => go(-1 as any));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});

describe("My day: the rest of the acceptance list", () => {
  it("quick add: a double Enter creates one task, sent with an idempotency key", async () => {
    const u = userEvent.setup();
    renderAt();
    await screen.findByText("QC check MacBook Air keyboard");
    await u.type(screen.getByLabelText("Add a task"), "Order Pixel 8 screen{Enter}{Enter}");
    await screen.findByText("Task added for today");
    expect(posts("/api/tasks")).toHaveLength(1);
    const init = (fetch as any).mock.calls.find((c: any[]) => c[0] === "/api/tasks" && c[1]?.method === "POST")[1];
    expect(init.headers["Idempotency-Key"]).toBeTruthy();
  });

  it("quick add for someone else doesn't land in my Today, and names who it went to", async () => {
    const u = userEvent.setup();
    renderAt();
    await screen.findByText("QC check MacBook Air keyboard");
    await u.click(within(screen.getByRole("group", { name: "Who" })).getByRole("button", { name: "Others" }));
    await u.selectOptions(screen.getByLabelText("Who it's for"), "e3");
    dayBody = DAY();   // the server's "me" view won't include it either
    failPost = false;
    const before = screen.getAllByRole("listitem").length;
    await u.type(screen.getByLabelText("Add a task"), "Test Tom's MacBook keyboard{Enter}");
    expect(await screen.findByText("Task added for Tom Reed, due today")).toBeInTheDocument();
    expect(screen.getAllByRole("listitem").length).toBeLessThanOrEqual(before + 1);
    expect(screen.queryByRole("button", { name: "Test Tom's MacBook keyboard" })).toBeNull();
  });

  it("an empty title can't be added, and says why", async () => {
    renderAt();
    await screen.findByText("QC check MacBook Air keyboard");
    const add = screen.getByRole("button", { name: "Add task" });
    expect(add).toHaveAttribute("aria-disabled", "true");
    await userEvent.setup().click(add);
    expect(screen.getByText("Type what needs doing first.")).toBeVisible();   // a tap says why, on a phone too
    expect(posts("/api/tasks")).toHaveLength(0);
  });

  it("Pick date sends the chosen date, with a time only when one is set", async () => {
    const u = userEvent.setup();
    renderAt();
    await screen.findByText("QC check MacBook Air keyboard");
    await u.click(within(screen.getByRole("group", { name: "When" })).getByRole("button", { name: "Pick date" }));
    await u.type(screen.getByLabelText("Add a task"), "Renew the bench insurance{Enter}");
    expect(await screen.findByText("Pick a date first")).toBeInTheDocument();
    expect(posts("/api/tasks")).toHaveLength(0);
  });

  it("whose day lives in the URL for managers; staff with a pasted link get their own day", async () => {
    const u = userEvent.setup();
    const { unmount } = renderAt();
    await screen.findByText("QC check MacBook Air keyboard");
    await u.selectOptions(screen.getByLabelText("Whose tasks"), "e2");
    expect(at).toBe("/tasks?assignee=e2");
    unmount();
    manager = false;
    calls = [];
    renderAt("/tasks?assignee=e2");
    await screen.findByText("QC check MacBook Air keyboard");
    expect(calls.filter((c) => c.url.startsWith("/api/tasks/my-day")).every((c) => c.url.startsWith("/api/tasks/my-day?assignee=me"))).toBe(true);
  });

  it("Everyone groups the day by person; Me is the plain list", async () => {
    const u = userEvent.setup();
    renderAt();
    await screen.findByText("QC check MacBook Air keyboard");
    expect(screen.queryByRole("region", { name: "Everyone's tasks, by person" })).not.toBeInTheDocument();
    await u.selectOptions(screen.getByLabelText("Whose tasks"), "all");
    const list = await screen.findByRole("region", { name: "Everyone's tasks, by person" });
    expect(within(list).getByRole("heading", { name: /^Me ?\(you\)/ })).toBeInTheDocument();
  });

  it("a ticked task offers Undo in its row too, and Undo puts back In progress", async () => {
    dayBody.today[1].status = "in_progress";
    const u = userEvent.setup();
    renderAt();
    await screen.findByText("QC check MacBook Air keyboard");
    await u.click(screen.getByRole("button", { name: 'Mark "Call Jane about collection" done' }));
    await u.click(await screen.findByRole("button", { name: /^Done\s*2$/ }));
    await u.click(await screen.findByRole("button", { name: 'Undo: mark "Call Jane about collection" not done' }));
    expect(calls.filter((c) => c.method === "PATCH").pop()).toMatchObject({ url: "/api/tasks/t2", body: { status: "in_progress" } });
  });

  it("uses the server's counts, and says when a group has more than it sent", async () => {
    dayBody.counts.catch_up = 137;
    renderAt();
    await screen.findByText("QC check MacBook Air keyboard");
    expect(screen.getByRole("heading", { name: "Catch up, 137 tasks" })).toBeInTheDocument();
    expect(screen.getByText(/Showing 2 of 137/)).toBeInTheDocument();
    const u = userEvent.setup();
    await u.click(screen.getByRole("button", { name: "Search" }));                 // no List view any more: search finds any of them
    expect(screen.getByLabelText("Search all tasks")).toHaveFocus();
  });

  it("an empty day puts the cursor in quick add", async () => {
    dayBody = { date: DATE, catch_up: [], today: [], upcoming: [], later: [], done_today: [], counts: { catch_up: 0, today: 0, upcoming: 0, later: 0, done_today: 0 } };
    renderAt();
    await screen.findByText("Nothing due today");
    await waitFor(() => expect(screen.getByLabelText("Add a task")).toHaveFocus());
  });
});

describe("the shop's clock (due times are UTC instants)", () => {
  const SAME_DAY = () => ({
    date: DATE,
    catch_up: [], upcoming: [], later: [],
    today: [
      { id: "a", title: "Order iPhone 14 screen", due_at: "2026-11-11T04:00:00" },        // 09:30 IST · 04:00 UTC
      { id: "b", title: "QC MacBook keyboard", due_at: "2026-11-11T09:30:00" },           // 15:00 IST · 09:30 UTC
      { id: "c", title: "Call Jane about pickup", due_at: "2026-11-11T12:30:00" },        // 18:00 IST · 12:30 UTC
      { id: "d", title: "Count iPhone screens", due_at: "2026-11-11T18:29:59" },          // date-only in IST: end of shop day
    ],
    done_today: [{ id: "z", title: "Open the till", status: "done", completed_at: "2026-11-11T08:55:00" }],   // 14:25 IST
    counts: { catch_up: 0, today: 4, upcoming: 0, later: 0, done_today: 1 },
  });
  const heads = () => screen.getAllByRole("heading", { level: 3 }).map((h) => h.textContent);
  const timeOf = (title: string) => screen.getByText(title).closest("li")!.textContent!;

  it("IST shop: groups by the shop's time of day; a date-only task is 'Any time today', not Evening", async () => {
    shopTz = "Asia/Kolkata"; dayBody = SAME_DAY();
    renderAt();
    await screen.findByText("Order iPhone 14 screen");
    expect(heads()).toEqual(["Morning1", "Afternoon1", "Evening1", "Any time today1"]);
    expect(timeOf("Order iPhone 14 screen")).toMatch(/9:30\s?AM/);
    expect(timeOf("Call Jane about pickup")).toMatch(/6:00\s?PM/);
    expect(timeOf("Count iPhone screens")).not.toMatch(/PM|AM/);
    const u = userEvent.setup();
    await u.click(screen.getByRole("button", { name: /^Done/ }));
    expect(timeOf("Open the till")).toMatch(/2:25\s?PM/);
  });

  it("UTC shop: the same instants group differently", async () => {
    shopTz = "UTC"; dayBody = SAME_DAY();
    renderAt();
    await screen.findByText("Order iPhone 14 screen");
    expect(heads()).toEqual(["Morning2", "Afternoon1", "Evening1"]);
    expect(timeOf("Order iPhone 14 screen")).toMatch(/4:00\s?AM/);
  });

  it("IST shop: the task panel and the list show the shop's time", async () => {
    shopTz = "Asia/Kolkata";
    const { unmount } = renderAt("/tasks?task=c1");
    expect(await screen.findByText(/Due Nov 6, 5:00\s?PM/)).toBeInTheDocument();     // 11:30 UTC
    unmount();
    dayStatus = 404;
    renderAt();
    expect(await screen.findByText(/Nov 11, 9:30\s?AM/)).toBeInTheDocument();
  });

  it("Move all to today says how many couldn't be moved", async () => {
    moveReply = { moved: 1, skipped: ["c2"] };
    const u = userEvent.setup();
    renderAt();
    await screen.findByText("QC check MacBook Air keyboard");
    await u.click(screen.getByRole("button", { name: "Move all to today" }));
    expect(await screen.findByText("Moved 1 task to today. 1 couldn't be moved")).toBeInTheDocument();
  });
});

describe("review fixes", () => {
  const POOL = { id: "p1", title: "Replace Switch HDMI port", due_at: `${DATE}T12:00:00`, created_by_id: "e2", assigned_to_id: null, assignee_ids: [] };
  const MY_TODO = { id: "m1", title: "Order more screen protectors", due_at: `${DATE}T23:59:59`, created_by_id: "me", assigned_to_id: null, assignee_ids: [] };

  it("staff see 'Up for grabs' (include_pool) and can take a task, which joins their day", async () => {
    manager = false;
    dayBody.today.push(POOL, MY_TODO); dayBody.counts.today += 2;
    const u = userEvent.setup();
    renderAt();
    const pool = await screen.findByRole("region", { name: /^Up for grabs/ });
    expect(calls.find((c) => c.url.startsWith("/api/tasks/my-day"))!.url).toBe("/api/tasks/my-day?assignee=me&include_pool=true");
    expect(within(pool).getByText("Replace Switch HDMI port")).toBeInTheDocument();
    expect(within(pool).queryByText("Order more screen protectors")).toBeNull();      // my own to-do stays in my day
    expect(screen.getByRole("button", { name: /Today\s*6/ })).toBeInTheDocument();     // the pool isn't counted as mine
    await u.click(within(pool).getByRole("button", { name: "Take it: Replace Switch HDMI port" }));
    expect(calls.filter((c) => c.method === "PATCH").pop()).toMatchObject({ url: "/api/tasks/p1", body: { assignee_ids: ["me"] } });
    expect(await screen.findByText("It's yours: Replace Switch HDMI port")).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByRole("region", { name: /^Up for grabs/ })).toBeNull());
    expect(screen.getByRole("button", { name: 'Mark "Replace Switch HDMI port" done' })).toBeInTheDocument();
  });

  it("taking a task someone else got first puts it back and says so; managers don't get a pool", async () => {
    manager = false; patchFail = true;
    dayBody.today.push(POOL);
    const u = userEvent.setup();
    const { unmount } = renderAt();
    const pool = await screen.findByRole("region", { name: /^Up for grabs/ });
    await u.click(within(pool).getByRole("button", { name: /Take/ }));
    expect(await screen.findByText("Couldn't take it. Someone may have taken it already.")).toBeInTheDocument();
    unmount();
    manager = true; patchFail = false; calls = [];
    renderAt();
    await screen.findByText("QC check MacBook Air keyboard");
    expect(calls.find((c) => c.url.startsWith("/api/tasks/my-day"))!.url).toBe("/api/tasks/my-day?assignee=me");
  });

  it("a removed person's day (404) goes back to Me; it doesn't turn My day off", async () => {
    const u = userEvent.setup();
    renderAt();
    await screen.findByText("QC check MacBook Air keyboard");
    goneId = "e3";
    await u.selectOptions(screen.getByLabelText("Whose tasks"), "e3");
    expect(await screen.findByText("That person's day isn't available")).toBeInTheDocument();
    await waitFor(() => expect(at).toBe("/tasks"));
    expect(screen.queryByText("My day isn't ready yet. Here's the full list for now.")).not.toBeInTheDocument();
    expect(within(screen.getByRole("group", { name: "Which days" })).getByRole("button", { name: /^Today/ })).not.toBeDisabled();
  });

  it("Move all to today moves ALL of them, page after page, not only the 100 on screen", async () => {
    dayBody.catch_up = Array.from({ length: 137 }, (_, i) => ({ id: `late${i}`, title: `Late ${i}`, due_at: "2026-11-05T09:00:00", overdue: true }));
    const u = userEvent.setup();
    renderAt();
    expect(await screen.findByRole("heading", { name: "Catch up, 137 tasks" })).toBeInTheDocument();
    moveReply = {} as any;   // like the server, count what moved
    await u.click(screen.getByRole("button", { name: "Move all to today" }));
    expect(await screen.findByText("Moved 137 tasks to today")).toBeInTheDocument();
    const moves = posts("/api/tasks/move-to-today");
    expect(moves.length).toBe(2);
    expect(moves.flatMap((m) => m.body.ids)).toHaveLength(137);
    expect(screen.queryByRole("region", { name: /^Catch up/ })).toBeNull();
  });

  it("if the shop's clock can't be reached: a retry, and no times saved on a guess", async () => {
    tzFail = true;
    const u = userEvent.setup();
    renderAt();
    expect(await screen.findByText("Couldn't reach your shop's clock")).toBeInTheDocument();
    await u.type(screen.getByLabelText("Add a task"), "Order Pixel 8 screen{Enter}");
    expect(screen.getByText("Can't save until your shop's clock loads.")).toBeVisible();
    expect(posts("/api/tasks")).toHaveLength(0);
    tzFail = false;
    await u.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText("QC check MacBook Air keyboard")).toBeInTheDocument();
  });

  it("reloads when the page is shown again (a new shop day may have started)", async () => {
    renderAt();
    await screen.findByText("QC check MacBook Air keyboard");
    const before = calls.filter((c) => c.url.startsWith("/api/tasks/my-day")).length;
    act(() => { document.dispatchEvent(new Event("visibilitychange")); });
    await waitFor(() => expect(calls.filter((c) => c.url.startsWith("/api/tasks/my-day")).length).toBe(before + 1));
  });

  it("announces Done and Undo through a live region that is always on the page", async () => {
    const u = userEvent.setup();
    renderAt();
    await screen.findByText("QC check MacBook Air keyboard");
    const live = document.querySelector('[aria-live="polite"][role="status"]')!;
    expect(live).toBeInTheDocument();
    expect(live.textContent).toBe("");
    await u.click(screen.getByRole("button", { name: 'Mark "Call Jane about collection" done' }));
    await waitFor(() => expect(live.textContent).toContain("Done: Call Jane about collection"));
  });

  it("UK shop: a late task shows a clear date ('Fri 6 Nov'), times are 24-hour, and the chip says who made it", async () => {
    shopTz = "Europe/London"; shopCountry = "GB";
    renderAt();
    await screen.findByText("QC check MacBook Air keyboard");
    expect(screen.getByText("Chase supplier RMA #A88").closest("li")!.textContent).toMatch(/Fri 6 Nov/);
    expect(screen.getByText("QC check MacBook Air keyboard").closest("li")!.textContent).toMatch(/15:00/);
    expect(screen.getByText("Call Jane about collection").closest("li")!.textContent).toMatch(/Made by Tekly/);
  });

  it("staff see why things are greyed, written on the page (not hover-only)", async () => {
    manager = false;
    renderAt();
    await screen.findByText("QC check MacBook Air keyboard");
    expect(screen.getByRole("option", { name: "Everyone (managers only)" })).toBeDisabled();
    expect(screen.getByText("Only managers can give tasks to others.")).toBeVisible();
  });
});

describe("one-page Tasks (v2)", () => {
  const TEAM = () => ({
    date: DATE,
    catch_up: [{ id: "c1", title: "Chase supplier RMA", due_at: "2026-11-06T17:00:00", overdue: true, assigned_to_id: "e3", assignee_ids: ["e3"], assignee_names: ["Tom Reed"] }],
    today: [
      { id: "t1", title: "QC check MacBook Air keyboard", due_at: `${DATE}T15:00:00`, assigned_to_id: "me", assignee_ids: ["me", "e3"], assignee_names: ["Me", "Tom Reed"] },
      { id: "t2", title: "Ring Ana about the iPad", due_at: `${DATE}T11:00:00`, assigned_to_id: "e2", assignee_ids: ["e2"], assignee_names: ["Jane Kim"] },
      { id: "p1", title: "Test the new charge port stock", due_at: `${DATE}T13:00:00`, assigned_to_id: null, assignee_ids: [], created_by_id: "e2" },
    ],
    upcoming: [{ id: "p2", title: "Restock screen protectors", due_at: "2026-11-13", assigned_to_id: null, assignee_ids: [] }],
    later: [],
    done_today: [{ id: "d1", title: "Open the till", status: "done", completed_at: `${DATE}T08:55:00Z`, assigned_to_id: "e3", assignee_ids: ["e3"], assignee_names: ["Tom Reed"] }],
    counts: { catch_up: 1, today: 3, upcoming: 1, later: 0, done_today: 1 },
  });
  const myDayCalls = () => calls.filter((c) => c.url.startsWith("/api/tasks/my-day")).map((c) => c.url);

  const region = () => screen.getByRole("region", { name: "Everyone's tasks, by person" });
  // What a screen reader says for each header (avatars and icons are hidden from it).
  const heads = () => within(region()).getAllByRole("heading", { level: 3 }).map((h) => {
    const c = h.cloneNode(true) as HTMLElement;
    c.querySelectorAll('[aria-hidden="true"], i').forEach((x) => x.remove());
    return c.textContent!.replace(/\s+/g, " ").trim();
  });
  const groupOf = (name: RegExp) => {
    const btn = within(region()).getByRole("button", { name });
    return document.getElementById(btn.getAttribute("aria-controls")!)!;
  };

  it("Everyone (managers): one list grouped by person, Up for grabs first, overdue first in each group; no team cards", async () => {
    dayBody = TEAM();
    const u = userEvent.setup();
    renderAt("/tasks?assignee=all");
    await screen.findByText("Ring Ana about the iPad");
    expect(screen.queryByRole("group", { name: "Team" })).not.toBeInTheDocument();       // the cards are gone (owner)
    expect(heads()).toEqual([
      "Up for grabs, Nobody has these yet: 2 waiting",
      "Me (you): 0 overdue · 1 today · 0 done",
      "Jane Kim: 0 overdue · 1 today · 0 done",
      "Tom Reed: 1 overdue · 1 today · 1 done",
    ]);
    const tom = within(groupOf(/^Tom Reed/)).getAllByRole("listitem").map((li) => li.textContent);
    expect(tom[0]).toContain("Chase supplier RMA");                                     // overdue leads
    expect(tom[1]).toContain("QC check MacBook Air keyboard");                         // shared: under Me and Tom
    expect(within(groupOf(/^Me ?\(you\)/)).getByText("QC check MacBook Air keyboard")).toBeInTheDocument();
    expect(within(groupOf(/^Up for grabs/)).getByText("Test the new charge port stock")).toBeInTheDocument();
    expect(within(region()).getByText(/overdue across the team/)).toBeInTheDocument();
    expect(within(region()).getByRole("button", { name: "Move all to today" })).toBeInTheDocument();
    // the menu still picks a person or Up for grabs
    await u.selectOptions(screen.getByLabelText("Whose tasks"), "pool");
    expect(at).toBe("/tasks?assignee=pool");
    await screen.findByText("Test the new charge port stock");
    expect(screen.queryByText("Ring Ana about the iPad")).not.toBeInTheDocument();
  });

  it("Everyone: header numbers are the server's exact counts; someone with nothing today folds to 'All clear'", async () => {
    dayBody = { ...TEAM(), team: [{ employee_id: "me", overdue: 0, today: 140, done_today: 2 }, { employee_id: "e3", overdue: 3, today: 41, done_today: 7 }],
      pool: { overdue: 1, today: 120, upcoming: 4, later: 2 } };
    dayBody.today = dayBody.today.filter((t: any) => t.id !== "t2");                    // Jane: nothing today
    renderAt("/tasks?assignee=all");
    await screen.findByText("Chase supplier RMA");
    expect(heads()).toEqual([
      "Up for grabs, Nobody has these yet: 1 overdue · 127 waiting",
      "Me (you): 0 overdue · 140 today · 2 done",
      "Tom Reed: 3 overdue · 41 today · 7 done",
      "Jane Kim: All clear",                                                              // folded, last
    ]);
    expect(within(region()).queryByRole("button", { name: /^Jane Kim/ })).not.toBeInTheDocument();   // nothing to open
  });

  it("Everyone: a person's list folds and opens from their header", async () => {
    dayBody = TEAM();
    const u = userEvent.setup();
    renderAt("/tasks?assignee=all");
    await screen.findByText("Ring Ana about the iPad");
    const jane = within(region()).getByRole("button", { name: /^Jane Kim/ });
    expect(jane).toHaveAttribute("aria-expanded", "true");
    await u.click(jane);
    expect(jane).toHaveAttribute("aria-expanded", "false");
    expect(groupOf(/^Jane Kim/).className).toContain("grid-rows-[0fr]");
    await u.click(jane);
    expect(groupOf(/^Jane Kim/).className).toContain("grid-rows-[1fr]");
  });

  it("Everyone, other segments: each person's count for that segment; Done keeps the search hint", async () => {
    dayBody = TEAM();
    const u = userEvent.setup();
    renderAt("/tasks?assignee=all");
    await screen.findByText("Ring Ana about the iPad");
    await u.click(screen.getByRole("button", { name: /^Upcoming/ }));
    expect(heads()[0]).toBe("Up for grabs, Nobody has these yet: 1 coming up");
    expect(heads()).toContain("Tom Reed: Nothing coming up");
    await u.click(screen.getByRole("button", { name: /^Done/ }));
    expect(heads()).toContain("Tom Reed: 1 done today");
    expect(within(region()).getByText(/Older tasks/)).toBeInTheDocument();
  });

  it("one person picked loads only that person's day (no second fetch of everyone's)", async () => {
    dayBody = TEAM();
    renderAt("/tasks?assignee=e3");
    await screen.findByText("Ring Ana about the iPad");
    await new Promise((r) => setTimeout(r, 400));
    expect(myDayCalls()).toEqual(["/api/tasks/my-day?assignee=e3"]);
    expect(screen.queryByRole("group", { name: "Team" })).not.toBeInTheDocument();
  });

  it("quick add gives the task to whoever is shown: a person, or nobody for Up for grabs", async () => {
    const u = userEvent.setup();
    const { unmount } = renderAt("/tasks?assignee=e3");
    await screen.findByLabelText("Add a task");
    await waitFor(() => expect(within(screen.getByRole("group", { name: "Who" })).getByRole("button", { name: "Tom" })).toHaveAttribute("aria-pressed", "true"));
    await u.type(screen.getByLabelText("Add a task"), "Label the returned Galaxy{Enter}");
    expect(posts("/api/tasks")[0].body).toMatchObject({ assigned_to_id: "e3", assignee_ids: ["e3"] });
    expect(await screen.findByText("Task added for Tom Reed, due today")).toBeInTheDocument();
    unmount();
    calls = [];
    renderAt("/tasks?assignee=pool");
    await screen.findByLabelText("Add a task");
    expect(within(screen.getByRole("group", { name: "Who" })).getByRole("button", { name: "Up for grabs" })).toHaveAttribute("aria-pressed", "true");
    await u.type(screen.getByLabelText("Add a task"), "Clean the ultrasonic bath{Enter}");
    expect(posts("/api/tasks")[0].body).toMatchObject({ assigned_to_id: null, assignee_ids: [] });
    expect(await screen.findByText("Added to Up for grabs, due today")).toBeInTheDocument();
  });

  it("staff can open Up for grabs and take a task from it", async () => {
    manager = false; dayBody = TEAM();
    const u = userEvent.setup();
    renderAt("/tasks?assignee=pool");
    await screen.findByText("Test the new charge port stock");
    expect(calls.filter((c) => c.url.startsWith("/api/tasks/my-day")).pop()!.url).toBe("/api/tasks/my-day?assignee=me&include_pool=true");
    await u.click(screen.getByRole("button", { name: "Take it: Test the new charge port stock" }));
    expect(calls.filter((c) => c.method === "PATCH").pop()).toMatchObject({ url: "/api/tasks/p1", body: { assignee_ids: ["me"] } });
  });

  it("Add task with nothing typed is greyed in place (chip grey, not faded sapphire) and says why when tapped", async () => {
    const u = userEvent.setup();
    renderAt();
    await screen.findByText("QC check MacBook Air keyboard");
    const add = screen.getByRole("button", { name: "Add task" });
    expect(add).toHaveAttribute("aria-disabled", "true");
    expect(add.className).toContain("!bg-slate-100");
    expect(add.className).not.toContain("bg-primary");
    expect(add.className).not.toContain("opacity-50 cursor");
    await u.click(add);
    expect(screen.getByText("Type what needs doing first.")).toBeInTheDocument();
    await u.type(screen.getByLabelText("Add a task"), "Ring Jane");
    expect(add).toHaveAttribute("aria-disabled", "false");
    expect(add.className).toContain("bg-primary");
  });
});

describe("one-page Tasks (v2): review fixes", () => {
  const TEAM = () => ({
    date: DATE,
    catch_up: [{ id: "c1", title: "Chase supplier RMA", due_at: "2026-11-06T17:00:00", overdue: true, assigned_to_id: "e3", assignee_ids: ["e3"], assignee_names: ["Tom Reed"] }],
    today: [
      { id: "t1", title: "QC check MacBook Air keyboard", due_at: `${DATE}T15:00:00`, assigned_to_id: "e3", assignee_ids: ["e3"], assignee_names: ["Tom Reed"] },
      { id: "t2", title: "Ring Ana about the iPad", due_at: `${DATE}T11:00:00`, assigned_to_id: "e3", assignee_ids: ["e3"], assignee_names: ["Tom Reed"] },
      { id: "p1", title: "Test the new charge port stock", due_at: `${DATE}T13:00:00`, assigned_to_id: null, assignee_ids: [] },
    ],
    upcoming: [], later: [],
    done_today: [{ id: "d1", title: "Open the till", status: "done", completed_at: `${DATE}T08:55:00Z`, assigned_to_id: "e3", assignee_ids: ["e3"], assignee_names: ["Tom Reed"] }],
    counts: { catch_up: 1, today: 3, upcoming: 0, later: 0, done_today: 1 },
  });
  it("an Up for grabs cut from a capped day (older server) says there may be more", async () => {
    dayBody = TEAM(); dayBody.counts.today = 180;                                   // the server sent 3 of 180
    renderAt("/tasks?assignee=pool");
    await screen.findByText("Test the new charge port stock");
    expect(screen.getByRole("button", { name: /^Today\s*1$/ })).toBeInTheDocument();  // what is listed, never a mix with 180
    expect(screen.getByText(/Showing 1, there may be more/)).toBeInTheDocument();
  });

  it("without My day, Up for grabs can't be listed: it says so and shows the full list", async () => {
    dayStatus = 404; manager = false;
    renderAt("/tasks?assignee=pool");
    expect(await screen.findByText("A task in the list")).toBeInTheDocument();
    expect(screen.getByText(/Up for grabs needs My day/)).toBeInTheDocument();
    expect(calls.filter((c) => c.url.startsWith("/api/tasks?")).pop()!.url).not.toContain("assignee=");
  });

  it("'from history' is the server's count of every closed match, not one page's", async () => {
    listPaged = true; closedTotal = 37;
    listItems = [{ id: "s1", title: "Call Jane about collection", status: "todo", assignee_ids: ["me"], assignee_names: ["Me"] },
      { id: "s2", title: "Order screen for Jane", status: "done", completed_at: "2026-11-03T10:00:00Z", assignee_ids: ["e2"], assignee_names: ["Jane Kim"] }];
    const u = userEvent.setup();
    renderAt();
    await screen.findByText("QC check MacBook Air keyboard");
    await u.type(screen.getByLabelText("Search all tasks"), "jane");
    expect(await screen.findByText(/across the shop, 37 from history/)).toBeInTheDocument();
    const count = calls.find((c) => c.url.includes("status=done%2Ccancelled"))!.url;
    expect(count).toContain("q=jane");
    expect(count).toContain("limit=1");
  });

  it("quick add sets a priority (Normal by default), sends it, and goes back to Normal", async () => {
    const u = userEvent.setup();
    renderAt();
    await screen.findByText("QC check MacBook Air keyboard");
    expect(screen.getByLabelText("Task priority")).toHaveValue("0");
    await u.type(screen.getByLabelText("Add a task"), "Ring Jane{Enter}");
    expect(posts("/api/tasks")[0].body).toMatchObject({ priority: 0 });
    await u.selectOptions(screen.getByLabelText("Task priority"), "2");
    await u.type(screen.getByLabelText("Add a task"), "Order the OLED today{Enter}");
    expect(posts("/api/tasks")[1].body).toMatchObject({ title: "Order the OLED today", priority: 2 });
    await waitFor(() => expect(screen.getByLabelText("Task priority")).toHaveValue("0"));
  });

  it("Pick date still takes an exact time", async () => {
    const u = userEvent.setup();
    renderAt();
    await screen.findByText("QC check MacBook Air keyboard");
    await u.click(within(screen.getByRole("group", { name: "When" })).getByRole("button", { name: "Pick date" }));
    expect(screen.getByTitle("Optional: leave empty for any time that day")).toBeInTheDocument();
  });
});

describe("server pool + search fix", () => {
  const POOL = () => {
    const today = Array.from({ length: 100 }, (_, i) => ({ id: `p${i}`, title: `Pool task ${i}`, due_at: `${DATE}T13:00:00`, assigned_to_id: null, assignee_ids: [] }));
    return { date: DATE, catch_up: [], today, upcoming: [], later: [], done_today: [], counts: { catch_up: 0, today: 250, upcoming: 0, later: 0, done_today: 0 } };
  };

  it("Up for grabs asks the server for the pool (exact counts, no client cut) when it can", async () => {
    poolServer = { pool: POOL() };
    renderAt("/tasks?assignee=pool");
    await screen.findByText("Pool task 0");
    const asked = calls.filter((c) => c.url.startsWith("/api/tasks/my-day")).map((c) => c.url);
    expect(asked).toContain("/api/tasks/my-day?assignee=pool");
    expect(asked.some((u) => u.includes("include_pool"))).toBe(false);
    expect(screen.getByRole("button", { name: /^Today\s*250$/ })).toBeInTheDocument();
    expect(screen.getByText(/Showing 100 of 250/)).toBeInTheDocument();
  });

  it("staff: Up for grabs uses assignee=pool too, and a taken task leaves the list", async () => {
    manager = false; poolServer = { pool: POOL() };
    const u = userEvent.setup();
    renderAt("/tasks?assignee=pool");
    await screen.findByText("Pool task 3");
    expect(calls.filter((c) => c.url.startsWith("/api/tasks/my-day")).pop()!.url).toBe("/api/tasks/my-day?assignee=pool");
    await u.click(screen.getByRole("button", { name: "Take it: Pool task 3" }));
    expect(screen.queryByText("Pool task 3")).not.toBeInTheDocument();
    expect(calls.filter((c) => c.method === "PATCH").pop()).toMatchObject({ url: "/api/tasks/p3", body: { assignee_ids: ["me"] } });
  });

  it("an older server that doesn't know 'pool' (404 manager, 403 staff, 422): falls back to cutting the pool from a day", async () => {
    for (const [isMgr, want] of [[true, "/api/tasks/my-day?assignee=all"], [false, "/api/tasks/my-day?assignee=me&include_pool=true"]] as const) {
      calls = []; manager = isMgr; dayBody = DAY(); dayBody.today.push({ id: "pp", title: "Unclaimed pool job", due_at: `${DATE}T10:00:00`, assigned_to_id: null, assignee_ids: [], created_by_id: "e2" });
      dayBody.counts.today += 1;
      const { unmount } = renderAt("/tasks?assignee=pool");
      await screen.findByText("Unclaimed pool job");
      const asked = calls.filter((c) => c.url.startsWith("/api/tasks/my-day") && !c.url.endsWith("assignee=all") || c.url === want).map((c) => c.url);
      expect(asked[0]).toBe("/api/tasks/my-day?assignee=pool");
      expect(asked).toContain(want);
      expect(screen.queryByText("QC check MacBook Air keyboard")).not.toBeInTheDocument();   // only the pool
      expect(at).toBe("/tasks?assignee=pool");                                               // not bounced to Me
      unmount();
    }
  });

  it("search: a paged server's rows are shown as sent, so the rows and the total agree", async () => {
    listPaged = true;
    // The server matched this one through its job's client; the task's own client differs.
    listItems = [{ id: "x9", title: "Replace the battery", client_name: "Bob Stone", order_number: "WO-7", assignee_ids: ["e2"] }];
    const u = userEvent.setup();
    renderAt();
    await screen.findByText("QC check MacBook Air keyboard");
    await u.type(screen.getByLabelText("Search all tasks"), "ana lee");
    expect(await screen.findByText("Replace the battery")).toBeInTheDocument();
    expect(screen.getByText(/task matches/).textContent).toMatch(/^1 task matches/);   // the total, and the one row
  });

  it("the old full-list API is still narrowed here", async () => {
    dayStatus = 404;
    listItems = [{ id: "x1", title: "Mine", assignee_ids: ["me"] }, { id: "x2", title: "Not mine", assignee_ids: ["e2"], created_by_id: "e2" }];
    renderAt();
    expect(await screen.findByText("Mine")).toBeInTheDocument();
    expect(screen.queryByText("Not mine")).not.toBeInTheDocument();
  });

  it("a critical task (3) shows as Urgent, and saving other fields never turns it into 2", async () => {
    const u = userEvent.setup();
    renderAt("/tasks?task=crit");
    const title = await screen.findByLabelText("Task title");
    await waitFor(() => expect(title).toHaveValue("Board-level repair for Ana"));
    expect(screen.getByLabelText("Priority")).toHaveValue("2");                 // reads as Urgent
    await u.clear(title); await u.type(title, "Board-level repair"); await u.tab();
    const patches = () => calls.filter((c) => c.method === "PATCH");
    expect(patches().pop()!.body).toEqual({ title: "Board-level repair" });
    await u.selectOptions(screen.getByLabelText("Priority"), "2");              // "Urgent" again: no change
    expect(patches().every((c) => !("priority" in (c.body || {})))).toBe(true);
    await u.selectOptions(screen.getByLabelText("Priority"), "1");              // a real change is sent
    expect(patches().pop()!.body).toEqual({ priority: 1 });
  });

  it("Pick date opens a panel over the page; the chip row keeps its children, and Today closes it", async () => {
    const u = userEvent.setup();
    renderAt();
    await screen.findByText("QC check MacBook Air keyboard");
    const when = screen.getByRole("group", { name: "When" });
    const rowBefore = [...when.parentElement!.parentElement!.children].map((el) => el.tagName + el.className);
    await u.click(within(when).getByRole("button", { name: "Pick date" }));
    const panel = screen.getByRole("dialog", { name: "Due date" });
    expect(panel.className).toContain("absolute");                              // drawn over, not in the row
    expect(within(panel).getByTitle("Optional: leave empty for any time that day")).toBeInTheDocument();
    expect([...when.parentElement!.parentElement!.children].map((el) => el.tagName + el.className)).toEqual(rowBefore);
    await u.click(within(when).getByRole("button", { name: "Today" }));
    expect(screen.queryByRole("dialog", { name: "Due date" })).not.toBeInTheDocument();
    await u.click(within(when).getByRole("button", { name: "Pick date" }));
    await u.click(within(screen.getByRole("dialog", { name: "Due date" })).getByRole("button", { name: "Done" }));
    expect(screen.queryByRole("dialog", { name: "Due date" })).not.toBeInTheDocument();
    await u.click(within(when).getByRole("button", { name: "Pick date" }));   // the chip opens it again
    expect(screen.getByRole("dialog", { name: "Due date" })).toBeInTheDocument();
  });

  it("Others opens a person panel the same way; picking someone closes it and the chip says who", async () => {
    const u = userEvent.setup();
    renderAt();
    await screen.findByText("QC check MacBook Air keyboard");
    const who = screen.getByRole("group", { name: "Who" });
    const rowBefore = [...who.parentElement!.parentElement!.children].map((el) => el.tagName + el.className);
    await u.click(within(who).getByRole("button", { name: "Others" }));
    const panel = screen.getByRole("dialog", { name: "Choose a person" });
    expect(panel.className).toContain("absolute");
    expect([...who.parentElement!.parentElement!.children].map((el) => el.tagName + el.className)).toEqual(rowBefore);
    await u.selectOptions(within(panel).getByLabelText("Who it's for"), "e3");
    expect(screen.queryByRole("dialog", { name: "Choose a person" })).not.toBeInTheDocument();
    expect(within(who).getByRole("button", { name: "Tom" })).toHaveAttribute("aria-pressed", "true");
    await u.type(screen.getByLabelText("Add a task"), "Order a new screen{Enter}");
    expect(posts("/api/tasks")[0].body).toMatchObject({ assigned_to_id: "e3" });
  });

  it("phones: focus alone never opens or folds the choices (a tab tap can't move the page); typing does", async () => {
    const u = userEvent.setup();
    renderAt();
    await screen.findByText("QC check MacBook Air keyboard");
    const row = () => screen.getByRole("group", { name: "When" }).parentElement!.parentElement!;
    expect(row().className).toContain("max-sm:hidden");
    await u.click(screen.getByLabelText("Add a task"));
    expect(row().className).toContain("max-sm:hidden");                         // focus: unchanged
    await u.type(screen.getByLabelText("Add a task"), "R");
    expect(row().className).not.toContain("max-sm:hidden");
    await u.click(screen.getByRole("button", { name: /^Upcoming/ }));
    expect(row().className).not.toContain("max-sm:hidden");                     // a tab tap: unchanged
  });

  it("phones: the date panel is a sheet on <body> (never over the chips), and a tap elsewhere or another chip closes it", async () => {
    const mm = vi.fn((q: string) => ({ matches: q.includes("max-width: 639px"), media: q, addEventListener: vi.fn(), removeEventListener: vi.fn() }));
    vi.stubGlobal("matchMedia", mm);
    const u = userEvent.setup();
    renderAt();
    await screen.findByText("QC check MacBook Air keyboard");
    const when = screen.getByRole("group", { name: "When" });
    await u.click(within(when).getByRole("button", { name: "Pick date" }));
    const sheet = screen.getByRole("dialog", { name: "Due date" });
    expect(sheet.parentElement).toBe(document.body);
    expect(sheet.className).toContain("bottom-0");
    await u.click(within(screen.getByRole("group", { name: "Who" })).getByRole("button", { name: "Others" }));   // another chip
    expect(screen.queryByRole("dialog", { name: "Due date" })).not.toBeInTheDocument();
    expect(screen.getByRole("dialog", { name: "Choose a person" })).toBeInTheDocument();
    await u.click(screen.getByRole("button", { name: /^Upcoming/ }));                                            // elsewhere
    expect(screen.queryByRole("dialog", { name: "Choose a person" })).not.toBeInTheDocument();
    await u.click(within(when).getByRole("button", { name: "Pick date" }));
    within(screen.getByRole("dialog", { name: "Due date" })).getByRole("button", { name: "Done" }).focus();
    await u.keyboard("{Escape}");                                                                                // Escape
    expect(screen.queryByRole("dialog", { name: "Due date" })).not.toBeInTheDocument();
  });
});

describe("quick add: smart link and the job you just looked at", () => {
  const WO = (id: string, n: string, client: string, extra: any = {}) => ({ id, order_number: n, status: "in_repair", closed_at: null,
    client: { name: client }, device: { brand: "Apple", model: "iPhone 14 Pro" }, ...extra });
  const box = () => screen.getByLabelText("Add a task");
  const searches = () => calls.filter((c) => c.url.startsWith("/api/orders?")).map((c) => c.url);

  it("a job number offers up to three matches, open first; Tab links the highlighted one and the words stay", async () => {
    orderSearch = () => ({ items: [
      WO("o0", "WO-1042-B", "Jane Kim", { closed_at: "2026-11-01T10:00:00Z", status: "done" }),
      WO("o1", "WO-1042", "Jane Kim"), WO("o2", "WO-10420", "Tom Reed"), WO("o3", "WO-10421", "Ana Lee"),
    ] });
    const u = userEvent.setup();
    renderAt();
    await screen.findByText("QC check MacBook Air keyboard");
    await u.type(box(), "Chase parts for WO-1042");
    const list = await screen.findByRole("listbox", { name: "Work orders that match" });
    const opts = within(list).getAllByRole("option");
    expect(opts.map((o) => o.textContent)).toEqual([
      "WO-1042Jane Kim · Apple iPhone 14 ProIn repair", "WO-10420Tom Reed · Apple iPhone 14 ProIn repair", "WO-10421Ana Lee · Apple iPhone 14 ProIn repair"]);
    expect(searches().pop()).toBe("/api/orders?q=1042&limit=5");
    expect(box()).toHaveAttribute("aria-activedescendant", "quick-add-jobs-0");
    expect(opts[0]).toHaveAttribute("aria-selected", "true");
    await u.keyboard("{ArrowDown}");
    expect(box()).toHaveAttribute("aria-activedescendant", "quick-add-jobs-1");
    await u.keyboard("{ArrowUp}{Tab}");                                         // never on its own: Tab picks
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
    expect(screen.getByTestId("linked-job")).toHaveTextContent("WO-1042");     // the Link a work order chip shows it
    expect(box()).toHaveValue("Chase parts for WO-1042");
    await u.keyboard("{Enter}");
    expect(posts("/api/tasks")[0].body).toMatchObject({ title: "Chase parts for WO-1042", order_id: "o1" });
  });

  it("nothing is linked unless picked: Enter saves without a job", async () => {
    orderSearch = () => ({ items: [WO("o1", "WO-1042", "Jane Kim")] });
    const u = userEvent.setup();
    renderAt();
    await screen.findByText("QC check MacBook Air keyboard");
    await u.type(box(), "Chase 1042");
    await screen.findByRole("listbox");
    await u.keyboard("{Enter}");
    expect(posts("/api/tasks")[0].body).toMatchObject({ title: "Chase 1042", order_id: null });
  });

  it("Esc dismisses it for this clue; a new clue brings suggestions back; a click links", async () => {
    orderSearch = (q) => ({ items: q === "1042" ? [WO("o1", "WO-1042", "Jane Kim")] : [WO("o7", "WO-2001", "Tom Reed")] });
    const u = userEvent.setup();
    renderAt();
    await screen.findByText("QC check MacBook Air keyboard");
    await u.type(box(), "Chase 1042");
    await screen.findByRole("listbox");
    await u.keyboard("{Escape}");
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
    await u.type(box(), " today");
    await new Promise((r) => setTimeout(r, 400));
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();             // same clue: still dismissed
    await u.clear(box()); await u.type(box(), "Chase 2001");
    const opt = await screen.findByRole("option", { name: /WO-2001/ });
    await u.click(opt);
    expect(screen.getByTestId("linked-job")).toHaveTextContent("WO-2001");
  });

  it("a slow, stale answer never replaces the newer one", async () => {
    orderSearch = (q) => (q === "1042" ? { items: [WO("o1", "WO-1042", "Jane Kim")], delay: 600 } : { items: [WO("o7", "WO-2001", "Tom Reed")] });
    const u = userEvent.setup();
    renderAt();
    await screen.findByText("QC check MacBook Air keyboard");
    await u.type(box(), "Chase 1042");
    await new Promise((r) => setTimeout(r, 300));                                // the 1042 search has gone out
    await u.clear(box()); await u.type(box(), "Chase 2001");
    await screen.findByRole("option", { name: /WO-2001/ });
    await new Promise((r) => setTimeout(r, 700));                                // the 1042 answer lands late
    expect(screen.queryByRole("option", { name: /WO-1042/ })).not.toBeInTheDocument();
    expect(within(screen.getByRole("listbox")).getAllByRole("option")).toHaveLength(1);
  });

  it("a name clue only offers jobs for that customer; plain task words ask nothing", async () => {
    orderSearch = () => ({ items: [WO("o1", "WO-1042", "Jane Kim"), WO("o2", "WO-1043", "Tom Reed")] });   // Tom: matched on the device
    const u = userEvent.setup();
    renderAt();
    await screen.findByText("QC check MacBook Air keyboard");
    await u.type(box(), "Call at 3pm");
    await new Promise((r) => setTimeout(r, 400));
    expect(searches()).toEqual([]);
    await u.clear(box()); await u.type(box(), "Ring Jane about pickup");
    await screen.findByRole("option", { name: /WO-1042/ });
    expect(within(screen.getByRole("listbox")).getAllByRole("option")).toHaveLength(1);
    expect(searches().pop()).toBe("/api/orders?q=Jane&limit=5");
  });

  const remember = (ago: number, id = "r1") => localStorage.setItem("tekly.recentJob.me", JSON.stringify({ id, number: "WO-1042", client: "Jane Kim", device: "iPhone 14 Pro", at: Date.now() - ago }));
  const chip = () => screen.queryByRole("button", { name: /Link the work order you just looked at/ });

  it("the job you just looked at: a one-tap chip that links it, hidden once a job is linked", async () => {
    remember(60_000);
    const u = userEvent.setup();
    renderAt();
    await screen.findByText("QC check MacBook Air keyboard");
    await waitFor(() => expect(chip()).toBeInTheDocument());
    expect(chip()).toHaveAccessibleName("Link the work order you just looked at: WO-1042, Jane Kim");
    expect(chip()!.textContent).toContain("WO-1042 · Jane Kim");
    await u.click(chip()!);
    expect(chip()).not.toBeInTheDocument();
    expect(screen.getByTestId("linked-job")).toHaveTextContent("WO-1042");
    await u.type(box(), "Order the screen{Enter}");
    expect(posts("/api/tasks")[0].body).toMatchObject({ order_id: "r1" });
  });

  it("older than ten minutes: no chip, and it is forgotten", async () => {
    remember(11 * 60_000);
    renderAt();
    await screen.findByText("QC check MacBook Air keyboard");
    await new Promise((r) => setTimeout(r, 100));
    expect(chip()).not.toBeInTheDocument();
    expect(localStorage.getItem("tekly.recentJob.me")).toBeNull();
    expect(calls.some((c) => c.url.startsWith("/api/orders/"))).toBe(false);
  });

  it("a job this person can no longer open is never offered, and is dropped quietly", async () => {
    remember(60_000); orderGet = () => 404;
    renderAt();
    await screen.findByText("QC check MacBook Air keyboard");
    await waitFor(() => expect(localStorage.getItem("tekly.recentJob.me")).toBeNull());
    expect(chip()).not.toBeInTheDocument();
  });

  it("if the job became off-limits since it was offered, the tap unlinks it and drops it quietly", async () => {
    remember(60_000);
    const u = userEvent.setup();
    renderAt();
    await screen.findByText("QC check MacBook Air keyboard");
    await waitFor(() => expect(chip()).toBeInTheDocument());
    orderGet = () => 403;
    await u.click(chip()!);
    await waitFor(() => expect(screen.getByRole("button", { name: "Link a work order" })).toBeInTheDocument());
    expect(chip()).not.toBeInTheDocument();
    expect(localStorage.getItem("tekly.recentJob.me")).toBeNull();
  });
});

