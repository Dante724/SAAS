import { useEffect, useState } from "react";
import { M } from "../../components/Shell";
import { api } from "../../api/client";
import { useMe, type PlanId, type PlanLimits } from "../../context/Me";

/* Settings › Billing — the shop's Tekly plan: trial days left, read-only state, Solo / Shop / Pro
   with monthly or yearly billing (yearly = 10 months), and the Stripe Customer Portal for cards,
   invoices and cancelling. Shops on custom pricing (billing_mode "custom") only see their plan:
   Tekly manages it. Data: GET /billing; actions: /billing/checkout, /billing/change-plan,
   /billing/portal. */

type Period = "month" | "year";
type Money = { month: number; year: number };
export type BillingInfo = {
  status: string; plan: PlanId; effective_plan: PlanId; period: Period;
  billing_mode: "stripe" | "custom";
  read_only: boolean; read_only_reason?: string | null;
  trial_ends_at?: string | null; trial_days_left?: number | null;
  features: string[]; limits?: PlanLimits | null;
  price_locked_until?: string | null;
  usage: { team_members: number; locations: number; jobs_per_month: number };
  suggested_plan?: PlanId | null;
  extra_seats?: number; extra_seat_price?: number | null;
  plans: { id: PlanId; name: string; limits: PlanLimits | null; features: string[] }[];
  prices: Record<PlanId, { base: Money; extra_location?: Money; extra_seat?: Money }>;
  yearly_months: number;
  has_subscription: boolean; has_billing_account: boolean;
  cancel_at_period_end: boolean; current_period_end?: string | null;
  configured: boolean;
};

type Deps = {
  load?: () => Promise<BillingInfo>;
  post?: (path: string, body?: unknown) => Promise<any>;
  go?: (url: string) => void;          // leave for Stripe (Checkout / Portal)
};

const PLAN_NAME: Record<PlanId, string> = { solo: "Solo", shop: "Shop", pro: "Pro" };
const CORE = ["Work orders, estimates and invoices", "Quick POS and customer portal",
  "Online booking", "Tekly Go phone app for techs", "One-tap texts and WhatsApp"];

const fmtDate = (iso?: string | null) => {
  if (!iso) return "";
  const d = new Date(`${iso}T00:00:00`);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
};
const money = (n: number) => `$${n.toLocaleString("en-US", { minimumFractionDigits: Number.isInteger(n) ? 0 : 2, maximumFractionDigits: 2 })}`;

/** What a plan costs this shop per period: the first location, each extra location (Shop, Pro)
 *  and each team member beyond the included pool (Shop 4, Pro 8). */
export function planCost(b: Pick<BillingInfo, "prices" | "usage" | "plans">, plan: PlanId, period: Period): number {
  const p = b.prices[plan];
  const locs = p.extra_location ? Math.max(0, (b.usage?.locations || 1) - 1) * p.extra_location[period] : 0;
  const included = b.plans.find((x) => x.id === plan)?.limits?.team_members ?? 0;
  const seats = p.extra_seat ? Math.max(0, (b.usage?.team_members || 0) - included) * p.extra_seat[period] : 0;
  return p.base[period] + locs + seats;
}

export function limitLines(l: PlanLimits | null, seatPrice?: number): string[] {
  if (!l) return ["Unlimited team members", "Unlimited locations", "Unlimited jobs"];
  return [seatPrice ? `${l.team_members} team members, shared across locations; then ${money(seatPrice)} each` : `Up to ${l.team_members} team members`,
    l.locations == null ? "Multiple locations" : l.locations === 1 ? "1 location" : `${l.locations} locations`,
    l.jobs_per_month == null ? "Unlimited jobs" : `${l.jobs_per_month} new jobs a month`];
}

