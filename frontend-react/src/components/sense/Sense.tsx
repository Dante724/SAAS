import { useEffect, useState } from "react";
import { M } from "../Shell";
import { api } from "../../api/client";
import { useMe } from "../../context/Me";
import { money, currencySymbol } from "../../pages/orders/types";
import Switch from "../Switch";
import { SettingRow, NumField } from "../SettingRow";

/* Tekly Sense — quiet suggestions learned from the shop's own history (no AI service).
   Everything here suggests and explains; nothing changes a job on its own. Styling is
   deliberately plain (slate chip, one small sapphire icon) so it reads as part of the app, not a robot. */

export type SenseFeature = "job_suggestions" | "deferred" | "ready_time_why" | "margin_check"
  | "morning_brief" | "added_together" | "service_due" | "watchdog";
export type WatchdogCfg = { discount_pct: number; cash_tolerance: number };
type AgentKey = "collections" | "quote_followup" | "win_back";
type AgentCfg = { on: boolean; auto_send: boolean; wait_days?: number; months?: number; channel: "sms" | "email" };
export type AgentsCfg = Record<AgentKey, AgentCfg> & { daily_cap: number };
export type BriefCfg = { recipients: string[]; hour: number; email: boolean; push: boolean };
export type SenseSettings = {
  enabled: boolean;
  features: Record<SenseFeature, boolean>;
  brief: BriefCfg;
  watchdog: WatchdogCfg;
  agents: AgentsCfg;
  learning?: { closed_jobs: number; needed: number; ready: boolean };   // owner only
};

let cached: Promise<SenseSettings | null> | null = null;
export function loadSense(force = false): Promise<SenseSettings | null> {
  if (!cached || force) cached = api.get<SenseSettings>("/sense/settings").catch(() => null);
  return cached;
}

/** Is this Sense feature on for the shop? null while loading. */
export function useSenseOn(feature: SenseFeature): boolean | null {
  const [on, setOn] = useState<boolean | null>(null);
  useEffect(() => { loadSense().then((s) => setOn(!!s && s.enabled && !!s.features?.[feature])); }, [feature]);
  return on;
}

export function Spark({ c = "" }: { c?: string }) {
  return <M n="lightbulb" c={`text-primary text-xs ${c}`} />;
}

/** A one-tap suggestion with a "Why?" explanation. */
export function SuggestedChip({ label, price, why, onAdd, onDismiss }: {
  label: string; price?: number; why: string; onAdd: () => void; onDismiss?: () => void;
}) {
  const [open, setOpen] = useState(false);
  return (
    <span className="relative inline-flex items-center rounded-full border border-slate-200 bg-slate-50 text-[11px] text-ink-secondary">
      <button type="button" onClick={onAdd} className="pl-2.5 pr-1.5 py-1 font-semibold text-ink-primary hover:text-primary" title="Add">
        + {label}{price != null && <span className="font-normal text-ink-tertiary"> {money(price)}</span>}
      </button>
      <button type="button" onClick={() => setOpen((v) => !v)} aria-expanded={open} className="px-1.5 py-1 text-ink-tertiary hover:text-primary underline decoration-dotted">Why?</button>
      {onDismiss && <button type="button" aria-label={`Hide ${label}`} onClick={onDismiss} className="pr-2 pl-0.5 py-1 text-ink-tertiary hover:text-rose-600"><M n="close" c="text-[12px]" /></button>}
      {open && (
        <span role="note" className="absolute left-0 top-full z-20 mt-1 w-64 rounded-xl border border-slate-200 bg-white p-2.5 text-[11px] leading-snug text-ink-secondary shadow-lg">
          {why}
        </span>
      )}
    </span>
  );
}

type DraftItem = { description: string; kind?: string; line_type?: string; ref_id?: string | null; unit_price?: number; why: { used: number; similar: number } };

/** "Suggested" lines for a new job, from similar closed jobs in this shop. */
export function JobSuggestions({ complaint, deviceId, onAdd }: {
  complaint: string; deviceId?: string | null; onAdd: (i: { description: string; kind: string; price: number; ref_id?: string | null }) => void;
}) {
  const { can } = useMe();
  const allowed = can("can_use_sense_suggestions");
  const on = useSenseOn("job_suggestions");
  const [items, setItems] = useState<DraftItem[]>([]);
  const [learning, setLearning] = useState(false);
  const [hidden, setHidden] = useState<string[]>([]);
  useEffect(() => {
    if (!allowed || !on || complaint.trim().length < 4) { setItems([]); return; }
    const t = setTimeout(() => {
      api.post<any>("/sense/draft", { complaint, device_id: deviceId || undefined })
        .then((r) => { setItems(r.items || []); setLearning(!!r.learning); })
        .catch(() => setItems([]));
    }, 400);
    return () => clearTimeout(t);
  }, [complaint, deviceId, allowed, on]);
  if (!allowed || !on) return null;
  const shown = items.filter((i) => !hidden.includes(i.description));
  if (!shown.length) return learning ? <p className="text-[11px] text-ink-tertiary flex items-center gap-1"><Spark /> Learning your shop — suggestions appear after 20 closed jobs.</p> : null;
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <span className="text-[11px] font-semibold text-ink-tertiary flex items-center gap-1"><Spark /> Suggested</span>
      {shown.map((i) => (
        <SuggestedChip key={i.description} label={i.description} price={i.unit_price}
          why={`Used on ${i.why.used} of your last ${i.why.similar} similar jobs.`}
          onAdd={() => { onAdd({ description: i.description, kind: i.kind || "service", price: i.unit_price || 0, ref_id: i.ref_id }); setHidden((h) => [...h, i.description]); }}
          onDismiss={() => setHidden((h) => [...h, i.description])} />
      ))}
    </div>
  );
}

