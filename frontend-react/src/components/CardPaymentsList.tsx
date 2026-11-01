import { useEffect, useState } from "react";
import { M } from "./Shell";
import { api } from "../api/client";
import { useMe } from "../context/Me";
import { money } from "../pages/orders/types";
import { promptDialog } from "../lib/dialog";
import type { Charge } from "./ReaderCharge";
import { serverDate } from "../lib/serverTime";

// F2/F3 — approved card-reader payments with a Refund action: for one work order (orderId),
// or for counter sales (sales). The refund goes back to the customer's card through the provider.

type Row = Charge & { refunded_amount: number; document?: string | null; terminal_label?: string | null; created_at?: string | null };

export default function CardPaymentsList({ orderId, sales, reloadKey, onChanged }: {
  orderId?: string; sales?: boolean; reloadKey?: number; onChanged?: () => void;
}) {
  const { can } = useMe();
  const [rows, setRows] = useState<Row[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const allowed = can("can_take_card_payments");

  const load = () => {
    if (!allowed) return;
    const q = orderId ? `order_id=${encodeURIComponent(orderId)}` : "kind=sale";
    api.get<{ items: Row[] }>(`/payments/terminal/charges?${q}&status=succeeded`)
      .then((r) => { setRows(r.items || []); setErr(null); }).catch((e) => setErr(e.message || "Couldn't load card payments"));
  };
  useEffect(load, [orderId, sales, reloadKey, allowed]);

  if (!allowed) return null;
  // On a work order the section only appears once there's a reader payment to show (no flash of "Loading…").
  if (orderId && !err && (!rows || rows.length === 0)) return null;

  async function refund(r: Row) {
    const left = Math.round((r.amount - (r.refunded_amount || 0)) * 100) / 100;
    const v = await promptDialog(`Refund to the customer's card (up to ${money(left)}):`, { defaultValue: String(left), confirmLabel: "Refund" });
    if (v == null) return;
    const amt = parseFloat(v);
    if (!(amt > 0)) { setMsg("Enter an amount above zero"); return; }
    setBusy(r.id); setMsg(null);
    try {
      await api.post(`/payments/terminal/charges/${r.id}/refund`, { amount: amt });
      setMsg(`Refunded ${money(amt)}`); load(); onChanged?.();
    } catch (e: any) { setMsg(e.message || "Refund failed"); } finally { setBusy(null); }
  }

  return (
    <div className="bg-white rounded-2xl border border-slate-200/90 shadow-sm overflow-hidden">
      <div className="p-3.5 border-b border-slate-100 flex items-center justify-between">
        <h5 className="text-xs font-bold text-ink-primary flex items-center gap-1.5"><M n="contactless" c="text-sm text-primary" />Card reader payments</h5>
        {msg && <span className="text-[11px] font-semibold text-ink-secondary">{msg}</span>}
      </div>
      {!rows && !err && <p className="p-3.5 text-xs text-ink-tertiary">Loading…</p>}
      {err && <p className="p-3.5 text-xs text-rose-600">{err}</p>}
      {rows && rows.length === 0 && <p className="p-3.5 text-xs text-ink-tertiary">No card reader payments yet.</p>}
      {rows && rows.length > 0 && (
        <ul className="divide-y divide-slate-100">
          {rows.map((r) => {
            const left = r.amount - (r.refunded_amount || 0);
            return (
              <li key={r.id} className="flex flex-wrap items-center gap-2 px-3.5 py-2.5 text-xs">
                <div className="min-w-0 flex-1">
                  <div className="font-bold text-ink-primary">{sales && r.document ? `${r.document} · ` : ""}{r.terminal_label || "Card reader"}</div>
                  <div className="text-[11px] text-ink-tertiary">{serverDate(r.created_at)?.toLocaleString() || ""}
                    {r.refunded_amount > 0 ? ` · refunded ${money(r.refunded_amount)}` : ""}</div>
                </div>
                <span className="tabular-nums font-bold text-ink-primary">{money(r.amount)}</span>
                {can("can_refund_card_payments") && left > 0.004 && (
                  <button onClick={() => refund(r)} disabled={busy === r.id} className="px-2 py-1 rounded-lg text-[11px] font-semibold text-rose-600 hover:bg-rose-50 disabled:opacity-50">{busy === r.id ? "Refunding…" : "Refund"}</button>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
