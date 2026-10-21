import { useEffect, useRef, useState } from "react";
import Shell, { M } from "../../components/Shell";
import SkeletonRows from "../../components/SkeletonRows";
import DateField from "../../components/DateField";
import CatalogPicker from "../../components/CatalogPicker";
import BulkPrintDialog from "../../components/BulkPrintDialog";
import Pager from "../../components/Pager";
import { useServerPage } from "../../hooks/useServerPage";
import { api, openAuthedDoc } from "../../api/client";
import { humanize } from "../../lib/humanize";
import { alertDialog, confirmDialog } from "../../lib/dialog";
import { useMe } from "../../context/Me";
import { money } from "../orders/types";
import InvoiceDrawer from "./InvoiceDrawer";
import CreditNotesList from "./CreditNotesList";
import { DISPLAY_MODES } from "./billing";

// Invoices are day-to-day front-desk work (issue, take payment, resend), so they get their own
// page next to Sales instead of a tab inside Finance — which also kept staff who only need
// invoices out of P&L and payroll. Finance links here from its Outstanding figure.
type Invoice = { id: string; invoice_number?: string; status?: string; total?: number; amount_paid?: number; credited?: number; balance_due?: number; order_number?: string; issued_at?: string; created_at?: string; client_name?: string | null; einvoice_status?: string; einvoice_number?: string };
const INV_PAGE_SIZE = 50;

