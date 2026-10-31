import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import { money } from "../pages/orders/types";
import { M } from "./Shell";

// Shared catalog search (legacy /catalog/search) — type to find a service or
// product and pick it. Used by WO create, WO S&P, POS, invoice builder so lines
// carry a real ref_id + price instead of hand-typed text.
export type CatalogItem = {
  kind: "service" | "product" | "bundle"; ref_id: string; name: string; price?: number; category?: string; fits?: boolean;
  available?: number | null; sku?: string | null; components?: number;
};

const ICON = { service: "build", product: "inventory_2", bundle: "package_2" } as const;
const KIND = { service: "Service", product: "Product", bundle: "Kit" } as const;

// How much is on the shelf, so nobody adds a part that isn't there without knowing.
function Stock({ n }: { n: number }) {
  if (n <= 0) return <span className="text-[11px] font-medium text-rose-600">Out of stock</span>;
  if (n <= 2) return <span className="text-[11px] font-medium text-amber-700">Only {n} left</span>;
  return <span className="text-[11px] text-ink-tertiary">{n} in stock</span>;
}

// Browsing (nothing typed): group by category so the catalog reads like a menu.
function groupByCategory(items: CatalogItem[]) {
  const groups: { title: string; items: CatalogItem[] }[] = [];
  for (const i of items) {
    const title = i.kind === "bundle" ? "Kits" : i.category?.trim() || (i.kind === "service" ? "Other services" : "Other products");
    let g = groups.find((x) => x.title === title);
    if (!g) groups.push((g = { title, items: [] }));
    g.items.push(i);
  }
  // Services, then products, then kits; categories A–Z with the uncategorised "Other …" last.
  const rank = (g: { title: string; items: CatalogItem[] }) => ["service", "product", "bundle"].indexOf(g.items[0].kind) * 2 + (g.title.startsWith("Other ") ? 1 : 0);
  return groups.sort((a, b) => rank(a) - rank(b) || a.title.localeCompare(b.title));
}

