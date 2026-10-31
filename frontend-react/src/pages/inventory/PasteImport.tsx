import { useState } from "react";
import { M } from "../../components/Shell";
import { api } from "../../api/client";
import { money } from "./types";
import { analyzePaste, gridToRows, loadRememberedMap, saveRememberedMap, FIELD_LABELS, FIELD_ORDER } from "./pasteMap";
import type { PasteField, PostRow } from "./pasteMap";

/** 13b — paste a supplier's sheet into a purchase order. Same pipeline as stock postings:
 * columns are detected (and fixable), each row is matched to the catalog by SKU / barcode /
 * code / MPN / name, and rows with no match can be created as new products in one go. */
export type ImportedLine = { part_id: string; name: string; qty: number; cost: number; created: boolean };
type Cand = { id: string; name: string; sku?: string };
type Review = PostRow & { match_status: "matched" | "unsure" | "new"; part_id: string | null; part_name?: string | null; sku?: string | null; candidates?: Cand[]; chosen: string; include: boolean };

// A supplier's code is theirs, not our SKU: a new product never takes it as its SKU. A GTIN-looking
// code (8/12/13/14 digits) is the barcode on the box; anything else is kept as the part number.
const GTIN = /^\d{8}$|^\d{12,14}$/;
export function newProductBody(x: { name?: string; code?: string; mpn?: string; product_type?: string; purchase_price?: number }) {
  const code = (x.code || "").trim(), mpn = (x.mpn || "").trim();
  return {
    new_name: (x.name || code || mpn).trim(), new_sku: null,
    new_barcode: GTIN.test(code) ? code : null,
    new_mpn: mpn || (code && !GTIN.test(code) ? code : null),
    product_type: x.product_type || null, cost: Number(x.purchase_price) || 0,
  };
}
// How a matched row was found — shown so a link to an existing product is never silent.
export function matchedBy(r: { code?: string; mpn?: string; sku?: string | null; part_name?: string | null; name?: string }) {
  const low = (v?: string | null) => (v || "").trim().toLowerCase();
  if (r.code && low(r.code) === low(r.sku)) return "same SKU";
  if (r.mpn) return "MPN";
  if (r.code) return "code";
  if (r.name && low(r.name) === low(r.part_name)) return "name";
  return "catalog";
}

