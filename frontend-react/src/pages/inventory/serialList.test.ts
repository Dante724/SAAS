import { describe, expect, it } from "vitest";
import { addSerials, localCheck, luhnOk, mergeServer, normalizeSerial, splitSerials } from "./serialList";

const IMEI = "490154203237518"; // valid check digit
const IMEI2 = "356789012345011";

describe("splitSerials", () => {
  it("splits on newlines, commas, semicolons and tabs, keeping inner spaces", () => {
    expect(splitSerials(`${IMEI}\r\n35 678901 2345011, abc-1 ;\tX2\n\n`)).toEqual([IMEI, "35 678901 2345011", "abc-1", "X2"]);
  });
  it("returns nothing for blank input", () => { expect(splitSerials("  \n ")).toEqual([]); });
});

describe("normalizeSerial", () => {
  it("treats grouped digits of IMEI length as an IMEI", () => {
    expect(normalizeSerial("49 015420 323751 8")).toEqual({ serial: IMEI, kind: "imei" });
  });
  it("upper-cases serials and drops spaces but keeps dashes", () => {
    expect(normalizeSerial(" sn-77 x ")).toEqual({ serial: "SN-77X", kind: "serial" });
  });
  it("a short number is a serial, not an IMEI", () => {
    expect(normalizeSerial("12345").kind).toBe("serial");
  });
});

describe("luhnOk", () => {
  it("accepts a valid IMEI and rejects a typo", () => {
    expect(luhnOk(IMEI)).toBe(true);
    expect(luhnOk(IMEI.slice(0, -1) + "9")).toBe(false);
  });
});

describe("localCheck", () => {
  it("blocks a repeat (even typed differently) and warns on a bad check digit", () => {
    const rows = localCheck([IMEI, "49015420 3237518", IMEI.slice(0, -1) + "9", "ABC"]);
    expect(rows.map((r) => r.status)).toEqual(["ok", "block", "warn", "ok"]);
    expect(rows[1].message).toMatch(/twice/);
  });
});

describe("mergeServer", () => {
  it("keeps the stricter verdict", () => {
    const local = localCheck([IMEI, IMEI2]);
    const merged = mergeServer(local, [{ status: "block", message: "Already in stock as iPhone 13." }, { status: "ok" }]);
    expect(merged[0].status).toBe("block");
    expect(merged[0].message).toMatch(/in stock/);
    expect(merged[1].status).toBe("ok");
  });
  it("returns local rows when the server hasn't answered", () => {
    const local = localCheck([IMEI]);
    expect(mergeServer(local, null)).toBe(local);
  });
});

describe("addSerials", () => {
  it("adds a scan, skips repeats, and accepts a pasted block", () => {
    let r = addSerials([], IMEI);
    expect(r.list).toEqual([IMEI]);
    r = addSerials(r.list, `${IMEI2}\n49 015420 323751 8`);
    expect(r.list).toEqual([IMEI, IMEI2]);
    expect(r.skipped).toBe(1);
  });
});
