// The shop's clock for task times: IST, UTC and a DST shop, independent of the test machine's zone.
import { describe, it, expect, vi, afterEach } from "vitest";
import { toShopWall, fromShopWall, shopToday, offsetMinutes, fmtShop, loadShopTz, knownShopTz, resetShopTz, localeFor, shopLocale } from "./shopTime";

describe("shop time", () => {
  it("shows a stored UTC instant on an IST shop's clock, and on a UTC shop's", () => {
    expect(toShopWall("2026-11-11T04:00:00", "Asia/Kolkata")).toBe("2026-11-11T09:30:00");
    expect(toShopWall("2026-11-11T18:29:59", "Asia/Kolkata")).toBe("2026-11-11T23:59:59");   // date-only = end of shop day
    expect(toShopWall("2026-11-11T19:00:00", "Asia/Kolkata")).toBe("2026-11-12T00:30:00");   // already tomorrow in the shop
    expect(toShopWall("2026-11-11T04:00:00", "UTC")).toBe("2026-11-11T04:00:00");
    expect(toShopWall("2026-11-11T04:00:00Z", "UTC")).toBe("2026-11-11T04:00:00");
    expect(toShopWall("2026-11-12", "Asia/Kolkata")).toBe("2026-11-12");
    expect(toShopWall(null, "UTC")).toBeNull();
  });

  it("sends a wall-clock pick with the shop's offset, and a bare date as it is", () => {
    expect(fromShopWall("2026-11-11T15:00", "Asia/Kolkata")).toBe("2026-11-11T15:00:00+05:30");
    expect(fromShopWall("2026-11-11T15:00", "UTC")).toBe("2026-11-11T15:00:00+00:00");
    expect(fromShopWall("2026-11-12", "Asia/Kolkata")).toBe("2026-11-12");
    // New York: winter and summer offsets, taken from that day, not from today
    expect(fromShopWall("2026-01-15T09:00", "America/New_York")).toBe("2026-01-15T09:00:00-05:00");
    expect(fromShopWall("2026-07-15T09:00", "America/New_York")).toBe("2026-07-15T09:00:00-04:00");
    // a round trip lands on the same wall time
    const sent = fromShopWall("2026-03-29T12:00", "Europe/London")!;
    expect(toShopWall(new Date(sent).toISOString(), "Europe/London")).toBe("2026-03-29T12:00:00");
  });

  it("knows the shop's today and offset", () => {
    const at = new Date("2026-11-12T18:00:00Z");     // 23:30 in India
    expect(shopToday("Asia/Kolkata", at)).toBe("2026-11-12");
    expect(shopToday("Asia/Kolkata", new Date("2026-11-12T19:00:00Z"))).toBe("2026-11-13");   // 00:30 next day
    expect(shopToday("UTC", new Date("2026-11-12T19:00:00Z"))).toBe("2026-11-12");
    expect(offsetMinutes(Date.UTC(2026, 10, 11), "Asia/Kolkata")).toBe(330);
  });

  it("formats on the shop's clock", () => {
    expect(fmtShop("2026-11-11T04:00:00", "Asia/Kolkata", { hour: "numeric", minute: "2-digit", hour12: false })).toMatch(/09:30|9:30/);
    expect(fmtShop("2026-11-11T04:00:00", "UTC", { hour: "numeric", minute: "2-digit", hour12: false })).toMatch(/04:00|4:00/);
    expect(fmtShop(null, "UTC")).toBe("");
  });
});

describe("loading the shop's clock", () => {
  afterEach(() => { vi.unstubAllGlobals(); resetShopTz(); });
  const reply = (body: unknown, status = 200) => ({ ok: status < 400, status, statusText: "", headers: new Headers(), json: async () => body, text: async () => JSON.stringify(body) }) as Response;

  it("never guesses UTC when the shop can't be reached, and loads on a retry", async () => {
    resetShopTz();
    let up = false;
    vi.stubGlobal("fetch", vi.fn(async () => (up ? reply({ timezone: "Asia/Kolkata", country: "GB" }) : reply({ detail: "down" }, 503))));
    expect(await loadShopTz()).toBeNull();
    expect(knownShopTz()).toBeNull();
    up = true;
    expect(await loadShopTz()).toBe("Asia/Kolkata");
    expect(shopLocale()).toBe("en-GB");
  });

  it("uses UTC only when the shop really has no zone set (the server's own rule)", async () => {
    resetShopTz();
    vi.stubGlobal("fetch", vi.fn(async () => reply({ timezone: null })));
    expect(await loadShopTz()).toBe("UTC");
  });

  it("takes the date style from the shop's country", () => {
    expect(localeFor("GB")).toBe("en-GB");
    expect(localeFor("in")).toBe("en-IN");
    expect(localeFor(null)).toBeUndefined();
    expect(new Date(2026, 10, 7, 15, 0).toLocaleString(localeFor("GB"), { hour: "numeric", minute: "2-digit" })).toBe("15:00");
  });
});