export default function InvoicesHub() {
  // One server page at a time (the API serves at most 200 rows per request).
  const ip = useServerPage<Invoice>("/finance/invoices", {}, { pageSize: INV_PAGE_SIZE });
  const invoices = ip.rows;
  const [newInv, setNewInv] = useState(false);
  const [invSel, setInvSel] = useState<Set<string>>(new Set());
  const [invPrint, setInvPrint] = useState(false);
  // E-invoicing (Peppol, ZATCA, …) only where the shop's region has a standard; US/CA have none.
  const [einv, setEinv] = useState(false);
  const reloadInvoices = () => ip.reload();
  const { can } = useMe();
  // Deleting cancels an invoice nobody has paid on (the API refuses one with payments).
  async function deleteInvoice(i: Invoice) {
    if (!(await confirmDialog(`Delete invoice ${i.invoice_number}? It stays on record as cancelled and is no longer owed.`, { title: "Delete invoice", confirmLabel: "Delete", danger: true }))) return;
    try { await api.del(`/finance/invoices/${i.id}`); reloadInvoices(); }
    catch (e: any) { await alertDialog(e.message || "Could not delete the invoice"); }
  }
  useEffect(() => {
    api.get<any>("/regions/pack").then((p) => setEinv(!!p?.einvoice?.standard && p.einvoice.standard !== "none")).catch(() => setEinv(false));
  }, []);
  // Busy guard: the ref blocks a second click landing before React re-renders the disabled button.
  const [issuingId, setIssuingId] = useState<string | null>(null); const issuingRef = useRef(false);
  async function issueEinvoice(id: string) {
    if (issuingRef.current) return;
    issuingRef.current = true; setIssuingId(id);
    try { await api.post(`/finance/invoices/${id}/einvoice`, {}); await reloadInvoices(); }
    catch { await alertDialog("Could not issue e-invoice"); }
    finally { issuingRef.current = false; setIssuingId(null); }
  }
  async function markEinvoiceCleared(id: string) {
    try { await api.patch(`/finance/invoices/${id}/einvoice`, { status: "cleared" }); reloadInvoices(); }
    catch { await alertDialog("Could not update status"); }
  }
  const cols = einv ? 11 : 10;
  const pageInv = invoices || [];
  const [openInv, setOpenInv] = useState<string | null>(null);
  const [view, setView] = useState<"invoices" | "credit_notes">("invoices");

  return (
    <Shell title="Invoices">
      <div className="flex-1 overflow-y-auto px-4 sm:px-6 py-5 space-y-4">
          {can("can_view_credit_notes") && (
            <div className="flex gap-4 border-b border-slate-200 text-sm font-semibold" role="tablist" aria-label="Billing documents">
              {([["invoices", "Invoices"], ["credit_notes", "Credit notes"]] as const).map(([k, l]) => (
                <button key={k} role="tab" aria-selected={view === k} onClick={() => setView(k)}
                  className={`py-2 -mb-px border-b-2 ${view === k ? "text-primary border-primary" : "text-ink-tertiary border-transparent hover:text-ink-primary"}`}>{l}</button>
              ))}
            </div>
          )}
          {view === "credit_notes" ? <CreditNotesList /> : <>
          <div className="flex justify-end items-center gap-2">
            {invSel.size > 0 && (
              <button onClick={() => setInvPrint(true)} className="px-4 py-2 rounded-xl border border-slate-200 bg-white text-ink-secondary text-xs font-semibold hover:bg-slate-50 flex items-center gap-1.5"><M n="print" c="text-sm" />Print {invSel.size} selected</button>
            )}
            <button onClick={() => setNewInv(true)} className="px-4 py-2 rounded-xl bg-primary text-white text-xs font-semibold shadow-clay-sm hover:brightness-110 flex items-center gap-1.5"><M n="add" c="text-sm" />New invoice</button>
          </div>
          <div className="bg-white rounded-2xl border border-slate-200/90 shadow-clay-card overflow-x-auto mt-3">
            <table className="w-full text-left border-collapse text-xs min-w-[860px]">
              <thead><tr className="bg-slate-50/80 border-b border-slate-200 text-ink-tertiary text-[11px] font-bold ">
                <th className="py-3 pl-4 pr-2 w-10"><input type="checkbox" aria-label="Select all" checked={!!invoices?.length && invoices.every((i) => invSel.has(i.id))} onChange={(e) => setInvSel((s) => { const n = new Set(s); (invoices || []).forEach((i) => (e.target.checked ? n.add(i.id) : n.delete(i.id))); return n; })} className="w-4 h-4 rounded border-slate-300 text-primary cursor-pointer accent-primary" /></th>
                <th className="py-3 px-4">Invoice #</th><th className="py-3 px-4">Customer</th><th className="py-3 px-4">Date</th><th className="py-3 px-4">Job</th><th className="py-3 px-4">Status</th><th className="py-3 px-4 text-right">Total</th><th className="py-3 px-4 text-right">Paid</th><th className="py-3 px-4 text-right">Balance</th>{einv && <th className="py-3 px-4">E-invoice</th>}<th className="py-3 px-4"></th>
              </tr></thead>
              <tbody className="divide-y divide-slate-100 font-medium text-ink-secondary">
                {!invoices && !ip.err && <SkeletonRows cols={cols} />}
                {ip.err && <tr><td colSpan={cols} className="py-6 px-4 text-rose-600">{ip.err}</td></tr>}
                {invoices?.length === 0 && <tr><td colSpan={cols} className="py-6 px-4 text-ink-tertiary">No invoices.</td></tr>}
                {pageInv.map((i) => {
                  const paid = /paid/i.test(i.status || "");
                  return (
                    <tr key={i.id} onClick={() => setOpenInv(i.id)} className="hover:bg-blue-50/30 cursor-pointer">
                      <td className="py-2.5 pl-4 pr-2" onClick={(e) => e.stopPropagation()}><input type="checkbox" aria-label="Select row" checked={invSel.has(i.id)} onChange={() => setInvSel((s) => { const n = new Set(s); n.has(i.id) ? n.delete(i.id) : n.add(i.id); return n; })} className="w-4 h-4 rounded border-slate-300 text-primary cursor-pointer accent-primary" /></td>
                      <td className="py-2.5 px-4 font-mono font-bold text-primary"><button onClick={(e) => { e.stopPropagation(); setOpenInv(i.id); }} className="hover:underline">{i.invoice_number}</button></td>
                      <td className="py-2.5 px-4 font-semibold text-ink-primary">{i.client_name || <span className="font-normal text-ink-tertiary">Walk-in</span>}</td>
                      <td className="py-2.5 px-4 text-ink-secondary whitespace-nowrap">{(i.issued_at || i.created_at) ? new Date((i.issued_at || i.created_at)!).toLocaleDateString([], { month: "short", day: "numeric", year: "numeric" }) : "—"}</td>
                      <td className="py-2.5 px-4 font-mono">{i.order_number || "—"}</td>
                      <td className="py-2.5 px-4"><span className={`px-2 py-0.5 rounded-full text-[11px] font-bold capitalize ${paid ? "bg-emerald-100 text-emerald-800" : /partial/i.test(i.status || "") ? "bg-amber-100 text-amber-800" : "bg-slate-100 text-slate-700"}`}>{humanize(i.status)}</span></td>
                      <td className="py-2.5 px-4 text-right tabular-nums font-bold text-ink-primary">{money(i.total)}</td>
                      <td className="py-2.5 px-4 text-right tabular-nums text-emerald-600">{money(i.amount_paid)}</td>
                      <td className={`py-2.5 px-4 text-right tabular-nums font-bold ${(i.balance_due || 0) > 0 ? "text-rose-600" : "text-ink-tertiary"}`}>{money(i.balance_due)}</td>
                      {einv && <td className="py-2.5 px-4" onClick={(e) => e.stopPropagation()}>
                        {(!i.einvoice_status || i.einvoice_status === "none") ? (
                          <button onClick={() => issueEinvoice(i.id)} disabled={!!issuingId} className="px-2 py-1 rounded-lg border border-slate-200 bg-white text-ink-secondary text-[11px] font-semibold hover:bg-slate-50 disabled:opacity-50">{issuingId === i.id ? "Issuing…" : "Issue e-invoice"}</button>
                        ) : (
                          <div className="flex items-center gap-1.5">
                            <span className={`px-2 py-0.5 rounded-full text-[11px] font-bold capitalize ${i.einvoice_status === "cleared" ? "bg-emerald-100 text-emerald-800" : i.einvoice_status === "rejected" ? "bg-rose-100 text-rose-800" : "bg-sky-100 text-sky-800"}`}>{humanize(i.einvoice_status)}</span>
                            <button aria-label="Download XML" onClick={() => openAuthedDoc(`/finance/invoices/${i.id}/einvoice.xml`).catch(() => alertDialog("Could not open e-invoice"))} title="Download XML" className="p-1 rounded-lg text-ink-tertiary hover:text-primary hover:bg-slate-100"><M n="download" c="text-sm" /></button>
                            {i.einvoice_status === "issued" && <button aria-label="Mark cleared" onClick={() => markEinvoiceCleared(i.id)} title="Mark cleared" className="p-1 rounded-lg text-ink-tertiary hover:text-emerald-600 hover:bg-slate-100"><M n="check" c="text-sm" /></button>}
                          </div>
                        )}
                      </td>}
                      <td className="py-2.5 px-4 text-right" onClick={(e) => e.stopPropagation()}><button aria-label="Print / PDF" onClick={() => openAuthedDoc(`/print/invoices/${i.id}`).catch(() => alertDialog("Could not open invoice PDF"))} title="Print / PDF" className="p-1.5 rounded-lg text-ink-tertiary hover:text-primary hover:bg-slate-100"><M n="print" c="text-base" /></button>
                        {can("can_delete_invoices") && !["cancelled", "canceled"].includes(i.status || "") && !(i.amount_paid || 0) && !(i.credited || 0) && <button aria-label="Delete invoice" onClick={() => deleteInvoice(i)} title="Delete" className="p-1.5 rounded-lg text-ink-tertiary hover:text-rose-600 hover:bg-rose-50"><M n="delete" c="text-base" /></button>}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            {invoices && invoices.length > 0 && <Pager total={ip.total} page={ip.page} pageSize={ip.pageSize} onPage={ip.setPage} noun="invoices" />}
          </div>
          </>}
      </div>
      {openInv && <InvoiceDrawer id={openInv} onClose={() => setOpenInv(null)} onChanged={reloadInvoices} />}
      {newInv && <NewInvoiceDrawer onClose={() => setNewInv(false)} onCreated={() => { setNewInv(false); reloadInvoices(); }} />}
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
  const [mode, setMode] = useState("detailed");   // starts at the shop's default (Settings › Finance)
  const [busy, setBusy] = useState(false); const [err, setErr] = useState<string | null>(null);
  useEffect(() => { api.get<{ items: any[] }>("/clients?limit=200").then((r) => setClients(r.items || [])).catch(() => {}); }, []);
  useEffect(() => { api.get<any>("/staff/company").then((c) => { if (c?.default_invoice_display) setMode(c.default_invoice_display); }).catch(() => {}); }, []);
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
      await api.post("/finance/invoices", { client_id: clientId || null, comment, due_at: due || null, display_mode: mode,
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
            <div><label className="block text-[11px] font-semibold text-ink-tertiary mb-1">Client</label>
              <select value={clientId} onChange={(e) => setClientId(e.target.value)} className="w-full bg-white border border-slate-200 rounded-lg text-sm px-3 py-2"><option value="">Walk-in / none</option>{clients.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}</select></div>
            <div><label className="block text-[11px] font-semibold text-ink-tertiary mb-1">Due date</label>
              <DateField mode="date" value={due} onChange={setDue} /></div>
          </div>
          <div>
            <div className="mb-2"><CatalogPicker placeholder="Add from catalog (service / product)…" onPick={(i) => setLines((ls) => [...ls.filter((l) => l.description.trim() || l.unit_price), { kind: i.kind, description: i.name, qty: 1, unit_price: i.price || 0 }])} /></div>
            <div className="flex items-center justify-between mb-1"><label className="text-[11px] font-semibold text-ink-tertiary ">Lines</label><button onClick={() => setLines([...lines, { kind: "custom", description: "", qty: 1, unit_price: 0 }])} className="text-primary text-xs font-semibold flex items-center gap-1"><M n="add" c="text-sm" />Add line</button></div>
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
              <div><label className="block text-[11px] font-semibold text-ink-tertiary mb-1">Discount %</label><input type="number" min={0} max={100} value={discount} onChange={(e) => setDiscount(Number(e.target.value))} className="w-full bg-white border border-slate-200 rounded-lg text-sm px-3 py-2 text-right" /></div>
              <div><label className="block text-[11px] font-semibold text-ink-tertiary mb-1">Tax %</label><input type="number" min={0} value={tax} onChange={(e) => setTax(Number(e.target.value))} className="w-full bg-white border border-slate-200 rounded-lg text-sm px-3 py-2 text-right" /></div>
            </div>
            <div className="mt-3 rounded-xl bg-slate-50 border border-slate-200 p-3 space-y-1.5 text-sm">
              <div className="flex justify-between text-ink-secondary"><span>Subtotal</span><span className="tabular-nums font-semibold text-ink-primary">{money(subtotal)}</span></div>
              {discountAmt > 0 && <div className="flex justify-between text-ink-secondary"><span>Discount ({discount}%)</span><span className="tabular-nums text-rose-600">−{money(discountAmt)}</span></div>}
              {taxAmt > 0 && <div className="flex justify-between text-ink-secondary"><span>Tax ({tax}%)</span><span className="tabular-nums text-ink-primary">{money(taxAmt)}</span></div>}
              <div className="pt-1.5 border-t border-slate-200 flex justify-between items-baseline"><span className="font-bold text-ink-primary">Total</span><span className="tabular-nums font-bold text-primary">{money(total)}</span></div>
            </div>
          </div>
          <div>
            <span className="block text-[11px] font-semibold text-ink-tertiary mb-1">Display on the invoice</span>
            <div role="radiogroup" aria-label="Display on the invoice" className="grid grid-cols-3 gap-1 rounded-xl bg-slate-100 p-1">
              {DISPLAY_MODES.map((m) => <button key={m.key} type="button" role="radio" aria-checked={mode === m.key} title={m.hint} onClick={() => setMode(m.key)}
                className={`px-2 py-1.5 rounded-lg text-xs font-semibold ${mode === m.key ? "bg-white text-primary shadow-sm" : "text-ink-secondary hover:text-ink-primary"}`}>{m.label}</button>)}
            </div>
          </div>
          <div><label className="block text-[11px] font-semibold text-ink-tertiary mb-1">Comment</label><textarea value={comment} onChange={(e) => setComment(e.target.value)} rows={2} className="w-full bg-white border border-slate-200 rounded-lg text-sm px-3 py-2" /></div>
        </div>
        <div className="px-5 py-4 border-t border-slate-200 flex justify-end gap-2"><button onClick={onClose} className="px-4 py-2 rounded-lg border border-slate-200 text-ink-secondary hover:bg-slate-100 font-semibold text-xs">Cancel</button><button onClick={save} disabled={busy} className="px-4 py-2 rounded-lg bg-primary text-white font-semibold text-xs hover:bg-primary-container disabled:opacity-60">{busy ? "Saving…" : "Create invoice"}</button></div>
      </div>
    </div>
  );
}
