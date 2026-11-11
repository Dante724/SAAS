// Simple edits on a stored template, for Settings › Documents ("Brand & details"): the page is a
// list of sections (the top-level blocks), each of which can be hidden, moved or resized, and
// plain text can be typed over. Every edit works on the stored HTML and gives stored HTML back:
// placeholders stay as they are, conditions between table rows are protected while the page is
// parsed, and nothing is rewritten unless the owner changes something.
import { splitLeadingStyles } from "./templateHtml";

const PROTECT = /\{\{[^{}<>]*\}\}|\{[#/:][^{}<>]*\}/g;
const BLOCKISH = new Set(["DIV", "SECTION", "MAIN", "ARTICLE"]);
const BLOCK_CHILD = new Set(["P", "DIV", "SECTION", "TABLE", "H1", "H2", "H3", "H4", "UL", "OL", "HR", "HEADER", "FOOTER"]);

export type Parsed = { styles: string; body: HTMLElement; saved: string[] };

/** Parse a template for editing. Conditions/components ride through as comments, which an HTML
 *  parser leaves where they are even between table rows. */
export function parse(html: string): Parsed {
  const { styles, body } = splitLeadingStyles(html || "");
  const saved: string[] = [];
  const prot = body.split(/(<[^>]*>)/).map((p) => (p.startsWith("<") ? p : p.replace(PROTECT, (m) => `<!--tkp${saved.push(m) - 1}-->`))).join("");
  const doc = new DOMParser().parseFromString(`<!doctype html><body>${prot}</body>`, "text/html");
  return { styles, body: doc.body, saved };
}
export function serialize(p: Parsed): string {
  return p.styles + p.body.innerHTML.replace(/<!--tkp(\d+)-->/g, (_m, n) => p.saved[Number(n)] ?? "");
}

/** Where the page's sections live: the body, or a single wrapper the whole page sits in (the
 *  receipt's padded <div>). */
export function contentRoot(body: Element): Element {
  let root = body;
  for (let i = 0; i < 2; i++) {
    const kids = Array.from(root.children).filter((c) => c.tagName !== "STYLE");
    const only = kids.length === 1 ? kids[0] : null;
    if (only && BLOCKISH.has(only.tagName) && Array.from(only.children).filter((c) => BLOCK_CHILD.has(c.tagName)).length > 1) root = only;
    else break;
  }
  return root;
}
export function sections(p: Parsed): Element[] {
  return Array.from(contentRoot(p.body).children).filter((c) => c.tagName !== "STYLE");
}

function edit(html: string, fn: (p: Parsed) => boolean | void): string {
  const p = parse(html);
  return fn(p) === false ? html : serialize(p);
}

// ── style helpers ──
/** Declarations of a style attribute, split on ";" only outside parentheses and quotes, so a
 *  url(data:image/png;base64,…) stays whole. */
function decls(style: string): [string, string][] {
  const out: string[] = []; let depth = 0, quote = "", cur = "";
  for (const ch of style) {
    if (quote) { if (ch === quote) quote = ""; cur += ch; continue; }
    if (ch === '"' || ch === "'") { quote = ch; cur += ch; continue; }
    if (ch === "(") depth++; else if (ch === ")") depth = Math.max(0, depth - 1);
    if (ch === ";" && depth === 0) { out.push(cur); cur = ""; } else cur += ch;
  }
  out.push(cur);
  return out.map((d) => d.trim()).filter(Boolean).map((d) => { const i = d.indexOf(":"); return [d.slice(0, i).trim().toLowerCase(), d.slice(i + 1).trim()] as [string, string]; }).filter(([k]) => k);
}
function getDecl(el: Element, prop: string): string | null {
  return decls(el.getAttribute("style") || "").find(([k]) => k === prop)?.[1] ?? null;
}
function setDecl(el: Element, prop: string, value: string | null) {
  const rest = decls(el.getAttribute("style") || "").filter(([k]) => k !== prop).map(([k, v]) => `${k}: ${v}`);
  if (value != null) rest.push(`${prop}: ${value}`);
  if (rest.length) el.setAttribute("style", rest.join("; ")); else el.removeAttribute("style");
}

// ── section edits (i = index in sections()) ──
export function isHidden(el: Element): boolean { return el.getAttribute("data-tk-hidden") === "1"; }
/** Hidden sections stay in the template (display:none), so Show brings them back exactly. */
export function setHidden(html: string, i: number, hidden: boolean): string {
  return edit(html, (p) => {
    const el = sections(p)[i]; if (!el || isHidden(el) === hidden) return false;
    if (hidden) { el.setAttribute("data-tk-hidden", "1"); setDecl(el, "display", "none"); }
    else { el.removeAttribute("data-tk-hidden"); setDecl(el, "display", null); }
  });
}
// ── moving: a condition and everything under it move as one ──
// At the page's top level a condition is a pair of markers ({#if X} … {/if}) around sections.
// Moving a section into or out of one would change when it prints, so the page is split into
// units: a section on its own, or a whole condition with its markers and sections. Moves swap
// whole units. A condition that doesn't close at the same level makes everything after it fixed.
const OPEN = /^\{#(if|unless)\b/, CLOSE = /^\{\/(if|unless)\b/;
type Unit = { nodes: ChildNode[]; secs: Element[] };
function markerOf(p: Parsed, n: ChildNode): string | null {
  if (n.nodeType !== 8) return null;
  const m = /^tkp(\d+)$/.exec((n as Comment).data);
  return m ? p.saved[Number(m[1])] ?? null : null;
}
/** The units, and the index of the first section that can't move (after an unclosed condition). */
function units(p: Parsed): { list: Unit[]; lockedFrom: number } {
  const root = contentRoot(p.body);
  const list: Unit[] = []; let group: Unit | null = null; let depth = 0; let count = 0; let lockedFrom = Infinity;
  for (const n of Array.from(root.childNodes)) {
    const tok = markerOf(p, n);
    const isSec = n.nodeType === 1 && (n as Element).tagName !== "STYLE";
    if (tok && OPEN.test(tok)) { if (!group) group = { nodes: [], secs: [] }; depth++; group.nodes.push(n); continue; }
    if (tok && CLOSE.test(tok)) {
      if (group) { group.nodes.push(n); depth--; if (depth === 0) { list.push(group); group = null; } }
      else lockedFrom = Math.min(lockedFrom, count);       // a close with no open here: stop moving after it
      continue;
    }
    if (group) { group.nodes.push(n); if (isSec) { group.secs.push(n as Element); count++; } continue; }
    if (isSec) { list.push({ nodes: [n], secs: [n as Element] }); count++; }
  }
  if (group) lockedFrom = Math.min(lockedFrom, count - group.secs.length);   // never closed at this level
  return { list: list.filter((u) => u.secs.length), lockedFrom };
}
/** Why a section can't move here (null = it can). */
export function moveBlocked(html: string, i: number): string | null {
  const { lockedFrom } = units(parse(html));
  return i >= lockedFrom ? "This part sits inside a condition that isn't closed here: move it in Customise layout." : null;
}
/** Move a section (with its whole condition, if it is under one) past the next unit. Returns the
 *  new template and the section's new index. */
export function moveSectionAt(html: string, i: number, dir: -1 | 1): { html: string; i: number } {
  const p = parse(html);
  const { list, lockedFrom } = units(p);
  const target = sections(p)[i];
  const at = list.findIndex((u) => target && u.secs.includes(target));
  const other = list[at + dir];
  if (at < 0 || !other || i >= lockedFrom || sections(p).indexOf(other.secs[0]) >= lockedFrom) return { html, i };
  const me = list[at];
  if (dir < 0) { const first = other.nodes[0]; me.nodes.forEach((n) => first.before(n)); }
  else { let last: ChildNode = other.nodes[other.nodes.length - 1]; me.nodes.forEach((n) => { last.after(n); last = n; }); }
  return { html: serialize(p), i: sections(p).indexOf(target) };
}
export function moveSection(html: string, i: number, dir: -1 | 1): string { return moveSectionAt(html, i, dir).html; }

// ── the one text the preview numbers and the edits apply to ──
export type EditOp =
  | { type: "hide"; i: number; hidden: boolean }
  | { type: "move"; i: number; dir: -1 | 1 }
  | { type: "size"; i: number; step: -1 | 1 }
  | { type: "text"; e: number; text: string };
let cleaner: ((h: string) => string) | null = null;
/** Set once by the app: the cleaner every save goes through (sanitizeHtml via cleanTemplate). */
export function setEditCleaner(fn: (h: string) => string) { cleaner = fn; }
/** The template as the preview numbers it and as edits apply to it: cleaned the way it is saved,
 *  so a tag the cleaner drops (link, meta, form…) can't shift the numbers. */
export function editBase(html: string): string { return cleaner ? cleaner(html) : html; }
/** Apply a click on the preview. `source` is the text the preview was numbered from; when the page
 *  changed since (another edit, a switch, another document), the click is ignored (null). */
export function applyEdit(html: string, op: EditOp, source: string): string | null {
  const base = editBase(html);
  if (base !== source) return null;
  switch (op.type) {
    case "hide": return setHidden(base, op.i, op.hidden);
    case "move": return moveSection(base, op.i, op.dir);
    case "size": return resizeSection(base, op.i, op.step);
    case "text": return setText(base, op.e, op.text);
  }
}

const BASE_PX = 13;
function toPx(v: string | null): number | null {
  if (!v) return null;
  const m = /^([\d.]+)\s*(px|pt|em|rem|%)?$/i.exec(v.trim());
  if (!m) return null;
  const n = parseFloat(m[1]);
  switch ((m[2] || "px").toLowerCase()) { case "pt": return n * 4 / 3; case "em": case "rem": return n * BASE_PX; case "%": return n / 100 * BASE_PX; default: return n; }
}
/** Bigger / Smaller: two pixels a step, between 8 and 40. */
export function resizeSection(html: string, i: number, step: -1 | 1): string {
  return edit(html, (p) => {
    const el = sections(p)[i]; if (!el) return false;
    const now = toPx(getDecl(el, "font-size")) ?? BASE_PX;
    const next = Math.max(8, Math.min(40, Math.round(now + 2 * step)));
    if (next === now) return false;
    setDecl(el, "font-size", `${next}px`);
  });
}

// ── typing over plain text (e = index among all elements under the content root) ──
// Plain text only: an element with no child elements and no placeholders or conditions in it.
// (Conditions ride as comments while parsed, so "only text nodes" also keeps {#if} out.)
const editable = (el: Element) => Array.from(el.childNodes).every((n) => n.nodeType === 3) && !/^(STYLE|SCRIPT)$/.test(el.tagName)
  && !!(el.textContent || "").trim() && !/[{}]/.test(el.textContent || "");
function allElements(p: Parsed): Element[] { return Array.from(contentRoot(p.body).querySelectorAll("*")); }
export function setText(html: string, e: number, text: string): string {
  return edit(html, (p) => {
    const el = allElements(p)[e];
    if (!el || !editable(el) || el.textContent === text) return false;
    el.textContent = text;
  });
}

/** The template marked up for the preview only (never saved): every section numbered
 *  (data-tk-i), and every piece of plain text that can be typed over numbered (data-tk-e). */
export function markForPreview(html: string): string {
  const p = parse(html);
  sections(p).forEach((el, i) => el.setAttribute("data-tk-i", String(i)));
  allElements(p).forEach((el, e) => { if (editable(el)) el.setAttribute("data-tk-e", String(e)); });
  return serialize(p);
}

// ── added sections ──
export function hasSection(html: string, key: string): boolean { return parse(html).body.querySelector(`[data-tk-section="${cssEsc(key)}"]`) !== null; }
const FOOTER = '[data-tk-section="signature"],[data-tk-section="warranty"],[data-block="signature"],[data-block="terms"]';
/** Add a section (an element carrying data-tk-section) above the footer sections, or at the end. */
export function addSection(html: string, snippet: string): string {
  return edit(html, (p) => {
    const tmp = p.body.ownerDocument.createElement("template");
    tmp.innerHTML = snippet;
    const node = tmp.content.firstElementChild; if (!node) return false;
    const root = contentRoot(p.body);
    const key = node.getAttribute("data-tk-section");
    const footer = key === "signature" || key === "warranty" ? null : Array.from(root.children).find((c) => c.matches(FOOTER));
    if (footer) footer.before(node); else root.appendChild(node);
  });
}
export function removeSection(html: string, key: string): string {
  return edit(html, (p) => {
    const found = p.body.querySelectorAll(`[data-tk-section="${cssEsc(key)}"]`);
    if (!found.length) return false;
    found.forEach((el) => el.remove());
  });
}
/** The shop's own sections (not added by us) that are only about one thing: every field in them
 *  matches `family`. Those can be hidden or shown as a whole without taking anything else away. */
function ownSections(p: Parsed, family: RegExp): Element[] {
  return sections(p).filter((el) => {
    if (el.hasAttribute("data-tk-section")) return false;
    const toks = fieldsIn(el);
    return toks.length > 0 && toks.every((t) => family.test(t));
  });
}
function fieldsIn(el: Element): string[] { return (el.textContent || "").match(/\{[A-Z][^{}]*\}/g) || []; }
export function setHiddenWhere(html: string, family: RegExp, hidden: boolean): string {
  return edit(html, (p) => {
    let changed = false;
    ownSections(p, family).forEach((el) => {
      if (isHidden(el) === hidden) return;
      changed = true;
      if (hidden) { el.setAttribute("data-tk-hidden", "1"); setDecl(el, "display", "none"); }
      else { el.removeAttribute("data-tk-hidden"); setDecl(el, "display", null); }
    });
    return changed;
  });
}
/** A field shown by the shop's own layout inside a bigger section (which a switch can't take away). */
export function fieldInMixedSection(html: string, tokens: string[], family: RegExp): boolean {
  const p = parse(html);
  const own = new Set(ownSections(p, family));
  return sections(p).some((el) => !own.has(el) && !el.hasAttribute("data-tk-section") && !isHidden(el) && tokens.some((t) => (el.textContent || "").includes(t)));
}
/** Is a field visibly on the page (in a section that isn't hidden)? */
export function showsField(html: string, tokens: string[]): boolean {
  const p = parse(html);
  return sections(p).some((el) => !isHidden(el) && tokens.some((t) => (el.textContent || "").includes(t)));
}
function cssEsc(s: string) { return s.replace(/["\\]/g, "\\$&"); }
