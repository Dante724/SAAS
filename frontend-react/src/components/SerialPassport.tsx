import { useEffect, useState } from "react";
import { M } from "./Shell";
import { api } from "../api/client";
import { useMe } from "../context/Me";
import { money } from "../pages/inventory/types";

type Passport = {
  id: string; serial_number: string; status: string; status_label: string;
  product?: { id: string; name: string; sku?: string | null } | null;
  unit_cost: number | null; supplier?: { id: string; name: string } | null;
  supplier_warranty_until: string | null; in_supplier_warranty: boolean | null; received_at: string | null;
  where: { location?: string | null; warehouse?: string | null; bin?: string | null; order_id?: string | null; order_number?: string | null; asset?: string | null };
  history: { kind: string; at: string | null; label?: string | null; ref_label?: string | null; location?: string | null; bin?: string | null; by?: string | null }[];
};

const KIND: Record<string, { label: string; icon: string }> = {
  received: { label: "Received", icon: "download" }, moved: { label: "Moved", icon: "swap_horiz" },
  used: { label: "Used on a job", icon: "build" }, returned: { label: "Back in stock", icon: "undo" },
  sold: { label: "Sold", icon: "point_of_sale" }, written_off: { label: "Written off", icon: "delete_sweep" },
  restored: { label: "Restored", icon: "restore" }, converted: { label: "Became an asset", icon: "devices" },
  counted: { label: "Counted", icon: "fact_check" },
};
const day = (s?: string | null) => (s ? new Date(s).toLocaleDateString() : "—");

/** ii144c — the unit passport: one serialized unit's cost, supplier, warranty, where it is and its history. */
export default function SerialPassport({ unitId, onClose }: { unitId: string; onClose: () => void }) {
  const { can } = useMe();
  const [p, setP] = useState<Passport | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [editW, setEditW] = useState<string | null>(null);
  const load = () => api.get<Passport>(`/serials/${unitId}`).then(setP).catch((e) => setErr(e?.message || "Couldn't load this unit"));
  useEffect(() => { load(); }, [unitId]); // eslint-disable-line
  async function saveWarranty() {
    try { await api.patch(`/serials/${unitId}`, { supplier_warranty_until: editW || null }); setEditW(null); load(); }
    catch (e: any) { setErr(e?.message || "Couldn't save the date"); }
  }
  const where = p ? [p.where.location, p.where.warehouse && p.where.warehouse !== p.where.location ? p.where.warehouse : null, p.where.bin ? `bin ${p.where.bin}` : null].filter(Boolean).join(" · ") : "";
  const row = "flex items-start justify-between gap-3 py-2";
  return (
    <div className="fixed inset-0 z-[60] flex justify-end" role="dialog" aria-modal="true" aria-label="Unit passport">
      <div aria-hidden="true" className="absolute inset-0 bg-ink-primary/35 backdrop-blur-[2px]" onClick={onClose} />
      <div className="relative h-full w-full max-w-[440px] bg-white shadow-drawer flex flex-col border-l border-slate-200">
        <div className="px-5 py-4 border-b border-slate-100 flex items-center justify-between bg-slate-50/70">
          <div className="min-w-0"><span className="text-[11px] font-bold text-primary block">Unit passport</span>
            <h3 className="text-base font-bold text-ink-primary font-mono truncate">{p?.serial_number || "…"}</h3></div>
          <button aria-label="Close" onClick={onClose} className="w-8 h-8 rounded-lg hover:bg-slate-200/60 grid place-items-center text-ink-tertiary"><M n="close" c="text-base" /></button>
        </div>
        <div className="flex-1 overflow-y-auto p-5 text-xs space-y-4">
          {err && <div role="alert" className="rounded-lg bg-rose-50 border border-rose-200 text-rose-700 px-3 py-2">{err}</div>}
          {!p ? !err && <p className="text-ink-tertiary">Loading…</p> : (<>
            <div className="rounded-2xl border border-slate-200/90 divide-y divide-slate-100 px-4">
              <div className={row}><span className="text-ink-tertiary">Product</span><span className="font-semibold text-ink-primary text-right">{p.product?.name || "—"}</span></div>
              <div className={row}><span className="text-ink-tertiary">Status</span><span className="font-semibold text-ink-primary">{p.status_label}</span></div>
              <div className={row}><span className="text-ink-tertiary">Where it is now</span><span className="font-semibold text-ink-primary text-right">
                {p.where.order_number ? `On job ${p.where.order_number}` : p.where.asset ? `Asset: ${p.where.asset}` : (p.status === "in_stock" ? where || "—" : "—")}</span></div>
              <div className={row}><span className="text-ink-tertiary">Received</span><span className="text-ink-primary">{day(p.received_at)}</span></div>
              <div className={row}><span className="text-ink-tertiary">Supplier</span><span className="text-ink-primary">{p.supplier?.name || "—"}</span></div>
              {p.unit_cost != null && <div className={row}><span className="text-ink-tertiary">Purchase price</span><span className="text-ink-primary tabular-nums">{money(p.unit_cost)}</span></div>}
              <div className={row}><span className="text-ink-tertiary">Supplier warranty</span>
                {editW != null ? (
                  <span className="flex items-center gap-1.5"><input type="date" value={editW} onChange={(e) => setEditW(e.target.value)} aria-label="Supplier warranty until" className="border border-slate-200 rounded-lg px-2 py-1 text-xs" />
                    <button onClick={saveWarranty} className="text-primary font-semibold">Save</button><button onClick={() => setEditW(null)} className="text-ink-tertiary">Cancel</button></span>
                ) : (
                  <span className="text-right">
                    {p.supplier_warranty_until ? <span className={p.in_supplier_warranty ? "text-emerald-700 font-semibold" : "text-ink-secondary"}>{p.in_supplier_warranty ? "Until" : "Ended"} {day(p.supplier_warranty_until)}</span> : <span className="text-ink-tertiary">Not set</span>}
                    {can("can_manage_inventory") && <button onClick={() => setEditW(p.supplier_warranty_until || "")} className="ml-2 text-primary font-semibold">Change</button>}
                  </span>
                )}</div>
            </div>
            <div>
              <div className="text-[11px] font-bold text-ink-tertiary mb-2">History</div>
              <ol className="relative border-l border-slate-200 ml-2 space-y-3">
                {p.history.map((h, i) => (
                  <li key={i} className="ml-4">
                    <span className="absolute -left-[9px] w-[18px] h-[18px] rounded-full bg-white border border-slate-200 grid place-items-center"><M n={KIND[h.kind]?.icon || "circle"} c="text-[11px] text-primary" /></span>
                    <div className="font-semibold text-ink-primary">{KIND[h.kind]?.label || h.kind}{h.ref_label ? ` · ${h.ref_label}` : ""}</div>
                    <div className="text-ink-tertiary">{[day(h.at), h.label, h.bin ? `bin ${h.bin}` : null, h.by].filter(Boolean).join(" · ")}</div>
                  </li>
                ))}
              </ol>
            </div>
          </>)}
        </div>
      </div>
    </div>
  );
}
