import { useEffect, useState } from "react";
import { M } from "../../components/Shell";
import { api } from "../../api/client";
import Drawer from "../../components/Drawer";
import Tabs from "../../components/Tabs";
import Button from "../../components/Button";
import { Field, Input, Select, labelClass } from "../../components/Field";

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

  const Seg = ({ opts, value, onChange, label }: { opts: [string, string][]; value: string; onChange: (v: string) => void; label: string }) => (
    <Tabs look="segmented" label={label} value={value} onChange={onChange} tabs={opts.map(([id, l]) => ({ id, label: l }))} />
  );
  const Check = ({ v, set, label }: { v: boolean; set: (b: boolean) => void; label: string }) => (
    <label className="flex items-center gap-2 cursor-pointer text-xs font-medium text-ink-primary"><input type="checkbox" checked={v} onChange={(e) => set(e.target.checked)} className="w-4 h-4 rounded text-primary border-slate-300" />{label}</label>
  );

  return (
    <Drawer size="md" z={60} label={edit ? "Configure account" : "New account"} title={edit ? "Configure account" : "New account"} onClose={onClose}
      footer={<><Button variant="primary" onClick={save} disabled={busy}>{busy ? "Saving…" : edit ? "Save changes" : "Create"}</Button><Button onClick={onClose}>Cancel</Button></>}>
        <Tabs look="underline" label="Account sections" value={tab} onChange={setTab} className="-mt-2"
          tabs={[{ id: "general", label: "General" }, { id: "access", label: "Access" }]} />
        <div className="space-y-5 text-xs">
          {tab === "general" ? (<>
            <div className="flex items-center gap-3">
              <div className="w-12 h-12 rounded-full bg-primary text-white flex items-center justify-center"><M n={icon} c="text-xl" /></div>
              <div className="flex items-center gap-1.5 flex-wrap">
                {ICONS.map((ic) => <button key={ic} type="button" aria-label={`Select icon ${ic}`} onClick={() => setIcon(ic)} className={`hit44 w-9 h-9 rounded-lg border flex items-center justify-center ${icon === ic ? "border-primary bg-blue-50 text-primary" : "border-slate-200 text-ink-tertiary hover:bg-slate-50"}`}><M n={ic} c="text-base" /></button>)}
              </div>
            </div>
            <Field label={<>Title <span className="text-rose-500">*</span></>} htmlFor="acct-name"><Input id="acct-name" autoFocus value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Main Register" /></Field>
            <Field label={<>Currency <span className="text-rose-500">*</span></>} htmlFor="acct-cur" hint={edit ? "Currency is locked after creation." : undefined}>
              <Select id="acct-cur" value={currency} onChange={(e) => setCurrency(e.target.value)} disabled={!!edit}>{curOpts.map(([c, l]) => <option key={c} value={c}>{l}</option>)}</Select></Field>
            <div><span className={labelClass}>Type</span><Seg label="Type" opts={[["company", "Company account"], ["location", "Location account"]]} value={type} onChange={(v) => setType(v as any)} /></div>
            <Check v={virtual} set={setVirtual} label="Virtual account" />
            <div><span className={labelClass}>Payment option</span><Seg label="Payment option" opts={[["cash", "Cash"], ["cashless", "Cashless"], ["card", "Credit card"]]} value={pay} onChange={setPay} /></div>
            <div className="space-y-2">
              <Check v={fees} set={setFees} label="Include transaction fees" />
              {fees && (
                <div className="grid grid-cols-2 gap-3 pl-6">
                  <Field label="Percentage fee (%)" htmlFor="acct-fee-pct"><Input id="acct-fee-pct" type="number" value={feePct} onChange={(e) => setFeePct(e.target.value)} className="tabular-nums text-right" /></Field>
                  <Field label={`Flat fee (${currency})`} htmlFor="acct-fee-fixed"><Input id="acct-fee-fixed" type="number" value={feeFixed} onChange={(e) => setFeeFixed(e.target.value)} className="tabular-nums text-right" /></Field>
                </div>
              )}
            </div>
          </>) : (
            <div className="space-y-4">
              <h4 className="text-sm font-semibold text-ink-primary">Allowed account actions</h4>
              <div className="space-y-2.5"><Check v={acts.balance} set={(b) => setActs({ ...acts, balance: b })} label="View the account balance" /><Check v={acts.txns} set={(b) => setActs({ ...acts, txns: b })} label="View account transactions" /></div>
              <div className="grid grid-cols-2 gap-6 pt-2">
                <div className="space-y-2.5"><div className="text-2xs font-semibold text-ink-tertiary">Incoming operations</div><Check v={acts.income} set={(b) => setActs({ ...acts, income: b })} label="Income" /><Check v={acts.inTransfer} set={(b) => setActs({ ...acts, inTransfer: b })} label="Transfer" /></div>
                <div className="space-y-2.5"><div className="text-2xs font-semibold text-ink-tertiary">Outgoing operations</div><Check v={acts.expense} set={(b) => setActs({ ...acts, expense: b })} label="Expense" /><Check v={acts.outTransfer} set={(b) => setActs({ ...acts, outTransfer: b })} label="Transfer" /></div>
              </div>
            </div>
          )}
          {err && <div role="alert" className="text-rose-600 font-semibold">{err}</div>}
        </div>

    </Drawer>
  );
}
