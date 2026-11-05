import SkeletonRows from "../../components/SkeletonRows";
import PageSummary, { type SummaryStat } from "../../components/PageSummary";
import { downloadSheet } from "../../lib/downloadSheet";
import { humanize } from "../../lib/humanize";
import { useEffect, useState } from "react";
import Shell, { M } from "../../components/Shell";
import DateField from "../../components/DateField";
import { api } from "../../api/client";
import { money } from "../orders/types";
import { alertDialog } from "../../lib/dialog";
import Pager from "../../components/Pager";
import { useTerminology } from "../../lib/useTerminology";
import { getLocale } from "../../i18n";
import { useMe } from "../../context/Me";
import { useNavigate } from "react-router-dom";
import { buildReportConfig, pickChartColumns } from "./reportConfig";
import TotalProfit from "./TotalProfit";
import { DEFAULT_FILTERS, REPORTS_PREF, REPORT_FILTERS, filterParams, readReportsPref, type ReportFilters } from "./profitView";
import { cachedPref, loadPref, savePref } from "../../lib/userPref";
import GoPilotReport from "./GoPilotReport";
import { loadFailed } from "../../lib/notice";
import PageLayout, { PanelHeader } from "../../components/PageLayout";
import Tabs from "../../components/Tabs";
import Button, { IconButton } from "../../components/Button";
import Card, { StatTile } from "../../components/Card";
import EmptyState from "../../components/EmptyState";
import { fieldClass } from "../../components/Field";
import { theadRowClass } from "../../components/Table";
import { SearchBox } from "../invoices/billing";
import ReportsToolbar from "./ReportsToolbar";

// A report column holds money when its name says so (the API sends plain labels): those cells show
// the shop's currency; every number column is right-aligned with even-width digits.
const MONEY_WORDS = /commission|hourly|bonus|penalt|base|total|revenue|sales|cost|price|amount|value|profit|paid|due|balance|tax|discount|wage|refund|expense|income|salary|margin/i;
const NOT_MONEY = /\b(count|qty|quantity|hours?|jobs|orders|units|items|%|pct|percent|rate|days|minutes|min|number|no\.)\b|%/i;
export const isMoneyCol = (label: string) => MONEY_WORDS.test(label) && !NOT_MONEY.test(label);
const sel = fieldClass("pr-8 w-auto max-w-[12rem]");

const REPORT_PAGE = 50;

// Shared by the report export and the custom-report export.
const exportAoa = downloadSheet;

type Catalog = { tabs: { name: string; reports: { key: string; title: string }[] }[] };
type RunResult = { key: string; title: string; columns: { key: string; label: string; align?: string }[] | string[]; rows: any[]; row_count: number };

// Dependency-free horizontal bar chart (CSS widths, no canvas/lib). Values may be negative.
// Solid fills (sapphire up, rose down), a leading rank and the value. Pass `color` to force one
// fill (e.g. the feedback ratings chart).
function BarChart({ rows, fmt = (n: number) => n.toLocaleString(getLocale()), color }: {
  rows: { label: string; value: number }[]; fmt?: (n: number) => string; color?: string;
}) {
  const max = Math.max(1, ...rows.map((r) => Math.abs(r.value)));
  if (!rows.length) return <div className="text-xs text-ink-tertiary py-4 text-center">Nothing to chart.</div>;
  return (
    <div className="space-y-2">
      {rows.map((r, i) => {
        const pos = r.value >= 0;
        const pct = Math.max(2, (Math.abs(r.value) / max) * 100);
        const fill = color ?? (pos
          ? "bg-primary"
          : "bg-rose-400");
        return (
          <div key={i} className="group flex items-center gap-2.5 text-xs">
            <span className="w-4 shrink-0 text-right text-2xs font-semibold text-ink-placeholder tabular-nums">{i + 1}</span>
            <span className="w-32 shrink-0 truncate text-ink-secondary group-hover:text-ink-primary transition-colors" title={r.label}>{r.label}</span>
            <div className="flex-1 h-5 rounded-full bg-slate-100 overflow-hidden">
              <div className={`h-full rounded-full ${fill} transition-[width] duration-700 ease-out`} style={{ width: `${pct}%` }} />
            </div>
            <span className={`w-24 shrink-0 text-right tabular-nums font-semibold tabular-nums ${pos ? "text-ink-primary" : "text-rose-600"}`}>{fmt(r.value)}</span>
          </div>
        );
      })}
    </div>
  );
}
// Local-date ISO (YYYY-MM-DD) — NOT toISOString(), which shifts to UTC and would
// move a local-midnight preset boundary to the previous day in ahead-of-UTC zones (e.g. IST).
const iso = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;

// ── Custom report builder (Tier 3) ──────────────────────────────────────────
type SrcField = { key: string; label: string; type: string };
type SrcMeta = { label: string; date_field: string | null; has_location: boolean; fields: SrcField[] };
type CRun = { columns: { key: string; label: string; align?: string }[]; rows: any[][]; row_count: number; title?: string };

const OPS_FOR = (t: string): [string, string][] =>
  t === "money" || t === "number" ? [["eq", "="], ["ne", "≠"], ["gt", ">"], ["lt", "<"], ["gte", "≥"], ["lte", "≤"]]
  : t === "date" ? [["gte", "on/after"], ["lte", "on/before"], ["gt", "after"], ["lt", "before"]]
  : [["eq", "is"], ["ne", "is not"], ["contains", "contains"]];

