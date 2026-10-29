import { Fragment, useEffect, useState } from "react";
import { M } from "../../components/Shell";
import FloatingMenu from "../../components/FloatingMenu";
import { api } from "../../api/client";
import { printHtmlInFrame } from "../../lib/safeHtml";
import Switch from "../../components/Switch";

/**
 * The work order's Print button: one click prints the last thing printed for this job
 * (or the best fit for its status); the arrow lists the bench sticker and the shop's own
 * document templates that fit the job's status (Settings → Documents you print), with
 * "Show all" so nothing is locked out, plus invoice templates for the job's latest invoice
 * (for staff who can see invoices). Documents render server-side at their exact size and
 * print through a hidden sandboxed frame. This is the job's one place to print: the itemised copy
 * (a kit job, listed item by item) is a row here too, and so are the shop's "print by itself when
 * a job closes" ticks: shop-wide, they stay as set for every job until someone changes them.
 */
type Tpl = { id: string; name: string; doc_type: string; is_default?: boolean; print_statuses?: string[]; auto_print_events?: string[]; auto_print_allowed?: string[] };

export const STICKER = "sticker";
export const ITEMISED = "itemised";
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
export function pickDefault(fitting: Tpl[], last: string | null, all: Tpl[], itemisedOk = false): string {
  if (last && (last === STICKER || (last === ITEMISED && itemisedOk) || all.some((t) => t.id === last || INV + t.id === last))) return last;
  const forStatus = fitting.find((t) => (t.print_statuses || []).length > 0);
  if (forStatus) return forStatus.id;
  const wo = fitting.find((t) => t.doc_type === "work_order" && t.is_default) || fitting.find((t) => t.doc_type === "work_order");
  return wo ? wo.id : STICKER;
}

