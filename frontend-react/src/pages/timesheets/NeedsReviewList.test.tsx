// Decision 6: an approval blocked by flagged time shows a "Needs review" list built from the 409
// (detail.flagged). Confirm accepts an item as it is, Edit fixes its times; either drops the row.
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

vi.mock("../../components/Shell", () => ({ M: ({ n }: { n: string }) => <i data-icon={n} />, default: ({ children }: any) => <div>{children}</div> }));
vi.mock("../../components/DateField", () => ({
  default: ({ value, onChange, label }: any) => <input aria-label={label || "date"} value={value} onChange={(e) => onChange(e.target.value)} />,
}));

import NeedsReviewList, { flaggedFrom, type Flagged } from "./NeedsReviewList";
import TimesheetsHub from "./TimesheetsHub";
import { ApiError } from "../../api/client";

const ENTRY: Flagged = { kind: "time_entry", id: "te1", employee_id: "e1", employee_name: "Ana Silva", date: "2026-08-05",
  reason: "timer left running, stopped at the 12 h limit", started_at: "2026-08-05T09:00:00Z", ended_at: "2026-08-05T21:00:00Z" };
const SHIFT: Flagged = { kind: "shift", id: "sh1", employee_id: "e1", employee_name: "Ana Silva", date: "2026-08-06",
  reason: "no clock-out: shift closed at the 16 h limit", started_at: "2026-08-06T08:00:00Z", ended_at: "2026-08-07T00:00:00Z" };
const BLOCKED = { detail: { message: "Can't approve payroll for this period: 2 items need a manager's review.", flagged: [ENTRY, SHIFT] } };

let calls: { method: string; url: string; body: any }[] = [];
const reply = (status: number, body: unknown) => ({ ok: status < 400, status, statusText: "", headers: new Headers(), json: async () => body, text: async () => JSON.stringify(body) }) as Response;

beforeEach(() => {
  calls = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    const method = init?.method || "GET";
    calls.push({ method, url, body: init?.body ? JSON.parse(String(init.body)) : undefined });
    if (method === "POST" && url.includes("/timeclock/timesheet/approve")) return reply(409, BLOCKED);
    if (method === "PATCH") return reply(200, { ok: true });
    if (url.includes("/staff/employees")) return reply(200, { items: [{ id: "e1", name: "Ana Silva" }] });
    if (url.includes("/timeclock/timesheet")) return reply(200, { entries: [], entry_count: 0, approved_count: 0 });
    return reply(200, { items: [] });
  }));
});
afterEach(() => vi.unstubAllGlobals());

const patches = () => calls.filter((c) => c.method === "PATCH");

describe("flaggedFrom", () => {
  it("reads the flagged items from a 409, and nothing from other errors", () => {
    expect(flaggedFrom(new ApiError(409, JSON.stringify(BLOCKED)))).toEqual([ENTRY, SHIFT]);
    expect(flaggedFrom(new ApiError(409, JSON.stringify({ detail: "An accrual already covers part of this period" })))).toBeNull();
    expect(flaggedFrom(new ApiError(500, "boom"))).toBeNull();
    expect(flaggedFrom(new Error("x"))).toBeNull();
  });
});

describe("NeedsReviewList", () => {
  it("shows a row per flagged item: who, which day, why, and Confirm before Edit", () => {
    render(<NeedsReviewList items={[ENTRY, SHIFT]} onDone={() => {}} />);
    const rows = screen.getAllByRole("listitem");
    expect(rows).toHaveLength(2);
    expect(within(rows[0]).getByText("Ana Silva")).toBeTruthy();
    expect(within(rows[0]).getByText(/5 Aug 2026/)).toBeTruthy();
    expect(within(rows[0]).getByText(ENTRY.reason)).toBeTruthy();
    expect(within(rows[1]).getByText(/Shift/)).toBeTruthy();
    const names = within(rows[0]).getAllByRole("button").map((b) => b.textContent);
    expect(names).toEqual(["Confirm", "Edit"]);          // main action first, on the left
  });

  it("Confirm on a time entry saves it unchanged and drops the row", async () => {
    const u = userEvent.setup();
    render(<NeedsReviewList items={[ENTRY, SHIFT]} onDone={() => {}} />);
    await u.click(within(screen.getAllByRole("listitem")[0]).getByRole("button", { name: "Confirm" }));
    expect(patches()).toEqual([{ method: "PATCH", url: "/api/timeclock/entries/te1", body: {} }]);
    expect(screen.getAllByRole("listitem")).toHaveLength(1);
    expect(screen.queryByText(ENTRY.reason)).toBeNull();
  });

  it("Confirm on a shift confirms it with a reason, and the last row says approve again", async () => {
    const u = userEvent.setup();
    const done = vi.fn();
    render(<NeedsReviewList items={[SHIFT]} onDone={done} />);
    await u.click(screen.getByRole("button", { name: "Confirm" }));
    const [p] = patches();
    expect(p.url).toBe("/api/timeclock/shifts/sh1");
    expect(Object.keys(p.body)).toEqual(["reason"]);
    expect(p.body.reason).toBeTruthy();
    expect(screen.queryAllByRole("listitem")).toHaveLength(0);
    expect(done).toHaveBeenCalledTimes(1);
  });

  it("Edit opens the edit form with the times; saving sends them and drops the row", async () => {
    const u = userEvent.setup();
    render(<NeedsReviewList items={[ENTRY]} onDone={() => {}} />);
    await u.click(screen.getByRole("button", { name: "Edit" }));
    const form = screen.getByRole("dialog", { name: "Edit time entry" });
    const end = within(form).getByLabelText("Ended at") as HTMLInputElement;
    expect(end.value).not.toBe("");
    await u.clear(end);
    await u.type(end, "2026-08-05T17:00");
    await u.click(within(form).getByRole("button", { name: "Save" }));
    const [p] = patches();
    expect(p.url).toBe("/api/timeclock/entries/te1");
    expect(p.body.ended_at).toBe(new Date("2026-08-05T17:00").toISOString());
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.queryAllByRole("listitem")).toHaveLength(0);
  });

  it("Edit on a shift asks for the reason the correction needs", async () => {
    const u = userEvent.setup();
    render(<NeedsReviewList items={[SHIFT]} onDone={() => {}} />);
    await u.click(screen.getByRole("button", { name: "Edit" }));
    const form = screen.getByRole("dialog", { name: "Edit shift" });
    await u.click(within(form).getByRole("button", { name: "Save" }));
    expect(patches()).toHaveLength(0);                     // no reason yet
    await u.type(within(form).getByLabelText("Reason"), "left at midnight");
    await u.click(within(form).getByRole("button", { name: "Save" }));
    expect(patches()[0].body.reason).toBe("left at midnight");
  });
});

describe("TimesheetsHub", () => {
  it("a blocked Approve range shows the Needs review list from the 409", async () => {
    const u = userEvent.setup();
    render(<TimesheetsHub />);
    await screen.findByRole("option", { name: "Ana Silva" }).catch(() => null);
    await u.click(screen.getByRole("button", { name: "Approve range" }));
    const list = await screen.findByRole("region", { name: "Needs review" });
    expect(within(list).getAllByRole("listitem")).toHaveLength(2);
    expect(within(list).getByText(ENTRY.reason)).toBeTruthy();
  });
});