type Deferred = { id: string; title: string; note?: string | null; kind?: string; ref_id?: string | null; qty?: number; unit_price?: number; decided_at?: string | null; from_order_number: string };

/** Work this customer said "not now" to last time — offered as a draft recommendation. */
export function DeferredWork({ orderId, onFlash, onChanged }: { orderId: string; onFlash: (m: string) => void; onChanged?: () => void }) {
  const { can } = useMe();
  const allowed = can("can_use_sense_suggestions") && can("can_recommend");
  const on = useSenseOn("deferred");
  const [items, setItems] = useState<Deferred[]>([]);
  const [done, setDone] = useState<string[]>([]);
  useEffect(() => {
    if (!allowed || !on) return;
    api.get<any>(`/sense/orders/${orderId}/deferred`).then((r) => setItems(r.items || [])).catch(() => setItems([]));
  }, [orderId, allowed, on]);
  const left = items.filter((i) => !done.includes(i.id));
  if (!allowed || !on || !left.length) return null;
  const add = async (i: Deferred) => {
    try {
      await api.post(`/orders/${orderId}/recommendations`, { title: i.title, note: i.note || undefined, kind: i.kind || "custom",
        ref_id: i.ref_id || undefined, qty: i.qty || 1, unit_price: i.unit_price || 0 });
      setDone((d) => [...d, i.id]); onFlash("Added as a draft recommendation"); onChanged?.();
    } catch (e: any) { onFlash(e.message || "Couldn't add it"); }
  };
  return (
    <div className="rounded-2xl border border-slate-200 bg-white p-3.5 space-y-2">
      <div className="text-[11px] font-semibold text-ink-tertiary flex items-center gap-1"><Spark /> Deferred last time</div>
      {left.map((i) => (
        <div key={i.id} className="flex items-center gap-2 text-xs">
          <span className="font-semibold text-ink-primary truncate">{i.title}</span>
          {i.unit_price != null && <span className="tabular-nums text-ink-tertiary">{money(i.unit_price)}</span>}
          <span className="text-[11px] text-ink-tertiary truncate">· said "not now" on {i.from_order_number}</span>
          <button type="button" onClick={() => add(i)} className="ml-auto flex-shrink-0 rounded-lg border border-slate-200 px-2.5 py-1 text-[11px] font-semibold text-ink-secondary hover:border-primary/40 hover:text-primary">Add as draft</button>
        </div>
      ))}
    </div>
  );
}

/** A quiet note when this job's margin is well under the shop's usual margin. Never blocks. */
export function MarginNote({ orderId, refreshKey }: { orderId: string; refreshKey?: unknown }) {
  const { can } = useMe();
  const allowed = can("can_see_profit") && can("can_view_purchase_price");
  const on = useSenseOn("margin_check");
  const [m, setM] = useState<any>(null);
  const [why, setWhy] = useState(false);
  useEffect(() => {
    if (!allowed || !on) return;
    api.get<any>(`/sense/orders/${orderId}/margin`).then(setM).catch(() => setM(null));
  }, [orderId, allowed, on, refreshKey]);
  if (!allowed || !on || !m?.flag) return null;
  return (
    <div className="rounded-xl border border-amber-200 bg-amber-50 px-3.5 py-2 text-[11px] text-amber-900">
      <span className="font-semibold">Margin {m.margin_pct}%</span> — your jobs usually make {m.usual_pct}%.{" "}
      <button type="button" onClick={() => setWhy((v) => !v)} className="underline decoration-dotted">Why?</button>
      {why && <span className="block mt-1 text-amber-800">Compared with the median margin of your last {m.sample} closed jobs (12 months). Check the prices and part costs before sending.</span>}
    </div>
  );
}

/** "Why?" for the ready-time estimate on a work order. */
export function ReadyWhy({ sampleN, typicalMin, fmt }: { sampleN: number; typicalMin: number | null; fmt: (m: number) => string }) {
  const on = useSenseOn("ready_time_why");
  const [open, setOpen] = useState(false);
  if (!on || !sampleN) return null;
  return (
    <span className="relative">
      <button type="button" onClick={() => setOpen((v) => !v)} className="text-ink-tertiary underline decoration-dotted hover:text-primary">Why?</button>
      {open && (
        <span role="note" className="absolute right-0 top-full z-20 mt-1 w-64 rounded-xl border border-slate-200 bg-white p-2.5 text-[11px] leading-snug text-ink-secondary shadow-lg">
          Based on how long your last {sampleN} similar job{sampleN === 1 ? "" : "s"} took in each step{typicalMin != null && typicalMin >= 1 ? ` (this step usually ~${fmt(typicalMin)})` : ""}. It gets sharper as you close more jobs.
        </span>
      )}
    </span>
  );
}

