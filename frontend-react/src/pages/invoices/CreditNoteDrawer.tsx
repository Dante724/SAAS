import { useEffect, useRef, useState } from "react";
import { M } from "../../components/Shell";
import BulkPrintDialog from "../../components/BulkPrintDialog";
import { api, openAuthedDoc } from "../../api/client";
import { alertDialog, confirmDialog } from "../../lib/dialog";
import { humanize } from "../../lib/humanize";
import { useMe } from "../../context/Me";
import { Drawer, ErrorNote, StatusPill, btnDanger, btnGhost, btnPrimary, cash, fmtDate, inputCls, labelCls, round2, type InvoiceDetail } from "./billing";

/* Credit note drawer: create / edit a draft, issue it (numbered + locked), cancel it, print it.
   Opened from the credit notes list, from an invoice ("Create credit note"), or from a client. */

type Line = { description: string; qty: number | string; unit_price: number | string; tax_rate: number | string };
export type CreditNote = {
  id: string; number?: string | null; status: string; reason?: string;
  client_id?: string | null; client_name?: string | null; invoice_id?: string | null; invoice_number?: string | null;
  subtotal: number; tax_total: number; total: number; issued_at?: string | null; canceled_at?: string | null; created_at?: string | null;
  lines?: (Line & { total?: number })[];
};

const blank = (): Line => ({ description: "", qty: 1, unit_price: 0, tax_rate: 0 });

// Mirrors services/credit_notes.line_amounts (half-up per line, then summed).
export function lineAmounts(l: Line) {
  const net = round2((Number(l.qty) || 0) * (Number(l.unit_price) || 0));
  const tax = round2(net * (Number(l.tax_rate) || 0) / 100);
  return { net, tax, total: round2(net + tax) };
}

// A new note raised from an invoice starts as a full credit of what the invoice can still take:
// its lines at the invoice's discount and tax, or one line for the remainder once part is credited.
export function linesFromInvoice(inv: InvoiceDetail): Line[] {
  const disc = Number(inv.discount) || 0;
  // Effective tax rate from the invoice's own numbers: Invoice.tax is a percent on invoices made
  // in the editor but the order's tax AMOUNT on auto-drafted ones, so never read it as a rate.
  const base = round2((Number(inv.subtotal) || 0) * (1 - disc / 100));
  const tax = base > 0 ? Math.max(0, Math.round(((Number(inv.total) || 0) / base - 1) * 100000) / 1000) : 0;
  const room = round2((inv.total || 0) - (inv.credited || 0));
  if ((inv.credited || 0) > 0 || !inv.lines?.length) {
    const net = round2(room / (1 + tax / 100));
    return [{ description: `Credit for invoice ${inv.invoice_number || ""}`.trim(), qty: 1, unit_price: net, tax_rate: tax }];
  }
  return inv.lines.map((l) => ({ description: l.description || "Item", qty: l.qty ?? 1,
    unit_price: round2((Number(l.unit_price) || 0) * (1 - disc / 100)), tax_rate: tax }));
}

