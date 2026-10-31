import SkeletonRows from "../../components/SkeletonRows";
import PageSummary, { type SummaryStat } from "../../components/PageSummary";
import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import Shell, { M } from "../../components/Shell";
import DateField from "../../components/DateField";
import { api, openAuthedDoc } from "../../api/client";
import Pager from "../../components/Pager";
import { money } from "../orders/types";
import { btnGhost, btnPrimary } from "../invoices/billing";
import { fmtDay, fmtServerTime } from "../../lib/serverTime";
import { balanceView } from "../../lib/balance";
import { alertDialog, confirmDialog, promptDialog, selectDialog } from "../../lib/dialog";
import { isoMonth } from "../../lib/localDate";
import { useMe } from "../../context/Me";
import CashPanel from "../cash/CashPanel";
import VoidableList from "../../components/VoidableList";
import PayrollCalcPanel from "./PayrollCalcPanel";
import { loadFailed } from "../../lib/notice";

type Tab = "cashboxes" | "overview" | "pnl" | "reconcile" | "balances" | "refunds" | "payroll";
type Account = { id: string; name?: string; balance?: number; payment_option?: string; is_virtual?: boolean };
type Balance = { id: string; name?: string; phone?: string; balance?: number };

// A raw API value ("high", "draft") as a sentence-case label ("High", "Draft").
const sentence = (s?: string | null) => { const t = String(s || "").replace(/[_-]+/g, " ").trim().toLowerCase(); return t ? t[0].toUpperCase() + t.slice(1) : ""; };
// "Oct 1 – Oct 31, 2026": a date range in the app's one date format, the year said once when shared.
export function dayRange(from?: string | null, to?: string | null): string {
  const a = fmtDay(from), b = fmtDay(to);
  if (!a || !b) return a || b || "—";
  return `${from?.slice(0, 4) === to?.slice(0, 4) ? a.replace(/,? \d{4}$/, "") : a} – ${b}`;
}

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
  const [runs, setRuns] = useState<any[] | null>(null);
  const [runBusy, setRunBusy] = useState(false);
  const [month, setMonth] = useState<string>(() => isoMonth());
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
  const cur = (n?: number) => money(n, undefined, true);   // same formatter as Overview
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
      // The pay leaves a real cashbox: it's posted there as a Payroll expense (and shows in the P&L).
      const tills = accounts.filter((a) => !a.is_virtual);
      if (!tills.length) { await alertDialog("Add a cashbox or bank account first. The pay is taken out of it."); return; }
      const labels = tills.map((a, i) => `${a.name || "Account"}${tills.findIndex((b) => b.name === a.name) !== i ? ` (${i + 1})` : ""}`);
      const picked = tills.length === 1 ? labels[0] : await selectDialog("Which account is the pay coming out of?", labels, { title: "Mark paid", confirmLabel: "Next" });
      if (!picked) return;
      const acct = tills[labels.indexOf(picked)];
      if (!(await confirmDialog(`Mark this payroll run as paid from ${picked}? This posts the payout and takes the money out of that account.`, { title: "Mark paid", confirmLabel: "Mark paid" }))) return;
      setPayingId(id);
      await api.post(`/payroll/runs/${id}/pay`, { account_id: acct.id });
      loadRuns();
      api.get<any>("/cash/summary").then(setCash).catch(loadFailed("the cash summary"));
    } catch (e: any) { await alertDialog(`Could not mark paid: ${e.message}`); } finally { payingRef.current = false; setPayingId(null); }
  }
  async function voidRun(id: string) {
    if (payingRef.current) return;
    payingRef.current = true;
    try {
      const reason = await promptDialog("Why are you voiding this run? Its pay comes back off the employee's balance, and a payout goes back into its account.", { title: "Void payroll run", confirmLabel: "Void run" });
      if (!reason?.trim()) return;
      setPayingId(id);
      await api.post(`/payroll/runs/${id}/void`, { reason: reason.trim() });
      loadRuns();
      api.get<any>("/cash/summary").then(setCash).catch(loadFailed("the cash summary"));
    } catch (e: any) { await alertDialog(`Could not void the run: ${e.message}`); } finally { payingRef.current = false; setPayingId(null); }
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
        <nav className="flex space-x-6 -mb-px text-xs font-semibold overflow-x-auto whitespace-nowrap">
          {([...(can("can_view_cashbox") ? [["cashboxes", "Cashboxes"]] as [Tab, string][] : []), ["overview", "Overview"], ["pnl", "Profit & loss"], ["reconcile", "Reconcile"], ["balances", "Client balances"], ["refunds", "Refunds"], ["payroll", "Payroll"]] as [Tab, string][]).map(([id, label]) => (
            <button key={id} onClick={() => setTab(id)} className={`pb-3 min-w-[24px] border-b-2 transition-colors ${tab === id ? "text-primary font-bold border-primary" : "text-ink-secondary hover:text-ink-primary border-transparent"}`}>{label}</button>
          ))}
        </nav>
      </div>

      {tab === "cashboxes" ? <CashPanel /> : (
      <div className="flex-1 overflow-y-auto px-6 py-5 space-y-4">
        {tab === "overview" && (<>
          {can("can_export_accountant") && (
            <div className="space-y-2.5">
              <div><h3 className="text-sm font-bold text-ink-primary">Since you started</h3><p className="text-xs text-ink-tertiary">Paid invoices, money still owed and expenses, all time. Pick dates to export a period for your accountant.</p></div>
              <div className="flex items-center gap-1.5 flex-wrap">
                <button onClick={() => { const qs = [exFrom && `date_from=${exFrom}`, exTo && `date_to=${exTo}`].filter(Boolean).join("&"); openAuthedDoc(`/finance/export/accountant.csv${qs ? `?${qs}` : ""}`).catch(() => alertDialog("Could not export.")); }} className={btnGhost}><M n="download" c="text-sm" />Export for accountant</button>
                <DateField mode="date" value={exFrom} onChange={setExFrom} placeholder="From" className="w-36" />
                <span className="text-ink-tertiary text-xs">→</span>
                <DateField mode="date" value={exTo} onChange={setExTo} placeholder="To" className="w-36" />
              </div>
            </div>
          )}
          <PageSummary stats={kpiStats} />
          <div className="bg-white rounded-2xl border border-slate-200/90 shadow-clay-card overflow-hidden">
            <div className="p-3.5 border-b border-slate-100 text-xs font-bold text-ink-primary ">Cash accounts</div>
            <div className="divide-y divide-slate-100">
              {accounts.length === 0 && <div className="p-6 text-xs text-ink-tertiary flex flex-col items-start gap-3"><span>No cash accounts yet. Add your till or bank account to see what's in it.</span>{can("can_view_cashbox") && <button onClick={() => setTab("cashboxes")} className={btnPrimary}><M n="add" c="text-sm" />Add a cashbox</button>}</div>}
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
            <div className="bg-white rounded-2xl border border-slate-200/90 shadow-clay-sm p-3.5 space-y-2.5">
              <div className="flex items-baseline gap-3 flex-wrap">
                <h3 className="text-sm font-bold text-ink-primary">Profit &amp; loss</h3>
                <span className="text-[11px] text-ink-tertiary">Did the shop make money?</span>
              </div>
              <div className="flex items-center gap-2 text-xs flex-wrap">
                <DateField mode="date" value={pFrom} onChange={setPFrom} className="w-36" />
                <span className="text-ink-tertiary">→</span>
                <DateField mode="date" value={pTo} onChange={setPTo} className="w-36" />
                <button onClick={loadPnl} className={btnPrimary}>Update</button>
              </div>
            </div>
            {pnlBusy && <div className="p-8 text-xs text-ink-tertiary text-center">Calculating…</div>}
            {!pnlBusy && pnl && (
              <>
                <div className={`rounded-2xl border shadow-clay-card p-5 ${pnl.net_profit >= 0 ? "bg-emerald-50/60 border-emerald-200" : "bg-rose-50/60 border-rose-200"}`}>
                  <p className="text-[11px] font-bold text-ink-tertiary">Net profit {pnl.net_profit >= 0 ? "(you made money)" : "(you're in the red)"}</p>
                  <p className={`text-3xl font-bold tabular-nums ${pnl.net_profit >= 0 ? "text-emerald-700" : "text-rose-700"}`}>{cur(pnl.net_profit)}</p>
                  <p className="text-[11px] text-ink-tertiary mt-1">Net margin {pnl.net_margin_pct}% · {pnl.invoice_count} invoices · {dayRange(pnl.date_from, pnl.date_to)}</p>
                </div>
                <div className="bg-white rounded-2xl border border-slate-200/90 shadow-clay-card p-4 text-xs text-ink-secondary max-w-2xl">
                  <div className="flex justify-between py-1.5 border-b border-slate-100"><span>Net revenue (ex-tax)</span><span className="tabular-nums font-semibold text-ink-primary">{cur(pnl.revenue)}</span></div>
                  <div className="flex justify-between py-1.5 border-b border-slate-100"><span>− Cost of parts (COGS)</span><span className="tabular-nums text-rose-600">−{cur(pnl.cogs)}</span></div>
                  <div className="flex justify-between py-1.5 border-b border-slate-100 font-bold text-ink-primary"><span>= Gross profit</span><span className="tabular-nums">{cur(pnl.gross_profit)} <span className="text-[11px] text-ink-tertiary">({pnl.gross_margin_pct}%)</span></span></div>
                  <div className="flex justify-between py-1.5 border-b border-slate-100"><span>− Operating expenses</span><span className="tabular-nums text-rose-600">−{cur(pnl.opex)}</span></div>
                  <div className="flex justify-between py-1.5 border-b border-slate-100"><span>− Processing fees (card/online)</span><span className="tabular-nums text-rose-600">−{cur(pnl.fees)}</span></div>
                  <div className="flex justify-between py-2 font-bold text-sm text-ink-primary"><span>= Net profit</span><span className={`tabular-nums ${pnl.net_profit >= 0 ? "text-emerald-700" : "text-rose-700"}`}>{cur(pnl.net_profit)}</span></div>
                  <div className="flex justify-between pt-2 mt-1 border-t border-slate-100 text-ink-tertiary"><span>Tax collected (not counted as revenue)</span><span className="tabular-nums">{cur(pnl.tax_collected)}</span></div>
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
            </div>
            <button onClick={recordPayout} className={btnGhost}><M n="account_balance" c="text-sm" />Record payout</button>
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
                    <span className={`text-[11px] px-1.5 py-0.5 rounded font-bold  ${it.severity === "high" ? "bg-rose-100 text-rose-700" : "bg-amber-100 text-amber-700"}`}>{sentence(it.severity)}</span></div>
                  <p className="text-xs text-ink-secondary mt-0.5">{it.detail}</p>
                </div>
              </div>
            ))}
          </div>
        )}


        {tab === "payroll" && (
          <div className="space-y-3">
            <PayrollCalcPanel month={month} onMonth={setMonth}
              headerAction={can("can_accrue_payroll") && <button onClick={runPayroll} disabled={runBusy} className={btnPrimary}><M n="save" c="text-sm" />{runBusy ? "Running…" : "Run payroll (draft)"}</button>} />

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
                    const paid = r.status === "paid";
                    const voided = r.status === "void";
                    return (
                      <tr key={r.id} className="hover:bg-blue-50/30">
                        <td className="py-2.5 px-4 font-semibold text-ink-primary">{dayRange(r.period_start, r.period_end)}</td>
                        <td className="py-2.5 px-4"><span className={`px-2 py-0.5 rounded-full text-[11px] font-bold ${paid ? "bg-emerald-100 text-emerald-800" : voided ? "bg-slate-100 text-ink-tertiary" : "bg-amber-100 text-amber-800"}`} title={voided ? r.void_reason || undefined : undefined}>{voided ? "Voided" : sentence(r.status || "draft")}</span></td>
                        <td className="py-2.5 px-4 text-right tabular-nums font-bold text-ink-primary">{money(r.totals?.gross_total ?? r.gross_total)}</td>
                        <td className="py-2.5 px-4 text-ink-tertiary">{r.accrued_at ? fmtServerTime(r.accrued_at, { month: "short", day: "numeric", year: "numeric" }) : "—"}</td>
                        <td className="py-2.5 px-4 text-right space-x-3">
                          {!paid && !voided && <button onClick={() => markPaid(r.id)} disabled={!!payingId} className="text-primary font-semibold hover:underline disabled:opacity-50 disabled:no-underline">{payingId === r.id ? "Marking…" : "Mark paid"}</button>}
                          {!voided && r.status !== "draft" && <button onClick={() => voidRun(r.id)} disabled={!!payingId} className="text-rose-600 font-semibold hover:underline disabled:opacity-50 disabled:no-underline">Void</button>}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {tab === "refunds" && (
          <div className="bg-white rounded-2xl border border-slate-200/90 shadow-clay-card overflow-hidden">
            <VoidableList perm="can_delete_refunds" noun="refund" empty="No refunds yet." onFlash={(m) => { alertDialog(m); }}
              deletePath={(id) => `/returns/refunds/${id}`}
              load={() => api.get<{ items: any[] }>("/returns/refunds").then((r) => (r.items || []).map((x) => ({
                id: x.id, title: x.order_number ? `Order ${x.order_number}` : "Refund", sub: [x.account_name, x.reason].filter(Boolean).join(" · "),
                amount: x.amount, created_at: x.created_at, voided_at: x.voided_at, void_reason: x.void_reason })))} />
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
                    {(() => { const bv = balanceView(b.balance); return <td className={`py-2.5 px-4 text-right tabular-nums font-bold ${bv.className}`}>{bv.label} {money(bv.amount)}</td>; })()}
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