const FEATURE_ROWS: { group: string; rows: { k: SenseFeature; label: string; hint: string }[] }[] = [
  { group: "At the counter", rows: [
    { k: "job_suggestions", label: "Job suggestions on new jobs", hint: "Lines your shop usually adds for a complaint like this" },
    { k: "added_together", label: "Usually added together", hint: "Lines your jobs often have alongside the ones already on this job" },
    { k: "deferred", label: "Work deferred last time", hint: "What the customer said \"not now\" to on an earlier visit" },
    { k: "service_due", label: "Service due", hint: "Regular services this item is due for, learned from how often you repeat them" },
    { k: "ready_time_why", label: "Explain the ready-time estimate", hint: "Shows what the estimate is based on" },
  ] },
  { group: "For you", rows: [
    { k: "morning_brief", label: "Morning Brief", hint: "Yesterday, today and what needs you, on your Today page" },
    { k: "margin_check", label: "Margin check", hint: "A note when a job makes far less than your usual margin" },
    { k: "watchdog", label: "Watchdog and team scorecard", hint: "Unusual discounts, refunds, voids and cash shortfalls, for your eyes only" },
  ] },
];

type Person = { id: string; name: string; active?: boolean };
const HOURS = Array.from({ length: 24 }, (_, h) => h);
const hourLabel = (h: number) => new Date(2000, 0, 1, h).toLocaleTimeString([], { hour: "numeric" });

/** A number field with its unit inside that saves when you leave it (not on every keystroke). */
function SavedNum({ id, value, onSave, unit, disabled, max, ok = (v) => v > 0, width }: {
  id: string; value: number | undefined; onSave: (v: number) => void; unit: string;
  disabled?: boolean; max?: number; ok?: (v: number) => boolean; width?: string;
}) {
  const [v, setV] = useState<number>(value ?? 0);
  useEffect(() => { setV(value ?? 0); }, [value]);
  return (
    <div onBlur={() => { if (ok(v) && v !== value) onSave(v); }}>
      <NumField id={id} value={v} onChange={setV} unit={unit} disabled={disabled} max={max} width={width} />
    </div>
  );
}

const subPanel = "ml-1 mb-2 rounded-xl bg-slate-50 border border-slate-100 px-4 py-4";

/** The owner's Watchdog thresholds. */
function WatchdogControls({ s, busy, save }: { s: SenseSettings; busy: boolean; save: (p: { watchdog: Partial<WatchdogCfg> }) => void }) {
  const w = s.watchdog;
  return (
    <div className={subPanel}>
      <div className="divide-y divide-slate-200/70">
        <SettingRow label="Big discount" help="Discounts at or above this count as big" htmlFor="sense-wd-discount">
          <SavedNum id="sense-wd-discount" value={w.discount_pct} unit="%" max={100} disabled={busy}
            onSave={(v) => save({ watchdog: { discount_pct: v } })} />
        </SettingRow>
        <SettingRow label="Cash drawer short by more than" help="A drawer count this far under expected gets flagged" htmlFor="sense-wd-cash">
          <SavedNum id="sense-wd-cash" value={w.cash_tolerance} unit={currencySymbol()} ok={(v) => v >= 0} disabled={busy}
            onSave={(v) => save({ watchdog: { cash_tolerance: v } })} />
        </SettingRow>
      </div>
      <p className="mt-3 text-xs text-ink-tertiary">Only you see Watchdog. A flag needs at least 3 events and twice what others usually do, and it shows the records behind it.</p>
    </div>
  );
}

const AGENT_ROWS: { k: AgentKey; label: string; hint: string; wait: "wait_days" | "months" }[] = [
  { k: "collections", label: "Collections", hint: "Reminds customers about overdue invoices", wait: "wait_days" },
  { k: "quote_followup", label: "Quote follow-up", hint: "Nudges customers who haven't answered a quote", wait: "wait_days" },
  { k: "win_back", label: "Win-back", hint: "Invites back customers who gave marketing consent and haven't been in a while", wait: "months" },
];

