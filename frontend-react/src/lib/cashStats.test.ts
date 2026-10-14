// Vitest: todayTotals (cash in/out/net for "today").
import { describe, it, expect } from "vitest";
import { todayTotals } from "./cashStats";

const now = new Date("2026-10-05T14:00:00");
const iso = (h: number) => `2026-10-05T${String(h).padStart(2, "0")}:00:00`;
const yesterday = "2026-10-04T10:00:00";

describe("todayTotals", () => {
  it("empty", () => expect(todayTotals([], now)).toEqual({ in: 0, out: 0, net: 0 }));

  it("basic in/out/net", () => expect(todayTotals([
    { txn_type: "income", amount: 1200, actual_date: iso(10) },
    { txn_type: "income", amount: 900, actual_date: iso(9) },
    { txn_type: "expense", amount: 150, actual_date: iso(11) },
  ], now)).toEqual({ in: 2100, out: 150, net: 1950 }));

  it("yesterday excluded", () => expect(todayTotals([
    { txn_type: "income", amount: 1000, actual_date: iso(10) },
    { txn_type: "income", amount: 5000, actual_date: yesterday },
  ], now)).toEqual({ in: 1000, out: 0, net: 1000 }));

  it("deleted excluded", () => expect(todayTotals([
    { txn_type: "income", amount: 1000, actual_date: iso(10), status: "deleted" },
    { txn_type: "income", amount: 300, actual_date: iso(10) },
  ], now)).toEqual({ in: 300, out: 0, net: 300 }));

  it("signed expense abs; transfer ignored", () => expect(todayTotals([
    { txn_type: "expense", amount: -200, actual_date: iso(10) },
    { txn_type: "transfer", amount: 5000, actual_date: iso(10) },
  ], now)).toEqual({ in: 0, out: 200, net: -200 }));
});
