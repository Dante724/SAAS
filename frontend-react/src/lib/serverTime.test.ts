import { describe, it, expect } from "vitest";
import { fmtServerTime, serverDate, signupTimezone, wallDate } from "./serverTime";

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
