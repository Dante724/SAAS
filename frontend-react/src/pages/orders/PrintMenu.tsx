import { useEffect, useState } from "react";
import { M } from "../../components/Shell";
import FloatingMenu from "../../components/FloatingMenu";
import { api } from "../../api/client";
import { printHtmlInFrame } from "../../lib/safeHtml";

/**
 * The work order's Print button: one click prints the last thing printed for this job
 * (or the best fit for its status); the arrow lists the bench sticker and the shop's own
 * document templates that fit the job's status (Settings → Documents you print), with
 * "Show all" so nothing is locked out, plus invoice templates for the job's latest invoice
 * (for staff who can see invoices). Documents render server-side at their exact size and
 * print through a hidden sandboxed frame.
 */
type Tpl = { id: string; name: string; doc_type: string; is_default?: boolean; print_statuses?: string[] };

export const STICKER = "sticker";
const HIDDEN = new Set(["label", "price_tag", "product_label", "vehicle_label", "component", "invoice"]);
const INV = "inv:";   // choice id prefix for an invoice template (printed against the latest invoice)
const memKey = (orderId: string) => `tekly.print.${orderId}`;

function remembered(orderId: string): string | null {
  try { return localStorage.getItem(memKey(orderId)); } catch { return null; }
}
function remember(orderId: string, choice: string) {
  try { localStorage.setItem(memKey(orderId), choice); } catch { /* private mode: no memory, still prints */ }
}

/** Best one-click choice: last used on this job, else a template made for this status, else the default work order, else the sticker. */
export function pickDefault(fitting: Tpl[], last: string | null, all: Tpl[]): string {
  if (last && (last === STICKER || all.some((t) => t.id === last || INV + t.id === last))) return last;
  const forStatus = fitting.find((t) => (t.print_statuses || []).length > 0);
  if (forStatus) return forStatus.id;
  const wo = fitting.find((t) => t.doc_type === "work_order" && t.is_default) || fitting.find((t) => t.doc_type === "work_order");
  return wo ? wo.id : STICKER;
}

