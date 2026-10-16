import DirectorySelect from "../../components/DirectorySelect";
import { humanize } from "../../lib/humanize";
import { useEffect, useState } from "react";
import TagChips from "../../components/TagChips";
import { M } from "../../components/Shell";
import DateField from "../../components/DateField";
import { api, openAuthedDoc } from "../../api/client";
import { alertDialog, promptDialog } from "../../lib/dialog";
import { useMe } from "../../context/Me";
import { money } from "./types";
import CustomFields from "../../components/CustomFields";
import AssetDrawer from "./AssetDrawer";
import CreateOrderDrawer from "./CreateOrderDrawer";
import SupplierPanel from "../clients/SupplierPanel";
import { useTerminology } from "../../lib/useTerminology";
import { useFormLayout, LayoutFields, reqStar, missingRequired, has } from "../../lib/formLayout";

const BASE_TABS = ["General info", "Devices & Assets", "Work orders", "Purchases", "Loyalty & Payout", "Communications"];

type Client = { id?: string; name?: string; phone?: string; email?: string; address?: string; source?: string;
  loyalty_tier?: string; discount_card?: string; currency_code?: string; balance?: number; order_count?: number; custom_data?: Record<string, any>;
  is_supplier?: boolean; supplier?: { id: string; contact_name?: string; lead_days?: number; currency?: string; balance?: number } | null;
  disc_services_mode?: string; disc_services_value?: number; disc_services_price_type_id?: string | null;
  disc_materials_mode?: string; disc_materials_value?: number; disc_materials_price_type_id?: string | null;
  devices?: { id: string; brand?: string; model?: string; serial?: string; imei?: string }[] };

function F({ l, v }: { l: string; v: string }) {
  return (
    <div><label className="block text-[11px] font-bold text-ink-tertiary mb-1">{l}</label>
      <div className="glass-input rounded-xl px-3 py-2.5 text-sm font-semibold text-ink-primary">{v || "—"}</div></div>
  );
}

