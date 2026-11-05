// WS-C — "Missed calls become inquiries" (Settings → Integrations → Calls & telephony).
// On by default: a missed call opens an inquiry (repeat calls from the same number are added
// to it), creates a call-back task and alerts whoever is clocked in.
import { useEffect, useState } from "react";
import { api } from "../../api/client";
import Switch from "../../components/Switch";
import SaveFooter from "../../components/SaveFooter";
import Section from "../../components/Section";
import { Select } from "../../components/Field";
import { SettingRow, NumField } from "../../components/SettingRow";
import { loadFailed } from "../../lib/notice";
import { sectionFooter, rowField } from "./kit";

export type MissedSettings = { enabled: boolean; config: { merge_hours: number; sla_minutes: number; notify: boolean; fallback_employee_id: string | null } };
type Emp = { id: string; name: string };

const DEFAULTS: MissedSettings = { enabled: true, config: { merge_hours: 24, sla_minutes: 30, notify: true, fallback_employee_id: null } };
const tidy = (r: any): MissedSettings => ({ enabled: r?.enabled ?? DEFAULTS.enabled, config: { ...DEFAULTS.config, ...(r?.config || {}) } });
const TITLE = "Missed calls";
const ICON = "phone_missed";
const ABOUT = "A missed call opens an inquiry linked to the contact, adds a \"Call back\" task and alerts whoever is clocked in. If the same number calls again while the inquiry is open, the call is added to it instead of opening a new one.";

export default function MissedCallSettings() {
  const [s, setS] = useState<MissedSettings | null>(null);
  const [saved, setSaved] = useState<MissedSettings | null>(null);
  const [staff, setStaff] = useState<Emp[]>([]);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    api.get<MissedSettings>("/leads/missed-call-settings").then((r) => { const t = tidy(r); setS(t); setSaved(t); }).catch((e) => setMsg({ ok: false, text: e.message }));
    api.get<any>("/staff/employees").then((r) => setStaff((Array.isArray(r) ? r : r.items || []).map((e: any) => ({ id: e.id, name: e.name })))).catch(loadFailed("the staff list"));
  }, []);
  if (!s) return (
    <Section title={TITLE} icon={ICON} description={ABOUT}>
      {msg ? <p role="alert" className="text-xs text-rose-600">{msg.text}</p>
        : <div className="space-y-2" aria-busy="true">{[0, 1].map((i) => <div key={i} className="h-9 rounded-xl bg-slate-100 animate-pulse" />)}</div>}
    </Section>
  );
  const c = s.config;
  const dirty = JSON.stringify(s) !== JSON.stringify(saved);
  const set = (patch: Partial<MissedSettings["config"]>) => { setS({ ...s, config: { ...c, ...patch } }); setMsg(null); };
  async function save() {
    setBusy(true); setMsg(null);
    try {
      const r = tidy(await api.put<MissedSettings>("/leads/missed-call-settings", { enabled: s!.enabled, merge_hours: Number(c.merge_hours) || 0,
        sla_minutes: Number(c.sla_minutes) || 30, notify: c.notify, fallback_employee_id: c.fallback_employee_id || null }));
      setS(r); setSaved(r);
      setMsg({ ok: true, text: "Saved." });
    } catch (e: any) { setMsg({ ok: false, text: e.message }); }
    finally { setBusy(false); }
  }
  return (
    <Section title={TITLE} icon={ICON} description={ABOUT}>
      <div className="divide-y divide-slate-100">
        <SettingRow label="Missed calls become inquiries">
          <Switch label="Missed calls become inquiries" on={s.enabled} onChange={(v) => { setS({ ...s, enabled: v }); setMsg(null); }} />
        </SettingRow>
        <SettingRow htmlFor="mc-merge" dim={!s.enabled} label="Add repeat calls within" help="A call from the same number in this time joins the open inquiry.">
          <NumField id="mc-merge" disabled={!s.enabled} max={336} value={c.merge_hours} onChange={(v) => set({ merge_hours: v })} unit="hours" />
        </SettingRow>
        <SettingRow htmlFor="mc-sla" dim={!s.enabled} label="Call back within" help="When the call-back task is due.">
          <NumField id="mc-sla" disabled={!s.enabled} min={1} value={c.sla_minutes} onChange={(v) => set({ sla_minutes: v })} unit="min" />
        </SettingRow>
        <SettingRow dim={!s.enabled} label="Send an alert" help="Alert whoever is clocked in when a call is missed.">
          <Switch label="Send an alert" disabled={!s.enabled} on={c.notify} onChange={(v) => set({ notify: v })} />
        </SettingRow>
        <SettingRow htmlFor="mc-fallback" dim={!s.enabled} label="When nobody is clocked in, alert" stack>
          <Select id="mc-fallback" disabled={!s.enabled} className={rowField} value={c.fallback_employee_id || ""} onChange={(e) => set({ fallback_employee_id: e.target.value || null })}>
            <option value="">The contact's manager, else the admins</option>
            {staff.map((e) => <option key={e.id} value={e.id}>{e.name}</option>)}
          </Select>
        </SettingRow>
      </div>
      {msg && !msg.ok && <div role="alert" className="mt-3 rounded-xl border px-3 py-2 text-xs bg-rose-50 border-rose-200 text-rose-700">{msg.text}</div>}
      <SaveFooter className={sectionFooter} dirty={dirty} busy={busy} onSave={save} onDiscard={() => { setS(saved); setMsg(null); }}
        msg={msg && msg.ok ? msg : null} saveLabel="Save missed-call settings" />
    </Section>
  );
}
