import SkeletonRows from "../../components/SkeletonRows";
import PageSummary from "../../components/PageSummary";
import { useEffect, useState } from "react";
import { useVisibleInterval } from "../../hooks/useVisibleInterval";
import Shell, { M } from "../../components/Shell";
import DateField from "../../components/DateField";
import { api } from "../../api/client";
import { money } from "../orders/types";
import { confirmDialog } from "../../lib/dialog";
import { isoDate, isoDateTimeLocal } from "../../lib/localDate";
import { EmployeeHours } from "../company/CompanyHub";
import { fmtServerTime } from "../../lib/serverTime";
import { loadFailed } from "../../lib/notice";
import PageLayout, { Toolbar } from "../../components/PageLayout";
import Button, { IconButton } from "../../components/Button";
import Tabs from "../../components/Tabs";
import Card, { Badge, StatTile, type BadgeTone } from "../../components/Card";
import Drawer from "../../components/Drawer";
import EmptyState, { ErrorState, TableEmpty } from "../../components/EmptyState";
import { Field, Input, Select, labelClass } from "../../components/Field";
import { tableClass, theadRowClass, Th, Td } from "../../components/Table";

// Timesheets — React port of legacy timesheets.js. Per-employee grid + approve,
// manual entry add/void/reopen, and a live "who's on the clock" roster.
// Endpoints: /timeclock/timesheet, /timesheet/approve, /entries(+/{id}, /reopen), /roster.
type Entry = { id: string; started_at?: string; ended_at?: string; order_number?: string; category?: string; duration_min?: number; billable?: boolean; state?: string; note?: string };
type Sheet = { total_hours?: number; straight_hours?: number; overtime_hours?: number; billable_sec?: number; utilization?: number; labor_cost?: number; entry_count?: number; approved_count?: number; entries?: Entry[] };

