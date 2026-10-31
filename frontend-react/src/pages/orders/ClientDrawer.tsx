import DirectorySelect from "../../components/DirectorySelect";
import { humanize } from "../../lib/humanize";
import { balanceView } from "../../lib/balance";
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import Section, { sectionCard } from "../../components/Section";
import TagChips from "../../components/TagChips";
import CallButton from "../../components/CallButton";
import { M } from "../../components/Shell";
import DateField from "../../components/DateField";
import Switch from "../../components/Switch";
import { fmtDay } from "../../lib/serverTime";
import { api, openAuthedDoc } from "../../api/client";
import { alertDialog, confirmDialog } from "../../lib/dialog";
import { useMe } from "../../context/Me";
import { money } from "./types";
import AddressFields, { ADDRESS_KEYS, addressPayload, type Address } from "../../components/AddressFields";
import CustomFields from "../../components/CustomFields";
import AssetDrawer from "./AssetDrawer";
import CreateOrderDrawer from "./CreateOrderDrawer";
import SupplierPanel from "../clients/SupplierPanel";
import { useTerminology } from "../../lib/useTerminology";
import { useFormLayout, LayoutFields, reqStar, missingRequired, has } from "../../lib/formLayout";
import { ClientCreditNotes } from "../invoices/CreditNotesList";
import { MembershipsCard } from "../memberships/shared";
import { MembershipDrawer } from "../memberships/MembershipsView";

const BASE_TABS = ["General info", "Devices & assets", "Work orders", "Purchases", "Loyalty & payout", "Communications"];
const LABEL = "text-xs font-medium text-ink-secondary";
const INPUT = "glass-input rounded-xl px-3 py-2 text-sm";
const GHOST = "h-9 px-3.5 rounded-xl bg-white border border-slate-200 text-xs font-semibold text-ink-secondary hover:bg-slate-50 disabled:opacity-50 inline-flex items-center gap-1.5";
const CHANNELS: [string, string][] = [["email", "Email"], ["sms", "SMS"], ["whatsapp", "WhatsApp"], ["facebook", "Facebook"], ["instagram", "Instagram"], ["call", "Call"]];
const channelLabel = (k?: string) => CHANNELS.find(([c]) => c === k)?.[1] || (k ? humanize(k) : "Other");
const DIRECTION: Record<string, string> = { outbound: "Sent", inbound: "Received" };

type Client = { id?: string; name?: string; phone?: string; email?: string; address?: string; source?: string;
  loyalty_tier?: string; discount_card?: string; currency_code?: string; balance?: number; order_count?: number; custom_data?: Record<string, any>;
  is_supplier?: boolean; supplier?: { id: string; contact_name?: string; lead_days?: number; currency?: string; balance?: number } | null;
  disc_services_mode?: string; disc_services_value?: number; disc_services_price_type_id?: string | null;
  disc_materials_mode?: string; disc_materials_value?: number; disc_materials_price_type_id?: string | null;
  devices?: { id: string; brand?: string; model?: string; serial?: string; imei?: string }[] };

// A read-only value: plain label and text, so it doesn't look like a field you can type in.
function F({ l, v }: { l: string; v: string }) {
  return (
    <div><div className={`${LABEL} mb-1`}>{l}</div>
      {v ? <div className="text-sm font-semibold text-ink-primary whitespace-pre-wrap break-words">{v}</div>
        : <div className="text-sm text-ink-tertiary">Not set</div>}</div>
  );
}