export function CustomView() {
  const [sources, setSources] = useState<Record<string, SrcMeta> | null>(null);
  const [saved, setSaved] = useState<any[]>([]);
  const [src, setSrc] = useState("");
  const [cols, setCols] = useState<string[]>([]);
  const [filters, setFilters] = useState<{ field: string; op: string; value: string }[]>([]);
  const [groupBy, setGroupBy] = useState("");
  const [aggs, setAggs] = useState<{ fn: string; field: string }[]>([{ fn: "count", field: "" }]);
  const [from, setFrom] = useState(iso(new Date(Date.now() - 30 * 864e5)));
  const [to, setTo] = useState(iso(new Date()));
  const [locs, setLocs] = useState<{ id: string; name: string }[]>([]);
  const [locId, setLocId] = useState("");
  const [res, setRes] = useState<CRun | null>(null);
  const [name, setName] = useState("");
  const [curId, setCurId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [cpage, setCpage] = useState(1);

  const loadSaved = () => api.get<any>("/custom-reports").then((r) => setSaved(r.items || [])).catch(() => setSaved([]));
  useEffect(() => {
    api.get<any>("/custom-reports/sources").then(setSources).catch(() => setSources({}));
    api.get<any>("/staff/locations").then((r) => setLocs(r.items || r || [])).catch(() => setLocs([]));
    loadSaved();
  }, []);

  const meta = src && sources ? sources[src] : null;
  const fieldsOf = meta?.fields ?? [];
  const numFields = fieldsOf.filter((f) => f.type === "money" || f.type === "number");
  const fType = (k: string) => fieldsOf.find((f) => f.key === k)?.type ?? "text";

  function pickSource(s: string) {
    const m = sources?.[s];
    setSrc(s); setCols((m?.fields || []).map((f) => f.key)); setFilters([]);
    setGroupBy(""); setAggs([{ fn: "count", field: "" }]); setRes(null); setCurId(null); setName("");
  }
  const buildConfig = () => buildReportConfig({ source: src, columns: cols, filters, groupBy, aggregates: aggs });
  async function run() {
    if (!src) return; setBusy(true); setErr("");
    try { setCpage(1); setRes(await api.post<CRun>("/custom-reports/run", { config: buildConfig(), date_from: from, date_to: to, location_id: locId || undefined })); }
    catch (e: any) { setErr(e?.message || "Run failed"); setRes(null); } finally { setBusy(false); }
  }
  async function save() {
    if (!src || !name.trim()) { setErr("Pick a source and enter a name to save."); return; }
    setBusy(true); setErr("");
    try {
      if (curId) await api.put(`/custom-reports/${curId}`, { name, config: buildConfig() });
      else { const c = await api.post<any>("/custom-reports", { name, config: buildConfig() }); setCurId(c.id); }
      loadSaved();
    } catch (e: any) { setErr(e?.message || "Save failed"); } finally { setBusy(false); }
  }
  async function del() {
    if (!curId) return; setBusy(true);
    try { await api.del(`/custom-reports/${curId}`); setCurId(null); setName(""); setRes(null); loadSaved(); }
    catch (e: any) { setErr(e?.message || "Delete failed"); } finally { setBusy(false); }
  }
  function loadReport(r: any) {
    const c = r.config || {};
    setSrc(c.source); setCols(c.columns || (sources?.[c.source]?.fields || []).map((f) => f.key));
    setFilters(c.filters || []); setGroupBy(c.group_by || "");
    setAggs(c.aggregates?.length ? c.aggregates.map((a: any) => ({ fn: a.fn, field: a.field || "" })) : [{ fn: "count", field: "" }]);
    setCurId(r.id); setName(r.name); setRes(null);
  }
  function exportRes(fmt: "xlsx" | "csv") {
    if (!res) return;
    const aoa: (string | number)[][] = [res.columns.map((c) => c.label)];
    for (const row of res.rows) aoa.push(row.map((v) => v == null ? "" : v));
    exportAoa(aoa, fmt, `${(name || src || "custom").replace(/\s+/g, "_")}_${from}_${to}`);
  }

  // chart: label column + numeric value column, chosen so the value is never the label — so
  // grouping by a numeric field (e.g. Discount) + Count charts the count, not the group key
  // against itself. Detect by actual cell values, not the `align` styling flag.
  const { labelIdx: labelColIdx, valueIdx: numColIdx } = res
    ? pickChartColumns(res.columns, res.rows)
    : { labelIdx: 0, valueIdx: -1 };
  const chartRows = res && numColIdx >= 0
    ? res.rows.map((r) => ({ label: String(r[labelColIdx] ?? "—"), value: Number(r[numColIdx]) || 0 }))
        .filter((r) => r.value !== 0).sort((a, b) => Math.abs(b.value) - Math.abs(a.value)).slice(0, 20)
    : [];

  return (
    <PageLayout>
      <ReportsToolbar
        actions={<>
          <Button variant="primary" disabled={busy || !src} onClick={run}>Run</Button>
          <Button icon={<M n="table_view" c="text-sm" />} disabled={!res} onClick={() => exportRes("xlsx")}>Excel</Button>
          <Button icon={<M n="download" c="text-sm" />} disabled={!res} onClick={() => exportRes("csv")}>CSV</Button>
        </>}
        locs={locs} locId={locId} onLoc={meta?.has_location ? setLocId : undefined}
        from={from} to={to} onFrom={meta?.date_field ? setFrom : undefined} onTo={meta?.date_field ? setTo : undefined} />
      <PanelHeader title="Custom report" sub="Build your own table from any data, then save it to run again." />
      <div className="flex flex-col md:flex-row gap-4">
        {/* saved reports */}
        <aside className="md:w-52 flex-shrink-0 bg-white rounded-2xl border border-slate-200/90 p-2 h-fit">
          <button onClick={() => pickSource("")} className="w-full text-left px-2.5 py-2 rounded-lg text-xs font-semibold text-primary hover:bg-slate-50 flex items-center gap-1.5"><M n="add" c="text-sm" />New report</button>
          <div className="text-2xs font-semibold text-ink-tertiary px-2 py-1.5 mt-1">Saved</div>
          {saved.length === 0 && <div className="px-2.5 py-1 text-xs text-ink-tertiary">None yet.</div>}
          {saved.map((r) => (
            <button key={r.id} onClick={() => loadReport(r)} className={`w-full text-left px-2.5 py-2 rounded-lg text-xs font-medium ${curId === r.id ? "bg-primary/10 text-primary" : "text-ink-secondary hover:bg-slate-100"}`}><span className="block truncate">{r.name}</span></button>
          ))}
        </aside>

        {/* builder + results */}
        <div className="flex-1 min-w-0 space-y-3">
          <div className="bg-white rounded-2xl border border-slate-200/90 p-4 space-y-3">
            <div className="flex items-center gap-2 flex-wrap">
              <span className="text-xs font-semibold text-ink-secondary">Source</span>
              <select aria-label="Source" value={src} onChange={(e) => pickSource(e.target.value)} className={sel}>
                <option value="">Choose data…</option>
                {sources && Object.entries(sources).map(([k, s]) => <option key={k} value={k}>{s.label}</option>)}
              </select>
            </div>

            {meta && (<>
              {/* columns — only meaningful in flat (ungrouped) mode. When a Group by is set,
                   the result is the group key + aggregates, so the column picker is hidden to
                   avoid the misleading "all columns selected but ignored" state (code-review #1). */}
              {groupBy
                ? <div className="text-xs text-ink-tertiary flex items-center gap-1.5"><M n="info" c="text-xs" />Columns are set by <span className="font-semibold text-ink-secondary">Group by</span> + <span className="font-semibold text-ink-secondary">Aggregates</span> below.</div>
                : (
                <div>
                  <span className="text-xs font-semibold text-ink-secondary">Columns</span>
                  <div className="flex flex-wrap gap-1.5 mt-1.5">
                    {fieldsOf.map((f) => {
                      const on = cols.includes(f.key);
                      return <button key={f.key} onClick={() => setCols((c) => on ? c.filter((k) => k !== f.key) : [...c, f.key])}
                        aria-pressed={on} className={`h-8 px-3 rounded-full text-xs font-semibold ring-1 ring-inset ${on ? "bg-primary/10 text-primary ring-primary/20" : "bg-white text-ink-secondary ring-slate-200 hover:bg-slate-50"}`}>{f.label}</button>;
                    })}
                  </div>
                  {cols.length === 0 && <p className="mt-1.5 text-xs text-ink-tertiary flex items-center gap-1"><M n="info" c="text-xs" />No columns selected — the report will show all of them.</p>}
                </div>
              )}

              {/* filters */}
              <div>
                <div className="flex items-center gap-2"><span className="text-xs font-semibold text-ink-secondary">Filters</span>
                  <Button variant="link" onClick={() => setFilters((f) => [...f, { field: fieldsOf[0]?.key || "", op: "eq", value: "" }])}>+ Add</Button></div>
                {filters.map((flt, i) => (
                  <div key={i} className="flex items-center gap-1.5 mt-1.5">
                    <select value={flt.field} onChange={(e) => setFilters((fs) => fs.map((x, j) => j === i ? { ...x, field: e.target.value, op: OPS_FOR(fType(e.target.value))[0][0] } : x))} className={fieldClass("pr-8 w-auto min-w-[130px]")}>
                      {fieldsOf.map((f) => <option key={f.key} value={f.key}>{f.label}</option>)}
                    </select>
                    <select value={flt.op} onChange={(e) => setFilters((fs) => fs.map((x, j) => j === i ? { ...x, op: e.target.value } : x))} className={fieldClass("pr-8 w-auto min-w-[110px]")}>
                      {OPS_FOR(fType(flt.field)).map(([v, l]) => <option key={v} value={v}>{l}</option>)}
                    </select>
                    {fType(flt.field) === "date"
                      ? <DateField className="w-40" value={flt.value} onChange={(d) => setFilters((fs) => fs.map((x, j) => j === i ? { ...x, value: d } : x))} label="Filter date" />
                      : <input value={flt.value} onChange={(e) => setFilters((fs) => fs.map((x, j) => j === i ? { ...x, value: e.target.value } : x))} placeholder="value" className={fieldClass("w-32")} />}
                    <IconButton size="sm" label="Remove filter" onClick={() => setFilters((fs) => fs.filter((_, j) => j !== i))} icon={<M n="close" c="text-sm" />} />
                  </div>
                ))}
              </div>

              {/* group by + aggregates — the "shape the output" panel */}
              <div className="rounded-xl bg-slate-50/70 border border-slate-200/70 p-3 flex flex-wrap items-start gap-x-6 gap-y-3">
                <div className="min-w-[160px]">
                  <span className="block text-xs font-semibold text-ink-secondary mb-1.5">Group by</span>
                  <select value={groupBy} onChange={(e) => setGroupBy(e.target.value)} className={fieldClass("pr-8")}>
                    <option value="">— none (list rows) —</option>
                    {fieldsOf.map((f) => <option key={f.key} value={f.key}>{f.label}</option>)}
                  </select>
                </div>
                {groupBy && (
                  <div className="min-w-[200px]">
                    <div className="flex items-center justify-between gap-3 mb-1.5">
                      <span className="text-xs font-semibold text-ink-secondary">Aggregates</span>
                      <Button variant="link" onClick={() => setAggs((a) => [...a, { fn: "sum", field: numFields[0]?.key || "" }])}>+ Add</Button>
                    </div>
                    <div className="space-y-1.5">
                      {aggs.map((a, i) => (
                        <div key={i} className="flex items-center gap-1.5">
                          <select value={a.fn} onChange={(e) => setAggs((as) => as.map((x, j) => j === i ? { ...x, fn: e.target.value } : x))} className={fieldClass("pr-8 w-auto min-w-[110px]")}>
                            <option value="count">Count</option><option value="sum">Sum</option><option value="avg">Average</option>
                          </select>
                          {a.fn !== "count" && (
                            <select value={a.field} onChange={(e) => setAggs((as) => as.map((x, j) => j === i ? { ...x, field: e.target.value } : x))} className={fieldClass("pr-8 w-auto min-w-[130px]")}>
                              <option value="">field…</option>
                              {numFields.map((f) => <option key={f.key} value={f.key}>{f.label}</option>)}
                            </select>
                          )}
                          <IconButton size="sm" label="Remove aggregate" onClick={() => setAggs((as) => as.filter((_, j) => j !== i))} icon={<M n="close" c="text-sm" />} />
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </div>

              {/* save this report (Run and the downloads are in the page toolbar) */}
              <div className="flex items-center gap-2 flex-wrap pt-3 border-t border-slate-100">
                <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Report name" aria-label="Report name" className={fieldClass("w-44")} />
                <Button disabled={busy} onClick={save}>{curId ? "Update" : "Save"}</Button>
                {curId && <Button variant="danger" disabled={busy} onClick={del}>Delete</Button>}
              </div>
              {err && <div className="text-xs text-rose-600">{err}</div>}
            </>)}
          </div>

          {!meta && <Card padded={false}><EmptyState title="Choose what to report on" body="Pick a data source above, or open a saved report." /></Card>}

          {res && chartRows.length > 0 && (
            <div className="bg-white rounded-2xl border border-slate-200/90 p-4">
              <h4 className="text-xs font-semibold text-ink-primary mb-3">Chart</h4>
              <BarChart rows={chartRows} />
            </div>
          )}
          {res && (() => {
            const cPages = Math.max(1, Math.ceil(res.rows.length / REPORT_PAGE));
            const cSafe = Math.min(cpage, cPages);
            const cShown = res.rows.slice((cSafe - 1) * REPORT_PAGE, cSafe * REPORT_PAGE);
            return (
            <div className="bg-white rounded-2xl border border-slate-200/90 shadow-clay-card overflow-hidden">
              <div className="overflow-auto max-h-[62vh]">
                <table className="w-full text-left border-collapse text-xs">
                  <thead className="sticky top-0 z-10"><tr className={theadRowClass}>
                    {res.columns.map((c) => <th key={c.key} className={`py-3 px-4 bg-slate-50 ${c.align === "right" ? "text-right" : ""}`}>{c.label}</th>)}
                  </tr></thead>
                  <tbody className="divide-y divide-slate-100 font-medium text-ink-secondary">
                    {res.rows.length === 0 && <tr><td colSpan={res.columns.length} className="p-0"><EmptyState size="inline" className="px-4" title="Nothing matches this report for these dates." /></td></tr>}
                    {cShown.map((row, ri) => (
                      <tr key={(cSafe - 1) * REPORT_PAGE + ri} className="odd:bg-slate-50/40 hover:bg-blue-50/50 transition-colors">
                        {res.columns.map((c, ci) => <td key={ci} className={`py-2.5 px-4 text-ink-primary ${c.align === "right" || typeof row[ci] === "number" ? "text-right tabular-nums whitespace-nowrap" : ""}`}>{typeof row[ci] === "number" ? (isMoneyCol(c.label) ? money(row[ci], undefined, true) : row[ci].toLocaleString(getLocale())) : String(row[ci] ?? "—")}</td>)}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {res.rows.length > 0
                ? <Pager total={res.rows.length} page={cSafe} pageSize={REPORT_PAGE} onPage={setCpage} noun="rows" />
                : <div className="px-4 py-3 bg-slate-50/60 border-t border-slate-100 text-2xs text-ink-tertiary">0 rows</div>}
            </div>
            );
          })()}
        </div>
      </div>
    </PageLayout>
  );
}

// Reports whose download needs its own right on top of seeing them (mirrors reports.EXPORT_PERMS).
const EXPORT_PERMS: Record<string, string> = {
  sold_products: "can_export_sales", provided_services: "can_export_sales",
  payroll: "can_export_payroll", labor_cost: "can_export_payroll",
};

export default function ReportsHub() {
  const nav = useNavigate();
  const { can } = useMe();
  const [dash, setDash] = useState<any>(null);
  const [cat, setCat] = useState<Catalog | null>(null);
  const [active, setActive] = useState<string | null>(null);
  const [result, setResult] = useState<RunResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [from, setFrom] = useState(iso(new Date(Date.now() - 30 * 864e5)));
  const [to, setTo] = useState(iso(new Date()));
  const [view, setView] = useState<"reports" | "total_profit" | "profitability" | "insights" | "feedback" | "custom" | "fleet" | "gopilot">("reports");
  const [profit, setProfit] = useState<any[] | null>(null);
  const [insights, setInsights] = useState<any[] | null>(null);
  const [feedback, setFeedback] = useState<any | null>(null);
  const [locs, setLocs] = useState<{ id: string; name: string }[]>([]);
  const [locId, setLocId] = useState(() => readReportsPref(cachedPref(REPORTS_PREF, {})).locId);
  // Report filters (turnover movement, hide zero amounts, dropped-job status/type), remembered per user.
  const [filters, setFilters] = useState<ReportFilters>(() => readReportsPref(cachedPref(REPORTS_PREF, {})).filters);
  const [dropStatuses, setDropStatuses] = useState<{ key: string; name: string }[]>([]);
  const [woTypes, setWoTypes] = useState<{ id: string; name: string }[]>([]);
  const [q, setQ] = useState("");
  const [sort, setSort] = useState<{ i: number; dir: 1 | -1 } | null>(null);
  const [exporting, setExporting] = useState(false);
  const [showChart, setShowChart] = useState(true);
  const [chartCol, setChartCol] = useState<number | null>(null);
  const [prevTotals, setPrevTotals] = useState<(number | null)[] | null>(null);
  const [page, setPage] = useState(1);
  const [ppage, setPpage] = useState(1);

  useEffect(() => { api.get<any>("/reports/dashboard").then((r) => setDash(r?.stats || r || {})).catch(() => setDash({})); }, []);
  useEffect(() => { api.get<any>("/staff/locations").then((r) => setLocs(r.items || r || [])).catch(() => setLocs([])); }, []);
  useEffect(() => {
    // Open the report you had last time (with its location and filters), else the first one.
    Promise.all([api.get<Catalog>("/reports/catalog"), loadPref(REPORTS_PREF, {})]).then(([c, raw]) => {
      setCat(c);
      const pref = readReportsPref(raw);
      setLocId(pref.locId); setFilters(pref.filters);
      const keys = (c.tabs || []).flatMap((t) => t.reports.map((r) => r.key));
      const first = pref.report && keys.includes(pref.report) ? pref.report : keys[0];
      if (first) runWith(first, from, to, pref.filters, pref.locId);
    }).catch(() => setCat({ tabs: [] }));
  }, []);   // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    api.get<any>("/statuses?record_type=work_order").then((r) => setDropStatuses((r.items || r || []).filter((x: any) => x.status_group === "dropped"))).catch(loadFailed("job statuses"));
    api.get<any>("/forms/types").then((r) => setWoTypes(r.items || [])).catch(loadFailed("job types"));
  }, []);
  const remember = (report: string | null, f: ReportFilters, loc: string) => savePref(REPORTS_PREF, { report, locId: loc, filters: f });
  function changeFilter(patch: Partial<ReportFilters>) {
    const f = { ...filters, ...patch };
    setFilters(f);
    if (active) runWith(active, from, to, f);
  }

  // ── date presets (Orderry-style quick ranges) ──
  const presets: [string, () => [Date, Date]][] = [
    ["Today", () => { const d = new Date(); return [d, d]; }],
    ["This week", () => { const n = new Date(); const s = new Date(n); s.setDate(n.getDate() - ((n.getDay() + 6) % 7)); return [s, n]; }],
    ["This month", () => { const n = new Date(); return [new Date(n.getFullYear(), n.getMonth(), 1), n]; }],
    ["Last month", () => { const n = new Date(); return [new Date(n.getFullYear(), n.getMonth() - 1, 1), new Date(n.getFullYear(), n.getMonth(), 0)]; }],
    ["This quarter", () => { const n = new Date(); const q0 = Math.floor(n.getMonth() / 3) * 3; return [new Date(n.getFullYear(), q0, 1), n]; }],
    ["This year", () => { const n = new Date(); return [new Date(n.getFullYear(), 0, 1), n]; }],
  ];
  function applyPreset(fn: () => [Date, Date]) {
    const [s, e] = fn(); const f = iso(s), t = iso(e);
    setFrom(f); setTo(t);
    if (active) runWith(active, f, t);
  }

  // Export the rows the user is actually looking at — filtered + sorted + totals —
  // built client-side from `viewRows` (see below) so the file matches the screen.
  // The sheet writer is loaded on demand so it never bloats the main bundle.
  async function download(fmt: "xlsx" | "csv") {
    if (!active || !result) return;
    setExporting(true);
    try {
      const aoa: (string | number)[][] = [cols.map((c) => colLabel(c))];
      for (const row of viewRows) aoa.push(cols.map((c, ci) => { const v = cellVal(row, c, ci); return v == null ? "" : v; }));
      if (hasTotals && viewRows.length) aoa.push(cols.map((_c, ci) => ci === totalsLabelIdx ? "Total" : (totals[ci] ?? "")));
      await exportAoa(aoa, fmt, `${active}_${from}_${to}`);
    } catch (e: any) { await alertDialog(e.message || "Export failed"); } finally { setExporting(false); }
  }
  useEffect(() => {
    if (view === "profitability" && profit === null) api.get<any>("/reports/job-profitability").then((r) => setProfit(r.items || r.rows || r || [])).catch(() => setProfit([]));
    if (view === "insights" && insights === null) api.get<any>("/reports/insights/cards").then((r) => setInsights(r.items || r.cards || r || [])).catch(() => setInsights([]));
    if (view === "feedback" && feedback === null) api.get<any>("/feedback/list").then((r) => setFeedback(r || {})).catch(() => setFeedback({}));
  }, [view]);
  const gv = (o: any, keys: string[], fb = "—") => { for (const k of keys) { const v = o?.[k]; if (v !== undefined && v !== null && v !== "") return String(v); } return fb; };

  async function runWith(key: string, f: string, t: string, flt: ReportFilters = filters, loc: string = locId) {
    setActive(key); setLoading(true); setSort(null); setQ(""); setChartCol(null); setPrevTotals(null); setPage(1);
    remember(key, flt, loc);
    try {
      const qs = new URLSearchParams({ date_from: f, date_to: t, ...filterParams(key, flt) });
      if (loc) qs.set("location_id", loc);
      const r = await api.get<RunResult>(`/reports/run/${key}?${qs.toString()}`); setResult(r);
      loadPrevPeriod(key, f, t, r, flt, loc);   // fire-and-forget; never blocks the table
    } catch { setResult(null); } finally { setLoading(false); }
  }
  const run = (key: string) => runWith(key, from, to);

  // Period-over-period: fetch the immediately-preceding equal-length window and
  // total its numeric columns, so the totals row can show deltas.
  async function loadPrevPeriod(key: string, f: string, t: string, cur: RunResult, flt: ReportFilters = filters, loc: string = locId) {
    try {
      const cols0 = cur.columns ?? [];
      const isNum = cols0.map((c, ci) => { const v = (cur.rows ?? []).map((r) => (Array.isArray(r) ? r[ci] : r[colKey(c, ci)])).filter((x) => x !== null && x !== undefined && x !== ""); return v.length > 0 && v.every((x) => typeof x === "number"); });
      if (!isNum.some(Boolean)) return;
      const dayMs = 86400000;
      const fD = new Date(f + "T00:00:00"), tD = new Date(t + "T00:00:00");
      const lenDays = Math.round((tD.getTime() - fD.getTime()) / dayMs) + 1;
      const prevTo = new Date(fD.getTime() - dayMs), prevFrom = new Date(fD.getTime() - lenDays * dayMs);
      const qs = new URLSearchParams({ date_from: iso(prevFrom), date_to: iso(prevTo), ...filterParams(key, flt) });
      if (loc) qs.set("location_id", loc);
      const pr = await api.get<RunResult>(`/reports/run/${key}?${qs.toString()}`);
      const sums = cols0.map((c, ci) => isNum[ci] ? (pr.rows ?? []).reduce((s, r) => s + (Number(Array.isArray(r) ? r[ci] : r[colKey(c, ci)]) || 0), 0) : null);
      setPrevTotals(sums);
    } catch { /* deltas are best-effort; ignore */ }
  }

  const kpiStats: SummaryStat[] = [
    { label: "Jobs and estimates", value: dash?.total_orders ?? "—", onClick: () => nav("/orders") },
    { label: "Still open", value: dash?.open_orders ?? "—", onClick: () => nav("/orders") },
    { label: "Customers", value: dash?.total_clients ?? "—", onClick: () => nav("/clients") },
    { label: "Sales", value: dash ? money(dash.revenue) : "—", hint: "Sales after returns and refunds. Money collected is under Paid on Home.", onClick: () => nav("/finance") },
    { label: "Still owed to you", value: dash ? money(dash.outstanding) : "—", onClick: () => nav("/invoices") },
  ];

  const colLabel = (c: any) => typeof c === "string" ? c : (c.label || c.key);
  const colKey = (c: any, i: number) => typeof c === "string" ? c : (c.key ?? i);
  // Raw keys from the server ("technician", "in_repair") read as words; names and emails are left alone.
  const cell = (v: any, label = "") => (typeof v === "number" ? <span className={`tabular-nums ${v < 0 ? "text-rose-600 font-semibold" : ""}`}>{isMoneyCol(label) ? money(v, undefined, true) : v.toLocaleString(getLocale())}</span>
    : typeof v === "string" && /^[a-z]+(_[a-z]+)*$/.test(v) ? humanize(v) : String(v ?? "—"));
  const cellVal = (row: any, c: any, ci: number) => Array.isArray(row) ? row[ci] : row[colKey(c, ci)];

  // ── derived view: search-filter + sort + numeric totals ──
  const cols = result?.columns ?? [];
  const rawRows = result?.rows ?? [];
  const numericCol = cols.map((c, ci) => {
    const vals = rawRows.map((r) => cellVal(r, c, ci)).filter((v) => v !== null && v !== undefined && v !== "");
    return vals.length > 0 && vals.every((v) => typeof v === "number");
  });
  const filtered = q.trim()
    ? rawRows.filter((r) => cols.some((c, ci) => String(cellVal(r, c, ci) ?? "").toLowerCase().includes(q.trim().toLowerCase())))
    : rawRows;
  const viewRows = sort
    ? [...filtered].sort((a, b) => {
        const av = cellVal(a, cols[sort.i], sort.i), bv = cellVal(b, cols[sort.i], sort.i);
        if (typeof av === "number" && typeof bv === "number") return (av - bv) * sort.dir;
        return String(av ?? "").localeCompare(String(bv ?? "")) * sort.dir;
      })
    : filtered;
  const totals = numericCol.map((isNum, ci) => isNum ? filtered.reduce((s, r) => s + (Number(cellVal(r, cols[ci], ci)) || 0), 0) : null);
  const hasTotals = numericCol.some(Boolean);
  // Put the "Total" label in the first NON-numeric column so a numeric leading column keeps its own sum.
  const totalsLabelIdx = numericCol.findIndex((n) => !n);
  const toggleSort = (i: number) => setSort((s) => s?.i === i ? (s.dir === 1 ? { i, dir: -1 } : null) : { i, dir: 1 });

  // Paginate the table so long reports don't doom-scroll. Totals/chart still use
  // the full filtered set above, so sums and the chart never change by page.
  const tablePages = Math.max(1, Math.ceil(viewRows.length / REPORT_PAGE));
  const safePage = Math.min(page, tablePages);
  useEffect(() => { setPage(1); }, [q, sort]);  // searching or re-sorting jumps back to page 1
  const pagedRows = viewRows.slice((safePage - 1) * REPORT_PAGE, safePage * REPORT_PAGE);

  // ── chart data: pick a numeric column, label by the first text column ──
  const numericIdxs = numericCol.map((n, i) => n ? i : -1).filter((i) => i >= 0);
  const activeChartCol = chartCol != null && numericCol[chartCol] ? chartCol : (numericIdxs[0] ?? -1);
  const labelIdx = totalsLabelIdx >= 0 ? totalsLabelIdx : 0;
  const chartRows = activeChartCol >= 0
    ? viewRows
        .map((r) => ({ label: String(cellVal(r, cols[labelIdx], labelIdx) ?? "—"), value: Number(cellVal(r, cols[activeChartCol], activeChartCol)) || 0 }))
        .filter((r) => r.value !== 0)
        .sort((a, b) => Math.abs(b.value) - Math.abs(a.value))
        .slice(0, 20)
    : [];
  const deltaPct = (cur: number, prev: number) => prev === 0 ? null : ((cur - prev) / Math.abs(prev)) * 100;

  return (
    <Shell title="Reports">
      <div className="px-6 flex-shrink-0">
        <Tabs look="underline" label="Report sections" value={view} onChange={setView}
          tabs={([["reports", "Reports"], ...(can("can_see_profit") ? [["total_profit", "Total profit"]] : []), ["custom", "Custom report"], ["fleet", "Business & analytics"], ["profitability", "Job profitability"], ["insights", "Insights"], ["feedback", "Feedback"], ...(can("can_manage_settings") ? [["gopilot", "Go pilot"]] : [])] as [typeof view, string][]).map(([id, label]) => ({ id, label }))} />
      </div>

      {view === "custom" && <CustomView />}
      {view === "total_profit" && <PageLayout><TotalProfit locs={locs} /></PageLayout>}
      {view === "fleet" && <PageLayout><FleetAnalytics locs={locs} from={from} to={to} /></PageLayout>}
      {view === "gopilot" && <GoPilotReport locs={locs} />}

      {view !== "reports" && view !== "custom" && view !== "fleet" && view !== "total_profit" && view !== "gopilot" && (
        <PageLayout>
          <ReportsToolbar locs={locs} from={from} to={to} />
          {view === "profitability" && <PanelHeader title="Job profitability" sub="What each job brought in against its parts, labour and commission." />}
          {view === "insights" && <PanelHeader title="Insights" sub="The numbers your shop tracks most closely." />}
          {view === "feedback" && <PanelHeader title="Feedback" sub="What customers said after their job." />}
          {view === "profitability" && (
            <Card padded={false}>
              <table className="w-full text-left border-collapse text-xs">
                <thead><tr className={theadRowClass}>
                  <th className="py-3 px-4">Order</th><th className="py-3 px-4">Client</th><th className="py-3 px-4 text-right">Sales</th><th className="py-3 px-4 text-right">Costs</th><th className="py-3 px-4 text-right">Profit</th><th className="py-3 px-4 text-right">Margin</th>
                </tr></thead>
                <tbody className="divide-y divide-slate-100 font-medium text-ink-secondary">
                  {profit == null && <SkeletonRows cols={6} />}
                  {profit?.length === 0 && <tr><td colSpan={6} className="p-0"><EmptyState size="inline" className="px-4" title="No finished jobs to show yet." /></td></tr>}
                  {profit?.slice((Math.min(ppage, Math.max(1, Math.ceil(profit.length / REPORT_PAGE))) - 1) * REPORT_PAGE,
                                 Math.min(ppage, Math.max(1, Math.ceil(profit.length / REPORT_PAGE))) * REPORT_PAGE).map((r, i) => (
                    <tr key={r?.id || i} className="odd:bg-slate-50/40 hover:bg-blue-50/50 transition-colors">
                      <td className="py-2.5 px-4 font-semibold text-primary tabular-nums">{gv(r, ["order_number", "order_id", "order"])}</td>
                      <td className="py-2.5 px-4">{gv(r, ["client_name", "client"])}</td>
                      <td className="py-2.5 px-4 text-right tabular-nums whitespace-nowrap">{money(r?.revenue ?? r?.total_revenue, undefined, true)}</td>
                      <td className="py-2.5 px-4 text-right tabular-nums whitespace-nowrap">{r?.parts_cost === undefined ? "—" : money((r.parts_cost || 0) + (r.labour_cost || 0) + (r.commission || 0), undefined, true)}</td>
                      <td className="py-2.5 px-4 text-right tabular-nums whitespace-nowrap font-semibold text-ink-primary">{r?.margin === undefined ? "—" : money(r.margin, undefined, true)}</td>
                      <td className="py-2.5 px-4 text-right tabular-nums">{gv(r, ["margin_pct", "margin"], "—")}{(r?.margin_pct != null || r?.margin != null) ? "%" : ""}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {profit && profit.length > 0 && (
                <Pager total={profit.length} page={Math.min(ppage, Math.max(1, Math.ceil(profit.length / REPORT_PAGE)))}
                       pageSize={REPORT_PAGE} onPage={setPpage} noun="jobs" />
              )}
            </Card>
          )}

          {view === "insights" && (
            <div>
              {insights == null && <div className="text-xs text-ink-tertiary">Loading insights…</div>}
              {insights?.length === 0 && <Card padded={false}><EmptyState title="No insights yet" body="Insight cards appear here once your shop has enough activity to compare." /></Card>}
              {!!insights?.length && (
                <Card className="grid grid-cols-2 lg:grid-cols-3 gap-6">
                  {insights.map((c, i) => (
                    <StatTile key={c?.id || i} label={gv(c, ["title", "name", "label"])} value={gv(c, ["value", "total", "count"], "—")}
                      hint={gv(c, ["subtitle", "hint", "description"], "") || undefined} />
                  ))}
                </Card>
              )}
            </div>
          )}

          {view === "feedback" && feedback == null && <div className="text-xs text-ink-tertiary">Loading feedback…</div>}
          {view === "feedback" && feedback != null && (() => {
            const items = feedback.items ?? [];
            const submitted = items.filter((f: any) => !f.pending && f.rating != null);
            const five = submitted.filter((f: any) => f.system === "five_point");
            const dist = [5, 4, 3, 2, 1].map((n) => ({ label: `${n} ★`, value: five.filter((f: any) => f.rating === n).length }));
            const hasDist = five.length > 0;
            const stats: SummaryStat[] = [
              { label: "Average rating", value: feedback.avg_five_point != null ? `${feedback.avg_five_point} / 5` : "—" },
              { label: "NPS", value: feedback.nps != null ? String(feedback.nps) : "—", hint: "Promoters minus detractors" },
              { label: "Response rate", value: `${feedback.response_rate ?? 0}%` },
              { label: "Responses", value: String(submitted.length) },
            ];
            return (
              <div className="space-y-4">
                <PageSummary stats={stats} />
                {hasDist && (
                  <div className="bg-white rounded-2xl border border-slate-200/90 p-4">
                    <h4 className="text-xs font-semibold text-ink-primary mb-3">Rating distribution</h4>
                    <BarChart rows={dist} color="bg-amber-400" fmt={(n) => String(n)} />
                  </div>
                )}
                <div className="bg-white rounded-2xl border border-slate-200/90 overflow-hidden">
                  <div className="px-4 py-3 border-b border-slate-100 text-xs font-semibold text-ink-primary">Comments</div>
                  <table className="w-full text-left border-collapse text-xs">
                    <thead><tr className={theadRowClass}>
                      <th className="py-3 px-4">Date</th><th className="py-3 px-4">Order</th><th className="py-3 px-4">Rating</th><th className="py-3 px-4">Comment</th>
                    </tr></thead>
                    <tbody className="divide-y divide-slate-100 font-medium text-ink-secondary">
                      {submitted.length === 0 && <tr><td colSpan={4} className="p-0"><EmptyState size="inline" className="px-4" title="No feedback submitted yet." /></td></tr>}
                      {submitted.map((f: any, i: number) => (
                        <tr key={f?.id || i} className="odd:bg-slate-50/40 hover:bg-blue-50/50 transition-colors">
                          <td className="py-2.5 px-4">{f?.submitted_at ? new Date(f.submitted_at).toLocaleDateString() : "—"}</td>
                          <td className="py-2.5 px-4 font-semibold text-primary tabular-nums">{f?.order_number ?? "—"}</td>
                          <td className="py-2.5 px-4 tabular-nums">{f?.rating ?? "—"}</td>
                          <td className="py-2.5 px-4 text-ink-tertiary truncate max-w-[360px]">{f?.comment || "—"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            );
          })()}
        </PageLayout>
      )}

      {view === "reports" && (
      <PageLayout>
        <ReportsToolbar
          actions={<>
            <Button variant="primary" onClick={() => active && run(active)} disabled={!active}>Run</Button>
            <Button icon={<M n="table_view" c="text-sm" />} disabled={exporting || !result || !can(EXPORT_PERMS[active || ""])} onClick={() => download("xlsx")}>Excel</Button>
            <Button icon={<M n="download" c="text-sm" />} disabled={exporting || !result || !can(EXPORT_PERMS[active || ""])} onClick={() => download("csv")}>CSV</Button>
          </>}
          locs={locs} locId={locId} onLoc={(v) => { setLocId(v); remember(active, filters, v); }}
          from={from} to={to} onFrom={setFrom} onTo={setTo} />
        <PanelHeader title={result?.title || "Reports"} sub={result ? "Pick another report on the left, or change the dates." : "Pick a report on the left to get started."} />
        <PageSummary stats={kpiStats} />

        <div className="flex flex-col md:flex-row gap-4">
          {/* report catalog */}
          <aside className="md:w-64 flex-shrink-0 bg-white rounded-2xl border border-slate-200/90 p-2 h-fit">
            {!cat && <div className="p-3 text-xs text-ink-tertiary">Loading…</div>}
            {cat?.tabs.map((t) => (
              <div key={t.name} className="mb-2">
                <div className="text-2xs font-semibold text-ink-tertiary px-2 py-1.5">{t.name}</div>
                {t.reports.map((r) => (
                  <button key={r.key} onClick={() => run(r.key)} title={r.title} aria-current={active === r.key || undefined} className={`w-full text-left px-2.5 py-2 rounded-lg text-xs font-medium transition-colors ${active === r.key ? "bg-primary/10 text-primary" : "text-ink-secondary hover:bg-slate-100"}`}><span className="block leading-snug">{r.title}</span></button>
                ))}
              </div>
            ))}
          </aside>

          {/* report result */}
          <div className="flex-1 min-w-0 space-y-3">
            <div className="bg-white rounded-2xl border border-slate-200/90 p-4 space-y-3">
              {active && (REPORT_FILTERS[active] || []).length > 0 && (
                <div className="flex items-center gap-2 flex-wrap text-xs" aria-label="Report filters">
                  {REPORT_FILTERS[active].includes("movement") && (
                    <select aria-label="Stock movement" value={filters.movement} onChange={(e) => changeFilter({ movement: e.target.value as ReportFilters["movement"] })} className={sel}>
                      <option value="with">Products that moved</option>
                      <option value="without">Products that didn't move</option>
                      <option value="all">All products</option>
                    </select>
                  )}
                  {REPORT_FILTERS[active].includes("hideZero") && (
                    <label className="inline-flex items-center gap-1.5 text-ink-secondary cursor-pointer">
                      <input type="checkbox" className="accent-primary" checked={filters.hideZero} onChange={(e) => changeFilter({ hideZero: e.target.checked })} />
                      Hide jobs with a zero total
                    </label>
                  )}
                  {REPORT_FILTERS[active].includes("status") && dropStatuses.length > 1 && (
                    <select aria-label="Status" value={filters.status} onChange={(e) => changeFilter({ status: e.target.value })} className={sel}>
                      <option value="">Any dropped status</option>
                      {dropStatuses.map((st) => <option key={st.key} value={st.key}>{st.name}</option>)}
                    </select>
                  )}
                  {REPORT_FILTERS[active].includes("typeId") && woTypes.length > 0 && (
                    <select aria-label="Job type" value={filters.typeId} onChange={(e) => changeFilter({ typeId: e.target.value })} className={sel}>
                      <option value="">Any job type</option>
                      {woTypes.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
                    </select>
                  )}
                  {JSON.stringify(filterParams(active, filters)) !== "{}" && (
                    <Button variant="link" onClick={() => changeFilter(DEFAULT_FILTERS)}>Clear filters</Button>
                  )}
                </div>
              )}
              <div className="flex items-center gap-2 flex-wrap">
                <Tabs look="chips" label="Quick dates" value={"" as string} onChange={(l) => { const p = presets.find(([x]) => x === l); if (p) applyPreset(p[1]); }}
                  tabs={presets.map(([label]) => ({ id: label, label }))} />
                <SearchBox value={q} onChange={setQ} label="Search report rows" placeholder="Search rows…" disabled={!result} className="sm:ml-auto sm:w-56" />
              </div>
            </div>
            {!loading && result && numericIdxs.length > 0 && (
              <div className="bg-white rounded-2xl border border-slate-200/90 p-4">
                <div className="flex items-center gap-2 mb-3">
                  <h4 className="text-xs font-semibold text-ink-primary">Chart</h4>
                  {numericIdxs.length > 1 && (
                    <select value={activeChartCol} onChange={(e) => setChartCol(Number(e.target.value))} aria-label="Chart column" className={fieldClass("pr-8 w-auto h-8 text-xs")}>
                      {numericIdxs.map((i) => <option key={i} value={i}>{colLabel(cols[i])}</option>)}
                    </select>
                  )}
                  <Button variant="ghost" size="sm" className="ml-auto -my-1" onClick={() => setShowChart((v) => !v)}>{showChart ? "Hide chart" : "Show chart"}</Button>
                </div>
                {showChart && (chartRows.length
                  ? <><BarChart rows={chartRows} /><p className="mt-2 text-2xs text-ink-tertiary">Top {chartRows.length} by {colLabel(cols[activeChartCol])}{q.trim() ? " (filtered)" : ""}.</p></>
                  : <div className="text-xs text-ink-tertiary py-3 text-center">No non-zero values to chart.</div>)}
              </div>
            )}
            <div className="bg-white rounded-2xl border border-slate-200/90 overflow-hidden">
              {loading && (
                <table className="w-full text-left border-collapse text-xs">
                  <tbody className="divide-y divide-slate-100"><SkeletonRows cols={Math.max(4, cols.length)} rows={8} /></tbody>
                </table>
              )}
              {!loading && result && (
                <div className="overflow-auto max-h-[62vh]">
                  <table className="w-full text-left border-collapse text-xs">
                    <thead className="sticky top-0 z-10"><tr className={theadRowClass}>
                      {cols.map((c, i) => (
                        <th key={colKey(c, i)} scope="col" role="button" tabIndex={0}
                            aria-sort={sort?.i === i ? (sort.dir === 1 ? "ascending" : "descending") : "none"}
                            onClick={() => toggleSort(i)}
                            onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggleSort(i); } }}
                            className={`py-3 px-4 cursor-pointer select-none hover:text-primary focus:outline-none focus-visible:ring-2 focus-visible:ring-primary/40 bg-slate-50 ${numericCol[i] ? "text-right" : ""} ${sort?.i === i ? "text-primary" : ""}`}>
                          <span className="inline-flex items-center gap-0.5">{colLabel(c)}{sort?.i === i && <M n={sort.dir === 1 ? "arrow_upward" : "arrow_downward"} c="text-2xs" />}</span>
                        </th>
                      ))}
                    </tr></thead>
                    <tbody className="divide-y divide-slate-100 font-medium text-ink-secondary">
                      {viewRows.length === 0 && <tr><td colSpan={cols.length} className="p-0"><EmptyState title={q.trim() ? "No rows match your search" : "Nothing in this date range"} body={q.trim() ? "Try another word." : "Pick other dates and press Run."} /></td></tr>}
                      {pagedRows.map((row, ri) => (
                        <tr key={(safePage - 1) * REPORT_PAGE + ri} className="odd:bg-slate-50/40 hover:bg-blue-50/50 transition-colors">
                          {cols.map((c, ci) => <td key={ci} className={`py-2.5 px-4 text-ink-primary ${numericCol[ci] ? "text-right tabular-nums whitespace-nowrap" : ""}`}>{cell(cellVal(row, c, ci), colLabel(c))}</td>)}
                        </tr>
                      ))}
                    </tbody>
                    {hasTotals && viewRows.length > 0 && (
                      <tfoot><tr className="bg-slate-50/80 border-t-2 border-slate-200 font-semibold text-ink-primary">
                        {cols.map((_c, ci) => {
                          if (ci === totalsLabelIdx) return <td key={ci} className="py-2.5 px-4">Total{prevTotals && <span className="ml-1 font-normal text-2xs text-ink-tertiary">vs the period before</span>}</td>;
                          if (totals[ci] == null) return <td key={ci} className="py-2.5 px-4" />;
                          const prev = prevTotals?.[ci];
                          const d = prev != null ? deltaPct(totals[ci]!, prev) : null;
                          const up = (totals[ci]! - (prev ?? 0)) >= 0;
                          return (
                            <td key={ci} className="py-2.5 px-4 text-right whitespace-nowrap">
                              <span className="tabular-nums block">{isMoneyCol(colLabel(cols[ci])) ? money(totals[ci]!, undefined, true) : totals[ci]!.toLocaleString(getLocale(), { maximumFractionDigits: 2 })}</span>
                              {prev != null && (
                                <span className={`text-2xs font-semibold tabular-nums ${up ? "text-emerald-600" : "text-rose-600"}`} title={`Previous: ${prev.toLocaleString(getLocale())}`}>
                                  {up ? "▲" : "▼"} {d == null ? "—" : `${Math.abs(d).toFixed(1)}%`}
                                </span>
                              )}
                            </td>
                          );
                        })}
                      </tr></tfoot>
                    )}
                  </table>
                </div>
              )}
              {!loading && result && viewRows.length > 0 && (
                <Pager total={viewRows.length} page={safePage} pageSize={REPORT_PAGE} onPage={setPage}
                       noun={q.trim() ? "matching rows" : "rows"} />
              )}
              {!loading && !result && <EmptyState size="page" icon={<M n="insert_chart" c="text-xl" />} title="No report open" body="Pick a report from the list to get started." />}
            </div>
          </div>
        </div>
      </PageLayout>
      )}
    </Shell>
  );
}

// Fleet & owner analytics — surfaces the /analytics/* layer that shipped in the backend
// but was never wired into the React app: owner KPIs, B2B fleet roll-up, technician
// productivity and per-location comparison. Read-only; range in days.
function FleetAnalytics({ locs: shopLocs, from, to }: { locs: { id: string; name: string }[]; from: string; to: string }) {
  const term = useTerminology();
  const assetsLc = term.assets.toLowerCase();   // "vehicles" / "devices" / "vessels" …
  const [days, setDays] = useState(30);
  const [ins, setIns] = useState<any>(null);
  const [fleet, setFleet] = useState<any[]>([]);
  const [techs, setTechs] = useState<any[]>([]);
  const [locs, setLocs] = useState<any[]>([]);
  const m = (n: number) => money(n || 0);   // reuse the hub's currency formatter
  useEffect(() => {
    api.get<any>(`/analytics/business-insights?days=${days}`).then(setIns).catch(() => setIns(null));
    api.get<any>(`/analytics/fleet`).then((r) => setFleet(r.companies || [])).catch(() => setFleet([]));
    api.get<any>(`/analytics/technician-productivity?days=${days}`).then((r) => setTechs(r.technicians || [])).catch(() => setTechs([]));
    api.get<any>(`/analytics/location-comparison?days=${days}`).then((r) => setLocs(r.locations || [])).catch(() => setLocs([]));
  }, [days]);
  // Plain numbers in the one summary strip: no tinted icon chips, no coloured underlines.
  const dash = <span className="sk inline-block h-7 w-20 rounded align-middle" aria-label="Loading" />;
  const kpis: SummaryStat[] = [
    { label: "Revenue", value: ins ? m(ins.revenue) : dash },
    { label: "Jobs closed", value: ins ? String(ins.orders_closed ?? 0) : dash },
    { label: "Close rate", value: ins ? `${ins.close_rate_pct ?? 0}%` : dash },
    { label: "Average job value", value: ins ? m(ins.avg_ticket_value) : dash },
    { label: "Average turnaround", value: ins ? `${ins.avg_turnaround_hours ?? 0} hours` : dash },
  ];
  const box = "bg-white rounded-2xl border border-slate-200/90 overflow-hidden";
  const th = "px-4 py-3 border-b border-slate-100 text-xs font-semibold text-ink-primary";
  return (
    <>
      {/* The period is this tab's own filter; the shared location and dates stay in place, greyed. */}
      <ReportsToolbar locs={shopLocs} from={from} to={to}
        extras={<Tabs look="segmented" label="Period" value={String(days)} onChange={(d) => setDays(Number(d))}
          tabs={[7, 30, 90].map((d) => ({ id: String(d), label: `${d} days` }))} />} />
      <PanelHeader title="Business & analytics" sub="How the shop is doing over the last few days, weeks or months." />
      <PageSummary stats={kpis} />

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <div className={box}>
          <div className={th}>Business accounts ({fleet.length})</div>
          <div className="divide-y divide-slate-100 max-h-80 overflow-y-auto">
            {fleet.length === 0 && <EmptyState size="inline" className="px-4" title="No business accounts yet." />}
            {fleet.map((c) => (
              <div key={c.client_id} className="px-4 py-2.5 flex items-center justify-between text-xs">
                <span className="font-semibold text-ink-primary truncate">{c.name}</span>
                <span className="text-ink-secondary shrink-0 tabular-nums">{c.vehicle_count} {assetsLc} · <span className={c.open_tickets ? "text-amber-600 font-semibold" : ""}>{c.open_tickets} open</span></span>
              </div>
            ))}
          </div>
        </div>

        <div className={box}>
          <div className={th}>Technician productivity</div>
          <div className="divide-y divide-slate-100 max-h-80 overflow-y-auto">
            {techs.length === 0 && <EmptyState size="inline" className="px-4" title="No technician activity in this period." />}
            {techs.map((t, i) => (
              <div key={i} className="px-4 py-2.5 flex items-center justify-between text-xs">
                <span className="font-semibold text-ink-primary truncate">{t.technician}</span>
                <span className="text-ink-secondary shrink-0 tabular-nums">{t.jobs_closed ?? 0} jobs{t.revenue != null ? ` · ${m(t.revenue)}` : ""}</span>
              </div>
            ))}
          </div>
        </div>
      </div>

      {locs.length > 0 && (
        <div className={box}>
          <div className={th}>Location comparison</div>
          <div className="divide-y divide-slate-100">
            {locs.map((l, i) => (
              <div key={i} className="px-4 py-2.5 flex items-center justify-between text-xs">
                <span className="font-semibold text-ink-primary truncate">{l.location || l.name || "—"}</span>
                <span className="text-ink-secondary shrink-0 tabular-nums">{l.orders_closed ?? l.jobs_closed ?? 0} closed{l.revenue != null ? ` · ${m(l.revenue)}` : ""}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </>
  );
}
