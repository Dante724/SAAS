import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { M } from "../../components/Shell";
import DateField from "../../components/DateField";
import EmptyState, { ErrorState } from "../../components/EmptyState";
import { StatTile, Badge } from "../../components/Card";
import { tableClass, theadRowClass, Th, Td } from "../../components/Table";
import { clickableRow } from "../../lib/clickable";
import { REPORT_PRESETS, isoDay, presetRange } from "./datePresets";
import { followupsApi, isMissing, moneyIn, type FollowupsReport as Report } from "../followups/followupsApi";

// Follow-ups Phase 1 — Reports › Follow-ups: the money the follow-ups brought back in a period
// (devices collected after a reminder, quotes approved after a nudge), what was sent, and a check
// that no customer was texted after collecting. Each job opens from its row.

const dayText = (iso: string) => {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "" : d.toLocaleDateString([], { day: "numeric", month: "short", year: "numeric" });
};

export default function FollowupsReport() {
  const nav = useNavigate();
  const [range, setRange] = useState<[string, string]>(() => presetRange("This month"));
  const [from, to] = range;
  const [rep, setRep] = useState<Report | null>(null);
  const [failed, setFailed] = useState<"" | "missing" | "error">("");

  const load = () => {
    let live = true;
    setRep(null); setFailed("");
    followupsApi.report(from, to).then((r) => { if (live) setRep(r); })
      .catch((e) => { if (live) setFailed(isMissing(e) ? "missing" : "error"); });
    return () => { live = false; };
  };
  useEffect(load, [from, to]);   // eslint-disable-line react-hooks/exhaustive-deps

  const preset = REPORT_PRESETS.find(([, fn]) => { const [s, e] = fn(); return isoDay(s) === from && isoDay(e) === to; })?.[0];
  const period = preset || `${dayText(from)} – ${dayText(to)}`;
  const items = rep?.items || [];

  return (
    <div className="flex-1 overflow-y-auto px-4 sm:px-6 py-5 space-y-4">
      <div className="bg-white rounded-2xl border border-slate-200/90 shadow-clay-sm p-3.5 flex items-center gap-2 flex-wrap text-xs">
        <h3 className="text-sm font-semibold text-ink-primary mr-auto">Follow-ups</h3>
        <div className="flex items-center gap-1.5 flex-wrap" aria-label="Quick periods">
          {REPORT_PRESETS.map(([label]) => (
            <button key={label} type="button" onClick={() => setRange(presetRange(label))} aria-pressed={preset === label}
              className={`px-2.5 py-1 rounded-full text-2xs font-semibold transition-colors ${preset === label ? "bg-blue-50 text-primary" : "text-ink-secondary bg-slate-100 hover:bg-blue-50 hover:text-primary"}`}>{label}</button>
          ))}
        </div>
        <div className="flex items-center gap-2">
          <DateField mode="date" label="From" value={from} max={to} onChange={(v) => v && setRange([v, to])} className="w-36" />
          <span className="text-ink-tertiary">→</span>
          <DateField mode="date" label="To" value={to} min={from} onChange={(v) => v && setRange([from, v])} className="w-36" />
        </div>
      </div>

      {!rep && !failed && <p className="text-xs text-ink-tertiary">Adding it up…</p>}
      {failed === "missing" && (
        <div className="bg-white rounded-2xl border border-slate-200/90 shadow-clay-card">
          <EmptyState icon={<M n="autorenew" c="text-base" />} title="The follow-ups report isn’t available yet"
            body="This server doesn’t have follow-ups yet. The report appears after the next Tekly update." />
        </div>
      )}
      {failed === "error" && <div className="bg-white rounded-2xl border border-slate-200/90 shadow-clay-card"><ErrorState title="The report didn’t load" onRetry={load} /></div>}

      {rep && (
        <>
          <div className="bg-white rounded-2xl border border-slate-200/90 shadow-clay-card p-5 space-y-4">
            <div>
              <p className="text-2xs font-semibold text-ink-tertiary">{period} · both follow-ups</p>
              <p className="mt-1 flex flex-wrap items-baseline gap-x-2">
                <span data-testid="recovered" className="text-3xl font-bold tabular-nums text-ink-primary">{moneyIn(rep.recovered_amount, rep.currency)}</span>
                <span className="text-sm font-medium text-ink-secondary">recovered by follow-ups</span>
              </p>
              <p className="mt-0.5 text-2xs text-ink-tertiary">Jobs paid and collected, and quotes approved, after Tekly followed up.</p>
            </div>
            <div className="grid grid-cols-2 lg:grid-cols-4 gap-2.5">
              <div className="rounded-xl bg-slate-50 px-3.5 py-3"><StatTile label="Devices collected after a reminder" value={rep.collected_after_reminder} /></div>
              <div className="rounded-xl bg-slate-50 px-3.5 py-3"><StatTile label="Quotes approved after a nudge" value={rep.quotes_approved_after_nudge} /></div>
              <div className="rounded-xl bg-slate-50 px-3.5 py-3"><StatTile label="Messages sent" value={rep.messages_sent} /></div>
              <div className="rounded-xl bg-slate-50 px-3.5 py-3">
                <StatTile label="Texts after collection" value={rep.texts_after_collection} tone={rep.texts_after_collection > 0 ? "bad" : "neutral"}
                  hint={rep.texts_after_collection > 0 ? "Should always be 0. Tell Tekly support." : "None, as it should be"} />
              </div>
            </div>
          </div>

          <div className="bg-white rounded-2xl border border-slate-200/90 shadow-clay-card overflow-hidden">
            <div className="px-4 py-3 border-b border-slate-100 flex items-center gap-2">
              <h4 className="text-sm font-semibold text-ink-primary">Each job</h4>
              {items.length > 0 && <span className="text-xs text-ink-tertiary tabular-nums">{items.length}</span>}
            </div>
            {items.length === 0 ? (
              <EmptyState icon={<M n="paid" c="text-base" />} title="Nothing recovered in this period yet"
                body="When a follow-up brings a customer back to collect or approve, the job shows up here." />
            ) : (
              <div className="overflow-x-auto">
                <table className={tableClass}>
                  <thead><tr className={theadRowClass}>
                    <Th>Job</Th><Th>Client</Th><Th>Outcome</Th><Th num>Amount</Th><Th className="hidden sm:table-cell">Date</Th>
                  </tr></thead>
                  <tbody className="divide-y divide-slate-100 text-ink-secondary">
                    {items.map((it) => (
                      <tr key={`${it.order_id}-${it.outcome}`} {...clickableRow(() => nav(`/orders?open=${encodeURIComponent(it.order_id)}`))}
                        className="cursor-pointer hover:bg-slate-50/80 focus-visible:bg-slate-50">
                        <Td className="font-semibold text-primary tabular-nums whitespace-nowrap">#{it.order_number}</Td>
                        <Td className="text-ink-primary font-medium">{it.client_name}</Td>
                        <Td>{it.outcome === "approved" ? <Badge tone="info" size="sm" dot>Quote approved</Badge> : <Badge tone="success" size="sm" dot>Collected</Badge>}</Td>
                        <Td num className="font-semibold text-ink-primary">{moneyIn(it.amount, rep.currency)}</Td>
                        <Td className="hidden sm:table-cell whitespace-nowrap">{dayText(it.outcome_at)}</Td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
}
