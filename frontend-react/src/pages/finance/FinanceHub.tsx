import SkeletonRows from "../../components/SkeletonRows";
import { humanize } from "../../lib/humanize";
import { useEffect, useState } from "react";
import Shell, { M } from "../../components/Shell";
import DateField from "../../components/DateField";
import { api, openAuthedDoc } from "../../api/client";
import CatalogPicker from "../../components/CatalogPicker";
import BulkPrintDialog from "../../components/BulkPrintDialog";
import Pager from "../../components/Pager";
import { money } from "../orders/types";
import { alertDialog, promptDialog } from "../../lib/dialog";
import { isoMonth } from "../../lib/localDate";
import { useMe } from "../../context/Me";
import CashPanel from "../cash/CashPanel";

type Tab = "cashboxes" | "overview" | "pnl" | "reconcile" | "invoices" | "balances" | "payroll";
type PayrollRow = { employee_id: string; name?: string; position?: string; wage_type?: string; base_salary?: number; hourly_total?: number; commissions_total?: number; bonuses_total?: number; penalties_total?: number; gross_total?: number; computed_total?: number; status?: string };
type Account = { id: string; name?: string; balance?: number; payment_option?: string; is_virtual?: boolean };
type Invoice = { id: string; invoice_number?: string; status?: string; total?: number; amount_paid?: number; balance_due?: number; order_number?: string; issued_at?: string; einvoice_status?: string; einvoice_number?: string };
type Balance = { id: string; name?: string; phone?: string; balance?: number };

