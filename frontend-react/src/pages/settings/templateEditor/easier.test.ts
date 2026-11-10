// The "easier to use" pass: real data (display only), safe automatic fixes, the simple style panel.
import { describe, it, expect, afterEach } from "vitest";
import { Editor } from "@tiptap/core";
import grapesjs from "grapesjs";
import { sanitizeHtml } from "../../../lib/safeHtml";
import { SEEDED_TEMPLATES } from "./seededTemplates.fixture";
import { buildProbe, fieldDisplay, parseProbe, sampleValue, type FieldValues } from "./fieldValues";
import { fixRowConditions, hoistStyles, proposeFix, HIDE_RULE } from "./autoFix";
import { applySimple, readSimple } from "./simpleStyle";
import { fromTextView, setTextDisplay, textExtensions, toTextView } from "./tiptapSchema";
import { addTeklyTypes, baseConfig, exportDesign, mergeStyle, setDesignDisplay, snapshotStyles, toDesignView } from "./grapesSetup";
import { modeReason } from "./modeSupport";
import { placeholders, sameTemplate, staticReasons, textBetweenRows } from "./templateHtml";

afterEach(() => { setTextDisplay(null); setDesignDisplay(null); });

const DATA: FieldValues = { values: { "{Client Name}": "Neha Gupta", "{Company Logo}": '<img src="data:image/png;base64,AAAA" alt="logo">' }, sample: "WO-1001" };

describe("real data in the editor (display only)", () => {
  it("asks the server to fill every field once, with item-row fields inside a table row", () => {
    const probe = buildProbe(["{Client Name}", "{#if Paid}", "{Product Name}"]);
    expect(probe).toContain('<div data-tk-k="0">{Client Name}</div>');
    expect(probe).not.toContain("{#if Paid}");
    expect(probe).toContain('<table><tbody><tr><td data-tk-k="2">{Product Name}</td></tr></tbody></table>');
  });
  it("reads values back, ignoring fields the server left unfilled", () => {
    const toks = ["{Client Name}", "{Order Total}", "{Product Name}"];
    const rendered = '<div data-tk-k="0">Neha Gupta</div><div data-tk-k="1">{Order Total}</div><table><tbody><tr><td data-tk-k="2">Screen</td></tr><tr><td data-tk-k="2">Battery</td></tr></tbody></table>';
    expect(parseProbe(rendered, toks)).toEqual({ "{Client Name}": "Neha Gupta", "{Product Name}": "Screen" });
  });
  it("shows real values, clearly fake samples when there is no data, and chips for conditions or when asked", () => {
    expect(fieldDisplay("{Client Name}", DATA, false, sanitizeHtml)).toMatchObject({ kind: "real", html: "Neha Gupta" });
    expect(fieldDisplay("{Order Total}", DATA, false, sanitizeHtml)).toMatchObject({ kind: "sample", html: sampleValue("{Order Total}") });
    expect(sampleValue("{Order Total}")).toBe("Sample order total");
    expect(fieldDisplay("{#if Paid}", DATA, false, sanitizeHtml).kind).toBe("chip");
    expect(fieldDisplay("{Client Name}", DATA, true, sanitizeHtml).kind).toBe("chip");
  });
  it("Text: showing real data never changes the saved template", () => {
    const html = SEEDED_TEMPLATES["Credit note"];
    setTextDisplay((t) => fieldDisplay(t, DATA, false, sanitizeHtml));
    const { styles, body } = toTextView(html);
    const host = document.createElement("div"); document.body.appendChild(host);
    const ed = new Editor({ element: host, extensions: textExtensions(), content: body });
    expect(host.textContent).toContain("Neha Gupta");          // the page shows the real value…
    const out = fromTextView(styles, ed.getHTML());
    ed.destroy(); host.remove();
    expect(sameTemplate(out, html)).toBe(true);               // …and saves the field
    expect(placeholders(out)).toEqual(placeholders(html));
    expect(out).not.toContain("Neha Gupta");
  });
  it("Design: showing real data never changes the saved template", () => {
    const html = SEEDED_TEMPLATES["Preset: Detailed invoice"];
    setDesignDisplay((t) => fieldDisplay(t, DATA, false, sanitizeHtml));
    const editor = grapesjs.init({ ...baseConfig(), headless: true, container: document.createElement("div") });
    addTeklyTypes(editor);
    const load = toDesignView(html);
    editor.setComponents(load.body);
    const out = exportDesign(editor, load, snapshotStyles(editor));
    editor.destroy();
    expect(sameTemplate(out, html)).toBe(true);
    expect(out).not.toContain("Neha Gupta");
  });
});