export default function PasteImport({ onAdd, onClose, canCreate }: { onAdd: (lines: ImportedLine[]) => void; onClose: () => void; canCreate: boolean }) {
  const [text, setText] = useState("");
  const [grid, setGrid] = useState<string[][] | null>(null);
  const [map, setMap] = useState<PasteField[] | null>(null);
  const [review, setReview] = useState<Review[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function match(g: string[][], m: PasteField[]) {
    const rows = gridToRows(g, m).filter((r) => r.qty > 0);
    if (!rows.length) { setErr("Couldn't read any rows from that paste."); setReview(null); return; }
    setBusy(true); setErr(null);
    try {
      const r = await api.post<any>("/inventory/postings/preview", { rows: rows.map((x) => ({
        code: x.code || null, mpn: x.mpn || null, name: x.name || null, qty: Number(x.qty),
        purchase_price: Number(x.purchase_price) || null, product_type: x.product_type || null })) });
      setReview((r.rows || []).map((rr: any, i: number) => ({ ...rows[i], ...rr,
        chosen: rr.match_status === "matched" ? rr.part_id : (rr.candidates?.[0]?.id || "__new__"), include: true })));
    } catch (e: any) { setErr(e.message || "Couldn't match the rows"); } finally { setBusy(false); }
  }
  function readPaste() {
    const a = analyzePaste(text, loadRememberedMap());
    if (!a.grid.length) { setErr("Paste some rows first."); return; }
    setGrid(a.grid); setMap(a.map); match(a.grid, a.map);
  }
  function remap(col: number, field: PasteField) {
    if (!map || !grid) return;
    const next = map.map((f, c) => (field !== "ignore" && f === field && c !== col ? "ignore" : f)) as PasteField[];
    next[col] = field;
    setMap(next); saveRememberedMap(next); match(grid, next);
  }
  const isNew = (r: Review) => !r.chosen || r.chosen === "__new__";
  const picked = (review || []).filter((r) => r.include);
  const newCount = picked.filter(isNew).length;

  async function add() {
    if (!picked.length) { setErr("Tick at least one row."); return; }
    if (newCount && !canCreate) { setErr("Your role can't create products. Untick the new rows or pick an existing product."); return; }
    setBusy(true); setErr(null);
    try {
      const r = await api.post<any>("/inventory/import/resolve-products", { rows: picked.map((x) => isNew(x)
        ? newProductBody(x)
        : { part_id: x.chosen }) });
      onAdd((r.rows || []).map((o: any, i: number) => ({ part_id: o.part_id, name: o.name, created: !!o.created,
        qty: Number(picked[i].qty) > 0 ? Number(picked[i].qty) : 1, cost: Number(picked[i].purchase_price) || 0 })));
    } catch (e: any) { setErr(e.message || "Couldn't add the rows"); setBusy(false); }
  }

  const fld = "glass-input rounded-lg px-2 py-1.5 text-xs";
  return (
    <div className="rounded-2xl border border-slate-200 bg-slate-50/60 p-3 space-y-3">
      <div className="flex items-center justify-between">
        <span className="text-xs font-bold text-ink-primary flex items-center gap-1.5"><M n="content_paste" c="text-sm text-primary" />Paste from Excel</span>
        <button type="button" aria-label="Close paste" onClick={onClose} className="text-ink-tertiary hover:text-ink-primary"><M n="close" c="text-sm" /></button>
      </div>
      {!review && (<>
        <p className="text-[11px] text-ink-tertiary">Paste rows from the supplier's sheet — SKU or barcode, name, quantity, cost. A header row is optional.</p>
        <textarea value={text} onChange={(e) => setText(e.target.value)} rows={4} aria-label="Pasted rows"
          placeholder={"SKU\tName\tQty\tCost\nSCR-IP13\tiPhone 13 screen\t10\t60"} className={`${fld} w-full font-mono`} />
        <div className="flex justify-end"><button type="button" onClick={readPaste} disabled={busy || !text.trim()} className="px-3 py-1.5 rounded-lg bg-primary text-white text-xs font-bold disabled:opacity-50">{busy ? "Matching…" : "Match to catalog"}</button></div>
      </>)}
      {map && grid && review && (
        <div className="flex gap-2 overflow-x-auto pb-1" aria-label="Column mapping">
          {map.map((f, c) => (
            <div key={c} className="shrink-0">
              <select value={f} onChange={(e) => remap(c, e.target.value as PasteField)} aria-label={`Column ${c + 1}`} className={`${fld} w-28`}>
                {FIELD_ORDER.map((o) => <option key={o} value={o}>{FIELD_LABELS[o]}</option>)}
              </select>
              <div className="text-[11px] text-ink-tertiary mt-0.5 px-1 truncate w-28" title={grid[0]?.[c] || ""}>{grid[0]?.[c] || "—"}</div>
            </div>
          ))}
        </div>
      )}
      {review && (
        <div className="space-y-1.5">
          {review.map((r, i) => (
            <div key={i} className={`flex items-center gap-2 rounded-lg border px-2.5 py-1.5 bg-white ${r.include ? "border-slate-200" : "border-slate-100 opacity-60"}`}>
              <input type="checkbox" checked={r.include} aria-label={`Include ${r.name || r.code}`} onChange={(e) => setReview(review.map((x, j) => j === i ? { ...x, include: e.target.checked } : x))} />
              {(() => {
                const unsure = r.match_status === "unsure" && !isNew(r);
                return <span className={`text-[11px] font-bold px-1.5 py-0.5 rounded border ${unsure ? "bg-amber-50 text-amber-800 border-amber-200" : "bg-slate-50 text-ink-secondary border-slate-200"}`}>{isNew(r) ? "new" : unsure ? "check" : "match"}</span>;
              })()}
              <div className="flex-1 min-w-0">
                <div className="text-xs font-semibold text-ink-primary truncate">{r.name || r.code || r.mpn}</div>
                {r.candidates && r.candidates.length > 0 ? (
                  <select value={r.chosen} aria-label={`Product for ${r.name || r.code}`} onChange={(e) => setReview(review.map((x, j) => j === i ? { ...x, chosen: e.target.value } : x))} className={`${fld} mt-1 w-full py-1`}>
                    {r.candidates.map((c) => <option key={c.id} value={c.id}>{c.name}{c.sku ? ` (${c.sku})` : ""}</option>)}
                    <option value="__new__">+ Create new: {r.name || r.code}</option>
                  </select>
                ) : r.match_status === "matched" && r.part_id ? (
                  <select value={r.chosen} aria-label={`Product for ${r.name || r.code}`} onChange={(e) => setReview(review.map((x, j) => j === i ? { ...x, chosen: e.target.value } : x))} className={`${fld} mt-1 w-full py-1`}>
                    <option value={r.part_id}>{`${r.part_name || "Existing product"}${r.sku ? ` · our SKU ${r.sku}` : ""} — matched by ${matchedBy(r)}`}</option>
                    <option value="__new__">+ Create new: {r.name || r.code}</option>
                  </select>
                ) : <div className="text-[11px] text-ink-tertiary truncate">{`Will be created${r.code ? ` · supplier code ${r.code}` : ""}`}</div>}
              </div>
              <span className="text-[11px] text-ink-tertiary tabular-nums">×{r.qty}</span>
              <span className="text-[11px] text-ink-secondary tabular-nums w-14 text-right">{money(r.purchase_price || 0)}</span>
            </div>
          ))}
          <div className="flex items-center justify-between pt-1">
            <span className="text-[11px] text-ink-secondary">{newCount > 0 ? `${newCount} new product${newCount === 1 ? "" : "s"} will be added to the catalog.` : "Every row matches the catalog."}</span>
            <div className="flex gap-2">
              <button type="button" onClick={() => { setReview(null); setGrid(null); setMap(null); }} className="px-3 py-1.5 rounded-lg border border-slate-200 text-ink-secondary text-xs font-semibold bg-white">Back</button>
              <button type="button" onClick={add} disabled={busy || !picked.length} className="px-3 py-1.5 rounded-lg bg-primary text-white text-xs font-bold disabled:opacity-50">{busy ? "Adding…" : `Add ${picked.length} line${picked.length === 1 ? "" : "s"}`}</button>
            </div>
          </div>
        </div>
      )}
      {err && <div className="text-xs text-rose-600 font-semibold">{err}</div>}
    </div>
  );
}