export default function ClientDrawer({ woNum, clientId, onClose, onOpenAsset }: { woNum?: string; clientId: string; onClose: () => void; onOpenAsset?: () => void }) {
  const { can } = useMe();
  const nav = useNavigate();
  // Open a work order in the work orders hub (it reads ?open=<id>), and close this drawer.
  const openOrder = (id: string) => { onClose(); nav(`/orders?open=${encodeURIComponent(id)}`); };
  const [tab, setTab] = useState(0);
  const [assetOpen, setAssetOpen] = useState<string | null>(null);
  const [planOpen, setPlanOpen] = useState<string | null>(null);
  const [newWO, setNewWO] = useState<null | "order" | "estimate">(null);
  // Open the specific device the user clicked. Self-contained so it works standalone
  // (Clients hub) and from a WO; falls back to the caller's handler if provided.
  const openAsset = (id?: string) => { if (id) setAssetOpen(id); else onOpenAsset?.(); };
  const [c, setC] = useState<Client>({});
  const [cd, setCd] = useState<Record<string, any>>({});
  const [source, setSource] = useState("");
  const [disc, setDisc] = useState<any>({});
  const [biz, setBiz] = useState<any>({ tax_id: "", reg_no: "", billing_address: "" });
  const [addr, setAddr] = useState<Address>({});
  const [addr0, setAddr0] = useState<Address>({});
  const [busy, setBusy] = useState(false); const [toast, setToast] = useState<string | null>(null);
  const [isSupplier, setIsSupplier] = useState(false);   // Build 144am — Supplier role toggle
  const reload = () => api.get<Client>(`/clients/${clientId}`).then((r) => { setC(r); setIsSupplier(!!r.is_supplier); }).catch(() => {});
  useEffect(() => { api.get<Client>(`/clients/${clientId}`).then((r) => {
    setC(r); setCd(r.custom_data || {}); setSource((r as any).source || ""); setIsSupplier(!!r.is_supplier);
    setBiz({ tax_id: (r as any).tax_id || "", reg_no: (r as any).reg_no || "", billing_address: (r as any).billing_address || "", birthday: (r as any).birthday || "" });
    const parts = Object.fromEntries(ADDRESS_KEYS.map((k) => [k, (r as any)[k] || ""])) as Address;
    setAddr(parts); setAddr0(parts);
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
    } catch (e: any) { setToast(e.message || "Couldn't add the credit. Check the amount and try again."); }
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
        // WS-C: send the address parts only when they were edited, so an old one-line address stays as it is
        ...(JSON.stringify(addressPayload(addr)) !== JSON.stringify(addressPayload(addr0)) ? addressPayload(addr) : {}),
        ...disc,
        disc_services_price_type_id: disc.disc_services_price_type_id || null,
        disc_materials_price_type_id: disc.disc_materials_price_type_id || null,
      });
      // Turning on the Supplier role creates the mirrored supplier profile on the server;
      // stay open and reload so the Supplier tab appears instead of bouncing the user out.
      if (isSupplier && !c.supplier?.id) { setToast("Saved. Supplier profile added."); await reload(); setBusy(false); return; }
      setToast("Saved"); setTimeout(() => onClose(), 700);
    }
    catch (e: any) { setToast(e.message || "Couldn't save. Check the details and try again."); setBusy(false); }
  }

  // Customers with history (jobs, invoices, payments, devices) can't be deleted; the server says so
  // and points to Merge instead, which we show as is.
  async function removeClient() {
    if (!(await confirmDialog("This removes the customer for good. Customers with work orders, invoices or payments can't be deleted; merge them into another customer instead.",
      { title: "Delete this customer?", confirmLabel: "Delete", danger: true }))) return;
    try { await api.del(`/clients/${clientId}`); onClose(); }
    catch (e: any) { await alertDialog(e?.message || "Couldn't delete this customer. Try again, or merge them into another customer.", "Can't delete"); }
  }

  return (
    <div className="fixed inset-0 flex justify-end" style={{ zIndex: 50 }}>
      <div className="absolute inset-0 bg-ink-primary/30" onClick={onClose} />
      <div className="relative h-full w-full max-w-[680px] bg-white shadow-clay-drawer flex flex-col border-l border-slate-200/90 animate-[slideIn_.3s_cubic-bezier(.16,1,.3,1)]">
        <style>{`@keyframes slideIn{from{transform:translateX(100%)}to{transform:translateX(0)}}`}</style>
        <header className="px-6 pt-4 border-b border-slate-200/90 bg-white">
          <div className="flex items-center gap-2 mb-3">
            <button onClick={onClose} className="flex items-center gap-1.5 text-xs font-semibold text-primary"><M n="arrow_back" c="text-sm" /> {woNum ? `Back to ${woNum}` : "Back"}</button>
            <span className="px-2 py-0.5 rounded-full text-[11px] font-bold bg-emerald-100 text-emerald-800 border border-emerald-200">Customer</span>
            {isSupplier && <span className="px-2 py-0.5 rounded-full text-[11px] font-bold bg-blue-100 text-blue-800 border border-blue-200 flex items-center gap-1"><M n="local_shipping" c="text-[13px]" />Supplier</span>}
            {c.source === "call" && <span className="px-2 py-0.5 rounded-full text-[11px] font-bold bg-slate-100 text-ink-secondary border border-slate-200 flex items-center gap-1"><M n="smartphone" c="text-[13px]" />Created from call</span>}
            <div className="flex-1" />
            <CallButton phone={c.phone} clientId={clientId} />
            <button aria-label="Close" onClick={onClose} className="w-8 h-8 rounded-full flex items-center justify-center text-ink-tertiary hover:text-ink-primary hover:bg-slate-100"><M n="close" c="text-lg" /></button>
          </div>
          <div className="flex items-center gap-3 pb-3">
            <div className="w-11 h-11 rounded-full bg-primary grid place-items-center text-white font-bold shadow-clay-sm">{initials}</div>
            <div><div className="text-base font-bold text-ink-primary">{c.name || "Customer"}</div><div className="text-xs text-ink-tertiary">{c.order_count ?? 0} work orders · {c.loyalty_tier?.toLowerCase() === "vip" ? "VIP" : humanize(c.loyalty_tier) || "Standard"}</div></div>
          </div>
          <div className="pb-3"><TagChips entityType="contact" entityId={clientId} /></div>
          <div className="flex gap-6 overflow-x-auto text-xs font-semibold">
            {(isSupplier ? [...BASE_TABS, "Supplier"] : BASE_TABS).map((t, i) => {
              const label = i === 1 && c.devices ? `Devices & assets (${c.devices.length})` : i === 2 && c.order_count != null ? `Work orders (${c.order_count})` : t;
              return <button key={t} onClick={() => setTab(i)} className={`py-3 border-b-2 whitespace-nowrap transition-colors ${tab === i ? "text-primary border-primary font-bold" : "text-ink-secondary border-transparent hover:text-ink-primary"}`}>{label}</button>;
            })}
          </div>
        </header>
        <div className="flex-1 overflow-y-auto p-6 bg-canvas space-y-4">
          {tab === 0 ? (
            <>
              {/* Memberships: the client's plans across their boats, vehicles and devices */}
              <MembershipsCard clientId={clientId} devices={c.devices} onOpen={setPlanOpen} />
              {planOpen && <MembershipDrawer id={planOpen} onClose={() => setPlanOpen(null)} />}
              {/* Build 144am — one contact, switchable roles. Customer is always on; toggle
                  Supplier to unlock purchasing (a mirrored supplier profile + a Supplier tab). */}
              <div className={`${sectionCard} p-4`}>
                <div className={`${LABEL} mb-2`}>Roles</div>
                <div className="flex items-center gap-2">
                  <span className="px-3 py-1.5 rounded-xl text-xs font-bold bg-emerald-50 text-emerald-700 border border-emerald-200 flex items-center gap-1.5"><M n="check_circle" c="text-sm" />Customer</span>
                  <button type="button" onClick={() => setIsSupplier((v) => !v)}
                    className={`px-3 py-1.5 rounded-xl text-xs font-bold border flex items-center gap-1.5 transition-colors ${isSupplier ? "bg-blue-50 text-blue-700 border-blue-200" : "bg-white text-ink-tertiary border-slate-200 hover:bg-slate-50"}`}>
                    <M n={isSupplier ? "check_circle" : "add_circle"} c="text-sm" />Supplier</button>
                  <span className="text-[11px] text-ink-tertiary">{isSupplier ? "Also a supplier, so they show in Purchasing too." : "Turn on to buy from this customer with purchase orders and a price list."}</span>
                </div>
              </div>
              <div className={`${sectionCard} p-4 space-y-3`}>
                <div><span className={LABEL}>Account balance</span>
                  <div className="text-xl font-bold tabular-nums text-ink-primary">{money(balanceView(bal).amount)} <span className={`text-xs font-bold ${balanceView(bal).className}`}>({balanceView(bal).label})</span></div></div>
                <div className="flex flex-wrap items-center gap-2">
                  {can("can_create_tickets") && <button onClick={() => setNewWO("order")} className={GHOST}><M n="add" c="text-sm" />New work order</button>}
                  {can("can_create_estimates") && <button onClick={() => setNewWO("estimate")} className={GHOST}><M n="request_quote" c="text-sm" />New estimate</button>}
                  {!can("can_make_client_payments") ? null : creditOpen ? (
                    <div className="flex items-center gap-2">
                      <input autoFocus aria-label="Credit amount" value={creditVal} onChange={(e) => setCreditVal(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") addCredit(); if (e.key === "Escape") { setCreditOpen(false); setCreditVal(""); } }} inputMode="decimal" placeholder="Amount" className="w-28 glass-input rounded-xl px-3 py-2 text-sm tabular-nums" />
                      <button onClick={addCredit} disabled={busy} className="h-9 px-3.5 rounded-xl bg-primary text-white text-xs font-semibold hover:bg-primary-container disabled:opacity-50">Add</button>
                      <button onClick={() => { setCreditOpen(false); setCreditVal(""); }} className="h-9 px-3 rounded-xl text-xs font-semibold text-ink-tertiary hover:text-ink-primary">Cancel</button>
                    </div>
                  ) : (
                    <button onClick={() => setCreditOpen(true)} className={GHOST}>Add credit</button>
                  )}
                </div>
              </div>
              {/* Built-in and custom fields in the order set in Settings → Forms → Customize. */}
              <LayoutFields layout={layout} blocks={{
                phone: { half: true, el: <F l="Phone number" v={c.phone || ""} /> },
                email: { half: true, el: <F l="Email address" v={c.email || ""} /> },
                source: { half: true, el: <div><label className="block text-[11px] font-bold text-ink-tertiary mb-1">Lead source{reqStar(layout, "source")}</label>
                  <DirectorySelect dirKey="lead_source" value={source} onChange={setSource} className="glass-input rounded-xl px-3 py-2 text-sm font-semibold text-ink-primary w-full" /></div> },
                loyalty_tier: { half: true, el: <F l="Loyalty tier" v={c.loyalty_tier || ""} /> },
                discount_card: { half: true, el: <F l="Discount card or member ID" v={c.discount_card || ""} /> },
                address: { el: <AddressFields value={addr} onChange={setAddr} legacy={c.address} idPrefix="cd-addr" /> },
                notes: (c as any).notes ? { el: <F l="Notes" v={(c as any).notes} /> } : null,
              }} custom={(ids) => <CustomFields entity="client" only={ids} bare={!!ids} value={cd} onChange={setCd} creating={false} inputSize="sm" />} />
              <div className="grid grid-cols-2 gap-3">
                <F l="Orders" v={String(c.order_count ?? 0)} />
                {c.currency_code && <F l="Billing currency" v={c.currency_code} />}
              </div>
              <div className={`${sectionCard} p-4 space-y-2.5`}>
                <div><div className="text-sm font-semibold text-ink-primary">Billing details</div><p className="text-xs text-ink-tertiary mt-0.5">Printed on this customer's invoices.</p></div>
                <div className="grid grid-cols-2 gap-2.5">
                  <label className="block"><span className={`${LABEL} block mb-1`}>Tax ID</span>
                    <input value={biz.tax_id} onChange={(e) => setBiz({ ...biz, tax_id: e.target.value })} className={`${INPUT} w-full`} /></label>
                  <label className="block"><span className={`${LABEL} block mb-1`}>Registration number</span>
                    <input value={biz.reg_no} onChange={(e) => setBiz({ ...biz, reg_no: e.target.value })} className={`${INPUT} w-full`} /></label>
                </div>
                <label className="block"><span className={`${LABEL} block mb-1`}>Billing address, if different</span>
                  <input value={biz.billing_address} onChange={(e) => setBiz({ ...biz, billing_address: e.target.value })} className={`${INPUT} w-full`} /></label>
                <div className="flex items-center gap-2">
                  <span className={`${LABEL} whitespace-nowrap`}>Birthday</span>
                  <DateField mode="date" label="Birthday" value={biz.birthday || ""} onChange={(v) => setBiz({ ...biz, birthday: v })} />
                  <span className="text-[11px] text-ink-tertiary">Used for birthday messages</span>
                </div>
              </div>
              <DiscountMatrix disc={disc} onChange={setDisc} />
              <GdprCard clientId={clientId} onErased={onClose} />
            </>
          ) : tab === 1 ? (
            <DevicesTab devices={c.devices} onOpen={openAsset} />
          ) : tab === 2 ? (
            <WorkOrdersTab clientId={clientId} onOpen={openOrder} />
          ) : tab === 3 ? (
            <PurchasesTab clientId={clientId} />
          ) : tab === 4 ? (
            <><LoyaltyTab clientId={clientId} /><ClientCreditNotes clientId={clientId} /></>
          ) : tab === 5 ? (
            <CommsTab clientId={clientId} />
          ) : c.supplier?.id ? (
            <SupplierPanel supplierId={c.supplier.id} onSaved={reload} />
          ) : (
            <div className={`${sectionCard} text-sm text-ink-tertiary p-6 text-center`}>Save to create this supplier profile, then its purchasing details appear here.</div>
          )}
        </div>
        <footer className="px-6 py-3.5 border-t border-slate-200/90 bg-white flex items-center gap-2">
          <button onClick={save} disabled={busy} className="h-9 px-4 rounded-xl bg-primary text-white text-xs font-semibold shadow-clay-sm hover:brightness-110 disabled:opacity-60">{busy ? "Saving…" : "Save"}</button>
          <button onClick={onClose} className="h-9 px-3.5 rounded-xl text-xs font-semibold text-ink-secondary bg-white border border-slate-200 hover:bg-slate-50">Cancel</button>
          {can("can_delete_clients") && <button onClick={removeClient} disabled={busy} className="ml-auto h-9 px-3 rounded-xl text-xs font-semibold text-rose-600 hover:bg-rose-50 disabled:opacity-50">Delete customer</button>}
        </footer>
        {toast && <div className="absolute bottom-16 left-1/2 -translate-x-1/2 bg-ink-primary text-white text-xs font-semibold px-4 py-2.5 rounded-xl shadow-drawer">{toast}</div>}
      </div>
      {assetOpen && <AssetDrawer assetId={assetOpen} onClose={() => setAssetOpen(null)} />}
      {newWO && <CreateOrderDrawer key={newWO} isEstimate={newWO === "estimate"} prefillClient={{ id: clientId, name: c.name, phone: c.phone, email: c.email }} onClose={() => setNewWO(null)} onCreated={() => { setNewWO(null); reload(); }} />}
    </div>
  );
}

const d = (iso?: string) => fmtDay(iso) || "";
const Empty = ({ t }: { t: string }) => <div className="grid place-items-center h-48 text-center text-sm font-semibold text-ink-tertiary">{t}</div>;
const Loading = () => <div className="text-xs text-ink-tertiary py-4">Loading…</div>;

function DevicesTab({ devices, onOpen }: { devices?: any[]; onOpen: (id?: string) => void }) {
  const term = useTerminology();
  if (!devices || devices.length === 0) return <Empty t={`No ${term.assets.toLowerCase()} on file.`} />;
  return (
    <div className="space-y-2">
      {devices.map((dv) => (
        <button type="button" key={dv.id} disabled={!dv.id} onClick={() => onOpen(dv.id)}
          className={`${sectionCard} w-full text-left p-3.5 flex items-center gap-3 transition-colors ${dv.id ? "hover:border-slate-300" : "cursor-default"}`}>
          <span className="w-9 h-9 rounded-lg bg-slate-100 text-ink-secondary grid place-items-center border border-slate-200"><M n="smartphone" c="text-base" /></span>
          <div className="flex-1"><div className="text-xs font-bold text-ink-primary">{[dv.brand, dv.model].filter(Boolean).join(" ") || term.asset}</div>
            {(dv.serial || dv.imei) && <div className="text-[11px] text-ink-tertiary font-mono">{[dv.serial ? `SN: ${dv.serial}` : "", dv.imei ? `IMEI: ${dv.imei}` : ""].filter(Boolean).join(" · ")}</div>}</div>
          {dv.id && <M n="chevron_right" c="text-ink-tertiary text-base" />}
        </button>
      ))}
    </div>
  );
}

function WorkOrdersTab({ clientId, onOpen }: { clientId: string; onOpen: (id: string) => void }) {
  const [rows, setRows] = useState<any[] | null>(null);
  useEffect(() => { api.get<any>(`/orders?client_id=${clientId}&limit=100`).then((r) => setRows(r.items || [])).catch(() => setRows([])); }, [clientId]);
  if (!rows) return <Loading />;
  if (rows.length === 0) return <Empty t="No work orders for this customer yet." />;
  return (
    <div className="space-y-2">
      {rows.map((o) => (
        <button type="button" key={o.id} onClick={() => onOpen(o.id)} aria-label={`Open work order ${o.order_number || o.code || ""}`.trim()}
          className={`${sectionCard} w-full text-left p-3.5 flex items-center gap-3 hover:border-slate-300 transition-colors`}>
          <div className="flex-1"><div className="text-xs font-bold text-ink-primary">{o.order_number || o.code || "Work order"}</div><div className="text-[11px] text-ink-tertiary">{[d(o.created_at), o.device_name].filter(Boolean).join(" · ")}</div></div>
          {o.status && <span className="px-2 py-0.5 rounded-full text-[11px] font-bold bg-slate-100 text-ink-secondary border border-slate-200 capitalize">{String(o.status).replace(/_/g, " ")}</span>}
          <span className="tabular-nums font-bold text-ink-primary text-xs">{money(o.total ?? o.grand_total ?? 0)}</span>
          <M n="chevron_right" c="text-ink-tertiary text-base" />
        </button>
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
        <div className={`${sectionCard} p-3`}><div className={LABEL}>Total spent</div><div className="text-base font-bold tabular-nums text-ink-primary">{money(s.total_spent || 0)}</div></div>
        <div className={`${sectionCard} p-3`}><div className={LABEL}>Items</div><div className="text-base font-bold tabular-nums text-ink-primary">{s.item_count ?? 0}</div></div>
        <div className={`${sectionCard} p-3`}><div className={LABEL}>Transactions</div><div className="text-base font-bold tabular-nums text-ink-primary">{s.transaction_count ?? 0}</div></div>
      </div>
      {items.length === 0 ? <Empty t="Nothing purchased yet." /> : (
        <div className={`${sectionCard} overflow-hidden`}>
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
  const { can } = useMe();
  // Same gates the server uses: credit = client payments, coupons = discounts, gift cards = finance.
  const canCredit = can("can_make_client_payments");
  const canCoupon = can("can_set_discount");
  const canGift = can("can_view_finance");
  const [loy, setLoy] = useState<any>(null);
  const [coupons, setCoupons] = useState<any[] | null>(null);
  const [bal, setBal] = useState<any>(null);
  const [cards, setCards] = useState<any[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [creditOpen, setCreditOpen] = useState(false);
  const [creditVal, setCreditVal] = useState("");
  const [couponOpen, setCouponOpen] = useState(false);
  const [couponPct, setCouponPct] = useState("");
  const [cardOpen, setCardOpen] = useState(false);
  const [cardVal, setCardVal] = useState("");
  const [cardExp, setCardExp] = useState("");
  const [err, setErr] = useState<string | null>(null);
  function reload() {
    api.get<any>(`/clients/${clientId}/loyalty`).then(setLoy).catch(() => setLoy({}));
    api.get<any>(`/clients/${clientId}/coupons`).then((r) => setCoupons(r.items || [])).catch(() => setCoupons([]));
    api.get<any>(`/clients/${clientId}/balance`).then(setBal).catch(() => setBal({}));
    api.get<any>(`/giftcards`).then((r) => setCards((r.items || []).filter((g: any) => g.client_id === clientId))).catch(() => setCards([]));
  }
  async function issueCard() {
    const val = Number(cardVal);
    if (!cardVal || isNaN(val) || val <= 0) { setErr("Enter a gift card value greater than 0."); return; }
    // Expiry is optional: blank means it never expires. The code is generated on the server.
    const expires_at = cardExp ? new Date(`${cardExp}T23:59:59`).toISOString() : null;
    setBusy(true); setErr(null);
    try { await api.post(`/giftcards`, { value: val, client_id: clientId, expires_at }); setCardOpen(false); setCardVal(""); setCardExp(""); reload(); }
    catch (e: any) { setErr(e.message || "Couldn't issue the gift card. Try again."); } finally { setBusy(false); }
  }
  useEffect(reload, [clientId]);
  async function genCoupon() {
    const val = Number(couponPct);
    if (!couponPct || isNaN(val) || val <= 0 || val > 100) { setErr("Enter a discount between 1 and 100 percent."); return; }
    setBusy(true); setErr(null);
    try { await api.post(`/clients/${clientId}/coupons`, { kind: "pct", value: val, name: "Returning customer", max_uses: 1 }); setCouponOpen(false); setCouponPct(""); reload(); }
    catch (e: any) { setErr(e.message || "Couldn't create the coupon. Try again."); } finally { setBusy(false); }
  }
  async function addCredit() {
    const amt = Number(creditVal);
    if (!creditVal || isNaN(amt) || amt <= 0) { setErr("Enter a credit amount greater than 0."); return; }
    setBusy(true); setErr(null);
    try { await api.post(`/clients/${clientId}/balance`, { kind: "advance", amount: amt, note: "Advance / credit" }); setCreditVal(""); setCreditOpen(false); reload(); }
    catch (e: any) { setErr(e.message || "Couldn't add the credit. Try again."); } finally { setBusy(false); }
  }
  if (!loy || !coupons || !bal || !cards) return <Loading />;
  const sym: string | undefined = bal.currency?.symbol || undefined;   // the customer's billing currency, else the shop's
  const bv = balanceView(bal.balance);
  const PRIMARY = "h-9 px-3.5 rounded-xl bg-primary text-white text-xs font-semibold disabled:opacity-50";
  const pill = (tone: "muted" | "ok" | "bad", t: string) => (
    <span className={`px-2 py-0.5 rounded-full text-[11px] font-bold border ${tone === "muted" ? "bg-slate-100 text-ink-tertiary border-slate-200" : tone === "ok" ? "bg-emerald-100 text-emerald-700 border-emerald-200" : "bg-rose-100 text-rose-700 border-rose-200"}`}>{t}</span>
  );
  return (
    <div className="space-y-3">
      {err && <div role="alert" className="rounded-xl text-xs px-3 py-2 border bg-rose-50 border-rose-100 text-rose-700">{err}</div>}
      <div className="grid grid-cols-2 gap-2">
        <div className={`${sectionCard} p-3`}><div className={LABEL}>Loyalty points</div>
          {loy.enabled
            ? <><div className="text-lg font-bold tabular-nums text-ink-primary">{Math.round(loy.points || 0)}</div><div className="text-[11px] text-ink-tertiary">{`${Math.round(loy.lifetime_points || 0)} lifetime · ${loy.tier || "Standard"}`}</div></>
            : <div className="text-sm text-ink-tertiary mt-1">Loyalty isn't set up for this shop.</div>}
        </div>
        <div className={`${sectionCard} p-3 flex flex-col`}><div className={LABEL}>Account balance</div>
          <div className="text-lg font-bold tabular-nums text-ink-primary">{money(bv.amount, sym)} <span className={`text-[11px] font-bold ${bv.className}`}>({bv.label})</span></div>
          {canCredit && (creditOpen ? (
            <div className="mt-2 flex items-center gap-1.5">
              <input autoFocus value={creditVal} onChange={(e) => setCreditVal(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") addCredit(); if (e.key === "Escape") { setCreditOpen(false); setCreditVal(""); } }} inputMode="decimal" placeholder="Amount" aria-label="Credit amount" className={`w-24 ${INPUT} tabular-nums`} />
              <button onClick={addCredit} disabled={busy} className={PRIMARY}>Add</button>
              <button onClick={() => { setCreditOpen(false); setCreditVal(""); }} className="h-9 px-3 rounded-xl text-xs font-semibold text-ink-tertiary hover:text-ink-primary">Cancel</button>
            </div>
          ) : (<button onClick={() => setCreditOpen(true)} disabled={busy} className="mt-2 self-start text-xs font-semibold text-primary hover:underline">Add credit</button>))}
        </div>
      </div>
      <Section title="Coupons" meta={coupons.length || undefined} actions={canCoupon ? (couponOpen ? (
        <>
          <label className="flex items-center gap-1.5"><span className={LABEL}>Discount</span>
            <input autoFocus value={couponPct} onChange={(e) => setCouponPct(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") genCoupon(); if (e.key === "Escape") setCouponOpen(false); }} inputMode="decimal" className={`w-20 ${INPUT} tabular-nums`} /><span className="text-xs text-ink-tertiary">%</span></label>
          <button onClick={genCoupon} disabled={busy} className={PRIMARY}>Create coupon</button>
          <button onClick={() => { setCouponOpen(false); setCouponPct(""); }} className={GHOST}>Cancel</button>
        </>
      ) : <button onClick={() => setCouponOpen(true)} disabled={busy} className={GHOST}><M n="add" c="text-sm" />Generate coupon</button>) : undefined}>
        {coupons.length === 0 ? <div className="text-xs text-ink-tertiary">No coupons issued to this customer.</div> : <div className="divide-y divide-slate-100">{coupons.map((c) => (
          <div key={c.id} className="py-2 flex items-center gap-2">
            <span className="font-mono font-bold text-primary text-xs">{c.code}</span>
            <span className="text-[11px] text-ink-secondary">{c.kind === "pct" ? `${c.value}% off` : `${money(c.value, sym)} off`}{c.name ? ` · ${c.name}` : ""}</span>
            <div className="flex-1" />
            {c.spent ? pill("muted", "Used") : c.active ? pill("ok", "Active") : pill("bad", "Inactive")}
          </div>
        ))}</div>}
      </Section>
      <Section title="Gift cards" meta={cards.length || undefined} actions={canGift ? (cardOpen ? (
        <>
          <label className="flex items-center gap-1.5"><span className={LABEL}>Value</span>
            <input autoFocus value={cardVal} onChange={(e) => setCardVal(e.target.value)} onKeyDown={(e) => { if (e.key === "Escape") setCardOpen(false); }} inputMode="decimal" className={`w-24 ${INPUT} tabular-nums`} /></label>
          <span className={LABEL}>Expires</span>
          <DateField mode="date" label="Gift card expiry date" placeholder="Never" value={cardExp} onChange={setCardExp} />
          <button onClick={issueCard} disabled={busy} className={PRIMARY}>Issue gift card</button>
          <button onClick={() => { setCardOpen(false); setCardVal(""); setCardExp(""); }} className={GHOST}>Cancel</button>
        </>
      ) : <button onClick={() => setCardOpen(true)} disabled={busy} className={GHOST}><M n="add" c="text-sm" />Issue gift card</button>) : undefined}>
        {cards.length === 0 ? <div className="text-xs text-ink-tertiary">No gift cards for this customer.</div> : <div className="divide-y divide-slate-100">{cards.map((g) => (
          <div key={g.id} className="py-2 flex items-center gap-2">
            <span className="font-mono font-bold text-primary text-xs">{g.code}</span>
            <span className="text-[11px] text-ink-secondary">{money(g.balance ?? 0, sym)} left <span className="text-ink-tertiary">of {money(g.initial_value ?? 0, sym)}</span>{g.expires_at ? ` · expires ${d(g.expires_at)}` : ""}</span>
            <div className="flex-1" />
            {(g.balance ?? 0) <= 0 ? pill("muted", "Spent") : g.active ? pill("ok", "Active") : pill("bad", "Inactive")}
          </div>
        ))}</div>}
      </Section>
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
    catch (e: any) { await alertDialog(e.message || "Couldn't send the message. Try again."); } finally { setBusy(false); }
  }
  async function suggest() {
    setBusy(true);
    try { const r = await api.post<any>(`/clients/${clientId}/communication/suggest`, {}); setBody(r.reply || ""); }
    catch { /* ignore */ } finally { setBusy(false); }
  }
  const sel = `${INPUT} text-ink-primary`;
  return (
    <div className="space-y-3">
      <div className={`${sectionCard} p-3 space-y-2`}>
        <div className="flex items-center gap-2">
          <select aria-label="Channel" value={channel} onChange={(e) => setChannel(e.target.value)} className={sel}>{CHANNELS.map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select>
          <select aria-label="Insert template" value="" onChange={(e) => { const t = templates.find((x) => x.id === e.target.value); if (t) setBody(t.body); }} className={`${sel} flex-1`}><option value="">Insert template…</option>{templates.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}</select>
          <button onClick={suggest} disabled={busy} className={GHOST}><M n="auto_awesome" c="text-sm" />Suggest</button>
        </div>
        <textarea aria-label="Message" value={body} onChange={(e) => setBody(e.target.value)} placeholder="Write a message…" className="glass-input rounded-xl px-3 py-2 text-sm w-full text-ink-primary h-20 resize-none" />
        <div className="flex"><button onClick={send} disabled={busy || !body.trim()} className="h-9 px-4 rounded-xl bg-primary text-white text-xs font-semibold shadow-clay-sm disabled:opacity-50">{busy ? "Sending…" : "Send"}</button></div>
      </div>
      {!msgs ? <Loading /> : msgs.length === 0 ? <Empty t="No messages exchanged yet." /> : (
        <div className="space-y-2">
          {msgs.map((m) => (
            <div key={m.id} className={`rounded-xl border p-3 shadow-sm ${m.direction === "outbound" ? "bg-blue-50/60 border-blue-100 ml-6" : "bg-white border-slate-200/90 mr-6"}`}>
              <div className="flex items-center gap-2 mb-1 text-[11px] font-bold text-ink-tertiary"><span className="px-1.5 py-0.5 rounded bg-slate-100">{channelLabel(m.channel)}</span><span>{DIRECTION[m.direction] || humanize(m.direction)}</span>{m.author ? <span>· {m.author}</span> : null}<div className="flex-1" /><span>{d(m.created_at)}</span></div>
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
    catch (e: any) { await alertDialog(e.message || "Couldn't delete the personal data."); setBusy(false); }
  }
  const consentRow = (ch: string, label: string) => (
    <div key={ch} className="flex items-center justify-between gap-3 py-1.5">
      <span className="text-xs font-semibold text-ink-secondary">{label}</span>
      <Switch size="sm" on={!!consent[ch]} onChange={(v) => setC(ch, v)} label={label} />
    </div>
  );
  return (
    <div className={`${sectionCard} p-4 space-y-3`}>
      <div className="text-sm font-semibold text-ink-primary flex items-center gap-1.5"><M n="privacy_tip" c="text-base text-ink-tertiary" />Privacy and GDPR</div>
      <div className="divide-y divide-slate-100">
        {consentRow("email", "Email consent")}
        {consentRow("phone", "Phone consent")}
        {consentRow("marketing", "Marketing consent")}
      </div>
      <div className="flex flex-wrap items-center gap-2 pt-1">
        <button onClick={() => openAuthedDoc(`/compliance/gdpr/client/${clientId}/export`).catch(() => alertDialog("Couldn't export the data. Try again."))} className={GHOST}><M n="download" c="text-sm" />Export data</button>
        {!confirmErase
          ? <button onClick={() => setConfirmErase(true)} className="h-9 px-3.5 rounded-xl bg-white border border-rose-200 text-rose-600 hover:bg-rose-50 text-xs font-semibold inline-flex items-center gap-1.5"><M n="delete_forever" c="text-sm" />Delete personal data…</button>
          : <div className="w-full rounded-xl border border-rose-200 bg-rose-50/60 p-3 space-y-2">
              <p className="text-xs text-rose-700">This removes this customer's name, contact details and address for good. It can't be undone.</p>
              <div className="flex items-center gap-2">
                <button onClick={erase} disabled={busy} className="h-9 px-3.5 rounded-xl bg-rose-600 text-white text-xs font-semibold disabled:opacity-50">{busy ? "Deleting…" : "Delete personal data"}</button>
                <button onClick={() => setConfirmErase(false)} className={GHOST}>Cancel</button>
              </div>
            </div>}
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
  // Effective-price preview on a 100-cost sample item (in the shop's currency), so staff see the real
  // outcome of "by price type" instead of guessing (Orderry shows nothing here).
  const preview = (mode: string, mk: string): string | null => {
    if (mode === "percent") {
      const v = Number(disc[`${mk}_value`] || 0);
      return v > 0 ? `A ${money(100)} item bills at ${money(100 * (1 - v / 100), undefined, true)} (−${v}%).` : null;
    }
    if (mode === "price_type") {
      const t = types.find((x) => x.id === disc[`${mk}_price_type_id`]);
      if (!t) return null;
      const m = Number(t.markup_pct || 0);
      return `An item costing ${money(100)} bills this customer at ${money(100 * (1 + m / 100), undefined, true)} (${t.name}, ${m}% markup).`;
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
          <select aria-label={`${label} discount`} value={mode} onChange={(e) => set(`${mk}_mode`, e.target.value)} className={`col-span-4 ${INPUT}`}>
            <option value="none">No discount</option><option value="percent">Fixed %</option><option value="price_type">By price type</option>
          </select>
          {mode === "percent" && (
            <div className="col-span-4 flex items-center gap-1"><input type="number" min={0} max={100} aria-label={`${label} discount percent`} value={disc[`${mk}_value`] ?? 0} onChange={(e) => set(`${mk}_value`, Number(e.target.value))} className={`${INPUT} w-full`} /><span className="text-xs text-ink-tertiary">%</span></div>
          )}
          {mode === "price_type" && (
            <select aria-label={`${label} price type`} value={disc[`${mk}_price_type_id`] || ""} onChange={(e) => set(`${mk}_price_type_id`, e.target.value)} className={`col-span-4 ${INPUT}`}>
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
    <div className={`${sectionCard} p-4 space-y-3`}>
      <div><h5 className="text-sm font-semibold text-ink-primary">Pricing and discounts</h5>
        <p className="text-xs text-ink-tertiary mt-0.5">Applied automatically to this customer's work orders, invoices and sales.</p></div>
      <Row label="Services and labour" mk="disc_services" />
      <Row label="Materials and products" mk="disc_materials" />
    </div>
  );
}