export default function FinanceHub({ initialTab }: { initialTab?: Tab } = {}) {
  const { can } = useMe();
  const [tab, setTab] = useState<Tab>(initialTab || "overview");
  const [exFrom, setExFrom] = useState("");   // accountant export range (blank = current month)
  const [exTo, setExTo] = useState("");
  const [newInv, setNewInv] = useState(false);
  const [invSel, setInvSel] = useState<Set<string>>(new Set());
  const [invPrint, setInvPrint] = useState(false);
  const [invPage, setInvPage] = useState(1);   // invoices pagination (50/page)
  const INV_PAGE_SIZE = 50;
  const reloadInvoices = () => api.get<{ items: Invoice[] }>("/finance/invoices?limit=1000").then((r) => setInvoices(r.items || [])).catch(() => {});
  async function issueEinvoice(id: string) {
    try { await api.post(`/finance/invoices/${id}/einvoice`, {}); reloadInvoices(); }
    catch { await alertDialog("Could not issue e-invoice"); }
  }
  async function markEinvoiceCleared(id: string) {
    try { await api.patch(`/finance/invoices/${id}/einvoice`, { status: "cleared" }); reloadInvoices(); }
    catch { await alertDialog("Could not update status"); }
  }
  async function recordPayout() {
    const gross = await promptDialog("Payout gross amount (total card/online sales in this deposit)");
    if (gross == null) return;
    const g = parseFloat(gross); if (!isFinite(g)) { await alertDialog("Enter a valid amount."); return; }
    const fee = await promptDialog("Processing fee withheld (blank = 0)");
    if (fee == null) return;
    const ref = await promptDialog("Reference (optional — payout ID from your statement)");
    try {
      await api.post("/finance/payouts", { gross_amount: g, fee_amount: parseFloat(fee || "0") || 0, reference: ref || undefined });
      const r = await api.get<{ items: any[]; count: number }>("/finance/reconciliation"); setRecon(r);
    } catch { await alertDialog("Could not record payout."); }
  }
  const [fin, setFin] = useState<any>(null);
  const [cash, setCash] = useState<any>(null);
  const [invoices, setInvoices] = useState<Invoice[] | null>(null);
  const [balances, setBalances] = useState<Balance[] | null>(null);
  const [payroll, setPayroll] = useState<PayrollRow[] | null>(null);
  const [runs, setRuns] = useState<any[] | null>(null);
  const [runBusy, setRunBusy] = useState(false);
  const [month] = useState<string>(() => isoMonth());
  const [pack, setPack] = useState<any>(null);
  const [pnl, setPnl] = useState<any>(null);
  const [pnlBusy, setPnlBusy] = useState(false);
  const [recon, setRecon] = useState<{ items: any[]; count: number } | null>(null);
  // Finance list pagination — 50/page, reset to 1 when the tab changes.
  const [finPage, setFinPage] = useState(1);
  useEffect(() => { setInvPage(1); setFinPage(1); }, [tab]);
  const invPages = Math.max(1, Math.ceil((invoices?.length ?? 0) / INV_PAGE_SIZE));
  const pageInv = (invoices || []).slice((Math.min(invPage, invPages) - 1) * INV_PAGE_SIZE, Math.min(invPage, invPages) * INV_PAGE_SIZE);
  const balPages = Math.max(1, Math.ceil((balances?.length ?? 0) / INV_PAGE_SIZE));
  const pageBal = (balances || []).slice((Math.min(finPage, balPages) - 1) * INV_PAGE_SIZE, Math.min(finPage, balPages) * INV_PAGE_SIZE);
  const isoLocal = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  const [pFrom, setPFrom] = useState(() => isoLocal(new Date(new Date().getFullYear(), new Date().getMonth(), 1)));
  const [pTo, setPTo] = useState(() => isoLocal(new Date()));
  const cur = (n?: number) => `${pack?.currency_symbol || "₹"}${(n ?? 0).toLocaleString(undefined, { maximumFractionDigits: 2 })}`;
  async function loadPnl() {
    setPnlBusy(true);
    try { setPnl(await api.get<any>(`/finance/pnl?date_from=${pFrom}&date_to=${pTo}`)); }
    catch { setPnl(null); } finally { setPnlBusy(false); }
  }
  const loadRuns = () => api.get<{ items: any[] }>("/payroll/runs").then((r) => setRuns(r.items || [])).catch(() => setRuns([]));
  async function runPayroll() {
    setRunBusy(true);
    try {
      const [y, m] = month.split("-").map(Number);
      const end = new Date(y, m, 0).getDate();
      await api.post("/payroll/run", { period_start: `${month}-01`, period_end: `${month}-${String(end).padStart(2, "0")}`, draft: true });
      loadRuns();
    } catch (e) { /* surfaced by 401 handler / ignore */ } finally { setRunBusy(false); }
  }
  async function markPaid(id: string) { try { await api.post(`/payroll/runs/${id}/pay`, {}); loadRuns(); } catch { /* ignore */ } }

  useEffect(() => {
    api.get<any>("/finance/reports/summary").then(setFin).catch(() => setFin({}));
    api.get<any>("/cash/summary").then(setCash).catch(() => setCash({ accounts: [] }));
    api.get<any>("/regions/pack").then(setPack).catch(() => setPack(null));
  }, []);
  useEffect(() => {
    if (tab === "pnl" && !pnl) loadPnl();
    if (tab === "reconcile" && !recon) api.get<{ items: any[]; count: number }>("/finance/reconciliation").then(setRecon).catch(() => setRecon({ items: [], count: 0 }));
    if (tab === "invoices" && !invoices) api.get<{ items: Invoice[] }>("/finance/invoices?limit=1000").then((r) => setInvoices(r.items || [])).catch(() => setInvoices([]));
    if (tab === "balances" && !balances) api.get<{ items: Balance[] }>("/cash/balances").then((r) => setBalances(r.items || [])).catch(() => setBalances([]));
    if (tab === "payroll" && !payroll) api.get<{ items: PayrollRow[] }>(`/payroll/calculation?month=${month}`).then((r) => setPayroll(r.items || [])).catch(() => setPayroll([]));
    if (tab === "payroll" && !runs) loadRuns();
  }, [tab]);

  const accounts: Account[] = cash?.accounts || [];

  // Each KPI jumps to the Finance tab that explains it (a tab switch, not a route).
  const KPIS: { l: string; v: string; icon: string; tint: string; tab?: Tab }[] = [
    { l: "Total cash", v: money(cash?.total_cash), icon: "account_balance_wallet", tint: "bg-emerald-50 text-emerald-600", tab: can("can_view_cashbox") ? "cashboxes" : "balances" },
    { l: "Revenue (paid)", v: money(fin?.total_revenue), icon: "trending_up", tint: "bg-blue-50 text-primary", tab: "pnl" },
    { l: "Outstanding", v: money(fin?.total_outstanding), icon: "pending_actions", tint: "bg-amber-50 text-amber-600", tab: "invoices" },
    { l: "Expenses", v: money(fin?.total_expenses), icon: "trending_down", tint: "bg-rose-50 text-rose-600", tab: "pnl" },
    { l: "Gross profit", v: money(fin?.gross_profit), icon: "savings", tint: "bg-indigo-50 text-indigo-600", tab: "pnl" },
  ];

  return (
    <Shell title="Finance">
      <div className="px-6 pt-3 border-b border-slate-200/60 bg-white/40 flex-shrink-0">
        <nav className="flex space-x-6 -mb-px text-xs font-semibold">
          {([...(can("can_view_cashbox") ? [["cashboxes", "Cashboxes"]] as [Tab, string][] : []), ["overview", "Overview"], ["pnl", "P&L"], ["reconcile", "Reconcile"], ["invoices", "Invoices"], ["balances", "Client balances"], ["payroll", "Payroll"]] as [Tab, string][]).map(([id, label]) => (
            <button key={id} onClick={() => setTab(id)} className={`pb-3 border-b-2 transition-colors ${tab === id ? "text-primary font-bold border-primary" : "text-ink-secondary hover:text-ink-primary border-transparent"}`}>{label}</button>
          ))}
        </nav>
      </div>

      {tab === "cashboxes" ? <CashPanel /> : (
      <div className="flex-1 overflow-y-auto px-6 py-5 space-y-4">
        {tab === "overview" && (<>
          {can("can_export_accountant") && (
            <div className="flex items-center justify-between flex-wrap gap-2">
              <div><h3 className="text-sm font-bold text-ink-primary">This month at a glance</h3><p className="text-xs text-ink-tertiary">Your books are done when the day is done — hand your accountant one clean file.</p></div>
              <div className="flex items-center gap-1.5 flex-wrap">
                <input type="date" value={exFrom} onChange={(e) => setExFrom(e.target.value)} aria-label="Export from date" className="glass-input py-1.5 px-2 text-xs rounded-lg" />
                <span className="text-ink-tertiary text-xs">→</span>
                <input type="date" value={exTo} onChange={(e) => setExTo(e.target.value)} aria-label="Export to date" className="glass-input py-1.5 px-2 text-xs rounded-lg" />
                <button onClick={() => { const qs = [exFrom && `date_from=${exFrom}`, exTo && `date_to=${exTo}`].filter(Boolean).join("&"); openAuthedDoc(`/finance/export/accountant.csv${qs ? `?${qs}` : ""}`).catch(() => alertDialog("Could not export.")); }} className="px-3.5 py-2 rounded-lg bg-white border border-slate-200 text-ink-secondary hover:bg-slate-50 text-xs font-bold flex items-center gap-1.5 shadow-clay-sm"><M n="download" c="text-sm" />Export for accountant</button>
              </div>
            </div>
          )}
          <div className="grid grid-cols-2 sm:grid-cols-5 gap-3">
            {KPIS.map((k) => {
              const clickable = !!k.tab;
              return (
                <div key={k.l}
                  onClick={clickable ? () => setTab(k.tab!) : undefined}
                  role={clickable ? "button" : undefined}
                  tabIndex={clickable ? 0 : undefined}
                  onKeyDown={clickable ? (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setTab(k.tab!); } } : undefined}
                  className={`bg-white rounded-xl p-3 border border-slate-200/80 shadow-clay-sm flex items-center justify-between ${clickable ? "cursor-pointer hover:border-primary/50 hover:shadow-clay-card transition-all focus:outline-none focus-visible:ring-2 focus-visible:ring-primary/40" : ""}`}>
                  <div><span className="text-[10px] font-bold uppercase tracking-wider text-ink-tertiary">{k.l}</span><p className="text-lg font-black font-mono text-ink-primary">{k.v}</p></div>
                  <div className={`w-8 h-8 rounded-lg flex items-center justify-center ${k.tint}`}><M n={k.icon} c="text-base" /></div>
                </div>
              );
            })}
          </div>
          <div className="bg-white rounded-2xl border border-slate-200/90 shadow-clay-card overflow-hidden">
            <div className="p-3.5 border-b border-slate-100 text-xs font-bold text-ink-primary uppercase tracking-wider">Cash accounts</div>
            <div className="divide-y divide-slate-100">
              {accounts.length === 0 && <div className="p-6 text-xs text-ink-tertiary">No accounts.</div>}
              {accounts.map((a) => (
                <div key={a.id} className="px-5 py-3 flex items-center justify-between text-xs">
                  <div className="flex items-center gap-2.5"><div className="w-8 h-8 rounded-lg bg-blue-50 text-primary flex items-center justify-center"><M n={a.payment_option === "cash" ? "payments" : a.payment_option === "card" ? "credit_card" : "account_balance"} c="text-sm" /></div><span className="font-bold text-ink-primary">{a.name}</span>{a.is_virtual && <span className="text-[9px] px-1.5 py-0.5 rounded bg-slate-100 text-ink-tertiary font-bold uppercase">Virtual</span>}</div>
                  <span className="font-mono font-bold text-ink-primary">{money(a.balance)}</span>
                </div>
              ))}
            </div>
          </div>
        </>)}

        {tab === "pnl" && (
          <>
            <div className="bg-white rounded-2xl border border-slate-200/90 shadow-clay-sm p-3.5 flex items-center gap-3 flex-wrap">
              <h3 className="text-sm font-bold text-ink-primary">Profit &amp; Loss</h3>
              <span className="text-[11px] text-ink-tertiary">Did the shop make money?</span>
              <div className="ml-auto flex items-center gap-2 text-xs">
                <DateField mode="date" value={pFrom} onChange={setPFrom} className="w-36" />
                <span className="text-ink-tertiary">→</span>
                <DateField mode="date" value={pTo} onChange={setPTo} className="w-36" />
                <button onClick={loadPnl} className="px-3 py-1.5 rounded-lg bg-gradient-to-r from-primary to-primary-container text-white text-xs font-bold shadow-clay-sm hover:brightness-110">Run</button>
              </div>
            </div>
            {pnlBusy && <div className="p-8 text-xs text-ink-tertiary text-center">Calculating…</div>}
            {!pnlBusy && pnl && (
              <>
                <div className={`rounded-2xl border shadow-clay-card p-5 ${pnl.net_profit >= 0 ? "bg-emerald-50/60 border-emerald-200" : "bg-rose-50/60 border-rose-200"}`}>
                  <p className="text-[11px] font-bold uppercase tracking-wider text-ink-tertiary">Net profit {pnl.net_profit >= 0 ? "(you made money)" : "(you're in the red)"}</p>
                  <p className={`text-3xl font-black font-mono ${pnl.net_profit >= 0 ? "text-emerald-700" : "text-rose-700"}`}>{cur(pnl.net_profit)}</p>
                  <p className="text-[11px] text-ink-tertiary mt-1">Net margin {pnl.net_margin_pct}% · {pnl.invoice_count} invoices · {pnl.date_from} → {pnl.date_to}</p>
                </div>
                <div className="grid grid-cols-2 sm:grid-cols-5 gap-3">
                  {[
                    { l: "Net revenue (ex-tax)", v: cur(pnl.revenue), icon: "trending_up", tint: "bg-blue-50 text-primary" },
                    { l: "Cost of parts", v: cur(pnl.cogs), icon: "inventory_2", tint: "bg-amber-50 text-amber-600" },
                    { l: "Gross profit", v: cur(pnl.gross_profit), sub: `${pnl.gross_margin_pct}% margin`, icon: "savings", tint: "bg-indigo-50 text-indigo-600" },
                    { l: "Operating expenses", v: cur(pnl.opex), icon: "trending_down", tint: "bg-rose-50 text-rose-600" },
                    { l: "Processing fees", v: cur(pnl.fees), icon: "credit_card", tint: "bg-rose-50 text-rose-600" },
                    { l: "Tax collected", v: cur(pnl.tax_collected), icon: "receipt_long", tint: "bg-slate-100 text-ink-secondary" },
                  ].map((k: any) => (
                    <div key={k.l} className="bg-white rounded-xl p-3 border border-slate-200/80 shadow-clay-sm flex items-center justify-between">
                      <div><span className="text-[10px] font-bold uppercase tracking-wider text-ink-tertiary">{k.l}</span><p className="text-lg font-black font-mono text-ink-primary">{k.v}</p>{k.sub && <p className="text-[10px] text-ink-tertiary">{k.sub}</p>}</div>
                      <div className={`w-8 h-8 rounded-lg flex items-center justify-center ${k.tint}`}><M n={k.icon} c="text-base" /></div>
                    </div>
                  ))}
                </div>
                <div className="bg-white rounded-2xl border border-slate-200/90 shadow-clay-card p-4 text-xs text-ink-secondary max-w-2xl">
                  <div className="flex justify-between py-1.5 border-b border-slate-100"><span>Net revenue (ex-tax)</span><span className="font-mono font-semibold text-ink-primary">{cur(pnl.revenue)}</span></div>
                  <div className="flex justify-between py-1.5 border-b border-slate-100"><span>− Cost of parts (COGS)</span><span className="font-mono text-rose-600">−{cur(pnl.cogs)}</span></div>
                  <div className="flex justify-between py-1.5 border-b border-slate-100 font-bold text-ink-primary"><span>= Gross profit</span><span className="font-mono">{cur(pnl.gross_profit)} <span className="text-[10px] text-ink-tertiary">({pnl.gross_margin_pct}%)</span></span></div>
                  <div className="flex justify-between py-1.5 border-b border-slate-100"><span>− Operating expenses</span><span className="font-mono text-rose-600">−{cur(pnl.opex)}</span></div>
                  <div className="flex justify-between py-1.5 border-b border-slate-100"><span>− Processing fees (card/online)</span><span className="font-mono text-rose-600">−{cur(pnl.fees)}</span></div>
                  <div className="flex justify-between py-2 font-black text-sm text-ink-primary"><span>= Net profit</span><span className={`font-mono ${pnl.net_profit >= 0 ? "text-emerald-700" : "text-rose-700"}`}>{cur(pnl.net_profit)}</span></div>
                  <p className="mt-2 text-[10px] text-ink-tertiary">Revenue is accrual (issued invoices, ex-tax); expenses are cash-basis for the period.{pack?.region ? ` Region: ${String(pack.region).toUpperCase()} · ${pack.currency_code}` : ""}</p>
                </div>
              </>
            )}
            {!pnlBusy && !pnl && <div className="p-8 text-xs text-ink-tertiary text-center">No data for this period.</div>}
          </>
        )}

        {tab === "reconcile" && (
          <div className="space-y-3">
            <div className="flex items-center gap-2">
              <h3 className="text-base font-bold text-ink-primary">Needs your eyes</h3>
              {recon && <span className={`text-[10px] px-2 py-0.5 rounded-full font-bold ${recon.count ? "bg-rose-100 text-rose-700" : "bg-emerald-100 text-emerald-700"}`}>{recon.count} {recon.count === 1 ? "issue" : "issues"}</span>}
              <button onClick={recordPayout} className="ml-auto text-xs font-bold px-3 py-1.5 rounded-lg bg-slate-100 text-ink-secondary hover:bg-slate-200 flex items-center gap-1"><M n="account_balance" c="text-sm" />Record payout</button>
            </div>
            <p className="text-xs text-ink-tertiary max-w-2xl">We scan your books for money problems you'd otherwise miss — overpaid invoices, drawers that didn't balance, supplier balances that drifted from their ledger, and card deposits that don't match what your processor paid out. Review each one instead of reconciling by hand.</p>
            {!recon && <div className="p-8 text-xs text-ink-tertiary text-center">Scanning…</div>}
            {recon && !recon.count && (
              <div className="bg-emerald-50/60 border border-emerald-200 rounded-2xl p-8 text-center">
                <M n="verified" c="text-3xl text-emerald-600" />
                <div className="text-sm font-bold text-emerald-800 mt-1">Your books are clean</div>
                <div className="text-xs text-emerald-700/80">No overpayments, unbalanced drawers, or drifted balances.</div>
              </div>
            )}
            {recon && recon.items.map((it: any, i: number) => (
              <div key={i} className="bg-white rounded-xl border border-slate-200/90 shadow-clay-sm p-4 flex items-start gap-3">
                <div className={`w-9 h-9 rounded-lg flex items-center justify-center shrink-0 ${it.severity === "high" ? "bg-rose-50 text-rose-600" : "bg-amber-50 text-amber-600"}`}>
                  <M n={it.type === "double_paid" ? "paid" : it.type === "drawer_unbalanced" ? "point_of_sale" : "sync_problem"} c="text-lg" />
                </div>
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2"><h4 className="text-sm font-bold text-ink-primary">{it.title}</h4>
                    <span className={`text-[9px] px-1.5 py-0.5 rounded font-bold uppercase ${it.severity === "high" ? "bg-rose-100 text-rose-700" : "bg-amber-100 text-amber-700"}`}>{it.severity}</span></div>
                  <p className="text-xs text-ink-secondary mt-0.5">{it.detail}</p>
                </div>
              </div>
            ))}
          </div>
        )}

        {tab === "invoices" && (<>
          <div className="flex justify-end items-center gap-2">
            {invSel.size > 0 && (
              <button onClick={() => setInvPrint(true)} className="px-4 py-2 rounded-xl border border-slate-200 bg-white text-ink-secondary text-xs font-semibold hover:bg-slate-50 flex items-center gap-1.5"><M n="print" c="text-sm" />Print {invSel.size} selected</button>
            )}
            <button onClick={() => setNewInv(true)} className="px-4 py-2 rounded-xl bg-gradient-to-r from-primary to-primary-container text-white text-xs font-semibold shadow-clay-sm hover:brightness-110 flex items-center gap-1.5"><M n="add" c="text-sm" />New invoice</button>
          </div>
          <div className="bg-white rounded-2xl border border-slate-200/90 shadow-clay-card overflow-hidden mt-3">
            <table className="w-full text-left border-collapse text-xs">
              <thead><tr className="bg-slate-50/80 border-b border-slate-200 text-ink-tertiary text-[10px] font-bold uppercase tracking-wider">
                <th className="py-3 pl-4 pr-2 w-10"><input type="checkbox" checked={!!invoices?.length && invoices.every((i) => invSel.has(i.id))} onChange={(e) => setInvSel(e.target.checked ? new Set((invoices || []).map((i) => i.id)) : new Set())} className="w-4 h-4 rounded border-slate-300 text-primary cursor-pointer accent-primary" /></th>
                <th className="py-3 px-4">Invoice #</th><th className="py-3 px-4">Order</th><th className="py-3 px-4">Status</th><th className="py-3 px-4 text-right">Total</th><th className="py-3 px-4 text-right">Paid</th><th className="py-3 px-4 text-right">Balance</th><th className="py-3 px-4">E-invoice</th><th className="py-3 px-4"></th>
              </tr></thead>
              <tbody className="divide-y divide-slate-100 font-medium text-ink-secondary">
                {!invoices && <SkeletonRows cols={9} />}
                {invoices?.length === 0 && <tr><td colSpan={9} className="py-6 px-4 text-ink-tertiary">No invoices.</td></tr>}
                {pageInv.map((i) => {
                  const paid = /paid/i.test(i.status || "");
                  return (
                    <tr key={i.id} className="hover:bg-blue-50/30">
                      <td className="py-2.5 pl-4 pr-2" onClick={(e) => e.stopPropagation()}><input type="checkbox" checked={invSel.has(i.id)} onChange={() => setInvSel((s) => { const n = new Set(s); n.has(i.id) ? n.delete(i.id) : n.add(i.id); return n; })} className="w-4 h-4 rounded border-slate-300 text-primary cursor-pointer accent-primary" /></td>
                      <td className="py-2.5 px-4 font-mono font-bold text-primary">{i.invoice_number}</td>
                      <td className="py-2.5 px-4 font-mono">{i.order_number || "—"}</td>
                      <td className="py-2.5 px-4"><span className={`px-2 py-0.5 rounded-full text-[10px] font-bold capitalize ${paid ? "bg-emerald-100 text-emerald-800" : /partial/i.test(i.status || "") ? "bg-amber-100 text-amber-800" : "bg-slate-100 text-slate-700"}`}>{humanize(i.status)}</span></td>
                      <td className="py-2.5 px-4 text-right font-mono font-bold text-ink-primary">{money(i.total)}</td>
                      <td className="py-2.5 px-4 text-right font-mono text-emerald-600">{money(i.amount_paid)}</td>
                      <td className="py-2.5 px-4 text-right font-mono font-bold text-rose-600">{money(i.balance_due)}</td>
                      <td className="py-2.5 px-4">
                        {(!i.einvoice_status || i.einvoice_status === "none") ? (
                          <button onClick={() => issueEinvoice(i.id)} className="px-2 py-1 rounded-lg border border-slate-200 bg-white text-ink-secondary text-[11px] font-semibold hover:bg-slate-50">Issue e-invoice</button>
                        ) : (
                          <div className="flex items-center gap-1.5">
                            <span className={`px-2 py-0.5 rounded-full text-[10px] font-bold capitalize ${i.einvoice_status === "cleared" ? "bg-emerald-100 text-emerald-800" : i.einvoice_status === "rejected" ? "bg-rose-100 text-rose-800" : "bg-sky-100 text-sky-800"}`}>{humanize(i.einvoice_status)}</span>
                            <button aria-label="Download XML" onClick={() => openAuthedDoc(`/finance/invoices/${i.id}/einvoice.xml`).catch(() => alertDialog("Could not open e-invoice"))} title="Download XML" className="p-1 rounded-lg text-ink-tertiary hover:text-primary hover:bg-slate-100"><M n="download" c="text-sm" /></button>
                            {i.einvoice_status === "issued" && <button aria-label="Mark cleared" onClick={() => markEinvoiceCleared(i.id)} title="Mark cleared" className="p-1 rounded-lg text-ink-tertiary hover:text-emerald-600 hover:bg-slate-100"><M n="check" c="text-sm" /></button>}
                          </div>
                        )}
                      </td>
                      <td className="py-2.5 px-4 text-right"><button aria-label="Print / PDF" onClick={() => openAuthedDoc(`/print/invoices/${i.id}`).catch(() => alertDialog("Could not open invoice PDF"))} title="Print / PDF" className="p-1.5 rounded-lg text-ink-tertiary hover:text-primary hover:bg-slate-100"><M n="print" c="text-base" /></button></td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            {invoices && invoices.length > 0 && <Pager total={invoices.length} page={Math.min(invPage, invPages)} pageSize={INV_PAGE_SIZE} onPage={setInvPage} noun="invoices" />}
          </div>
        </>)}

        {tab === "payroll" && (
          <div className="space-y-3">
            <div className="flex items-center justify-between">
              <h3 className="text-sm font-bold text-ink-primary">Payroll calculation — {month}</h3>
              <button onClick={runPayroll} disabled={runBusy} className="px-3 py-1.5 rounded-lg bg-primary text-white text-xs font-bold hover:bg-primary-container disabled:opacity-60 flex items-center gap-1"><M n="save" c="text-xs" />{runBusy ? "Running…" : "Run payroll (draft)"}</button>
            </div>
            <div className="bg-white rounded-2xl border border-slate-200/90 shadow-clay-card overflow-hidden">
              <table className="w-full text-left border-collapse text-xs">
                <thead><tr className="bg-slate-50/80 border-b border-slate-200 text-ink-tertiary text-[10px] font-bold uppercase tracking-wider">
                  <th className="py-3 px-4">Employee</th><th className="py-3 px-4">Position</th><th className="py-3 px-4">Wage type</th>
                  <th className="py-3 px-4 text-right">Base</th><th className="py-3 px-4 text-right">Hourly</th><th className="py-3 px-4 text-right">Commission</th>
                  <th className="py-3 px-4 text-right">Bonus</th><th className="py-3 px-4 text-right">Penalty</th><th className="py-3 px-4 text-right">Total</th><th className="py-3 px-4"></th>
                </tr></thead>
                <tbody className="divide-y divide-slate-100 font-medium text-ink-secondary">
                  {!payroll && <SkeletonRows cols={10} />}
                  {payroll?.length === 0 && <tr><td colSpan={10} className="py-6 px-4 text-ink-tertiary">No payroll data for this month.</td></tr>}
                  {payroll?.map((p) => (
                    <tr key={p.employee_id} className="hover:bg-blue-50/30">
                      <td className="py-2.5 px-4 font-bold text-ink-primary">{p.name || "—"}</td>
                      <td className="py-2.5 px-4">{p.position || "—"}</td>
                      <td className="py-2.5 px-4 capitalize">{p.wage_type || "—"}</td>
                      <td className="py-2.5 px-4 text-right font-mono">{money(p.base_salary)}</td>
                      <td className="py-2.5 px-4 text-right font-mono">{money(p.hourly_total)}</td>
                      <td className="py-2.5 px-4 text-right font-mono text-emerald-600">{money(p.commissions_total)}</td>
                      <td className="py-2.5 px-4 text-right font-mono text-emerald-600">{money(p.bonuses_total)}</td>
                      <td className="py-2.5 px-4 text-right font-mono text-rose-600">{money(p.penalties_total)}</td>
                      <td className="py-2.5 px-4 text-right font-mono font-bold text-ink-primary">{money(p.computed_total ?? p.gross_total)}</td>
                      <td className="py-2.5 px-4 text-right"><button aria-label="Print payslip" onClick={() => openAuthedDoc(`/print/payroll-statement/${p.employee_id}`).catch(() => alertDialog("Could not open payslip"))} title="Print payslip" className="p-1.5 rounded-lg text-ink-tertiary hover:text-primary hover:bg-slate-100"><M n="print" c="text-base" /></button></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <div className="bg-white rounded-2xl border border-slate-200/90 shadow-clay-card overflow-hidden">
              <div className="p-3.5 border-b border-slate-100 text-xs font-bold text-ink-primary uppercase tracking-wider">Payroll runs</div>
              <table className="w-full text-left border-collapse text-xs">
                <thead><tr className="bg-slate-50/80 border-b border-slate-200 text-ink-tertiary text-[10px] font-bold uppercase tracking-wider">
                  <th className="py-3 px-4">Period</th><th className="py-3 px-4">Status</th><th className="py-3 px-4 text-right">Gross</th><th className="py-3 px-4">Accrued</th><th className="py-3 px-4 text-right">Action</th>
                </tr></thead>
                <tbody className="divide-y divide-slate-100 font-medium text-ink-secondary">
                  {!runs && <SkeletonRows cols={5} />}
                  {runs?.length === 0 && <tr><td colSpan={5} className="py-6 px-4 text-ink-tertiary">No payroll runs yet — use “Run payroll (draft)”.</td></tr>}
                  {runs?.map((r) => {
                    const paid = /paid/i.test(r.status || "");
                    return (
                      <tr key={r.id} className="hover:bg-blue-50/30">
                        <td className="py-2.5 px-4 font-semibold text-ink-primary">{r.period_start} → {r.period_end}</td>
                        <td className="py-2.5 px-4"><span className={`px-2 py-0.5 rounded-full text-[10px] font-bold capitalize ${paid ? "bg-emerald-100 text-emerald-800" : "bg-amber-100 text-amber-800"}`}>{r.status || "draft"}</span></td>
                        <td className="py-2.5 px-4 text-right font-mono font-bold text-ink-primary">{money(r.totals?.gross_total ?? r.gross_total)}</td>
                        <td className="py-2.5 px-4 text-ink-tertiary">{r.accrued_at ? new Date(r.accrued_at).toLocaleDateString() : "—"}</td>
                        <td className="py-2.5 px-4 text-right">{!paid && <button onClick={() => markPaid(r.id)} className="text-primary font-semibold hover:underline">Mark paid</button>}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {tab === "balances" && (
          <div className="bg-white rounded-2xl border border-slate-200/90 shadow-clay-card overflow-hidden">
            <table className="w-full text-left border-collapse text-xs">
              <thead><tr className="bg-slate-50/80 border-b border-slate-200 text-ink-tertiary text-[10px] font-bold uppercase tracking-wider">
                <th className="py-3 px-4">Client</th><th className="py-3 px-4">Phone</th><th className="py-3 px-4 text-right">Balance</th>
              </tr></thead>
              <tbody className="divide-y divide-slate-100 font-medium text-ink-secondary">
                {!balances && <SkeletonRows cols={3} />}
                {balances?.length === 0 && <tr><td colSpan={3} className="py-6 px-4 text-ink-tertiary">Everyone is settled up.</td></tr>}
                {pageBal.map((b) => (
                  <tr key={b.id} className="hover:bg-blue-50/30">
                    <td className="py-2.5 px-4 font-bold text-ink-primary">{b.name}</td>
                    <td className="py-2.5 px-4 font-mono">{b.phone || "—"}</td>
                    <td className={`py-2.5 px-4 text-right font-mono font-bold ${(b.balance || 0) < 0 ? "text-rose-600" : "text-emerald-600"}`}>{(b.balance || 0) < 0 ? "Owes " : "Credit "}{money(Math.abs(b.balance || 0))}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {balances && balances.length > 0 && <Pager total={balances.length} page={Math.min(finPage, balPages)} pageSize={INV_PAGE_SIZE} onPage={setFinPage} noun="client balances" />}
          </div>
        )}
      </div>
      )}
      {newInv && <NewInvoiceDrawer onClose={() => setNewInv(false)} onCreated={() => { setNewInv(false); api.get<{ items: Invoice[] }>("/finance/invoices?limit=1000").then((r) => setInvoices(r.items || [])).catch(() => {}); }} />}
      <BulkPrintDialog open={invPrint} onClose={() => setInvPrint(false)} ids={[...invSel]} noun="invoice"
        endpoint="/documents/render-invoice-batch" idKey="invoice_ids"
        docTypes={["invoice"]} copies={false} />
    </Shell>
  );
}

/* ── New invoice drawer: client + free-form lines → POST /finance/invoices ── */
function NewInvoiceDrawer({ onClose, onCreated }: { onClose: () => void; onCreated: () => void }) {
  const [clients, setClients] = useState<{ id: string; name?: string }[]>([]);
  const [clientId, setClientId] = useState("");
  const [comment, setComment] = useState("");
  const [due, setDue] = useState("");
  const [lines, setLines] = useState<{ kind: string; description: string; qty: number; unit_price: number }[]>([{ kind: "custom", description: "", qty: 1, unit_price: 0 }]);
  const [discount, setDiscount] = useState(0); const [tax, setTax] = useState(0); // percentages, per backend InvoiceIn
  const [busy, setBusy] = useState(false); const [err, setErr] = useState<string | null>(null);
  useEffect(() => { api.get<{ items: any[] }>("/clients?limit=200").then((r) => setClients(r.items || [])).catch(() => {}); }, []);
  const setLine = (i: number, k: string, v: any) => setLines(lines.map((l, j) => j === i ? { ...l, [k]: v } : l));
  const subtotal = lines.reduce((s, l) => s + (Number(l.qty) || 0) * (Number(l.unit_price) || 0), 0);
  const discountAmt = +(subtotal * (Number(discount) || 0) / 100).toFixed(2);
  const taxAmt = +((subtotal - discountAmt) * (Number(tax) || 0) / 100).toFixed(2);
  const total = +(subtotal - discountAmt + taxAmt).toFixed(2); // matches backend: sub*(1-d/100)*(1+t/100)
  async function save() {
    const clean = lines.filter((l) => l.description.trim() && l.qty > 0);
    if (!clean.length) { setErr("Add at least one line."); return; }
    setBusy(true); setErr(null);
    try {
      await api.post("/finance/invoices", { client_id: clientId || null, comment, due_at: due || null,
        discount: Number(discount) || 0, tax: Number(tax) || 0,
        lines: clean.map((l) => ({ kind: l.kind || "custom", description: l.description.trim(), qty: Number(l.qty), unit_price: Number(l.unit_price) })) });
      onCreated();
    } catch (e: any) { setErr(e.message || "Could not create invoice"); setBusy(false); }
  }
  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      <div className="absolute inset-0 bg-ink-primary/40 backdrop-blur-[2px]" onClick={onClose} />
      <div className="relative w-[520px] max-w-full bg-white h-full shadow-drawer flex flex-col">
        <div className="flex items-center justify-between px-5 py-4 border-b border-slate-200"><h2 className="font-bold text-ink-primary">New invoice</h2><button aria-label="Close" onClick={onClose} className="w-8 h-8 rounded-lg hover:bg-slate-200/60 flex items-center justify-center text-ink-tertiary"><M n="close" c="text-base" /></button></div>
        <div className="flex-1 overflow-y-auto p-5 space-y-4">
          {err && <div className="rounded-lg bg-rose-50 border border-rose-200 text-rose-700 text-xs px-3 py-2">{err}</div>}
          <div className="grid grid-cols-2 gap-3">
            <div><label className="block text-[11px] font-semibold text-ink-tertiary uppercase tracking-wide mb-1">Client</label>
              <select value={clientId} onChange={(e) => setClientId(e.target.value)} className="w-full bg-white border border-slate-200 rounded-lg text-sm px-3 py-2"><option value="">Walk-in / none</option>{clients.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}</select></div>
            <div><label className="block text-[11px] font-semibold text-ink-tertiary uppercase tracking-wide mb-1">Due date</label>
              <DateField mode="date" value={due} onChange={setDue} /></div>
          </div>
          <div>
            <div className="mb-2"><CatalogPicker placeholder="Add from catalog (service / product)…" onPick={(i) => setLines((ls) => [...ls.filter((l) => l.description.trim() || l.unit_price), { kind: i.kind, description: i.name, qty: 1, unit_price: i.price || 0 }])} /></div>
            <div className="flex items-center justify-between mb-1"><label className="text-[11px] font-semibold text-ink-tertiary uppercase tracking-wide">Lines</label><button onClick={() => setLines([...lines, { kind: "custom", description: "", qty: 1, unit_price: 0 }])} className="text-primary text-xs font-semibold flex items-center gap-1"><M n="add" c="text-sm" />Add line</button></div>
            <div className="space-y-2">
              {lines.map((l, i) => (
                <div key={i} className="flex items-center gap-2">
                  <select value={l.kind} onChange={(e) => setLine(i, "kind", e.target.value)} title="Line type" className="bg-white border border-slate-200 rounded-lg text-xs px-1.5 py-2"><option value="custom">Custom</option><option value="service">Service</option><option value="product">Product</option><option value="labor">Labor</option></select>
                  <input value={l.description} onChange={(e) => setLine(i, "description", e.target.value)} placeholder="Description" className="flex-1 bg-white border border-slate-200 rounded-lg text-sm px-2 py-2" />
                  <input type="number" min={0} value={l.qty} onChange={(e) => setLine(i, "qty", Number(e.target.value))} title="Qty" className="w-14 bg-white border border-slate-200 rounded-lg text-sm px-2 py-2 text-right" />
                  <input type="number" min={0} value={l.unit_price} onChange={(e) => setLine(i, "unit_price", Number(e.target.value))} placeholder="Price" className="w-24 bg-white border border-slate-200 rounded-lg text-sm px-2 py-2 text-right" />
                  <button aria-label="Delete" onClick={() => setLines(lines.filter((_, j) => j !== i))} className="w-8 h-8 rounded-lg hover:bg-rose-50 text-ink-tertiary hover:text-rose-600 flex items-center justify-center"><M n="delete" c="text-sm" /></button>
                </div>
              ))}
            </div>
            <div className="grid grid-cols-2 gap-3 mt-3">
              <div><label className="block text-[11px] font-semibold text-ink-tertiary uppercase tracking-wide mb-1">Discount %</label><input type="number" min={0} max={100} value={discount} onChange={(e) => setDiscount(Number(e.target.value))} className="w-full bg-white border border-slate-200 rounded-lg text-sm px-3 py-2 text-right" /></div>
              <div><label className="block text-[11px] font-semibold text-ink-tertiary uppercase tracking-wide mb-1">Tax %</label><input type="number" min={0} value={tax} onChange={(e) => setTax(Number(e.target.value))} className="w-full bg-white border border-slate-200 rounded-lg text-sm px-3 py-2 text-right" /></div>
            </div>
            <div className="mt-3 rounded-xl bg-slate-50 border border-slate-200 p-3 space-y-1.5 text-sm">
              <div className="flex justify-between text-ink-secondary"><span>Subtotal</span><span className="font-mono font-semibold text-ink-primary">{money(subtotal)}</span></div>
              {discountAmt > 0 && <div className="flex justify-between text-ink-secondary"><span>Discount ({discount}%)</span><span className="font-mono text-rose-600">−{money(discountAmt)}</span></div>}
              {taxAmt > 0 && <div className="flex justify-between text-ink-secondary"><span>Tax ({tax}%)</span><span className="font-mono text-ink-primary">{money(taxAmt)}</span></div>}
              <div className="pt-1.5 border-t border-slate-200 flex justify-between items-baseline"><span className="font-bold text-ink-primary">Total</span><span className="font-mono font-extrabold text-primary">{money(total)}</span></div>
            </div>
          </div>
          <div><label className="block text-[11px] font-semibold text-ink-tertiary uppercase tracking-wide mb-1">Comment</label><textarea value={comment} onChange={(e) => setComment(e.target.value)} rows={2} className="w-full bg-white border border-slate-200 rounded-lg text-sm px-3 py-2" /></div>
        </div>
        <div className="px-5 py-4 border-t border-slate-200 flex justify-end gap-2"><button onClick={onClose} className="px-4 py-2 rounded-lg border border-slate-200 text-ink-secondary hover:bg-slate-100 font-semibold text-xs">Cancel</button><button onClick={save} disabled={busy} className="px-4 py-2 rounded-lg bg-primary text-white font-semibold text-xs hover:bg-primary-container disabled:opacity-60">{busy ? "Saving…" : "Create invoice"}</button></div>
      </div>
    </div>
  );
}
