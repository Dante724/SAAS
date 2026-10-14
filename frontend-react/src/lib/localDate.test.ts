import { describe, it, expect } from "vitest";
import { isoDate, isoMonth, isoDateTimeLocal } from "./localDate";

describe("localDate (uses LOCAL components, never UTC)", () => {
  it("isoDate zero-pads and uses month+1", () => {
    expect(isoDate(new Date(2026, 2, 9))).toBe("2026-03-09");   // March 9
    expect(isoDate(new Date(2026, 11, 31))).toBe("2026-12-31");
  });

  it("isoMonth → YYYY-MM", () => {
    expect(isoMonth(new Date(2026, 0, 15))).toBe("2026-01");
  });

  it("isoDateTimeLocal → YYYY-MM-DDTHH:MM, padded", () => {
    expect(isoDateTimeLocal(new Date(2026, 5, 7, 8, 5))).toBe("2026-06-07T08:05");
    expect(isoDateTimeLocal(new Date(2026, 5, 7, 23, 0))).toBe("2026-06-07T23:00");
  });

  it("a late-evening local time keeps the LOCAL calendar day (the toISOString bug)", () => {
    // 2026-06-07 23:30 local. isoDate must say the 7th — toISOString would say the 8th
    // in any timezone ahead of UTC. We assert against the local day explicitly.
    const d = new Date(2026, 5, 7, 23, 30);
    expect(isoDate(d)).toBe("2026-06-07");
    expect(isoDate(d)).toBe(`${d.getFullYear()}-06-0${d.getDate()}`);
  });
});