function weekAgo() { const d = new Date(); d.setDate(d.getDate() - 6); return isoDate(d); }
const today = () => isoDate();
const STATE_LABEL: Record<string, string> = { logged: "Logged", running: "Running", approved: "Approved" };
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

  const [empsLoaded, setEmpsLoaded] = useState(false);
  const [sheetErr, setSheetErr] = useState(false);
  useEffect(() => { api.get<any>("/staff/employees").then((r) => { const it = r.items || r || []; setEmps(it); if (it[0] && !empId) setEmpId(it[0].id); }).catch(loadFailed("the staff list")).finally(() => setEmpsLoaded(true)); }, []);
  const load = () => { if (empId) api.get<Sheet>(`/timeclock/timesheet?employee_id=${empId}&date_from=${from}&date_to=${to}`).then((r) => { setSheet(r); setSheetErr(false); }).catch(() => { setSheet(null); setSheetErr(true); }); };
  const loadRoster = () => api.get<any>("/timeclock/roster").then((r) => setRoster(r.on_shift || [])).catch(() => setRoster([]));
  useEffect(() => { load(); }, [empId, from, to]);
  useVisibleInterval(loadRoster, 30000);
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

  // Still waiting for the first timesheet: show placeholders, never "0h" / "₹0".
  const sheetLoading = !sheet && !sheetErr && (!empsLoaded || emps.length > 0);
  const num = (v: string) => (sheetLoading ? <span className="sk inline-block h-7 w-16 align-middle" aria-hidden /> : sheet ? v : "—");
  const onHours = topTab === "hours";
  const STATE_TONE: Record<string, BadgeTone> = { approved: "success", running: "info", logged: "neutral" };

  return (
    <Shell title="Timesheets">
      <div className="px-6 pt-5 flex-shrink-0">
        <Tabs label="Timesheet sections" value={topTab} onChange={setTopTab} tabs={[{ id: "timesheet", label: "Timesheet" }, { id: "hours", label: "Employee hours" }]} />
      </div>
      <PageLayout>
        {/* The same toolbar on both tabs. Employee hours covers everyone, so the employee and date
            filters and Approve range stay in place there, greyed out. */}
        <Toolbar
          actions={<>
            <Button variant="primary" onClick={approve} disabled={onHours || !empId}>Approve range</Button>
            <Button onClick={() => setAdd(true)} icon={<M n="add" c="text-sm" />}>Add entry</Button>
          </>}
          filters={<>
            <Select aria-label="Employee" value={empId} onChange={(e) => setEmpId(e.target.value)} disabled={onHours} className="sm:!w-44">{emps.map((e) => <option key={e.id} value={e.id}>{e.name}</option>)}</Select>
            <div className="flex items-center gap-1.5">
              <DateField mode="date" value={from} onChange={setFrom} className="w-36" label="From" disabled={onHours} />
              <span className="text-xs text-ink-tertiary" aria-hidden>to</span>
              <DateField mode="date" value={to} onChange={setTo} className="w-36" label="To" disabled={onHours} />
            </div>
          </>}
        />
      {onHours ? <EmployeeHours /> : (<>
        {/* summary */}
        <PageSummary stats={[
          { label: "Hours worked", value: num(`${sheet?.total_hours ?? 0}h`) },
          { label: "Regular / overtime", value: num(`${sheet?.straight_hours ?? 0}h / ${sheet?.overtime_hours ?? 0}h`) },
          { label: "Charged to jobs", value: num(`${Math.round((sheet?.billable_sec || 0) / 360) / 10}h`) },
          { label: "Share charged to jobs", value: num(`${Math.round((sheet?.utilization || 0) * 100)}%`) },
          { label: "Labour cost", value: num(money(sheet?.labor_cost)) },
        ]} />

        <div className="grid grid-cols-1 lg:grid-cols-3 gap-5">
          {/* entries */}
          <Card padded={false} className="lg:col-span-2">
            <div className="px-4 py-3 border-b border-slate-100 flex items-center justify-between"><h2 className="text-xs font-semibold text-ink-primary">Entries</h2><span className="text-2xs text-ink-tertiary tabular-nums">{sheet?.entry_count ?? 0} · {sheet?.approved_count ?? 0} approved</span></div>
            <div className="overflow-x-auto">
            <table className={tableClass}>
              <thead><tr className={theadRowClass}><Th>Started</Th><Th>For</Th><Th num>Duration</Th><Th>State</Th><Th><span className="sr-only">Actions</span></Th></tr></thead>
              <tbody className="divide-y divide-slate-100">
                {sheet == null && !sheetErr && <SkeletonRows cols={5} />}
                {sheetErr && <tr><td colSpan={5} className="p-0"><ErrorState onRetry={load} /></td></tr>}
                {sheet?.entries?.length === 0 && <TableEmpty cols={5} icon={<M n="schedule" c="text-lg" />} title="No entries in this range" body="Clock-ins and added entries for this employee show up here." />}
                {sheet?.entries?.map((e) => (
                  <tr key={e.id}>
                    <Td className="text-ink-secondary">{fmtServerTime(e.started_at) || "—"}</Td>
                    <Td className="font-semibold text-ink-primary">{e.order_number || e.category || "—"}{e.billable ? "" : <span className="ml-1 text-2xs font-normal text-ink-tertiary">Not billable</span>}</Td>
                    <Td num>{hm(e.duration_min)}</Td>
                    <Td><Badge tone={STATE_TONE[e.state || "logged"] || "neutral"}>{STATE_LABEL[e.state || "logged"] || e.state}</Badge></Td>
                    <Td className="text-right whitespace-nowrap">{e.state === "approved" ? <Button variant="link" onClick={() => reopen(e.id)}>Reopen</Button> : <Button variant="link" className="!text-rose-600" onClick={() => voidEntry(e.id)}>Void</Button>}</Td>
                  </tr>
                ))}
              </tbody>
            </table>
            </div>
          </Card>

          {/* roster */}
          <Card padded={false} className="h-fit">
            <div className="px-4 py-3 border-b border-slate-100 flex items-center gap-2"><span className="w-2 h-2 rounded-full bg-emerald-500" /><h2 className="text-xs font-semibold text-ink-primary">On the clock now</h2></div>
            <div className="divide-y divide-slate-100">
              {roster.length === 0 && <EmptyState size="inline" className="px-4" title="Nobody clocked in." />}
              {roster.map((r) => (
                <div key={r.employee_id} className="px-4 py-2.5 flex items-center justify-between text-xs">
                  <div><div className="font-semibold text-ink-primary">{r.employee_name}</div><div className="text-2xs text-ink-tertiary">{r.location_name}</div></div>
                  <span className="tabular-nums font-semibold text-emerald-700">{Math.floor((r.elapsed_sec || 0) / 3600)}h {Math.floor(((r.elapsed_sec || 0) % 3600) / 60)}m</span>
                </div>
              ))}
            </div>
          </Card>
        </div>

        {/* Planned shifts (schedule) — the plan the clock-ins are measured against */}
        <Card padded={false}>
          <div className="px-4 py-3 border-b border-slate-100 flex flex-wrap items-center gap-x-3 gap-y-2">
            <div className="min-w-0 flex-1"><h2 className="inline text-xs font-semibold text-ink-primary">Planned shifts</h2><span className="text-2xs text-ink-tertiary ml-2">what actual hours are measured against</span></div>
            <Button size="sm" onClick={() => setAddSched(true)} icon={<M n="add" c="text-sm" />}>Add shift or absence</Button>
          </div>
          {cmp && (
            <div className="grid grid-cols-3 gap-4 px-4 py-3 border-b border-slate-100">
              <StatTile label="Planned" value={`${cmp.planned_hours}h`} />
              <StatTile label="Actual" value={`${cmp.actual_hours}h`} />
              <StatTile label="Variance" tone={cmp.variance_hours < 0 ? "bad" : cmp.variance_hours === 0 ? "good" : "neutral"} value={`${cmp.variance_hours > 0 ? "+" : ""}${cmp.variance_hours}h`} />
            </div>
          )}
          <div className="divide-y divide-slate-100 text-xs">
            {sched == null && <div aria-hidden className="p-4 space-y-2.5"><span className="sk block h-4 w-1/3 rounded" /><span className="sk block h-4 w-1/2 rounded" /></div>}
            {sched?.length === 0 && <EmptyState icon={<M n="calendar_month" c="text-lg" />} title="No planned shifts in this range" body="Add one to compare against actual hours." />}
            {sched?.map((w) => (
              <div key={w.id} className="px-4 py-2.5 flex items-center justify-between">
                <div className="flex items-center gap-3">
                  <span className="tabular-nums text-ink-secondary w-28">{new Date(w.day).toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" })}</span>
                  {w.kind === "shift"
                    ? <Badge tone="info" className="tabular-nums">{w.start}–{w.end} · {w.planned_hours}h</Badge>
                    : <Badge tone="warning" className="capitalize">{w.kind.replace(/_/g, " ")}</Badge>}
                  {w.note && <span className="text-ink-tertiary">{w.note}</span>}
                </div>
                <IconButton label="Remove" size="sm" onClick={() => delSched(w.id)} icon={<M n="close" c="text-sm" />} />
              </div>
            ))}
          </div>
        </Card>
      </>)}
      </PageLayout>
      {add && <ManualEntryDrawer employees={emps} defaultEmp={empId} onClose={() => setAdd(false)} onDone={() => { setAdd(false); load(); }} />}
      {addSched && <ScheduleDrawer employees={emps} defaultEmp={empId} onClose={() => setAddSched(false)} onDone={() => { setAddSched(false); loadSched(); }} />}
      {toast && <div role="status" className="fixed bottom-6 left-1/2 -translate-x-1/2 bg-ink-primary text-white text-xs font-semibold px-4 py-2.5 rounded-xl shadow-clay-drawer z-[70]">{toast}</div>}
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
  return (
    <Drawer size="sm" label="Add time entry" title="Add time entry" onClose={onClose}
      footer={<><Button variant="primary" onClick={save} busy={busy}>{busy ? "Saving…" : "Add entry"}</Button><Button onClick={onClose}>Cancel</Button></>}>
      {err && <div role="alert" className="rounded-xl bg-rose-50 border border-rose-200 text-rose-700 text-xs px-3 py-2">{err}</div>}
      <Field label="Employee" htmlFor="te-emp"><Select id="te-emp" value={f.employee_id} onChange={(e) => set("employee_id", e.target.value)}>{employees.map((e) => <option key={e.id} value={e.id}>{e.name}</option>)}</Select></Field>
      <div className="grid grid-cols-2 gap-3">
        <Field label="Category" htmlFor="te-cat"><Select id="te-cat" value={f.category} onChange={(e) => set("category", e.target.value)}><option value="job">Job</option><option value="admin">Admin</option><option value="break">Break</option></Select></Field>
        <Field label="Duration (min)" htmlFor="te-dur"><Input id="te-dur" type="number" min={0} value={f.duration_min} onChange={(e) => set("duration_min", Number(e.target.value))} className="tabular-nums" /></Field>
      </div>
      <div><span className={labelClass}>Started at</span><DateField mode="datetime" value={f.started_at} onChange={(v) => set("started_at", v)} label="Started at" /></div>
      <label className="flex items-center gap-2 text-xs font-medium text-ink-secondary"><input type="checkbox" checked={f.billable} onChange={(e) => set("billable", e.target.checked)} className="w-4 h-4 accent-primary" />Billable</label>
      <Field label="Note" htmlFor="te-note"><textarea id="te-note" value={f.note} onChange={(e) => set("note", e.target.value)} rows={2} className="glass-input w-full rounded-xl px-3 py-2 text-sm text-ink-primary" /></Field>
    </Drawer>
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
  return (
    <Drawer size="sm" label="Add planned shift" title="Add planned shift" onClose={onClose}
      footer={<><Button variant="primary" onClick={save} busy={busy}>{busy ? "Saving…" : "Add to schedule"}</Button><Button onClick={onClose}>Cancel</Button></>}>
      {err && <div role="alert" className="rounded-xl bg-rose-50 border border-rose-200 text-rose-700 text-xs px-3 py-2">{err}</div>}
      <Field label="Employee" htmlFor="ws-emp"><Select id="ws-emp" value={f.employee_id} onChange={(e) => set("employee_id", e.target.value)}>{employees.map((e) => <option key={e.id} value={e.id}>{e.name}</option>)}</Select></Field>
      <div className="grid grid-cols-2 gap-3">
        <div><span className={labelClass}>Day</span><DateField mode="date" value={f.day} onChange={(v) => set("day", v)} label="Day" /></div>
        <Field label="Type" htmlFor="ws-kind"><Select id="ws-kind" value={f.kind} onChange={(e) => set("kind", e.target.value)}><option value="shift">Shift</option><option value="day_off">Day off</option><option value="vacation">Vacation</option><option value="sick">Sick</option></Select></Field>
      </div>
      {isShift && (
        <div className="grid grid-cols-2 gap-3">
          <div><span className={labelClass}>Start</span><DateField mode="time" value={f.start} onChange={(v) => set("start", v)} label="Start" /></div>
          <div><span className={labelClass}>End</span><DateField mode="time" value={f.end} onChange={(v) => set("end", v)} label="End" /></div>
        </div>
      )}
      <Field label="Note" htmlFor="ws-note"><Input id="ws-note" value={f.note} onChange={(e) => set("note", e.target.value)} placeholder="Optional" /></Field>
    </Drawer>
  );
}
