import { useEffect, useState } from "react";
import { M } from "../Shell";
import { api } from "../../api/client";
import { useMe } from "../../context/Me";
import { money } from "../../pages/orders/types";

/* Tekly Sense — quiet suggestions learned from the shop's own history (no AI service).
   Everything here suggests and explains; nothing changes a job on its own. Styling is
   deliberately plain (slate chip, one sapphire ✦) so it reads as part of the app, not a robot. */

export type SenseFeature = "job_suggestions" | "deferred" | "ready_time_why" | "margin_check"
  | "morning_brief" | "added_together" | "service_due" | "watchdog";
export type WatchdogCfg = { discount_pct: number; cash_tolerance: number };
export type BriefCfg = { recipients: string[]; hour: number; email: boolean; push: boolean };
export type SenseSettings = {
  enabled: boolean;
  features: Record<SenseFeature, boolean>;
  brief: BriefCfg;
  watchdog: WatchdogCfg;
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
  return <span aria-hidden className={`text-primary ${c}`}>✦</span>;
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

/** "✦ Suggested" lines for a new job, from similar closed jobs in this shop. */
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

/** The owner's Watchdog thresholds. */
function WatchdogControls({ s, busy, save }: { s: SenseSettings; busy: boolean; save: (p: { watchdog: Partial<WatchdogCfg> }) => void }) {
  const w = s.watchdog;
  const num = (v: string) => (v === "" ? undefined : Number(v));
  return (
    <div className="ml-1 mb-2 flex flex-wrap items-center gap-x-4 gap-y-2 rounded-xl bg-slate-50 border border-slate-100 px-3.5 py-3 text-xs text-ink-secondary">
      <label className="flex items-center gap-1.5">Big discount means
        <input aria-label="Big discount percent" type="number" min={1} max={100} defaultValue={w.discount_pct} disabled={busy}
          onBlur={(e) => { const v = num(e.target.value); if (v && v !== w.discount_pct) save({ watchdog: { discount_pct: v } }); }}
          className="w-16 rounded-lg border border-slate-200 bg-white px-2 py-1 text-xs" />% or more</label>
      <label className="flex items-center gap-1.5">Flag a drawer short by more than
        <input aria-label="Cash tolerance" type="number" min={0} defaultValue={w.cash_tolerance} disabled={busy}
          onBlur={(e) => { const v = num(e.target.value); if (v != null && v !== w.cash_tolerance) save({ watchdog: { cash_tolerance: v } }); }}
          className="w-20 rounded-lg border border-slate-200 bg-white px-2 py-1 text-xs" /></label>
      <span className="w-full text-[11px] text-ink-tertiary">Only you see Watchdog. A flag needs at least 3 events and twice what others usually do, and it shows the records behind it.</span>
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
    <div className="ml-1 mb-2 space-y-2.5 rounded-xl bg-slate-50 border border-slate-100 px-3.5 py-3 text-xs text-ink-secondary">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <label className="flex items-center gap-1.5">Send at
          <select aria-label="Brief send time" value={b.hour} disabled={busy} onChange={(e) => save({ brief: { hour: Number(e.target.value) } })}
            className="rounded-lg border border-slate-200 bg-white px-2 py-1 text-xs">{HOURS.map((h) => <option key={h} value={h}>{hourLabel(h)}</option>)}</select>
        </label>
        <label className="flex items-center gap-1.5"><input type="checkbox" checked={b.email} disabled={busy} onChange={() => save({ brief: { email: !b.email } })} /> Email</label>
        <label className="flex items-center gap-1.5"><input type="checkbox" checked={b.push} disabled={busy} onChange={() => save({ brief: { push: !b.push } })} /> Push to the phone app</label>
      </div>
      <div>
        <div className="mb-1 text-[11px] text-ink-tertiary">Also send to (you always get it):</div>
        {others.length ? (
          <div className="flex flex-wrap gap-1.5">
            {others.map((p) => (
              <label key={p.id} className={`flex items-center gap-1.5 rounded-full border px-2.5 py-1 ${b.recipients.includes(p.id) ? "border-primary/40 bg-white text-ink-primary" : "border-slate-200 bg-white"}`}>
                <input type="checkbox" checked={b.recipients.includes(p.id)} disabled={busy} onChange={() => toggle(p.id)} /> {p.name}
              </label>
            ))}
          </div>
        ) : <span className="text-[11px] text-ink-tertiary">No one else on the team yet.</span>}
      </div>
      <p className="text-[11px] text-ink-tertiary">Counts cover the whole shop; money lines follow each person's own permissions. The push only says the brief is ready.</p>
    </div>
  );
}

// Declared at module level so a save doesn't remount the switch (keeps keyboard focus on it).
function Toggle({ on, label, onClick, disabled, busy }: { on: boolean; label: string; onClick: () => void; disabled?: boolean; busy?: boolean }) {
  return (
    <button type="button" role="switch" aria-checked={on} aria-label={label} onClick={onClick} disabled={disabled}
      aria-busy={busy || undefined}
      className={`ml-auto w-10 h-6 rounded-full flex-shrink-0 relative transition-colors disabled:opacity-50 ${on ? "bg-primary" : "bg-slate-300"}`}>
      <span className={`absolute top-0.5 w-5 h-5 rounded-full bg-white shadow transition-all ${on ? "left-[18px]" : "left-0.5"}`} />
    </button>
  );
}

/** Settings → General: the owner's switches for Tekly Sense. Saves each change straight away. */
export function SenseSettingsSection() {
  const [s, setS] = useState<SenseSettings | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { loadSense(true).then(setS); }, []);
  if (!s) return null;
  const save = async (patch: { enabled?: boolean; features?: Partial<Record<SenseFeature, boolean>>; brief?: Partial<BriefCfg>; watchdog?: Partial<WatchdogCfg> }) => {
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
        <span className="w-9 h-9 rounded-xl bg-primary/10 text-primary grid place-items-center flex-shrink-0 text-base">✦</span>
        <div className="min-w-0">
          <h3 className="text-sm font-semibold text-ink-primary">Tekly Sense</h3>
          <p className="text-[11px] text-ink-tertiary">Learns from your own jobs. Suggests, never acts, and every hint shows why. Only you see these settings.</p>
        </div>
        <Toggle on={s.enabled} label="Tekly Sense" busy={busy} onClick={() => save({ enabled: !s.enabled })} />
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
              <div className="pt-2 text-[11px] font-semibold text-ink-tertiary">{g.group}</div>
              {g.rows.map((r) => (
                <div key={r.k} className="flex items-center gap-3 py-2.5 border-t border-slate-100 first:border-0">
                  <span className="min-w-0"><span className="block text-sm text-ink-primary">{r.label}</span><span className="block text-[11px] text-ink-tertiary">{r.hint}</span></span>
                  <Toggle on={s.features[r.k]} label={r.label} busy={busy} disabled={!s.enabled} onClick={() => save({ features: { [r.k]: !s.features[r.k] } })} />
                </div>
              )).flatMap((el, i) => g.rows[i].k === "morning_brief" && s.enabled && s.features.morning_brief
                ? [el, <BriefControls key="brief" s={s} busy={busy} save={save} />]
                : g.rows[i].k === "watchdog" && s.enabled && s.features.watchdog
                  ? [el, <WatchdogControls key="watchdog" s={s} busy={busy} save={save} />] : [el])}
            </div>
          ))}
        </div>
        <p className="text-[11px] text-ink-tertiary">Staff see suggestions when their role has "See Tekly Sense suggestions on jobs" (Settings → Team).</p>
        {err && <p className="text-[11px] font-semibold text-status-overdue-ink">{err}</p>}
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
