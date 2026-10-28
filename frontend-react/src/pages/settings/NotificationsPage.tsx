import { useEffect, useMemo, useState, type ReactNode } from "react";
import { M } from "../../components/Shell";
import { api } from "../../api/client";
import { selectDialog } from "../../lib/dialog";
import { cellRules, inView, isCustomerRule, toggleCell } from "../../lib/notifRules";
import { serverDate } from "../../lib/serverTime";

/* Settings › Notifications › Customers — "what your customer gets": a journey strip (comes in,
   in the shop, ready and done, updates and payment), then one card per moment. Each message
   shows its wording, Text / Email pills (filled = on), and how many went out this week. A
   status is listed only once it has its own message. Clicking a row opens the message with a
   live phone / inbox preview and "Send me a sample". It reads and writes the same
   notification rules and templates the engine already uses (/notifications/rules, /templates). */

type Row = { id: string; label: string; sub?: string; color?: string; event: string; status: string | null; allStatuses?: boolean };
type Rule = any;

const SAMPLE: Record<string, string> = {
  "{Client First Name}": "Sam", "{Client Name}": "Sam Rivera", "{Client Last Name}": "Rivera",
  "{Order Number}": "WO-1042", "{Asset Brand}": "Apple", "{Asset Model}": "iPhone 14", "{Asset Type}": "Phone",
  "{Order Total}": "$229.00", "{Order Balance Due}": "$229.00", "{Balance Due}": "$229.00",
  "{Public Order URL}": "shop.link/t/8F2K", "{Order Payment URL}": "shop.link/p/8F2K", "{Order Scheduled Date}": "Oct 18, 10:00 AM",
  "{Today Date}": new Date().toLocaleDateString(), "{Location}": "Main Shop",
  // Short message variables (services/message_vars.py on the server).
  "{client_name}": "Sam Rivera", "{client_first_name}": "Sam", "{client_last_name}": "Rivera", "{client_phone}": "(555) 010-0123",
  "{client_birthday}": "14 March", "{client_age}": "36", "{order_number}": "WO-1042", "{device}": "Apple iPhone 14",
  "{technician}": "Alex", "{scheduled_for}": "Fri 18 Oct, 10:00", "{total}": "$229.00", "{balance_due}": "$129.00",
  "{portal_link}": "shop.link/portal/8F2K",
};
export const CHIPS: [string, string][] = [["{Client First Name}", "First name"], ["{client_last_name}", "Last name"], ["{client_phone}", "Phone"],
  ["{Order Number}", "Job number"], ["{Order Status}", "Status"], ["{Asset Model}", "Item"], ["{Order Total}", "Total"],
  ["{balance_due}", "Balance due"], ["{scheduled_for}", "Scheduled for"], ["{Public Order URL}", "Tracking link"],
  ["{portal_link}", "Customer portal"], ["{client_birthday}", "Birthday"], ["{client_age}", "Age"], ["{Company Name}", "Shop name"]];

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

type Stat = { sent_7d: number; failed_7d: number; skipped_7d: number; last_sent_at: string | null };
type Moment = "in" | "shop" | "done" | "after";
const MOMENTS: { key: Moment; label: string; icon: string; hint: string }[] = [
  { key: "in", label: "Comes in", icon: "login", hint: "When you check it in" },
  { key: "shop", label: "In the shop", icon: "build", hint: "While you work on it" },
  { key: "done", label: "Ready and done", icon: "task_alt", hint: "When it finishes" },
  { key: "after", label: "Updates and payment", icon: "payments", hint: "Anything else along the way" },
];

