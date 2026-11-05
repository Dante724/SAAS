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
import Card from "../../components/Card";
import Button, { IconButton } from "../../components/Button";
import EmptyState, { ErrorState, TableEmpty } from "../../components/EmptyState";
import { Td, Th, tableClass, theadRowClass } from "../../components/Table";
import { clickableRow } from "../../lib/clickable";

/* Credit notes list (Invoices page tab). Server-paged, filter by status, search by number,
   client or invoice. */

export type CnStatus = "" | "draft" | "issued" | "canceled";
export const CN_FILTERS: { id: CnStatus; label: string }[] = [{ id: "", label: "All" }, { id: "draft", label: "Drafts" }, { id: "issued", label: "Issued" }, { id: "canceled", label: "Canceled" }];

/* The status filter, search and "New credit note" live in the Invoices page toolbar (one toolbar
   for both tabs), so this list takes them as props. */
export default function CreditNotesList({ status, q, creating, onCreatingDone }: { status: CnStatus; q: string; creating: boolean; onCreatingDone: () => void }) {
  const search = useDebounced(q);
  const p = useServerPage<CreditNote>("/finance/credit-notes", { status, search }, { pageSize: 50 });
  const [open, setOpen] = useState<string | null>(null);
  const rows = p.rows;
  const none = status || search ? "No credit notes match." : "No credit notes yet. Create one from an invoice, or here for a client's goodwill credit.";
  return (
    <>
      <Card padded={false}>
        {p.err ? <ErrorState title="Credit notes didn't load" body={p.err} onRetry={p.reload} /> : <>
        {/* Phones: one card per credit note instead of the table. */}
        <ul className="sm:hidden divide-y divide-slate-100" aria-label="Credit notes">
          {!rows && <li className="p-4 text-xs text-ink-tertiary">Loading credit notes…</li>}
          {rows?.length === 0 && <li><EmptyState size="inline" className="px-4" title={none} /></li>}
          {rows?.map((c) => (
            <li key={c.id}><button onClick={() => setOpen(c.id)} className="w-full text-left px-4 py-3 flex flex-col gap-1 hover:bg-slate-50/80">
              <span className="flex items-center gap-2">
                <span className="tabular-nums font-semibold text-primary text-xs">{c.number || <span className="text-ink-tertiary">Draft</span>}</span>
                <StatusPill status={c.status} label={humanize(c.status)} />
                <span className="ml-auto text-xs text-ink-tertiary">{fmtDate(c.issued_at || c.created_at)}</span>
              </span>
              <span className="flex items-baseline gap-2">
                <span className="text-sm font-semibold text-ink-primary truncate">{c.client_name || "—"}</span>
                <span className={`ml-auto text-sm font-semibold tabular-nums ${c.status === "canceled" ? "line-through text-ink-tertiary" : "text-ink-primary"}`}>{cash(c.total)}</span>
              </span>
              <span className="text-xs text-ink-tertiary truncate">{c.invoice_number ? <>Invoice <span className="tabular-nums">{c.invoice_number}</span></> : "Standalone credit"}</span>
            </button></li>
          ))}
        </ul>
        <div className="hidden sm:block overflow-x-auto">
        <table className={`${tableClass} min-w-[640px]`}>
          <thead><tr className={theadRowClass}>
            <Th>Number</Th><Th>Client</Th><Th>Invoice</Th><Th>Date</Th><Th>Status</Th><Th num>Total</Th><Th><span className="sr-only">PDF</span></Th>
          </tr></thead>
          <tbody className="divide-y divide-slate-100 text-ink-secondary">
            {!rows && <SkeletonRows cols={7} />}
            {rows?.length === 0 && <TableEmpty cols={7} size="inline" className="px-4" title={none} />}
            {rows?.map((c) => (
              <tr key={c.id} {...clickableRow(() => setOpen(c.id))} className="hover:bg-slate-50/80 cursor-pointer">
                <td className="py-2.5 px-4 tabular-nums font-semibold text-primary">{c.number || <span className="font-semibold text-ink-tertiary">Draft</span>}</td>
                <td className="py-2.5 px-4 font-semibold text-ink-primary">{c.client_name || "—"}</td>
                <td className="py-2.5 px-4 tabular-nums text-ink-tertiary">{c.invoice_number || "Standalone"}</td>
                <td className="py-2.5 px-4 whitespace-nowrap tabular-nums">{fmtDate(c.issued_at || c.created_at)}</td>
                <td className="py-2.5 px-4"><StatusPill status={c.status} label={humanize(c.status)} /></td>
                <Td num className={`py-2.5 font-semibold ${c.status === "canceled" ? "text-ink-tertiary line-through" : "text-ink-primary"}`}>{cash(c.total)}</Td>
                <td className="py-1.5 px-4 text-right" onClick={(e) => e.stopPropagation()}>
                  <IconButton size="sm" label={`PDF of ${c.number || "draft"}`} onClick={() => openAuthedDoc(`/print/credit-notes/${c.id}`).catch(() => alertDialog("Could not open the PDF"))} icon={<M n="print" c="text-base" />} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        </div>
        {rows && rows.length > 0 && <Pager total={p.total} page={p.page} pageSize={p.pageSize} onPage={p.setPage} noun="credit notes" />}
        </>}
      </Card>
      {open && <CreditNoteDrawer id={open} onClose={() => setOpen(null)} onChanged={p.reload} />}
      {creating && <CreditNoteDrawer onClose={onCreatingDone} onChanged={p.reload} />}
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
        <div className="text-xs font-semibold text-ink-secondary">Credit notes{p.rows && p.rows.length > 0 ? ` · ${cash(issued)} issued` : ""}</div>
        {can("can_create_credit_notes") && <Button size="sm" icon={<M n="add" c="text-sm" />} onClick={() => setCreating(true)}>Credit note</Button>}
      </div>
      {p.err ? <div className="text-xs text-rose-600">{p.err}</div>
        : !p.rows ? <div className="text-xs text-ink-tertiary">Loading…</div>
        : p.rows.length === 0 ? <div className="text-xs text-ink-tertiary">No credit notes for this client.</div>
        : p.rows.map((c) => (
          <button key={c.id} onClick={() => setOpen(c.id)} className="w-full bg-white rounded-xl border border-slate-200/90 p-3 flex items-center gap-2 text-left hover:border-slate-300 hover:bg-slate-50/60">
            <span className="tabular-nums font-semibold text-primary text-xs">{c.number || "Draft"}</span>
            <span className="text-xs text-ink-secondary truncate">{c.invoice_number ? `Invoice ${c.invoice_number}` : "Client credit"}{c.reason ? ` · ${c.reason}` : ""}</span>
            <span className="flex-1" />
            <StatusPill status={c.status} label={humanize(c.status)} />
            <span className={`text-xs font-semibold tabular-nums ${c.status === "canceled" ? "line-through text-ink-tertiary" : "text-ink-primary"}`}>{cash(c.total)}</span>
          </button>
        ))}
      {open && <CreditNoteDrawer id={open} onClose={() => setOpen(null)} onChanged={p.reload} />}
      {creating && <CreditNoteDrawer clientId={clientId} onClose={() => setCreating(false)} onChanged={p.reload} />}
    </section>
  );
}
