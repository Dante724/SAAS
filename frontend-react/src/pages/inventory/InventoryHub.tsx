import SkeletonRows from "../../components/SkeletonRows";
import { useEffect, useState, useRef } from "react";
import { useSearchParams } from "react-router-dom";
import Shell, { M } from "../../components/Shell";
import ScanStockPanel from "../../components/ScanStockPanel";
import { api, openAuthedDoc } from "../../api/client";
import { isoDate } from "../../lib/localDate";
import { missingBarcodesMessage } from "../../lib/barcodes";
import { Product, Supplier, PO, Service, Bundle, Asset, currencySymbol, money } from "./types";
import { analyzePaste, gridToRows, mergeRemap, loadRememberedMap, saveRememberedMap, FIELD_LABELS, FIELD_ORDER } from "./pasteMap";
import type { PasteField, PostRow } from "./pasteMap";
import InventoryDrawer, { DrawerKind } from "./InventoryDrawer";
import PODrawer from "./PODrawer";
import NewPODrawer from "./NewPODrawer";
import PurchasingCockpit from "./PurchasingCockpit";
import AssetDrawer from "../orders/AssetDrawer";
import { useTerminology, assetNoun } from "../../lib/useTerminology";
import { assetStatusTone } from "./statusTone";
import { cachedPref, loadPref, savePref } from "../../lib/userPref";
import Pager from "../../components/Pager";
import SerialEntry from "../../components/SerialEntry";
import ScanButton from "../../components/ScanButton";
import DateField from "../../components/DateField";
import AssetStateTabs, { AssetCounts, useAssetState } from "../../components/AssetStateTabs";
import { useDebounced, useServerPage } from "../../hooks/useServerPage";
import { useMe } from "../../context/Me";
import { alertDialog, confirmDialog, promptDialog } from "../../lib/dialog";
import { printHtmlInFrame } from "../../lib/safeHtml";
import { inputCls } from "../invoices/billing";
import { productGlyph } from "../sales/SalesPOS";
import { RowMenu } from "../company/CompanyHub";

type Tab = "stock" | "devices" | "services" | "bundles" | "purchase-orders"
  | "postings" | "reservations" | "conversions" | "transfers" | "stock-takes" | "write-offs" | "returns" | "bins";

const TABS: { id: Tab; label: string; count?: string }[] = [
  { id: "stock", label: "Stock" }, { id: "devices", label: "Devices & Assets" },
  { id: "purchase-orders", label: "Purchase orders" }, { id: "postings", label: "Postings" },
  { id: "reservations", label: "Reservations" }, { id: "bins", label: "Bins" }, { id: "conversions", label: "Conversions" },
  { id: "transfers", label: "Transfers" }, { id: "stock-takes", label: "Stock takes" },
  { id: "write-offs", label: "Write-offs" }, { id: "returns", label: "Returns" },
];

// A set filter reads as active in the primary tint; an unset one stays neutral.
const filterSel = (on: boolean) => `h-8 px-3 text-xs font-semibold rounded-lg border cursor-pointer ${on ? "bg-primary/10 text-primary border-primary/30" : "bg-white text-ink-secondary border-slate-200 hover:bg-slate-50"}`;

// Margin as a percentage: ink when healthy, red only when the part sells below cost.
export function marginOf(p: Pick<Product, "margin_pct" | "sell_price" | "cost_price">): number | null {
  if (p.margin_pct != null) return p.margin_pct;
  return (p.sell_price || 0) > 0 && p.cost_price != null ? Math.round((((p.sell_price || 0) - (p.cost_price || 0)) / (p.sell_price || 1)) * 100) : null;
}

// Exception-only stock status: healthy rows show a plain number (returns null),
// only out/low get a coloured pill — so the eye lands on what needs action.
// A failed load must not look like an empty shop: "No stock yet" after a server error sent
// people to re-enter stock they already had (launch review B9).
export function StockLoadError({ onRetry }: { onRetry: () => void }) {
  return (
    <div role="alert" className="flex flex-col items-center text-center gap-3 max-w-sm mx-auto">
      <div className="w-11 h-11 rounded-full bg-slate-100 text-ink-tertiary flex items-center justify-center"><M n="cloud_off" c="text-2xl" /></div>
      <div><div className="font-semibold text-ink-primary text-sm">Couldn't load your stock</div><div className="text-ink-tertiary mt-0.5">Your parts are safe. Check your connection and try again.</div></div>
      <button onClick={onRetry} className={`${ghostBtn} mt-1`}><M n="refresh" c="text-sm" />Try again</button>
    </div>
  );
}

const primaryBtn = "h-9 px-4 rounded-lg bg-primary text-white text-xs font-semibold hover:bg-primary-container flex items-center gap-1.5";
const ghostBtn = "h-9 px-3.5 rounded-lg bg-white border border-slate-200 text-ink-secondary text-xs font-semibold hover:bg-slate-50 flex items-center gap-1.5";

// Empty history tab: why it's worth having, and (when the header has no button) the first step.
const AUDIT_EMPTY: Record<string, { why: string; action?: string }> = {
  postings: { why: "Paste or type a supplier delivery to bring stock in. Every receipt, count and correction is listed here.", action: "New posting" },
  reservations: { why: "Parts are held here when you add them to a work order or a confirmed appointment, so nobody sells them twice." },
  conversions: { why: "Turn a traded-in device into parts, or parts into a device, and keep both sides of your stock right." },
  transfers: { why: "Move stock between locations to keep each branch supplied. Every move is listed here." },
  "stock-takes": { why: "Count what is on the shelf and see the difference from what your stock says, part by part." },
  "write-offs": { why: "Record damaged, lost or expired parts so your stock and costs stay accurate." },
  returns: { why: "Log a part a customer brings back, or one you send back to a supplier, and the stock follows." },
};
function AuditEmpty({ tab, title, sub, onStart }: { tab: string; title: string; sub: string; onStart?: () => void }) {
  const e = AUDIT_EMPTY[tab];
  return (
    <div className="flex flex-col items-center text-center gap-1 max-w-sm mx-auto">
      <div className="text-sm font-semibold text-ink-primary">No {title.toLowerCase()} yet</div>
      <div className="text-xs text-ink-tertiary">{e?.why || sub}</div>
      {onStart && e?.action && <button onClick={onStart} className={`${ghostBtn} mt-2`}><M n="add" c="text-sm" />{e.action}</button>}
    </div>
  );
}

function stockPill(p: Product): { cls: string; dot: string; label: string } | null {
  const q = p.qty_available ?? 0;
  if (q <= 0) return { cls: "bg-rose-50 text-rose-700 border-rose-200/60", dot: "bg-rose-500", label: "Out of stock" };
  if (p.low_stock) return { cls: "bg-amber-50 text-amber-700 border-amber-200/60", dot: "bg-amber-500", label: `${q} left` };
  return null;
}