/** Customers: the follow-up agents. Off, and asking before every send, until the owner says otherwise. */
function AgentControls({ s, busy, save }: { s: SenseSettings; busy: boolean; save: (p: { agents: any }) => void }) {
  const a = s.agents;
  return (
    <div className={s.enabled ? "" : "opacity-50"}>
      <div className="pt-2 text-xs font-semibold text-ink-tertiary">Customers</div>
      {AGENT_ROWS.map((r) => {
        const c = a[r.k];
        const waitVal = r.wait === "months" ? c.months : c.wait_days;
        return (
          <div key={r.k} className="py-2.5 border-t border-slate-100 first:border-0">
            <div className="flex items-center gap-3">
              <span className="min-w-0 flex-1"><span className="block text-sm font-medium text-ink-primary">{r.label}</span><span className="block text-xs text-ink-tertiary">{r.hint}</span></span>
              <Switch on={c.on} label={r.label} disabled={!s.enabled} onChange={() => save({ agents: { [r.k]: { on: !c.on } } })} />
            </div>
            {c.on && (
              <div className={`mt-2 ${subPanel}`}>
                <div className="divide-y divide-slate-200/70">
                  <SettingRow label="Ask me before sending" help="Drafts wait for your OK before they go out">
                    <Switch size="sm" on={!c.auto_send} label="Ask me before sending"
                      onChange={() => save({ agents: { [r.k]: { auto_send: !c.auto_send } } })} />
                  </SettingRow>
                  <SettingRow label={r.wait === "months" ? "Customers away for" : r.k === "collections" ? "Days after it's due" : "Wait before nudging"}
                    help={r.wait === "months" ? "How long since their last visit" : r.k === "collections" ? "When the first reminder goes out" : "Days after the quote was sent"} htmlFor={`sense-agent-${r.k}-wait`}>
                    <SavedNum id={`sense-agent-${r.k}-wait`} value={waitVal} unit={r.wait === "months" ? "months" : "days"} disabled={busy}
                      onSave={(v) => save({ agents: { [r.k]: { [r.wait]: v } } })} />
                  </SettingRow>
                  <SettingRow label="Send by" htmlFor={`sense-agent-${r.k}-channel`}>
                    <select id={`sense-agent-${r.k}-channel`} aria-label={`${r.label} channel`} value={c.channel} disabled={busy} onChange={(e) => save({ agents: { [r.k]: { channel: e.target.value } } })}
                      className="glass-input rounded-xl px-3 py-2 text-sm">
                      {r.k !== "win_back" && <option value="sms">Text</option>}<option value="email">Email</option></select>
                  </SettingRow>
                </div>
                {r.k === "win_back" && <p className="mt-3 text-xs text-ink-tertiary">Email only, to customers who gave marketing consent. Your shop address from Settings goes at the bottom, as the law requires.</p>}
              </div>
            )}
          </div>
        );
      })}
      <div className="py-2.5 border-t border-slate-100">
        <SettingRow label="Most messages a day" help="Each customer gets at most one every 3 days. Consent, opening hours and these switches are checked again right before sending, and every email has an unsubscribe link." htmlFor="sense-daily-cap">
          <SavedNum id="sense-daily-cap" value={a.daily_cap} unit="a day" max={500} width="w-32" disabled={busy}
            onSave={(v) => save({ agents: { daily_cap: v } })} />
        </SettingRow>
      </div>
    </div>
  );
}

/** Who gets the brief, when, and how. Email and push stay off until the owner turns them on. */
function BriefControls({ s, busy, save }: { s: SenseSettings; busy: boolean; save: (p: { brief: Partial<BriefCfg> }) => void }) {
  const [people, setPeople] = useState<Person[]>([]);
  const { me } = useMe();
  useEffect(() => { api.get<Person[]>("/staff/employees").then((r) => setPeople((Array.isArray(r) ? r : []).filter((p) => p.active !== false))).catch(() => setPeople([])); }, []);
  const b = s.brief;
  const others = people.filter((p) => p.id !== me?.id);
  const toggle = (id: string) => save({ brief: { recipients: b.recipients.includes(id) ? b.recipients.filter((x) => x !== id) : [...b.recipients, id] } });
  return (
    <div className={subPanel}>
      <div className="divide-y divide-slate-200/70">
        <SettingRow label="Send at" htmlFor="sense-brief-hour">
          <select id="sense-brief-hour" aria-label="Brief send time" value={b.hour} disabled={busy} onChange={(e) => save({ brief: { hour: Number(e.target.value) } })}
            className="glass-input rounded-xl px-3 py-2 text-sm">{HOURS.map((h) => <option key={h} value={h}>{hourLabel(h)}</option>)}</select>
        </SettingRow>
        <SettingRow label="Email" help="Send the brief by email too">
          <Switch size="sm" on={b.email} label="Email" onChange={() => save({ brief: { email: !b.email } })} />
        </SettingRow>
        <SettingRow label="Push to the phone app" help="The push only says the brief is ready">
          <Switch size="sm" on={b.push} label="Push to the phone app" onChange={() => save({ brief: { push: !b.push } })} />
        </SettingRow>
        <div className="py-4 last:pb-0">
          <div className="text-sm font-medium text-ink-primary">Also send to</div>
          <p className="mt-0.5 mb-2 text-xs text-ink-tertiary">You always get it.</p>
          {others.length ? (
            <div className="flex flex-wrap gap-1.5">
              {others.map((p) => (
                <label key={p.id} className={`flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs ${b.recipients.includes(p.id) ? "border-primary/40 bg-white text-ink-primary" : "border-slate-200 bg-white text-ink-secondary"}`}>
                  <input type="checkbox" checked={b.recipients.includes(p.id)} disabled={busy} onChange={() => toggle(p.id)} className="accent-primary" /> {p.name}
                </label>
              ))}
            </div>
          ) : <span className="text-xs text-ink-tertiary">No one else on the team yet.</span>}
        </div>
      </div>
      <p className="mt-3 text-xs text-ink-tertiary">Counts cover the whole shop; money lines follow each person's own permissions.</p>
    </div>
  );
}

