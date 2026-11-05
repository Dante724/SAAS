import { useEffect, useState } from "react";
import Shell, { M } from "../../components/Shell";
import DateField from "../../components/DateField";
import { api, getLocationScope } from "../../api/client";
import { loadFailed } from "../../lib/notice";
import { clickable } from "../../lib/clickable";
import { useClientSearch } from "../../hooks/useClientSearch";
import { fmtDay, wallDate } from "../../lib/serverTime";
import PageLayout, { Toolbar } from "../../components/PageLayout";
import Button, { IconButton } from "../../components/Button";
import Tabs from "../../components/Tabs";
import Card, { Badge, type BadgeTone } from "../../components/Card";
import Drawer from "../../components/Drawer";
import EmptyState from "../../components/EmptyState";
import { Field, Input, Select, labelClass } from "../../components/Field";

type Appointment = { id: string; client_name?: string; service_name?: string; employee_name?: string; scheduled_for?: string; ends_at?: string; duration_min?: number; status?: string; note?: string; order_id?: string | null };
type Emp = { id: string; name: string };
type Service = { id: string; name?: string; duration_min?: number };
type ClientLite = { id: string; name: string; phone?: string };
type DayHours = { open?: string; close?: string; closed?: boolean };
type Loc = { id: string; active?: boolean; working_hours?: Record<string, DayHours> | null };

// Appointment status keys match the backend (booked|done|canceled|no_show). Changing status
// fires the org's appointment notification rules server-side (confirm / cancel / no-show / done).
const STATUS_TONE: Record<string, BadgeTone> = { booked: "warning", done: "success", canceled: "danger", no_show: "neutral" };
const STATUS_LABEL: Record<string, string> = { booked: "Booked", done: "Done", canceled: "Canceled", no_show: "No-show" };
function F({ l, v }: { l: string; v: string }) {
  return <div><div className="text-xs text-ink-tertiary mb-0.5">{l}</div><div className="text-sm font-semibold text-ink-primary">{v || "—"}</div></div>;
}
// scheduled_for / ends_at are shop wall-clock times: read them with wallDate, never new Date(iso).
const dayKey = (iso?: string) => { const d = wallDate(iso); return d ? d.toLocaleDateString([], { weekday: "long", day: "numeric", month: "long" }) : "Unscheduled"; };
const time = (iso?: string) => { const d = wallDate(iso); return d ? d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }) : "—"; };
const pad = (n: number) => String(n).padStart(2, "0");
const ymd = (d: Date) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
const todayYmd = () => ymd(new Date());
const addDays = (day: string, n: number) => { const d = wallDate(`${day}T00:00`) || new Date(); d.setDate(d.getDate() + n); return ymd(d); };
/** "Thu, Oct 30, 2026" for a "YYYY-MM-DD" shop day. */
const dayLabel = (day: string) => { const d = wallDate(`${day}T00:00`); return d ? `${d.toLocaleDateString([], { weekday: "short" })}, ${fmtDay(day)}` : ""; };
const WEEKDAY_KEYS = ["sun", "mon", "tue", "wed", "thu", "fri", "sat"];   // Date.getDay() → working_hours key
const hourOf = (hm?: string) => { const m = /^(\d{1,2}):(\d{2})/.exec(hm || ""); return m ? { h: Number(m[1]), m: Number(m[2]) } : null; };
const fmtHm = (hm: string) => { const t = hourOf(hm); if (!t) return hm; const d = new Date(2000, 0, 1, t.h, t.m); return d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }); };

/** The scheduler's hour rows for a day: the location's opening hours for that weekday, or 09:00–17:00
 *  (the server's default booking window) when the day is closed or no hours are saved — always
 *  widened so no appointment falls outside. */
