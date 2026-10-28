// Vitest: todayTotals (cash in/out/net for "today").
import { describe, it, expect } from "vitest";
import { todayTotals, txnDirection } from "./cashStats";

const now = new Date("2026-10-05T14:00:00");
const iso = (h: number) => `2026-10-05T${String(h).padStart(2, "0")}:00:00`;
const yesterday = "2026-10-04T10:00:00";

describe("todayTotals", () => {
  it("empty", () => expect(todayTotals([], now)).toEqual({ in: 0, out: 0, net: 0 }));

  it("basic in/out/net", () => expect(todayTotals([
    { txn_type: "income", amount: 1200, actual_date: iso(10) },
    { txn_type: "income", amount: 900, actual_date: iso(9) },
    { txn_type: "expense", amount: -150, actual_date: iso(11) },
  ], now)).toEqual({ in: 2100, out: 150, net: 1950 }));

  it("yesterday excluded", () => expect(todayTotals([
    { txn_type: "income", amount: 1000, actual_date: iso(10) },
    { txn_type: "income", amount: 5000, actual_date: yesterday },
  ], now)).toEqual({ in: 1000, out: 0, net: 1000 }));

  it("deleted excluded", () => expect(todayTotals([
    { txn_type: "income", amount: 1000, actual_date: iso(10), status: "deleted" },
    { txn_type: "income", amount: 300, actual_date: iso(10) },
  ], now)).toEqual({ in: 300, out: 0, net: 300 }));

  it("negative amount is money out; transfers ignored either way", () => expect(todayTotals([
    { txn_type: "expense", amount: -200, actual_date: iso(10) },
    { txn_type: "transfer", amount: 5000, actual_date: iso(10) },
    { txn_type: "transfer", amount: -5000, actual_date: iso(10) },
  ], now)).toEqual({ in: 0, out: 200, net: -200 }));

  it("sales (invoice_payment) count as money in, not out", () => expect(todayTotals([
    { txn_type: "invoice_payment", amount: 80, actual_date: iso(10) },
    { txn_type: "income", amount: 20, actual_date: iso(11) },
  ], now)).toEqual({ in: 100, out: 0, net: 100 }));

  it("a refund booked as an expense with a negative amount is money out", () => expect(todayTotals([
    { txn_type: "invoice_payment", amount: 100, actual_date: iso(10) },
    { txn_type: "expense", amount: -30, actual_date: iso(12) },
  ], now)).toEqual({ in: 100, out: 30, net: 70 }));
});

describe("txnDirection", () => {
  it("follows the sign whatever the type", () => {
    expect(txnDirection({ txn_type: "invoice_payment", amount: 50 })).toBe("in");
    expect(txnDirection({ txn_type: "expense", amount: -50 })).toBe("out");
    expect(txnDirection({ txn_type: "income", amount: -10 })).toBe("out");
    expect(txnDirection({ txn_type: "transfer", amount: -10 })).toBe("out");
    expect(txnDirection({ txn_type: "transfer", amount: 10 })).toBe("in");
  });
});