function ago(iso: string | null) {
  if (!iso) return "";
  const min = Math.max(0, Math.round((Date.now() - (serverDate(iso)?.getTime() ?? Date.now())) / 60000));
  if (min < 60) return min <= 1 ? "just now" : `${min} min ago`;
  if (min < 1440) return `${Math.round(min / 60)} h ago`;
  return `${Math.round(min / 1440)} d ago`;
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
  const [adding, setAdding] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [stats, setStats] = useState<{ rules: Record<string, Stat>; texting: { with_phone: number; opted_out: number } } | null>(null);
  const [quiet, setQuiet] = useState<{ on: boolean; some: boolean } | null>(null);
  const [peek, setPeek] = useState<Moment | null>(null);
  useEffect(() => { api.get<any>("/notifications/providers").then((r) => setProviders(r.channels || {})).catch(() => setProviders({})); }, []);
  useEffect(() => { setStatuses(null); statusesFor(rt).then(setStatuses).catch(() => setStatuses([])); }, [rt]);  // eslint-disable-line
  useEffect(() => {
    api.get<any>("/notifications/rules/stats").then(setStats).catch(() => setStats(null));
    api.get<any>("/notifications/quiet-hours").then(setQuiet).catch(() => setQuiet(null));
  }, [rules]);

  const channels: [string, string, string][] = [["sms", "Text", "smartphone"], ["email", "Email", "mail"],
    ...(providers.whatsapp === "live" ? [["whatsapp", "WhatsApp", "chat"] as [string, string, string]] : [])];
  const noun = rt === "estimate" ? "estimate" : "job";
  const Noun = rt === "estimate" ? "Estimate" : "Job";

  const isOn = (row: Row, ch: string) => cellRules(rules, rt, row.event, row.status, ch).some((r) => r.active);
  const anyOn = (row: Row) => channels.some(([c]) => isOn(row, c));

  // Every possible row; per-status rows are listed only when they have a message of their own.
  const all: (Row & { moment: Moment })[] = useMemo(() => {
    const out: (Row & { moment: Moment })[] = [
      { id: "created", moment: "in", label: rt === "estimate" ? "Estimate created" : "Job received", sub: "Right after it's checked in", event: "created", status: null },
      { id: "any", moment: "shop", label: "Every status change", event: "status_changed", status: null, allStatuses: true },
    ];
    for (const s of statuses || []) {
      const term = !!s.is_terminal;
      out.push({ id: `s:${s.key}`, moment: term ? "done" : "shop", label: s.name, color: s.color, event: term ? "closed" : "status_changed", status: s.key, sub: term ? `Finishes the ${noun}` : undefined });
    }
    out.push({ id: "closed-any", moment: "done", label: `${Noun} finished`, sub: "Any closing status", event: "closed", status: null, allStatuses: true });
    out.push({ id: "comment", moment: "after", label: "You post an update", sub: "A comment the customer can see", event: "comment_added", status: null });
    out.push({ id: "paid", moment: "after", label: "Payment received", event: "payment_received", status: null });
    return out;
  }, [statuses, rt, noun, Noun]);  // eslint-disable-line
  const listed = all.filter((r) => !r.status || anyOn(r));
  const unlisted = all.filter((r) => r.status && !anyOn(r));
  const catchAllFor = (row: Row) => (row.status ? all.find((x) => x.allStatuses && x.event === row.event) : undefined);

  async function toggle(row: Row, ch: string) {
    const key = `${row.id}:${ch}`; if (busy) return; setBusy(key);
    try { await toggleCell(api, rules, rt, row.event, row.status, ch); reload(); }
    catch (e: any) { flash(e?.message || "Couldn't change that message"); } finally { setBusy(null); }
  }
  async function addForStatus(m: Moment) {
    const choices = unlisted.filter((r) => r.moment === m);
    const pick = await selectDialog("Which status should send its own message?", choices.map((r) => r.label), { title: "Message for a status", confirmLabel: "Write it" });
    const row = choices.find((r) => r.label === pick);
    if (row) { setAdding(true); setEditing(row); }
  }
  async function setQuietHours(on: boolean) {
    try { await api.put("/notifications/quiet-hours", { on }); setQuiet({ on, some: false }); reload(); flash(on ? "Messages now wait until you open" : "Messages send any time"); }
    catch (e: any) { flash(e?.message || "Couldn't change quiet hours"); }
  }

  const rowRules = (row: Row) => channels.flatMap(([c]) => cellRules(rules, rt, row.event, row.status, c));
  const rowStat = (row: Row) => rowRules(row).reduce((acc, r) => {
    const st = stats?.rules?.[r.id]; if (!st) return acc;
    return { sent: acc.sent + st.sent_7d, failed: acc.failed + st.failed_7d, last: [acc.last, st.last_sent_at].filter(Boolean).sort().pop() || null };
  }, { sent: 0, failed: 0, last: null as string | null });
  const previewOf = (row: Row) => {
    const r = channels.map(([c]) => cellRules(rules, rt, row.event, row.status, c).find((x) => x.active)).find(Boolean)
      || rowRules(row)[0];
    const t = r?.template_id ? templates.find((x) => x.id === r.template_id) : null;
    return fill(t?.body_template || defaultBody(row), { "{Order Status}": row.status ? row.label : "Ready for pickup", "{Company Name}": shopName || "Your shop" });
  };

  const reminderRules = rules.filter((r) => (r.module || "order") === "order" && (r.scope || "client") === "client" && r.event === "reminder");
  const testMode = (["sms", "email"] as const).filter((k) => providers[k] && providers[k] !== "live").map((k) => (k === "sms" ? "Texts" : "Emails"));
  const ownStatusCount = (event: string) => listed.filter((r) => r.status && r.event === event).length;

  const Seg = ({ value, set, opts }: { value: string; set: (v: any) => void; opts: [string, string][] }) => (
    <div className="inline-flex rounded-xl bg-slate-100 p-1">
      {opts.map(([v, l]) => (
        <button key={v} onClick={() => set(v)} aria-pressed={value === v}
          className={`px-3.5 py-1.5 rounded-lg text-xs font-semibold transition-colors ${value === v ? "bg-white text-ink-primary shadow-sm" : "text-ink-secondary hover:text-ink-primary"}`}>{l}</button>
      ))}
    </div>
  );

  const RowView = ({ row }: { row: Row }) => {
    const st = rowStat(row); const ca = catchAllFor(row);
    const replaces = ca ? channels.filter(([c]) => isOn(row, c) && isOn(ca, c)).map(([, l]) => l.toLowerCase()) : [];
    const base = row.id === "any" ? `One message whenever the ${noun} moves to a new status` : row.sub;
    const note = row.allStatuses && ownStatusCount(row.event) ? `${base}. Statuses with their own message send that instead` : base;
    return (
      <div className="group flex items-start gap-3 px-4 py-3.5 hover:bg-slate-50/70 cursor-pointer" onClick={() => { setAdding(false); setEditing(row); }}>
        {row.color ? <span className="mt-1.5 w-2.5 h-2.5 rounded-full flex-shrink-0" style={{ background: row.color, boxShadow: `0 0 0 3px ${row.color}22` }} />
          : <span className="mt-0.5 w-6 h-6 rounded-lg bg-slate-100 text-ink-secondary grid place-items-center flex-shrink-0"><M n={row.allStatuses ? "all_inclusive" : row.event === "payment_received" ? "payments" : row.event === "comment_added" ? "chat_bubble" : "login"} c="text-[13px]" /></span>}
        <div className="flex-1 min-w-0">
          <div className="text-sm font-medium text-ink-primary truncate">{row.label}</div>
          {note && <div className="text-[11px] text-ink-tertiary truncate">{note}</div>}
          {anyOn(row) && <div className="mt-1 text-xs text-ink-secondary truncate italic">“{previewOf(row)}”</div>}
          <div className="mt-1 flex flex-wrap gap-x-2 text-[11px]">
            {st.sent > 0 && <span className="text-ink-tertiary">Sent {st.sent} this week{st.last ? ` · last ${ago(st.last)}` : ""}</span>}
            {st.failed > 0 && <span className="text-rose-600">{st.failed} didn't go through</span>}
            {replaces.length > 0 && <span className="text-ink-tertiary">Sent instead of the “{ca!.label}” {replaces.join(" and ")}</span>}
          </div>
        </div>
        <div className="flex items-center gap-1.5 flex-shrink-0" onClick={(e) => e.stopPropagation()}>
          {channels.map(([ch, l, icon]) => {
            const on = isOn(row, ch);
            return (
              <button key={ch} type="button" role="switch" aria-checked={on} disabled={busy === `${row.id}:${ch}`} aria-label={`${l} when ${row.label}`} onClick={() => toggle(row, ch)}
                className={`inline-flex items-center gap-1 h-8 px-2.5 rounded-full text-[11px] font-semibold border transition-colors disabled:opacity-50 ${on ? "bg-primary/10 border-primary/30 text-primary" : "border-dashed border-slate-300 text-ink-tertiary hover:text-ink-secondary hover:border-slate-400"}`}>
                <M n={on ? "check" : icon} c="text-[13px]" />{l}
              </button>
            );
          })}
          <M n="chevron_right" c="text-base text-ink-tertiary ml-1" />
        </div>
      </div>
    );
  };

  return (
    <div className="max-w-4xl space-y-4 pb-10">
      <div className="flex items-start gap-3">
        <div>
          <h2 className="text-base font-semibold text-ink-primary">Notifications</h2>
          <p className="text-xs text-ink-tertiary">The texts and emails your customers and team get automatically.</p>
        </div>
        <div className="ml-auto"><Seg value={view} set={setView} opts={[["customers", "Customers"], ["internal", "Your team"], ["automations", "Automations"]]} /></div>
      </div>

      {view !== "customers" ? renderOther(view) : (<>
        {testMode.length > 0 && (
          <div className="flex items-center gap-3 rounded-2xl bg-amber-50 border border-amber-200/80 px-4 py-3">
            <span className="w-8 h-8 rounded-xl bg-amber-100 text-amber-700 grid place-items-center flex-shrink-0"><M n="warning" c="text-base" /></span>
            <div className="min-w-0 flex-1 text-xs text-amber-900 leading-relaxed"><span className="font-semibold">{testMode.join(" and ")} are in test mode.</span> They're saved in the log but nobody receives them until you connect {testMode.length === 2 ? "a phone number and a mail account" : testMode[0] === "Texts" ? "a phone number" : "a mail account"}.</div>
          </div>
        )}

        <div className="flex items-center gap-2">
          <Seg value={rt} set={setRt} opts={[["work_order", "Jobs"], ["estimate", "Estimates"]]} />
        </div>

        {/* What your customer gets: the job's journey, one stop per moment. */}
        <div className="bg-white rounded-2xl border border-slate-200/90 shadow-clay-card p-5">
          <div className="text-[11px] font-semibold text-ink-tertiary mb-3">What your customer gets</div>
          <div className="relative grid grid-cols-2 md:grid-cols-4 gap-3">
            <div className="hidden md:block absolute top-5 left-[12%] right-[12%] h-0.5 bg-primary/30" />
            {MOMENTS.map((m) => {
              const on = listed.filter((r) => r.moment === m.key && anyOn(r));
              const first = on[0];
              return (
                <button key={m.key} type="button" onMouseEnter={() => setPeek(m.key)} onMouseLeave={() => setPeek(null)} onFocus={() => setPeek(m.key)} onBlur={() => setPeek(null)}
                  onClick={() => document.getElementById(`moment-${m.key}`)?.scrollIntoView({ behavior: "smooth", block: "start" })}
                  className="relative flex flex-col items-center text-center gap-1.5 rounded-xl px-2 py-1 hover:bg-slate-50">
                  <span className={`relative z-10 w-10 h-10 rounded-2xl grid place-items-center ring-4 ring-white ${on.length ? "bg-primary text-white shadow-[0_6px_16px_-6px] shadow-primary/60" : "bg-slate-100 text-ink-tertiary"}`}><M n={m.icon} c="text-lg" /></span>
                  <span className="text-xs font-semibold text-ink-primary">{m.label}</span>
                  <span className={`text-[11px] ${on.length ? "text-primary" : "text-ink-tertiary"}`}>{on.length ? `${on.length} message${on.length === 1 ? "" : "s"}` : "Nothing sent"}</span>
                  {peek === m.key && first && (
                    <span className="absolute top-full mt-2 z-20 w-56 text-left rounded-2xl rounded-tl-md bg-white border border-slate-200 shadow-clay-drawer px-3 py-2 text-[11.5px] leading-snug text-ink-primary">
                      <span className="block text-[11px] font-semibold text-ink-tertiary mb-0.5">{first.label}</span>{previewOf(first)}
                    </span>
                  )}
                </button>
              );
            })}
          </div>
        </div>

        {statuses === null && <div className="text-xs text-ink-tertiary px-1">Loading…</div>}
        {statuses !== null && MOMENTS.map((m) => {
          const rows = listed.filter((r) => r.moment === m.key);
          const canAdd = (m.key === "shop" || m.key === "done") && unlisted.some((r) => r.moment === m.key);
          return (
            <section key={m.key} id={`moment-${m.key}`} className="bg-white rounded-2xl border border-slate-200/90 shadow-clay-card overflow-hidden scroll-mt-4">
              <div className="flex items-center gap-2.5 px-4 pt-4 pb-2">
                <span className="w-7 h-7 rounded-lg bg-primary/10 text-primary grid place-items-center"><M n={m.icon} c="text-[15px]" /></span>
                <span className="text-sm font-semibold text-ink-primary">{m.label}</span>
                <span className="text-[11px] text-ink-tertiary">{m.hint}</span>
              </div>
              <div className="divide-y divide-slate-100">{rows.map((r) => <RowView key={r.id} row={r} />)}</div>
              {canAdd && (
                <button onClick={() => addForStatus(m.key)} className="w-full flex items-center gap-2 px-4 py-3 border-t border-slate-100 text-xs font-semibold text-primary hover:bg-slate-50">
                  <M n="add" c="text-sm" />Message for another status
                </button>
              )}
            </section>
          );
        })}

        <div className="grid md:grid-cols-2 gap-3">
          <div className="bg-white rounded-2xl border border-slate-200/90 shadow-clay-card p-4 flex items-start gap-3">
            <span className="w-9 h-9 rounded-xl bg-indigo-50 text-indigo-600 grid place-items-center flex-shrink-0"><M n="bedtime" c="text-lg" /></span>
            <div className="flex-1 min-w-0">
              <div className="text-sm font-medium text-ink-primary">Quiet hours</div>
              <div className="text-[11px] text-ink-tertiary">{quiet?.some ? "On for some messages. Switch to set it for all." : "Messages that would go out after hours wait until you open."}</div>
            </div>
            <button type="button" role="switch" aria-checked={!!quiet?.on} aria-label="Quiet hours" disabled={!quiet} onClick={() => setQuietHours(!quiet?.on)}
              className={`w-10 h-6 rounded-full relative transition-colors flex-shrink-0 ${quiet?.on ? "bg-primary" : "bg-slate-300"}`}><span className={`absolute top-0.5 w-5 h-5 rounded-full bg-white shadow transition-all ${quiet?.on ? "left-[18px]" : "left-0.5"}`} /></button>
          </div>
          <div className="bg-white rounded-2xl border border-slate-200/90 shadow-clay-card p-4 flex items-start gap-3">
            <span className="w-9 h-9 rounded-xl bg-emerald-50 text-emerald-600 grid place-items-center flex-shrink-0"><M n="smartphone" c="text-lg" /></span>
            <div className="min-w-0">
              <div className="text-sm font-medium text-ink-primary">{stats ? `Texts reach ${stats.texting.with_phone - stats.texting.opted_out} of ${stats.texting.with_phone} customers` : "Who can get texts"}</div>
              <div className="text-[11px] text-ink-tertiary">{stats?.texting.opted_out ? `${stats.texting.opted_out} replied STOP. They can reply START to get texts again.` : "Customers can reply STOP to any text to turn them off."}</div>
            </div>
          </div>
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
        <MessageEditor row={editing} rt={rt} channels={channels} rules={rules.filter((r: Rule) => isCustomerRule(r) && inView(r, rt))} templates={templates} shopName={shopName}
          flash={flash} startOn={adding} onClose={() => setEditing(null)} onSaved={() => { setEditing(null); reload(); flash("Message saved"); }} />
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

function MessageEditor({ row, rt, channels, rules, templates, shopName, flash, startOn, onClose, onSaved }: {
  row: Row; rt: string; startOn?: boolean; channels: [string, string, string][]; rules: Rule[]; templates: any[]; shopName: string; flash: (m: string) => void; onClose: () => void; onSaved: () => void;
}) {
  const ruleFor = (ch: string) => rules.find((r) => r.event === row.event && r.channel === ch && (row.status ? r.trigger_status_key === row.status : !r.trigger_status_key) && r.active)
    || rules.find((r) => r.event === row.event && r.channel === ch && (row.status ? r.trigger_status_key === row.status : !r.trigger_status_key));
  const firstOn = channels.find(([c]) => ruleFor(c)?.active)?.[0] || "sms";
  const [ch, setCh] = useState(firstOn);
  const rule = ruleFor(ch);
  const tpl = rule?.template_id ? templates.find((t) => t.id === rule.template_id) : null;
  const [body, setBody] = useState<string>(tpl?.body_template || "");
  const [subject, setSubject] = useState<string>(tpl?.subject || "");
  const [on, setOn] = useState<boolean>(!!rule?.active || !!startOn);
  const [delay, setDelay] = useState<number>(rule?.delay_min || 0);
  const [hours, setHours] = useState<boolean>(!!rule?.working_hours_only);
  const [busy, setBusy] = useState(false); const [err, setErr] = useState<string | null>(null);
  const [ref, setRef] = useState<HTMLTextAreaElement | null>(null);

  useEffect(() => {   // switching channel loads that channel's message
    const r = ruleFor(ch); const t = r?.template_id ? templates.find((x) => x.id === r.template_id) : null;
    setBody(t?.body_template || ""); setSubject(t?.subject || ""); setOn(!!r?.active || (!!startOn && ch === firstOn)); setDelay(r?.delay_min || 0); setHours(!!r?.working_hours_only);
  }, [ch]);  // eslint-disable-line
  useEffect(() => {
    const k = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", k); return () => window.removeEventListener("keydown", k);
  }, [onClose]);

  const extra = { "{Order Status}": row.status ? row.label : "Ready for pickup", "{status}": row.status ? row.label : "Ready for pickup",
    "{Company Name}": shopName || "Your shop", "{shop_name}": shopName || "Your shop" };
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

  async function sendSample() {
    try {
      const r = await api.post<any>("/notifications/test", { channel: ch, subject: ch === "email" ? (subject.trim() || null) : null, body: shown, sample: true });
      flash(r?.status === "sent" ? `Sample sent to ${r.recipient}` : r?.error ? `Not sent: ${r.error}` : `Sample logged for ${r?.recipient} (test mode)`);
    } catch (e: any) {
      let m = e?.message || "Couldn't send a sample";
      try { m = JSON.parse(m).detail || m; } catch { /* plain text */ }
      flash(m);
    }
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
                    <div className="w-7 h-7 rounded-full bg-slate-200 mx-auto grid place-items-center text-[11px] font-semibold text-ink-secondary">{(shopName || "S").slice(0, 1).toUpperCase()}</div>
                    <div className="text-[11px] text-ink-secondary mt-0.5 truncate">{shopName || "Your shop"}</div>
                  </div>
                  <div className="p-2.5 min-h-[220px] flex flex-col justify-end">
                    <div className="self-start max-w-[92%] bg-white border border-slate-200 rounded-2xl rounded-bl-md px-2.5 py-1.5 text-[11.5px] text-ink-primary leading-snug whitespace-pre-wrap break-words shadow-sm">{preview}</div>
                  </div>
                </div>
              )}
              <p className="text-[11px] text-ink-tertiary mt-2 text-center">Sample customer and job. Real details are filled in when it sends.</p>
            </div>
          </div>
          {err && <div className="mt-4 rounded-lg bg-rose-50 border border-rose-200 text-rose-700 text-xs px-3 py-2">{err}</div>}
        </div>

        <div className="px-5 py-4 border-t border-slate-100 flex items-center gap-2">
          <button onClick={sendSample} className="inline-flex items-center gap-1.5 px-3 py-2 rounded-xl border border-slate-200 text-xs font-semibold text-ink-secondary hover:bg-slate-50"><M n="send" c="text-sm" />Send me a sample</button>
          {body.trim() && <button onClick={() => setBody("")} className="px-3 py-2 rounded-xl text-xs font-semibold text-ink-secondary hover:bg-slate-100">Use standard wording</button>}
          <button onClick={onClose} className="ml-auto px-4 py-2 rounded-xl border border-slate-200 text-ink-secondary hover:bg-slate-50 font-semibold text-xs">Cancel</button>
          <button onClick={save} disabled={busy} className="px-5 py-2 rounded-xl bg-ink-primary text-white font-semibold text-xs hover:opacity-90 disabled:opacity-60">{busy ? "Saving…" : "Save"}</button>
        </div>
      </div>
    </div>
  );
}
