import { humanize } from "../../lib/humanize";
import { useEffect, useState, useCallback } from "react";
import Shell, { M } from "../../components/Shell";
import { api } from "../../api/client";
import { promptDialog } from "../../lib/dialog";
import BayChecklist from "./BayChecklist";

// 144az — the technician's own shop-floor view: live job timer, today/week clocked hours,
// my assigned open service lines (booked vs actual + one-tap clock-in), and my recent time
// entries with a dispute flag. Backend: /timeclock/my-work, /start, /stop, /entries,
// /entries/{id}/dispute, /entries/{id}/audit.
type MyLine = { order_id: string; order_number?: string; line_id: string; description?: string; booked_hours?: number; actual_hours?: number; running?: boolean };
type BayOrder = {
  order_id: string; order_number?: string;
  status: string; status_label?: string; status_color?: string | null;
  next_status?: string | null; next_label?: string | null;
  checklist_done: number; checklist_total: number;
  client?: string | null; device?: string | null; lines: MyLine[];
};
type Active = { id: string; order_number?: string; order_line_id?: string | null; duration_sec?: number } | null;
type Work = { today_seconds: number; week_seconds: number; active: Active; lines: MyLine[]; orders: BayOrder[] };
type Entry = { id: string; order_number?: string; line_description?: string; category?: string; duration_min?: number; started_at?: string; state?: string; disputed?: boolean; dispute_note?: string };

const hm = (s: number) => { const m = Math.floor((s || 0) / 60); return `${Math.floor(m / 60)}h ${m % 60}m`; };
const hmMin = (min?: number) => { const m = Math.round(min || 0); return `${Math.floor(m / 60)}h ${m % 60}m`; };

