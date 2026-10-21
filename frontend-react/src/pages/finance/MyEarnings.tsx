import { useCallback, useEffect, useRef, useState, type RefObject } from "react";
import Shell, { M } from "../../components/Shell";
import DateField from "../../components/DateField";
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

const STATUS: Record<Earnings["accrual_status"], { label: string; hint: string; cls: string }> = {
  not_accrued: { label: "Not accrued yet", hint: "These are live figures. They're final once your manager accrues the period.", cls: "bg-slate-100 text-ink-secondary" },
  draft: { label: "Draft", hint: "Your manager has drafted this period but not accrued it yet.", cls: "bg-amber-50 text-amber-800" },
  accrued: { label: "Accrued", hint: "This period is accrued and waiting to be paid.", cls: "bg-blue-50 text-primary" },
  paid: { label: "Paid", hint: "This period has been paid out.", cls: "bg-emerald-50 text-emerald-700" },
};

const longDate = (iso: string) => new Date(`${iso}T00:00:00`).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
// "Oct 1 – 31, 2026" style ranges read faster than two full dates.
function rangeText(a: string, b: string) {
  const x = new Date(`${a}T00:00:00`), y = new Date(`${b}T00:00:00`);
  if (x.getFullYear() !== y.getFullYear()) return `${longDate(a)} – ${longDate(b)}`;
  const md = (d: Date) => d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
  return `${md(x)} – ${md(y)}, ${y.getFullYear()}`;
}
const signed = (n: number) => `${n < 0 ? "−" : ""}${money(Math.abs(n))}`;

// Pay run stages, shown as the thin segmented bar the customer tracking page uses.
const RUN_STAGES = ["Estimated", "Accrued", "Paid"] as const;
const runStage = (status: string) => (status === "paid" ? 2 : status === "draft" ? 0 : 1);

type Stat = { label: string; value: string; hint?: string; go?: () => void };

