// Tiptap (MIT, open-source packages only) set up for printable templates. Loaded on demand.
//
// Out of the box Tiptap rewrites markup: it drops inline styles and classes, wraps table cells
// in <p>, adds <colgroup> and link targets. Templates are hand-tuned print layouts, so this
// schema keeps the attributes a template uses, has container nodes for <div>/<section>, and
// table cells that hold plain text without a wrapping <p>. Anything it still can't keep is
// caught by the round-trip check, and the Text view stays greyed out for that template.
import { Extension, Node, getHTMLFromFragment, getSchema, mergeAttributes, type Extensions } from "@tiptap/core";
import { DOMParser as PMDOMParser } from "@tiptap/pm/model";
import StarterKit from "@tiptap/starter-kit";
import { Table } from "@tiptap/extension-table";
import Image from "@tiptap/extension-image";
import { TextStyle } from "@tiptap/extension-text-style";
import { Placeholder } from "@tiptap/extensions";
import Bold from "@tiptap/extension-bold";
import Italic from "@tiptap/extension-italic";
import Underline from "@tiptap/extension-underline";
import Strike from "@tiptap/extension-strike";
import { chipLabel, chipsToTokens, escapeAttr, hasBlockChild, isLogicToken, splitLeadingStyles, tokensToChips } from "./templateHtml";

// Attributes kept as written on every node and mark that can carry them. ProseMirror writes
// style through the browser's CSS parser (#666 comes back as rgb(102, 102, 102)), so the exact
// text also rides along in data-tk-style and fromTextView puts it back.
const KEEP = ["style", "class", "id", "align", "valign", "width", "height", "border", "cellpadding", "cellspacing", "bgcolor", "dir", "lang", "data-block", "data-label"];
const keepAttrs = (skip: string[] = []) => Object.fromEntries(KEEP.filter((k) => !skip.includes(k)).map((k) => [k, {
  default: null,
  parseHTML: (el: HTMLElement) => el.getAttribute(k),
  renderHTML: (a: Record<string, any>) => (a[k] == null ? {} : k === "style" ? { style: a[k], "data-tk-style": a[k] } : { [k]: a[k] }),
}]));

const KeepAttributes = Extension.create({
  name: "teklyKeepAttributes",
  addGlobalAttributes() {
    return [
      { types: ["paragraph", "heading", "blockquote", "bulletList", "orderedList", "listItem", "horizontalRule", "table", "textStyle", "link", "bold", "italic", "underline", "strike"], attributes: keepAttrs() },
      { types: ["image"], attributes: keepAttrs(["width", "height"]) },
    ];
  },
});

/** <div>/<section>/… holding blocks. */
const Box = Node.create({
  name: "box",
  group: "block",
  content: "block+",
  defining: true,
  addAttributes() { return { tag: { default: "div", parseHTML: (el) => el.tagName.toLowerCase(), renderHTML: () => ({}) }, ...keepAttrs() }; },
  parseHTML() { return ["div", "section", "header", "footer", "article", "aside", "main", "nav", "center", "address"].map((tag) => ({ tag, getAttrs: (el: HTMLElement) => (hasBlockChild(el) ? null : false) })); },
  renderHTML({ node, HTMLAttributes }) { return [node.attrs.tag, HTMLAttributes, 0]; },
});
/** <div>/<section>/… holding only text and placeholders. */
const TextBox = Node.create({
  name: "textBox",
  group: "block",
  content: "inline*",
  defining: true,
  addAttributes() { return { tag: { default: "div", parseHTML: (el) => el.tagName.toLowerCase(), renderHTML: () => ({}) }, ...keepAttrs() }; },
  parseHTML() { return ["div", "section", "header", "footer", "article", "aside", "main", "nav", "center", "address"].map((tag) => ({ tag, getAttrs: (el: HTMLElement) => (hasBlockChild(el) ? false : null) })); },
  renderHTML({ node, HTMLAttributes }) { return [node.attrs.tag, HTMLAttributes, 0]; },
});