export default function InventoryHub() {
  const term = useTerminology();
  const { can } = useMe();
  const [tab, setTab] = useState<Tab>("stock");
  const [products, setProducts] = useState<Product[] | null>(null);
  const [productsFailed, setProductsFailed] = useState(false);
  // Devices tab pages on the server (the API serves at most 200 rows per request).
  const [aq, setAq] = useState("");
  const daq = useDebounced(aq.trim());
  const [aState, setAState] = useAssetState();                      // 19: active / written off, remembered
  const [aOwner, setAOwner] = useState<"" | "company" | "client">("");
  const ap = useServerPage<Asset, { counts?: AssetCounts }>("/assets", { search: daq || undefined, state: aState, owner_type: aOwner || undefined },
    { pageSize: 50, enabled: tab === "devices" });
  const assets = ap.rows;
  const [services, setServices] = useState<Service[] | null>(null);
  const [bundles, setBundles] = useState<Bundle[] | null>(null);
  const [pos, setPos] = useState<PO[] | null>(null);
  const [poOpen, setPoOpen] = useState<string | null>(null);
  const [newPo, setNewPo] = useState(false);
  const [suppliers, setSuppliers] = useState<Supplier[]>([]);
  const [drawer, setDrawer] = useState<{ kind: DrawerKind; id?: string | null; initial?: any } | null>(null);
  const [assetOpen, setAssetOpen] = useState<string | null>(null);
  const [scanStock, setScanStock] = useState(false);
  const [assetCreate, setAssetCreate] = useState(false);
  const [auditRows, setAuditRows] = useState<Record<string, any[] | null>>({});
  const [psel, setPsel] = useState<Set<string>>(new Set());
  const [asel, setAsel] = useState<Set<string>>(new Set());   // asset selection
  const PAGE_SIZE = 50;                 // numbered pagination — 50 rows/page (Stock + Devices)
  const [pPage, setPPage] = useState(1);   // Stock page
  const [poQ, setPoQ] = useState("");   // purchase orders search
  const [secPage, setSecPage] = useState(1);  // shared page for the other tabs (PO + audit) — one visible at a time
  const aToggle = (id: string) => setAsel((s) => { const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n; });
  const [labelDlg, setLabelDlg] = useState(false);
  const [labelMode, setLabelMode] = useState<"product" | "asset">("product");
  const [labelTpls, setLabelTpls] = useState<any[]>([]);
  const [labelTplId, setLabelTplId] = useState("");
  const [labelCopies, setLabelCopies] = useState(1);
  const [labelErr, setLabelErr] = useState<string | null>(null);   // e.g. a product whose MPN isn't a valid EAN-13
  async function openLabelDlg(mode: "product" | "asset") {
    setLabelMode(mode);
    try {
      const d = await api.get<any>("/documents/templates");
      const wanted = mode === "asset" ? ["vehicle_label", "label", "product_label"] : ["label", "price_tag", "product_label"];
      const items = (d?.items || []).filter((t: any) => wanted.includes(t.doc_type));
      setLabelTpls(items); setLabelTplId(items[0]?.id || "");
    } catch { setLabelTpls([]); }
    setLabelErr(null); setLabelDlg(true);
  }
  async function printLabels() {
    if (!labelTplId) return;
    const url = labelMode === "asset" ? "/documents/render-asset-labels" : "/documents/render-labels";
    const body = labelMode === "asset"
      ? { template_id: labelTplId, asset_ids: [...asel], copies: labelCopies || 1 }
      : { template_id: labelTplId, part_ids: [...psel], copies: labelCopies || 1 };
    setLabelErr(null);
    try {
      const r = await api.post<any>(url, body);
      const html = r?.html; if (!html) return;
      printHtmlInFrame(html); setLabelDlg(false);
    } catch (e: any) { setLabelErr(e?.message || "Couldn't print these labels"); }
  }
  const labelSelCount = labelMode === "asset" ? asel.size : psel.size;
  const [pBusy, setPBusy] = useState(false);
  const [action, setAction] = useState<null | { kind: "transfer" | "stocktake" | "write-off" | "return" | "conversion" }>(null);
  const [stk, setStk] = useState<null | { id?: string }>(null);
  const [conv, setConv] = useState(false);
  const [stockMenu, setStockMenu] = useState(false);
  const [navMore, setNavMore] = useState(false);   // Phase 2: overflow menu for secondary tabs
  const [pq, setPq] = useState("");
  const [avail, setAvail] = useState<"all" | "in" | "low" | "out">("in");
  const [cat, setCat] = useState("");   // selected category_id ("" = all)
  const [wh, setWh] = useState("");     // selected warehouse_id ("" = all)
  const [cats, setCats] = useState<{ id: string; name: string; depth: number }[]>([]);
  // Phase 2 — saved views: named filter combos (availability + category + warehouse), per-browser.
  type SavedView = { name: string; avail: "all" | "in" | "low" | "out"; cat: string; wh: string };
  // 144ca — saved views now sync across devices via /api/prefs (localStorage = instant cache).
  const VKEY = "listview:inventory-views";
  const [savedViews, setSavedViews] = useState<SavedView[]>(() => cachedPref<{ views: SavedView[] }>(VKEY, { views: [] }).views || []);
  const persistViews = (v: SavedView[]) => { setSavedViews(v); savePref(VKEY, { views: v }); };
  const viewActive = (v: SavedView) => avail === v.avail && cat === v.cat && wh === v.wh;
  function applyView(v: SavedView) { setAvail(v.avail); setCat(v.cat); setWh(v.wh); }
  async function saveCurrentView() {
    const name = await promptDialog("Name this view (its current Availability, Category and Warehouse filters are saved):");
    if (!name || !name.trim()) return;
    persistViews([...savedViews.filter((v) => v.name !== name.trim()), { name: name.trim(), avail, cat, wh }]);
  }
  function deleteView(name: string) { persistViews(savedViews.filter((v) => v.name !== name)); }
  // Phase C — persisted density toggle (per browser). Comfortable default; compact for bulk scanning.
  const DKEY = "listview:inventory-density";
  const [density, setDensity] = useState<"comfortable" | "compact">(() => cachedPref<{ d: "comfortable" | "compact" }>(DKEY, { d: "comfortable" }).d || "comfortable");
  const setDens = (d: "comfortable" | "compact") => { setDensity(d); savePref(DKEY, { d }); };
  // reconcile saved views + density with the server once on mount (cross-device)
  useEffect(() => {
    loadPref<{ views: SavedView[] }>(VKEY, { views: savedViews }).then((v) => setSavedViews(v.views || []));
    loadPref<{ d: "comfortable" | "compact" }>(DKEY, { d: density }).then((v) => { if (v.d) setDensity(v.d); });
  }, []); // eslint-disable-line
  const [warehouses, setWarehouses] = useState<{ id: string; name: string }[]>([]);
  const [reorderOpen, setReorderOpen] = useState(false);
  const [cockpitOpen, setCockpitOpen] = useState(false);
  const [postingOpen, setPostingOpen] = useState(false);
  const [bulkAdj, setBulkAdj] = useState(false);
  const [toOrderOpen, setToOrderOpen] = useState(false);
  // Inventory settings gate which tabs/columns show — the legacy enable_bins / enable_reservations reveal.
  const [cfg, setCfg] = useState<{ enable_bins?: boolean; enable_reservations?: boolean }>({});
  useEffect(() => { api.get<any>("/staff/inventory-settings").then(setCfg).catch(() => setCfg({})); }, []);
  const visibleTabs = TABS.filter((t) =>
    (t.id !== "devices" || can("can_view_asset_info")) &&
    (t.id !== "reservations" || cfg.enable_reservations) &&
    (t.id !== "bins" || cfg.enable_bins));

  function loadProducts() {
    const qs = new URLSearchParams();
    if (cat) qs.set("category_id", cat);
    if (wh) qs.set("warehouse_id", wh);
    const q = qs.toString();
    api.get<{ items: Product[] }>(`/catalog/products${q ? "?" + q : ""}`)
      .then((r) => { setProductsFailed(false); setProducts(r.items || []); })
      .catch(() => { setProductsFailed(true); setProducts([]); });
  }
  function loadServices() { api.get<{ items: Service[] }>("/catalog/services").then((r) => setServices(r.items || [])).catch(() => setServices([])); }
  useEffect(() => { loadProducts(); }, [cat, wh]); // eslint-disable-line
  // deep-open from a scan (?open=<part id>): open the product drawer, then clear the param
  const [sp, setSp] = useSearchParams();
  useEffect(() => {
    const id = sp.get("open");
    if (id) { setTab("stock"); setDrawer({ kind: "product", id }); setSp((p) => { p.delete("open"); return p; }, { replace: true }); }
  }, []); // eslint-disable-line
  // Category + warehouse filter options
  useEffect(() => {
    const flat: { id: string; name: string; depth: number }[] = [];
    const walk = (nodes: any[], d: number) => (nodes || []).forEach((n) => { flat.push({ id: n.id, name: n.name, depth: d }); if (n.children?.length) walk(n.children, d + 1); });
    api.get<any>("/catalog/categories?entity_type=product").then((r) => { walk(r.tree || [], 0); setCats(flat); }).catch(() => setCats([]));
    api.get<any>("/settings/warehouses?kind=product").then((r) => setWarehouses(r.items || [])).catch(() => setWarehouses([]));
  }, []);
  useEffect(() => {
    setSecPage(1);   // leaving a tab resets the shared (PO/audit) pager to page 1
    if (tab === "services" && !services) loadServices();
    if (tab === "bundles" && !bundles) api.get<{ items: Bundle[] }>("/bundles").then((r) => setBundles(r.items || [])).catch(() => setBundles([]));
    if (tab === "purchase-orders" && !pos) { api.get<{ items: PO[] }>("/inventory/po").then((r) => setPos(r.items || [])).catch(() => setPos([])); api.get<{ items: Supplier[] }>("/suppliers").then((r) => setSuppliers(r.items || [])).catch(() => {}); }
    if (AUDIT[tab] && auditRows[tab] === undefined) {
      const url = AUDIT_URL[tab];
      if (url) api.get<any>(url).then((r) => setAuditRows((s) => ({ ...s, [tab]: r.items || r || [] }))).catch(() => setAuditRows((s) => ({ ...s, [tab]: [] })));
      else setAuditRows((s) => ({ ...s, [tab]: [] }));
    }
  }, [tab]);

  const AUDIT: Record<string, { icon: string; title: string; sub: string }> = {
    postings: { icon: "receipt_long", title: "Stock postings", sub: "Audit trail of manual adjustments, received shipments and reconciliations." },
    reservations: { icon: "bookmark_added", title: "Part reservations", sub: "Items reserved for active work orders and confirmed appointments." },
    conversions: { icon: "sync_alt", title: "Conversions", sub: "Assets converted into stock parts and vice-versa." },
    transfers: { icon: "swap_horiz", title: "Transfers", sub: "Stock moved between warehouses and branches." },
    "stock-takes": { icon: "fact_check", title: "Stock takes", sub: "Physical count sessions and variance reconciliation." },
    "write-offs": { icon: "delete_sweep", title: "Write-offs", sub: "Damaged, lost or expired stock removed from inventory." },
    returns: { icon: "assignment_return", title: "Returns", sub: "Customer and supplier returns processed here." },
  };
  // The first step for a history tab whose header has no button of its own.
  const AUDIT_START: Record<string, (() => void) | undefined> = { postings: () => setPostingOpen(true) };
  const AUDIT_URL: Record<string, string> = {
    postings: "/inventory/movements", reservations: "/inventory/reservations",
    conversions: "/conversions", transfers: "/transfers",
    "stock-takes": "/stocktake", "write-offs": "/stock-docs/write-offs", returns: "/returns",
  };
  const av = (o: any, keys: string[], fb = "—") => { for (const k of keys) { const v = o?.[k]; if (v !== undefined && v !== null && v !== "") return typeof v === "number" ? String(v) : String(v); } return fb; };
  const dt = (o: any, keys: string[]) => { for (const k of keys) { if (o?.[k]) return new Date(o[k]).toLocaleDateString(); } return "—"; };
  const AUDIT_COLS: Record<string, [string, (o: any) => string][]> = {
    postings: [["Date", (r) => dt(r, ["created_at", "date", "posted_at"])], ["Part", (r) => av(r, ["part_name", "part", "product_name"])], ["Type", (r) => av(r, ["movement_type", "type", "kind"])], ["Qty", (r) => av(r, ["qty", "quantity", "delta"])], ["Note", (r) => av(r, ["note", "reason", "comment"])]],
    reservations: [["Part", (r) => av(r, ["part_name", "part", "product_name"])], ["Qty", (r) => av(r, ["qty", "quantity"])], ["Order", (r) => av(r, ["order_number", "order_id"])], ["Status", (r) => av(r, ["status", "state"])]],
    conversions: [["Date", (r) => dt(r, ["created_at", "date"])], ["From", (r) => av(r, ["from_name", "source_name", "source", "from"])], ["To", (r) => av(r, ["to_name", "target_name", "target", "to"])], ["Direction", (r) => av(r, ["direction", "type", "kind"])]],
    transfers: [["Date", (r) => dt(r, ["created_at", "date"])], ["From", (r) => av(r, ["from_location_name", "from_location", "source"])], ["To", (r) => av(r, ["to_location_name", "to_location", "dest"])], ["Items", (r) => av(r, ["item_count", "lines_count"], String((r?.lines || r?.items || []).length || 0))], ["Status", (r) => av(r, ["status", "state"])]],
    "stock-takes": [["Date", (r) => dt(r, ["created_at", "date", "started_at"])], ["Location", (r) => av(r, ["location_name", "warehouse_name", "warehouse"])], ["Status", (r) => av(r, ["status", "state"])], ["Counted", (r) => `${r?.counted ?? 0} / ${r?.lines ?? 0}`]],
    "write-offs": [["Date", (r) => dt(r, ["created_at", "date"])], ["Part", (r) => av(r, ["part_name", "part", "product_name"])], ["Qty", (r) => av(r, ["qty", "quantity"])], ["Reason", (r) => av(r, ["reason", "note"])]],
    returns: [["Date", (r) => dt(r, ["created_at", "date"])], ["Order", (r) => av(r, ["order_number", "order_id"])], ["Client", (r) => av(r, ["client_name", "client"])], ["Amount", (r) => money(r?.amount ?? r?.refund_total ?? r?.total)], ["Status", (r) => av(r, ["status", "state"])]],
  };

  const supName = Object.fromEntries(suppliers.map((s) => [s.id, s.name]));
  const catName = Object.fromEntries(cats.map((c) => [c.id, c.name]));

  // ── Products bulk-select ──────────────────────────────────────────
  // "Select all" means all rows matching the current filters (shownProducts), not just
  // the visible page and not the unfiltered catalog. pAllSel is computed after
  // shownProducts (below); these handlers read it at call-time so order is fine.
  function pToggle(id: string) { const n = new Set(psel); n.has(id) ? n.delete(id) : n.add(id); setPsel(n); }
  function pToggleAll() { const ids = shownProducts.map((p) => p.id); setPsel(ids.every((id) => psel.has(id)) ? new Set() : new Set(ids)); }
  function pClear() { setPsel(new Set()); }
  async function pBulk(act: "delete" | "price", extra: any = {}) {
    setPBusy(true);
    try {
      const r = await api.post<{ count?: number; blocked?: string[] }>("/catalog/products/bulk", { ids: [...psel], action: act, ...extra });
      pClear(); loadProducts();
      if (r?.blocked?.length) await alertDialog(`${r.blocked.length} item(s) were kept because they're used in a bundle or job kit: ${r.blocked.join(", ")}. Remove them there first.`, "Some items weren't deleted");
    }
    catch (e: any) { await alertDialog(e.message || "Bulk action failed"); }
    finally { setPBusy(false); }
  }
  // ii144a — give products without a barcode one (shop-made EAN-13 codes; existing ones untouched).
  async function pAddBarcodes(onlySelected: boolean) {
    const n = onlySelected ? psel.size : 0;
    const q = onlySelected
      ? `Add a barcode to the ${n} selected product${n === 1 ? "" : "s"} that don't have one yet?`
      : "Add a barcode to every product that doesn't have one yet?";
    if (!(await confirmDialog(`${q} Products that already have a barcode keep it.`, { confirmLabel: "Add barcodes" }))) return;
    setPBusy(true);
    try {
      const r = await api.post<{ generated: number; more?: boolean }>("/catalog/products/barcodes/generate-missing",
        onlySelected ? { ids: [...psel] } : {});
      if (onlySelected) pClear();
      loadProducts();
      await alertDialog(missingBarcodesMessage(r, n), "Barcodes");
    } catch (e: any) { await alertDialog(e.message || "Couldn't add barcodes"); }
    finally { setPBusy(false); }
  }
  async function pDelete() {
    if (!(await confirmDialog(`Archive ${psel.size} product(s)? History is kept; they leave active stock.`, { danger: true, confirmLabel: "Delete" }))) return;
    pBulk("delete");
  }
  async function pMarkup() {
    const v = await promptDialog("Adjust sell price by what percent? (e.g. 10 for +10%, -5 for -5%)");
    if (v == null || v.trim() === "" || isNaN(Number(v))) return;
    pBulk("price", { price_pct: Number(v) });
  }
  function pExport() {
    const chosen = (products || []).filter((p) => psel.has(p.id));
    const head = ["Name", "SKU", "MPN", "Barcode", "Unit", "Cost", "Sell", "In stock"];
    const esc = (x: any) => `"${String(x ?? "").replace(/"/g, '""')}"`;
    const body = chosen.map((p) => [p.name, p.sku || p.code, p.mpn, p.barcode, p.uom || "pcs", p.avg_cost ?? p.cost_price ?? 0, p.sell_price ?? 0, p.qty_available ?? 0].map(esc).join(","));
    const blob = new Blob([[head.join(","), ...body].join("\n")], { type: "text/csv" });
    const a = document.createElement("a"); a.href = URL.createObjectURL(blob);
    a.download = `products-${isoDate()}.csv`; a.click();
  }

  const newBtn = (label: string, kind: DrawerKind) => (
    <button onClick={() => setDrawer({ kind })} className={primaryBtn}><M n="add" c="text-sm" />{label}</button>
  );
  const searchBar = (ph: string, value?: string, onChange?: (v: string) => void) => (
    <div className="relative flex-1 min-w-[200px] max-w-md"><M n="search" c="absolute left-3 top-1/2 -translate-y-1/2 text-ink-tertiary text-sm pointer-events-none" /><input value={value} onChange={onChange ? (e) => onChange(e.target.value) : undefined} aria-label={ph.replace(/…$/, "")} className={`${inputCls} pl-9 text-ink-primary`} placeholder={ph} /></div>
  );

  // Client-side stock filter (data already loaded) — search + availability wired; warehouse/category left static (no resolved names loaded).
  const AVAIL_LABEL = { all: "All", in: "In stock", low: "Low stock", out: "Out of stock" } as const;
  const matchesSearch = (p: any) => {
    if (!pq.trim()) return true;
    const q = pq.toLowerCase();
    return `${p.name} ${p.sku || ""} ${p.code || ""} ${p.barcode || ""} ${(p.extra_barcodes || []).join(" ")} ${p.mpn || ""}`.toLowerCase().includes(q);
  };
  const searchMatches = (products || []).filter(matchesSearch);
  const shownProducts = searchMatches.filter((p) => {
    const qa = p.qty_available ?? 0;
    if (avail === "in") return qa > 0;
    if (avail === "low") return !!p.low_stock;
    if (avail === "out") return qa <= 0;
    return true;
  });
  // A searched part that exists but is hidden by the availability filter (e.g. an MPN with no stock
  // yet) used to look like "no such part"; the empty state offers to show those matches instead.
  const hiddenByAvail = pq.trim() && avail !== "all" ? searchMatches.length - shownProducts.length : 0;
  const stockCols = cfg.enable_reservations ? 10 : 9;  // SKU folded into Product cell (Phase 3)
  const pAllSel = shownProducts.length > 0 && shownProducts.every((p) => psel.has(p.id));
  // Numbered pagination (50/page). Reset to page 1 when the filtered set changes, and
  // clamp if the current page falls off the end (e.g. after an archive/delete).
  useEffect(() => { setPPage(1); }, [pq, avail, cat, wh]);
  const pPages = Math.max(1, Math.ceil(shownProducts.length / PAGE_SIZE));
  const pageProducts = shownProducts.slice((Math.min(pPage, pPages) - 1) * PAGE_SIZE, Math.min(pPage, pPages) * PAGE_SIZE);
  const pageAssets = assets || [];
  // PO + audit tables share secPage (only one of these tabs renders at a time).
  // PO search: number, supplier or status.
  const poNeedle = poQ.trim().toLowerCase();
  const shownPos = (pos || []).filter((po) => !poNeedle || [po.po_number, supName[po.supplier_id || ""], po.status]
    .some((x) => String(x || "").toLowerCase().includes(poNeedle)));
  const poPages = Math.max(1, Math.ceil(shownPos.length / PAGE_SIZE));
  const pagePos = shownPos.slice((Math.min(secPage, poPages) - 1) * PAGE_SIZE, Math.min(secPage, poPages) * PAGE_SIZE);
  const auditAll: any[] = auditRows[tab] || [];
  const auditPages = Math.max(1, Math.ceil(auditAll.length / PAGE_SIZE));
  const pageAudit = auditAll.slice((Math.min(secPage, auditPages) - 1) * PAGE_SIZE, Math.min(secPage, auditPages) * PAGE_SIZE);

  return (
    <Shell title="Inventory">
      {/* sub-nav */}
      <div className="px-6 pt-3 border-b border-slate-200/60 bg-white/40 flex-shrink-0">
        {(() => {
          // Phase 2 — keep the daily tabs first-class, fold the long tail into a labelled "More"
          // menu (NN/g "fewer tabs"; ~5 visible max). Honors the settings gates via visibleTabs.
          const PRIMARY: Tab[] = ["stock", "devices", "purchase-orders"];
          const primaryTabs = visibleTabs.filter((t) => PRIMARY.includes(t.id));
          const overflowTabs = visibleTabs.filter((t) => !PRIMARY.includes(t.id));
          const inOverflow = overflowTabs.some((t) => t.id === tab);
          const tabLabel = (t: { id: Tab; label: string }) => (t.id === "devices" ? term.assets : t.label);
          const tabBtn = (t: { id: Tab; label: string }) => {
            const cnt = t.id === "stock" ? products?.length : t.id === "services" ? services?.length : undefined;
            return (
              <button key={t.id} onClick={() => setTab(t.id)} className={`pb-3 whitespace-nowrap flex items-center gap-2 border-b-2 transition-colors ${tab === t.id ? "text-primary font-bold border-primary" : "text-ink-secondary hover:text-ink-primary border-transparent"}`}>
                {tabLabel(t)}{cnt != null && <span className={`px-1.5 py-0.5 rounded-full text-[11px] font-bold ${tab === t.id ? "bg-blue-100 text-primary" : "bg-slate-100 text-ink-secondary"}`}>{cnt}</span>}
              </button>
            );
          };
          return (
            <nav className="flex space-x-6 -mb-px text-xs font-semibold items-center">
              {primaryTabs.map(tabBtn)}
              {overflowTabs.length > 0 && (
                <div className="relative">
                  <button onClick={() => setNavMore((o) => !o)} className={`pb-3 whitespace-nowrap flex items-center gap-1 border-b-2 transition-colors ${inOverflow ? "text-primary font-bold border-primary" : "text-ink-secondary hover:text-ink-primary border-transparent"}`}>
                    {inOverflow ? tabLabel(overflowTabs.find((t) => t.id === tab)!) : "More"}<M n="expand_more" c="text-sm" />
                  </button>
                  {navMore && (<>
                    <div className="fixed inset-0 z-30" onClick={() => setNavMore(false)} />
                    <div className="absolute left-0 mt-1 w-52 bg-white rounded-xl border border-slate-200 shadow-drawer z-40 py-1.5 text-xs">
                      {overflowTabs.map((t) => (
                        <button key={t.id} onClick={() => { setTab(t.id); setNavMore(false); }} className={`w-full flex items-center gap-2 px-3 py-2 text-left hover:bg-slate-50 ${tab === t.id ? "text-primary font-bold" : "text-ink-secondary"}`}>
                          {tabLabel(t)}
                        </button>
                      ))}
                    </div>
                  </>)}
                </div>
              )}
            </nav>
          );
        })()}
      </div>

      <div className="flex-1 overflow-y-auto px-6 py-5">
        {/* STOCK */}
        {tab === "stock" && (<>
          <div className="flex items-center gap-3 mb-5 flex-wrap">
            {newBtn("Product / Stock", "product")}
            <button onClick={() => setScanStock(true)} className={ghostBtn}><M n="qr_code_scanner" c="text-sm text-ink-tertiary" />Scan to stock</button>
            <div className="flex items-center gap-1.5 flex-wrap">
              <select aria-label="Warehouse" value={wh} onChange={(e) => setWh(e.target.value)} className={filterSel(!!wh)}>
                <option value="">Warehouse: All</option>
                {warehouses.map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
              </select>
              <select aria-label="Availability" value={avail} onChange={(e) => setAvail(e.target.value as any)} className={filterSel(avail !== "in")}>
                {(["all", "in", "low", "out"] as const).map((k) => <option key={k} value={k}>Availability: {AVAIL_LABEL[k]}</option>)}
              </select>
              <select aria-label="Category" value={cat} onChange={(e) => setCat(e.target.value)} className={filterSel(!!cat)}>
                <option value="">Category: All</option>
                {cats.map((c) => <option key={c.id} value={c.id}>{`${"  ".repeat(c.depth)}${c.name}`}</option>)}
              </select>
              {(cat || wh || avail !== "in") && (
                <button onClick={() => { setCat(""); setWh(""); setAvail("in"); }} className="h-8 px-2.5 text-xs font-semibold rounded-lg text-primary hover:bg-primary/10 flex items-center gap-1"><M n="filter_alt_off" c="text-sm" />Clear <span className="px-1.5 rounded-full bg-primary/15 text-[11px] tabular-nums">{(cat ? 1 : 0) + (wh ? 1 : 0) + (avail !== "in" ? 1 : 0)}</span></button>
              )}
            </div>
            {searchBar("Search products, SKU, MPN, barcode…", pq, setPq)}
            <div className="ml-auto relative">
              <button onClick={() => setStockMenu((o) => !o)} aria-expanded={stockMenu} className={ghostBtn}><span>Actions</span><M n="expand_more" c="text-sm" /></button>
              {stockMenu && (
                <>
                  <div className="fixed inset-0 z-30" onClick={() => setStockMenu(false)} />
                  <div className="absolute right-0 mt-1 w-56 bg-white rounded-xl border border-slate-200 shadow-drawer z-40 py-1.5 text-xs">
                    <button onClick={() => { setStockMenu(false); setToOrderOpen(true); }} className="w-full flex items-center gap-2 px-3 py-2 hover:bg-slate-50 text-ink-secondary"><M n="assignment_late" c="text-sm" />Parts to order (jobs)</button>
                    <button onClick={() => { setStockMenu(false); setReorderOpen(true); }} className="w-full flex items-center gap-2 px-3 py-2 hover:bg-slate-50 text-ink-secondary"><M n="sync_alt" c="text-sm" />Reorder list</button>
                    <button onClick={() => { setStockMenu(false); setPostingOpen(true); }} className="w-full flex items-center gap-2 px-3 py-2 hover:bg-slate-50 text-ink-secondary"><M n="add" c="text-sm" />New posting</button>
                    <button onClick={() => { setStockMenu(false); if (products?.length) { setPsel(new Set(products.map((p) => p.id))); } }} className="w-full flex items-center gap-2 px-3 py-2 hover:bg-slate-50 text-ink-secondary"><M n="filter_list" c="text-sm" />Bulk edit filtered</button>
                    <button onClick={() => { setStockMenu(false); pAddBarcodes(false); }} className="w-full flex items-center gap-2 px-3 py-2 hover:bg-slate-50 text-ink-secondary"><M n="barcode" c="text-sm" />Add missing barcodes</button>
                    <button onClick={() => { setStockMenu(false); setAction({ kind: "write-off" }); }} className="w-full flex items-center gap-2 px-3 py-2 hover:bg-slate-50 text-ink-secondary"><M n="delete_sweep" c="text-sm" />New write-off</button>
                  </div>
                </>
              )}
            </div>
          </div>
          {/* Phase 2 — saved views: quick switch between named filter combos (reuses availability/category/warehouse) */}
          <div className="flex items-center gap-2 mb-4 flex-wrap text-xs">
            <button onClick={() => { setAvail("all"); setCat(""); setWh(""); }} className={`px-3 py-1.5 rounded-full border font-semibold ${avail === "all" && !cat && !wh ? "bg-primary/10 text-primary border-primary/40" : "bg-white text-ink-secondary border-slate-200 hover:bg-slate-50"}`}>All stock</button>
            {savedViews.map((v) => (
              <span key={v.name} className={`group inline-flex items-center rounded-full border font-semibold ${viewActive(v) ? "bg-primary/10 text-primary border-primary/40" : "bg-white text-ink-secondary border-slate-200 hover:bg-slate-50"}`}>
                <button onClick={() => applyView(v)} className="pl-3 pr-1.5 py-1.5">{v.name}</button>
                <button aria-label="Delete view" onClick={() => deleteView(v.name)} title="Delete view" className="pr-2 pl-0.5 py-1.5 text-ink-tertiary hover:text-rose-600"><M n="close" c="text-[13px]" /></button>
              </span>
            ))}
            <button onClick={saveCurrentView} className="px-2.5 py-1.5 rounded-full border border-dashed border-slate-300 text-ink-tertiary hover:text-primary hover:border-primary/40 font-semibold flex items-center gap-1"><M n="bookmark_add" c="text-sm" />Save view</button>
            <div className="ml-auto inline-flex items-center gap-0.5 bg-white border border-slate-200 rounded-lg p-0.5" title="Row density">
              <button aria-label="Comfortable" onClick={() => setDens("comfortable")} className={`p-1.5 rounded-md ${density === "comfortable" ? "bg-primary/10 text-primary" : "text-ink-tertiary hover:text-ink-secondary"}`} title="Comfortable"><M n="density_medium" c="text-base" /></button>
              <button aria-label="Compact" onClick={() => setDens("compact")} className={`p-1.5 rounded-md ${density === "compact" ? "bg-primary/10 text-primary" : "text-ink-tertiary hover:text-ink-secondary"}`} title="Compact"><M n="density_small" c="text-base" /></button>
            </div>
          </div>
          {psel.size > 0 && (
            <div className="bg-primary/5 rounded-xl border border-primary/30 px-4 py-2.5 mb-4 flex flex-wrap items-center gap-2 text-xs font-semibold">
              <span className="text-primary">{psel.size} selected</span>
              <div className="flex-1" />
              <button onClick={() => openLabelDlg("product")} disabled={pBusy} className="px-3 py-1.5 rounded-lg border border-slate-200 bg-white text-ink-secondary hover:bg-slate-50 flex items-center gap-1"><M n="print" c="text-sm" />Print labels</button>
              <button onClick={pExport} disabled={pBusy} className="px-3 py-1.5 rounded-lg border border-slate-200 bg-white text-ink-secondary hover:bg-slate-50 flex items-center gap-1"><M n="download" c="text-sm" />Export CSV</button>
              <button onClick={() => pAddBarcodes(true)} disabled={pBusy} className="px-3 py-1.5 rounded-lg border border-slate-200 bg-white text-ink-secondary hover:bg-slate-50 flex items-center gap-1"><M n="barcode" c="text-sm" />Add missing barcodes</button>
              <button onClick={pMarkup} disabled={pBusy} className="px-3 py-1.5 rounded-lg border border-slate-200 bg-white text-ink-secondary hover:bg-slate-50 flex items-center gap-1"><M n="percent" c="text-sm" />Adjust price</button>
              <button onClick={() => setBulkAdj(true)} disabled={pBusy} className="px-3 py-1.5 rounded-lg border border-slate-200 bg-white text-ink-secondary hover:bg-slate-50 flex items-center gap-1"><M n="tune" c="text-sm" />Adjust qty</button>
              <button onClick={() => setAction({ kind: "write-off" })} disabled={pBusy} className="px-3 py-1.5 rounded-lg border border-slate-200 bg-white text-ink-secondary hover:bg-slate-50 flex items-center gap-1"><M n="delete_sweep" c="text-sm" />Write off</button>
              {can("can_delete_products") && <button onClick={pDelete} disabled={pBusy} className="px-3 py-1.5 rounded-lg bg-rose-600 text-white hover:bg-rose-700 flex items-center gap-1"><M n="archive" c="text-sm" />Archive</button>}
              <button aria-label="Close" onClick={pClear} className="px-2 py-1.5 rounded-lg hover:bg-slate-200/60 text-ink-tertiary"><M n="close" c="text-sm" /></button>
            </div>
          )}
          <div className="bg-white rounded-2xl border border-slate-200/90 overflow-hidden">
            <div className="overflow-x-auto">
            <table data-density={density} className="w-full min-w-[880px] text-left border-collapse">
              <thead><tr className="border-b border-slate-200 bg-slate-50/60 text-xs font-semibold text-ink-tertiary">
                <th className="py-3.5 pl-5 pr-2 w-10"><input type="checkbox" aria-label="Select all" checked={pAllSel} onChange={pToggleAll} className="w-4 h-4 rounded border-slate-300 text-primary cursor-pointer accent-primary" /></th>
                <th className="py-3.5 px-3">Product</th><th className="py-3.5 px-3">Category</th><th className="py-3.5 px-3">Unit</th>
                <th className="py-3.5 px-3 text-right">Cost ({currencySymbol()})</th><th className="py-3.5 px-3 text-right">Sell price ({currencySymbol()})</th><th className="py-3.5 px-3 text-right">Margin</th><th className="py-3.5 px-3 text-center">In stock</th>{cfg.enable_reservations && <th className="py-3.5 px-3 text-center">Reserved</th>}<th className="py-3.5 pr-5 pl-2 w-12"><span className="sr-only">Actions</span></th>
              </tr></thead>
              <tbody className="divide-y divide-slate-100 text-xs">
                {/* 144bu — table-body skeleton (chrome stays real; geometry matches rows → no layout shift) */}
                {!products && Array.from({ length: 6 }).map((_, i) => (
                  <tr key={`sk-${i}`}>
                    <td className="py-3.5 pl-5 pr-2"><span className="sk block w-4 h-4 rounded" /></td>
                    <td className="py-3.5 px-3"><div className="flex items-center gap-3"><span className="sk block w-8 h-8 rounded-lg" /><div className="space-y-1.5"><span className="sk block h-3 w-32" /><span className="sk block h-2.5 w-20" /></div></div></td>
                    <td className="py-3.5 px-3"><span className="sk block h-3 w-16" /></td>
                    <td className="py-3.5 px-3"><span className="sk block h-3 w-10" /></td>
                    <td className="py-3.5 px-3"><span className="sk block h-3 w-14 ml-auto" /></td>
                    <td className="py-3.5 px-3"><span className="sk block h-3 w-14 ml-auto" /></td>
                    <td className="py-3.5 px-3"><span className="sk block h-3 w-10 ml-auto" /></td>
                    <td className="py-3.5 px-3"><span className="sk block h-5 w-16 rounded-full mx-auto" /></td>
                    {cfg.enable_reservations && <td className="py-3.5 px-3"><span className="sk block h-3 w-8 mx-auto" /></td>}
                    <td className="py-3.5 pr-5 pl-2" />
                  </tr>
                ))}
                {products && productsFailed && (
                  <tr><td colSpan={stockCols} className="py-14 px-5"><StockLoadError onRetry={loadProducts} /></td></tr>
                )}
                {products && !productsFailed && shownProducts.length === 0 && (
                  <tr><td colSpan={stockCols} className="py-14 px-5">
                    {products.length === 0 ? (
                      <div className="flex flex-col items-center text-center gap-3 max-w-sm mx-auto">
                        <div className="w-11 h-11 rounded-full bg-slate-100 text-ink-tertiary flex items-center justify-center"><M n="inventory_2" c="text-2xl" /></div>
                        <div><div className="font-semibold text-ink-primary text-sm">No stock yet</div><div className="text-ink-tertiary mt-0.5">Add your first part, or paste a stock sheet to bring your inventory in.</div></div>
                        <div className="flex gap-2 mt-1">
                          <button onClick={() => setDrawer({ kind: "product" })} className={primaryBtn}><M n="add" c="text-sm" />Add product/stock</button>
                          <button onClick={() => setPostingOpen(true)} className={ghostBtn}><M n="content_paste" c="text-sm" />New posting</button>
                        </div>
                      </div>
                    ) : (
                      <div className="flex flex-col items-center text-center gap-3 max-w-sm mx-auto">
                        <div className="w-11 h-11 rounded-full bg-slate-100 text-ink-tertiary flex items-center justify-center"><M n="search_off" c="text-2xl" /></div>
                        {hiddenByAvail > 0 ? (
                          <>
                            <div><div className="font-semibold text-ink-primary text-sm">{hiddenByAvail === 1 ? "1 part matches, but it isn't shown" : `${hiddenByAvail} parts match, but they aren't shown`}</div><div className="text-ink-tertiary mt-0.5">The availability filter is set to {AVAIL_LABEL[avail].toLowerCase()}.</div></div>
                            <button onClick={() => setAvail("all")} className={`${primaryBtn} mt-1`}><M n="visibility" c="text-sm" />Show all matches</button>
                          </>
                        ) : (
                          <>
                            <div><div className="font-semibold text-ink-primary text-sm">No parts match these filters</div><div className="text-ink-tertiary mt-0.5">Try adjusting availability, category, warehouse or your search.</div></div>
                            <button onClick={() => { setAvail("in"); setCat(""); setWh(""); setPq(""); }} className={`${ghostBtn} mt-1`}><M n="filter_alt_off" c="text-sm" />Clear filters</button>
                          </>
                        )}
                      </div>
                    )}
                  </td></tr>
                )}
                {pageProducts.map((p) => {
                  const pill = stockPill(p); const m = marginOf(p);
                  return (
                    <tr key={p.id} onClick={() => setDrawer({ kind: "product", id: p.id })} className={`hover:bg-slate-50 transition-colors cursor-pointer group ${psel.has(p.id) ? "bg-primary/5" : ""}`}>
                      <td className="py-3.5 pl-5 pr-2" onClick={(e) => e.stopPropagation()}><input type="checkbox" aria-label="Select row" checked={psel.has(p.id)} onChange={() => pToggle(p.id)} className="w-4 h-4 rounded border-slate-300 text-primary cursor-pointer accent-primary" /></td>
                      <td className="py-3.5 px-3"><div className="flex items-center gap-3">
                        {p.image_data ? <img src={p.image_data} alt="" className="w-8 h-8 rounded-lg object-cover border border-slate-200 flex-shrink-0" /> : <span data-testid="product-glyph" className="w-8 h-8 rounded-lg bg-slate-100 text-ink-tertiary flex items-center justify-center flex-shrink-0"><M n={productGlyph(p.name, p.brand)} c="text-sm" /></span>}
                        <div className="min-w-0"><span className="font-semibold text-ink-primary block truncate">{p.name}</span><span className="text-[11px] text-ink-tertiary block truncate">{(() => { const codes = [p.sku || p.code, p.mpn ? `MPN ${p.mpn}` : "", p.barcode].filter(Boolean).join(" · "); return codes || p.brand ? <>{codes && <span className="font-mono">{codes}</span>}{codes && p.brand ? " · " : ""}{p.brand}</> : "—"; })()}</span></div>
                      </div></td>
                      <td className="py-3.5 px-3 text-ink-secondary font-medium">{p.category_id ? (catName[p.category_id] || "—") : <span className="text-ink-tertiary">Uncategorized</span>}</td>
                      <td className="py-3.5 px-3 text-ink-tertiary">{p.uom || "pcs"}</td>
                      <td className="py-3.5 px-3 tabular-nums text-ink-secondary text-right">{money(p.avg_cost || p.cost_price)}</td>
                      <td className="py-3.5 px-3 tabular-nums font-semibold text-ink-primary text-right">{money(p.sell_price)}</td>
                      <td data-testid="margin" className={`py-3.5 px-3 tabular-nums text-right ${m == null ? "text-ink-tertiary" : m < 0 ? "text-rose-600 font-semibold" : "text-ink-secondary"}`}>{m != null ? `${m < 0 ? "−" : ""}${Math.abs(m)}%` : "—"}</td>
                      <td className="py-3.5 px-3 text-center">{pill ? <span className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px] font-semibold border ${pill.cls}`}><span className={`w-1.5 h-1.5 rounded-full ${pill.dot}`} />{pill.label}</span> : <span className="font-semibold tabular-nums text-ink-primary">{p.qty_available ?? 0}</span>}</td>
                      {cfg.enable_reservations && <td className="py-3.5 px-3 text-center font-semibold tabular-nums text-ink-secondary">{p.qty_reserved ? <span className="text-amber-700">{p.qty_reserved}</span> : <span className="text-ink-tertiary">—</span>}</td>}
                      <td className="py-3.5 pr-5 pl-2 text-right" onClick={(e) => e.stopPropagation()}><RowMenu label={`More actions for ${p.name}`} items={[
                        { label: "Open", icon: "edit", onClick: () => setDrawer({ kind: "product", id: p.id }) },
                        { label: "Adjust quantity", icon: "tune", onClick: () => { setPsel(new Set([p.id])); setBulkAdj(true); } },
                        { label: "Print label", icon: "print", onClick: () => { setPsel(new Set([p.id])); openLabelDlg("product"); } },
                        { label: "Write off", icon: "delete_sweep", onClick: () => { setPsel(new Set([p.id])); setAction({ kind: "write-off" }); } },
                      ]} /></td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            </div>
            <Pager total={shownProducts.length} page={Math.min(pPage, pPages)} pageSize={PAGE_SIZE} onPage={setPPage} noun="items in stock" />
          </div>
        </>)}

        {/* DEVICES & ASSETS */}
        {tab === "devices" && (<>
          <div className="flex items-center gap-3 mb-5 flex-wrap">
            <button onClick={() => setAssetCreate(true)} className={primaryBtn}><M n="add" c="text-sm" />Add {term.asset}</button>
            <AssetStateTabs value={aState} onChange={setAState} counts={ap.extra?.counts} />
            <select value={aOwner} onChange={(e) => setAOwner(e.target.value as "" | "company" | "client")} aria-label="Ownership" className={`${inputCls} w-auto h-8 py-0 text-xs font-semibold`}>
              <option value="">Ownership: all</option><option value="company">Company-owned</option><option value="client">Client-owned</option>
            </select>
            {searchBar("Search serial, IMEI, plate, client…", aq, setAq)}
          </div>
          {asel.size > 0 && (
            <div className="bg-primary/5 rounded-xl border border-primary/30 px-4 py-2.5 mb-4 flex flex-wrap items-center gap-2 text-xs font-semibold">
              <span className="text-primary">{asel.size} selected</span>
              <div className="flex-1" />
              <button onClick={() => openLabelDlg("asset")} className="px-3 py-1.5 rounded-lg border border-slate-200 bg-white text-ink-secondary hover:bg-slate-50 flex items-center gap-1"><M n="print" c="text-sm" />Print labels</button>
              <button aria-label="Close" onClick={() => setAsel(new Set())} className="px-2 py-1.5 rounded-lg hover:bg-slate-200/60 text-ink-tertiary"><M n="close" c="text-sm" /></button>
            </div>
          )}
          <div className="bg-white rounded-2xl border border-slate-200/90 overflow-hidden">
            <table className="w-full text-left border-collapse">
              <thead className="border-b border-slate-200 bg-slate-50/60 text-xs font-semibold text-ink-tertiary"><tr>
                <th className="py-3.5 pl-5 pr-2 w-10"><input type="checkbox" aria-label="Select all" checked={!!assets?.length && assets.every((a) => asel.has(a.id))} onChange={(e) => setAsel((s) => { const n = new Set(s); (assets || []).forEach((a) => (e.target.checked ? n.add(a.id) : n.delete(a.id))); return n; })} className="w-4 h-4 rounded border-slate-300 text-primary cursor-pointer accent-primary" /></th>
                <th className="py-3.5 px-3">Device / Model</th><th className="py-3.5 px-3">Serial / IMEI</th><th className="py-3.5 px-3">Ownership</th><th className="py-3.5 px-3">Status</th><th className="py-3.5 pr-5 pl-2 text-right">Actions</th>
              </tr></thead>
              <tbody className="divide-y divide-slate-100 text-xs">
                {!assets && <SkeletonRows cols={6} />}
                {assets?.length === 0 && <tr><td colSpan={6} className="py-6 px-5 text-ink-tertiary">{daq ? `Nothing matches “${daq}”.` : aState === "written_off" ? `No written-off ${term.assets.toLowerCase()}.` : `No ${term.assets.toLowerCase()} registered.`}</td></tr>}
                {pageAssets.map((a) => (
                  <tr key={a.id} className="hover:bg-blue-50/30 transition-colors cursor-pointer" onClick={() => setAssetOpen(a.id)}>
                    <td className="py-3.5 pl-5 pr-2" onClick={(e) => e.stopPropagation()}><input type="checkbox" aria-label="Select row" checked={asel.has(a.id)} onChange={() => aToggle(a.id)} className="w-4 h-4 rounded border-slate-300 text-primary cursor-pointer accent-primary" /></td>
                    <td className="py-3.5 px-3"><div className="font-bold text-ink-primary">{[a.brand, a.model].filter(Boolean).join(" ") || a.asset_type || assetNoun(a, term)}</div><span className="text-[11px] text-ink-tertiary font-normal block">{a.asset_type && a.asset_type !== "Device" ? a.asset_type : assetNoun(a, term)}</span></td>
                    <td className="py-3.5 px-3 font-mono text-[11px] text-ink-secondary">{a.imei || a.serial || "—"}</td>
                    <td className="py-3.5 px-3"><span className={`px-2 py-0.5 rounded text-[11px] font-semibold border ${a.owner_type === "company" ? "bg-purple-50 text-purple-600 border-purple-100" : "bg-blue-50 text-primary border-blue-200/60"}`}>{a.owner_type === "company" ? "Company" : "Client"}</span></td>
                    <td className="py-3.5 px-3">{(() => { const tone = assetStatusTone(a.asset_status); return <span className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px] font-semibold border ${tone.cls}`}><span className={`w-1.5 h-1.5 rounded-full ${tone.dot}`} />{tone.label}</span>; })()}</td>
                    <td className="py-3.5 pr-5 pl-2 text-right"><button className="text-primary font-semibold text-xs hover:underline">Inspect</button></td>
                  </tr>
                ))}
              </tbody>
            </table>
            <Pager total={ap.total} page={ap.page} pageSize={ap.pageSize} onPage={ap.setPage} noun="devices & assets" />
          </div>
        </>)}

        {/* Services & Bundles live under My company (CompanyHub), not Inventory. */}

        {/* PURCHASE ORDERS */}
        {tab === "purchase-orders" && (<>
          <div className="flex items-center gap-3 mb-5 flex-wrap"><button onClick={() => setNewPo(true)} className={primaryBtn}><M n="add" c="text-sm" />New purchase order</button><button onClick={() => setReorderOpen(true)} className={ghostBtn} title="Suggest POs for products below minimum stock"><M n="sync_alt" c="text-sm text-ink-tertiary" />Reorder suggestions</button><button onClick={() => setCockpitOpen(true)} className={ghostBtn} title="What needs attention: overdue, arriving, recost, invoices, low stock"><M n="target" c="text-sm text-ink-tertiary" />Cockpit</button>{searchBar("Search purchase orders, supplier, PO#…", poQ, (v) => { setPoQ(v); setSecPage(1); })}</div>
          <div className="bg-white rounded-2xl border border-slate-200/90 overflow-hidden">
            <table className="w-full text-left border-collapse">
              <thead className="border-b border-slate-200 bg-slate-50/60 text-xs font-semibold text-ink-tertiary"><tr>
                <th className="py-3.5 pl-5 px-3">PO number</th><th className="py-3.5 px-3">Supplier</th><th className="py-3.5 px-3">Expected</th><th className="py-3.5 px-3 text-right">Total ({currencySymbol()})</th><th className="py-3.5 px-3 text-center">Status</th><th className="py-3.5 pr-5 pl-2 text-right">Actions</th>
              </tr></thead>
              <tbody className="divide-y divide-slate-100 text-xs">
                {!pos && <SkeletonRows cols={6} />}
                {pos?.length === 0 && <tr><td colSpan={6} className="py-6 px-5 text-ink-tertiary">No purchase orders yet.</td></tr>}
                {!!pos?.length && shownPos.length === 0 && <tr><td colSpan={6} className="py-6 px-5 text-ink-tertiary">No purchase orders match "{poQ.trim()}".</td></tr>}
                {pagePos.map((po) => {
                  const st = (po.status || "draft").toLowerCase();
                  const cls = st === "received" ? "bg-emerald-50 text-emerald-700 border-emerald-200/60" : st === "partial" || st === "ordered" ? "bg-amber-50 text-amber-700 border-amber-200/60" : "bg-slate-100 text-slate-600 border-slate-200";
                  return (
                    <tr key={po.id} className="hover:bg-blue-50/30 transition-colors">
                      <td className="py-3.5 pl-5 px-3 font-mono font-bold text-primary">{po.po_number || po.id}</td>
                      <td className="py-3.5 px-3 font-semibold text-ink-primary">{supName[po.supplier_id || ""] || "—"}</td>
                      <td className="py-3.5 px-3 text-ink-secondary">{po.expected_at ? new Date(po.expected_at).toLocaleDateString() : "—"}</td>
                      <td className="py-3.5 px-3 text-right tabular-nums font-bold text-ink-primary">{money(po.total)}</td>
                      <td className="py-3.5 px-3 text-center"><span className={`inline-flex items-center px-2.5 py-1 rounded-full text-[11px] font-semibold border capitalize ${cls}`}>{st}</span></td>
                      <td className="py-3.5 pr-5 pl-2 text-right"><button aria-label="Print / PDF" onClick={() => openAuthedDoc(`/print/purchase-order/${po.id}`).catch(() => alertDialog("Could not open PO PDF"))} title="Print / PDF" className="p-1.5 rounded-lg text-ink-tertiary hover:text-primary hover:bg-slate-100 align-middle"><M n="print" c="text-sm" /></button><button onClick={() => setPoOpen(po.id)} className="text-primary font-semibold text-xs hover:underline ml-1">View details</button></td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            {shownPos.length > 0 && <Pager total={shownPos.length} page={Math.min(secPage, poPages)} pageSize={PAGE_SIZE} onPage={setSecPage} noun="purchase orders" />}
          </div>
        </>)}

        {/* AUDIT tabs — live data tables */}
        {AUDIT[tab] && (
          <>
            <div className="mb-4 flex items-start justify-between gap-3">
              <div><h3 className="text-sm font-semibold text-ink-primary">{AUDIT[tab].title}</h3><p className="text-xs text-ink-tertiary">{AUDIT[tab].sub}</p></div>
              {tab === "transfers" && <button onClick={() => setAction({ kind: "transfer" })} className={`${primaryBtn} whitespace-nowrap`}><M n="add" c="text-sm" />New transfer</button>}
              {tab === "write-offs" && <button onClick={() => setAction({ kind: "write-off" })} className={`${primaryBtn} whitespace-nowrap`}><M n="add" c="text-sm" />New write-off</button>}
              {tab === "returns" && <button onClick={() => setAction({ kind: "return" })} className={`${primaryBtn} whitespace-nowrap`}><M n="add" c="text-sm" />New return</button>}
              {tab === "conversions" && <button onClick={() => setConv(true)} className={`${primaryBtn} whitespace-nowrap`}><M n="add" c="text-sm" />New conversion</button>}
              {tab === "stock-takes" && <button onClick={() => setStk({})} className={`${primaryBtn} whitespace-nowrap`}><M n="add" c="text-sm" />New stock take</button>}
            </div>
            <div className="bg-white rounded-2xl border border-slate-200/90 overflow-hidden">
              <table className="w-full text-left border-collapse text-xs">
                <thead className="border-b border-slate-200 bg-slate-50/60 text-xs font-semibold text-ink-tertiary"><tr>
                  {(AUDIT_COLS[tab] || []).map(([h]) => <th key={h} className="py-3 px-4">{h}</th>)}
                </tr></thead>
                <tbody className="divide-y divide-slate-100 text-ink-secondary">
                  {auditRows[tab] == null && <SkeletonRows cols={8} />}
                  {auditRows[tab]?.length === 0 && <tr><td colSpan={8} className="py-12 px-4"><AuditEmpty tab={tab} title={AUDIT[tab].title} sub={AUDIT[tab].sub} onStart={AUDIT_START[tab]} /></td></tr>}
                  {pageAudit.map((row: any, i: number) => (
                    <tr key={row?.id || i} onClick={tab === "stock-takes" && row?.id ? () => setStk({ id: row.id }) : undefined} className={`hover:bg-blue-50/30 ${tab === "stock-takes" ? "cursor-pointer" : ""}`}>
                      {(AUDIT_COLS[tab] || []).map(([h, fn], j) => <td key={h} className={`py-2.5 px-4 ${j === 0 ? "font-bold text-ink-primary" : ""}`}>{fn(row)}</td>)}
                    </tr>
                  ))}
                </tbody>
              </table>
              {auditAll.length > 0 && <Pager total={auditAll.length} page={Math.min(secPage, auditPages)} pageSize={PAGE_SIZE} onPage={setSecPage} noun={AUDIT[tab].title.toLowerCase()} />}
            </div>
          </>
        )}

        {tab === "bins" && <BinsPanel />}
      </div>

      {labelDlg && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-ink-primary/40 backdrop-blur-[2px]" onClick={() => setLabelDlg(false)}>
          <div className="bg-white rounded-2xl shadow-drawer w-[380px] max-w-full p-5" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between mb-3"><h3 className="font-bold text-ink-primary">Print labels</h3><button aria-label="Close" onClick={() => setLabelDlg(false)} className="w-7 h-7 rounded-lg hover:bg-slate-100 grid place-items-center text-ink-tertiary"><M n="close" c="text-base" /></button></div>
            <p className="text-xs text-ink-tertiary mb-3">{labelSelCount} {labelMode === "asset" ? "asset" : "product"}{labelSelCount === 1 ? "" : "s"} selected. Each prints at the template's exact label size.</p>
            <label className="block text-[11px] font-semibold text-ink-tertiary mb-1">Label template</label>
            <select value={labelTplId} onChange={(e) => setLabelTplId(e.target.value)} className="w-full bg-white border border-slate-200 rounded-lg text-sm px-2.5 py-2 mb-3">
              {labelTpls.length === 0 && <option value="">No label templates — create one in Settings</option>}
              {labelTpls.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
            </select>
            <label className="block text-[11px] font-semibold text-ink-tertiary mb-1">Copies each</label>
            <input type="number" min={1} max={500} value={labelCopies} onChange={(e) => setLabelCopies(Math.max(1, +e.target.value || 1))} className="w-full bg-white border border-slate-200 rounded-lg text-sm px-2.5 py-2 mb-4" />
            {labelErr && <div role="alert" className="mb-3 rounded-lg bg-rose-50 border border-rose-200 text-rose-700 text-xs px-3 py-2">{labelErr}</div>}
            <div className="flex justify-end gap-2">
              <button onClick={() => setLabelDlg(false)} className="px-4 py-2 rounded-lg border border-slate-200 text-ink-secondary hover:bg-slate-100 font-semibold text-xs">Cancel</button>
              <button onClick={printLabels} disabled={!labelTplId} className="px-4 py-2 rounded-lg bg-primary text-white font-semibold text-xs hover:bg-primary-container disabled:opacity-60 flex items-center gap-1"><M n="print" c="text-[15px]" /> Print {labelSelCount * labelCopies} label{labelSelCount * labelCopies === 1 ? "" : "s"}</button>
            </div>
          </div>
        </div>
      )}
      {newPo && <NewPODrawer products={products || []} onClose={() => setNewPo(false)} onCreated={() => { setNewPo(false); api.get<{ items: PO[] }>("/inventory/po").then((r) => setPos(r.items || [])).catch(() => {}); }} />}
      {poOpen && <PODrawer poId={poOpen} onClose={() => setPoOpen(null)} onChanged={() => { api.get<{ items: PO[] }>("/inventory/po").then((r) => setPos(r.items || [])).catch(() => {}); loadProducts(); }} />}
      {drawer && <InventoryDrawer kind={drawer.kind} id={drawer.id} initial={drawer.initial} onClose={() => setDrawer(null)} onSaved={() => { if (drawer.kind === "product") loadProducts(); if (drawer.kind === "service") loadServices(); }} />}
      {assetOpen && <AssetDrawer assetId={assetOpen} onClose={() => { setAssetOpen(null); ap.reload(); }} />}
      {assetCreate && <AssetDrawer onClose={() => setAssetCreate(false)} onSaved={() => ap.reload()} />}
      {action && <ActionModal kind={action.kind} products={products || []} preselected={[...psel]}
        onClose={() => setAction(null)}
        onDone={() => { setAction(null); pClear(); setAuditRows((s) => ({ ...s, transfers: undefined as any, "write-offs": undefined as any, returns: undefined as any })); loadProducts(); setTab(action.kind === "transfer" ? "transfers" : action.kind === "return" ? "returns" : "write-offs"); }} />}
      {stk && <StocktakeDrawer id={stk.id} products={products || []}
        onClose={() => setStk(null)}
        onDone={() => { setStk(null); setAuditRows((s) => ({ ...s, "stock-takes": undefined as any })); loadProducts(); setTab("stock-takes"); }} />}
      {conv && <ConversionModal onClose={() => setConv(false)} onDone={() => { setConv(false); setAuditRows((s) => ({ ...s, conversions: undefined as any })); loadProducts(); setTab("conversions"); }} />}
      {reorderOpen && <ReorderModal onClose={() => setReorderOpen(false)} onCreated={() => { setReorderOpen(false); setPos(null); setTab("purchase-orders"); }} />}
      {cockpitOpen && <PurchasingCockpit onClose={() => setCockpitOpen(false)} onOpenPO={(id) => { setCockpitOpen(false); setPoOpen(id); }} onReorder={() => { setCockpitOpen(false); setReorderOpen(true); }} />}
      {postingOpen && <PostingModal products={products || []} onClose={() => setPostingOpen(false)} onDone={() => { setPostingOpen(false); loadProducts(); setAuditRows((s) => ({ ...s, postings: undefined as any })); setTab("postings"); }} />}
      {bulkAdj && <BulkAdjustModal items={(products || []).filter((p) => psel.has(p.id))} onClose={() => setBulkAdj(false)} onDone={() => { setBulkAdj(false); pClear(); loadProducts(); }} />}
      {toOrderOpen && <PartsToOrderModal onClose={() => setToOrderOpen(false)} onCreated={() => { setToOrderOpen(false); setPos(null); setTab("purchase-orders"); }} />}
      {scanStock && <ScanStockPanel onClose={() => setScanStock(false)} onPosted={loadProducts} />}
    </Shell>
  );
}

/* ── Stock take run-flow: New count → scan-to-count + manual edit → post variance ─ */
type StLine = { id: string; part_id: string; part_name: string; system_qty: number; counted_qty: number | null; variance: number | null };
function StocktakeDrawer({ id, products, onClose, onDone }:
  { id?: string; products: Product[]; onClose: () => void; onDone: () => void }) {
  const [stId, setStId] = useState<string | undefined>(id);
  const [status, setStatus] = useState<string>("open");
  const [lines, setLines] = useState<StLine[] | null>(null);
  const [locations, setLocations] = useState<{ id: string; name: string }[]>([]);
  const [locId, setLocId] = useState(""); const [kind, setKind] = useState<"full" | "partial">("full");
  const [scan, setScan] = useState(""); const [scanQty, setScanQty] = useState(1); const [regUnknown, setRegUnknown] = useState(false);
  const [busy, setBusy] = useState(false); const [err, setErr] = useState<string | null>(null); const [flash, setFlash] = useState<string | null>(null);
  const scanRef = useRef<HTMLInputElement>(null);

  async function load(theId: string) {
    const r = await api.get<any>(`/stocktake/${theId}`);
    setStatus(r.status || "open"); setLines(r.lines || []);
  }
  useEffect(() => {
    api.get<any>("/staff/locations").then((r) => { const it = r.items || r || []; setLocations(it); if (it[0]) setLocId(it[0].id); }).catch(() => {});
    if (id) load(id).catch(() => setErr("Could not load stock take"));
  }, []); // eslint-disable-line

  async function start() {
    if (!locId) { setErr("Pick a location."); return; }
    setBusy(true); setErr(null);
    try {
      const r = await api.post<any>("/stocktake", { location_id: locId, kind });
      setStId(r.id); await load(r.id);
    } catch (e: any) { setErr(e.message || "Could not start count"); }
    finally { setBusy(false); }
  }

  async function doScan(scanned?: string) {
    const code = (scanned ?? scan).trim(); if (!code || !stId) return;
    setErr(null);
    try {
      const r = await api.post<any>(`/stocktake/${stId}/scan`, { code, qty: scanQty, register_unknown: regUnknown });
      setFlash(`${r.part_name}: counted ${r.counted_qty}${r.is_unknown ? " (new)" : ""}`);
      setScan(""); scanRef.current?.focus();
      await load(stId);
    } catch (e: any) {
      setErr(e.message || "Scan failed");
      if (/register/i.test(e.message || "")) setRegUnknown(true);
    }
  }

  async function setCount(line: StLine, v: number) {
    if (!stId) return;
    setLines((ls) => ls!.map((l) => l.id === line.id ? { ...l, counted_qty: v, variance: Math.round((v - l.system_qty) * 1000) / 1000 } : l));
    try { await api.patch(`/stocktake/${stId}/lines/${line.id}`, { counted_qty: v }); } catch { /* reload on failure */ await load(stId); }
  }

  async function post() {
    if (!stId) return;
    const counted = (lines || []).filter((l) => l.counted_qty != null);
    const varLines = counted.filter((l) => (l.variance || 0) !== 0);
    if (!(await confirmDialog(`Post this count? ${varLines.length} line(s) with a variance will adjust stock. ${(lines!.length - counted.length)} uncounted line(s) are left untouched.`))) return;
    setBusy(true); setErr(null);
    try { await api.post(`/stocktake/${stId}/post`, {}); onDone(); }
    catch (e: any) { setErr(e.message || "Could not post"); setBusy(false); }
  }

  const posted = status === "posted";
  const totVar = (lines || []).reduce((s, l) => s + (l.variance || 0), 0);
  const nameFor = (l: StLine) => l.part_name || products.find((p) => p.id === l.part_id)?.name || "—";

  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      <div className="absolute inset-0 bg-ink-primary/40 backdrop-blur-[2px]" onClick={onClose} />
      <div className="relative w-[600px] max-w-full bg-white h-full shadow-drawer flex flex-col">
        <div className="flex items-center justify-between px-5 py-4 border-b border-slate-200">
          <h2 className="font-bold text-ink-primary flex items-center gap-2"><M n="fact_check" c="text-base text-primary" />{stId ? (posted ? "Stock take (posted)" : "Count in progress") : "New stock take"}</h2>
          <button aria-label="Close" onClick={onClose} className="w-8 h-8 rounded-lg hover:bg-slate-200/60 flex items-center justify-center text-ink-tertiary"><M n="close" c="text-base" /></button>
        </div>
        <div className="flex-1 overflow-y-auto p-5 space-y-4">
          {err && <div className="rounded-lg bg-rose-50 border border-rose-200 text-rose-700 text-xs px-3 py-2">{err}</div>}

          {!stId ? (
            <>
              <div><label className="block text-[11px] font-semibold text-ink-tertiary mb-1">Location</label>
                <select value={locId} onChange={(e) => setLocId(e.target.value)} className="w-full bg-white border border-slate-200 rounded-lg text-sm px-3 py-2">{locations.map((l) => <option key={l.id} value={l.id}>{l.name}</option>)}</select></div>
              <div><label className="block text-[11px] font-semibold text-ink-tertiary mb-1">Count type</label>
                <div className="flex gap-2">
                  <button onClick={() => setKind("full")} className={`flex-1 rounded-lg border px-3 py-2 text-xs font-semibold text-left ${kind === "full" ? "border-primary bg-primary/5 text-primary" : "border-slate-200 text-ink-secondary"}`}>Full count<span className="block font-normal text-ink-tertiary mt-0.5">Seeds every part on hand at this location.</span></button>
                  <button onClick={() => setKind("partial")} className={`flex-1 rounded-lg border px-3 py-2 text-xs font-semibold text-left ${kind === "partial" ? "border-primary bg-primary/5 text-primary" : "border-slate-200 text-ink-secondary"}`}>Partial count<span className="block font-normal text-ink-tertiary mt-0.5">Starts empty — only parts you scan or add are counted.</span></button>
                </div></div>
            </>
          ) : (
            <>
              {!posted && (
                <div className="rounded-xl border border-slate-200 bg-slate-50/60 p-3 space-y-2">
                  <label className="block text-[11px] font-semibold text-ink-tertiary ">Scan or type a code</label>
                  <div className="flex items-center gap-2">
                    <input ref={scanRef} autoFocus value={scan} onChange={(e) => setScan(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") doScan(); }} placeholder="Barcode / SKU / serial" className="flex-1 bg-white border border-slate-200 rounded-lg text-sm px-3 py-2" />
                    <input type="number" min={1} value={scanQty} onChange={(e) => setScanQty(Math.max(1, +e.target.value || 1))} className="w-16 bg-white border border-slate-200 rounded-lg text-sm px-2 py-2 text-right" title="Quantity per scan" />
                    <ScanButton title="Count with camera" onScan={(c) => doScan(c)} />
                    <button onClick={() => doScan()} className="px-3 py-2 rounded-lg bg-primary text-white font-semibold text-xs hover:bg-primary-container flex items-center gap-1"><M n="qr_code_scanner" c="text-sm" />Count</button>
                  </div>
                  <label className="flex items-center gap-2 text-xs text-ink-secondary"><input type="checkbox" checked={regUnknown} onChange={(e) => setRegUnknown(e.target.checked)} />Register unknown codes as new products</label>
                  {flash && <div className="text-xs text-emerald-700 font-semibold">{flash}</div>}
                </div>
              )}
              <div className="flex items-center justify-between text-xs text-ink-tertiary">
                <span>{lines?.length ?? 0} line(s) · {(lines || []).filter((l) => l.counted_qty != null).length} counted</span>
                <span>Net variance: <b className={totVar === 0 ? "text-ink-secondary" : totVar < 0 ? "text-rose-600" : "text-emerald-600"}>{totVar > 0 ? "+" : ""}{totVar}</b></span>
              </div>
              <div className="rounded-xl border border-slate-200 overflow-hidden">
                <table className="w-full text-left text-xs">
                  <thead className="bg-slate-50/60 text-[11px] font-bold text-ink-tertiary "><tr>
                    <th className="py-2 px-3">Part</th><th className="py-2 px-3 text-right">System</th><th className="py-2 px-3 text-right w-24">Counted</th><th className="py-2 px-3 text-right">Variance</th>
                  </tr></thead>
                  <tbody className="divide-y divide-slate-100 text-ink-secondary">
                    {lines == null && <SkeletonRows cols={4} />}
                    {lines?.length === 0 && <tr><td colSpan={4} className="py-5 px-3 text-ink-tertiary">No lines yet — scan a part to count it.</td></tr>}
                    {lines?.map((l) => (
                      <tr key={l.id}>
                        <td className="py-2 px-3 font-semibold text-ink-primary">{nameFor(l)}</td>
                        <td className="py-2 px-3 text-right tabular-nums">{l.system_qty}</td>
                        <td className="py-2 px-3 text-right">
                          {posted ? <span className="tabular-nums">{l.counted_qty ?? "—"}</span> :
                            <input type="number" value={l.counted_qty ?? ""} onChange={(e) => setCount(l, e.target.value === "" ? 0 : Number(e.target.value))} className="w-20 bg-white border border-slate-200 rounded-lg text-xs px-2 py-1 text-right" />}
                        </td>
                        <td className={`py-2 px-3 text-right tabular-nums font-bold ${(l.variance || 0) === 0 ? "text-ink-tertiary" : (l.variance || 0) < 0 ? "text-rose-600" : "text-emerald-600"}`}>{l.counted_qty == null ? "—" : `${(l.variance || 0) > 0 ? "+" : ""}${l.variance || 0}`}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </div>
        <div className="px-5 py-4 border-t border-slate-200 flex justify-end gap-2">
          <button onClick={onClose} className="px-4 py-2 rounded-lg border border-slate-200 text-ink-secondary hover:bg-slate-100 font-semibold text-xs">{posted ? "Close" : "Cancel"}</button>
          {!stId && <button onClick={start} disabled={busy} className="px-4 py-2 rounded-lg bg-primary text-white font-semibold text-xs hover:bg-primary-container disabled:opacity-60">{busy ? "Starting…" : "Start count"}</button>}
          {stId && !posted && <button onClick={post} disabled={busy} className="px-4 py-2 rounded-lg bg-primary text-white font-semibold text-xs hover:bg-primary-container disabled:opacity-60">{busy ? "Posting…" : "Post variance"}</button>}
        </div>
      </div>
    </div>
  );
}

/* ── Stock action modal: Transfer + Write-off with a line builder ─────────── */
function ActionModal({ kind, products, preselected, onClose, onDone }:
  { kind: "transfer" | "stocktake" | "write-off" | "return" | "conversion"; products: Product[]; preselected: string[]; onClose: () => void; onDone: () => void }) {
  const [locations, setLocations] = useState<{ id: string; name: string }[]>([]);
  const [fromLoc, setFromLoc] = useState(""); const [toLoc, setToLoc] = useState("");
  const [reason, setReason] = useState(""); const [note, setNote] = useState("");
  const [lines, setLines] = useState<{ part_id: string; qty: number }[]>(
    preselected.length ? preselected.map((id) => ({ part_id: id, qty: 1 })) : [{ part_id: "", qty: 1 }]);
  const [busy, setBusy] = useState(false); const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    api.get<any>("/staff/locations").then((r) => {
      const items = r.items || r || [];
      setLocations(items); if (items[0]) { setFromLoc(items[0].id); setToLoc(items[1]?.id || items[0].id); }
    }).catch(() => setLocations([]));
  }, []);

  const title = kind === "transfer" ? "New stock transfer" : kind === "return" ? "New client return" : "New write-off";
  const setLine = (i: number, k: string, v: any) => setLines(lines.map((l, j) => j === i ? { ...l, [k]: v } : l));
  const addLine = () => setLines([...lines, { part_id: "", qty: 1 }]);
  const rmLine = (i: number) => setLines(lines.filter((_, j) => j !== i));

  async function save() {
    const clean = lines.filter((l) => l.part_id && l.qty > 0);
    if (!clean.length) { setErr("Add at least one product line."); return; }
    setBusy(true); setErr(null);
    try {
      if (kind === "transfer") {
        if (fromLoc === toLoc) { setErr("Source and destination must differ."); setBusy(false); return; }
        await api.post("/transfers", { from_location_id: fromLoc, to_location_id: toLoc, note, lines: clean });
      } else if (kind === "return") {
        await api.post("/returns", { reason, lines: clean.map((l) => ({ ...l, restock: true })) });
      } else {
        await api.post("/stock-docs/write-offs", { location_id: fromLoc, reason, note, lines: clean });
      }
      onDone();
    } catch (e: any) { setErr(e.message || "Could not save"); setBusy(false); }
  }

  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      <div className="absolute inset-0 bg-ink-primary/40 backdrop-blur-[2px]" onClick={onClose} />
      <div className="relative w-[520px] max-w-full bg-white h-full shadow-drawer flex flex-col">
        <div className="flex items-center justify-between px-5 py-4 border-b border-slate-200">
          <h2 className="font-bold text-ink-primary">{title}</h2>
          <button aria-label="Close" onClick={onClose} className="w-8 h-8 rounded-lg hover:bg-slate-200/60 flex items-center justify-center text-ink-tertiary"><M n="close" c="text-base" /></button>
        </div>
        <div className="flex-1 overflow-y-auto p-5 space-y-4">
          {err && <div className="rounded-lg bg-rose-50 border border-rose-200 text-rose-700 text-xs px-3 py-2">{err}</div>}
          {kind === "transfer" ? (
            <div className="grid grid-cols-2 gap-3">
              <div><label className="block text-[11px] font-semibold text-ink-tertiary mb-1">From location</label>
                <select value={fromLoc} onChange={(e) => setFromLoc(e.target.value)} className="w-full bg-white border border-slate-200 rounded-lg text-sm px-3 py-2">{locations.map((l) => <option key={l.id} value={l.id}>{l.name}</option>)}</select></div>
              <div><label className="block text-[11px] font-semibold text-ink-tertiary mb-1">To location</label>
                <select value={toLoc} onChange={(e) => setToLoc(e.target.value)} className="w-full bg-white border border-slate-200 rounded-lg text-sm px-3 py-2">{locations.map((l) => <option key={l.id} value={l.id}>{l.name}</option>)}</select></div>
            </div>
          ) : kind === "return" ? (
            <div><label className="block text-[11px] font-semibold text-ink-tertiary mb-1">Reason</label>
              <input value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Faulty / wrong item / changed mind" className="w-full bg-white border border-slate-200 rounded-lg text-sm px-3 py-2" /></div>
          ) : (
            <div className="grid grid-cols-2 gap-3">
              <div><label className="block text-[11px] font-semibold text-ink-tertiary mb-1">Location</label>
                <select value={fromLoc} onChange={(e) => setFromLoc(e.target.value)} className="w-full bg-white border border-slate-200 rounded-lg text-sm px-3 py-2">{locations.map((l) => <option key={l.id} value={l.id}>{l.name}</option>)}</select></div>
              <div><label className="block text-[11px] font-semibold text-ink-tertiary mb-1">Reason</label>
                <input value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Damaged / lost / expired" className="w-full bg-white border border-slate-200 rounded-lg text-sm px-3 py-2" /></div>
            </div>
          )}
          <div>
            <div className="flex items-center justify-between mb-1"><label className="text-[11px] font-semibold text-ink-tertiary ">Lines</label>
              <button onClick={addLine} className="text-primary text-xs font-semibold flex items-center gap-1"><M n="add" c="text-sm" />Add line</button></div>
            <div className="space-y-2">
              {lines.map((l, i) => (
                <div key={i} className="flex items-center gap-2">
                  <select value={l.part_id} onChange={(e) => setLine(i, "part_id", e.target.value)} className="flex-1 bg-white border border-slate-200 rounded-lg text-sm px-2 py-2">
                    <option value="">Select product…</option>
                    {products.map((p) => <option key={p.id} value={p.id}>{p.name}{p.sku ? ` (${p.sku})` : ""}</option>)}
                  </select>
                  <input type="number" min={0} value={l.qty} onChange={(e) => setLine(i, "qty", Number(e.target.value))} className="w-20 bg-white border border-slate-200 rounded-lg text-sm px-2 py-2 text-right" />
                  <button aria-label="Delete" onClick={() => rmLine(i)} className="w-8 h-8 rounded-lg hover:bg-rose-50 text-ink-tertiary hover:text-rose-600 flex items-center justify-center"><M n="delete" c="text-sm" /></button>
                </div>
              ))}
            </div>
          </div>
          <div><label className="block text-[11px] font-semibold text-ink-tertiary mb-1">Note</label>
            <textarea value={note} onChange={(e) => setNote(e.target.value)} rows={2} className="w-full bg-white border border-slate-200 rounded-lg text-sm px-3 py-2" /></div>
        </div>
        <div className="px-5 py-4 border-t border-slate-200 flex justify-end gap-2">
          <button onClick={onClose} className="px-4 py-2 rounded-lg border border-slate-200 text-ink-secondary hover:bg-slate-100 font-semibold text-xs">Cancel</button>
          <button onClick={save} disabled={busy} className="px-4 py-2 rounded-lg bg-primary text-white font-semibold text-xs hover:bg-primary-container disabled:opacity-60">{busy ? "Saving…" : title.replace("New ", "Create ")}</button>
        </div>
      </div>
    </div>
  );
}

/* ── Conversion modal: turn a client/company asset into a sellable product ── */
function ConversionModal({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
  const term = useTerminology();
  const [assets, setAssets] = useState<{ id: string; brand?: string; model?: string; serial?: string }[]>([]);
  const [locations, setLocations] = useState<{ id: string; name: string }[]>([]);
  const [deviceId, setDeviceId] = useState("");
  const [name, setName] = useState(""); const [sku, setSku] = useState(""); const [category, setCategory] = useState("");
  const [cost, setCost] = useState(0); const [sell, setSell] = useState(0);
  const [stockUnit, setStockUnit] = useState(false); const [locId, setLocId] = useState(""); const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false); const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    api.get<any>("/assets?limit=200").then((r) => setAssets(r.items || [])).catch(() => setAssets([]));
    api.get<any>("/staff/locations").then((r) => { const it = r.items || r || []; setLocations(it); if (it[0]) setLocId(it[0].id); }).catch(() => {});
  }, []);
  // prefill name from the picked asset (never the serial → sku, per backend note)
  useEffect(() => {
    if (!deviceId) return;
    const a = assets.find((x) => x.id === deviceId);
    if (a && !name) setName([a.brand, a.model].filter(Boolean).join(" "));
  }, [deviceId]); // eslint-disable-line
  async function save() {
    if (!deviceId) { setErr("Pick an asset to convert."); return; }
    if (!name.trim()) { setErr("Product name is required."); return; }
    setBusy(true); setErr(null);
    try {
      await api.post("/conversions/asset-to-product", { device_id: deviceId, name: name.trim(), sku: sku.trim() || null,
        category: category.trim() || null, cost_price: Number(cost) || 0, sell_price: Number(sell) || 0,
        create_stock_unit: stockUnit, location_id: stockUnit ? (locId || null) : null, notes: note || null });
      onDone();
    } catch (e: any) { setErr(e.message || "Could not convert"); setBusy(false); }
  }
  const fld = "w-full bg-white border border-slate-200 rounded-lg text-sm px-3 py-2";
  const lbl = "block text-[11px] font-semibold text-ink-tertiary   mb-1";
  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      <div className="absolute inset-0 bg-ink-primary/40 backdrop-blur-[2px]" onClick={onClose} />
      <div className="relative w-[520px] max-w-full bg-white h-full shadow-drawer flex flex-col">
        <div className="flex items-center justify-between px-5 py-4 border-b border-slate-200"><h2 className="font-bold text-ink-primary">Convert asset → product</h2><button aria-label="Close" onClick={onClose} className="w-8 h-8 rounded-lg hover:bg-slate-200/60 flex items-center justify-center text-ink-tertiary"><M n="close" c="text-base" /></button></div>
        <div className="flex-1 overflow-y-auto p-5 space-y-4">
          {err && <div className="rounded-lg bg-rose-50 border border-rose-200 text-rose-700 text-xs px-3 py-2">{err}</div>}
          <div><label className={lbl}>Asset to convert</label>
            <select value={deviceId} onChange={(e) => setDeviceId(e.target.value)} className={fld}><option value="">Select {term.asset.toLowerCase()}…</option>{assets.map((a) => <option key={a.id} value={a.id}>{[a.brand, a.model].filter(Boolean).join(" ") || term.asset}{a.serial ? ` · ${a.serial}` : ""}</option>)}</select></div>
          <div className="grid grid-cols-2 gap-3">
            <div><label className={lbl}>Product name</label><input value={name} onChange={(e) => setName(e.target.value)} className={fld} /></div>
            <div><label className={lbl}>SKU (optional)</label><input value={sku} onChange={(e) => setSku(e.target.value)} className={fld} placeholder="Set your own" /></div>
          </div>
          <div className="grid grid-cols-3 gap-3">
            <div><label className={lbl}>Category</label><input value={category} onChange={(e) => setCategory(e.target.value)} className={fld} /></div>
            <div><label className={lbl}>Cost</label><input type="number" min={0} value={cost} onChange={(e) => setCost(Number(e.target.value))} className={`${fld} text-right`} /></div>
            <div><label className={lbl}>Sell price</label><input type="number" min={0} value={sell} onChange={(e) => setSell(Number(e.target.value))} className={`${fld} text-right`} /></div>
          </div>
          <label className="flex items-center gap-2 text-xs font-medium text-ink-secondary"><input type="checkbox" checked={stockUnit} onChange={(e) => setStockUnit(e.target.checked)} className="accent-primary" />Also create a stock unit at a location</label>
          {stockUnit && <div><label className={lbl}>Location</label><select value={locId} onChange={(e) => setLocId(e.target.value)} className={fld}>{locations.map((l) => <option key={l.id} value={l.id}>{l.name}</option>)}</select></div>}
          <div><label className={lbl}>Note</label><textarea value={note} onChange={(e) => setNote(e.target.value)} rows={2} className={fld} /></div>
        </div>
        <div className="px-5 py-4 border-t border-slate-200 flex justify-end gap-2"><button onClick={onClose} className="px-4 py-2 rounded-lg border border-slate-200 text-ink-secondary hover:bg-slate-100 font-semibold text-xs">Cancel</button><button onClick={save} disabled={busy} className="px-4 py-2 rounded-lg bg-primary text-white font-semibold text-xs hover:bg-primary-container disabled:opacity-60">{busy ? "Converting…" : "Convert"}</button></div>
      </div>
    </div>
  );
}

/* ── Reorder suggestions — min-stock list + velocity forecast → one-click draft POs ── */
function ReorderModal({ onClose, onCreated }: { onClose: () => void; onCreated?: () => void }) {
  const [mode, setMode] = useState<"forecast" | "min">("forecast");
  const [items, setItems] = useState<any[] | null>(null);       // min-stock list
  const [fc, setFc] = useState<any[] | null>(null);              // velocity forecast
  const [sel, setSel] = useState<Record<string, boolean>>({});
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  useEffect(() => { api.get<any>("/inventory/reorder-list").then((r) => setItems(r.items || [])).catch(() => setItems([])); }, []);
  useEffect(() => {
    api.get<any>("/inventory/reorder-forecast").then((r) => {
      const its = r.items || []; setFc(its);
      setSel(Object.fromEntries(its.map((i: any) => [i.part_id, true])));
    }).catch(() => setFc([]));
  }, []);
  const chosen = (fc || []).filter((i) => sel[i.part_id]);
  const noSupplier = chosen.filter((i) => !i.supplier_id).length;
  async function createPOs() {
    setBusy(true); setMsg("");
    try {
      const r = await api.post<any>("/inventory/reorder-forecast/create-pos", {
        items: chosen.map((i) => ({ part_id: i.part_id, qty: i.suggest_qty })),
      });
      const skipped = (r.skipped_no_supplier || []).length;
      setMsg(`Created ${r.po_count} draft PO${r.po_count === 1 ? "" : "s"}${skipped ? ` · ${skipped} skipped (no supplier)` : ""}.`);
      setTimeout(() => onCreated?.(), 900);
    } catch (e: any) { setMsg(e.message || "Could not create POs"); } finally { setBusy(false); }
  }
  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      <div className="absolute inset-0 bg-ink-primary/40 backdrop-blur-[2px]" onClick={onClose} />
      <div className="relative w-[560px] max-w-full bg-white h-full shadow-drawer flex flex-col">
        <div className="flex items-center justify-between px-5 py-4 border-b border-slate-200"><h2 className="font-bold text-ink-primary">Reorder</h2><button aria-label="Close" onClick={onClose} className="w-8 h-8 rounded-lg hover:bg-slate-200/60 flex items-center justify-center text-ink-tertiary"><M n="close" c="text-base" /></button></div>
        <div className="px-5 pt-3 flex gap-1.5">
          {(["forecast", "min"] as const).map((m) => (
            <button key={m} onClick={() => setMode(m)} className={`px-3 py-1 rounded-lg text-xs ${mode === m ? "bg-blue-50 text-primary font-bold" : "text-ink-secondary hover:text-ink-primary font-semibold"}`}>{m === "forecast" ? "Velocity forecast" : "Min-stock list"}</button>
          ))}
        </div>
        <div className="flex-1 overflow-y-auto p-5">
          {mode === "min" ? (
            <>
              <p className="text-xs text-ink-tertiary mb-3">Products at or below their minimum stock. Suggested qty brings each back to 2× min.</p>
              <table className="w-full text-xs">
                <thead className="text-ink-tertiary text-[11px] font-bold"><tr><th className="text-left py-2">Product</th><th className="text-right py-2">Available</th><th className="text-right py-2">Min</th><th className="text-right py-2">Suggested</th></tr></thead>
                <tbody className="divide-y divide-slate-100">
                  {items == null && <SkeletonRows cols={4} />}
                  {items?.length === 0 && <tr><td colSpan={4} className="py-6 text-center text-ink-tertiary">Nothing to reorder — all above minimum.</td></tr>}
                  {items?.map((p) => (
                    <tr key={p.id}><td className="py-2 font-semibold text-ink-primary">{p.name}{p.sku ? ` · ${p.sku}` : ""}</td><td className="py-2 text-right">{p.available}</td><td className="py-2 text-right text-ink-tertiary">{p.min_stock}</td><td className="py-2 text-right font-bold text-primary">{p.suggested_qty}</td></tr>
                  ))}
                </tbody>
              </table>
            </>
          ) : (
            <>
              <p className="text-xs text-ink-tertiary mb-3">Based on 28-day sales velocity. Suggested qty covers each supplier's lead time plus a 14-day buffer, netting off what's already on order. Check the ones to buy, then create draft POs grouped by supplier.</p>
              <table className="w-full text-xs">
                <thead className="text-ink-tertiary text-[11px] font-bold"><tr><th className="py-2 w-6"></th><th className="text-left py-2">Product</th><th className="text-right py-2">Free</th><th className="text-right py-2">Days left</th><th className="text-right py-2">Order</th><th className="text-left py-2 pl-2">Supplier</th></tr></thead>
                <tbody className="divide-y divide-slate-100">
                  {fc == null && <SkeletonRows cols={6} />}
                  {fc?.length === 0 && <tr><td colSpan={6} className="py-6 text-center text-ink-tertiary">Nothing forecast to run out — no moving parts below cover.</td></tr>}
                  {fc?.map((p) => (
                    <tr key={p.part_id}>
                      <td className="py-2"><input type="checkbox" aria-label="Select row" checked={!!sel[p.part_id]} onChange={(e) => setSel((s) => ({ ...s, [p.part_id]: e.target.checked }))} className="w-4 h-4 rounded border-slate-300 text-primary" /></td>
                      <td className="py-2 font-semibold text-ink-primary">{p.name}{p.sku ? ` · ${p.sku}` : ""}</td>
                      <td className="py-2 text-right">{p.free}</td>
                      <td className={`py-2 text-right font-semibold ${p.days_left != null && p.days_left <= 7 ? "text-rose-600" : "text-ink-secondary"}`}>{p.days_left != null ? `${p.days_left}d` : "—"}</td>
                      <td className="py-2 text-right font-bold text-primary">{p.suggest_qty}</td>
                      <td className={`py-2 pl-2 ${p.supplier_id ? "text-ink-secondary" : "text-amber-600"}`}>{p.supplier_name || "no supplier"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          )}
        </div>
        <div className="px-5 py-4 border-t border-slate-200 flex items-center justify-between gap-3">
          <span className="text-[11px] text-ink-tertiary">{msg || (mode === "forecast" && noSupplier ? `${noSupplier} selected have no default supplier — they'll be skipped.` : "")}</span>
          <div className="flex gap-2">
            <button onClick={onClose} className="px-4 py-2 rounded-lg border border-slate-200 text-ink-secondary text-xs font-semibold">Close</button>
            {mode === "forecast" && <button onClick={createPOs} disabled={busy || chosen.length === 0} className="px-4 py-2 rounded-lg bg-primary text-white text-xs font-bold hover:bg-primary-container disabled:opacity-50">{busy ? "Creating…" : `Create draft POs (${chosen.filter((i) => i.supplier_id).length})`}</button>}
          </div>
        </div>
      </div>
    </div>
  );
}

/* ── New posting — bulk stock inflow → POST /inventory/postings ── */
type PType = "standard" | "serialized" | "expiry";
type ReviewRow = PostRow & {
  match_status: "matched" | "unsure" | "new"; part_id: string | null; part_name?: string | null; sku?: string | null;
  candidates?: { id: string; name: string; sku?: string; serialized?: boolean; expiry_tracking?: boolean }[]; chosen: string;
  serialized?: boolean; expiry_tracking?: boolean; ptype: PType;          // 16a: type for a new product
  serials: string[]; serialState?: { blocked: number; checking: boolean }; expiry_date?: string;
};
// Is this review row serial-tracked, given what the user picked? (a match keeps its own type)
const rowSerialized = (r: ReviewRow) => !r.chosen || r.chosen === "__new__" ? r.ptype === "serialized"
  : r.chosen === r.part_id ? !!r.serialized : !!r.candidates?.find((c) => c.id === r.chosen)?.serialized;
const rowExpiry = (r: ReviewRow) => !r.chosen || r.chosen === "__new__" ? r.ptype === "expiry"
  : r.chosen === r.part_id ? !!r.expiry_tracking : !!r.candidates?.find((c) => c.id === r.chosen)?.expiry_tracking;
const unitsOf = (r: ReviewRow) => Math.ceil(Number(r.qty) || 0);
function PostingModal({ onClose, onDone }: { products: Product[]; onClose: () => void; onDone: () => void }) {
  const [locations, setLocations] = useState<{ id: string; name: string }[]>([]);
  const [suppliers, setSuppliers] = useState<{ id: string; name: string }[]>([]);
  const [locId, setLocId] = useState(""); const [supplierId, setSupplierId] = useState(""); const [note, setNote] = useState("");
  const blank = (): PostRow => ({ code: "", mpn: "", name: "", qty: 1, purchase_price: 0, markup_pct: 0 });
  const [rows, setRows] = useState<PostRow[]>([blank()]);
  const [review, setReview] = useState<ReviewRow[] | null>(null);
  const [kept, setKept] = useState<Record<string, { serials: string[]; ptype: PType; expiry_date?: string }>>({});
  const rowKey = (r: { code?: string | null; mpn?: string | null; name?: string | null }) => [r.code || "", r.mpn || "", r.name || ""].join("|").toLowerCase();
  const backToEdit = () => {
    if (review) setKept(Object.fromEntries(review.map((r) => [rowKey(r), { serials: r.serials, ptype: r.ptype, expiry_date: r.expiry_date }])));
    setReview(null);
  };
  const [pasteOpen, setPasteOpen] = useState(false); const [pasteText, setPasteText] = useState("");
  const [busy, setBusy] = useState(false); const [err, setErr] = useState<string | null>(null); const [info, setInfo] = useState<string | null>(null);
  const [mapCols, setMapCols] = useState<PasteField[] | null>(null); const [dataGrid, setDataGrid] = useState<string[][] | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  useEffect(() => {
    api.get<any>("/staff/locations").then((r) => { const it = r.items || r || []; setLocations(it); if (it[0]) setLocId(it[0].id); }).catch(() => {});
    api.get<any>("/suppliers").then((r) => setSuppliers(r.items || [])).catch(() => {});
  }, []);
  const fld = "bg-white border border-slate-200 rounded-lg text-sm px-2.5 py-2";
  const lbl2 = "block text-[11px] font-bold text-ink-tertiary  mb-1";
  const setRow = (i: number, k: keyof PostRow, v: any) => setRows(rows.map((r, j) => j === i ? { ...r, [k]: v } : r));

  function applyPaste() {
    const { grid, map, hadHeader } = analyzePaste(pasteText, loadRememberedMap());
    const parsed = gridToRows(grid, map);
    if (!parsed.length) { setErr("Couldn't read any rows from that paste."); return; }
    setErr(null);
    setRows(parsed); setDataGrid(grid); setMapCols(map); setPasteOpen(false); setPasteText("");
    setInfo(hadHeader ? "Detected a header row and mapped columns automatically — check the mapping below." : "Mapped columns automatically — check the mapping below if a column looks wrong.");
  }
  const clearMap = () => { setMapCols(null); setDataGrid(null); };
  function remap(col: number, field: PasteField) {
    if (!mapCols || !dataGrid) return;
    // a field can only map to one column: clear it elsewhere first
    const next = mapCols.map((f, c) => (field !== "ignore" && f === field && c !== col ? "ignore" : f)) as PasteField[];
    next[col] = field;
    // mergeRemap keeps any cell the user hand-edited while the mapping bar is open
    setMapCols(next); setRows(mergeRemap(dataGrid, mapCols, next, rows)); saveRememberedMap(next);
  }
  async function onFile(e: any) {
    const f = e.target.files?.[0]; if (!f) return;
    setBusy(true); setErr(null); setInfo(null);
    try {
      const fd = new FormData(); fd.append("file", f);
      const r = await api.postForm<any>("/inventory/postings/extract", fd);
      const rws: PostRow[] = (r.rows || []).map((x: any) => ({ code: x.code || "", mpn: x.mpn || "", name: x.name || "", qty: Number(x.qty) || 1, purchase_price: Number(x.purchase_price) || 0, markup_pct: 0 }));
      if (rws.length) { setRows(rws); setMapCols(null); setDataGrid(null); setInfo(`Read ${rws.length} line${rws.length === 1 ? "" : "s"} from the file — check each before posting.`); }
      else setInfo(r.note || "Nothing detected in that file. Paste or type the rows instead.");
    } catch (e: any) { setErr(e.message || "Could not read file"); }
    finally { setBusy(false); if (fileRef.current) fileRef.current.value = ""; }
  }
  async function doPreview() {
    const clean = rows.filter((r) => (r.code.trim() || r.mpn.trim() || r.name.trim()) && r.qty > 0);
    if (!clean.length) { setErr("Add at least one row (code, MPN or name + qty)."); return; }
    setBusy(true); setErr(null);
    try {
      const r = await api.post<any>("/inventory/postings/preview", { rows: clean.map((x) => ({ code: x.code || null, mpn: x.mpn || null, name: x.name || null, qty: Number(x.qty), purchase_price: Number(x.purchase_price) || null, markup_pct: x.markup_pct ? Number(x.markup_pct) : null, product_type: x.product_type || null })) });
      // Back → edit → review again keeps what was scanned and chosen for the same row.
      setReview((r.rows || []).map((rr: any) => {
        const k = kept[rowKey(rr)];
        return { ...rr, ptype: (k?.ptype || rr.product_type || "standard") as PType, serials: k?.serials?.length ? k.serials : (rr.serials || []),
          expiry_date: k?.expiry_date, chosen: rr.match_status === "matched" ? rr.part_id : (rr.candidates?.[0]?.id || "__new__") };
      }));
    } catch (e: any) { setErr(e.message || "Could not match rows"); } finally { setBusy(false); }
  }
  const extras = (r: ReviewRow) => ({
    ...(rowSerialized(r) ? { serials: r.serials } : {}),
    ...(rowExpiry(r) && r.expiry_date ? { expiry_date: r.expiry_date } : {}),
  });
  const setReviewRow = (i: number, patch: Partial<ReviewRow>) => setReview((rv) => rv && rv.map((x, j) => j === i ? { ...x, ...patch } : x));
  // 16b: serial-tracked rows need one good serial per unit before stock can move.
  const needSerials = (review || []).filter((r) => rowSerialized(r) && (r.serials.length !== unitsOf(r) || unitsOf(r) !== Number(r.qty)));
  const badSerials = (review || []).filter((r) => rowSerialized(r) && (r.serialState?.blocked || r.serialState?.checking));
  async function doCommit() {
    if (!review) return;
    if (needSerials.length) { setErr(`${needSerials.length} row${needSerials.length === 1 ? " still needs" : "s still need"} a serial for each unit.`); return; }
    if (badSerials.length) { setErr("Fix the serials marked in red first."); return; }
    setBusy(true); setErr(null);
    try {
      const body = {
        location_id: locId, supplier_id: supplierId || null, note: note || null,
        rows: review.map((r) => r.chosen && r.chosen !== "__new__"
          ? { part_id: r.chosen, qty: Number(r.qty), purchase_price: Number(r.purchase_price) || null, markup_pct: r.markup_pct ? Number(r.markup_pct) : null, ...extras(r) }
          : { new_name: (r.name || r.code || r.mpn || "").trim(), new_sku: r.code || null, new_mpn: r.mpn || null, new_type: r.ptype, qty: Number(r.qty), purchase_price: Number(r.purchase_price) || null, markup_pct: r.markup_pct ? Number(r.markup_pct) : null, ...extras(r) }),
      };
      await api.post("/inventory/postings/commit", body);
      onDone();
    } catch (e: any) { setErr(e.message || "Could not post"); setBusy(false); }
  }
  const stBadge = (s: string) => s === "matched" ? "bg-emerald-50 text-emerald-700" : s === "unsure" ? "bg-amber-50 text-amber-700" : "bg-blue-50 text-primary";
  const newCount = review?.filter((r) => !r.chosen || r.chosen === "__new__").length || 0;

  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      <div className="absolute inset-0 bg-ink-primary/40 backdrop-blur-[2px]" onClick={onClose} />
      <div className="relative w-[680px] max-w-full bg-white h-full shadow-drawer flex flex-col">
        <div className="flex items-center justify-between px-5 py-4 border-b border-slate-200">
          <h2 className="font-bold text-ink-primary">{review ? "Review & confirm posting" : "New posting — bulk stock inflow"}</h2>
          <button aria-label="Close" onClick={onClose} className="w-8 h-8 rounded-lg hover:bg-slate-200/60 flex items-center justify-center text-ink-tertiary"><M n="close" c="text-base" /></button>
        </div>
        <div className="flex-1 overflow-y-auto p-5 space-y-4">
          {err && <div className="rounded-lg bg-rose-50 border border-rose-200 text-rose-700 text-xs px-3 py-2">{err}</div>}
          {info && <div className="rounded-lg bg-blue-50 border border-blue-200 text-primary text-xs px-3 py-2">{info}</div>}
          <div className="grid grid-cols-2 gap-3">
            <div><label className={lbl2}>Warehouse</label><select value={locId} onChange={(e) => setLocId(e.target.value)} className={`${fld} w-full`}>{locations.map((l) => <option key={l.id} value={l.id}>{l.name}</option>)}</select></div>
            <div><label className={lbl2}>Supplier (optional)</label><select value={supplierId} onChange={(e) => setSupplierId(e.target.value)} className={`${fld} w-full`}><option value="">—</option>{suppliers.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}</select></div>
          </div>

          {!review ? (<>
            <input ref={fileRef} type="file" accept="image/*,application/pdf" className="hidden" onChange={onFile} />
            <div className="flex items-center justify-between flex-wrap gap-2">
              <label className={lbl2} style={{ marginBottom: 0 }}>Rows — Code/SKU · MPN · Name · Qty · Cost · Markup%</label>
              <div className="flex gap-2">
                <button onClick={() => fileRef.current?.click()} disabled={busy} className="text-primary text-xs font-semibold flex items-center gap-1 disabled:opacity-50"><M n="document_scanner" c="text-sm" />Scan invoice / photo</button>
                <button onClick={() => setPasteOpen((o) => !o)} className="text-primary text-xs font-semibold flex items-center gap-1"><M n="content_paste" c="text-sm" />Paste from Excel</button>
                <button onClick={() => { setRows([...rows, blank()]); clearMap(); }} className="text-primary text-xs font-semibold">+ Add row</button>
              </div>
            </div>
            {pasteOpen && (
              <div className="rounded-lg border border-slate-200 bg-slate-50/60 p-3">
                <p className="text-[11px] text-ink-tertiary mb-2">Paste tab- or comma-separated rows — with or without a header row. Columns are detected automatically and you can fix the mapping after.</p>
                <textarea value={pasteText} onChange={(e) => setPasteText(e.target.value)} rows={4} placeholder={"SCR-IP13\tiPhone 13 Screen\t10\t60\t40\nBAT-GS22\t\t5\t14\t50"} className={`${fld} w-full tabular-nums text-xs`} />
                <div className="flex justify-end mt-2"><button onClick={applyPaste} className="px-3 py-1.5 rounded-lg bg-primary text-white text-xs font-semibold">Fill {pasteText.split(/\r?\n/).filter((x) => x.trim()).length || 0} rows</button></div>
              </div>
            )}
            {mapCols && dataGrid && (
              <div className="rounded-lg border border-blue-200 bg-blue-50/50 p-3">
                <p className="text-[11px] font-semibold text-primary mb-2 flex items-center gap-1"><M n="tune" c="text-sm" />Column mapping — fix any that's wrong</p>
                <div className="flex gap-2 overflow-x-auto pb-1">
                  {mapCols.map((f, c) => (
                    <div key={c} className="shrink-0">
                      <select value={f} onChange={(e) => remap(c, e.target.value as PasteField)} className={`${fld} w-32`}>
                        {FIELD_ORDER.map((opt) => <option key={opt} value={opt}>{FIELD_LABELS[opt]}</option>)}
                      </select>
                      <div className="text-[11px] text-ink-tertiary mt-1 px-1 truncate w-32" title={dataGrid[0]?.[c] || ""}>{dataGrid[0]?.[c] || <span className="opacity-40">—</span>}</div>
                    </div>
                  ))}
                </div>
                <p className="text-[11px] text-ink-tertiary mt-1.5">Remembered for next time.</p>
              </div>
            )}
            <div className="space-y-1.5">
              <div className="flex items-center gap-1.5 text-[11px] font-bold text-ink-tertiary px-1"><span className="w-24">Code/SKU</span><span className="w-24">MPN</span><span className="flex-1">Name</span><span className="w-12 text-right">Qty</span><span className="w-16 text-right">Cost</span><span className="w-12 text-right">Mk%</span><span className="w-8" /></div>
              {rows.map((r, i) => (
                <div key={i} className="flex items-center gap-1.5">
                  <input value={r.code} onChange={(e) => setRow(i, "code", e.target.value)} placeholder="SKU" aria-label="Code or SKU" className={`${fld} w-24 min-w-0`} />
                  <input value={r.mpn} onChange={(e) => setRow(i, "mpn", e.target.value)} placeholder="MPN" aria-label="MPN" className={`${fld} w-24 min-w-0`} />
                  <input value={r.name} onChange={(e) => setRow(i, "name", e.target.value)} placeholder="Product name (if new)" className={`${fld} flex-1`} />
                  <input type="number" min={0} value={r.qty} onChange={(e) => setRow(i, "qty", Number(e.target.value))} className={`${fld} w-12 text-right`} />
                  <input type="number" min={0} value={r.purchase_price} onChange={(e) => setRow(i, "purchase_price", Number(e.target.value))} className={`${fld} w-16 text-right`} />
                  <input type="number" min={0} value={r.markup_pct} onChange={(e) => setRow(i, "markup_pct", Number(e.target.value))} className={`${fld} w-12 text-right`} />
                  <button aria-label="Delete" onClick={() => { setRows(rows.filter((_, j) => j !== i)); clearMap(); }} className="w-8 h-8 rounded-lg hover:bg-rose-50 text-ink-tertiary hover:text-rose-600 flex items-center justify-center"><M n="delete" c="text-sm" /></button>
                </div>
              ))}
            </div>
            <div><label className={lbl2}>Note / reference</label><textarea value={note} onChange={(e) => setNote(e.target.value)} rows={2} className={`${fld} w-full`} /></div>
          </>) : (<>
            <p className="text-xs text-ink-secondary">Confirm each row before stock moves. {newCount > 0 && <b className="text-primary">{newCount} new product{newCount === 1 ? "" : "s"} will be created.</b>}</p>
            {needSerials.length > 0 && (
              <div className="rounded-lg bg-amber-50 border border-amber-200 text-amber-800 text-xs px-3 py-2 flex items-center gap-1.5">
                <M n="barcode_scanner" c="text-sm" />{needSerials.length} row{needSerials.length === 1 ? " still needs" : "s still need"} a serial for each unit. Scan them below.
              </div>
            )}
            <div className="space-y-1.5">
              {review.map((r, i) => {
                const ser = rowSerialized(r), exp = rowExpiry(r), isNew = !r.chosen || r.chosen === "__new__";
                const units = unitsOf(r), missing = ser ? Math.max(0, units - r.serials.length) : 0;
                return (
                <div key={i} className="border border-slate-100 rounded-lg px-2.5 py-2 space-y-2">
                  <div className="flex items-center gap-2">
                    <span className={`text-[11px] font-bold  px-1.5 py-0.5 rounded ${stBadge(isNew ? "new" : "matched")}`}>{isNew ? "new" : "match"}</span>
                    <div className="flex-1 min-w-0">
                      <div className="text-xs font-semibold text-ink-primary truncate">{r.part_name || r.name || r.code || r.mpn || "—"}</div>
                      {(r.candidates && r.candidates.length > 0) ? (
                        <select value={r.chosen} onChange={(e) => setReviewRow(i, { chosen: e.target.value })} className="mt-1 bg-white border border-slate-200 rounded text-[11px] px-1.5 py-1 w-full">
                          {r.candidates.map((c) => <option key={c.id} value={c.id}>{c.name}{c.sku ? ` (${c.sku})` : ""}</option>)}
                          <option value="__new__">+ Create new: {r.name || r.code}</option>
                        </select>
                      ) : (
                        <div className="text-[11px] text-ink-tertiary">{r.part_id ? `SKU ${r.sku || "—"}` : `New product${r.code ? ` · SKU ${r.code}` : ""}${r.mpn ? ` · MPN ${r.mpn}` : ""}`}</div>
                      )}
                    </div>
                    {isNew ? (
                      <select value={r.ptype} onChange={(e) => setReviewRow(i, { ptype: e.target.value as PType })} aria-label="Product type" className="bg-white border border-slate-200 rounded text-[11px] px-1.5 py-1">
                        <option value="standard">Standard</option><option value="serialized">Serial-tracked</option><option value="expiry">Expiry-tracked</option>
                      </select>
                    ) : (ser || exp) && <span className="text-[11px] font-semibold px-1.5 py-0.5 rounded bg-slate-100 text-ink-secondary">{ser ? "Serial-tracked" : "Expiry-tracked"}</span>}
                    <span className="text-[11px] text-ink-tertiary">×{r.qty}</span>
                    <span className="text-[11px] tabular-nums text-ink-secondary w-14 text-right">{money(r.purchase_price || 0)}</span>
                  </div>
                  {ser && (
                    <div className="ml-1 pl-2 border-l-2 border-primary/20 space-y-1">
                      <div className="flex items-center justify-between text-[11px]">
                        <span className="font-semibold text-ink-secondary">Serials / IMEIs</span>
                        {missing > 0 ? <span className="text-amber-700 font-semibold">Needs {missing} more</span>
                          : units !== Number(r.qty) ? <span className="text-rose-700 font-semibold">Quantity must be a whole number</span>
                          : <span className="text-emerald-700 font-semibold flex items-center gap-0.5"><M n="check" c="text-[13px]" />All units entered</span>}
                      </div>
                      <SerialEntry value={r.serials} target={units} partId={isNew ? null : r.chosen}
                        others={review.flatMap((x, j) => j !== i && rowSerialized(x) ? x.serials : [])}
                        onChange={(v) => setReviewRow(i, { serials: v })}
                        onState={(st) => setReviewRow(i, { serialState: { blocked: st.blocked, checking: st.checking } })} />
                    </div>
                  )}
                  {exp && (
                    <div className="ml-1 pl-2 border-l-2 border-primary/20 flex items-center gap-2 text-[11px] text-ink-secondary">
                      <span className="whitespace-nowrap">Use by (optional)</span>
                      <div className="w-44"><DateField value={r.expiry_date || ""} onChange={(v) => setReviewRow(i, { expiry_date: v })} /></div>
                    </div>
                  )}
                </div>
              ); })}
            </div>
          </>)}
        </div>
        <div className="px-5 py-4 border-t border-slate-200 flex justify-between gap-2">
          <button onClick={review ? backToEdit : onClose} className="px-4 py-2 rounded-lg border border-slate-200 text-ink-secondary text-xs font-semibold">{review ? "Back" : "Cancel"}</button>
          {!review
            ? <button onClick={doPreview} disabled={busy} className="px-4 py-2 rounded-lg bg-primary text-white text-xs font-semibold disabled:opacity-60">{busy ? "Matching…" : "Review & match →"}</button>
            : <button onClick={doCommit} disabled={busy || needSerials.length > 0 || badSerials.length > 0} title={needSerials.length ? "Enter a serial for each unit first" : undefined} className="px-4 py-2 rounded-lg bg-primary text-white text-xs font-semibold disabled:opacity-60">{busy ? "Posting…" : "Post stock"}</button>}
        </div>
      </div>
    </div>
  );
}

/* ── Bulk quantity adjust across selected parts (ShopMonkey-style) ─────────── */
function BulkAdjustModal({ items, onClose, onDone }: { items: Product[]; onClose: () => void; onDone: () => void }) {
  const [locations, setLocations] = useState<{ id: string; name: string }[]>([]);
  const [locId, setLocId] = useState("");
  const [mode, setMode] = useState<"set" | "delta">("set");
  const [vals, setVals] = useState<Record<string, number>>({});
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false); const [err, setErr] = useState<string | null>(null);
  useEffect(() => { api.get<any>("/staff/locations").then((r) => { const it = r.items || r || []; setLocations(it); if (it[0]) setLocId(it[0].id); }).catch(() => {}); }, []);
  const fld = "bg-white border border-slate-200 rounded-lg text-sm px-2.5 py-2";
  const lbl2 = "block text-[11px] font-bold text-ink-tertiary  mb-1";
  async function save() {
    const rows = items.map((p) => ({ part_id: p.id, ...(mode === "set" ? { set_qty: vals[p.id] ?? (p.qty_available ?? 0) } : { delta: vals[p.id] || 0 }), note: note || null }))
      .filter((r) => mode === "set" ? true : (r as any).delta !== 0);
    if (!rows.length) { setErr("Nothing to adjust."); return; }
    setBusy(true); setErr(null);
    try { await api.post("/inventory/parts/bulk-adjust", { location_id: locId, rows }); onDone(); }
    catch (e: any) { setErr(e.message || "Could not adjust"); setBusy(false); }
  }
  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      <div className="absolute inset-0 bg-ink-primary/40 backdrop-blur-[2px]" onClick={onClose} />
      <div className="relative w-[520px] max-w-full bg-white h-full shadow-drawer flex flex-col">
        <div className="flex items-center justify-between px-5 py-4 border-b border-slate-200"><h2 className="font-bold text-ink-primary">Adjust quantity — {items.length} item{items.length === 1 ? "" : "s"}</h2><button aria-label="Close" onClick={onClose} className="w-8 h-8 rounded-lg hover:bg-slate-200/60 flex items-center justify-center text-ink-tertiary"><M n="close" c="text-base" /></button></div>
        <div className="flex-1 overflow-y-auto p-5 space-y-4">
          {err && <div className="rounded-lg bg-rose-50 border border-rose-200 text-rose-700 text-xs px-3 py-2">{err}</div>}
          <div className="grid grid-cols-2 gap-3">
            <div><label className={lbl2}>Warehouse</label><select value={locId} onChange={(e) => setLocId(e.target.value)} className={`${fld} w-full`}>{locations.map((l) => <option key={l.id} value={l.id}>{l.name}</option>)}</select></div>
            <div><label className={lbl2}>Mode</label>
              <div className="flex gap-1.5">
                <button onClick={() => setMode("set")} className={`flex-1 rounded-lg border px-2 py-2 text-xs font-semibold ${mode === "set" ? "border-primary bg-primary/5 text-primary" : "border-slate-200 text-ink-secondary"}`}>Set to</button>
                <button onClick={() => setMode("delta")} className={`flex-1 rounded-lg border px-2 py-2 text-xs font-semibold ${mode === "delta" ? "border-primary bg-primary/5 text-primary" : "border-slate-200 text-ink-secondary"}`}>Add / subtract</button>
              </div>
            </div>
          </div>
          <div className="space-y-1.5">
            <div className="flex items-center gap-2 text-[11px] font-bold text-ink-tertiary px-1"><span className="flex-1">Product</span><span className="w-16 text-right">On hand</span><span className="w-20 text-right">{mode === "set" ? "New qty" : "± Delta"}</span></div>
            {items.map((p) => (
              <div key={p.id} className="flex items-center gap-2">
                <span className="flex-1 text-xs font-semibold text-ink-primary truncate">{p.name}{p.sku ? <span className="text-ink-tertiary font-normal"> · {p.sku}</span> : null}</span>
                <span className="w-16 text-right text-xs tabular-nums text-ink-tertiary">{p.qty_available ?? 0}</span>
                <input type="number" value={vals[p.id] ?? (mode === "set" ? (p.qty_available ?? 0) : 0)} onChange={(e) => setVals({ ...vals, [p.id]: Number(e.target.value) })} className={`${fld} w-20 text-right`} />
              </div>
            ))}
          </div>
          <div><label className={lbl2}>Reason / note</label><input value={note} onChange={(e) => setNote(e.target.value)} placeholder="Recount, correction…" className={`${fld} w-full`} /></div>
        </div>
        <div className="px-5 py-4 border-t border-slate-200 flex justify-end gap-2"><button onClick={onClose} className="px-4 py-2 rounded-lg border border-slate-200 text-ink-secondary text-xs font-semibold">Cancel</button><button onClick={save} disabled={busy} className="px-4 py-2 rounded-lg bg-primary text-white text-xs font-semibold disabled:opacity-60">{busy ? "Adjusting…" : "Apply adjustments"}</button></div>
      </div>
    </div>
  );
}

/* ── Parts to order: what open jobs need, netted vs stock + on-order, grouped by supplier ── */
type ToOrderRow = { part_id: string; name: string; sku?: string; needed: number; on_hand: number; on_order: number; shortfall: number; unit_cost: number; supplier_id: string | null; supplier_name: string; jobs: string[]; stalled_days: number };
function PartsToOrderModal({ onClose, onCreated }: { onClose: () => void; onCreated: () => void }) {
  const [rows, setRows] = useState<ToOrderRow[] | null>(null);
  const [summary, setSummary] = useState<{ parts: number; jobs_waiting: number; shortfall_units: number } | null>(null);
  const [locId, setLocId] = useState(""); const [locations, setLocations] = useState<{ id: string; name: string }[]>([]);
  const [busy, setBusy] = useState(""); const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    api.get<any>("/inventory/to-order").then((r) => { setRows(r.items || []); setSummary(r.summary || null); }).catch(() => setRows([]));
    api.get<any>("/staff/locations").then((r) => { const it = r.items || r || []; setLocations(it); if (it[0]) setLocId(it[0].id); }).catch(() => {});
  }, []);
  // group by supplier
  const groups: Record<string, { name: string; sid: string | null; rows: ToOrderRow[] }> = {};
  (rows || []).forEach((r) => { const k = r.supplier_id || "__none__"; (groups[k] ||= { name: r.supplier_name, sid: r.supplier_id, rows: [] }).rows.push(r); });
  async function createPO(g: { name: string; sid: string | null; rows: ToOrderRow[] }) {
    if (!g.sid) { setErr(`"${g.name}" parts have no default supplier — set one on the product first.`); return; }
    if (!locId) { setErr("Pick a warehouse."); return; }
    setBusy(g.sid); setErr(null);
    try {
      await api.post("/inventory/po", { supplier_id: g.sid, location_id: locId, draft: true,
        lines: g.rows.map((r) => ({ part_id: r.part_id, qty_ordered: Math.ceil(r.shortfall), unit_cost: r.unit_cost || 0 })) });
      onCreated();
    } catch (e: any) { setErr(e.message || "Could not create PO"); setBusy(""); }
  }
  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      <div className="absolute inset-0 bg-ink-primary/40 backdrop-blur-[2px]" onClick={onClose} />
      <div className="relative w-[620px] max-w-full bg-white h-full shadow-drawer flex flex-col">
        <div className="flex items-center justify-between px-5 py-4 border-b border-slate-200">
          <div><h2 className="font-bold text-ink-primary">Parts to order</h2><p className="text-[11px] text-ink-tertiary">What open jobs need, minus stock on hand and already on order.</p></div>
          <button aria-label="Close" onClick={onClose} className="w-8 h-8 rounded-lg hover:bg-slate-200/60 flex items-center justify-center text-ink-tertiary"><M n="close" c="text-base" /></button>
        </div>
        <div className="flex-1 overflow-y-auto p-5 space-y-4">
          {err && <div className="rounded-lg bg-rose-50 border border-rose-200 text-rose-700 text-xs px-3 py-2">{err}</div>}
          {summary && (rows?.length ?? 0) > 0 && (
            <div className="flex items-center gap-4 text-xs">
              <span className="font-semibold text-ink-primary">{summary.parts} part{summary.parts === 1 ? "" : "s"} short</span>
              <span className="text-ink-tertiary">{summary.jobs_waiting} job{summary.jobs_waiting === 1 ? "" : "s"} waiting</span>
              <div className="ml-auto flex items-center gap-2"><span className="text-[11px] text-ink-tertiary">Order into</span>
                <select value={locId} onChange={(e) => setLocId(e.target.value)} className="bg-white border border-slate-200 rounded-lg text-xs px-2 py-1.5">{locations.map((l) => <option key={l.id} value={l.id}>{l.name}</option>)}</select></div>
            </div>
          )}
          {rows == null && <p className="text-xs text-ink-tertiary">Loading…</p>}
          {rows?.length === 0 && <div className="text-center py-10 text-ink-tertiary text-sm"><M n="check_circle" c="text-3xl text-emerald-500 block mb-2" />Nothing to order — every open job's parts are covered.</div>}
          {Object.entries(groups).map(([k, g]) => (
            <div key={k} className="rounded-xl border border-slate-200 overflow-hidden">
              <div className="flex items-center justify-between px-3 py-2 bg-slate-50/70 border-b border-slate-100">
                <div className="text-xs font-bold text-ink-primary flex items-center gap-1.5"><M n="local_shipping" c="text-sm text-ink-tertiary" />{g.name}<span className="text-ink-tertiary font-normal">· {g.rows.length} line{g.rows.length === 1 ? "" : "s"}</span></div>
                <button onClick={() => createPO(g)} disabled={!!busy} className="h-[28px] px-3 rounded-lg bg-primary text-white text-[11px] font-bold hover:bg-primary-container disabled:opacity-60 flex items-center gap-1"><M n="receipt_long" c="text-[13px]" />{busy === g.sid ? "Creating…" : "Create PO"}</button>
              </div>
              <table className="w-full text-left text-xs">
                <thead className="text-[11px] font-bold text-ink-tertiary "><tr><th className="py-1.5 px-3">Part</th><th className="py-1.5 px-2 text-right">Need</th><th className="py-1.5 px-2 text-right">Have</th><th className="py-1.5 px-2 text-right">On order</th><th className="py-1.5 px-2 text-right">Order</th><th className="py-1.5 px-3">Jobs</th></tr></thead>
                <tbody className="divide-y divide-slate-100">
                  {g.rows.map((r) => (
                    <tr key={r.part_id}>
                      <td className="py-2 px-3"><div className="font-semibold text-ink-primary">{r.name}</div><div className="text-[11px] text-ink-tertiary">{r.sku || ""}{r.stalled_days > 0 ? <span className="text-amber-600 font-semibold"> · stalled {r.stalled_days}d</span> : null}</div></td>
                      <td className="py-2 px-2 text-right tabular-nums">{r.needed}</td>
                      <td className="py-2 px-2 text-right tabular-nums text-ink-tertiary">{r.on_hand}</td>
                      <td className="py-2 px-2 text-right tabular-nums text-ink-tertiary">{r.on_order || "—"}</td>
                      <td className="py-2 px-2 text-right tabular-nums font-bold text-primary">{Math.ceil(r.shortfall)}</td>
                      <td className="py-2 px-3 text-[11px] text-ink-tertiary">{r.jobs.slice(0, 3).join(", ")}{r.jobs.length > 3 ? ` +${r.jobs.length - 3}` : ""}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ))}
        </div>
        <div className="px-5 py-4 border-t border-slate-200 flex justify-end"><button onClick={onClose} className="px-4 py-2 rounded-lg border border-slate-200 text-ink-secondary text-xs font-semibold">Close</button></div>
      </div>
    </div>
  );
}

/* ── Bins panel (revealed by Settings → Inventory → enable_bins) → /api/bins ── */
function BinsPanel() {
  const [bins, setBins] = useState<any[] | null>(null);
  const [locations, setLocations] = useState<{ id: string; name: string }[]>([]);
  const [nf, setNf] = useState({ location_id: "", name: "", zone: "", shelf: "" });
  const [busy, setBusy] = useState(false); const [err, setErr] = useState<string | null>(null);
  const load = () => api.get<any>("/bins").then((r) => setBins(r.items || [])).catch(() => setBins([]));
  useEffect(() => { load(); api.get<any>("/staff/locations").then((r) => { const it = r.items || r || []; setLocations(it); setNf((s) => ({ ...s, location_id: it[0]?.id || "" })); }).catch(() => {}); }, []);
  async function add() {
    if (!nf.name.trim() || !nf.location_id) { setErr("Pick a warehouse and name the bin."); return; }
    setBusy(true); setErr(null);
    try { await api.post("/bins", { location_id: nf.location_id, name: nf.name.trim(), zone: nf.zone || null, shelf: nf.shelf || null }); setNf({ ...nf, name: "", zone: "", shelf: "" }); load(); }
    catch (e: any) { setErr(e.message || "Could not add bin"); } finally { setBusy(false); }
  }
  async function del(id: string) { if (!(await confirmDialog("Delete this bin?", { danger: true, confirmLabel: "Delete" }))) return; try { await api.del(`/bins/${id}`); load(); } catch (e: any) { setErr(e.message || "In use — move its stock first"); } }
  const locName = (id: string) => locations.find((l) => l.id === id)?.name || "—";
  const fld = "bg-white border border-slate-200 rounded-lg text-sm px-3 py-2";
  return (
    <div className="max-w-3xl space-y-4">
      <div><h3 className="text-sm font-bold text-ink-primary">Storage bins</h3><p className="text-xs text-ink-tertiary">Give every product an address inside a warehouse — shelf / bin. Enabled in Settings → Inventory &amp; warehouses.</p></div>
      {err && <div className="rounded-lg bg-rose-50 border border-rose-200 text-rose-700 text-xs px-3 py-2">{err}</div>}
      <div className="bg-white rounded-2xl border border-slate-200/90 overflow-hidden">
        <table className="w-full text-xs">
          <thead className="bg-slate-50/70 text-ink-tertiary text-[11px] font-bold"><tr><th className="text-left py-2.5 px-4">Bin</th><th className="text-left py-2.5 px-4">Warehouse</th><th className="text-left py-2.5 px-4">Zone / shelf</th><th className="py-2.5 px-4"></th></tr></thead>
          <tbody className="divide-y divide-slate-100">
            {bins == null && <SkeletonRows cols={4} />}
            {bins?.length === 0 && <tr><td colSpan={4} className="py-6 text-center text-ink-tertiary">No bins yet — add one below.</td></tr>}
            {bins?.map((b) => (
              <tr key={b.id}>
                <td className="py-2.5 px-4 font-bold text-ink-primary">{b.name}</td>
                <td className="py-2.5 px-4 text-ink-secondary">{locName(b.location_id)}</td>
                <td className="py-2.5 px-4 text-ink-tertiary">{[b.zone, b.shelf, b.bin_number].filter(Boolean).join(" / ") || "—"}</td>
                <td className="py-2.5 px-4 text-right"><button onClick={() => del(b.id)} className="text-rose-600 font-semibold">Delete</button></td>
              </tr>
            ))}
          </tbody>
        </table>
        <div className="p-3 border-t border-slate-100 flex items-end gap-2 flex-wrap">
          <div><label className="block text-[11px] font-bold text-ink-tertiary mb-1">Warehouse</label><select value={nf.location_id} onChange={(e) => setNf({ ...nf, location_id: e.target.value })} className={fld}>{locations.map((l) => <option key={l.id} value={l.id}>{l.name}</option>)}</select></div>
          <div><label className="block text-[11px] font-bold text-ink-tertiary mb-1">Bin name</label><input value={nf.name} onChange={(e) => setNf({ ...nf, name: e.target.value })} placeholder="A-01" className={fld} /></div>
          <div><label className="block text-[11px] font-bold text-ink-tertiary mb-1">Zone</label><input value={nf.zone} onChange={(e) => setNf({ ...nf, zone: e.target.value })} className={`${fld} w-24`} /></div>
          <div><label className="block text-[11px] font-bold text-ink-tertiary mb-1">Shelf</label><input value={nf.shelf} onChange={(e) => setNf({ ...nf, shelf: e.target.value })} className={`${fld} w-24`} /></div>
          <button onClick={add} disabled={busy} className="px-3 py-2 rounded-lg bg-primary text-white text-xs font-semibold disabled:opacity-60">+ Add bin</button>
        </div>
      </div>
    </div>
  );
}
