import { describe, it, expect } from "vitest";
import { countBy, errorsCsv, errorsSheet, missingRequired, sampleValues, type FieldDef, type RowVerdict } from "./importRows";

const parsed = { headers: ["Name", "SKU"], rows: [["Good", "G-1"], ["", "X"], ["=cmd", "B-2"]], row_numbers: [2, 4, 5] };
const verdicts: RowVerdict[] = [
  { row: 2, action: "create", label: "Good", matched_on: null, errors: [], warnings: [] },
  { row: 4, action: "error", label: "", matched_on: null, errors: ["Name is required for a new product"], warnings: [] },
  { row: 5, action: "error", label: "=cmd", matched_on: null, errors: ["SKU B-2 is also on row 3", "Bad unit"], warnings: [] },
];

describe("import drawer helpers", () => {
  it("lists only the failed rows, with the sheet's own row number and the problem", () => {
    expect(errorsSheet(parsed, verdicts)).toEqual([
      ["Row", "Name", "SKU", "Problem"],
      [4, "", "X", "Name is required for a new product"],
      [5, "=cmd", "B-2", "SKU B-2 is also on row 3; Bad unit"],
    ]);
  });

  it("guards formula-looking cells in the errors CSV", () => {
    expect(errorsCsv(parsed, verdicts)).toContain("'=cmd");
  });

  it("flags required fields with no column", () => {
    const fields = [{ key: "name", label: "Name", required: true }, { key: "sku", label: "SKU", required: false }] as FieldDef[];
    expect(missingRequired(fields, { SKU: "sku" }).map((f) => f.key)).toEqual(["name"]);
    expect(missingRequired(fields, { Title: "name" })).toEqual([]);
  });

  it("shows the first non-blank value of each column", () => {
    expect(sampleValues(parsed)).toEqual(["Good", "G-1"]);
  });

  it("counts verdicts", () => {
    expect(countBy(verdicts)).toEqual({ create: 1, update: 0, skip: 0, error: 2 });
  });
});
