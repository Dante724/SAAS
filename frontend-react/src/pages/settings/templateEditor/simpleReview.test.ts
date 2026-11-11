// Review fixes for the simple document setup (PR #183): one failing test per finding first.
import { describe, it, expect } from "vitest";
import { SEEDED_TEMPLATES } from "./seededTemplates.fixture";
import { sanitizeHtml } from "../../../lib/safeHtml";
import { applyEdit, editBase, markForPreview, moveBlocked, moveSection, moveSectionAt, parse, sections, setEditCleaner, setHidden } from "./simpleDoc";
import { cleanTemplate, placeholders } from "./templateHtml";

setEditCleaner((h) => cleanTemplate(h, sanitizeHtml));

const invoice = SEEDED_TEMPLATES["Preset: Detailed invoice"];
const receipt = SEEDED_TEMPLATES["Sales receipt"];

describe("review H1: moving never crosses a condition", () => {
  const between = (html: string, open: string) => { const a = html.indexOf(open); return html.slice(a, html.indexOf("{/if}", a)); };
  const tableAt = (html: string) => sections(parse(html)).findIndex((s) => s.tagName === "TABLE");
  it("the items table moved up stays outside {#if Client Name}, so walk-in receipts still print it", () => {
    const out = moveSection(receipt, tableAt(receipt), -1);
    expect(out).not.toBe(receipt);
    expect(between(out, "{#if Client Name}")).not.toContain("<table");
    expect(out.indexOf("{Product Name}")).toBeLessThan(out.indexOf("{#if Client Name}"));
  });
  it("“Receipt {Sale Number}” moved down jumps over the whole condition, never into it", () => {
    const i = sections(parse(receipt)).findIndex((s) => (s.textContent || "").includes("{Sale Number}"));
    const out = moveSection(receipt, i, 1);
    expect(between(out, "{#if Client Name}")).not.toContain("{Sale Number}");
    expect(out.indexOf("{Sale Number}")).toBeGreaterThan(out.indexOf("{/if}"));
  });
  it("a section under a condition moves together with its condition", () => {
    const i = sections(parse(receipt)).findIndex((s) => (s.textContent || "").includes("Customer:"));
    const out = moveSection(receipt, i, -1);
    expect(between(out, "{#if Client Name}")).toContain("Customer:");
    expect(out.indexOf("{#if Client Name}")).toBeLessThan(out.indexOf("{Sale Number}"));
    expect(placeholders(out).sort()).toEqual(placeholders(receipt).sort());
  });
  it("reports where the moved section ended up, so the popover can follow it", () => {
    const i = sections(parse(receipt)).findIndex((s) => (s.textContent || "").includes("{Sale Number}"));
    const r = moveSectionAt(receipt, i, 1);
    expect((sections(parse(r.html))[r.i].textContent || "")).toContain("{Sale Number}");
    expect(r.i).toBe(i + 1);
  });
  it("refuses a move where the condition isn't closed at the same level, and says why", () => {
    const odd = "<p>A</p>{#if Paid}<p>B</p><div>C{/if}</div><p>D</p>";
    expect(moveSection(odd, 1, -1)).toBe(odd);
    expect(moveBlocked(odd, 1)).toMatch(/condition/);
    expect(moveBlocked(receipt, 0)).toBeNull();
  });
});

describe("review L1: styles with url(data:…;base64,…) survive an edit", () => {
  it("keeps the data URL whole", () => {
    const html = '<div style="background:url(data:image/png;base64,AAAA);color:red">A</div><p>B</p>';
    const out = setHidden(html, 0, true);
    expect(out).toContain("url(data:image/png;base64,AAAA)");
    expect(setHidden(out, 0, false)).toContain('style="background: url(data:image/png;base64,AAAA); color: red"');
  });
});

describe("review M1: edits use the same text the preview numbered", () => {
  it("a dropped <link> doesn't shift the section numbers", () => {
    const raw = '<link rel="stylesheet" href="x.css"><h1>Title</h1><p>Hello</p>';
    const base = editBase(raw);
    expect(base).not.toContain("<link");                       // the preview numbers the cleaned page
    const marked = new DOMParser().parseFromString(markForPreview(base), "text/html");
    const title = Number(marked.querySelector("h1")!.getAttribute("data-tk-i"));
    const out = applyEdit(raw, { type: "hide", i: title, hidden: true }, base)!;
    expect(out).toContain('<h1 data-tk-hidden="1" style="display: none">Title</h1>');
    expect(out).toContain("<p>Hello</p>");
  });
  it("an edit numbered against an older page is ignored", () => {
    const base = editBase(invoice);
    const changed = moveSection(invoice, 4, -1);
    expect(applyEdit(changed, { type: "hide", i: 3, hidden: true }, base)).toBeNull();
  });
});
