import { useState } from "react";
import { M } from "./Shell";
import ScanButton from "./ScanButton";
import { api } from "../api/client";

export type ScannedBin = { id: string; name: string; location_id?: string; warehouse_id?: string | null };

/**
 * ii144c — "Scan bin": scan (or type) a bin label to say which bin the next lines go to or come
 * from. Used where stock is put away, moved, received or counted. A code that isn't a bin is
 * reported, not guessed at.
 */
export default function BinScanField({ onBin, locationId, current, onClear, label = "Bin" }: {
  onBin: (b: ScannedBin) => void; locationId?: string; current?: ScannedBin | null; onClear?: () => void; label?: string;
}) {
  const [code, setCode] = useState("");
  const [err, setErr] = useState<string | null>(null);
  async function resolve(raw?: string) {
    const c = (raw ?? code).trim(); if (!c) return;
    setErr(null);
    try {
      const r = await api.post<any>("/scan/resolve", { code: c });
      if (r?.type !== "bin") { setErr(`“${c}” isn't a bin label`); return; }
      if (locationId && r.location_id && r.location_id !== locationId) { setErr(`Bin ${r.label} is at another location`); return; }
      onBin({ id: r.id, name: r.label, location_id: r.location_id, warehouse_id: r.warehouse_id });
      setCode("");
    } catch (e: any) { setErr(e?.message || "Couldn't read that code"); }
  }
  return (
    <div>
      <div className="flex items-center gap-2">
        {current ? (
          <span className="inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg bg-blue-50 border border-blue-100 text-primary text-xs font-bold">
            <M n="warehouse" c="text-sm" />{label}: {current.name}
            {onClear && <button aria-label="Clear bin" onClick={onClear} className="ml-1 text-primary/70 hover:text-primary"><M n="close" c="text-sm" /></button>}
          </span>
        ) : null}
        <input value={code} onChange={(e) => setCode(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); resolve(); } }}
          aria-label="Scan a bin label" placeholder={current ? "Scan another bin" : "Scan a bin label"}
          className="flex-1 min-w-0 bg-white border border-slate-200 rounded-lg text-xs px-2.5 py-1.5" />
        <ScanButton title="Scan a bin with the camera" onScan={(c) => resolve(c)} />
      </div>
      {err && <div className="mt-1 text-[11px] text-rose-600">{err}</div>}
    </div>
  );
}
