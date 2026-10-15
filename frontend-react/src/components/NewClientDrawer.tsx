import DirectorySelect from "./DirectorySelect";
import { useState } from "react";
import { M } from "./Shell";
import { api } from "../api/client";
import CustomFields from "./CustomFields";
import { useCurrencies } from "../hooks/useCurrencies";

export type NewClient = { id: string; name?: string; phone?: string; email?: string };

const lbl = "block font-semibold text-ink-secondary mb-1";
const field = "glass-input w-full px-3 py-2 rounded-lg text-ink-primary text-xs";

export default function NewClientDrawer({ prefillName, onClose, onCreated }: { prefillName?: string; onClose: () => void; onCreated: (c: NewClient) => void }) {
  const [f, setF] = useState<any>({ name: prefillName || "", client_type: "person", loyalty_tier: "standard" });
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const set = (k: string, v: any) => setF((s: any) => ({ ...s, [k]: v }));
  const { opts: curOpts, multi: multiCurrency } = useCurrencies();

  async function save() {
    if (!f.name?.trim()) { setErr("Name is required"); return; }
    setBusy(true); setErr(null);
    try {
      const c = await api.post<NewClient>("/clients", {
        name: f.name.trim(), client_type: f.client_type, phone: f.phone || null, email: f.email || null,
        address: f.address || null, source: f.source || null, loyalty_tier: f.loyalty_tier,
        discount_card: f.discount_card || null, notes: f.notes || null, custom_data: f.custom_data || {},
        currency_code: f.currency_code || null,
      });
      onCreated(c);
    } catch (e: any) { setErr(e.message || "Failed to create client"); } finally { setBusy(false); }
  }

  return (
    <div className="fixed inset-0 flex justify-end" style={{ zIndex: 70 }}>
      <div className="absolute inset-0 bg-ink-primary/40 backdrop-blur-[2px]" onClick={onClose} />
      <div className="relative h-full w-full max-w-[480px] bg-white shadow-clay-drawer flex flex-col border-l border-slate-200/90 animate-[slideIn_.3s_cubic-bezier(.16,1,.3,1)]">
        <style>{`@keyframes slideIn{from{transform:translateX(100%)}to{transform:translateX(0)}}`}</style>
        <div className="px-6 py-5 border-b border-slate-100 flex items-center justify-between bg-slate-50/70">
          <div><span className="text-[10px] font-bold uppercase tracking-wider text-primary block">Contacts › New</span><h3 className="text-base font-bold text-ink-primary">New client</h3></div>
          <button aria-label="Close" onClick={onClose} className="w-8 h-8 rounded-lg hover:bg-slate-200/60 flex items-center justify-center text-ink-tertiary hover:text-ink-primary"><M n="close" c="text-base" /></button>
        </div>

        <div className="flex-1 overflow-y-auto p-6 space-y-4 text-xs">
          <div className="flex items-center gap-1.5 bg-slate-100/90 p-0.5 rounded-lg border border-slate-200/60 w-fit">
            {["person", "company"].map((t) => (
              <button key={t} onClick={() => set("client_type", t)} className={`px-3 py-1.5 rounded-md font-semibold capitalize ${f.client_type === t ? "bg-white text-ink-primary shadow-xs" : "text-ink-tertiary"}`}>{t}</button>
            ))}
          </div>
          <div><label className={lbl}>Name <span className="text-rose-500">*</span></label><input autoFocus value={f.name} onChange={(e) => set("name", e.target.value)} className={field} placeholder={f.client_type === "company" ? "Company name" : "Full name"} /></div>
          <div className="grid grid-cols-2 gap-3">
            <div><label className={lbl}>Phone</label><input value={f.phone || ""} onChange={(e) => set("phone", e.target.value)} className={`${field} font-mono`} placeholder="+91 …" /></div>
            <div><label className={lbl}>Email</label><input value={f.email || ""} onChange={(e) => set("email", e.target.value)} className={field} placeholder="name@email.com" /></div>
          </div>
          <div><label className={lbl}>Address</label><input value={f.address || ""} onChange={(e) => set("address", e.target.value)} className={field} placeholder="Billing / home address" /></div>
          <div className="grid grid-cols-2 gap-3">
            <div><label className={lbl}>Lead source</label><DirectorySelect dirKey="lead_source" value={f.source || ""} onChange={(v) => set("source", v)} className={field} /></div>
            <div><label className={lbl}>Loyalty tier</label>
              <select value={f.loyalty_tier} onChange={(e) => set("loyalty_tier", e.target.value)} className={`${field} appearance-none`}><option value="standard">Standard</option><option value="silver">Silver</option><option value="gold">Gold</option><option value="vip">VIP</option></select></div>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div><label className={lbl}>Discount card / Member ID</label><input value={f.discount_card || ""} onChange={(e) => set("discount_card", e.target.value)} className={`${field} font-mono`} /></div>
            {multiCurrency && (
              <div><label className={lbl}>Billing currency</label>
                <select value={f.currency_code || ""} onChange={(e) => set("currency_code", e.target.value)} className={`${field} appearance-none`}>
                  <option value="">Base currency</option>{curOpts.map(([c, l]) => <option key={c} value={c}>{l}</option>)}
                </select></div>
            )}
          </div>
          <div><label className={lbl}>Notes</label><textarea value={f.notes || ""} onChange={(e) => set("notes", e.target.value)} className={`${field} h-16 resize-none`} placeholder="Optional internal notes…" /></div>
          <CustomFields entity="client" value={f.custom_data || {}} onChange={(cd) => set("custom_data", cd)} creating />
          {err && <div className="text-status-overdue-ink font-semibold">{err}</div>}
        </div>

        <div className="p-4 border-t border-slate-100 flex items-center justify-between bg-slate-50/70">
          <button onClick={onClose} className="px-4 py-2 rounded-lg border border-slate-200 text-ink-secondary hover:bg-slate-100 font-semibold text-xs">Cancel</button>
          <button onClick={save} disabled={busy} className="px-5 py-2 rounded-lg bg-gradient-to-r from-primary to-primary-container text-white font-bold text-xs shadow-clay-sm hover:brightness-110 disabled:opacity-50">{busy ? "Creating…" : "Create client"}</button>
        </div>
      </div>
    </div>
  );
}