describe("Make this editable visually", () => {
  const receipt = SEEDED_TEMPLATES["Sales receipt"];
  it("moves each row condition into its row and hides empty ones with one CSS rule", () => {
    const f = fixRowConditions(receipt)!;
    expect(f).not.toBeNull();
    expect(f.html.startsWith(`<style>${HIDE_RULE}</style>`)).toBe(true);
    expect(f.html).toContain('<tr data-tk-show="{#if Total Discount}yes{/if}"><td>Discount</td>');
    expect(f.html).toContain('<tr data-tk-show="{#if Gift Card}yes{/if}">');
    expect(textBetweenRows(f.html)).toBeNull();
    // the condition outside the table is left as it was
    expect(f.html).toContain("{#if Client Name}<p");
    expect(placeholders(f.html).sort()).toEqual(placeholders(receipt).sort());
    expect(f.changes.map((c) => c.what).join("\n")).toMatch(/Total Discount/);
  });
  it("prints the same: the server fills the condition, and an emptied row is hidden", () => {
    const f = fixRowConditions(receipt)!;
    // the server's own rule (documents.py _conditionals): {#if X}a{/if} → a when X has a value
    const render = (h: string, vals: Record<string, string>) => h.replace(/\{#(if|unless)\s+([^}]+?)\}((?:(?!\{#(?:if|unless)\b).)*?)\{\/\1\}/gs,
      (_m, k, n, inner) => ((!!vals[n.trim()]) === (k === "if") ? inner : ""));
    const out = render(f.html, { "Tax Value": "1.20" });
    const doc = new DOMParser().parseFromString(out, "text/html");
    const hidden = Array.from(doc.querySelectorAll('tr[data-tk-show=""]')).map((r) => r.textContent);
    const shown = Array.from(doc.querySelectorAll('tr[data-tk-show="yes"]')).map((r) => r.textContent);
    expect(hidden).toEqual(["Discount−{Total Discount}", "Gift card−{Gift Card}"]);
    expect(shown).toEqual(["Tax{Tax Value}"]);
  });
  it("is offered for the sales receipt: after the fix both views open it and save it unchanged", async () => {
    const f = proposeFix(receipt)!;
    expect(await modeReason("design", f.html)).toBeNull();
    expect(await modeReason("text", f.html)).toBeNull();
  });
  it("{#else} rows get the opposite condition", () => {
    const f = fixRowConditions("<table><tbody>{#if Paid}<tr><td>PAID</td></tr>{#else}<tr><td>DUE</td></tr>{/if}</tbody></table>")!;
    expect(f.html).toContain('<tr data-tk-show="{#if Paid}yes{/if}"><td>PAID</td></tr><tr data-tk-show="{#unless Paid}yes{/unless}"><td>DUE</td></tr>');
  });
  it("moves a style block from inside the page to the top", () => {
    const f = hoistStyles("<style>a{}</style><p>x</p><style>p{color:red}</style><p>y</p>")!;
    expect(f.html).toBe("<style>a{}</style><style>p{color:red}</style><p>x</p><p>y</p>");
    expect(staticReasons(f.html).design).toBeNull();
  });
  it("offers nothing when there is no safe rewrite", () => {
    // a condition that opens in one row and closes in another can't be moved into a row
    expect(proposeFix("<table><tbody>{#if Paid}<tr><td>a</td></tr><tr><td>b</td></tr>{/if}{#if X}<tr><td>c</td>{/if}</tr></tbody></table>")?.html ?? "").not.toContain("data-tk-show=\"{#if X}");
    expect(proposeFix("<p>{Client Name}</p>")).toBeNull();
  });
});

describe("simple style panel", () => {
  it("writes plain CSS for each choice and reads it back", () => {
    let s = applySimple({}, "size", "large");
    s = applySimple(s, "bold", true);
    s = applySimple(s, "colour", "#0059ba");
    s = applySimple(s, "align", "center");
    s = applySimple(s, "spacing", "roomy");
    s = applySimple(s, "font", "Georgia");
    expect(s).toEqual({ "font-size": "16px", "font-weight": "bold", color: "#0059ba", "text-align": "center", margin: "0 0 16px", "font-family": "Georgia, Arial, sans-serif" });
    expect(readSimple(s)).toEqual({ font: "Georgia", size: "large", bold: true, colour: "#0059ba", align: "center", spacing: "roomy" });
    expect(applySimple(s, "bold", false)["font-weight"]).toBeUndefined();
    expect(readSimple({ "font-size": "17px" }).size).toBeNull();      // a custom size shows as no choice
  });
  it("Design saves only what changed, keeping the rest of the original style text", () => {
    const html = '<p style="color:#666;padding:4mm 3mm">Terms</p><p>Other</p>';
    const editor = grapesjs.init({ ...baseConfig(), headless: true, container: document.createElement("div") });
    addTeklyTypes(editor);
    const load = toDesignView(html);
    editor.setComponents(load.body);
    const snap = snapshotStyles(editor);
    const [p1, p2] = editor.getWrapper()!.components().models;
    p1.setStyle(applySimple(p1.getStyle() as any, "size", "title"));
    p2.setStyle(applySimple(p2.getStyle() as any, "align", "right"));
    const out = exportDesign(editor, load, snap);
    editor.destroy();
    expect(out).toBe('<p style="color: #666; padding: 4mm 3mm; font-size: 22px">Terms</p><p style="text-align: right">Other</p>');
  });
  it("mergeStyle replaces a shorthand family when it changes", () => {
    expect(mergeStyle("margin-top:4px;color:red", { "margin-top": "4px", color: "red" }, { color: "red", margin: "0 0 2px" })).toBe("color: red; margin: 0 0 2px");
  });
});