export function statusLine(b: BillingInfo): { tone: "ok" | "warn" | "bad"; title: string; text: string } {
  if (b.read_only) return { tone: "bad", title: "Your shop is read-only",
    text: `${b.read_only_reason || ""} You can still view and export everything. ${b.billing_mode === "custom" ? "Contact Tekly to renew." : "Choose a plan to make changes again."}`.trim() };
  if (b.status === "trial") {
    const n = b.trial_days_left ?? 0;
    return { tone: n <= 3 ? "warn" : "ok", title: `${n} ${n === 1 ? "day" : "days"} left in your free trial`,
      text: `Every Pro feature is open until ${fmtDate(b.trial_ends_at)}. No card needed until you choose a plan.` };
  }
  if (b.billing_mode === "custom") return { tone: "ok", title: `You're on ${PLAN_NAME[b.plan]}`,
    text: `Your plan is managed by Tekly. Contact us to make changes.${b.current_period_end ? ` Renews on ${fmtDate(b.current_period_end)}.` : ""}` };
  if (b.status === "past_due") return { tone: "warn", title: "Your last payment didn't go through",
    text: "Update your card in Manage billing to keep your shop running without interruption." };
  if (b.cancel_at_period_end) return { tone: "warn", title: `${PLAN_NAME[b.plan]} ends on ${fmtDate(b.current_period_end)}`,
    text: "Your subscription is cancelled and won't renew. Reopen it in Manage billing." };
  return { tone: "ok", title: `You're on ${PLAN_NAME[b.plan]}, billed ${b.period === "year" ? "yearly" : "monthly"}`,
    text: b.current_period_end ? `Renews on ${fmtDate(b.current_period_end)}.` : "Thanks for running your shop on Tekly." };
}

