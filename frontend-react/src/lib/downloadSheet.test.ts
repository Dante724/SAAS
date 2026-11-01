// Vitest: browser CSV exports (contacts, products, reports) can't carry a live formula.
import { afterEach, describe, expect, it, vi } from "vitest";
import { downloadSheet } from "./downloadSheet";

describe("downloadSheet", () => {
  afterEach(() => vi.restoreAllMocks());

  it("guards formula-looking cells and keeps plain numbers", async () => {
    let blob: Blob | undefined;
    const createUrl = vi.fn((b: Blob) => { blob = b; return "blob:x"; });
    Object.assign(URL, { createObjectURL: createUrl, revokeObjectURL: vi.fn() });
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    await downloadSheet([["Name", "Phone"], ['=HYPERLINK("http://evil.test","x")', "+15550199"], ["@SUM(A1)", "-12"]], "csv", "contacts");
    expect(click).toHaveBeenCalled();
    const text = await blob!.text();
    expect(text).toContain(`"'=HYPERLINK(""http://evil.test"",""x"")"`);
    expect(text).toContain("'@SUM(A1)");
    expect(text).toContain("+15550199");   // a phone number is a number, not a formula
    expect(text).not.toContain("'+15550199");
    expect(text).toContain(",-12");
  });

  it("is the only place a page builds a CSV file", () => {
    const files = import.meta.glob(["../**/*.tsx", "../**/*.ts", "!../**/*.test.*", "!./downloadSheet.ts"],
      { query: "?raw", import: "default", eager: true }) as Record<string, string>;
    const byHand = Object.entries(files).filter(([, src]) => /type:\s*["'`]text\/csv/.test(src)).map(([f]) => f);
    expect(byHand).toEqual([]);
  });
});
