import { describe, expect, it } from "vitest";
import { safeNext } from "./safeNext";

describe("safeNext", () => {
  it("accepts same-site paths", () => {
    expect(safeNext("?next=%2Fgo%2Ftoday")).toBe("/go/today");
    expect(safeNext("?next=/orders?open=1")).toBe("/orders?open=1");
  });
  it("refuses other sites and junk", () => {
    for (const bad of ["?next=//evil.example", "?next=https://evil.example", "?next=/%5Cevil", "?next=javascript:alert(1)", "?next=", "", "?next=/%0d%0aSet-Cookie"]) {
      expect(safeNext(bad)).toBe("/");
    }
  });
});