export default function CreditNoteDrawer({ id, invoice, clientId, onClose, onChanged }: {
  id?: string; invoice?: InvoiceDetail; clientId?: string; onClose: () => void; onChanged?: () => void;
}) {
  const { can } = useMe();
  const [cn, setCn] = useState<CreditNote | null>(null);
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [clients, setClients] = useState<{ id: string; name?: string }[] | null>(null);
  const [client, setClient] = useState(clientId || invoice?.client_id || "");
  const [reason, setReason] = useState("");
  const [lines, setLines] = useState<Line[]>(invoice ? linesFromInvoice(invoice) : [blank()]);
  const [busy, setBusy] = useState(false); const busyRef = useRef(false);
  const [err, setErr] = useState<string | null>(null);
  const [printOpen, setPrintOpen] = useState(false);
  const invoiceId = cn?.invoice_id || invoice?.id || null;

  function adopt(r: CreditNote) {
    setCn(r); setClient(r.client_id || ""); setReason(r.reason || "");
    setLines((r.lines || []).map((l) => ({ description: l.description, qty: l.qty, unit_price: l.unit_price, tax_rate: l.tax_rate })));
  }
  useEffect(() => {
    if (!id) return;
    api.get<CreditNote>(`/finance/credit-notes/${id}`).then(adopt).catch((e) => setLoadErr(e.message || "Could not load the credit note"));
  }, [id]);
  // Only a draft is editable — including one this drawer just created (no id prop) and then issued.
  const editable = (cn ? cn.status === "draft" : !id) && can("can_create_credit_notes");
  useEffect(() => {
    if (!editable || invoiceId) return;
    api.get<{ items: any[] }>("/clients?limit=200").then((r) => setClients(r.items || [])).catch(() => setClients([]));
  }, [editable, invoiceId]);

  const setLine = (i: number, k: keyof Line, v: string) => setLines((ls) => ls.map((l, j) => (j === i ? { ...l, [k]: v } : l)));
  const amounts = lines.map(lineAmounts);
  const subtotal = round2(amounts.reduce((s, a) => s + a.net, 0));
  const taxTotal = round2(amounts.reduce((s, a) => s + a.tax, 0));
  const total = round2(subtotal + taxTotal);
  const room = invoice ? round2((invoice.total || 0) - (invoice.credited || 0)) : null;

  function payload() {
    return { client_id: invoiceId ? null : client || null, invoice_id: invoiceId, reason: reason.trim(),
      lines: lines.filter((l) => String(l.description).trim()).map((l) => ({
        description: String(l.description).trim(), qty: Number(l.qty) || 0,
        unit_price: Number(l.unit_price) || 0, tax_rate: Number(l.tax_rate) || 0 })) };
  }
  function check(): string | null {
    if (!invoiceId && !client) return "Pick the client this credit is for.";
    if (lines.some((l) => String(l.description).trim() && (Number(l.qty) || 0) <= 0)) return "Quantities must be above zero.";
    if (!payload().lines.length) return "Add at least one line.";
    return null;
  }
  async function run<T>(fn: () => Promise<T>): Promise<T | undefined> {
    if (busyRef.current) return;
    busyRef.current = true; setBusy(true); setErr(null);
    try { return await fn(); }
    catch (e: any) { setErr(e.message || "Something went wrong"); }
    finally { busyRef.current = false; setBusy(false); }
  }
  async function saveDraft(): Promise<CreditNote | undefined> {
    const problem = check(); if (problem) { setErr(problem); return; }
    const body = payload();
    const r = cn ? await api.put<CreditNote>(`/finance/credit-notes/${cn.id}`, body) : await api.post<CreditNote>("/finance/credit-notes", body);
    adopt(r); onChanged?.();
    return r;
  }
  async function issue() {
    if (busyRef.current) return;
    if (check()) { setErr(check()); return; }
    busyRef.current = true;   // held across the confirm so a double click can't open two
    const ok = await confirmDialog(`Issue this credit note for ${cash(total)}? It gets a number and can't be edited afterwards.`, { title: "Issue credit note", confirmLabel: "Issue" });
    busyRef.current = false;
    if (!ok) return;
    await run(async () => {
      const saved = await saveDraft(); if (!saved) return;
      const r = await api.post<CreditNote>(`/finance/credit-notes/${saved.id}/issue`, {});
      adopt(r); onChanged?.();
    });
  }
  async function cancel() {
    if (!cn) return;
    const msg = cn.status === "issued"
      ? `Cancel ${cn.number}? The ${cash(cn.total)} credit is reversed${cn.invoice_number ? ` and invoice ${cn.invoice_number} owes it again` : ""}.`
      : "Cancel this draft credit note?";
    if (!(await confirmDialog(msg, { title: "Cancel credit note", confirmLabel: "Cancel credit note", danger: true }))) return;
    await run(async () => { adopt(await api.post<CreditNote>(`/finance/credit-notes/${cn.id}/cancel`, {})); onChanged?.(); });
  }
  async function remove() {
    if (!cn || !(await confirmDialog("Delete this draft? This can't be undone.", { title: "Delete draft", confirmLabel: "Delete", danger: true }))) return;
    await run(async () => { await api.del(`/finance/credit-notes/${cn.id}`); onChanged?.(); onClose(); });
  }

  const title = cn?.number ? `Credit note ${cn.number}` : cn ? "Draft credit note" : "New credit note";
  const status = cn?.status || "draft";
  if (loadErr) return <Drawer label="Credit note" title="Credit note" onClose={onClose} z={60}><ErrorNote msg={loadErr} /></Drawer>;
  if (id && !cn) return <Drawer label="Credit note" title="Credit note" onClose={onClose} z={60}><div className="text-xs text-ink-tertiary">Loading…</div></Drawer>;

  return (
    <Drawer z={60} label={title} onClose={onClose}
      title={<span className="flex items-center gap-2">{title}<StatusPill status={status} label={humanize(status)} /></span>}
      sub={invoiceId ? <>Credits invoice {cn?.invoice_number || invoice?.invoice_number}</> : "Standalone: adds credit to the client's balance"}
      footer={<>
        {cn && <button onClick={() => openAuthedDoc(`/print/credit-notes/${cn.id}`).catch(() => alertDialog("Could not open the PDF"))} className={btnGhost}><M n="download" c="text-sm" />PDF</button>}
        {cn && <button onClick={() => setPrintOpen(true)} className={btnGhost}><M n="print" c="text-sm" />Print</button>}
        {cn?.status === "draft" && can("can_create_credit_notes") && <button onClick={remove} disabled={busy} className={btnDanger}>Delete draft</button>}
        {cn && cn.status !== "canceled" && can("can_issue_credit_notes") && <button onClick={cancel} disabled={busy} className={btnDanger}>{cn.status === "issued" ? "Cancel credit note" : "Cancel draft"}</button>}
        {editable && <button onClick={() => run(saveDraft)} disabled={busy} className={btnGhost}>{busy ? "Saving…" : "Save draft"}</button>}
        {editable && can("can_issue_credit_notes") && <button onClick={issue} disabled={busy || total <= 0} className={btnPrimary}>Issue {cash(total)}</button>}
      </>}>
      <ErrorNote msg={err} />
      {cn?.status === "issued" && <div className="rounded-lg bg-sky-50 border border-sky-200 text-sky-800 text-xs px-3 py-2">Issued {fmtDate(cn.issued_at)}. Issued credit notes are locked. Cancel it to reverse the credit.</div>}
      {cn?.status === "canceled" && <div className="rounded-lg bg-slate-50 border border-slate-200 text-ink-secondary text-xs px-3 py-2">Canceled {fmtDate(cn.canceled_at)}. Nothing on it counts any more.</div>}
      {!invoiceId && (
        <div>
          <label htmlFor="cn-client" className={labelCls}>Client</label>
          {editable ? (
            <select id="cn-client" value={client} onChange={(e) => setClient(e.target.value)} className={inputCls}>
              <option value="">{clients === null ? "Loading clients…" : "Pick a client"}</option>
              {(clients || []).map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
              {cn?.client_id && !(clients || []).some((c) => c.id === cn.client_id) && <option value={cn.client_id}>{cn.client_name}</option>}
            </select>
          ) : <div className="text-sm font-semibold text-ink-primary">{cn?.client_name || "—"}</div>}
        </div>
      )}
      {invoiceId && <div className="text-xs text-ink-secondary">Client: <span className="font-semibold text-ink-primary">{cn?.client_name || invoice?.customer?.name || "Walk-in"}</span></div>}
      <div>
        <label htmlFor="cn-reason" className={labelCls}>Reason</label>
        {editable ? <input id="cn-reason" value={reason} onChange={(e) => setReason(e.target.value)} maxLength={500} placeholder="Returned part, goodwill, price correction…" className={inputCls} />
          : <div className="text-sm text-ink-primary">{cn?.reason || "—"}</div>}
      </div>
      <div>
        <div className="flex items-center justify-between mb-1"><span className={labelCls}>Lines</span>
          {editable && <button onClick={() => setLines([...lines, blank()])} className="text-primary text-xs font-semibold flex items-center gap-1"><M n="add" c="text-sm" />Add line</button>}</div>
        {editable && lines.length > 0 && <div className="grid grid-cols-4 gap-2 px-2 mb-1 text-[11px] text-ink-tertiary text-right" aria-hidden><span>Qty</span><span>Unit price</span><span>Tax</span><span>Total</span></div>}
        <div className="space-y-2">
          {lines.map((l, i) => editable ? (
            <div key={i} className="rounded-lg border border-slate-200 p-2 space-y-2">
              <div className="flex gap-2">
                <input aria-label={`Line ${i + 1} description`} value={l.description} onChange={(e) => setLine(i, "description", e.target.value)} placeholder="Description" className={`${inputCls} flex-1`} />
                <button aria-label={`Remove line ${i + 1}`} onClick={() => setLines(lines.filter((_, j) => j !== i))} className="w-9 h-9 flex-shrink-0 rounded-lg hover:bg-rose-50 text-ink-tertiary hover:text-rose-600 flex items-center justify-center"><M n="delete" c="text-sm" /></button>
              </div>
              <div className="grid grid-cols-4 gap-2 items-center">
                <input aria-label={`Line ${i + 1} quantity`} type="number" min={0} step="any" value={l.qty} onChange={(e) => setLine(i, "qty", e.target.value)} className={`${inputCls} text-right`} />
                <input aria-label={`Line ${i + 1} unit price`} type="number" min={0} step="0.01" value={l.unit_price} onChange={(e) => setLine(i, "unit_price", e.target.value)} className={`${inputCls} text-right`} />
                <div className="relative"><input aria-label={`Line ${i + 1} tax percent`} type="number" min={0} max={100} step="any" value={l.tax_rate} onChange={(e) => setLine(i, "tax_rate", e.target.value)} className={`${inputCls} text-right pr-6`} /><span className="absolute right-2 top-2 text-xs text-ink-tertiary">%</span></div>
                <div className="text-right text-sm font-semibold tabular-nums text-ink-primary">{cash(amounts[i].total)}</div>
              </div>
            </div>
          ) : (
            <div key={i} className="flex justify-between gap-3 text-sm border-b border-slate-100 pb-2">
              <span className="text-ink-primary">{l.description} <span className="text-[11px] text-ink-tertiary">{Number(l.qty)} × {cash(Number(l.unit_price))}{Number(l.tax_rate) ? ` + ${Number(l.tax_rate)}% tax` : ""}</span></span>
              <span className="tabular-nums font-semibold">{cash(amounts[i].total)}</span>
            </div>
          ))}
          {editable && lines.length === 0 && <div className="text-xs text-ink-tertiary">No lines yet.</div>}
        </div>
      </div>
      <div className="rounded-xl bg-slate-50 border border-slate-200 p-3 space-y-1.5 text-sm">
        <div className="flex justify-between text-ink-secondary"><span>Subtotal</span><span className="tabular-nums font-semibold text-ink-primary">{cash(subtotal)}</span></div>
        <div className="flex justify-between text-ink-secondary"><span>Tax</span><span className="tabular-nums text-ink-primary">{cash(taxTotal)}</span></div>
        <div className="pt-1.5 border-t border-slate-200 flex justify-between items-baseline"><span className="font-bold text-ink-primary">Credit total</span><span className="tabular-nums font-bold text-primary">{cash(total)}</span></div>
        {editable && room !== null && <div className={`text-[11px] ${total > room ? "text-rose-600" : "text-ink-tertiary"}`}>This invoice can still be credited up to {cash(room)}.</div>}
      </div>
      {cn && <BulkPrintDialog open={printOpen} onClose={() => setPrintOpen(false)} ids={[cn.id]} noun="credit note"
        endpoint="/documents/render-credit-note-batch" idKey="credit_note_ids" docTypes={["credit_note"]} copies={false} />}
    </Drawer>
  );
}
