// Tasks: one reading of status/priority/source that works on the old API and the new one.
import { describe, it, expect } from "vitest";
import { normStatus, statusLabel, normPriority, priorityLabel, isAuto, involves, matchesQuery, applyFilters, type Task } from "./taskModel";

describe("normStatus / statusLabel", () => {
  it("maps legacy and blank statuses to To do, never an empty label", () => {
    expect(normStatus("open")).toBe("todo");
    expect(normStatus("")).toBe("todo");
    expect(normStatus(undefined)).toBe("todo");
    expect(normStatus("weird")).toBe("todo");
    expect(statusLabel({ status: "weird" })).toBe("To do");
    expect(statusLabel({})).toBe("To do");
  });
  it("keeps canonical statuses and reads completion as Done", () => {
    expect(normStatus("in_progress")).toBe("in_progress");
    expect(normStatus("cancelled")).toBe("cancelled");
    expect(statusLabel({ status: "in_progress" })).toBe("In progress");
    expect(statusLabel({ status: "cancelled" })).toBe("Cancelled");
    expect(statusLabel({ status: "todo", completed_at: "2026-01-01T00:00:00Z" })).toBe("Done");
  });
});

describe("normPriority", () => {
  it("reads ints, numeric strings and legacy words", () => {
    expect(normPriority(0)).toBe(0);
    expect(normPriority(2)).toBe(2);
    expect(normPriority("1")).toBe(1);
    expect(normPriority("high")).toBe(1);
    expect(normPriority("Urgent")).toBe(2);
    expect(normPriority("low")).toBe(0);
    expect(normPriority("medium")).toBe(0);
    expect(normPriority(null)).toBe(0);
    expect(normPriority(9)).toBe(2);
    expect(priorityLabel("high")).toBe("High");
    expect(priorityLabel(undefined)).toBe("Normal");
  });
});

describe("isAuto", () => {
  it("is true for a non-manual source or an automation creator", () => {
    expect(isAuto({ source: "automation" })).toBe(true);
    expect(isAuto({ source: "manual" })).toBe(false);
    expect(isAuto({})).toBe(false);
    expect(isAuto({ created_by: "automation" })).toBe(true);
  });
});

describe("filters", () => {
  const a: Task = { id: "a", title: "Order screen", order_number: "WO-12", client_name: "Ann Lee", assignee_ids: ["me"], priority: 2, status: "todo" };
  const b: Task = { id: "b", title: "Call back", description: "about the quote", created_by_id: "me", priority: "high", status: "open" };
  const c: Task = { id: "c", title: "Clean bench", assigned_to_id: "x", status: "in_progress", overdue: true };
  const d: Task = { id: "d", title: "Done one", assignee_ids: ["x"], completed_at: "2026-01-01T00:00:00Z", status: "done" };

  it("mine means assignee or creator", () => {
    expect(involves(a, "me")).toBe(true);
    expect(involves(b, "me")).toBe(true);
    expect(involves(c, "me")).toBe(false);
  });
  it("search matches title, description, work order and client", () => {
    expect(matchesQuery(a, "wo-12")).toBe(true);
    expect(matchesQuery(a, "ann")).toBe(true);
    expect(matchesQuery(b, "quote")).toBe(true);
    expect(matchesQuery(c, "screen")).toBe(false);
  });
  it("applies scope, status, person and priority together", () => {
    const all = [a, b, c, d];
    const ids = (f: Parameters<typeof applyFilters>[1]) => applyFilters(all, f).map((t) => t.id);
    expect(ids({ me: "me", scope: "mine", status: "all" })).toEqual(["a", "b"]);
    expect(ids({ me: "me", scope: "everyone", status: "todo" })).toEqual(["a", "b"]);
    expect(ids({ me: "me", scope: "everyone", status: "overdue" })).toEqual(["c"]);
    expect(ids({ me: "me", scope: "everyone", status: "done" })).toEqual(["d"]);
    expect(ids({ me: "me", scope: "everyone", status: "all", assignee: "x" })).toEqual(["c", "d"]);
    expect(ids({ me: "me", scope: "everyone", status: "all", priority: "1" })).toEqual(["b"]);
    expect(ids({ me: "me", scope: "everyone", status: "all", q: "bench" })).toEqual(["c"]);
  });
});
