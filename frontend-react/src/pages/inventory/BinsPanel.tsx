import { Fragment, useEffect, useState } from "react";
import { loadFailed } from "../../lib/notice";
import { M } from "../../components/Shell";
import SkeletonRows from "../../components/SkeletonRows";
import BinScanField from "../../components/BinScanField";
import LabelPrintDialog from "../../components/LabelPrintDialog";
import MoveStockDialog from "../../components/MoveStockDialog";
import { api } from "../../api/client";
import { barcodeTypeLabel } from "../../lib/barcodes";
import { confirmDialog } from "../../lib/dialog";

type Bin = { id: string; name: string; location_id: string; warehouse_id?: string | null; zone?: string | null; shelf?: string | null;
  bin_number?: string | null; barcode?: string | null; barcode_type?: string; is_default?: boolean; qty_on_hand?: number };
type Content = { part_id: string; name: string; sku?: string | null; qty: number; reserved: number };

/* ii144c — bins: add, rename, a default bin per warehouse, labels in any barcode type, scan a bin to
   open it, see what it holds and move stock to another bin. */
export default function BinsPanel() {
  const [bins, setBins] = useState<Bin[] | null>(null);
  const [locations, setLocations] = useState<{ id: string; name: string }[]>([]);
  const [nf, setNf] = useState({ location_id: "", name: "", zone: "", shelf: "", is_default: false });
  const [busy, setBusy] = useState(false); const [err, setErr] = useState<string | null>(null);
  const [sel, setSel] = useState<Set<string>>(new Set());
  const [labels, setLabels] = useState(false);
  const [edit, setEdit] = useState<Bin | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const [contents, setContents] = useState<Content[] | null>(null);
  const [move, setMove] = useState<{ bin: Bin; item: Content } | null>(null);
  const [q, setQ] = useState("");

  const load = () => api.get<any>("/bins").then((r) => setBins(r.items || [])).catch(() => setBins([]));
  useEffect(() => { load(); api.get<any>("/staff/locations").then((r) => { const it = r.items || r || []; setLocations(it); setNf((s) => ({ ...s, location_id: it[0]?.id || "" })); }).catch(loadFailed("locations")); }, []);
  const loadContents = (id: string) => { setContents(null); api.get<any>(`/bins/${id}/contents`).then((r) => setContents(r.items || [])).catch(() => setContents([])); };
  function toggleOpen(id: string) { if (open === id) { setOpen(null); return; } setOpen(id); loadContents(id); }

  async function add() {
    if (!nf.location_id) { setErr("Pick a location for the bin."); return; }
    setBusy(true); setErr(null);
    try {
      await api.post("/bins", { location_id: nf.location_id, name: nf.name.trim() || null, zone: nf.zone || null, shelf: nf.shelf || null, is_default: nf.is_default || null });
      setNf({ ...nf, name: "", zone: "", shelf: "", is_default: false }); load();
    } catch (e: any) { setErr(e.message || "Could not add bin"); } finally { setBusy(false); }
  }
  async function saveEdit() {
    if (!edit) return;
    setBusy(true); setErr(null);
    try {
      await api.put(`/bins/${edit.id}`, { location_id: edit.location_id, name: edit.name.trim(), zone: edit.zone || null, shelf: edit.shelf || null,
        bin_number: edit.bin_number || null });
      setEdit(null); load();
    } catch (e: any) { setErr(e.message || "Could not save the bin"); } finally { setBusy(false); }
  }
  async function makeDefault(b: Bin) {
    setErr(null);
    try { await api.put(`/bins/${b.id}`, { location_id: b.location_id, name: b.name, zone: b.zone, shelf: b.shelf, bin_number: b.bin_number, is_default: !b.is_default }); load(); }
    catch (e: any) { setErr(e.message || "Could not change the default bin"); }
  }
  async function del(b: Bin) {
    if (!(await confirmDialog(`Delete bin ${b.name}?`, { danger: true, confirmLabel: "Delete" }))) return;
    try { await api.del(`/bins/${b.id}`); load(); } catch (e: any) { setErr(e.message || "In use — move its stock first"); }
  }
  const locName = (id: string) => locations.find((l) => l.id === id)?.name || "—";
  const shown = (bins || []).filter((b) => !q.trim() || `${b.name} ${b.barcode || ""} ${b.zone || ""} ${b.shelf || ""}`.toLowerCase().includes(q.trim().toLowerCase()));
  const allSel = shown.length > 0 && shown.every((b) => sel.has(b.id));
  const fld = "bg-white border border-slate-200 rounded-lg text-sm px-3 py-2";
  return (
    <div className="max-w-5xl space-y-4">
      <div><h3 className="text-sm font-bold text-ink-primary">Storage bins</h3><p className="text-xs text-ink-tertiary">Give every product an address inside a location. Stock received without a bin goes to the location's default bin. Print a label for each shelf and scan it when you count, move or receive stock.</p></div>
      {err && <div role="alert" className="rounded-lg bg-rose-50 border border-rose-200 text-rose-700 text-xs px-3 py-2">{err}</div>}

      <div className="bg-white rounded-2xl border border-slate-200/90 p-3 flex items-end gap-2 flex-wrap">
        <div><label className="block text-[11px] font-bold text-ink-tertiary mb-1">Location</label><select value={nf.location_id} onChange={(e) => setNf({ ...nf, location_id: e.target.value })} aria-label="Location" className={fld}>{locations.map((l) => <option key={l.id} value={l.id}>{l.name}</option>)}</select></div>
        <div><label className="block text-[11px] font-bold text-ink-tertiary mb-1">Bin name</label><input value={nf.name} onChange={(e) => setNf({ ...nf, name: e.target.value })} placeholder="Blank = its code" aria-label="Bin name" className={`${fld} w-36`} /></div>
        <div><label className="block text-[11px] font-bold text-ink-tertiary mb-1">Zone</label><input value={nf.zone} onChange={(e) => setNf({ ...nf, zone: e.target.value })} aria-label="Zone" className={`${fld} w-24`} /></div>
        <div><label className="block text-[11px] font-bold text-ink-tertiary mb-1">Shelf</label><input value={nf.shelf} onChange={(e) => setNf({ ...nf, shelf: e.target.value })} aria-label="Shelf" className={`${fld} w-24`} /></div>
        <label className="flex items-center gap-1.5 text-xs text-ink-secondary pb-2.5"><input type="checkbox" checked={nf.is_default} onChange={(e) => setNf({ ...nf, is_default: e.target.checked })} />Default bin</label>
        <button onClick={add} disabled={busy} className="px-3 py-2 rounded-lg bg-primary text-white text-xs font-semibold disabled:opacity-60 flex items-center gap-1"><M n="add" c="text-sm" />Add bin</button>
      </div>

      <div className="flex items-center gap-2 flex-wrap">
        <button onClick={() => setLabels(true)} disabled={sel.size === 0} className="px-3 py-1.5 rounded-lg border border-slate-200 bg-white text-ink-secondary text-xs font-semibold hover:bg-slate-50 disabled:opacity-50 flex items-center gap-1"><M n="print" c="text-sm" />Print labels{sel.size ? ` (${sel.size})` : ""}</button>
        <div className="flex-1" />
        <div className="w-64"><BinScanField onBin={(b) => { setQ(""); setOpen(b.id); loadContents(b.id); document.getElementById(`bin-${b.id}`)?.scrollIntoView({ block: "center" }); }} /></div>
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search bins…" aria-label="Search bins" className="bg-white border border-slate-200 rounded-lg text-xs px-2.5 py-1.5 w-44" />
      </div>

      <div className="bg-white rounded-2xl border border-slate-200/90 overflow-hidden">
        <table className="w-full text-xs">
          <thead className="bg-slate-50/70 text-ink-tertiary text-[11px] font-bold"><tr>
            <th className="py-2.5 pl-4 w-8"><input type="checkbox" aria-label="Select all bins" checked={allSel} onChange={() => setSel(allSel ? new Set() : new Set(shown.map((b) => b.id)))} /></th>
            <th className="text-left py-2.5 px-3">Bin</th><th className="text-left py-2.5 px-3">Label code</th><th className="text-left py-2.5 px-3">Location</th>
            <th className="text-left py-2.5 px-3">Zone / shelf</th><th className="text-right py-2.5 px-3">Holds</th><th className="py-2.5 px-3"></th></tr></thead>
          <tbody className="divide-y divide-slate-100">
            {bins == null && <SkeletonRows cols={7} />}
            {bins?.length === 0 && <tr><td colSpan={7} className="py-6 text-center text-ink-tertiary">No bins yet — add one above.</td></tr>}
            {shown.map((b) => (
              <Fragment key={b.id}>
                <tr id={`bin-${b.id}`} className={open === b.id ? "bg-blue-50/40" : ""}>
                  <td className="py-2.5 pl-4"><input type="checkbox" aria-label={`Select bin ${b.name}`} checked={sel.has(b.id)} onChange={() => { const n = new Set(sel); n.has(b.id) ? n.delete(b.id) : n.add(b.id); setSel(n); }} /></td>
                  <td className="py-2.5 px-3 font-bold text-ink-primary">
                    <button onClick={() => toggleOpen(b.id)} className="inline-flex items-center gap-1 hover:text-primary"><M n={open === b.id ? "expand_less" : "expand_more"} c="text-sm text-ink-tertiary" />{b.name}</button>
                    {b.is_default && <span className="ml-2 px-1.5 py-0.5 rounded-full bg-emerald-50 text-emerald-700 border border-emerald-200/70 text-[10px] font-bold">Default</span>}
                  </td>
                  <td className="py-2.5 px-3 font-mono text-ink-secondary">{b.barcode || "—"}<span className="ml-1.5 text-[10px] text-ink-tertiary font-sans">{barcodeTypeLabel(b.barcode_type)}</span></td>
                  <td className="py-2.5 px-3 text-ink-secondary">{locName(b.location_id)}</td>
                  <td className="py-2.5 px-3 text-ink-tertiary">{[b.zone, b.shelf, b.bin_number].filter(Boolean).join(" / ") || "—"}</td>
                  <td className="py-2.5 px-3 text-right tabular-nums font-semibold text-ink-primary">{b.qty_on_hand ?? 0}</td>
                  <td className="py-2.5 px-3 text-right whitespace-nowrap">
                    <button onClick={() => setEdit({ ...b })} className="text-primary font-semibold mr-3">Edit</button>
                    <button onClick={() => makeDefault(b)} className="text-ink-secondary font-semibold mr-3">{b.is_default ? "Not default" : "Make default"}</button>
                    <button onClick={() => del(b)} className="text-rose-600 font-semibold">Delete</button>
                  </td>
                </tr>
                {open === b.id && (
                  <tr><td colSpan={7} className="px-6 pb-3 pt-1 bg-blue-50/30">
                    {contents == null ? <p className="text-ink-tertiary py-2">Loading…</p>
                      : contents.length === 0 ? <p className="text-ink-tertiary py-2">This bin is empty.</p>
                      : <table className="w-full text-xs"><tbody className="divide-y divide-slate-100">
                          {contents.map((c) => (
                            <tr key={c.part_id}>
                              <td className="py-1.5 font-semibold text-ink-primary">{c.name}{c.sku && <span className="ml-2 font-mono text-ink-tertiary">{c.sku}</span>}</td>
                              <td className="py-1.5 text-right tabular-nums w-20">{c.qty}{c.reserved ? <span className="text-ink-tertiary"> ({c.reserved} held)</span> : null}</td>
                              <td className="py-1.5 text-right w-24"><button onClick={() => setMove({ bin: b, item: c })} disabled={c.qty <= 0} className="text-primary font-semibold disabled:opacity-40">Move…</button></td>
                            </tr>))}
                        </tbody></table>}
                  </td></tr>
                )}
              </Fragment>
            ))}
          </tbody>
        </table>
      </div>

      {edit && (
        <div className="fixed inset-0 z-[60] flex items-center justify-center p-4" role="dialog" aria-modal="true" aria-label="Edit bin">
          <div aria-hidden="true" className="absolute inset-0 bg-ink-primary/40 backdrop-blur-[2px]" onClick={() => setEdit(null)} />
          <div className="relative w-full max-w-sm bg-white rounded-2xl shadow-drawer border border-slate-200 p-5 space-y-3">
            <h3 className="font-bold text-ink-primary">Edit bin</h3>
            <div><label className="block text-[11px] font-semibold text-ink-tertiary mb-1">Name</label><input autoFocus value={edit.name} onChange={(e) => setEdit({ ...edit, name: e.target.value })} aria-label="Bin name" className={`${fld} w-full`} />
              <p className="mt-1 text-[11px] text-ink-tertiary">Renaming keeps the label code, so labels already on the shelf still scan.</p></div>
            <div className="grid grid-cols-3 gap-2">
              <div><label className="block text-[11px] font-semibold text-ink-tertiary mb-1">Zone</label><input value={edit.zone || ""} onChange={(e) => setEdit({ ...edit, zone: e.target.value })} aria-label="Zone" className={`${fld} w-full`} /></div>
              <div><label className="block text-[11px] font-semibold text-ink-tertiary mb-1">Shelf</label><input value={edit.shelf || ""} onChange={(e) => setEdit({ ...edit, shelf: e.target.value })} aria-label="Shelf" className={`${fld} w-full`} /></div>
              <div><label className="block text-[11px] font-semibold text-ink-tertiary mb-1">Number</label><input value={edit.bin_number || ""} onChange={(e) => setEdit({ ...edit, bin_number: e.target.value })} aria-label="Bin number" className={`${fld} w-full`} /></div>
            </div>
            <div className="flex gap-2 pt-1">
              <button onClick={saveEdit} disabled={busy || !edit.name.trim()} className="px-4 py-2 rounded-lg bg-primary text-white font-semibold text-xs disabled:opacity-60">{busy ? "Saving…" : "Save"}</button>
              <button onClick={() => setEdit(null)} className="px-4 py-2 rounded-lg border border-slate-200 text-ink-secondary hover:bg-slate-100 font-semibold text-xs">Cancel</button>
            </div>
          </div>
        </div>
      )}
      {labels && <LabelPrintDialog title="Print bin labels" count={sel.size} noun={["bin", "bins"]} prefer="Bin label" typeChoice="own"
        render={(tid, copies, kind) => api.post<any>("/documents/render-bin-labels", { template_id: tid, bin_ids: [...sel], copies, barcode_type: kind || null })}
        onClose={() => setLabels(false)} />}
      {move && <MoveStockDialog partId={move.item.part_id} partName={move.item.name} locationId={move.bin.location_id} fromBinId={move.bin.id}
        fromLabel={move.bin.name} max={move.item.qty} onClose={() => setMove(null)} onMoved={() => { setMove(null); loadContents(move.bin.id); load(); }} />}
    </div>
  );
}
