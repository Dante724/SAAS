import { describe, expect, it } from "vitest";
import { toBody, toSteps } from "./ChecklistSettings";

// The editor folds sections into steps and shows only some per-check settings. Saving must not
// lose the rest (upsell rules, descriptions, notes) — the old editor dropped them.
describe("checklist editor round trip", () => {
  const tpl = {
    id: "t1", name: "Intake", scope: "all", type_ids: [],
    steps: [{
      title: "Condition", description: "Look it over",
      sections: [
        { title: "A", allow_notes: true, items: [{ label: "Battery", field_kind: "check_result", required: true, client_visible: true, affects_result: true, options: [],
          description: "Under 80% health fails", upsell_kind: "product", upsell_ref_id: "p1", upsell_price: 49, upsell_threshold: null, upsell_comparator: "fail" }] },
        { title: "B", allow_notes: false, items: [{ label: "Screen", field_kind: "dropdown", required: false, client_visible: false, affects_result: false, options: ["Perfect", "Cracked"] }] },
      ],
    }],
  };

  it("keeps hidden settings and folds sections into one step", () => {
    const body = toBody("Intake", "all", [], toSteps(tpl));
    expect(body.steps).toHaveLength(1);
    const sec = body.steps[0].sections[0];
    expect(body.steps[0].description).toBe("Look it over");
    expect(sec.allow_notes).toBe(true);
    expect(sec.items.map((i: any) => i.label)).toEqual(["Battery", "Screen"]);
    expect(sec.items[0]).toMatchObject({ description: "Under 80% health fails", upsell_kind: "product", upsell_ref_id: "p1", upsell_price: 49, upsell_comparator: "fail", client_visible: true });
    expect(sec.items[1]).toMatchObject({ field_kind: "dropdown", options: ["Perfect", "Cracked"], required: false });
  });

  it("only sends choices for pick-one / pick-several", () => {
    const steps = toSteps(tpl);
    steps[0].items[1].field_kind = "text";
    expect(toBody("x", "selected", ["a"], steps).steps[0].sections[0].items[1].options).toEqual([]);
    expect(toBody("x", "selected", ["a"], steps).type_ids).toEqual(["a"]);
  });
});
