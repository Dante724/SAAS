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

function Num({ id, value, onChange, unit, disabled, max }: { id: string; value: number; onChange: (v: number) => void; unit: string; disabled?: boolean; max?: number }) {
  return (
    <div className={`glass-input h-9 w-28 rounded-xl flex items-center pr-3 focus-within:ring-2 focus-within:ring-primary/30 ${disabled ? "opacity-50" : ""}`}>
      <input id={id} inputMode="numeric" disabled={disabled} value={value}
        onChange={(e) => { const n = Number(e.target.value.replace(/[^\d]/g, "")) || 0; onChange(max ? Math.min(n, max) : n); }}
        className="w-full min-w-0 bg-transparent pl-3 text-sm font-medium tabular-nums text-ink-primary outline-none disabled:cursor-not-allowed" />
      <span className="text-xs text-ink-tertiary shrink-0">{unit}</span>
    </div>
  );
}

// One setting: what it is and why on the left, its control on the right.
function Row({ label, help, htmlFor, children, dim }: { label: string; help?: string; htmlFor?: string; children: React.ReactNode; dim?: boolean }) {
  return (
    <div className={`flex items-center gap-6 py-4 first:pt-0 last:pb-0 ${dim ? "opacity-60" : ""}`}>
      <div className="min-w-0 flex-1">
        <label htmlFor={htmlFor} className="block text-sm font-medium text-ink-primary">{label}</label>
        {help && <p className="mt-0.5 text-xs text-ink-tertiary leading-relaxed">{help}</p>}
      </div>
      <div className="shrink-0">{children}</div>
    </div>
  );
}

function Group({ icon, title, children }: { icon: string; title: string; children: React.ReactNode }) {
  return (
    <div className="px-6 py-5">
      <div className="mb-4 flex items-center gap-2">
        <M n={icon} c="text-base text-ink-tertiary" />
        <h3 className="text-xs font-semibold uppercase tracking-wide text-ink-tertiary">{title}</h3>
      </div>
      <div className="divide-y divide-slate-100">{children}</div>
    </div>
  );
}

export default function MembershipSettings() {
  const { can } = useMe();
  const canEdit = can("can_manage_membership_plans");
  const [s, setS] = useState<S | null>(null);
  const [saved, setSaved] = useState<S | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  useEffect(() => { api.get<S>("/memberships/settings/shop").then((r) => { setS(r); setSaved(r); }).catch(() => {}); }, []);
  if (!s) return <p className="text-xs text-ink-tertiary">Loading…</p>;
  const set = (patch: Partial<S>) => { if (canEdit) { setS({ ...s, ...patch }); setMsg(null); } };
  const dirty = JSON.stringify(s) !== JSON.stringify(saved);
  async function save() {
    setBusy(true);
    try { const r = await api.put<S>("/memberships/settings/shop", s); setS(r); setSaved(r); setMsg({ ok: true, text: "Saved" }); }
    catch (e: any) { setMsg({ ok: false, text: e?.message || "Couldn't save" }); }
    setBusy(false);
  }
  const offer = s.membership_checkout_prompt;
  return (
    <section className="max-w-3xl space-y-5 pb-10">
      <div className="flex items-start gap-4">
        <div className="h-10 w-10 shrink-0 rounded-xl grid place-items-center bg-primary/10 text-primary"><M n="workspace_premium" c="text-xl" /></div>
        <div className="min-w-0 flex-1">
          <h2 className="text-base font-semibold text-ink-primary">Memberships</h2>
          <p className="mt-0.5 text-sm text-ink-tertiary">How plans are offered, what happens when a payment is missed, and how plan money shows in your books.</p>
        </div>
        <a href="/catalog?tab=plans" className="h-9 px-3.5 rounded-xl border border-slate-200 bg-white text-xs font-semibold text-ink-secondary hover:bg-slate-50 flex items-center gap-1.5 whitespace-nowrap">
          Manage plans<M n="arrow_forward" c="text-sm" />
        </a>
      </div>

      <div className="rounded-2xl border border-slate-200/90 bg-white shadow-clay-sm divide-y divide-slate-100">
        <Group icon="point_of_sale" title="Checkout offer">
          <Row label="Offer a plan at checkout" help="When a non-member pays, show what a plan would have saved them, with one tap to join.">
            <Toggle label="Offer a plan at checkout" on={offer} onChange={(v) => set({ membership_checkout_prompt: v })} />
          </Row>
          <Row htmlFor="ms-thr" dim={!offer} label="Minimum saving to show it" help="Only offer a plan when today's bill would save at least this share of the yearly price.">
            <Num id="ms-thr" disabled={!canEdit || !offer} max={100} value={s.membership_prompt_threshold_pct} onChange={(v) => set({ membership_prompt_threshold_pct: v })} unit="%" />
          </Row>
          <Row htmlFor="ms-snooze" dim={!offer} label={'After "Not now"'} help="Don't offer it to the same customer again for this long.">
            <Num id="ms-snooze" disabled={!canEdit || !offer} value={s.membership_prompt_snooze_days} onChange={(v) => set({ membership_prompt_snooze_days: v })} unit="days" />
          </Row>
        </Group>

        <Group icon="schedule" title="Missed payments">
          <Row htmlFor="ms-grace" label="Pause benefits" help="Days a payment can be overdue before benefits stop. The manager is alerted.">
            <Num id="ms-grace" disabled={!canEdit} value={s.membership_grace_days} onChange={(v) => set({ membership_grace_days: v })} unit="days" />
          </Row>
          <Row htmlFor="ms-lapse" label="End the plan" help="Days overdue before the membership ends.">
            <Num id="ms-lapse" disabled={!canEdit} value={s.membership_lapse_days} onChange={(v) => set({ membership_lapse_days: v })} unit="days" />
          </Row>
          <Row htmlFor="ms-renew" label="Renewal invoice" help="How long before a plan year ends the renewal invoice goes out.">
            <Num id="ms-renew" disabled={!canEdit} value={s.membership_renewal_notice_days} onChange={(v) => set({ membership_renewal_notice_days: v })} unit="days" />
          </Row>
        </Group>

        <Group icon="account_balance" title="Accounting">
          <Row label="Spread yearly payments over the plan's months"
            help="Off: plan money counts when it's paid. On: a yearly payment is earned month by month, and the unearned part shows as prepaid. Ask your accountant if unsure.">
            <Toggle label="Spread yearly payments" on={s.membership_spread_revenue} onChange={(v) => set({ membership_spread_revenue: v })} />
          </Row>
        </Group>

        {canEdit && (
          <div className="px-6 py-3.5 flex items-center gap-2">
            {msg && <span role="status" className={`text-xs ${msg.ok ? "text-emerald-700" : "text-rose-600"}`}>{msg.text}</span>}
            <span className="ml-auto text-[11px] text-ink-tertiary">{dirty ? "Unsaved changes" : ""}</span>
            {dirty && <button onClick={() => { setS(saved); setMsg(null); }} className="px-3 py-2 rounded-xl text-xs font-semibold text-ink-secondary hover:bg-slate-100">Discard</button>}
            <button onClick={save} disabled={busy || !dirty} className="px-5 py-2 rounded-xl bg-ink-primary text-white font-semibold text-xs hover:opacity-90 disabled:opacity-40">{busy ? "Saving…" : "Save"}</button>
          </div>
        )}
      </div>
    </section>
  );
}
