import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { M } from "../../components/Shell";
import { api } from "../../api/client";
import { useMe } from "../../context/Me";
import { money } from "../orders/types";
import { tint, hueText } from "../../lib/tint";
import { loadFailed } from "../../lib/notice";

// Memberships (docs/SPEC_memberships.md): the pieces every screen shares — the status pill, the
// member card on a client or an asset, and the one-step "Add a plan" dialog.

export type Item = { id: string; kind: "service" | "discount" | "perk"; text: string; left?: number; of?: number; window?: [string, string] };
export type Member = {
  id: string; plan_id: string; plan: string; color: string; client_id: string; client: string;
  device_id?: string | null; asset: string; billing: "monthly" | "yearly"; status: string; renews: boolean;
  renews_on: string; starts_on: string; period_end: string; ended_on?: string | null; ended_reason?: string | null;
  manager_id?: string | null; unpaid: boolean; price?: number; owed?: number; items?: Item[]; saved?: number;
  attention?: { kind: "unpaid" | "window"; text: string } | null;
  value_given?: number; paid?: number; refund_if_cancelled_now?: number;
  periods?: { id: string; start: string; end: string; amount: number; paid: boolean; invoice_id?: string | null; order_id?: string | null; due_on: string }[];
};
export type PlanItem = { id?: string; kind: "service" | "discount" | "perk"; service_id?: string | null; label: string; service_name?: string | null;
  qty: number; pct: number; applies_to: "labour" | "parts" | "both"; window_start?: string | null; window_end?: string | null; rollover: boolean; text?: string };
export type Plan = {
  id: string; name: string; description: string; template_key?: string | null; asset_group_ids: string[];
  price_monthly?: number | null; price_yearly?: number | null; term: "rolling" | "season"; season_start?: string | null;
  season_end?: string | null; presale_days: number; late_join: "prorated" | "full"; color: string; active: boolean;
  members: number; items: PlanItem[]; value_yearly?: number;
};

export const fmtDate = (iso?: string | null) => {
  if (!iso) return "";
  const d = new Date(`${iso}T00:00:00`);
  return d.toLocaleDateString(undefined, { day: "numeric", month: "short", year: d.getFullYear() === new Date().getFullYear() ? undefined : "numeric" });
};
const dayBefore = (iso: string) => { const d = new Date(`${iso}T00:00:00`); d.setDate(d.getDate() - 1); return d.toISOString().slice(0, 10); };

export function priceLine(p: { price_monthly?: number | null; price_yearly?: number | null }) {
  return [p.price_monthly ? `${money(p.price_monthly)}/mo` : "", p.price_yearly ? `${money(p.price_yearly)}/yr` : ""].filter(Boolean).join(" · ");
}

const STATUS: Record<string, { label: string; hue: string }> = {
  active: { label: "Active", hue: "#16a34a" }, scheduled: { label: "Starts soon", hue: "#2563eb" },
  paused: { label: "Paused · unpaid", hue: "#d97706" }, lapsed: { label: "Ended · unpaid", hue: "#64748b" },
  cancelled: { label: "Cancelled", hue: "#64748b" },
};
export function StatusPill({ m }: { m: Pick<Member, "status" | "renews" | "starts_on"> }) {
  const s = STATUS[m.status] || STATUS.active;
  const label = m.status === "scheduled" ? `Starts ${fmtDate(m.starts_on)}` : m.status === "active" && !m.renews ? "Ends at term end" : s.label;
  return <span className="inline-flex items-center px-2 py-0.5 rounded-full text-[11px] font-semibold whitespace-nowrap" style={{ background: tint(s.hue), color: hueText(s.hue) }}>{label}</span>;
}

/** A thin ring for what's left of an included service, with the count inside (same look as the
    work-order checklist rings). */
export function Ring({ left, of, color, size = 34 }: { left: number; of: number; color: string; size?: number }) {
  const sw = 3, r = (size - sw) / 2, len = 2 * Math.PI * r;
  const pct = of > 0 ? Math.max(0, Math.min(1, left / of)) : 0;
  return (
    <span className="relative grid place-items-center shrink-0" style={{ width: size, height: size }} role="img" aria-label={`${left} of ${of} left`}>
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} className="-rotate-90" aria-hidden>
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" strokeWidth={sw} className="stroke-slate-200" />
        {pct > 0 && <circle cx={size / 2} cy={size / 2} r={r} fill="none" strokeWidth={sw} strokeLinecap="round" stroke={color}
          strokeDasharray={len} strokeDashoffset={len * (1 - pct)} />}
      </svg>
      <span className="absolute text-[11px] font-semibold tabular-nums text-ink-primary">{left}</span>
    </span>
  );
}

