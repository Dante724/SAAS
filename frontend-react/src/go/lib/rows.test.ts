import { describe, expect, it } from "vitest";
import { mixesStatuses, rowDetail, rowTitle, rowWhen } from "./rows";

const now = new Date(2026, 9, 23, 10, 0); // Fri 23 Oct 2026, 10:00 local
const local = (d: number, h: number, m = 0) => {
  const t = new Date(2026, 9, d, h, m);
  const p = (n: number) => String(n).padStart(2, "0");
  return `${t.getFullYear()}-${p(t.getMonth() + 1)}-${p(t.getDate())}T${p(t.getHours())}:${p(t.getMinutes())}:00`;
};

describe("Today rows", () => {
  it("names the customer, or says walk-in", () => {
    expect(rowTitle({ client: "Ana Lee" })).toBe("Ana Lee");
    expect(rowTitle({ client: "  " })).toBe("Walk-in");
    expect(rowTitle({})).toBe("Walk-in");
  });

  it("reads Device · Problem and skips what's missing", () => {
    expect(rowDetail({ device: "iPhone 14 Pro", complaint: "Cracked screen" })).toBe("iPhone 14 Pro · Cracked screen");
    expect(rowDetail({ device: "", complaint: "Battery" })).toBe("Battery");
    expect(rowDetail({ device: null, complaint: null })).toBe("");
  });

  it("shows today's promise as a time, not late before it", () => {
    const w = rowWhen({ due_at: local(23, 15, 30) }, now)!;
    expect(w.late).toBe(false);
    expect(w.text).toMatch(/3:30/);
  });

  it("goes red once the promise has passed", () => {
    expect(rowWhen({ due_at: local(23, 9) }, now)!.late).toBe(true);
    expect(rowWhen({ due_at: local(21, 9) }, now)).toEqual({ text: "2 days late", late: true });
    expect(rowWhen({ due_at: local(22, 18) }, now)).toEqual({ text: "1 day late", late: true });
  });

  it("trusts the server's overdue flag", () => {
    expect(rowWhen({ due_at: local(23, 15), overdue: true }, now)!.late).toBe(true);
    expect(rowWhen({ overdue: true }, now)).toEqual({ text: "Late", late: true });
  });

  it("says tomorrow, then a date; a past booking is never 'late'", () => {
    expect(rowWhen({ due_at: local(24, 11) }, now)!.text).toMatch(/^Tomorrow /);
    expect(rowWhen({ due_at: local(28, 11) }, now)!.late).toBe(false);
    expect(rowWhen({ scheduled_for: local(22, 11) }, now)!.late).toBe(false);
    expect(rowWhen({}, now)).toBeNull();
  });

  it("only mixes in status names when a list mixes statuses", () => {
    expect(mixesStatuses([{ status: "new" }, { status: "new" }])).toBe(false);
    expect(mixesStatuses([{ status: "new" }, { status: "diagnosed" }])).toBe(true);
  });
});
