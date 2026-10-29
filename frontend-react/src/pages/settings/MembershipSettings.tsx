import { useEffect, useState } from "react";
import { M } from "../../components/Shell";
import { api } from "../../api/client";
import { useMe } from "../../context/Me";

// Settings › Memberships: the few shop-wide choices — the checkout offer, how forgiving unpaid
// plans are, when renewals go out, and how plan money shows in the books.

type S = {
  membership_checkout_prompt: boolean; membership_prompt_threshold_pct: number; membership_prompt_snooze_days: number;
  membership_grace_days: number; membership_lapse_days: number; membership_renewal_notice_days: number; membership_spread_revenue: boolean;
};

function Toggle({ on, onChange, label }: { on: boolean; onChange: (v: boolean) => void; label: string }) {
  return (
    <button type="button" role="switch" aria-checked={on} aria-label={label} onClick={() => onChange(!on)}
      className={`relative h-6 w-11 shrink-0 rounded-full transition ${on ? "bg-primary" : "bg-slate-300"}`}>
      <span className={`absolute top-0.5 h-5 w-5 rounded-full bg-white shadow transition-all ${on ? "left-[22px]" : "left-0.5"}`} />
    </button>
  );
}

function Num({ id, value, onChange, suffix, disabled }: { id: string; value: number; onChange: (v: number) => void; suffix: string; disabled?: boolean }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <input id={id} inputMode="numeric" disabled={disabled} value={value} onChange={(e) => onChange(Number(e.target.value.replace(/[^\d.]/g, "")) || 0)}
        className="glass-input h-9 w-16 rounded-lg px-2 text-center text-sm tabular-nums" />
      <span className="text-xs text-ink-tertiary">{suffix}</span>
    </span>
  );
}

export default function MembershipSettings() {
  const { can } = useMe();
  const canEdit = can("can_manage_membership_plans");
  const [s, setS] = useState<S | null>(null);
  const [saved, setSaved] = useState<S | null>(null);
  const [msg, setMsg] = useState("");
  useEffect(() => { api.get<S>("/memberships/settings/shop").then((r) => { setS(r); setSaved(r); }).catch(() => {}); }, []);
  if (!s) return <p className="text-xs text-ink-tertiary">Loading…</p>;
  const set = (patch: Partial<S>) => setS({ ...s, ...patch });
  const dirty = JSON.stringify(s) !== JSON.stringify(saved);
  async function save() {
    try { const r = await api.put<S>("/memberships/settings/shop", s); setS(r); setSaved(r); setMsg("Saved"); }
    catch (e: any) { setMsg(e?.message || "Couldn't save"); }
    setTimeout(() => setMsg(""), 2500);
  }
  const card = "rounded-2xl border border-slate-200/90 bg-white shadow-clay-sm p-5";
  const row = "flex items-start gap-4";
  return (
    <section className="max-w-2xl space-y-4 pb-10">
      <div className="flex items-start gap-3">
        <div className="min-w-0 flex-1 basis-64">
          <h2 className="text-base font-semibold text-ink-primary">Memberships</h2>
          <p className="text-xs text-ink-tertiary">How plans are offered, what happens when a payment is missed, and how plan money shows in your books. Plans themselves are in Catalog › Plans.</p>
        </div>
        <a href="/catalog?tab=plans" className="h-9 px-3.5 rounded-xl border border-slate-200 bg-white text-xs font-semibold text-ink-secondary hover:bg-slate-50 flex items-center gap-1.5"><M n="workspace_premium" c="text-sm" />Plans</a>
      </div>

      <div className={card}>
        <div className={row}>
          <div className="flex-1"><div className="text-sm font-semibold text-ink-primary">Offer a plan at checkout</div>
            <div className="text-xs text-ink-tertiary">When a non-member pays, show what a plan would have saved them, with one tap to join.</div></div>
          <Toggle label="Offer a plan at checkout" on={s.membership_checkout_prompt} onChange={(v) => canEdit && set({ membership_checkout_prompt: v })} />
        </div>
        {s.membership_checkout_prompt && (
          <div className="mt-4 space-y-2 border-t border-slate-100 pt-4 text-xs text-ink-secondary">
            <div className="flex flex-wrap items-center gap-2"><label htmlFor="ms-thr">Only when today's saving is at least</label>
              <Num id="ms-thr" disabled={!canEdit} value={s.membership_prompt_threshold_pct} onChange={(v) => set({ membership_prompt_threshold_pct: v })} suffix="% of the yearly price" /></div>
            <div className="flex flex-wrap items-center gap-2"><label htmlFor="ms-snooze">After "Not now", hide it for</label>
              <Num id="ms-snooze" disabled={!canEdit} value={s.membership_prompt_snooze_days} onChange={(v) => set({ membership_prompt_snooze_days: v })} suffix="days" /></div>
          </div>
        )}
      </div>

      <div className={card}>
        <div className="text-sm font-semibold text-ink-primary">Missed payments</div>
        <div className="mt-3 space-y-2 text-xs text-ink-secondary">
          <div className="flex flex-wrap items-center gap-2"><label htmlFor="ms-grace">Pause benefits after</label>
            <Num id="ms-grace" disabled={!canEdit} value={s.membership_grace_days} onChange={(v) => set({ membership_grace_days: v })} suffix="days unpaid, and alert the manager" /></div>
          <div className="flex flex-wrap items-center gap-2"><label htmlFor="ms-lapse">End the plan after</label>
            <Num id="ms-lapse" disabled={!canEdit} value={s.membership_lapse_days} onChange={(v) => set({ membership_lapse_days: v })} suffix="days unpaid" /></div>
          <div className="flex flex-wrap items-center gap-2"><label htmlFor="ms-renew">Send the renewal invoice</label>
            <Num id="ms-renew" disabled={!canEdit} value={s.membership_renewal_notice_days} onChange={(v) => set({ membership_renewal_notice_days: v })} suffix="days before a plan year ends" /></div>
        </div>
      </div>

      <div className={card}>
        <div className={row}>
          <div className="flex-1"><div className="text-sm font-semibold text-ink-primary">Spread yearly payments over the plan's months</div>
            <div className="text-xs text-ink-tertiary">Off: plan money counts when it's paid. On: a yearly payment is earned month by month, and the unearned part shows as prepaid. Ask your accountant if unsure.</div></div>
          <Toggle label="Spread yearly payments" on={s.membership_spread_revenue} onChange={(v) => canEdit && set({ membership_spread_revenue: v })} />
        </div>
      </div>

      {canEdit && (
        <div className="flex items-center gap-3">
          <button onClick={save} disabled={!dirty} className="h-10 px-5 rounded-xl bg-primary text-white text-sm font-semibold shadow-clay-sm disabled:opacity-40">Save</button>
          {msg && <span role="status" className="text-xs text-ink-secondary">{msg}</span>}
        </div>
      )}
    </section>
  );
}
