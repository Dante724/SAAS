import { describe, expect, it } from "vitest";
import { countRows, parseCsv } from "./countSheet";

describe("parseCsv", () => {
  it("reads quoted fields, doubled quotes, CRLF and a BOM", () => {
    expect(parseCsv('﻿code,name\r\n"A,1","Say ""hi"""\r\nB2,Plain\r\n')).toEqual([
      ["code", "name"], ["A,1", 'Say "hi"'], ["B2", "Plain"]]);
  });
  it("reads semicolon and tab sheets", () => {
    expect(parseCsv("code;counted_qty\nX;3")).toEqual([["code", "counted_qty"], ["X", "3"]]);
    expect(parseCsv("code\tcounted_qty\nX\t3")).toEqual([["code", "counted_qty"], ["X", "3"]]);
  });
  it("drops blank lines", () => {
    expect(parseCsv("a\n\n,\nb\n")).toEqual([["a"], ["b"]]);
  });
});

describe("countRows", () => {
  it("round-trips the exported sheet with bins", () => {
    const sheet = "code,barcode,mpn,name,bin,system_qty,counted_qty\r\nRT-1,,,Round trip,A1,4.0,3\r\nRT-1,,,Round trip,B1,2.0,\r\n";
    const r = countRows(sheet);
    expect(r).toEqual({ hasBin: true, rows: [
      { code: "RT-1", counted_qty: "3", bin: "A1" }, { code: "RT-1", counted_qty: "", bin: "B1" }] });
  });
  it("reads a bin name the export guarded against formulas", () => {
    expect(countRows("code,bin,counted_qty\nX,'-A1,2")).toEqual({ hasBin: true, rows: [{ code: "X", bin: "-A1", counted_qty: "2" }] });
  });
  it("accepts the shop's own column names", () => {
    expect(countRows("SKU;Qty\n'0123;2")).toEqual({ hasBin: false, rows: [{ code: "0123", counted_qty: "2" }] });
  });
  it("says which column is missing", () => {
    expect(countRows("name,qty\nX,1")).toEqual({ error: "Add a column called code (or SKU, barcode, serial)." });
    expect(countRows("code,name\nX,1")).toEqual({ error: "Add a column called counted_qty with what you counted." });
    expect(countRows("code,counted_qty\n")).toEqual({ error: "The file has no rows to import." });
  });
});
