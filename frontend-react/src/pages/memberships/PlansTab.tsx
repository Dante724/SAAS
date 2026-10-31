import { useEffect, useRef, useState } from "react";
import { M } from "../../components/Shell";
import { api } from "../../api/client";
import { useMe } from "../../context/Me";
import { money } from "../orders/types";
import { tint, hueText } from "../../lib/tint";
import { type Plan, type PlanItem } from "./shared";
import { loadFailed } from "../../lib/notice";

// Catalog › Plans: what the shop sells as a membership. Each plan is a short list of what's
// included; the editor keeps the everyday fields up front and the season options tucked away.

type Svc = { id: string; name: string; price?: number };
const STARTER_TEMPLATES = [
  { k: "marine", label: "Boat Care", icon: "sailing", hint: "Spring launch, winterisation, haul-out, 10% off labour" },
  { k: "auto", label: "Auto Care", icon: "directions_car", hint: "3 oil changes, 2 tyre rotations, an inspection, 10% off labour" },
  { k: "electronics", label: "Device Protect", icon: "devices", hint: "A screen repair, 2 battery checks, 15% off labour, a loaner" },
];
const SWATCHES = ["#0059ba", "#0f766e", "#7c3aed", "#b45309", "#be123c", "#334155"];

const blankItem = (kind: PlanItem["kind"]): PlanItem => ({ kind, service_id: null, label: "", qty: 1, pct: 10, applies_to: "labour", window_start: null, window_end: null, rollover: false });
const blankPlan = (): Omit<Plan, "id" | "members"> => ({ name: "", description: "", template_key: null, asset_group_ids: [], price_monthly: null,
  price_yearly: null, term: "rolling", season_start: null, season_end: null, presale_days: 60, late_join: "prorated", color: SWATCHES[0], active: true, items: [] });

