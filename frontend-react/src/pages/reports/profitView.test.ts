import { describe, expect, it } from "vitest";
import { DEFAULT_FILTERS, drillRows, filterParams, readReportsPref, trend, type ProfitRow } from "./profitView";

const row = (p: Partial<ProfitRow>): ProfitRow => ({
  kind: "job", id: "x", number: "WO-1", date: "2026-10-01", revenue: 0, tax: 0, labour_cost: 0,
  commission: 0, written_off: 0, ...p,
});

describe("trend", () => {
  it("compares with the previous period", () => {
    expect(trend(50, 35)).toEqual({ delta: 15, pct: 42.9, dir: "up", label: "Up 42.9% on last period" });
    expect(trend(-10, 40).dir).toBe("down");
    expect(trend(-10, 40).label).toBe("Down 125% on last period");
  });
  it("never shows a percentage against nothing", () => {
    expect(trend(20, 0).pct).toBeNull();
    expect(trend(20, 0).label).toBe("Up from nothing last period");
    expect(trend(0, 0).label).toBe("Same as last period");
  });
  it("uses the size of a negative previous figure", () => {
    expect(trend(10, -10).pct).toBe(200);
  });
});

describe("drillRows", () => {
  const rows = [
    row({ id: "a", kind: "job", location_id: "L1", employee_id: "E1" }),
    row({ id: "b", kind: "dropped", location_id: "L2", employee_id: null }),
    row({ id: "c", kind: "sale", location_id: "L1", employee_id: "E2" }),
  ];
  it("opens the rows behind a bucket", () => {
    expect(drillRows(rows, { kind: "dropped" }).map((r) => r.id)).toEqual(["b"]);
  });
  it("opens the rows behind a location or technician", () => {
    expect(drillRows(rows, { location_id: "L1" }).map((r) => r.id)).toEqual(["a", "c"]);
    expect(drillRows(rows, { employee_id: "" }).map((r) => r.id)).toEqual(["b"]);   // nobody assigned
    expect(drillRows(rows, {}).length).toBe(3);
  });
});

describe("report filters", () => {
  it("sends only the filters the report understands", () => {
    const f = { movement: "without" as const, hideZero: true, status: "dropped", typeId: "t1" };
    expect(filterParams("product_turnover", f)).toEqual({ movement: "without" });
    expect(filterParams("wo_closed", f)).toEqual({ hide_zero: "true" });
    expect(filterParams("dropped_jobs", f)).toEqual({ status: "dropped", type_id: "t1" });
    expect(filterParams("payroll", f)).toEqual({});
    expect(filterParams("product_turnover", DEFAULT_FILTERS)).toEqual({});
  });
  it("restores remembered filters and drops anything odd", () => {
    expect(readReportsPref(null)).toEqual({ report: null, locId: "", filters: DEFAULT_FILTERS });
    const p = readReportsPref({ report: "wo_closed", locId: "L1", filters: { movement: "all", hideZero: true, status: 5 } });
    expect(p).toEqual({ report: "wo_closed", locId: "L1", filters: { movement: "all", hideZero: true, status: "", typeId: "" } });
    expect(readReportsPref({ filters: { movement: "sideways", hideZero: "yes" } }).filters).toEqual(DEFAULT_FILTERS);
  });
});
