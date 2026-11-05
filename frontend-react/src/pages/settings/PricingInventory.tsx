import { useEffect, useState, type ReactNode } from "react";
import { M } from "../../components/Shell";
import { api } from "../../api/client";
import Switch from "../../components/Switch";
import SettingsPage from "../../components/SettingsPage";
import Section from "../../components/Section";
import SaveFooter from "../../components/SaveFooter";
import Tabs from "../../components/Tabs";
import Button, { IconButton } from "../../components/Button";
import EmptyState from "../../components/EmptyState";
import { Input, Select } from "../../components/Field";
import { SettingRow, NumField } from "../../components/SettingRow";
import { clickable } from "../../lib/clickable";
import { sectionFooter, rowField } from "./kit";

/* Settings › Pricing and Settings › Inventory & warehouses. Same endpoints as before
   (/pricing/types, /pricing/defaults, /staff/inventory-settings, /settings/warehouses);
   adding or editing a price level / warehouse still opens the hub's editor drawer.
   Each card of fields ends with its own Save; every Save sends the whole settings record, as before. */

type Msg = { card: string; ok: boolean; text: string } | null;

// Per-card "changed?" and "discard" over the saved snapshot, so each card's Save lights up only
// for its own fields.
function useCards(d: any, orig: string, setD: (fn: (s: any) => any) => void) {
  let o: any = {};
  try { o = JSON.parse(orig || "{}"); } catch { o = {}; }
  const changed = (keys: string[]) => !!d && keys.some((k) => JSON.stringify(d[k] ?? null) !== JSON.stringify(o[k] ?? null));
  const discard = (keys: string[]) => setD((s: any) => ({ ...s, ...Object.fromEntries(keys.map((k) => [k, o[k]])) }));
  return { changed, discard };
}

const pct = (markup: number) => (100 + markup === 0 ? 0 : (markup / (100 + markup)) * 100);

const DOC_KEYS = ["price_type_tickets_id", "price_type_sales_id", "price_type_invoices_id"];
const MARGIN_KEYS = ["margin_guard", "min_margin_pct", "discount_approval_pct", "price_decimals"];

