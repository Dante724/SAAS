import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Shell, { M } from "../../components/Shell";
import { api } from "../../api/client";
import { useMe } from "../../context/Me";
import VoidableList from "../../components/VoidableList";
import { useClientSearch } from "../../hooks/useClientSearch";
import NewClientDrawer from "../../components/NewClientDrawer";
import { currencySymbol, money } from "../orders/types";
import { escapeHtml } from "../../lib/safeHtml";
import ScanButton from "../../components/ScanButton";
import ReaderCharge from "../../components/ReaderCharge";
import CardPaymentsList from "../../components/CardPaymentsList";
import { scanBeep, useWedgeScanner } from "../../lib/scanner";

type Product = { id: string; name?: string; sku?: string; sell_price?: number; qty_available?: number; image_data?: string | null; low_stock?: boolean };
type Service = { id: string; name?: string; price?: number; duration_min?: number };
type ClientLite = { id: string; name?: string; phone?: string };
type Account = { id: string; name?: string; payment_option?: string };
// F1: what GET /sales/lookup returns for a scanned code.
type Match = { id: string; name: string; sku?: string | null; barcode?: string | null; code?: string | null; mpn?: string | null; sell_price: number; qty_available: number; matched_on?: string | null };
type Receipt = {
  sale_number: string; shop_name: string; total: number; discount?: number; paid: boolean; created_at?: string | null;
  lines: { description: string; qty: number; total: number }[];
  client_email?: string | null; client_phone?: string | null; text: string;
};