/** Settings → General: the owner's switches for Tekly Sense. Saves each change straight away. */
export function SenseSettingsSection() {
  const [s, setS] = useState<SenseSettings | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { loadSense(true).then(setS); }, []);
  if (!s) return null;
  const save = async (patch: { enabled?: boolean; features?: Partial<Record<SenseFeature, boolean>>; brief?: Partial<BriefCfg>; watchdog?: Partial<WatchdogCfg>; agents?: any }) => {
    if (busy) return;
    setBusy(true); setErr(null);
    try { const r = await api.put<SenseSettings>("/sense/settings", patch); setS(r); cached = Promise.resolve(r); }
    catch (e: any) { setErr(e.message || "Couldn't save"); }
    finally { setBusy(false); }
  };
  const lr = s.learning;
  const pct = lr ? Math.min(100, Math.round((lr.closed_jobs / Math.max(1, lr.needed)) * 100)) : 0;
  return (
    <section className="bg-white rounded-2xl border border-slate-200/90 shadow-clay-card" aria-label="Tekly Sense">
      <div className="flex items-start gap-3 px-5 pt-4 pb-3">
        <span className="w-9 h-9 rounded-xl bg-primary/10 text-primary grid place-items-center flex-shrink-0"><M n="lightbulb" c="text-base" /></span>
        <div className="min-w-0 flex-1">
          <h3 className="text-sm font-semibold text-ink-primary">Tekly Sense</h3>
          <p className="text-xs text-ink-tertiary">Learns from your own jobs. Suggests, never acts, and every hint shows why. Only you see these settings.</p>
        </div>
        <Switch on={s.enabled} label="Tekly Sense" onChange={() => save({ enabled: !s.enabled })} />
      </div>
      <div className="px-5 pb-5 space-y-3">
        {lr && !lr.ready && (
          <div className="rounded-xl bg-slate-50 border border-slate-100 px-3.5 py-2.5">
            <div className="flex justify-between text-[11px] text-ink-secondary"><span>Learning your shop</span><span className="tabular-nums">{lr.closed_jobs} of {lr.needed} closed jobs</span></div>
            <div className="mt-1.5 h-1.5 rounded-full bg-slate-200 overflow-hidden"><div className="h-full bg-primary" style={{ width: `${pct}%` }} /></div>
          </div>
        )}
        <div className={s.enabled ? "" : "opacity-50"}>
          {FEATURE_ROWS.map((g) => (
            <div key={g.group}>
              <div className="pt-2 text-xs font-semibold text-ink-tertiary">{g.group}</div>
              {g.rows.map((r) => (
                <div key={r.k} className="flex items-center gap-3 py-2.5 border-t border-slate-100 first:border-0">
                  <span className="min-w-0 flex-1"><span className="block text-sm font-medium text-ink-primary">{r.label}</span><span className="block text-xs text-ink-tertiary">{r.hint}</span></span>
                  <Switch on={s.features[r.k]} label={r.label} disabled={!s.enabled} onChange={() => save({ features: { [r.k]: !s.features[r.k] } })} />
                </div>
              )).flatMap((el, i) => g.rows[i].k === "morning_brief" && s.enabled && s.features.morning_brief
                ? [el, <BriefControls key="brief-controls" s={s} busy={busy} save={save} />]
                : g.rows[i].k === "watchdog" && s.enabled && s.features.watchdog
                  ? [el, <WatchdogControls key="watchdog-controls" s={s} busy={busy} save={save} />] : [el])}
            </div>
          ))}
        </div>
        {s.agents && <AgentControls s={s} busy={busy} save={save} />}
        <p className="text-xs text-ink-tertiary">Staff see suggestions when their role has "See Tekly Sense suggestions on jobs" (Settings → Team).</p>
        {err && <p className="text-xs font-semibold text-status-overdue-ink">{err}</p>}
      </div>
    </section>
  );
}


type BriefItem = { key: string; title: string; count: number; amount?: number; to: string };
type Brief = { enabled: boolean; date?: string; yesterday?: { jobs_closed: number; paid?: number }; today?: { due: number }; attention?: BriefItem[] };