export default function PlansTab() {
  const { can } = useMe();
  const canEdit = can("can_manage_membership_plans");
  const [plans, setPlans] = useState<Plan[] | null>(null);
  const [edit, setEdit] = useState<(Omit<Plan, "id" | "members"> & { id?: string }) | null>(null);
  const [starter, setStarter] = useState(false);
  const starterRef = useRef<HTMLDivElement>(null);
  const [report, setReport] = useState<Record<string, { money_in: number; value_given: number }>>({});
  const load = () => api.get<{ items: Plan[] }>("/memberships/plans").then((r) => setPlans(r.items || [])).catch(() => setPlans([]));
  useEffect(() => {
    load();
    // Owners see what each plan brought in against the work it gave away (last 12 months).
    if (can("can_see_profit")) api.get<{ plans: { plan_id: string; money_in: number; value_given: number }[] }>("/memberships/reports/profitability")
      .then((r) => setReport(Object.fromEntries((r.plans || []).map((p) => [p.plan_id, p])))).catch(loadFailed("plan profitability"));
  }, []);   // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {   // the template menu closes on an outside click or Escape
    if (!starter) return;
    const onDoc = (e: MouseEvent) => { if (!starterRef.current?.contains(e.target as Node)) setStarter(false); };
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setStarter(false); };
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("mousedown", onDoc); document.removeEventListener("keydown", onKey); };
  }, [starter]);

  async function fromStarter(k: string) {
    const r = await api.get<{ items: any[] }>(`/memberships/plans/starters?template=${k}`);
    setStarter(false);
    if (r.items?.[0]) setEdit({ ...blankPlan(), ...r.items[0] });
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-col items-start gap-3">
        <div className="min-w-0">
          <div className="text-base font-semibold text-ink-primary">Membership plans</div>
          <div className="text-xs text-ink-tertiary">Sell included services and member discounts monthly or yearly. Plans go on a customer's boat, vehicle or device.</div>
        </div>
        {canEdit && <div className="flex gap-2">
          <button onClick={() => setEdit(blankPlan())} className="h-9 px-3.5 rounded-xl bg-primary text-white text-xs font-semibold shadow-clay-sm flex items-center gap-1.5 whitespace-nowrap"><M n="add" c="text-sm" />New plan</button>
          <div ref={starterRef} className="relative">
            <button onClick={() => setStarter((x) => !x)} aria-haspopup="menu" aria-expanded={starter}
              className="h-9 px-3.5 rounded-xl border border-slate-200 bg-white text-xs font-semibold text-ink-secondary hover:bg-slate-50 whitespace-nowrap flex items-center gap-1">
              From a template<M n={starter ? "expand_less" : "expand_more"} c="text-base text-ink-tertiary" />
            </button>
            {starter && (
              <div role="menu" className="absolute left-0 top-full mt-1.5 z-40 w-80 bg-raised rounded-2xl border border-slate-200/80 shadow-clay-card p-1.5">
                <div className="px-3 pt-2 pb-1.5 text-[11px] font-semibold uppercase tracking-wide text-ink-tertiary">Start from your trade</div>
                {STARTER_TEMPLATES.map((t) => (
                  <button key={t.k} role="menuitem" onClick={() => fromStarter(t.k)}
                    className="w-full flex items-start gap-3 rounded-xl px-3 py-2.5 text-left hover:bg-slate-50 focus-visible:outline-none focus-visible:bg-slate-50">
                    <span className="h-9 w-9 shrink-0 rounded-xl grid place-items-center bg-primary/10 text-primary"><M n={t.icon} c="text-lg" /></span>
                    <span className="min-w-0">
                      <span className="block text-sm font-semibold text-ink-primary">{t.label}</span>
                      <span className="block text-xs text-ink-tertiary leading-snug">{t.hint}</span>
                    </span>
                  </button>))}
                <div className="px-3 pt-1.5 pb-2 text-[11px] text-ink-tertiary border-t border-slate-100 mt-1">You can change every price and item before saving.</div>
              </div>
            )}
          </div>
        </div>}
      </div>
      {plans === null && <p className="text-xs text-ink-tertiary">Loading…</p>}
      {plans && plans.length === 0 && (
        <div className="rounded-2xl border border-dashed border-slate-300 bg-white/60 p-8 text-center">
          <div className="mx-auto h-11 w-11 rounded-2xl grid place-items-center bg-primary/10 text-primary"><M n="workspace_premium" c="text-xl" /></div>
          <div className="mt-3 text-sm font-semibold text-ink-primary">No plans yet</div>
          <div className="mt-1 text-xs text-ink-tertiary">Start from a template for your trade, then adjust the prices.</div>
        </div>
      )}
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
        {plans?.map((p) => {
          const r = report[p.id];
          const hero = p.price_yearly ? { n: p.price_yearly, per: "/yr" } : { n: p.price_monthly || 0, per: "/mo" };
          return (
            <button key={p.id} type="button" onClick={() => canEdit && setEdit(p)} className={`text-left rounded-2xl border border-slate-200/90 bg-white shadow-clay-card overflow-hidden transition ${canEdit ? "hover:border-slate-300" : "cursor-default"} ${p.active ? "" : "opacity-60"}`}>
              <div className="px-5 pt-4 pb-3.5" style={{ background: tint(p.color, 14) }}>
                <div className="flex items-center gap-3">
                  <div className="h-10 w-10 rounded-xl grid place-items-center bg-white/75" style={{ color: hueText(p.color) }}><M n="workspace_premium" c="text-lg" /></div>
                  <div className="min-w-0 flex-1"><div className="text-sm font-semibold text-ink-primary truncate">{p.name}</div>
                    <div className="text-xs text-ink-secondary">{p.members} member{p.members === 1 ? "" : "s"}{!p.active ? " · not on sale" : ""}</div></div>
                </div>
                <div className="mt-3 flex items-baseline gap-1.5">
                  <span className="text-2xl font-semibold tracking-tight tabular-nums text-ink-primary">{money(hero.n)}</span>
                  <span className="text-sm text-ink-tertiary">{hero.per}</span>
                  {p.price_yearly && p.price_monthly ? <span className="ml-auto text-xs text-ink-secondary">or {money(p.price_monthly)}/mo</span> : null}
                </div>
              </div>
              <div className="px-5 py-4">
                <ul className="space-y-1.5">{p.items.map((i) => (
                  <li key={i.id} className="flex items-center gap-2 text-xs text-ink-secondary"><span style={{ color: p.color }}><M n="check_circle" c="text-[14px]" /></span>{i.text}</li>))}</ul>
                {p.value_yearly ? <div className="mt-3 text-[11px] font-semibold text-emerald-700">Worth {money(p.value_yearly)} a year in services</div> : null}
                {r && (r.money_in > 0 || r.value_given > 0) && (
                  <div className="mt-3 flex items-center gap-2 border-t border-slate-100 pt-3 text-[11px] text-ink-tertiary">
                    <span>12 months: <b className="text-ink-primary tabular-nums">{money(r.money_in)}</b> in · <b className="text-ink-primary tabular-nums">{money(r.value_given)}</b> work given</span>
                    <span className={`ml-auto rounded-full px-1.5 py-px font-semibold ${r.money_in >= r.value_given ? "bg-emerald-50 text-emerald-700" : "bg-amber-50 text-amber-700"}`}>{r.money_in >= r.value_given ? "Paying off" : "Giving more"}</span>
                  </div>
                )}
                {p.term === "season" && <div className="mt-2 text-[11px] text-ink-tertiary">Season {p.season_start} → {p.season_end}</div>}
              </div>
            </button>
          );
        })}
      </div>
      {edit && <PlanEditor plan={edit} onClose={() => setEdit(null)} onSaved={() => { setEdit(null); load(); }} />}
    </div>
  );
}

