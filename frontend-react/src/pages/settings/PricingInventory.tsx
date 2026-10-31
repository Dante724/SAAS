import { useEffect, useState, type ReactNode } from "react";
import { M } from "../../components/Shell";
import { api } from "../../api/client";
import { clickable } from "../../lib/clickable";

/* Settings › Pricing and Settings › Inventory & warehouses. Same endpoints as before
   (/pricing/types, /pricing/defaults, /staff/inventory-settings, /settings/warehouses);
   adding or editing a price level / warehouse still opens the hub's editor drawer. */

const inp = "w-full bg-white border border-slate-200 rounded-xl px-3 py-2.5 text-sm text-ink-primary outline-none focus:border-primary focus:ring-2 focus:ring-primary/10";

function Card({ icon, title, hint, right, children }: { icon: string; title: string; hint?: string; right?: ReactNode; children?: ReactNode }) {
  return (
    <section className="bg-white rounded-2xl border border-slate-200/90 shadow-clay-card">
      <div className="flex items-start gap-3 px-5 pt-4 pb-3">
        <span className="w-9 h-9 rounded-xl bg-primary/10 text-primary grid place-items-center flex-shrink-0"><M n={icon} c="text-lg" /></span>
        <div className="min-w-0"><h3 className="text-sm font-semibold text-ink-primary">{title}</h3>{hint && <p className="text-[11px] text-ink-tertiary">{hint}</p>}</div>
        {right && <div className="ml-auto flex-shrink-0">{right}</div>}
      </div>
      {children && <div className="px-5 pb-5">{children}</div>}
    </section>
  );
}
function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <label className="block">
      <span className="block text-xs font-medium text-ink-secondary mb-1.5">{label}</span>
      {children}
      {hint && <span className="block text-[11px] text-ink-tertiary mt-1">{hint}</span>}
    </label>
  );
}
function Seg({ value, set, opts }: { value: string; set: (v: string) => void; opts: [string, string][] }) {
  return (
    <div className="inline-flex rounded-xl bg-slate-100 p-1">
      {opts.map(([v, l]) => (
        <button key={v} type="button" onClick={() => set(v)} aria-pressed={value === v}
          className={`px-3 py-1.5 rounded-lg text-xs font-semibold transition-colors ${value === v ? "bg-white text-ink-primary shadow-sm" : "text-ink-secondary hover:text-ink-primary"}`}>{l}</button>
      ))}
    </div>
  );
}
function SaveBar({ dirty, busy, onSave, onDiscard }: { dirty: boolean; busy: boolean; onSave: () => void; onDiscard: () => void }) {
  if (!dirty) return null;
  return (
    <div className="fixed bottom-5 left-1/2 -translate-x-1/2 z-40 flex items-center gap-3 bg-ink-primary text-white rounded-2xl pl-4 pr-2 py-2 shadow-clay-drawer">
      <span className="w-2 h-2 rounded-full bg-amber-400" /><span className="text-xs">You have unsaved changes</span>
      <button onClick={onDiscard} className="px-3 py-1.5 rounded-lg text-xs text-white/80 hover:text-white hover:bg-white/10">Discard</button>
      <button onClick={onSave} disabled={busy} className="px-4 py-1.5 rounded-lg bg-white text-ink-primary text-xs font-semibold hover:bg-white/90 disabled:opacity-60">{busy ? "Saving…" : "Save changes"}</button>
    </div>
  );
}

const pct = (markup: number) => (100 + markup === 0 ? 0 : (markup / (100 + markup)) * 100);

