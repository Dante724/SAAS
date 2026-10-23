// Quote at the counter: pick services/parts from the catalogue, or type a price, before the job
// exists. Nothing is saved here; the lines go in with the new job (parts take stock then, except
// on an estimate).
import { useEffect, useState } from "react";
import { Loader2, Package, Plus, Search, Wrench } from "lucide-react";
import { api } from "../../api/client";
import { useMe } from "../../context/Me";
import { PrimaryButton, Sheet } from "./ui";
import { money } from "../lib/pay";
import type { QuoteLine } from "../lib/newJob";

type Hit = { kind: "service" | "product" | "bundle"; ref_id: string; name: string; price?: number; available?: number | null };
const field = "w-full min-h-[3rem] rounded-xl border border-slate-200 bg-white px-4 text-go-body text-ink-primary placeholder:text-ink-placeholder focus:outline-none focus:border-primary";

export default function QuoteSheet({ open, onClose, onAdd, deviceId }: {
  open: boolean; onClose: () => void; onAdd: (l: QuoteLine) => void; deviceId?: string | null;
}) {
  const { can, me } = useMe();
  const fmt = (n?: number | null) => money(me?.currency, n);
  const mayService = can("can_add_services");
  const mayPart = can("can_add_products");
  const [type, setType] = useState<"service" | "product">(mayService ? "service" : "product");
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<Hit[] | null>(null);
  const [desc, setDesc] = useState("");
  const [price, setPrice] = useState("");
  useEffect(() => { if (open) { setQ(""); setDesc(""); setPrice(""); } }, [open]);
  useEffect(() => {
    if (!open || (!mayService && !mayPart)) return;
    setHits(null);
    const t = setTimeout(() => {
      const qs = new URLSearchParams({ q: q.trim(), type, limit: "15", ...(deviceId ? { device_id: deviceId } : {}) });
      api.get<{ items?: Hit[] } | Hit[]>(`/catalog/search?${qs}`).then((r) => setHits(Array.isArray(r) ? r : r.items || [])).catch(() => setHits([]));
    }, 200);
    return () => clearTimeout(t);
  }, [open, q, type, deviceId]);

  const amount = Number(price.replace(",", "."));
  const okCustom = desc.trim() && price.trim() && Number.isFinite(amount) && amount >= 0;

  return (
    <Sheet open={open} onClose={onClose} title="Add to the quote" wide>
      <div className="px-2">
        {(mayService || mayPart) && (
          <>
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
            <div className="relative mb-2">
              <Search size={18} className="absolute left-4 top-1/2 -translate-y-1/2 text-ink-tertiary" />
              <input value={q} onChange={(e) => setQ(e.target.value)} placeholder={type === "service" ? "Search services" : "Search parts"} aria-label="Search the catalogue" className={`${field} pl-11`} />
            </div>
            {hits === null ? <div className="py-5 flex justify-center text-ink-tertiary"><Loader2 className="animate-spin" /></div> : (
              <div className="divide-y-[0.5px] divide-slate-200 max-h-[36vh] overflow-y-auto">
                {hits.map((h) => (
                  <button key={`${h.kind}-${h.ref_id}`} onClick={() => onAdd({ kind: h.kind, ref_id: h.ref_id, description: h.name, qty: 1, unit_price: h.price ?? 0 })}
                    className="w-full min-h-[3.25rem] flex items-center gap-3 px-2 text-left active:bg-slate-50">
                    <span className="min-w-0 flex-1">
                      <span className="block font-medium text-ink-primary truncate">{h.name}</span>
                      {h.kind === "product" && h.available != null && <span className={`block text-go-sub ${h.available <= 0 ? "text-amber-700" : "text-ink-secondary"}`}>{h.available <= 0 ? "Out of stock" : `${h.available} in stock`}</span>}
                    </span>
                    <span className="tabular-nums text-ink-primary">{fmt(h.price)}</span>
                    <Plus size={18} className="text-primary" />
                  </button>
                ))}
                {!hits.length && <p className="px-2 py-5 text-center text-ink-secondary">{q ? "Nothing matches" : "Nothing in the catalogue yet"}</p>}
              </div>
            )}
          </>
        )}
        <div className="mt-4 rounded-xl border border-slate-200 bg-white p-3 space-y-2">
          <p className="px-1 text-go-sub font-semibold text-ink-tertiary">Or type a price</p>
          <input value={desc} onChange={(e) => setDesc(e.target.value)} placeholder="What for? e.g. Screen replacement" aria-label="Description" className={field} />
          <div className="flex gap-2">
            <input value={price} onChange={(e) => setPrice(e.target.value)} placeholder="Price" inputMode="decimal" aria-label="Price" className={`${field} flex-1`} />
            <PrimaryButton className="!w-auto px-5" disabled={!okCustom}
              onClick={() => { onAdd({ kind: "custom", description: desc.trim(), qty: 1, unit_price: amount }); setDesc(""); setPrice(""); }}>Add</PrimaryButton>
          </div>
        </div>
      </div>
    </Sheet>
  );
}
