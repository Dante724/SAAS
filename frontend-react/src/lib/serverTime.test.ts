import { describe, it, expect } from "vitest";
import { dayDate, fmtServerTime, serverDate, signupTimezone, wallDate } from "./serverTime";

describe("serverTime (launch B6)", () => {
  it("reads an instant without a zone as UTC, not as local time", () => {
    // Web clock-in at 10:00 in India is stored as 04:30 UTC.
    expect(serverDate("2026-10-28T04:30:00")!.toISOString()).toBe("2026-10-28T04:30:00.000Z");
    expect(serverDate("2026-10-28T04:30:00Z")!.toISOString()).toBe("2026-10-28T04:30:00.000Z");
    expect(serverDate("2026-10-28T10:00:00+05:30")!.toISOString()).toBe("2026-10-28T04:30:00.000Z");
    expect(serverDate("2026-10-28T04:30:00.123456")!.getUTCMinutes()).toBe(30);
  });

  it("keeps a wall-clock time's own clock numbers", () => {
    const d = wallDate("2026-11-04T11:01:00")!;
    expect([d.getHours(), d.getMinutes()]).toEqual([11, 1]);
    expect(wallDate("2026-11-04T11:01:00Z")!.getHours()).toBe(11);
  });

  it("puts an instant on the viewer's day and a bare date or wall time on its own day", () => {
    const inst = dayDate("2026-11-11T02:00:00Z")!;      // 21:00 on the 10th in New York
    expect(inst.getTime()).toBe(Date.UTC(2026, 10, 11, 2));
    const day = dayDate("2026-11-11")!;
    expect([day.getFullYear(), day.getMonth(), day.getDate(), day.getHours()]).toEqual([2026, 10, 11, 0]);
    const wall = dayDate("2026-11-11T17:00:00")!;       // a due time: the shop's clock as stored
    expect([wall.getDate(), wall.getHours()]).toEqual([11, 17]);
    expect(dayDate(null)).toBeNull();
  });

  it("handles empty and bad input", () => {
    expect(serverDate(null)).toBeNull();
    expect(serverDate("nonsense")).toBeNull();
    expect(wallDate(undefined)).toBeNull();
    expect(fmtServerTime(null)).toBe("");
  });

  it("sign-up keeps the browser zone on the same continent, else the country's", () => {
    expect(signupTimezone("America/New_York", "America/Los_Angeles")).toBe("America/Los_Angeles");
    expect(signupTimezone("Europe/London", "UTC")).toBe("Europe/London");
    expect(signupTimezone("Asia/Kolkata", "Europe/Berlin")).toBe("Asia/Kolkata");
    expect(signupTimezone(undefined, "Asia/Dubai")).toBe("Asia/Dubai");
  });
});
