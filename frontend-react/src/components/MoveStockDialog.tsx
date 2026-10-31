import { useEffect, useState } from "react";
import { M } from "./Shell";
import BinScanField from "./BinScanField";
import { api } from "../api/client";

type Bin = { id: string; name: string; location_id: string };

/**
 * ii144c — move some of a product from one bin to another at the same location (or put unbinned
 * stock away into a bin). The destination can be picked or scanned.
 */
export default function MoveStockDialog({ partId, partName, locationId, fromBinId, fromLabel, max, onClose, onMoved }: {
  partId: string; partName: string; locationId: string; fromBinId: string | null; fromLabel: string; max: number;
  onClose: () => void; onMoved: () => void;
}) {
  const [bins, setBins] = useState<Bin[]>([]);
  const [to, setTo] = useState("");
  const [qty, setQty] = useState(Math.min(1, max) || 1);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    api.get<any>(`/bins?location_id=${encodeURIComponent(locationId)}`).then((r) => {
      const it = (r.items || []).filter((b: Bin) => b.id !== fromBinId && b.location_id === locationId);
      setBins(it); setTo((c) => c || it[0]?.id || "");
    }).catch(() => setBins([]));
  }, [locationId, fromBinId]);
  async function move() {
    if (!to) { setErr("Pick the bin to move it to."); return; }
    if (!(qty > 0) || qty > max) { setErr(`Move between 1 and ${max}.`); return; }
    setBusy(true); setErr(null);
    try {
      await api.post("/bins/transfer", { part_id: partId, source_bin_id: fromBinId, destination_bin_id: to, qty, location_id: locationId });
      onMoved();
    } catch (e: any) { setErr(e?.message || "Couldn't move the stock"); setBusy(false); }
  }
  const fld = "w-full bg-white border border-slate-200 rounded-lg text-sm px-2.5 py-2";
  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center p-4" role="dialog" aria-modal="true" aria-label="Move stock">
      <div className="absolute inset-0 bg-ink-primary/40" aria-hidden="true" onClick={onClose} />
      <div className="relative w-full max-w-sm bg-white rounded-2xl shadow-drawer border border-slate-200 p-5 space-y-3">
        <div className="flex items-center justify-between"><h3 className="font-bold text-ink-primary">Move {partName}</h3><button aria-label="Close" onClick={onClose} className="w-7 h-7 rounded-lg hover:bg-slate-100 grid place-items-center text-ink-tertiary"><M n="close" c="text-base" /></button></div>
        <p className="text-xs text-ink-tertiary">From <b className="text-ink-secondary">{fromLabel}</b> · {max} here</p>
        <div><label className="block text-[11px] font-semibold text-ink-tertiary mb-1">To bin</label>
          {bins.length === 0
            ? <p className="text-xs text-ink-tertiary">No other bins at this location — add one in Inventory › Bins.</p>
            : <select value={to} onChange={(e) => setTo(e.target.value)} aria-label="Destination bin" className={fld}>{bins.map((b) => <option key={b.id} value={b.id}>{b.name}</option>)}</select>}
        </div>
        <BinScanField locationId={locationId} onBin={(b) => { if (b.id === fromBinId) setErr("That's the bin it's in now."); else setTo(b.id); }} />
        <div><label className="block text-[11px] font-semibold text-ink-tertiary mb-1">Quantity</label>
          <input type="number" min={1} max={max} value={qty} onChange={(e) => setQty(Number(e.target.value))} aria-label="Quantity to move" className={fld} /></div>
        {err && <div role="alert" className="rounded-lg bg-rose-50 border border-rose-200 text-rose-700 text-xs px-3 py-2">{err}</div>}
        <div className="flex gap-2 pt-1">
          <button onClick={move} disabled={busy || !to} className="px-4 py-2 rounded-lg bg-primary text-white font-semibold text-xs hover:bg-primary-container disabled:opacity-60 flex items-center gap-1"><M n="swap_horiz" c="text-[15px]" />{busy ? "Moving…" : "Move"}</button>
          <button onClick={onClose} className="px-4 py-2 rounded-lg border border-slate-200 text-ink-secondary hover:bg-slate-100 font-semibold text-xs">Cancel</button>
        </div>
      </div>
    </div>
  );
}
