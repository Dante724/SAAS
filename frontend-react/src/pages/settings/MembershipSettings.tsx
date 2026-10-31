import { useEffect, useState } from "react";
import { M } from "../../components/Shell";
import { api } from "../../api/client";
import { useMe } from "../../context/Me";
import Switch from "../../components/Switch";
import SaveFooter from "../../components/SaveFooter";
import { SettingRow as Row, SettingGroup as Group, NumField as Num } from "../../components/SettingRow";

// Settings › Memberships: the few shop-wide choices — the checkout offer, how forgiving unpaid
// plans are, when renewals go out, and how plan money shows in the books.

type S = {
  membership_checkout_prompt: boolean; membership_prompt_threshold_pct: number; membership_prompt_snooze_days: number;
  membership_grace_days: number; membership_lapse_days: number; membership_renewal_notice_days: number; membership_spread_revenue: boolean;
};

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
          <a href="/catalog?tab=plans" className="mt-3 h-9 px-3.5 rounded-xl border border-slate-200 bg-white text-xs font-semibold text-ink-secondary hover:bg-slate-50 inline-flex items-center gap-1.5 whitespace-nowrap">
            Manage plans<M n="arrow_forward" c="text-sm" />
          </a>
        </div>
      </div>

      <div className="rounded-2xl border border-slate-200/90 bg-white shadow-clay-sm divide-y divide-slate-100">
        <Group icon="point_of_sale" title="Checkout offer">
          <Row label="Offer a plan at checkout" help="When a non-member pays, show what a plan would have saved them, with one tap to join.">
            <Switch label="Offer a plan at checkout" on={offer} onChange={(v) => set({ membership_checkout_prompt: v })} />
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
            <Switch label="Spread yearly payments" on={s.membership_spread_revenue} onChange={(v) => set({ membership_spread_revenue: v })} />
          </Row>
        </Group>

        {canEdit && <SaveFooter dirty={dirty} busy={busy} msg={msg} onSave={save} onDiscard={() => { setS(saved); setMsg(null); }} />}
      </div>
    </section>
  );
}
