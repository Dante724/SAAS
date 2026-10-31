import { describe, expect, it } from "vitest";
import { groupKitLines, KIT_PRINT_LABELS, kitPrintLabel } from "./kitGroups";

const l = (id: string, total: number, g?: string, extra: Record<string, unknown> = {}) => ({
  id, total, description: id, bundle_group_id: g ?? null, bundle_name: g ? "Screen Replacement" : null,
  bundle_print_mode: g ? "one_line" : null, bundle_price_locked: g ? 149.99 : null, bundle_qty: g ? 1 : null, ...extra,
});

describe("groupKitLines", () => {
  it("leaves a job with no kits as plain lines", () => {
    const out = groupKitLines([l("a", 1), l("b", 2)]);
    expect(out.map((s) => s.type)).toEqual(["line", "line"]);
  });

  it("groups a kit at its first line and labels the rest as extras", () => {
    const out = groupKitLines([l("case", 5), l("scr", 90, "g1"), l("fit", 59.99, "g1"), l("chg", 25)]);
    expect(out.map((s) => s.type)).toEqual(["kit", "extras", "line", "line"]);
    const kit = out[0];
    if (kit.type !== "kit") throw new Error("expected a kit");
    expect(kit.lines.map((x) => x.id)).toEqual(["scr", "fit"]);
    expect(kit.total).toBe(149.99);
    expect(kit.locked).toBe(149.99);
    expect(kit.mode).toBe("one_line");
  });

  it("keeps two kits apart", () => {
    const out = groupKitLines([l("a", 1, "g1"), l("b", 2, "g2"), l("c", 3, "g1")]);
    const kits = out.filter((s) => s.type === "kit");
    expect(kits).toHaveLength(2);
    expect(out.some((s) => s.type === "extras")).toBe(false);
  });

  it("treats an unknown print setting as item by item", () => {
    const out = groupKitLines([l("a", 1, "g1", { bundle_print_mode: "weird" })]);
    expect(out[0].type === "kit" && out[0].mode).toBe("itemized");
  });
});

describe("kit print labels", () => {
  it("uses plain words", () => {
    expect(KIT_PRINT_LABELS.map((o) => o.label)).toEqual(["Item by item", "One line", "One line and what's included"]);
    expect(kitPrintLabel("one_line_includes")).toBe("One line and what's included");
    expect(kitPrintLabel(undefined)).toBe("Item by item");
  });
});
