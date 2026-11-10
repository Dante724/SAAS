// Real data in the editor: Design and Text show each field filled in from the shop's own recent
// job (or sale / invoice / product, as the server's preview picks), so the page looks like what
// prints. This is display only: the stored template keeps the same {Field} text.
//
// No new API: we ask the existing /documents/preview to render a small "probe" document holding
// every field once, each wrapped in a numbered element, and read the values back out of it.
import { chipLabel, isLogicToken } from "./templateHtml";

// Fields the server only fills inside a repeating item row (documents.py _ROW_VAR_RE).
const ROW_FIELD = /^\{(?:Product [^}]+|Row Number|Row Order [^}]+|Technician (?:Name|Phone|Job Title))\}$/;

export type FieldValues = { values: Record<string, string>; sample: string | null };

/** A document holding each field once, numbered, with item-row fields inside a table row. */
export function buildProbe(tokens: string[]): string {
  const plain: string[] = [], row: string[] = [];
  tokens.forEach((t, i) => {
    if (isLogicToken(t)) return;
    (ROW_FIELD.test(t) ? row : plain).push(`<div data-tk-k="${i}">${t}</div>`);
  });
  const cells = row.map((d) => d.replace(/^<div/, "<td").replace(/<\/div>$/, "</td>"));
  return plain.join("") + (cells.length ? `<table><tbody><tr>${cells.join("")}</tr></tbody></table>` : "");
}

/** Values out of the rendered probe. A field the server left as-is (no data) gets no value. */
export function parseProbe(rendered: string, tokens: string[]): Record<string, string> {
  const doc = new DOMParser().parseFromString(`<!doctype html><body>${rendered || ""}</body>`, "text/html");
  const out: Record<string, string> = {};
  doc.body.querySelectorAll("[data-tk-k]").forEach((el) => {
    const t = tokens[Number(el.getAttribute("data-tk-k"))];
    if (!t || t in out) return;
    const v = el.innerHTML.trim();
    if (v && v !== t && (el.textContent || "").trim() !== t) out[t] = v;
  });
  return out;
}

/** A clearly made-up value for a field with no real data yet. */
export function sampleValue(tok: string): string {
  return `Sample ${chipLabel(tok).toLowerCase()}`;
}

export async function fetchFieldValues(
  post: (path: string, body: unknown) => Promise<any>, tokens: string[], docType: string,
): Promise<FieldValues> {
  const uniq = Array.from(new Set(tokens.filter((t) => !isLogicToken(t))));
  if (!uniq.length) return { values: {}, sample: null };
  const r = await post("/documents/preview", { body_html: buildProbe(uniq), doc_type: docType });
  return { values: parseProbe(r?.html || "", uniq), sample: r?.sample || null };
}

export type FieldDisplay = { kind: "chip" | "real" | "sample"; html: string; label: string };
/** What a field shows on the page. Conditions always stay chips: they are structure, not data. */
export function fieldDisplay(tok: string, data: FieldValues | null, showFields: boolean, clean: (h: string) => string): FieldDisplay {
  const label = chipLabel(tok);
  if (showFields || !data || isLogicToken(tok)) return { kind: "chip", html: escape(label), label };
  const v = data.values[tok];
  if (v) return { kind: "real", html: clean(v), label };
  return { kind: "sample", html: escape(sampleValue(tok)), label };
}
const escape = (s: string) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

/** CSS class for a field on the page, by how it is shown. */
export const FIELD_CLASS: Record<FieldDisplay["kind"], string> = { chip: "tok-pill", real: "tok-real", sample: "tok-real tok-sample" };

/** Paint a field element (in the Text page or the Design canvas) the way `d` says. */
export function paintField(el: HTMLElement, tok: string, d: FieldDisplay) {
  el.className = d.kind === "chip" ? (isLogicToken(tok) ? "tok-pill tok-logic" : "tok-pill") : FIELD_CLASS[d.kind];
  el.innerHTML = d.html;
  el.title = d.kind === "chip" ? tok : `${d.label}${d.kind === "sample" ? " (sample)" : ""}`;
}