function PlanEditor({ plan, onClose, onSaved }: { plan: Omit<Plan, "id" | "members"> & { id?: string }; onClose: () => void; onSaved: () => void }) {
  const [p, setP] = useState(plan);
  const [svcs, setSvcs] = useState<Svc[]>([]);
  const [more, setMore] = useState(plan.term === "season");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { api.get<{ items: Svc[] }>("/catalog/services").then((r) => setSvcs(r.items || [])).catch(loadFailed("services")); }, []);
  const set = (patch: Partial<typeof p>) => setP((x) => ({ ...x, ...patch }));
  const setItem = (i: number, patch: Partial<PlanItem>) => set({ items: p.items.map((it, j) => (j === i ? { ...it, ...patch } : it)) });
  const box = "glass-input h-10 rounded-xl px-3 text-sm";
  const inp = `${box} w-full`;
  const lbl = "block text-[11px] font-bold text-ink-tertiary mb-1";

  async function save() {
    setBusy(true); setErr(null);
    const body = { ...p, price_monthly: p.price_monthly || null, price_yearly: p.price_yearly || null,
      items: p.items.map(({ id, text, service_name, ...rest }) => rest) };   // eslint-disable-line @typescript-eslint/no-unused-vars
    try {
      if (p.id) await api.put(`/memberships/plans/${p.id}`, body); else await api.post("/memberships/plans", body);
      onSaved();
    } catch (e: any) { setErr(e?.message || "Couldn't save the plan"); setBusy(false); }
  }

  return (
    <div className="fixed inset-0 flex justify-end" style={{ zIndex: 50 }}>
      <div className="absolute inset-0 bg-ink-primary/35 backdrop-blur-[2px]" aria-hidden="true" onClick={onClose} />
      <div className="relative h-full w-full max-w-[560px] bg-white shadow-clay-drawer flex flex-col border-l border-slate-200/90">
        <header className="px-6 py-4 border-b border-slate-200/90 flex items-center gap-2">
          <div className="text-base font-semibold text-ink-primary">{p.id ? "Edit plan" : "New plan"}</div>
          <button aria-label="Close" onClick={onClose} className="ml-auto h-8 w-8 rounded-full grid place-items-center text-ink-tertiary hover:bg-slate-100"><M n="close" c="text-lg" /></button>
        </header>
        <div className="flex-1 overflow-y-auto bg-canvas p-6 space-y-4">
          <section className="rounded-2xl border border-slate-200/90 bg-white shadow-sm p-4 space-y-3">
            <div><label htmlFor="plan-name" className={lbl}>Plan name</label>
              <input id="plan-name" value={p.name} onChange={(e) => set({ name: e.target.value })} placeholder="Boat Care" className={inp} /></div>
            <div className="grid grid-cols-2 gap-3">
              <div><label htmlFor="plan-month" className={lbl}>Monthly price</label>
                <input id="plan-month" inputMode="decimal" value={p.price_monthly ?? ""} onChange={(e) => set({ price_monthly: e.target.value === "" ? null : Number(e.target.value) })} placeholder="Not offered" className={inp} /></div>
              <div><label htmlFor="plan-year" className={lbl}>Yearly price</label>
                <input id="plan-year" inputMode="decimal" value={p.price_yearly ?? ""} onChange={(e) => set({ price_yearly: e.target.value === "" ? null : Number(e.target.value) })} placeholder="Not offered" className={inp} /></div>
            </div>
            {p.price_monthly && p.price_yearly && p.price_monthly * 12 > p.price_yearly
              ? <p className="text-[11px] text-emerald-700">Yearly saves members {money(p.price_monthly * 12 - p.price_yearly)}.</p> : null}
            <div className="flex items-center gap-1.5">{SWATCHES.map((c) => (
              <button key={c} type="button" aria-label={`Colour ${c}`} onClick={() => set({ color: c })} className={`h-6 w-6 rounded-full transition ${p.color === c ? "ring-2 ring-offset-2 ring-slate-400" : ""}`} style={{ background: c }} />))}
              <label className="ml-auto flex items-center gap-2 text-xs text-ink-secondary"><input type="checkbox" checked={p.active} onChange={(e) => set({ active: e.target.checked })} />On sale</label>
            </div>
          </section>

          <section className="rounded-2xl border border-slate-200/90 bg-white shadow-sm p-4">
            <div className="text-sm font-semibold text-ink-primary">What's included</div>
            <div className="text-xs text-ink-tertiary mb-3">Counts are per plan year. Work orders use them up automatically.</div>
            <div className="space-y-2">
              {p.items.map((it, i) => (
                <div key={i} className="rounded-xl border border-slate-200 p-3">
                  <div className="flex items-center gap-2">
                    {it.kind === "service" ? (<>
                      <select aria-label="Included service" value={it.service_id || ""} onChange={(e) => setItem(i, { service_id: e.target.value, label: svcs.find((s) => s.id === e.target.value)?.name || "" })} className={`${box} min-w-0 flex-1`}>
                        <option value="">Choose a service…</option>
                        {svcs.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
                      </select>
                      <span className="text-xs text-ink-tertiary">×</span>
                      <input aria-label="Times per year" inputMode="numeric" value={it.qty} onChange={(e) => setItem(i, { qty: Math.max(0, Number(e.target.value) || 0) })} className={`${box} w-16 shrink-0 text-center`} />
                    </>) : it.kind === "discount" ? (<>
                      <input aria-label="Discount percent" inputMode="decimal" value={it.pct} onChange={(e) => setItem(i, { pct: Number(e.target.value) || 0 })} className={`${box} w-20 shrink-0 text-center`} />
                      <span className="text-xs text-ink-tertiary">% off</span>
                      <select aria-label="Discount applies to" value={it.applies_to} onChange={(e) => setItem(i, { applies_to: e.target.value as PlanItem["applies_to"] })} className={`${box} min-w-0 flex-1`}>
                        <option value="labour">labour</option><option value="parts">parts</option><option value="both">labour and parts</option>
                      </select>
                    </>) : (
                      <input aria-label="Perk" value={it.label} onChange={(e) => setItem(i, { label: e.target.value })} placeholder="Priority slot when the yard is busy" className={`${box} min-w-0 flex-1`} />
                    )}
                    <button aria-label="Remove" onClick={() => set({ items: p.items.filter((_, j) => j !== i) })} className="h-8 w-8 rounded-lg grid place-items-center text-ink-tertiary hover:bg-slate-100"><M n="close" c="text-base" /></button>
                  </div>
                  {it.kind === "service" && more && (
                    <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-ink-secondary">
                      <span>Book between</span>
                      <input aria-label="Window start" placeholder="10-01" value={it.window_start || ""} onChange={(e) => setItem(i, { window_start: e.target.value || null })} className="glass-input h-8 w-20 rounded-lg px-2 text-center" />
                      <span>and</span>
                      <input aria-label="Window end" placeholder="11-30" value={it.window_end || ""} onChange={(e) => setItem(i, { window_end: e.target.value || null })} className="glass-input h-8 w-20 rounded-lg px-2 text-center" />
                      <label className="ml-auto flex items-center gap-1.5"><input type="checkbox" checked={it.rollover} onChange={(e) => setItem(i, { rollover: e.target.checked })} />Unused carries over</label>
                    </div>
                  )}
                </div>
              ))}
            </div>
            <div className="mt-3 flex flex-wrap gap-3 text-xs font-semibold text-primary">
              <button onClick={() => set({ items: [...p.items, blankItem("service")] })}>+ Service</button>
              <button onClick={() => set({ items: [...p.items, blankItem("discount")] })}>+ Member discount</button>
              <button onClick={() => set({ items: [...p.items, { ...blankItem("perk"), qty: 0 }] })}>+ Perk</button>
            </div>
          </section>

          {!more ? (
            <button onClick={() => setMore(true)} className="text-xs font-semibold text-ink-secondary">+ Season dates and booking windows</button>
          ) : (
            <section className="rounded-2xl border border-slate-200/90 bg-white shadow-sm p-4 space-y-3">
              <div className="text-sm font-semibold text-ink-primary">Plan year</div>
              <div className="grid grid-cols-2 gap-1 rounded-xl bg-slate-100 p-1 text-xs font-semibold">
                {(["rolling", "season"] as const).map((t) => (
                  <button key={t} type="button" aria-pressed={p.term === t} onClick={() => set({ term: t })} className={`h-9 rounded-lg ${p.term === t ? "bg-white shadow-sm text-ink-primary" : "text-ink-tertiary"}`}>
                    {t === "rolling" ? "12 months from joining" : "A fixed season"}</button>))}
              </div>
              {p.term === "season" && (<>
                <div className="flex flex-wrap items-center gap-2 text-xs text-ink-secondary">
                  <span>Runs</span>
                  <input aria-label="Season start" placeholder="04-01" value={p.season_start || ""} onChange={(e) => set({ season_start: e.target.value })} className="glass-input h-9 w-20 rounded-lg px-2 text-center" />
                  <span>to</span>
                  <input aria-label="Season end" placeholder="03-31" value={p.season_end || ""} onChange={(e) => set({ season_end: e.target.value })} className="glass-input h-9 w-20 rounded-lg px-2 text-center" />
                  <span className="text-ink-tertiary">(month-day)</span>
                </div>
                <div className="flex flex-wrap items-center gap-2 text-xs text-ink-secondary">
                  <span>On sale</span>
                  <input aria-label="Days before the season" inputMode="numeric" value={p.presale_days} onChange={(e) => set({ presale_days: Number(e.target.value) || 0 })} className="glass-input h-9 w-16 rounded-lg px-2 text-center" />
                  <span>days before it starts. Late joiners pay</span>
                  <select aria-label="Late joiners pay" value={p.late_join} onChange={(e) => set({ late_join: e.target.value as Plan["late_join"] })} className="glass-input h-9 rounded-lg px-2">
                    <option value="prorated">for the months left</option><option value="full">the full price</option>
                  </select>
                </div>
              </>)}
            </section>
          )}
        </div>
        <footer className="border-t border-slate-200/90 p-4 bg-white">
          {err && <p role="alert" className="mb-2 rounded-lg bg-rose-50 px-3 py-2 text-xs text-rose-700">{err}</p>}
          <button onClick={save} disabled={busy || !p.name.trim()} className="h-11 w-full rounded-xl bg-primary text-white text-sm font-semibold shadow-clay-sm disabled:opacity-50">{busy ? "Saving…" : "Save plan"}</button>
        </footer>
      </div>
    </div>
  );
}