export function PricingPage({ types, currency, onAdd, onEdit, onDelete, tiers }: {
  types: any[] | null | undefined; currency: string; onAdd: () => void; onEdit: (t: any) => void; onDelete: (t: any) => void; tiers: ReactNode;
}) {
  const [d, setD] = useState<any>(null);
  const [orig, setOrig] = useState<string>("");
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<Msg>(null);
  const [cost, setCost] = useState("100");
  const [showTiers, setShowTiers] = useState(false);
  const load = () => api.get<any>("/pricing/defaults").then((r) => { setD(r || {}); setOrig(JSON.stringify(r || {})); }).catch(() => { setD({}); setOrig("{}"); });
  useEffect(() => { load(); }, []);
  const set = (k: string, v: any) => { setD((s: any) => ({ ...s, [k]: v })); setMsg(null); };
  const { changed, discard } = useCards(d, orig, setD);
  const list = types || [];
  const cur = currency || "$";
  const c = Number(cost) || 0;
  const floor = Number(d?.min_margin_pct) || 0;
  const guard = d?.margin_guard || "off";

  async function save(card: string) {
    setBusy(card); setMsg(null);
    try {
      await api.put("/pricing/defaults", {
        price_type_tickets_id: d.price_type_tickets_id || null, price_type_sales_id: d.price_type_sales_id || null,
        price_type_invoices_id: d.price_type_invoices_id || null,
        price_decimals: d.price_decimals === "" || d.price_decimals == null ? null : Number(d.price_decimals),
        min_margin_pct: Number(d.min_margin_pct) || 0, margin_guard: d.margin_guard || "off",
        discount_approval_pct: Number(d.discount_approval_pct) || 0,
      });
      setOrig(JSON.stringify(d)); setMsg({ card, ok: true, text: "Saved" });
    } catch (e: any) { setMsg({ card, ok: false, text: e?.message || "Couldn't save" }); } finally { setBusy(null); }
  }
  const footer = (card: string, keys: string[]) => (
    <SaveFooter className={sectionFooter} dirty={changed(keys)} busy={busy === card} onSave={() => save(card)}
      onDiscard={() => { discard(keys); setMsg(null); }} msg={msg?.card === card ? msg : null} />
  );

  const docPick = (k: string, label: string) => (
    <SettingRow key={k} stack label={label} htmlFor={`pd-${k}`}>
      <Select id={`pd-${k}`} value={d?.[k] || ""} onChange={(e) => set(k, e.target.value)} className={rowField}>
        <option value="">Standard price</option>{list.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
      </Select>
    </SettingRow>
  );

  return (
    <SettingsPage title="Pricing" sub="How parts are priced from their cost, and the guardrails that stop a job from losing money."
      actions={<Button variant="primary" onClick={onAdd} icon={<M n="add" c="text-sm" />}>Add level</Button>}>
      <Section icon="sell" title="Price levels" description="Each level adds a markup on cost. Use them for retail, trade, insurance customers…">
        {types == null ? (
          <div className="grid sm:grid-cols-2 gap-2.5" aria-hidden="true"><div className="sk h-24 rounded-xl" /><div className="sk h-24 rounded-xl" /></div>
        ) : list.length === 0 ? (
          <EmptyState icon={<M n="sell" c="text-lg" />} title="No price levels yet" body="Parts sell at the price you set on each one." />
        ) : (
          <div className="grid sm:grid-cols-2 gap-2.5">
            {list.map((t) => {
              const mk = Number(t.markup_pct ?? t.percent ?? t.value) || 0;
              return (
                <div key={t.id} {...clickable(() => onEdit(t))} className="group relative rounded-xl border border-slate-200 p-3.5 cursor-pointer hover:border-slate-300 hover:bg-slate-50/60">
                  <div className="flex items-center gap-2 min-w-0">
                    <span className="text-sm font-medium text-ink-primary truncate">{t.name}</span>
                    {t.is_default && <span className="text-2xs font-semibold px-1.5 py-0.5 rounded-md bg-primary/10 text-primary">Default</span>}
                    <IconButton size="sm" label={`Delete ${t.name}`} onClick={(e) => { e.stopPropagation(); onDelete(t); }}
                      className="ml-auto hover:!bg-rose-50 hover:!text-rose-600 sm:opacity-0 sm:group-hover:opacity-100 focus:opacity-100" icon={<M n="close" c="text-sm" />} />
                  </div>
                  <div className="text-xl font-semibold tabular-nums text-ink-primary mt-1">+{mk}%</div>
                  <div className="text-2xs text-ink-tertiary tabular-nums">{cur}100 cost sells for {cur}{(100 * (1 + mk / 100)).toFixed(2)} · {pct(mk).toFixed(0)}% margin</div>
                  {(t.location_scope && t.location_scope !== "all") || t.starts_at || t.ends_at ? (
                    <div className="flex gap-1 mt-2">
                      {t.location_scope && t.location_scope !== "all" && <span className="text-2xs px-1.5 py-0.5 rounded-md bg-slate-100 text-ink-secondary">Some locations</span>}
                      {(t.starts_at || t.ends_at) && <span className="text-2xs px-1.5 py-0.5 rounded-md bg-amber-50 text-amber-700">Limited dates</span>}
                    </div>
                  ) : null}
                </div>
              );
            })}
          </div>
        )}
      </Section>

      {list.length > 0 && (
        <Section icon="percent" title="Try a price" description="See what a part sells for at each level, and whether it clears your margin floor.">
          <div className="flex items-center gap-2 mb-3">
            <label htmlFor="try-cost" className="text-xs text-ink-secondary">Part costs</label>
            <span className="relative w-28"><span className="absolute left-3 top-2 text-sm text-ink-tertiary">{cur}</span>
              <Input id="try-cost" type="number" min={0} value={cost} onChange={(e) => setCost(e.target.value)} aria-label="Part cost" className="pl-7 tabular-nums" /></span>
          </div>
          <div className="rounded-xl border border-slate-100 divide-y divide-slate-100">
            {list.map((t) => {
              const mk = Number(t.markup_pct ?? t.percent ?? t.value) || 0;
              const m = pct(mk); const low = guard !== "off" && m < floor;
              return (
                <div key={t.id} className="flex items-center gap-3 px-3 py-2 text-sm">
                  <span className="text-ink-secondary flex-1 min-w-0 truncate">{t.name}</span>
                  <span className="font-semibold tabular-nums text-ink-primary">{cur}{(c * (1 + mk / 100)).toFixed(2)}</span>
                  <span className={`w-28 inline-flex items-center justify-end gap-1 text-xs tabular-nums ${low ? "text-rose-600 font-semibold" : "text-ink-tertiary"}`}>
                    {m.toFixed(0)}% margin{low && <M n="warning" c="text-sm" />}
                  </span>
                </div>
              );
            })}
          </div>
        </Section>
      )}

      {d && (<>
        <Section icon="description" title="Which price each document uses" description="Picked automatically when you add a part. Staff can still change it on the line.">
          <div className="divide-y divide-slate-100">
            {docPick("price_type_tickets_id", "Work orders")}
            {docPick("price_type_sales_id", "Counter sales")}
            {docPick("price_type_invoices_id", "Invoices")}
          </div>
          {footer("docs", DOC_KEYS)}
        </Section>

        <Section icon="verified_user" title="Protect your margin" description="Stop discounts and low prices from turning a job into a loss.">
          <div className="divide-y divide-slate-100">
            <SettingRow stack label="When a line's margin is below the floor">
              <Tabs look="segmented" label="When a line's margin is below the floor" value={guard} onChange={(v) => set("margin_guard", v)}
                tabs={[{ id: "off", label: "Do nothing" }, { id: "warn", label: "Warn" }, { id: "block", label: "Don't allow it" }]} />
            </SettingRow>
            <SettingRow label="Margin floor" htmlFor="pd-floor" dim={guard === "off"} help={guard === "off" ? "Turned off above." : "Lowest margin allowed."}>
              <NumField id="pd-floor" max={100} value={Number(d.min_margin_pct) || 0} onChange={(v) => set("min_margin_pct", v)} unit="%" disabled={guard === "off"} />
            </SettingRow>
            <SettingRow label="Discounts over this need a manager" htmlFor="pd-disc" help="0 means anyone can discount.">
              <NumField id="pd-disc" max={100} value={Number(d.discount_approval_pct) || 0} onChange={(v) => set("discount_approval_pct", v)} unit="%" />
            </SettingRow>
            <SettingRow stack label="Decimals on prices" htmlFor="pd-dec">
              <Select id="pd-dec" value={d.price_decimals ?? 2} onChange={(e) => set("price_decimals", Number(e.target.value))} className={rowField}>
                {[0, 1, 2].map((n) => <option key={n} value={n}>{n === 0 ? "None (whole numbers)" : `${n} (${(1.5).toFixed(n)})`}</option>)}
              </Select>
            </SettingRow>
          </div>
          {footer("margin", MARGIN_KEYS)}
        </Section>
      </>)}

      {list.length > 0 && (
        <Section icon="trending_down" title="Quantity breaks" description="Lower the markup when someone buys more. Optional."
          actions={<Button onClick={() => setShowTiers((v) => !v)} aria-expanded={showTiers} icon={<M n={showTiers ? "expand_less" : "expand_more"} c="text-sm" />}>
            {showTiers ? "Hide quantity breaks" : "Set up quantity breaks"}</Button>}>
          {showTiers ? tiers : undefined}
        </Section>
      )}
    </SettingsPage>
  );
}

const TRACK_KEYS = ["enable_bins", "enable_reservations", "reservation_max_days", "po_to_job"];
const CODE_KEYS = ["unique_identifier", "default_product_type", "barcode_prefix", "sku_template", "product_serial_template", "bin_template"];
const ITEM_KEYS = ["default_vehicle_type", "asset_serial_template"];
const BUY_KEYS = ["po_approval_threshold"];

export function InventoryPage({ warehouses, currency, onAdd, onEdit, onDelete }: {
  warehouses: any[] | null | undefined; currency: string; onAdd: () => void; onEdit: (w: any) => void; onDelete: (w: any) => void;
}) {
  const [c, setC] = useState<any>(null);
  const [orig, setOrig] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<Msg>(null);
  const [patterns, setPatterns] = useState(false);
  useEffect(() => { api.get<any>("/staff/inventory-settings").then((r) => { setC(r || {}); setOrig(JSON.stringify(r || {})); }).catch(() => { setC({}); setOrig("{}"); }); }, []);
  const set = (k: string, v: any) => { setC((s: any) => ({ ...s, [k]: v })); setMsg(null); };
  const { changed, discard } = useCards(c, orig, setC);
  async function save(card: string) {
    setBusy(card); setMsg(null);
    const body = { enable_bins: !!c.enable_bins, enable_reservations: !!c.enable_reservations, reservation_max_days: Number(c.reservation_max_days) || 0,
      unique_identifier: c.unique_identifier || "name", default_product_type: c.default_product_type || "default",
      product_serial_template: c.product_serial_template || null, default_vehicle_type: c.default_vehicle_type || "Car",
      asset_serial_template: c.asset_serial_template || null, po_approval_threshold: Number(c.po_approval_threshold) || 0,
      sku_template: c.sku_template || null, bin_template: c.bin_template || null, barcode_prefix: c.barcode_prefix || "20",
      po_to_job: c.po_to_job === "manual" ? "manual" : "auto" };
    try { await api.put("/staff/inventory-settings", body); setOrig(JSON.stringify(c)); setMsg({ card, ok: true, text: "Saved" }); }
    catch (e: any) { setMsg({ card, ok: false, text: e?.message || "Couldn't save" }); } finally { setBusy(null); }
  }
  const footer = (card: string, keys: string[]) => (
    <SaveFooter className={sectionFooter} dirty={changed(keys)} busy={busy === card} onSave={() => save(card)}
      onDiscard={() => { discard(keys); setMsg(null); }} msg={msg?.card === card ? msg : null} />
  );
  // Plain functions (not components), so rows aren't remounted on every render.
  const sw = (k: string, label: string, hint?: string) => (
    <SettingRow key={k} label={label} help={hint}><Switch label={label} on={!!c?.[k]} onChange={(v) => set(k, v)} /></SettingRow>
  );
  const text = (k: string, label: string, opts: { help?: string; placeholder?: string; mono?: boolean; def?: string } = {}) => (
    <SettingRow key={k} stack label={label} help={opts.help} htmlFor={`inv-${k}`}>
      <Input id={`inv-${k}`} value={c?.[k] || opts.def || ""} onChange={(e) => set(k, e.target.value)} placeholder={opts.placeholder} maxLength={40}
        className={`${rowField} ${opts.mono ? "font-mono" : ""}`} />
    </SettingRow>
  );
  const list = warehouses || [];
  const parts = list.filter((w) => w.kind !== "asset"); const items = list.filter((w) => w.kind === "asset");
  const place = (w: any) => (
    <div key={w.id} {...clickable(() => onEdit(w))} className={`group flex items-center gap-3 px-3 py-2.5 rounded-xl cursor-pointer hover:bg-slate-50 ${w.active === false ? "opacity-50" : ""}`}>
      <span className={`w-9 h-9 rounded-xl grid place-items-center flex-shrink-0 ${w.kind === "asset" ? "bg-violet-50 text-violet-700" : "bg-amber-50 text-amber-700"}`}><M n={w.kind === "asset" ? "devices" : "inventory_2"} c="text-lg" /></span>
      <span className="min-w-0 flex-1"><span className="block text-sm text-ink-primary truncate">{w.name}</span>
        <span className="block text-2xs text-ink-tertiary">{w.scope === "company" ? "Shared by all locations" : "One location"}{w.active === false ? " · Not in use" : ""}</span></span>
      <IconButton size="sm" label={`Delete ${w.name}`} onClick={(e) => { e.stopPropagation(); onDelete(w); }}
        className="hover:!bg-rose-50 hover:!text-rose-600 sm:opacity-0 sm:group-hover:opacity-100 focus:opacity-100" icon={<M n="close" c="text-base" />} />
      <M n="chevron_right" c="text-base text-ink-tertiary" />
    </div>
  );
  const group = (title: string, rows: any[]) => (
    <div className="min-w-0">
      <div className="text-2xs font-semibold text-ink-tertiary mb-1 px-1">{title}</div>
      {rows.length ? rows.map((w) => place(w)) : <EmptyState size="inline" className="px-3" title="None yet" />}
    </div>
  );

  return (
    <SettingsPage title="Inventory & warehouses" sub="Where parts and customers' items are kept, and how stock is tracked."
      actions={<Button variant="primary" onClick={onAdd} icon={<M n="add" c="text-sm" />}>Add place</Button>}>
      <Section icon="warehouse" title="Where things are kept" description="Parts you sell, and the customers' items you're repairing, each have their own places.">
        {warehouses == null ? (
          <div className="grid md:grid-cols-2 gap-4" aria-hidden="true"><div className="sk h-14 rounded-xl" /><div className="sk h-14 rounded-xl" /></div>
        ) : (
          <div className="grid md:grid-cols-2 gap-4">
            {group("Parts and stock", parts)}
            {group("Customers' items", items)}
          </div>
        )}
      </Section>

      {c && (<>
        <Section icon="inventory_2" title="Tracking stock">
          <div className="divide-y divide-slate-100">
            {sw("enable_bins", "Shelf and bin locations", "Record exactly where each part sits.")}
            {sw("enable_reservations", "Hold parts for a job", "A part added to a job is set aside so it isn't sold.")}
            {c.enable_reservations && (
              <SettingRow htmlFor="inv-hold-days" label="Release held parts after" help="A held part goes back on sale if the job hasn't used it by then.">
                <NumField id="inv-hold-days" value={c.reservation_max_days ?? 14} onChange={(v) => set("reservation_max_days", v)} unit="days" />
              </SettingRow>
            )}
            <div className="py-4 last:pb-0">
              <div className="text-sm font-medium text-ink-primary">Parts that arrive for a job</div>
              <div className="text-xs text-ink-tertiary mb-2">When a purchase order raised from a job is received.</div>
              <div className="grid sm:grid-cols-2 gap-2">
                {([["auto", "Write off automatically", "bolt", "Stock goes straight to the job the moment it's received."],
                   ["manual", "Staff write it off", "back_hand", "Parts land in stock; someone writes them off on the job."]] as const).map(([v, l, icon, hint]) => {
                  const on = (c.po_to_job || "auto") === v;
                  return (
                    <button key={v} type="button" onClick={() => set("po_to_job", v)} aria-pressed={on}
                      className={`text-left rounded-xl border p-3 transition-colors ${on ? "border-primary bg-primary/5 ring-2 ring-primary/15" : "border-slate-200 hover:border-slate-300"}`}>
                      <span className="flex items-center gap-2 text-xs font-semibold text-ink-primary"><M n={icon} c={`text-sm ${on ? "text-primary" : "text-ink-tertiary"}`} />{l}</span>
                      <span className="block text-2xs text-ink-tertiary mt-0.5">{hint}</span>
                    </button>
                  );
                })}
              </div>
            </div>
          </div>
          {footer("track", TRACK_KEYS)}
        </Section>

        <Section icon="qr_code_2" title="Product codes" description="How products are identified and how new codes are made. Codes you already have are never changed.">
          <div className="divide-y divide-slate-100">
            <SettingRow stack label="Each product is unique by" help="Two products can't share it, and imports use it to find the product to update. SKUs and barcodes are always unique.">
              <Tabs look="segmented" label="Each product is unique by" value={c.unique_identifier || "name"} onChange={(v) => set("unique_identifier", v)}
                tabs={[{ id: "name", label: "Name" }, { id: "sku", label: "SKU" }, { id: "barcode", label: "Barcode" }, { id: "code", label: "Code" }]} />
            </SettingRow>
            {text("default_product_type", "New products start as", { def: "default" })}
            <SettingRow stack label="New barcodes start with" htmlFor="inv-barcode_prefix" help="20–29 is set aside for a shop's own barcodes, so they never match a manufacturer's.">
              <Select id="inv-barcode_prefix" value={c.barcode_prefix || "20"} onChange={(e) => set("barcode_prefix", e.target.value)} aria-label="Barcode prefix" className={rowField}>
                {Array.from({ length: 10 }, (_, i) => String(20 + i)).map((v) => <option key={v} value={v}>{v}</option>)}
              </Select>
            </SettingRow>
            <div className="py-4 last:pb-0">
              <Button variant="link" onClick={() => setPatterns((v) => !v)} aria-expanded={patterns} icon={<M n={patterns ? "expand_less" : "expand_more"} c="text-sm" />}>
                Custom patterns for new codes
              </Button>
              {!patterns && <p className="text-xs text-ink-tertiary mt-1">{[c.sku_template, c.product_serial_template, c.bin_template].some(Boolean) ? "Your own patterns are in use for new SKUs, serials or bin codes." : "New SKUs, serials and bin codes use the standard format."}</p>}
            </div>
            {patterns && text("sku_template", "SKU pattern", { help: "Fills the SKU of new products left blank. {CAT} = first letters of the category. Empty = off.", placeholder: "{CAT}-{C:5}", mono: true })}
            {patterns && text("product_serial_template", "Serial number pattern", { help: "For units that arrive without a serial. {C:6} = a 6-digit counter.", placeholder: "SN{C:6}", mono: true })}
            {patterns && text("bin_template", "Bin code pattern", { help: "The code printed on a new shelf or bin label.", placeholder: "BIN-{C:4}", mono: true })}
          </div>
          {footer("codes", CODE_KEYS)}
        </Section>

        <Section icon="devices" title="Customers' items" description="Defaults when an item is checked in without these details.">
          <div className="divide-y divide-slate-100">
            {text("default_vehicle_type", "Default item type", { def: "Car" })}
            {text("asset_serial_template", "Serial number pattern", { help: "For items checked in without a serial. {T} = first letters of the item type.", placeholder: "{T}-{C:6}", mono: true })}
          </div>
          {footer("items", ITEM_KEYS)}
        </Section>

        <Section icon="shopping_cart" title="Purchasing">
          <SettingRow label="Purchase orders over this need approval" htmlFor="inv-po" help="0 means no approval is needed.">
            <div className="relative w-40"><span className="absolute left-3 top-2 text-sm text-ink-tertiary">{currency || "$"}</span>
              <Input id="inv-po" type="number" min={0} value={c.po_approval_threshold ?? 0} onChange={(e) => set("po_approval_threshold", Number(e.target.value))} className="pl-8 tabular-nums" /></div>
          </SettingRow>
          {footer("buy", BUY_KEYS)}
        </Section>
      </>)}
    </SettingsPage>
  );
}
