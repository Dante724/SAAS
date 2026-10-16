import { useEffect, useState } from "react";
import { M } from "../../components/Shell";
import { api } from "../../api/client";

// ADR-144po — everything about this job's parts in one place, opened from the work order's
// ⋯ menu (or the "Waiting on parts" chip): the purchase orders linked to it, the stock held
// for it, and the actions to order or hold more. Parts that arrive on a linked PO are written
// off to the job by the server, so there's nothing to click when they land.
type POLineView = { part: string; ordered: number; received: number; outstanding: number };
type LinkedPO = {
  po_id: string; po_number: string; status: string; supplier?: string | null; raised_here: boolean;
  ordered_at?: string | null; expected_at?: string | null; received_at?: string | null; lines: POLineView[];
};
type Hold = { id: string; part: string; qty: number; expires_at?: string | null };

const STATUS: Record<string, [string, string]> = {
  draft: ["Draft", "bg-slate-100 text-ink-secondary"],
  ordered: ["Ordered", "bg-blue-50 text-primary"],
  partial: ["Partly arrived", "bg-amber-50 text-amber-700"],
  received: ["Arrived", "bg-emerald-50 text-emerald-700"],
  closed: ["Closed", "bg-slate-100 text-ink-secondary"],
  cancelled: ["Cancelled", "bg-rose-50 text-rose-700"],
};
const day = (iso?: string | null) => (iso ? new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric" }) : "");
const HOLD_PRESETS = [7, 14, 30];

export default function PartsPOPanel({ orderId, canBuy, isEstimate, onClose, onChanged }: {
  orderId: string; canBuy: boolean; isEstimate: boolean; onClose: () => void; onChanged: () => void;
}) {
  const [pos, setPos] = useState<LinkedPO[] | null>(null);
  const [holds, setHolds] = useState<Hold[]>([]);
  const [suppliers, setSuppliers] = useState<{ id: string; name: string }[]>([]);
  const [supplier, setSupplier] = useState("");
  const [onlyShort, setOnlyShort] = useState(true);
  const [autoSend, setAutoSend] = useState(false);
  const [holdDays, setHoldDays] = useState(7);
  const [busy, setBusy] = useState<"po" | "hold" | null>(null);
  const [msg, setMsg] = useState<string | null>(null);

  const load = () => {
    api.get<any>(`/orders/${orderId}/purchase-orders`).then((r) => setPos(r.items || [])).catch(() => setPos([]));
    if (canBuy) api.get<any>(`/orders/${orderId}/reservations`).then((r) => setHolds(r.items || [])).catch(() => setHolds([]));
  };
  useEffect(() => { load(); /* eslint-disable-next-line */ }, [orderId, canBuy]);
  useEffect(() => { if (canBuy && !isEstimate) api.get<any>("/suppliers").then((r) => setSuppliers(r.items || r || [])).catch(() => {}); }, [canBuy, isEstimate]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey); return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const changed = () => { load(); onChanged(); };
  async function raisePO() {
    if (!supplier) { setMsg("Pick a supplier first"); return; }
    setBusy("po"); setMsg(null);
    try {
      const r = await api.post<any>(`/orders/${orderId}/create-po`, { supplier_id: supplier, only_shortfall: onlyShort, auto_send: autoSend });
      setMsg(r.line_count ? `${r.po_number} raised for ${r.line_count} part${r.line_count === 1 ? "" : "s"}${r.sent ? " and sent to the supplier" : ""}` : "Nothing is short of stock");
      changed();
    } catch (e: any) { setMsg(e.message || "Couldn't raise the PO"); } finally { setBusy(null); }
  }
  async function reserve() {
    setBusy("hold"); setMsg(null);
    try {
      const r = await api.post<any>(`/orders/${orderId}/reserve-stock`, { hold_days: holdDays });
      const held = (r.reserved || []).reduce((n: number, x: any) => n + (x.qty || 0), 0);
      setMsg(held ? `Holding ${held} unit${held === 1 ? "" : "s"} for ${r.hold_days} days${(r.short || []).length ? ". Some parts are short, so order those" : ""}` : "Nothing in stock to hold. Order the parts instead");
      changed();
    } catch (e: any) { setMsg(e.message || "Couldn't hold the stock"); } finally { setBusy(null); }
  }
  async function release(id: string) {
    try { await api.post(`/inventory/reservations/${id}/release`, {}); changed(); }
    catch (e: any) { setMsg(e.message || "Couldn't release the hold"); }
  }

  const open = (pos || []).filter((p) => ["draft", "ordered", "partial"].includes(p.status));
  const done = (pos || []).filter((p) => !["draft", "ordered", "partial"].includes(p.status));
  const card = (p: LinkedPO) => {
    const [label, tone] = STATUS[p.status] || [p.status, "bg-slate-100 text-ink-secondary"];
    const dates = [p.ordered_at && `Ordered ${day(p.ordered_at)}`,
      p.status !== "received" && p.expected_at && `Expected ${day(p.expected_at)}`,
      p.received_at && `${p.status === "received" ? "Arrived" : "Last delivery"} ${day(p.received_at)}`].filter(Boolean).join(" · ");
    return (
      <div key={p.po_id} className="rounded-xl border border-slate-200/90 bg-white p-3.5">
        <div className="flex items-center gap-2">
          <span className="text-sm font-semibold text-ink-primary tabular-nums">{p.po_number}</span>
          <span className={`text-[11px] font-semibold px-1.5 py-0.5 rounded-md ${tone}`}>{label}</span>
          <span className="ml-auto text-[11px] text-ink-tertiary truncate">{p.supplier}</span>
        </div>
        {dates && <div className="text-[11px] text-ink-tertiary mt-0.5">{dates}{p.raised_here ? "" : " · shared with other jobs"}</div>}
        <div className="mt-2.5 space-y-2">
          {p.lines.map((l, i) => (
            <div key={i}>
              <div className="flex items-baseline gap-2 text-xs">
                <span className="text-ink-primary truncate flex-1">{l.part}</span>
                <span className="tabular-nums text-ink-secondary">{l.received} of {l.ordered}</span>
                {l.outstanding > 0 && p.status !== "cancelled" && <span className="text-[11px] text-amber-700 tabular-nums">{l.outstanding} to come</span>}
              </div>
              <div className="mt-1 h-1 rounded-full bg-slate-100 overflow-hidden">
                <div className={`h-full rounded-full ${l.outstanding === 0 ? "bg-emerald-500" : "bg-primary"}`} style={{ width: `${l.ordered ? Math.min(100, (l.received / l.ordered) * 100) : 0}%` }} />
              </div>
            </div>
          ))}
        </div>
      </div>
    );
  };

  return (
    <div className="absolute inset-0 z-30 flex justify-end">
      <div className="absolute inset-0 bg-ink-primary/20" onClick={onClose} />
      <div role="dialog" aria-label="Parts and purchase orders" className="relative w-[440px] max-w-full h-full bg-slate-50 border-l border-slate-200/90 shadow-clay-drawer flex flex-col">
        <div className="px-5 py-4 border-b border-slate-200/90 bg-white flex items-center gap-3">
          <span className="w-8 h-8 rounded-lg bg-primary/10 text-primary grid place-items-center"><M n="local_shipping" c="text-base" /></span>
          <div className="flex-1 min-w-0">
            <div className="text-sm font-semibold text-ink-primary">Parts &amp; purchase orders</div>
            <div className="text-[11px] text-ink-tertiary">Parts that arrive are written off to this job automatically.</div>
          </div>
          <button aria-label="Close" onClick={onClose} className="w-8 h-8 rounded-lg hover:bg-slate-100 grid place-items-center text-ink-tertiary"><M n="close" c="text-base" /></button>
        </div>

        <div className="flex-1 overflow-y-auto p-5 space-y-5">
          {msg && <div className="rounded-lg bg-blue-50 border border-blue-100 text-primary text-xs px-3 py-2">{msg}</div>}

          <section className="space-y-2">
            <h4 className="text-[11px] font-semibold text-ink-tertiary">On order</h4>
            {pos === null ? <div className="text-xs text-ink-tertiary">Loading…</div>
              : open.length ? open.map(card)
              : <div className="text-xs text-ink-tertiary rounded-xl border border-dashed border-slate-200 px-3 py-4 text-center">Nothing on order for this job.</div>}
          </section>

          {done.length > 0 && (
            <section className="space-y-2">
              <h4 className="text-[11px] font-semibold text-ink-tertiary">Arrived or closed</h4>
              {done.map(card)}
            </section>
          )}

          {canBuy && (
            <section className="space-y-2">
              <h4 className="text-[11px] font-semibold text-ink-tertiary">Held for this job</h4>
              {holds.length ? holds.map((h) => (
                <div key={h.id} className="flex items-center gap-2 text-xs bg-white rounded-xl border border-slate-200/90 px-3 py-2">
                  <M n="lock_clock" c="text-sm text-ink-tertiary" />
                  <span className="text-ink-primary truncate flex-1">{h.part}</span>
                  <span className="tabular-nums text-ink-secondary">×{h.qty}</span>
                  {h.expires_at && <span className="text-[11px] text-ink-tertiary">until {day(h.expires_at)}</span>}
                  <button type="button" onClick={() => release(h.id)} className="text-[11px] font-semibold text-ink-secondary hover:text-rose-600">Release</button>
                </div>
              )) : <div className="text-xs text-ink-tertiary">No stock is held for this job.</div>}
            </section>
          )}

          {canBuy && !isEstimate && (
            <section className="rounded-xl border border-slate-200/90 bg-white p-3.5 space-y-3">
              <div className="space-y-1.5">
                <div className="text-xs font-semibold text-ink-primary">Order parts from a supplier</div>
                <div className="flex items-center gap-1.5">
                  <select value={supplier} onChange={(e) => { setSupplier(e.target.value); setMsg(null); }} aria-label="Supplier"
                    className="glass-input rounded-lg px-2 py-2 text-xs text-ink-primary flex-1 min-w-0">
                    <option value="">Choose a supplier</option>
                    {suppliers.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
                  </select>
                  <button type="button" onClick={raisePO} disabled={busy !== null} className="px-3 py-2 rounded-lg bg-primary text-white text-xs font-semibold hover:bg-primary-container disabled:opacity-50">{busy === "po" ? "Raising…" : "Raise PO"}</button>
                </div>
                <label className="flex items-center gap-1.5 text-[11px] text-ink-secondary cursor-pointer"><input type="checkbox" checked={onlyShort} onChange={(e) => setOnlyShort(e.target.checked)} />Only what's short of stock</label>
                <label className="flex items-center gap-1.5 text-[11px] text-ink-secondary cursor-pointer"><input type="checkbox" checked={autoSend} onChange={(e) => setAutoSend(e.target.checked)} />Mark it ordered and tell the supplier</label>
              </div>
              <div className="border-t border-slate-100" />
              <div className="space-y-1.5">
                <div className="text-xs font-semibold text-ink-primary">Hold parts already in stock</div>
                <div className="flex items-center gap-1.5 flex-wrap">
                  {HOLD_PRESETS.map((n) => (
                    <button key={n} type="button" onClick={() => setHoldDays(n)} aria-pressed={holdDays === n}
                      className={`px-2.5 py-1 rounded-full text-[11px] font-semibold border ${holdDays === n ? "bg-blue-50 text-primary border-blue-200" : "text-ink-secondary border-slate-200 hover:bg-slate-50"}`}>{n} days</button>
                  ))}
                  <button type="button" onClick={reserve} disabled={busy !== null} className="ml-auto px-3 py-2 rounded-lg bg-white border border-slate-200 text-ink-primary text-xs font-semibold hover:bg-slate-50 disabled:opacity-50">{busy === "hold" ? "Holding…" : "Hold stock"}</button>
                </div>
              </div>
            </section>
          )}
        </div>
      </div>
    </div>
  );
}