export default function BillingPage({ load, post, go }: Deps = {}) {
  const { refresh } = useMe();
  const doLoad = load || (() => api.get<BillingInfo>("/billing"));
  const doPost = post || ((path: string, body?: unknown) => api.post<any>(path, body));
  const leave = go || ((url: string) => window.location.assign(url));
  const [b, setB] = useState<BillingInfo | null>(null);
  const [period, setPeriod] = useState<Period>("month");
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const returned = typeof window !== "undefined" ? new URLSearchParams(window.location.search).get("checkout") : null;

  const reload = () => doLoad().then((x) => { setB(x); if (x.has_subscription) setPeriod(x.period); })
    .catch((e) => setErr(e?.message || "Couldn't load billing"));
  useEffect(() => { reload(); }, []);   // eslint-disable-line react-hooks/exhaustive-deps

  async function act(key: string, fn: () => Promise<void>) {
    setBusy(key); setErr(null);
    try { await fn(); } catch (e: any) { setErr(e?.message || "Something went wrong"); } finally { setBusy(null); }
  }
  const choose = (plan: PlanId) => act(plan, async () => {
    if (b?.has_subscription) {
      await doPost("/billing/change-plan", { plan, period });
      await reload(); await refresh();
    } else {
      const r = await doPost("/billing/checkout", { plan, period });
      leave(r.url);
    }
  });
  const manage = () => act("portal", async () => { leave((await doPost("/billing/portal")).url); });

  if (!b) return <div className="max-w-4xl text-xs text-ink-tertiary">{err || "Loading billing…"}</div>;

  const custom = b.billing_mode === "custom";
  const st = statusLine(b);
  const tone = { ok: "bg-emerald-50 border-emerald-200 text-emerald-900", warn: "bg-amber-50 border-amber-200 text-amber-900",
    bad: "bg-rose-50 border-rose-200 text-rose-900" }[st.tone];
  const isCurrent = (plan: PlanId) => b.has_subscription && b.plan === plan && b.period === period;
  const trialPct = b.status === "trial" && !b.read_only ? Math.max(4, Math.min(100, ((14 - (b.trial_days_left ?? 0)) / 14) * 100)) : null;
  const locked = b.price_locked_until && new Date(`${b.price_locked_until}T00:00:00`) > new Date();
  const limits = b.limits;
  const usageTiles = ([["team_members", "Active team members"], ["locations", "Locations"], ["jobs_per_month", "New jobs this month"]] as const)
    .filter(([k]) => limits && limits[k] != null);

  return (
    <div className="max-w-4xl space-y-5">
      <div>
        <h2 className="text-base font-bold text-ink-primary">Billing</h2>
        <p className="text-[12px] text-ink-tertiary mt-0.5">{custom ? "Your Tekly plan." : "Your Tekly plan. Prices are in US dollars; checkout shows them in your currency and adds any tax."}</p>
      </div>

      {returned === "success" && !b.has_subscription && !custom && (
        <div role="status" className="rounded-xl border border-indigo-200 bg-indigo-50 px-4 py-3 text-[13px] text-indigo-900">
          Thanks! We're confirming your payment with Stripe. This takes a few seconds.
          <button onClick={() => { reload(); refresh(); }} className="ml-2 font-semibold underline">Check again</button>
        </div>
      )}

      <section aria-label="Current plan" className={`rounded-2xl border px-5 py-4 ${tone}`}>
        <div className="flex items-start gap-3">
          <M n={st.tone === "ok" ? "verified" : st.tone === "warn" ? "schedule" : "lock"} c="text-xl mt-0.5" />
          <div className="flex-1 min-w-0">
            <div className="text-sm font-bold">{st.title}</div>
            <p className="text-[12px] mt-0.5 opacity-90">{st.text}</p>
            {locked && <p className="text-[12px] mt-1 font-semibold">Founding price locked until {fmtDate(b.price_locked_until)}.</p>}
            {trialPct !== null && (
              <div className="mt-3 h-1.5 rounded-full bg-white/70 overflow-hidden" aria-hidden>
                <div className="h-full bg-emerald-500" style={{ width: `${trialPct}%` }} />
              </div>
            )}
            {b.has_billing_account && !custom && (
              <button onClick={manage} disabled={!!busy || !b.configured}
                className="mt-3 px-3 py-2 rounded-lg bg-white border border-slate-200 text-xs font-semibold text-ink-primary hover:border-slate-300 disabled:opacity-50">
                {busy === "portal" ? "Opening…" : "Manage billing"}
              </button>
            )}
          </div>
        </div>
      </section>

      {usageTiles.length > 0 && (
        <section aria-label="Usage" className="grid grid-cols-1 sm:grid-cols-3 gap-3">
          {usageTiles.map(([k, label]) => {
            const used = b.usage[k]; const cap = limits![k] as number;
            const seats = k === "team_members" && !!b.extra_seat_price;   // Shop and Pro: more are billed, not refused
            return (
              <div key={k} className="bg-white rounded-xl border border-slate-200/90 px-4 py-3">
                <div className="text-[11px] font-bold text-ink-tertiary">{label}</div>
                {seats
                  ? <div className="text-lg font-bold tabular-nums text-ink-primary">{used} <span className="text-xs font-semibold text-ink-tertiary">{cap} included{b.extra_seats ? ` + ${b.extra_seats} extra at ${money(b.extra_seat_price!)} a month` : ""}</span></div>
                  : <div className={`text-lg font-bold tabular-nums ${used >= cap ? "text-amber-700" : "text-ink-primary"}`}>{used} <span className="text-xs font-semibold text-ink-tertiary">of {cap}</span></div>}
              </div>
            );
          })}
        </section>
      )}

      {custom ? (
        <section aria-label="Managed plan" className="bg-white rounded-2xl border border-slate-200/90 p-5">
          <div className="text-sm font-bold text-ink-primary">{PLAN_NAME[b.plan]} plan</div>
          <ul className="mt-2 space-y-1.5">
            {limitLines(b.plans.find((p) => p.id === b.plan)?.limits ?? null, b.prices[b.plan]?.extra_seat?.month).map((t) => (
              <li key={t} className="flex gap-2 text-[12px] text-ink-secondary"><M n="check" c="text-sm text-emerald-600" />{t}</li>
            ))}
          </ul>
          <p className="text-[12px] text-ink-secondary mt-3">Your plan is managed by Tekly. Contact us to make changes.</p>
        </section>
      ) : (<>
        {!b.configured && (
          <p className="text-[12px] text-ink-secondary bg-slate-50 border border-slate-200 rounded-xl px-4 py-3">
            Online billing isn't switched on yet. Contact Tekly support to choose a plan.
          </p>
        )}
        {err && <p role="alert" className="text-[12px] font-semibold text-rose-700">{err}</p>}

        <div className="flex flex-wrap items-center justify-between gap-2">
          <h3 className="text-sm font-bold text-ink-primary">{b.has_subscription ? "Change plan" : "Choose a plan"}</h3>
          <div role="radiogroup" aria-label="Billing period" className="inline-flex p-0.5 rounded-lg bg-slate-100 text-xs font-semibold">
            {(["month", "year"] as const).map((p) => (
              <button key={p} role="radio" aria-checked={period === p} onClick={() => setPeriod(p)}
                className={`px-3 py-1.5 rounded-md ${period === p ? "bg-white shadow-sm text-ink-primary" : "text-ink-secondary"}`}>
                {p === "month" ? "Monthly" : `Yearly · ${12 - b.yearly_months} months free`}
              </button>
            ))}
          </div>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          {b.plans.map((plan, i) => {
            const pr = b.prices[plan.id];
            const points = [...limitLines(plan.limits, pr.extra_seat?.[period]),
              ...(i === 0 ? CORE : [`Everything in ${b.plans[i - 1].name}`]), ...plan.features];
            const current = isCurrent(plan.id);
            const fits = b.status === "trial" && b.suggested_plan === plan.id;
            const total = planCost(b, plan.id, period);
            return (
              <section key={plan.id} aria-label={`${plan.name} plan`}
                className={`bg-white rounded-2xl border p-5 flex flex-col ${plan.id === "shop" ? "border-primary/40 shadow-clay-card" : "border-slate-200/90"}`}>
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-sm font-bold text-ink-primary">{plan.name}</span>
                  {plan.id === "shop" && <span className="px-2 py-0.5 rounded-full bg-indigo-50 text-indigo-700 text-[11px] font-bold">Most popular</span>}
                  {fits && <span className="px-2 py-0.5 rounded-full bg-emerald-50 text-emerald-700 text-[11px] font-bold">Fits your shop</span>}
                </div>
                <div className="mt-2">
                  <span className="text-2xl font-bold text-ink-primary tabular-nums">{money(pr.base[period])}</span>
                  <span className="text-xs text-ink-tertiary"> / {period}</span>
                </div>
                <div className="text-[11px] text-ink-tertiary min-h-[2rem]">
                  {pr.extra_location ? <>First location, then {money(pr.extra_location[period])} for each extra one.</> : "One location."}
                  {total !== pr.base[period] && <> For your shop ({b.usage.locations} {b.usage.locations === 1 ? "location" : "locations"}, {b.usage.team_members} people): <span className="font-semibold text-ink-secondary">{money(total)} / {period}</span>.</>}
                </div>
                <ul className="mt-3 space-y-1.5 flex-1">
                  {points.map((t) => (
                    <li key={t} className="flex gap-2 text-[12px] text-ink-secondary"><M n="check" c="text-sm text-emerald-600" />{t}</li>
                  ))}
                </ul>
                <button onClick={() => choose(plan.id)} disabled={current || !!busy || !b.configured}
                  className={`mt-4 w-full py-2 rounded-lg text-xs font-semibold disabled:opacity-60 ${plan.id === "shop" ? "bg-primary text-white hover:bg-primary-container" : "bg-slate-900 text-white hover:bg-slate-800"}`}>
                  {current ? "Current plan" : busy === plan.id ? "One moment…" : b.has_subscription ? `Switch to ${plan.name}` : `Choose ${plan.name}`}
                </button>
              </section>
            );
          })}
        </div>
        <p className="text-[11px] text-ink-tertiary">
          Payments are handled securely by Stripe. Plan changes are prorated. Your data is never deleted if a plan lapses.
          {locked && b.has_subscription && " Switching plan or billing period moves you to today's price for it."}
          {" "}In India? Pricing is arranged with you directly. Contact us.
        </p>
      </>)}
    </div>
  );
}
