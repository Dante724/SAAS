// WS-C — "Missed calls become inquiries" (Settings → Integrations → Calls & telephony).
// On by default: a missed call opens an inquiry (repeat calls from the same number are added
// to it), creates a call-back task and alerts whoever is clocked in.
import { useEffect, useState } from "react";
import { api } from "../../api/client";
import { btnPrimary, inputCls, labelCls } from "../invoices/billing";
import { loadFailed } from "../../lib/notice";

export type MissedSettings = { enabled: boolean; config: { merge_hours: number; sla_minutes: number; notify: boolean; fallback_employee_id: string | null } };
type Emp = { id: string; name: string };

const DEFAULTS: MissedSettings = { enabled: true, config: { merge_hours: 24, sla_minutes: 30, notify: true, fallback_employee_id: null } };
const tidy = (r: any): MissedSettings => ({ enabled: r?.enabled ?? DEFAULTS.enabled, config: { ...DEFAULTS.config, ...(r?.config || {}) } });
const hint = "text-xs text-ink-tertiary leading-relaxed";
const field = `${inputCls} text-ink-primary disabled:opacity-60`;

export default function MissedCallSettings() {
  const [s, setS] = useState<MissedSettings | null>(null);
  const [staff, setStaff] = useState<Emp[]>([]);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    api.get<MissedSettings>("/leads/missed-call-settings").then((r) => setS(tidy(r))).catch((e) => setMsg({ ok: false, text: e.message }));
    api.get<any>("/staff/employees").then((r) => setStaff((Array.isArray(r) ? r : r.items || []).map((e: any) => ({ id: e.id, name: e.name })))).catch(loadFailed("the staff list"));
  }, []);
  if (!s) return msg ? <div className="p-4"><p role="alert" className="text-xs text-rose-600">{msg.text}</p></div> : null;
  const c = s.config;
  const set = (patch: Partial<MissedSettings["config"]>) => setS({ ...s, config: { ...c, ...patch } });
  async function save() {
    setBusy(true); setMsg(null);
    try {
      setS(tidy(await api.put<MissedSettings>("/leads/missed-call-settings", { enabled: s!.enabled, merge_hours: Number(c.merge_hours) || 0,
        sla_minutes: Number(c.sla_minutes) || 30, notify: c.notify, fallback_employee_id: c.fallback_employee_id || null })));
      setMsg({ ok: true, text: "Saved." });
    } catch (e: any) { setMsg({ ok: false, text: e.message }); }
    finally { setBusy(false); }
  }
  return (
    <div className="p-4 space-y-3">
      <label className="flex items-center gap-2 text-sm font-semibold text-ink-primary">
        <input type="checkbox" className="accent-primary" checked={s.enabled} onChange={(e) => setS({ ...s, enabled: e.target.checked })} />Missed calls become inquiries
      </label>
      <p className={hint}>A missed call opens an inquiry linked to the contact, adds a "Call back" task and alerts whoever is clocked in. If the same number calls again while the inquiry is open, the call is added to it instead of opening a new one.</p>
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
        <div><label htmlFor="mc-merge" className={labelCls}>Add repeat calls within (hours)</label>
          <input id="mc-merge" type="number" min={0} max={336} disabled={!s.enabled} className={field} value={c.merge_hours} onChange={(e) => set({ merge_hours: e.target.value as any })} /></div>
        <div><label htmlFor="mc-sla" className={labelCls}>Call back within (minutes)</label>
          <input id="mc-sla" type="number" min={1} disabled={!s.enabled} className={field} value={c.sla_minutes} onChange={(e) => set({ sla_minutes: e.target.value as any })} /></div>
        <div><label htmlFor="mc-fallback" className={labelCls}>When nobody is clocked in, alert</label>
          <select id="mc-fallback" disabled={!s.enabled} className={field} value={c.fallback_employee_id || ""} onChange={(e) => set({ fallback_employee_id: e.target.value || null })}>
            <option value="">The contact's manager, else the admins</option>
            {staff.map((e) => <option key={e.id} value={e.id}>{e.name}</option>)}
          </select></div>
      </div>
      <label className="flex items-center gap-2 text-xs font-semibold text-ink-primary">
        <input type="checkbox" className="accent-primary" disabled={!s.enabled} checked={c.notify} onChange={(e) => set({ notify: e.target.checked })} />Send an alert
      </label>
      {msg && <div role={msg.ok ? "status" : "alert"} className={`rounded-lg border px-3 py-2 text-xs ${msg.ok ? "bg-emerald-50 border-emerald-200 text-emerald-800" : "bg-rose-50 border-rose-200 text-rose-700"}`}>{msg.text}</div>}
      <div><button className={btnPrimary} disabled={busy} onClick={save}>Save missed-call settings</button></div>
    </div>
  );
}
