import { useEffect, useMemo, useState, type ReactNode } from "react";
import { M } from "../../components/Shell";
import { api } from "../../api/client";

/* Settings › Notifications › Customers — one table: each moment in a job (received, every
   status, payment, update) × each channel (text, email). A switch turns a message on; clicking
   the row opens the message with a live phone / inbox preview. It reads and writes the same
   notification rules and templates the engine already uses (/notifications/rules, /templates). */

type Row = { id: string; label: string; sub?: string; color?: string; event: string; status: string | null; allStatuses?: boolean };
type Rule = any;

const SAMPLE: Record<string, string> = {
  "{Client First Name}": "Sam", "{Client Name}": "Sam Rivera", "{Client Last Name}": "Rivera",
  "{Order Number}": "WO-1042", "{Asset Brand}": "Apple", "{Asset Model}": "iPhone 14", "{Asset Type}": "Phone",
  "{Order Total}": "$229.00", "{Order Balance Due}": "$229.00", "{Balance Due}": "$229.00",
  "{Public Order URL}": "shop.link/t/8F2K", "{Order Payment URL}": "shop.link/p/8F2K", "{Order Scheduled Date}": "Oct 18, 10:00 AM",
  "{Today Date}": new Date().toLocaleDateString(), "{Location}": "Main Shop",
};
const CHIPS: [string, string][] = [["{Client First Name}", "First name"], ["{Order Number}", "Job number"], ["{Order Status}", "Status"],
  ["{Asset Model}", "Item"], ["{Order Total}", "Total"], ["{Public Order URL}", "Tracking link"], ["{Company Name}", "Shop name"]];

function fill(text: string, extra: Record<string, string>) {
  return (text || "").replace(/\{[^}]+\}/g, (m) => extra[m] ?? SAMPLE[m] ?? m);
}

// Mirrors the engine's built-in wording when a rule has no template (services/notification_rules._render).
function defaultBody(row: Row) {
  if (row.event === "created") return "We've received your order {Order Number}. We'll keep you posted.";
  if (row.event === "comment_added") return "There's a new update on your order {Order Number}.";
  if (row.event === "payment_received") return "Thanks! We've recorded your payment on order {Order Number}.";
  if (row.event === "closed" && !row.status) return "Your order {Order Number} is complete.";
  return "Your order {Order Number} is now: {Order Status}";
}
function suggestedBody(row: Row) {
  if (row.event === "created") return "Hi {Client First Name}, we've got your {Asset Model} (job {Order Number}). Track it here: {Public Order URL}";
  if (row.event === "payment_received") return "Thanks {Client First Name}! Payment received for job {Order Number}.";
  if (row.event === "comment_added") return "Hi {Client First Name}, there's an update on your {Asset Model}: {Public Order URL}";
  if (row.event === "closed") return "Hi {Client First Name}, your {Asset Model} is all done. Thanks for choosing {Company Name}!";
  return "Hi {Client First Name}, your {Asset Model} is now: {Order Status}. Track it here: {Public Order URL}";
}