export default function MyWorkHub() {
  const [w, setW] = useState<Work | null>(null);
  const [entries, setEntries] = useState<Entry[]>([]);
  const [flash, setFlash] = useState("");
  const [auditFor, setAuditFor] = useState<string | null>(null);
  const [audit, setAudit] = useState<any[]>([]);
  const [openChecklist, setOpenChecklist] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [mw, es] = await Promise.all([
        api.get<Work>("/timeclock/my-work"),
        api.get<any>("/timeclock/entries?limit=20"),
      ]);
      setW(mw); setEntries((es.items || es || []).slice(0, 20));
    } catch (e: any) { setFlash(e.message || "Could not load"); }
  }, []);
  useEffect(() => { load(); }, [load]);

  async function clockIn(l: MyLine) {
    try { await api.post(`/timeclock/start?switch=true`, { order_id: l.order_id, order_line_id: l.line_id, category: "job" }); setFlash(`Clocked into ${l.description || "line"}`); load(); }
    catch (e: any) { setFlash(e.message || "Could not start"); }
  }
  async function stop() {
    try { await api.post(`/timeclock/stop`, {}); setFlash("Timer stopped"); load(); }
    catch (e: any) { setFlash(e.message || "Could not stop"); }
  }
  async function advance(o: BayOrder) {
    if (!o.next_status) return;
    try { await api.patch(`/orders/${o.order_id}/status`, { status: o.next_status }); setFlash(`${o.order_number} → ${o.next_label}`); load(); }
    catch (e: any) { setFlash(e.message || "Could not advance"); }  // surfaces 409 gate messages (checklist/comment/payment)
  }
  async function dispute(e: Entry) {
    const note = await promptDialog("What's wrong with these hours?", { defaultValue: e.dispute_note || "" });
    if (note === null) return;
    try { await api.post(`/timeclock/entries/${e.id}/dispute`, { note }); setFlash("Flagged for your manager"); load(); }
    catch (err: any) { setFlash(err.message || "Could not flag"); }
  }
  async function showAudit(id: string) {
    if (auditFor === id) { setAuditFor(null); return; }
    try { const r = await api.get<any>(`/timeclock/entries/${id}/audit`); setAudit(r.items || []); setAuditFor(id); }
    catch (e: any) { setFlash(e.message || "Could not load history"); }
  }

  const card = "bg-white rounded-2xl border border-slate-200/90 shadow-sm";
  return (
    <Shell title="My work">
      <div className="flex-1 overflow-y-auto p-6">
      <div className="max-w-4xl mx-auto space-y-5">
        {flash && <div className="text-xs font-semibold text-emerald-700 bg-emerald-50 border border-emerald-200 rounded-lg px-3 py-2">{flash}</div>}

        {/* live timer + totals */}
        <div className="grid grid-cols-3 gap-3">
          <div className={`${card} p-4`}>
            <div className="text-[11px] font-bold text-ink-tertiary">On the clock</div>
            {w?.active ? (
              <div className="mt-1 flex items-center justify-between">
                <span className="flex items-center gap-1.5 text-sm font-bold text-emerald-700"><span className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse" />{w.active.order_number || "running"}</span>
                <button onClick={stop} className="px-2.5 py-1 rounded-lg text-[11px] font-bold text-white bg-emerald-600 hover:bg-emerald-700">Stop</button>
              </div>
            ) : <div className="mt-1 text-sm text-ink-tertiary">Not clocked in</div>}
          </div>
          <div className={`${card} p-4`}><div className="text-[11px] font-bold text-ink-tertiary">Today</div><div className="mt-1 text-xl font-bold tabular-nums text-ink-primary">{hm(w?.today_seconds || 0)}</div></div>
          <div className={`${card} p-4`}><div className="text-[11px] font-bold text-ink-tertiary">This week</div><div className="mt-1 text-xl font-bold tabular-nums text-ink-primary">{hm(w?.week_seconds || 0)}</div></div>
        </div>

        {/* my bay — one card per job: status + one-tap advance + checklist + clock-in */}
        <div className={card}>
          <div className="px-4 py-2.5 border-b border-slate-200 flex items-center gap-2"><M n="engineering" c="text-primary text-sm" /><h3 className="text-xs font-bold text-ink-primary">My bay</h3><span className="ml-auto text-[11px] text-ink-tertiary font-semibold">{(w?.orders || []).length} job{(w?.orders || []).length === 1 ? "" : "s"}</span></div>
          {(w?.orders || []).length === 0 ? (
            <p className="p-4 text-xs text-ink-tertiary">Nothing assigned to you on open work orders.</p>
          ) : (
            <div className="divide-y divide-slate-100">
              {w!.orders.map((o) => {
                const pct = o.checklist_total > 0 ? Math.round((o.checklist_done / o.checklist_total) * 100) : 0;
                return (
                  <div key={o.order_id} className="p-3.5 space-y-2.5">
                    <div className="flex items-center justify-between gap-2">
                      <div className="min-w-0">
                        <div className="flex items-center gap-2">
                          <span className="text-xs font-bold text-ink-primary font-mono">{o.order_number}</span>
                          <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded-md text-[11px] font-bold text-white" style={{ backgroundColor: o.status_color || "#64748b" }}>{o.status_label}</span>
                        </div>
                        <div className="text-[11px] text-ink-tertiary truncate">{[o.client, o.device].filter(Boolean).join(" · ") || "—"}</div>
                      </div>
                      {o.next_status
                        ? <button onClick={() => advance(o)} className="flex-none flex items-center gap-1 px-2.5 py-1.5 rounded-lg text-[11px] font-bold text-white bg-primary hover:opacity-90 shadow-clay-sm"><M n="arrow_forward" c="text-[13px]" />{o.next_label}</button>
                        : <span className="flex-none text-[11px] font-semibold text-ink-tertiary ">Final</span>}
                    </div>

                    {o.checklist_total > 0 && (
                      <>
                        <button onClick={() => setOpenChecklist(openChecklist === o.order_id ? null : o.order_id)}
                          className="w-full flex items-center gap-2 group" aria-expanded={openChecklist === o.order_id}>
                          <div className="flex-1 h-1.5 rounded-full bg-slate-100 overflow-hidden"><div className="h-full rounded-full bg-emerald-500" style={{ width: `${pct}%` }} /></div>
                          <span className="text-[11px] font-semibold text-ink-tertiary tabular-nums flex-none group-hover:text-primary">{o.checklist_done}/{o.checklist_total} checks</span>
                          <M n={openChecklist === o.order_id ? "expand_less" : "expand_more"} c="text-[14px] text-ink-tertiary group-hover:text-primary" />
                        </button>
                        {openChecklist === o.order_id && (
                          <div className="rounded-xl bg-slate-50/70 border border-slate-200/70 p-2.5">
                            <BayChecklist orderId={o.order_id} onChange={load} />
                          </div>
                        )}
                      </>
                    )}

                    <div className="space-y-1">
                      {o.lines.map((l) => {
                        const eff = (l.booked_hours || 0) > 0 && (l.actual_hours || 0) > 0 ? Math.round((l.booked_hours! / l.actual_hours!) * 100) : null;
                        return (
                          <div key={l.line_id} className="flex items-center justify-between gap-2 rounded-lg bg-slate-50/60 px-2.5 py-1.5">
                            <div className="min-w-0">
                              <div className="text-[11px] font-semibold text-ink-primary truncate">{l.description || "Service"}</div>
                              <div className="text-[11px] text-ink-tertiary tabular-nums">booked {l.booked_hours || 0}h · actual {l.actual_hours || 0}h{eff !== null ? ` · ${eff}%` : ""}</div>
                            </div>
                            {l.running
                              ? <span className="flex-none flex items-center gap-1 text-[11px] font-semibold text-emerald-700"><span className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse" />running</span>
                              : <button onClick={() => clockIn(l)} className="flex-none flex items-center gap-1 px-2 py-0.5 rounded-md text-[11px] font-semibold text-emerald-700 border border-emerald-200 hover:bg-emerald-50"><M n="play_arrow" c="text-[12px]" />clock in</button>}
                          </div>
                        );
                      })}
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>

        {/* my recent entries + dispute */}
        <div className={card}>
          <div className="px-4 py-2.5 border-b border-slate-200 flex items-center gap-2"><M n="history" c="text-primary text-sm" /><h3 className="text-xs font-bold text-ink-primary">My recent time</h3></div>
          {entries.length === 0 ? <p className="p-4 text-xs text-ink-tertiary">No time logged yet.</p> : (
            <div className="divide-y divide-slate-100">
              {entries.map((e) => (
                <div key={e.id} className="p-3.5">
                  <div className="flex items-center justify-between">
                    <div className="min-w-0">
                      <div className="text-xs font-semibold text-ink-primary truncate">{e.order_number || e.category || "time"}{e.line_description ? ` · ${e.line_description}` : ""}</div>
                      <div className="text-[11px] text-ink-tertiary tabular-nums">{hmMin(e.duration_min)} · {humanize(e.state)}{e.disputed ? " · Disputed" : ""}</div>
                    </div>
                    <div className="flex items-center gap-2 flex-none">
                      <button onClick={() => showAudit(e.id)} className="text-[11px] font-semibold text-ink-secondary hover:text-primary">History</button>
                      {!e.disputed && e.state !== "void" && <button onClick={() => dispute(e)} className="text-[11px] font-semibold text-amber-700 hover:text-amber-800">Dispute</button>}
                    </div>
                  </div>
                  {auditFor === e.id && (
                    <div className="mt-2 ml-1 border-l-2 border-slate-200 pl-3 space-y-1">
                      {audit.length === 0 ? <div className="text-[11px] text-ink-tertiary">No history.</div> : audit.map((a, i) => (
                        <div key={i} className="text-[11px] text-ink-tertiary tabular-nums">
                          <b className="text-ink-secondary">{a.action}</b>{a.field ? ` ${a.field}` : ""}{a.old != null || a.new != null ? `: ${a.old ?? "—"} → ${a.new ?? "—"}` : ""} · {a.actor} · {(a.at || "").slice(0, 16).replace("T", " ")}
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
      </div>
    </Shell>
  );
}
