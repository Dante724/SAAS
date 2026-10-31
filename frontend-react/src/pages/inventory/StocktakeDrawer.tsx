import { useEffect, useRef, useState } from "react";
import { loadFailed } from "../../lib/notice";
import { M } from "../../components/Shell";
import SkeletonRows from "../../components/SkeletonRows";
import ScanButton from "../../components/ScanButton";
import { api, openAuthedDoc } from "../../api/client";
import { alertDialog, confirmDialog } from "../../lib/dialog";
import { countRows } from "./countSheet";
import type { Product } from "./types";

type StLine = { id: string; part_id: string; part_name: string; sku?: string | null; bin_id?: string | null; bin?: string | null;
  system_qty: number | null; counted_qty: number | null; variance: number | null; is_unknown?: boolean; serial_mismatch?: boolean; note?: string | null };
type Sheet = { id: string; status: string; kind: string; blind: boolean; by_bin: boolean; bin_id?: string | null; location_id?: string;
  category_id?: string | null; note?: string | null; items: StLine[]; summary: { total: number; counted: number; uncounted: number } };
type Opt = { id: string; name: string };

/* ii144c — a stock take: count a location, one bin or one category; bin by bin; blind (the counter
   never sees book quantities); scan (a bin label says which bin you're in; a serial counts once),
   type, or export the sheet and import it filled in; save as a draft and carry on later; post the
   differences, with what nobody counted kept or set to zero. */
