// My day: regrouping and optimistic moves on the /tasks/my-day reply.
import { describe, it, expect } from "vitest";
import { splitPool, normDay, moveTasks, insertTask, replaceTask, dueClock, groupByTime, groupByDay, dayLabel, lateLabel, quickDue, bucketForDue, type Task } from "./taskModel";

const T = (id: string, due?: string | null, extra: Partial<Task> = {}): Task => ({ id, title: id, due_at: due ?? null, ...extra });

describe("normDay", () => {
  it("fills missing groups and counts from what is there", () => {
    const d = normDay({ today: [T("a")], counts: { today: 4 } as any }, "2026-11-11");
    expect(d.date).toBe("2026-11-11");
    expect(d.catch_up).toEqual([]);
    expect(d.counts.today).toBe(4);           // the server's count wins (it may page)
    expect(d.counts.later).toBe(0);
  });
});

describe("moveTasks / insertTask / replaceTask", () => {
  const day = normDay({ catch_up: [T("a"), T("b")], today: [T("c")], counts: { catch_up: 2, today: 1, upcoming: 0, later: 0, done_today: 0 } }, "2026-11-11");
  it("moves tasks and keeps counts in step", () => {
    const n = moveTasks(day, ["a", "b"], "today", { due_at: "2026-11-11" });
    expect(n.catch_up).toEqual([]);
    expect(n.today.map((t) => t.id)).toEqual(["c", "a", "b"]);
    expect(n.today[1].due_at).toBe("2026-11-11");
    expect(n.counts).toMatchObject({ catch_up: 0, today: 3 });
  });
  it("puts newly done tasks on top of Done today", () => {
    const n = moveTasks(moveTasks(day, ["a"], "done_today"), ["c"], "done_today");
    expect(n.done_today.map((t) => t.id)).toEqual(["c", "a"]);
  });
  it("inserts, replaces and drops a quick-added task", () => {
    const n = insertTask(day, T("tmp"), "today");
    expect(n.counts.today).toBe(2);
    expect(replaceTask(n, "tmp", T("real")).today.map((t) => t.id)).toEqual(["c", "real"]);
    const gone = replaceTask(n, "tmp", null);
    expect(gone.today.map((t) => t.id)).toEqual(["c"]);
    expect(gone.counts.today).toBe(1);
  });
});

describe("times and days", () => {
  it("treats a date-only or end-of-day due as 'any time'", () => {
    expect(dueClock("2026-11-11")).toBeNull();
    expect(dueClock("2026-11-11T23:59:59")).toBeNull();
    expect(dueClock("2026-11-11T09:30:00")).toBe("09:30");
  });
  it("groups today by part of the day, earliest first, untimed last", () => {
    const g = groupByTime([T("eve", "2026-11-11T18:00"), T("any", "2026-11-11"), T("am2", "2026-11-11T11:00"), T("am1", "2026-11-11T09:00"), T("pm", "2026-11-11T14:00")]);
    expect(g.map((x) => x.label)).toEqual(["Morning", "Afternoon", "Evening", "Any time today"]);
    expect(g[0].items.map((t) => t.id)).toEqual(["am1", "am2"]);
  });
  it("groups upcoming by day with no-date last", () => {
    const g = groupByDay([T("x", "2026-11-14T10:00"), T("n"), T("y", "2026-11-12")], "2026-11-11");
    expect(g.map((x) => x.label)[0]).toBe("Tomorrow");
    expect(g[g.length - 1].label).toBe("No date");
    expect(dayLabel("2026-11-11", "2026-11-11")).toBe("Today");
  });
  it("labels how late a task is", () => {
    expect(lateLabel("2026-11-10T10:00", "2026-11-11")).toBe("Yesterday");
    expect(lateLabel("2026-10-01", "2026-11-11")).toMatch(/Oct/);
  });
  it("quick add dates use the shop's today and land in the right group", () => {
    expect(quickDue("today", "", "2026-11-30")).toBe("2026-11-30");
    expect(quickDue("tomorrow", "", "2026-11-30")).toBe("2026-12-01");
    expect(quickDue("pick", "2026-12-24", "2026-11-30")).toBe("2026-12-24");
    expect(bucketForDue("2026-11-30", "2026-11-30")).toBe("today");
    expect(bucketForDue("2026-12-01", "2026-11-30")).toBe("upcoming");
    expect(bucketForDue("2026-12-24", "2026-11-30")).toBe("later");
  });
});

describe("splitPool (Up for grabs)", () => {
  it("takes other people's unassigned tasks out of my day, keeps my own to-dos, and fixes the counts", () => {
    const day = normDay({
      today: [T("mine", "2026-11-11T10:00", { assigned_to_id: "me" }), T("pool", "2026-11-11T12:00", { created_by_id: "e2" }), T("todo", "2026-11-11", { created_by_id: "me" })],
      later: [T("pool2", null, { created_by_id: "e3", assignee_ids: [] })],
      counts: { catch_up: 0, today: 3, upcoming: 0, later: 1, done_today: 0 },
    }, "2026-11-11");
    const { day: d, pool } = splitPool(day, "me");
    expect(d.today.map((t) => t.id)).toEqual(["mine", "todo"]);
    expect(d.counts.today).toBe(2);
    expect(d.counts.later).toBe(0);
    expect(pool.map((t) => t.id)).toEqual(["pool", "pool2"]);   // dated first
    expect(splitPool(day, null).pool).toEqual([]);              // managers and other views: as sent
  });
});
