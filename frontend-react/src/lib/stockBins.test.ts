import { describe, expect, it } from "vitest";
import { binChoices, byLocation, needsBinChoice, rowLabel } from "./stockBins";

const rows = [
  { location: "Main", location_id: "L1", bin_id: "a", bin: "A1", available: 2 },
  { location: "Main", location_id: "L1", bin_id: "b", bin: "B1", available: 5 },
  { location: "Main", location_id: "L1", bin_id: null, bin: null, available: 1 },
  { location: "Annex", location_id: "L2", bin_id: "c", bin: "C1", available: 4 },
  { location: "Main", location_id: "L1", bin_id: "e", bin: "Empty", available: 0 },
];

describe("stock by bin", () => {
  it("labels bin rows with their bin and unbinned rows with the location", () => {
    expect(rowLabel(rows[0])).toBe("Main · A1");
    expect(rowLabel(rows[2])).toBe("Main");
  });
  it("adds the bins up per location", () => {
    expect(byLocation(rows)).toEqual([
      { location: "Main", location_id: "L1", available: 8 },
      { location: "Annex", location_id: "L2", available: 4 },
    ]);
  });
  it("offers the location's bins that hold stock, fullest first", () => {
    expect(binChoices(rows, "L1").map((b) => b.bin_id)).toEqual(["b", "a"]);
    expect(binChoices(rows, "L1")[0].label).toBe("B1 (5)");
    expect(binChoices(rows, "L2").map((b) => b.bin_id)).toEqual(["c"]);
  });
  it("asks only when two places at the location hold it", () => {
    expect(needsBinChoice(rows, "L1")).toBe(true);
    expect(needsBinChoice(rows, "L2")).toBe(false);
    expect(needsBinChoice([{ location: "Main", location_id: "L1", bin_id: "a", bin: "A1", available: 3 },
      { location: "Main", location_id: "L1", bin_id: null, available: 2 }], "L1")).toBe(true);
    expect(needsBinChoice([], "L1")).toBe(false);
    expect(needsBinChoice(rows, null)).toBe(false);
  });
});
