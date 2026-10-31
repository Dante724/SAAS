import { useEffect, useState } from "react";
import { M } from "../../components/Shell";
import { api } from "../../api/client";
import { money } from "../orders/types";
import { tint, hueText } from "../../lib/tint";
import { type Member, SellPlanDialog, Ring, fmtDate } from "./shared";

// Memberships on a work order: the plan banner above the lines, and the offer shown to a
// non-member when payment is taken (docs/SPEC_memberships.md §7.3–7.4).

type Offer = { plan_id: string; name: string; saving: number; price_monthly?: number | null; price_yearly?: number | null; includes: string[]; order_total?: number };
type ForOrder = { plans: Member[]; coverable: number; can_apply: boolean; offer: Offer | null; client_id?: string | null; device_id?: string | null };

export function PlanBanner({ orderId, refreshKey, onChanged }: { orderId: string; refreshKey?: unknown; onChanged?: () => void }) {
  const [d, setD] = useState<ForOrder | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => { api.get<ForOrder>(`/memberships/order/${orderId}`).then(setD).catch(() => setD(null)); }, [orderId, refreshKey]);
  if (!d?.plans.length) return null;
  async function apply() {
    setBusy(true);
    try { await api.post(`/memberships/order/${orderId}/apply`, {}); onChanged?.(); } finally { setBusy(false); }
  }
  return (
    <div className="space-y-2">
      {d.plans.map((m) => {
        const services = (m.items || []).filter((i) => i.kind === "service");
        const discount = (m.items || []).find((i) => i.kind === "discount");
        return (
          <div key={m.id} className="rounded-xl border overflow-hidden" style={{ borderColor: tint(m.color, 30) }}>
            <div className="flex flex-wrap items-center gap-3 px-3 py-2.5" style={{ background: tint(m.color, 10) }}>
              <div className="h-8 w-8 rounded-lg grid place-items-center bg-white/75" style={{ color: hueText(m.color) }}><M n="workspace_premium" c="text-base" /></div>
              <div className="min-w-0 flex-1">
                <div className="text-xs font-semibold text-ink-primary">{m.plan} member{m.status === "paused" ? " · paused until paid" : ""}</div>
                <div className="text-[11px] text-ink-secondary">Since {fmtDate(m.starts_on)}{m.saved ? ` · saved ${money(m.saved)} this plan year` : ""}{discount ? ` · ${discount.text}` : ""}</div>
              </div>
              {d.coverable > 0 && (d.can_apply
                ? <button onClick={apply} disabled={busy} className="h-8 px-3 rounded-lg bg-white border border-slate-200 text-xs font-semibold text-ink-primary hover:bg-slate-50">Apply plan to {d.coverable} line{d.coverable > 1 ? "s" : ""}</button>
                : <span className="text-[11px] text-ink-tertiary">{d.coverable} line{d.coverable > 1 ? "s" : ""} could be covered. Ask a manager.</span>)}
            </div>
            {services.length > 0 && (
              <div className="flex flex-wrap gap-x-4 gap-y-2 bg-white px-3 py-2.5">
                {services.map((i) => (
                  <span key={i.id} className="inline-flex items-center gap-2 text-[11px] text-ink-secondary">
                    <Ring left={Math.round(i.left ?? 0)} of={Math.round(i.of ?? 0)} color={m.color} size={26} />
                    {i.text.replace(/ × \d+$/, "")}
                  </span>
                ))}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

/** The non-member offer in the payment dialog: what the plan would save today, one tap to join. */
export function CheckoutOffer({ orderId, onJoined }: { orderId: string; onJoined: () => void }) {
  const [d, setD] = useState<ForOrder | null>(null);
  const [sell, setSell] = useState(false);
  const [hidden, setHidden] = useState(false);
  useEffect(() => { api.get<ForOrder>(`/memberships/order/${orderId}`).then(setD).catch(() => setD(null)); }, [orderId]);
  const o = d?.offer;
  if (!o || hidden || !d?.client_id) return null;
  const yearly = !!o.price_yearly;
  const planPrice = yearly ? o.price_yearly! : o.price_monthly || 0;
  const now = o.order_total ?? 0;
  const withPlan = Math.max(0, now - o.saving) + planPrice;
  return (
    <div className="rounded-xl border border-emerald-200 bg-emerald-50/60 p-3.5">
      <div className="flex items-start gap-3">
        <div className="h-9 w-9 shrink-0 rounded-lg grid place-items-center bg-white text-emerald-700"><M n="workspace_premium" c="text-base" /></div>
        <div className="min-w-0 flex-1">
          <div className="text-[11px] font-semibold text-emerald-800">{o.name} saves this customer</div>
          <div className="text-2xl font-semibold tracking-tight tabular-nums text-emerald-700 leading-tight">{money(o.saving)} today</div>
          {now > 0 && <div className="mt-0.5 text-xs text-ink-secondary tabular-nums">Today <s className="text-ink-tertiary">{money(now)}</s> → <b className="text-ink-primary">{money(withPlan)}</b> with the plan{withPlan <= now ? ", and it pays for itself today" : ""}</div>}
        </div>
      </div>
      <div className="mt-2.5 flex flex-wrap gap-1.5 pl-12">{o.includes.map((t) => <span key={t} className="rounded-full bg-white px-2 py-0.5 text-[11px] font-medium text-emerald-800">{t}</span>)}</div>
      <div className="mt-3 flex gap-2 pl-12">
        <button onClick={() => setSell(true)} className="h-9 px-4 rounded-lg bg-emerald-600 text-white text-xs font-semibold whitespace-nowrap shadow-clay-sm">Join · {money(planPrice)}{yearly ? "/yr" : "/mo"}</button>
        <button onClick={() => { setHidden(true); api.post(`/memberships/order/${orderId}/snooze`, {}).catch(() => {}); }} className="h-9 px-3 rounded-lg text-xs font-semibold text-ink-tertiary hover:bg-white whitespace-nowrap">Not now</button>
      </div>
      {sell && <SellPlanDialog clientId={d.client_id} deviceId={d.device_id || undefined} orderId={orderId} presetPlanId={o.plan_id}
        onClose={() => setSell(false)} onDone={() => { setSell(false); setHidden(true); onJoined(); }} />}
    </div>
  );
}
