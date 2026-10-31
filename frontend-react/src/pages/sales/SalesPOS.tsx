import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Shell, { M } from "../../components/Shell";
import { api } from "../../api/client";
import { useMe } from "../../context/Me";
import VoidableList from "../../components/VoidableList";
import { useClientSearch } from "../../hooks/useClientSearch";
import NewClientDrawer from "../../components/NewClientDrawer";
import { currencySymbol, money } from "../orders/types";
import { escapeHtml, printHtmlInFrame } from "../../lib/safeHtml";
import { autoPrintForSale } from "../../lib/autoPrint";
import AutoPrintPicker from "../../components/AutoPrintPicker";
import ScanButton from "../../components/ScanButton";
import ReaderCharge from "../../components/ReaderCharge";
import CardPaymentsList from "../../components/CardPaymentsList";
import { scanBeep, useWedgeScanner } from "../../lib/scanner";
import { inputCls } from "../invoices/billing";
import { Avatar, deviceIcon } from "../orders/look";
import { loadFailed } from "../../lib/notice";

type Product = { id: string; name?: string; sku?: string; sell_price?: number; qty_available?: number; image_data?: string | null; low_stock?: boolean };
type Service = { id: string; name?: string; price?: number; duration_min?: number };
type ClientLite = { id: string; name?: string; phone?: string };
type Account = { id: string; name?: string; payment_option?: string };
// F1: what GET /sales/lookup returns for a scanned code.
type Match = { id: string; name: string; sku?: string | null; barcode?: string | null; code?: string | null; mpn?: string | null; sell_price: number; qty_available: number; matched_on?: string | null };
type Receipt = {
  sale_number: string; shop_name: string; total: number; discount?: number; tax?: number; gift_card?: number; paid: boolean; created_at?: string | null;
  lines: { description: string; qty: number; total: number }[];
  client_email?: string | null; client_phone?: string | null; text: string;
};

const tileCls = "text-left bg-white rounded-xl border border-slate-200 p-3 hover:border-primary/40 hover:bg-slate-50/60 active:bg-slate-100/70 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/30";
const qtyBtn = "w-9 h-9 flex items-center justify-center text-ink-secondary hover:bg-slate-50 hover:text-ink-primary rounded-lg disabled:opacity-40 disabled:hover:bg-transparent";

// Payment goes into the account used last on this device, else the cash drawer, else the first.
const LAST_ACCT = "pos_last_account";
function defaultAccount(items: Account[]): string {
  let last = "";
  try { last = localStorage.getItem(LAST_ACCT) || ""; } catch { /* ignore */ }
  return (items.find((a) => a.id === last) || items.find((a) => a.payment_option === "cash") || items[0])?.id || "";
}
// A neutral glyph for a product tile: the device it fits when the name says so, else a box.
export function productGlyph(name?: string | null, brand?: string | null) {
  const t = `${brand || ""} ${name || ""}`;
  if (/\bcards?\b|cartridge|charger|cable/i.test(t)) return "inventory_2";
  const g = deviceIcon({ model: t });
  return g === "devices" && !/ipad|tablet|watch/i.test(t) ? "inventory_2" : g;
}

// Stock on a product tile: a quiet count, amber only when it is running low or gone.
export function stockChip(qty?: number | null, low?: boolean) {
  const q = qty ?? 0;
  if (q <= 0) return { label: "Out of stock", warn: true };
  if (low) return { label: `${q} left`, warn: true };
  return { label: `${q} in stock`, warn: false };
}

type CartLine = { key: string; kind: "product" | "service"; ref_id: string; description: string; unit_price: number; qty: number; discount: number; stock?: number };
// B5: what POST /sales/quote returns — the sale priced exactly as the charge will save it.
type Quote = {
  subtotal: number; discount: number; coupon: number; loyalty: number; discount_total: number;
  taxes: { name: string; rate: number; mode: string; amount: number }[]; tax_total: number; tax_included: number;
  total: number; gift_card: number; due: number; cash_tendered: number | null; change_due: number | null;
  problems: Partial<Record<"coupon" | "gift_card" | "loyalty", string>>;
};
// A saved sale that still needs paying: a declined card (the cart stays) or one picked from Recent sales.
type Pending = { id: string; number: string; due: number; fromCart: boolean };
type SaleRow = { id: string; sale_number: string; total: number; due?: number; paid: boolean; voided_at?: string | null };

// Money at the till always shows cents: "Charge $58.20", never "$58.2".
const cash = (n?: number | null) => money(n ?? 0, undefined, true);

