import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";

// Shared catalog search (legacy /catalog/search) — type to find a service or
// product and pick it. Used by WO create, WO S&P, POS, invoice builder so lines
// carry a real ref_id + price instead of hand-typed text.
export type CatalogItem = { kind: "service" | "product" | "bundle"; ref_id: string; name: string; price?: number; category?: string; fits?: boolean };

export default function CatalogPicker({ type = "all", placeholder = "Search services & products…", onPick, deviceId, openOnFocus = false }: {
  type?: "all" | "service" | "product"; placeholder?: string; onPick: (i: CatalogItem) => void;
  deviceId?: string;   // 144jt: when set, products get a "Fits" badge for this device
  openOnFocus?: boolean;   // 144da: focus shows the full list to browse (dropdown-style), no typing needed
}) {
  const [q, setQ] = useState("");
  const [items, setItems] = useState<CatalogItem[]>([]);
  const [open, setOpen] = useState(false);
  const [browse, setBrowse] = useState(false);   // 144da: focused with openOnFocus → list the full catalog
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const qs = q.trim();
    if (qs.length < 2 && !(openOnFocus && browse)) { setItems([]); return; }
    const dev = deviceId ? `&device_id=${encodeURIComponent(deviceId)}` : "";
    const lim = openOnFocus ? "&limit=100" : "";   // 144da: browse mode lists the whole catalog
    const h = setTimeout(() => api.get<{ items: CatalogItem[] }>(`/catalog/search?q=${encodeURIComponent(qs)}&type=${type}${dev}${lim}`).then((r) => { setItems(r.items || []); setOpen(true); }).catch(() => setItems([])), qs ? 220 : 0);
    return () => clearTimeout(h);
  }, [q, type, deviceId, browse, openOnFocus]);
  return (
    <div className="relative" ref={box}>
      <input value={q} onChange={(e) => setQ(e.target.value)} onFocus={() => { if (openOnFocus) setBrowse(true); if (items.length) setOpen(true); }} placeholder={placeholder}
        className="glass-input w-full px-3 py-2 rounded-lg text-sm font-semibold text-ink-primary" />
      {open && items.length > 0 && (
        <>
          <div className="fixed inset-0 z-30" onClick={() => setOpen(false)} />
          <div className="absolute z-40 left-0 right-0 mt-1 bg-white border border-slate-200 rounded-xl shadow-clay-drawer p-1.5 max-h-64 overflow-y-auto">
            {items.map((i) => (
              <button key={i.kind + i.ref_id} onClick={() => { onPick(i); setQ(""); setItems([]); setOpen(false); }}
                className="flex items-center gap-2 w-full text-left px-2.5 py-2 rounded-lg hover:bg-blue-50/60 text-sm">
                <span className={`text-[9px] uppercase font-bold px-1.5 py-0.5 rounded ${i.kind === "service" ? "bg-emerald-100 text-emerald-700" : (i.kind as string) === "bundle" ? "bg-violet-100 text-violet-700" : "bg-blue-100 text-primary"}`}>{i.kind === "service" ? "svc" : (i.kind as string) === "bundle" ? "kit" : "prod"}</span>
                <span className="font-semibold text-ink-primary flex-1 truncate">{i.name}</span>
                {i.fits && <span className="text-[9px] uppercase font-bold px-1.5 py-0.5 rounded bg-emerald-100 text-emerald-700" title="Fits this device">Fits</span>}
                <span className="font-mono text-ink-tertiary text-xs">{(i.price ?? 0).toLocaleString()}</span>
              </button>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
