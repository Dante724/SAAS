import { useCallback, useEffect, useState } from "react";
import Shell, { M } from "../../components/Shell";
import DateField from "../../components/DateField";
import PageSummary, { type SummaryStat } from "../../components/PageSummary";
import { api } from "../../api/client";
import { money } from "../orders/types";
import { isoDate } from "../../lib/localDate";
import EarningsBreakdown, { type BreakdownRule } from "./EarningsBreakdown";

/* My earnings — what the signed-in person earned in a period, and where every amount came
   from. Reached from the account menu and from Finance. Only ever their own figures
   (GET /payroll/me); the role's pay-history limit decides how far back they can look. */

type Adj = { id: string; date: string; amount: number; note: string };
type Accrual = { id: string; period_start: string; period_end: string; status: string; gross_total: number; paid_at: string | null };
export type Earnings = {
  employee_id: string; name: string; wage_type: string | null;
  period_start: string; period_end: string; visible_from: string | null;
  base_salary: number; hourly_total: number; commissions_total: number; bonuses_total: number; penalties_total: number; total: number;
  accrual_status: "not_accrued" | "draft" | "accrued" | "paid";
  accruals: Accrual[]; commissions: BreakdownRule[]; bonuses: Adj[]; penalties: Adj[];
};

type Preset = "this" | "last" | "custom";

function monthRange(offset: number): [string, string] {
  const now = new Date();
  const first = new Date(now.getFullYear(), now.getMonth() + offset, 1);
  const last = new Date(now.getFullYear(), now.getMonth() + offset + 1, 0);
  return [isoDate(first), isoDate(last)];
}

const STATUS: Record<Earnings["accrual_status"], { label: string; hint: string; cls: string; icon: string }> = {
  not_accrued: { label: "Not accrued yet", hint: "These are live figures. They're final once your manager accrues the period.", cls: "bg-slate-100 text-ink-secondary", icon: "hourglass_empty" },
  draft: { label: "Draft", hint: "Your manager has drafted this period but not accrued it yet.", cls: "bg-amber-50 text-amber-700", icon: "edit_note" },
  accrued: { label: "Accrued", hint: "This period is accrued and waiting to be paid.", cls: "bg-blue-50 text-primary", icon: "event_available" },
  paid: { label: "Paid", hint: "This period has been paid out.", cls: "bg-emerald-50 text-emerald-700", icon: "verified" },
};

const longDate = (iso: string) => new Date(`${iso}T00:00:00`).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });

