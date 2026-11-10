// Every seeded template, and the placeholder syntax the server understands, must come back out of
// the Design (GrapesJS) and Text (Tiptap) views unchanged; anything that can't is greyed out.
import { describe, it, expect } from "vitest";
import { Editor } from "@tiptap/core";
import grapesjs from "grapesjs";
import { SEEDED_TEMPLATES } from "./seededTemplates.fixture";
import { fromTextView, textExtensions, textRoundTrip, toTextView } from "./tiptapSchema";
import { addTeklyTypes, baseConfig, designChip, designRoundTrip, exportDesign, snapshotStyles, toDesignView } from "./grapesSetup";
import { modeReason } from "./modeSupport";
import { placeholders, sameTemplate } from "./templateHtml";

const SYNTAX: Record<string, string> = {
  "inline condition": "<p>Status: {#if Order Paid}<strong>PAID</strong>{#else}due {Order Balance Due}{/if}</p>",
  "unless around paragraphs": "<p>{Company Name}</p>\n{#unless Client Email}\n<p>Ask for an email address.</p>\n{/unless}",
  "component block": "{{component: Footer}}\n<p>Thanks, {Client Name}!</p>",
  "row loop with conditions in cells": '<table style="width:100%"><thead><tr><th>Item</th><th>Qty</th></tr></thead><tbody><tr><td>{Product Name}{#if Product Warranty} ({Product Warranty}){/if}</td><td style="text-align:right">{Product Qty}</td></tr></tbody><tfoot><tr><td>Total</td><td>{Total Amount}</td></tr></tfoot></table>',
  "placeholder in an attribute": '<p><a href="https://example.com/track/{Order Number}">Track {Order Number}</a></p>',
};
const ALL = { ...SEEDED_TEMPLATES, ...SYNTAX };
const supported = Object.entries(ALL).filter(([n]) => n !== "Sales receipt");

describe.each(["text", "design"] as const)("%s view", (mode) => {
  const trip = mode === "text" ? textRoundTrip : designRoundTrip;
  it.each(supported)("opens and saves %s unchanged", async (_n, html) => {
    const out = trip(html);
    expect(sameTemplate(out, html)).toBe(true);
    expect(placeholders(out)).toEqual(placeholders(html));
    expect(await modeReason(mode, html)).toBeNull();
  });
  it("is greyed out for the sales receipt, whose conditions sit between table rows", async () => {
    expect(await modeReason(mode, SEEDED_TEMPLATES["Sales receipt"])).toMatch(/between table rows/);
  });
  it("is greyed out when it would lose formatting", async () => {
    // A <colgroup> (Text) / a mid-page style block (both) can't be kept.
    const html = '<table><colgroup><col width="40"></colgroup><tbody><tr><td>a</td></tr></tbody></table>';
    if (mode === "text") expect(await modeReason("text", html)).toMatch(/column groups/);
    expect(await modeReason(mode, "<p>a</p><style>p{color:red}</style>")).toMatch(/style block/);
  });
});

it("Text: inserting a variable stores plain placeholder text and leaves the rest alone", () => {
  const html = SEEDED_TEMPLATES["Credit note"];
  const { styles, body } = toTextView(html);
  const ed = new Editor({ extensions: textExtensions(), content: body });
  ed.commands.setTextSelection(ed.state.doc.content.size - 1);
  ed.commands.insertContent({ type: "variable", attrs: { tok: "{Client Phone}" } });
  const out = fromTextView(styles, ed.getHTML());
  ed.destroy();
  expect(out).not.toContain("data-tok");
  expect(placeholders(out).filter((t) => t === "{Client Phone}").length).toBe(placeholders(html).filter((t) => t === "{Client Phone}").length + 1);
  expect(out).toContain("{Credit Note Table}");
  expect(out).toContain('style="border-collapse: collapse; width: 100%;"');
});

it("Design: dropping a variable block stores plain placeholder text and keeps the original styles", () => {
  const html = SEEDED_TEMPLATES["Estimate"];
  const editor = grapesjs.init({ ...baseConfig(), headless: true, container: document.createElement("div") });
  addTeklyTypes(editor);
  const load = toDesignView(html);
  editor.setComponents(load.body);
  const snap = snapshotStyles(editor);
  editor.getWrapper()!.append(designChip("{Order Total}", true));
  const out = exportDesign(editor, load, snap);
  editor.destroy();
  expect(out).not.toContain("data-tok");
  expect(out.endsWith("{Order Total}")).toBe(true);
  expect(out.startsWith("<style>")).toBe(true);
  expect(out).toContain('<td style="width: 50%; text-align: right;">');
  expect(placeholders(out).slice(0, -1)).toEqual(placeholders(html));
});

it("Design never phones home (telemetry off, nothing stored in the browser)", () => {
  const cfg = baseConfig() as any;
  expect(cfg.telemetry).toBe(false);
  expect(cfg.storageManager).toBe(false);
  expect(cfg.panels).toEqual({ defaults: [] });
});
