// Pure helpers shared by the three template editor views (Design, Text, HTML).
//
// The stored template is plain HTML with Tekly's own placeholders as text:
//   {Client Name}                      a variable (always starts with a capital letter)
//   {#if Var} … {#else} … {/if}        a condition (also {#unless Var} … {/unless})
//   {{component: Footer}}              a reusable block
//   a <tbody> holding {Product …} / {Row Number} repeats once per item (the row loop)
// See backend/routers/documents.py (_render, _conditionals, _COMPONENT_RE).
//
// The visual views must never rewrite that markup behind the owner's back, so each view is only
// offered when a round trip through it gives back the same document (see sameTemplate), and
// placeholders are shown as chips that turn back into the exact same text.

/** Every placeholder, in the order it appears: variables, conditions and component blocks. */
export const PLACEHOLDER_RE = /\{\{\s*component\s*:[^{}]*\}\}|\{[#/](?:if|unless|else)\b[^{}]*\}|\{[A-Z][^{}]*\}/g;
const MARKER_RE = /\{\{\s*component\s*:[^{}]*\}\}|\{[#/](?:if|unless|else)\b[^{}]*\}/;

/** Placeholders in the template's text, in document order. */
export function placeholders(html: string): string[] {
  return (html || "").match(PLACEHOLDER_RE) || [];
}

/** What a chip shows for a placeholder: "Client Name", "if Paid", "end if", "component: Footer". */
export function chipLabel(tok: string): string {
  if (/^\{\{/.test(tok)) return tok.replace(/^\{\{\s*|\s*\}\}$/g, "");
  if (/^\{\/(if|unless)/.test(tok)) return "end " + tok.slice(2, -1).trim();
  if (/^\{#/.test(tok)) return tok.slice(2, -1).trim();
  return tok.slice(1, -1);
}
export function isLogicToken(tok: string): boolean { return MARKER_RE.test(tok); }

// ── leading <style> blocks ─────────────────────────────────────────────────────────────────────
// Templates often start with a <style> block (the seeded Estimate does). Neither visual library
// keeps CSS text as written, so the views hold the leading block aside, untouched, and put it back.
export function splitLeadingStyles(html: string): { styles: string; body: string } {
  const m = /^(\s*(?:<style\b[^>]*>[\s\S]*?<\/style>\s*)+)/i.exec(html || "");
  if (!m) return { styles: "", body: html || "" };
  return { styles: m[1], body: html.slice(m[1].length) };
}
/** The CSS text of the leading style blocks (to show them in a canvas). */
export function styleText(styles: string): string {
  return Array.from(styles.matchAll(/<style\b[^>]*>([\s\S]*?)<\/style>/gi)).map((m) => m[1]).join("\n");
}

// ── chips ──────────────────────────────────────────────────────────────────────────────────────
const SKIP_TEXT_IN = new Set(["STYLE", "SCRIPT", "TEXTAREA", "TITLE"]);
export const BLOCK_TAGS = new Set(["ADDRESS", "ARTICLE", "ASIDE", "BLOCKQUOTE", "CENTER", "DIV", "DL", "FIGURE", "FOOTER", "H1", "H2", "H3", "H4", "H5", "H6",
  "HEADER", "HR", "MAIN", "NAV", "OL", "P", "PRE", "SECTION", "TABLE", "UL"]);
export const hasBlockChild = (el: Element) => Array.from(el.children).some((c) => BLOCK_TAGS.has(c.tagName));

/** Turn placeholders in text (never in attributes) into chip elements the views understand.
 *  Placeholders standing on their own between blocks, like {#if Paid}<p>…</p>{/if} or a
 *  {Items Table} between paragraphs, become one block chip (block = true) holding that exact
 *  text, so a view never wraps them in a paragraph of their own. */
export function tokensToChips(html: string, chip: (tok: string, block: boolean) => string): string {
  if ((html || "").search(PLACEHOLDER_RE) < 0) return html || "";
  const doc = new DOMParser().parseFromString(`<!doctype html><body>${html}</body>`, "text/html");
  const walker = doc.createTreeWalker(doc.body, NodeFilter.SHOW_TEXT);
  const texts: Text[] = [];
  for (let n = walker.nextNode(); n; n = walker.nextNode()) texts.push(n as Text);
  for (const t of texts) {
    const parent = t.parentElement || doc.body;
    if (SKIP_TEXT_IN.has(parent.tagName)) continue;
    const s = t.data;
    if (s.search(PLACEHOLDER_RE) < 0) continue;
    const tmp = doc.createElement("template");
    const core = s.trim();
    if (hasBlockChild(parent) && core.replace(PLACEHOLDER_RE, "").trim() === "") {
      const lead = s.slice(0, s.indexOf(core)), tail = s.slice(s.indexOf(core) + core.length);
      tmp.innerHTML = escapeText(lead) + chip(core, true) + escapeText(tail);
    } else {
      tmp.innerHTML = escapeText(s).replace(PLACEHOLDER_RE, (m) => chip(unescapeText(m), false));
    }
    t.replaceWith(tmp.content);
  }
  return doc.body.innerHTML;
}

/** Turn chip elements (anything carrying data-tok) back into their placeholder text. */
export function chipsToTokens(html: string): string {
  if (!/data-tok=/.test(html || "")) return html || "";
  const doc = new DOMParser().parseFromString(`<!doctype html><body>${html}</body>`, "text/html");
  doc.body.querySelectorAll("[data-tok]").forEach((el) => el.replaceWith(doc.createTextNode(el.getAttribute("data-tok") || "")));
  return doc.body.innerHTML;
}

function escapeText(s: string) { return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/ /g, "&nbsp;"); }
function unescapeText(s: string) { return s.replace(/&nbsp;/g, " ").replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&amp;/g, "&"); }
export function escapeAttr(s: string) { return s.replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;").replace(/>/g, "&gt;"); }

// ── equivalence ────────────────────────────────────────────────────────────────────────────────
const VOID = new Set(["area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"]);
const ALIAS: Record<string, string> = { b: "strong", i: "em", strike: "s", del: "s" };
const BLOCK = "address|article|aside|blockquote|body|caption|center|col|colgroup|dd|div|dl|dt|figcaption|figure|footer|h[1-6]|header|hr|li|main|nav|ol|p|pre|section|table|tbody|td|tfoot|th|thead|tr|ul|style";
const AROUND_BLOCK = new RegExp(`\\s*(</?(?:${BLOCK})(?:\\s[^>]*)?>)\\s*`, "g");

function normStyle(v: string): string {
  return v.split(";").map((d) => d.trim()).filter(Boolean).map((d) => {
    const i = d.indexOf(":");
    if (i < 0) return d.toLowerCase();
    return d.slice(0, i).trim().toLowerCase() + ":" + d.slice(i + 1).trim().replace(/\s+/g, " ").replace(/\s*,\s*/g, ",").toLowerCase().replace(/(^|[\s(,])0px\b/g, (_m, p) => p + "0");
  }).sort().join(";");
}

/** A canonical form of a template: same canonical form ⇔ prints the same. Ignores attribute
 *  order, style declaration order and spacing, b/strong-style aliases and whitespace that HTML
 *  itself ignores; keeps every tag, attribute value, placeholder and character of text. */
export function canonical(html: string): string {
  const doc = new DOMParser().parseFromString(`<!doctype html><body>${html || ""}</body>`, "text/html");
  const out: string[] = [];
  const walk = (n: Node) => {
    if (n.nodeType === 3) { out.push(escapeText((n as Text).data).replace(/[ \t\r\n\f]+/g, " ")); return; }
    if (n.nodeType === 8) { out.push(`<!--${(n as Comment).data}-->`); return; }
    if (n.nodeType !== 1) return;
    const el = n as Element;
    const raw = el.tagName.toLowerCase();
    const tag = ALIAS[raw] || raw;
    const attrs = Array.from(el.attributes).map((a) => {
      let v = a.value;
      if (a.name === "style") v = normStyle(v);
      else if (a.name === "class") v = v.split(/\s+/).filter(Boolean).sort().join(" ");
      return [a.name.toLowerCase(), v] as const;
    }).filter(([k, v]) => !(k === "style" || k === "class") || v !== "").sort((a, b) => (a[0] < b[0] ? -1 : 1));
    out.push(`<${tag}${attrs.map(([k, v]) => ` ${k}="${escapeAttr(v)}"`).join("")}>`);
    if (raw === "style") { out.push((el.textContent || "").replace(/\s+/g, " ").trim()); out.push("</style>"); return; }
    if (VOID.has(raw)) return;
    el.childNodes.forEach(walk);
    out.push(`</${tag}>`);
  };
  doc.body.childNodes.forEach(walk);
  return out.join("").replace(AROUND_BLOCK, "$1").trim();
}

export function sameTemplate(a: string, b: string): boolean { return canonical(a) === canonical(b); }

/** Where two templates first differ (for a short, honest reason). */
export function firstDifference(a: string, b: string): string | null {
  const x = canonical(a), y = canonical(b);
  if (x === y) return null;
  let i = 0; while (i < x.length && x[i] === y[i]) i++;
  const tag = /<([a-z0-9]+)[^<]*$/.exec(x.slice(0, i + 1));
  return tag ? tag[1] : "text";
}

// ── what a view can't hold ─────────────────────────────────────────────────────────────────────
export type ModeId = "design" | "text" | "html";
const TABLE_PARTS = new Set(["table", "thead", "tbody", "tfoot", "tr"]);

/** Text sitting directly between table rows, e.g. {#if Tax}<tr>…</tr>{/if}. Any HTML parser
 *  (so both visual views) moves that text out of the table, breaking the condition or loop. */
export function textBetweenRows(html: string): string | null {
  const stack: string[] = [];
  for (const part of (html || "").split(/(<[^>]*>)/)) {
    if (!part) continue;
    if (part.startsWith("<")) {
      const m = /^<\s*(\/)?\s*([a-zA-Z0-9]+)/.exec(part);
      if (!m) continue;
      const name = m[2].toLowerCase();
      if (m[1]) { const at = stack.lastIndexOf(name); if (at >= 0) stack.length = at; }
      else if (!VOID.has(name) && !part.endsWith("/>")) stack.push(name);
      continue;
    }
    const top = stack[stack.length - 1];
    if (top && TABLE_PARTS.has(top) && part.replace(/&nbsp;/g, "").trim()) return part.trim();
  }
  return null;
}

/** A <style> block anywhere except the very start. */
function midStyle(html: string): boolean {
  const { body } = splitLeadingStyles(html);
  return /<style\b/i.test(body);
}

/** Reasons a view must stay greyed out for this template (null = fine), from what can be seen
 *  without loading the editors. The round-trip check (modeSupport) adds anything this misses. */
export function staticReasons(html: string): Record<ModeId, string | null> {
  const between = textBetweenRows(html);
  const doc = new DOMParser().parseFromString(`<!doctype html><body>${html || ""}</body>`, "text/html");
  const both = between
    ? `It has ${shortTok(between)} between table rows, which this view would move. Edit it in HTML.`
    : midStyle(html) ? "It has a style block inside the page. Edit it in HTML." : null;
  let text = both;
  if (!text && doc.body.querySelector("colgroup, caption")) text = "Its tables have column groups or captions that the text view can't keep. Use Design or HTML.";
  if (!text && doc.body.querySelector("table table")) text = "It has a table inside a table, which the text view can't keep. Use Design or HTML.";
  return { design: both, text, html: null };
}
function shortTok(s: string): string {
  const t = s.match(PLACEHOLDER_RE)?.[0] || s;
  return t.length > 28 ? t.slice(0, 27) + "…" : t;
}

export const LOST_IN: Record<"design" | "text", string> = {
  design: "Some of its layout would change in Design. Edit it in Text or HTML.",
  text: "Some of its formatting would be lost in the text view. Use Design or HTML.",
};

/** The template's own CSS limited to one element on the page (for the Text view, which isn't in
 *  a frame): every selector is prefixed with `scope`, and html/body become the scope itself.
 *  @-rules (@page, @media) are left out; they only matter when printing. */
export function scopeCss(css: string, scope: string): string {
  const out: string[] = [];
  const clean = css.replace(/\/\*[\s\S]*?\*\//g, "");
  let depth = 0, start = 0, sel = "", skip = false;
  for (let i = 0; i < clean.length; i++) {
    const ch = clean[i];
    if (ch === "{") {
      if (depth === 0) { sel = clean.slice(start, i).trim(); skip = sel.startsWith("@"); start = i + 1; }
      depth++;
    } else if (ch === "}") {
      depth--;
      if (depth === 0) {
        if (!skip && sel) {
          const s = sel.split(",").map((p) => p.trim()).filter(Boolean)
            .map((p) => (/^(html|body)\b/.test(p) ? p.replace(/^(html|body)\b/, scope) : `${scope} ${p}`)).join(", ");
          out.push(`${s}{${clean.slice(start, i).replace(/<\/?style/gi, "")}}`);
        }
        start = i + 1;
      }
    }
  }
  return out.join("\n");
}

// ── cleaning ───────────────────────────────────────────────────────────────────────────────────
// Conditions and components ride through the cleaner as comments, which an HTML parser leaves in
// place even between table rows (the server cleaner does the same: services/html_clean.py).
const PROTECT = /\{\{[^{}<>]*\}\}|\{[#/:][^{}<>]*\}/g;
function protect(html: string): { html: string; saved: string[] } {
  const saved: string[] = [];
  const out = (html || "").split(/(<[^>]*>)/).map((p) => (p.startsWith("<") ? p : p.replace(PROTECT, (m) => `<!--tktpl${saved.push(m) - 1}-->`))).join("");
  return { html: out, saved };
}

/** The template as it is saved, printed and shown in the views: run through sanitizeHtml (no
 *  script, event handlers or script URLs). When cleaning removes nothing, the owner's own text is
 *  kept exactly as written, so an untouched template saves byte for byte the same. */
export function cleanTemplate(html: string, sanitize: (h: string) => string): string {
  const p = protect(html || "");
  const cleaned = sanitize(p.html).replace(/<!--tktpl(\d+)-->/g, (_m, n) => p.saved[Number(n)] ?? "");
  return sameTemplate(cleaned, html || "") ? (html || "") : cleaned;
}