// prosemirror-tables needs colspan/rowspan/colwidth on every cell; only real spans are written.
const span = (name: string) => ({ default: 1, parseHTML: (el: HTMLElement) => Number(el.getAttribute(name) || 1), renderHTML: (a: any) => (a[name] && a[name] !== 1 ? { [name]: a[name] } : {}) });
const cellAttrs = () => ({ colspan: span("colspan"), rowspan: span("rowspan"), colwidth: { default: null, parseHTML: () => null, renderHTML: () => ({}) }, ...keepAttrs(), nowrap: { default: null, parseHTML: (el: HTMLElement) => el.getAttribute("nowrap"), renderHTML: (a: any) => (a.nowrap == null ? {} : { nowrap: a.nowrap }) } });
const cell = (name: string, tag: "td" | "th", role: "cell" | "header_cell", blocks: boolean) => Node.create({
  name,
  content: blocks ? "block+" : "inline*",
  tableRole: role,
  isolating: true,
  addAttributes: cellAttrs,
  parseHTML() { return [{ tag, getAttrs: (el: HTMLElement) => (hasBlockChild(el) === blocks ? null : false) }]; },
  renderHTML({ HTMLAttributes }) { return [tag, HTMLAttributes, 0]; },
});
// The plain-text cells come last, so new tables get them (no <p> inside cells).
const CELLS = [cell("tableCellBlocks", "td", "cell", true), cell("tableHeaderBlocks", "th", "header_cell", true),
  cell("tableHeader", "th", "header_cell", false), cell("tableCell", "td", "cell", false)];
// Table editing works on a flat list of rows, so each row remembers which <thead>/<tbody>/<tfoot>
// it came from (data-tk-sec, e.g. "tbody:1") and regroupSections rebuilds them on the way out.
// That keeps the item-row loop (a <tbody> with {Product …}) apart from the header and totals.
function sectionOf(el: HTMLElement): string | null {
  const p = el.parentElement;
  if (!p || !/^(THEAD|TBODY|TFOOT)$/.test(p.tagName)) return null;
  const parts = p.parentElement ? Array.from(p.parentElement.children).filter((c) => /^(THEAD|TBODY|TFOOT)$/.test(c.tagName)) : [p];
  return `${p.tagName.toLowerCase()}:${parts.indexOf(p)}`;
}
const Row = Node.create({
  name: "tableRow",
  content: "(tableCell | tableHeader | tableCellBlocks | tableHeaderBlocks)*",
  tableRole: "row",
  addAttributes() { return { section: { default: null, parseHTML: sectionOf, renderHTML: (a: any) => (a.section ? { "data-tk-sec": a.section } : {}) }, ...keepAttrs() }; },
  parseHTML() { return [{ tag: "tr" }]; },
  renderHTML({ HTMLAttributes }) { return ["tr", HTMLAttributes, 0]; },
});
function regroupSections(root: HTMLElement) {
  root.querySelectorAll("table").forEach((table) => {
    const body = Array.from(table.children).find((c) => c.tagName === "TBODY");
    if (!body) return;
    const rows = Array.from(body.children).filter((r) => r.tagName === "TR") as HTMLElement[];
    if (!rows.some((r) => r.hasAttribute("data-tk-sec"))) return;
    const doc = table.ownerDocument;
    const groups: { sec: string; el: HTMLElement }[] = [];
    for (const r of rows) {
      const sec = r.getAttribute("data-tk-sec") || groups[groups.length - 1]?.sec || "tbody:0";
      r.removeAttribute("data-tk-sec");
      if (!groups.length || groups[groups.length - 1].sec !== sec) groups.push({ sec, el: doc.createElement(sec.split(":")[0]) });
      groups[groups.length - 1].el.appendChild(r);
    }
    body.replaceWith(...groups.map((g) => g.el));
  });
}
const PrintTable = Table.extend({
  renderHTML({ HTMLAttributes }) { return ["table", mergeAttributes(HTMLAttributes), ["tbody", 0]]; },
}).configure({ resizable: false, renderWrapper: false, HTMLAttributes: {}, View: null });

// Bold/italic/underline/strike from their tags only. The stock marks also read inline styles
// (font-weight: bold), which would add a <strong> the template never had.
const tagsOnly = (tags: string[]) => () => tags.map((tag) => ({ tag }));
const MARKS = [Bold.extend({ parseHTML: tagsOnly(["strong", "b"]) }), Italic.extend({ parseHTML: tagsOnly(["em", "i"]) }),
  Underline.extend({ parseHTML: tagsOnly(["u"]) }), Strike.extend({ parseHTML: tagsOnly(["s", "strike", "del"]) })];

