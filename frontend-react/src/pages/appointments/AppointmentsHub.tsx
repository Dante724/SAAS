import { useEffect, useState } from "react";
import Shell, { M } from "../../components/Shell";
import DateField from "../../components/DateField";
import { api } from "../../api/client";
import { loadFailed } from "../../lib/notice";
import { clickable } from "../../lib/clickable";

type Appointment = { id: string; client_name?: string; service_name?: string; employee_name?: string; scheduled_for?: string; ends_at?: string; duration_min?: number; status?: string; note?: string; order_id?: string | null };
type Emp = { id: string; name: string };
type Service = { id: string; name?: string; duration_min?: number };

// Appointment status keys match the backend (booked|done|canceled|no_show). Changing status
// fires the org's appointment notification rules server-side (confirm / cancel / no-show / done).
const STATUS: Record<string, string> = { booked: "bg-amber-100 text-amber-800 border-amber-200", done: "bg-emerald-100 text-emerald-800 border-emerald-200", canceled: "bg-rose-100 text-rose-800 border-rose-200", no_show: "bg-slate-200 text-slate-700 border-slate-300" };
const STATUS_LABEL: Record<string, string> = { booked: "Booked", done: "Done", canceled: "Canceled", no_show: "No-show" };
function F({ l, v }: { l: string; v: string }) {
  return <div><div className="text-[11px] font-bold text-ink-tertiary mb-0.5">{l}</div><div className="text-sm font-semibold text-ink-primary">{v || "—"}</div></div>;
}
const dayKey = (iso?: string) => (iso ? new Date(iso).toLocaleDateString([], { weekday: "long", day: "numeric", month: "long" }) : "Unscheduled");
const time = (iso?: string) => (iso ? new Date(iso).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }) : "—");

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
    const b = (rows || []).find((x) => x.id === id); if (!b || !b.scheduled_for) return;
    // Scheduled times are shop wall-clock: keep the day as stored and send the new hour as-is.
    const iso = `${b.scheduled_for.slice(0, 10)}T${String(hour).padStart(2, "0")}:00:00`;
    setRows((cur) => (cur || []).map((x) => x.id === id ? { ...x, scheduled_for: iso } : x)); // optimistic
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

  function load() { api.get<{ items: Appointment[] }>("/appointments").then((r) => setRows(r.items || [])).catch(() => setRows([])); }
  useEffect(() => { load(); api.get<any>("/staff/employees").then((r) => setEmps(r.items || r || [])).catch(loadFailed("the staff list")); api.get<{ items: Service[] }>("/catalog/services").then((r) => setServices(r.items || [])).catch(loadFailed("services")); }, []);

  async function create() {
    if (!f.scheduled_for) { flash("Pick a date & time"); return; }
    setBusy(true);
    try {
      await api.post("/appointments", { client_name: f.client_name || null, service_id: f.service_id || null, employee_id: f.employee_id || null, scheduled_for: f.scheduled_for, duration_min: Number(f.duration_min) || null, note: f.note || null });
      setDrawer(false); setF({ client_name: "", service_id: "", employee_id: "", scheduled_for: "", duration_min: 60, note: "" }); setBkDate(""); setSlots(null); load(); flash("Appointment created");
    } catch (e: any) { flash(`Failed: ${e.message}`); } finally { setBusy(false); }
  }

  const groups: Record<string, Appointment[]> = {};
  (rows || []).forEach((b) => { const k = dayKey(b.scheduled_for); (groups[k] ||= []).push(b); });
  const inp = "glass-input w-full py-2.5 px-3 text-sm font-semibold text-ink-primary rounded-lg";

  return (
    <Shell title="Appointments">
      <div className="flex-1 overflow-y-auto px-6 py-5 space-y-4">
        <div className="flex items-center gap-2">
          <button onClick={() => setDrawer(true)} className="h-[38px] px-4 rounded-xl bg-primary text-white text-xs font-bold shadow-clay-sm hover:brightness-110 flex items-center gap-1.5"><M n="add" c="text-sm" />New appointment</button>
          <div className="ml-auto flex items-center bg-slate-100 p-0.5 rounded-lg border border-slate-200/70 text-xs">
            <button onClick={() => setView("agenda")} className={`px-2.5 py-1 rounded-md font-semibold flex items-center gap-1 ${view === "agenda" ? "bg-white text-ink-primary shadow-xs" : "text-ink-tertiary hover:text-ink-primary"}`}><M n="table_rows" c="text-xs" />Agenda</button>
            <button onClick={() => setView("scheduler")} className={`px-2.5 py-1 rounded-md font-semibold flex items-center gap-1 ${view === "scheduler" ? "bg-white text-ink-primary shadow-xs" : "text-ink-tertiary hover:text-ink-primary"}`}><M n="schedule" c="text-xs" />Scheduler</button>
          </div>
        </div>
        {!rows && <p className="text-xs text-ink-tertiary">Loading…</p>}
        {view === "scheduler" && rows && (() => {
          const todayStr = new Date().toDateString();
          const today = rows.filter((b) => b.scheduled_for && new Date(b.scheduled_for).toDateString() === todayStr);
          const hours = Array.from({ length: 12 }, (_, i) => i + 8); // 08:00–19:00
          return (
            <div className="bg-white rounded-2xl border border-slate-200/90 shadow-clay-card overflow-hidden">
              <div className="px-5 py-3 border-b border-slate-100 text-xs font-bold text-ink-primary">Today — drag an appointment to reschedule</div>
              <div className="divide-y divide-slate-100">
                {hours.map((h) => {
                  const cards = today.filter((b) => new Date(b.scheduled_for!).getHours() === h);
                  return (
                    <div key={h} onDragOver={(e) => e.preventDefault()} onDrop={() => { if (drag) { moveToHour(drag, h); setDrag(null); } }} className="flex gap-3 px-5 py-2 min-h-[52px] hover:bg-blue-50/20">
                      <div className="w-14 flex-shrink-0 text-[11px] tabular-nums font-bold text-ink-tertiary pt-1.5">{String(h).padStart(2, "0")}:00</div>
                      <div className="flex-1 flex flex-wrap gap-2">
                        {cards.length === 0 && <div className="text-[11px] text-ink-placeholder pt-1.5">—</div>}
                        {cards.map((b) => (
                          <div key={b.id} draggable onDragStart={() => setDrag(b.id)} onDragEnd={() => setDrag(null)} {...clickable(() => { setSel(b); setReDate(""); setReTime(""); })} className={`bg-white rounded-xl border border-slate-200/80 shadow-clay-sm px-3 py-1.5 cursor-pointer hover:border-primary/40 ${drag === b.id ? "opacity-50" : ""}`}>
                            <div className="text-xs font-bold text-ink-primary">{b.service_name || "Appointment"} · {time(b.scheduled_for)}</div>
                            <div className="text-[11px] text-ink-tertiary">{b.client_name || "Walk-in"}{b.employee_name ? ` · ${b.employee_name}` : ""}</div>
                          </div>
                        ))}
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          );
        })()}
        {view === "agenda" && rows?.length === 0 && <div className="bg-white rounded-2xl border border-slate-200/90 shadow-clay-card p-12 text-center"><M n="calendar_month" c="mx-auto mb-2 text-3xl text-ink-placeholder" /><p className="text-sm font-bold text-ink-secondary mt-2">No appointments yet</p><p className="text-xs text-ink-tertiary">Create an appointment or wait for online requests.</p></div>}
        {view === "agenda" && Object.entries(groups).map(([day, list]) => (
          <div key={day}>
            <div className="text-[11px] font-bold text-ink-tertiary mb-2">{day}</div>
            <div className="bg-white rounded-2xl border border-slate-200/90 shadow-clay-card overflow-hidden divide-y divide-slate-100">
              {list.map((b) => {
                const st = (b.status || "booked").toLowerCase();
                return (
                  <div key={b.id} {...clickable(() => { setSel(b); setReDate(""); setReTime(""); })} className="px-5 py-3.5 flex items-center gap-4 hover:bg-blue-50/30 transition-colors cursor-pointer">
                    <div className="text-center flex-shrink-0 w-16"><div className="text-sm font-bold tabular-nums text-ink-primary">{time(b.scheduled_for)}</div>{b.duration_min && <div className="text-[11px] text-ink-tertiary">{b.duration_min}m</div>}</div>
                    <div className="w-px h-9 bg-slate-200" />
                    <div className="flex-1 min-w-0"><div className="text-sm font-bold text-ink-primary truncate">{b.service_name || "Appointment"}</div><div className="text-[11px] text-ink-tertiary">{b.client_name || "Walk-in"}{b.employee_name ? ` · ${b.employee_name}` : ""}</div></div>
                    {b.order_id && <span className="px-2 py-0.5 rounded-full text-[11px] font-bold bg-blue-50 text-primary border border-blue-100">Converted</span>}
                    <span className={`px-2.5 py-1 rounded-full text-[11px] font-semibold border ${STATUS[st] || STATUS.booked}`}>{STATUS_LABEL[st] || st}</span>
                  </div>
                );
              })}
            </div>
          </div>
        ))}
      </div>

      {drawer && (
        <div className="fixed inset-0 flex justify-end" style={{ zIndex: 50 }}>
          <div className="absolute inset-0 bg-ink-primary/35 backdrop-blur-[2px]" aria-hidden="true" onClick={() => setDrawer(false)} />
          <div className="relative h-full w-full max-w-[460px] bg-white shadow-clay-drawer flex flex-col border-l border-slate-200/90 animate-[slideIn_.3s_cubic-bezier(.16,1,.3,1)]">
            <style>{`@keyframes slideIn{from{transform:translateX(100%)}to{transform:translateX(0)}}`}</style>
            <div className="px-6 py-5 border-b border-slate-100 flex items-center justify-between bg-slate-50/70"><h3 className="text-base font-bold text-ink-primary">New appointment</h3><button aria-label="Close" onClick={() => setDrawer(false)} className="w-8 h-8 rounded-lg hover:bg-slate-200/60 flex items-center justify-center text-ink-tertiary"><M n="close" c="text-base" /></button></div>
            <div className="flex-1 overflow-y-auto p-6 space-y-4 text-xs">
              <div><label className="block font-semibold text-ink-secondary mb-1">Client</label><input value={f.client_name} onChange={(e) => setF({ ...f, client_name: e.target.value })} className={inp} placeholder="Client name" /></div>
              <div><label className="block font-semibold text-ink-secondary mb-1">Service</label><select value={f.service_id} onChange={(e) => { const s = services.find((x) => x.id === e.target.value); setF({ ...f, service_id: e.target.value, duration_min: s?.duration_min || f.duration_min }); }} className={`${inp} appearance-none`}><option value="">— Select —</option>{services.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}</select></div>
              <div><label className="block font-semibold text-ink-secondary mb-1">Specialist</label><select value={f.employee_id} onChange={(e) => setF({ ...f, employee_id: e.target.value })} className={`${inp} appearance-none`}><option value="">— Unassigned —</option>{emps.map((e) => <option key={e.id} value={e.id}>{e.name}</option>)}</select></div>
              <div className="grid grid-cols-2 gap-3">
                <div><label className="block font-semibold text-ink-secondary mb-1">Day</label><DateField mode="date" value={bkDate} onChange={setBkDate} /></div>
                <div><label className="block font-semibold text-ink-secondary mb-1">Duration (min)</label><input type="number" value={f.duration_min} onChange={(e) => setF({ ...f, duration_min: e.target.value })} className={`${inp} font-mono`} /></div>
              </div>
              {bkDate && (
                <div>
                  <label className="block font-semibold text-ink-secondary mb-1">Available slots</label>
                  {slots === null && <div className="text-ink-tertiary py-1">Loading slots…</div>}
                  {slots && slots.length > 0 && (
                    <div className="flex flex-wrap gap-1.5">
                      {slots.map((s) => {
                        const on = f.scheduled_for === s.start_iso;
                        return <button key={s.start_iso} onClick={() => setF({ ...f, scheduled_for: s.start_iso })}
                          className={`px-2.5 py-1.5 rounded-lg border text-[11px] font-semibold ${on ? "bg-primary text-white border-primary" : "bg-slate-50 text-ink-secondary border-slate-200/60 hover:bg-slate-100"}`}>{s.start}–{s.end}</button>;
                      })}
                    </div>
                  )}
                  {slots && slots.length === 0 && <div className="text-amber-700 py-1">{slotMsg}</div>}
                </div>
              )}
              <div><label className="block font-semibold text-ink-secondary mb-1">Exact time {f.scheduled_for ? "" : "(or pick a slot above)"}</label><DateField mode="datetime" value={f.scheduled_for ? f.scheduled_for.slice(0, 16) : ""} onChange={(v) => setF({ ...f, scheduled_for: v })} /></div>
              <div><label className="block font-semibold text-ink-secondary mb-1">Note</label><textarea value={f.note} onChange={(e) => setF({ ...f, note: e.target.value })} className={`${inp} h-20 resize-none`} placeholder="Optional…" /></div>
            </div>
            <div className="p-4 border-t border-slate-100 bg-slate-50/70 flex items-center justify-end gap-2"><button onClick={() => setDrawer(false)} className="px-4 py-2 rounded-lg border border-slate-200 text-ink-secondary hover:bg-white font-semibold text-xs">Cancel</button><button onClick={create} disabled={busy} className="px-5 py-2 rounded-lg bg-primary text-white font-bold text-xs shadow-clay-sm disabled:opacity-50">{busy ? "Creating…" : "Create appointment"}</button></div>
          </div>
        </div>
      )}
      {sel && (
        <div className="fixed inset-0 flex justify-end" style={{ zIndex: 55 }}>
          <div className="absolute inset-0 bg-ink-primary/35 backdrop-blur-[2px]" aria-hidden="true" onClick={() => setSel(null)} />
          <div className="relative h-full w-full max-w-[460px] bg-white shadow-clay-drawer flex flex-col border-l border-slate-200/90 animate-[slideIn_.3s_cubic-bezier(.16,1,.3,1)]">
            <div className="px-6 py-5 border-b border-slate-100 flex items-center justify-between bg-slate-50/70">
              <div><h3 className="text-base font-bold text-ink-primary">{sel.service_name || "Appointment"}</h3><div className="text-[11px] text-ink-tertiary">{dayKey(sel.scheduled_for)} · {time(sel.scheduled_for)}</div></div>
              <button aria-label="Close" onClick={() => setSel(null)} className="w-8 h-8 rounded-lg hover:bg-slate-200/60 flex items-center justify-center text-ink-tertiary"><M n="close" c="text-base" /></button>
            </div>
            <div className="flex-1 overflow-y-auto p-6 space-y-5 text-xs">
              <div className="grid grid-cols-2 gap-3">
                <F l="Client" v={sel.client_name || "Walk-in"} />
                <F l="Specialist" v={sel.employee_name || "Unassigned"} />
                <F l="Time" v={`${time(sel.scheduled_for)}${sel.ends_at ? `–${time(sel.ends_at)}` : ""}`} />
                <F l="Duration" v={sel.duration_min ? `${sel.duration_min} min` : "—"} />
              </div>
              {sel.note && <F l="Note" v={sel.note} />}

              <div>
                <div className="text-[11px] font-bold text-ink-tertiary mb-1.5">Status — changing it notifies the client</div>
                <div className="flex flex-wrap gap-1.5">
                  {["booked", "done", "canceled", "no_show"].map((s) => (
                    <button key={s} disabled={selBusy} onClick={() => setStatus(s)} className={`px-2.5 py-1.5 rounded-lg text-[11px] font-bold border transition-colors ${(sel.status || "booked") === s ? STATUS[s] : "bg-white text-ink-secondary border-slate-200 hover:bg-slate-50"}`}>{STATUS_LABEL[s]}</button>
                  ))}
                </div>
              </div>

              {sel.order_id ? (
                <div className="rounded-2xl bg-blue-50/60 border border-blue-100 p-3.5 text-[11px] font-semibold text-primary flex items-center gap-2"><M n="link" c="text-sm" />Converted — linked to a work order / estimate.</div>
              ) : (
                <div>
                  <div className="text-[11px] font-bold text-ink-tertiary mb-1.5">Convert to</div>
                  <div className="flex gap-2">
                    <button disabled={selBusy} onClick={() => convert("work_order")} className="flex-1 px-3 py-2 rounded-xl bg-primary text-white text-[11px] font-bold shadow-clay-sm hover:brightness-110 disabled:opacity-60 flex items-center justify-center gap-1"><M n="handyman" c="text-sm" />Work order</button>
                    <button disabled={selBusy} onClick={() => convert("estimate")} className="flex-1 px-3 py-2 rounded-xl bg-white text-primary border border-blue-100 text-[11px] font-bold hover:bg-blue-50 disabled:opacity-60 flex items-center justify-center gap-1"><M n="request_quote" c="text-sm" />Estimate</button>
                  </div>
                </div>
              )}

              <div>
                <div className="text-[11px] font-bold text-ink-tertiary mb-1.5">Reschedule</div>
                <div className="flex gap-2">
                  <DateField mode="date" value={reDate} onChange={setReDate} className="flex-1" />
                  <DateField mode="time" value={reTime} onChange={setReTime} className="w-28" />
                  <button disabled={selBusy} onClick={reschedule} className="px-3 py-2 rounded-lg bg-blue-50 text-primary border border-blue-100 text-[11px] font-bold hover:bg-blue-100 disabled:opacity-60">Move</button>
                </div>
              </div>
            </div>
          </div>
        </div>
      )}
      {toast && <div className="fixed bottom-6 left-1/2 -translate-x-1/2 bg-ink-primary text-white text-xs font-semibold px-4 py-2.5 rounded-xl shadow-clay-drawer z-[70]">{toast}</div>}
    </Shell>
  );
}
