// Tekly Go shop pilot report (owners and admins): per person, how many status changes and photos
// on the pilot's jobs were made on a phone, messages sent from Go, scans, and how the counter
// looked jobs up. The headline is the share of status changes made on a phone against the 50%
// decision rule. See docs/pilot/tekly-go-shop-pilot.md.
import { useEffect, useState } from "react";
import { api, openAuthedDoc } from "../../api/client";
import { alertDialog } from "../../lib/dialog";

type Counts = { total: number; go: number };
export type PilotRow = {
  employee_id: string; name: string;
  status_changes: Counts & { share_go: number | null };
  photos: Counts; messages: Counts;
  scans: { total: number; on_pilot_jobs: number };
  counter_lookups: Record<string, number>;
};
export type PilotReport = {
  range: { from: string; to: string };
  jobs: { source: string; count: number };
  headline: { status_changes_total: number; status_changes_go: number; share_status_changes_on_phone: number | null;
              photos_total: number; photos_go: number; threshold: number; verdict: string; reading: string };
  counter_lookups: Record<string, number>;
  employees: PilotRow[];
};

const LOOKUP_KINDS: [string, string][] = [["name", "Name"], ["phone", "Phone"], ["wo", "Ticket no."], ["last4", "Last 4"], ["scan", "Scanner"], ["other", "Other"]];
const iso = (d: Date) => d.toISOString().slice(0, 10);
export const pct = (x: number | null | undefined) => (x == null ? "—" : `${Math.round(x * 100)}%`);

export function reportQuery(from: string, to: string, extra = ""): string {
  const p = new URLSearchParams({ from, to });
  if (extra) p.set("format", extra);
  return `/pilot/go-report?${p}`;
}

export default function GoPilotReport() {
  const [to, setTo] = useState(iso(new Date()));
  const [from, setFrom] = useState(iso(new Date(Date.now() - 6 * 864e5)));
  const [rep, setRep] = useState<PilotReport | null>(null);
  const [err, setErr] = useState("");

  useEffect(() => {
    let live = true;
    setErr("");
    api.get<PilotReport>(reportQuery(from, to)).then((r) => { if (live) setRep(r); })
      .catch((e) => { if (live) { setRep(null); setErr(e?.message || "Couldn't load the report."); } });
    return () => { live = false; };
  }, [from, to]);

  async function csv() {
    try { await openAuthedDoc(reportQuery(from, to, "csv")); } catch (e: any) { await alertDialog(e?.message || "Couldn't download the CSV."); }
  }

  const h = rep?.headline;
  const field = "text-xs px-2 py-1.5 rounded-lg border border-slate-200 bg-white";
  const th = "py-2 px-3 text-left text-[11px] font-semibold text-ink-tertiary whitespace-nowrap";
  const td = "py-2 px-3 text-xs tabular-nums whitespace-nowrap";
  return (
    <div className="flex-1 overflow-y-auto px-6 py-5 space-y-4">
      <div className="flex flex-wrap items-end gap-3">
        <label className="space-y-1"><span className="block text-[11px] font-semibold text-ink-secondary">From</span>
          <input type="date" value={from} max={to} onChange={(e) => setFrom(e.target.value)} className={field} /></label>
        <label className="space-y-1"><span className="block text-[11px] font-semibold text-ink-secondary">To</span>
          <input type="date" value={to} min={from} onChange={(e) => setTo(e.target.value)} className={field} /></label>
        <button type="button" onClick={csv} className="px-3 py-2 rounded-xl text-xs font-semibold border border-slate-200 hover:bg-slate-50">Download CSV</button>
        <p className="text-[11px] text-ink-tertiary">Dates are UTC. Jobs: those that got a pilot QR label.</p>
      </div>

      {err && <p className="text-sm text-red-700">{err}</p>}

      {h && rep && (
        <div className="bg-white rounded-2xl border border-slate-200 p-5 space-y-2">
          <div className="text-[11px] font-semibold text-ink-tertiary">Status changes made on a phone · {rep.jobs.count} pilot job{rep.jobs.count === 1 ? "" : "s"}</div>
          <div className="flex items-baseline gap-3">
            <span className="text-3xl font-bold tabular-nums" data-testid="pilot-share">{pct(h.share_status_changes_on_phone)}</span>
            <span className="text-sm text-ink-secondary tabular-nums">{h.status_changes_go} of {h.status_changes_total} · decision line {pct(h.threshold)}</span>
          </div>
          <p className="text-sm text-ink-primary">{h.reading}</p>
          <p className="text-xs text-ink-tertiary tabular-nums">Photos on a phone: {h.photos_go} of {h.photos_total}</p>
        </div>
      )}

      {rep && (
        <div className="bg-white rounded-2xl border border-slate-200 overflow-x-auto">
          <table className="w-full">
            <thead className="border-b border-slate-100">
              <tr>
                <th className={th}>Person</th><th className={th}>Status changes (phone / all)</th><th className={th}>Photos (phone / all)</th>
                <th className={th}>Messages from Go</th><th className={th}>Scans</th>
                {LOOKUP_KINDS.map(([k, l]) => <th key={k} className={th}>Lookups: {l}</th>)}
              </tr>
            </thead>
            <tbody>
              {rep.employees.length === 0 && <tr><td className={`${td} text-ink-tertiary`} colSpan={5 + LOOKUP_KINDS.length}>Nothing recorded in this range yet.</td></tr>}
              {rep.employees.map((r) => (
                <tr key={r.employee_id} className="border-b border-slate-50">
                  <td className={`${td} font-semibold`}>{r.name}</td>
                  <td className={td}>{r.status_changes.go} / {r.status_changes.total} <span className="text-ink-tertiary">({pct(r.status_changes.share_go)})</span></td>
                  <td className={td}>{r.photos.go} / {r.photos.total}</td>
                  <td className={td}>{r.messages.go}</td>
                  <td className={td}>{r.scans.total}</td>
                  {LOOKUP_KINDS.map(([k]) => <td key={k} className={td}>{r.counter_lookups[k] ?? 0}</td>)}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
