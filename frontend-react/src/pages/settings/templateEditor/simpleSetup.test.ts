// Simple document setup: section edits, switches and Add detail all work on the stored HTML.
import { describe, it, expect } from "vitest";
import { SEEDED_TEMPLATES } from "./seededTemplates.fixture";
import { contentRoot, markForPreview, moveSection, parse, resizeSection, sections, serialize, setHidden, setText } from "./simpleDoc";
import { FEATURES, SUGGESTED, detailState, featureLocked, featureOn, setFeature, toggleDetail, detailFor } from "./docFeatures";
import { placeholders, sameTemplate, textBetweenRows } from "./templateHtml";

const invoice = SEEDED_TEMPLATES["Preset: Detailed invoice"];
const receipt = SEEDED_TEMPLATES["Sales receipt"];
const feature = (k: string) => FEATURES.find((f) => f.key === k)!;
const labels = (html: string) => sections(parse(html)).map((s) => s.getAttribute("data-label") || s.tagName);

describe("sections", () => {
  it("finds the page's sections, inside the receipt's wrapper too", () => {
    expect(labels(invoice)).toEqual(["Header", "Client", "Line items", "Totals", "Terms & notes"]);
    const r = parse(receipt);
    expect(contentRoot(r.body).tagName).toBe("DIV");
    expect(sections(r).map((s) => s.tagName)).toEqual(["P", "P", "P", "TABLE", "TABLE", "P"]);
  });
  it("parsing and saving back without a change keeps every condition where it was", () => {
    const back = serialize(parse(receipt));
    expect(textBetweenRows(back)).toBe(textBetweenRows(receipt));
    expect(placeholders(back)).toEqual(placeholders(receipt));
    expect(back).toContain("{#if Tax Value}<tr>");
  });
});

describe("click-to-edit", () => {
  it("Hide keeps the section in the template (hidden) and Show brings it back exactly", () => {
    const hidden = setHidden(invoice, 3, true);
    const totals = sections(parse(hidden))[3];
    expect(totals.getAttribute("data-tk-hidden")).toBe("1");
    expect(totals.getAttribute("style")).toContain("display: none");
    expect(sameTemplate(setHidden(hidden, 3, false), invoice)).toBe(true);
  });
  it("Move up / Move down swap sections and round-trip", () => {
    const moved = moveSection(invoice, 4, -1);
    expect(labels(moved)).toEqual(["Header", "Client", "Line items", "Terms & notes", "Totals"]);
    expect(sameTemplate(moveSection(moved, 3, 1), invoice)).toBe(true);
    expect(moveSection(invoice, 0, -1)).toBe(invoice);                 // nothing above the first: unchanged
  });
  it("Bigger / Smaller step the font size and round-trip", () => {
    const big = resizeSection(invoice, 1, 1);
    expect(sections(parse(big))[1].getAttribute("style")).toBe("font-size: 15px");
    const back = resizeSection(big, 1, -1);
    expect(sections(parse(back))[1].getAttribute("style")).toBe("font-size: 13px");
    // an existing size in pt is read correctly (18pt = 24px → 26px)
    const pt = resizeSection('<p style="font-size: 18pt;">A</p><p>B</p>', 0, 1);
    expect(pt).toBe('<p style="font-size: 26px">A</p><p>B</p>');
  });
  it("typing over plain text changes only that text; fields and conditions can't be typed over", () => {
    const marked = markForPreview(invoice);
    const doc = new DOMParser().parseFromString(marked, "text/html");
    const work = Array.from(doc.querySelectorAll("[data-tk-e]")).find((e) => e.textContent === "Work")!;
    const out = setText(invoice, Number(work.getAttribute("data-tk-e")), "Repairs done");
    expect(out).toContain("<h3>Repairs done</h3>");
    expect(placeholders(out)).toEqual(placeholders(invoice));
    expect(doc.querySelector('[data-tk-e]')!.textContent).not.toMatch(/[{}]/);
    expect(Array.from(doc.querySelectorAll("[data-tk-e]")).some((e) => /\{/.test(e.textContent || ""))).toBe(false);
  });
  it("an edit keeps the receipt's row conditions in place", () => {
    const out = resizeSection(receipt, 0, 1);
    expect(out).toContain("{#if Total Discount}<tr>");
    expect(textBetweenRows(out)).toBe(textBetweenRows(receipt));
  });
  it("marking for the preview never changes the stored template", () => {
    markForPreview(invoice);
    expect(invoice).toBe(SEEDED_TEMPLATES["Preset: Detailed invoice"]);
  });
});

describe("Show on documents switches", () => {
  it("add and remove the right section, and the saved HTML is the template plus that section", () => {
    const on = setFeature(invoice, feature("technician"), "invoice", true);
    expect(on).toContain('<div data-tk-section="technician"><p><strong>Technician:</strong> {Order Assignee Name}</p></div>');
    expect(featureOn(on, feature("technician"))).toBe(true);
    // above the terms footer, not after it
    expect(on.indexOf("Technician:")).toBeLessThan(on.indexOf('data-block="terms"'));
    const off = setFeature(on, feature("technician"), "invoice", false);
    expect(sameTemplate(off, invoice)).toBe(true);
  });
  it("turn off a section the shop's own layout has, and back on exactly as it was", () => {
    const wo = SEEDED_TEMPLATES["Preset: Full work order"];
    expect(featureOn(wo, feature("device"))).toBe(true);
    const off = setFeature(wo, feature("device"), "work_order", false);
    expect(featureOn(off, feature("device"))).toBe(false);
    expect(sameTemplate(setFeature(off, feature("device"), "work_order", true), wo)).toBe(true);
  });
  it("never takes away a bigger section: a field inside one locks the switch with a reason", () => {
    // the receipt's totals table shows {Tax Value} among other rows
    expect(featureOn(receipt, feature("tax"))).toBe(true);
    expect(featureLocked(receipt, feature("tax"))).toMatch(/own layout/);
  });
  it("is greyed (no section) where the kind of document can't show it, with a reason", () => {
    expect(feature("device").snippet("receipt")).toBeNull();
    expect(feature("device").reason("receipt")).toMatch(/sales/);
    expect(feature("payqr").snippet("invoice")).toBeNull();
    expect(setFeature(receipt, feature("device"), "receipt", true)).toBe(receipt);
  });
});

describe("Add detail", () => {
  it("suggests 6–8 details per kind of document", () => {
    for (const list of Object.values(SUGGESTED)) { expect(list.length).toBeGreaterThanOrEqual(6); expect(list.length).toBeLessThanOrEqual(8); }
  });
  it("tap adds a line with the right field, tap again takes it off", () => {
    const d = SUGGESTED.invoice.find((x) => x.tok === "{Legal Entity Tax ID}")!;
    const added = toggleDetail(invoice, d);
    expect(added).toContain('<p data-tk-section="field:{Legal Entity Tax ID}"><strong>Your tax number:</strong> {Legal Entity Tax ID}</p>');
    expect(detailState(added, d.tok)).toBe("ours");
    expect(sameTemplate(toggleDetail(added, d), invoice)).toBe(true);
  });
  it("a field the layout already shows is left alone", () => {
    expect(detailState(invoice, "{Invoice Number}")).toBe("layout");
    expect(toggleDetail(invoice, detailFor("{Invoice Number}"))).toBe(invoice);
  });
});