export default function CustomerMessages({ rules, templates, statusesFor, shopName, reload, flash, renderOther }: {
  rules: Rule[]; templates: any[]; statusesFor: (rt: string) => Promise<any[]>; shopName: string;
  reload: () => void; flash: (m: string) => void; renderOther: (view: "internal" | "automations") => ReactNode;
}) {
  const [view, setView] = useState<"customers" | "internal" | "automations">("customers");
  const [rt, setRt] = useState<"work_order" | "estimate">("work_order");
  const [statuses, setStatuses] = useState<any[] | null>(null);
  const [providers, setProviders] = useState<Record<string, string>>({});
  const [editing, setEditing] = useState<Row | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  useEffect(() => { api.get<any>("/notifications/providers").then((r) => setProviders(r.channels || {})).catch(() => setProviders({})); }, []);
  useEffect(() => { setStatuses(null); statusesFor(rt).then(setStatuses).catch(() => setStatuses([])); }, [rt]);  // eslint-disable-line

  const channels: [string, string, string][] = [["sms", "Text", "smartphone"], ["email", "Email", "mail"],
    ...(providers.whatsapp === "live" ? [["whatsapp", "WhatsApp", "chat"] as [string, string, string]] : [])];
  const noun = rt === "estimate" ? "estimate" : "job";

  const rows: Row[] = useMemo(() => {
    const out: Row[] = [{ id: "created", label: rt === "estimate" ? "Estimate created" : "Job received", sub: "Right after it's checked in", event: "created", status: null }];
    out.push({ id: "any", label: "Every status change", sub: "One message for any status. Turn off to choose per status.", event: "status_changed", status: null, allStatuses: true });
    for (const s of statuses || []) {
      const term = !!s.is_terminal;
      out.push({ id: `s:${s.key}`, label: s.name, color: s.color, event: term ? "closed" : "status_changed", status: s.key, sub: term ? "Finishes the " + noun : undefined });
    }
    out.push({ id: "closed-any", label: `${rt === "estimate" ? "Estimate" : "Job"} finished`, sub: "Any closing status", event: "closed", status: null, allStatuses: true });
    out.push({ id: "comment", label: "You post an update", sub: "A comment the customer can see", event: "comment_added", status: null });
    out.push({ id: "paid", label: "Payment received", event: "payment_received", status: null });
    return out;
  }, [statuses, rt, noun]);

  const mine = (r: Rule) => (r.module || "order") === "order" && (r.scope || "client") === "client" && r.event !== "reminder"
    && (rt === "estimate" ? r.record_type === "estimate" : r.record_type !== "estimate");
  const cellRules = (row: Row, ch: string) => rules.filter((r) => mine(r) && r.event === row.event && r.channel === ch
    && (row.status ? r.trigger_status_key === row.status : !r.trigger_status_key));
  const isOn = (row: Row, ch: string) => cellRules(row, ch).some((r) => r.active);
  const covered = (row: Row, ch: string) => {
    if (!row.status) return false;
    const catchAll = rows.find((x) => x.allStatuses && x.event === row.event);
    return !!catchAll && isOn(catchAll, ch);
  };

  async function toggle(row: Row, ch: string) {
    const key = `${row.id}:${ch}`; if (busy) return; setBusy(key);
    try {
      const existing = cellRules(row, ch);
      if (isOn(row, ch)) {
        for (const r of existing.filter((x) => x.active)) await api.patch(`/notifications/rules/${r.id}`, { active: false });
      } else if (existing.length) {
        await api.patch(`/notifications/rules/${existing[0].id}`, { active: true });
      } else {
        await api.post("/notifications/rules", { module: "order", scope: "client", record_type: rt, event: row.event, trigger_status_key: row.status, channel: ch, active: true });
      }
      reload();
    } catch (e: any) { flash(e?.message || "Couldn't change that message"); } finally { setBusy(null); }
  }

  const reminderRules = rules.filter((r) => (r.module || "order") === "order" && (r.scope || "client") === "client" && r.event === "reminder");
  const liveNote = providers.sms && providers.sms !== "live" ? "Texts are in test mode until a phone number is connected" : null;

  const Seg = ({ value, set, opts }: { value: string; set: (v: any) => void; opts: [string, string][] }) => (
    <div className="inline-flex rounded-xl bg-slate-100 p-1">
      {opts.map(([v, l]) => (
        <button key={v} onClick={() => set(v)} aria-pressed={value === v}
          className={`px-3.5 py-1.5 rounded-lg text-xs font-semibold transition-colors ${value === v ? "bg-white text-ink-primary shadow-sm" : "text-ink-secondary hover:text-ink-primary"}`}>{l}</button>
      ))}
    </div>
  );

  return (
    <div className="max-w-3xl space-y-4">
      <div className="flex items-start gap-3">
        <div>
          <h2 className="text-base font-semibold text-ink-primary">Notifications</h2>
          <p className="text-xs text-ink-tertiary">The texts and emails your customers and team get automatically.</p>
        </div>
        <div className="ml-auto flex flex-wrap justify-end gap-1.5">
          {([["sms", "Text"], ["email", "Email"], ["whatsapp", "WhatsApp"]] as [string, string][]).filter(([k]) => providers[k]).map(([k, l]) => (
            <span key={k} title={providers[k] === "live" ? `${l} messages are sent` : `${l} is in test mode: messages are logged, not sent`}
              className={`inline-flex items-center gap-1.5 px-2 py-1 rounded-lg text-[11px] font-semibold ${providers[k] === "live" ? "bg-emerald-50 text-emerald-700" : "bg-amber-50 text-amber-700"}`}>
              <span className={`w-1.5 h-1.5 rounded-full ${providers[k] === "live" ? "bg-emerald-500" : "bg-amber-500"}`} />{l} {providers[k] === "live" ? "on" : "test mode"}
            </span>
          ))}
        </div>
      </div>

      <Seg value={view} set={setView} opts={[["customers", "Customers"], ["internal", "Your team"], ["automations", "Automations"]]} />

      {view !== "customers" ? renderOther(view) : (<>
        <div className="flex items-center gap-2">
          <Seg value={rt} set={setRt} opts={[["work_order", "Jobs"], ["estimate", "Estimates"]]} />
          {liveNote && <span className="text-[11px] text-amber-700 ml-auto">{liveNote}</span>}
        </div>

        <div className="bg-white rounded-2xl border border-slate-200/90 shadow-clay-card overflow-hidden">
          <div className="grid items-center px-4 py-2.5 border-b border-slate-100 text-[11px] font-semibold text-ink-tertiary" style={{ gridTemplateColumns: `minmax(0,1fr) repeat(${channels.length}, 72px) 28px` }}>
            <span>When</span>
            {channels.map(([k, l, icon]) => <span key={k} className="flex items-center justify-center gap-1"><M n={icon} c="text-[13px]" />{l}</span>)}
            <span />
          </div>
          {statuses === null && <div className="p-4 text-xs text-ink-tertiary">Loading…</div>}
          {rows.map((row, i) => {
            const firstStatus = row.status && !rows[i - 1]?.status;
            return (
              <div key={row.id}>
                {firstStatus && <div className="px-4 pt-3 pb-1 text-[11px] font-semibold text-ink-tertiary">Or pick statuses</div>}
                <div className="group grid items-center px-4 py-2.5 hover:bg-slate-50 cursor-pointer" style={{ gridTemplateColumns: `minmax(0,1fr) repeat(${channels.length}, 72px) 28px` }}
                  onClick={() => setEditing(row)}>
                  <span className="flex items-center gap-2.5 min-w-0">
                    {row.color ? <span className="w-2.5 h-2.5 rounded-full flex-shrink-0" style={{ background: row.color }} />
                      : <span className="w-2.5 h-2.5 flex-shrink-0" />}
                    <span className="min-w-0">
                      <span className={`block text-sm truncate ${row.allStatuses ? "font-medium text-ink-primary" : "text-ink-primary"}`}>{row.label}</span>
                      {row.sub && <span className="block text-[11px] text-ink-tertiary truncate">{row.sub}</span>}
                    </span>
                  </span>
                  {channels.map(([ch, l]) => {
                    const on = isOn(row, ch); const cov = !on && covered(row, ch);
                    return (
                      <span key={ch} className="flex justify-center" onClick={(e) => e.stopPropagation()}>
                        <button type="button" role="switch" aria-checked={on || cov} disabled={cov || busy === `${row.id}:${ch}`}
                          aria-label={`${l} when ${row.label}`} onClick={() => toggle(row, ch)}
                          title={cov ? `Already sent by "${rows.find((x) => x.allStatuses && x.event === row.event)?.label}"` : undefined}
                          className={`w-9 h-5 rounded-full relative transition-colors ${on ? "bg-primary" : cov ? "bg-primary/30 cursor-default" : "bg-slate-300"}`}>
                          <span className={`absolute top-0.5 w-4 h-4 rounded-full bg-white shadow transition-all ${on || cov ? "left-[18px]" : "left-0.5"}`} />
                        </button>
                      </span>
                    );
                  })}
                  <M n="chevron_right" c="text-base text-ink-tertiary justify-self-end" />
                </div>
              </div>
            );
          })}
        </div>

        <div className="bg-white rounded-2xl border border-slate-200/90 shadow-clay-card p-4">
          <div className="flex items-center gap-3">
            <span className="w-9 h-9 rounded-xl bg-primary/10 text-primary grid place-items-center flex-shrink-0"><M n="event_available" c="text-lg" /></span>
            <div className="min-w-0">
              <div className="text-sm text-ink-primary">Appointment reminders</div>
              <div className="text-[11px] text-ink-tertiary">{reminderRules.filter((r) => r.active).length
                ? reminderRules.filter((r) => r.active).map((r) => `${r.channel === "sms" ? "Text" : r.channel === "email" ? "Email" : r.channel} ${fmtOffset(r.reminder_offset_min)} before`).join(" · ")
                : "Off. Remind customers before their drop-off or visit."}</div>
            </div>
            <ReminderQuickAdd rules={reminderRules} onChanged={reload} flash={flash} />
          </div>
        </div>
      </>)}

      {editing && (
        <MessageEditor row={editing} rt={rt} channels={channels} rules={rules.filter(mine)} templates={templates} shopName={shopName}
          onClose={() => setEditing(null)} onSaved={() => { setEditing(null); reload(); flash("Message saved"); }} />
      )}
    </div>
  );
}

