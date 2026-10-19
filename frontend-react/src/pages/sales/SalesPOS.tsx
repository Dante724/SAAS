import { useEffect, useMemo, useState } from "react";
import Shell, { M } from "../../components/Shell";
import { api } from "../../api/client";
import { useMe } from "../../context/Me";
import VoidableList from "../../components/VoidableList";
import { useClientSearch } from "../../hooks/useClientSearch";
import NewClientDrawer from "../../components/NewClientDrawer";
import { currencySymbol, money } from "../orders/types";

type Product = { id: string; name?: string; sku?: string; sell_price?: number; qty_available?: number; image_data?: string | null; low_stock?: boolean };
type Service = { id: string; name?: string; price?: number; duration_min?: number };
type ClientLite = { id: string; name?: string; phone?: string };
type Account = { id: string; name?: string };
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
  useEffect(() => { api.get<{ items: Account[] }>("/cash/accounts").then((r) => { setAccounts(r.items || []); if (r.items?.[0]) setAccountId(r.items[0].id); }).catch(() => {}); }, []);
  const [cOpen, setCOpen] = useState(false);
  const clientResults = useClientSearch<ClientLite>(clientQ, cOpen && !client, 6);

  const grid = useMemo(() => {
    const term = q.trim().toLowerCase();
    if (mode === "products") return products.filter((p) => !term || (p.name || "").toLowerCase().includes(term) || (p.sku || "").toLowerCase().includes(term));
    return services.filter((s) => !term || (s.name || "").toLowerCase().includes(term));
  }, [mode, q, products, services]);

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

  async function charge() {
    if (cart.length === 0) { flash("Cart is empty"); return; }
    setBusy(true);
    try {
      const r = await api.post<any>("/sales", {
        client_id: client?.id || null, account_id: accountId || null, discount,
        coupon_code: coupon.trim() || null, gift_card_code: giftCard.trim() || null, redeem_points: redeemPts || 0,
        lines: cart.map((l) => ({ kind: l.kind, ref_id: l.ref_id, description: l.description, qty: l.qty, unit_price: l.unit_price, discount: l.discount })),
      });
      const red = r.redeemed && Object.keys(r.redeemed).length
        ? " · redeemed " + Object.entries(r.redeemed).map(([k, v]) => `${k} ${money(v as number)}`).join(", ") : "";
      flash(`Sale ${r.sale_number} · ${money(r.total)} · profit ${money(r.profit)}${red}`);
      setRecentKey((k) => k + 1);
      setCart([]); setDiscount(0); setCoupon(""); setGiftCard(""); setRedeemPts(0); setShowRewards(false); setClient(null); setClientQ("");
      api.get<{ items: Product[] }>("/catalog/products").then((res) => setProducts(res.items || [])).catch(() => {});
    } catch (e: any) { flash(`Failed: ${e.message}`); } finally { setBusy(false); }
  }

  return (
    <Shell title="Sales · Quick POS">
      <div className="flex-1 flex overflow-hidden">
        {/* LEFT: catalog grid */}
        <div className="flex-1 min-w-0 flex flex-col overflow-hidden">
          <div className="p-6 pb-3 flex items-center gap-3 flex-shrink-0">
            <div className="flex items-center bg-slate-100/90 p-0.5 rounded-lg border border-slate-200/60 text-xs">
              <button onClick={() => setMode("products")} className={`px-3 py-1.5 rounded-md font-semibold flex items-center gap-1.5 ${mode === "products" ? "bg-white text-ink-primary shadow-xs" : "text-ink-tertiary hover:text-ink-primary"}`}><M n="inventory_2" c="text-xs" />Products</button>
              {can("can_sell_services") && <button onClick={() => setMode("services")} className={`px-3 py-1.5 rounded-md font-semibold flex items-center gap-1.5 ${mode === "services" ? "bg-white text-ink-primary shadow-xs" : "text-ink-tertiary hover:text-ink-primary"}`}><M n="build" c="text-xs" />Services</button>}
            </div>
            <div className="relative flex-1 max-w-md"><M n="search" c="absolute left-2.5 top-2 text-ink-tertiary text-sm" /><input value={q} onChange={(e) => setQ(e.target.value)} className="glass-input w-full pl-8 pr-3 py-1.5 text-xs rounded-lg font-medium" placeholder={`Search ${mode}…`} /></div>
            <button onClick={() => setRecent((v) => !v)} className="ml-auto px-3 py-1.5 rounded-lg border border-slate-200 bg-white text-xs font-semibold text-ink-secondary hover:bg-slate-50 flex items-center gap-1.5"><M n="receipt_long" c="text-sm" />Recent sales</button>
          </div>
          {recent && (
            <div className="mx-6 mb-3 rounded-2xl border border-slate-200 bg-white max-h-72 overflow-y-auto flex-shrink-0">
              <VoidableList key={recentKey} perm="can_delete_sales" noun="sale" empty="No sales yet." onFlash={flash}
                deletePath={(id) => `/sales/${id}`}
                load={() => api.get<{ items: any[] }>("/sales").then((r) => (r.items || []).map((s) => ({
                  id: s.id, title: s.sale_number, sub: s.paid ? "Paid" : "Not paid", amount: s.total,
                  created_at: s.created_at, voided_at: s.voided_at, void_reason: s.void_reason })))} />
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
        </div>

        {/* RIGHT: cart */}
        <aside className="w-[380px] flex-shrink-0 bg-white border-l border-slate-200/80 flex flex-col shadow-clay-drawer">
          <div className="px-5 py-4 border-b border-slate-200/80 flex items-center justify-between">
            <h3 className="text-sm font-bold text-ink-primary flex items-center gap-2"><M n="shopping_cart" c="text-base text-primary" />Current sale</h3>
            {cart.length > 0 && <button onClick={() => setCart([])} className="text-[11px] font-semibold text-rose-600 hover:underline">Clear</button>}
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
              <div key={l.key} className="bg-slate-50/70 rounded-xl border border-slate-200/70 p-2.5">
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
            <button onClick={charge} disabled={busy || cart.length === 0} className="w-full py-2.5 rounded-xl bg-primary text-white text-sm font-bold shadow-clay-sm hover:brightness-110 disabled:opacity-50 flex items-center justify-center gap-2"><M n="point_of_sale" c="text-base" />{busy ? "Processing…" : `Charge ${money(total)}`}</button>
          </div>
        </aside>
      </div>
      {toast && <div className="fixed bottom-6 left-1/2 -translate-x-1/2 bg-ink-primary text-white text-xs font-semibold px-4 py-2.5 rounded-xl shadow-clay-drawer z-[60]">{toast}</div>}
      {newClient && <NewClientDrawer prefillName={clientQ.trim()} onClose={() => setNewClient(false)} onCreated={(c) => { setClient(c); setNewClient(false); setClientQ(""); flash(`Client "${c.name}" created & attached`); }} />}
    </Shell>
  );
}