export default function ClientDrawer({ woNum, clientId, onClose, onOpenAsset }: { woNum?: string; clientId: string; onClose: () => void; onOpenAsset?: () => void }) {
  const term = useTerminology();
  const [tab, setTab] = useState(0);
  const [assetOpen, setAssetOpen] = useState<string | null>(null);
  const [newWO, setNewWO] = useState(false);
  // Open the specific device the user clicked. Self-contained so it works standalone
  // (Clients hub) and from a WO; falls back to the caller's handler if provided.
  const openAsset = (id?: string) => { if (id) setAssetOpen(id); else onOpenAsset?.(); };
  const [c, setC] = useState<Client>({});
  const [cd, setCd] = useState<Record<string, any>>({});
  const [source, setSource] = useState("");
  const [disc, setDisc] = useState<any>({});
  const [biz, setBiz] = useState<any>({ tax_id: "", reg_no: "", billing_address: "" });
  const [busy, setBusy] = useState(false); const [toast, setToast] = useState<string | null>(null);
  const [isSupplier, setIsSupplier] = useState(false);   // Build 144am — Supplier role toggle
  const reload = () => api.get<Client>(`/clients/${clientId}`).then((r) => { setC(r); setIsSupplier(!!r.is_supplier); }).catch(() => {});
  useEffect(() => { api.get<Client>(`/clients/${clientId}`).then((r) => {
    setC(r); setCd(r.custom_data || {}); setSource((r as any).source || ""); setIsSupplier(!!r.is_supplier);
    setBiz({ tax_id: (r as any).tax_id || "", reg_no: (r as any).reg_no || "", billing_address: (r as any).billing_address || "", birthday: (r as any).birthday || "" });
    setDisc({
      disc_services_mode: r.disc_services_mode || "none", disc_services_value: r.disc_services_value || 0, disc_services_price_type_id: r.disc_services_price_type_id || "",
      disc_materials_mode: r.disc_materials_mode || "none", disc_materials_value: r.disc_materials_value || 0, disc_materials_price_type_id: r.disc_materials_price_type_id || "",
    });
  }).catch(() => {}); }, [clientId]);
  const initials = (c.name || "C").trim().split(/\s+/).slice(0, 2).map((w) => w[0] || "").join("").toUpperCase() || "C";
  const bal = c.balance || 0;
  const [creditOpen, setCreditOpen] = useState(false);
  const [creditVal, setCreditVal] = useState("");
  async function addCredit() {
    const amt = Number(creditVal);
    if (!creditVal || isNaN(amt) || amt <= 0) { setToast("Enter an amount greater than 0"); return; }
    setBusy(true);
    try {
      await api.post(`/clients/${clientId}/balance`, { kind: "advance", amount: amt, note: "Advance / credit" });
      await reload(); setToast("Credit added"); setCreditVal(""); setCreditOpen(false);
    } catch (e: any) { setToast(e.message || "Could not add credit"); }
    finally { setBusy(false); }
  }
  const layout = useFormLayout("client");
  async function save() {
    // Only what this screen edits is checked here (lead source + custom fields).
    const m = missingRequired(layout, { source: has(source) }, cd);
    if (m) { setToast(m); return; }
    setBusy(true);
    try {
      await api.patch(`/clients/${clientId}`, {
        custom_data: cd,
        source: source || null,
        is_supplier: isSupplier,
        tax_id: biz.tax_id || null, reg_no: biz.reg_no || null, billing_address: biz.billing_address || null,
        birthday: biz.birthday || null,
        ...disc,
        disc_services_price_type_id: disc.disc_services_price_type_id || null,
        disc_materials_price_type_id: disc.disc_materials_price_type_id || null,
      });
      // Turning on the Supplier role creates the mirrored supplier profile on the server;
      // stay open and reload so the Supplier tab appears instead of bouncing the user out.
      if (isSupplier && !c.supplier?.id) { setToast("Saved — supplier profile added"); await reload(); setBusy(false); return; }
      setToast("Saved"); setTimeout(() => onClose(), 700);
    }
    catch (e: any) { setToast(e.message || "Save failed"); setBusy(false); }
  }

  return (
    <div className="fixed inset-0 flex justify-end" style={{ zIndex: 50 }}>
      <div className="absolute inset-0 bg-ink-primary/35 backdrop-blur-[2px]" onClick={onClose} />
      <div className="relative h-full w-full max-w-[680px] bg-white shadow-clay-drawer flex flex-col border-l border-slate-200/90 animate-[slideIn_.3s_cubic-bezier(.16,1,.3,1)]">
        <style>{`@keyframes slideIn{from{transform:translateX(100%)}to{transform:translateX(0)}}`}</style>
        <header className="px-6 pt-4 border-b border-slate-200/90 bg-white/95 backdrop-blur">
          <div className="flex items-center gap-2 mb-3">
            <button onClick={onClose} className="flex items-center gap-1.5 text-xs font-semibold text-primary"><M n="arrow_back" c="text-sm" /> {woNum ? `Back to ${woNum}` : "Back"}</button>
            <span className="px-2 py-0.5 rounded-full text-[11px] font-bold bg-emerald-100 text-emerald-800 border border-emerald-200">Customer</span>
            {isSupplier && <span className="px-2 py-0.5 rounded-full text-[11px] font-bold bg-blue-100 text-blue-800 border border-blue-200 flex items-center gap-1"><M n="local_shipping" c="text-[13px]" />Supplier</span>}
            <div className="flex-1" />
            <button aria-label="Close" onClick={onClose} className="w-8 h-8 rounded-full flex items-center justify-center text-ink-tertiary hover:text-ink-primary hover:bg-slate-100"><M n="close" c="text-lg" /></button>
          </div>
          <div className="flex items-center gap-3 pb-3">
            <div className="w-11 h-11 rounded-full bg-primary grid place-items-center text-white font-bold shadow-clay-sm">{initials}</div>
            <div><div className="text-base font-bold text-ink-primary">{c.name || "Client"}</div><div className="text-xs text-ink-tertiary">{c.order_count ?? 0} work orders · {c.loyalty_tier?.toLowerCase() === "vip" ? "VIP" : humanize(c.loyalty_tier) || "Standard"}</div></div>
          </div>
          <div className="pb-3"><TagChips entityType="contact" entityId={clientId} /></div>
          <div className="flex gap-6 overflow-x-auto text-xs font-semibold">
            {(isSupplier ? [...BASE_TABS, "Supplier"] : BASE_TABS).map((t, i) => {
              const label = i === 1 && c.devices ? `Devices & Assets (${c.devices.length})` : i === 2 && c.order_count != null ? `Work orders (${c.order_count})` : t;
              return <button key={t} onClick={() => setTab(i)} className={`py-3 border-b-2 whitespace-nowrap transition-colors ${tab === i ? "text-primary border-primary font-bold" : "text-ink-secondary border-transparent hover:text-ink-primary"}`}>{label}</button>;
            })}
          </div>
        </header>
        <div className="flex-1 overflow-y-auto p-6 bg-[#f8faff] space-y-4">
          {tab === 0 ? (
            <>
              {/* Build 144am — one contact, switchable roles. Customer is always on; toggle
                  Supplier to unlock purchasing (a mirrored supplier profile + a Supplier tab). */}
              <div className="bg-white rounded-2xl border border-slate-200/90 p-4 shadow-sm">
                <div className="text-[11px] font-bold text-ink-tertiary mb-2">Roles</div>
                <div className="flex items-center gap-2">
                  <span className="px-3 py-1.5 rounded-xl text-xs font-bold bg-emerald-50 text-emerald-700 border border-emerald-200 flex items-center gap-1.5"><M n="check_circle" c="text-sm" />Customer</span>
                  <button type="button" onClick={() => setIsSupplier((v) => !v)}
                    className={`px-3 py-1.5 rounded-xl text-xs font-bold border flex items-center gap-1.5 transition-colors ${isSupplier ? "bg-blue-50 text-blue-700 border-blue-200" : "bg-white text-ink-tertiary border-slate-200 hover:bg-slate-50"}`}>
                    <M n={isSupplier ? "check_circle" : "add_circle"} c="text-sm" />Supplier</button>
                  <span className="text-[11px] text-ink-tertiary">{isSupplier ? "Buys & sells — appears in Contacts and Purchasing." : "Turn on to buy from this contact (purchase orders, price list)."}</span>
                </div>
              </div>
              <div className="bg-white rounded-2xl border border-slate-200/90 p-4 shadow-sm flex items-center justify-between">
                <div><span className="text-[11px] font-bold text-ink-tertiary">Account balance</span>
                  <div className="text-xl font-bold tabular-nums text-ink-primary">{money(Math.abs(bal))} <span className={`text-xs font-bold ${bal > 0 ? "text-rose-600" : "text-emerald-600"}`}>{bal > 0 ? "(Owes)" : "(Settled)"}</span></div></div>
                <div className="flex items-center gap-2">
                  {creditOpen ? (
                    <div className="flex items-center gap-1">
                      <input autoFocus value={creditVal} onChange={(e) => setCreditVal(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") addCredit(); if (e.key === "Escape") { setCreditOpen(false); setCreditVal(""); } }} inputMode="decimal" placeholder="Amount" className="w-24 glass-input rounded-lg px-2 py-1.5 text-xs tabular-nums" />
                      <button onClick={addCredit} disabled={busy} className="px-2.5 py-1.5 rounded-lg bg-primary text-white text-xs font-bold hover:bg-primary-container disabled:opacity-50">Add</button>
                      <button onClick={() => { setCreditOpen(false); setCreditVal(""); }} className="px-2 py-1.5 rounded-lg text-xs font-semibold text-ink-tertiary hover:text-ink-primary">Cancel</button>
                    </div>
                  ) : (
                    <button onClick={() => setCreditOpen(true)} className="px-3 py-1.5 rounded-lg border border-slate-200 text-xs font-bold text-ink-secondary hover:bg-slate-50">+ Add Credit</button>
                  )}
                  <button onClick={() => setNewWO(true)} className="px-3 py-1.5 rounded-lg bg-primary text-white text-xs font-bold shadow-clay-sm hover:brightness-110">+ New Work Order</button>
                </div>
              </div>
              {/* Built-in and custom fields in the order set in Settings → Forms → Customize. */}
              <LayoutFields layout={layout} blocks={{
                phone: { half: true, el: <F l="Phone number" v={c.phone || ""} /> },
                email: { half: true, el: <F l="Email address" v={c.email || ""} /> },
                source: { half: true, el: <div><label className="block text-[11px] font-bold text-ink-tertiary mb-1">Lead source{reqStar(layout, "source")}</label>
                  <DirectorySelect dirKey="lead_source" value={source} onChange={setSource} className="glass-input rounded-xl px-3 py-2.5 text-sm font-semibold text-ink-primary w-full" /></div> },
                loyalty_tier: { half: true, el: <F l="Loyalty tier" v={c.loyalty_tier || ""} /> },
                discount_card: { half: true, el: <F l="Discount card / Member ID" v={c.discount_card || ""} /> },
                address: { el: <F l="Billing & home address" v={c.address || ""} /> },
                notes: (c as any).notes ? { el: <F l="Notes" v={(c as any).notes} /> } : null,
              }} custom={(ids) => <CustomFields entity="client" only={ids} bare={!!ids} value={cd} onChange={setCd} creating={false} inputSize="sm" />} />
              <div className="grid grid-cols-2 gap-3">
                <F l="Orders" v={String(c.order_count ?? 0)} />
                {c.currency_code && <F l="Billing currency" v={c.currency_code} />}
              </div>
              <div className="bg-white rounded-2xl border border-slate-200/90 p-4 shadow-sm space-y-2.5">
                <div className="text-[11px] font-bold text-ink-tertiary">Billing details (for invoices)</div>
                <div className="grid grid-cols-2 gap-2.5">
                  <input value={biz.tax_id} onChange={(e) => setBiz({ ...biz, tax_id: e.target.value })} placeholder="Tax ID" className="glass-input rounded-xl px-3 py-2 text-sm" />
                  <input value={biz.reg_no} onChange={(e) => setBiz({ ...biz, reg_no: e.target.value })} placeholder="Registration no." className="glass-input rounded-xl px-3 py-2 text-sm" />
                </div>
                <input value={biz.billing_address} onChange={(e) => setBiz({ ...biz, billing_address: e.target.value })} placeholder="Billing address (if different)" className="glass-input rounded-xl px-3 py-2 text-sm w-full" />
                <div className="flex items-center gap-2">
                  <label className="text-[11px] font-bold text-ink-tertiary whitespace-nowrap">Birthday</label>
                  <DateField mode="date" value={biz.birthday || ""} onChange={(v) => setBiz({ ...biz, birthday: v })} />
                  <span className="text-[11px] text-ink-tertiary">used for birthday notifications</span>
                </div>
              </div>
              <DiscountMatrix disc={disc} onChange={setDisc} />
              <GdprCard clientId={clientId} onErased={onClose} />
              <div className="text-[11px] font-bold text-ink-tertiary mt-2">Registered {term.assets.toLowerCase()} on file</div>
              {(c.devices || []).length === 0 && <div className="text-xs text-ink-tertiary">No {term.assets.toLowerCase()} on file.</div>}
              {(c.devices || []).map((dv) => (
                <div key={dv.id} onClick={() => openAsset(dv.id)} className="bg-white rounded-xl border border-slate-200/90 p-3.5 shadow-sm flex items-center gap-3 cursor-pointer hover:border-primary/50 transition-all">
                  <span className="w-9 h-9 rounded-lg bg-purple-50 text-purple-600 grid place-items-center border border-purple-100"><M n="smartphone" c="text-base" /></span>
                  <div className="flex-1"><div className="text-xs font-bold text-ink-primary">{[dv.brand, dv.model].filter(Boolean).join(" ") || term.asset}</div><div className="text-[11px] text-ink-tertiary font-mono">SN: {dv.serial || "—"}{dv.imei ? ` · IMEI: ${dv.imei}` : ""}</div></div>
                  <M n="chevron_right" c="text-ink-tertiary text-base" />
                </div>
              ))}
            </>
          ) : tab === 1 ? (
            <DevicesTab devices={c.devices} onOpen={openAsset} />
          ) : tab === 2 ? (
            <WorkOrdersTab clientId={clientId} />
          ) : tab === 3 ? (
            <PurchasesTab clientId={clientId} />
          ) : tab === 4 ? (
            <LoyaltyTab clientId={clientId} />
          ) : tab === 5 ? (
            <CommsTab clientId={clientId} />
          ) : c.supplier?.id ? (
            <SupplierPanel supplierId={c.supplier.id} onSaved={reload} />
          ) : (
            <div className="text-sm text-ink-tertiary bg-white rounded-2xl border border-slate-200/90 p-6 text-center">Save to create this supplier profile, then its purchasing details appear here.</div>
          )}
        </div>
        <footer className="px-6 py-3.5 border-t border-slate-200/90 bg-white/95 flex items-center gap-2">
          <button className="text-xs font-semibold text-rose-600">Delete Client</button>
          <div className="ml-auto flex gap-2"><button onClick={onClose} className="px-3.5 py-2 rounded-xl text-xs font-bold text-ink-secondary bg-white border border-slate-200 hover:bg-slate-50">Cancel</button><button onClick={save} disabled={busy} className="px-4 py-2 rounded-xl bg-primary text-white text-xs font-bold shadow-clay-sm hover:brightness-110 disabled:opacity-60">{busy ? "Saving…" : "Save Client"}</button></div>
        </footer>
        {toast && <div className="absolute bottom-16 left-1/2 -translate-x-1/2 bg-ink-primary text-white text-xs font-semibold px-4 py-2.5 rounded-xl shadow-drawer">{toast}</div>}
      </div>
      {assetOpen && <AssetDrawer assetId={assetOpen} onClose={() => setAssetOpen(null)} />}
      {newWO && <CreateOrderDrawer prefillClient={{ id: clientId, name: c.name, phone: c.phone, email: c.email }} onClose={() => setNewWO(false)} onCreated={() => { setNewWO(false); reload(); }} />}
    </div>
  );
}

const d = (iso?: string) => (iso ? new Date(iso).toLocaleDateString([], { day: "2-digit", month: "short", year: "numeric" }) : "—");
const Empty = ({ t }: { t: string }) => <div className="grid place-items-center h-48 text-center text-sm font-semibold text-ink-tertiary">{t}</div>;
const Loading = () => <div className="text-xs text-ink-tertiary py-4">Loading…</div>;

function DevicesTab({ devices, onOpen }: { devices?: any[]; onOpen: (id?: string) => void }) {
  const term = useTerminology();
  if (!devices || devices.length === 0) return <Empty t={`No ${term.assets.toLowerCase()} on file.`} />;
  return (
    <div className="space-y-2">
      {devices.map((dv) => (
        <div key={dv.id} onClick={() => onOpen(dv.id)} className="bg-white rounded-xl border border-slate-200/90 p-3.5 shadow-sm flex items-center gap-3 cursor-pointer hover:border-primary/50 transition-all">
          <span className="w-9 h-9 rounded-lg bg-purple-50 text-purple-600 grid place-items-center border border-purple-100"><M n="smartphone" c="text-base" /></span>
          <div className="flex-1"><div className="text-xs font-bold text-ink-primary">{[dv.brand, dv.model].filter(Boolean).join(" ") || term.asset}</div><div className="text-[11px] text-ink-tertiary font-mono">SN: {dv.serial || "—"}{dv.imei ? ` · IMEI: ${dv.imei}` : ""}</div></div>
          <M n="chevron_right" c="text-ink-tertiary text-base" />
        </div>
      ))}
    </div>
  );
}

function WorkOrdersTab({ clientId }: { clientId: string }) {
  const [rows, setRows] = useState<any[] | null>(null);
  useEffect(() => { api.get<any>(`/orders?client_id=${clientId}&limit=100`).then((r) => setRows(r.items || [])).catch(() => setRows([])); }, [clientId]);
  if (!rows) return <Loading />;
  if (rows.length === 0) return <Empty t="No work orders for this client yet." />;
  return (
    <div className="space-y-2">
      {rows.map((o) => (
        <div key={o.id} className="bg-white rounded-xl border border-slate-200/90 p-3.5 shadow-sm flex items-center gap-3">
          <div className="flex-1"><div className="text-xs font-bold text-ink-primary">{o.order_number || o.code || "Order"}</div><div className="text-[11px] text-ink-tertiary">{d(o.created_at)}{o.device_name ? ` · ${o.device_name}` : ""}</div></div>
          <span className="px-2 py-0.5 rounded-full text-[11px] font-bold bg-slate-100 text-ink-secondary border border-slate-200 capitalize">{(o.status || "—").replace(/_/g, " ")}</span>
          <span className="tabular-nums font-bold text-ink-primary text-xs">{money(o.total ?? o.grand_total ?? 0)}</span>
        </div>
      ))}
    </div>
  );
}

function PurchasesTab({ clientId }: { clientId: string }) {
  const [data, setData] = useState<any>(null);
  useEffect(() => { api.get<any>(`/clients/${clientId}/purchases`).then(setData).catch(() => setData({ items: [], summary: {} })); }, [clientId]);
  if (!data) return <Loading />;
  const items = data.items || []; const s = data.summary || {};
  return (
    <div className="space-y-3">
      <div className="grid grid-cols-3 gap-2">
        <div className="bg-white rounded-xl border border-slate-200/90 p-3 shadow-sm"><div className="text-[11px] font-bold text-ink-tertiary">Total spent</div><div className="text-base font-bold tabular-nums text-ink-primary">{money(s.total_spent || 0)}</div></div>
        <div className="bg-white rounded-xl border border-slate-200/90 p-3 shadow-sm"><div className="text-[11px] font-bold text-ink-tertiary">Items</div><div className="text-base font-bold tabular-nums text-ink-primary">{s.item_count ?? 0}</div></div>
        <div className="bg-white rounded-xl border border-slate-200/90 p-3 shadow-sm"><div className="text-[11px] font-bold text-ink-tertiary">Transactions</div><div className="text-base font-bold tabular-nums text-ink-primary">{s.transaction_count ?? 0}</div></div>
      </div>
      {items.length === 0 ? <Empty t="Nothing purchased yet." /> : (
        <div className="bg-white rounded-xl border border-slate-200/90 shadow-sm overflow-hidden">
          <table className="w-full text-left text-xs">
            <thead><tr className="bg-slate-50/60 text-[11px] font-bold text-ink-tertiary "><th className="py-2 px-3">Date</th><th className="py-2 px-3">Item</th><th className="py-2 px-3 text-right">Qty</th><th className="py-2 px-3 text-right">Total</th></tr></thead>
            <tbody className="divide-y divide-slate-100">
              {items.map((it: any, i: number) => (
                <tr key={i}><td className="py-2 px-3 text-ink-tertiary whitespace-nowrap">{d(it.date)}</td><td className="py-2 px-3"><span className="font-semibold text-ink-primary">{it.item}</span> <span className="text-[11px] text-ink-tertiary">({it.kind})</span>{it.source_ref ? <span className="block text-[11px] text-ink-tertiary">{it.source_ref}</span> : null}</td><td className="py-2 px-3 text-right font-mono">{it.qty}</td><td className="py-2 px-3 text-right font-mono font-bold text-ink-primary">{money(it.total)}</td></tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function LoyaltyTab({ clientId }: { clientId: string }) {
  const [loy, setLoy] = useState<any>(null);
  const [coupons, setCoupons] = useState<any[] | null>(null);
  const [bal, setBal] = useState<any>(null);
  const [cards, setCards] = useState<any[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [creditOpen, setCreditOpen] = useState(false);
  const [creditVal, setCreditVal] = useState("");
  function reload() {
    api.get<any>(`/clients/${clientId}/loyalty`).then(setLoy).catch(() => setLoy({}));
    api.get<any>(`/clients/${clientId}/coupons`).then((r) => setCoupons(r.items || [])).catch(() => setCoupons([]));
    api.get<any>(`/clients/${clientId}/balance`).then(setBal).catch(() => setBal({}));
    api.get<any>(`/giftcards`).then((r) => setCards((r.items || []).filter((g: any) => g.client_id === clientId))).catch(() => setCards([]));
  }
  async function issueCard() {
    const val = await promptDialog("Gift card value:"); if (!val || isNaN(Number(val)) || Number(val) <= 0) return;
    // Optional validity date — blank = never expires. Code is generated server-side (always unique).
    const exp = await promptDialog("Expiry date (YYYY-MM-DD) — leave blank for no expiry:", { defaultValue: "" });
    let expires_at: string | null = null;
    if (exp && exp.trim()) { const d = new Date(exp.trim()); if (isNaN(d.getTime())) { await alertDialog("Invalid date"); return; } expires_at = d.toISOString(); }
    setBusy(true);
    try { await api.post(`/giftcards`, { value: Number(val), client_id: clientId, expires_at }); reload(); }
    catch (e: any) { await alertDialog(e.message || "Could not issue gift card"); } finally { setBusy(false); }
  }
  useEffect(reload, [clientId]);
  async function genCoupon() {
    const val = await promptDialog("Discount % for this coupon (e.g. 10):"); if (!val || isNaN(Number(val))) return;
    setBusy(true);
    try { await api.post(`/clients/${clientId}/coupons`, { kind: "pct", value: Number(val), name: "Returning customer", max_uses: 1 }); reload(); }
    catch (e: any) { await alertDialog(e.message || "Could not create coupon"); } finally { setBusy(false); }
  }
  async function addCredit() {
    const amt = Number(creditVal);
    if (!creditVal || isNaN(amt) || amt <= 0) return;
    setBusy(true);
    try { await api.post(`/clients/${clientId}/balance`, { kind: "advance", amount: amt, note: "Advance / credit" }); setCreditVal(""); setCreditOpen(false); reload(); }
    catch (e: any) { await alertDialog(e.message || "Could not add credit"); } finally { setBusy(false); }
  }
  if (!loy || !coupons || !bal || !cards) return <Loading />;
  const sym = bal.currency?.symbol || "";
  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 gap-2">
        <div className="bg-white rounded-xl border border-slate-200/90 p-3 shadow-sm"><div className="text-[11px] font-bold text-ink-tertiary">Loyalty points</div><div className="text-lg font-bold tabular-nums text-ink-primary">{loy.enabled ? Math.round(loy.points || 0) : "—"}</div><div className="text-[11px] text-ink-tertiary">{loy.enabled ? `${Math.round(loy.lifetime_points || 0)} lifetime · ${loy.tier || "Standard"}` : "Loyalty not configured"}</div></div>
        <div className="bg-white rounded-xl border border-slate-200/90 p-3 shadow-sm flex flex-col"><div className="text-[11px] font-bold text-ink-tertiary">Account balance</div><div className="text-lg font-bold tabular-nums text-ink-primary">{sym}{Math.abs(bal.balance || 0).toLocaleString()} <span className={`text-[11px] font-bold ${(bal.balance || 0) > 0 ? "text-rose-600" : "text-emerald-600"}`}>{(bal.balance || 0) > 0 ? "(Owes)" : "(Settled)"}</span></div>{creditOpen ? (<div className="mt-auto flex items-center gap-1"><input autoFocus value={creditVal} onChange={(e) => setCreditVal(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") addCredit(); if (e.key === "Escape") { setCreditOpen(false); setCreditVal(""); } }} inputMode="decimal" placeholder="Amount" className="w-20 glass-input rounded-lg px-2 py-1 text-[11px] tabular-nums" /><button onClick={addCredit} disabled={busy} className="px-2 py-1 rounded-lg bg-primary text-white text-[11px] font-bold disabled:opacity-50">Add</button><button onClick={() => { setCreditOpen(false); setCreditVal(""); }} className="text-[11px] font-semibold text-ink-tertiary">✕</button></div>) : (<button onClick={() => setCreditOpen(true)} disabled={busy} className="mt-auto self-start text-[11px] font-bold text-primary">+ Add credit</button>)}</div>
      </div>
      <div className="flex items-center justify-between"><div className="text-[11px] font-bold text-ink-tertiary">Coupons</div><button onClick={genCoupon} disabled={busy} className="px-2.5 py-1 rounded-lg bg-primary text-white text-[11px] font-bold shadow-clay-sm disabled:opacity-50">+ Generate</button></div>
      {coupons.length === 0 ? <div className="text-xs text-ink-tertiary">No coupons issued to this client.</div> : coupons.map((c) => (
        <div key={c.id} className="bg-white rounded-xl border border-slate-200/90 p-3 shadow-sm flex items-center gap-2">
          <span className="font-mono font-bold text-primary text-xs">{c.code}</span>
          <span className="text-[11px] text-ink-secondary">{c.kind === "pct" ? `${c.value}% off` : `${sym}${c.value} off`}{c.name ? ` · ${c.name}` : ""}</span>
          <div className="flex-1" />
          <span className={`px-2 py-0.5 rounded-full text-[11px] font-bold border ${c.spent ? "bg-slate-100 text-ink-tertiary border-slate-200" : c.active ? "bg-emerald-100 text-emerald-700 border-emerald-200" : "bg-rose-100 text-rose-700 border-rose-200"}`}>{c.spent ? "Used" : c.active ? "Active" : "Inactive"}</span>
        </div>
      ))}
      <div className="flex items-center justify-between pt-1"><div className="text-[11px] font-bold text-ink-tertiary">Gift cards</div><button onClick={issueCard} disabled={busy} className="px-2.5 py-1 rounded-lg bg-primary text-white text-[11px] font-bold shadow-clay-sm disabled:opacity-50">+ Issue</button></div>
      {cards.length === 0 ? <div className="text-xs text-ink-tertiary">No gift cards for this client.</div> : cards.map((g) => (
        <div key={g.id} className="bg-white rounded-xl border border-slate-200/90 p-3 shadow-sm flex items-center gap-2">
          <span className="font-mono font-bold text-primary text-xs">{g.code}</span>
          <span className="text-[11px] text-ink-secondary">{sym}{(g.balance ?? 0).toLocaleString()} left <span className="text-ink-tertiary">of {sym}{(g.initial_value ?? 0).toLocaleString()}</span>{g.expires_at ? ` · exp ${d(g.expires_at)}` : ""}</span>
          <div className="flex-1" />
          <span className={`px-2 py-0.5 rounded-full text-[11px] font-bold border ${(g.balance ?? 0) <= 0 ? "bg-slate-100 text-ink-tertiary border-slate-200" : g.active ? "bg-emerald-100 text-emerald-700 border-emerald-200" : "bg-rose-100 text-rose-700 border-rose-200"}`}>{(g.balance ?? 0) <= 0 ? "Spent" : g.active ? "Active" : "Inactive"}</span>
        </div>
      ))}
    </div>
  );
}

function CommsTab({ clientId }: { clientId: string }) {
  const [msgs, setMsgs] = useState<any[] | null>(null);
  const [templates, setTemplates] = useState<any[]>([]);
  const [channel, setChannel] = useState("email");
  const [body, setBody] = useState("");
  const [busy, setBusy] = useState(false);
  function reload() { api.get<any>(`/clients/${clientId}/communication`).then((r) => setMsgs(r.items || [])).catch(() => setMsgs([])); }
  useEffect(() => { reload(); api.get<any>(`/clients/message-templates/all`).then((r) => setTemplates(r.items || [])).catch(() => {}); }, [clientId]);
  async function send() {
    if (!body.trim()) return; setBusy(true);
    try { await api.post(`/clients/${clientId}/communication`, { channel, body }); setBody(""); reload(); }
    catch (e: any) { await alertDialog(e.message || "Could not send"); } finally { setBusy(false); }
  }
  async function suggest() {
    setBusy(true);
    try { const r = await api.post<any>(`/clients/${clientId}/communication/suggest`, {}); setBody(r.reply || ""); }
    catch { /* ignore */ } finally { setBusy(false); }
  }
  const sel = "glass-input rounded-lg px-2 py-1.5 text-xs font-semibold text-ink-primary";
  return (
    <div className="space-y-3">
      <div className="bg-white rounded-xl border border-slate-200/90 p-3 shadow-sm space-y-2">
        <div className="flex items-center gap-2">
          <select value={channel} onChange={(e) => setChannel(e.target.value)} className={sel}>{["email", "sms", "whatsapp", "facebook", "instagram", "call"].map((c) => <option key={c} value={c}>{c}</option>)}</select>
          <select value="" onChange={(e) => { const t = templates.find((x) => x.id === e.target.value); if (t) setBody(t.body); }} className={`${sel} flex-1`}><option value="">Insert template…</option>{templates.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}</select>
          <button onClick={suggest} disabled={busy} className="px-2.5 py-1.5 rounded-lg border border-slate-200 text-[11px] font-bold text-ink-secondary hover:bg-slate-50 flex items-center gap-1"><M n="auto_awesome" c="text-xs" />Suggest</button>
        </div>
        <textarea value={body} onChange={(e) => setBody(e.target.value)} placeholder="Write a message…" className="glass-input w-full rounded-lg px-3 py-2 text-sm text-ink-primary h-20 resize-none" />
        <div className="flex justify-end"><button onClick={send} disabled={busy || !body.trim()} className="px-4 py-1.5 rounded-lg bg-primary text-white text-xs font-bold shadow-clay-sm disabled:opacity-50">{busy ? "…" : "Send"}</button></div>
      </div>
      {!msgs ? <Loading /> : msgs.length === 0 ? <Empty t="No messages exchanged yet." /> : (
        <div className="space-y-2">
          {msgs.map((m) => (
            <div key={m.id} className={`rounded-xl border p-3 shadow-sm ${m.direction === "outbound" ? "bg-blue-50/60 border-blue-100 ml-6" : "bg-white border-slate-200/90 mr-6"}`}>
              <div className="flex items-center gap-2 mb-1 text-[11px] font-bold text-ink-tertiary"><span className="px-1.5 py-0.5 rounded bg-slate-100">{m.channel || "—"}</span><span>{m.direction}</span>{m.author ? <span>· {m.author}</span> : null}<div className="flex-1" /><span>{d(m.created_at)}</span></div>
              <div className="text-xs text-ink-primary whitespace-pre-wrap">{m.body}</div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// Privacy & GDPR — export / consent / erase. Gated to can_manage_settings (matches the backend). Erase is destructive → double-confirm.
function GdprCard({ clientId, onErased }: { clientId: string; onErased: () => void }) {
  const { can } = useMe();
  const [consent, setConsent] = useState<Record<string, boolean>>({});
  const [busy, setBusy] = useState(false);
  const [confirmErase, setConfirmErase] = useState(false);
  useEffect(() => {
    api.get<any>(`/compliance/gdpr/client/${clientId}/consent`).then((r) => {
      const latest: Record<string, boolean> = {};
      (r.items || []).forEach((it: any) => { if (!(it.channel in latest)) latest[it.channel] = it.granted; }); // items newest-first
      setConsent(latest);
    }).catch(() => {});
  }, [clientId]);
  if (!can("can_manage_settings")) return null;
  async function setC(channel: string, granted: boolean) {
    setConsent((c) => ({ ...c, [channel]: granted }));
    try { await api.post(`/compliance/gdpr/client/${clientId}/consent`, { channel, granted, source: "staff" }); } catch { /* ignore */ }
  }
  async function erase() {
    setBusy(true);
    try { await api.post(`/compliance/gdpr/client/${clientId}/erase`, {}); onErased(); }
    catch (e: any) { await alertDialog(e.message || "Erase failed"); setBusy(false); }
  }
  const Toggle = ({ ch, label }: { ch: string; label: string }) => (
    <label className="flex items-center gap-2 text-xs font-semibold text-ink-secondary cursor-pointer">
      <input type="checkbox" checked={!!consent[ch]} onChange={(e) => setC(ch, e.target.checked)} className="accent-primary" />{label}
    </label>
  );
  return (
    <div className="bg-white rounded-2xl border border-slate-200/90 p-4 shadow-sm space-y-3">
      <div className="text-[11px] font-bold text-ink-tertiary flex items-center gap-1.5"><M n="privacy_tip" c="text-sm" />Privacy &amp; GDPR</div>
      <div className="flex flex-wrap items-center gap-4">
        <Toggle ch="email" label="Email consent" />
        <Toggle ch="phone" label="Phone consent" />
        <Toggle ch="marketing" label="Marketing consent" />
      </div>
      <div className="flex items-center gap-2 pt-1">
        <button onClick={() => openAuthedDoc(`/compliance/gdpr/client/${clientId}/export`).catch(() => alertDialog("Export failed"))} className="px-3 py-1.5 rounded-lg border border-slate-200 bg-white text-ink-secondary hover:bg-slate-50 text-xs font-bold flex items-center gap-1"><M n="download" c="text-sm" />Export data</button>
        {!confirmErase
          ? <button onClick={() => setConfirmErase(true)} className="px-3 py-1.5 rounded-lg border border-rose-200 bg-white text-rose-600 hover:bg-rose-50 text-xs font-bold flex items-center gap-1"><M n="delete_forever" c="text-sm" />Erase (GDPR)</button>
          : <span className="flex items-center gap-1.5"><span className="text-[11px] font-semibold text-rose-600">Permanently scrub PII?</span><button onClick={erase} disabled={busy} className="px-2.5 py-1.5 rounded-lg bg-rose-600 text-white text-xs font-bold disabled:opacity-50">{busy ? "…" : "Yes, erase"}</button><button onClick={() => setConfirmErase(false)} className="px-2.5 py-1.5 rounded-lg border border-slate-200 text-ink-secondary text-xs font-semibold">Cancel</button></span>}
      </div>
    </div>
  );
}

// Contact discount matrix (Orderry "Managing contact discounts"): a discount on
// services and on materials, each either a fixed % or a chosen price type. The
// backend applies these automatically when lines are added to the client's orders,
// invoices and sales (see _apply_client_discount).
function DiscountMatrix({ disc, onChange }: { disc: any; onChange: (d: any) => void }) {
  const [types, setTypes] = useState<any[]>([]);
  useEffect(() => { api.get<any>("/pricing/types").then((r) => setTypes(r.items || r || [])).catch(() => setTypes([])); }, []);
  const set = (k: string, v: any) => onChange({ ...disc, [k]: v });
  // Effective-price preview on a $100-cost sample item, so staff see the real
  // outcome of "by price type" instead of guessing (Orderry shows nothing here).
  const preview = (mode: string, mk: string): string | null => {
    if (mode === "percent") {
      const v = Number(disc[`${mk}_value`] || 0);
      return v > 0 ? `A $100 item bills at $${(100 * (1 - v / 100)).toFixed(2)} (−${v}%).` : null;
    }
    if (mode === "price_type") {
      const t = types.find((x) => x.id === disc[`${mk}_price_type_id`]);
      if (!t) return null;
      const m = Number(t.markup_pct || 0);
      return `A $100-cost item bills this client at $${(100 * (1 + m / 100)).toFixed(2)} (${t.name}, ${m}% markup).`;
    }
    return null;
  };
  const Row = ({ label, mk }: { label: string; mk: string }) => {
    const mode = disc[`${mk}_mode`] || "none";
    const hint = preview(mode, mk);
    return (
      <div className="space-y-1">
        <div className="grid grid-cols-12 gap-2 items-center">
          <div className="col-span-4 text-xs font-semibold text-ink-secondary">{label}</div>
          <select value={mode} onChange={(e) => set(`${mk}_mode`, e.target.value)} className="col-span-4 glass-input px-2 py-1.5 rounded-lg text-xs">
            <option value="none">No discount</option><option value="percent">Fixed %</option><option value="price_type">By price type</option>
          </select>
          {mode === "percent" && (
            <div className="col-span-4 flex items-center gap-1"><input type="number" min={0} max={100} value={disc[`${mk}_value`] ?? 0} onChange={(e) => set(`${mk}_value`, Number(e.target.value))} className="glass-input px-2 py-1.5 rounded-lg text-xs w-full" /><span className="text-xs text-ink-tertiary">%</span></div>
          )}
          {mode === "price_type" && (
            <select value={disc[`${mk}_price_type_id`] || ""} onChange={(e) => set(`${mk}_price_type_id`, e.target.value)} className="col-span-4 glass-input px-2 py-1.5 rounded-lg text-xs">
              <option value="">Select price…</option>{types.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
            </select>
          )}
          {mode === "none" && <div className="col-span-4" />}
        </div>
        {hint && <p className="text-[11px] text-ink-tertiary pl-[34%]">{hint}</p>}
      </div>
    );
  };
  return (
    <div className="bg-white rounded-2xl border border-slate-200/90 p-4 shadow-sm space-y-3">
      <div><h5 className="text-xs font-bold text-ink-tertiary">Pricing &amp; discounts</h5>
        <p className="text-[11px] text-ink-tertiary mt-0.5">Applied automatically to this client's orders, invoices and sales.</p></div>
      <Row label="Services / labour" mk="disc_services" />
      <Row label="Materials / products" mk="disc_materials" />
    </div>
  );
}