function ServiceRing({ it, color }: { it: Item; color: string }) {
  const of = Math.max(0, Math.round(it.of ?? 0)), left = Math.max(0, Math.round(it.left ?? 0));
  return (
    <div className="flex items-center gap-2.5 min-w-0">
      <Ring left={left} of={of} color={color} />
      <div className="min-w-0">
        <div className="text-xs font-semibold text-ink-primary truncate">{it.text.replace(/ × \d+$/, "")}</div>
        <div className="text-[11px] text-ink-tertiary">{left === 0 ? "none left" : `${left} of ${of} left`}
          {it.window && left > 0 ? ` · book ${fmtDate(it.window[0])}–${fmtDate(it.window[1])}` : ""}</div>
      </div>
    </div>
  );
}

export function MemberCard({ m, onOpen }: { m: Member; onOpen?: () => void }) {
  const services = (m.items || []).filter((i) => i.kind === "service");
  const extras = (m.items || []).filter((i) => i.kind !== "service");
  const sub = [m.asset, m.billing === "monthly" ? "Monthly" : "Yearly",
    m.status === "scheduled" ? "" : !m.renews ? `ends ${fmtDate(dayBefore(m.renews_on))}`
      : m.billing === "monthly" ? `next payment ${fmtDate(m.period_end)}` : `renews ${fmtDate(m.renews_on)}`].filter(Boolean).join(" · ");
  const Tag = onOpen ? "button" : "div";
  return (
    <Tag type={onOpen ? "button" : undefined} onClick={onOpen}
      className={`block w-full text-left rounded-2xl border border-slate-200/90 bg-white shadow-clay-sm overflow-hidden ${onOpen ? "hover:border-slate-300 transition" : ""}`}>
      <div className="flex items-start gap-3 px-4 py-3" style={{ background: tint(m.color, 10) }}>
        <div className="h-10 w-10 shrink-0 rounded-xl grid place-items-center bg-white/70" style={{ color: hueText(m.color) }}><M n="workspace_premium" c="text-[20px]" /></div>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2"><span className="text-sm font-semibold text-ink-primary">{m.plan}</span><StatusPill m={m} /></div>
          <div className="text-xs text-ink-secondary leading-snug">{sub}</div>
        </div>
        {m.owed ? <span className="shrink-0 rounded-full bg-white/80 px-2 py-0.5 text-[11px] font-semibold text-amber-700 tabular-nums">{money(m.owed)} due</span> : null}
      </div>
      {(services.length > 0 || extras.length > 0) && (
        <div className="px-4 py-3 space-y-3">
          {services.length > 0 && <div className="grid gap-3 sm:grid-cols-2">{services.map((it) => <ServiceRing key={it.id} it={it} color={m.color} />)}</div>}
          {extras.length > 0 && <div className="flex flex-wrap gap-1.5">{extras.map((it) => (
            <span key={it.id} className="inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium" style={{ background: tint(m.color, 10), color: hueText(m.color) }}>
              <M n={it.kind === "discount" ? "redeem" : "verified"} c="text-[12px]" />{it.text}</span>))}</div>}
        </div>
      )}
      {m.saved ? <div className="border-t border-slate-100 px-4 py-2 text-[11px] text-emerald-700 font-semibold">Saved {money(m.saved)} this plan year</div> : null}
    </Tag>
  );
}

