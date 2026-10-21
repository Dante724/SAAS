import { useEffect, useState } from "react";
import { M } from "../../components/Shell";
import { api, openAuthedDoc } from "../../api/client";
import { alertDialog } from "../../lib/dialog";
import { humanize } from "../../lib/humanize";
import { useMe } from "../../context/Me";
import RecordPaymentDrawer from "./RecordPaymentDrawer";
import CreditNoteDrawer from "./CreditNoteDrawer";
import { DISPLAY_MODES, Drawer, ErrorNote, StatusPill, btnGhost, btnPrimary, cash, fmtDate, isVoid, round2, type InvoiceDetail } from "./billing";

/* The invoice view: lines (in the chosen display mode), the Balance due block, payments and
   credit notes, plus Record payment / Create credit note / Print. */

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

  return (
    <>
      <Drawer label={`Invoice ${inv.invoice_number}`} onClose={onClose}
        title={<span className="flex items-center gap-2 font-mono">{inv.invoice_number}<StatusPill status={inv.status} label={humanize(inv.status)} /></span>}
        sub={<>{billTo ? <>Bill to <span className="font-semibold text-ink-secondary">{billTo}</span> · </> : "Walk-in · "}Issued {fmtDate(inv.issued_at)}{inv.due_at ? ` · Due ${fmtDate(inv.due_at)}` : ""}</>}
        footer={<>
          <button onClick={() => openAuthedDoc(`/print/invoices/${inv.id}`).catch(() => alertDialog("Could not open invoice PDF"))} className={btnGhost}><M n="print" c="text-sm" />Print / PDF</button>
          {can("can_create_credit_notes") && !voided && room > 0 && <button onClick={() => setNewNote(true)} className={btnGhost}>Create credit note</button>}
          {can("can_view_finance") && !voided && due > 0 && <button onClick={() => setPaying(true)} className={btnPrimary}>Record payment</button>}
        </>}>
        {voided && <div className="rounded-lg bg-rose-50 border border-rose-200 text-rose-700 text-xs px-3 py-2">This invoice was cancelled. It no longer counts as owed.</div>}

        <section aria-label="Balance due" className="rounded-2xl border border-slate-200 bg-slate-50 p-4">
          <div className="flex items-baseline justify-between">
            <span className="text-xs font-bold text-ink-tertiary">Balance due</span>
            <span className={`text-2xl font-bold tabular-nums ${due > 0 ? "text-rose-600" : "text-emerald-600"}`}>{cash(due)}</span>
          </div>
          <dl className="mt-3 grid grid-cols-3 gap-2 text-xs">
            <div><dt className="text-ink-tertiary">Total</dt><dd className="font-semibold tabular-nums text-ink-primary">{cash(inv.total)}</dd></div>
            <div><dt className="text-ink-tertiary">Paid</dt><dd className="font-semibold tabular-nums text-emerald-700">{cash(inv.amount_paid)}</dd></div>
            <div><dt className="text-ink-tertiary">Credited</dt><dd className="font-semibold tabular-nums text-sky-700">{cash(inv.credited)}</dd></div>
          </dl>
        </section>

        <div>
          <span className="block text-[11px] font-semibold text-ink-tertiary mb-1">Display on the invoice</span>
          <div role="radiogroup" aria-label="Display on the invoice" className="grid grid-cols-3 gap-1 rounded-xl bg-slate-100 p-1">
            {DISPLAY_MODES.map((m) => (
              <button key={m.key} role="radio" aria-checked={(inv.display_mode || "detailed") === m.key} title={m.hint}
                disabled={!canEditMode || modeBusy} onClick={() => setMode(m.key)}
                className={`px-2 py-1.5 rounded-lg text-xs font-semibold disabled:cursor-not-allowed ${(inv.display_mode || "detailed") === m.key ? "bg-white text-primary shadow-sm" : "text-ink-secondary hover:text-ink-primary"}`}>{m.label}</button>
            ))}
          </div>
          <p className="text-[11px] text-ink-tertiary mt-1">{DISPLAY_MODES.find((m) => m.key === (inv.display_mode || "detailed"))?.hint}. Only the printed lines change, not the amounts.</p>
        </div>

        <div className="rounded-xl border border-slate-200 overflow-hidden">
          <table className="w-full text-xs">
            <thead><tr className="bg-slate-50 text-[11px] font-bold text-ink-tertiary"><th className="py-2 px-3 text-left">Description</th><th className="py-2 px-3 text-right">Qty</th><th className="py-2 px-3 text-right">Amount</th></tr></thead>
            <tbody className="divide-y divide-slate-100">
              {(inv.lines || []).length === 0 && <tr><td colSpan={3} className="py-3 px-3 text-ink-tertiary">No lines.</td></tr>}
              {(inv.lines || []).map((l, i) => (
                <tr key={i}><td className="py-2 px-3 text-ink-primary">{l.item ? <span className="text-ink-tertiary">{l.item} · </span> : null}{l.description}</td>
                  <td className="py-2 px-3 text-right tabular-nums">{Number(l.qty ?? 0)}</td>
                  <td className="py-2 px-3 text-right tabular-nums font-semibold">{cash(l.total)}</td></tr>
              ))}
            </tbody>
          </table>
        </div>

        <div>
          <h3 className="text-xs font-bold text-ink-primary mb-1.5">Payments</h3>
          {(inv.payments || []).length === 0 ? <div className="text-xs text-ink-tertiary">No payments yet.</div> : (
            <ul className="divide-y divide-slate-100 rounded-xl border border-slate-200">
              {(inv.payments || []).map((p, i) => (
                <li key={p.id || i} className="flex items-center justify-between gap-3 px-3 py-2 text-xs">
                  <span><span className="font-semibold text-ink-primary">{KIND_LABEL[p.kind || "payment"] || humanize(p.kind)}</span> · {humanize(p.method)} · {fmtDate(p.paid_at)}{p.reference ? <span className="block text-ink-tertiary">{p.reference}</span> : null}</span>
                  <span className={`tabular-nums font-semibold ${p.kind === "refund" ? "text-rose-600" : "text-emerald-700"}`}>{p.kind === "refund" ? "−" : ""}{cash(p.amount)}</span>
                </li>
              ))}
            </ul>
          )}
        </div>

        {can("can_view_credit_notes") && (
          <div>
            <h3 className="text-xs font-bold text-ink-primary mb-1.5">Credit notes</h3>
            {(inv.credit_notes || []).length === 0 ? <div className="text-xs text-ink-tertiary">No credit notes on this invoice.</div> : (
              <ul className="divide-y divide-slate-100 rounded-xl border border-slate-200">
                {(inv.credit_notes || []).map((c) => (
                  <li key={c.id}><button onClick={() => setNoteId(c.id)} className="w-full flex items-center justify-between gap-3 px-3 py-2 text-xs hover:bg-slate-50 text-left">
                    <span className="flex items-center gap-2"><span className="font-mono font-semibold text-primary">{c.number || "Draft"}</span><StatusPill status={c.status} label={humanize(c.status)} /></span>
                    <span className={`tabular-nums font-semibold ${c.status === "canceled" ? "line-through text-ink-tertiary" : "text-sky-700"}`}>{cash(c.total)}</span>
                  </button></li>
                ))}
              </ul>
            )}
          </div>
        )}
        {inv.comment ? <div className="text-xs text-ink-secondary whitespace-pre-wrap"><span className="font-semibold">Comment:</span> {inv.comment}</div> : null}
      </Drawer>
      {paying && <RecordPaymentDrawer invoice={inv} onClose={() => setPaying(false)} onPaid={() => { setPaying(false); changed(); }} />}
      {newNote && <CreditNoteDrawer invoice={inv} onClose={() => setNewNote(false)} onChanged={changed} />}
      {noteId && <CreditNoteDrawer id={noteId} onClose={() => setNoteId(null)} onChanged={changed} />}
    </>
  );
}
