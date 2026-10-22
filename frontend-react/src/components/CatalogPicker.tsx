import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import { money } from "../pages/orders/types";

// Shared catalog search (legacy /catalog/search) — type to find a service or
// product and pick it. Used by WO create, WO S&P, POS, invoice builder so lines
// carry a real ref_id + price instead of hand-typed text.
export type CatalogItem = { kind: "service" | "product" | "bundle"; ref_id: string; name: string; price?: number; category?: string; fits?: boolean };

export default function CatalogPicker({ type = "all", placeholder = "Search services & products…", onPick, deviceId, openOnFocus = true }: {
  type?: "all" | "service" | "product"; placeholder?: string; onPick: (i: CatalogItem) => void;
  deviceId?: string;   // 144jt: when set, products get a "Fits" badge for this device
  openOnFocus?: boolean;   // focus shows the catalog to browse (dropdown-style), no typing needed
}) {
  const [q, setQ] = useState("");
  const [items, setItems] = useState<CatalogItem[]>([]);
  const [open, setOpen] = useState(false);
  const [browse, setBrowse] = useState(false);   // focused with openOnFocus → list the catalog
  const [loaded, setLoaded] = useState(false);   // a search finished (so "no matches" is real)
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const qs = q.trim();
    if (!qs && !(openOnFocus && browse)) { setItems([]); setLoaded(false); return; }
    const dev = deviceId ? `&device_id=${encodeURIComponent(deviceId)}` : "";
    const lim = qs ? "&limit=30" : "&limit=100";   // empty box: browse the catalog
    const h = setTimeout(() => api.get<{ items: CatalogItem[] }>(`/catalog/search?q=${encodeURIComponent(qs)}&type=${type}${dev}${lim}`)
      .then((r) => { setItems(r.items || []); setLoaded(true); setOpen(true); }).catch(() => setItems([])), qs ? 200 : 0);
    return () => clearTimeout(h);
  }, [q, type, deviceId, browse, openOnFocus]);
  return (
    <div className="relative" ref={box}>
      <input value={q} onChange={(e) => { setQ(e.target.value); setOpen(true); }} placeholder={placeholder} role="combobox" aria-expanded={open} autoComplete="off"
        onFocus={() => { if (openOnFocus) setBrowse(true); setOpen(true); }} onClick={() => setOpen(true)}
        onKeyDown={(e) => { if (e.key === "Escape") setOpen(false); }}
        className="glass-input w-full px-3 py-2 rounded-lg text-sm font-semibold text-ink-primary" />
      {open && (items.length > 0 || (loaded && q.trim())) && (
        <>
          <div className="fixed inset-0 z-30" onClick={() => setOpen(false)} />
          <div className="absolute z-40 left-0 right-0 mt-1 bg-white border border-slate-200 rounded-xl shadow-clay-drawer p-1.5 max-h-64 overflow-y-auto">
            {items.length === 0 && <div className="px-2.5 py-2 text-xs text-ink-tertiary">Nothing matches "{q.trim()}"</div>}
            {items.map((i) => (
              <button key={i.kind + i.ref_id} onClick={() => { onPick(i); setQ(""); setItems([]); setBrowse(false); setOpen(false); }}
                className="flex items-center gap-2 w-full text-left px-2.5 py-2 rounded-lg hover:bg-blue-50/60 text-sm">
                <span className={`text-[11px]  font-bold px-1.5 py-0.5 rounded ${i.kind === "service" ? "bg-emerald-100 text-emerald-700" : (i.kind as string) === "bundle" ? "bg-violet-100 text-violet-700" : "bg-blue-100 text-primary"}`}>{i.kind === "service" ? "Service" : (i.kind as string) === "bundle" ? "Job kit" : "Product"}</span>
                <span className="font-semibold text-ink-primary flex-1 truncate">{i.name}</span>
                {i.fits && <span className="text-[11px] font-bold px-1.5 py-0.5 rounded bg-emerald-100 text-emerald-700" title="Fits this device">Fits</span>}
                <span className="tabular-nums text-ink-tertiary text-xs">{money(i.price ?? 0)}</span>
              </button>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