/** A client's or an asset's plans: live cards, a quiet "former member" line, and Add a plan. */
export function MembershipsCard({ clientId, deviceId, devices, refreshKey = 0, onOpen }: {
  clientId: string; deviceId?: string; devices?: { id: string; brand?: string; model?: string }[];
  refreshKey?: number; onOpen?: (id: string) => void;
}) {
  const { can } = useMe();
  const [data, setData] = useState<{ live: Member[]; former: Member[] } | null>(null);
  const [sell, setSell] = useState(false);
  const [n, setN] = useState(0);
  useEffect(() => {
    const q = deviceId ? `device_id=${deviceId}` : `client_id=${clientId}`;
    api.get<{ live: Member[]; former: Member[] }>(`/memberships/for?${q}`).then(setData).catch(() => setData({ live: [], former: [] }));
  }, [clientId, deviceId, refreshKey, n]);
  if (!data) return null;
  const canSell = can("can_sell_memberships");
  if (!data.live.length && !data.former.length && !canSell) return null;
  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <div className="text-[11px] font-bold text-ink-tertiary">Membership</div>
        {canSell && data.live.length > 0 && <button onClick={() => setSell(true)} className="text-xs font-semibold text-primary">+ Add a plan</button>}
      </div>
      {data.live.map((m) => <MemberCard key={m.id} m={m} onOpen={onOpen ? () => onOpen(m.id) : undefined} />)}
      {!data.live.length && canSell && (
        <button onClick={() => setSell(true)} className="w-full rounded-2xl border border-dashed border-slate-300 bg-white/60 p-4 text-left hover:border-primary/50 transition flex items-center gap-3">
          <div className="h-9 w-9 rounded-xl grid place-items-center bg-primary/10 text-primary"><M n="workspace_premium" c="text-[18px]" /></div>
          <div><div className="text-sm font-semibold text-ink-primary">Add a membership plan</div>
            <div className="text-xs text-ink-tertiary">Included services, member discounts and renewal reminders.</div></div>
        </button>
      )}
      {data.former.map((m) => (
        <div key={m.id} className="flex items-center gap-2 px-1 text-xs text-ink-tertiary">
          <M n="history" c="text-sm" />Former {m.plan} member · ended {fmtDate(m.ended_on)}{m.ended_reason === "unpaid" ? " (unpaid)" : ""}
          {canSell && !data.live.some((x) => x.plan_id === m.plan_id) && <button onClick={() => setSell(true)} className="ml-auto font-semibold text-primary">Re-join</button>}
        </div>
      ))}
      {sell && <SellPlanDialog clientId={clientId} deviceId={deviceId || data.former[0]?.device_id || undefined} devices={devices}
        onClose={() => setSell(false)} onDone={() => { setSell(false); setN((x) => x + 1); }} />}
    </div>
  );
}