export default function PrintMenu({ orderId, status, canInvoices = true, onSticker, onItemised, itemisedType = "work_order", autoPrint, onError }: {
  orderId: string;
  status?: string | null;
  /** "View invoices": without it the menu offers no invoice templates. */
  canInvoices?: boolean;
  onSticker: () => void | Promise<void>;
  /** Set only for a job with a kit and someone allowed to print every item: adds "… (itemised)". */
  onItemised?: () => void | Promise<void>;
  /** The document the itemised copy is a version of (an estimate prints an itemised estimate). */
  itemisedType?: "work_order" | "estimate";
  /** Show what prints by itself when a job closes, as ticks; `canChange` lets this person change them. */
  autoPrint?: { canChange: boolean };
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
  const [autoTpls, setAutoTpls] = useState<Tpl[] | null>(null);   // documents that can print by themselves on close
  const [autoSaving, setAutoSaving] = useState<string | null>(null);
  const [undo, setUndo] = useState<{ t: Tpl; prev: string[]; text: string } | null>(null);   // last auto-print change, undoable
  const [kitItems, setKitItems] = useState(false);   // "List every kit item": for these prints only, never saved

  // The parent mounts one PrintMenu per job (key = order id), so state never leaks between jobs.
  // All templates and the job's invoice load once; the status-fitting list reloads on a status change.
  // Templates reload every time the menu opens: the same shop-wide auto-print ticks are also
  // changed in the payment window and on the new-job screen, so never show a stale copy.
  const loadTemplates = (live: () => boolean) => api.get<any>("/documents/templates").then((a) => {
    if (!live()) return;
    setAll((a?.items || []).filter((t: Tpl) => !HIDDEN.has(t.doc_type)));
    setInvTpls((a?.items || []).filter((t: Tpl) => t.doc_type === "invoice"));
    setAutoTpls((a?.items || []).filter((t: Tpl) => (t.auto_print_allowed || []).includes("close")));
  }).catch(() => { /* the sticker still prints */ });
  useEffect(() => { if (open) { let live = true; loadTemplates(() => live); return () => { live = false; }; } }, [open]);   // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    let live = true;
    loadTemplates(() => live);
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

  const choice = pickDefault(fitting, last, all, !!onItemised);
  const woName = (all.find((t) => t.doc_type === itemisedType && t.is_default) || all.find((t) => t.doc_type === itemisedType))?.name || (itemisedType === "estimate" ? "Estimate" : "Work order");
  const nameOf = (id: string) => id === STICKER ? "sticker" : id === ITEMISED ? `${woName} (itemised)`
    : id.startsWith(INV) ? (invTpls.find((t) => INV + t.id === id)?.name || "invoice")
    : (all.find((t) => t.id === id)?.name || "document");

  // Shop-wide: ticking a document makes it print by itself whenever any job is closed, until unticked.
  async function saveAuto(t: Tpl, events: string[]) {
    setAutoSaving(t.id);
    try {
      const r = await api.put<Tpl>(`/documents/templates/${t.id}/auto-print`, { events });
      setAutoTpls((ts) => (ts || []).map((x) => (x.id === t.id ? { ...x, auto_print_events: r?.auto_print_events ?? events } : x)));
      return true;
    } catch (e: any) {
      onError(e?.message || "Couldn't save that");
      return false;
    } finally { setAutoSaving(null); }
  }
  async function toggleAuto(t: Tpl) {
    if (!autoPrint?.canChange || autoSaving) return;
    const cur = t.auto_print_events || [];
    const turningOn = !cur.includes("close");
    const events = turningOn ? [...cur, "close"] : cur.filter((e) => e !== "close");
    if (await saveAuto(t, events)) setUndo({ t, prev: cur, text: turningOn ? `${t.name} will print when any job closes.` : `${t.name} won't print by itself any more.` });
  }

  async function print(id: string) {
    if (busy) return;                       // one print at a time — no double dialogs
    setOpen(false);
    remember(orderId, id); setLast(id);
    if (id.startsWith(INV) && !invoice) { onError("This job has no invoice yet"); return; }
    setBusy(true);
    try {
      if (id === STICKER) { await onSticker(); return; }
      if (id === ITEMISED) { await onItemised?.(); return; }
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
  const autoOn = (id: string) => (autoTpls || []).some((t) => t.id === id && (t.auto_print_events || []).includes("close"));
  // Which listed document the "List every kit item" switch belongs to: the default work order (or estimate).
  const itemisedAnchor = (listed.find((t) => t.doc_type === itemisedType && t.is_default) || listed.find((t) => t.doc_type === itemisedType))?.id;
  const row = "flex w-full min-h-10 items-center gap-2 rounded-lg px-3 py-2 text-left text-[13px] text-ink-primary hover:bg-slate-50";
  const tag = "shrink-0 rounded-full px-1.5 py-px text-[10px] font-semibold";
  const printRow = (id: string, label: string, opts?: { auto?: boolean }) => (
    <button key={id} type="button" onClick={() => print(id)} className={row}>
      <span className="min-w-0 flex-1 truncate">{label}</span>
      {opts?.auto && <span className={`${tag} bg-primary/10 text-primary`} title="Prints by itself when a job closes">Auto</span>}
      {(id === last || (id === itemisedAnchor && last === ITEMISED)) && <span className="shrink-0 text-[11px] text-ink-tertiary">Last used</span>}
    </button>
  );
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
        <FloatingMenu anchorSel={`[data-print-menu="${orderId}"]`} onClose={() => { setOpen(false); setUndo(null); }} width={300}>
          <div className="-m-1.5">
            {/* Print now: click a row and it prints. The sticker sits in the same list. */}
            <div className="p-1.5">
              <div className="px-3 pb-1 pt-1.5 text-[11px] font-semibold uppercase tracking-wide text-ink-tertiary">Print now</div>
              {listed.map((t) => (<Fragment key={t.id}>
                {printRow(onItemised && t.id === itemisedAnchor && kitItems ? ITEMISED : t.id, t.name, { auto: autoOn(t.id) })}
                {/* The itemised copy isn't another document: it's this one with every kit item listed. */}
                {onItemised && t.id === itemisedAnchor && (
                  <label className="flex items-center gap-2.5 pb-2 pl-6 pr-3 text-xs text-ink-secondary">
                    <span className="flex-1">List every kit item</span>
                    <Switch size="sm" on={kitItems} onChange={setKitItems} label="List every kit item" />
                  </label>
                )}
              </Fragment>))}
              {onItemised && !itemisedAnchor && printRow(ITEMISED, `${woName} (itemised)`)}
              {printRow(STICKER, "Bench sticker")}
              {invoice && invTpls.map((t) => printRow(INV + t.id, `${t.name} · ${invoice.invoice_number || "invoice"}`))}
              {listed.length === 0 && (
                <p className="px-3 py-2 text-xs text-ink-tertiary">No documents for this status yet. Add them in Settings → Documents you print.</p>
              )}
              {!showAll && all.some((t) => !fitting.some((f) => f.id === t.id)) && (
                <button type="button" onClick={() => setShowAll(true)} className="w-full rounded-lg px-3 py-2 text-left text-xs font-semibold text-primary hover:bg-slate-50">All documents…</button>
              )}
            </div>

            {/* Automatic: a shop-wide setting, kept in its own tinted box so nobody changes it by accident.
                Only the switch changes it; the row text does nothing. */}
            {autoPrint && autoTpls && autoTpls.length > 0 && (
              <div className="rounded-b-xl border-t border-slate-200 bg-slate-50 px-1.5 pb-2 pt-2">
                <div className="flex items-baseline justify-between px-3 pb-1">
                  <span className="text-[11px] font-semibold uppercase tracking-wide text-ink-tertiary">Automatic</span>
                  <span className="text-[11px] text-ink-tertiary">Applies to every job</span>
                </div>
                {autoTpls.map((t) => {
                  const on = (t.auto_print_events || []).includes("close");
                  return (
                    <div key={t.id} className="flex min-h-10 items-center gap-3 px-3 py-1.5">
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-[13px] text-ink-primary">{t.name}</span>
                        <span className="block text-[11px] text-ink-tertiary">when a job closes</span>
                      </span>
                      <Switch size="sm" on={on} disabled={!autoPrint.canChange || autoSaving === t.id} onChange={() => toggleAuto(t)}
                        label={`Print ${t.name} by itself when a job closes`} />
                    </div>
                  );
                })}
                {undo ? (
                  <p role="status" className="mx-1.5 mt-1 flex items-center gap-2 rounded-lg bg-white px-2.5 py-2 text-[11px] text-ink-secondary ring-1 ring-slate-200">
                    <span className="flex-1">{undo.text}</span>
                    <button type="button" onClick={async () => { if (await saveAuto(undo.t, undo.prev)) setUndo(null); }} className="font-semibold text-primary hover:underline">Undo</button>
                  </p>
                ) : !autoPrint.canChange && (
                  <p className="px-3 pt-1 text-[11px] text-ink-tertiary">Only a manager can change this.</p>
                )}
              </div>
            )}
          </div>
        </FloatingMenu>
      )}
    </div>
  );
}