export default function SalesPOS() {
  const [mode, setMode] = useState<"products" | "services">("products");
  const [products, setProducts] = useState<Product[]>([]);
  const [services, setServices] = useState<Service[]>([]);
  const [q, setQ] = useState("");
  const [cart, setCart] = useState<CartLine[]>([]);
  const [discount, setDiscount] = useState(0);
  const [coupon, setCoupon] = useState("");          // 144bb — POS rewards
  const [giftCard, setGiftCard] = useState("");
  const [redeemPts, setRedeemPts] = useState(0);
  const [showRewards, setShowRewards] = useState(false);
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [accountId, setAccountId] = useState("");
  const [cashGiven, setCashGiven] = useState("");
  const [client, setClient] = useState<ClientLite | null>(null);
  const [clientQ, setClientQ] = useState("");
  const [newClient, setNewClient] = useState(false);
  const [busy, setBusy] = useState(false);
  const [toast, setToast] = useState<string | null>(null);
  const flash = (m: string) => { setToast(m); setTimeout(() => setToast(null), 2600); };
  const { can } = useMe();
  const [recent, setRecent] = useState(false);
  const [recentKey, setRecentKey] = useState(0);   // bumped after a charge so the list re-reads
  const saleRows = useRef<Record<string, SaleRow>>({});

  const loadProducts = () => api.get<{ items: Product[] }>("/catalog/products").then((r) => setProducts(r.items || [])).catch(loadFailed("products"));
  useEffect(() => { loadProducts(); }, []);
  useEffect(() => { api.get<{ items: Service[] }>("/catalog/services").then((r) => setServices(r.items || [])).catch(loadFailed("services")); }, []);
  useEffect(() => { api.get<{ items: Account[] }>("/cash/accounts").then((r) => { setAccounts(r.items || []); setAccountId(defaultAccount(r.items || [])); }).catch(loadFailed("cash accounts")); }, []);
  const [receiptFor, setReceiptFor] = useState<{ id: string; change: number | null } | null>(null);   // the after-sale receipt choices
  const [returnFor, setReturnFor] = useState<string | null>(null);
  const [cOpen, setCOpen] = useState(false);
  const clientResults = useClientSearch<ClientLite>(clientQ, cOpen && !client, 6);

  const grid = useMemo(() => {
    const term = q.trim().toLowerCase();
    if (mode === "products") return products.filter((p) => !term || (p.name || "").toLowerCase().includes(term) || (p.sku || "").toLowerCase().includes(term));
    return services.filter((s) => !term || (s.name || "").toLowerCase().includes(term));
  }, [mode, q, products, services]);

  // B5: a sale that is saved but not paid. While it came from this cart, the cart is kept (nothing
  // is lost if the card is declined) and locked, so it can't drift from what was saved.
  const [pending, setPending] = useState<Pending | null>(null);
  const locked = !!pending?.fromCart;
  const lockedRef = useRef(false);
  lockedRef.current = locked;

  // F1 scan-to-cart: a scanner (keyboard wedge or camera) or Enter in the search box looks the code
  // up by SKU / barcode / internal code / MPN. One match goes in the cart, several open a picker.
  const [matches, setMatches] = useState<{ code: string; items: Match[] } | null>(null);
  const [lit, setLit] = useState<{ id: string; n: number } | null>(null);   // cart line that just changed
  const [miss, setMiss] = useState(false);
  const litTimer = useRef<ReturnType<typeof setTimeout>>();
  const addMatch = useCallback((m: Match) => {
    add("product", m.id, m.name, m.sell_price || 0, m.qty_available);
    scanBeep(true);
    setLit((x) => ({ id: m.id, n: (x?.n || 0) + 1 }));
    clearTimeout(litTimer.current);
    litTimer.current = setTimeout(() => setLit(null), 900);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  const scan = useCallback(async (raw: string, fromSearch = false) => {
    const code = raw.trim();
    if (!code) return;
    try {
      const r = await api.get<{ items: Match[] }>(`/sales/lookup?code=${encodeURIComponent(code)}`);
      const items = r.items || [];
      if (items.length === 1) { addMatch(items[0]); flash(`Added ${items[0].name}`); if (fromSearch) setQ(""); return; }
      if (items.length > 1) { setMatches({ code, items }); return; }
      scanBeep(false); setMiss(true); setTimeout(() => setMiss(false), 900);
      if (fromSearch) { setMode("products"); flash(`No product has the code "${code}". Showing search results.`); }
      else flash(`No product has the code "${code}"`);
    } catch (e: any) { scanBeep(false); flash(e.message || "Couldn't look up that code"); }
  }, [addMatch]);

  function add(kind: "product" | "service", id: string, name: string, price: number, stock?: number) {
    if (lockedRef.current) { flash("Finish or delete the unpaid sale first"); return; }
    setCart((c) => {
      const ex = c.find((l) => l.ref_id === id && l.kind === kind);
      if (ex) return c.map((l) => l === ex ? { ...l, qty: l.qty + 1 } : l);
      return [...c, { key: Math.random().toString(36).slice(2), kind, ref_id: id, description: name, unit_price: price, qty: 1, discount: 0, stock }];
    });
  }
  const setQty = (key: string, d: number) => setCart((c) => c.map((l) => l.key === key ? { ...l, qty: Math.max(1, l.qty + d) } : l));
  const remove = (key: string) => setCart((c) => c.filter((l) => l.key !== key));

  const subtotal = cart.reduce((s, l) => s + l.unit_price * l.qty * (1 - l.discount / 100), 0);
  const itemCount = cart.reduce((n, l) => n + l.qty, 0);
  const account = accounts.find((a) => a.id === accountId);
  const isCash = account?.payment_option === "cash";
  const tendered = isCash && cashGiven.trim() !== "" && Number.isFinite(Number(cashGiven)) ? Number(cashGiven) : null;

  // B5: the server prices every cart and tender change (coupon, points, line tax, gift card). The
  // Charge button only shows a quote for exactly this cart, and the charge sends that amount back
  // so the server refuses to save a different one.
  const quoteBody = useMemo(() => cart.length === 0 ? null : {
    client_id: client?.id || null, discount, coupon_code: coupon.trim() || null, gift_card_code: giftCard.trim() || null,
    redeem_points: redeemPts || 0, cash_tendered: tendered,
    lines: cart.map((l) => ({ kind: l.kind, ref_id: l.ref_id, description: l.description, qty: l.qty, unit_price: l.unit_price, discount: l.discount })),
  }, [cart, client, discount, coupon, giftCard, redeemPts, tendered]);
  const quoteKey = quoteBody ? JSON.stringify(quoteBody) : "";
  const [quoted, setQuoted] = useState<{ key: string; q: Quote } | null>(null);
  const [quoteErr, setQuoteErr] = useState<string | null>(null);
  const [requote, setRequote] = useState(0);
  useEffect(() => {
    if (!quoteBody) { setQuoted(null); setQuoteErr(null); return; }
    let live = true;   // a slower, older quote never overwrites a newer one
    const t = setTimeout(() => {
      api.post<Quote>("/sales/quote", quoteBody)
        .then((r) => { if (live) { setQuoted({ key: quoteKey, q: r }); setQuoteErr(null); } })
        .catch((e: any) => { if (live) setQuoteErr(e.message || "Couldn't price this sale"); });
    }, 150);
    return () => { live = false; clearTimeout(t); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [quoteKey, requote]);
  const quote = quoted && quoted.key === quoteKey ? quoted.q : null;
  const problems = quote?.problems || {};
  const blocked = Object.keys(problems).length > 0;
  const due = quote ? quote.due : Math.max(subtotal - discount, 0);
  const short = quote != null && tendered != null && tendered + 0.005 < quote.due;
  // Phones: the cart is a full-screen sheet opened from the bottom bar instead of a side column.
  const [cartOpen, setCartOpen] = useState(false);

  // F2/F3: "Charge on reader" saves the sale unpaid, then the reader takes the card; the server
  // marks it paid once the card is approved. A declined card leaves the sale pending, cart intact.
  const [readerFor, setReaderFor] = useState<Pending | null>(null);
  const cardAccount = accounts.find((a) => a.id === accountId && (a.payment_option === "card" || a.payment_option === "cashless"))?.id;

  useWedgeScanner((code) => { scan(code); }, { enabled: !receiptFor && !readerFor && !newClient && !matches && !returnFor });

  function clearSale() {
    setCart([]); setCartOpen(false); setDiscount(0); setCoupon(""); setGiftCard(""); setRedeemPts(0); setShowRewards(false);
    setClient(null); setClientQ(""); setCashGiven("");
    loadProducts();
  }
  // Paid: clear the cart if the sale came from it, and offer the receipt.
  function paid(p: { id: string; fromCart: boolean }, msg: string, change: number | null) {
    flash(msg);
    setRecentKey((k) => k + 1);
    setPending(null);
    if (p.fromCart) clearSale();
    void autoPrintForSale(p.id);   // whatever is switched on to print when a sale is made
    setReceiptFor({ id: p.id, change: change && change > 0 ? change : null });
  }

  async function charge(onReader = false) {
    if (!quoteBody || !quote) return;
    setBusy(true);
    try {
      const r = await api.post<any>("/sales", {
        ...quoteBody, account_id: onReader ? null : (accountId || null), cash_tendered: onReader ? null : tendered,
        expected_due: quote.due,
      });
      try { if (accountId) localStorage.setItem(LAST_ACCT, accountId); } catch { /* ignore */ }
      setRecentKey((k) => k + 1);
      if (onReader && r.due > 0) {
        const p = { id: r.id, number: r.sale_number, due: r.due, fromCart: true };
        setPending(p); setReaderFor(p);
        return;
      }
      const red = r.redeemed && Object.keys(r.redeemed).length
        ? " · " + Object.entries(r.redeemed).map(([k, v]) => `${k === "gift_card" ? "gift card" : k} ${cash(v as number)}`).join(", ") : "";
      // No profit here: the customer is usually looking at this screen.
      paid({ id: r.id, fromCart: true }, r.paid ? `Sale ${r.sale_number} · ${cash(r.total)}${red}` : `Sale ${r.sale_number} saved · ${cash(r.due)} to pay later`, r.change_due);
    } catch (e: any) { flash(`Failed: ${e.message}`); setRequote((n) => n + 1); } finally { setBusy(false); }
  }

  const [payAcct, setPayAcct] = useState("");
  const [payCash, setPayCash] = useState("");
  const payAccount = accounts.find((a) => a.id === (payAcct || accountId)) || accounts.find((a) => a.payment_option === "cash") || accounts[0];
  const payTendered = payAccount?.payment_option === "cash" && payCash.trim() !== "" ? Number(payCash) : null;
  async function payPending() {
    if (!pending || !payAccount) return;
    setBusy(true);
    try {
      const r = await api.post<{ due: number; change_due: number | null }>(`/sales/${pending.id}/pay`, { account_id: payAccount.id, cash_tendered: payTendered });
      setPayCash("");
      paid(pending, `Sale ${pending.number} paid · ${cash(r.due)}`, r.change_due);
    } catch (e: any) { flash(e.message || "Couldn't take the payment"); } finally { setBusy(false); }
  }
  async function deletePending() {
    if (!pending) return;
    setBusy(true);
    try {
      await api.del(`/sales/${pending.id}?reason=${encodeURIComponent("Not paid")}`);
      flash(pending.fromCart ? `Sale ${pending.number} deleted. The items are still in the cart.` : `Sale ${pending.number} deleted`);
      setPending(null); setRecentKey((k) => k + 1); loadProducts();
    } catch (e: any) { flash(e.message || "Couldn't delete the sale"); } finally { setBusy(false); }
  }

  const row = (label: string, value: string, strong = false) => (
    <div className="flex items-center justify-between text-xs"><span className="text-ink-secondary">{label}</span><span className={`tabular-nums ${strong ? "font-semibold text-ink-primary" : "text-ink-primary"}`}>{value}</span></div>
  );
  const problem = (msg?: string) => msg ? <p role="alert" className="text-[11px] text-rose-700">{msg}</p> : null;

  return (
    <Shell title="Point of sale">
      <div className="flex-1 flex overflow-hidden">
        {/* LEFT: catalog grid */}
        <div className="flex-1 min-w-0 flex flex-col overflow-hidden">
          <div className="p-6 pb-3 flex flex-wrap items-center gap-3 flex-shrink-0">
            <div className="flex items-center bg-slate-100 p-0.5 rounded-lg border border-slate-200 text-xs">
              <button onClick={() => setMode("products")} className={`h-8 px-3 rounded-md font-semibold flex items-center gap-1.5 ${mode === "products" ? "bg-white text-ink-primary shadow-xs" : "text-ink-tertiary hover:text-ink-primary"}`}><M n="inventory_2" c="text-xs" />Products</button>
              {can("can_sell_services") && <button onClick={() => setMode("services")} className={`h-8 px-3 rounded-md font-semibold flex items-center gap-1.5 ${mode === "services" ? "bg-white text-ink-primary shadow-xs" : "text-ink-tertiary hover:text-ink-primary"}`}><M n="build" c="text-xs" />Services</button>}
            </div>
            <div className="relative flex-1 min-w-[160px] max-w-md"><M n="search" c="absolute left-3 top-1/2 -translate-y-1/2 text-ink-tertiary text-sm pointer-events-none" /><input value={q} onChange={(e) => setQ(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); scan(q, true); } }} aria-label="Search or scan"
              className={`${inputCls} pl-9 transition-shadow ${miss ? "!border-rose-400 ring-2 ring-rose-400/30" : ""}`} placeholder={mode === "products" ? "Search or scan a barcode…" : `Search ${mode}…`} /></div>
            <ScanButton onScan={(c) => scan(c)} title="Scan barcode" />
            <button onClick={() => setRecent((v) => !v)} aria-label="Recent sales" title="Recent sales" aria-expanded={recent} className={`h-9 px-3.5 rounded-xl border text-xs font-semibold flex items-center gap-1.5 ${recent ? "bg-primary/10 text-primary border-primary/30" : "bg-white text-ink-secondary border-slate-200 hover:bg-slate-50"}`}><M n="receipt_long" c="text-sm" /><span className="hidden sm:inline">Recent sales</span></button>
          </div>
          {recent && (
            <div className="mx-6 mb-3 rounded-2xl border border-slate-200 bg-white max-h-72 overflow-y-auto flex-shrink-0">
              <VoidableList key={recentKey} perm="can_delete_sales" noun="sale" empty="No sales yet." onFlash={flash}
                deletePath={(id) => `/sales/${id}`}
                load={() => api.get<{ items: SaleRow[] }>("/sales").then((r) => (r.items || []).map((s: any) => {
                  saleRows.current[s.id] = s;
                  return { id: s.id, title: s.sale_number, sub: s.paid ? "Paid" : `Not paid · ${cash(s.due)} to pay`, amount: s.total,
                    created_at: s.created_at, voided_at: s.voided_at, void_reason: s.void_reason };
                }))}
                actions={(r) => {
                  const s = saleRows.current[r.id];
                  if (!s || s.voided_at) return null;
                  if (!s.paid) return <button onClick={() => { setPending({ id: s.id, number: s.sale_number, due: s.due || 0, fromCart: false }); setCartOpen(true); }} disabled={locked}
                    className="h-8 px-3 rounded-xl border border-slate-200 text-[11px] font-semibold text-primary hover:bg-primary/5 disabled:opacity-50">Take payment</button>;
                  return can("can_create_returns") ? <button onClick={() => setReturnFor(s.id)} aria-label={`Return items from sale ${s.sale_number}`}
                    className="h-8 px-3 rounded-xl border border-slate-200 text-[11px] font-semibold text-ink-secondary hover:bg-slate-50">Return items</button> : null;
                }} />
              {can("can_take_card_payments") && <div className="border-t border-slate-100 p-2"><CardPaymentsList sales reloadKey={recentKey} /></div>}
            </div>
          )}
          <div className="flex-1 overflow-y-auto px-6 pb-6">
            <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-3">
              {mode === "products" && grid.map((p: any) => {
                const chip = stockChip(p.qty_available, p.low_stock);
                return (
                  <button key={p.id} onClick={() => add("product", p.id, p.name, p.sell_price || 0, p.qty_available)} className={tileCls}>
                    <div className="flex items-start gap-2 mb-2.5">
                      {p.image_data ? <img src={p.image_data} alt="" className="w-9 h-9 rounded-lg object-cover border border-slate-200" /> : <span className="w-9 h-9 rounded-lg bg-slate-100 text-ink-tertiary flex items-center justify-center flex-shrink-0"><M n={productGlyph(p.name, p.brand)} c="text-base" /></span>}
                      <span data-testid="stock-chip" data-warn={chip.warn ? "true" : "false"}
                        className={`ml-auto text-[11px] tabular-nums whitespace-nowrap ${chip.warn ? "font-semibold px-1.5 py-0.5 rounded-full bg-amber-50 text-amber-800 border border-amber-200/70" : "text-ink-tertiary pt-0.5"}`}>{chip.label}</span>
                    </div>
                    <div className="text-xs font-semibold text-ink-primary leading-snug line-clamp-2 min-h-[32px]">{p.name}</div>
                    <div className="text-sm font-semibold tabular-nums text-ink-primary mt-1">{money(p.sell_price)}</div>
                  </button>
                );
              })}
              {mode === "services" && grid.map((s: any) => (
                <button key={s.id} onClick={() => add("service", s.id, s.name, s.price || 0)} className={tileCls}>
                  <span className="w-9 h-9 rounded-lg bg-slate-100 text-ink-tertiary flex items-center justify-center mb-2.5"><M n="build" c="text-base" /></span>
                  <div className="text-xs font-semibold text-ink-primary leading-snug line-clamp-2 min-h-[32px]">{s.name}</div>
                  <div className="flex items-center justify-between mt-1"><span className="text-sm font-semibold tabular-nums text-ink-primary">{money(s.price)}</span>{s.duration_min ? <span className="text-[11px] text-ink-tertiary tabular-nums">{s.duration_min} min</span> : null}</div>
                </button>
              ))}
              {grid.length === 0 && (
                <div className="col-span-full py-12 text-center">
                  <div className="text-sm font-semibold text-ink-primary">{q.trim() ? `No ${mode} match "${q.trim()}"` : `No ${mode} yet`}</div>
                  <div className="text-xs text-ink-tertiary mt-1">{q.trim() ? "Check the spelling, or scan the barcode instead." : mode === "products" ? "Add products in Catalog to sell them here." : "Add services in Catalog to sell them here."}</div>
                </div>
              )}
            </div>
          </div>
          <button onClick={() => setCartOpen(true)} className="md:hidden m-3 mt-0 min-h-[48px] px-4 py-3 rounded-xl bg-primary text-white text-sm font-semibold flex items-center gap-2 flex-shrink-0">
            <M n="shopping_cart" c="text-base" />Current sale{itemCount > 0 ? ` · ${itemCount} ${itemCount === 1 ? "item" : "items"}` : ""}<span className="ml-auto tabular-nums">{cash(pending ? pending.due : due)}</span>
          </button>
        </div>

        {/* RIGHT: cart */}
        <aside className={`md:static md:w-[340px] lg:w-[380px] flex-shrink-0 bg-white md:border-l border-slate-200 flex-col ${cartOpen ? "fixed inset-0 z-[60] flex" : "hidden md:flex"}`}>
          <div className="px-5 py-4 border-b border-slate-200/80 flex items-center justify-between gap-2">
            <h3 className="text-sm font-semibold text-ink-primary flex items-center gap-2">Current sale{itemCount > 0 && <span className="text-xs font-medium text-ink-tertiary tabular-nums">{itemCount} {itemCount === 1 ? "item" : "items"}</span>}</h3>
            {cart.length > 0 && !locked && <button onClick={() => setCart([])} className="ml-auto h-8 px-3 rounded-xl text-xs font-semibold text-ink-secondary hover:bg-slate-100">Clear</button>}
            <button onClick={() => setCartOpen(false)} aria-label="Back to items" className="md:hidden w-9 h-9 rounded-lg flex items-center justify-center text-ink-tertiary hover:bg-slate-100"><M n="close" c="text-base" /></button>
          </div>

          {/* client */}
          <div className="px-5 py-3 border-b border-slate-100">
            {client ? (
              <div className="flex items-center gap-2.5 px-2.5 py-2 rounded-lg border border-slate-200 bg-slate-50 text-xs"><Avatar name={client.name} size={28} /><span className="min-w-0"><span className="block font-semibold text-ink-primary truncate">{client.name}</span>{client.phone && <span className="block text-ink-tertiary tabular-nums">{client.phone}</span>}</span>{!locked && <button onClick={() => { setClient(null); setClientQ(""); }} className="ml-auto h-8 px-2 rounded-lg text-primary font-semibold hover:bg-primary/10">Change</button>}</div>
            ) : (
              <div className="flex items-center gap-2">
                <div className="relative flex-1"><M n="search" c="absolute left-3 top-1/2 -translate-y-1/2 text-ink-tertiary text-sm pointer-events-none" /><input value={clientQ} onChange={(e) => { setClientQ(e.target.value); setCOpen(true); }} onFocus={() => setCOpen(true)} onClick={() => setCOpen(true)}
                    onKeyDown={(e) => { if (e.key === "Escape") setCOpen(false); }} autoComplete="off" disabled={locked} className={`${inputCls} pl-9`} placeholder="Attach client (optional)…" />
                  {cOpen && (<>
                    <div className="fixed inset-0 z-[9]" aria-hidden="true" onClick={() => setCOpen(false)} />
                    <div className="absolute z-10 left-0 right-0 mt-1 bg-white border border-slate-200 rounded-xl shadow-clay-drawer p-1.5 max-h-72 overflow-y-auto">
                      {!clientQ.trim() && clientResults.length > 0 && <div className="px-2.5 pt-1 pb-0.5 text-[11px] text-ink-tertiary">Recent clients</div>}
                      {clientQ.trim() && clientResults.length === 0 && <div className="px-2.5 py-2 text-xs text-ink-tertiary">No client matches "{clientQ.trim()}"</div>}
                      {clientResults.map((r) => <button key={r.id} onClick={() => { setClient(r); setCOpen(false); }} className="flex items-center gap-2.5 w-full text-left px-2.5 py-2 rounded-lg hover:bg-slate-50 text-xs"><Avatar name={r.name} size={24} /><span className="font-semibold text-ink-primary truncate">{r.name}</span><span className="ml-auto text-ink-tertiary tabular-nums">{r.phone}</span></button>)}
                      <button onClick={() => setNewClient(true)} className="flex items-center gap-2 w-full text-left px-2.5 py-2 rounded-lg hover:bg-slate-50 text-xs text-primary font-semibold border-t border-slate-100 mt-1"><M n="person_add" c="text-sm" />Create new client{clientQ.trim() ? ` "${clientQ.trim()}"` : ""}</button>
                    </div>
                  </>)}
                </div>
                <button aria-label="New client" onClick={() => setNewClient(true)} disabled={locked} title="New client" className="w-9 h-9 flex-shrink-0 rounded-lg bg-white text-ink-secondary border border-slate-200 flex items-center justify-center hover:bg-slate-50 hover:text-primary disabled:opacity-50"><M n="person_add" c="text-base" /></button>
              </div>
            )}
          </div>

          {/* lines */}
          <div className="flex-1 overflow-y-auto px-5 py-3 space-y-2">
            {cart.length === 0 && !pending && (
              <div className="text-center py-12 px-4">
                <span className="w-10 h-10 mx-auto rounded-full bg-slate-100 text-ink-tertiary flex items-center justify-center"><M n="shopping_cart" c="text-base" /></span>
                <div className="text-sm font-semibold text-ink-primary mt-3">No items yet</div>
                <div className="text-xs text-ink-tertiary mt-1">Tap a product or service, or scan a barcode.</div>
              </div>
            )}
            {cart.map((l) => (
              <div key={l.key} data-testid="cart-line" className={`rounded-xl border px-3 py-2.5 transition-colors ${lit && lit.id === l.ref_id && l.kind === "product" ? "bg-emerald-50 border-emerald-300" : "bg-white border-slate-200"}`}>
                <div className="flex items-start gap-2">
                  <div className="flex-1 min-w-0 pt-0.5"><div className="text-xs font-semibold text-ink-primary truncate">{l.description}</div><div className="text-[11px] text-ink-tertiary tabular-nums">{money(l.unit_price)} each</div></div>
                  <button aria-label="Remove item" title="Remove item" disabled={locked} onClick={() => remove(l.key)} className="w-9 h-9 -mr-1.5 -mt-1.5 flex-shrink-0 rounded-lg flex items-center justify-center text-ink-tertiary hover:text-rose-600 hover:bg-rose-50 disabled:opacity-40"><M n="close" c="text-sm" /></button>
                </div>
                <div className="flex items-center justify-between mt-1.5">
                  <div className="flex items-center rounded-lg border border-slate-200 bg-white">
                    <button aria-label="Decrease quantity" disabled={locked || l.qty <= 1} onClick={() => setQty(l.key, -1)} className={qtyBtn}><M n="remove" c="text-sm" /></button>
                    <span className="text-sm font-semibold tabular-nums min-w-[28px] text-center text-ink-primary">{l.qty}</span>
                    <button aria-label="Increase quantity" disabled={locked} onClick={() => setQty(l.key, 1)} className={qtyBtn}><M n="add" c="text-sm" /></button>
                  </div>
                  <span className="text-sm font-semibold tabular-nums text-ink-primary">{money(l.unit_price * l.qty * (1 - l.discount / 100))}</span>
                </div>
              </div>
            ))}
          </div>

          {/* totals + charge */}
          <div className="px-5 py-4 border-t border-slate-200 space-y-3 bg-slate-50/60">
            {pending ? (
              <div data-testid="pending-sale" className="space-y-2.5">
                <div className="rounded-xl border border-amber-200 bg-amber-50 px-3 py-2.5">
                  <div className="text-xs font-semibold text-amber-900">Sale {pending.number} isn't paid yet</div>
                  <div className="text-[11px] text-amber-800 mt-0.5">{pending.fromCart ? "The items stay in the cart until it's paid." : "Take the payment, or delete the sale."}</div>
                </div>
                <div className="flex items-baseline justify-between"><span className="text-sm font-semibold text-ink-primary">To pay</span><span className="text-2xl font-semibold tabular-nums text-ink-primary">{cash(pending.due)}</span></div>
                <div><label htmlFor="pos-pay-account" className="block text-xs font-semibold text-ink-tertiary mb-1">Take payment into</label>
                  <select id="pos-pay-account" value={payAccount?.id || ""} onChange={(e) => setPayAcct(e.target.value)} className={inputCls}>
                    {accounts.length === 0 && <option value="">No cashbox set up</option>}
                    {accounts.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
                  </select>
                </div>
                {payAccount?.payment_option === "cash" && <div className="flex items-center justify-between gap-3 text-xs"><label htmlFor="pos-pay-cash" className="text-ink-secondary">Cash received</label><div className="w-28"><input id="pos-pay-cash" type="number" min={0} inputMode="decimal" value={payCash} onChange={(e) => setPayCash(e.target.value)} className={`${inputCls} text-right tabular-nums`} /></div></div>}
                {payTendered != null && payTendered >= pending.due && row("Change due", cash(payTendered - pending.due), true)}
                <button onClick={payPending} disabled={busy || !payAccount || (payTendered != null && payTendered + 0.005 < pending.due)} className="w-full h-11 rounded-xl bg-primary text-white text-sm font-semibold hover:bg-primary-container disabled:opacity-50 tabular-nums">Take payment {cash(pending.due)}</button>
                <div className="flex gap-2">
                  {can("can_take_card_payments") && pending.due > 0 && <button onClick={() => setReaderFor(pending)} disabled={busy} className="flex-1 h-9 rounded-xl border border-slate-200 bg-white text-ink-primary text-xs font-semibold hover:bg-slate-50 disabled:opacity-50 flex items-center justify-center gap-1.5"><M n="contactless" c="text-sm text-ink-tertiary" />Try the card again</button>}
                  {can("can_delete_sales") && <button onClick={deletePending} disabled={busy} className="h-9 px-3 rounded-xl border border-slate-200 bg-white text-xs font-semibold text-rose-700 hover:bg-rose-50 disabled:opacity-50">Delete sale</button>}
                  {!pending.fromCart && <button onClick={() => setPending(null)} className="h-9 px-3 rounded-xl text-xs font-semibold text-ink-secondary hover:bg-slate-100">Not now</button>}
                </div>
              </div>
            ) : (<>
            {row("Subtotal", cash(quote?.subtotal ?? subtotal), true)}
            <div className="flex items-center justify-between gap-3 text-xs"><label htmlFor="pos-discount" className="text-ink-secondary">Discount ({currencySymbol()})</label><div className="w-28"><input id="pos-discount" type="number" min={0} inputMode="decimal" value={discount} onChange={(e) => setDiscount(Math.max(0, Number(e.target.value) || 0))} className={`${inputCls} text-right tabular-nums`} /></div></div>
            {!!quote?.coupon && row("Coupon", `−${cash(quote.coupon)}`)}
            {!!quote?.loyalty && row("Loyalty points", `−${cash(quote.loyalty)}`)}
            {quote?.taxes.map((t) => <div key={`${t.name}-${t.rate}-${t.mode}`}>{row(`${t.name} ${t.rate}%${t.mode === "included" ? " (included)" : ""}`, cash(t.amount))}</div>)}

            {/* 144bb — rewards: coupon / gift card / loyalty, priced by the server on every change */}
            <button onClick={() => setShowRewards((v) => !v)} aria-expanded={showRewards} className="flex items-center gap-1.5 text-xs font-semibold text-primary hover:underline"><M n="redeem" c="text-sm" />Rewards &amp; credits<M n={showRewards ? "expand_less" : "expand_more"} c="text-sm" /></button>
            {showRewards && (
              <div className="space-y-2 rounded-xl border border-slate-200 bg-white p-3">
                <div className="flex items-center justify-between gap-3"><label htmlFor="pos-coupon" className="text-xs text-ink-secondary flex-shrink-0">Coupon code</label><div className="w-36"><input id="pos-coupon" value={coupon} onChange={(e) => setCoupon(e.target.value.toUpperCase())} className={`${inputCls} font-mono`} placeholder="SAVE10" /></div></div>
                {problem(problems.coupon)}
                <div className="flex items-center justify-between gap-3"><label htmlFor="pos-gift" className="text-xs text-ink-secondary flex-shrink-0">Gift card</label><div className="w-36"><input id="pos-gift" value={giftCard} onChange={(e) => setGiftCard(e.target.value.toUpperCase())} className={`${inputCls} font-mono`} placeholder="GC-XXXX" /></div></div>
                {problem(problems.gift_card)}
                <div className="flex items-center justify-between gap-3"><label htmlFor="pos-points" className="text-xs text-ink-secondary flex-shrink-0">Loyalty points</label><div className="w-36"><input id="pos-points" type="number" min={0} value={redeemPts} onChange={(e) => setRedeemPts(Math.max(0, Number(e.target.value) || 0))} disabled={!client} className={`${inputCls} text-right tabular-nums disabled:opacity-50`} /></div></div>
                {problem(problems.loyalty)}
                {!client && <p className="text-xs text-ink-tertiary">Attach a client to redeem loyalty points.</p>}
              </div>
            )}

            <div className="flex items-baseline justify-between pt-3 border-t border-slate-200"><span className="text-sm font-semibold text-ink-primary">Total</span><span className="text-2xl font-semibold tabular-nums text-ink-primary">{cash(quote?.total ?? Math.max(subtotal - discount, 0))}</span></div>
            {!!quote?.gift_card && <>{row("Paid by gift card", `−${cash(quote.gift_card)}`)}{row("To pay", cash(quote.due), true)}</>}
            <div><label htmlFor="pos-account" className="block text-xs font-semibold text-ink-tertiary mb-1">Take payment into</label>
              <select id="pos-account" value={accountId} onChange={(e) => setAccountId(e.target.value)} className={inputCls}>
                <option value="">Pay later (save the sale)</option>
                {accounts.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
              </select>
            </div>
            {/* B5: cash — what the customer handed over, and the change to give back */}
            {isCash && <div className="flex items-center justify-between gap-3 text-xs"><label htmlFor="pos-cash" className="text-ink-secondary">Cash received</label><div className="w-28"><input id="pos-cash" type="number" min={0} inputMode="decimal" value={cashGiven} onChange={(e) => setCashGiven(e.target.value)} placeholder={cash(due)} className={`${inputCls} text-right tabular-nums`} /></div></div>}
            {quote?.change_due != null && !short && <div data-testid="change-due">{row("Change due", cash(quote.change_due), true)}</div>}
            {short && <p role="alert" className="text-[11px] text-rose-700">Cash received is less than the total.</p>}
            {quoteErr && <p role="alert" className="text-[11px] text-rose-700">{quoteErr}</p>}
            <button onClick={() => charge()} disabled={busy || !quote || blocked || short} className="w-full h-11 rounded-xl bg-primary text-white text-sm font-semibold hover:bg-primary-container disabled:opacity-50 flex items-center justify-center gap-2 tabular-nums">{busy ? "Processing…" : cart.length === 0 ? "Charge" : quote ? `Charge ${cash(quote.due)}` : quoteErr ? "Can't price this sale" : "Pricing…"}</button>
            {can("can_take_card_payments") && <button onClick={() => charge(true)} disabled={busy || !quote || blocked || quote.due <= 0} className="w-full h-9 rounded-xl border border-slate-200 bg-white text-ink-primary text-xs font-semibold hover:bg-slate-50 disabled:opacity-50 flex items-center justify-center gap-2"><M n="contactless" c="text-base text-ink-tertiary" />Charge on reader</button>}
            <div className="flex justify-center"><AutoPrintPicker event="sale" /></div>
            </>)}
          </div>
        </aside>
      </div>
      {toast && <div role="status" className="fixed bottom-6 left-1/2 -translate-x-1/2 max-w-[calc(100vw-32px)] bg-ink-primary text-white text-xs font-semibold px-4 py-2.5 rounded-xl shadow-clay-drawer z-[60]">{toast}</div>}
      {matches && <MatchPicker code={matches.code} items={matches.items} onPick={(m) => { addMatch(m); flash(`Added ${m.name}`); setMatches(null); setQ(""); }} onClose={() => setMatches(null)} />}
      {readerFor && <ReaderCharge saleId={readerFor.id} amount={readerFor.due} accountId={cardAccount} title={`Sale ${readerFor.number}`}
        onApproved={() => setRecentKey((k) => k + 1)}
        onClose={(c) => {
          const p = readerFor;
          setReaderFor(null);
          if (c?.status === "succeeded") paid(p, `Sale ${p.number} paid by card`, null);
          else { flash(`Sale ${p.number} wasn't paid. Try the card again or take payment another way.`); setRecentKey((k) => k + 1); }
        }} />}
      {receiptFor && <ReceiptSheet saleId={receiptFor.id} change={receiptFor.change} onClose={() => setReceiptFor(null)} onFlash={flash} />}
      {returnFor && <ReturnSheet saleId={returnFor} accounts={accounts} onClose={() => setReturnFor(null)}
        onDone={(m) => { flash(m); setReturnFor(null); setRecentKey((k) => k + 1); loadProducts(); }} />}
      {newClient && <NewClientDrawer prefillName={clientQ.trim()} onClose={() => setNewClient(false)} onCreated={(c) => { setClient(c); setNewClient(false); setClientQ(""); flash(`Client "${c.name}" created & attached`); }} />}
    </Shell>
  );
}

// Several products share the scanned code: let the cashier pick the right one.
function MatchPicker({ code, items, onPick, onClose }: { code: string; items: Match[]; onPick: (m: Match) => void; onClose: () => void }) {
  useEffect(() => {
    const k = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", k); return () => window.removeEventListener("keydown", k);
  }, [onClose]);
  return (
    <div className="fixed inset-0 z-[70] flex items-center justify-center p-4" role="dialog" aria-modal="true" aria-label="Choose a product">
      <div className="absolute inset-0 bg-ink-primary/40" aria-hidden="true" onClick={onClose} />
      <div className="relative w-full max-w-sm rounded-2xl border border-slate-200 bg-white p-5 shadow-clay-drawer">
        <div className="flex items-start justify-between gap-3">
          <div><div className="text-sm font-semibold text-ink-primary">Which product?</div>
            <div className="text-xs text-ink-tertiary">{items.length} products have the code <span className="font-mono">{code}</span></div></div>
          <button aria-label="Close" onClick={onClose} className="w-9 h-9 -mr-2 -mt-2 rounded-lg grid place-items-center text-ink-tertiary hover:text-ink-primary hover:bg-slate-100"><M n="close" c="text-lg" /></button>
        </div>
        <div className="mt-3 space-y-1.5 max-h-80 overflow-y-auto">
          {items.map((m) => (
            <button key={m.id} onClick={() => onPick(m)} className="w-full text-left flex items-center gap-2.5 px-3 py-2.5 rounded-xl border border-slate-200 hover:border-primary/40 hover:bg-slate-50">
              <span className="w-8 h-8 rounded-lg bg-slate-100 text-ink-tertiary flex items-center justify-center flex-shrink-0"><M n={productGlyph(m.name)} c="text-sm" /></span>
              <div className="min-w-0 flex-1"><div className="text-xs font-semibold text-ink-primary truncate">{m.name}</div>
                <div className="text-[11px] text-ink-tertiary truncate">{[m.sku && `SKU ${m.sku}`, m.barcode && `Barcode ${m.barcode}`, m.mpn && `MPN ${m.mpn}`].filter(Boolean).join(" · ")} · {m.qty_available} in stock</div></div>
              <span className="text-xs font-semibold tabular-nums text-ink-primary">{money(m.sell_price)}</span>
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}

// After a sale: give the customer a receipt — print it, email it or send it on WhatsApp.
function ReceiptSheet({ saleId, change, onClose, onFlash }: { saleId: string; change?: number | null; onClose: () => void; onFlash: (m: string) => void }) {
  const [rc, setRc] = useState<Receipt | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [email, setEmail] = useState("");
  const [phone, setPhone] = useState("");
  const [sending, setSending] = useState(false);
  useEffect(() => {
    api.get<Receipt>(`/sales/${saleId}/receipt`).then((r) => { setRc(r); setEmail(r.client_email || ""); setPhone(r.client_phone || ""); })
      .catch((e) => setErr(e.message || "Couldn't load the receipt"));
  }, [saleId]);
  useEffect(() => {
    const k = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", k); return () => window.removeEventListener("keydown", k);
  }, [onClose]);

  // The shop's receipt template (Settings › Documents you print), printed through a hidden frame —
  // no pop-up, and one click when the browser's print dialog is turned off. Without a receipt
  // template it falls back to the built-in layout below.
  async function print() {
    if (!rc) return;
    try {
      const r = await api.get<{ html: string | null }>(`/documents/render-sale/${saleId}`);
      if (r?.html) { printHtmlInFrame(r.html); return; }
    } catch { /* fall back to the built-in receipt */ }
    const rows = rc.lines.map((l) => `<tr><td>${l.qty !== 1 ? `${l.qty} × ` : ""}${escapeHtml(l.description)}</td><td class="r">${escapeHtml(money(l.total))}</td></tr>`).join("");
    const css = "body{font-family:Inter,system-ui,sans-serif;margin:0;padding:18px;color:#14213f;font-size:13px}h1{font-size:15px;margin:0}p{margin:2px 0 12px;color:#5b6b8c}table{width:100%;border-collapse:collapse}td{padding:4px 0}.r{text-align:right}.t td{border-top:1px dashed #c9d3ea;padding-top:8px;font-weight:700;font-size:15px}";
    printHtmlInFrame(`<html><head><title>${escapeHtml(rc.sale_number)}</title><style>${css}</style></head><body><h1>${escapeHtml(rc.shop_name)}</h1>`
      + `<p>Receipt ${escapeHtml(rc.sale_number)}${rc.created_at ? " · " + escapeHtml(new Date(rc.created_at + (rc.created_at.endsWith("Z") ? "" : "Z")).toLocaleString()) : ""}</p>`
      + `<table>${rows}${rc.discount ? `<tr><td>Discount</td><td class="r">−${escapeHtml(money(rc.discount))}</td></tr>` : ""}${rc.tax ? `<tr><td>Tax</td><td class="r">${escapeHtml(money(rc.tax))}</td></tr>` : ""}<tr class="t"><td>Total</td><td class="r">${escapeHtml(money(rc.total))}</td></tr>${rc.gift_card ? `<tr><td>Gift card</td><td class="r">−${escapeHtml(money(rc.gift_card))}</td></tr>` : ""}</table>`
      + `<p style="margin-top:12px">${rc.paid ? "Paid" : "Not paid yet"} · Thank you!</p></body></html>`);
  }
  async function sendEmail() {
    setSending(true);
    try { const r = await api.post<{ recipient: string }>(`/sales/${saleId}/receipt/email`, { email: email.trim() || null }); onFlash(`Receipt emailed to ${r.recipient}`); onClose(); }
    catch (e: any) { onFlash(e.message || "Couldn't email the receipt"); } finally { setSending(false); }
  }
  const digits = phone.replace(/[^0-9]/g, "");
  const waHref = rc && digits.length >= 7 ? `https://wa.me/${digits}?text=${encodeURIComponent(rc.text)}` : null;

  return (
    <div className="fixed inset-0 z-[70] flex items-center justify-center p-4" role="dialog" aria-modal="true" aria-label="Receipt">
      <div className="absolute inset-0 bg-ink-primary/40" aria-hidden="true" onClick={onClose} />
      <div className="relative w-full max-w-sm rounded-2xl border border-slate-200 bg-white p-5 shadow-clay-drawer">
        <div className="flex items-start justify-between gap-3">
          <div>
            <div className="text-sm font-semibold text-ink-primary">Sale complete</div>
            <div className="text-xs text-ink-tertiary tabular-nums">{rc ? `${rc.sale_number} · ${money(rc.total)}` : err || "Loading…"}</div>
          </div>
          <button aria-label="Close" onClick={onClose} className="w-9 h-9 -mr-2 -mt-2 rounded-lg grid place-items-center text-ink-tertiary hover:text-ink-primary hover:bg-slate-100"><M n="close" c="text-lg" /></button>
        </div>
        {change ? <div data-testid="receipt-change" className="mt-4 rounded-xl border border-emerald-200 bg-emerald-50 px-3 py-2.5 flex items-baseline justify-between"><span className="text-xs font-semibold text-emerald-900">Give change</span><span className="text-xl font-semibold tabular-nums text-emerald-900">{cash(change)}</span></div> : null}
        <div className="mt-4 text-xs font-semibold text-ink-tertiary">Give the customer a receipt</div>
        <div className="mt-2 space-y-2.5">
          <button onClick={print} disabled={!rc} className="w-full h-9 flex items-center justify-center gap-2 rounded-xl border border-slate-200 bg-white text-xs font-semibold text-ink-primary hover:bg-slate-50 disabled:opacity-50"><M n="print" c="text-base" />Print receipt</button>
          <div className="flex gap-2">
            <input value={email} onChange={(e) => setEmail(e.target.value)} type="email" placeholder="Customer email" aria-label="Customer email" className={`${inputCls} min-w-0 flex-1`} />
            <button onClick={sendEmail} disabled={!rc || sending || !email.includes("@")} className="flex-shrink-0 h-9 px-3.5 rounded-xl bg-primary text-white text-xs font-semibold hover:bg-primary-container disabled:opacity-50 flex items-center gap-1.5"><M n="mail" c="text-sm" />{sending ? "Sending…" : "Email"}</button>
          </div>
          <div className="flex gap-2">
            <input value={phone} onChange={(e) => setPhone(e.target.value)} inputMode="tel" placeholder="WhatsApp number with country code" aria-label="WhatsApp number" className={`${inputCls} min-w-0 flex-1`} />
            {waHref
              ? <a href={waHref} target="_blank" rel="noopener noreferrer" onClick={onClose} className="flex-shrink-0 h-9 px-3.5 rounded-xl border border-slate-200 bg-white text-ink-primary text-xs font-semibold hover:bg-slate-50 flex items-center gap-1.5"><M n="chat" c="text-sm text-emerald-600" />WhatsApp</a>
              : <span className="flex-shrink-0 h-9 px-3.5 rounded-xl border border-slate-200 bg-white text-ink-primary text-xs font-semibold flex items-center gap-1.5 opacity-50" aria-disabled="true"><M n="chat" c="text-sm" />WhatsApp</span>}
          </div>
        </div>
        <button onClick={onClose} className="mt-3 w-full h-9 rounded-xl text-xs font-semibold text-ink-secondary hover:text-ink-primary hover:bg-slate-50">No receipt</button>
      </div>
    </div>
  );
}

type SaleDetail = {
  id: string; sale_number: string; total: number; account_id?: string | null; gift_card: number;
  lines: { id: string; kind: string; description: string; qty: number; total: number; returnable: number }[];
};

// B5: items coming back. Products go back on the shelf; the customer gets their share of what was
// paid (discounts and tax included): the cash part from a cashbox, any gift-card part onto the card.
function ReturnSheet({ saleId, accounts, onClose, onDone }: { saleId: string; accounts: Account[]; onClose: () => void; onDone: (m: string) => void }) {
  const [sale, setSale] = useState<SaleDetail | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [qty, setQty] = useState<Record<string, number>>({});
  const [acct, setAcct] = useState("");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    api.get<SaleDetail>(`/sales/${saleId}`).then((r) => {
      setSale(r);
      setAcct((accounts.find((a) => a.id === r.account_id) || accounts.find((a) => a.payment_option === "cash") || accounts[0])?.id || "");
    }).catch((e) => setErr(e.message || "Couldn't load the sale"));
  }, [saleId]);   // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    const k = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", k); return () => window.removeEventListener("keydown", k);
  }, [onClose]);

  // The same share the server refunds: the items' part of the lines, scaled to what the sale took.
  const base = sale ? sale.lines.reduce((s, l) => s + l.total, 0) : 0;
  const value = sale && base ? sale.lines.reduce((s, l) => s + (l.total * (qty[l.id] || 0)) / l.qty, 0) * sale.total / base : 0;
  const gift = sale && sale.total ? value * sale.gift_card / sale.total : 0;
  const refund = Math.round((value - gift) * 100) / 100;
  const picked = Object.values(qty).some((n) => n > 0);

  async function submit() {
    if (!sale) return;
    setBusy(true);
    try {
      const r = await api.post<{ refunded: number; gift_card: number }>(`/sales/${sale.id}/returns`, {
        account_id: acct || null, reason: reason.trim() || null,
        lines: Object.entries(qty).filter(([, n]) => n > 0).map(([line_id, n]) => ({ line_id, qty: n })) });
      onDone(`Items returned from ${sale.sale_number} · refunded ${cash(r.refunded)}${r.gift_card ? `, ${cash(r.gift_card)} back on the gift card` : ""}`);
    } catch (e: any) { setErr(e.message || "Couldn't return the items"); } finally { setBusy(false); }
  }

  return (
    <div className="fixed inset-0 z-[70] flex items-center justify-center p-4" role="dialog" aria-modal="true" aria-label="Return items">
      <div className="absolute inset-0 bg-ink-primary/40" aria-hidden="true" onClick={onClose} />
      <div className="relative w-full max-w-sm rounded-2xl border border-slate-200 bg-white p-5 shadow-clay-drawer">
        <div className="flex items-start justify-between gap-3">
          <div><div className="text-sm font-semibold text-ink-primary">Return items</div>
            <div className="text-xs text-ink-tertiary">{sale ? `Sale ${sale.sale_number} · ${cash(sale.total)}` : err || "Loading…"}</div></div>
          <button aria-label="Close" onClick={onClose} className="w-9 h-9 -mr-2 -mt-2 rounded-lg grid place-items-center text-ink-tertiary hover:text-ink-primary hover:bg-slate-100"><M n="close" c="text-lg" /></button>
        </div>
        {sale && (<>
          <div className="mt-3 space-y-1.5 max-h-64 overflow-y-auto">
            {sale.lines.map((l) => (
              <div key={l.id} className="flex items-center gap-2 px-3 py-2 rounded-xl border border-slate-200">
                <div className="min-w-0 flex-1"><div className="text-xs font-semibold text-ink-primary truncate">{l.description}</div>
                  <div className="text-[11px] text-ink-tertiary tabular-nums">{l.returnable > 0 ? `${l.returnable} of ${l.qty} can come back` : "Already returned"}</div></div>
                <div className="flex items-center rounded-lg border border-slate-200 bg-white">
                  <button aria-label={`Return one less ${l.description}`} disabled={!qty[l.id]} onClick={() => setQty((m) => ({ ...m, [l.id]: Math.max(0, (m[l.id] || 0) - 1) }))} className={qtyBtn}><M n="remove" c="text-sm" /></button>
                  <span className="text-sm font-semibold tabular-nums min-w-[24px] text-center text-ink-primary">{qty[l.id] || 0}</span>
                  <button aria-label={`Return one more ${l.description}`} disabled={(qty[l.id] || 0) >= l.returnable} onClick={() => setQty((m) => ({ ...m, [l.id]: Math.min(l.returnable, (m[l.id] || 0) + 1) }))} className={qtyBtn}><M n="add" c="text-sm" /></button>
                </div>
              </div>
            ))}
          </div>
          <div className="mt-3 space-y-2">
            <div><label htmlFor="pos-return-account" className="block text-xs font-semibold text-ink-tertiary mb-1">Refund from</label>
              <select id="pos-return-account" value={acct} onChange={(e) => setAcct(e.target.value)} className={inputCls}>
                {accounts.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
              </select></div>
            <input value={reason} onChange={(e) => setReason(e.target.value)} maxLength={300} placeholder="Reason (optional)" aria-label="Reason" className={inputCls} />
            {gift > 0.005 && <p className="text-[11px] text-ink-tertiary tabular-nums">{cash(gift)} goes back onto the gift card.</p>}
            {err && <p role="alert" className="text-[11px] text-rose-700">{err}</p>}
          </div>
          <button onClick={submit} disabled={busy || !picked || (refund > 0 && !acct)} className="mt-3 w-full h-11 rounded-xl bg-primary text-white text-sm font-semibold hover:bg-primary-container disabled:opacity-50 tabular-nums">{busy ? "Returning…" : `Return and refund ${cash(refund)}`}</button>
        </>)}
      </div>
    </div>
  );
}
