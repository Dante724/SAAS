// A3 — a pasted MPN column maps to the MPN field (it used to be swallowed as Code / SKU).
import { describe, it, expect } from "vitest";
import { analyzePaste, gridToRows, headerFieldFor, mergeRemap, FIELD_ORDER } from "./pasteMap";

describe("pasteMap MPN", () => {
  it("recognises MPN headers as their own field", () => {
    for (const h of ["MPN", "Mfr. Part #", "Manufacturer Part Number", "MFG PN", "OEM part#"]) expect(headerFieldFor(h)).toBe("mpn");
    expect(headerFieldFor("SKU")).toBe("code");
    expect(headerFieldFor("Barcode")).toBe("code");
    expect(FIELD_ORDER).toContain("mpn");
  });

  it("keeps SKU and MPN apart in a pasted sheet", () => {
    const { grid, map, hadHeader } = analyzePaste("SKU\tMPN\tName\tQty\tCost\nSCR-13\t661-12345\tiPhone 13 Screen\t4\t60.50");
    expect(hadHeader).toBe(true);
    expect(map).toEqual(["code", "mpn", "name", "qty", "cost"]);
    expect(gridToRows(grid, map)).toEqual([{ code: "SCR-13", mpn: "661-12345", name: "iPhone 13 Screen", qty: 4, purchase_price: 60.5, markup_pct: 0 }]);
  });

  it("keeps a row that only has an MPN", () => {
    expect(gridToRows([["661-1", "2"]], ["mpn", "qty"])).toEqual([{ code: "", mpn: "661-1", name: "", qty: 2, purchase_price: 0, markup_pct: 0 }]);
  });

  it("remapping a column to MPN keeps hand edits elsewhere", () => {
    const grid = [["A-1", "Screen", "3"]];
    const oldMap = ["code", "name", "qty"] as const;
    const edited = [{ ...gridToRows(grid, [...oldMap])[0], name: "Screen (edited)" }];
    const out = mergeRemap(grid, [...oldMap], ["mpn", "name", "qty"], edited);
    expect(out[0]).toMatchObject({ code: "", mpn: "A-1", name: "Screen (edited)" });
  });
});

describe("pasteMap file upload + serials", () => {
  it("uses the server's suggested (remembered per supplier) mapping and drops duplicates", async () => {
    const { fileToGrid, mapByHeader } = await import("./pasteMap");
    const { grid, map } = fileToGrid(["Part No", "Desc", "Qty", "Serials", "Desc 2"], [["SCR-1", "Screen", "2", "X1, X2", "x"]],
      { "Part No": "code", Desc: "name", Qty: "qty", Serials: "serials", "Desc 2": "name" });
    expect(map).toEqual(["code", "name", "qty", "serials", "ignore"]);
    expect(gridToRows(grid, map)[0]).toMatchObject({ code: "SCR-1", name: "Screen", qty: 2, serials: "X1, X2" });
    expect(mapByHeader(["Part No", "Desc", "Qty", "Serials", "Desc 2"], map)).toEqual({ "Part No": "code", Desc: "name", Qty: "qty", Serials: "serials", "Desc 2": "ignore" });
  });

  it("falls back to header synonyms and splits serials", async () => {
    const { fileToGrid, splitSerials } = await import("./pasteMap");
    expect(fileToGrid(["SKU", "IMEI"], [], {}).map).toEqual(["code", "serials"]);
    expect(splitSerials("A1, A2;A3\nA4 |  ")).toEqual(["A1", "A2", "A3", "A4"]);
    expect(splitSerials(undefined)).toEqual([]);
  });

  it("a remembered mapping beats a header guess", async () => {
    const { fileToGrid } = await import("./pasteMap");
    expect(fileToGrid(["Cost", "Net"], [], { Net: "cost" }).map).toEqual(["ignore", "cost"]);
    expect(fileToGrid(["SKU", "Name"], [], { SKU: "ignore" }).map).toEqual(["ignore", "name"]);
  });

  it("leaves serials off rows when no column maps to it", () => {
    expect(gridToRows([["A", "1"]], ["code", "qty"])[0]).not.toHaveProperty("serials");
  });
});