export default function CatalogPicker({ type = "all", placeholder = "Search services & products…", onPick, deviceId, openOnFocus = true }: {
  type?: "all" | "service" | "product" | "bundle"; placeholder?: string; onPick: (i: CatalogItem) => void;
  deviceId?: string;   // 144jt: when set, products get a "Fits" badge for this device
  openOnFocus?: boolean;   // focus shows the catalog to browse (dropdown-style), no typing needed
}) {
  const [q, setQ] = useState("");
  const [items, setItems] = useState<CatalogItem[]>([]);
  const [open, setOpen] = useState(false);
  const [browse, setBrowse] = useState(false);   // focused with openOnFocus → list the catalog
  const [loaded, setLoaded] = useState(false);   // a search finished (so "no matches" is real)
  const box = useRef<HTMLDivElement>(null);
  // Browsing: "By category" (collapsible groups) or "All items" (one A–Z list). Remembered per browser.
  const [view, setView] = useState<"category" | "all">(() => { try { return localStorage.getItem("ros_catalog_view") === "all" ? "all" : "category"; } catch { return "category"; } });
  const [openCats, setOpenCats] = useState<Set<string>>(new Set());
  const pickView = (v: "category" | "all") => { setView(v); try { localStorage.setItem("ros_catalog_view", v); } catch { /* ignore */ } };
  const toggleCat = (t: string) => setOpenCats((s) => { const n = new Set(s); if (n.has(t)) n.delete(t); else n.add(t); return n; });
  useEffect(() => {
    const qs = q.trim();
    if (!qs && !(openOnFocus && browse)) { setItems([]); setLoaded(false); return; }
    const dev = deviceId ? `&device_id=${encodeURIComponent(deviceId)}` : "";
    const lim = qs ? "&limit=30" : "&limit=100";   // empty box: browse the catalog
    let live = true;   // a slower, older request must never replace newer results
    const h = setTimeout(() => api.get<{ items: CatalogItem[] }>(`/catalog/search?q=${encodeURIComponent(qs)}&type=${type}${dev}${lim}`)
      .then((r) => { if (!live) return; setItems(r.items || []); setLoaded(true); setOpen(true); }).catch(() => { if (live) setItems([]); }), qs ? 200 : 0);
    return () => { live = false; clearTimeout(h); };
  }, [q, type, deviceId, browse, openOnFocus]);
  const flat = !!q.trim() || view === "all";
  const groups = q.trim() ? [{ title: "", items }]
    : view === "all" ? [{ title: "", items: [...items].sort((a, b) => a.name.localeCompare(b.name)) }]
    : groupByCategory(items);
  const allOpen = groups.every((g) => openCats.has(g.title));
  return (
    <div className="relative" ref={box}>
      <input value={q} onChange={(e) => { setQ(e.target.value); setOpen(true); }} placeholder={placeholder} role="combobox" aria-expanded={open} autoComplete="off"
        onFocus={() => { if (openOnFocus) setBrowse(true); setOpen(true); }} onClick={() => { if (openOnFocus) setBrowse(true); setOpen(true); }}
        onKeyDown={(e) => { if (e.key === "Escape") setOpen(false); }}
        className="glass-input w-full px-3 py-2 rounded-lg text-sm font-semibold text-ink-primary" />
      {open && (items.length > 0 || (loaded && q.trim())) && (
        <>
          <div className="fixed inset-0 z-30" aria-hidden="true" onClick={() => setOpen(false)} />
          <div role="listbox" className="absolute z-40 left-0 right-0 mt-1.5 bg-white border border-slate-200 rounded-2xl shadow-clay-drawer p-1.5 max-h-80 overflow-y-auto">
            {items.length === 0 && <div className="px-3 py-3 text-xs text-ink-tertiary">Nothing matches "{q.trim()}"</div>}
            {!q.trim() && items.length > 0 && (
              <div className="sticky -top-1.5 z-[2] -mx-1.5 -mt-1.5 mb-1 flex items-center gap-2 border-b border-slate-100 bg-white px-3 py-2">
                <span role="tablist" aria-label="Show" className="inline-flex items-center gap-0.5 rounded-lg bg-slate-100 p-0.5">
                  {([["category", "By category"], ["all", "All items"]] as const).map(([v, t]) => (
                    <button key={v} type="button" role="tab" aria-selected={view === v} onClick={() => pickView(v)}
                      className={`h-7 rounded-md px-2.5 text-xs font-medium transition ${view === v ? "bg-white text-ink-primary shadow-sm ring-1 ring-slate-200/70" : "text-ink-secondary hover:text-ink-primary"}`}>{t}</button>
                  ))}
                </span>
                {view === "category" && groups.length > 1 && (
                  <button type="button" onClick={() => setOpenCats(allOpen ? new Set() : new Set(groups.map((g) => g.title)))}
                    className="ml-auto text-xs font-medium text-ink-secondary hover:text-ink-primary">{allOpen ? "Collapse all" : "Expand all"}</button>
                )}
              </div>
            )}
            {groups.map((g, gi) => {
              const isOpen = !g.title || groups.length === 1 || openCats.has(g.title);
              const kind = g.items[0]?.kind;
              const kindLabel = g.title && type === "all" && kind !== groups[gi - 1]?.items[0]?.kind ? ({ service: "Services", product: "Products", bundle: "" } as const)[kind] : "";
              return (
              <div key={g.title || "results"}>
                {kindLabel && <div className={`px-2.5 pb-1 text-[11px] font-semibold uppercase tracking-wide text-ink-tertiary ${gi ? "mt-1.5 border-t border-slate-100 pt-3" : "pt-1"}`}>{kindLabel}</div>}
                {g.title && (
                  <button type="button" aria-expanded={isOpen} onClick={() => toggleCat(g.title)}
                    className="flex w-full items-center gap-2 rounded-xl px-2.5 py-2 text-left hover:bg-slate-50">
                    <M n="expand_more" c={`text-base text-ink-tertiary transition-transform ${isOpen ? "" : "-rotate-90"}`} />
                    <span className="flex-1 text-[13px] font-semibold text-ink-primary">{g.title}</span>
                    <span className="rounded-full bg-slate-100 px-2 py-px text-[11px] font-medium tabular-nums text-ink-secondary">{g.items.length}</span>
                  </button>
                )}
                {isOpen && g.items.map((i) => {
                  const sub = i.kind === "bundle"
                    ? [KIND.bundle, i.components ? `${i.components} items` : null]
                    : [flat || !i.category ? (i.category || KIND[i.kind]) : KIND[i.kind], i.sku];
                  return (
                    <button key={i.kind + i.ref_id} role="option" aria-selected={false} onClick={() => { onPick(i); setQ(""); setItems([]); setBrowse(false); setOpen(false); }}
                      className={`flex items-center gap-3 w-full text-left ${g.title ? "pl-8 pr-2.5" : "px-2.5"} py-2 rounded-xl hover:bg-slate-50 focus-visible:bg-slate-50 outline-none`}>
                      <span className="grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-slate-100 text-ink-secondary"><M n={ICON[i.kind]} c="text-base" /></span>
                      <span className="min-w-0 flex-1">
                        <span className="flex items-center gap-1.5">
                          <span className="truncate text-sm font-medium text-ink-primary">{i.name}</span>
                          {i.fits && <span className="shrink-0 rounded-full bg-emerald-50 px-1.5 py-px text-[11px] font-semibold text-emerald-700" title="Fits this device">Fits</span>}
                        </span>
                        <span className="block truncate text-xs text-ink-tertiary">{sub.filter(Boolean).join(" · ")}</span>
                      </span>
                      <span className="shrink-0 text-right leading-tight">
                        {i.price != null && <span className="block text-sm font-medium tabular-nums text-ink-primary">{money(i.price)}</span>}
                        {i.kind === "product" && i.available != null && <Stock n={i.available} />}
                      </span>
                    </button>
                  );
                })}
              </div>
              );
            })}
          </div>
        </>
      )}
    </div>
  );
}
