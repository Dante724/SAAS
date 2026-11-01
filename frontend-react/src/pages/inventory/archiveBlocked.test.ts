import { describe, expect, it } from "vitest";
import { keptMessage, splitBlocked, writeOffPrompt } from "./archiveBlocked";

const stocked = { id: "a", name: "Screen", reason: "3 still in stock", in_stock: 3 };
const kit = { id: "b", name: "Cable", reason: "Used in a bundle or job kit. Remove it there first", in_stock: 0 };

describe("archive blocked items", () => {
  it("separates stock (one-step write-off) from other reasons", () => {
    expect(splitBlocked([stocked, kit])).toEqual({ stocked: [stocked], other: [kit] });
  });

  it("offers to write off and archive", () => {
    expect(writeOffPrompt([stocked])).toBe("“Screen”: 3 still in stock. Write them off and archive?");
    expect(writeOffPrompt([stocked, { ...stocked, id: "c", name: "Glass", in_stock: 1 }]))
      .toContain("2 products still have stock:\nScreen — 3\nGlass — 1");
  });

  it("shows the server's reason per item, not a guess", () => {
    expect(keptMessage([kit, { ...stocked, reason: "2 of “Screen” are reserved for a job." }]))
      .toBe("2 item(s) were kept:\nCable — Used in a bundle or job kit. Remove it there first\n"
        + "Screen — 2 of “Screen” are reserved for a job.");
  });
});
