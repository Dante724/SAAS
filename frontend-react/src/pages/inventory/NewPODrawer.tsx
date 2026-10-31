import { useEffect, useRef, useState } from "react";
import { M } from "../../components/Shell";
import { api } from "../../api/client";
import DateField from "../../components/DateField";
import { Product, Supplier, money } from "./types";
import { FIELD_LABELS, FIELD_ORDER, fileToGrid, gridToRows, mapByHeader, type PasteField } from "./pasteMap";

/** Create a purchase order: supplier + location + line items → POST /inventory/po.
 * This is the entry point that was missing — the toolbar button used to open the
 * product form, so a PO could never actually be raised from the app. */
type Loc = { id: string; name: string };
type Row = { part_id: string; qty: number; cost: number };

export default function NewPODrawer({ products, onClose, onCreated }:
  { products: Product[]; onClose: () => void; onCreated: () => void }) {
  const [suppliers, setSuppliers] = useState<Supplier[]>([]);
  const [locs, setLocs] = useState<Loc[]>([]);
  const [supplierId, setSupplierId] = useState("");
  const [locId, setLocId] = useState("");
  const [onCredit, setOnCredit] = useState(true);
  const [expected, setExpected] = useState("");
  const [rows, setRows] = useState<Row[]>([{ part_id: "", qty: 1, cost: 0 }]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [pool, setPool] = useState<Product[]>(products);
  // inline quick-create so an empty system is never a dead end
  const [newSup, setNewSup] = useState(false); const [supName, setSupName] = useState(""); const [supBusy, setSupBusy] = useState(false);
  const [newProd, setNewProd] = useState(false); const [prodName, setProdName] = useState(""); const [prodCost, setProdCost] = useState(""); const [prodBusy, setProdBusy] = useState(false);
  // lines from a supplier's Excel/CSV: same column mapping as postings, remembered per supplier
  const [sheet, setSheet] = useState<{ name: string; headers: string[]; grid: string[][]; map: PasteField[] } | null>(null);
  const [unmatched, setUnmatched] = useState<string[]>([]);
  const sheetRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    api.get<any>("/suppliers").then((r) => setSuppliers(r.items || r || [])).catch(() => {}).finally(() => setLoaded(true));
    api.get<any>("/staff/locations").then((r) => { const it = r.items || r || []; setLocs(it); if (it[0]) setLocId(it[0].id); }).catch(() => {});
    // self-fetch products if the parent hasn't loaded any yet
    if (!products || products.length === 0) api.get<any>("/catalog/products").then((r) => setPool(r.items || [])).catch(() => {});
  }, []);

  const setRow = (i: number, patch: Partial<Row>) => setRows((rs) => rs.map((r, j) => j === i ? { ...r, ...patch } : r));
  const addRow = () => setRows((rs) => [...rs, { part_id: "", qty: 1, cost: 0 }]);
  const delRow = (i: number) => setRows((rs) => rs.filter((_, j) => j !== i));
  const total = rows.reduce((n, r) => n + (r.qty > 0 ? r.qty : 0) * (r.cost || 0), 0);

  // when a product is chosen, prefill its cost if we have one
  function pickPart(i: number, pid: string) {
    const p = pool.find((x) => x.id === pid) as any;
    setRow(i, { part_id: pid, cost: (p && (p.cost_price ?? p.avg_cost)) || rows[i].cost || 0 });
  }

  async function save(draft = false) {
    setErr(null);
    if (!supplierId) return setErr("Pick a supplier");
    if (!locId) return setErr("Pick a location");
    const lines = rows.filter((r) => r.part_id && r.qty > 0).map((r) => ({ part_id: r.part_id, qty_ordered: r.qty, unit_cost: r.cost || 0 }));
    if (lines.length === 0) return setErr("Add at least one product line");
    setBusy(true);
    try {
      await api.post("/inventory/po", { supplier_id: supplierId, location_id: locId, on_credit: onCredit, expected_at: expected || null, draft, lines });
      onCreated();
    } catch (e: any) { setErr(e.message || "Couldn't create purchase order"); setBusy(false); }
  }

  const supQ = (first: boolean) => (supplierId ? `${first ? "?" : "&"}supplier_id=${encodeURIComponent(supplierId)}` : "");
  async function onSheet(e: any) {
    const f = e.target.files?.[0]; if (!f) return;
    setErr(null); setUnmatched([]); setBusy(true);
    try {
      const fd = new FormData(); fd.append("file", f);
      const r = await api.postForm<any>(`/imports/parse?kind=po${supQ(false)}`, fd);
      const { grid, map } = fileToGrid(r.headers || [], r.rows || [], r.suggested || {});
      setSheet({ name: r.file_name || f.name, headers: r.headers || [], grid, map });
    } catch (e: any) { setErr(e.message || "Couldn't read that file"); }
    finally { setBusy(false); if (sheetRef.current) sheetRef.current.value = ""; }
  }
  function remapSheet(col: number, field: PasteField) {
    if (!sheet) return;
    const map = sheet.map.map((f, c) => (field !== "ignore" && f === field && c !== col ? "ignore" : f)) as PasteField[];
    map[col] = field;
    setSheet({ ...sheet, map });
    api.put(`/imports/po/mapping${supQ(true)}`, { mapping: mapByHeader(sheet.headers, map) }).catch(() => {});
  }
  async function matchSheet() {
    if (!sheet) return;
    const lines = gridToRows(sheet.grid, sheet.map).filter((x) => x.qty > 0);
    if (!lines.length) return setErr("No lines with a product and a quantity — check the column mapping.");
    setBusy(true); setErr(null);
    try {
      const r = await api.post<any>("/inventory/postings/preview", { rows: lines.map((x) => ({ code: x.code || null, mpn: x.mpn || null, name: x.name || null, qty: x.qty, purchase_price: x.purchase_price || null })) });
      const found: Row[] = []; const missing: string[] = [];
      (r.rows || []).forEach((m: any, i: number) => {
        if (m.match_status === "matched" && m.part_id) {
          const p = pool.find((x) => x.id === m.part_id) as any;
          found.push({ part_id: m.part_id, qty: lines[i].qty, cost: lines[i].purchase_price || (p && (p.cost_price ?? p.avg_cost)) || 0 });
        } else missing.push(lines[i].name || lines[i].code || lines[i].mpn);
      });
      setRows((rs) => [...rs.filter((x) => x.part_id), ...found].length ? [...rs.filter((x) => x.part_id), ...found] : [{ part_id: "", qty: 1, cost: 0 }]);
      setUnmatched(missing); setSheet(null);
    } catch (e: any) { setErr(e.message || "Couldn't match the lines"); }
    finally { setBusy(false); }
  }

  async function addSupplier() {
    const name = supName.trim();
    if (!name) return;
    setSupBusy(true);
    try {
      const s = await api.post<any>("/suppliers", { name });
      const created = { id: s.id || s.supplier?.id, name: s.name || name };
      setSuppliers((xs) => [...xs, created].sort((a, b) => (a.name || "").localeCompare(b.name || "")));
      setSupplierId(created.id); setNewSup(false); setSupName("");
    } catch (e: any) { setErr(e.message || "Couldn't add supplier"); } finally { setSupBusy(false); }
  }

  async function addProduct() {
    const name = prodName.trim();
    if (!name) return;
    setProdBusy(true);
    try {
      const p = await api.post<any>("/inventory/parts", { name, cost_price: Number(prodCost) || 0 });
      const created: any = { id: p.id || p.part?.id, name: p.name || name, cost_price: Number(prodCost) || 0 };
      setPool((xs) => [...xs, created]);
      // drop it into the first empty line
      setRows((rs) => { const i = rs.findIndex((r) => !r.part_id); const j = i >= 0 ? i : rs.length; const base = i >= 0 ? rs : [...rs, { part_id: "", qty: 1, cost: 0 }]; return base.map((r, k) => k === j ? { ...r, part_id: created.id, cost: created.cost_price || 0 } : r); });
      setNewProd(false); setProdName(""); setProdCost("");
    } catch (e: any) { setErr(e.message || "Couldn't add product"); } finally { setProdBusy(false); }
  }

  const fld = "glass-input rounded-lg px-3 py-2 text-sm w-full";
  const lbl = "block text-[11px] font-bold  text-ink-tertiary mb-1";

  return (
    <div className="fixed inset-0 flex justify-end" style={{ zIndex: 50 }}>
      <div className="absolute inset-0 bg-ink-primary/35 backdrop-blur-[2px]" onClick={onClose} />
      <div className="relative h-full w-full max-w-[640px] bg-raised shadow-clay-card flex flex-col border-l border-slate-200/90">
        <div className="px-6 pt-5 pb-3 border-b border-slate-100 flex items-center justify-between">
          <div className="flex items-center gap-2.5">
            <div className="w-10 h-10 rounded-xl bg-blue-50 text-primary grid place-items-center"><M n="receipt_long" c="text-xl" /></div>
            <h2 className="font-bold text-ink-primary text-lg">New purchase order</h2>
          </div>
          <button aria-label="Close" onClick={onClose} className="w-8 h-8 rounded-full grid place-items-center text-ink-tertiary hover:text-ink-primary hover:bg-slate-100"><M n="close" c="text-lg" /></button>
        </div>

        <div className="flex-1 overflow-y-auto px-6 py-5 space-y-4">
          {loaded && (suppliers.length === 0 || pool.length === 0) && (
            <div className="rounded-xl bg-blue-50 border border-blue-200/70 px-4 py-3 text-xs text-primary flex items-start gap-2">
              <M n="lightbulb" c="text-sm mt-0.5" />
              <span>First purchase order? Use <b>+ New</b> next to Supplier{pool.length === 0 ? " and New product below" : ""} to create {suppliers.length === 0 && pool.length === 0 ? "them" : suppliers.length === 0 ? "one" : "a product"} right here — no need to leave this screen.</span>
            </div>
          )}
          <div className="grid grid-cols-2 gap-3">
            <div>
              <div className="flex items-center justify-between mb-1"><label className={lbl} style={{ margin: 0 }}>Supplier *</label>
                <button type="button" onClick={() => setNewSup((v) => !v)} className="text-[11px] font-bold text-primary hover:underline">{newSup ? "Cancel" : "+ New"}</button></div>
              {newSup ? (
                <div className="flex gap-1.5">
                  <input autoFocus value={supName} onChange={(e) => setSupName(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") addSupplier(); }} placeholder="Supplier name" className={fld} />
                  <button type="button" onClick={addSupplier} disabled={supBusy || !supName.trim()} className="px-3 rounded-lg bg-primary text-white text-xs font-bold disabled:opacity-50">Add</button>
                </div>
              ) : (
                <select value={supplierId} onChange={(e) => setSupplierId(e.target.value)} className={fld}>
                  <option value="">Select supplier…</option>
                  {suppliers.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
                </select>
              )}
            </div>
            <div><label className={lbl}>Deliver to *</label>
              <select value={locId} onChange={(e) => setLocId(e.target.value)} className={fld}>
                {locs.map((l) => <option key={l.id} value={l.id}>{l.name}</option>)}
              </select></div>
            <div><label className={lbl}>Expected</label>
              <DateField value={expected} onChange={setExpected} /></div>
            <label className="flex items-end gap-2 pb-2 text-sm text-ink-secondary cursor-pointer">
              <input type="checkbox" checked={onCredit} onChange={(e) => setOnCredit(e.target.checked)} />Buy on credit (adds to supplier balance)</label>
          </div>

          <div className="rounded-2xl border border-slate-200/80 overflow-hidden">
            <table className="w-full text-left text-xs">
              <thead className="bg-slate-50/70 text-[11px] font-bold text-ink-tertiary">
                <tr><th className="py-2.5 px-3">Product</th><th className="py-2.5 px-2 text-right w-16">Qty</th><th className="py-2.5 px-2 text-right w-24">Unit cost</th><th className="py-2.5 px-2 text-right w-24">Total</th><th className="w-8" /></tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {rows.map((r, i) => (
                  <tr key={i}>
                    <td className="py-2 px-3">
                      <select value={r.part_id} onChange={(e) => pickPart(i, e.target.value)} className="glass-input rounded-lg px-2 py-1.5 text-xs w-full">
                        <option value="">Select product…</option>
                        {pool.map((p) => <option key={p.id} value={p.id}>{p.name}{p.sku ? ` · ${p.sku}` : ""}</option>)}
                      </select>
                    </td>
                    <td className="py-2 px-2"><input type="number" min={1} value={r.qty} onChange={(e) => setRow(i, { qty: Math.max(1, Number(e.target.value) || 1) })} className="glass-input rounded-lg px-2 py-1.5 text-xs w-full text-right font-mono" /></td>
                    <td className="py-2 px-2"><input type="number" min={0} value={r.cost} onChange={(e) => setRow(i, { cost: Math.max(0, Number(e.target.value) || 0) })} className="glass-input rounded-lg px-2 py-1.5 text-xs w-full text-right font-mono" /></td>
                    <td className="py-2 px-2 text-right tabular-nums text-ink-primary">{money((r.qty > 0 ? r.qty : 0) * (r.cost || 0))}</td>
                    <td className="py-2 px-1 text-center">{rows.length > 1 && <button aria-label="Close" onClick={() => delRow(i)} className="text-ink-tertiary hover:text-rose-600"><M n="close" c="text-sm" /></button>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="flex items-center gap-4 flex-wrap">
            <button onClick={addRow} className="text-xs font-bold text-primary hover:underline flex items-center gap-1"><M n="add" c="text-sm" />Add line</button>
            <button onClick={() => sheetRef.current?.click()} disabled={busy} className="text-xs font-bold text-primary hover:underline flex items-center gap-1 disabled:opacity-50"><M n="upload" c="text-sm" />Lines from Excel / CSV</button>
            <input ref={sheetRef} type="file" accept=".xlsx,.csv,text/csv" className="hidden" onChange={onSheet} />
            <button onClick={() => setNewProd((v) => !v)} className="text-xs font-bold text-primary hover:underline flex items-center gap-1"><M n="add_box" c="text-sm" />{newProd ? "Cancel new product" : "New product"}</button>
          </div>
          {newProd && (
            <div className="flex gap-1.5 items-end rounded-xl bg-slate-50/70 border border-slate-200/70 p-2.5">
              <div className="flex-1"><label className={lbl}>New product name</label>
                <input autoFocus value={prodName} onChange={(e) => setProdName(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") addProduct(); }} placeholder="e.g. Oil filter" className={fld} /></div>
              <div className="w-24"><label className={lbl}>Cost</label>
                <input type="number" min={0} value={prodCost} onChange={(e) => setProdCost(e.target.value)} className={`${fld} text-right font-mono`} /></div>
              <button type="button" onClick={addProduct} disabled={prodBusy || !prodName.trim()} className="px-3 py-2 rounded-lg bg-primary text-white text-xs font-bold disabled:opacity-50">Add</button>
            </div>
          )}

          {sheet && (
            <div className="rounded-xl border border-blue-200 bg-blue-50/50 p-3 space-y-2">
              <p className="text-[11px] font-semibold text-primary flex items-center gap-1"><M n="tune" c="text-sm" />{sheet.name}: {sheet.grid.length} rows — check the columns{supplierId ? " (remembered for this supplier)" : ""}</p>
              <div className="flex gap-2 overflow-x-auto pb-1">
                {sheet.headers.map((h, c) => (
                  <div key={h} className="shrink-0 w-32">
                    <select aria-label={`Field for ${h}`} value={sheet.map[c]} onChange={(e) => remapSheet(c, e.target.value as PasteField)} className="glass-input rounded-lg px-2 py-1.5 text-xs w-full">
                      {FIELD_ORDER.map((f) => <option key={f} value={f}>{FIELD_LABELS[f]}</option>)}
                    </select>
                    <div className="text-[11px] text-ink-tertiary mt-1 px-1 truncate" title={h}>{h}</div>
                  </div>
                ))}
              </div>
              <div className="flex items-center gap-2">
                <button onClick={matchSheet} disabled={busy} className="px-3 py-1.5 rounded-lg bg-primary text-white text-xs font-bold disabled:opacity-50">{busy ? "Matching…" : "Add matching lines"}</button>
                <button onClick={() => setSheet(null)} className="px-3 py-1.5 rounded-lg border border-slate-200 text-ink-secondary text-xs font-semibold">Cancel</button>
              </div>
            </div>
          )}
          {unmatched.length > 0 && (
            <div className="rounded-xl bg-amber-50 border border-amber-200 px-3 py-2 text-xs text-amber-800">
              {unmatched.length} line{unmatched.length === 1 ? " isn't" : "s aren't"} in your catalog yet: {unmatched.slice(0, 8).join(", ")}{unmatched.length > 8 ? "…" : ""}. Add {unmatched.length === 1 ? "it" : "them"} with New product, or import products first.
            </div>
          )}
          {err && <div className="text-xs text-rose-600 font-semibold">{err}</div>}
        </div>

        <div className="px-6 py-3 border-t border-slate-100 flex items-center justify-between">
          <span className="text-sm font-bold text-ink-primary">Total {money(total)}</span>
          <div className="flex gap-2">
            <button onClick={onClose} className="px-4 py-2 rounded-lg border border-slate-200 text-ink-secondary text-xs font-semibold hover:bg-slate-100">Cancel</button>
            <button onClick={() => save(true)} disabled={busy} className="px-4 py-2 rounded-lg border border-slate-200 text-ink-secondary text-xs font-bold hover:bg-slate-100 disabled:opacity-50">Save as draft</button>
            <button onClick={() => save(false)} disabled={busy} className="px-4 py-2 rounded-lg bg-primary text-white text-xs font-bold hover:bg-primary-container disabled:opacity-50 flex items-center gap-1.5"><M n="check" c="text-sm" />{busy ? "Creating…" : "Create & order"}</button>
          </div>
        </div>
      </div>
    </div>
  );
}