const fmtOffset = (m: number) => (!m ? "at the time" : m % 1440 === 0 ? `${m / 1440} day${m === 1440 ? "" : "s"}` : m % 60 === 0 ? `${m / 60} hour${m === 60 ? "" : "s"}` : `${m} min`);

function ReminderQuickAdd({ rules, onChanged, flash }: { rules: Rule[]; onChanged: () => void; flash: (m: string) => void }) {
  const [open, setOpen] = useState(false);
  const [ch, setCh] = useState("sms"); const [off, setOff] = useState(1440);
  async function add() {
    try { await api.post("/notifications/rules", { module: "order", scope: "client", event: "reminder", channel: ch, reminder_offset_min: off, active: true }); setOpen(false); onChanged(); }
    catch (e: any) { flash(e?.message || "Couldn't add the reminder"); }
  }
  async function del(r: Rule) { try { await api.del(`/notifications/rules/${r.id}`); onChanged(); } catch (e: any) { flash(e?.message || "Couldn't remove it"); } }
  return (
    <div className="ml-auto flex items-center gap-2 flex-wrap justify-end">
      {rules.map((r) => (
        <span key={r.id} className="inline-flex items-center gap-1 pl-2.5 pr-1 py-1 rounded-full bg-slate-100 text-[11px] text-ink-secondary">
          {r.channel === "sms" ? "Text" : "Email"} · {fmtOffset(r.reminder_offset_min)}
          <button aria-label="Remove reminder" onClick={() => del(r)} className="w-4 h-4 grid place-items-center rounded-full hover:bg-slate-200"><M n="close" c="text-[11px]" /></button>
        </span>
      ))}
      {open ? (
        <span className="flex items-center gap-1.5">
          <select value={ch} onChange={(e) => setCh(e.target.value)} aria-label="Send by" className="bg-white border border-slate-200 rounded-lg text-xs px-2 py-1.5"><option value="sms">Text</option><option value="email">Email</option></select>
          <select value={off} onChange={(e) => setOff(Number(e.target.value))} aria-label="How long before" className="bg-white border border-slate-200 rounded-lg text-xs px-2 py-1.5">
            {[[60, "1 hour"], [180, "3 hours"], [1440, "1 day"], [2880, "2 days"]].map(([v, l]) => <option key={v} value={v}>{l} before</option>)}
          </select>
          <button onClick={add} className="px-3 py-1.5 rounded-lg bg-ink-primary text-white text-xs font-semibold">Add</button>
        </span>
      ) : <button onClick={() => setOpen(true)} className="px-3 py-1.5 rounded-xl border border-slate-200 text-xs font-semibold text-ink-secondary hover:bg-slate-50">Add reminder</button>}
    </div>
  );
}

