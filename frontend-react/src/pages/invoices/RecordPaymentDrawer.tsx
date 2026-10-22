import { useEffect, useRef, useState } from "react";
import DateField from "../../components/DateField";
import { api } from "../../api/client";
import { useMe } from "../../context/Me";
import { Drawer, ErrorNote, btnGhost, btnPrimary, cash, inputCls, labelCls, round2, todayIso, type InvoiceDetail } from "./billing";

/* Record payment against an invoice → POST /finance/invoices/{id}/pay.
   Partial amounts are fine. When the amount is more than the balance the user picks what happens
   to the difference: keep it as client balance (store credit) or refund it now. */

type Account = { id: string; name: string; payment_option?: string };
const METHODS = [
  { key: "cash", label: "Cash", option: "cash" },
  { key: "card", label: "Card", option: "card" },
  { key: "transfer", label: "Bank transfer", option: "cashless" },
  { key: "balance", label: "Client balance", option: "" },
];

export default function RecordPaymentDrawer({ invoice, onClose, onPaid }: { invoice: InvoiceDetail; onClose: () => void; onPaid: () => void }) {
  const { can } = useMe();
  const due = round2(invoice.balance_due || 0);
  const [amount, setAmount] = useState(due ? String(due) : "");
  const [method, setMethod] = useState("cash");
  const [accounts, setAccounts] = useState<Account[] | null>(null);
  const [accountId, setAccountId] = useState("");
  const [paidAt, setPaidAt] = useState(todayIso());
  const [reference, setReference] = useState("");
  const [overpay, setOverpay] = useState<"" | "keep_balance" | "refund">("");
  const [busy, setBusy] = useState(false); const busyRef = useRef(false);
  const [err, setErr] = useState<string | null>(null);
  const hasClient = !!invoice.client_id;
  const canRefund = can("can_create_returns");

  useEffect(() => {
    api.get<{ items: Account[] }>("/cash/accounts").then((r) => setAccounts(r.items || [])).catch(() => setAccounts([]));
  }, []);
  // Default the account to the first one that takes this method (cash → a till, card → a card account).
  const option = METHODS.find((m) => m.key === method)?.option || "";
  useEffect(() => {
    if (!accounts || method === "balance") return;
    const match = accounts.find((a) => a.payment_option === option) || accounts[0];
    setAccountId((cur) => (accounts.some((a) => a.id === cur && a.payment_option === option) ? cur : match?.id || ""));
  }, [accounts, method, option]);

  const amt = round2(Number(amount) || 0);
  const excess = round2(Math.max(0, amt - due));
  const over = excess > 0;
  useEffect(() => {
    if (!over) setOverpay("");
    else if (!overpay) setOverpay(hasClient && method !== "balance" ? "keep_balance" : canRefund ? "refund" : "");
  }, [over, overpay, hasClient, canRefund, method]);

  const problem =
    amt <= 0 ? "Enter an amount above zero."
    : over && method === "balance" ? "A payment from client balance can't be more than the balance due."
    : over && !overpay ? "Choose what to do with the difference."
    : method !== "balance" && accounts && accounts.length > 0 && !accountId ? "Pick the account the money goes into."
    : paidAt > todayIso() ? "The payment date can't be in the future."
    : null;

  async function save() {
    if (busyRef.current || problem) { setErr(problem); return; }
    busyRef.current = true; setBusy(true); setErr(null);
    try {
      await api.post(`/finance/invoices/${invoice.id}/pay`, {
        amount: amt, method,
        account_id: method === "balance" ? null : accountId || null,
        reference: reference.trim() || null,
        paid_at: paidAt && paidAt !== todayIso() ? paidAt : null,
        overpay: over ? overpay : null,
      });
      onPaid();
    } catch (e: any) { setErr(e.message || "Could not record the payment"); }
    finally { busyRef.current = false; setBusy(false); }
  }

  return (
    <Drawer z={60} width={440} label="Record payment" onClose={onClose}
      title="Record payment" sub={<>Invoice {invoice.invoice_number} · balance due <span className="font-semibold text-ink-primary">{cash(due)}</span></>}
      footer={<><button onClick={onClose} className={btnGhost}>Cancel</button>
        <button onClick={save} disabled={busy || !!problem} className={btnPrimary}>{busy ? "Saving…" : `Record ${cash(amt)}`}</button></>}>
      <ErrorNote msg={err} />
      <div>
        <label htmlFor="pay-amount" className={labelCls}>Amount</label>
        <input id="pay-amount" type="number" inputMode="decimal" min={0} step="0.01" value={amount}
          onChange={(e) => setAmount(e.target.value)} className={`${inputCls} text-right tabular-nums`} />
        <div className="flex gap-2 mt-1.5">
          <button type="button" onClick={() => setAmount(String(due))} className="text-xs font-semibold text-primary">Full balance</button>
          {due > 0 && <button type="button" onClick={() => setAmount(String(round2(due / 2)))} className="text-xs font-semibold text-primary">Half</button>}
        </div>
      </div>
      <div>
        <span className={labelCls}>Method</span>
        <div className="grid grid-cols-2 gap-2" role="radiogroup" aria-label="Method">
          {METHODS.map((m) => (
            <button key={m.key} type="button" role="radio" aria-checked={method === m.key} onClick={() => setMethod(m.key)}
              disabled={m.key === "balance" && !hasClient}
              className={`px-3 py-2.5 rounded-lg border text-xs font-semibold disabled:opacity-40 ${method === m.key ? "border-primary bg-primary/5 text-primary" : "border-slate-200 text-ink-secondary hover:bg-slate-50"}`}>{m.label}</button>
          ))}
        </div>
      </div>
      {method !== "balance" && (
        <div>
          <label htmlFor="pay-account" className={labelCls}>Account</label>
          {accounts === null ? <div className="text-xs text-ink-tertiary">Loading accounts…</div>
            : accounts.length === 0 ? <div className="text-xs text-ink-tertiary">No accounts yet. The payment is recorded without moving a cashbox.</div>
            : <select id="pay-account" value={accountId} onChange={(e) => setAccountId(e.target.value)} className={inputCls}>
                {accounts.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
              </select>}
        </div>
      )}
      <div className="grid grid-cols-2 gap-3">
        <div><span className={labelCls}>Date</span><DateField mode="date" value={paidAt} onChange={setPaidAt} /></div>
        <div><label htmlFor="pay-ref" className={labelCls}>Reference</label>
          <input id="pay-ref" value={reference} onChange={(e) => setReference(e.target.value)} maxLength={200} placeholder="Optional" className={inputCls} /></div>
      </div>
      {over && (
        <fieldset className="rounded-xl border border-amber-200 bg-amber-50/60 p-3 space-y-2">
          <legend className="px-1 text-xs font-semibold text-amber-900">{cash(excess)} more than the balance due</legend>
          <label className={`flex items-start gap-2 text-xs ${!hasClient || method === "balance" ? "opacity-50" : ""}`}>
            <input type="radio" name="overpay" checked={overpay === "keep_balance"} disabled={!hasClient || method === "balance"}
              onChange={() => setOverpay("keep_balance")} className="mt-0.5 accent-primary" />
            <span><span className="font-semibold text-ink-primary">Keep as client balance</span>
              <span className="block text-ink-tertiary">{hasClient ? `${cash(excess)} stays on the client's account to use next time.` : "This invoice has no client to hold the credit."}</span></span>
          </label>
          <label className={`flex items-start gap-2 text-xs ${!canRefund ? "opacity-50" : ""}`}>
            <input type="radio" name="overpay" checked={overpay === "refund"} disabled={!canRefund}
              onChange={() => setOverpay("refund")} className="mt-0.5 accent-primary" />
            <span><span className="font-semibold text-ink-primary">Refund the difference</span>
              <span className="block text-ink-tertiary">{canRefund ? `Hand ${cash(excess)} back now. It's recorded as a refund from the same account.` : "Your role can't create refunds."}</span></span>
          </label>
        </fieldset>
      )}
      <div className="rounded-xl bg-slate-50 border border-slate-200 p-3 space-y-1.5 text-sm">
        <div className="flex justify-between text-ink-secondary"><span>Applied to this invoice</span><span className="tabular-nums font-semibold text-ink-primary">{cash(Math.min(amt, due))}</span></div>
        <div className="pt-1.5 border-t border-slate-200 flex justify-between text-ink-secondary"><span>Balance due after</span><span className="tabular-nums font-semibold text-ink-primary">{cash(Math.max(0, due - amt))}</span></div>
      </div>
      {problem && amount !== "" && <p className="text-xs text-ink-tertiary">{problem}</p>}
    </Drawer>
  );
}
