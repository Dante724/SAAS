import { useEffect, useState } from "react";
import { M } from "../../components/Shell";
import { api } from "../../api/client";
import { money } from "../orders/types";
import { tint, hueText } from "../../lib/tint";
import { type Member, SellPlanDialog } from "./shared";

// Memberships on a work order: the plan banner above the lines, and the offer shown to a
// non-member when payment is taken (docs/SPEC_memberships.md §7.3–7.4).

type Offer = { plan_id: string; name: string; saving: number; price_monthly?: number | null; price_yearly?: number | null; includes: string[] };
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
        const left = (m.items || []).filter((i) => i.kind === "service" && (i.left ?? 0) > 0);
        return (
          <div key={m.id} className="flex flex-wrap items-center gap-3 rounded-xl border p-3" style={{ borderColor: tint(m.color, 30), background: tint(m.color, 6) }}>
            <div className="h-8 w-8 rounded-lg grid place-items-center" style={{ background: tint(m.color), color: hueText(m.color) }}><M n="workspace_premium" c="text-base" /></div>
            <div className="min-w-0 flex-1">
              <div className="text-xs font-semibold text-ink-primary">{m.plan} member{m.status === "paused" ? " · paused until paid" : ""}</div>
              <div className="text-[11px] text-ink-tertiary truncate">{left.length ? left.map((i) => `${i.text.replace(/ × \d+$/, "")}: ${i.left} left`).join(" · ")
                : (m.items || []).filter((i) => i.kind === "discount").map((i) => i.text).join(" · ") || "Included services used for this year"}</div>
            </div>
            {d.coverable > 0 && (d.can_apply
              ? <button onClick={apply} disabled={busy} className="h-8 px-3 rounded-lg bg-white border border-slate-200 text-xs font-semibold text-ink-primary hover:bg-slate-50">Apply plan to {d.coverable} line{d.coverable > 1 ? "s" : ""}</button>
              : <span className="text-[11px] text-ink-tertiary">{d.coverable} line{d.coverable > 1 ? "s" : ""} could be covered. Ask a manager.</span>)}
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
  const price = o.price_yearly ? `${money(o.price_yearly)}/yr` : `${money(o.price_monthly || 0)}/mo`;
  return (
    <div className="rounded-xl border border-primary/25 bg-primary/[0.04] p-3.5">
      <div className="flex items-start gap-3">
        <div className="h-8 w-8 shrink-0 rounded-lg grid place-items-center bg-primary/10 text-primary"><M n="workspace_premium" c="text-base" /></div>
        <div className="min-w-0 flex-1">
          <div className="text-xs font-semibold text-ink-primary">{o.name} would save {money(o.saving)} today</div>
          <div className="text-[11px] text-ink-tertiary truncate">{o.includes.join(" · ")} · {price}</div>
        </div>
      </div>
      <div className="mt-2.5 flex gap-2 pl-11">
        <button onClick={() => setSell(true)} className="h-8 px-3.5 rounded-lg bg-primary text-white text-xs font-semibold whitespace-nowrap">Join</button>
        <button onClick={() => { setHidden(true); api.post(`/memberships/order/${orderId}/snooze`, {}).catch(() => {}); }} className="h-8 px-3 rounded-lg text-xs font-semibold text-ink-tertiary hover:bg-white whitespace-nowrap">Not now</button>
      </div>
      {sell && <SellPlanDialog clientId={d.client_id} deviceId={d.device_id || undefined} orderId={orderId} presetPlanId={o.plan_id}
        onClose={() => setSell(false)} onDone={() => { setSell(false); setHidden(true); onJoined(); }} />}
    </div>
  );
}
