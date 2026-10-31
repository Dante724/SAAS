// Vitest: plain-words dates used on the Work orders list and drawer.
import { describe, it, expect, vi, afterEach } from "vitest";
import { dueText, friendlyDate } from "./look";

afterEach(() => { vi.useRealTimers(); });
const at = (s: string) => new Date(s).toISOString();

describe("friendlyDate", () => {
  it("says Today / Tomorrow with the time, and a short date further out", () => {
    vi.useFakeTimers(); vi.setSystemTime(new Date("2026-10-15T08:00:00"));
    expect(friendlyDate(at("2026-10-15T09:00:00"))).toMatch(/^Today 9:00/);
    expect(friendlyDate(at("2026-10-16T14:30:00"))).toMatch(/^Tomorrow 2:30/);
    expect(friendlyDate(at("2026-10-20T10:00:00"))).not.toMatch(/Today|Tomorrow|10:00/);
    expect(friendlyDate(null)).toBeNull();
  });
});

describe("dueText", () => {
  it("counts days late in red, days left otherwise, nothing for closed jobs", () => {
    vi.useFakeTimers(); vi.setSystemTime(new Date("2026-10-15T12:00:00"));
    expect(dueText(at("2026-10-13T09:00:00"), true)).toEqual(["2 days late", true]);
    expect(dueText(at("2026-10-15T09:00:00"), true)).toEqual(["Due earlier today", true]);
    expect(dueText(at("2026-10-15T18:00:00"), true)).toEqual(["Due today", false]);
    expect(dueText(at("2026-10-18T09:00:00"), true)).toEqual(["Due in 3 days", false]);
    expect(dueText(at("2026-10-13T09:00:00"), false)).toBeNull();
  });
});
