// GrapesJS (BSD-3-Clause, core only, no plugins) set up for printable templates. Loaded on demand.
//
// GrapesJS normally moves inline styles into a generated stylesheet keyed by made-up ids and
// rewrites <style> blocks. Templates are printed by the server from the stored HTML alone, so
// the export below writes every style back inline, drops the made-up ids, keeps the leading
// <style> block exactly as written, and turns chips back into placeholder text.
import grapesjs, { type Component, type Editor, type EditorConfig } from "grapesjs";
import { chipLabel, chipsToTokens, escapeAttr, splitLeadingStyles, tokensToChips } from "./templateHtml";
import { paintField, type FieldDisplay } from "./fieldValues";

export const VAR_TYPE = "tekly-var";

function escapeText(s: string) { return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/ /g, "&nbsp;"); }

/** Chip element for a placeholder; GrapesJS turns it into a VAR_TYPE component. */
export const designChip = (tok: string, block = false) =>
  block ? `<div data-tok="${escapeAttr(tok)}"></div>` : `<span data-tok="${escapeAttr(tok)}"></span>`;

// How a field shows in the canvas (a chip, or the shop's real value). Set by the visible editor;
// the headless round-trip check never renders, so it doesn't need one.
let display: ((tok: string) => FieldDisplay) | null = null;
export function setDesignDisplay(fn: ((tok: string) => FieldDisplay) | null) { display = fn; }

/** Placeholders become one piece in the canvas: dragged or deleted as a whole, never typed into.
 *  What it shows is painted by the view; what it saves is always the placeholder text. */
function addVariableType(editor: Editor) {
  editor.Components.addType(VAR_TYPE, {
    isComponent: (el: HTMLElement) => (el?.getAttribute?.("data-tok") != null ? { type: VAR_TYPE, tok: el.getAttribute("data-tok"), tagName: el.tagName.toLowerCase() } : false),
    model: {
      defaults: { tok: "", droppable: false, editable: false, textable: true, copyable: true, stylable: false, highlightable: true, layerable: true, components: [] },
      init(this: Component) {
        const tok = String(this.get("tok") || this.getAttributes()["data-tok"] || "");
        this.set("tok", tok);
        this.addAttributes({ "data-tok": tok, contenteditable: "false" });
        this.components("");
        this.set("name", chipLabel(tok));
      },
      toHTML(this: Component) { return escapeText(String(this.get("tok") || "")); },
    },
    view: {
      onRender({ el, model }: { el: HTMLElement; model: Component }) {
        const tok = String(model.get("tok") || "");
        paintField(el, tok, display ? display(tok) : { kind: "chip", html: escapeText(chipLabel(tok)), label: chipLabel(tok) });
      },
    },
  });
}

/** Stored template → what GrapesJS loads. Placeholders that stand between blocks become block
 *  chips; ones inside text become inline chips. */
// Each inline style is numbered (data-tk-s) on the way in. GrapesJS parses styles into objects
// (padding: 4mm 3mm comes back as four longhands plus the shorthand), so an element whose
// style the owner hasn't touched is written back with its original text.
export type DesignLoad = { styles: string; body: string; originals: string[]; keepIds: Set<string> };
export function toDesignView(html: string): DesignLoad {
  const { styles, body } = splitLeadingStyles(html);
  const doc = new DOMParser().parseFromString(`<!doctype html><body>${tokensToChips(body, designChip)}</body>`, "text/html");
  const originals: string[] = [];
  doc.body.querySelectorAll("[style]").forEach((el) => { el.setAttribute("data-tk-s", String(originals.length)); originals.push(el.getAttribute("style") || ""); });
  return { styles, body: doc.body.innerHTML, originals, keepIds: idsIn(html) };
}

const AUTO_ID = /^i[a-z0-9]{2,8}(-\d+)?$/;
type Obj = Record<string, string>;
const styleOf = (cmp: Component): Obj => Object.fromEntries(Object.entries(cmp.getStyle() as Record<string, any>).filter(([k, v]) => k !== "__p" && v !== "" && v != null).map(([k, v]) => [k, String(v)]));