function dayWindow(loc: Loc | null | undefined, day: string, appts: { scheduled_for?: string }[]) {
  const wd = wallDate(`${day}T00:00`);
  const dh = wd && loc?.working_hours ? loc.working_hours[WEEKDAY_KEYS[wd.getDay()]] : undefined;
  const closed = !!dh?.closed;
  const o = !closed ? hourOf(dh?.open) : null; const c = !closed ? hourOf(dh?.close) : null;
  let first = 9, last = 16, open: { open: string; close: string } | null = null;
  if (o && c && (c.h * 60 + c.m) > (o.h * 60 + o.m)) {
    first = o.h; last = c.m > 0 ? c.h : c.h - 1; open = { open: dh!.open!, close: dh!.close! };
  }
  for (const a of appts) { const d = wallDate(a.scheduled_for); if (d) { first = Math.min(first, d.getHours()); last = Math.max(last, d.getHours()); } }
  return { hours: Array.from({ length: last - first + 1 }, (_, i) => first + i), closed, open };
}

export default function AppointmentsHub() {
  const [rows, setRows] = useState<Appointment[] | null>(null);
  const [emps, setEmps] = useState<Emp[]>([]);
  const [services, setServices] = useState<Service[]>([]);
  const [drawer, setDrawer] = useState(false);
  const [f, setF] = useState<any>({ client_name: "", service_id: "", employee_id: "", scheduled_for: "", duration_min: 60, note: "" });
  const [busy, setBusy] = useState(false);
  const [toast, setToast] = useState<string | null>(null);
  const flash = (m: string) => { setToast(m); setTimeout(() => setToast(null), 2200); };
  const [view, setView] = useState<"agenda" | "scheduler">("agenda");
  const [drag, setDrag] = useState<string | null>(null);
  // The scheduler's day ("YYYY-MM-DD", shop wall-clock). Shared by both views so switching keeps it.
  const [day, setDay] = useState(todayYmd);
  const [dayRows, setDayRows] = useState<Appointment[] | null>(null);
  const [locs, setLocs] = useState<Loc[]>([]);
  // Client picker: search existing clients, or keep the typed name as a new walk-in.
  const [clientQ, setClientQ] = useState(""); const [clientOpen, setClientOpen] = useState(false);
  const clientResults = useClientSearch<ClientLite>(clientQ, clientOpen && !f.client_id, 6);
  const [customTime, setCustomTime] = useState(false);
  // New-appointment availability: pick a day → show real open slots from the schedule engine (legacy /appointments/availability).
  const [bkDate, setBkDate] = useState("");
  const [slots, setSlots] = useState<{ start: string; end: string; start_iso: string }[] | null>(null);
  const [slotMsg, setSlotMsg] = useState("");
  useEffect(() => {
    if (!drawer || !bkDate) { setSlots(null); setSlotMsg(""); return; }
    setSlots(null); setSlotMsg("");
    const p = new URLSearchParams({ date_str: bkDate, duration_min: String(Number(f.duration_min) || 60) });
    if (f.employee_id) p.set("employee_id", f.employee_id);
    if (f.service_id) p.set("service_id", f.service_id);
    api.get<any>(`/appointments/availability?${p}`).then((r) => {
      setSlots(r.slots || []);
      setSlotMsg(r.reason || (!r.slots?.length ? "No open slots this day" : ""));
    }).catch(() => { setSlots([]); setSlotMsg("Couldn't load slots"); });
  }, [drawer, bkDate, f.employee_id, f.service_id, f.duration_min]);
  async function moveToHour(id: string, hour: number) {
    const b = (dayRows || rows || []).find((x) => x.id === id); if (!b || !b.scheduled_for) return;
    // Scheduled times are shop wall-clock: keep the day as stored and send the new hour as-is.
    const iso = `${b.scheduled_for.slice(0, 10)}T${String(hour).padStart(2, "0")}:00:00`;
    setRows((cur) => (cur || []).map((x) => x.id === id ? { ...x, scheduled_for: iso } : x)); // optimistic
    setDayRows((cur) => (cur || []).map((x) => x.id === id ? { ...x, scheduled_for: iso } : x));
    try { await api.patch(`/appointments/${id}`, { scheduled_for: iso }); flash("Rescheduled"); } catch { load(); }
  }

  // Appointment detail drawer (open an appointment → see details, change status, reschedule, convert).
  const [sel, setSel] = useState<Appointment | null>(null);
  const [selBusy, setSelBusy] = useState(false);
  const [reDate, setReDate] = useState(""); const [reTime, setReTime] = useState("");
  async function setStatus(s: string) {
    if (!sel) return; setSelBusy(true);
    try { await api.patch(`/appointments/${sel.id}`, { status: s }); setSel({ ...sel, status: s }); flash(`Marked ${STATUS_LABEL[s] || s}`); load(); }
    catch (e: any) { flash(e.message || "Failed"); } finally { setSelBusy(false); }
  }
  async function reschedule() {
    if (!sel || !reDate || !reTime) { flash("Pick a new date & time"); return; }
    setSelBusy(true);
    try { const iso = `${reDate}T${reTime.slice(0, 5)}:00`; /* shop wall-clock, as picked */ await api.patch(`/appointments/${sel.id}`, { scheduled_for: iso }); setSel({ ...sel, scheduled_for: iso }); flash("Rescheduled — client notified"); setReDate(""); setReTime(""); load(); }
    catch (e: any) { flash(e.message || "Reschedule clashes with another appointment"); } finally { setSelBusy(false); }
  }
  async function convert(record: "work_order" | "estimate") {
    if (!sel) return; setSelBusy(true);
    try {
      const r = await api.post<any>(`/appointments/${sel.id}/to-order?record=${record}`, {});
      flash(`Converted to ${record === "estimate" ? "estimate" : "work order"} ${r.order_number || ""}`.trim());
      setSel({ ...sel, order_id: r.order_id, status: "done" }); load();
    } catch (e: any) { flash(e.message || "Convert failed"); } finally { setSelBusy(false); }
  }

  // The agenda lists every appointment; the scheduler asks the server for one day (?date_str=YYYY-MM-DD).
  function loadDay(d = day) {
    api.get<{ items: Appointment[] }>(`/appointments?date_str=${d}`)
      .then((r) => setDayRows((r.items || []).filter((b) => b.scheduled_for?.slice(0, 10) === d)))
      .catch(() => setDayRows([]));
  }
  function load() { api.get<{ items: Appointment[] }>("/appointments").then((r) => setRows(r.items || [])).catch(() => setRows([])); if (view === "scheduler") loadDay(); }
  useEffect(() => {
    load(); api.get<any>("/staff/employees").then((r) => setEmps(r.items || r || [])).catch(loadFailed("the staff list")); api.get<{ items: Service[] }>("/catalog/services").then((r) => setServices(r.items || [])).catch(loadFailed("services"));
    api.get<any>("/staff/locations").then((r) => setLocs(r.items || r || [])).catch(() => setLocs([]));
  }, []);  // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { if (view === "scheduler") { setDayRows(null); loadDay(day); } }, [view, day]);  // eslint-disable-line react-hooks/exhaustive-deps
  const scopeId = getLocationScope();
  const loc = locs.find((l) => l.id === scopeId) || locs.find((l) => l.active !== false) || locs[0] || null;

  const blank = { client_id: "", client_name: "", service_id: "", employee_id: "", scheduled_for: "", duration_min: 60, note: "" };
  function openNew(prefillDay = day) {
    setF(blank); setClientQ(""); setClientOpen(false); setCustomTime(false); setBkDate(prefillDay); setDrawer(true);
  }
  function pickDate(d: string) {
    setBkDate(d);
    if (f.scheduled_for && f.scheduled_for.slice(0, 10) !== d) setF({ ...f, scheduled_for: "" });
  }

  async function create() {
    if (!f.scheduled_for) { flash("Pick a date and a time"); return; }
    setBusy(true);
    try {
      await api.post("/appointments", { client_id: f.client_id || null, client_name: f.client_name || null, service_id: f.service_id || null, employee_id: f.employee_id || null, scheduled_for: f.scheduled_for, duration_min: Number(f.duration_min) || null, note: f.note || null });
      setDrawer(false); setF(blank); setClientQ(""); setCustomTime(false); setBkDate(""); setSlots(null); load(); flash("Appointment created");
    } catch (e: any) { flash(`Failed: ${e.message}`); } finally { setBusy(false); }
  }

  const groups: Record<string, Appointment[]> = {};
  (rows || []).forEach((b) => { const k = dayKey(b.scheduled_for); (groups[k] ||= []).push(b); });
  const openSel = (b: Appointment) => { setSel(b); setReDate(""); setReTime(""); };
  const onAgenda = view === "agenda";

  return (
    <Shell title="Appointments">
      <PageLayout>
        {/* One toolbar for both views. The day controls only steer the scheduler, so on the agenda
            they stay in place, greyed out: switching views never pushes the content down. */}
        <Toolbar
          actions={<Button variant="primary" onClick={() => openNew()} icon={<M n="add" c="text-sm" />}>New appointment</Button>}
          view={<Tabs look="segmented" label="Appointment views" value={view} onChange={setView} tabs={[
            { id: "agenda", label: "Agenda", icon: <M n="table_rows" c="text-xs" /> },
            { id: "scheduler", label: "Scheduler", icon: <M n="calendar_month" c="text-xs" /> },
          ]} />}
          filters={<div className="flex items-center gap-1.5">
            <IconButton variant="secondary" label="Previous day" disabled={onAgenda} onClick={() => setDay(addDays(day, -1))} icon={<M n="chevron_left" c="text-sm" />} />
            <Button onClick={() => setDay(todayYmd())} disabled={onAgenda || day === todayYmd()}>Today</Button>
            <IconButton variant="secondary" label="Next day" disabled={onAgenda} onClick={() => setDay(addDays(day, 1))} icon={<M n="chevron_right" c="text-sm" />} />
            <DateField mode="date" value={day} onChange={(v) => v && setDay(v)} className="w-44" label="Jump to date" disabled={onAgenda} />
          </div>}
        />
        {!rows && onAgenda && <Card padded={false}><div aria-hidden className="p-5 space-y-3"><span className="sk block h-4 w-1/3 rounded" /><span className="sk block h-4 w-1/2 rounded" /><span className="sk block h-4 w-2/5 rounded" /></div><span className="sr-only" role="status">Loading…</span></Card>}
        {view === "scheduler" && (() => {
          const list = dayRows || [];
          const win = dayWindow(loc, day, list);
          return (
            <Card padded={false}>
              <div className="px-5 py-3 border-b border-slate-100 flex flex-wrap items-center gap-x-3 gap-y-1">
                <span className="text-sm font-semibold text-ink-primary" data-testid="scheduler-day">{dayLabel(day)}</span>
                <span className="text-xs text-ink-tertiary flex items-center gap-1"><M n="schedule" c="text-xs" />{win.closed ? "Closed" : win.open ? `Open ${fmtHm(win.open.open)}–${fmtHm(win.open.close)}` : "No opening hours saved"}</span>
                {list.length > 0 && <span className="text-xs text-ink-tertiary">· Drag an appointment to another hour to reschedule</span>}
              </div>
              {!dayRows && <div aria-hidden className="px-5 py-5 space-y-3"><span className="sk block h-4 w-1/3 rounded" /><span className="sk block h-4 w-1/2 rounded" /></div>}
              {/* No button here: "New appointment" is the first button in the toolbar and opens on this day. */}
              {dayRows && list.length === 0 && <EmptyState icon={<M n="calendar_today" c="text-lg" />} title="No appointments on this day"
                body={win.closed ? "The shop is closed this day." : "The day is open for bookings."} />}
              {list.length > 0 && (
              <div className="divide-y divide-slate-100">
                {win.hours.map((h) => {
                  const cards = list.filter((b) => wallDate(b.scheduled_for)?.getHours() === h);
                  return (
                    <div key={h} onDragOver={(e) => e.preventDefault()} onDrop={() => { if (drag) { moveToHour(drag, h); setDrag(null); } }} className="flex gap-3 px-5 py-2 min-h-[52px] hover:bg-blue-50/20">
                      <div className="w-14 flex-shrink-0 text-2xs tabular-nums font-semibold text-ink-tertiary pt-1.5">{pad(h)}:00</div>
                      <div className="flex-1 flex flex-wrap gap-2">
                        {cards.length === 0 && <div className="text-2xs text-ink-placeholder pt-1.5">—</div>}
                        {cards.map((b) => (
                          <div key={b.id} draggable onDragStart={() => setDrag(b.id)} onDragEnd={() => setDrag(null)} {...clickable(() => openSel(b))} className={`bg-white rounded-xl border border-slate-200/80 shadow-clay-sm px-3 py-1.5 cursor-pointer hover:border-primary/40 ${drag === b.id ? "opacity-50" : ""}`}>
                            <div className="text-xs font-semibold text-ink-primary">{b.service_name || "Appointment"} · {time(b.scheduled_for)}</div>
                            <div className="text-2xs text-ink-tertiary">{b.client_name || "Walk-in"}{b.employee_name ? ` · ${b.employee_name}` : ""}</div>
                          </div>
                        ))}
                      </div>
                    </div>
                  );
                })}
              </div>
              )}
            </Card>
          );
        })()}
        {onAgenda && rows?.length === 0 && <Card padded={false}><EmptyState size="page" icon={<M n="calendar_month" c="text-lg" />} title="No appointments yet" body="Create an appointment or wait for online requests." /></Card>}
        {onAgenda && Object.entries(groups).map(([day, list]) => (
          <section key={day} aria-label={day}>
            <h2 className="text-xs font-semibold text-ink-tertiary mb-2">{day}</h2>
            <Card padded={false} className="divide-y divide-slate-100">
              {list.map((b) => {
                const st = (b.status || "booked").toLowerCase();
                return (
                  <div key={b.id} {...clickable(() => openSel(b))} className="px-5 py-3.5 flex items-center gap-4 hover:bg-blue-50/30 transition-colors cursor-pointer">
                    <div className="text-center flex-shrink-0 w-16"><div className="text-sm font-bold tabular-nums text-ink-primary">{time(b.scheduled_for)}</div>{b.duration_min && <div className="text-2xs text-ink-tertiary tabular-nums">{b.duration_min}m</div>}</div>
                    <div className="w-px h-9 bg-slate-200" />
                    <div className="flex-1 min-w-0"><div className="text-sm font-semibold text-ink-primary truncate">{b.service_name || "Appointment"}</div><div className="text-2xs text-ink-tertiary">{b.client_name || "Walk-in"}{b.employee_name ? ` · ${b.employee_name}` : ""}</div></div>
                    {b.order_id && <Badge tone="info">Work order created</Badge>}
                    <Badge tone={STATUS_TONE[st] || "warning"}>{STATUS_LABEL[st] || st}</Badge>
                  </div>
                );
              })}
            </Card>
          </section>
        ))}
      </PageLayout>

      {drawer && (
        <Drawer size="sm" label="New appointment" title="New appointment" onClose={() => setDrawer(false)}
          footer={<><Button variant="primary" onClick={create} busy={busy}>{busy ? "Creating…" : "Create appointment"}</Button><Button onClick={() => setDrawer(false)}>Cancel</Button></>}>
          <div className="relative">
            <span className={labelClass}>Client</span>
            {f.client_id ? (
              <div className="glass-input rounded-xl h-9 px-3 text-sm text-ink-primary flex items-center justify-between">
                <span className="flex items-center gap-1.5"><M n="person" c="text-sm text-ink-tertiary" />{f.client_name}</span>
                <Button variant="link" onClick={() => { setF({ ...f, client_id: "", client_name: "" }); setClientQ(""); setClientOpen(true); }}>Change</Button>
              </div>
            ) : (
              <>
                <Input value={clientQ} autoComplete="off" role="combobox" aria-expanded={clientOpen} aria-label="Client"
                  onChange={(e) => { setClientQ(e.target.value); setF({ ...f, client_id: "", client_name: e.target.value }); setClientOpen(true); }}
                  onFocus={() => setClientOpen(true)} onBlur={() => setTimeout(() => setClientOpen(false), 150)}
                  onKeyDown={(e) => { if (e.key === "Escape") { e.stopPropagation(); setClientOpen(false); } }}
                  placeholder="Search clients, or type a walk-in name" />
                {clientOpen && (clientResults.length > 0 || clientQ.trim()) && (
                  <div role="listbox" className="absolute z-20 left-0 right-0 mt-1 bg-white border border-slate-200 rounded-xl shadow-clay-drawer p-1.5 max-h-64 overflow-y-auto">
                    {!clientQ.trim() && <div className="px-2.5 pt-1 pb-0.5 text-2xs text-ink-tertiary">Recent clients</div>}
                    {clientResults.map((r) => (
                      <button key={r.id} type="button" role="option" aria-selected={false} onMouseDown={(e) => { e.preventDefault(); setF({ ...f, client_id: r.id, client_name: r.name }); setClientQ(r.name); setClientOpen(false); }}
                        className="flex items-center gap-2 w-full text-left px-2.5 py-2 rounded-lg hover:bg-blue-50/60 text-sm"><span className="font-semibold text-ink-primary">{r.name}</span>{r.phone && <span className="text-ink-tertiary tabular-nums text-xs">{r.phone}</span>}</button>
                    ))}
                    {clientQ.trim() && (
                      <button type="button" onMouseDown={(e) => { e.preventDefault(); setClientOpen(false); }} className="flex items-center gap-2 w-full text-left px-2.5 py-2 rounded-lg hover:bg-slate-50 text-xs text-ink-secondary"><M n="person_add" c="text-sm" />Use "{clientQ.trim()}" as a walk-in</button>
                    )}
                  </div>
                )}
              </>
            )}
          </div>
          <Field label="Service" htmlFor="appt-service"><Select id="appt-service" value={f.service_id} onChange={(e) => { const s = services.find((x) => x.id === e.target.value); setF({ ...f, service_id: e.target.value, duration_min: s?.duration_min || f.duration_min }); }}><option value="">— Select —</option>{services.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}</Select></Field>
          <Field label="Specialist" htmlFor="appt-emp"><Select id="appt-emp" value={f.employee_id} onChange={(e) => setF({ ...f, employee_id: e.target.value })}><option value="">— Unassigned —</option>{emps.map((e) => <option key={e.id} value={e.id}>{e.name}</option>)}</Select></Field>
          <div className="grid grid-cols-2 gap-3">
            <div><span className={labelClass}>Date</span><DateField mode="date" value={bkDate} onChange={pickDate} label="Date" /></div>
            <Field label="Duration (min)" htmlFor="appt-dur"><Input id="appt-dur" type="number" value={f.duration_min} onChange={(e) => setF({ ...f, duration_min: e.target.value })} className="tabular-nums" /></Field>
          </div>
          {bkDate && (
            <div className="text-xs">
              <span className={labelClass}>Available slots</span>
              {slots === null && <div aria-hidden className="flex gap-1.5 py-1"><span className="sk block h-8 w-24 rounded-full" /><span className="sk block h-8 w-24 rounded-full" /><span className="sk block h-8 w-24 rounded-full" /></div>}
              {slots && slots.length > 0 && (
                <Tabs look="chips" label="Available slots" value={f.scheduled_for} onChange={(v) => setF({ ...f, scheduled_for: v })}
                  tabs={slots.map((s) => ({ id: s.start_iso, label: `${s.start}–${s.end}` }))} />
              )}
              {slots && slots.length === 0 && <div className="text-amber-700 py-1">{slotMsg}</div>}
              {!customTime ? (
                <Button variant="link" className="mt-2" onClick={() => setCustomTime(true)}>Custom time</Button>
              ) : (
                <div className="mt-2 flex items-center gap-2">
                  <span className="font-semibold text-ink-secondary flex items-center gap-1"><M n="schedule" c="text-xs" />Custom time</span>
                  <DateField mode="time" value={f.scheduled_for ? f.scheduled_for.slice(11, 16) : ""} onChange={(t) => setF({ ...f, scheduled_for: t ? `${bkDate}T${t}` : "" })} className="w-32" label="Custom time" />
                </div>
              )}
            </div>
          )}
          {!bkDate && <p className="text-xs text-ink-tertiary">Pick a date to see open times.</p>}
          <Field label="Note" htmlFor="appt-note"><textarea id="appt-note" value={f.note} onChange={(e) => setF({ ...f, note: e.target.value })} className="glass-input w-full rounded-xl px-3 py-2 h-20 text-sm text-ink-primary resize-none" placeholder="Optional…" /></Field>
        </Drawer>
      )}
      {sel && (
        <Drawer size="sm" z={55} label="Appointment" title={sel.service_name || "Appointment"} sub={`${dayKey(sel.scheduled_for)} · ${time(sel.scheduled_for)}`} onClose={() => setSel(null)}
          footer={sel.order_id ? undefined : <>
            <Button variant="primary" disabled={selBusy} onClick={() => convert("work_order")} icon={<M n="handyman" c="text-sm" />}>Create work order</Button>
            <Button disabled={selBusy} onClick={() => convert("estimate")} icon={<M n="request_quote" c="text-sm" />}>Create estimate</Button>
          </>}>
          <div className="grid grid-cols-2 gap-3">
            <F l="Client" v={sel.client_name || "Walk-in"} />
            <F l="Specialist" v={sel.employee_name || "Unassigned"} />
            <F l="Time" v={`${time(sel.scheduled_for)}${sel.ends_at ? `–${time(sel.ends_at)}` : ""}`} />
            <F l="Duration" v={sel.duration_min ? `${sel.duration_min} min` : "—"} />
          </div>
          {sel.note && <F l="Note" v={sel.note} />}

          <div>
            <span className={labelClass}>Status — changing it notifies the client</span>
            <Tabs look="segmented" label="Appointment status" value={sel.status || "booked"} onChange={setStatus}
              tabs={["booked", "done", "canceled", "no_show"].map((s) => ({ id: s, label: STATUS_LABEL[s], disabled: selBusy }))} />
          </div>

          {sel.order_id && <div className="rounded-2xl bg-blue-50/60 border border-blue-100 p-3.5 text-xs font-semibold text-primary flex items-center gap-2"><M n="link" c="text-sm" />Linked to a work order or estimate.</div>}

          <div>
            <span className={labelClass}>Reschedule</span>
            <div className="flex gap-2">
              <DateField mode="date" value={reDate} onChange={setReDate} className="flex-1" />
              <DateField mode="time" value={reTime} onChange={setReTime} className="w-28" />
              <Button disabled={selBusy} onClick={reschedule}>Move</Button>
            </div>
          </div>
        </Drawer>
      )}
      {toast && <div role="status" className="fixed bottom-6 left-1/2 -translate-x-1/2 bg-ink-primary text-white text-xs font-semibold px-4 py-2.5 rounded-xl shadow-clay-drawer z-[70]">{toast}</div>}
    </Shell>
  );
}
