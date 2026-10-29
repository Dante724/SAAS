import { useEffect, useState } from "react";
import { M } from "../../components/Shell";
import { api, openAuthedDoc } from "../../api/client";
import { alertDialog } from "../../lib/dialog";
import { humanize } from "../../lib/humanize";
import { useMe } from "../../context/Me";
import RecordPaymentDrawer from "./RecordPaymentDrawer";
import CreditNoteDrawer from "./CreditNoteDrawer";
import { DISPLAY_MODES, Drawer, ErrorNote, StatusPill, btnGhost, btnPrimary, cash, daysLate, fmtDate, isVoid, lateText, round2, type InvoiceDetail } from "./billing";

/* The invoice view, read like a statement: the lines (in the chosen display mode), the total,
   then every payment and credit note taken off it, a rule, and Balance due. Balance due is
   ink; it only turns red, with "N days late", once the due date has passed.
   Actions: Record payment / Create credit note / Print. */

const KIND_LABEL: Record<string, string> = { payment: "Payment", prepayment: "Prepayment", refund: "Refund" };

export default function InvoiceDrawer({ id, onClose, onChanged }: { id: string; onClose: () => void; onChanged: () => void }) {
  const { can } = useMe();
  const [inv, setInv] = useState<InvoiceDetail | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [modeBusy, setModeBusy] = useState(false);
  const [paying, setPaying] = useState(false);
  const [noteId, setNoteId] = useState<string | null>(null);
  const [newNote, setNewNote] = useState(false);
  const load = () => api.get<InvoiceDetail>(`/finance/invoices/${id}`).then((r) => { setInv(r); setErr(null); })
    .catch((e) => setErr(e.message || "Could not load the invoice"));
  useEffect(() => { load(); }, [id]); // eslint-disable-line react-hooks/exhaustive-deps
  const changed = () => { load(); onChanged(); };

  async function setMode(mode: string) {
    if (!inv || mode === inv.display_mode) return;
    setModeBusy(true);
    try { setInv(await api.patch<InvoiceDetail>(`/finance/invoices/${id}`, { display_mode: mode })); onChanged(); }
    catch (e: any) { await alertDialog(e.message || "Could not change the display"); }
    finally { setModeBusy(false); }
  }

  if (err && !inv) return <Drawer label="Invoice" title="Invoice" onClose={onClose}><ErrorNote msg={err} /></Drawer>;
  if (!inv) return <Drawer label="Invoice" title="Invoice" onClose={onClose}><div className="text-xs text-ink-tertiary">Loading…</div></Drawer>;

  const voided = isVoid(inv.status);
  const due = round2(inv.balance_due || 0);
  const room = round2((inv.total || 0) - (inv.credited || 0));
  const canEditMode = can("can_edit_invoices") && !voided && ((inv.status || "draft") === "draft" || can("can_edit_issued_invoices"));
  const billTo = inv.payer?.name || inv.customer?.name;
  const late = daysLate(inv);
  const seeNotes = can("can_view_credit_notes");

  // Statement rows. Payment rows are used when they add up to what the invoice says was paid;
  // otherwise (older data) one "Paid" line carries the amount so the statement still balances.
  const pays = inv.payments || [];
  const paidByRows = round2(pays.reduce((s, p) => s + (p.kind === "refund" ? -1 : 1) * (Number(p.amount) || 0), 0));
  const rowsBalance = pays.length > 0 && Math.abs(paidByRows - round2(inv.amount_paid || 0)) < 0.01;
  const notes = inv.credit_notes || [];
  const issuedNotes = round2(notes.filter((c) => c.status === "issued").reduce((s, c) => s + (Number(c.total) || 0), 0));
  const notesBalance = seeNotes && Math.abs(issuedNotes - round2(inv.credited || 0)) < 0.01;

  const rowCls = "flex items-start justify-between gap-3 px-4 py-2.5 text-sm";
  return (
    <>
      <Drawer label={`Invoice ${inv.invoice_number}`} onClose={onClose}
        title={<span className="flex items-center gap-2"><span className="font-mono">{inv.invoice_number}</span><StatusPill status={inv.status} label={humanize(inv.status)} /></span>}
        sub={<>{billTo ? <>Bill to <span className="font-semibold text-ink-secondary">{billTo}</span> · </> : "Walk-in · "}Issued {fmtDate(inv.issued_at)}{inv.due_at ? <> · Due {fmtDate(inv.due_at)}{late > 0 && <span className="font-semibold text-rose-600"> · {lateText(late)}</span>}</> : null}</>}
        footer={<>
          <button onClick={() => openAuthedDoc(`/print/invoices/${inv.id}`).catch(() => alertDialog("Could not open invoice PDF"))} className={btnGhost}><M n="print" c="text-sm" />Print / PDF</button>
          {can("can_create_credit_notes") && !voided && room > 0 && <button onClick={() => setNewNote(true)} className={btnGhost}>Create credit note</button>}
          {can("can_view_finance") && !voided && due > 0 && <button onClick={() => setPaying(true)} className={btnPrimary}>Record payment</button>}
        </>}>
        {voided && <div role="note" className="rounded-xl bg-slate-50 border border-slate-200 text-ink-secondary text-xs px-3 py-2">This invoice was cancelled. It no longer counts as owed.</div>}

        <div className="flex flex-wrap items-center justify-between gap-2">
          <h3 className="text-sm font-semibold text-ink-primary">Statement</h3>
          <div role="radiogroup" aria-label="Display on the invoice" className="inline-flex gap-0.5 rounded-lg bg-slate-100 p-0.5">
            {DISPLAY_MODES.map((m) => (
              <button key={m.key} role="radio" aria-checked={(inv.display_mode || "detailed") === m.key} title={m.hint}
                disabled={!canEditMode || modeBusy} onClick={() => setMode(m.key)}
                className={`px-2.5 py-1 rounded-md text-xs font-semibold disabled:cursor-not-allowed ${(inv.display_mode || "detailed") === m.key ? "bg-white text-ink-primary shadow-sm" : "text-ink-tertiary hover:text-ink-primary"}`}>{m.label}</button>
            ))}
          </div>
        </div>

        <section aria-label="Balance due" className="-mt-1 rounded-2xl border border-slate-200 overflow-hidden">
          <ul aria-label="Lines" className="divide-y divide-slate-100">
            {(inv.lines || []).length === 0 && <li className={`${rowCls} text-ink-tertiary`}>No lines.</li>}
            {(inv.lines || []).map((l, i) => (
              <li key={i} className={rowCls}>
                <span className="min-w-0 text-ink-primary">{l.item ? <span className="text-ink-tertiary">{l.item} · </span> : null}{l.description}
                  {Number(l.qty ?? 0) !== 1 && <span className="block text-xs text-ink-tertiary tabular-nums">{Number(l.qty ?? 0)} × {cash(l.unit_price ?? (Number(l.total) || 0) / (Number(l.qty) || 1))}</span>}</span>
                <span className="tabular-nums text-ink-primary whitespace-nowrap">{cash(l.total)}</span>
              </li>
            ))}
          </ul>
          <ol aria-label="Statement" className="border-t border-slate-200 bg-slate-50/60 py-1">
            <li data-row="total" className={`${rowCls} font-semibold text-ink-primary`}><span>Total</span><span className="tabular-nums">{cash(inv.total)}</span></li>
            {rowsBalance ? pays.map((p, i) => (
              <li key={p.id || i} data-row="payment" className={rowCls}>
                <span className="min-w-0 text-ink-secondary"><span className="text-ink-primary">{KIND_LABEL[p.kind || "payment"] || humanize(p.kind)}</span> · {humanize(p.method)} · {fmtDate(p.paid_at)}
                  {p.reference ? <span className="block text-xs text-ink-tertiary truncate">{p.reference}</span> : null}</span>
                <span className="tabular-nums text-ink-secondary whitespace-nowrap">{p.kind === "refund" ? "+" : "−"} {cash(p.amount)}</span>
              </li>
            )) : (inv.amount_paid || 0) !== 0 && (
              <li data-row="payment" className={rowCls}><span className="text-ink-secondary">Paid</span><span className="tabular-nums text-ink-secondary whitespace-nowrap">− {cash(inv.amount_paid)}</span></li>
            )}
            {seeNotes && notes.map((c) => {
              const counts = c.status === "issued";
              return (
                <li key={c.id} data-row="credit">
                  <button onClick={() => setNoteId(c.id)} className={`${rowCls} w-full text-left hover:bg-slate-100/70`}>
                    <span className="min-w-0 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-ink-secondary">
                      <span>Credit note <span className="font-mono font-semibold text-primary">{c.number || "Draft"}</span>{c.issued_at && counts ? <> · {fmtDate(c.issued_at)}</> : null}</span>
                      {!counts && <StatusPill status={c.status} label={c.status === "draft" ? "Draft, not applied" : humanize(c.status)} />}
                    </span>
                    <span className={`tabular-nums whitespace-nowrap ${counts ? "text-ink-secondary" : "text-ink-tertiary line-through"}`}>{counts ? "− " : ""}{cash(c.total)}</span>
                  </button>
                </li>
              );
            })}
            {!notesBalance && (inv.credited || 0) !== 0 && (
              <li data-row="credit" className={rowCls}><span className="text-ink-secondary">Credited</span><span className="tabular-nums text-ink-secondary whitespace-nowrap">− {cash(inv.credited)}</span></li>
            )}
            <li data-row="balance" className="mx-4 mt-1 pt-3 pb-2 border-t border-slate-300 flex items-baseline justify-between gap-3">
              <span className="text-sm font-semibold text-ink-primary">Balance due
                {late > 0 ? <span className="block text-xs font-semibold text-rose-600">{lateText(late)}</span>
                  : due <= 0 && !voided ? <span className="block text-xs font-normal text-ink-tertiary">Paid in full</span> : null}</span>
              <span data-testid="balance-due" className={`text-2xl font-semibold tracking-tight tabular-nums ${late > 0 ? "text-rose-600" : "text-ink-primary"}`}>{cash(due)}</span>
            </li>
          </ol>
        </section>
        <p className="-mt-2 text-xs text-ink-tertiary">{DISPLAY_MODES.find((m) => m.key === (inv.display_mode || "detailed"))?.hint}. Only the printed lines change, not the amounts.</p>

        {inv.comment ? (
          <div>
            <h3 className="text-sm font-semibold text-ink-primary mb-1.5">Comment</h3>
            <p className="rounded-xl border border-slate-200 px-4 py-3 text-sm text-ink-secondary whitespace-pre-wrap">{inv.comment}</p>
          </div>
        ) : null}
      </Drawer>
      {paying && <RecordPaymentDrawer invoice={inv} onClose={() => setPaying(false)} onPaid={() => { setPaying(false); changed(); }} />}
      {newNote && <CreditNoteDrawer invoice={inv} onClose={() => setNewNote(false)} onChanged={changed} />}
      {noteId && <CreditNoteDrawer id={noteId} onClose={() => setNoteId(null)} onChanged={changed} />}
    </>
  );
}