export default function PrintMenu({ orderId, status, canInvoices = true, onSticker, onError }: {
  orderId: string;
  status?: string | null;
  /** "View invoices": without it the menu offers no invoice templates. */
  canInvoices?: boolean;
  onSticker: () => void | Promise<void>;
  onError: (msg: string) => void;
}) {
  const [fitting, setFitting] = useState<Tpl[]>([]);
  const [all, setAll] = useState<Tpl[]>([]);
  const [invTpls, setInvTpls] = useState<Tpl[]>([]);
  const [invoice, setInvoice] = useState<{ id: string; invoice_number?: string } | null>(null);
  const [showAll, setShowAll] = useState(false);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [last, setLast] = useState<string | null>(() => remembered(orderId));

  // The parent mounts one PrintMenu per job (key = order id), so state never leaks between jobs.
  // All templates and the job's invoice load once; the status-fitting list reloads on a status change.
  useEffect(() => {
    let live = true;
    api.get<any>("/documents/templates").then((a) => {
      if (!live) return;
      setAll((a?.items || []).filter((t: Tpl) => !HIDDEN.has(t.doc_type)));
      setInvTpls((a?.items || []).filter((t: Tpl) => t.doc_type === "invoice"));
    }).catch(() => { /* the sticker still prints */ });
    if (canInvoices) {
      // The newest usable invoice on this job (the list comes oldest → newest).
      api.get<any>(`/cash/orders/${encodeURIComponent(orderId)}/invoices`).then((r) => {
        const usable = (r?.invoices || []).filter((i: any) => i.status !== "cancelled");
        if (live) setInvoice(usable.length ? usable[usable.length - 1] : null);
      }).catch(() => { if (live) setInvoice(null); });
    }
    return () => { live = false; };
  }, [orderId, canInvoices]);

  useEffect(() => {
    let live = true;
    api.get<any>(`/documents/templates?order_id=${encodeURIComponent(orderId)}`).then((f) => {
      if (live) setFitting((f?.items || []).filter((t: Tpl) => !HIDDEN.has(t.doc_type)));
    }).catch(() => { /* keep the last list */ });
    return () => { live = false; };
  }, [orderId, status]);

  const choice = pickDefault(fitting, last, all);
  const nameOf = (id: string) => id === STICKER ? "sticker"
    : id.startsWith(INV) ? (invTpls.find((t) => INV + t.id === id)?.name || "invoice")
    : (all.find((t) => t.id === id)?.name || "document");

  async function print(id: string) {
    if (busy) return;                       // one print at a time — no double dialogs
    setOpen(false);
    remember(orderId, id); setLast(id);
    if (id.startsWith(INV) && !invoice) { onError("This job has no invoice yet"); return; }
    setBusy(true);
    try {
      if (id === STICKER) { await onSticker(); return; }
      const url = id.startsWith(INV)
        ? `/documents/render-invoice/${encodeURIComponent(invoice!.id)}/${encodeURIComponent(id.slice(INV.length))}`
        : `/documents/render/${encodeURIComponent(orderId)}/${encodeURIComponent(id)}`;
      const r = await api.get<{ html: string }>(url);
      printHtmlInFrame(r.html);
    } catch (e: any) {
      onError(e?.message || "Couldn't prepare that document");
    } finally { setBusy(false); }
  }

  const listed = showAll ? all : fitting;
  const row = "w-full flex items-center gap-2 px-2 py-1.5 rounded-lg hover:bg-slate-50 text-left text-ink-primary";
  return (
    <div className="flex items-stretch">
      <button type="button" onClick={() => print(choice)} disabled={busy}
        className="h-9 px-3.5 text-xs text-ink-primary font-semibold flex items-center gap-1.5 border border-slate-200 rounded-l-xl bg-white hover:bg-slate-50 disabled:opacity-60">
        <M n="print" c="text-sm" />{busy ? "Preparing…" : `Print ${nameOf(choice)}`}</button>
      <button type="button" data-print-menu={orderId} aria-label="Choose what to print" aria-expanded={open}
        onClick={() => setOpen((v) => !v)} disabled={busy}
        className="h-9 px-2 text-ink-secondary hover:text-ink-primary border border-l-0 border-slate-200 rounded-r-xl bg-white hover:bg-slate-50">
        <M n="expand_more" c="text-[14px]" /></button>
      {open && (
        <FloatingMenu anchorSel={`[data-print-menu="${orderId}"]`} onClose={() => setOpen(false)} width={256}>
          <div>
            <div className="text-[11px] font-bold text-ink-tertiary px-2 py-1">Labels</div>
            <button onClick={() => print(STICKER)} className={row}>
              <M n="label" c="text-[14px] text-ink-tertiary" />Bench sticker</button>
            <div className="text-[11px] font-bold text-ink-tertiary px-2 py-1 mt-1 border-t border-slate-100">Documents</div>
            {listed.map((t) => (
              <button key={t.id} onClick={() => print(t.id)} className={row}>
                <M n="description" c="text-[14px] text-ink-tertiary" /><span className="truncate">{t.name}</span>
                {t.id === last && <span className="ml-auto text-[10px] font-semibold text-ink-tertiary">last used</span>}
              </button>
            ))}
            {listed.length === 0 && (
              <p className="px-2 py-1.5 text-ink-tertiary">No documents for this status. Add them in Settings → Documents you print.</p>
            )}
            {!showAll && all.some((t) => !fitting.some((f) => f.id === t.id)) && (
              <button onClick={() => setShowAll(true)} className="w-full text-left px-2 py-1.5 rounded-lg hover:bg-slate-50 text-primary font-semibold">Show all documents</button>
            )}
            {invoice && invTpls.length > 0 && (<>
              <div className="text-[11px] font-bold text-ink-tertiary px-2 py-1 mt-1 border-t border-slate-100">Invoice {invoice.invoice_number || ""}</div>
              {invTpls.map((t) => (
                <button key={t.id} onClick={() => print(INV + t.id)} className={row}>
                  <M n="receipt_long" c="text-[14px] text-ink-tertiary" /><span className="truncate">{t.name}</span>
                  {INV + t.id === last && <span className="ml-auto text-[10px] font-semibold text-ink-tertiary">last used</span>}
                </button>
              ))}
            </>)}
          </div>
        </FloatingMenu>
      )}
    </div>
  );
}
