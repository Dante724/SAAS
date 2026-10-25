import { humanize } from "../../lib/humanize";
import { useEffect, useState } from "react";
import { M } from "../../components/Shell";
import { useMe } from "../../context/Me";
import { api } from "../../api/client";
import { sortBarcodes } from "../../lib/barcodes";
import { Product, currencySymbol, money } from "./types";
import CustomFields from "../../components/CustomFields";
import CategoryPicker from "../../components/CategoryPicker";
import CatalogPicker, { CatalogItem } from "../../components/CatalogPicker";
import { useFormLayout, LayoutFields, reqStar, missingRequired, has } from "../../lib/formLayout";
import ServiceForm, { serviceBody } from "./ServiceForm";

export type DrawerKind = "product" | "service" | "asset" | "bundle";
type Props = { kind: DrawerKind; id?: string | null; initial?: any; onClose: () => void; onSaved?: () => void };

const BREADCRUMB: Record<DrawerKind, string> = { product: "Inventory › Stock", service: "Catalog › Services", asset: "Inventory › Devices & assets", bundle: "Catalog › Job Kits" };
const lbl = "block font-semibold text-ink-secondary mb-1";
const field = "glass-input w-full px-3 py-2 rounded-lg text-ink-primary text-xs";

type ProdTab = "details" | "stock" | "movements" | "purchasing" | "batches";
type Movement = { type: string; qty: number; at?: string; ref_type?: string; ref_id?: string; location?: string; unit_cost?: number };
type POItem = { id: string; po_number?: string; status?: string; qty_ordered?: number; qty_received?: number; expected_at?: string };
type Reservation = { id: string; part_id?: string; qty: number; order_id?: string; expires_at?: string };
type StockRow = { location: string; available: number; reserved: number; value: number };
type StockResp = { total_available: number; total_reserved: number; total_value: number; unit_cost: number; items: StockRow[] };
type Batch = { id: string; document?: string | null; supplier?: string | null; location?: string | null; warehouse?: string | null; unit_cost?: number; qty_received?: number; qty_remaining?: number; amount?: number; expiry_date?: string | null; received_at?: string | null; in_stock?: boolean };
type LocLite = { id: string; name: string };
const fmtDate = (s?: string) => (s ? new Date(s).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" }) : "—");

// Inlines the authed barcode/QR SVG (/catalog/code.svg needs the bearer, so <img src> can't be used).
type CodeType = "code128" | "qr" | "ean13" | "upca";
const svgType = (t?: string | null): CodeType => (t === "qr" || t === "ean13" || t === "upca" ? t : "code128");

function CodeSvg({ data, type }: { data: string; type: CodeType }) {
  const [svg, setSvg] = useState("");
  useEffect(() => {
    let tok = ""; try { tok = localStorage.getItem("ros_tok") || ""; } catch { /* ignore */ }
    fetch(`/api/catalog/code.svg?type=${type}&data=${encodeURIComponent(data)}`, { headers: tok ? { Authorization: `Bearer ${tok}` } : {} })
      .then((r) => (r.ok ? r.text() : "")).then(setSvg).catch(() => setSvg(""));
  }, [data, type]);
  return <div className={`${type === "qr" ? "w-16 h-16" : "h-12 w-40"} bg-white flex items-center justify-center [&>svg]:max-w-full [&>svg]:max-h-full`} dangerouslySetInnerHTML={{ __html: svg || "" }} />;
}

export default function InventoryDrawer({ kind, id, initial, onClose, onSaved }: Props) {
  const { can } = useMe();
  const [p, setP] = useState<any>(initial || {});
  const [compKind, setCompKind] = useState<"service" | "product">("service");  // 144cz — job-kit component picker tab
  const [toast, setToast] = useState<string | null>(null);
  const [ptab, setPtab] = useState<ProdTab>("details");
  const [moves, setMoves] = useState<Movement[] | null>(null);
  const [mvFilter, setMvFilter] = useState<"all" | "in" | "out">("all");
  const [pos, setPos] = useState<POItem[] | null>(null);
  const [resv, setResv] = useState<Reservation[] | null>(null);
  const [stock, setStock] = useState<StockResp | null>(null);
  const [batches, setBatches] = useState<Batch[] | null>(null);
  // Manual stock adjust (receive / write-off / correction) — backend: POST /inventory/parts/:id/adjust
  const [locs, setLocs] = useState<LocLite[]>([]);
  const [adjOpen, setAdjOpen] = useState(false);
  const [adjLoc, setAdjLoc] = useState(""); const [adjQty, setAdjQty] = useState(""); const [adjType, setAdjType] = useState("receive"); const [adjNote, setAdjNote] = useState(""); const [adjBusy, setAdjBusy] = useState(false);
  const flash = (m: string) => { setToast(m); setTimeout(() => setToast(null), 2000); };
  const editing = !!id;
  const [codeBusy, setCodeBusy] = useState(false);
  const [showCode, setShowCode] = useState(false);
  // ii144a — a product can answer to several barcodes (a supplier's EAN, a repack's UPC).
  const [codes, setCodes] = useState<{ id: string; barcode: string; is_main: boolean }[] | null>(null);
  const [newCode, setNewCode] = useState("");
  useEffect(() => {
    if (kind === "product" && id && codes === null)
      api.get<{ items: { id: string; barcode: string; is_main: boolean }[] }>(`/catalog/products/${id}/barcodes`)
        .then((r) => setCodes(sortBarcodes(r.items || []))).catch(() => setCodes([]));
  }, [kind, id, codes]);
  async function addCode() {
    const v = newCode.trim();
    if (!v || !id) return;
    setCodeBusy(true);
    try {
      const r = await api.post<{ barcode: string; is_main: boolean }>(`/catalog/products/${id}/barcodes`, { barcode: v });
      if (r.is_main) set("barcode", r.barcode);
      setNewCode(""); setCodes(null); flash("Barcode added");
    } catch (e: any) { flash(e.message || "Couldn't add the barcode"); }
    finally { setCodeBusy(false); }
  }
  async function removeCode(c: { id: string; barcode: string; is_main: boolean }) {
    if (!id) return;
    setCodeBusy(true);
    try {
      await api.del(`/catalog/products/${id}/barcodes/${c.id}`);
      if (c.is_main) set("barcode", null);
      setCodes(null); flash("Barcode removed");
    } catch (e: any) { flash(e.message || "Couldn't remove the barcode"); }
    finally { setCodeBusy(false); }
  }
  // Assign + persist a barcode or QR value to this product, then preview it. Needs a saved
  // product (the endpoint writes to the row). ii144a: a barcode is a shop-made EAN-13 (20–29),
  // valid at any till and never a manufacturer's number.
  async function genCode(type: "ean13" | "qr") {
    if (!id) { flash("Save the product first, then generate a code"); return; }
    setCodeBusy(true);
    try {
      const r = await api.post<{ barcode: string; barcode_type: string }>(`/catalog/products/${id}/barcode/generate?type=${type}`, {});
      set("barcode", r.barcode); set("barcode_type", r.barcode_type); setShowCode(true); setCodes(null);
      flash(type === "qr" ? "QR generated" : "Barcode generated");
    } catch (e: any) { flash(e.message || "Could not generate code"); }
    finally { setCodeBusy(false); }
  }

  useEffect(() => {
    setPtab("details"); setMoves(null); setPos(null); setResv(null); setStock(null); setBatches(null); setMvFilter("all"); setAdjOpen(false);
    if (kind === "product" && id) api.get<{ product: Product }>(`/catalog/products/${id}`).then((r) => setP(r.product)).catch(() => {});
    else if (initial) setP(initial);
  }, [kind, id]);

  // Locations for the adjust form (id + name) — loaded once when editing a product.
  useEffect(() => {
    if (kind === "product" && id && locs.length === 0)
      api.get<{ items: LocLite[] }>(`/staff/locations`).then((r) => { const it = r.items || []; setLocs(it); if (it[0]) setAdjLoc((c) => c || it[0].id); }).catch(() => {});
  }, [kind, id]);

  useEffect(() => {
    if (kind !== "product" || !id) return;
    if (ptab === "stock" && stock === null)
      api.get<StockResp>(`/inventory/parts/${id}/stock`).then(setStock).catch(() => setStock({ total_available: 0, total_reserved: 0, total_value: 0, unit_cost: 0, items: [] }));
    if (ptab === "movements" && moves === null)
      api.get<Movement[]>(`/inventory/parts/${id}/movements`).then((r) => setMoves(Array.isArray(r) ? r : [])).catch(() => setMoves([]));
    if (ptab === "batches" && batches === null)
      api.get<{ items: Batch[] }>(`/inventory/parts/${id}/batches`).then((r) => setBatches(r.items || [])).catch(() => setBatches([]));
    if (ptab === "purchasing" && pos === null) {
      api.get<{ items: POItem[] }>(`/inventory/parts/${id}/purchase-orders`).then((r) => setPos(r.items || [])).catch(() => setPos([]));
      api.get<{ items: Reservation[] }>(`/inventory/reservations`).then((r) => setResv((r.items || []).filter((x) => x.part_id === id))).catch(() => setResv([]));
    }
  }, [ptab, kind, id, moves, pos, stock, batches]);

  async function submitAdjust() {
    if (!adjLoc || !adjQty) { flash("Pick a location and quantity"); return; }
    setAdjBusy(true);
    try {
      await api.post(`/inventory/parts/${id}/adjust`, { location_id: adjLoc, qty: Number(adjQty), movement_type: adjType, note: adjNote || null });
      flash("Stock adjusted");
      setAdjOpen(false); setAdjQty(""); setAdjNote("");
      setStock(null); setMoves(null); setBatches(null);   // refetch affected tabs
    } catch (e: any) { flash(e.message || "Adjust failed"); } finally { setAdjBusy(false); }
  }

  const set = (k: string, v: any) => setP((s: any) => ({ ...s, [k]: v }));
  const margin = (() => { const sell = Number(p.sell_price ?? p.price ?? 0), cost = Number(p.cost_price ?? p.avg_cost ?? 0); return sell > 0 ? Math.round((sell - cost) / sell * 100) : 0; })();

  const layout = useFormLayout("product", kind === "product");
  const star = (k: string) => reqStar(layout, k);
  async function save() {
    try {
      if (kind === "product") {
        const m = missingRequired(layout, { category: has(p.category_id), description: has(p.description), code: has(p.code), sku: has(p.sku), mpn: has(p.mpn) }, p.custom_data);
        if (m) { flash(m); return; }
        // Everything this drawer shows goes back; fields it doesn't show (weight, image…) are left out
        // and the API keeps them (it only writes what was sent).
        const body = { name: p.name, code: p.code, sku: p.sku, mpn: (p.mpn || "").trim() || null, barcode: p.barcode, barcode_type: p.barcode_type || "code128", brand: p.brand, description: p.description,
          category_id: p.category_id || null, uom: p.uom || "pcs", cost_price: Number(p.cost_price) || 0, sell_price: Number(p.sell_price) || 0,
          min_stock: p.min_stock ?? 0, serialized: !!p.serialized, expiry_tracking: !!p.expiry_tracking, warranty_days: p.warranty_days ?? 0,
          compatibility_json: p.compatibility_json || {}, custom_data: p.custom_data || {} };
        if (editing) await api.put(`/catalog/products/${id}`, body); else await api.post("/catalog/products", body);
      } else if (kind === "service") {
        const body = serviceBody(p);
        if (!body.name) { flash("Service title is required"); return; }
        if (editing) await api.put(`/catalog/services/${id}`, body); else await api.post("/catalog/services", body);
      } else if (kind === "bundle") {
        if (!can("can_manage_bundles")) { flash("Your role can't change job kits"); return; }
        const components = (p.components || []).map((c: any) => ({ ref_type: c.ref_type, ref_id: c.ref_id, qty: Number(c.qty) || 1, device_variant: !!c.device_variant }));
        if (components.length === 0) { flash("Add at least one service or product"); return; }
        const body = { name: p.name, price_mode: p.price_mode || "sum", price: Number(p.price) || 0, category_id: p.category_id || null, components };
        if (editing) await api.put(`/bundles/${id}`, body); else await api.post("/bundles", body);
      } else { flash("Saved (view only for this type)"); onSaved?.(); return; }
      flash(editing ? "Saved" : "Created"); onSaved?.(); onClose();
    } catch (e: any) { flash(`Failed: ${e.message}`); }
  }

  const footerLabel = editing ? "Save changes"
    : kind === "product" ? "Create product & add stock"
    : kind === "service" ? "Create service"
    : kind === "bundle" ? "Create job kit" : "Register asset";

  return (
    <div className="fixed inset-0 flex justify-end" style={{ zIndex: 50 }}>
      <div className="absolute inset-0 bg-ink-primary/35 backdrop-blur-[2px]" onClick={onClose} />
      <div className="relative h-full w-full max-w-[540px] bg-white shadow-clay-drawer flex flex-col border-l border-slate-200/90 animate-[slideIn_.3s_cubic-bezier(.16,1,.3,1)]">
        <style>{`@keyframes slideIn{from{transform:translateX(100%)}to{transform:translateX(0)}}`}</style>
        <div className="px-6 py-4 border-b border-slate-100 flex items-center justify-between bg-slate-50/70">
          <div className="flex items-center gap-3 min-w-0">
            {kind === "product" && editing && (
              p.image_data
                ? <img src={p.image_data} alt="" className="w-10 h-10 rounded-xl object-cover border border-slate-200 shrink-0" />
                : <div className="w-10 h-10 rounded-xl bg-blue-50 text-primary flex items-center justify-center border border-blue-100 shrink-0"><M n="inventory_2" c="text-xl" /></div>
            )}
            <div className="min-w-0">
              <span className="text-[11px] font-bold text-primary block">{BREADCRUMB[kind]}</span>
              <h3 className="text-base font-bold text-ink-primary truncate">{p.name || (editing ? "Edit item" : `New ${kind === "bundle" ? "job kit" : kind}`)}</h3>
              {kind === "product" && editing && (p.sku || p.code || p.barcode) && (
                <p className="text-[11px] text-ink-tertiary font-mono truncate">{p.sku || p.code}{p.barcode ? ` · ${p.barcode}` : ""}</p>
              )}
            </div>
            {kind === "product" && editing && p.low_stock && <span className="px-2 py-0.5 rounded-full text-[11px] font-bold bg-rose-100 text-rose-800 border border-rose-200 shrink-0">Low stock</span>}
          </div>
          <button aria-label="Close" onClick={onClose} className="w-8 h-8 rounded-lg hover:bg-slate-200/60 flex items-center justify-center text-ink-tertiary hover:text-ink-primary shrink-0"><M n="close" c="text-base" /></button>
        </div>

        {kind === "product" && editing && (
          <nav className="px-6 border-b border-slate-200 flex gap-6 text-xs font-semibold bg-slate-50/70">
            {([["details", "Details"], ["stock", "Stock"], ["batches", "Batches"], ["movements", "Movements"], ["purchasing", "Purchasing"]] as [ProdTab, string][]).map(([t, label]) => (
              <button key={t} onClick={() => setPtab(t)} className={`py-3 border-b-2 transition-colors ${ptab === t ? "text-primary border-primary font-bold" : "text-ink-secondary border-transparent hover:text-ink-primary"}`}>{label}</button>
            ))}
          </nav>
        )}

        <div className="flex-1 overflow-y-auto p-6 space-y-5 text-xs">
          {kind === "product" && editing && ptab === "details" && (
            <div className="grid grid-cols-4 gap-2.5">
              {[["Available", (p.qty_available ?? 0), p.low_stock ? "text-rose-600" : "text-ink-primary"],
                ["Reserved", (p.qty_reserved ?? 0), "text-ink-primary"],
                ["Avg cost", money(p.avg_cost ?? p.cost_price ?? 0), "text-ink-primary"],
                ["Stock value", money((p.qty_available ?? 0) * Number(p.avg_cost ?? p.cost_price ?? 0)), "text-emerald-600"]].map(([l, v, c]) => (
                <div key={l as string} className="bg-white rounded-xl p-3 border border-slate-200/90 shadow-sm">
                  <span className="text-[11px] font-bold text-ink-tertiary block">{l}</span>
                  <p className={`text-lg font-bold tabular-nums truncate ${c}`}>{v}</p>
                </div>
              ))}
            </div>
          )}
          {(kind === "asset" || (kind === "product" && ptab === "details" && !(editing && p.image_data))) && (
            <div className="border-2 border-dashed border-slate-200 hover:border-primary/50 rounded-2xl p-5 text-center cursor-pointer bg-slate-50/40 hover:bg-blue-50/20 transition-all">
              <div className="w-10 h-10 rounded-xl bg-blue-50 border border-blue-100 text-primary flex items-center justify-center mx-auto mb-2"><M n="add_a_photo" c="text-xl" /></div>
              <span className="text-xs font-semibold text-ink-secondary block">Drop image or browse, or take a photo</span>
              <span className="text-[11px] text-ink-tertiary">PNG, JPG, WebP up to 5MB</span>
            </div>
          )}

          {kind === "product" && ptab === "details" && (<>
            <div className="space-y-3.5">
              <div><label className={lbl}>Name <span className="text-rose-500">*</span></label><input value={p.name || ""} onChange={(e) => set("name", e.target.value)} className={field} /></div>
              <div className="grid grid-cols-2 gap-3">
                <div><label className={lbl}>Type</label><select value={p.type || "Part"} onChange={(e) => set("type", e.target.value)} className={`${field} appearance-none`}><option>Part</option><option>Accessory</option><option>Consumable</option></select></div>
                <div><label className={lbl}>Unit <span className="text-rose-500">*</span></label><input value={p.uom || "pcs"} onChange={(e) => set("uom", e.target.value)} className={field} /></div>
              </div>
              <div className="pt-1 flex items-center gap-2 flex-wrap">
                <button type="button" onClick={() => setShowCode((v) => !v)} disabled={!p.barcode}
                  className="px-2.5 py-1 rounded-lg bg-slate-100 hover:bg-slate-200 text-ink-secondary text-xs font-semibold flex items-center gap-1.5 disabled:opacity-50">
                  <M n="barcode" c="text-xs" />{showCode ? "Hide code" : "Show code"}</button>
                <button type="button" onClick={() => genCode("ean13")} disabled={codeBusy || !editing || !!p.barcode} title={!editing ? "Save first" : p.barcode ? "This product already has a barcode" : ""}
                  className="px-2.5 py-1 rounded-lg border border-slate-200 hover:bg-slate-50 text-ink-secondary text-xs font-medium disabled:opacity-50">Generate barcode</button>
                <button type="button" onClick={() => genCode("qr")} disabled={codeBusy || !editing} title={editing ? "" : "Save first"}
                  className="px-2.5 py-1 rounded-lg border border-slate-200 hover:bg-slate-50 text-ink-secondary text-xs font-medium disabled:opacity-50">Generate QR</button>
              </div>
              {showCode && p.barcode && (
                <div className="flex items-center gap-3 p-2.5 rounded-lg bg-white border border-slate-200">
                  <CodeSvg data={p.barcode} type={svgType(p.barcode_type)} />
                  <span className="font-mono text-xs text-ink-secondary break-all">{p.barcode}</span>
                </div>
              )}
              {editing && (
                <div className="pt-1">
                  <div className={lbl}>Barcodes <span className="font-normal text-ink-tertiary">(scanning any of them finds this product)</span></div>
                  {codes === null ? <div className="text-[11px] text-ink-tertiary">Loading…</div> : (
                    <ul className="space-y-1">
                      {codes.map((c) => (
                        <li key={c.id} className="flex items-center gap-2 text-xs">
                          <span className="font-mono text-ink-primary break-all">{c.barcode}</span>
                          {c.is_main && <span className="px-1.5 py-0.5 rounded bg-slate-100 text-[11px] text-ink-secondary">Main</span>}
                          <button type="button" onClick={() => removeCode(c)} disabled={codeBusy} aria-label={`Remove barcode ${c.barcode}`}
                            className="ml-auto w-6 h-6 rounded-md grid place-items-center text-ink-tertiary hover:bg-rose-50 hover:text-rose-600 disabled:opacity-50"><M n="close" c="text-sm" /></button>
                        </li>
                      ))}
                      {codes.length === 0 && <li className="text-[11px] text-ink-tertiary">No barcode yet.</li>}
                    </ul>
                  )}
                  <div className="mt-1.5 flex items-center gap-2">
                    <input value={newCode} onChange={(e) => setNewCode(e.target.value)} maxLength={100} aria-label="Add a barcode"
                      onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); addCode(); } }}
                      placeholder="Scan or type another barcode" className={`${field} font-mono`} />
                    <button type="button" onClick={addCode} disabled={codeBusy || !newCode.trim()}
                      className="px-2.5 py-2 rounded-lg border border-slate-200 hover:bg-slate-50 text-ink-secondary text-xs font-medium disabled:opacity-50 whitespace-nowrap">Add</button>
                  </div>
                </div>
              )}
            </div>
            {/* Built-in and custom fields in the order set in Settings → Forms → Customize. */}
            <LayoutFields layout={layout} gap="gap-x-3 gap-y-4" blocks={{
              category: { half: true, el: <div><label className={lbl}>Category{star("category")}</label><CategoryPicker entity="product" value={p.category_id} onChange={(id) => set("category_id", id)} /></div> },
              description: { el: <div><label className={lbl}>Description{star("description")}</label><textarea value={p.description || ""} onChange={(e) => set("description", e.target.value)} className={`${field} h-20 resize-none`} placeholder="Optional details..." /></div> },
              code: { half: true, el: <div><label className={lbl}>Code{star("code")}</label><input value={p.code || ""} onChange={(e) => set("code", e.target.value)} className={`${field} font-mono`} /></div> },
              sku: { half: true, el: <div><label className={lbl}>SKU{star("sku")}</label><input value={p.sku || ""} onChange={(e) => set("sku", e.target.value)} className={`${field} font-mono`} /></div> },
              mpn: { half: true, el: <div><label htmlFor="prod-mpn" className={lbl}>MPN{star("mpn")} <span className="font-normal text-ink-tertiary">(manufacturer part number)</span></label><input id="prod-mpn" value={p.mpn || ""} maxLength={64} onChange={(e) => set("mpn", e.target.value)} className={`${field} font-mono`} /></div> },
              stock_policy: { el: <>
                <div className="p-4 rounded-xl bg-slate-50/90 border border-slate-200/80 space-y-3">
                  <div className="text-[11px] font-bold text-ink-primary ">Stock tracking & policies</div>
                  <label className="flex items-center justify-between cursor-pointer"><span className="text-ink-secondary font-medium">Expiration tracking</span><input type="checkbox" checked={!!p.expiry_tracking} onChange={(e) => set("expiry_tracking", e.target.checked)} className="w-4 h-4 rounded text-primary border-slate-300" /></label>
                  <label className="flex items-center justify-between cursor-pointer"><span className="text-ink-secondary font-medium">Enable serial-number tracking <span className="ml-1 inline-flex items-center px-1.5 py-0.5 rounded text-[11px] font-bold bg-emerald-100 text-emerald-800">one-way</span></span><input type="checkbox" checked={!!p.serialized} disabled={!p.serialized && !can("can_track_serials")} onChange={(e) => set("serialized", e.target.checked)} className="w-4 h-4 rounded text-primary border-slate-300" /></label>
                  <div className="flex items-center justify-between"><span className="text-ink-secondary font-medium">Min stock (reorder at)</span><input type="number" value={p.min_stock ?? 0} onChange={(e) => set("min_stock", Number(e.target.value))} className="glass-input w-20 px-2 py-1 rounded-lg text-xs text-right font-mono" /></div>
                </div>
              </> },
              pricing: { el: <>
                <div className="p-4 rounded-xl bg-blue-50/50 border border-blue-100 space-y-3">
                  <div className="flex items-center justify-between"><span className="text-[11px] font-bold text-primary ">Pricing & margin</span><span className="px-2 py-0.5 rounded-full text-[11px] font-bold bg-emerald-100 text-emerald-800">Margin: {margin}%</span></div>
                  <div className="grid grid-cols-2 gap-3">
                    <div><label className={lbl}>Cost price ({currencySymbol()})</label><input type="number" value={p.cost_price ?? 0} onChange={(e) => set("cost_price", e.target.value)} className={`${field} font-mono font-bold bg-white`} /></div>
                    <div><label className={lbl}>Store price ({currencySymbol()})</label><input type="number" value={p.sell_price ?? 0} onChange={(e) => set("sell_price", e.target.value)} className={`${field} font-mono font-bold bg-white`} /></div>
                  </div>
                  <div className="text-[11px] text-ink-tertiary flex items-center justify-between pt-1"><span>Repair price (on work orders):</span><span className="font-semibold text-ink-secondary">= store price ({money(Number(p.sell_price) || 0)})</span></div>
                </div>
              </> },
              compatibility: { el: <>
                {(() => {
                  const compat = p.compatibility_json || {};
                  const fits: { brand?: string; model?: string }[] = Array.isArray(compat.fits) ? compat.fits : [];
                  const setFits = (next: any[]) => set("compatibility_json", { ...compat, fits: next });
                  return (
                    <div className="p-4 rounded-xl bg-slate-50/90 border border-slate-200/80 space-y-2">
                      <div className="flex items-center gap-2">
                        <span className="text-[11px] font-bold text-ink-primary ">Compatible devices</span>
                        <button type="button" onClick={() => setFits([...fits, { brand: "", model: "" }])} className="text-[11px] font-bold text-primary hover:underline">+ Add</button>
                      </div>
                      <p className="text-[11px] text-ink-tertiary">Flagged as “Fits” when adding parts on a matching device. Blank model = any model of that brand.</p>
                      {fits.length === 0 && <p className="text-[11px] text-ink-tertiary">None yet.</p>}
                      {fits.map((f, i) => (
                        <div key={i} className="flex items-center gap-1.5">
                          <input className={field} placeholder="Brand (e.g. Apple)" value={f.brand || ""} onChange={(e) => setFits(fits.map((x, j) => j === i ? { ...x, brand: e.target.value } : x))} />
                          <input className={field} placeholder="Model (blank = any)" value={f.model || ""} onChange={(e) => setFits(fits.map((x, j) => j === i ? { ...x, model: e.target.value } : x))} />
                          <button aria-label="Close" type="button" onClick={() => setFits(fits.filter((_, j) => j !== i))} className="text-ink-tertiary hover:text-rose-500 shrink-0"><M n="close" c="text-sm" /></button>
                        </div>
                      ))}
                    </div>
                  );
                })()}
              </> },
            }} custom={(ids) => <CustomFields entity="product" only={ids} bare={!!ids} value={p.custom_data || {}} onChange={(cd) => set("custom_data", cd)} creating={!editing} />} />
          </>)}

          {kind === "product" && ptab === "stock" && (<>
            <div className="flex items-center justify-between mb-3">
              <span className="text-[11px] font-bold text-ink-tertiary">Stock by location</span>
              <button type="button" onClick={() => setAdjOpen((o) => !o)} className="flex items-center gap-1 px-2.5 py-1.5 rounded-lg bg-blue-50 text-primary border border-blue-100 text-[11px] font-bold hover:bg-blue-100 transition-colors"><M n={adjOpen ? "close" : "tune"} c="text-sm" />{adjOpen ? "Cancel" : "Adjust stock"}</button>
            </div>
            {adjOpen && (
              <div className="mb-3 rounded-2xl bg-slate-50/70 border border-slate-200/80 p-3.5 space-y-3">
                <div className="grid grid-cols-2 gap-3">
                  <div className="space-y-1"><label className="block text-[11px] font-bold text-ink-tertiary">Location</label>
                    <div className="relative"><select value={adjLoc} onChange={(e) => setAdjLoc(e.target.value)} className="glass-input block w-full py-2 px-3 text-xs font-semibold text-ink-primary rounded-lg appearance-none">{locs.map((l) => <option key={l.id} value={l.id}>{l.name}</option>)}</select><M n="expand_more" c="absolute right-2 top-2 text-ink-tertiary pointer-events-none text-xs" /></div></div>
                  <div className="space-y-1"><label className="block text-[11px] font-bold text-ink-tertiary">Type</label>
                    <div className="relative"><select value={adjType} onChange={(e) => setAdjType(e.target.value)} className="glass-input block w-full py-2 px-3 text-xs font-semibold text-ink-primary rounded-lg appearance-none"><option value="receive">Receive (+)</option><option value="writeoff">Write-off (−)</option><option value="adjust">Correction</option></select><M n="expand_more" c="absolute right-2 top-2 text-ink-tertiary pointer-events-none text-xs" /></div></div>
                </div>
                <div className="grid grid-cols-2 gap-3">
                  <div className="space-y-1"><label className="block text-[11px] font-bold text-ink-tertiary">Quantity</label><input type="number" value={adjQty} onChange={(e) => setAdjQty(e.target.value)} className="glass-input font-mono block w-full py-2 px-3 text-xs font-semibold text-ink-primary rounded-lg" placeholder="0" /></div>
                  <div className="space-y-1"><label className="block text-[11px] font-bold text-ink-tertiary">Note</label><input value={adjNote} onChange={(e) => setAdjNote(e.target.value)} className="glass-input block w-full py-2 px-3 text-xs font-semibold text-ink-primary rounded-lg" placeholder="Reason (optional)" /></div>
                </div>
                <div className="flex justify-end"><button type="button" onClick={submitAdjust} disabled={adjBusy} className="px-4 py-2 rounded-xl bg-primary text-white text-xs font-bold shadow-clay-sm hover:brightness-110 disabled:opacity-60">{adjBusy ? "Saving…" : "Apply adjustment"}</button></div>
              </div>
            )}
            {stock === null
              ? <p className="text-ink-tertiary">Loading stock…</p>
              : stock.items.length === 0
                ? <p className="text-ink-tertiary">No stock at any location yet.</p>
                : <div className="bg-white rounded-2xl border border-slate-200/90 shadow-sm overflow-hidden">
                    <div className="grid grid-cols-[1fr_auto_auto_auto] gap-3 px-4 py-2.5 border-b border-slate-100 text-[11px] font-bold text-ink-tertiary"><span>Location</span><span className="text-right">Reserved</span><span className="text-right">In stock</span><span className="text-right">Value</span></div>
                    <div className="divide-y divide-slate-100">
                      {stock.items.map((s, i) => (
                        <div key={i} className="grid grid-cols-[1fr_auto_auto_auto] gap-3 px-4 py-2.5 items-center">
                          <span className="font-semibold text-ink-primary truncate">{s.location}</span>
                          <span className="text-right tabular-nums text-ink-secondary">{s.reserved}</span>
                          <span className="text-right tabular-nums font-bold text-ink-primary">{s.available}</span>
                          <span className="text-right tabular-nums text-ink-secondary">{money(s.value ?? 0)}</span>
                        </div>
                      ))}
                    </div>
                    <div className="grid grid-cols-[1fr_auto_auto_auto] gap-3 px-4 py-2.5 bg-slate-50 border-t border-slate-200 text-xs font-bold">
                      <span className="text-ink-tertiary text-[11px] self-center">Total</span>
                      <span className="text-right tabular-nums">{stock.total_reserved ?? 0}</span>
                      <span className="text-right tabular-nums">{stock.total_available}</span>
                      <span className="text-right tabular-nums text-emerald-600">{money(stock.total_value ?? 0)}</span>
                    </div>
                  </div>}
          </>)}

          {kind === "product" && ptab === "batches" && (
            batches === null
              ? <p className="text-ink-tertiary">Loading batches…</p>
              : batches.length === 0
                ? <p className="text-ink-tertiary">No cost/stock layers recorded yet. Batches appear as stock is received against purchase orders.</p>
                : <div className="bg-white rounded-2xl border border-slate-200/90 shadow-sm overflow-hidden">
                    <div className="grid grid-cols-[1fr_auto_auto_auto_auto] gap-3 px-4 py-2.5 border-b border-slate-100 text-[11px] font-bold text-ink-tertiary"><span>Document · Supplier</span><span className="text-right">Unit cost</span><span className="text-right">Remaining</span><span className="text-right">Amount</span><span className="text-right">Expiry</span></div>
                    <div className="divide-y divide-slate-100">
                      {batches.map((b) => (
                        <div key={b.id} className="grid grid-cols-[1fr_auto_auto_auto_auto] gap-3 px-4 py-2.5 items-center text-xs">
                          <div className="min-w-0"><span className="font-bold text-ink-primary tabular-nums">{b.document || "—"}</span>{b.supplier && <span className="ml-2 text-ink-tertiary">{b.supplier}</span>}<div className="text-[11px] text-ink-tertiary">{[b.warehouse, b.location].filter(Boolean).join(" · ") || "—"} · {fmtDate(b.received_at || undefined)}</div></div>
                          <span className="text-right tabular-nums text-ink-secondary">{money(b.unit_cost ?? 0)}</span>
                          <span className="text-right tabular-nums font-bold text-ink-primary">{b.qty_remaining ?? 0}<span className="text-ink-tertiary font-normal">/{b.qty_received ?? 0}</span></span>
                          <span className="text-right tabular-nums text-emerald-600">{money(b.amount ?? 0)}</span>
                          <span className="text-right text-ink-tertiary whitespace-nowrap">{b.expiry_date ? fmtDate(b.expiry_date) : "—"}</span>
                        </div>
                      ))}
                    </div>
                  </div>
          )}

          {kind === "product" && ptab === "movements" && (
            moves === null
              ? <p className="text-ink-tertiary">Loading movements…</p>
              : moves.length === 0
                ? <p className="text-ink-tertiary">No stock movements yet.</p>
                : (() => {
                    const shown = moves.filter((m) => mvFilter === "all" || (mvFilter === "in" ? m.qty >= 0 : m.qty < 0));
                    return <>
                      <div className="flex gap-1.5">
                        {([["all", "All"], ["in", "Inbound"], ["out", "Outbound"]] as ["all" | "in" | "out", string][]).map(([f, l]) => (
                          <button key={f} onClick={() => setMvFilter(f)} className={`px-3 py-1 rounded-lg text-[11px] font-bold border transition-colors ${mvFilter === f ? "bg-primary text-white border-primary" : "bg-white text-ink-secondary border-slate-200 hover:bg-slate-50"}`}>{l}</button>
                        ))}
                      </div>
                      <div className="bg-white rounded-2xl border border-slate-200/90 shadow-sm overflow-hidden mt-3">
                    <div className="grid grid-cols-[1fr_auto_auto] gap-3 px-4 py-2.5 border-b border-slate-100 text-[11px] font-bold text-ink-tertiary"><span>Type · reference</span><span className="text-right">Qty</span><span className="text-right">When</span></div>
                    <div className="divide-y divide-slate-100">
                      {shown.map((m, i) => (
                        <div key={i} className="grid grid-cols-[1fr_auto_auto] gap-3 px-4 py-2.5 items-center">
                          <div>
                            <span className="font-bold text-ink-primary capitalize">{(m.type || "").replace(/_/g, " ")}</span>
                            {m.location && <span className="text-ink-tertiary"> · {m.location}</span>}
                            {m.ref_type && <div className="text-[11px] text-ink-tertiary font-mono">{humanize(m.ref_type)}{m.ref_id ? ` #${String(m.ref_id).slice(0, 8)}` : ""}{m.unit_cost ? ` · ${money(m.unit_cost)}` : ""}</div>}
                          </div>
                          <span className={`text-right tabular-nums font-bold ${m.qty >= 0 ? "text-emerald-600" : "text-rose-600"}`}>{m.qty >= 0 ? "+" : ""}{m.qty}</span>
                          <span className="text-right text-ink-tertiary whitespace-nowrap">{fmtDate(m.at)}</span>
                        </div>
                      ))}
                      {shown.length === 0 && <p className="text-ink-tertiary px-4 py-3">No {mvFilter === "in" ? "inbound" : "outbound"} movements.</p>}
                    </div>
                      </div>
                    </>;
                  })()
          )}

          {kind === "product" && ptab === "purchasing" && (<>
            <div className="bg-white rounded-2xl border border-slate-200/90 shadow-sm overflow-hidden">
              <div className="p-3.5 border-b border-slate-100 text-[11px] font-bold text-ink-primary ">Purchase orders{pos ? ` (${pos.length})` : ""}</div>
              {pos === null ? <p className="text-ink-tertiary p-4">Loading…</p>
                : pos.length === 0 ? <p className="text-ink-tertiary p-4">No purchase orders reference this product.</p>
                : <div className="divide-y divide-slate-100">
                    {pos.map((o) => (
                      <div key={o.id} className="px-4 py-2.5 flex items-center justify-between">
                        <div><span className="font-bold text-ink-primary font-mono">{o.po_number || o.id.slice(0, 8)}</span><span className="ml-2 px-1.5 py-0.5 rounded-full text-[11px] font-bold bg-slate-100 text-ink-secondary capitalize">{(o.status || "").replace(/_/g, " ")}</span></div>
                        <div className="text-right text-ink-secondary"><span className="tabular-nums font-semibold">{o.qty_received ?? 0}/{o.qty_ordered ?? 0}</span><span className="text-ink-tertiary"> · exp {fmtDate(o.expected_at)}</span></div>
                      </div>
                    ))}
                  </div>}
            </div>
            <div className="bg-white rounded-2xl border border-slate-200/90 shadow-sm overflow-hidden">
              <div className="p-3.5 border-b border-slate-100 text-[11px] font-bold text-ink-primary ">Reservations{resv ? ` (${resv.length})` : ""}</div>
              {resv === null ? <p className="text-ink-tertiary p-4">Loading…</p>
                : resv.length === 0 ? <p className="text-ink-tertiary p-4">No open reservations against this product.</p>
                : <div className="divide-y divide-slate-100">
                    {resv.map((r) => (
                      <div key={r.id} className="px-4 py-2.5 flex items-center justify-between"><span className="font-mono font-semibold text-ink-primary">Order {r.order_id ? `#${String(r.order_id).slice(0, 8)}` : "—"}</span><div className="text-right text-ink-secondary"><span className="font-mono font-bold">{r.qty}</span><span className="text-ink-tertiary"> · until {fmtDate(r.expires_at)}</span></div></div>
                    ))}
                  </div>}
            </div>
          </>)}

          {kind === "service" && <ServiceForm p={p} set={set} />}

          {kind === "asset" && (
            <div className="space-y-3.5">
              <div><label className={lbl}>Device / model</label><div className="glass-input w-full px-3 py-2 rounded-lg text-ink-primary text-xs font-semibold">{[p.brand, p.model].filter(Boolean).join(" ") || "—"}</div></div>
              <div className="grid grid-cols-2 gap-3">
                <div><label className={lbl}>Serial / IMEI</label><div className="glass-input w-full px-3 py-2 rounded-lg font-mono text-[11px]">{p.imei || p.serial || "—"}</div></div>
                <div><label className={lbl}>Ownership</label><div className="glass-input w-full px-3 py-2 rounded-lg text-xs font-semibold capitalize">{p.owner_type || "client"}</div></div>
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div><label className={lbl}>Condition</label><div className="glass-input w-full px-3 py-2 rounded-lg text-xs">{p.condition || "—"}</div></div>
                <div><label className={lbl}>Status</label><div className="glass-input w-full px-3 py-2 rounded-lg text-xs">{p.asset_status || "in use"}</div></div>
              </div>
              <p className="text-[11px] text-ink-tertiary">Full editing opens from the Work Order → asset card. This drawer shows the registered profile.</p>
            </div>
          )}

          {kind === "bundle" && (() => {
            const comps: any[] = p.components || [];
            const addComp = (i: CatalogItem) => {
              if ((i.kind as string) === "bundle") { flash("A job kit can't contain another job kit"); return; }
              if (comps.some((c) => c.ref_id === i.ref_id && c.ref_type === i.kind)) { flash("Already added"); return; }
              set("components", [...comps, { ref_type: i.kind, ref_id: i.ref_id, name: i.name, qty: 1, std_price: i.price || 0 }]);
            };
            const setQty = (idx: number, v: number) => set("components", comps.map((c, j) => j === idx ? { ...c, qty: Math.max(1, v || 1) } : c));
            const removeComp = (idx: number) => set("components", comps.filter((_, j) => j !== idx));
            const toggleVariant = (idx: number) => set("components", comps.map((c, j) => j === idx ? { ...c, device_variant: !c.device_variant } : c));
            const stdTotal = comps.reduce((s, c) => s + (c.std_price || 0) * (c.qty || 1), 0);
            return (
            <div className="space-y-3.5">
              <div><label className={lbl}>Job kit name <span className="text-rose-500">*</span></label><input value={p.name || ""} onChange={(e) => set("name", e.target.value)} className={field} /></div>
              <div className="p-4 rounded-xl bg-slate-50/90 border border-slate-200/80 space-y-2.5">
                <div className="text-[11px] font-bold text-ink-primary ">Services & products</div>
                {comps.length === 0 && <p className="text-[11px] text-ink-tertiary">No items yet — search below to add services and products.</p>}
                {comps.map((c: any, idx: number) => (
                  <div key={c.ref_id + idx} className="bg-white rounded-lg border border-slate-200/80 px-2.5 py-1.5 space-y-1">
                    <div className="flex items-center gap-2">
                      <span className={`text-[11px]  font-bold px-1.5 py-0.5 rounded ${c.ref_type === "service" ? "bg-emerald-100 text-emerald-700" : "bg-blue-100 text-primary"}`}>{c.ref_type === "service" ? "Service" : "Product"}</span>
                      <span className="flex-1 text-xs font-semibold text-ink-primary truncate">{c.name || c.ref_id}{c.missing && <span className="ml-1 text-[11px] font-bold text-amber-700">missing</span>}</span>
                      <input type="number" min={1} value={c.qty} onChange={(e) => setQty(idx, Number(e.target.value))} className="w-14 glass-input px-2 py-1 text-xs rounded-lg text-center" aria-label="Quantity" />
                      <span className="w-16 text-right tabular-nums text-[11px] text-ink-tertiary">{money((c.std_price || 0) * (c.qty || 1))}</span>
                      <button type="button" onClick={() => removeComp(idx)} aria-label="Remove" className="text-ink-tertiary hover:text-rose-600"><M n="close" c="text-sm" /></button>
                    </div>
                    {c.ref_type === "product" && (
                      <button type="button" onClick={() => toggleVariant(idx)} className="flex items-center gap-1.5 text-[11px] font-semibold text-ink-tertiary hover:text-primary pl-0.5" title="At apply-time, swap this part for the one in its category that fits the work order's device">
                        <M n={c.device_variant ? "check_box" : "check_box_outline_blank"} c={`text-[15px] ${c.device_variant ? "text-primary" : ""}`} />
                        Auto-pick the part that fits the device
                      </button>
                    )}
                  </div>
                ))}
                <div className="pt-1 space-y-1.5">
                  <div className="inline-flex rounded-lg bg-slate-100 p-0.5">
                    {(["service", "product"] as const).map((k) => (
                      <button key={k} type="button" onClick={() => setCompKind(k)}
                        className={`px-3 py-1 rounded-md text-[11px] font-bold capitalize transition-colors ${compKind === k ? "bg-white text-primary shadow-clay-sm" : "text-ink-tertiary hover:text-ink-secondary"}`}>
                        {k === "service" ? "Services" : "Products"}
                      </button>
                    ))}
                  </div>
                  <CatalogPicker key={compKind} type={compKind} openOnFocus placeholder={`Choose a ${compKind}…`} onPick={addComp} />
                </div>
                <div className="text-[11px] text-ink-tertiary text-right">Components total <span className="tabular-nums font-semibold text-ink-secondary">{money(stdTotal)}</span></div>
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className={lbl}>Pricing</label>
                  <select value={p.price_mode || "sum"} onChange={(e) => set("price_mode", e.target.value)} className={field}>
                    <option value="sum">Sum of items</option>
                    <option value="fixed">Fixed kit price</option>
                  </select>
                </div>
                <div>
                  <label className={lbl}>{(p.price_mode || "sum") === "fixed" ? `Kit price (${currencySymbol()})` : "Price (auto)"}</label>
                  <input type="number" value={(p.price_mode || "sum") === "fixed" ? (p.price ?? 0) : stdTotal} disabled={(p.price_mode || "sum") !== "fixed"} onChange={(e) => set("price", e.target.value)} className={`${field} font-mono font-bold disabled:opacity-60`} />
                </div>
              </div>
            </div>
            );
          })()}
        </div>

        <div className="p-4 border-t border-slate-100 flex items-center justify-between bg-slate-50/70">
          <button onClick={onClose} className="px-4 py-2 rounded-lg border border-slate-200 text-ink-secondary hover:bg-slate-100 font-semibold text-xs">Cancel</button>
          <button onClick={save} className="px-5 py-2 rounded-lg bg-primary text-white font-bold text-xs shadow-clay-sm hover:brightness-110">{footerLabel}</button>
        </div>
        {toast && <div className="absolute bottom-20 left-1/2 -translate-x-1/2 bg-ink-primary text-white text-xs font-semibold px-4 py-2 rounded-xl shadow-clay-drawer z-20">{toast}</div>}
      </div>
    </div>
  );
}