function MessageEditor({ row, rt, channels, rules, templates, shopName, onClose, onSaved }: {
  row: Row; rt: string; channels: [string, string, string][]; rules: Rule[]; templates: any[]; shopName: string; onClose: () => void; onSaved: () => void;
}) {
  const ruleFor = (ch: string) => rules.find((r) => r.event === row.event && r.channel === ch && (row.status ? r.trigger_status_key === row.status : !r.trigger_status_key) && r.active)
    || rules.find((r) => r.event === row.event && r.channel === ch && (row.status ? r.trigger_status_key === row.status : !r.trigger_status_key));
  const firstOn = channels.find(([c]) => ruleFor(c)?.active)?.[0] || "sms";
  const [ch, setCh] = useState(firstOn);
  const rule = ruleFor(ch);
  const tpl = rule?.template_id ? templates.find((t) => t.id === rule.template_id) : null;
  const [body, setBody] = useState<string>(tpl?.body_template || "");
  const [subject, setSubject] = useState<string>(tpl?.subject || "");
  const [on, setOn] = useState<boolean>(!!rule?.active);
  const [delay, setDelay] = useState<number>(rule?.delay_min || 0);
  const [hours, setHours] = useState<boolean>(!!rule?.working_hours_only);
  const [busy, setBusy] = useState(false); const [err, setErr] = useState<string | null>(null);
  const [ref, setRef] = useState<HTMLTextAreaElement | null>(null);

  useEffect(() => {   // switching channel loads that channel's message
    const r = ruleFor(ch); const t = r?.template_id ? templates.find((x) => x.id === r.template_id) : null;
    setBody(t?.body_template || ""); setSubject(t?.subject || ""); setOn(!!r?.active); setDelay(r?.delay_min || 0); setHours(!!r?.working_hours_only);
  }, [ch]);  // eslint-disable-line
  useEffect(() => {
    const k = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", k); return () => window.removeEventListener("keydown", k);
  }, [onClose]);

  const extra = { "{Order Status}": row.status ? row.label : "Ready for pickup", "{Company Name}": shopName || "Your shop" };
  const shown = body.trim() ? body : defaultBody(row);
  const preview = fill(shown, extra);
  const segments = Math.max(1, Math.ceil(preview.length / 160));

  function insert(token: string) {
    if (!ref) { setBody((b) => (b ? `${b} ${token}` : token)); return; }
    const s = ref.selectionStart ?? body.length, e = ref.selectionEnd ?? body.length;
    const next = body.slice(0, s) + token + body.slice(e);
    setBody(next);
    requestAnimationFrame(() => { ref.focus(); ref.setSelectionRange(s + token.length, s + token.length); });
  }

  async function save() {
    setBusy(true); setErr(null);
    try {
      let templateId: string | null = rule?.template_id || null;
      if (body.trim()) {
        const tb = { name: `${row.label} · ${ch === "sms" ? "Text" : ch === "email" ? "Email" : "WhatsApp"}`, channel: ch, trigger_event: "status_change",
          trigger_status: row.status, subject: ch === "email" ? (subject.trim() || null) : null, body_template: body, active: true };
        if (templateId && templates.some((t) => t.id === templateId)) await api.put(`/notifications/templates/${templateId}`, tb);
        else templateId = (await api.post<any>("/notifications/templates", tb)).id;
      }
      const rb = { template_id: body.trim() ? templateId : null, delay_min: delay, working_hours_only: hours, active: on };
      if (rule) await api.patch(`/notifications/rules/${rule.id}`, rb);
      else if (on) await api.post("/notifications/rules", { module: "order", scope: "client", record_type: rt, event: row.event, trigger_status_key: row.status, channel: ch, ...rb });
      onSaved();
    } catch (e: any) { setErr(e?.message || "Couldn't save the message"); setBusy(false); }
  }

  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      <div className="absolute inset-0 bg-ink-primary/30 backdrop-blur-[2px]" onClick={onClose} />
      <div role="dialog" aria-label={`Message for ${row.label}`} className="relative w-[640px] max-w-full bg-white h-full shadow-clay-drawer flex flex-col border-l border-slate-200/90 animate-[slideIn_.28s_cubic-bezier(.16,1,.3,1)]">
        <style>{`@keyframes slideIn{from{transform:translateX(100%)}to{transform:translateX(0)}}`}</style>
        <div className="px-5 pt-5 pb-3">
          <div className="flex items-center gap-3">
            {row.color ? <span className="w-3.5 h-3.5 rounded-full flex-shrink-0" style={{ background: row.color, boxShadow: `0 0 0 3px #fff, 0 0 0 5px ${row.color}55` }} />
              : <span className="w-9 h-9 rounded-xl bg-primary/10 text-primary grid place-items-center flex-shrink-0"><M n="send" c="text-lg" /></span>}
            <div className="flex-1 min-w-0">
              <div className="text-lg font-semibold text-ink-primary truncate">{row.status ? `When a ${rt === "estimate" ? "estimate" : "job"} becomes ${row.label}` : row.label}</div>
              <div className="text-[11px] text-ink-tertiary">{row.sub || "Sent to the customer automatically"}</div>
            </div>
            <button aria-label="Close" onClick={onClose} className="w-8 h-8 rounded-lg hover:bg-slate-100 grid place-items-center text-ink-tertiary flex-shrink-0"><M n="close" c="text-base" /></button>
          </div>
          <div className="mt-4 flex items-center gap-3">
            <div className="inline-flex rounded-xl bg-slate-100 p-1">
              {channels.map(([k, l, icon]) => (
                <button key={k} onClick={() => setCh(k)} aria-pressed={ch === k}
                  className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold transition-colors ${ch === k ? "bg-white text-ink-primary shadow-sm" : "text-ink-secondary hover:text-ink-primary"}`}>
                  <M n={icon} c="text-[13px]" />{l}{ruleFor(k)?.active && <span className="w-1.5 h-1.5 rounded-full bg-emerald-500" />}
                </button>
              ))}
            </div>
            <label className="ml-auto flex items-center gap-2 text-xs text-ink-secondary cursor-pointer">
              {on ? "On" : "Off"}
              <button type="button" role="switch" aria-checked={on} aria-label="Send this message" onClick={() => setOn(!on)}
                className={`w-10 h-6 rounded-full relative transition-colors ${on ? "bg-primary" : "bg-slate-300"}`}><span className={`absolute top-0.5 w-5 h-5 rounded-full bg-white shadow transition-all ${on ? "left-[18px]" : "left-0.5"}`} /></button>
            </label>
          </div>
        </div>

        <div className="flex-1 overflow-y-auto px-5 pb-5">
          <div className="grid md:grid-cols-[minmax(0,1fr)_236px] gap-5">
            <div className="space-y-3 min-w-0">
              {ch === "email" && (
                <label className="block">
                  <span className="block text-xs font-medium text-ink-secondary mb-1.5">Subject</span>
                  <input value={subject} onChange={(e) => setSubject(e.target.value)} placeholder="Update on your repair" className="w-full bg-white border border-slate-200 rounded-xl px-3 py-2.5 text-sm outline-none focus:border-primary" />
                </label>
              )}
              <label className="block">
                <span className="flex items-center text-xs font-medium text-ink-secondary mb-1.5">Message
                  {!body.trim() && <span className="ml-2 text-[11px] font-normal text-ink-tertiary">Using the standard wording</span>}
                </span>
                <textarea ref={setRef} value={body} onChange={(e) => setBody(e.target.value)} rows={6} placeholder={defaultBody(row)}
                  className="w-full bg-white border border-slate-200 rounded-xl px-3 py-2.5 text-sm outline-none focus:border-primary resize-y leading-relaxed" />
              </label>
              <div className="flex flex-wrap gap-1.5">
                {CHIPS.map(([t, l]) => (
                  <button key={t} type="button" onClick={() => insert(t)} className="inline-flex items-center gap-1 px-2.5 py-1 rounded-full border border-slate-200 text-[11px] text-ink-secondary hover:border-primary/40 hover:text-primary"><M n="add" c="text-[11px]" />{l}</button>
                ))}
              </div>
              {!body.trim() && <button type="button" onClick={() => setBody(suggestedBody(row))} className="text-xs font-semibold text-primary hover:underline">Start from a friendlier message</button>}
              {ch === "sms" && <div className="text-[11px] text-ink-tertiary">{preview.length} characters · {segments} text{segments === 1 ? "" : "s"} per send{segments > 1 ? " (each costs as one message)" : ""}</div>}

              <div className="pt-3 border-t border-slate-100 space-y-2.5">
                <div className="text-xs font-medium text-ink-secondary">When to send</div>
                <div className="inline-flex rounded-xl bg-slate-100 p-1">
                  {([[0, "Right away"], [15, "After 15 min"], [60, "After 1 hour"]] as [number, string][]).map(([v, l]) => (
                    <button key={v} type="button" onClick={() => setDelay(v)} aria-pressed={delay === v}
                      className={`px-3 py-1.5 rounded-lg text-xs font-semibold ${delay === v ? "bg-white text-ink-primary shadow-sm" : "text-ink-secondary"}`}>{l}</button>
                  ))}
                  {![0, 15, 60].includes(delay) && <span className="px-3 py-1.5 rounded-lg text-xs font-semibold bg-white text-ink-primary shadow-sm">After {delay} min</span>}
                </div>
                <label className="flex items-center gap-2.5 text-sm text-ink-primary cursor-pointer">
                  <button type="button" role="switch" aria-checked={hours} aria-label="Only during opening hours" onClick={() => setHours(!hours)}
                    className={`w-9 h-5 rounded-full relative transition-colors ${hours ? "bg-primary" : "bg-slate-300"}`}><span className={`absolute top-0.5 w-4 h-4 rounded-full bg-white shadow transition-all ${hours ? "left-[18px]" : "left-0.5"}`} /></button>
                  Only during opening hours
                  <span className="text-[11px] text-ink-tertiary">Late changes wait until you open</span>
                </label>
              </div>
            </div>

            <div>
              <div className="text-[11px] font-semibold text-ink-tertiary mb-2">Preview</div>
              {ch === "email" ? (
                <div className="rounded-2xl border border-slate-200 overflow-hidden bg-white">
                  <div className="px-3 py-2 border-b border-slate-100 bg-slate-50">
                    <div className="text-[11px] text-ink-tertiary">From {shopName || "Your shop"}</div>
                    <div className="text-xs font-semibold text-ink-primary truncate">{fill(subject.trim() || (row.status ? "Update on {Order Number}" : "Your order {Order Number}"), extra)}</div>
                  </div>
                  <div className="p-3 text-[12px] text-ink-primary leading-relaxed whitespace-pre-wrap break-words">{preview}</div>
                </div>
              ) : (
                <div className="rounded-[28px] border-[6px] border-slate-800 bg-slate-50 overflow-hidden mx-auto w-[220px]">
                  <div className="bg-white px-3 py-2 text-center border-b border-slate-100">
                    <div className="w-7 h-7 rounded-full bg-slate-200 mx-auto grid place-items-center text-[10px] font-semibold text-ink-secondary">{(shopName || "S").slice(0, 1).toUpperCase()}</div>
                    <div className="text-[10px] text-ink-secondary mt-0.5 truncate">{shopName || "Your shop"}</div>
                  </div>
                  <div className="p-2.5 min-h-[220px] flex flex-col justify-end">
                    <div className="self-start max-w-[92%] bg-white border border-slate-200 rounded-2xl rounded-bl-md px-2.5 py-1.5 text-[11.5px] text-ink-primary leading-snug whitespace-pre-wrap break-words shadow-sm">{preview}</div>
                  </div>
                </div>
              )}
              <p className="text-[10px] text-ink-tertiary mt-2 text-center">Sample customer and job. Real details are filled in when it sends.</p>
            </div>
          </div>
          {err && <div className="mt-4 rounded-lg bg-rose-50 border border-rose-200 text-rose-700 text-xs px-3 py-2">{err}</div>}
        </div>

        <div className="px-5 py-4 border-t border-slate-100 flex items-center gap-2">
          {body.trim() && <button onClick={() => setBody("")} className="px-3 py-2 rounded-xl text-xs font-semibold text-ink-secondary hover:bg-slate-100">Use standard wording</button>}
          <button onClick={onClose} className="ml-auto px-4 py-2 rounded-xl border border-slate-200 text-ink-secondary hover:bg-slate-50 font-semibold text-xs">Cancel</button>
          <button onClick={save} disabled={busy} className="px-5 py-2 rounded-xl bg-ink-primary text-white font-semibold text-xs hover:opacity-90 disabled:opacity-60">{busy ? "Saving…" : "Save"}</button>
        </div>
      </div>
    </div>
  );
}