/** Today page: the owner's morning brief. Only the owner and chosen people get one (else hidden). */
export function MorningBriefCard({ onOpen }: { onOpen: (to: string) => void }) {
  const [b, setB] = useState<Brief | null>(null);
  const [open, setOpen] = useState(true);
  useEffect(() => { api.get<Brief>("/sense/brief").then(setB).catch(() => setB(null)); }, []);
  if (!b?.enabled || !b.yesterday || !b.today) return null;
  const y = b.yesterday;
  const items = b.attention || [];
  return (
    <section aria-label="Morning Brief" className="rounded-2xl bg-white border border-slate-200/80 shadow-clay-sm p-4">
      <button onClick={() => setOpen((v) => !v)} aria-expanded={open} className="w-full flex items-center gap-2 text-left focus:outline-none">
        <M n={open ? "expand_more" : "chevron_right"} c="text-ink-tertiary text-base" />
        <h3 className="text-sm font-semibold text-ink-primary flex items-center gap-1.5"><Spark /> Morning Brief</h3>
        <span className="ml-auto text-[11px] text-ink-tertiary">Learned from your own jobs</span>
      </button>
      {open && (
        <div className="mt-3 grid gap-3 sm:grid-cols-3">
          <div className="rounded-xl bg-slate-50 px-3.5 py-2.5">
            <div className="text-[11px] text-ink-tertiary">Yesterday</div>
            <div className="text-sm font-semibold text-ink-primary">{y.jobs_closed} job{y.jobs_closed === 1 ? "" : "s"} closed</div>
            {y.paid != null && <div className="text-[11px] text-ink-secondary tabular-nums">{money(y.paid)} taken</div>}
          </div>
          <div className="rounded-xl bg-slate-50 px-3.5 py-2.5">
            <div className="text-[11px] text-ink-tertiary">Today</div>
            <div className="text-sm font-semibold text-ink-primary">{b.today.due} job{b.today.due === 1 ? "" : "s"} due</div>
          </div>
          <div className="rounded-xl bg-slate-50 px-3.5 py-2.5 sm:col-span-1">
            <div className="text-[11px] text-ink-tertiary">Worth a look</div>
            {items.length ? (
              <ul className="mt-0.5 space-y-1">
                {items.map((i) => (
                  <li key={i.key}>
                    <button onClick={() => onOpen(i.to)} className="text-left text-xs font-medium text-ink-primary hover:text-primary">
                      {i.title}{i.amount != null && <span className="text-ink-tertiary tabular-nums"> · {money(i.amount)}</span>}
                    </button>
                  </li>
                ))}
              </ul>
            ) : <div className="text-xs text-ink-secondary">Nothing else needs you.</div>}
          </div>
        </div>
      )}
    </section>
  );
}

type Together = { description: string; kind?: string; line_type?: string; ref_id?: string | null; unit_price?: number; why: { with: string; together: number; of: number } };

/** Work order: lines this shop's jobs usually have alongside the ones already on this job. */
export function TogetherChips({ orderId, linesKey, onFlash, onChanged }: { orderId: string; linesKey: string; onFlash: (m: string) => void; onChanged?: () => void }) {
  const { can } = useMe();
  const allowed = can("can_use_sense_suggestions") && (can("can_add_services") || can("can_add_products"));
  const on = useSenseOn("added_together");
  const [items, setItems] = useState<Together[]>([]);
  const [hidden, setHidden] = useState<string[]>([]);
  useEffect(() => {
    if (!allowed || !on || !linesKey) { setItems([]); return; }
    api.get<any>(`/sense/orders/${orderId}/together`).then((r) => setItems(r.items || [])).catch(() => setItems([]));
  }, [orderId, linesKey, allowed, on]);
  const shown = items.filter((i) => !hidden.includes(i.description));
  if (!allowed || !on || !shown.length) return null;
  const add = async (i: Together) => {
    setHidden((h) => [...h, i.description]);
    try {
      const kind = i.kind || "service";
      await api.post(`/orders/${orderId}/lines`, { description: i.description, kind, line_type: i.line_type || (kind === "service" ? "labour" : "part"),
        ref_id: i.ref_id || undefined, qty: 1, unit_price: i.unit_price || 0 });
      onFlash("Line added"); onChanged?.();
    } catch (e: any) { setHidden((h) => h.filter((x) => x !== i.description)); onFlash(e.message || "Couldn't add the line"); }
  };
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <span className="text-[11px] font-semibold text-ink-tertiary flex items-center gap-1"><Spark /> Usually added</span>
      {shown.map((i) => (
        <SuggestedChip key={i.description} label={i.description} price={i.unit_price}
          why={`On ${i.why.together} of your last ${i.why.of} jobs with ${i.why.with}.`}
          onAdd={() => add(i)} onDismiss={() => setHidden((h) => [...h, i.description])} />
      ))}
    </div>
  );
}

type Due = { description: string; every_days: number; last_done_days_ago: number; due_in_days: number; unit_price?: number };
const ago = (d: number) => (d >= 60 ? `${Math.round(d / 30)} months` : `${d} days`);