export function PricingPage({ types, currency, onAdd, onEdit, onDelete, tiers }: {
  types: any[] | null | undefined; currency: string; onAdd: () => void; onEdit: (t: any) => void; onDelete: (t: any) => void; tiers: ReactNode;
}) {
  const [d, setD] = useState<any>(null);
  const [orig, setOrig] = useState<string>("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [cost, setCost] = useState("100");
  const [showTiers, setShowTiers] = useState(false);
  const load = () => api.get<any>("/pricing/defaults").then((r) => { setD(r || {}); setOrig(JSON.stringify(r || {})); }).catch(() => { setD({}); setOrig("{}"); });
  useEffect(() => { load(); }, []);
  const set = (k: string, v: any) => setD((s: any) => ({ ...s, [k]: v }));
  const dirty = !!d && JSON.stringify(d) !== orig;
  const list = types || [];
  const cur = currency || "$";
  const c = Number(cost) || 0;
  const floor = Number(d?.min_margin_pct) || 0;
  const guard = d?.margin_guard || "off";

  async function save() {
    setBusy(true); setMsg(null);
    try {
      await api.put("/pricing/defaults", {
        price_type_tickets_id: d.price_type_tickets_id || null, price_type_sales_id: d.price_type_sales_id || null,
        price_type_invoices_id: d.price_type_invoices_id || null,
        price_decimals: d.price_decimals === "" || d.price_decimals == null ? null : Number(d.price_decimals),
        min_margin_pct: Number(d.min_margin_pct) || 0, margin_guard: d.margin_guard || "off",
        discount_approval_pct: Number(d.discount_approval_pct) || 0,
      });
      setOrig(JSON.stringify(d)); setMsg("Saved");
      setTimeout(() => setMsg(null), 1800);
    } catch (e: any) { setMsg(e?.message || "Couldn't save"); } finally { setBusy(false); }
  }

  const DocPick = ({ k, label }: { k: string; label: string }) => (
    <Field label={label}>
      <select value={d?.[k] || ""} onChange={(e) => set(k, e.target.value)} className={inp}>
        <option value="">Standard price</option>{list.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
      </select>
    </Field>
  );

  return (
    <div className="max-w-3xl space-y-5 pb-20">
      <div>
        <h2 className="text-base font-semibold text-ink-primary">Pricing</h2>
        <p className="text-xs text-ink-tertiary">How parts are priced from their cost, and the guardrails that stop a job from losing money.</p>
      </div>
      {msg && <div className={`text-xs ${msg === "Saved" ? "text-emerald-700" : "text-rose-600"}`}>{msg}</div>}

      <Card icon="sell" title="Price levels" hint="Each level adds a markup on cost. Use them for retail, trade, insurance customers…"
        right={<button onClick={onAdd} className="inline-flex items-center gap-1.5 px-3 py-2 rounded-xl border border-slate-200 text-xs font-semibold text-ink-primary hover:bg-slate-50"><M n="add" c="text-sm" />Add level</button>}>
        {types == null ? <div className="text-xs text-ink-tertiary">Loading…</div> : list.length === 0 ? (
          <div className="rounded-xl border border-dashed border-slate-300 p-5 text-center text-xs text-ink-tertiary">No price levels yet. Parts sell at the price you set on each one.</div>
        ) : (
          <div className="grid sm:grid-cols-2 gap-2.5">
            {list.map((t) => {
              const mk = Number(t.markup_pct ?? t.percent ?? t.value) || 0;
              return (
                <div key={t.id} {...clickable(() => onEdit(t))} className="group relative rounded-xl border border-slate-200 p-3.5 cursor-pointer hover:border-slate-300 hover:bg-slate-50/60">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-medium text-ink-primary truncate">{t.name}</span>
                    {t.is_default && <span className="text-[11px] font-semibold px-1.5 py-0.5 rounded-md bg-primary/10 text-primary">Default</span>}
                    <button aria-label={`Delete ${t.name}`} onClick={(e) => { e.stopPropagation(); onDelete(t); }}
                      className="ml-auto w-6 h-6 rounded-md grid place-items-center text-ink-tertiary hover:bg-rose-50 hover:text-rose-600 opacity-0 group-hover:opacity-100 focus:opacity-100"><M n="close" c="text-sm" /></button>
                  </div>
                  <div className="text-xl font-semibold text-ink-primary mt-1">+{mk}%</div>
                  <div className="text-[11px] text-ink-tertiary">{cur}100 cost sells for {cur}{(100 * (1 + mk / 100)).toFixed(2)} · {pct(mk).toFixed(0)}% margin</div>
                  {(t.location_scope && t.location_scope !== "all") || t.starts_at || t.ends_at ? (
                    <div className="flex gap-1 mt-2">
                      {t.location_scope && t.location_scope !== "all" && <span className="text-[11px] px-1.5 py-0.5 rounded-md bg-slate-100 text-ink-secondary">Some locations</span>}
                      {(t.starts_at || t.ends_at) && <span className="text-[11px] px-1.5 py-0.5 rounded-md bg-amber-50 text-amber-700">Limited dates</span>}
                    </div>
                  ) : null}
                </div>
              );
            })}
          </div>
        )}
      </Card>

      {list.length > 0 && (
        <Card icon="percent" title="Try a price" hint="See what a part sells for at each level, and whether it clears your margin floor.">
          <div className="flex items-center gap-2 mb-3">
            <span className="text-xs text-ink-secondary">Part costs</span>
            <span className="relative"><span className="absolute left-3 top-2 text-sm text-ink-tertiary">{cur}</span>
              <input type="number" min={0} value={cost} onChange={(e) => setCost(e.target.value)} aria-label="Part cost" className="w-28 bg-white border border-slate-200 rounded-xl pl-7 pr-3 py-2 text-sm outline-none focus:border-primary" /></span>
          </div>
          <div className="rounded-xl border border-slate-100 divide-y divide-slate-100">
            {list.map((t) => {
              const mk = Number(t.markup_pct ?? t.percent ?? t.value) || 0;
              const m = pct(mk); const low = guard !== "off" && m < floor;
              return (
                <div key={t.id} className="flex items-center gap-3 px-3 py-2 text-sm">
                  <span className="text-ink-secondary flex-1 truncate">{t.name}</span>
                  <span className="font-semibold text-ink-primary">{cur}{(c * (1 + mk / 100)).toFixed(2)}</span>
                  <span className={`w-24 text-right text-xs ${low ? "text-rose-600 font-semibold" : "text-ink-tertiary"}`}>{m.toFixed(0)}% margin{low ? " ⚠" : ""}</span>
                </div>
              );
            })}
          </div>
        </Card>
      )}

      {d && (<>
        <Card icon="description" title="Which price each document uses" hint="Picked automatically when you add a part. Staff can still change it on the line.">
          <div className="grid sm:grid-cols-3 gap-3">
            <DocPick k="price_type_tickets_id" label="Work orders" />
            <DocPick k="price_type_sales_id" label="Counter sales" />
            <DocPick k="price_type_invoices_id" label="Invoices" />
          </div>
        </Card>

        <Card icon="verified_user" title="Protect your margin" hint="Stop discounts and low prices from turning a job into a loss.">
          <div className="space-y-4">
            <div>
              <div className="text-xs font-medium text-ink-secondary mb-1.5">When a line's margin is below the floor</div>
              <Seg value={guard} set={(v) => set("margin_guard", v)} opts={[["off", "Do nothing"], ["warn", "Warn"], ["block", "Don't allow it"]]} />
            </div>
            <div className="grid sm:grid-cols-3 gap-3">
              <Field label="Margin floor" hint={guard === "off" ? "Turned off above" : "Lowest margin allowed"}>
                <div className="relative"><input type="number" min={0} max={100} value={d.min_margin_pct ?? 0} onChange={(e) => set("min_margin_pct", e.target.value)} className={`${inp} pr-8`} disabled={guard === "off"} /><span className="absolute right-3 top-2.5 text-sm text-ink-tertiary">%</span></div>
              </Field>
              <Field label="Discounts over this need a manager" hint="0 = anyone can discount">
                <div className="relative"><input type="number" min={0} max={100} value={d.discount_approval_pct ?? 0} onChange={(e) => set("discount_approval_pct", e.target.value)} className={`${inp} pr-8`} /><span className="absolute right-3 top-2.5 text-sm text-ink-tertiary">%</span></div>
              </Field>
              <Field label="Decimals on prices">
                <select value={d.price_decimals ?? 2} onChange={(e) => set("price_decimals", Number(e.target.value))} className={inp}>
                  {[0, 1, 2, 3, 4].map((n) => <option key={n} value={n}>{n === 0 ? "None (whole numbers)" : `${n} (${(1.5).toFixed(n)})`}</option>)}
                </select>
              </Field>
            </div>
          </div>
        </Card>
      </>)}

      {list.length > 0 && (
        <div className="rounded-2xl border border-slate-200/90 bg-white">
          <button onClick={() => setShowTiers((v) => !v)} aria-expanded={showTiers} className="w-full flex items-center gap-3 px-5 py-4 text-left">
            <span className="w-9 h-9 rounded-xl bg-slate-100 text-ink-secondary grid place-items-center"><M n="trending_down" c="text-lg" /></span>
            <span><span className="block text-sm text-ink-primary">Quantity breaks</span><span className="block text-[11px] text-ink-tertiary">Lower the markup when someone buys more. Optional.</span></span>
            <M n={showTiers ? "expand_less" : "expand_more"} c="ml-auto text-base text-ink-tertiary" />
          </button>
          {showTiers && <div className="px-5 pb-5">{tiers}</div>}
        </div>
      )}

      <SaveBar dirty={dirty} busy={busy} onSave={save} onDiscard={() => setD(JSON.parse(orig || "{}"))} />
    </div>
  );
}

export function InventoryPage({ warehouses, currency, onAdd, onEdit, onDelete }: {
  warehouses: any[] | null | undefined; currency: string; onAdd: () => void; onEdit: (w: any) => void; onDelete: (w: any) => void;
}) {
  const [c, setC] = useState<any>(null);
  const [orig, setOrig] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  useEffect(() => { api.get<any>("/staff/inventory-settings").then((r) => { setC(r || {}); setOrig(JSON.stringify(r || {})); }).catch(() => { setC({}); setOrig("{}"); }); }, []);
  const set = (k: string, v: any) => setC((s: any) => ({ ...s, [k]: v }));
  const dirty = !!c && JSON.stringify(c) !== orig;
  async function save() {
    setBusy(true); setMsg(null);
    const body = { enable_bins: !!c.enable_bins, enable_reservations: !!c.enable_reservations, reservation_max_days: Number(c.reservation_max_days) || 0,
      unique_identifier: c.unique_identifier || "sku", default_product_type: c.default_product_type || "default",
      product_serial_template: c.product_serial_template || null, default_vehicle_type: c.default_vehicle_type || "Car",
      asset_serial_template: c.asset_serial_template || null, po_approval_threshold: Number(c.po_approval_threshold) || 0,
      po_to_job: c.po_to_job === "manual" ? "manual" : "auto" };
    try { await api.put("/staff/inventory-settings", body); setOrig(JSON.stringify(c)); setMsg("Saved"); setTimeout(() => setMsg(null), 1800); }
    catch (e: any) { setMsg(e?.message || "Couldn't save"); } finally { setBusy(false); }
  }
  const Sw = ({ k, label, hint }: { k: string; label: string; hint?: string }) => (
    <label className="flex items-center gap-3 py-3 border-t border-slate-100 first:border-0 cursor-pointer">
      <span className="min-w-0"><span className="block text-sm text-ink-primary">{label}</span>{hint && <span className="block text-[11px] text-ink-tertiary">{hint}</span>}</span>
      <button type="button" role="switch" aria-checked={!!c?.[k]} aria-label={label} onClick={() => set(k, !c?.[k])}
        className={`ml-auto w-10 h-6 rounded-full flex-shrink-0 relative transition-colors ${c?.[k] ? "bg-primary" : "bg-slate-300"}`}><span className={`absolute top-0.5 w-5 h-5 rounded-full bg-white shadow transition-all ${c?.[k] ? "left-[18px]" : "left-0.5"}`} /></button>
    </label>
  );
  const list = warehouses || [];
  const parts = list.filter((w) => w.kind !== "asset"); const items = list.filter((w) => w.kind === "asset");
  const Place = ({ w }: { w: any }) => (
    <div {...clickable(() => onEdit(w))} className={`group flex items-center gap-3 px-3 py-2.5 rounded-xl cursor-pointer hover:bg-slate-50 ${w.active === false ? "opacity-50" : ""}`}>
      <span className={`w-9 h-9 rounded-xl grid place-items-center flex-shrink-0 ${w.kind === "asset" ? "bg-violet-50 text-violet-700" : "bg-amber-50 text-amber-700"}`}><M n={w.kind === "asset" ? "devices" : "inventory_2"} c="text-lg" /></span>
      <span className="min-w-0"><span className="block text-sm text-ink-primary truncate">{w.name}</span>
        <span className="block text-[11px] text-ink-tertiary">{w.scope === "company" ? "Shared by all locations" : "One location"}{w.active === false ? " · Not in use" : ""}</span></span>
      <button aria-label={`Delete ${w.name}`} onClick={(e) => { e.stopPropagation(); onDelete(w); }}
        className="ml-auto w-7 h-7 rounded-lg grid place-items-center text-ink-tertiary hover:bg-rose-50 hover:text-rose-600 opacity-0 group-hover:opacity-100 focus:opacity-100"><M n="close" c="text-base" /></button>
      <M n="chevron_right" c="text-base text-ink-tertiary" />
    </div>
  );

  return (
    <div className="max-w-3xl space-y-5 pb-20">
      <div>
        <h2 className="text-base font-semibold text-ink-primary">Inventory</h2>
        <p className="text-xs text-ink-tertiary">Where parts and customers' items are kept, and how stock is tracked.</p>
      </div>
      {msg && <div className={`text-xs ${msg === "Saved" ? "text-emerald-700" : "text-rose-600"}`}>{msg}</div>}

      <Card icon="warehouse" title="Where things are kept" hint="Parts you sell, and the customers' items you're repairing, each have their own places."
        right={<button onClick={onAdd} className="inline-flex items-center gap-1.5 px-3 py-2 rounded-xl border border-slate-200 text-xs font-semibold text-ink-primary hover:bg-slate-50"><M n="add" c="text-sm" />Add place</button>}>
        {warehouses == null ? <div className="text-xs text-ink-tertiary">Loading…</div> : (
          <div className="grid md:grid-cols-2 gap-4">
            <div>
              <div className="text-[11px] font-semibold text-ink-tertiary mb-1 px-1">Parts and stock</div>
              {parts.length ? parts.map((w) => <Place key={w.id} w={w} />) : <div className="px-3 py-2 text-xs text-ink-tertiary">None yet</div>}
            </div>
            <div>
              <div className="text-[11px] font-semibold text-ink-tertiary mb-1 px-1">Customers' items</div>
              {items.length ? items.map((w) => <Place key={w.id} w={w} />) : <div className="px-3 py-2 text-xs text-ink-tertiary">None yet</div>}
            </div>
          </div>
        )}
      </Card>

      {c && (<>
        <Card icon="inventory_2" title="Tracking stock">
          <Sw k="enable_bins" label="Shelf and bin locations" hint="Record exactly where each part sits." />
          <Sw k="enable_reservations" label="Hold parts for a job" hint="A part added to a job is set aside so it isn't sold." />
          {c.enable_reservations && (
            <div className="flex items-center gap-3 pb-1 pl-4">
              <span className="text-xs text-ink-secondary">Release held parts after</span>
              <input type="number" min={0} value={c.reservation_max_days ?? 14} onChange={(e) => set("reservation_max_days", Number(e.target.value))} aria-label="Days to hold"
                className="w-16 bg-white border border-slate-200 rounded-lg text-sm px-2 py-1.5 text-right" />
              <span className="text-xs text-ink-secondary">days</span>
            </div>
          )}
          <div className="py-3 border-t border-slate-100">
            <div className="text-sm text-ink-primary">Parts that arrive for a job</div>
            <div className="text-[11px] text-ink-tertiary mb-2">When a purchase order raised from a job is received.</div>
            <div className="grid sm:grid-cols-2 gap-2">
              {([["auto", "Write off automatically", "bolt", "Stock goes straight to the job the moment it's received."],
                 ["manual", "Staff write it off", "back_hand", "Parts land in stock; someone writes them off on the job."]] as const).map(([v, l, icon, hint]) => {
                const on = (c.po_to_job || "auto") === v;
                return (
                  <button key={v} type="button" onClick={() => set("po_to_job", v)} aria-pressed={on}
                    className={`text-left rounded-xl border p-3 transition-colors ${on ? "border-primary bg-primary/5 ring-2 ring-primary/15" : "border-slate-200 hover:border-slate-300"}`}>
                    <span className="flex items-center gap-2 text-xs font-semibold text-ink-primary"><M n={icon} c={`text-sm ${on ? "text-primary" : "text-ink-tertiary"}`} />{l}</span>
                    <span className="block text-[11px] text-ink-tertiary mt-0.5">{hint}</span>
                  </button>
                );
              })}
            </div>
          </div>
        </Card>

        <Card icon="qr_code_2" title="Product codes" hint="How products are identified and how new serial numbers are made.">
          <div className="space-y-4">
            <div>
              <div className="text-xs font-medium text-ink-secondary mb-1.5">Each product is unique by</div>
              <Seg value={c.unique_identifier || "sku"} set={(v) => set("unique_identifier", v)} opts={[["name", "Name"], ["sku", "SKU"], ["barcode", "Barcode"], ["code", "Code"]]} />
            </div>
            <div className="grid sm:grid-cols-2 gap-3">
              <Field label="New products start as"><input value={c.default_product_type || "default"} onChange={(e) => set("default_product_type", e.target.value)} className={inp} /></Field>
              <Field label="Serial number pattern" hint="{C:5} = a 5-digit counter"><input value={c.product_serial_template || ""} onChange={(e) => set("product_serial_template", e.target.value)} placeholder="0-{C:19}" className={`${inp} font-mono`} /></Field>
            </div>
          </div>
        </Card>

        <Card icon="devices" title="Customers' items" hint="Defaults when an item is checked in without these details.">
          <div className="grid sm:grid-cols-2 gap-3">
            <Field label="Default item type"><input value={c.default_vehicle_type || "Car"} onChange={(e) => set("default_vehicle_type", e.target.value)} className={inp} /></Field>
            <Field label="Serial number pattern"><input value={c.asset_serial_template || ""} onChange={(e) => set("asset_serial_template", e.target.value)} placeholder="9-{C:4}" className={`${inp} font-mono`} /></Field>
          </div>
        </Card>

        <Card icon="shopping_cart" title="Purchasing">
          <Field label="Purchase orders over this need approval" hint="0 = no approval needed">
            <div className="relative max-w-[200px]"><span className="absolute left-3 top-2.5 text-sm text-ink-tertiary">{currency || "$"}</span>
              <input type="number" min={0} value={c.po_approval_threshold ?? 0} onChange={(e) => set("po_approval_threshold", Number(e.target.value))} className={`${inp} pl-8`} /></div>
          </Field>
        </Card>
      </>)}

      <SaveBar dirty={dirty} busy={busy} onSave={save} onDiscard={() => setC(JSON.parse(orig || "{}"))} />
    </div>
  );
}
