import { useEffect, useState } from "react";
import { M } from "../../components/Shell";
import { api } from "../../api/client";

export type Account = { id: string; name?: string; icon?: string; payment_option?: string; is_company?: boolean; is_virtual?: boolean; fee_pct?: number; fee_fixed?: number; currency_code?: string; balance?: number };
type Props = { edit?: Account | null; onClose: () => void; onSaved: () => void };

const ICONS = ["account_balance", "payments", "credit_card", "savings", "account_balance_wallet", "currency_exchange"];

export default function NewAccountDrawer({ edit, onClose, onSaved }: Props) {
  const [tab, setTab] = useState<"general" | "access">("general");
  const [icon, setIcon] = useState(edit?.icon || "account_balance");
  const [name, setName] = useState(edit?.name || "");
  // Currency options = base currency + the org's additional currencies (real Currency table).
  const [curOpts, setCurOpts] = useState<[string, string][]>(edit?.currency_code ? [[edit.currency_code, edit.currency_code]] : []);
  const [currency, setCurrency] = useState(edit?.currency_code || "");
  useEffect(() => {
    api.get<{ base: { code: string; symbol: string }; items: { code: string; symbol: string }[] }>("/finance/currencies")
      .then((r) => {
        const opts: [string, string][] = [[r.base.code, `${r.base.code} — ${r.base.symbol} (base)`],
          ...(r.items || []).map((c) => [c.code, `${c.code} — ${c.symbol}`] as [string, string])];
        setCurOpts(opts);
        if (!edit && !currency) setCurrency(r.base.code);
      })
      .catch(() => { if (!edit && !currency) setCurrency("USD"); });
  }, []);
  const [type, setType] = useState<"company" | "location">(edit?.is_company ? "company" : "location");
  const [virtual, setVirtual] = useState(!!edit?.is_virtual);
  const [pay, setPay] = useState(edit?.payment_option || "cash");
  const [fees, setFees] = useState(!!(edit?.fee_pct || edit?.fee_fixed));
  const [feePct, setFeePct] = useState(String(edit?.fee_pct ?? 0));
  const [feeFixed, setFeeFixed] = useState(String(edit?.fee_fixed ?? 0));
  const [acts, setActs] = useState({ balance: true, txns: true, income: true, inTransfer: true, expense: true, outTransfer: true });
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function save() {
    if (!name.trim()) { setErr("Title is required"); setTab("general"); return; }
    setBusy(true); setErr(null);
    const body = { name: name.trim(), icon, payment_option: pay, is_company: type === "company", is_virtual: virtual,
      fee_pct: fees ? parseFloat(feePct) || 0 : 0, fee_fixed: fees ? parseFloat(feeFixed) || 0 : 0, currency_code: currency };
    try {
      if (edit) await api.put(`/cash/accounts/${edit.id}`, body); else await api.post("/cash/accounts", body);
      onSaved();
    } catch (e: any) { setErr(e.message || "Failed"); } finally { setBusy(false); }
  }

  const Seg = ({ opts, value, onChange }: { opts: [string, string][]; value: string; onChange: (v: string) => void }) => (
    <div className="inline-flex items-center bg-slate-100 p-0.5 rounded-lg border border-slate-200/60">
      {opts.map(([v, label]) => <button key={v} type="button" onClick={() => onChange(v)} className={`px-3.5 py-1.5 rounded-md text-xs font-semibold ${value === v ? "bg-white text-ink-primary shadow-xs" : "text-ink-tertiary hover:text-ink-primary"}`}>{label}</button>)}
    </div>
  );
  const Check = ({ v, set, label }: { v: boolean; set: (b: boolean) => void; label: string }) => (
    <label className="flex items-center gap-2 cursor-pointer text-xs font-medium text-ink-primary"><input type="checkbox" checked={v} onChange={(e) => set(e.target.checked)} className="w-4 h-4 rounded text-primary border-slate-300" />{label}</label>
  );

  return (
    <div className="fixed inset-0 flex justify-end" style={{ zIndex: 60 }}>
      <div className="absolute inset-0 bg-ink-primary/35 backdrop-blur-[2px]" onClick={onClose} />
      <div className="relative h-full w-full max-w-[540px] bg-white shadow-clay-drawer flex flex-col border-l border-slate-200/90 animate-[slideIn_.3s_cubic-bezier(.16,1,.3,1)]">
        <style>{`@keyframes slideIn{from{transform:translateX(100%)}to{transform:translateX(0)}}`}</style>
        <header className="px-6 py-4 border-b border-slate-100 flex items-center justify-between">
          <h3 className="text-lg font-bold text-ink-primary">{edit ? "Configure account" : "New account"}</h3>
          <button aria-label="Close" onClick={onClose} className="w-8 h-8 rounded-lg hover:bg-slate-100 flex items-center justify-center text-ink-tertiary hover:text-ink-primary"><M n="close" c="text-lg" /></button>
        </header>
        <nav className="px-6 border-b border-slate-200 flex space-x-6 text-sm font-semibold">
          {(["general", "access"] as const).map((t) => <button key={t} onClick={() => setTab(t)} className={`py-3 border-b-2 capitalize transition-colors ${tab === t ? "text-primary border-primary font-bold" : "text-ink-secondary border-transparent hover:text-ink-primary"}`}>{t}</button>)}
        </nav>

        <div className="flex-1 overflow-y-auto p-6 space-y-5 text-xs">
          {tab === "general" ? (<>
            <div className="flex items-center gap-3">
              <div className="w-12 h-12 rounded-full bg-gradient-to-tr from-primary to-primary-container text-white flex items-center justify-center shadow-clay-sm"><M n={icon} c="text-xl" /></div>
              <div className="flex items-center gap-1.5 flex-wrap">
                {ICONS.map((ic) => <button key={ic} type="button" aria-label={`Select icon ${ic}`} onClick={() => setIcon(ic)} className={`w-8 h-8 rounded-lg border flex items-center justify-center ${icon === ic ? "border-primary bg-blue-50 text-primary" : "border-slate-200 text-ink-tertiary hover:bg-slate-50"}`}><M n={ic} c="text-base" /></button>)}
              </div>
            </div>
            <div><label className="block font-semibold text-ink-secondary mb-1">Title <span className="text-rose-500">*</span></label><input autoFocus value={name} onChange={(e) => setName(e.target.value)} className="glass-input w-full px-3 py-2.5 rounded-lg text-sm font-semibold text-ink-primary" placeholder="e.g. Main Register" /></div>
            <div><label className="block font-semibold text-ink-secondary mb-1">Currency <span className="text-rose-500">*</span></label>
              <select value={currency} onChange={(e) => setCurrency(e.target.value)} disabled={!!edit} className="glass-input w-full px-3 py-2.5 rounded-lg text-sm font-semibold text-ink-primary appearance-none disabled:opacity-60">{curOpts.map(([c, l]) => <option key={c} value={c}>{l}</option>)}</select>
              {edit && <p className="text-[10px] text-ink-tertiary mt-1">Currency is locked after creation.</p>}</div>
            <div><label className="block font-semibold text-ink-secondary mb-1.5">Type</label><Seg opts={[["company", "Company account"], ["location", "Location account"]]} value={type} onChange={(v) => setType(v as any)} /></div>
            <Check v={virtual} set={setVirtual} label="Virtual account" />
            <div><label className="block font-semibold text-ink-secondary mb-1.5">Payment option</label><Seg opts={[["cash", "Cash"], ["cashless", "Cashless"], ["card", "Credit card"]]} value={pay} onChange={setPay} /></div>
            <div className="space-y-2">
              <Check v={fees} set={setFees} label="Include transaction fees" />
              {fees && (
                <div className="grid grid-cols-2 gap-3 pl-6">
                  <div><label className="block font-semibold text-ink-secondary mb-1">Percentage fee (%)</label><input type="number" value={feePct} onChange={(e) => setFeePct(e.target.value)} className="glass-input w-full px-3 py-2 rounded-lg text-xs font-mono font-bold" /></div>
                  <div><label className="block font-semibold text-ink-secondary mb-1">Flat fee ({currency})</label><input type="number" value={feeFixed} onChange={(e) => setFeeFixed(e.target.value)} className="glass-input w-full px-3 py-2 rounded-lg text-xs font-mono font-bold" /></div>
                </div>
              )}
            </div>
          </>) : (
            <div className="space-y-4">
              <h4 className="text-sm font-bold text-ink-primary">Allowed account actions</h4>
              <div className="space-y-2.5"><Check v={acts.balance} set={(b) => setActs({ ...acts, balance: b })} label="View the account balance" /><Check v={acts.txns} set={(b) => setActs({ ...acts, txns: b })} label="View account transactions" /></div>
              <div className="grid grid-cols-2 gap-6 pt-2">
                <div className="space-y-2.5"><div className="text-[11px] font-bold uppercase tracking-wider text-ink-tertiary">Incoming operations</div><Check v={acts.income} set={(b) => setActs({ ...acts, income: b })} label="Income" /><Check v={acts.inTransfer} set={(b) => setActs({ ...acts, inTransfer: b })} label="Transfer" /></div>
                <div className="space-y-2.5"><div className="text-[11px] font-bold uppercase tracking-wider text-ink-tertiary">Outgoing operations</div><Check v={acts.expense} set={(b) => setActs({ ...acts, expense: b })} label="Expense" /><Check v={acts.outTransfer} set={(b) => setActs({ ...acts, outTransfer: b })} label="Transfer" /></div>
              </div>
            </div>
          )}
          {err && <div className="text-status-overdue-ink font-semibold">{err}</div>}
        </div>

        <div className="p-4 border-t border-slate-100 bg-blue-50/30 flex items-center justify-end gap-2">
          <button onClick={onClose} className="px-4 py-2 rounded-lg border border-slate-200 text-ink-secondary hover:bg-white font-semibold text-xs">Cancel</button>
          <button onClick={save} disabled={busy} className="px-5 py-2 rounded-lg bg-gradient-to-r from-primary to-primary-container text-white font-bold text-xs shadow-clay-sm hover:brightness-110 disabled:opacity-50">{busy ? "Saving…" : edit ? "Save changes" : "Create"}</button>
        </div>
      </div>
    </div>
  );
}
