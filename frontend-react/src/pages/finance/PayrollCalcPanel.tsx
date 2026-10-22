import { Fragment, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { M } from "../../components/Shell";
import SkeletonRows from "../../components/SkeletonRows";
import { api, openAuthedDoc } from "../../api/client";
import { alertDialog } from "../../lib/dialog";
import { useMe } from "../../context/Me";
import { money } from "../orders/types";
import EarningsBreakdown, { type BreakdownRule } from "./EarningsBreakdown";
import { Avatar } from "../orders/look";
import { btnGhost } from "../invoices/billing";

/* Finance › Payroll calculation: the month's live pay per person, narrowed by location, role
   and employee. A row opens to the commissions behind it (rule → job → line item) plus the
   month's bonuses and penalties. Export downloads exactly what the filters show. */

export type PayrollRow = {
  employee_id: string; name?: string; position?: string; wage_type?: string; role_id?: string | null; location_id?: string | null;
  base_salary?: number; hourly_total?: number; commissions_total?: number; bonuses_total?: number; penalties_total?: number;
  computed_total?: number; accrual_status?: string | null;
};
type Detail = { breakdown: BreakdownRule[]; bonuses: { id: string; amount: number; note: string; date: string }[]; penalties: { id: string; amount: number; note: string; date: string }[] };
type Opt = { id: string; name: string };
export type PayrollFilters = { location_id: string; role_id: string; employee_id: string };

export function filterQuery(month: string, f: PayrollFilters) {
  const p = new URLSearchParams({ month });
  (Object.keys(f) as (keyof PayrollFilters)[]).forEach((k) => { if (f[k]) p.set(k, f[k]); });
  return p.toString();
}

const WAGE: Record<string, string> = { fixed: "Fixed", hourly: "Hourly", commission: "Commission only" };

/* Money cells stay neutral; a column only takes its colour when there is something in it,
   so a row of zeros reads as quiet rather than a wall of green and red. */
export function amountTone(v: number | null | undefined, tone: "gain" | "loss"): string {
  if (!Number(v)) return "text-ink-tertiary";
  return tone === "gain" ? "text-emerald-700" : "text-rose-600";
}

const ACCRUAL: Record<string, [string, string]> = {
  accrued: ["Accrued", "bg-blue-50 text-primary"], paid: ["Paid", "bg-emerald-50 text-emerald-700"],
  draft: ["Draft", "bg-amber-50 text-amber-700"], void: ["Voided", "bg-slate-100 text-ink-tertiary"],
};

export default function PayrollCalcPanel({ month, onMonth, headerAction }: { month: string; onMonth: (m: string) => void; headerAction?: React.ReactNode }) {
  const { can } = useMe();
  const [filters, setFilters] = useState<PayrollFilters>({ location_id: "", role_id: "", employee_id: "" });
  const [rows, setRows] = useState<PayrollRow[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [locations, setLocations] = useState<Opt[]>([]);
  const [roles, setRoles] = useState<Opt[]>([]);
  const [people, setPeople] = useState<Opt[]>([]);
  const [open, setOpen] = useState<string | null>(null);
  const [details, setDetails] = useState<Record<string, Detail | "error">>({});
  const [exporting, setExporting] = useState(false);

  useEffect(() => {
    api.get<any>("/staff/locations").then((r) => setLocations((r.items || r || []).map((l: any) => ({ id: l.id, name: l.name })))).catch(() => setLocations([]));
    api.get<any>("/settings/rbac/roles").then((r) => setRoles((r.items || []).map((x: any) => ({ id: x.id, name: x.name })))).catch(() => setRoles([]));
  }, []);
  // The people list is everyone the payroll scope shows this month, before the other filters:
  // taken from the unfiltered load, fetched on its own only when the month changes under a filter.
  const [peopleMonth, setPeopleMonth] = useState("");
  const toPeople = (items: PayrollRow[]) => items.map((x) => ({ id: x.employee_id, name: x.name || "—" }));
  const unfiltered = !filters.location_id && !filters.role_id && !filters.employee_id;
  useEffect(() => {
    if (unfiltered || peopleMonth === month) return;
    api.get<{ items: PayrollRow[] }>(`/payroll/calculation?month=${month}`)
      .then((r) => { setPeople(toPeople(r.items || [])); setPeopleMonth(month); }).catch(() => setPeople([]));
  }, [month, unfiltered, peopleMonth]);
  useEffect(() => {
    let live = true;
    setRows(null); setErr(null); setOpen(null); setDetails({});
    api.get<{ items: PayrollRow[] }>(`/payroll/calculation?${filterQuery(month, filters)}`)
      .then((r) => { if (!live) return; setRows(r.items || []); if (unfiltered) { setPeople(toPeople(r.items || [])); setPeopleMonth(month); } })
      .catch((e: any) => { if (live) { setErr(e?.message || "Couldn't load payroll"); setRows([]); } });
    return () => { live = false; };
  }, [month, filters]);

  async function loadDetail(id: string) {
    setDetails((x) => { const n = { ...x }; delete n[id]; return n; });
    try { const d = await api.get<Detail>(`/payroll/calculation/detail?employee_id=${id}&month=${month}`); setDetails((x) => ({ ...x, [id]: d })); }
    catch { setDetails((x) => ({ ...x, [id]: "error" })); }
  }
  function toggle(id: string) {
    if (open === id) { setOpen(null); return; }
    setOpen(id);
    if (!details[id] || details[id] === "error") loadDetail(id);
  }
  async function exportCsv() {
    setExporting(true);
    try { await openAuthedDoc(`/payroll/calculation/export.csv?${filterQuery(month, filters)}`); }
    catch (e: any) { await alertDialog(`Could not export: ${e?.message || "try again"}`); }
    finally { setExporting(false); }
  }
  const set = (k: keyof PayrollFilters) => (e: React.ChangeEvent<HTMLSelectElement>) => setFilters((f) => ({ ...f, [k]: e.target.value }));
  const active = Object.values(filters).filter(Boolean).length;
  const total = (rows || []).reduce((s, r) => s + (r.computed_total || 0), 0);
  const sel = "bg-white border border-slate-200 rounded-lg px-2.5 py-1.5 text-xs text-ink-primary outline-none focus:border-primary max-w-[11rem]";

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between gap-2 flex-wrap">
        <div className="flex items-center gap-2 flex-wrap">
          <h3 className="text-sm font-semibold text-ink-primary">Payroll calculation</h3>
          <input type="month" value={month} onChange={(e) => e.target.value && onMonth(e.target.value)} aria-label="Month" className={sel} />
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          {can("can_view_own_payroll") && can("can_view_own_compensation") && <Link to="/my-earnings" className={`${btnGhost} bg-white`}><M n="payments" c="text-sm" />My earnings</Link>}
          {can("can_export_payroll") && <button onClick={exportCsv} disabled={exporting || !rows?.length} className={`${btnGhost} bg-white`}><M n="download" c="text-sm" />{exporting ? "Exporting…" : "Export"}</button>}
          {headerAction}
        </div>
      </div>

      <div className="flex items-center gap-2 flex-wrap" role="group" aria-label="Payroll filters">
        <M n="filter_list" c="text-sm text-ink-tertiary" />
        {locations.length > 1 && (
          <select value={filters.location_id} onChange={set("location_id")} aria-label="Location" className={sel}>
            <option value="">All locations</option>
            {locations.map((l) => <option key={l.id} value={l.id}>{l.name}</option>)}
          </select>
        )}
        <select value={filters.role_id} onChange={set("role_id")} aria-label="Role" className={sel}>
          <option value="">All roles</option>
          {roles.map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}
        </select>
        <select value={filters.employee_id} onChange={set("employee_id")} aria-label="Employee" className={sel}>
          <option value="">Everyone</option>
          {people.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
        </select>
        {active > 0 && <button onClick={() => setFilters({ location_id: "", role_id: "", employee_id: "" })} className="text-xs font-semibold text-primary hover:underline">Clear filters</button>}
        {rows && rows.length > 0 && <span className="ml-auto text-xs text-ink-tertiary">{rows.length} {rows.length === 1 ? "person" : "people"} · <span className="font-semibold text-ink-primary tabular-nums">{money(total)}</span></span>}
      </div>

      <div className="bg-white rounded-2xl border border-slate-200/90 overflow-x-auto">
        <table className="w-full min-w-[840px] text-left border-collapse text-xs">
          <thead><tr className="bg-slate-50/80 border-b border-slate-200 text-ink-tertiary text-xs font-semibold">
            <th className="py-3 pl-3 pr-1 w-8"></th><th className="py-3 px-3">Employee</th><th className="py-3 px-3">Position</th><th className="py-3 px-3">Wage type</th>
            <th className="py-3 px-3 text-right">Base</th><th className="py-3 px-3 text-right">Hourly</th><th className="py-3 px-3 text-right">Commission</th>
            <th className="py-3 px-3 text-right">Bonus</th><th className="py-3 px-3 text-right">Penalty</th><th className="py-3 px-3 text-right">Total</th><th className="py-3 px-3"></th>
          </tr></thead>
          <tbody className="divide-y divide-slate-100 font-medium text-ink-secondary">
            {!rows && <SkeletonRows cols={11} />}
            {err && <tr><td colSpan={11} className="py-6 px-4 text-rose-600" role="alert">{err}</td></tr>}
            {rows?.length === 0 && !err && <tr><td colSpan={11} className="py-6 px-4 text-ink-tertiary">{active ? "Nobody matches these filters." : "No payroll data for this month."}</td></tr>}
            {rows?.map((p) => {
              const isOpen = open === p.employee_id;
              const d = details[p.employee_id];
              const acc = p.accrual_status ? ACCRUAL[p.accrual_status] : null;
              return (
                <Fragment key={p.employee_id}>
                  <tr className={`hover:bg-blue-50/30 cursor-pointer ${isOpen ? "bg-blue-50/40" : ""}`} onClick={() => toggle(p.employee_id)}>
                    <td className="py-2 pl-3 pr-1"><button type="button" aria-expanded={isOpen} aria-label={`${isOpen ? "Hide" : "Show"} breakdown for ${p.name || "employee"}`} onClick={(e) => { e.stopPropagation(); toggle(p.employee_id); }} className="w-7 h-7 rounded-md grid place-items-center text-ink-tertiary hover:bg-slate-100"><M n={isOpen ? "expand_more" : "chevron_right"} c="text-sm" /></button></td>
                    <td className="py-2 px-3"><span className="flex items-center gap-2.5 min-w-0"><Avatar name={p.name} size={26} /><span className="font-semibold text-ink-primary truncate">{p.name || "—"}</span>{acc && <span className={`px-2 py-0.5 rounded-full text-[11px] font-semibold whitespace-nowrap ${acc[1]}`}>{acc[0]}</span>}</span></td>
                    <td className="py-2.5 px-3">{p.position || "—"}</td>
                    <td className="py-2.5 px-3">{WAGE[p.wage_type || ""] || "—"}</td>
                    <td className={`py-2.5 px-3 text-right tabular-nums ${Number(p.base_salary) ? "" : "text-ink-tertiary"}`}>{money(p.base_salary)}</td>
                    <td className={`py-2.5 px-3 text-right tabular-nums ${Number(p.hourly_total) ? "" : "text-ink-tertiary"}`}>{money(p.hourly_total)}</td>
                    <td data-col="commission" className={`py-2.5 px-3 text-right tabular-nums ${amountTone(p.commissions_total, "gain")}`}>{money(p.commissions_total)}</td>
                    <td data-col="bonus" className={`py-2.5 px-3 text-right tabular-nums ${amountTone(p.bonuses_total, "gain")}`}>{money(p.bonuses_total)}</td>
                    <td data-col="penalty" className={`py-2.5 px-3 text-right tabular-nums ${amountTone(p.penalties_total, "loss")}`}>{money(p.penalties_total)}</td>
                    <td className="py-2.5 px-3 text-right tabular-nums font-semibold text-ink-primary">{money(p.computed_total)}</td>
                    <td className="py-1.5 px-3 text-right"><button aria-label="Print payslip" onClick={(e) => { e.stopPropagation(); openAuthedDoc(`/print/payroll-statement/${p.employee_id}`).catch(() => alertDialog("Could not open payslip")); }} title="Print payslip" className="w-8 h-8 inline-grid place-items-center rounded-lg text-ink-tertiary hover:text-ink-primary hover:bg-slate-100"><M n="print" c="text-base" /></button></td>
                  </tr>
                  {isOpen && (
                    <tr className="bg-slate-50/50">
                      <td colSpan={11} className="p-0">
                        {!d && <div className="px-12 py-4 text-xs text-ink-tertiary">Loading the breakdown…</div>}
                        {d === "error" && <div className="px-12 py-4 text-xs text-rose-600" role="alert">Couldn't load the breakdown. <button className="font-semibold underline" onClick={() => loadDetail(p.employee_id)}>Try again</button></div>}
                        {d && d !== "error" && (
                          <div className="grid lg:grid-cols-[2fr_1fr] gap-0 lg:divide-x divide-slate-100 border-t border-slate-100">
                            <div><div className="px-4 pt-3 text-xs font-semibold text-ink-secondary">Commissions</div><EarningsBreakdown rules={d.breakdown} /></div>
                            <div className="px-4 py-3">
                              <div className="text-xs font-semibold text-ink-secondary mb-1.5">Bonuses and penalties</div>
                              {d.bonuses.length + d.penalties.length === 0 ? <div className="text-xs text-ink-tertiary">None this month.</div> : (
                                <ul className="space-y-1">
                                  {d.bonuses.map((b) => <li key={b.id} className="flex justify-between gap-2"><span className="truncate">{b.note || "Bonus"}</span><span className="tabular-nums text-emerald-700">+{money(b.amount)}</span></li>)}
                                  {d.penalties.map((b) => <li key={b.id} className="flex justify-between gap-2"><span className="truncate">{b.note || "Penalty"}</span><span className="tabular-nums text-rose-600">−{money(Math.abs(b.amount))}</span></li>)}
                                </ul>
                              )}
                            </div>
                          </div>
                        )}
                      </td>
                    </tr>
                  )}
                </Fragment>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
