import { useEffect, useRef, useState } from "react";
import { M } from "./Shell";
import { api } from "../api/client";
import ScanButton from "./ScanButton";
import { loadFailed } from "../lib/notice";

/**
 * Phase 2 — hands-free stock posting. Pick a location + movement type once, then
 * scan parts one after another (USB keyboard-wedge, Enter per scan); each posts
 * to /inventory/scan-adjust and drops into a running log. No part-id lookups.
 */
type Loc = { id: string; name: string };
type Row = { code: string; ok: boolean; msg: string };

export default function ScanStockPanel({ onClose, onPosted }: { onClose: () => void; onPosted?: () => void }) {
  const [locs, setLocs] = useState<Loc[]>([]);
  const [loc, setLoc] = useState("");
  const [mode, setMode] = useState<"receive" | "writeoff" | "adjust">("receive");
  const [qty, setQty] = useState(1);
  const [code, setCode] = useState("");
  const [log, setLog] = useState<Row[]>([]);
  const ref = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    api.get<any>("/staff/locations").then((r) => {
      const items = r.items || r || [];
      setLocs(items); if (items[0]) setLoc(items[0].id);
    }).catch(loadFailed("locations"));
    ref.current?.focus();
  }, []);

  async function post(scanned?: string) {
    const c = (scanned ?? code).trim();
    if (!c || !loc) return;
    try {
      const r = await api.post<any>("/inventory/scan-adjust", { code: c, location_id: loc, qty, movement_type: mode });
      setLog((l) => [{ code: c, ok: true, msg: `${r.part_name} → ${r.qty_available} on hand` }, ...l].slice(0, 40));
      onPosted?.();
    } catch (e: any) {
      setLog((l) => [{ code: c, ok: false, msg: e.message || "No match" }, ...l].slice(0, 40));
    }
    setCode(""); ref.current?.focus();
  }

  const verb = { receive: "Receive", writeoff: "Write off", adjust: "Count / adjust" }[mode];

  return (
    <div className="fixed inset-0 z-[70] flex items-center justify-center bg-ink-primary/45 backdrop-blur-[2px]" role="presentation" onClick={onClose}>
      <div className="w-[380px] bg-raised rounded-2xl shadow-clay-card border border-slate-200/80 overflow-hidden" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between px-4 py-3 border-b border-slate-100">
          <span className="text-sm font-bold text-ink-primary flex items-center gap-2"><M n="qr_code_scanner" c="text-base text-primary" />Scan to stock</span>
          <button aria-label="Close" onClick={onClose} className="w-7 h-7 rounded-lg hover:bg-slate-100 grid place-items-center text-ink-tertiary"><M n="close" c="text-base" /></button>
        </div>
        <div className="p-4 space-y-3">
          <div className="grid grid-cols-2 gap-2">
            <div><label className="block text-[11px] font-bold text-ink-tertiary mb-1">Location</label>
              <select value={loc} onChange={(e) => setLoc(e.target.value)} className="glass-input rounded-lg px-2 py-2 text-sm w-full">{locs.map((l) => <option key={l.id} value={l.id}>{l.name}</option>)}</select></div>
            <div><label className="block text-[11px] font-bold text-ink-tertiary mb-1">Action</label>
              <select value={mode} onChange={(e) => setMode(e.target.value as any)} className="glass-input rounded-lg px-2 py-2 text-sm w-full">
                <option value="receive">Receive</option><option value="writeoff">Write off</option><option value="adjust">Count / adjust</option></select></div>
          </div>
          <div className="flex items-end gap-2">
            <div className="w-20"><label className="block text-[11px] font-bold text-ink-tertiary mb-1">Qty</label>
              <input type="number" min={1} value={qty} onChange={(e) => setQty(Math.max(1, Number(e.target.value) || 1))} className="glass-input rounded-lg px-2 py-2 text-sm w-full font-mono" /></div>
            <div className="flex-1"><label className="block text-[11px] font-bold text-ink-tertiary mb-1">Scan / type code</label>
              <input ref={ref} value={code} onChange={(e) => setCode(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") post(); }} placeholder="SKU / barcode" className="glass-input rounded-lg px-3 py-2 text-sm w-full font-mono" /></div>
            <ScanButton title="Scan with camera" onScan={(c) => { setCode(c); post(c); }} />
          </div>
          <div className="text-[11px] text-ink-tertiary">{verb} {qty} to the selected location per scan. Enter posts each one.</div>
          {log.length > 0 && (
            <div className="max-h-52 overflow-y-auto rounded-lg border border-slate-200/70 divide-y divide-slate-100">
              {log.map((r, i) => (
                <div key={i} className="flex items-center gap-2 px-2.5 py-1.5 text-[11px]">
                  <M n={r.ok ? "check_circle" : "error"} c={`text-sm ${r.ok ? "text-emerald-500" : "text-rose-500"}`} />
                  <span className="font-mono text-ink-secondary">{r.code}</span>
                  <span className={`ml-auto ${r.ok ? "text-ink-primary" : "text-rose-600"}`}>{r.msg}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
