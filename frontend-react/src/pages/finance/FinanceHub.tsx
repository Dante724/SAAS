import SkeletonRows from "../../components/SkeletonRows";
import PageSummary, { type SummaryStat } from "../../components/PageSummary";
import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import Shell, { M } from "../../components/Shell";
import DateField from "../../components/DateField";
import { api, openAuthedDoc } from "../../api/client";
import Pager from "../../components/Pager";
import { currencySymbol, money } from "../orders/types";
import { alertDialog, confirmDialog, promptDialog } from "../../lib/dialog";
import { isoMonth } from "../../lib/localDate";
import { useMe } from "../../context/Me";
import CashPanel from "../cash/CashPanel";

type Tab = "cashboxes" | "overview" | "pnl" | "reconcile" | "balances" | "payroll";
type PayrollRow = { employee_id: string; name?: string; position?: string; wage_type?: string; base_salary?: number; hourly_total?: number; commissions_total?: number; bonuses_total?: number; penalties_total?: number; gross_total?: number; computed_total?: number; status?: string };
type Account = { id: string; name?: string; balance?: number; payment_option?: string; is_virtual?: boolean };
type Balance = { id: string; name?: string; phone?: string; balance?: number };

export default function FinanceHub({ initialTab }: { initialTab?: Tab } = {}) {
  const { can } = useMe();
  const navigate = useNavigate();
  const [tab, setTab] = useState<Tab>(initialTab || "overview");
  const [exFrom, setExFrom] = useState("");   // accountant export range (blank = current month)
  const [exTo, setExTo] = useState("");
  const INV_PAGE_SIZE = 50;
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
  useEffect(() => { setFinPage(1); }, [tab]);
  const balPages = Math.max(1, Math.ceil((balances?.length ?? 0) / INV_PAGE_SIZE));
  const pageBal = (balances || []).slice((Math.min(finPage, balPages) - 1) * INV_PAGE_SIZE, Math.min(finPage, balPages) * INV_PAGE_SIZE);
  const isoLocal = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  const [pFrom, setPFrom] = useState(() => isoLocal(new Date(new Date().getFullYear(), new Date().getMonth(), 1)));
  const [pTo, setPTo] = useState(() => isoLocal(new Date()));
  const cur = (n?: number) => `${pack?.currency_symbol || currencySymbol()}${(n ?? 0).toLocaleString(undefined, { maximumFractionDigits: 2 })}`;
  async function loadPnl() {
    setPnlBusy(true);
    try { setPnl(await api.get<any>(`/finance/pnl?date_from=${pFrom}&date_to=${pTo}`)); }
    catch { setPnl(null); } finally { setPnlBusy(false); }
  }
  const loadRuns = () => api.get<{ items: any[] }>("/payroll/runs").then((r) => setRuns(r.items || [])).catch(() => setRuns([]));
  // Refs block a second click that lands before React re-renders the disabled button.
  const runBusyRef = useRef(false);
  async function runPayroll() {
    if (runBusyRef.current) return;
    runBusyRef.current = true; setRunBusy(true);
    try {
      const [y, m] = month.split("-").map(Number);
      const end = new Date(y, m, 0).getDate();
      // Drafts the whole team for the month; the single-run endpoint needs an employee.
      await api.post("/payroll/run-all", { period_start: `${month}-01`, period_end: `${month}-${String(end).padStart(2, "0")}`, draft: true });
      loadRuns();
    } catch (e: any) { await alertDialog(`Could not run payroll: ${e.message}`); } finally { runBusyRef.current = false; setRunBusy(false); }
  }
  const [payingId, setPayingId] = useState<string | null>(null); const payingRef = useRef(false);
  async function markPaid(id: string) {
    if (payingRef.current) return;
    payingRef.current = true;
    try {
      if (!(await confirmDialog("Mark this payroll run as paid? This posts the payout and can't be undone here.", { title: "Mark paid", confirmLabel: "Mark paid" }))) return;
      setPayingId(id);
      await api.post(`/payroll/runs/${id}/pay`, {}); loadRuns();
    } catch (e: any) { await alertDialog(`Could not mark paid: ${e.message}`); } finally { payingRef.current = false; setPayingId(null); }
  }

  useEffect(() => {
    api.get<any>("/finance/reports/summary").then(setFin).catch(() => setFin({}));
    api.get<any>("/cash/summary").then(setCash).catch(() => setCash({ accounts: [] }));
    api.get<any>("/regions/pack").then(setPack).catch(() => setPack(null));
  }, []);
  useEffect(() => {
    if (tab === "pnl" && !pnl) loadPnl();
    if (tab === "reconcile" && !recon) api.get<{ items: any[]; count: number }>("/finance/reconciliation").then(setRecon).catch(() => setRecon({ items: [], count: 0 }));
    if (tab === "balances" && !balances) api.get<{ items: Balance[] }>("/cash/balances").then((r) => setBalances(r.items || [])).catch(() => setBalances([]));
    if (tab === "payroll" && !payroll) api.get<{ items: PayrollRow[] }>(`/payroll/calculation?month=${month}`).then((r) => setPayroll(r.items || [])).catch(() => setPayroll([]));
    if (tab === "payroll" && !runs) loadRuns();
  }, [tab]);

  const accounts: Account[] = cash?.accounts || [];

  // Each KPI jumps to the Finance tab that explains it; Outstanding opens the Invoices page.
  const kpiStats: SummaryStat[] = [
    { label: "Cash and bank now", value: money(cash?.total_cash), onClick: () => setTab(can("can_view_cashbox") ? "cashboxes" : "balances") },
    { label: "Paid invoices", value: money(fin?.total_revenue), onClick: () => setTab("pnl") },
    { label: "Still owed to you", value: money(fin?.total_outstanding), onClick: () => navigate("/invoices") },
    { label: "Expenses", value: money(fin?.total_expenses), onClick: () => setTab("pnl") },
    { label: "Profit before tax", value: money(fin?.gross_profit), tone: (fin?.gross_profit || 0) < 0 ? "bad" : undefined, onClick: () => setTab("pnl") },
  ];

  return (
    <Shell title="Finance">
      <div className="px-6 pt-3 border-b border-slate-200/60 bg-white/40 flex-shrink-0">
        <nav className="flex space-x-6 -mb-px text-xs font-semibold">
          {([...(can("can_view_cashbox") ? [["cashboxes", "Cashboxes"]] as [Tab, string][] : []), ["overview", "Overview"], ["pnl", "P&L"], ["reconcile", "Reconcile"], ["balances", "Client balances"], ["payroll", "Payroll"]] as [Tab, string][]).map(([id, label]) => (
            <button key={id} onClick={() => setTab(id)} className={`pb-3 border-b-2 transition-colors ${tab === id ? "text-primary font-bold border-primary" : "text-ink-secondary hover:text-ink-primary border-transparent"}`}>{label}</button>
          ))}
        </nav>
      </div>

      {tab === "cashboxes" ? <CashPanel /> : (
      <div className="flex-1 overflow-y-auto px-6 py-5 space-y-4">
        {tab === "overview" && (<>
          {can("can_export_accountant") && (
            <div className="flex items-center justify-between flex-wrap gap-2">
              <div><h3 className="text-sm font-bold text-ink-primary">Since you started</h3><p className="text-xs text-ink-tertiary">Paid invoices, money still owed and expenses, all time. Pick dates to export a period for your accountant.</p></div>
              <div className="flex items-center gap-1.5 flex-wrap">
                <DateField mode="date" value={exFrom} onChange={setExFrom} placeholder="From" className="w-36" />
                <span className="text-ink-tertiary text-xs">→</span>
                <DateField mode="date" value={exTo} onChange={setExTo} placeholder="To" className="w-36" />
                <button onClick={() => { const qs = [exFrom && `date_from=${exFrom}`, exTo && `date_to=${exTo}`].filter(Boolean).join("&"); openAuthedDoc(`/finance/export/accountant.csv${qs ? `?${qs}` : ""}`).catch(() => alertDialog("Could not export.")); }} className="px-3.5 py-2 rounded-lg bg-white border border-slate-200 text-ink-secondary hover:bg-slate-50 text-xs font-bold flex items-center gap-1.5 shadow-clay-sm"><M n="download" c="text-sm" />Export for accountant</button>
              </div>
            </div>
          )}
          <PageSummary stats={kpiStats} />
          <div className="bg-white rounded-2xl border border-slate-200/90 shadow-clay-card overflow-hidden">
            <div className="p-3.5 border-b border-slate-100 text-xs font-bold text-ink-primary ">Cash accounts</div>
            <div className="divide-y divide-slate-100">
              {accounts.length === 0 && <div className="p-6 text-xs text-ink-tertiary">No accounts.</div>}
              {accounts.map((a) => (
                <div key={a.id} className="px-5 py-3 flex items-center justify-between text-xs">
                  <div className="flex items-center gap-2.5"><div className="w-8 h-8 rounded-lg bg-blue-50 text-primary flex items-center justify-center"><M n={a.payment_option === "cash" ? "payments" : a.payment_option === "card" ? "credit_card" : "account_balance"} c="text-sm" /></div><span className="font-bold text-ink-primary">{a.name}</span>{a.is_virtual && <span className="text-[11px] px-1.5 py-0.5 rounded bg-slate-100 text-ink-tertiary font-bold ">Virtual</span>}</div>
                  <span className="tabular-nums font-bold text-ink-primary">{money(a.balance)}</span>
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
                <button onClick={loadPnl} className="px-3 py-1.5 rounded-lg bg-primary text-white text-xs font-bold shadow-clay-sm hover:brightness-110">Run</button>
              </div>
            </div>
            {pnlBusy && <div className="p-8 text-xs text-ink-tertiary text-center">Calculating…</div>}
            {!pnlBusy && pnl && (
              <>
                <div className={`rounded-2xl border shadow-clay-card p-5 ${pnl.net_profit >= 0 ? "bg-emerald-50/60 border-emerald-200" : "bg-rose-50/60 border-rose-200"}`}>
                  <p className="text-[11px] font-bold text-ink-tertiary">Net profit {pnl.net_profit >= 0 ? "(you made money)" : "(you're in the red)"}</p>
                  <p className={`text-3xl font-bold tabular-nums ${pnl.net_profit >= 0 ? "text-emerald-700" : "text-rose-700"}`}>{cur(pnl.net_profit)}</p>
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
                      <div><span className="text-[11px] font-bold text-ink-tertiary">{k.l}</span><p className="text-lg font-bold tabular-nums text-ink-primary">{k.v}</p>{k.sub && <p className="text-[11px] text-ink-tertiary">{k.sub}</p>}</div>
                      <div className={`w-8 h-8 rounded-lg flex items-center justify-center ${k.tint}`}><M n={k.icon} c="text-base" /></div>
                    </div>
                  ))}
                </div>
                <div className="bg-white rounded-2xl border border-slate-200/90 shadow-clay-card p-4 text-xs text-ink-secondary max-w-2xl">
                  <div className="flex justify-between py-1.5 border-b border-slate-100"><span>Net revenue (ex-tax)</span><span className="tabular-nums font-semibold text-ink-primary">{cur(pnl.revenue)}</span></div>
                  <div className="flex justify-between py-1.5 border-b border-slate-100"><span>− Cost of parts (COGS)</span><span className="tabular-nums text-rose-600">−{cur(pnl.cogs)}</span></div>
                  <div className="flex justify-between py-1.5 border-b border-slate-100 font-bold text-ink-primary"><span>= Gross profit</span><span className="tabular-nums">{cur(pnl.gross_profit)} <span className="text-[11px] text-ink-tertiary">({pnl.gross_margin_pct}%)</span></span></div>
                  <div className="flex justify-between py-1.5 border-b border-slate-100"><span>− Operating expenses</span><span className="tabular-nums text-rose-600">−{cur(pnl.opex)}</span></div>
                  <div className="flex justify-between py-1.5 border-b border-slate-100"><span>− Processing fees (card/online)</span><span className="tabular-nums text-rose-600">−{cur(pnl.fees)}</span></div>
                  <div className="flex justify-between py-2 font-bold text-sm text-ink-primary"><span>= Net profit</span><span className={`tabular-nums ${pnl.net_profit >= 0 ? "text-emerald-700" : "text-rose-700"}`}>{cur(pnl.net_profit)}</span></div>
                  <p className="mt-2 text-[11px] text-ink-tertiary">Revenue is accrual (issued invoices, ex-tax); expenses are cash-basis for the period.{pack?.region ? ` Region: ${String(pack.region).toUpperCase()} · ${pack.currency_code}` : ""}</p>
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
              {recon && <span className={`text-[11px] px-2 py-0.5 rounded-full font-bold ${recon.count ? "bg-rose-100 text-rose-700" : "bg-emerald-100 text-emerald-700"}`}>{recon.count} {recon.count === 1 ? "issue" : "issues"}</span>}
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
                    <span className={`text-[11px] px-1.5 py-0.5 rounded font-bold  ${it.severity === "high" ? "bg-rose-100 text-rose-700" : "bg-amber-100 text-amber-700"}`}>{it.severity}</span></div>
                  <p className="text-xs text-ink-secondary mt-0.5">{it.detail}</p>
                </div>
              </div>
            ))}
          </div>
        )}


        {tab === "payroll" && (
          <div className="space-y-3">
            <div className="flex items-center justify-between">
              <h3 className="text-sm font-bold text-ink-primary">Payroll calculation — {month}</h3>
              <button onClick={runPayroll} disabled={runBusy} className="px-3 py-1.5 rounded-lg bg-primary text-white text-xs font-bold hover:bg-primary-container disabled:opacity-60 flex items-center gap-1"><M n="save" c="text-xs" />{runBusy ? "Running…" : "Run payroll (draft)"}</button>
            </div>
            <div className="bg-white rounded-2xl border border-slate-200/90 shadow-clay-card overflow-hidden">
              <table className="w-full text-left border-collapse text-xs">
                <thead><tr className="bg-slate-50/80 border-b border-slate-200 text-ink-tertiary text-[11px] font-bold ">
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
                      <td className="py-2.5 px-4 text-right tabular-nums">{money(p.base_salary)}</td>
                      <td className="py-2.5 px-4 text-right tabular-nums">{money(p.hourly_total)}</td>
                      <td className="py-2.5 px-4 text-right tabular-nums text-emerald-600">{money(p.commissions_total)}</td>
                      <td className="py-2.5 px-4 text-right tabular-nums text-emerald-600">{money(p.bonuses_total)}</td>
                      <td className="py-2.5 px-4 text-right tabular-nums text-rose-600">{money(p.penalties_total)}</td>
                      <td className="py-2.5 px-4 text-right tabular-nums font-bold text-ink-primary">{money(p.computed_total ?? p.gross_total)}</td>
                      <td className="py-2.5 px-4 text-right"><button aria-label="Print payslip" onClick={() => openAuthedDoc(`/print/payroll-statement/${p.employee_id}`).catch(() => alertDialog("Could not open payslip"))} title="Print payslip" className="p-1.5 rounded-lg text-ink-tertiary hover:text-primary hover:bg-slate-100"><M n="print" c="text-base" /></button></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <div className="bg-white rounded-2xl border border-slate-200/90 shadow-clay-card overflow-hidden">
              <div className="p-3.5 border-b border-slate-100 text-xs font-bold text-ink-primary ">Payroll runs</div>
              <table className="w-full text-left border-collapse text-xs">
                <thead><tr className="bg-slate-50/80 border-b border-slate-200 text-ink-tertiary text-[11px] font-bold ">
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
                        <td className="py-2.5 px-4"><span className={`px-2 py-0.5 rounded-full text-[11px] font-bold capitalize ${paid ? "bg-emerald-100 text-emerald-800" : "bg-amber-100 text-amber-800"}`}>{r.status || "draft"}</span></td>
                        <td className="py-2.5 px-4 text-right tabular-nums font-bold text-ink-primary">{money(r.totals?.gross_total ?? r.gross_total)}</td>
                        <td className="py-2.5 px-4 text-ink-tertiary">{r.accrued_at ? new Date(r.accrued_at).toLocaleDateString() : "—"}</td>
                        <td className="py-2.5 px-4 text-right">{!paid && <button onClick={() => markPaid(r.id)} disabled={!!payingId} className="text-primary font-semibold hover:underline disabled:opacity-50 disabled:no-underline">{payingId === r.id ? "Marking…" : "Mark paid"}</button>}</td>
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
              <thead><tr className="bg-slate-50/80 border-b border-slate-200 text-ink-tertiary text-[11px] font-bold ">
                <th className="py-3 px-4">Client</th><th className="py-3 px-4">Phone</th><th className="py-3 px-4 text-right">Balance</th>
              </tr></thead>
              <tbody className="divide-y divide-slate-100 font-medium text-ink-secondary">
                {!balances && <SkeletonRows cols={3} />}
                {balances?.length === 0 && <tr><td colSpan={3} className="py-6 px-4 text-ink-tertiary">Everyone is settled up.</td></tr>}
                {pageBal.map((b) => (
                  <tr key={b.id} className="hover:bg-blue-50/30">
                    <td className="py-2.5 px-4 font-bold text-ink-primary">{b.name}</td>
                    <td className="py-2.5 px-4 tabular-nums">{b.phone || "—"}</td>
                    <td className={`py-2.5 px-4 text-right tabular-nums font-bold ${(b.balance || 0) < 0 ? "text-rose-600" : "text-emerald-600"}`}>{(b.balance || 0) < 0 ? "Owes " : "Credit "}{money(Math.abs(b.balance || 0))}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {balances && balances.length > 0 && <Pager total={balances.length} page={Math.min(finPage, balPages)} pageSize={INV_PAGE_SIZE} onPage={setFinPage} noun="client balances" />}
          </div>
        )}
      </div>
      )}
    </Shell>
  );
}