export function EarningsView({ data }: { data: Earnings }) {
  const st = STATUS[data.accrual_status] || STATUS.not_accrued;
  const commRef = useRef<HTMLDivElement>(null);
  const adjRef = useRef<HTMLDivElement>(null);
  const go = (r: RefObject<HTMLDivElement>) => () => r.current?.scrollIntoView?.({ behavior: "smooth", block: "start" });

  // At most three figures under the total, and only the ones that aren't zero.
  const base = (data.base_salary || 0) + (data.hourly_total || 0);
  const net = (data.bonuses_total || 0) - (data.penalties_total || 0);
  const stats: Stat[] = [];
  if (base) stats.push({
    label: data.base_salary && data.hourly_total ? "Base pay" : data.hourly_total ? "Hourly" : "Base salary",
    value: money(base),
    hint: data.base_salary && data.hourly_total ? `${money(data.base_salary)} salary, ${money(data.hourly_total)} hourly` : undefined,
  });
  if (data.commissions_total) stats.push({ label: "Commissions", value: signed(data.commissions_total), hint: `${data.commissions.length} ${data.commissions.length === 1 ? "rule" : "rules"}`, go: go(commRef) });
  if (data.bonuses_total || data.penalties_total) stats.push({
    label: data.bonuses_total && data.penalties_total ? "Bonuses and penalties" : data.bonuses_total ? "Bonuses" : "Penalties",
    value: data.bonuses_total && data.penalties_total ? `${net > 0 ? "+" : ""}${signed(net)}` : data.bonuses_total ? money(data.bonuses_total) : `−${money(data.penalties_total)}`,
    hint: data.bonuses_total && data.penalties_total ? `${money(data.bonuses_total)} bonuses, −${money(data.penalties_total)} penalties` : undefined,
    go: go(adjRef),
  });

  const adjustments = [...data.bonuses.map((b) => ({ ...b, kind: "bonus" as const })), ...data.penalties.map((p) => ({ ...p, kind: "penalty" as const }))]
    .sort((a, b) => b.date.localeCompare(a.date));
  return (
    <div className="space-y-4">
      {data.visible_from && (
        <div role="note" className="rounded-xl border border-slate-200 bg-white text-ink-secondary text-xs px-3.5 py-2.5 flex items-start gap-2">
          <M n="history" c="text-sm mt-px text-ink-tertiary" />Your role shows pay from {longDate(data.visible_from)} onwards, so earlier days aren't included.
        </div>
      )}

      <section aria-label="Summary" className="bg-white rounded-2xl border border-slate-200/90">
        <div className="px-5 pt-5 pb-4 flex flex-wrap items-start gap-x-6 gap-y-3">
          <div className="min-w-0">
            <p className="text-xs font-medium text-ink-tertiary">Earned {rangeText(data.period_start, data.period_end)}</p>
            <p className="mt-1 text-[32px] leading-none font-semibold tracking-tight tabular-nums text-ink-primary" data-testid="earnings-total">{signed(data.total)}</p>
          </div>
          <div className="sm:ml-auto sm:text-right max-w-xs">
            <span className={`inline-flex px-2.5 py-0.5 rounded-full text-xs font-semibold ${st.cls}`}>{st.label}</span>
            <p className="text-xs text-ink-tertiary mt-1.5">{st.hint}</p>
          </div>
        </div>
        {stats.length > 0 && (
          <dl data-testid="earnings-stats" className="border-t border-slate-100 flex flex-wrap sm:flex-nowrap sm:divide-x divide-slate-100">
            {stats.map((x) => {
              const body = <><dt className="text-xs font-medium text-ink-tertiary">{x.label}</dt>
                <dd className="mt-0.5 text-lg font-semibold tabular-nums text-ink-primary whitespace-nowrap">{x.value}</dd>
                {x.hint && <dd className="text-xs text-ink-tertiary truncate">{x.hint}</dd>}</>;
              return x.go
                ? <button key={x.label} type="button" onClick={x.go} className="flex-1 min-w-[150px] text-left px-5 py-3.5 hover:bg-slate-50/80 transition-colors last:rounded-br-2xl first:rounded-bl-2xl focus:outline-none focus-visible:ring-2 focus-visible:ring-primary/30">{body}</button>
                : <div key={x.label} className="flex-1 min-w-[150px] px-5 py-3.5">{body}</div>;
            })}
          </dl>
        )}
      </section>

      <section aria-label="Breakdown" className="bg-white rounded-2xl border border-slate-200/90 overflow-hidden">
        <div ref={commRef} className="scroll-mt-4 px-4 py-3.5 border-b border-slate-100 flex items-baseline gap-2">
          <h3 className="text-sm font-semibold text-ink-primary">Commissions</h3>
          <span className="hidden sm:inline text-xs text-ink-tertiary">By rule, then job, then line item</span>
          {(data.commissions.length > 0 || !!data.commissions_total) && <span className="ml-auto text-sm font-semibold tabular-nums text-ink-primary">{signed(data.commissions_total)}</span>}
        </div>
        <EarningsBreakdown rules={data.commissions} empty="No commissions in this period yet." />
        <div ref={adjRef} className="scroll-mt-4 px-4 py-3.5 border-y border-slate-100 flex items-baseline gap-2">
          <h3 className="text-sm font-semibold text-ink-primary">Bonuses and penalties</h3>
          {adjustments.length > 0 && <span className="ml-auto text-sm font-semibold tabular-nums text-ink-primary">{net > 0 ? "+" : ""}{signed(net)}</span>}
        </div>
        {adjustments.length === 0 ? <div className="px-4 py-6 text-xs text-ink-tertiary text-center">None in this period.</div> : (
          <ul className="divide-y divide-slate-100">
            {adjustments.map((a) => (
              <li key={a.id} className="px-4 py-3 flex items-center gap-3 text-sm">
                <span className="min-w-0 flex-1"><span className="block text-ink-primary truncate">{a.note || (a.kind === "bonus" ? "Bonus" : "Penalty")}</span>
                  <span className="block text-xs text-ink-tertiary">{a.kind === "bonus" ? "Bonus" : "Penalty"} · {longDate(a.date.slice(0, 10))}</span></span>
                <span className="tabular-nums font-semibold text-ink-primary whitespace-nowrap">{a.kind === "bonus" ? "+" : "−"}{money(a.amount)}</span>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section aria-label="Pay runs" className="bg-white rounded-2xl border border-slate-200/90 overflow-hidden">
        <div className="px-4 py-3.5 border-b border-slate-100"><h3 className="text-sm font-semibold text-ink-primary">Pay runs</h3></div>
        {data.accruals.length === 0 ? <div className="px-4 py-6 text-xs text-ink-tertiary text-center">Nothing accrued for this period yet.</div> : (
          <ul className="divide-y divide-slate-100">
            {data.accruals.map((r) => {
              const at = runStage(r.status);
              const caption = at === 2 ? (r.paid_at ? `Paid ${longDate(r.paid_at.slice(0, 10))}` : "Paid") : at === 1 ? "Accrued, waiting to be paid" : "Estimated, not accrued yet";
              return (
                <li key={r.id} data-testid="pay-run" className="px-4 py-3.5">
                  <div className="flex items-baseline gap-3">
                    <span className="min-w-0 flex-1 text-sm font-medium text-ink-primary">{rangeText(r.period_start, r.period_end)}</span>
                    <span className="tabular-nums text-sm font-semibold text-ink-primary">{money(r.gross_total)}</span>
                  </div>
                  <div className="mt-2.5 flex gap-1" role="progressbar" aria-label={`Pay run ${rangeText(r.period_start, r.period_end)}`} aria-valuemin={1} aria-valuemax={3} aria-valuenow={at + 1} aria-valuetext={RUN_STAGES[at]}>
                    {RUN_STAGES.map((_, i) => <span key={i} className={`h-1 flex-1 rounded-full ${i <= at ? "bg-primary" : "bg-slate-200"}`} />)}
                  </div>
                  <div className="mt-1.5 hidden sm:flex">
                    {RUN_STAGES.map((l, i) => <span key={l} className={`flex-1 text-xs ${i === at ? "font-semibold text-ink-primary" : "text-ink-tertiary"}`}>{l}</span>)}
                  </div>
                  <p className="mt-1.5 text-xs text-ink-tertiary sm:mt-1">{caption}</p>
                </li>
              );
            })}
          </ul>
        )}
      </section>
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
      <div className="flex-1 overflow-y-auto px-4 sm:px-6 py-5 space-y-4 max-w-4xl w-full mx-auto">
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
              <span className="text-ink-tertiary text-xs" aria-hidden>–</span>
              <DateField mode="date" value={to} onChange={(v) => setRange([from, v])} placeholder="To" className="w-36" />
            </div>
          )}
        </div>
        {badRange && <div className="text-xs text-rose-600">The start date is after the end date.</div>}

        {loading && !data && (
          <div className="space-y-4" aria-busy="true" aria-label="Loading your earnings">
            <div className="h-40 rounded-2xl bg-slate-100 animate-pulse" />
            <div className="h-56 rounded-2xl bg-slate-100 animate-pulse" />
            <div className="h-24 rounded-2xl bg-slate-100 animate-pulse" />
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
