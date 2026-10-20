import { useEffect, useState } from "react";
import { M } from "../Shell";
import { api } from "../../api/client";
import { useMe } from "../../context/Me";
import { money } from "../../pages/orders/types";

/* Tekly Sense — quiet suggestions learned from the shop's own history (no AI service).
   Everything here suggests and explains; nothing changes a job on its own. Styling is
   deliberately plain (slate chip, one sapphire ✦) so it reads as part of the app, not a robot. */

export type SenseFeature = "job_suggestions" | "deferred" | "ready_time_why" | "margin_check";
export type SenseSettings = {
  enabled: boolean;
  features: Record<SenseFeature, boolean>;
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
    { k: "deferred", label: "Work deferred last time", hint: "What the customer said \"not now\" to on an earlier visit" },
    { k: "ready_time_why", label: "Explain the ready-time estimate", hint: "Shows what the estimate is based on" },
  ] },
  { group: "For you", rows: [
    { k: "margin_check", label: "Margin check", hint: "A note when a job makes far less than your usual margin" },
  ] },
];

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
  const save = async (patch: { enabled?: boolean; features?: Partial<Record<SenseFeature, boolean>> }) => {
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
              ))}
            </div>
          ))}
        </div>
        <p className="text-[11px] text-ink-tertiary">Staff see suggestions when their role has "See Tekly Sense suggestions on jobs" (Settings → Team).</p>
        {err && <p className="text-[11px] font-semibold text-status-overdue-ink">{err}</p>}
      </div>
    </section>
  );
}
