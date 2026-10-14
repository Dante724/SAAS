import SkeletonRows from "../../components/SkeletonRows";
import { useEffect, useState } from "react";
import Shell, { M } from "../../components/Shell";
import DateField from "../../components/DateField";
import { api } from "../../api/client";
import { money } from "../orders/types";
import { confirmDialog } from "../../lib/dialog";
import { isoDate, isoDateTimeLocal } from "../../lib/localDate";
import { EmployeeHours } from "../company/CompanyHub";

// Timesheets — React port of legacy timesheets.js. Per-employee grid + approve,
// manual entry add/void/reopen, and a live "who's on the clock" roster.
// Endpoints: /timeclock/timesheet, /timesheet/approve, /entries(+/{id}, /reopen), /roster.
type Entry = { id: string; started_at?: string; ended_at?: string; order_number?: string; category?: string; duration_min?: number; billable?: boolean; state?: string; note?: string };
type Sheet = { total_hours?: number; straight_hours?: number; overtime_hours?: number; billable_sec?: number; utilization?: number; labor_cost?: number; entry_count?: number; approved_count?: number; entries?: Entry[] };

function weekAgo() { const d = new Date(); d.setDate(d.getDate() - 6); return isoDate(d); }
const today = () => isoDate();
const hm = (min?: number) => { const m = Math.round(min || 0); return `${Math.floor(m / 60)}h ${m % 60}m`; };