/** Call once the template is loaded: remembers each numbered element's parsed style. */
export function snapshotStyles(editor: Editor): Map<string, Obj> {
  const snap = new Map<string, Obj>();
  const walk = (c: Component) => {
    const n = c.getAttributes()["data-tk-s"];
    if (n != null && !snap.has(String(n))) snap.set(String(n), styleOf(c));
    c.components().forEach((k: Component) => walk(k));
  };
  const w = editor.getWrapper(); if (w) walk(w);
  return snap;
}

/** The original style text with only what the owner changed: changed or new properties are
 *  set, removed ones dropped, and everything else keeps its original wording and order. */
export function mergeStyle(original: string, before: Obj, now: Obj): string {
  const decls = original.split(";").map((d) => d.trim()).filter(Boolean).map((d) => {
    const i = d.indexOf(":"); return [d.slice(0, i).trim().toLowerCase(), d.slice(i + 1).trim()] as [string, string];
  });
  const changed = Object.keys(now).filter((k) => now[k] !== before[k]);
  const removed = new Set(Object.keys(before).filter((k) => !(k in now)));
  // A changed shorthand (margin) replaces its longhands (margin-top …) and the reverse.
  const family = (k: string) => k.split("-")[0];
  const touched = new Set([...changed, ...removed].map(family));
  const kept = decls.filter(([k]) => !removed.has(k) && !changed.includes(k) && !(touched.has(family(k)) && !(k in now)));
  return [...kept.map(([k, v]) => `${k}: ${v}`), ...changed.map((k) => `${k}: ${now[k]}`)].join("; ");
}

/** GrapesJS content → stored template. */
export function exportDesign(editor: Editor, load: DesignLoad, snap: Map<string, Obj>): string {
  const wrapper = editor.getWrapper();
  if (!wrapper) return load.styles;
  const attributes = (cmp: Component, attrs: Record<string, any>) => {
    const out = { ...attrs };
    const n = out["data-tk-s"]; delete out["data-tk-s"];
    const now = styleOf(cmp);
    const before = n != null ? snap.get(String(n)) : undefined;
    let st: string;
    if (n != null && before && JSON.stringify(before) === JSON.stringify(now)) st = load.originals[Number(n)];
    else if (n != null && before) st = mergeStyle(load.originals[Number(n)], before, now);
    else st = Object.entries(now).map(([k, v]) => `${k}: ${v}`).join("; ");
    if (st) out.style = st; else delete out.style;
    if (out.id && AUTO_ID.test(out.id) && !load.keepIds.has(out.id)) delete out.id;
    return out;
  };
  const html = wrapper.components().map((c: Component) => c.toHTML({ attributes })).join("");
  return load.styles + chipsToTokens(html);
}

export function idsIn(html: string): Set<string> {
  return new Set(Array.from((html || "").matchAll(/\sid\s*=\s*["']?([^"'\s>]+)/gi)).map((m) => m[1]));
}

/** The settings both the visible editor and the headless round-trip check use. */
export function baseConfig(): EditorConfig {
  return {
    telemetry: false,              // GrapesJS reports usage to its website unless told not to
    storageManager: false,         // the template lives in Tekly, not in the browser
    undoManager: { trackSelection: false },
    panels: { defaults: [] },      // Tekly draws its own panels; nothing from the library shows
    selectorManager: { componentFirst: true },
    parser: { optionsHtml: { keepEmptyTextNodes: true, allowScripts: false, allowUnsafeAttr: false, allowUnsafeAttrValue: false } },
    avoidInlineStyle: true,
  } as EditorConfig;
}

export function addTeklyTypes(editor: Editor) { addVariableType(editor); }

/** What Design would save for this template if opened and changed nowhere. */
export function designRoundTrip(html: string): string {
  const container = document.createElement("div");
  const editor = grapesjs.init({ ...baseConfig(), headless: true, container });
  try {
    addTeklyTypes(editor);
    const load = toDesignView(html);
    editor.setComponents(load.body);
    return exportDesign(editor, load, snapshotStyles(editor));
  } finally {
    editor.destroy();
  }
}