/** Work order: regular services this item is due for, offered as a draft recommendation. */
export function ServiceDue({ orderId, onFlash, onChanged }: { orderId: string; onFlash: (m: string) => void; onChanged?: () => void }) {
  const { can } = useMe();
  const allowed = can("can_use_sense_suggestions") && can("can_recommend");
  const on = useSenseOn("service_due");
  const [items, setItems] = useState<Due[]>([]);
  const [done, setDone] = useState<string[]>([]);
  useEffect(() => {
    if (!allowed || !on) return;
    api.get<any>(`/sense/orders/${orderId}/service-due`).then((r) => setItems(r.items || [])).catch(() => setItems([]));
  }, [orderId, allowed, on]);
  const left = items.filter((i) => !done.includes(i.description));
  if (!allowed || !on || !left.length) return null;
  const add = async (i: Due) => {
    try {
      await api.post(`/orders/${orderId}/recommendations`, { title: i.description, kind: "service", qty: 1,
        ...(i.unit_price != null ? { unit_price: i.unit_price } : {}),
        note: `Last done ${ago(i.last_done_days_ago)} ago; usually every ${ago(i.every_days)}.`, urgency: i.due_in_days <= 0 ? "now" : "soon" });
      setDone((d) => [...d, i.description]); onFlash("Added as a draft recommendation"); onChanged?.();
    } catch (e: any) { onFlash(e.message || "Couldn't add it"); }
  };
  return (
    <div className="rounded-2xl border border-slate-200 bg-white p-3.5 space-y-2">
      <div className="text-[11px] font-semibold text-ink-tertiary flex items-center gap-1"><Spark /> Due for service</div>
      {left.map((i) => (
        <div key={i.description} className="flex items-center gap-2 text-xs">
          <span className="font-semibold text-ink-primary truncate">{i.description}</span>
          {i.unit_price != null && <span className="tabular-nums text-ink-tertiary">{money(i.unit_price)}</span>}
          <span className="text-[11px] text-ink-tertiary truncate">· last done {ago(i.last_done_days_ago)} ago, usually every {ago(i.every_days)}{i.due_in_days < 0 ? " — overdue" : ""}</span>
          <button type="button" onClick={() => add(i)} className="ml-auto flex-shrink-0 rounded-lg border border-slate-200 px-2.5 py-1 text-[11px] font-semibold text-ink-secondary hover:border-primary/40 hover:text-primary">Add as draft</button>
        </div>
      ))}
    </div>
  );
}


type Evidence = { date: string; ref?: string | null; what: string; pct?: number; amount: number };
type Flag = { key: string; kind: string; employee_name: string; title: string; rule: string; count: number;
  others_median: number | null; amount: number; evidence: Evidence[] };
type ScoreRow = { employee_id: string; name: string; jobs_closed: number; value: number; comebacks: number;
  comeback_rate: number | null; big_discounts: number; refunds: number };

