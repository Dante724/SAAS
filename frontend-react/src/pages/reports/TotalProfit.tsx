import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../../api/client";
import DateField from "../../components/DateField";
import { M } from "../../components/Shell";
import { money } from "../orders/types";
import { clickableRow } from "../../lib/clickable";
import { KIND_LABEL, drillRows, trend, type Drill, type ProfitRow } from "./profitView";

type Bucket = { count: number; revenue: number; tax: number; cogs?: number; labour_cost: number; commission: number; profit?: number; written_off: number };
type Group = Bucket & { name: string; jobs: number; dropped: number; sales: number; location_id?: string | null; employee_id?: string | null };
type Report = {
  totals: Bucket & { margin_pct?: number };
  buckets: { jobs: Bucket; dropped: Bucket; sales: Bucket };
  previous: Bucket & { buckets: { jobs: Bucket; dropped: Bucket; sales: Bucket } };
  by_location: Group[]; by_technician: Group[]; rows: ProfitRow[];
};

const iso = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
const n = (v: number | undefined) => (typeof v === "number" ? v : null);

function TrendChip({ cur, prev }: { cur: number | null; prev: number | null }) {
  if (cur == null || prev == null) return null;
  const t = trend(cur, prev);
  const tone = t.dir === "up" ? "text-emerald-700 bg-emerald-50" : t.dir === "down" ? "text-rose-700 bg-rose-50" : "text-ink-secondary bg-slate-100";
  return (
    <span className={`inline-flex items-center gap-0.5 rounded-full px-2 py-0.5 text-[11px] font-semibold ${tone}`} title={`Last period: ${money(prev)}`}>
      <M n={t.dir === "up" ? "trending_up" : t.dir === "down" ? "trending_down" : "trending_flat"} c="text-[13px]" />{t.label}
    </span>
  );
}