export function EarningsView({ data }: { data: Earnings }) {
  const st = STATUS[data.accrual_status] || STATUS.not_accrued;
  const stats: SummaryStat[] = [
    { label: "Base salary", value: money(data.base_salary), zero: !data.base_salary },
    { label: "Hourly", value: money(data.hourly_total), zero: !data.hourly_total },
    { label: "Commissions", value: money(data.commissions_total), zero: !data.commissions_total, tone: data.commissions_total < 0 ? "bad" : undefined },
    { label: "Bonuses", value: money(data.bonuses_total), zero: !data.bonuses_total },
    { label: "Penalties", value: data.penalties_total ? `−${money(data.penalties_total)}` : money(0), tone: data.penalties_total ? "bad" : undefined, zero: !data.penalties_total },
  ];
  const adjustments = [...data.bonuses.map((b) => ({ ...b, kind: "bonus" as const })), ...data.penalties.map((p) => ({ ...p, kind: "penalty" as const }))]
    .sort((a, b) => b.date.localeCompare(a.date));
  return (
    <div className="space-y-4">
      {data.visible_from && (
        <div role="note" className="rounded-xl border border-amber-200 bg-amber-50 text-amber-800 text-xs px-3.5 py-2.5 flex items-start gap-2">
          <M n="history" c="text-sm mt-px" />Your role shows pay from {longDate(data.visible_from)} onwards, so earlier days aren't included.
        </div>
      )}
      <section className="bg-white rounded-2xl border border-slate-200/90 p-5 flex flex-wrap items-end gap-4">
        <div className="min-w-0">
          <p className="text-xs text-ink-tertiary">Earned {longDate(data.period_start)} – {longDate(data.period_end)}</p>
          <p className={`text-3xl font-semibold tabular-nums ${data.total < 0 ? "text-rose-600" : "text-ink-primary"}`} data-testid="earnings-total">{data.total < 0 ? "−" : ""}{money(Math.abs(data.total))}</p>
        </div>
        <div className="sm:ml-auto sm:text-right max-w-xs">
          <span className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-semibold ${st.cls}`}><M n={st.icon} c="text-sm" />{st.label}</span>
          <p className="text-[11px] text-ink-tertiary mt-1">{st.hint}</p>
        </div>
      </section>

      <PageSummary stats={stats} />

      <section className="bg-white rounded-2xl border border-slate-200/90 overflow-hidden">
        <div className="px-4 py-3 border-b border-slate-100 flex items-center gap-2">
          <h3 className="text-sm font-semibold text-ink-primary">Commissions</h3>
          <span className="hidden sm:inline text-[11px] text-ink-tertiary">By rule, then job, then line item</span>
          <span className="ml-auto text-sm font-semibold tabular-nums text-ink-primary">{money(data.commissions_total)}</span>
        </div>
        <EarningsBreakdown rules={data.commissions} empty="No commissions in this period yet." />
      </section>

      <div className="grid gap-4 lg:grid-cols-2">
        <section className="bg-white rounded-2xl border border-slate-200/90 overflow-hidden">
          <div className="px-4 py-3 border-b border-slate-100 text-sm font-semibold text-ink-primary">Bonuses and penalties</div>
          {adjustments.length === 0 ? <div className="px-4 py-6 text-xs text-ink-tertiary text-center">None in this period.</div> : (
            <ul className="divide-y divide-slate-100">
              {adjustments.map((a) => (
                <li key={a.id} className="px-4 py-2.5 flex items-center gap-3 text-xs">
                  <span className={`w-7 h-7 rounded-lg grid place-items-center flex-shrink-0 ${a.kind === "bonus" ? "bg-emerald-50 text-emerald-700" : "bg-rose-50 text-rose-600"}`}><M n={a.kind === "bonus" ? "redeem" : "flag"} c="text-sm" /></span>
                  <span className="min-w-0 flex-1"><span className="block text-ink-primary truncate">{a.note || (a.kind === "bonus" ? "Bonus" : "Penalty")}</span><span className="text-[11px] text-ink-tertiary">{longDate(a.date.slice(0, 10))}</span></span>
                  <span className={`tabular-nums font-semibold ${a.kind === "bonus" ? "text-emerald-700" : "text-rose-600"}`}>{a.kind === "bonus" ? "+" : "−"}{money(a.amount)}</span>
                </li>
              ))}
            </ul>
          )}
        </section>
        <section className="bg-white rounded-2xl border border-slate-200/90 overflow-hidden">
          <div className="px-4 py-3 border-b border-slate-100 text-sm font-semibold text-ink-primary">Pay runs</div>
          {data.accruals.length === 0 ? <div className="px-4 py-6 text-xs text-ink-tertiary text-center">Nothing accrued for this period yet.</div> : (
            <ul className="divide-y divide-slate-100">
              {data.accruals.map((r) => {
                const s = STATUS[(r.status === "paid" ? "paid" : r.status === "draft" ? "draft" : "accrued")];
                return (
                  <li key={r.id} className="px-4 py-2.5 flex items-center gap-3 text-xs">
                    <span className="min-w-0 flex-1 text-ink-primary">{longDate(r.period_start)} – {longDate(r.period_end)}{r.paid_at && <span className="block text-[11px] text-ink-tertiary">Paid {longDate(r.paid_at.slice(0, 10))}</span>}</span>
                    <span className={`px-2 py-0.5 rounded-full text-[11px] font-semibold ${s.cls}`}>{s.label}</span>
                    <span className="tabular-nums font-semibold text-ink-primary">{money(r.gross_total)}</span>
                  </li>
                );
              })}
            </ul>
          )}
        </section>
      </div>
    </div>
  );
}

export default function MyEarnings() {
  const [preset, setPreset] = useState<Preset>("this");
  const [[from, to], setRange] = useState<[string, string]>(() => monthRange(0));
  const [data, setData] = useState<Earnings | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async (f: string, t: string) => {
    setLoading(true); setErr(null);
    try { setData(await api.get<Earnings>(`/payroll/me?from=${f}&to=${t}`)); }
    catch (e: any) { setErr(e?.status === 403 ? "Your role doesn't show your earnings. Ask your manager to turn on “See their own earnings”." : (e?.message || "Couldn't load your earnings.")); setData(null); }
    finally { setLoading(false); }
  }, []);
  useEffect(() => { if (from && to && from <= to) load(from, to); }, [from, to, load]);

  const pick = (p: Preset) => {
    setPreset(p);
    if (p === "this") setRange(monthRange(0));
    if (p === "last") setRange(monthRange(-1));
  };
  const badRange = !!from && !!to && from > to;

  return (
    <Shell title="My earnings">
      <div className="flex-1 overflow-y-auto px-4 sm:px-6 py-5 space-y-4 max-w-5xl w-full mx-auto">
        <div className="flex flex-wrap items-center gap-2">
          <div className="inline-flex rounded-xl bg-slate-100 p-1" role="group" aria-label="Period">
            {([["this", "This month"], ["last", "Last month"], ["custom", "Custom"]] as [Preset, string][]).map(([v, l]) => (
              <button key={v} type="button" onClick={() => pick(v)} aria-pressed={preset === v}
                className={`px-3 py-1.5 rounded-lg text-xs font-semibold transition-colors ${preset === v ? "bg-white text-ink-primary shadow-sm" : "text-ink-secondary hover:text-ink-primary"}`}>{l}</button>
            ))}
          </div>
          {preset === "custom" && (
            <div className="flex items-center gap-1.5">
              <DateField mode="date" value={from} onChange={(v) => setRange([v, to])} placeholder="From" className="w-36" />
              <span className="text-ink-tertiary text-xs">→</span>
              <DateField mode="date" value={to} onChange={(v) => setRange([from, v])} placeholder="To" className="w-36" />
            </div>
          )}
        </div>
        {badRange && <div className="text-xs text-rose-600">The start date is after the end date.</div>}

        {loading && !data && (
          <div className="space-y-4" aria-busy="true" aria-label="Loading your earnings">
            <div className="h-24 rounded-2xl bg-slate-100 animate-pulse" />
            <div className="h-20 rounded-2xl bg-slate-100 animate-pulse" />
            <div className="h-48 rounded-2xl bg-slate-100 animate-pulse" />
          </div>
        )}
        {err && !loading && (
          <div role="alert" className="rounded-2xl border border-rose-200 bg-rose-50 p-6 text-center">
            <p className="text-sm text-rose-700">{err}</p>
            <button onClick={() => load(from, to)} className="mt-3 px-3.5 py-1.5 rounded-lg bg-white border border-rose-200 text-xs font-semibold text-rose-700 hover:bg-rose-100">Try again</button>
          </div>
        )}
        {data && <div className={loading ? "opacity-60 transition-opacity" : ""}><EarningsView data={data} /></div>}
      </div>
    </Shell>
  );
}