// Payment goes into the account used last on this device, else the cash drawer, else the first.
const LAST_ACCT = "pos_last_account";
function defaultAccount(items: Account[]): string {
  let last = "";
  try { last = localStorage.getItem(LAST_ACCT) || ""; } catch { /* ignore */ }
  return (items.find((a) => a.id === last) || items.find((a) => a.payment_option === "cash") || items[0])?.id || "";
}
type CartLine = { key: string; kind: "product" | "service"; ref_id: string; description: string; unit_price: number; qty: number; discount: number; stock?: number };

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
  const [client, setClient] = useState<ClientLite | null>(null);
  const [clientQ, setClientQ] = useState("");
  const [newClient, setNewClient] = useState(false);
  const [busy, setBusy] = useState(false);
  const [toast, setToast] = useState<string | null>(null);
  const flash = (m: string) => { setToast(m); setTimeout(() => setToast(null), 2600); };
  const { can } = useMe();
  const [recent, setRecent] = useState(false);
  const [recentKey, setRecentKey] = useState(0);   // bumped after a charge so the list re-reads

  useEffect(() => { api.get<{ items: Product[] }>("/catalog/products").then((r) => setProducts(r.items || [])).catch(() => {}); }, []);
  useEffect(() => { api.get<{ items: Service[] }>("/catalog/services").then((r) => setServices(r.items || [])).catch(() => {}); }, []);
  useEffect(() => { api.get<{ items: Account[] }>("/cash/accounts").then((r) => { setAccounts(r.items || []); setAccountId(defaultAccount(r.items || [])); }).catch(() => {}); }, []);
  const [receiptFor, setReceiptFor] = useState<string | null>(null);   // sale id → the after-sale receipt choices
  const [cOpen, setCOpen] = useState(false);
  const clientResults = useClientSearch<ClientLite>(clientQ, cOpen && !client, 6);

  const grid = useMemo(() => {
    const term = q.trim().toLowerCase();
    if (mode === "products") return products.filter((p) => !term || (p.name || "").toLowerCase().includes(term) || (p.sku || "").toLowerCase().includes(term));
    return services.filter((s) => !term || (s.name || "").toLowerCase().includes(term));
  }, [mode, q, products, services]);

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
    setCart((c) => {
      const ex = c.find((l) => l.ref_id === id && l.kind === kind);
      if (ex) return c.map((l) => l === ex ? { ...l, qty: l.qty + 1 } : l);
      return [...c, { key: Math.random().toString(36).slice(2), kind, ref_id: id, description: name, unit_price: price, qty: 1, discount: 0, stock }];
    });
  }
  const setQty = (key: string, d: number) => setCart((c) => c.map((l) => l.key === key ? { ...l, qty: Math.max(1, l.qty + d) } : l));
  const remove = (key: string) => setCart((c) => c.filter((l) => l.key !== key));

  const subtotal = cart.reduce((s, l) => s + l.unit_price * l.qty * (1 - l.discount / 100), 0);
  const total = Math.max(subtotal - discount, 0);
  // Phones: the cart is a full-screen sheet opened from the bottom bar instead of a side column.
  const [cartOpen, setCartOpen] = useState(false);

  // F2/F3: "Charge on reader" saves the sale unpaid, then the reader takes the card; the server
  // marks it paid once the card is approved. A declined or canceled card leaves it "Not paid".
  const [readerFor, setReaderFor] = useState<{ saleId: string; number: string; total: number } | null>(null);
  const cardAccount = accounts.find((a) => a.id === accountId && (a.payment_option === "card" || a.payment_option === "cashless"))?.id;

  useWedgeScanner((code) => { scan(code); }, { enabled: !receiptFor && !readerFor && !newClient && !matches });

  async function charge(onReader = false) {
    if (cart.length === 0) { flash("Cart is empty"); return; }
    setBusy(true);
    try {
      const r = await api.post<any>("/sales", {
        client_id: client?.id || null, account_id: onReader ? null : (accountId || null), discount,
        coupon_code: coupon.trim() || null, gift_card_code: giftCard.trim() || null, redeem_points: redeemPts || 0,
        lines: cart.map((l) => ({ kind: l.kind, ref_id: l.ref_id, description: l.description, qty: l.qty, unit_price: l.unit_price, discount: l.discount })),
      });
      const red = r.redeemed && Object.keys(r.redeemed).length
        ? " · redeemed " + Object.entries(r.redeemed).map(([k, v]) => `${k} ${money(v as number)}`).join(", ") : "";
      // No profit here: the customer is usually looking at this screen.
      try { if (accountId) localStorage.setItem(LAST_ACCT, accountId); } catch { /* ignore */ }
      if (onReader && r.total > 0) setReaderFor({ saleId: r.id, number: r.sale_number, total: r.total });
      else { flash(`Sale ${r.sale_number} · ${money(r.total)}${red}`); setReceiptFor(r.id); }
      setRecentKey((k) => k + 1);
      setCart([]); setCartOpen(false); setDiscount(0); setCoupon(""); setGiftCard(""); setRedeemPts(0); setShowRewards(false); setClient(null); setClientQ("");
      api.get<{ items: Product[] }>("/catalog/products").then((res) => setProducts(res.items || [])).catch(() => {});
    } catch (e: any) { flash(`Failed: ${e.message}`); } finally { setBusy(false); }
  }

  return (
    <Shell title="Sales · Quick POS">
      <div className="flex-1 flex overflow-hidden">
        {/* LEFT: catalog grid */}
        <div className="flex-1 min-w-0 flex flex-col overflow-hidden">
          <div className="p-6 pb-3 flex flex-wrap items-center gap-3 flex-shrink-0">
            <div className="flex items-center bg-slate-100/90 p-0.5 rounded-lg border border-slate-200/60 text-xs">
              <button onClick={() => setMode("products")} className={`px-3 py-1.5 rounded-md font-semibold flex items-center gap-1.5 ${mode === "products" ? "bg-white text-ink-primary shadow-xs" : "text-ink-tertiary hover:text-ink-primary"}`}><M n="inventory_2" c="text-xs" />Products</button>
              {can("can_sell_services") && <button onClick={() => setMode("services")} className={`px-3 py-1.5 rounded-md font-semibold flex items-center gap-1.5 ${mode === "services" ? "bg-white text-ink-primary shadow-xs" : "text-ink-tertiary hover:text-ink-primary"}`}><M n="build" c="text-xs" />Services</button>}
            </div>
            <div className="relative flex-1 min-w-[160px] max-w-md"><M n="search" c="absolute left-2.5 top-2 text-ink-tertiary text-sm" /><input value={q} onChange={(e) => setQ(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); scan(q, true); } }} aria-label="Search or scan"
              className={`glass-input w-full pl-8 pr-3 py-1.5 text-xs rounded-lg font-medium transition-shadow ${miss ? "ring-2 ring-rose-400" : ""}`} placeholder={mode === "products" ? "Search or scan a barcode…" : `Search ${mode}…`} /></div>
            <ScanButton onScan={(c) => scan(c)} title="Scan barcode" />
            <button onClick={() => setRecent((v) => !v)} className="ml-auto px-3 py-1.5 rounded-lg border border-slate-200 bg-white text-xs font-semibold text-ink-secondary hover:bg-slate-50 flex items-center gap-1.5"><M n="receipt_long" c="text-sm" />Recent sales</button>
          </div>
          {recent && (
            <div className="mx-6 mb-3 rounded-2xl border border-slate-200 bg-white max-h-72 overflow-y-auto flex-shrink-0">
              <VoidableList key={recentKey} perm="can_delete_sales" noun="sale" empty="No sales yet." onFlash={flash}
                deletePath={(id) => `/sales/${id}`}
                load={() => api.get<{ items: any[] }>("/sales").then((r) => (r.items || []).map((s) => ({
                  id: s.id, title: s.sale_number, sub: s.paid ? "Paid" : "Not paid", amount: s.total,
                  created_at: s.created_at, voided_at: s.voided_at, void_reason: s.void_reason })))} />
              {can("can_take_card_payments") && <div className="border-t border-slate-100 p-2"><CardPaymentsList sales reloadKey={recentKey} /></div>}
            </div>
          )}
          <div className="flex-1 overflow-y-auto px-6 pb-6">
            <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-3">
              {mode === "products" && grid.map((p: any) => (
                <button key={p.id} onClick={() => add("product", p.id, p.name, p.sell_price || 0, p.qty_available)} className="text-left bg-white rounded-xl border border-slate-200/90 shadow-clay-sm p-3 hover:border-primary/50 hover:-translate-y-0.5 transition-all">
                  <div className="flex items-center gap-2 mb-2">
                    {p.image_data ? <img src={p.image_data} alt="" className="w-9 h-9 rounded-lg object-cover border border-slate-200" /> : <div className="w-9 h-9 rounded-lg bg-blue-50 text-primary flex items-center justify-center"><M n="inventory_2" c="text-base" /></div>}
                    <span className={`ml-auto text-[11px] font-bold px-1.5 py-0.5 rounded-full ${p.low_stock ? "bg-amber-100 text-amber-700" : "bg-emerald-50 text-emerald-700"}`}>{p.qty_available ?? 0}</span>
                  </div>
                  <div className="text-xs font-bold text-ink-primary leading-snug line-clamp-2 min-h-[32px]">{p.name}</div>
                  <div className="text-sm font-bold tabular-nums text-primary mt-1">{money(p.sell_price)}</div>
                </button>
              ))}
              {mode === "services" && grid.map((s: any) => (
                <button key={s.id} onClick={() => add("service", s.id, s.name, s.price || 0)} className="text-left bg-white rounded-xl border border-slate-200/90 shadow-clay-sm p-3 hover:border-primary/50 hover:-translate-y-0.5 transition-all">
                  <div className="w-9 h-9 rounded-lg bg-purple-50 text-purple-600 flex items-center justify-center mb-2"><M n="build" c="text-base" /></div>
                  <div className="text-xs font-bold text-ink-primary leading-snug line-clamp-2 min-h-[32px]">{s.name}</div>
                  <div className="flex items-center justify-between mt-1"><span className="text-sm font-bold tabular-nums text-primary">{money(s.price)}</span>{s.duration_min ? <span className="text-[11px] text-ink-tertiary">{s.duration_min}m</span> : null}</div>
                </button>
              ))}
              {grid.length === 0 && <div className="col-span-full text-xs text-ink-tertiary py-8 text-center">No {mode} found.</div>}
            </div>
          </div>
          <button onClick={() => setCartOpen(true)} className="md:hidden m-3 mt-0 px-4 py-3 rounded-xl bg-primary text-white text-sm font-bold shadow-clay-sm flex items-center gap-2 flex-shrink-0">
            <M n="shopping_cart" c="text-base" />Current sale{cart.length > 0 ? ` · ${cart.length}` : ""}<span className="ml-auto tabular-nums">{money(total)}</span>
          </button>
        </div>

        {/* RIGHT: cart */}
        <aside className={`md:static md:w-[340px] lg:w-[380px] flex-shrink-0 bg-white md:border-l border-slate-200/80 flex-col shadow-clay-drawer ${cartOpen ? "fixed inset-0 z-[60] flex" : "hidden md:flex"}`}>
          <div className="px-5 py-4 border-b border-slate-200/80 flex items-center justify-between gap-2">
            <h3 className="text-sm font-bold text-ink-primary flex items-center gap-2"><M n="shopping_cart" c="text-base text-primary" />Current sale</h3>
            {cart.length > 0 && <button onClick={() => setCart([])} className="ml-auto px-2 py-1 text-[11px] font-semibold text-rose-600 hover:underline">Clear</button>}
            <button onClick={() => setCartOpen(false)} aria-label="Back to items" className="md:hidden w-9 h-9 rounded-lg flex items-center justify-center text-ink-tertiary hover:bg-slate-100"><M n="close" c="text-base" /></button>
          </div>

          {/* client */}
          <div className="px-5 py-3 border-b border-slate-100">
            {client ? (
              <div className="glass-input flex items-center gap-2 px-3 py-2 rounded-lg text-xs"><M n="person" c="text-sm text-ink-tertiary" /><span className="font-semibold text-ink-primary">{client.name}</span><span className="text-ink-tertiary">{client.phone}</span><button onClick={() => { setClient(null); setClientQ(""); }} className="ml-auto text-primary font-semibold">Change</button></div>
            ) : (
              <div className="flex items-center gap-2">
                <div className="relative flex-1"><M n="search" c="absolute left-2.5 top-2 text-ink-tertiary text-xs" /><input value={clientQ} onChange={(e) => { setClientQ(e.target.value); setCOpen(true); }} onFocus={() => setCOpen(true)} onClick={() => setCOpen(true)}
                    onKeyDown={(e) => { if (e.key === "Escape") setCOpen(false); }} autoComplete="off" className="glass-input w-full pl-8 pr-3 py-1.5 text-xs rounded-lg" placeholder="Attach client (optional)…" />
                  {cOpen && (<>
                    <div className="fixed inset-0 z-[9]" onClick={() => setCOpen(false)} />
                    <div className="absolute z-10 left-0 right-0 mt-1 bg-white border border-slate-200 rounded-xl shadow-clay-drawer p-1.5 max-h-72 overflow-y-auto">
                      {!clientQ.trim() && clientResults.length > 0 && <div className="px-2.5 pt-1 pb-0.5 text-[11px] text-ink-tertiary">Recent clients</div>}
                      {clientQ.trim() && clientResults.length === 0 && <div className="px-2.5 py-2 text-xs text-ink-tertiary">No client matches "{clientQ.trim()}"</div>}
                      {clientResults.map((r) => <button key={r.id} onClick={() => { setClient(r); setCOpen(false); }} className="flex items-center gap-2 w-full text-left px-2.5 py-2 rounded-lg hover:bg-blue-50/60 text-xs"><span className="font-semibold text-ink-primary">{r.name}</span><span className="text-ink-tertiary font-mono">{r.phone}</span></button>)}
                      <button onClick={() => setNewClient(true)} className="flex items-center gap-2 w-full text-left px-2.5 py-2 rounded-lg hover:bg-blue-50/60 text-xs text-primary font-bold border-t border-slate-100 mt-1"><M n="person_add" c="text-sm" />Create new client{clientQ.trim() ? ` "${clientQ.trim()}"` : ""}</button>
                    </div>
                  </>)}
                </div>
                <button aria-label="New client" onClick={() => setNewClient(true)} title="New client" className="w-8 h-8 flex-shrink-0 rounded-lg bg-blue-50 text-primary border border-blue-100 flex items-center justify-center hover:bg-blue-100"><M n="person_add" c="text-base" /></button>
              </div>
            )}
          </div>

          {/* lines */}
          <div className="flex-1 overflow-y-auto px-5 py-3 space-y-2">
            {cart.length === 0 && <div className="text-xs text-ink-tertiary text-center py-10">Tap products or services to add them.</div>}
            {cart.map((l) => (
              <div key={l.key} data-testid="cart-line" className={`rounded-xl border p-2.5 transition-colors ${lit && lit.id === l.ref_id && l.kind === "product" ? "bg-emerald-50 border-emerald-300 ring-2 ring-emerald-300" : "bg-slate-50/70 border-slate-200/70"}`}>
                <div className="flex items-start gap-2">
                  <div className="flex-1 min-w-0"><div className="text-xs font-bold text-ink-primary truncate">{l.description}</div><div className="text-[11px] text-ink-tertiary tabular-nums">{money(l.unit_price)} each</div></div>
                  <button aria-label="Close" onClick={() => remove(l.key)} className="text-ink-tertiary hover:text-rose-600"><M n="close" c="text-sm" /></button>
                </div>
                <div className="flex items-center justify-between mt-2">
                  <div className="flex items-center gap-1.5">
                    <button aria-label="Remove" onClick={() => setQty(l.key, -1)} className="w-6 h-6 rounded-lg bg-white border border-slate-200 flex items-center justify-center text-ink-secondary hover:bg-slate-50"><M n="remove" c="text-xs" /></button>
                    <span className="text-xs font-bold tabular-nums w-6 text-center">{l.qty}</span>
                    <button aria-label="Add" onClick={() => setQty(l.key, 1)} className="w-6 h-6 rounded-lg bg-white border border-slate-200 flex items-center justify-center text-ink-secondary hover:bg-slate-50"><M n="add" c="text-xs" /></button>
                  </div>
                  <span className="text-xs font-bold tabular-nums text-ink-primary">{money(l.unit_price * l.qty * (1 - l.discount / 100))}</span>
                </div>
              </div>
            ))}
          </div>

          {/* totals + charge */}
          <div className="px-5 py-4 border-t border-slate-200/80 space-y-3 bg-slate-50/40">
            <div className="flex items-center justify-between text-xs"><span className="text-ink-secondary">Subtotal</span><span className="tabular-nums font-semibold text-ink-primary">{money(subtotal)}</span></div>
            <div className="flex items-center justify-between text-xs"><span className="text-ink-secondary">Discount ({currencySymbol()})</span><input type="number" value={discount} onChange={(e) => setDiscount(Math.max(0, Number(e.target.value)))} className="glass-input w-24 px-2 py-1 rounded-lg text-xs text-right font-mono" /></div>

            {/* 144bb — rewards: coupon / gift card / loyalty applied server-side at charge */}
            <button onClick={() => setShowRewards((v) => !v)} className="flex items-center gap-1.5 text-[11px] font-semibold text-primary hover:underline"><M n="redeem" c="text-sm" />Rewards &amp; credits<M n={showRewards ? "expand_less" : "expand_more"} c="text-sm" /></button>
            {showRewards && (
              <div className="space-y-2 rounded-xl border border-slate-200/70 bg-white/70 p-2.5">
                <div className="flex items-center justify-between gap-2"><span className="text-[11px] text-ink-secondary flex-shrink-0">Coupon code</span><input value={coupon} onChange={(e) => setCoupon(e.target.value.toUpperCase())} className="glass-input w-32 px-2 py-1 rounded-lg text-xs font-mono " placeholder="SAVE10" /></div>
                <div className="flex items-center justify-between gap-2"><span className="text-[11px] text-ink-secondary flex-shrink-0">Gift card</span><input value={giftCard} onChange={(e) => setGiftCard(e.target.value.toUpperCase())} className="glass-input w-32 px-2 py-1 rounded-lg text-xs tabular-nums " placeholder="GC-XXXX" /></div>
                <div className="flex items-center justify-between gap-2"><span className="text-[11px] text-ink-secondary flex-shrink-0">Loyalty points</span><input type="number" min={0} value={redeemPts} onChange={(e) => setRedeemPts(Math.max(0, Number(e.target.value)))} disabled={!client} className="glass-input w-32 px-2 py-1 rounded-lg text-xs text-right font-mono disabled:opacity-50" /></div>
                {!client && <p className="text-[11px] text-ink-tertiary">Attach a client to redeem loyalty points.</p>}
              </div>
            )}

            <div className="flex items-center justify-between"><span className="text-sm font-bold text-ink-primary">Total</span><span className="text-xl font-bold tabular-nums text-primary">{money(total)}</span></div>
            <div><label className="block text-[11px] font-bold text-ink-tertiary mb-1">Take payment into</label>
              <select value={accountId} onChange={(e) => setAccountId(e.target.value)} className="glass-input w-full py-2 px-3 text-xs font-semibold rounded-lg appearance-none">
                <option value="">No payment (park sale)</option>
                {accounts.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
              </select>
            </div>
            <button onClick={() => charge()} disabled={busy || cart.length === 0} className="w-full py-2.5 rounded-xl bg-primary text-white text-sm font-bold shadow-clay-sm hover:brightness-110 disabled:opacity-50 flex items-center justify-center gap-2"><M n="point_of_sale" c="text-base" />{busy ? "Processing…" : `Charge ${money(total)}`}</button>
            {can("can_take_card_payments") && <button onClick={() => charge(true)} disabled={busy || cart.length === 0 || total <= 0} className="w-full py-2 rounded-xl border border-primary/30 bg-white text-primary text-xs font-bold hover:bg-blue-50 disabled:opacity-50 flex items-center justify-center gap-2"><M n="contactless" c="text-base" />Charge on reader</button>}
          </div>
        </aside>
      </div>
      {toast && <div className="fixed bottom-6 left-1/2 -translate-x-1/2 bg-ink-primary text-white text-xs font-semibold px-4 py-2.5 rounded-xl shadow-clay-drawer z-[60]">{toast}</div>}
      {matches && <MatchPicker code={matches.code} items={matches.items} onPick={(m) => { addMatch(m); flash(`Added ${m.name}`); setMatches(null); setQ(""); }} onClose={() => setMatches(null)} />}
      {readerFor && <ReaderCharge saleId={readerFor.saleId} amount={readerFor.total} accountId={cardAccount} title={`Sale ${readerFor.number}`}
        onApproved={() => { flash(`Sale ${readerFor.number} paid by card`); setRecentKey((k) => k + 1); }}
        onClose={(c) => {
          if (c?.status === "succeeded") setReceiptFor(readerFor.saleId);
          else flash(`Sale ${readerFor.number} wasn't paid. It stays in Recent sales as Not paid, where you can delete it.`);
          setRecentKey((k) => k + 1); setReaderFor(null);
        }} />}
      {receiptFor && <ReceiptSheet saleId={receiptFor} onClose={() => setReceiptFor(null)} onFlash={flash} />}
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
      <div className="absolute inset-0 bg-ink-primary/40" onClick={onClose} />
      <div className="relative w-full max-w-sm rounded-2xl border border-slate-200 bg-white p-5 shadow-clay-drawer">
        <div className="flex items-start justify-between gap-3">
          <div><div className="text-sm font-bold text-ink-primary">Which product?</div>
            <div className="text-xs text-ink-tertiary">{items.length} products have the code <span className="font-mono">{code}</span></div></div>
          <button aria-label="Close" onClick={onClose} className="text-ink-tertiary hover:text-ink-primary"><M n="close" c="text-lg" /></button>
        </div>
        <div className="mt-3 space-y-1.5 max-h-80 overflow-y-auto">
          {items.map((m) => (
            <button key={m.id} onClick={() => onPick(m)} className="w-full text-left flex items-center gap-2 px-3 py-2 rounded-xl border border-slate-200 hover:border-primary/50 hover:bg-blue-50/40">
              <div className="min-w-0 flex-1"><div className="text-xs font-bold text-ink-primary truncate">{m.name}</div>
                <div className="text-[11px] text-ink-tertiary truncate">{[m.sku && `SKU ${m.sku}`, m.barcode && `Barcode ${m.barcode}`, m.mpn && `MPN ${m.mpn}`].filter(Boolean).join(" · ")} · {m.qty_available} in stock</div></div>
              <span className="text-xs font-bold tabular-nums text-primary">{money(m.sell_price)}</span>
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}

// After a sale: give the customer a receipt — print it, email it or send it on WhatsApp.
function ReceiptSheet({ saleId, onClose, onFlash }: { saleId: string; onClose: () => void; onFlash: (m: string) => void }) {
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

  function print() {
    if (!rc) return;
    const w = window.open("", "_blank", "width=380,height=600"); if (!w) { onFlash("Allow pop-ups to print the receipt"); return; }
    const rows = rc.lines.map((l) => `<tr><td>${l.qty !== 1 ? `${l.qty} × ` : ""}${escapeHtml(l.description)}</td><td class="r">${escapeHtml(money(l.total))}</td></tr>`).join("");
    const css = "body{font-family:Inter,system-ui,sans-serif;margin:0;padding:18px;color:#14213f;font-size:13px}h1{font-size:15px;margin:0}p{margin:2px 0 12px;color:#5b6b8c}table{width:100%;border-collapse:collapse}td{padding:4px 0}.r{text-align:right}.t td{border-top:1px dashed #c9d3ea;padding-top:8px;font-weight:700;font-size:15px}";
    w.document.write(`<html><head><title>${escapeHtml(rc.sale_number)}</title><style>${css}</style></head><body><h1>${escapeHtml(rc.shop_name)}</h1>`
      + `<p>Receipt ${escapeHtml(rc.sale_number)}${rc.created_at ? " · " + escapeHtml(new Date(rc.created_at + (rc.created_at.endsWith("Z") ? "" : "Z")).toLocaleString()) : ""}</p>`
      + `<table>${rows}${rc.discount ? `<tr><td>Discount</td><td class="r">−${escapeHtml(money(rc.discount))}</td></tr>` : ""}<tr class="t"><td>Total</td><td class="r">${escapeHtml(money(rc.total))}</td></tr></table>`
      + `<p style="margin-top:12px">${rc.paid ? "Paid" : "Not paid yet"} · Thank you!</p></body></html>`);
    w.document.close(); w.focus(); w.print();
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
      <div className="absolute inset-0 bg-ink-primary/40" onClick={onClose} />
      <div className="relative w-full max-w-sm rounded-2xl border border-slate-200 bg-white p-5 shadow-clay-drawer">
        <div className="flex items-start justify-between gap-3">
          <div>
            <div className="text-sm font-bold text-ink-primary">Sale complete</div>
            <div className="text-xs text-ink-tertiary">{rc ? `${rc.sale_number} · ${money(rc.total)}` : err || "Loading…"}</div>
          </div>
          <button aria-label="Close" onClick={onClose} className="text-ink-tertiary hover:text-ink-primary"><M n="close" c="text-lg" /></button>
        </div>
        <div className="mt-4 text-[11px] font-bold text-ink-tertiary">Give the customer a receipt</div>
        <div className="mt-2 space-y-2.5">
          <button onClick={print} disabled={!rc} className="w-full flex items-center justify-center gap-2 py-2 rounded-xl border border-slate-200 bg-white text-xs font-bold text-ink-primary hover:bg-slate-50 disabled:opacity-50"><M n="print" c="text-base" />Print receipt</button>
          <div className="flex gap-2">
            <input value={email} onChange={(e) => setEmail(e.target.value)} type="email" placeholder="Customer email" aria-label="Customer email" className="glass-input min-w-0 flex-1 px-3 py-2 rounded-lg text-xs" />
            <button onClick={sendEmail} disabled={!rc || sending || !email.includes("@")} className="px-3 py-2 rounded-xl bg-primary text-white text-xs font-bold disabled:opacity-50 flex items-center gap-1.5"><M n="mail" c="text-sm" />{sending ? "Sending…" : "Email"}</button>
          </div>
          <div className="flex gap-2">
            <input value={phone} onChange={(e) => setPhone(e.target.value)} inputMode="tel" placeholder="WhatsApp number with country code" aria-label="WhatsApp number" className="glass-input min-w-0 flex-1 px-3 py-2 rounded-lg text-xs" />
            {waHref
              ? <a href={waHref} target="_blank" rel="noopener noreferrer" onClick={onClose} className="px-3 py-2 rounded-xl bg-emerald-600 text-white text-xs font-bold flex items-center gap-1.5"><M n="chat" c="text-sm" />WhatsApp</a>
              : <span className="px-3 py-2 rounded-xl bg-emerald-600 text-white text-xs font-bold flex items-center gap-1.5 opacity-50" aria-disabled="true"><M n="chat" c="text-sm" />WhatsApp</span>}
          </div>
        </div>
        <button onClick={onClose} className="mt-4 w-full py-2 text-xs font-bold text-ink-secondary hover:text-ink-primary">No receipt</button>
      </div>
    </div>
  );
}