/** Placeholders standing between blocks ({#if Paid} before a paragraph, {Items Table}). */
export const VariableBlock = Node.create({
  name: "variableBlock",
  group: "block",
  atom: true,
  selectable: true,
  draggable: true,
  addAttributes() { return { tok: { default: "", parseHTML: (el) => el.getAttribute("data-tok") || "", renderHTML: (a) => ({ "data-tok": a.tok }) } }; },
  parseHTML() { return [{ tag: "div[data-tok]", priority: 200 }]; },
  renderHTML({ node, HTMLAttributes }) {
    return ["div", mergeAttributes(HTMLAttributes, { class: "tok-block", contenteditable: "false" }),
      ["span", { class: isLogicToken(node.attrs.tok) ? "tok-pill tok-logic" : "tok-pill" }, chipLabel(node.attrs.tok)]];
  },
  renderText({ node }) { return node.attrs.tok; },
});

/** A placeholder ({Client Name}, {#if Paid}, …) as one uneditable chip. */
export const Variable = Node.create({
  name: "variable",
  group: "inline",
  inline: true,
  atom: true,
  selectable: true,
  addAttributes() { return { tok: { default: "", parseHTML: (el) => el.getAttribute("data-tok") || "", renderHTML: (a) => ({ "data-tok": a.tok }) } }; },
  parseHTML() { return [{ tag: "span[data-tok]", priority: 200 }]; },
  renderHTML({ node, HTMLAttributes }) {
    return ["span", mergeAttributes(HTMLAttributes, { class: isLogicToken(node.attrs.tok) ? "tok-pill tok-logic" : "tok-pill", contenteditable: "false" }), chipLabel(node.attrs.tok)];
  },
  renderText({ node }) { return node.attrs.tok; },
});

export function textExtensions(placeholder?: string): Extensions {
  return [
    StarterKit.configure({
      code: false, codeBlock: false, trailingNode: false, bold: false, italic: false, underline: false, strike: false,
      link: { openOnClick: false, autolink: false, HTMLAttributes: { target: null, rel: null, class: null } },
    }),
    ...MARKS,
    TextStyle.configure({ mergeNestedSpanStyles: false }),
    Image.configure({ inline: true, allowBase64: true }),
    PrintTable, Row, ...CELLS,
    Box, TextBox, Variable, VariableBlock, KeepAttributes,
    ...(placeholder ? [Placeholder.configure({ placeholder })] : []),
  ];
}

export const chipHTML = (tok: string, block = false) => (block ? `<div data-tok="${escapeAttr(tok)}"></div>` : `<span data-tok="${escapeAttr(tok)}"></span>`);

/** Stored template → what the Text view loads (leading <style> blocks held aside). */
export function toTextView(html: string): { styles: string; body: string } {
  const { styles, body } = splitLeadingStyles(html);
  return { styles, body: tokensToChips(body, chipHTML) };
}
function restoreMarkup(html: string): string {
  if (!html.includes("data-tk-")) return html;
  const doc = new window.DOMParser().parseFromString(`<!doctype html><body>${html}</body>`, "text/html");
  doc.body.querySelectorAll("[data-tk-style]").forEach((el) => { el.setAttribute("style", el.getAttribute("data-tk-style") || ""); el.removeAttribute("data-tk-style"); });
  regroupSections(doc.body);
  return doc.body.innerHTML;
}

/** Text view HTML → stored template. */
export function fromTextView(styles: string, editorHtml: string): string {
  const body = chipsToTokens(restoreMarkup(editorHtml));
  // An empty document is <p></p>; store nothing rather than an empty paragraph.
  return styles + (body === "<p></p>" ? "" : body);
}

let schemaCache: ReturnType<typeof getSchema> | null = null;
/** What the Text view would save for this template if opened and edited without changes. */
export function textRoundTrip(html: string): string {
  const schema = (schemaCache ||= getSchema(textExtensions()));
  const { styles, body } = toTextView(html);
  const dom = new window.DOMParser().parseFromString(`<!doctype html><body>${body}</body>`, "text/html").body;
  const doc = PMDOMParser.fromSchema(schema).parse(dom);
  return fromTextView(styles, getHTMLFromFragment(doc.content, schema));
}