/** Add a plan: pick it, monthly or yearly, which asset. One screen, one button. */
export function SellPlanDialog({ clientId, deviceId, devices, orderId, presetPlanId, onClose, onDone }: {
  clientId: string; deviceId?: string; devices?: { id: string; brand?: string; model?: string }[]; orderId?: string;
  presetPlanId?: string; onClose: () => void; onDone: (m: Member & { invoice_id?: string | null }) => void;
}) {
  const [plans, setPlans] = useState<Plan[] | null>(null);
  const [assets, setAssets] = useState(devices || []);
  const [planId, setPlanId] = useState(presetPlanId || "");
  const [billing, setBilling] = useState<"monthly" | "yearly">("yearly");
  const [device, setDevice] = useState(deviceId || "");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    api.get<{ items: Plan[] }>("/memberships/plans").then((r) => {
      const live = (r.items || []).filter((p) => p.active);
      setPlans(live);
      if (!planId && live[0]) setPlanId(live[0].id);
    }).catch(() => setPlans([]));
    if (!devices) api.get<any>(`/clients/${clientId}`).then((c) => {
      setAssets(c.devices || []);
      if (!deviceId && c.devices?.length === 1) setDevice(c.devices[0].id);
    }).catch(loadFailed("the client's devices"));
  }, [clientId]);   // eslint-disable-line react-hooks/exhaustive-deps
  const plan = plans?.find((p) => p.id === planId);
  useEffect(() => { if (plan && !plan[billing === "yearly" ? "price_yearly" : "price_monthly"]) setBilling(plan.price_yearly ? "yearly" : "monthly"); }, [plan]);  // eslint-disable-line react-hooks/exhaustive-deps
  const price = plan ? (billing === "yearly" ? plan.price_yearly : plan.price_monthly) : null;

  async function save() {
    if (!plan) return;
    setBusy(true); setErr(null);
    try {
      const m = await api.post<Member & { invoice_id?: string | null }>("/memberships", {
        plan_id: plan.id, client_id: clientId, device_id: device || null, billing, order_id: orderId || null });
      onDone(m);
    } catch (e: any) { setErr(e?.message || "Couldn't add the plan"); setBusy(false); }
  }

  // Portalled to <body> so it sits above whatever opened it (the payment dialog, a drawer).
  return createPortal(
    <div className="fixed inset-0 z-[90] grid place-items-center p-4">
      <div className="absolute inset-0 bg-ink-primary/35 backdrop-blur-[2px]" aria-hidden="true" onClick={onClose} />
      <div role="dialog" aria-label="Add a membership plan" className="relative w-full max-w-md rounded-2xl bg-white shadow-clay-card border border-slate-200/90 p-5">
        <div className="flex items-center gap-2">
          <div className="text-base font-semibold text-ink-primary">Add a membership plan</div>
          <button aria-label="Close" onClick={onClose} className="ml-auto h-8 w-8 rounded-full grid place-items-center text-ink-tertiary hover:bg-slate-100"><M n="close" c="text-lg" /></button>
        </div>
        {plans === null ? <p className="mt-4 text-xs text-ink-tertiary">Loading…</p>
          : plans.length === 0 ? <p className="mt-4 text-sm text-ink-secondary">No plans yet. Set one up in Catalog › Plans.</p> : (<>
          <div className="mt-4 space-y-2">
            {plans.map((p) => {
              const on = p.id === planId;
              return (
                <button key={p.id} type="button" onClick={() => setPlanId(p.id)} aria-pressed={on}
                  className={`w-full text-left rounded-xl border p-3 transition flex items-start gap-3 ${on ? "border-primary ring-2 ring-primary/15" : "border-slate-200 hover:border-slate-300"}`}>
                  <span className="h-9 w-9 shrink-0 rounded-lg grid place-items-center" style={{ background: tint(p.color, 14), color: hueText(p.color) }}><M n="workspace_premium" c="text-[18px]" /></span>
                  <span className="min-w-0 flex-1">
                    <span className="flex items-baseline gap-2"><span className="text-sm font-semibold text-ink-primary">{p.name}</span>
                      <span className="ml-auto text-xs font-semibold text-ink-primary tabular-nums">{priceLine(p)}</span></span>
                    <span className="block mt-0.5 text-xs text-ink-tertiary line-clamp-2">{p.items.map((i) => i.text).join(" · ")}</span>
                    {p.value_yearly ? <span className="block mt-1 text-[11px] font-semibold text-emerald-700">Worth {money(p.value_yearly)} a year in services</span> : null}
                  </span>
                </button>
              );
            })}
          </div>
          {plan && plan.price_monthly && plan.price_yearly ? (
            <div className="mt-4 grid grid-cols-2 gap-1 rounded-xl bg-slate-100 p-1 text-xs font-semibold">
              {(["monthly", "yearly"] as const).map((b) => (
                <button key={b} type="button" onClick={() => setBilling(b)} aria-pressed={billing === b}
                  className={`h-9 rounded-lg transition ${billing === b ? "bg-white shadow-sm text-ink-primary" : "text-ink-tertiary"}`}>
                  {b === "monthly" ? `${money(plan.price_monthly!)} a month`
                    : `${money(plan.price_yearly!)} a year${plan.price_monthly! * 12 > plan.price_yearly! ? ` · save ${money(plan.price_monthly! * 12 - plan.price_yearly!)}` : ""}`}
                </button>
              ))}
            </div>
          ) : null}
          {!deviceId && (
            <div className="mt-4">
              <label htmlFor="sell-asset" className="block text-[11px] font-bold text-ink-tertiary mb-1">Covers</label>
              <select id="sell-asset" value={device} onChange={(e) => setDevice(e.target.value)} className="glass-input w-full h-10 rounded-xl px-3 text-sm">
                <option value="">Choose the boat, vehicle or device…</option>
                {assets.map((d) => <option key={d.id} value={d.id}>{[d.brand, d.model].filter(Boolean).join(" ") || "Asset"}</option>)}
              </select>
            </div>
          )}
          {err && <p role="alert" className="mt-3 rounded-lg bg-rose-50 px-3 py-2 text-xs text-rose-700">{err}</p>}
          <button onClick={save} disabled={busy || !plan || (!deviceId && !device)}
            className="mt-5 h-11 w-full rounded-xl bg-primary text-white text-sm font-semibold shadow-clay-sm disabled:opacity-50">
            {busy ? "Adding…" : plan && price ? `Add ${plan.name} · ${money(price)}${billing === "yearly" ? "/yr" : "/mo"}` : "Add plan"}
          </button>
          <p className="mt-2 text-center text-[11px] text-ink-tertiary">{orderId ? "Added to this job's bill." : "An invoice is created for the first payment."}</p>
        </>)}
      </div>
    </div>,
    document.body,
  );
}
