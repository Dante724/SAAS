import SkeletonRows from "../../components/SkeletonRows";
import { useEffect, useState } from "react";
import { M } from "../../components/Shell";
import { api } from "../../api/client";
import NewAccountDrawer, { Account } from "./NewAccountDrawer";
import CashDrawer from "./CashDrawer";
import Pager from "../../components/Pager";
import { currencySymbol, money } from "../orders/types";
import { promptDialog } from "../../lib/dialog";
import { todayTotals } from "../../lib/cashStats";

type Txn = { id: string; txn_type?: string; amount?: number; account?: string; account_id?: string; cash_flow_item?: string; comment?: string; actual_date?: string; status?: string; balance_after?: number; reverses_txn_id?: string; void_reason?: string };
type FlowItem = { id: string; name?: string; direction?: string };

const CCY: Record<string, string> = { INR: "₹", EUR: "€", USD: "$", GBP: "£", AED: "د.إ" };
const iconFor = (a: Account) => a.icon || (a.payment_option === "cash" ? "payments" : a.payment_option === "card" ? "credit_card" : "account_balance");

export default function CashPanel() {
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [sel, setSel] = useState<Account | null>(null);
  const [txns, setTxns] = useState<Txn[] | null>(null);
  const [flow, setFlow] = useState<FlowItem[]>([]);
  const [menu, setMenu] = useState<string | null>(null);
  const [drawer, setDrawer] = useState<null | { edit: Account | null }>(null);
  const [modal, setModal] = useState<null | "income" | "expense" | "transfer">(null);
  const [form, setForm] = useState<any>({});
  const [toast, setToast] = useState<string | null>(null);
  const flash = (m: string) => { setToast(m); setTimeout(() => setToast(null), 2400); };

  function loadAccounts(keepSel = true) {
    return api.get<{ items: Account[] }>("/cash/accounts").then((r) => {
      const items = r.items || []; setAccounts(items);
      setSel((cur) => keepSel && cur ? items.find((a) => a.id === cur.id) || items[0] || null : items[0] || null);
      return items;
    }).catch(() => setAccounts([]));
  }
  useEffect(() => { loadAccounts(false); api.get<{ items: FlowItem[] }>("/cash/flow-items").then((r) => setFlow(r.items || [])).catch(() => {}); }, []);
  useEffect(() => { if (sel) api.get<{ items: Txn[] }>(`/cash/transactions?account_id=${sel.id}&limit=1000`).then((r) => setTxns(r.items || [])).catch(() => setTxns([])); }, [sel]);

  function openTxn(kind: "income" | "expense") { setForm({ amount: "", cash_flow_item_id: "", comment: "" }); setMenu(null); setModal(kind); }
  function openTransfer() { setForm({ to_account_id: accounts.find((a) => a.id !== sel?.id)?.id || "", amount: "", comment: "" }); setMenu(null); setModal("transfer"); }

  async function submit() {
    try {
      if (modal === "transfer") {
        await api.post("/cash/transfer", { from_account_id: sel!.id, to_account_id: form.to_account_id, amount: parseFloat(form.amount), comment: form.comment || null });
      } else {
        await api.post("/cash/transactions", { account_id: sel!.id, txn_type: modal, amount: parseFloat(form.amount), cash_flow_item_id: form.cash_flow_item_id || null, comment: form.comment || null });
      }
      flash(modal === "transfer" ? "Transfer recorded" : `${modal} recorded`); setModal(null);
      const items = await loadAccounts(); const cur = (items as Account[]).find((a) => a.id === sel!.id); if (cur) setSel(cur);
    } catch (e: any) { flash(`Failed: ${e.message}`); }
  }

  const sym = sel?.currency_code ? (CCY[sel.currency_code] || "") : currencySymbol();
  // Pagination — 50/page, reset when the selected account changes. Totals below stay full-set.
  const PAGE_SIZE = 50;
  const [txnPage, setTxnPage] = useState(1);
  useEffect(() => { setTxnPage(1); }, [sel]);
  const txnTotal = txns?.length ?? 0;
  const txnPages = Math.max(1, Math.ceil(txnTotal / PAGE_SIZE));
  const pageTxns = (txns || []).slice((Math.min(txnPage, txnPages) - 1) * PAGE_SIZE, Math.min(txnPage, txnPages) * PAGE_SIZE);
  const live = (txns || []).filter((t) => t.status !== "deleted");
  const income = live.filter((t) => t.txn_type === "income").reduce((s, t) => s + (t.amount || 0), 0);
  const expense = live.filter((t) => t.txn_type === "expense").reduce((s, t) => s + Math.abs(t.amount || 0), 0);

  async function reloadTxns() {
    const items = await loadAccounts(); const cur = (items as Account[]).find((a) => a.id === sel!.id); if (cur) setSel({ ...cur });
    if (sel) api.get<{ items: Txn[] }>(`/cash/transactions?account_id=${sel.id}&limit=1000`).then((r) => setTxns(r.items || [])).catch(() => {});
  }
  async function correctTxn(t: Txn) {
    const raw = await promptDialog("Correction amount (signed: +increase, −reduce):", { defaultValue: "" }); if (raw === null) return;
    const delta = parseFloat(raw); if (!delta) { flash("Enter a non-zero amount"); return; }
    const reason = await promptDialog("Reason (required):", { defaultValue: "" }); if (!reason || !reason.trim()) { flash("A reason is required"); return; }
    try { await api.post(`/cash/transactions/${t.id}/correct`, { delta, reason: reason.trim() }); flash("Correction posted"); await reloadTxns(); }
    catch (e: any) { flash(`Failed: ${e.message}`); }
  }
  async function deleteTxn(t: Txn) {
    const reason = (await promptDialog("Delete this transaction? Optional reason:", { defaultValue: "" })) ?? undefined;
    try { await api.del(`/cash/transactions/${t.id}${reason ? `?reason=${encodeURIComponent(reason)}` : ""}`); flash("Transaction voided"); await reloadTxns(); }
    catch (e: any) { flash(`Failed: ${e.message}`); }
  }
  async function restoreTxn(t: Txn) {
    try { await api.post(`/cash/transactions/${t.id}/restore`, {}); flash("Transaction restored"); await reloadTxns(); }
    catch (e: any) { flash(`Failed: ${e.message}`); }
  }

  const tt = todayTotals(txns || []);

  return (
    <>
      <div className="flex-1 overflow-hidden flex">
        {/* LEFT: cashbox rail */}
        <aside className="w-56 flex-shrink-0 border-r border-slate-200/80 bg-white/40 overflow-y-auto p-3.5 space-y-2">
          <div className="text-[11px] font-bold text-ink-tertiary px-1 pb-0.5">Cashboxes</div>
          {accounts.map((a) => {
            const active = sel?.id === a.id;
            return (
              <div key={a.id} onClick={() => setSel(a)} className={`relative flex items-center gap-2.5 bg-white rounded-2xl border p-2.5 cursor-pointer transition-all shadow-clay-sm ${active ? "border-primary ring-2 ring-primary/15" : "border-slate-200/90 hover:border-primary/40"}`}>
                <div className="w-9 h-9 rounded-full bg-primary text-white flex items-center justify-center shadow-clay-sm shrink-0"><M n={iconFor(a)} c="text-base" /></div>
                <div className="min-w-0 flex-1">
                  <div className="text-xs font-bold text-ink-primary truncate">{a.name}</div>
                  <div className="font-mono text-xs font-bold text-ink-secondary">{money(a.balance, a.currency_code ? (CCY[a.currency_code] || "") : currencySymbol())}</div>
                </div>
                <button aria-label="More options" onClick={(e) => { e.stopPropagation(); setMenu(menu === a.id ? null : a.id); }} className="text-ink-tertiary hover:text-ink-primary p-0.5 shrink-0"><M n="more_vert" c="text-base" /></button>
                {menu === a.id && (
                  <div className="absolute z-20 right-2 top-10 bg-white border border-slate-200 rounded-xl shadow-clay-drawer p-1.5 w-40 text-xs" onClick={(e) => e.stopPropagation()}>
                    <button onClick={() => { setSel(a); openTxn("income"); }} className="flex items-center gap-2 w-full text-left px-2.5 py-2 rounded-lg hover:bg-emerald-50 text-emerald-700 font-semibold"><M n="add" c="text-sm" />Income</button>
                    <button onClick={() => { setSel(a); openTxn("expense"); }} className="flex items-center gap-2 w-full text-left px-2.5 py-2 rounded-lg hover:bg-rose-50 text-rose-700 font-semibold"><M n="remove" c="text-sm" />Expense</button>
                    <button onClick={() => { setSel(a); openTransfer(); }} className="flex items-center gap-2 w-full text-left px-2.5 py-2 rounded-lg hover:bg-slate-50 text-ink-secondary font-semibold"><M n="swap_horiz" c="text-sm" />Transfer</button>
                    <div className="border-t border-slate-100 my-1" />
                    <button onClick={() => { setMenu(null); setDrawer({ edit: a }); }} className="flex items-center gap-2 w-full text-left px-2.5 py-2 rounded-lg hover:bg-slate-50 text-ink-secondary font-semibold"><M n="settings" c="text-sm" />Configure / rename</button>
                  </div>
                )}
              </div>
            );
          })}
          <button onClick={() => setDrawer({ edit: null })} className="w-full rounded-2xl border-2 border-dashed border-slate-300 text-ink-tertiary hover:border-primary/60 hover:text-primary hover:bg-blue-50/20 py-3 flex items-center justify-center gap-1.5 text-xs font-bold transition-all"><M n="add" c="text-base" />Cashbox</button>
        </aside>

        {/* RIGHT: hero + drawer controls + activity */}
        <div className="flex-1 min-w-0 overflow-y-auto p-6 space-y-4">
          {!accounts.length ? (
            <div className="bg-white rounded-2xl border border-slate-200/90 shadow-clay-card p-12 flex flex-col items-center text-center">
              <div className="w-14 h-14 rounded-2xl bg-primary text-white flex items-center justify-center shadow-clay-sm mb-3"><M n="point_of_sale" c="text-3xl" /></div>
              <h3 className="text-base font-bold text-ink-primary">Add your first cashbox</h3>
              <p className="text-xs text-ink-tertiary max-w-sm mt-1">A cashbox is where money lands — your till, a card account, UPI, petty cash. Create one to start taking payments and tracking the drawer.</p>
              <button onClick={() => setDrawer({ edit: null })} className="mt-4 px-4 py-2.5 rounded-xl bg-primary text-white text-xs font-bold shadow-clay-sm flex items-center gap-1.5"><M n="add" c="text-sm" />New cashbox</button>
            </div>
          ) : (
          <>
          {/* HERO: selected cashbox balance + today's glance */}
          <div className="bg-white rounded-2xl border border-slate-200/90 shadow-clay-card p-6">
            <div className="flex items-center gap-2.5 mb-3">
              <div className="w-9 h-9 rounded-full bg-primary text-white flex items-center justify-center shadow-clay-sm"><M n={sel ? iconFor(sel) : "account_balance"} c="text-base" /></div>
              <div className="text-sm font-bold text-ink-primary">{sel?.name || "—"}</div>
              {sel?.payment_option && <span className="text-[11px] font-bold text-ink-tertiary bg-slate-100 px-2 py-0.5 rounded-full">{sel.payment_option}</span>}
            </div>
            <div className="text-xs font-bold text-ink-tertiary">{sel?.payment_option === "cash" ? "In the till right now" : "Balance"}</div>
            <div className="tabular-nums text-5xl font-bold text-ink-primary tracking-tight leading-none mt-1">{money(sel?.balance, sym)}</div>
            <div className="grid grid-cols-3 gap-px mt-5 rounded-2xl overflow-hidden border border-slate-200/80 bg-slate-200/60">
              <div className="bg-white p-3.5"><div className="text-[11px] font-bold text-ink-tertiary ">In today</div><div className="tabular-nums text-lg font-bold text-emerald-600 mt-0.5">{money(tt.in, sym)}</div></div>
              <div className="bg-white p-3.5"><div className="text-[11px] font-bold text-ink-tertiary ">Out today</div><div className="tabular-nums text-lg font-bold text-rose-600 mt-0.5">{money(tt.out, sym)}</div></div>
              <div className="bg-white p-3.5"><div className="text-[11px] font-bold text-ink-tertiary ">Net today</div><div className={`tabular-nums text-lg font-bold mt-0.5 ${tt.net >= 0 ? "text-ink-primary" : "text-rose-600"}`}>{money(tt.net, sym)}</div></div>
            </div>
          </div>

          {/* Drawer (shift) controls — only cash boxes have a physical till to open/close/count */}
          {sel?.payment_option === "cash"
            ? <CashDrawer sym={sym} currencyCode={sel?.currency_code} />
            : <div className="bg-slate-50/70 rounded-2xl border border-dashed border-slate-200 p-3.5 flex items-center gap-2.5 text-xs text-ink-tertiary"><M n="info" c="text-base" />No physical drawer on a {sel?.payment_option === "card" ? "card" : "cashless"} account — record income, expenses, and transfers below.</div>}

          {/* ACTIVITY: command bar + ledger */}
          <div className="bg-white rounded-2xl border border-slate-200/90 shadow-clay-card overflow-hidden">
            <div className="bg-ink-primary px-4 py-2.5 flex items-center gap-3 flex-wrap">
              <span className="text-xs font-bold text-white">Activity</span>
              <span className="text-[11px] font-semibold text-white/60">· {sel?.name || "—"} · {live.length} transactions</span>
              <div className="ml-auto flex items-center gap-1.5">
                <button onClick={() => sel && openTxn("income")} disabled={!sel} className="px-2.5 py-1.5 rounded-lg bg-white/10 hover:bg-white/20 border border-white/15 text-white text-[11px] font-bold flex items-center gap-1 disabled:opacity-40"><M n="add" c="text-sm" />Income</button>
                <button onClick={() => sel && openTxn("expense")} disabled={!sel} className="px-2.5 py-1.5 rounded-lg bg-white/10 hover:bg-white/20 border border-white/15 text-white text-[11px] font-bold flex items-center gap-1 disabled:opacity-40"><M n="remove" c="text-sm" />Expense</button>
                <button onClick={() => sel && openTransfer()} disabled={!sel || accounts.length < 2} className="px-2.5 py-1.5 rounded-lg bg-white/10 hover:bg-white/20 border border-white/15 text-white text-[11px] font-bold flex items-center gap-1 disabled:opacity-40"><M n="swap_horiz" c="text-sm" />Transfer</button>
              </div>
            </div>
            <div className="overflow-x-auto">
            <table className="w-full text-left border-collapse text-xs">
              <thead><tr className="bg-slate-50/80 border-b border-slate-200 text-ink-tertiary text-[11px] font-bold ">
                <th className="py-3 px-4">Created</th><th className="py-3 px-4">Comment</th><th className="py-3 px-4">Category</th><th className="py-3 px-4 text-right">Income</th><th className="py-3 px-4 text-right">Expense</th><th className="py-3 px-4 text-right">Balance</th><th className="py-3 px-4 text-right">Actions</th>
              </tr></thead>
              <tbody className="divide-y divide-slate-100 font-medium text-ink-secondary">
                {!txns && <SkeletonRows cols={7} />}
                {txns?.length === 0 && (
                  <tr><td colSpan={7} className="py-16 px-4 text-center"><div className="flex flex-col items-center gap-2"><M n="account_balance_wallet" c="text-3xl text-ink-placeholder" /><div className="text-sm font-bold text-ink-secondary">Nothing here yet</div><div className="text-xs text-ink-tertiary">Add income or expenses to track your transactions.</div></div></td></tr>
                )}
                {pageTxns.map((t) => {
                  const inc = t.txn_type === "income";
                  const del = t.status === "deleted";
                  const corrected = t.status === "corrected";
                  return (
                    <tr key={t.id} className={`hover:bg-blue-50/30 ${del ? "opacity-50 line-through" : ""}`} title={del ? `Deleted${t.void_reason ? `: ${t.void_reason}` : ""}` : corrected ? "Corrected — see the linked adjustment row" : ""}>
                      <td className="py-2.5 px-4 text-ink-tertiary">{t.actual_date ? new Date(t.actual_date).toLocaleString([], { month: "short", day: "numeric", year: "numeric", hour: "numeric", minute: "2-digit" }) : "—"}</td>
                      <td className="py-2.5 px-4 text-ink-primary font-semibold">{t.comment || "—"}{corrected && <span className="ml-2 text-[11px] font-bold text-amber-600 no-underline">CORRECTED</span>}{t.reverses_txn_id && <span className="ml-2 text-[11px] font-bold text-sky-600 no-underline">ADJUSTMENT</span>}</td>
                      <td className="py-2.5 px-4">{t.cash_flow_item || "—"}</td>
                      <td className="py-2.5 px-4 text-right tabular-nums font-bold text-emerald-600">{inc ? money(Math.abs(t.amount || 0), sym) : ""}</td>
                      <td className="py-2.5 px-4 text-right tabular-nums font-bold text-rose-600">{!inc ? money(Math.abs(t.amount || 0), sym) : ""}</td>
                      <td className="py-2.5 px-4 text-right tabular-nums text-ink-tertiary">{t.balance_after != null ? money(t.balance_after, sym) : "—"}</td>
                      <td className="py-2.5 px-4 text-right whitespace-nowrap no-underline">
                        {del ? (
                          <button onClick={() => restoreTxn(t)} className="text-[11px] font-bold text-sky-600 hover:underline">Restore</button>
                        ) : (
                          <>
                            <button onClick={() => correctTxn(t)} className="text-[11px] font-bold text-amber-600 hover:underline mr-3">Correct</button>
                            <button onClick={() => deleteTxn(t)} className="text-[11px] font-bold text-rose-600 hover:underline">Delete</button>
                          </>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
              {txns && txns.length > 0 && (
                <tfoot><tr className="bg-slate-50/70 border-t border-slate-200 text-xs font-bold text-ink-primary"><td className="py-2.5 px-4" colSpan={3}>Total — {live.length}</td><td className="py-2.5 px-4 text-right tabular-nums text-emerald-600">{money(income, sym)}</td><td className="py-2.5 px-4 text-right tabular-nums text-rose-600">{money(expense, sym)}</td><td className="py-2.5 px-4 text-right tabular-nums">{money(sel?.balance, sym)}</td><td className="py-2.5 px-4"></td></tr></tfoot>
              )}
            </table>
            </div>
            {txns && txnTotal > 0 && <Pager total={txnTotal} page={Math.min(txnPage, txnPages)} pageSize={PAGE_SIZE} onPage={setTxnPage} noun="transactions" />}
          </div>
          </>
          )}
        </div>
      </div>

      {/* income/expense/transfer modal */}
      {modal && sel && (
        <div className="fixed inset-0 z-[60] flex items-center justify-center p-4">
          <div className="absolute inset-0 bg-ink-primary/40" onClick={() => setModal(null)} />
          <div className="relative bg-white rounded-2xl shadow-clay-drawer border border-slate-200 w-full max-w-md p-5 space-y-4">
            <div className="flex items-center justify-between"><h4 className="text-sm font-bold text-ink-primary capitalize">{modal === "transfer" ? "Transfer money" : `Record ${modal}`} · {sel.name}</h4><button aria-label="Close" onClick={() => setModal(null)} className="text-ink-tertiary hover:text-ink-primary"><M n="close" c="text-lg" /></button></div>
            {modal === "transfer" && (
              <div><label className="block text-[11px] font-bold text-ink-tertiary mb-1">To account</label><select value={form.to_account_id} onChange={(e) => setForm({ ...form, to_account_id: e.target.value })} className="glass-input w-full py-2 px-3 text-xs font-semibold rounded-lg">{accounts.filter((a) => a.id !== sel.id).map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}</select></div>
            )}
            <div><label className="block text-[11px] font-bold text-ink-tertiary mb-1">Amount ({sym})</label><input type="number" value={form.amount} onChange={(e) => setForm({ ...form, amount: e.target.value })} className="glass-input w-full py-2 px-3 text-sm font-bold font-mono rounded-lg" /></div>
            {modal !== "transfer" && (
              <div><label className="block text-[11px] font-bold text-ink-tertiary mb-1">Category</label><select value={form.cash_flow_item_id} onChange={(e) => setForm({ ...form, cash_flow_item_id: e.target.value })} className="glass-input w-full py-2 px-3 text-xs font-semibold rounded-lg"><option value="">— None —</option>{flow.filter((f) => !f.direction || f.direction === modal).map((f) => <option key={f.id} value={f.id}>{f.name}</option>)}</select></div>
            )}
            <div><label className="block text-[11px] font-bold text-ink-tertiary mb-1">Comment</label><input value={form.comment} onChange={(e) => setForm({ ...form, comment: e.target.value })} className="glass-input w-full py-2 px-3 text-xs rounded-lg" placeholder="Optional…" /></div>
            <div className="flex justify-end gap-2 pt-1"><button onClick={() => setModal(null)} className="px-3.5 py-2 rounded-xl text-xs font-bold text-ink-secondary bg-white border border-slate-200">Cancel</button><button onClick={submit} className="px-4 py-2 rounded-xl bg-primary text-white text-xs font-bold shadow-clay-sm capitalize">{modal}</button></div>
          </div>
        </div>
      )}

      {drawer && <NewAccountDrawer edit={drawer.edit} onClose={() => setDrawer(null)} onSaved={() => { setDrawer(null); loadAccounts(); flash(drawer.edit ? "Account updated" : "Account created"); }} />}
      {toast && <div className="fixed bottom-6 left-1/2 -translate-x-1/2 bg-ink-primary text-white text-xs font-semibold px-4 py-2.5 rounded-xl shadow-clay-drawer z-[70]">{toast}</div>}
    </>
  );
}