export default function TimesheetsHub() {
  const [topTab, setTopTab] = useState<"timesheet" | "hours">("timesheet");
  const [emps, setEmps] = useState<{ id: string; name: string }[]>([]);
  const [empId, setEmpId] = useState("");
  const [from, setFrom] = useState(weekAgo()); const [to, setTo] = useState(today());
  const [sheet, setSheet] = useState<Sheet | null>(null);
  const [roster, setRoster] = useState<any[]>([]);
  const [add, setAdd] = useState(false);
  const [toast, setToast] = useState<string | null>(null);
  // Build 73 (FE) — planned shifts/absences (WorkSchedule) + plan-vs-actual compare.
  const [sched, setSched] = useState<any[] | null>(null);
  const [cmp, setCmp] = useState<any | null>(null);
  const [addSched, setAddSched] = useState(false);
  const flash = (m: string) => { setToast(m); setTimeout(() => setToast(null), 1800); };

  useEffect(() => { api.get<any>("/staff/employees").then((r) => { const it = r.items || r || []; setEmps(it); if (it[0] && !empId) setEmpId(it[0].id); }).catch(() => {}); }, []);
  const load = () => { if (empId) api.get<Sheet>(`/timeclock/timesheet?employee_id=${empId}&date_from=${from}&date_to=${to}`).then(setSheet).catch(() => setSheet(null)); };
  const loadRoster = () => api.get<any>("/timeclock/roster").then((r) => setRoster(r.on_shift || [])).catch(() => setRoster([]));
  useEffect(() => { load(); }, [empId, from, to]);
  useEffect(() => { loadRoster(); const id = setInterval(loadRoster, 30000); return () => clearInterval(id); }, []);
  const loadSched = () => {
    if (!empId) return;
    api.get<any>(`/schedules?employee_id=${empId}&date_from=${from}&date_to=${to}`).then((r) => setSched(r.items || [])).catch(() => setSched([]));
    api.get<any>(`/schedules/compare?employee_id=${empId}&date_from=${from}&date_to=${to}`).then(setCmp).catch(() => setCmp(null)); // compare is payroll-gated; null if denied
  };
  useEffect(() => { loadSched(); }, [empId, from, to]); // eslint-disable-line
  async function delSched(id: string) { try { await api.del(`/schedules/${id}`); loadSched(); } catch (e: any) { flash(e.message); } }

  async function approve() { try { await api.post("/timeclock/timesheet/approve", { employee_id: empId, date_from: from, date_to: to }); flash("Timesheet approved"); load(); } catch (e: any) { flash(e.message); } }
  async function voidEntry(id: string) { if (!(await confirmDialog("Void this entry?", { danger: true, confirmLabel: "Void" }))) return; try { await api.del(`/timeclock/entries/${id}`); load(); } catch (e: any) { flash(e.message); } }
  async function reopen(id: string) { try { await api.post(`/timeclock/entries/${id}/reopen`, {}); load(); } catch (e: any) { flash(e.message); } }

  const chip = "bg-white rounded-2xl border border-slate-200/90 shadow-clay-card px-4 py-3";
  return (
    <Shell title="Timesheets">
      <div className="px-6 pt-4 flex items-center gap-1 border-b border-slate-200/70">
        {([["timesheet", "Timesheet"], ["hours", "Employee hours"]] as [typeof topTab, string][]).map(([id, label]) => (
          <button key={id} onClick={() => setTopTab(id)}
            className={`px-3 py-2 text-sm font-semibold rounded-t-lg -mb-px border-b-2 ${topTab === id ? "text-primary border-primary" : "text-ink-tertiary border-transparent hover:text-ink-primary"}`}>{label}</button>
        ))}
      </div>
      {topTab === "hours" ? (
        <div className="flex-1 overflow-y-auto px-6 py-5"><EmployeeHours /></div>
      ) : (
      <div className="flex-1 overflow-y-auto px-6 py-5 space-y-5">
        {/* controls */}
        <div className="flex flex-wrap items-end gap-3">
          <div><label className="block text-[11px] font-bold text-ink-tertiary uppercase mb-1">Employee</label>
            <select value={empId} onChange={(e) => setEmpId(e.target.value)} className="glass-input rounded-lg px-3 py-2 text-sm font-semibold text-ink-primary">{emps.map((e) => <option key={e.id} value={e.id}>{e.name}</option>)}</select></div>
          <div><label className="block text-[11px] font-bold text-ink-tertiary uppercase mb-1">From</label><DateField mode="date" value={from} onChange={setFrom} className="w-40" /></div>
          <div><label className="block text-[11px] font-bold text-ink-tertiary uppercase mb-1">To</label><DateField mode="date" value={to} onChange={setTo} className="w-40" /></div>
          <button onClick={() => setAdd(true)} className="h-[38px] px-3.5 rounded-xl bg-white border border-slate-200 text-xs font-bold text-ink-secondary hover:bg-slate-50 flex items-center gap-1.5"><M n="add" c="text-sm" />Add entry</button>
          <button onClick={approve} className="h-[38px] px-4 rounded-xl bg-gradient-to-r from-primary to-primary-container text-white text-xs font-bold shadow-clay-sm hover:brightness-110 ml-auto">Approve range</button>
        </div>

        {/* summary */}
        <div className="grid grid-cols-2 sm:grid-cols-5 gap-3">
          <div className={chip}><div className="text-[10px] font-bold uppercase text-ink-tertiary">Total</div><div className="text-lg font-black text-ink-primary">{sheet?.total_hours ?? 0}h</div></div>
          <div className={chip}><div className="text-[10px] font-bold uppercase text-ink-tertiary">Straight / OT</div><div className="text-lg font-black text-ink-primary">{sheet?.straight_hours ?? 0} / {sheet?.overtime_hours ?? 0}</div></div>
          <div className={chip}><div className="text-[10px] font-bold uppercase text-ink-tertiary">Billable</div><div className="text-lg font-black text-ink-primary">{Math.round((sheet?.billable_sec || 0) / 360) / 10}h</div></div>
          <div className={chip}><div className="text-[10px] font-bold uppercase text-ink-tertiary">Utilization</div><div className="text-lg font-black text-ink-primary">{Math.round((sheet?.utilization || 0) * 100)}%</div></div>
          <div className={chip}><div className="text-[10px] font-bold uppercase text-ink-tertiary">Labor cost</div><div className="text-lg font-black text-ink-primary">{money(sheet?.labor_cost)}</div></div>
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-3 gap-5">
          {/* entries */}
          <div className="lg:col-span-2 bg-white rounded-2xl border border-slate-200/90 shadow-clay-card overflow-hidden">
            <div className="p-3.5 border-b border-slate-100 flex items-center justify-between"><span className="text-xs font-bold text-ink-primary uppercase tracking-wider">Entries</span><span className="text-[11px] text-ink-tertiary">{sheet?.entry_count ?? 0} · {sheet?.approved_count ?? 0} approved</span></div>
            <table className="w-full text-xs">
              <thead className="bg-slate-50/70 text-ink-tertiary uppercase text-[10px] font-bold"><tr><th className="text-left py-2.5 px-4">Started</th><th className="text-left py-2.5 px-4">For</th><th className="text-right py-2.5 px-4">Duration</th><th className="text-left py-2.5 px-4">State</th><th className="py-2.5 px-4"></th></tr></thead>
              <tbody className="divide-y divide-slate-100">
                {sheet == null && <SkeletonRows cols={5} />}
                {sheet?.entries?.length === 0 && <tr><td colSpan={5} className="py-6 text-center text-ink-tertiary">No entries in this range.</td></tr>}
                {sheet?.entries?.map((e) => (
                  <tr key={e.id}>
                    <td className="py-2.5 px-4 text-ink-secondary">{e.started_at ? new Date(e.started_at).toLocaleString() : "—"}</td>
                    <td className="py-2.5 px-4 font-semibold text-ink-primary">{e.order_number || e.category || "—"}{e.billable ? "" : <span className="ml-1 text-[9px] uppercase text-ink-tertiary">non-bill</span>}</td>
                    <td className="py-2.5 px-4 text-right font-mono">{hm(e.duration_min)}</td>
                    <td className="py-2.5 px-4"><span className={`text-[10px] uppercase font-bold px-1.5 py-0.5 rounded ${e.state === "approved" ? "bg-emerald-100 text-emerald-700" : e.state === "running" ? "bg-blue-100 text-primary" : "bg-slate-100 text-ink-secondary"}`}>{e.state || "logged"}</span></td>
                    <td className="py-2.5 px-4 text-right whitespace-nowrap">{e.state === "approved" ? <button onClick={() => reopen(e.id)} className="text-primary font-semibold">Reopen</button> : <button onClick={() => voidEntry(e.id)} className="text-rose-600 font-semibold">Void</button>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* roster */}
          <div className="bg-white rounded-2xl border border-slate-200/90 shadow-clay-card overflow-hidden h-fit">
            <div className="p-3.5 border-b border-slate-100 flex items-center gap-2"><span className="w-2 h-2 rounded-full bg-emerald-500" /><span className="text-xs font-bold text-ink-primary uppercase tracking-wider">On the clock now</span></div>
            <div className="divide-y divide-slate-100">
              {roster.length === 0 && <div className="p-5 text-xs text-ink-tertiary">Nobody clocked in.</div>}
              {roster.map((r) => (
                <div key={r.employee_id} className="px-4 py-2.5 flex items-center justify-between text-xs">
                  <div><div className="font-bold text-ink-primary">{r.employee_name}</div><div className="text-[11px] text-ink-tertiary">{r.location_name}</div></div>
                  <span className="font-mono font-semibold text-emerald-700">{Math.floor((r.elapsed_sec || 0) / 3600)}h {Math.floor(((r.elapsed_sec || 0) % 3600) / 60)}m</span>
                </div>
              ))}
            </div>
          </div>
        </div>

        {/* Planned shifts (schedule) — the plan the clock-ins are measured against */}
        <div className="bg-white rounded-2xl border border-slate-200/90 shadow-clay-card overflow-hidden">
          <div className="p-3.5 border-b border-slate-100 flex items-center justify-between">
            <div><span className="text-xs font-bold text-ink-primary uppercase tracking-wider">Planned shifts</span><span className="text-[11px] text-ink-tertiary ml-2">what actual hours are measured against</span></div>
            <button onClick={() => setAddSched(true)} className="text-xs font-semibold text-primary flex items-center gap-1"><M n="add" c="text-sm" />Add shift / absence</button>
          </div>
          {cmp && (
            <div className="grid grid-cols-3 divide-x divide-slate-100 border-b border-slate-100 text-center">
              <div className="py-3"><div className="text-[10px] font-bold uppercase text-ink-tertiary">Planned</div><div className="text-base font-black text-ink-primary tabular-nums">{cmp.planned_hours}h</div></div>
              <div className="py-3"><div className="text-[10px] font-bold uppercase text-ink-tertiary">Actual</div><div className="text-base font-black text-ink-primary tabular-nums">{cmp.actual_hours}h</div></div>
              <div className="py-3"><div className="text-[10px] font-bold uppercase text-ink-tertiary">Variance</div><div className={`text-base font-black tabular-nums ${cmp.variance_hours < 0 ? "text-rose-600" : cmp.variance_hours > 0 ? "text-amber-600" : "text-emerald-600"}`}>{cmp.variance_hours > 0 ? "+" : ""}{cmp.variance_hours}h</div></div>
            </div>
          )}
          <div className="divide-y divide-slate-100 text-xs">
            {sched == null && <div className="p-5 text-ink-tertiary">Loading planned shifts…</div>}
            {sched?.length === 0 && <div className="p-6 text-center text-ink-tertiary">No planned shifts in this range. Add one to compare against actual hours.</div>}
            {sched?.map((w) => (
              <div key={w.id} className="px-4 py-2.5 flex items-center justify-between">
                <div className="flex items-center gap-3">
                  <span className="font-mono text-ink-secondary w-28">{new Date(w.day).toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" })}</span>
                  {w.kind === "shift"
                    ? <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px] font-semibold bg-blue-50 text-primary border border-blue-200/60 tabular-nums">{w.start}–{w.end} · {w.planned_hours}h</span>
                    : <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px] font-semibold bg-amber-50 text-amber-700 border border-amber-200/60 capitalize">{w.kind.replace(/_/g, " ")}</span>}
                  {w.note && <span className="text-ink-tertiary">{w.note}</span>}
                </div>
                <button aria-label="Remove" onClick={() => delSched(w.id)} title="Remove" className="text-ink-tertiary hover:text-rose-600"><M n="close" c="text-sm" /></button>
              </div>
            ))}
          </div>
        </div>
      </div>
      )}
      {add && <ManualEntryDrawer employees={emps} defaultEmp={empId} onClose={() => setAdd(false)} onDone={() => { setAdd(false); load(); }} />}
      {addSched && <ScheduleDrawer employees={emps} defaultEmp={empId} onClose={() => setAddSched(false)} onDone={() => { setAddSched(false); loadSched(); }} />}
      {toast && <div className="fixed bottom-6 left-1/2 -translate-x-1/2 bg-ink-primary text-white text-xs font-semibold px-4 py-2.5 rounded-xl shadow-clay-drawer z-[70]">{toast}</div>}
    </Shell>
  );
}

function ManualEntryDrawer({ employees, defaultEmp, onClose, onDone }: { employees: { id: string; name: string }[]; defaultEmp: string; onClose: () => void; onDone: () => void }) {
  const [f, setF] = useState<any>({ employee_id: defaultEmp, category: "job", started_at: isoDateTimeLocal(), duration_min: 60, billable: true, note: "" });
  const [busy, setBusy] = useState(false); const [err, setErr] = useState<string | null>(null);
  const set = (k: string, v: any) => setF((s: any) => ({ ...s, [k]: v }));
  async function save() {
    if (!f.started_at) { setErr("Start time is required"); return; }
    setBusy(true); setErr(null);
    try { await api.post("/timeclock/entries", { employee_id: f.employee_id, category: f.category, started_at: new Date(f.started_at).toISOString(), duration_min: Number(f.duration_min) || 0, billable: f.billable, note: f.note || null }); onDone(); }
    catch (e: any) { setErr(e.message || "Could not add"); setBusy(false); }
  }
  const fld = "glass-input rounded-lg px-3 py-2 text-sm w-full";
  const lbl = "block text-[11px] font-bold text-ink-tertiary uppercase mb-1";
  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      <div className="absolute inset-0 bg-ink-primary/40 backdrop-blur-[2px]" onClick={onClose} />
      <div className="relative w-[460px] max-w-full bg-white h-full shadow-drawer flex flex-col">
        <div className="flex items-center justify-between px-5 py-4 border-b border-slate-200"><h2 className="font-bold text-ink-primary">Add time entry</h2><button aria-label="Close" onClick={onClose} className="w-8 h-8 rounded-lg hover:bg-slate-200/60 flex items-center justify-center text-ink-tertiary"><M n="close" c="text-base" /></button></div>
        <div className="flex-1 overflow-y-auto p-5 space-y-4">
          {err && <div className="rounded-lg bg-rose-50 border border-rose-200 text-rose-700 text-xs px-3 py-2">{err}</div>}
          <div><label className={lbl}>Employee</label><select value={f.employee_id} onChange={(e) => set("employee_id", e.target.value)} className={fld}>{employees.map((e) => <option key={e.id} value={e.id}>{e.name}</option>)}</select></div>
          <div className="grid grid-cols-2 gap-3">
            <div><label className={lbl}>Category</label><select value={f.category} onChange={(e) => set("category", e.target.value)} className={fld}><option value="job">Job</option><option value="admin">Admin</option><option value="break">Break</option></select></div>
            <div><label className={lbl}>Duration (min)</label><input type="number" min={0} value={f.duration_min} onChange={(e) => set("duration_min", Number(e.target.value))} className={fld} /></div>
          </div>
          <div><label className={lbl}>Started at</label><DateField mode="datetime" value={f.started_at} onChange={(v) => set("started_at", v)} /></div>
          <label className="flex items-center gap-2 text-xs font-medium text-ink-secondary"><input type="checkbox" checked={f.billable} onChange={(e) => set("billable", e.target.checked)} className="accent-primary" />Billable</label>
          <div><label className={lbl}>Note</label><textarea value={f.note} onChange={(e) => set("note", e.target.value)} rows={2} className={fld} /></div>
        </div>
        <div className="px-5 py-4 border-t border-slate-200 flex justify-end gap-2"><button onClick={onClose} className="px-4 py-2 rounded-lg border border-slate-200 text-ink-secondary text-xs font-semibold">Cancel</button><button onClick={save} disabled={busy} className="px-4 py-2 rounded-lg bg-primary text-white text-xs font-semibold disabled:opacity-60">{busy ? "Saving…" : "Add entry"}</button></div>
      </div>
    </div>
  );
}

// Build 73 (FE) — add a planned shift or absence (WorkSchedule). Manager-gated server-side.
function ScheduleDrawer({ employees, defaultEmp, onClose, onDone }: { employees: { id: string; name: string }[]; defaultEmp: string; onClose: () => void; onDone: () => void }) {
  const [f, setF] = useState<any>({ employee_id: defaultEmp, day: isoDate(), kind: "shift", start: "09:00", end: "17:00", note: "" });
  const [busy, setBusy] = useState(false); const [err, setErr] = useState<string | null>(null);
  const set = (k: string, v: any) => setF((s: any) => ({ ...s, [k]: v }));
  const isShift = f.kind === "shift";
  async function save() {
    if (!f.employee_id) { setErr("Pick an employee"); return; }
    if (isShift && f.end <= f.start) { setErr("Shift end must be after start"); return; }
    setBusy(true); setErr(null);
    try {
      await api.post("/schedules", { employee_id: f.employee_id, day: f.day, kind: f.kind, start: isShift ? f.start : null, end: isShift ? f.end : null, note: f.note || null });
      onDone();
    } catch (e: any) { setErr(e.message || "Could not save (managers only)"); setBusy(false); }
  }
  const fld = "glass-input rounded-lg px-3 py-2 text-sm w-full";
  const lbl = "block text-[11px] font-bold text-ink-tertiary uppercase mb-1";
  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      <div className="absolute inset-0 bg-ink-primary/40 backdrop-blur-[2px]" onClick={onClose} />
      <div className="relative w-[460px] max-w-full bg-white h-full shadow-drawer flex flex-col">
        <div className="flex items-center justify-between px-5 py-4 border-b border-slate-200"><h2 className="font-bold text-ink-primary">Add planned shift</h2><button aria-label="Close" onClick={onClose} className="w-8 h-8 rounded-lg hover:bg-slate-200/60 flex items-center justify-center text-ink-tertiary"><M n="close" c="text-base" /></button></div>
        <div className="flex-1 overflow-y-auto p-5 space-y-4">
          {err && <div className="rounded-lg bg-rose-50 border border-rose-200 text-rose-700 text-xs px-3 py-2">{err}</div>}
          <div><label className={lbl}>Employee</label><select value={f.employee_id} onChange={(e) => set("employee_id", e.target.value)} className={fld}>{employees.map((e) => <option key={e.id} value={e.id}>{e.name}</option>)}</select></div>
          <div className="grid grid-cols-2 gap-3">
            <div><label className={lbl}>Day</label><DateField mode="date" value={f.day} onChange={(v) => set("day", v)} /></div>
            <div><label className={lbl}>Type</label><select value={f.kind} onChange={(e) => set("kind", e.target.value)} className={fld}><option value="shift">Shift</option><option value="day_off">Day off</option><option value="vacation">Vacation</option><option value="sick">Sick</option></select></div>
          </div>
          {isShift && (
            <div className="grid grid-cols-2 gap-3">
              <div><label className={lbl}>Start</label><input type="time" value={f.start} onChange={(e) => set("start", e.target.value)} className={fld} /></div>
              <div><label className={lbl}>End</label><input type="time" value={f.end} onChange={(e) => set("end", e.target.value)} className={fld} /></div>
            </div>
          )}
          <div><label className={lbl}>Note</label><input value={f.note} onChange={(e) => set("note", e.target.value)} placeholder="Optional" className={fld} /></div>
        </div>
        <div className="px-5 py-4 border-t border-slate-200 flex justify-end gap-2"><button onClick={onClose} className="px-4 py-2 rounded-lg border border-slate-200 text-ink-secondary text-xs font-semibold">Cancel</button><button onClick={save} disabled={busy} className="px-4 py-2 rounded-lg bg-primary text-white text-xs font-semibold disabled:opacity-60">{busy ? "Saving…" : "Add to schedule"}</button></div>
      </div>
    </div>
  );
}
