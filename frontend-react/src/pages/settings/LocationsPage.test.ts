import { describe, it, expect } from "vitest";
import { DAYS, hoursOf } from "./LocationsPage";

// A location with no saved hours books 09:00–18:00 every day on the server (public._day_window);
// the Locations page and the booking page must show the same, not "closed".
describe("location hours", () => {
  it("treats missing or empty hours as the server's 9–6 every day", () => {
    for (const wh of [null, undefined, {}]) {
      const h = hoursOf(wh as any);
      expect(DAYS.every(([k]) => h[k].open === "09:00" && h[k].close === "18:00" && !h[k].closed)).toBe(true);
    }
  });
  it("keeps saved hours as they are", () => {
    const saved = { mon: { open: "08:00", close: "17:00" }, sun: { closed: true } };
    expect(hoursOf(saved)).toBe(saved);
  });
});
