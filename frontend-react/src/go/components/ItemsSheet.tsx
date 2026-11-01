// Add services and parts to a job. Tap a result to add it (qty 1, catalogue price); the sheet stays
// open so several can go on in a row. Online only: a product line takes stock the moment it's added,
// so a queued replay could take it twice.
import { useEffect, useRef, useState } from "react";
import { Check, Loader2, Package, PackageSearch, ScanBarcode, Search, Wrench, X } from "lucide-react";
import { api } from "../../api/client";
import { useMe } from "../../context/Me";
import { Sheet } from "./ui";
import { cameraSupported, haptic, startScanner, type Stop } from "../lib/scanner";
import { useToast } from "../lib/useGo";
import { money } from "../lib/pay";

type Hit = { kind: "service" | "product" | "bundle"; ref_id: string; name: string; price?: number; available?: number | null; sku?: string | null; fits?: boolean };

export default function ItemsSheet({ open, onClose, orderId, deviceId, onAdded, onRequestPart }: {
  open: boolean; onClose: () => void; orderId: string; deviceId?: string | null; onAdded: () => void;
  /** "Part not here?" — ask the desk to order one (named from what was searched). */
  onRequestPart?: (name: string) => void;
}) {
  const { can, me } = useMe();
  const fmt = (n?: number | null) => money(me?.currency, n);
  const toast = useToast();
  const mayService = can("can_add_services");
  const mayPart = can("can_add_products");
  const [type, setType] = useState<"service" | "product">(mayService ? "service" : "product");
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<Hit[] | null>(null);
  const [adding, setAdding] = useState<string | null>(null);
  const [added, setAdded] = useState<Record<string, number>>({});

  const [scanning, setScanning] = useState(false);
  const video = useRef<HTMLVideoElement>(null);
  const stopRef = useRef<Stop | null>(null);
  useEffect(() => { if (open) { setQ(""); setAdded({}); } else setScanning(false); }, [open]);
  // Scan a part's barcode/SKU label and put it straight on the job.
  useEffect(() => {
    if (!scanning || !video.current) return;
    let alive = true;
    startScanner(video.current, async (code) => {
      stopRef.current = null;
      if (!alive) return;
      setScanning(false);
      try {
        const r = await api.get<{ product: { id: string; name: string; sell_price?: number; repair_price?: number; qty_available?: number } }>(`/inventory/scan?code=${encodeURIComponent(code)}`);
        const p = r.product;
        await add({ kind: "product", ref_id: p.id, name: p.name, price: p.repair_price || p.sell_price || 0, available: p.qty_available ?? null });
      } catch (e: any) {
        toast(e?.status === 404 ? { title: "No part matches that code", body: code, tone: "warn" } : { title: "Couldn't look that up", body: e?.message, tone: "error" });
      }
    }).then((stop) => { if (alive) stopRef.current = stop; else stop(); })
      .catch(() => { setScanning(false); toast({ title: "Camera unavailable", body: "Allow camera access, or type the SKU.", tone: "warn" }); });
    return () => { alive = false; stopRef.current?.(); stopRef.current = null; };
  }, [scanning]);
  useEffect(() => {
    if (!open) return;
    setHits(null);
    const t = setTimeout(() => {
      const qs = new URLSearchParams({ q: q.trim(), type, limit: "20", ...(deviceId ? { device_id: deviceId } : {}) });
      api.get<{ items?: Hit[] } | Hit[]>(`/catalog/search?${qs}`)
        .then((r) => setHits(Array.isArray(r) ? r : r.items || [])).catch(() => setHits([]));
    }, 200);
    return () => clearTimeout(t);
  }, [open, q, type, deviceId]);

  async function add(h: Hit) {
    if (adding) return;
    if (!navigator.onLine) { toast({ title: "You're offline", body: "Adding items needs a connection (it takes stock).", tone: "warn" }); return; }
    setAdding(h.ref_id);
    try {
      await api.post(`/orders/${encodeURIComponent(orderId)}/lines`, {
        description: h.name, kind: h.kind === "bundle" ? "bundle" : h.kind, line_type: h.kind === "service" ? "labour" : "part",
        ref_id: h.ref_id, qty: 1, unit_price: h.price ?? 0,
      });
      haptic();
      if (h.kind === "product" && h.available != null && h.available <= 0) toast({ title: `${h.name} added`, body: "It's out of stock, so it shows as waiting for stock until it's ordered and received." });
      setAdded((a) => ({ ...a, [h.ref_id]: (a[h.ref_id] || 0) + 1 }));
      onAdded();
    } catch (e: any) {
      toast({ title: `Couldn't add ${h.name}`, body: e?.message, tone: "error" });
    } finally { setAdding(null); }
  }

  const count = Object.values(added).reduce((a, b) => a + b, 0);
  return (
    <Sheet open={open} onClose={onClose} title={count ? `Added ${count}` : "Add to job"}>
      <div className="px-2">
        {mayService && mayPart && (
          <div className="mb-3 grid grid-cols-2 rounded-xl bg-slate-100 p-1">
            {(["service", "product"] as const).map((t) => (
              <button key={t} onClick={() => setType(t)} aria-pressed={type === t}
                className={`min-h-[2.75rem] rounded-lg text-go-body font-semibold inline-flex items-center justify-center gap-1.5 ${type === t ? "bg-white text-ink-primary" : "text-ink-secondary"}`}>
                {t === "service" ? <Wrench size={16} /> : <Package size={16} />}{t === "service" ? "Service" : "Part"}
              </button>
            ))}
          </div>
        )}
        <div className="mb-2 flex gap-2">
          <div className="relative flex-1">
            <Search size={18} className="absolute left-4 top-1/2 -translate-y-1/2 text-ink-tertiary" />
            <input value={q} onChange={(e) => setQ(e.target.value)} placeholder={type === "service" ? "Search services" : "Search parts, SKU, barcode"} enterKeyHint="search" aria-label="Search the catalogue"
              className="w-full min-h-[3rem] rounded-xl border border-slate-200 bg-white pl-11 pr-4 text-go-body text-ink-primary placeholder:text-ink-placeholder focus:outline-none focus:border-primary" />
          </div>
          {type === "product" && cameraSupported() && (
            <button onClick={() => setScanning((v) => !v)} aria-label={scanning ? "Stop scanning" : "Scan a part's barcode"} aria-pressed={scanning}
              className={`h-12 w-12 shrink-0 rounded-xl border inline-flex items-center justify-center ${scanning ? "border-primary bg-blue-50 text-primary" : "border-slate-200 bg-white text-ink-primary"}`}>
              {scanning ? <X size={20} /> : <ScanBarcode size={22} />}
            </button>
          )}
        </div>
        {scanning && (
          <div className="relative mb-3 aspect-[4/3] w-full overflow-hidden rounded-xl bg-[#0b1020]">
            <video ref={video} className="h-full w-full object-cover" playsInline muted />
            <div className="pointer-events-none absolute inset-x-8 top-1/2 h-0.5 -translate-y-1/2 bg-rose-500/80" />
            <p className="absolute inset-x-0 bottom-2 text-center text-go-sub text-white/90">Point at the part's barcode</p>
          </div>
        )}
        {!mayService && !mayPart && <p className="px-2 py-4 text-ink-secondary">Your role can't add items to jobs.</p>}
        {hits === null ? <div className="py-6 flex justify-center text-ink-tertiary"><Loader2 className="animate-spin" /></div> : (
          <div className="divide-y-[0.5px] divide-slate-200">
            {hits.map((h) => {
              const n = added[h.ref_id] || 0;
              const out = h.kind === "product" && h.available != null && h.available <= 0;
              return (
                <button key={`${h.kind}-${h.ref_id}`} onClick={() => add(h)} disabled={!!adding}
                  className="w-full min-h-[3.5rem] flex items-center gap-3 px-2 text-left active:bg-slate-50">
                  <span className="min-w-0 flex-1">
                    <span className="block font-medium text-ink-primary truncate">{h.name}</span>
                    <span className="block text-go-sub text-ink-secondary">
                      {h.kind === "product" ? (out ? <span className="text-amber-700">Out of stock</span> : h.available != null ? `${h.available} in stock` : h.sku || "") : "Service"}
                      {h.fits ? " · fits this device" : ""}
                    </span>
                  </span>
                  <span className="tabular-nums text-ink-primary">{fmt(h.price)}</span>
                  <span className="w-8 inline-flex justify-center">
                    {adding === h.ref_id ? <Loader2 size={18} className="animate-spin text-ink-tertiary" />
                      : n ? <span className="inline-flex items-center gap-0.5 text-go-sub font-semibold text-emerald-700"><Check size={16} />{n > 1 ? n : ""}</span> : null}
                  </span>
                </button>
              );
            })}
            {!hits.length && <p className="px-2 py-6 text-center text-ink-secondary">{q ? "Nothing matches" : "Nothing in the catalogue yet"}</p>}
          </div>
        )}
        {type === "product" && onRequestPart && (
          <button onClick={() => onRequestPart(q.trim())} className="mt-2 w-full min-h-[3.25rem] rounded-xl border border-dashed border-slate-300 inline-flex items-center justify-center gap-2 font-semibold text-primary active:bg-slate-50">
            <PackageSearch size={18} />Part not here? Ask for it to be ordered
          </button>
        )}
      </div>
    </Sheet>
  );
}
