import { describe, it, expect } from "vitest";
import { buildReportConfig, pickChartColumns } from "./reportConfig";

const base = {
  source: "invoices",
  columns: ["invoice_number", "status", "total"],
  filters: [{ field: "status", op: "eq", value: "paid" }],
  groupBy: "",
  aggregates: [{ fn: "count", field: "" }],
};

describe("buildReportConfig", () => {
  it("flat mode: includes columns, omits group_by/aggregates", () => {
    const c = buildReportConfig(base);
    expect(c.columns).toEqual(["invoice_number", "status", "total"]);
    expect(c.group_by).toBeUndefined();
    expect(c.aggregates).toBeUndefined();
  });

  it("grouped mode: OMITS columns (the #1 fix), sets group_by + aggregates", () => {
    const c = buildReportConfig({ ...base, groupBy: "status" });
    expect(c.columns).toBeUndefined();          // columns are meaningless when grouping
    expect(c.group_by).toBe("status");
    expect(c.aggregates).toEqual([{ fn: "count" }]);
  });

  it("grouped mode maps sum/avg with their field, drops empty-field aggregates", () => {
    const c = buildReportConfig({
      ...base, groupBy: "status",
      aggregates: [{ fn: "count", field: "" }, { fn: "sum", field: "total" }, { fn: "avg", field: "" }],
    });
    expect(c.aggregates).toEqual([{ fn: "count" }, { fn: "sum", field: "total" }]);
  });

  it("drops incomplete filters (missing field or op)", () => {
    const c = buildReportConfig({
      ...base,
      filters: [{ field: "status", op: "eq", value: "paid" }, { field: "", op: "eq", value: "x" }, { field: "total", op: "", value: "1" }],
    });
    expect(c.filters).toEqual([{ field: "status", op: "eq", value: "paid" }]);
  });

  it("grouped mode with no aggregates still groups (backend defaults to count)", () => {
    const c = buildReportConfig({ ...base, groupBy: "status", aggregates: [] });
    expect(c.group_by).toBe("status");
    expect(c.columns).toBeUndefined();
  });
});

describe("pickChartColumns", () => {
  it("group by a TEXT field + count: label=group, value=count", () => {
    const r = pickChartColumns(["status", "count"], [["paid", 2], ["draft", 1]]);
    expect(r).toEqual({ labelIdx: 0, valueIdx: 1 });
  });

  it("group by a NUMERIC field (Discount) + count: value must be count, not the group key", () => {
    // The regression this guards: both columns are numeric, so picking "first numeric"
    // wrongly chose the group key. The value column must skip the label column.
    const r = pickChartColumns(["discount", "count"], [[0, 50], [5, 2], [12.5, 1]]);
    expect(r.valueIdx).toBe(1);           // count, NOT discount(0)
    expect(r.valueIdx).not.toBe(r.labelIdx);
  });

  it("flat mode: text label + first numeric value", () => {
    const r = pickChartColumns(["invoice_number", "total"], [["INV1", 100]]);
    expect(r).toEqual({ labelIdx: 0, valueIdx: 1 });
  });

  it("no numeric column → valueIdx -1 (no chart)", () => {
    expect(pickChartColumns(["name"], [["a"], ["b"]]).valueIdx).toBe(-1);
  });

  it("empty rows → no value column", () => {
    expect(pickChartColumns(["status", "count"], []).valueIdx).toBe(-1);
  });
});
