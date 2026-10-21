import { useState } from "react";
import { M } from "../../components/Shell";
import SkeletonRows from "../../components/SkeletonRows";
import Pager from "../../components/Pager";
import { useDebounced, useServerPage } from "../../hooks/useServerPage";
import { openAuthedDoc } from "../../api/client";
import { alertDialog } from "../../lib/dialog";
import { humanize } from "../../lib/humanize";
import { useMe } from "../../context/Me";
import CreditNoteDrawer, { type CreditNote } from "./CreditNoteDrawer";
import { StatusPill, cash, fmtDate } from "./billing";

/* Credit notes list (Invoices page tab). Server-paged, filter by status, search by number,
   client or invoice. */

const FILTERS = [{ key: "", label: "All" }, { key: "draft", label: "Drafts" }, { key: "issued", label: "Issued" }, { key: "canceled", label: "Canceled" }];

export default function CreditNotesList() {
  const { can } = useMe();
  const [status, setStatus] = useState("");
  const [q, setQ] = useState("");
  const search = useDebounced(q);
  const p = useServerPage<CreditNote>("/finance/credit-notes", { status, search }, { pageSize: 50 });
  const [open, setOpen] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const rows = p.rows;
  return (
    <>
      <div className="flex flex-wrap items-center gap-2">
        <div className="flex gap-1 rounded-xl bg-slate-100 p-1" role="tablist" aria-label="Credit note status">
          {FILTERS.map((f) => <button key={f.key} role="tab" aria-selected={status === f.key} onClick={() => setStatus(f.key)}
            className={`px-3 py-1.5 rounded-lg text-xs font-semibold ${status === f.key ? "bg-white text-primary shadow-sm" : "text-ink-secondary"}`}>{f.label}</button>)}
        </div>
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search number, client, invoice" aria-label="Search credit notes"
          className="flex-1 min-w-[180px] bg-white border border-slate-200 rounded-xl text-sm px-3 py-2" />
        {can("can_create_credit_notes") && <button onClick={() => setCreating(true)} className="px-4 py-2 rounded-xl bg-primary text-white text-xs font-semibold shadow-clay-sm hover:brightness-110 flex items-center gap-1.5"><M n="add" c="text-sm" />New credit note</button>}
      </div>
      <div className="bg-white rounded-2xl border border-slate-200/90 shadow-clay-card overflow-x-auto mt-3">
        <table className="w-full text-left border-collapse text-xs min-w-[640px]">
          <thead><tr className="bg-slate-50/80 border-b border-slate-200 text-ink-tertiary text-[11px] font-bold">
            <th className="py-3 px-4">Number</th><th className="py-3 px-4">Client</th><th className="py-3 px-4">Invoice</th><th className="py-3 px-4">Date</th><th className="py-3 px-4">Status</th><th className="py-3 px-4 text-right">Total</th><th className="py-3 px-4"></th>
          </tr></thead>
          <tbody className="divide-y divide-slate-100 font-medium text-ink-secondary">
            {!rows && !p.err && <SkeletonRows cols={7} />}
            {p.err && <tr><td colSpan={7} className="py-6 px-4 text-rose-600">{p.err}</td></tr>}
            {rows?.length === 0 && <tr><td colSpan={7} className="py-6 px-4 text-ink-tertiary">{status || search ? "No credit notes match." : "No credit notes yet. Create one from an invoice, or here for a client's goodwill credit."}</td></tr>}
            {rows?.map((c) => (
              <tr key={c.id} onClick={() => setOpen(c.id)} className="hover:bg-blue-50/30 cursor-pointer">
                <td className="py-2.5 px-4 font-mono font-bold text-primary">{c.number || <span className="font-sans font-semibold text-ink-tertiary">Draft</span>}</td>
                <td className="py-2.5 px-4 font-semibold text-ink-primary">{c.client_name || "—"}</td>
                <td className="py-2.5 px-4 font-mono">{c.invoice_number || <span className="font-sans text-ink-tertiary">Standalone</span>}</td>
                <td className="py-2.5 px-4 whitespace-nowrap">{fmtDate(c.issued_at || c.created_at)}</td>
                <td className="py-2.5 px-4"><StatusPill status={c.status} label={humanize(c.status)} /></td>
                <td className={`py-2.5 px-4 text-right tabular-nums font-bold ${c.status === "canceled" ? "text-ink-tertiary line-through" : "text-ink-primary"}`}>{cash(c.total)}</td>
                <td className="py-2.5 px-4 text-right" onClick={(e) => e.stopPropagation()}>
                  <button aria-label={`PDF of ${c.number || "draft"}`} title="PDF" onClick={() => openAuthedDoc(`/print/credit-notes/${c.id}`).catch(() => alertDialog("Could not open the PDF"))} className="p-1.5 rounded-lg text-ink-tertiary hover:text-primary hover:bg-slate-100"><M n="print" c="text-base" /></button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {rows && rows.length > 0 && <Pager total={p.total} page={p.page} pageSize={p.pageSize} onPage={p.setPage} noun="credit notes" />}
      </div>
      {open && <CreditNoteDrawer id={open} onClose={() => setOpen(null)} onChanged={p.reload} />}
      {creating && <CreditNoteDrawer onClose={() => setCreating(false)} onChanged={p.reload} />}
    </>
  );
}

/* A client's credit notes, for the client drawer's money tab. */
export function ClientCreditNotes({ clientId }: { clientId: string }) {
  const { can } = useMe();
  const p = useServerPage<CreditNote>("/finance/credit-notes", { client_id: clientId }, { pageSize: 20, enabled: can("can_view_credit_notes") });
  const [open, setOpen] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  if (!can("can_view_credit_notes")) return null;
  const issued = (p.rows || []).filter((c) => c.status === "issued").reduce((s, c) => s + (c.total || 0), 0);
  return (
    <section aria-label="Credit notes" className="space-y-2">
      <div className="flex items-center justify-between">
        <div className="text-[11px] font-bold text-ink-tertiary">Credit notes{p.rows && p.rows.length > 0 ? ` · ${cash(issued)} issued` : ""}</div>
        {can("can_create_credit_notes") && <button onClick={() => setCreating(true)} className="px-2.5 py-1 rounded-lg bg-white border border-slate-200 text-[11px] font-bold text-primary hover:bg-slate-50">+ Credit note</button>}
      </div>
      {p.err ? <div className="text-xs text-rose-600">{p.err}</div>
        : !p.rows ? <div className="text-xs text-ink-tertiary">Loading…</div>
        : p.rows.length === 0 ? <div className="text-xs text-ink-tertiary">No credit notes for this client.</div>
        : p.rows.map((c) => (
          <button key={c.id} onClick={() => setOpen(c.id)} className="w-full bg-white rounded-xl border border-slate-200/90 p-3 shadow-sm flex items-center gap-2 text-left hover:border-primary/40">
            <span className="font-mono font-bold text-primary text-xs">{c.number || "Draft"}</span>
            <span className="text-[11px] text-ink-secondary truncate">{c.invoice_number ? `Invoice ${c.invoice_number}` : "Client credit"}{c.reason ? ` · ${c.reason}` : ""}</span>
            <span className="flex-1" />
            <StatusPill status={c.status} label={humanize(c.status)} />
            <span className={`text-xs font-bold tabular-nums ${c.status === "canceled" ? "line-through text-ink-tertiary" : "text-ink-primary"}`}>{cash(c.total)}</span>
          </button>
        ))}
      {open && <CreditNoteDrawer id={open} onClose={() => setOpen(null)} onChanged={p.reload} />}
      {creating && <CreditNoteDrawer clientId={clientId} onClose={() => setCreating(false)} onChanged={p.reload} />}
    </section>
  );
}