export default function StocktakeDrawer({ id, onClose, onDone }:
  { id?: string; products?: Product[]; onClose: () => void; onDone: () => void }) {
  const [stId, setStId] = useState<string | undefined>(id);
  const [sheet, setSheet] = useState<Sheet | null>(null);
  const [reveal, setReveal] = useState(false);
  const [locations, setLocations] = useState<Opt[]>([]);
  const [bins, setBins] = useState<(Opt & { location_id: string })[]>([]);
  const [cats, setCats] = useState<Opt[]>([]);
  const [form, setForm] = useState({ location_id: "", scope: "all" as "all" | "bin" | "category", bin_id: "", category_id: "",
    kind: "full" as "full" | "partial", by_bin: false, blind: false, note: "" });
  const [scan, setScan] = useState(""); const [scanQty, setScanQty] = useState(1); const [regUnknown, setRegUnknown] = useState(false);
  const [curBin, setCurBin] = useState<Opt | null>(null);
  const [busy, setBusy] = useState(false); const [err, setErr] = useState<string | null>(null);
  const [flash, setFlash] = useState<{ text: string; tone: "ok" | "warn" } | null>(null);
  const [posting, setPosting] = useState(false); const [uncounted, setUncounted] = useState<"keep" | "zero">("keep");
  const scanRef = useRef<HTMLInputElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  async function load(theId: string, rev = reveal) {
    const r = await api.get<Sheet>(`/stocktake/${theId}/lines${rev ? "?reveal=true" : ""}`);
    setSheet(r);
  }
  useEffect(() => {
    api.get<any>("/staff/locations").then((r) => { const it = r.items || r || []; setLocations(it); setForm((f) => ({ ...f, location_id: f.location_id || it[0]?.id || "" })); }).catch(loadFailed("locations"));
    api.get<any>("/bins").then((r) => setBins(r.items || [])).catch(loadFailed("bins"));
    api.get<any>("/catalog/categories?entity_type=product").then((r) => {
      const flat: Opt[] = [];
      const walk = (nodes: any[], d: number) => (nodes || []).forEach((n) => { flat.push({ id: n.id, name: `${"— ".repeat(d)}${n.name}` }); walk(n.children, d + 1); });
      walk(r.tree || [], 0); setCats(flat);
    }).catch(loadFailed("categories"));
    if (id) load(id).catch(() => setErr("Could not load this stock take"));
  }, []); // eslint-disable-line

  async function start() {
    if (!form.location_id) { setErr("Pick a location."); return; }
    if (form.scope === "bin" && !form.bin_id) { setErr("Pick the bin to count."); return; }
    if (form.scope === "category" && !form.category_id) { setErr("Pick the category to count."); return; }
    setBusy(true); setErr(null);
    try {
      const r = await api.post<any>("/stocktake", { location_id: form.location_id, kind: form.kind, blind: form.blind, note: form.note || null,
        bin_id: form.scope === "bin" ? form.bin_id : null, category_id: form.scope === "category" ? form.category_id : null,
        by_bin: form.scope !== "bin" && form.by_bin });
      setStId(r.id); await load(r.id);
      if (form.scope === "bin") setCurBin(bins.find((b) => b.id === form.bin_id) || null);
    } catch (e: any) { setErr(e.message || "Could not start the count"); }
    finally { setBusy(false); }
  }

  async function doScan(scanned?: string) {
    const code = (scanned ?? scan).trim(); if (!code || !stId) return;
    setErr(null);
    try {
      const r = await api.post<any>(`/stocktake/${stId}/scan`, { code, qty: scanQty, register_unknown: regUnknown, bin_id: curBin?.id || null });
      setScan(""); scanRef.current?.focus();
      if (r.bin) { setCurBin(r.bin); setFlash({ text: `Counting in bin ${r.bin.name}`, tone: "ok" }); await load(stId); return; }
      setFlash(r.duplicate ? { text: `${r.serial} was already counted — not added again`, tone: "warn" }
        : { text: `${r.part_name}: ${r.counted_qty} counted${r.is_unknown ? " (new product — check it before posting)" : ""}`, tone: "ok" });
      await load(stId);
    } catch (e: any) {
      setErr(e.message || "Scan failed");
      if (/register/i.test(e.message || "")) setRegUnknown(true);
    }
  }

  async function setCount(line: StLine, v: number) {
    if (!stId) return;
    setSheet((s) => s && ({ ...s, items: s.items.map((l) => l.id === line.id ? { ...l, counted_qty: v, variance: l.system_qty == null ? null : Math.round((v - l.system_qty) * 1000) / 1000 } : l) }));
    try { await api.patch(`/stocktake/${stId}/lines/${line.id}`, { counted_qty: v }); } catch (e: any) { setErr(e.message || "Could not save the count"); await load(stId); }
  }

  async function importFile(f: File | undefined) {
    if (!f || !stId) return;
    setErr(null);
    const parsed = countRows(await f.text());
    if ("error" in parsed) { setErr(parsed.error); return; }
    setBusy(true);
    try {
      const r = await api.post<any>(`/stocktake/${stId}/import`, { rows: parsed.rows, bin_field: parsed.hasBin ? "bin" : null, register_unknown: regUnknown });
      const bad = r.errors || [];
      setFlash({ text: `Imported ${r.applied} row${r.applied === 1 ? "" : "s"}${bad.length ? ` · ${bad.length} not imported` : ""}`, tone: bad.length ? "warn" : "ok" });
      if (bad.length) await alertDialog(bad.slice(0, 12).map((e: any) => `Row ${e.row}: ${e.error}`).join("\n") + (bad.length > 12 ? `\n…and ${bad.length - 12} more` : ""));
      await load(stId);
    } catch (e: any) { setErr(e.message || "Could not import the sheet"); }
    finally { setBusy(false); if (fileRef.current) fileRef.current.value = ""; }
  }

  async function post() {
    if (!stId) return;
    setBusy(true); setErr(null);
    try { await api.post(`/stocktake/${stId}/post`, { uncounted }); onDone(); }
    catch (e: any) { setErr(e.message || "Could not post"); setBusy(false); }
  }
  async function discard() {
    if (!stId || !(await confirmDialog("Discard this draft count? Nothing it counted is kept; stock doesn't change.", { danger: true, confirmLabel: "Discard" }))) return;
    try { await api.del(`/stocktake/${stId}`); onDone(); } catch (e: any) { setErr(e.message || "Could not discard"); }
  }
  async function toggleReveal() {
    if (!stId) return;
    const next = !reveal; setReveal(next); await load(stId, next);
  }

  const posted = sheet?.status === "posted";
  const hideBook = !!sheet?.blind && !posted && !reveal;
  const lines = sheet?.items || null;
  const counted = (lines || []).filter((l) => l.counted_qty != null);
  const toAdjust = counted.filter((l) => (l.variance || 0) !== 0).length;
  const notCounted = (lines || []).length - counted.length;
  const showBin = !!sheet && (sheet.by_bin || (lines || []).some((l) => l.bin));
  const locBins = bins.filter((b) => b.location_id === form.location_id);
  const choice = (on: boolean) => `flex-1 rounded-lg border px-3 py-2 text-xs font-semibold text-left ${on ? "border-primary bg-primary/5 text-primary" : "border-slate-200 text-ink-secondary"}`;
  const fld = "w-full bg-white border border-slate-200 rounded-lg text-sm px-3 py-2";
  const lbl = "block text-[11px] font-semibold text-ink-tertiary mb-1";
  const btnGhost = "px-3 py-2 rounded-lg border border-slate-200 bg-white text-ink-secondary hover:bg-slate-50 font-semibold text-xs flex items-center gap-1 disabled:opacity-50";

  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      <div aria-hidden="true" className="absolute inset-0 bg-ink-primary/40 backdrop-blur-[2px]" onClick={onClose} />
      <div className="relative w-[680px] max-w-full bg-white h-full shadow-drawer flex flex-col">
        <div className="flex items-center justify-between px-5 py-4 border-b border-slate-200">
          <h2 className="font-bold text-ink-primary flex items-center gap-2"><M n="fact_check" c="text-base text-primary" />{stId ? (posted ? "Stock take (posted)" : "Count in progress") : "New stock take"}
            {sheet && !posted && <span className="px-2 py-0.5 rounded-full bg-amber-50 text-amber-700 border border-amber-200/70 text-[11px] font-bold">Draft</span>}
            {sheet?.blind && <span className="px-2 py-0.5 rounded-full bg-slate-100 text-ink-secondary border border-slate-200 text-[11px] font-bold">Blind</span>}
            {sheet?.by_bin && <span className="px-2 py-0.5 rounded-full bg-blue-50 text-primary border border-blue-100 text-[11px] font-bold">Bin by bin</span>}
          </h2>
          <button aria-label="Close" onClick={onClose} className="w-8 h-8 rounded-lg hover:bg-slate-200/60 flex items-center justify-center text-ink-tertiary"><M n="close" c="text-base" /></button>
        </div>
        <div className="flex-1 overflow-y-auto p-5 space-y-4">
          {err && <div role="alert" className="rounded-lg bg-rose-50 border border-rose-200 text-rose-700 text-xs px-3 py-2 whitespace-pre-line">{err}</div>}

          {!stId ? (
            <>
              <div><label className={lbl}>Location</label>
                <select value={form.location_id} onChange={(e) => setForm({ ...form, location_id: e.target.value, bin_id: "" })} aria-label="Location" className={fld}>{locations.map((l) => <option key={l.id} value={l.id}>{l.name}</option>)}</select></div>
              <div><label className={lbl}>What to count</label>
                <div className="flex gap-2">
                  <button onClick={() => setForm({ ...form, scope: "all" })} className={choice(form.scope === "all")}>Whole location<span className="block font-normal text-ink-tertiary mt-0.5">Every product here.</span></button>
                  <button onClick={() => setForm({ ...form, scope: "bin" })} className={choice(form.scope === "bin")}>One bin<span className="block font-normal text-ink-tertiary mt-0.5">Only what's in that bin.</span></button>
                  <button onClick={() => setForm({ ...form, scope: "category" })} className={choice(form.scope === "category")}>One category<span className="block font-normal text-ink-tertiary mt-0.5">e.g. just screens.</span></button>
                </div></div>
              {form.scope === "bin" && (
                <div><label className={lbl}>Bin</label>
                  <select value={form.bin_id} onChange={(e) => setForm({ ...form, bin_id: e.target.value })} aria-label="Bin to count" className={fld}>
                    <option value="">Pick a bin…</option>{locBins.map((b) => <option key={b.id} value={b.id}>{b.name}</option>)}</select></div>
              )}
              {form.scope === "category" && (
                <div><label className={lbl}>Category</label>
                  <select value={form.category_id} onChange={(e) => setForm({ ...form, category_id: e.target.value })} aria-label="Category to count" className={fld}>
                    <option value="">Pick a category…</option>{cats.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}</select></div>
              )}
              <div><label className={lbl}>Count type</label>
                <div className="flex gap-2">
                  <button onClick={() => setForm({ ...form, kind: "full" })} className={choice(form.kind === "full")}>Full count<span className="block font-normal text-ink-tertiary mt-0.5">Lists everything on hand to count.</span></button>
                  <button onClick={() => setForm({ ...form, kind: "partial" })} className={choice(form.kind === "partial")}>Partial count<span className="block font-normal text-ink-tertiary mt-0.5">Starts empty — only what you scan or import.</span></button>
                </div></div>
              <div className="space-y-2">
                {form.scope !== "bin" && <label className="flex items-start gap-2 text-xs text-ink-secondary"><input type="checkbox" className="mt-0.5" checked={form.by_bin} onChange={(e) => setForm({ ...form, by_bin: e.target.checked })} /><span><b className="text-ink-primary">Bin by bin</b> — a line for each bin a product is in; scan a bin label, then what's in it.</span></label>}
                <label className="flex items-start gap-2 text-xs text-ink-secondary"><input type="checkbox" className="mt-0.5" checked={form.blind} onChange={(e) => setForm({ ...form, blind: e.target.checked })} /><span><b className="text-ink-primary">Blind count</b> — the counter doesn't see how many there should be. Every scan adds one.</span></label>
              </div>
              <div><label className={lbl}>Note (optional)</label><input value={form.note} onChange={(e) => setForm({ ...form, note: e.target.value })} placeholder="e.g. Back room, month end" aria-label="Note" className={fld} /></div>
            </>
          ) : (
            <>
              {!posted && (
                <div className="rounded-xl border border-slate-200 bg-slate-50/60 p-3 space-y-2">
                  <div className="flex items-center justify-between">
                    <label className="block text-[11px] font-semibold text-ink-tertiary">Scan or type a code — a bin label, a barcode, SKU, MPN or serial</label>
                    {curBin && <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-lg bg-blue-50 border border-blue-100 text-primary text-[11px] font-bold"><M n="warehouse" c="text-sm" />In bin {curBin.name}
                      {!sheet?.bin_id && <button aria-label="Stop counting in this bin" onClick={() => setCurBin(null)} className="ml-0.5"><M n="close" c="text-sm" /></button>}</span>}
                  </div>
                  <div className="flex items-center gap-2">
                    <input ref={scanRef} autoFocus value={scan} onChange={(e) => setScan(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") doScan(); }} placeholder="Scan…" aria-label="Code to count" className="flex-1 bg-white border border-slate-200 rounded-lg text-sm px-3 py-2" />
                    {!sheet?.blind && <input type="number" min={1} value={scanQty} onChange={(e) => setScanQty(Math.max(1, +e.target.value || 1))} aria-label="Quantity per scan" className="w-16 bg-white border border-slate-200 rounded-lg text-sm px-2 py-2 text-right" title="Quantity per scan" />}
                    <ScanButton title="Count with the camera" onScan={(c) => doScan(c)} />
                    <button onClick={() => doScan()} className="px-3 py-2 rounded-lg bg-primary text-white font-semibold text-xs hover:bg-primary-container flex items-center gap-1"><M n="qr_code_scanner" c="text-sm" />Count</button>
                  </div>
                  <label className="flex items-center gap-2 text-xs text-ink-secondary"><input type="checkbox" checked={regUnknown} onChange={(e) => setRegUnknown(e.target.checked)} />Register unknown codes as new products</label>
                  {flash && <div className={`text-xs font-semibold ${flash.tone === "ok" ? "text-emerald-700" : "text-amber-700"}`}>{flash.text}</div>}
                </div>
              )}
              <div className="flex items-center gap-2 flex-wrap">
                <button onClick={() => stId && openAuthedDoc(`/stocktake/${stId}/export/csv`).catch(() => setErr("Could not download the sheet"))} className={btnGhost}><M n="download" c="text-sm" />Export count sheet</button>
                {!posted && <button onClick={() => fileRef.current?.click()} disabled={busy} className={btnGhost}><M n="upload" c="text-sm" />Import counts</button>}
                <input ref={fileRef} type="file" accept=".csv,text/csv" className="hidden" aria-label="Count sheet file" onChange={(e) => importFile(e.target.files?.[0])} />
                {sheet?.blind && !posted && <button onClick={toggleReveal} className={btnGhost}><M n={reveal ? "visibility_off" : "visibility"} c="text-sm" />{reveal ? "Hide book quantities" : "Review differences"}</button>}
                {posted && <button onClick={() => stId && openAuthedDoc(`/print/stocktakes/${stId}`).catch(() => setErr("Could not open the settlement act"))} className={btnGhost}><M n="print" c="text-sm" />Settlement act</button>}
                <span className="flex-1" />
                <span className="text-xs text-ink-tertiary">{lines?.length ?? 0} line(s) · {counted.length} counted</span>
              </div>
              <div className="rounded-xl border border-slate-200 overflow-hidden">
                <table className="w-full text-left text-xs">
                  <thead className="bg-slate-50/60 text-[11px] font-bold text-ink-tertiary"><tr>
                    <th className="py-2 px-3">Product</th>{showBin && <th className="py-2 px-3">Bin</th>}
                    <th className="py-2 px-3 text-right">Book</th><th className="py-2 px-3 text-right w-24">Counted</th><th className="py-2 px-3 text-right">Difference</th>
                  </tr></thead>
                  <tbody className="divide-y divide-slate-100 text-ink-secondary">
                    {lines == null && <SkeletonRows cols={showBin ? 5 : 4} />}
                    {lines?.length === 0 && <tr><td colSpan={showBin ? 5 : 4} className="py-5 px-3 text-ink-tertiary">No lines yet — scan something to count it.</td></tr>}
                    {lines?.map((l) => (
                      <tr key={l.id}>
                        <td className="py-2 px-3"><span className="font-semibold text-ink-primary">{l.part_name}</span>{l.sku && <span className="ml-1.5 font-mono text-ink-tertiary">{l.sku}</span>}
                          {l.is_unknown && <span className="ml-1.5 px-1.5 rounded bg-amber-50 text-amber-700 text-[10px] font-bold">New</span>}
                          {l.serial_mismatch && <div className="text-[11px] text-amber-700">{l.note || "Serial is somewhere else in the system"}</div>}</td>
                        {showBin && <td className="py-2 px-3">{l.bin || "No bin"}</td>}
                        <td className="py-2 px-3 text-right tabular-nums">{hideBook ? "•" : l.system_qty}</td>
                        <td className="py-2 px-3 text-right">
                          {posted ? <span className="tabular-nums">{l.counted_qty ?? "—"}</span> :
                            <input type="number" min={0} value={l.counted_qty ?? ""} onChange={(e) => setCount(l, e.target.value === "" ? 0 : Math.max(0, Number(e.target.value)))} aria-label={`Counted ${l.part_name}`} className="w-20 bg-white border border-slate-200 rounded-lg text-xs px-2 py-1 text-right" />}
                        </td>
                        <td className={`py-2 px-3 text-right tabular-nums font-bold ${hideBook || (l.variance || 0) === 0 ? "text-ink-tertiary" : (l.variance || 0) < 0 ? "text-rose-600" : "text-emerald-600"}`}>{hideBook || l.counted_qty == null ? "—" : `${(l.variance || 0) > 0 ? "+" : ""}${l.variance || 0}`}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {posting && !posted && (
                <div className="rounded-xl border border-primary/30 bg-blue-50/40 p-3 space-y-2" role="group" aria-label="Post this count">
                  <p className="text-xs text-ink-secondary">{hideBook ? "Differences are hidden in a blind count until you post." : `${toAdjust} line(s) with a difference will adjust stock.`} {notCounted ? `${notCounted} line(s) weren't counted:` : "Every line was counted."}</p>
                  {notCounted > 0 && <div className="flex gap-2">
                    <button onClick={() => setUncounted("keep")} className={choice(uncounted === "keep")}>Keep their book quantity<span className="block font-normal text-ink-tertiary mt-0.5">Not counted means not checked.</span></button>
                    <button onClick={() => setUncounted("zero")} className={choice(uncounted === "zero")}>Set them to zero<span className="block font-normal text-ink-tertiary mt-0.5">Not counted means not on the shelf.</span></button>
                  </div>}
                </div>
              )}
            </>
          )}
        </div>
        <div className="px-5 py-4 border-t border-slate-200 flex items-center gap-2">
          {!stId && <button onClick={start} disabled={busy} className="px-4 py-2 rounded-lg bg-primary text-white font-semibold text-xs hover:bg-primary-container disabled:opacity-60">{busy ? "Starting…" : "Start count"}</button>}
          {stId && !posted && !posting && <button onClick={() => setPosting(true)} disabled={busy || !sheet} className="px-4 py-2 rounded-lg bg-primary text-white font-semibold text-xs hover:bg-primary-container disabled:opacity-60">Post count…</button>}
          {stId && !posted && posting && <button onClick={post} disabled={busy} className="px-4 py-2 rounded-lg bg-primary text-white font-semibold text-xs hover:bg-primary-container disabled:opacity-60">{busy ? "Posting…" : "Post and adjust stock"}</button>}
          <button onClick={posting ? () => setPosting(false) : stId && !posted ? onDone : onClose} className="px-4 py-2 rounded-lg border border-slate-200 text-ink-secondary hover:bg-slate-100 font-semibold text-xs">{posting ? "Back" : posted ? "Close" : stId ? "Save draft and close" : "Cancel"}</button>
          {stId && !posted && !posting && <button onClick={discard} className="ml-auto px-3 py-2 rounded-lg text-rose-600 hover:bg-rose-50 font-semibold text-xs">Discard draft</button>}
        </div>
      </div>
    </div>
  );
}