export default function TotalProfit({ locs }: { locs: { id: string; name: string }[] }) {
  const nav = useNavigate();
  const [from, setFrom] = useState(iso(new Date(Date.now() - 29 * 864e5)));
  const [to, setTo] = useState(iso(new Date()));
  const [locId, setLocId] = useState("");
  const [techId, setTechId] = useState("");
  const [techs, setTechs] = useState<{ id: string; name: string }[]>([]);
  const [rep, setRep] = useState<Report | null>(null);
  const [err, setErr] = useState("");
  const [drill, setDrill] = useState<{ title: string; d: Drill } | null>(null);

  useEffect(() => { api.get<any>("/staff/employees").then((r) => setTechs(r.items || r || [])).catch(() => setTechs([])); }, []);
  function load(f = from, t = to) {
    setRep(null); setErr("");
    const qs = new URLSearchParams({ date_from: f, date_to: t });
    if (locId) qs.set("location_id", locId);
    if (techId) qs.set("technician_id", techId);
    api.get<Report>(`/reports/total-profit?${qs.toString()}`).then(setRep).catch((e) => setErr(e?.message || "Couldn't load the report."));
  }
  useEffect(() => { load(); }, [locId, techId]);   // eslint-disable-line react-hooks/exhaustive-deps

  const costsHidden = !!rep && rep.totals.profit === undefined;
  const cards: { key: "jobs" | "dropped" | "sales"; title: string; hint: string; icon: string }[] = [
    { key: "jobs", title: "Jobs", hint: "Closed jobs", icon: "build" },
    { key: "dropped", title: "Dropped jobs", hint: "Money kept from jobs that weren't finished", icon: "block" },
    { key: "sales", title: "Counter sales", hint: "Sales without a job", icon: "point_of_sale" },
  ];
  const kindOf = { jobs: "job", dropped: "dropped", sales: "sale" } as const;
  const shown = rep && drill ? drillRows(rep.rows, drill.d) : [];

  return (
    <div className="space-y-4">
      <div className="bg-white rounded-2xl border border-slate-200/90 shadow-clay-sm p-3.5 flex items-center gap-2 flex-wrap text-xs">
        <h3 className="text-sm font-bold text-ink-primary mr-auto">Total profit</h3>
        {locs.length > 1 && (
          <select aria-label="Location" value={locId} onChange={(e) => setLocId(e.target.value)} className="glass-input px-2.5 py-1.5 rounded-lg">
            <option value="">All locations</option>
            {locs.map((l) => <option key={l.id} value={l.id}>{l.name}</option>)}
          </select>
        )}
        <select aria-label="Technician" value={techId} onChange={(e) => setTechId(e.target.value)} className="glass-input px-2.5 py-1.5 rounded-lg">
          <option value="">Everyone</option>
          {techs.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
        </select>
        <DateField mode="date" value={from} onChange={setFrom} className="w-36" />
        <span className="text-ink-tertiary">→</span>
        <DateField mode="date" value={to} onChange={setTo} className="w-36" />
        <button onClick={() => load()} className="px-3 py-1.5 rounded-lg bg-primary text-white text-xs font-bold shadow-clay-sm hover:brightness-110">Show</button>
      </div>

      {err && <div role="alert" className="text-xs text-rose-600">{err}</div>}
      {!rep && !err && <div className="text-xs text-ink-tertiary">Adding it up…</div>}

      {rep && (
        <>
          <div className="bg-white rounded-2xl border border-slate-200/90 shadow-clay-card p-4 flex flex-wrap items-end gap-x-8 gap-y-3">
            <div>
              <p className="text-[11px] font-bold text-ink-tertiary">Profit for the period</p>
              <p className={`text-3xl font-bold tabular-nums ${(n(rep.totals.profit) ?? 0) < 0 ? "text-rose-600" : "text-ink-primary"}`}>
                {costsHidden ? "—" : money(rep.totals.profit)}
              </p>
              {!costsHidden && <div className="mt-1"><TrendChip cur={n(rep.totals.profit)} prev={n(rep.previous.profit)} /></div>}
            </div>
            <div className="text-xs text-ink-secondary space-y-0.5">
              <p>Takings after tax <span className="font-semibold text-ink-primary tabular-nums">{money(rep.totals.revenue)}</span></p>
              <p>Tax collected <span className="font-semibold text-ink-primary tabular-nums">{money(rep.totals.tax)}</span></p>
              <p>Commissions <span className="font-semibold text-ink-primary tabular-nums">{money(rep.totals.commission)}</span></p>
              {rep.totals.written_off > 0 && <p>Written off on dropped jobs <span className="font-semibold text-ink-primary tabular-nums">{money(rep.totals.written_off)}</span></p>}
            </div>
            {costsHidden && <p className="text-[11px] text-ink-tertiary max-w-xs">Profit and costs are hidden for your role. Ask the owner for "View the purchase price".</p>}
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
            {cards.map((c) => {
              const b = rep.buckets[c.key];
              const pb = rep.previous.buckets[c.key];
              return (
                <button key={c.key} type="button" onClick={() => setDrill({ title: c.title, d: { kind: kindOf[c.key] } })}
                  className="text-left bg-white rounded-2xl border border-slate-200/90 shadow-clay-sm p-3.5 hover:border-primary/40 hover:shadow-clay-card transition-colors">
                  <div className="flex items-center gap-2">
                    <M n={c.icon} c="text-base text-primary" />
                    <span className="text-xs font-bold text-ink-primary">{c.title}</span>
                    <span className="ml-auto text-[11px] text-ink-tertiary">{b.count}</span>
                  </div>
                  <p className="mt-2 text-xl font-bold tabular-nums text-ink-primary">{costsHidden ? money(b.revenue) : money(b.profit)}</p>
                  <p className="text-[11px] text-ink-tertiary">{costsHidden ? "Takings after tax" : `Profit on ${money(b.revenue)} taken`}</p>
                  {!costsHidden && <div className="mt-1.5"><TrendChip cur={n(b.profit)} prev={n(pb.profit)} /></div>}
                  <p className="mt-1.5 text-[11px] text-ink-tertiary">{c.hint}</p>
                </button>
              );
            })}
          </div>

          <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
            {([["By location", rep.by_location, "location_id"], ["By technician", rep.by_technician, "employee_id"]] as const).map(([title, list, key]) => (
              <div key={title} className="bg-white rounded-2xl border border-slate-200/90 shadow-clay-sm overflow-hidden">
                <div className="p-3 border-b border-slate-100 text-xs font-bold text-ink-primary">{title}</div>
                <table className="w-full text-left text-xs">
                  <thead><tr className="text-[11px] font-bold text-ink-tertiary bg-slate-50/60">
                    <th className="py-2 px-3">Name</th><th className="py-2 px-3 text-right">Jobs · sales</th>
                    <th className="py-2 px-3 text-right">Taken</th><th className="py-2 px-3 text-right">{costsHidden ? "" : "Profit"}</th>
                  </tr></thead>
                  <tbody className="divide-y divide-slate-100">
                    {list.length === 0 && <tr><td colSpan={4} className="py-4 px-3 text-ink-tertiary">Nothing in this period.</td></tr>}
                    {list.map((g, i) => (
                      <tr key={i} {...clickableRow(() => setDrill({ title: `${key === "location_id" ? "Location" : "Technician"}: ${key === "employee_id" && !g.employee_id ? "Nobody assigned" : g.name}`, d: { [key]: (g as any)[key] ?? "" } }))}
                        className="cursor-pointer hover:bg-blue-50/50">
                        <td className="py-2 px-3 font-semibold text-ink-primary">{key === "employee_id" && !g.employee_id ? "Nobody assigned" : g.name}</td>
                        <td className="py-2 px-3 text-right tabular-nums">{g.jobs + g.dropped} · {g.sales}</td>
                        <td className="py-2 px-3 text-right tabular-nums">{money(g.revenue)}</td>
                        <td className={`py-2 px-3 text-right tabular-nums font-bold ${(g.profit ?? 0) < 0 ? "text-rose-600" : "text-ink-primary"}`}>{costsHidden ? "" : money(g.profit)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ))}
          </div>

          {drill && (
            <div className="bg-white rounded-2xl border border-slate-200/90 shadow-clay-card overflow-hidden">
              <div className="p-3 border-b border-slate-100 flex items-center gap-2">
                <span className="text-xs font-bold text-ink-primary">{drill.title}</span>
                <span className="text-[11px] text-ink-tertiary">{shown.length} {shown.length === 1 ? "entry" : "entries"}</span>
                <button type="button" onClick={() => setDrill(null)} className="ml-auto text-[11px] font-semibold text-ink-secondary hover:text-primary">Close</button>
              </div>
              <div className="overflow-auto max-h-[50vh]">
                <table className="w-full text-left text-xs">
                  <thead className="sticky top-0"><tr className="text-[11px] font-bold text-ink-tertiary bg-slate-50">
                    <th className="py-2 px-3">Number</th><th className="py-2 px-3">What</th><th className="py-2 px-3">Date</th>
                    <th className="py-2 px-3">Customer</th><th className="py-2 px-3 text-right">Taken</th><th className="py-2 px-3 text-right">Tax</th>
                    {!costsHidden && <th className="py-2 px-3 text-right">Parts</th>}
                    <th className="py-2 px-3 text-right">Labour</th><th className="py-2 px-3 text-right">Commission</th>
                    {!costsHidden && <th className="py-2 px-3 text-right">Profit</th>}
                  </tr></thead>
                  <tbody className="divide-y divide-slate-100">
                    {shown.length === 0 && <tr><td colSpan={10} className="py-4 px-3 text-ink-tertiary">Nothing here for this period.</td></tr>}
                    {shown.map((r) => (
                      <tr key={r.kind + r.id} className="hover:bg-blue-50/50">
                        <td className="py-2 px-3 whitespace-nowrap">
                          {r.kind === "sale"
                            ? <span className="font-mono font-semibold">{r.number}</span>
                            : <button type="button" onClick={() => nav(`/orders?open=${r.id}`)} className="font-mono font-semibold text-primary hover:underline">{r.number}</button>}
                        </td>
                        <td className="py-2 px-3">{KIND_LABEL[r.kind]}{r.reason ? <span className="text-ink-tertiary"> · {r.reason}</span> : null}
                          {r.written_off > 0 && <span className="block text-[11px] text-ink-tertiary">{money(r.written_off)} written off</span>}</td>
                        <td className="py-2 px-3 whitespace-nowrap">{new Date(r.date).toLocaleDateString()}</td>
                        <td className="py-2 px-3">{r.client_name || "—"}</td>
                        <td className="py-2 px-3 text-right tabular-nums">{money(r.revenue)}</td>
                        <td className="py-2 px-3 text-right tabular-nums">{money(r.tax)}</td>
                        {!costsHidden && <td className="py-2 px-3 text-right tabular-nums">{money(r.cogs)}</td>}
                        <td className="py-2 px-3 text-right tabular-nums">{money(r.labour_cost)}</td>
                        <td className="py-2 px-3 text-right tabular-nums">{money(r.commission)}</td>
                        {!costsHidden && <td className={`py-2 px-3 text-right tabular-nums whitespace-nowrap font-bold ${(r.profit ?? 0) < 0 ? "text-rose-600" : "text-ink-primary"}`}>{money(r.profit)}</td>}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
          <p className="text-[11px] text-ink-tertiary">
            Profit = takings after tax − parts − labour − commissions. Jobs count on the day they were closed or dropped; a dropped job counts only the money you kept. Counter sales carry no tax or commission.
          </p>
        </>
      )}
    </div>
  );
}