/** Today page, shop owner only: unusual money events per person, and the team scorecard. */
export function WatchdogPanel() {
  const { me } = useMe();
  const owner = !!(me?.is_shop_owner || me?.is_owner);
  const [d, setD] = useState<{ enabled: boolean; flags: Flag[]; scorecard: ScoreRow[]; window_days?: number } | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const [team, setTeam] = useState(false);
  const load = () => api.get<any>("/sense/watchdog").then(setD).catch(() => setD(null));
  useEffect(() => { if (owner) load(); }, [owner]);  // eslint-disable-line react-hooks/exhaustive-deps
  if (!owner || !d?.enabled) return null;
  const review = async (key: string) => {
    try { await api.post("/sense/watchdog/review", { key }); setD((x) => x && { ...x, flags: x.flags.filter((f) => f.key !== key) }); } catch { /* stays listed */ }
  };
  return (
    <section aria-label="Watchdog" className="rounded-2xl bg-white border border-slate-200/80 shadow-clay-sm p-4 space-y-3">
      <div className="flex items-center gap-2">
        <h3 className="text-sm font-semibold text-ink-primary flex items-center gap-1.5"><Spark /> Watchdog</h3>
        <span className="text-[11px] text-ink-tertiary">Last {d.window_days || 30} days · only you see this</span>
        <button onClick={() => setTeam((v) => !v)} aria-expanded={team} className="ml-auto text-[11px] font-semibold text-ink-secondary hover:text-primary">{team ? "Hide team scorecard" : "Team scorecard"}</button>
      </div>
      {d.flags.length === 0 ? (
        <div className="flex items-center gap-2 text-sm text-ink-tertiary"><M n="check_circle" c="text-base text-emerald-500" />Nothing unusual.</div>
      ) : (
        <ul className="space-y-2">
          {d.flags.map((f) => (
            <li key={f.key} className="rounded-xl border border-amber-200 bg-amber-50/60 px-3.5 py-2.5">
              <div className="flex items-center gap-2">
                <span className="text-sm font-semibold text-amber-900">{f.title}</span>
                <span className="text-xs tabular-nums text-amber-900/80">{f.count > 1 ? `${f.count} · ` : ""}{money(f.amount)}</span>
                <button onClick={() => setOpen(open === f.key ? null : f.key)} aria-expanded={open === f.key} className="ml-auto text-[11px] underline decoration-dotted text-amber-900">Why?</button>
                <button onClick={() => review(f.key)} className="text-[11px] font-semibold rounded-lg border border-amber-300 bg-white px-2 py-0.5 text-amber-900 hover:bg-amber-100">Mark reviewed</button>
              </div>
              {open === f.key && (
                <div className="mt-2 text-[11px] text-amber-900/90 space-y-1">
                  <div>{f.rule}.{f.others_median != null ? ` ${f.employee_name}: ${f.count}; others usually ${f.others_median}.` : ""} This is a pattern worth a conversation, not a conclusion.</div>
                  <ul className="list-disc pl-4">
                    {f.evidence.map((e, i) => <li key={i}>{e.date}{e.ref ? ` · ${e.ref}` : ""} · {e.what}{e.pct != null ? ` (${e.pct}%)` : ""} · {money(e.amount)}</li>)}
                  </ul>
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
      {team && (
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead><tr className="text-left text-[11px] text-ink-tertiary">
              <th className="py-1.5 pr-3 font-medium">Person</th><th className="py-1.5 pr-3 font-medium text-right">Jobs closed</th>
              <th className="py-1.5 pr-3 font-medium text-right">Value</th><th className="py-1.5 pr-3 font-medium text-right">Comebacks</th>
              <th className="py-1.5 pr-3 font-medium text-right">Big discounts</th><th className="py-1.5 font-medium text-right">Refunds</th></tr></thead>
            <tbody>
              {d.scorecard.map((r) => (
                <tr key={r.employee_id} className="border-t border-slate-100">
                  <td className="py-1.5 pr-3 text-ink-primary">{r.name}</td>
                  <td className="py-1.5 pr-3 text-right tabular-nums">{r.jobs_closed}</td>
                  <td className="py-1.5 pr-3 text-right tabular-nums">{money(r.value)}</td>
                  <td className="py-1.5 pr-3 text-right tabular-nums">{r.comebacks}{r.comeback_rate != null ? ` (${r.comeback_rate}%)` : ""}</td>
                  <td className="py-1.5 pr-3 text-right tabular-nums">{r.big_discounts}</td>
                  <td className="py-1.5 text-right tabular-nums">{r.refunds}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}


type Draft = { id: string; agent: string; customer?: string | null; channel: string; recipient?: string | null; body: string };
const AGENT_LABEL: Record<string, string> = { collections: "Collections", quote_followup: "Quote follow-up", win_back: "Win-back" };

/** Today page: customer messages the agents drafted, waiting for an OK. */
export function AgentDraftsCard() {
  const { can } = useMe();
  const allowed = can("can_approve_agent_sends");
  const [items, setItems] = useState<Draft[] | null>(null);
  const [busy, setBusy] = useState(false);
  const load = () => api.get<any>("/sense/agents/drafts").then((r) => setItems(r.items || [])).catch(() => setItems(null));
  useEffect(() => { if (allowed) load(); }, [allowed]);  // eslint-disable-line react-hooks/exhaustive-deps
  if (!allowed || !items?.length) return null;
  const act = async (verb: "approve" | "discard", ids: string[]) => {
    setBusy(true);
    try { await api.post(`/sense/agents/drafts/${verb}`, { ids }); setItems((x) => (x || []).filter((d) => !ids.includes(d.id))); }
    finally { setBusy(false); }
  };
  return (
    <section aria-label="Messages waiting for your OK" className="rounded-2xl bg-white border border-slate-200/80 shadow-clay-sm p-4 space-y-2.5">
      <div className="flex items-center gap-2">
        <h3 className="text-sm font-semibold text-ink-primary flex items-center gap-1.5"><Spark /> Messages waiting for your OK</h3>
        <span className="text-[11px] font-bold bg-blue-50 text-primary rounded-full px-1.5 py-0.5 leading-none">{items.length}</span>
        <div className="ml-auto flex gap-1.5">
          <button disabled={busy} onClick={() => act("approve", items.map((d) => d.id))} className="rounded-lg bg-primary px-2.5 py-1 text-[11px] font-semibold text-white disabled:opacity-50">Send all</button>
          <button disabled={busy} onClick={() => act("discard", items.map((d) => d.id))} className="rounded-lg border border-slate-200 px-2.5 py-1 text-[11px] font-semibold text-ink-secondary disabled:opacity-50">Discard all</button>
        </div>
      </div>
      <ul className="space-y-1.5">
        {items.map((d) => (
          <li key={d.id} className="rounded-xl border border-slate-200/80 px-3 py-2">
            <div className="flex items-center gap-2 text-[11px] text-ink-tertiary">
              <span className="font-semibold text-ink-secondary">{AGENT_LABEL[d.agent] || d.agent}</span>
              <span>· {d.customer || "Customer"} · {d.channel === "sms" ? "Text" : d.channel === "whatsapp" ? "WhatsApp" : "Email"}</span>
              <span className="ml-auto flex gap-1.5">
                <button disabled={busy} onClick={() => act("approve", [d.id])} className="rounded-md border border-primary/30 px-2 py-0.5 font-semibold text-primary disabled:opacity-50">Send</button>
                <button disabled={busy} onClick={() => act("discard", [d.id])} className="rounded-md border border-slate-200 px-2 py-0.5 font-semibold text-ink-secondary disabled:opacity-50">Discard</button>
              </span>
            </div>
            <p className="mt-1 whitespace-pre-line text-xs text-ink-primary">{d.body}</p>
          </li>
        ))}
      </ul>
      <p className="text-[11px] text-ink-tertiary">Sent inside opening hours. A customer who replies STOP or unsubscribes before then won't get it.</p>
    </section>
  );
}
