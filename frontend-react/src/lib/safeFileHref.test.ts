import { describe, expect, it } from "vitest";
import { safeFileHref, safeHttpHref } from "./safeFileHref";

describe("safeFileHref", () => {
  it("keeps real files and our own media links", () => {
    expect(safeFileHref("data:application/pdf;base64,JVBERi0=")).toBe("data:application/pdf;base64,JVBERi0=");
    expect(safeFileHref("data:image/png;base64,iVBOR")).toBeTruthy();
    expect(safeFileHref("/api/media/abc123")).toBe("/api/media/abc123");
  });
  it("drops script, web pages, SVG and outside links", () => {
    for (const bad of [
      "javascript:alert(1)", " JaVaScRiPt:alert(1)", "data:text/html;base64,PHNjcmlwdD4=",
      "data:image/svg+xml;base64,PHN2Zz4=", "https://evil.example/x.exe", "data:application/pdf,notbase64",
      "/api/media/../../etc", "", undefined, null,
    ]) expect(safeFileHref(bad as any)).toBeUndefined();
  });
});

describe("safeHttpHref", () => {
  it("keeps web links only", () => {
    expect(safeHttpHref("https://g.page/r/abc")).toBe("https://g.page/r/abc");
    expect(safeHttpHref("http://example.com")).toBeTruthy();
    for (const bad of ["javascript:alert(1)", "data:text/html;base64,x", "//evil", "", null]) expect(safeHttpHref(bad as any)).toBeUndefined();
  });
});
