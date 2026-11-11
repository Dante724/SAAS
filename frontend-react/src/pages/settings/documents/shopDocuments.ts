// The shop's five everyday documents for Settings › Documents, and the plain rules for them:
// which template each chip shows, the standard layout when there's none yet, the paper and the
// style (font set). Everything is stored in the existing template fields.
import type { DocKind } from "../templateEditor/docFeatures";

export const KINDS: { kind: DocKind; label: string }[] = [
  { kind: "invoice", label: "Invoice" }, { kind: "receipt", label: "Receipt" }, { kind: "estimate", label: "Quote" },
  { kind: "work_order", label: "Work order" }, { kind: "label", label: "Label" },
];

/** The layout a kind of document starts from when the shop has none of its own yet (Smart
 *  builder presets, the same ones the editor's gallery offers). */
export const STANDARD: Record<DocKind, { preset: string | null; page_size: string; name: string }> = {
  invoice: { preset: "invoice_detailed", page_size: "a4", name: "Invoice" },
  receipt: { preset: "receipt_simple", page_size: "receipt80", name: "Receipt" },
  estimate: { preset: "estimate_standard", page_size: "a4", name: "Quote" },
  work_order: { preset: "work_order_full", page_size: "a4", name: "Work order" },
  label: { preset: null, page_size: "label", name: "Label" },
};

/** The template a chip shows: the default one for that kind, else the first. */
export function pickTemplate(templates: any[], kind: DocKind): any | null {
  const mine = templates.filter((t) => t.doc_type === kind && t.active !== false);
  if (kind === "label") return mine.find((t) => t.is_default) || mine.find((t) => /price/i.test(t.name || "")) || mine[0] || null;
  return mine.find((t) => t.is_default) || mine[0] || null;
}

export type Meta = {
  name: string; doc_type: string; page_size: string; font_family: string; is_default: boolean; auto_print_on: string;
  page_w_mm: number | null; page_h_mm: number | null; margin_mm: number | null; barcode_field: string | null; barcode_types: Record<string, string>;
};
export const metaOf = (t: any, kind: DocKind): Meta => ({
  name: t?.name || STANDARD[kind].name, doc_type: t?.doc_type || kind, page_size: t?.page_size || STANDARD[kind].page_size,
  font_family: t?.font_family || "Inter", is_default: t ? !!t.is_default : true, auto_print_on: t?.auto_print_on || "",
  page_w_mm: t?.page_w_mm ?? null, page_h_mm: t?.page_h_mm ?? null, margin_mm: t?.margin_mm ?? null,
  barcode_field: t?.barcode_field ?? null, barcode_types: t?.barcode_types || {},
});

// ── paper ──
export const PAPERS: { id: string; label: string }[] = [
  { id: "a4", label: "A4" }, { id: "letter", label: "Letter" }, { id: "receipt80", label: "80 mm" }, { id: "receipt58", label: "58 mm" },
];
const MM = 3.7795;
/** The printed width and height in screen pixels (96 dpi), and the margin. */
export function pageBox(m: Meta): { w: number; h: number | null; pad: number } {
  const sizes: Record<string, [number, number | null]> = { a4: [210, 297], letter: [215.9, 279.4], receipt80: [80, null], receipt58: [58, null] };
  const [wmm, hmm] = m.page_size === "label" ? [m.page_w_mm || 50, m.page_h_mm || 30] : sizes[m.page_size] || sizes.a4;
  const margin = m.margin_mm ?? (m.page_size === "a4" || m.page_size === "letter" ? 10 : m.page_size === "label" ? 0 : 0);
  return { w: Math.round(wmm * MM), h: hmm ? Math.round(hmm * MM) : null, pad: Math.round(margin * MM) };
}

// ── style (font set) ──
export const STYLES: { id: string; label: string; hint: string; font: string; margin: number | null }[] = [
  { id: "modern", label: "Modern", hint: "Clean sans-serif", font: "Inter", margin: null },
  { id: "classic", label: "Classic", hint: "Traditional serif", font: "Georgia", margin: null },
  { id: "compact", label: "Compact", hint: "Narrow, fits more", font: "Arial", margin: 6 },
];
export function styleOf(m: Meta): string | null {
  const s = STYLES.find((x) => x.font.toLowerCase() === (m.font_family || "").toLowerCase());
  return s?.id ?? null;
}
/** A style's font for every document; Compact also tightens the margins of full pages (receipt
 *  rolls and labels keep theirs). */
export function withStyle(m: Meta, id: string): Meta {
  const s = STYLES.find((x) => x.id === id); if (!s) return m;
  const page = m.page_size === "a4" || m.page_size === "letter";
  // only Compact's own margin is undone when leaving it; a margin the shop set stays
  const margin = !page ? m.margin_mm : s.margin != null ? s.margin : m.margin_mm === 6 ? null : m.margin_mm;
  return { ...m, font_family: s.font, margin_mm: margin };
}

/** What a save sends: the server's latest copy of the template, with only the fields this page
 *  changed (compared with what it last had from us) and the body. Auto-print is never sent, so a
 *  change made elsewhere is kept (the server keeps its value when the field is left out). */
export function payloadFor(row: any | null, meta: Meta, loaded: Meta, body_html: string): Record<string, unknown> {
  const base: Record<string, unknown> = row ? { ...metaOf(row, meta.doc_type as DocKind) } : { ...meta };
  for (const k of Object.keys(meta) as (keyof Meta)[]) if (JSON.stringify(meta[k]) !== JSON.stringify(loaded[k])) base[k] = meta[k];
  delete base.auto_print_on;
  return { ...base, body_html };
}
