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
import InvoiceDrawer from "./InvoiceDrawer";
import CreditNotesList, { CN_FILTERS, type CnStatus } from "./CreditNotesList";
import PageLayout, { Toolbar } from "../../components/PageLayout";
import Tabs from "../../components/Tabs";
import Button, { IconButton } from "../../components/Button";
import Card, { Badge } from "../../components/Card";
import EmptyState, { ErrorState } from "../../components/EmptyState";
import { Td, Th, tableClass, theadRowClass } from "../../components/Table";
import { Avatar } from "../orders/look";
import { DisplayModes, Drawer, cash, ErrorNote, MoreMenu, SearchBox, StatusPill, daysLate, fmtDate, inputCls, isVoid, labelCls, lateText, sendInvoice, type MenuItem } from "./billing";
import { loadFailed } from "../../lib/notice";
import { clickableRow } from "../../lib/clickable";

// Invoices are day-to-day front-desk work (issue, take payment, resend), so they get their own
// page next to Sales instead of a tab inside Finance — which also kept staff who only need
// invoices out of P&L and payroll. Finance links here from its Outstanding figure.
type Invoice = { id: string; invoice_number?: string; status?: string; total?: number; amount_paid?: number; credited?: number; balance_due?: number; order_number?: string; issued_at?: string; created_at?: string; due_at?: string | null; client_name?: string | null; einvoice_status?: string; einvoice_number?: string };
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
  // I6 — send from the list; the answer says plainly if it didn't reach the customer.
  async function sendFromList(i: Invoice, way: "email" | "sms") {
    try { const r = await sendInvoice(i.id, way); await alertDialog(r.text, r.tone ? "Not sent" : "Invoice sent"); reloadInvoices(); }
    catch (e: any) { await alertDialog(e.message || "Couldn't send the invoice"); }
  }
  // Extra row actions live in "⋮"; the row itself opens the invoice.
  // On phones the e-invoice column isn't there, so its actions join the menu.
  const rowActions = (i: Invoice, phone = false): MenuItem[] => [
    ...(phone && einv ? [
      { label: "Issue e-invoice", icon: "send", onClick: () => { issueEinvoice(i.id); }, hidden: !!i.einvoice_status && i.einvoice_status !== "none", disabled: !!issuingId },
      { label: "Download e-invoice XML", icon: "download", onClick: () => { openAuthedDoc(`/finance/invoices/${i.id}/einvoice.xml`).catch(() => alertDialog("Could not open e-invoice")); }, hidden: !i.einvoice_status || i.einvoice_status === "none" },
      { label: "Mark e-invoice cleared", icon: "check", onClick: () => { markEinvoiceCleared(i.id); }, hidden: i.einvoice_status !== "issued" },
    ] : []),
    { label: "Send by email", icon: "mail", onClick: () => { sendFromList(i, "email"); }, hidden: isVoid(i.status) },
    { label: "Send by text", icon: "sms", onClick: () => { sendFromList(i, "sms"); }, hidden: isVoid(i.status) },
    { label: "Print / PDF", icon: "print", onClick: () => { openAuthedDoc(`/print/invoices/${i.id}`).catch(() => alertDialog("Could not open invoice PDF")); } },
    { label: "Delete invoice", icon: "delete", danger: true, onClick: () => { deleteInvoice(i); },
      hidden: !can("can_delete_invoices") || ["cancelled", "canceled"].includes(i.status || "") || !!(i.amount_paid || 0) || !!(i.credited || 0) },
  ];
  const cols = einv ? 11 : 10;
  const pageInv = invoices || [];
  const [openInv, setOpenInv] = useState<string | null>(null);
  const [view, setView] = useState<"invoices" | "credit_notes">("invoices");
  // Credit notes' filters live in the page toolbar, so the toolbar is the same on both tabs.
  const [cnStatus, setCnStatus] = useState<CnStatus>("");
  const [cnQ, setCnQ] = useState("");
  const [cnCreating, setCnCreating] = useState(false);

  const seeNotes = can("can_view_credit_notes");
  const onNotes = view === "credit_notes";

  return (
    <Shell title="Invoices">
      <PageLayout>
          {seeNotes && (
            <Tabs look="underline" label="Billing documents" value={view} onChange={setView}
              tabs={[{ id: "invoices", label: "Invoices" }, { id: "credit_notes", label: "Credit notes" }]} />
          )}
          {/* One toolbar for both tabs: what doesn't apply on a tab stays in place, greyed. */}
          <Toolbar
            actions={<>
              {onNotes
                ? <Button variant="primary" icon={<M n="add" c="text-sm" />} onClick={() => setCnCreating(true)} disabled={!can("can_create_credit_notes")}>New credit note</Button>
                : <Button variant="primary" icon={<M n="add" c="text-sm" />} onClick={() => setNewInv(true)}>New invoice</Button>}
              <Button icon={<M n="print" c="text-sm" />} onClick={() => setInvPrint(true)} disabled={onNotes || invSel.size === 0}>
                {invSel.size > 0 && !onNotes ? `Print ${invSel.size} selected` : "Print selected"}</Button>
            </>}
            filters={seeNotes ? <Tabs look="chips" label="Credit note status" value={cnStatus} onChange={setCnStatus}
              tabs={CN_FILTERS.map((f) => ({ ...f, disabled: !onNotes }))} /> : undefined}
            search={seeNotes ? <SearchBox value={cnQ} onChange={setCnQ} label="Search credit notes" placeholder="Search number, client, invoice" disabled={!onNotes} /> : undefined}
          />
          {onNotes ? <CreditNotesList status={cnStatus} q={cnQ} creating={cnCreating} onCreatingDone={() => setCnCreating(false)} /> : <>
          {ip.err ? (
            <Card padded={false}><ErrorState title="Invoices didn't load" body={ip.err} onRetry={reloadInvoices} /></Card>
          ) : invoices?.length === 0 ? (
            <Card padded={false}>
              <EmptyState icon={<M n="receipt_long" c="text-xl" />} title="No invoices yet"
                body="Invoices you send show here, with what each customer has paid and what they still owe." />
            </Card>
          ) : (
          <Card padded={false}>
            {/* Phones: one card per invoice instead of the wide table. */}
            <ul className="sm:hidden divide-y divide-slate-100" aria-label="Invoices">
              {!invoices && <li className="p-4 text-xs text-ink-tertiary">Loading invoices…</li>}
              {pageInv.map((i) => {
                const late = daysLate(i);
                return (
                  <li key={i.id} className="flex items-start">
                    <button onClick={() => setOpenInv(i.id)} className="flex-1 min-w-0 text-left pl-4 pr-1 py-3 flex flex-col gap-1 hover:bg-slate-50/80">
                      <span className="flex items-center gap-2">
                        <span className="tabular-nums font-semibold text-primary text-xs whitespace-nowrap">{i.invoice_number}</span>
                        <StatusPill status={i.status} label={humanize(i.status)} />
                        <span className="ml-auto text-xs text-ink-tertiary whitespace-nowrap">{fmtDate(i.issued_at || i.created_at)}</span>
                      </span>
                      <span className="flex items-baseline gap-2">
                        <span className="text-sm font-semibold text-ink-primary truncate">{i.client_name || "Walk-in"}</span>
                        <span className="ml-auto tabular-nums font-semibold text-ink-primary text-sm">{cash(i.total)}</span>
                      </span>
                      <span className="flex items-baseline gap-2 text-xs">
                        <span className="text-ink-tertiary truncate">{i.order_number ? <span className="tabular-nums">{i.order_number}</span> : "No job"}</span>
                        <span className={`ml-auto tabular-nums whitespace-nowrap ${late ? "text-rose-600 font-semibold" : "text-ink-tertiary"}`}>
                          {(i.balance_due || 0) > 0 ? <>{cash(i.balance_due)} owed{late ? ` · ${lateText(late)}` : ""}</> : "Nothing owed"}
                        </span>
                      </span>
                    </button>
                    <MoreMenu className="mt-2 mr-2" label={`More actions for ${i.invoice_number}`} items={rowActions(i, true)} />
                  </li>
                );
              })}
            </ul>
            <div className="hidden sm:block overflow-x-auto">
            <table className={`${tableClass} min-w-[820px]`}>
              <thead><tr className={theadRowClass}>
                <Th className="pl-4 pr-2 w-10"><input type="checkbox" aria-label="Select all" checked={!!invoices?.length && invoices.every((i) => invSel.has(i.id))} onChange={(e) => setInvSel((s) => { const n = new Set(s); (invoices || []).forEach((i) => (e.target.checked ? n.add(i.id) : n.delete(i.id))); return n; })} className="w-4 h-4 rounded border-slate-300 text-primary cursor-pointer accent-primary" /></Th>
                <Th>Invoice</Th><Th>Customer</Th><Th>Date</Th><Th>Job</Th><Th>Status</Th><Th num>Total</Th><Th num>Paid</Th><Th num>Balance</Th>{einv && <Th>E-invoice</Th>}<Th className="px-2 w-12"><span className="sr-only">Actions</span></Th>
              </tr></thead>
              <tbody className="divide-y divide-slate-100 text-ink-secondary">
                {!invoices && <SkeletonRows cols={cols} />}
                {pageInv.map((i) => {
                  const late = daysLate(i);
                  const owed = (i.balance_due || 0) > 0;
                  return (
                    <tr key={i.id} {...clickableRow(() => setOpenInv(i.id))} className={`hover:bg-slate-50/80 cursor-pointer ${invSel.has(i.id) ? "bg-primary/5" : ""}`}>
                      <td className="py-2.5 pl-4 pr-2" onClick={(e) => e.stopPropagation()}><input type="checkbox" aria-label="Select row" checked={invSel.has(i.id)} onChange={() => setInvSel((s) => { const n = new Set(s); n.has(i.id) ? n.delete(i.id) : n.add(i.id); return n; })} className="w-4 h-4 rounded border-slate-300 text-primary cursor-pointer accent-primary" /></td>
                      <td className="py-2.5 px-4 tabular-nums font-semibold text-primary whitespace-nowrap"><button onClick={(e) => { e.stopPropagation(); setOpenInv(i.id); }} className="hover:underline">{i.invoice_number}</button></td>
                      <td className="py-2.5 px-4">{i.client_name
                        ? <span className="flex items-center gap-2 min-w-0"><Avatar name={i.client_name} size={24} /><span className="font-semibold text-ink-primary truncate">{i.client_name}</span></span>
                        : <span className="flex items-center gap-2 text-ink-tertiary"><Avatar name={null} size={24} />Walk-in</span>}</td>
                      <td className="py-2.5 px-4 whitespace-nowrap tabular-nums">{fmtDate(i.issued_at || i.created_at)}</td>
                      <td className="py-2.5 px-4 tabular-nums text-ink-tertiary">{i.order_number || "—"}</td>
                      <td className="py-2.5 px-4"><StatusPill status={i.status} label={humanize(i.status)} /></td>
                      <Td num className="py-2.5 font-semibold text-ink-primary">{cash(i.total)}</Td>
                      <Td num className="py-2.5 text-ink-secondary">{cash(i.amount_paid)}</Td>
                      <Td num className="py-2.5">
                        <span data-testid="row-balance" className={`block tabular-nums ${late ? "font-semibold text-rose-600" : owed ? "font-semibold text-ink-primary" : "text-ink-tertiary"}`}>{cash(i.balance_due)}</span>
                        {late > 0 && <span className="block text-xs text-rose-600">{lateText(late)}</span>}
                      </Td>
                      {einv && <td className="py-2.5 px-4" onClick={(e) => e.stopPropagation()}>
                        {(!i.einvoice_status || i.einvoice_status === "none") ? (
                          <Button size="sm" onClick={() => issueEinvoice(i.id)} disabled={!!issuingId}>{issuingId === i.id ? "Issuing…" : "Issue e-invoice"}</Button>
                        ) : (
                          <div className="flex items-center gap-1.5">
                            <Badge tone={i.einvoice_status === "cleared" ? "success" : i.einvoice_status === "rejected" ? "danger" : "neutral"}>{humanize(i.einvoice_status)}</Badge>
                            <IconButton size="sm" label="Download XML" onClick={() => openAuthedDoc(`/finance/invoices/${i.id}/einvoice.xml`).catch(() => alertDialog("Could not open e-invoice"))} icon={<M n="download" c="text-sm" />} />
                            {i.einvoice_status === "issued" && <IconButton size="sm" label="Mark cleared" onClick={() => markEinvoiceCleared(i.id)} icon={<M n="check" c="text-sm" />} />}
                          </div>
                        )}
                      </td>}
                      <td className="py-1.5 px-2 text-right" onClick={(e) => e.stopPropagation()}><MoreMenu className="inline-block" label={`More actions for ${i.invoice_number}`} items={rowActions(i)} /></td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            </div>
            {invoices && invoices.length > 0 && <Pager total={ip.total} page={ip.page} pageSize={ip.pageSize} onPage={ip.setPage} noun="invoices" />}
          </Card>
          )}
          </>}
      </PageLayout>
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
  const [discount, setDiscount] = useState(0); const [tax, setTax] = useState(0); // percentages; the invoice stores the amounts
  const [mode, setMode] = useState("detailed");   // starts at the shop's default (Settings › Finance)
  const [busy, setBusy] = useState(false); const [err, setErr] = useState<string | null>(null);
  useEffect(() => { api.get<{ items: any[] }>("/clients?limit=200").then((r) => setClients(r.items || [])).catch(loadFailed("clients")); }, []);
  useEffect(() => { api.get<any>("/staff/company").then((c) => { if (c?.default_invoice_display) setMode(c.default_invoice_display); }).catch(() => { /* silent: defaults to the standard layout */ }); }, []);
  const setLine = (i: number, k: string, v: any) => setLines(lines.map((l, j) => j === i ? { ...l, [k]: v } : l));
  const subtotal = lines.reduce((s, l) => s + (Number(l.qty) || 0) * (Number(l.unit_price) || 0), 0);
  const discountAmt = +(subtotal * (Number(discount) || 0) / 100).toFixed(2);
  const taxAmt = +((subtotal - discountAmt) * (Number(tax) || 0) / 100).toFixed(2);
  const total = +(subtotal - discountAmt + taxAmt).toFixed(2); // matches backend: subtotal − discount + tax
  async function save() {
    const clean = lines.filter((l) => l.description.trim() && l.qty > 0);
    if (!clean.length) { setErr("Add at least one line."); return; }
    setBusy(true); setErr(null);
    try {
      await api.post("/finance/invoices", { client_id: clientId || null, comment, due_at: due || null, display_mode: mode,
        discount_pct: Number(discount) || 0, tax_pct: Number(tax) || 0,
        lines: clean.map((l) => ({ kind: l.kind || "custom", description: l.description.trim(), qty: Number(l.qty), unit_price: Number(l.unit_price) })) });
      onCreated();
    } catch (e: any) { setErr(e.message || "Could not create invoice"); setBusy(false); }
  }
  return (
    <Drawer label="New invoice" title="New invoice" width={520} onClose={onClose}
      footer={<><Button variant="primary" onClick={save} disabled={busy}>{busy ? "Saving…" : "Create invoice"}</Button><Button onClick={onClose}>Cancel</Button></>}>
      <ErrorNote msg={err} />
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        <div><label htmlFor="ni-client" className={labelCls}>Client</label>
          <select id="ni-client" value={clientId} onChange={(e) => setClientId(e.target.value)} className={inputCls}><option value="">Walk-in / none</option>{clients.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}</select></div>
        <div><span className={labelCls}>Due date</span>
          <DateField mode="date" value={due} onChange={setDue} /></div>
      </div>
      <div>
        <div className="mb-2"><CatalogPicker placeholder="Add from catalog (service / product)…" onPick={(i) => setLines((ls) => [...ls.filter((l) => l.description.trim() || l.unit_price), { kind: i.kind, description: i.name, qty: 1, unit_price: i.price || 0 }])} /></div>
        <div className="flex items-center justify-between mb-1"><span className={labelCls}>Lines</span><Button variant="ghost" size="sm" className="text-primary" icon={<M n="add" c="text-sm" />} onClick={() => setLines([...lines, { kind: "custom", description: "", qty: 1, unit_price: 0 }])}>Add line</Button></div>
        <div className="space-y-2">
          {lines.map((l, i) => (
            <div key={i} className="grid grid-cols-[auto_4rem_1fr_auto] sm:flex items-center gap-2 pb-2 border-b border-slate-100 last:border-0 sm:pb-0 sm:border-0">
              <select value={l.kind} onChange={(e) => setLine(i, "kind", e.target.value)} title="Line type" aria-label={`Line ${i + 1} type`} className={`${inputCls} !w-auto !px-2`}><option value="custom">Custom</option><option value="service">Service</option><option value="product">Product</option><option value="labor">Labor</option></select>
              <input value={l.description} onChange={(e) => setLine(i, "description", e.target.value)} placeholder="Description" aria-label={`Line ${i + 1} description`} className={`${inputCls} col-span-4 order-first sm:order-none sm:!w-auto sm:flex-1 sm:min-w-[140px]`} />
              <input type="number" min={0} value={l.qty} onChange={(e) => setLine(i, "qty", Number(e.target.value))} title="Qty" aria-label={`Line ${i + 1} quantity`} className={`${inputCls} sm:!w-16 text-right tabular-nums`} />
              <input type="number" min={0} value={l.unit_price} onChange={(e) => setLine(i, "unit_price", Number(e.target.value))} placeholder="Price" aria-label={`Line ${i + 1} price`} className={`${inputCls} sm:!w-24 text-right tabular-nums`} />
              <IconButton label="Delete" onClick={() => setLines(lines.filter((_, j) => j !== i))} className="hover:!text-rose-600" icon={<M n="delete" c="text-sm" />} />
            </div>
          ))}
        </div>
        <div className="grid grid-cols-2 gap-3 mt-3">
          <div><label htmlFor="ni-discount" className={labelCls}>Discount %</label><input id="ni-discount" type="number" min={0} max={100} value={discount} onChange={(e) => setDiscount(Number(e.target.value))} className={`${inputCls} text-right tabular-nums`} /></div>
          <div><label htmlFor="ni-tax" className={labelCls}>Tax %</label><input id="ni-tax" type="number" min={0} value={tax} onChange={(e) => setTax(Number(e.target.value))} className={`${inputCls} text-right tabular-nums`} /></div>
        </div>
        <dl className="mt-3 rounded-xl bg-slate-50 border border-slate-200 p-3 space-y-1.5 text-sm">
          <div className="flex justify-between text-ink-secondary"><dt>Subtotal</dt><dd className="tabular-nums text-ink-primary">{cash(subtotal)}</dd></div>
          {discountAmt > 0 && <div className="flex justify-between text-ink-secondary"><dt>Discount ({discount}%)</dt><dd className="tabular-nums text-ink-primary">−{cash(discountAmt)}</dd></div>}
          {taxAmt > 0 && <div className="flex justify-between text-ink-secondary"><dt>Tax ({tax}%)</dt><dd className="tabular-nums text-ink-primary">{cash(taxAmt)}</dd></div>}
          <div className="pt-1.5 border-t border-slate-200 flex justify-between items-baseline"><dt className="font-semibold text-ink-primary">Total</dt><dd className="tabular-nums text-base font-semibold text-ink-primary">{cash(total)}</dd></div>
        </dl>
      </div>
      <div>
        <span className={labelCls}>Display on the invoice</span>
        <DisplayModes value={mode} onChange={setMode} />
      </div>
      <div><label htmlFor="ni-comment" className={labelCls}>Comment</label><textarea id="ni-comment" value={comment} onChange={(e) => setComment(e.target.value)} rows={2} className={inputCls} /></div>
    </Drawer>
  );
}
