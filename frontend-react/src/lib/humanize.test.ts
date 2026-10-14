// Vitest: humanize — raw enum/key → human label for display.
import { describe, it, expect } from "vitest";
import { humanize } from "./humanize";

describe("humanize", () => {
  it("empty/nullish → ''", () => {
    expect(humanize("")).toBe("");
    expect(humanize(undefined)).toBe("");
    expect(humanize(null)).toBe("");
  });

  it("underscores and dashes become spaces, each word capitalised", () => {
    expect(humanize("in_use")).toBe("In Use");
    expect(humanize("partially_paid")).toBe("Partially Paid");
    expect(humanize("write-off")).toBe("Write Off");
    expect(humanize("awaiting_parts")).toBe("Awaiting Parts");
  });

  it("collapses repeated/mixed separators and trims", () => {
    expect(humanize("__in___repair__")).toBe("In Repair");
    expect(humanize("  ready for  pickup ")).toBe("Ready For Pickup");
  });

  it("is idempotent on already-nice strings", () => {
    expect(humanize("In Use")).toBe("In Use");
    expect(humanize(humanize("in_use"))).toBe("In Use");
  });

  it("single word", () => {
    expect(humanize("booked")).toBe("Booked");
  });
});
