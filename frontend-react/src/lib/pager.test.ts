// Vitest: pager page-number/ellipsis sequence.
import { describe, it, expect } from "vitest";
import { pageNumbers } from "./pager";

describe("pageNumbers", () => {
  it("single page", () => expect(pageNumbers(1, 1)).toEqual([1]));
  it("3 pages, all shown", () => expect(pageNumbers(1, 3)).toEqual([1, 2, 3]));
  it("5 pages, window covers all, no ellipsis", () => expect(pageNumbers(3, 5)).toEqual([1, 2, 3, 4, 5]));
  it("page 1 of 6 — trailing gap only", () => expect(pageNumbers(1, 6)).toEqual([1, 2, "…", 6]));
  it("page 2 of 6 — trailing gap only", () => expect(pageNumbers(2, 6)).toEqual([1, 2, 3, "…", 6]));
  it("last page of 6 — leading gap only", () => expect(pageNumbers(6, 6)).toEqual([1, "…", 5, 6]));
  it("middle page — gaps both sides", () => expect(pageNumbers(5, 9)).toEqual([1, "…", 4, 5, 6, "…", 9]));
  it("zero pages treated as 1", () => expect(pageNumbers(1, 0)).toEqual([1]));
});
