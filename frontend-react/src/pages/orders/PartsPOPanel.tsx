import { useEffect, useState } from "react";
import { M } from "../../components/Shell";
import { api } from "../../api/client";
import { loadFailed } from "../../lib/notice";

// ADR-144po — everything about this job's parts in one place, opened from the work order's
// ⋯ menu (or the "Waiting on parts" chip): what the job still owes from stock, the purchase
// orders linked to it, the stock held for it, and the actions to order or hold more.
// Settings → Inventory decides whether arriving parts are written off to the job by the server
// ("automatic") or left for staff ("manual", the Write off buttons here).
type POLineView = { part: string; ordered: number; received: number; outstanding: number };
type LinkedPO = {
  po_id: string; po_number: string; status: string; supplier?: string | null; raised_here: boolean;
  ordered_at?: string | null; expected_at?: string | null; received_at?: string | null; lines: POLineView[];
};
type Hold = { id: string; part: string; qty: number; expires_at?: string | null };
type JobLine = { id: string; kind?: string; description?: string; qty?: number; pending_stock?: number };

const STATUS: Record<string, [string, string]> = {
  draft: ["Draft", "bg-slate-100 text-ink-secondary"],
  ordered: ["Ordered", "bg-blue-50 text-primary"],
  partial: ["Partly arrived", "bg-amber-50 text-amber-700"],
  received: ["Arrived", "bg-emerald-50 text-emerald-700"],
  closed: ["Closed", "bg-slate-100 text-ink-secondary"],
  cancelled: ["Cancelled", "bg-rose-50 text-rose-700"],
};
const OPEN = ["draft", "ordered", "partial"];
const day = (iso?: string | null) => (iso ? new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric" }) : "");
const daysUntil = (iso: string) => Math.round((new Date(iso).setHours(0, 0, 0, 0) - new Date().setHours(0, 0, 0, 0)) / 86400000);
const HOLD_PRESETS = [7, 14, 30];
const n = (x: number) => (Number.isInteger(x) ? String(x) : x.toFixed(2));

function Eta({ po }: { po: LinkedPO }) {
  if (!OPEN.includes(po.status) || !po.expected_at) return null;
  const d = daysUntil(po.expected_at);
  const [txt, tone] = d < 0 ? [`${-d} day${d === -1 ? "" : "s"} late`, "text-rose-700 bg-rose-50"]
    : d === 0 ? ["Due today", "text-amber-800 bg-amber-50"]
    : d === 1 ? ["Due tomorrow", "text-primary bg-blue-50"]
    : [`Due in ${d} days`, "text-ink-secondary bg-slate-100"];
  return <span className={`text-[11px] font-semibold px-1.5 py-0.5 rounded-md tabular-nums ${tone}`}>{txt}</span>;
}

// Draft → Ordered → Arrived, with the date under each step that happened.
function Track({ po }: { po: LinkedPO }) {
  if (po.status === "cancelled") return <div className="text-[11px] text-rose-700 flex items-center gap-1"><M n="block" c="text-sm" />Cancelled, so these parts aren't coming</div>;
  const steps: [string, boolean, string][] = [
    ["Raised", true, ""],
    ["Ordered", po.status !== "draft", day(po.ordered_at)],
    [po.status === "partial" ? "Partly in" : "Arrived", ["partial", "received", "closed"].includes(po.status), day(po.received_at) || (po.expected_at ? `exp. ${day(po.expected_at)}` : "")],
  ];
  return (
    <div className="flex items-start">
      {steps.map(([label, done, when], i) => (
        <div key={label} className="flex-1 min-w-0">
          <div className="flex items-center">
            <span className={`w-2.5 h-2.5 rounded-full flex-shrink-0 ${done ? (i === 2 && po.status === "partial" ? "bg-amber-500" : "bg-primary") : "bg-white border-2 border-slate-300"}`} />
            {i < steps.length - 1 && <span className={`h-0.5 flex-1 mx-1 rounded ${steps[i + 1][1] ? "bg-primary" : "bg-slate-200"}`} />}
          </div>
          <div className={`text-[11px] mt-1 ${done ? "text-ink-primary" : "text-ink-tertiary"}`}>{label}</div>
          {when && <div className="text-[11px] text-ink-tertiary tabular-nums">{when}</div>}
        </div>
      ))}
    </div>
  );
}

export default function PartsPOPanel({ orderId, lines, canBuy, isEstimate, onClose, onChanged }: {
  orderId: string; lines: JobLine[]; canBuy: boolean; isEstimate: boolean; onClose: () => void; onChanged: () => void;
}) {
  const [pos, setPos] = useState<LinkedPO[] | null>(null);
  const [holds, setHolds] = useState<Hold[]>([]);
  const [auto, setAuto] = useState(true);
  const [suppliers, setSuppliers] = useState<{ id: string; name: string }[]>([]);
  const [supplier, setSupplier] = useState("");
  const [onlyShort, setOnlyShort] = useState(true);
  const [autoSend, setAutoSend] = useState(false);
  const [holdDays, setHoldDays] = useState(7);
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<{ text: string; bad?: boolean } | null>(null);

  const load = () => {
    api.get<any>(`/orders/${orderId}/purchase-orders`).then((r) => setPos(r.items || [])).catch(() => setPos([]));
    if (canBuy) api.get<any>(`/orders/${orderId}/reservations`).then((r) => setHolds(r.items || [])).catch(() => setHolds([]));
  };
  useEffect(() => { load(); /* eslint-disable-next-line */ }, [orderId, canBuy]);
  useEffect(() => { api.get<any>("/staff/inventory-settings").then((r) => setAuto((r?.po_to_job || "auto") !== "manual")).catch(() => { /* silent: defaults to auto */ }); }, []);
  useEffect(() => { if (canBuy && !isEstimate) api.get<any>("/suppliers").then((r) => setSuppliers(r.items || r || [])).catch(loadFailed("suppliers")); }, [canBuy, isEstimate]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey); return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const changed = () => { load(); onChanged(); };
  const run = async (key: string, fn: () => Promise<string>) => {
    setBusy(key); setMsg(null);
    try { setMsg({ text: await fn() }); changed(); }
    catch (e: any) { setMsg({ text: e?.message || "Something went wrong", bad: true }); }
    finally { setBusy(null); }
  };
  const raisePO = () => {
    if (!supplier) { setMsg({ text: "Pick a supplier first", bad: true }); return; }
    run("po", async () => {
      const r = await api.post<any>(`/orders/${orderId}/create-po`, { supplier_id: supplier, only_shortfall: onlyShort, auto_send: autoSend });
      return r.line_count ? `${r.po_number} raised for ${r.line_count} part${r.line_count === 1 ? "" : "s"}${r.sent ? " and sent to the supplier" : ""}` : "Nothing is short of stock";
    });
  };
  const reserve = () => run("hold", async () => {
    const r = await api.post<any>(`/orders/${orderId}/reserve-stock`, { hold_days: holdDays });
    const held = (r.reserved || []).reduce((s: number, x: any) => s + (x.qty || 0), 0);
    return held ? `Holding ${held} unit${held === 1 ? "" : "s"} for ${r.hold_days} days${(r.short || []).length ? ". Some parts are short, so order those" : ""}` : "Nothing in stock to hold. Order the parts instead";
  });
  const writeOff = (l: JobLine) => run(`w${l.id}`, async () => {
    const r = await api.post<any>(`/orders/${orderId}/lines/${l.id}/receive-stock`, {});
    return r.pending ? `Wrote off what's in stock. ${n(r.pending)} still owed` : `${l.description || "Part"} written off to this job`;
  });
  const release = (id: string) => run(`r${id}`, async () => { await api.post(`/inventory/reservations/${id}/release`, {}); return "Hold released"; });

  const owed = lines.filter((l) => l.kind === "product" && (l.pending_stock || 0) > 0);
  const open = (pos || []).filter((p) => OPEN.includes(p.status));
  const done = (pos || []).filter((p) => !OPEN.includes(p.status));
  const toCome = open.reduce((s, p) => s + p.lines.reduce((a, l) => a + l.outstanding, 0), 0);
  const arrived = (pos || []).reduce((s, p) => s + p.lines.reduce((a, l) => a + l.received, 0), 0);
  const late = open.some((p) => p.expected_at && daysUntil(p.expected_at) < 0);

  const card = (p: LinkedPO) => {
    const [label, tone] = STATUS[p.status] || [p.status, "bg-slate-100 text-ink-secondary"];
    return (
      <div key={p.po_id} className={`rounded-2xl border bg-white p-4 space-y-3.5 ${p.status === "cancelled" ? "border-slate-200/70 opacity-75" : "border-slate-200/90 shadow-sm"}`}>
        <div className="flex items-center gap-3">
          <span className="w-9 h-9 rounded-xl bg-slate-100 text-ink-secondary grid place-items-center text-xs font-semibold flex-shrink-0">{(p.supplier || "?").slice(0, 2).toUpperCase()}</span>
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              <span className="text-sm font-semibold text-ink-primary tabular-nums">{p.po_number}</span>
              <span className={`text-[11px] font-semibold px-1.5 py-0.5 rounded-md ${tone}`}>{label}</span>
            </div>
            <div className="text-[11px] text-ink-tertiary truncate">{p.supplier || "Supplier"}{p.raised_here ? "" : " · shared with other jobs"}</div>
          </div>
          <Eta po={p} />
        </div>
        <Track po={p} />
        <div className="rounded-xl bg-slate-50/80 border border-slate-100 divide-y divide-slate-100">
          {p.lines.map((l, i) => {
            const pct = l.ordered ? Math.min(100, (l.received / l.ordered) * 100) : 0;
            return (
              <div key={i} className="px-3 py-2.5">
                <div className="flex items-baseline gap-2 text-xs">
                  <span className="text-ink-primary truncate flex-1">{l.part}</span>
                  <span className="tabular-nums text-ink-secondary">{n(l.received)}<span className="text-ink-tertiary"> / {n(l.ordered)}</span></span>
                </div>
                <div className="mt-1.5 flex items-center gap-2">
                  <div className="flex-1 h-1.5 rounded-full bg-slate-200/70 overflow-hidden">
                    <div className={`h-full rounded-full transition-all ${pct >= 100 ? "bg-emerald-500" : "bg-primary"}`} style={{ width: `${pct}%` }} />
                  </div>
                  <span className={`text-[11px] tabular-nums w-16 text-right ${l.outstanding > 0 && p.status !== "cancelled" ? "text-amber-700" : "text-emerald-700"}`}>
                    {p.status === "cancelled" ? "" : l.outstanding > 0 ? `${n(l.outstanding)} to come` : "All in"}
                  </span>
                </div>
              </div>
            );
          })}
        </div>
      </div>
    );
  };

  const Stat = ({ v, l, tone }: { v: string; l: string; tone: string }) => (
    <div className="flex-1 min-w-0 px-3 py-2.5">
      <div className={`text-lg font-semibold tabular-nums leading-tight ${tone}`}>{v}</div>
      <div className="text-[11px] text-ink-tertiary">{l}</div>
    </div>
  );

  return (
    <div className="absolute inset-0 z-30 flex justify-end">
      <div className="absolute inset-0 bg-ink-primary/20 backdrop-blur-[1px]" aria-hidden="true" onClick={onClose} />
      <div role="dialog" aria-label="Parts and purchase orders" className="relative w-[480px] max-w-full h-full bg-slate-50 border-l border-slate-200/90 shadow-clay-drawer flex flex-col animate-[panelIn_.24s_cubic-bezier(.16,1,.3,1)]">
        <style>{`@keyframes panelIn{from{transform:translateX(24px);opacity:0}to{transform:none;opacity:1}}`}</style>
        <div className="px-5 pt-4 pb-3 bg-white border-b border-slate-200/90">
          <div className="flex items-center gap-3">
            <span className="w-9 h-9 rounded-xl bg-primary/10 text-primary grid place-items-center"><M n="local_shipping" c="text-lg" /></span>
            <div className="flex-1 min-w-0">
              <div className="text-sm font-semibold text-ink-primary">Parts &amp; purchase orders</div>
              <div className="text-[11px] text-ink-tertiary flex items-center gap-1">
                <M n={auto ? "bolt" : "back_hand"} c="text-xs" />
                {auto ? "Parts that arrive are written off to this job automatically" : "Parts that arrive wait for you to write them off"}
              </div>
            </div>
            <button aria-label="Close" onClick={onClose} className="w-8 h-8 rounded-lg hover:bg-slate-100 grid place-items-center text-ink-tertiary"><M n="close" c="text-base" /></button>
          </div>
          <div className="mt-3 flex divide-x divide-slate-100 rounded-xl border border-slate-200/80 bg-slate-50/60">
            <Stat v={n(toCome)} l={late ? "To come · late" : "To come"} tone={late ? "text-rose-700" : toCome ? "text-primary" : "text-ink-primary"} />
            <Stat v={n(arrived)} l="Arrived" tone="text-emerald-700" />
            <Stat v={n(owed.reduce((s, l) => s + (l.pending_stock || 0), 0))} l="Owed to job" tone={owed.length ? "text-amber-700" : "text-ink-primary"} />
          </div>
        </div>

        <div className="flex-1 overflow-y-auto p-5 space-y-6">
          {msg && <div className={`rounded-xl text-xs px-3 py-2 border ${msg.bad ? "bg-rose-50 border-rose-100 text-rose-700" : "bg-emerald-50 border-emerald-100 text-emerald-800"}`}>{msg.text}</div>}

          {owed.length > 0 && (
            <section className="space-y-2">
              <h4 className="text-xs font-semibold text-ink-primary">Owed to this job <span className="font-normal text-ink-tertiary">· used on the job, not yet taken from stock</span></h4>
              <div className="rounded-2xl border border-amber-200/80 bg-white divide-y divide-slate-100">
                {owed.map((l) => (
                  <div key={l.id} className="flex items-center gap-3 px-3.5 py-2.5">
                    <span className="w-7 h-7 rounded-lg bg-amber-50 text-amber-700 grid place-items-center flex-shrink-0"><M n="inventory_2" c="text-sm" /></span>
                    <span className="text-xs text-ink-primary truncate flex-1">{l.description || "Part"}</span>
                    <span className="text-xs tabular-nums text-amber-700">{n(l.pending_stock || 0)} owed</span>
                    {canBuy && <button type="button" onClick={() => writeOff(l)} disabled={busy !== null}
                      className="px-2.5 py-1.5 rounded-lg border border-slate-200 text-[11px] font-semibold text-ink-primary hover:bg-slate-50 disabled:opacity-50">{busy === `w${l.id}` ? "Writing off…" : "Write off"}</button>}
                  </div>
                ))}
              </div>
            </section>
          )}

          <section className="space-y-2.5">
            <h4 className="text-xs font-semibold text-ink-primary">On order</h4>
            {pos === null ? <div className="h-24 rounded-2xl bg-white border border-slate-200/80 animate-pulse" />
              : open.length ? open.map(card)
              : (
                <div className="rounded-2xl border border-dashed border-slate-200 bg-white px-4 py-6 text-center">
                  <M n="inventory" c="text-2xl text-ink-tertiary" />
                  <div className="text-xs text-ink-secondary mt-1">Nothing on order for this job</div>
                  {canBuy && !isEstimate && <div className="text-[11px] text-ink-tertiary">Raise a PO below when a part is short.</div>}
                </div>
              )}
          </section>

          {done.length > 0 && (
            <section className="space-y-2.5">
              <h4 className="text-xs font-semibold text-ink-primary">Arrived or closed</h4>
              {done.map(card)}
            </section>
          )}

          {canBuy && (holds.length > 0 || !isEstimate) && (
            <section className="space-y-2">
              <h4 className="text-xs font-semibold text-ink-primary">Held for this job</h4>
              {holds.length ? (
                <div className="rounded-2xl border border-slate-200/90 bg-white divide-y divide-slate-100">
                  {holds.map((h) => (
                    <div key={h.id} className="flex items-center gap-3 px-3.5 py-2.5 text-xs">
                      <span className="w-7 h-7 rounded-lg bg-slate-100 text-ink-secondary grid place-items-center flex-shrink-0"><M n="lock_clock" c="text-sm" /></span>
                      <span className="text-ink-primary truncate flex-1">{h.part}</span>
                      <span className="tabular-nums text-ink-secondary">×{n(h.qty)}</span>
                      {h.expires_at && <span className="text-[11px] text-ink-tertiary">until {day(h.expires_at)}</span>}
                      <button type="button" onClick={() => release(h.id)} disabled={busy !== null} className="text-[11px] font-semibold text-ink-secondary hover:text-rose-600 disabled:opacity-50">Release</button>
                    </div>
                  ))}
                </div>
              ) : <div className="text-xs text-ink-tertiary">No stock is held for this job.</div>}
            </section>
          )}

          {canBuy && !isEstimate && (
            <section className="rounded-2xl border border-slate-200/90 bg-white shadow-sm overflow-hidden">
              <div className="p-4 space-y-2.5">
                <div className="flex items-center gap-2 text-xs font-semibold text-ink-primary"><M n="add_shopping_cart" c="text-sm text-primary" />Order parts from a supplier</div>
                <div className="flex items-center gap-2">
                  <select value={supplier} onChange={(e) => { setSupplier(e.target.value); setMsg(null); }} aria-label="Supplier"
                    className="glass-input rounded-lg px-2.5 py-2 text-xs text-ink-primary flex-1 min-w-0">
                    <option value="">Choose a supplier</option>
                    {suppliers.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
                  </select>
                  <button type="button" onClick={raisePO} disabled={busy !== null} className="px-3.5 py-2 rounded-lg bg-primary text-white text-xs font-semibold hover:bg-primary-container disabled:opacity-50">{busy === "po" ? "Raising…" : "Raise PO"}</button>
                </div>
                <div className="flex flex-wrap gap-x-4 gap-y-1">
                  <label className="flex items-center gap-1.5 text-[11px] text-ink-secondary cursor-pointer"><input type="checkbox" checked={onlyShort} onChange={(e) => setOnlyShort(e.target.checked)} />Only what's short</label>
                  <label className="flex items-center gap-1.5 text-[11px] text-ink-secondary cursor-pointer"><input type="checkbox" checked={autoSend} onChange={(e) => setAutoSend(e.target.checked)} />Mark ordered and tell the supplier</label>
                </div>
              </div>
              <div className="p-4 space-y-2.5 border-t border-slate-100 bg-slate-50/50">
                <div className="flex items-center gap-2 text-xs font-semibold text-ink-primary"><M n="lock_clock" c="text-sm text-primary" />Hold parts already in stock</div>
                <div className="flex items-center gap-1.5 flex-wrap">
                  {HOLD_PRESETS.map((d) => (
                    <button key={d} type="button" onClick={() => setHoldDays(d)} aria-pressed={holdDays === d}
                      className={`px-2.5 py-1 rounded-full text-[11px] font-semibold border ${holdDays === d ? "bg-blue-50 text-primary border-blue-200" : "bg-white text-ink-secondary border-slate-200 hover:bg-slate-50"}`}>{d} days</button>
                  ))}
                  <button type="button" onClick={reserve} disabled={busy !== null} className="ml-auto px-3.5 py-2 rounded-lg bg-white border border-slate-200 text-ink-primary text-xs font-semibold hover:bg-slate-50 disabled:opacity-50">{busy === "hold" ? "Holding…" : "Hold stock"}</button>
                </div>
              </div>
            </section>
          )}
        </div>
      </div>
    </div>
  );
}
