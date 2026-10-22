// @vitest-environment node
import { describe, expect, it } from "vitest";
import { unzipSync, strFromU8 } from "fflate";
import { guardCell, toCsv, toXlsx } from "./sheet";

describe("sheet writer", () => {
  it("guards formula-looking text but leaves numbers and phones", () => {
    expect(guardCell("=HYPERLINK(1)")).toBe("'=HYPERLINK(1)");
    expect(guardCell("+91 98200 11223")).toBe("+91 98200 11223");
    expect(guardCell(-3)).toBe(-3);
  });

  it("writes quoted CSV", () => {
    expect(toCsv([["a,b", 'say "hi"', 3]])).toBe('"a,b","say ""hi""",3');
  });

  it("writes a valid xlsx with inline strings and numbers", () => {
    const files = unzipSync(toXlsx([["Name", "Total"], ["=cmd|x", 12.5]]));
    expect(Object.keys(files)).toContain("xl/worksheets/sheet1.xml");
    const sheet = strFromU8(files["xl/worksheets/sheet1.xml"]);
    expect(sheet).toContain('<c r="B2"><v>12.5</v></c>');
    expect(sheet).toContain('t="inlineStr"><is><t xml:space="preserve">=cmd|x</t>');
    expect(sheet).not.toContain("<f>");
  });
});
