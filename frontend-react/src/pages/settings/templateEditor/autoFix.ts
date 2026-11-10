// "Make this editable visually": safe automatic rewrites for the constructs that grey out Design
// and Text. Each rewrite prints the same page; the editor only offers it when the rewritten
// template also passes the round-trip check (modeSupport), and shows the owner every change first.
import { splitLeadingStyles } from "./templateHtml";

export type Change = { what: string; before: string; after: string };
export type Fix = { html: string; changes: Change[] };

// Rows hidden by a condition: the condition moves INTO the row, as an attribute the server fills
// in ({#if X}yes{/if} → "yes" or ""), and a CSS rule hides a row whose attribute came out empty.
// display:none rows take no space, so the printed page is the same as before.
export const HIDE_RULE = 'tr[data-tk-show=""]{display:none}';
const ROWS = String.raw`(?:\s*<tr\b[^>]*>[\s\S]*?<\/tr>\s*)+`;
const ROW_BLOCK = new RegExp(String.raw`\{#(if|unless)\s+([^{}]+?)\}(${ROWS})(?:\{#else\}(${ROWS}))?\{\/\1\}`, "g");

function markRows(rows: string, kind: string, name: string): string {
  return rows.replace(/<tr\b([^>]*)>/g, (m, attrs: string) =>
    /data-tk-show/.test(attrs) ? m : `<tr data-tk-show="{#${kind} ${name}}yes{/${kind}}"${attrs}>`);
}

export function fixRowConditions(html: string): Fix | null {
  const changes: Change[] = [];
  const out = html.replace(ROW_BLOCK, (m, kind: string, name: string, yes: string, no?: string) => {
    if (/data-tk-show/.test(m) || /<table\b/i.test(m)) return m;          // nested or odd: leave it
    const other = kind === "if" ? "unless" : "if";
    const after = markRows(yes, kind, name) + (no ? markRows(no, other, name) : "");
    changes.push({ what: `Rows shown only when "${name}" ${kind === "if" ? "has a value" : "is empty"}: the condition moves into the row`, before: m.trim(), after: after.trim() });
    return after;
  });
  if (!changes.length) return null;
  return { html: addRule(out, changes), changes };
}

function addRule(html: string, changes: Change[]): string {
  if (html.includes(HIDE_RULE)) return html;
  changes.push({ what: "A one-line rule at the top hides those rows when their condition is false", before: "", after: `<style>${HIDE_RULE}</style>` });
  return `<style>${HIDE_RULE}</style>` + html;
}

/** <style> blocks inside the page move to the top. Where a style block sits doesn't change what
 *  it styles, and their order is kept, so the page prints the same. */
export function hoistStyles(html: string): Fix | null {
  const { styles, body } = splitLeadingStyles(html);
  const found: string[] = [];
  const rest = body.replace(/<style\b[^>]*>[\s\S]*?<\/style>/gi, (m) => { found.push(m); return ""; });
  if (!found.length) return null;
  return { html: styles + found.join("") + rest, changes: found.map((s) => ({ what: "A style block inside the page moves to the top", before: s, after: s })) };
}

/** Every safe rewrite that applies, in order, or null when there is nothing to fix. */
export function proposeFix(html: string): Fix | null {
  let cur = html; const changes: Change[] = [];
  for (const step of [hoistStyles, fixRowConditions]) {
    const f = step(cur);
    if (f) { cur = f.html; changes.push(...f.changes); }
  }
  return changes.length ? { html: cur, changes } : null;
}
